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


def _controller(servo):
    ctrl = RevisitController(interest_engine=MagicMock(), servo_ptz=servo,
                             camera_state=MagicMock())
    ctrl._started_at = 1000.0 - 10.0   # inside the 60s startup sweep window
    ctrl._last_move = 0.0              # sweep is due
    ctrl._last_revisit = 0.0           # revisit gate open
    return ctrl


def test_sweep_does_not_level_the_tilt_across_a_detection_gap():
    """A follow has multi-second detector gaps. Levelling the tilt because one
    frame missed the target is what reset it every cycle on hardware."""
    servo = MagicMock(); servo.moving = False; servo.pan = 90; servo.tilt = 110
    ctrl = _controller(servo)
    ctrl._last_track_hit = 1000.0 - 5.0    # tracked 5s ago: past the layer's
                                           # hold, still inside presence
    targets = []
    servo.tilt_to.side_effect = lambda a: targets.append(a)

    ctrl.tick(1000.0, faces=[], objects=[])

    assert 95 not in targets, \
        "the tilt must not be levelled while a target was tracked recently"


def test_sweep_levels_the_tilt_once_presence_lapses():
    servo = MagicMock(); servo.moving = False; servo.pan = 90; servo.tilt = 110
    ctrl = _controller(servo)
    ctrl._last_track_hit = 1000.0 - 20.0   # nothing tracked for 20s
    targets = []
    servo.tilt_to.side_effect = lambda a: targets.append(a)

    # The sweep sets its goal after step() has already run this frame, so the
    # command goes out on the next one. Irrelevant at the 8s sweep cadence.
    ctrl.tick(1000.0, faces=[], objects=[])
    ctrl.tick(1000.2, faces=[], objects=[])

    assert 95 in targets, "an empty room must still scan at level"


def test_sweep_cannot_reset_tilt_onto_a_tracked_target():
    """End to end through tick(): the startup sweep is due and a person is
    vertically off-centre, so tracking moves the tilt. The sweep's tilt→95
    must never reach the servo."""
    servo = MagicMock()
    servo.moving = False
    servo.pan = 90
    servo.tilt = 100
    ctrl = RevisitController(interest_engine=MagicMock(), servo_ptz=servo,
                             camera_state=MagicMock())
    now = 1000.0
    ctrl._started_at = now - 10.0     # inside the 60s startup sweep window
    ctrl._last_move = 0.0             # sweep is due
    ctrl._last_revisit = 0.0          # revisit gate open

    tilt_targets = []
    servo.tilt_to.side_effect = lambda a: tilt_targets.append(a)

    for i in range(4):
        servo.moving = False
        ctrl.tick(now + i * 0.2, faces=FACE_LOW, objects=[])

    assert tilt_targets, "tracking should have moved the tilt"
    assert 95 not in tilt_targets, \
        "the sweep must not reset tilt while a target is being tracked"
