"""
Perception - L2: Frame Differencing (the gatekeeper)

Pure numpy implementation — no OpenCV needed.
Cheapest possible check: "did the frame change?"

Replaces MOG2 background subtraction from motion.py.
"""

import logging
from typing import Optional

import numpy as np
from config import FRAME_DIFF_THRESHOLD, FRAME_DIFF_MIN_PIXELS, OBSERVATION_MAX_AGE_SEC

logger = logging.getLogger("L2.FrameDiff")


class FrameDiff:
    """Detect whether the frame changed since last check. Cheapest gatekeeper in L2."""

    def __init__(
        self,
        threshold: int = FRAME_DIFF_THRESHOLD,
        min_pixels: int = FRAME_DIFF_MIN_PIXELS,
        observation_max_age: float = OBSERVATION_MAX_AGE_SEC,
    ):
        self.threshold = threshold
        self.min_pixels = min_pixels
        self.observation_max_age = observation_max_age
        self._prev: Optional[np.ndarray] = None
        self._motion_level: float = 0.0
        self._last_observation: float = 0.0

    def changed(self, frame_bgr: np.ndarray) -> bool:
        """
        Return True if the frame is significantly different from the previous.
        Also updates self.motion_level with the actual change ratio.
        """
        # Stride-sample the frame, then copy it: callers mutate their frame
        # in place after this call (preview rendering draws overlays onto it),
        # and a strided slice is a view — those edits would land inside the
        # cached reference and forge a change on the next frame.
        small = frame_bgr[::2, ::2].copy()

        # The cached reference keeps the camera's geometry. A capture device
        # can renegotiate resolution mid-stream (e.g. 4:3 → 16:9 after another
        # app grabs the camera), which made the subtraction below
        # broadcast-fail and kill the loop. Treat the new geometry as
        # "changed" and reseed.
        if self._prev is None or self._prev.shape != small.shape:
            if self._prev is not None:
                logger.warning(
                    "Frame geometry changed %s → %s — reseeding reference frame",
                    self._prev.shape, small.shape,
                )
                # Nothing comparable to measure against, so don't keep
                # reporting the previous geometry's motion level.
                self._motion_level = 0.0
            self._prev = small
            return True

        diff = np.abs(small.astype(np.int16) - self._prev.astype(np.int16))
        motion_mask = (diff > self.threshold).any(axis=2)
        changed_pixels = np.count_nonzero(motion_mask)
        total_pixels = motion_mask.size
        self._motion_level = min(1.0, (changed_pixels / max(total_pixels, 1)) * 3)

        self._prev = small
        return changed_pixels > self.min_pixels

    @property
    def motion_level(self) -> float:
        return self._motion_level

    # ── Observation validity ──

    def observation_stale(self, now: float) -> bool:
        """True when the last accepted observation is too old to rely on.

        The gate can stay closed indefinitely through slow drift (per-frame
        changes below threshold), so a retained observation must never be
        asserted without a bound. The caller forces a fresh detection instead
        of assuming the last one still holds.
        """
        return (now - self._last_observation) >= self.observation_max_age

    def mark_observed(self, now: float) -> None:
        """Record that a detection observation was accepted at `now`."""
        self._last_observation = now

    def reset(self) -> None:
        """Forget the reference frame (e.g., after scene change)."""
        self._prev = None
        # The motion level was measured against the forgotten reference, so
        # it must not outlive it. 0.0 is the module's neutral value.
        self._motion_level = 0.0
        # The observation is forgotten too, so it must count as stale rather
        # than keep claiming validity.
        self._last_observation = 0.0
