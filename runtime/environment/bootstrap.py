"""
Environment — Visual Environment Bootstrap (P0008.2)

What this replaces. Startup used to be a timer inside RevisitController: 60
seconds elapsing *was* the contract, a fixed relative sweep ran during it, and
nothing recorded whether anything had actually been seen. "Startup finished"
meant "the clock moved on". There was no READY state, no coverage result, and
no evidence that any direction had been observed.

What it is now. An explicit lifecycle —

    INITIALIZING → SURVEYING → READY

— over a deterministic set of viewpoints, each following

    move → settle → observe → record

and answering three questions before normal runtime takes over: which
viewpoints can be observed, which have been observed, and what they looked like.
Nothing here needs YOLO, YuNet or a VLM to decide that a viewpoint was observed:
a valid image is the whole requirement.

Two boundaries worth stating, because they are what make this bootstrap and not
an environmental model:

  * A viewpoint is covered by an *observation*, never by a command. Issuing a
    movement does not cover anything, which is why the observation waits for a
    frame captured after the camera actually arrived and settled.
  * READY means "enough initial visual reference to begin normal operation". It
    does not mean the room is understood, that novelty is zero, or that every
    view has been visited. Coverage is spatial only — it is not a novelty
    score, and there is deliberately no combined `environment_score`.

The survey owns pan/tilt through the existing Motion Layer (`survey()` /
`end_survey()`) rather than by scattered `if startup_phase` checks, so normal
tracking/explore cannot fight it for the camera and hands back cleanly at READY.
This is bounded work: `MAX_ATTEMPTS` per viewpoint with a timeout each, so the
worst case is the 60 s the old fixed window had — as a failure path with a loud
warning per viewpoint, never a deadlock. The normal path is ~11 s on hardware.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence

from config import PERCEPTION_FPS
from runtime.environment.viewpoint import (Viewpoint, ViewSignature,
                                           bootstrap_viewpoints)
from runtime.perception.servo_ptz import PAN_MAX, PAN_MIN, TILT_MIN

logger = logging.getLogger("Env.Bootstrap")

# The framing loop's documented horizontal FOV. It is not a calibration, which
# is exactly why the layout keeps a deliberate overlap instead of claiming
# gapless coverage.
DEFAULT_FOV_H = 55.0

# How long to wait after arrival before believing the picture. Two frame
# periods: one to replace whatever was buffered mid-move, one to spare.
SETTLE_SEC = 2.0 / PERCEPTION_FPS

# Per-attempt bound. A full 155° survey move takes ~1.3 s at the measured
# 8 ms/degree, so this is ~4× headroom for a camera that is merely slow. It also
# fixes the worst case: MAX_ATTEMPTS × this × viewpoints = 60 s, the same bound
# the fixed startup window it replaced had. The difference is that 60 s is now
# the *failure* path — a PTZ that connected and then stopped answering — while
# the normal path is the measured ~11 s.
VIEWPOINT_TIMEOUT_SEC = 5.0

# Each viewpoint gets a retry, then its failure is recorded and the survey
# moves on. Bounded: the survey can always finish.
MAX_ATTEMPTS = 2

# The servo takes whole degrees; this is slack for a viewpoint that is "close
# enough" without letting the survey accept a pose it never reached.
POSITION_TOLERANCE_DEG = 2.0


class Lifecycle(Enum):
    INITIALIZING = "initializing"
    SURVEYING = "surveying"
    READY = "ready"


class ViewState(str, Enum):
    PENDING = "pending"
    MOVING = "moving"
    SETTLING = "settling"
    OBSERVED = "observed"
    FAILED = "failed"


@dataclass
class ViewpointProgress:
    """One viewpoint's identity plus how the survey went for it."""

    viewpoint: Viewpoint
    state: ViewState = ViewState.PENDING
    attempts: int = 0
    arrived_at: float = 0.0
    signature_at: float = 0.0
    signature: Optional[ViewSignature] = None
    note: str = ""

    @property
    def index(self) -> int:
        return self.viewpoint.index

    @property
    def pose(self):
        return self.viewpoint.pose


@dataclass(frozen=True)
class Coverage:
    """Spatial coverage only. Not a novelty score, not a quality score."""

    total: int
    observed: int
    failed: int

    @property
    def covered_fraction(self) -> float:
        return self.observed / self.total if self.total else 0.0

    @property
    def pending(self) -> int:
        return self.total - self.observed - self.failed


class EnvironmentBootstrap:
    """Runs the startup survey, then hands movement ownership back."""

    def __init__(self, motion, servo_ptz, anchor_manager=None,
                 viewpoints: Optional[Sequence[Viewpoint]] = None,
                 settle_sec: float = SETTLE_SEC,
                 viewpoint_timeout: float = VIEWPOINT_TIMEOUT_SEC,
                 max_attempts: int = MAX_ATTEMPTS,
                 position_tolerance: float = POSITION_TOLERANCE_DEG):
        self._motion = motion
        self._servo = servo_ptz
        self._anchors = anchor_manager
        self._views: List[ViewpointProgress] = [
            ViewpointProgress(v) for v in (viewpoints if viewpoints is not None
                                           else bootstrap_viewpoints(
                                               PAN_MIN, PAN_MAX, TILT_MIN, DEFAULT_FOV_H))
        ]
        self._settle = settle_sec
        self._timeout = viewpoint_timeout
        self._max_attempts = max_attempts
        self._tolerance = position_tolerance
        self._lifecycle = Lifecycle.INITIALIZING
        self._started_at = 0.0
        self._goal_issued_at = 0.0
        self._skipped = False

    # ── State ──

    @property
    def lifecycle(self) -> Lifecycle:
        return self._lifecycle

    @property
    def skipped(self) -> bool:
        """True when physical initialization failed and no survey ran."""
        return self._skipped

    @property
    def viewpoints(self) -> List[ViewpointProgress]:
        return self._views

    def coverage(self) -> Coverage:
        observed = sum(1 for v in self._views if v.state is ViewState.OBSERVED)
        failed = sum(1 for v in self._views if v.state is ViewState.FAILED)
        return Coverage(total=len(self._views), observed=observed, failed=failed)

    @property
    def current_viewpoint(self) -> Optional[ViewpointProgress]:
        """The viewpoint the survey is working on, or None when it is done."""
        return self._current()

    def baselines(self) -> List[ViewpointProgress]:
        """Observed viewpoints with their evidence, in survey order."""
        return [v for v in self._views if v.state is ViewState.OBSERVED]

    # ── Lifecycle ──

    def start(self, physical_ok: bool) -> Lifecycle:
        """Report the physical-initialization result and enter the survey.

        Called once the camera and PTZ are up (or known not to be). A failed
        physical init means there is no survey to run — the camera cannot be
        aimed at anything — so the lifecycle goes straight to READY with the
        skip recorded, rather than spending the attempt budget discovering what
        is already known.
        """
        if self._lifecycle is not Lifecycle.INITIALIZING:
            return self._lifecycle

        if not physical_ok:
            self._skipped = True
            self._lifecycle = Lifecycle.READY
            logger.warning("Environment Bootstrap: physical initialization failed — "
                           "survey skipped, runtime starts with no visual baseline")
            return self._lifecycle

        if not self._views:
            self._lifecycle = Lifecycle.READY
            logger.info("Environment Bootstrap: INITIALIZING → READY (no viewpoints)")
            return self._lifecycle

        self._lifecycle = Lifecycle.SURVEYING
        first, last = self._views[0].viewpoint, self._views[-1].viewpoint
        logger.info("Environment Bootstrap: INITIALIZING → SURVEYING "
                    "(%d viewpoints, pan %d..%d, tilt %d)",
                    len(self._views), round(first.pan), round(last.pan), round(first.tilt))
        return self._lifecycle

    # ── The survey protocol ──

    def step(self, now: float, frame=None, frame_ts: Optional[float] = None) -> None:
        """Advance the survey by one frame. Call once per loop iteration.

        Only sets movement *goals* — the Motion Layer's `step()` remains the
        single writer that emits commands, exactly as for every other owner.
        """
        if self._lifecycle is not Lifecycle.SURVEYING:
            return
        if self._started_at == 0.0:
            self._started_at = now

        vp = self._current()
        if vp is None:
            self._finish(now)
            return

        if vp.state is ViewState.PENDING:
            self._begin(vp, now)
        elif vp.state is ViewState.MOVING:
            self._advance_from_moving(vp, now)
        elif vp.state is ViewState.SETTLING:
            self._advance_from_settling(vp, now, frame, frame_ts)

    def _current(self) -> Optional[ViewpointProgress]:
        for v in self._views:
            if v.state in (ViewState.PENDING, ViewState.MOVING, ViewState.SETTLING):
                return v
        return None

    def _begin(self, vp: ViewpointProgress, now: float) -> None:
        vp.attempts += 1
        vp.state = ViewState.MOVING
        self._goal_issued_at = now
        # One request per attempt, not one per frame: re-issuing every frame
        # would overwrite a wear-protect tilt and make the survey fight itself.
        self._motion.survey(now, pan=vp.viewpoint.pan, tilt=vp.viewpoint.tilt)
        logger.info("Bootstrap [viewpoint %d/%d]: pose=%s → moving (attempt %d/%d)",
                    vp.index + 1, len(self._views), vp.pose, vp.attempts, self._max_attempts)

    def _advance_from_moving(self, vp: ViewpointProgress, now: float) -> None:
        if self._at_pose(vp):
            vp.arrived_at = now
            vp.state = ViewState.SETTLING
            logger.info("Bootstrap [viewpoint %d/%d]: arrived at pose=%s",
                        vp.index + 1, len(self._views), vp.pose)
        elif now - self._goal_issued_at > self._timeout:
            self._fail(vp, now, "never reached the pose")

    def _advance_from_settling(self, vp: ViewpointProgress, now: float,
                               frame, frame_ts: Optional[float]) -> None:
        if now - vp.arrived_at > self._timeout:
            self._fail(vp, now, "camera never settled")
            return
        if now - vp.arrived_at < self._settle:
            return
        # The frame must postdate the arrival: a frame captured during the move
        # shows the direction we were leaving, not the one we came to see.
        if frame is None or frame_ts is None or frame_ts < vp.arrived_at:
            return
        self._observe(vp, frame, now)

    def _observe(self, vp: ViewpointProgress, frame, now: float) -> None:
        try:
            signature = ViewSignature.from_frame(frame)
        except Exception as exc:
            # A frame we cannot describe fails this viewpoint, bounded by the
            # attempt budget above. It must not reach the main loop: startup
            # is not the place for an unhandled decode error to end the run.
            self._fail(vp, now, f"unusable frame ({exc!r})")
            return

        vp.signature = signature
        vp.signature_at = now
        vp.state = ViewState.OBSERVED
        if self._anchors is not None:
            # Evidence goes to the anchor layer, which is the spatial index the
            # rest of the runtime already uses — so the baseline survives READY
            # and keeps accumulating from normal observation afterwards.
            self._anchors.record_visual(pan=vp.viewpoint.pan, tilt=vp.viewpoint.tilt,
                                        signature=signature.values, now=now)
        cov = self.coverage()      # already includes this viewpoint
        logger.info("Bootstrap [viewpoint %d/%d]: observed pose=%s → covered %d/%d",
                    vp.index + 1, len(self._views), vp.pose,
                    cov.observed, cov.total)
        self._settle_remaining(now)

    def _fail(self, vp: ViewpointProgress, now: float, reason: str) -> None:
        if vp.attempts < self._max_attempts:
            vp.note = reason
            vp.state = ViewState.PENDING
            logger.warning("Bootstrap [viewpoint %d/%d]: %s — retrying (%d/%d)",
                           vp.index + 1, len(self._views), reason,
                           vp.attempts, self._max_attempts)
            return
        vp.note = reason
        vp.state = ViewState.FAILED
        logger.warning("Bootstrap [viewpoint %d/%d]: FAILED pose=%s — %s",
                       vp.index + 1, len(self._views), vp.pose, reason)
        self._settle_remaining(now)

    def _settle_remaining(self, now: float) -> None:
        if self._current() is None:
            self._finish(now)

    def _finish(self, now: float) -> None:
        self._motion.end_survey()
        self._lifecycle = Lifecycle.READY
        cov = self.coverage()
        logger.info("Environment Bootstrap: SURVEYING → READY "
                    "(coverage %d/%d, %d failed, %.1fs)",
                    cov.observed, cov.total, cov.failed,
                    (now - self._started_at) if self._started_at else 0.0)
        if cov.failed:
            logger.warning("Environment Bootstrap: uncovered viewpoints: %s",
                           [v.pose for v in self._views if v.state is ViewState.FAILED])

    def _at_pose(self, vp: ViewpointProgress) -> bool:
        if self._servo is None:
            return False
        return (abs(self._servo.pan - vp.viewpoint.pan) <= self._tolerance
                and abs(self._servo.tilt - vp.viewpoint.tilt) <= self._tolerance)
