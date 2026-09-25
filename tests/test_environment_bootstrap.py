"""Startup visual-environment bootstrap: lifecycle, coverage, PTZ ownership.

What this replaces: startup used to be a timer inside RevisitController — 60
seconds elapsing was the whole contract, a fixed relative sweep ran during it,
and nothing recorded whether anything had actually been seen. "Startup
finished" meant "the clock moved on".

What it is now: an explicit INITIALIZING → SURVEYING → READY lifecycle over a
deterministic set of viewpoints, each following move → settle → valid
observation → record. A viewpoint is covered by an *observation*, never by a
command, and never by recognition — a valid image counts even when YOLO, YuNet
and the VLM have nothing to say about it.
"""
import sys

import numpy as np

sys.path.insert(0, '.')

from runtime.environment.bootstrap import EnvironmentBootstrap, Lifecycle
from runtime.environment.viewpoint import Viewpoint, ViewSignature
from runtime.interest.anchor import AnchorManager
from runtime.perception.ptz_motion import PtzMotion

T0 = 1000.0
SETTLE = 0.1
TIMEOUT = 2.0
DT = 0.1


def frame(colour=(40, 60, 80)):
    return np.full((480, 640, 3), colour, dtype=np.uint8)


def scene_for(vp):
    """A distinct image per viewpoint, so evidence can be traced to its pose."""
    return frame((30 + vp.index * 40, 60, 90))


class FakeServo:
    """A servo that takes `delay` seconds to arrive, like the real one."""

    def __init__(self, delay=0.0, stuck=False, pan=90, tilt=95):
        self.pan, self.tilt = pan, tilt
        self.moving = False
        self.calls = []
        self._delay = delay
        self._stuck = stuck
        self._arrive = None
        self._goal = None

    def pan_to(self, angle):
        self.calls.append(("pan", int(angle)))
        self._goal, self._arrive = int(angle), None

    def tilt_to(self, angle):
        self.calls.append(("tilt", int(angle)))
        self._goal, self._arrive = int(angle), None

    def tick(self, now):
        if self._stuck:
            self.moving = True
            return
        if not self.calls:
            self.moving = False
            return
        axis, goal = self.calls[-1]
        current = self.pan if axis == "pan" else self.tilt
        if current == goal:
            self.moving = False
            self._arrive = None
            return
        if self._arrive is None:
            self._arrive = now + self._delay
        self.moving = now < self._arrive
        if not self.moving:
            if axis == "pan":
                self.pan = goal
            else:
                self.tilt = goal


VIEWS = (Viewpoint(0, 10.0, 95.0), Viewpoint(1, 90.0, 95.0), Viewpoint(2, 165.0, 95.0))


def build(views=VIEWS, delay=0.0, stuck=False, anchors=True, pan=90, tilt=95, **kw):
    servo = FakeServo(delay=delay, stuck=stuck, pan=pan, tilt=tilt)
    motion = PtzMotion(servo)
    manager = AnchorManager(pan_spacing=20, tilt_spacing=15) if anchors else None
    boot = EnvironmentBootstrap(motion, servo, anchor_manager=manager,
                                viewpoints=views, settle_sec=SETTLE,
                                viewpoint_timeout=TIMEOUT, **kw)
    return boot, servo, motion, manager


def run(boot, servo, motion, seconds, colour_of=None, t0=T0, stop_at_ready=True):
    """Drive production-shaped frames: the surveyor sets goals, the layer emits,
    then the servo moves — the same order the loop uses.

    Each viewpoint sees its own scene unless `colour_of` overrides it. Returns
    the time the survey finished (or the end of the window).
    """
    t = t0
    for _ in range(int(seconds / DT)):
        vp = boot.current_viewpoint
        f = scene_for(vp.viewpoint) if (colour_of is None and vp is not None) \
            else (frame() if colour_of is None else colour_of(t))
        boot.step(t, f, t)
        servo.tick(t)
        motion.step(t)
        t += DT
        if stop_at_ready and boot.lifecycle is Lifecycle.READY:
            break
    return t


# ── Lifecycle ──

def test_the_lifecycle_is_explicit_and_in_order():
    boot, _, _, _ = build()
    assert boot.lifecycle is Lifecycle.INITIALIZING

    boot.start(physical_ok=True)
    assert boot.lifecycle is Lifecycle.SURVEYING


def test_a_movement_command_alone_covers_nothing():
    """The surveyor issues a goal, and the camera has not even arrived yet."""
    boot, servo, motion, manager = build()
    boot.start(physical_ok=True)

    boot.step(T0, scene_for(VIEWS[0]), T0)
    motion.step(T0)

    assert servo.calls, "the survey must actually move the camera"
    assert boot.coverage().observed == 0, "a command is not an observation"
    assert all(v.signature is None for v in boot.viewpoints)
    assert manager.visual_baselines() == [], "no evidence may be recorded yet"


def test_a_valid_view_covers_a_viewpoint_without_any_recognition():
    """YOLO/YuNet/VLM are not involved: the frames here contain no objects at
    all, and that must not stop a viewpoint from being covered."""
    boot, servo, motion, manager = build()
    boot.start(physical_ok=True)

    run(boot, servo, motion, 30.0, colour_of=lambda t: frame())

    assert boot.coverage().observed == len(VIEWS), "every viewpoint must be covered"
    assert all(v.signature is not None for v in boot.viewpoints)
    assert len(manager.visual_baselines()) == len(VIEWS), \
        "each covered viewpoint must leave a visual baseline"


def test_the_observation_waits_for_the_camera_to_settle():
    boot, servo, motion, manager = build(delay=0.5)
    boot.start(physical_ok=True)

    recorded_while_moving = 0
    t = T0
    for _ in range(120):
        before = boot.coverage().observed
        moving_before = servo.moving
        boot.step(t, frame(), t)
        if boot.coverage().observed > before and moving_before:
            recorded_while_moving += 1
        servo.tick(t)
        motion.step(t)
        t += DT
        if boot.lifecycle is Lifecycle.READY:
            break

    assert boot.lifecycle is Lifecycle.READY
    assert recorded_while_moving == 0, \
        "no viewpoint may be recorded while the camera is still moving into place"
    for v in boot.viewpoints:
        assert v.signature is not None or v.state == "failed"
    assert all(v.signature_at >= v.arrived_at for v in boot.viewpoints if v.signature), \
        "the recorded frame must postdate the arrival, not predate the move"


def test_ready_comes_from_coverage_not_from_elapsed_time():
    boot, servo, motion, _ = build()
    boot.start(physical_ok=True)

    end = run(boot, servo, motion, 60.0)

    assert boot.lifecycle is Lifecycle.READY
    assert end - T0 < 20.0, \
        f"READY must follow coverage, not a 60s clock (took {end - T0:.1f}s)"
    assert boot.coverage().observed == len(VIEWS)


def test_every_viewpoint_looks_different_in_the_recorded_evidence():
    boot, servo, motion, manager = build()
    boot.start(physical_ok=True)
    run(boot, servo, motion, 60.0)

    # the three fake views differ only in their blue channel
    sigs = [v.signature for v in boot.viewpoints]
    assert sigs[0].distance(sigs[1]) > 0.0
    assert sigs[0].distance(sigs[2]) > 0.0


# ── Bounded failure ──

def test_a_viewpoint_that_never_settles_is_failed_and_startup_still_completes():
    boot, servo, motion, _ = build(stuck=True, pan=60, tilt=120)
    boot.start(physical_ok=True)

    end = run(boot, servo, motion, 60.0, colour_of=lambda t: frame())

    assert boot.lifecycle is Lifecycle.READY, \
        "a stuck PTZ must not deadlock startup"
    assert boot.coverage().observed == 0, "a stuck camera cannot have observed a viewpoint"
    assert boot.coverage().failed == len(VIEWS)
    assert end - T0 <= TIMEOUT * 2 * len(VIEWS) + 1.0, \
        "the survey must be bounded by attempts x timeout per viewpoint"


def test_a_failed_viewpoint_does_not_block_the_others():
    boot, servo, motion, _ = build(stuck=False)
    boot.start(physical_ok=True)
    # viewpoint 1 is unreachable: the servo refuses to leave its current pose
    servo.calls.clear()
    original = servo.pan_to

    def stubborn(angle):
        if abs(angle - 10) < 1:      # viewpoint 0's pose never arrives
            return
        original(angle)
    servo.pan_to = stubborn

    run(boot, servo, motion, 60.0, colour_of=lambda t: frame())

    assert boot.lifecycle is Lifecycle.READY
    assert boot.coverage().observed >= len(VIEWS) - 1, \
        "one unreachable viewpoint must not cost the others"


def test_physical_init_failure_skips_the_survey_without_stalling():
    boot, servo, motion, manager = build()
    boot.start(physical_ok=False)

    assert boot.lifecycle is Lifecycle.READY, "no PTZ means no survey to run"
    assert servo.calls == [], "and nothing may be commanded"
    assert boot.coverage().observed == 0
    assert manager.visual_baselines() == []


# ── Ownership and handover ──

def test_the_survey_owns_movement_until_ready_then_hands_back():
    boot, servo, motion, _ = build()
    boot.start(physical_ok=True)
    boot.step(T0, frame(), T0)

    assert motion.surveying is True, "the survey must own the axes while running"
    motion.track(T0, pan=150)
    assert motion.blocked_track >= 1, "behavior must not fight the survey for the camera"

    run(boot, servo, motion, 30.0, colour_of=lambda t: frame(), t0=T0 + 0.1)

    assert boot.lifecycle is Lifecycle.READY
    assert motion.surveying is False, "ownership must return to normal runtime"
    assert motion.explore(T0 + 40.0, pan=90) is True


def test_the_evidence_is_still_readable_after_ready():
    boot, servo, motion, manager = build()
    boot.start(physical_ok=True)
    run(boot, servo, motion, 30.0, colour_of=lambda t: frame())

    assert boot.lifecycle is Lifecycle.READY
    baselines = manager.visual_baselines()
    assert len(baselines) == len(VIEWS)
    assert all(b.visual_signature for b in baselines)

    # and a later reader can put the future question to them: the same view
    # still matches the baseline it recorded ...
    same = ViewSignature.from_frame(frame())
    assert same.distance(ViewSignature(baselines[0].visual_signature)) < 0.02
    # ... and a materially different one does not
    other = ViewSignature.from_frame(frame((200, 200, 200)))
    assert other.distance(ViewSignature(baselines[0].visual_signature)) > 0.05


def test_coverage_reports_progress_while_surveying():
    boot, servo, motion, _ = build()
    boot.start(physical_ok=True)
    assert boot.coverage().total == len(VIEWS)

    run(boot, servo, motion, 30.0, colour_of=lambda t: frame())

    cov = boot.coverage()
    assert cov.covered_fraction == 1.0
    assert cov.observed + cov.failed == cov.total


def test_a_frame_captured_before_arrival_cannot_cover_a_viewpoint():
    """The freshness rule, mutation-sensitively: every frame offered here is
    older than the arrival, so nothing may ever be covered. Drop the
    `frame_ts < arrived_at` check and every viewpoint covers immediately on the
    strength of a frame showing the direction the camera was leaving."""
    boot, servo, motion, manager = build()
    boot.start(physical_ok=True)

    t = T0
    for _ in range(200):
        vp = boot.current_viewpoint
        stale_ts = (vp.arrived_at - 1.0) if (vp is not None and vp.arrived_at) else (t - 1.0)
        boot.step(t, frame(), stale_ts)
        servo.tick(t)
        motion.step(t)
        t += DT
        if boot.lifecycle is Lifecycle.READY:
            break

    assert boot.lifecycle is Lifecycle.READY
    assert boot.coverage().observed == 0, \
        "a frame from before the camera arrived must never cover a viewpoint"
    assert boot.coverage().failed == len(VIEWS)
    assert manager.visual_baselines() == []


def test_the_bootstrap_telemetry_reconstructs_the_startup(caplog):
    """Criterion 11: someone reading only the log must be able to rebuild
    startup → viewpoint visits → observations → coverage → READY."""
    import logging

    boot, servo, motion, _ = build()
    with caplog.at_level(logging.INFO, logger="Env.Bootstrap"):
        boot.start(physical_ok=True)
        run(boot, servo, motion, 60.0)

    text = caplog.text
    assert "INITIALIZING → SURVEYING" in text, "the lifecycle transition must be logged"
    assert "SURVEYING → READY" in text
    assert text.count("→ moving") == len(VIEWS), "every viewpoint visit"
    assert text.count("observed pose=") == len(VIEWS), "every successful observation"
    assert f"coverage {len(VIEWS)}/{len(VIEWS)}" in text, "the coverage result"


def test_a_skipped_survey_is_visible_in_the_log(caplog):
    import logging

    boot, _, _, _ = build()
    with caplog.at_level(logging.WARNING, logger="Env.Bootstrap"):
        boot.start(physical_ok=False)

    assert "survey skipped" in caplog.text
    assert boot.skipped is True
