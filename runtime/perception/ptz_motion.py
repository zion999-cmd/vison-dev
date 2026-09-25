"""
Perception - L1: PTZ Motion Layer

The single movement writer for pan/tilt, sitting between the decision layer
(revisit: which target, where to look) and ServoPTZ (serial commands).

It exists because two schedulers used to write the same axis directly and
fought over it. Hardware run 2026-09-24 09:35: tracking converged the tilt
onto a target (95→103→111→114→115) and the exploration path reset it to
level every ~8s, so the tilt never stayed converged, the first command of
every cycle saturated at the ±8° clamp, and the servo was visibly driven
down and immediately back up. 25% of tracking commands were saturated on
tilt against 9% on pan.

What it does:
  - ownership: TRACK > EXPLORE, with WEAR_PROTECT able to preempt either.
    While tracking holds the axes, exploration requests are dropped so it
    cannot steal an axis mid-follow.
  - large setpoints are walked in steps of at most MAX_STEP_DEG, at most one
    per step() call, so a 60° sweep is a sequence of short moves instead of
    one bang-bang command.
  - latest setpoint wins: a newer request replaces the pending remainder
    rather than queueing behind it, so there is no backlog and no stale
    setpoint left to execute.

It does NOT filter or predict the target: the setpoint it is given is the
setpoint it drives. Filtering, prediction and gain changes are out of scope
for this phase.
"""

import logging
from typing import Optional

logger = logging.getLogger("L1.PtzMotion")

# The servo travels at ~125°/s (measured: 8 ms/degree, symmetric on both
# axes). One step per frame at 5 FPS budgets 0.2s, so a step of at most 20°
# is ~160 ms of travel — the servo is still settling when the next step
# arrives, which keeps a long move continuous rather than a burst-and-pause.
MAX_STEP_DEG = 20

# A tracking request holds both axes for this long. Tracking re-requests
# every _track_interval (1.5s), so this outlives it by one interval and
# exploration cannot slip in between two tracking updates.
TRACK_HOLD_SEC = 3.0


class PtzMotion:
    """Arbitrates pan/tilt movement and rate-limits large setpoints."""

    def __init__(self, servo_ptz, max_step: int = MAX_STEP_DEG,
                 track_hold: float = TRACK_HOLD_SEC):
        self._ptz = servo_ptz
        self._max_step = max_step
        self._track_hold = track_hold
        # Tracking owns both axes until this timestamp.
        self._track_until = 0.0
        # Latest requested setpoint per axis; None means "nothing pending".
        self._goal_pan: Optional[int] = None
        self._goal_tilt: Optional[int] = None
        # A startup survey holds both axes until it completes — not on a timer.
        self._survey_active = False
        # Diagnostics for the hardware smoke test.
        self.blocked_explore = 0
        self.blocked_track = 0

    # ── Ownership ──

    def tracking_active(self, now: float) -> bool:
        """True while tracking owns pan and tilt."""
        return now < self._track_until

    # ── Startup survey ownership ──

    @property
    def surveying(self) -> bool:
        """True while a startup survey owns pan and tilt."""
        return self._survey_active

    def survey(self, now: float, pan: Optional[int] = None,
               tilt: Optional[int] = None) -> None:
        """A startup survey claims both axes and sets a viewpoint setpoint.

        Unlike tracking's claim this one does not expire on a timer: the survey
        is a bounded protocol with its own completion, and a claim that lapsed
        between two viewpoints would let normal behavior take the camera.
        Movement still goes through step(), so a survey move is rate-limited
        and coalesced exactly like any other.
        """
        self._survey_active = True
        if pan is not None:
            self._goal_pan = int(pan)
        if tilt is not None:
            self._goal_tilt = int(tilt)

    def end_survey(self) -> None:
        """Hand movement ownership back to normal runtime behavior.

        Also drops any setpoint the survey left behind. A viewpoint that never
        arrived (a stuck or unresponsive PTZ) leaves its goal pending, and
        `step()` would otherwise keep driving the camera toward a pose the
        survey already gave up on — after READY, when behavior thinks it owns
        the axes again.
        """
        self._survey_active = False
        self._goal_pan = None
        self._goal_tilt = None

    # ── Requests ──

    def track(self, now: float, pan: Optional[int] = None,
              tilt: Optional[int] = None) -> None:
        """Tracking claims both axes and sets its setpoints.

        Claiming both axes is deliberate: a target that needs no pan
        correction this instant still needs the pan left alone, or
        exploration turns the camera away mid-follow.

        Dropped while a startup survey holds the axes — the survey owns
        movement until it completes, and two writers aiming the camera at once
        is the thing this layer exists to prevent.
        """
        if self._survey_active:
            self.blocked_track += 1
            logger.debug("PtzMotion: track dropped, startup survey owns the axes")
            return
        self._track_until = now + self._track_hold
        if pan is not None:
            self._goal_pan = int(pan)
        if tilt is not None:
            self._goal_tilt = int(tilt)

    def explore(self, now: float, pan: Optional[int] = None,
                tilt: Optional[int] = None) -> bool:
        """Exploration request. Dropped on both axes while tracking holds them.

        Returns True when the request was accepted.
        """
        if self._survey_active or self.tracking_active(now):
            self.blocked_explore += 1
            logger.debug("PtzMotion: explore dropped (survey=%s tracking=%s pan=%s tilt=%s)",
                         self._survey_active, self.tracking_active(now), pan, tilt)
            return False
        if pan is not None:
            self._goal_pan = int(pan)
        if tilt is not None:
            self._goal_tilt = int(tilt)
        return True

    def explore_pan_by(self, delta: int, now: float) -> bool:
        """Exploration pan turn, resolved against the current position."""
        return self.explore(now, pan=self._ptz.pan + int(delta))

    def wear_protect(self, tilt: int) -> None:
        """Hardware-wear move (pull back from an extreme hold).

        Always accepted — it protects the servo and fires at most once per
        90s, so it cannot fight tracking for long.
        """
        self._goal_tilt = int(tilt)

    # ── Emission ──

    def pending(self) -> bool:
        return self._goal_pan is not None or self._goal_tilt is not None

    def step(self, now: float) -> bool:
        """Emit one rate-limited step per axis toward the pending goals.

        Call once per frame. Returns True when a command was issued.
        """
        if not self.pending():
            return False
        if self._ptz.moving:
            return False  # one move in flight at a time; never queue behind it

        pan_now, tilt_now = self._ptz.pan, self._ptz.tilt
        emitted = False

        if self._goal_pan is not None:
            remaining = self._goal_pan - pan_now
            if remaining:
                step = max(-self._max_step, min(self._max_step, remaining))
                self._ptz.pan_to(pan_now + step)
                emitted = True
            if abs(remaining) <= self._max_step:
                self._goal_pan = None

        if self._goal_tilt is not None:
            remaining = self._goal_tilt - tilt_now
            if remaining:
                step = max(-self._max_step, min(self._max_step, remaining))
                self._ptz.tilt_to(tilt_now + step)
                emitted = True
            if abs(remaining) <= self._max_step:
                self._goal_tilt = None

        return emitted
