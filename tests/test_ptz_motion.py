"""PTZ Motion Layer: ownership arbitration and rate-limited setpoints.

Hardware context (run 2026-09-24 09:35): tracking converged the tilt onto a
target (95→103→111→114→115) and the exploration path reset it to 95 every
~8s. The tilt never stayed converged, the first command of every cycle
saturated at the ±8° clamp, and the servo was driven down and immediately
back up. 25% of tracking commands were saturated on tilt vs 9% on pan.
"""
import pathlib
import sys
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from runtime.perception.ptz_motion import PtzMotion
from runtime.interest.revisit import RevisitController

FACE_LOW = [{"bbox": {"x": 0, "y": 380, "width": 80, "height": 80},
             "confidence": 0.9}]


def build(max_step=20, track_hold=3.0):
    servo = MagicMock()
    servo.moving = False
    servo.pan = 90
    servo.tilt = 100
    return PtzMotion(servo, max_step=max_step, track_hold=track_hold), servo


def drain(motion, servo, n=10):
    """Run step() until nothing is pending, returning emitted pan targets."""
    seen = []
    for _ in range(n):
        servo.moving = False
        if not motion.step(0.0):
            break
        if servo.pan_to.called:
            seen.append(servo.pan_to.call_args[0][0])
            servo.pan = seen[-1]
    return seen


# ── Ownership ──

def test_explore_is_dropped_while_tracking_holds_the_axes():
    motion, servo = build()
    motion.track(1000.0, tilt=110)          # tracking claims both axes

    assert motion.explore(1000.5, tilt=95) is False, \
        "the periodic tilt→95 reset must not steal the axis mid-follow"
    assert motion.explore_pan_by(40, 1000.5) is False

    motion.step(1000.5)
    assert servo.tilt_to.call_args[0][0] == 110, "explore must not alter the goal"


def test_explore_resumes_once_the_track_hold_expires():
    motion, _ = build(track_hold=3.0)
    motion.track(1000.0, tilt=110)

    assert motion.explore(1002.9, tilt=95) is False
    assert motion.explore(1003.1, tilt=95) is True


def test_wear_protect_is_never_blocked():
    motion, servo = build()
    motion.track(1000.0, tilt=160)

    motion.wear_protect(120)                 # hardware-wear move always wins
    motion.step(1000.0)

    assert servo.tilt_to.call_args[0][0] == 120


# ── Rate limiting / no backlog ──

def test_large_setpoint_is_walked_in_bounded_steps():
    motion, servo = build(max_step=20)
    motion.explore(1000.0, pan=150)          # 90 → 150 in one intent

    seen = drain(motion, servo)

    assert seen == [110, 130, 150], \
        "a 60° intent must not be issued as one 60° step"
    assert not motion.pending()


def test_latest_setpoint_supersedes_a_pending_one():
    motion, servo = build(max_step=20)
    motion.explore(1000.0, pan=150)
    motion.explore(1000.1, pan=110)          # newer intent replaces it

    seen = drain(motion, servo)

    assert seen == [110], "the superseded setpoint must never be executed"
    assert not motion.pending(), "no stale setpoint may be left pending"


def test_nothing_is_queued_while_a_move_is_in_flight():
    motion, servo = build()
    servo.moving = True
    motion.explore(1000.0, pan=150)

    assert motion.step(1000.0) is False
    assert servo.pan_to.call_count == 0, "must not pile commands onto the servo queue"

    servo.moving = False
    assert motion.step(1001.0) is True, "the pending goal must survive, not be lost"


def test_a_setpoint_already_reached_emits_nothing():
    motion, servo = build()
    motion.explore(1000.0, tilt=100)         # servo already at 100
    assert motion.step(1000.0) is False
    assert not motion.pending()


# ── Single writer ──

def test_revisit_writes_movement_only_through_the_motion_layer():
    src = pathlib.Path("runtime/interest/revisit.py").read_text(encoding="utf-8")
    for call in ("pan_to(", "tilt_to(", "pan_relative(", "center()"):
        assert f"_servo_ptz.{call}" not in src, \
            f"revisit.py must not call _servo_ptz.{call} directly"


def _idle_controller(servo):
    """A controller with nothing to stay at and nothing to chase, so tick()
    reaches the explore path — which is where the tilt-levelling guard lives
    now that the startup sweep is gone."""
    engine = MagicMock()
    engine.next_revisit.return_value = None      # no legacy curiosity target
    ctrl = RevisitController(interest_engine=engine, servo_ptz=servo,
                             camera_state=MagicMock())
    ctrl._last_move = 0.0              # an explore turn is due
    ctrl._last_revisit = 0.0           # revisit gate open
    return ctrl


def test_explore_does_not_level_the_tilt_while_a_target_was_tracked_recently():
    """A follow has multi-second detector gaps. Levelling the tilt because one
    frame missed the target is what reset it every cycle on hardware."""
    servo = MagicMock(); servo.moving = False; servo.pan = 90; servo.tilt = 110
    ctrl = _idle_controller(servo)
    ctrl._last_track_hit = 1000.0 - 5.0    # tracked 5s ago: past the layer's
                                           # hold, still inside presence
    targets = []
    servo.tilt_to.side_effect = lambda a: targets.append(a)

    ctrl.tick(1000.0, faces=[], objects=[])
    ctrl.tick(1000.2, faces=[], objects=[])

    assert 95 not in targets, \
        "the tilt must not be levelled while a target was tracked recently"


def test_explore_levels_the_tilt_once_presence_lapses():
    servo = MagicMock(); servo.moving = False; servo.pan = 90; servo.tilt = 110
    ctrl = _idle_controller(servo)
    ctrl._last_track_hit = 1000.0 - 20.0   # nothing tracked for 20s
    targets = []
    servo.tilt_to.side_effect = lambda a: targets.append(a)

    ctrl.tick(1000.0, faces=[], objects=[])
    ctrl.tick(1000.2, faces=[], objects=[])

    assert 95 in targets, "an empty room must still scan at level"


def test_the_revisit_controller_has_no_startup_clock():
    """Startup is the visual-environment bootstrap's lifecycle now, not a
    timer in here: the decision phase cannot depend on how long the controller
    has been running."""
    def phase_at(elapsed):
        servo = MagicMock(); servo.moving = False; servo.pan = 90; servo.tilt = 100
        ctrl = _idle_controller(servo)
        ctrl.tick(1000.0 + elapsed, faces=[], objects=[])
        return (ctrl._last_revisit != 0.0, servo.pan_to.call_count)

    assert phase_at(10.0) == phase_at(200.0), \
        "the decision path must not have a 60-second startup phase"

    src = pathlib.Path("runtime/interest/revisit.py").read_text(encoding="utf-8")
    assert "startup_phase" not in src and "_started_at" not in src, \
        "no implicit startup window may remain in the revisit controller"
    assert "Revisit [sweep]" not in src, "the timed startup sweep is gone"


# ── Startup survey ownership ──

def test_the_survey_owns_both_axes_while_it_runs():
    """Startup survey must not race normal behavior for movement ownership."""
    motion, servo = build()
    motion.survey(1000.0, pan=40, tilt=95)

    motion.track(1000.1, pan=150)          # normal behavior asks for the pan
    motion.step(1000.1)

    # 90 → 40 is a 50° survey move, so the first emitted command is one bounded
    # step (90-20); the point is that it heads for the *survey* viewpoint and
    # never for the 150 the behavior asked for.
    assert servo.pan_to.call_args[0][0] == 70, \
        f"the survey's viewpoint must drive the motion, not the tracking request (got {servo.pan_to.call_args[0][0]})"
    assert motion.blocked_track >= 1, "and the drop must be visible in telemetry"
    assert not motion.tracking_active(1000.2), "a survey is not a tracking session"


def test_explore_is_dropped_while_the_survey_runs():
    motion, _ = build()
    motion.survey(1000.0, pan=40)

    assert motion.explore(1000.1, tilt=95) is False
    assert motion.explore_pan_by(30, 1000.1) is False
    assert motion.blocked_explore > 0


def test_the_survey_walks_a_long_move_in_bounded_steps():
    """The survey is a movement writer like any other: same slew limit."""
    motion, servo = build(max_step=20)
    motion.survey(1000.0, pan=120)
    servo.pan = 10

    seen = []
    servo.moving = False
    for _ in range(12):
        servo.moving = False
        if not motion.step(1000.0):
            break
        seen.append(servo.pan_to.call_args[0][0])
        servo.pan = seen[-1]

    assert seen and all(abs(b - a) <= 20 for a, b in zip([10] + seen, seen)), \
        f"a survey must not be a bang-bang move: {seen}"
    assert seen[-1] == 120


def test_wear_protect_still_preempts_a_survey():
    motion, servo = build()
    motion.survey(1000.0, tilt=160)

    motion.wear_protect(120)
    motion.step(1000.0)

    assert servo.tilt_to.call_args[0][0] == 120, \
        "hardware protection outranks the survey"


def test_behavior_owns_movement_again_after_the_survey_ends():
    motion, servo = build()
    motion.survey(1000.0, pan=40)
    motion.end_survey()

    assert motion.explore(1000.1, pan=60) is True
    motion.step(1000.1)
    assert servo.pan_to.call_args[0][0] == 70, "90 → 60, one bounded step"


def test_ending_the_survey_clears_any_pending_goal():
    """A viewpoint the camera never reached leaves its setpoint pending. Handing
    ownership back must not leave behavior driving toward a pose the survey
    already gave up on."""
    motion, servo = build(max_step=20)
    motion.survey(1000.0, pan=150)      # 90 → 150, so it takes several steps
    motion.step(1000.0)
    assert motion.pending(), "a long survey move is still in progress"

    motion.end_survey()

    assert not motion.pending(), "the survey's stale goal must not outlive the survey"
    assert motion.step(1001.0) is False, "and nothing may still be emitted"
