"""
Environment — Visual Environment Bootstrap: viewpoints and visual evidence

Two things a bootstrap viewpoint needs, kept apart from the survey state
machine that consumes them:

  * where to look — a deterministic layout derived from the PTZ's real travel
    limits and the camera's horizontal field of view. Not the old
    `_SWEEP_SEQUENCE` deltas: those were relative turns tuned for a timed
    sweep, and they do not describe coverage of anything.

  * what it looked like — the cheapest visual baseline that can support the
    future question "does this view look materially different from the one I
    recorded?". P0008.2 does not answer that question. It only has to keep
    enough evidence that the question stays answerable, and it must not
    introduce a semantic model to answer it with: no YOLO, no VLM, no
    embedding network. A coarse grid of mean HSV is the cheapest robust thing
    already available here, and `ViewSignature` is a plain list of floats so a
    stronger descriptor can replace it later without changing the contract.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np

logger = logging.getLogger("Env.Viewpoint")

# Neighbouring bootstrap views must share at least this fraction of their
# horizontal field, so a pose between two viewpoints is still represented by
# the nearer one instead of falling into a gap.
BOOTSTRAP_OVERLAP = 0.30

# Coarse grid over the frame. 4×4 cells of mean hue/saturation/value is enough
# to tell "the same view" from "a different view", and cheap enough to compute
# on the main thread at 5 FPS without touching the inference budget.
SIGNATURE_GRID = 4

# Distance above which two views are a materially different scene. This is a
# first-cut reference for the *future* novelty decision — nothing in P0008.2
# gates on it, and it is expected to be calibrated on hardware before it is
# used for anything.
MATERIAL_DIFFERENCE = 0.10


def material_difference() -> float:
    """The reference distance above which two views count as different."""
    return MATERIAL_DIFFERENCE


@dataclass(frozen=True)
class Viewpoint:
    """One bootstrap direction: a stable identity plus the pose to observe it from.

    Identity is the index in the deterministic layout plus its pose — stable
    across runs, because the layout is derived from the PTZ limits rather than
    from where the camera happened to be pointing last time.
    """

    index: int
    pan: float
    tilt: float

    @property
    def pose(self) -> Tuple[int, int]:
        return (int(round(self.pan)), int(round(self.tilt)))

    @property
    def key(self) -> str:
        return f"viewpoint_{self.index}_{self.pose[0]}_{self.pose[1]}"


def bootstrap_viewpoints(pan_min: float, pan_max: float, tilt: float,
                         fov_h: float,
                         overlap: float = BOOTSTRAP_OVERLAP) -> Tuple[Viewpoint, ...]:
    """The deterministic bootstrap layout.

    Poses are spread across the PTZ's usable pan travel, never outside it, with
    neighbouring centres at most `fov_h * (1 - overlap)` apart so adjacent views
    share field. The count therefore follows the field of view: a wider lens
    needs fewer stops.

    v1 is a single row at `tilt` — the level horizon, which is where the room
    is. Vertical coverage is deliberately not attempted here: it would multiply
    the survey time for structure that normal runtime observation picks up
    anyway through the anchor layer. This is bootstrap coverage, not a claim of
    geometric coverage.
    """
    if pan_max < pan_min:
        raise ValueError(f"pan_max {pan_max} < pan_min {pan_min}")
    travel = float(pan_max - pan_min)
    step = max(1.0, float(fov_h) * (1.0 - float(overlap)))
    count = max(2, int(math.ceil(travel / step)) + 1)

    poses = [(float(pan_min) + i * travel / (count - 1), float(tilt))
             for i in range(count)]

    views = tuple(Viewpoint(index=i, pan=p, tilt=t) for i, (p, t) in enumerate(poses))
    logger.debug("Bootstrap layout: %d viewpoints over %.0f° travel (fov %.0f°, overlap %.0f%%)",
                 len(views), travel, fov_h, overlap * 100)
    return views


@dataclass(frozen=True)
class ViewSignature:
    """A cheap visual baseline for one observed viewpoint.

    `values` is a plain, inspectable vector: per SIGNATURE_GRID² cell, four
    numbers in 0..1 — hue as a saturation-weighted unit vector (so the wrap at
    red does not average hue 175 and hue 5 into 90), saturation, and value.

    Weighting hue by saturation matters: on a grey or near-grey frame hue is
    undefined and cv2 returns noise, so an unweighted hue mean makes two
    pictures of the same white wall read as different views.
    """

    values: List[float]

    @staticmethod
    def from_frame(frame_bgr) -> "ViewSignature":
        """Describe a frame. Reduces, never resizes: no inference, no model."""
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            raise ValueError("no frame to describe")
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        ys = np.linspace(0, h, SIGNATURE_GRID + 1).astype(int)
        xs = np.linspace(0, w, SIGNATURE_GRID + 1).astype(int)
        values: List[float] = []
        for gy in range(SIGNATURE_GRID):
            for gx in range(SIGNATURE_GRID):
                cell = hsv[ys[gy]:ys[gy + 1], xs[gx]:xs[gx + 1]].reshape(-1, 3).astype(np.float32)
                if not len(cell):
                    values.extend((0.5, 0.5, 0.0, 0.0))
                    continue
                theta = cell[:, 0] * (2.0 * np.pi / 179.0)     # cv2 hue is 0..179
                sat = cell[:, 1] / 255.0
                val = cell[:, 2] / 255.0
                values.extend((
                    float((np.cos(theta) * sat).mean() + 1.0) / 2.0,
                    float((np.sin(theta) * sat).mean() + 1.0) / 2.0,
                    float(sat.mean()),
                    float(val.mean()),
                ))
        return ViewSignature(values)

    def distance(self, other: "ViewSignature") -> float:
        """Mean absolute difference, 0.0 for identical views.

        Deliberately not a similarity score and deliberately not thresholded
        here — the caller decides what "materially different" means, and that
        decision belongs to a later phase.
        """
        if other is None or len(other.values) != len(self.values):
            return float("inf")
        return sum(abs(a - b) for a, b in zip(self.values, other.values)) / len(self.values)
