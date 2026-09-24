"""Tracking cadence must not be bound by the revisit cadence.

Hardware evidence (runtime_20260924_071532.log): PTZ commands were issued
every ~8-9s while tracking, though _track_interval is 1.5s — every
_track_target() call site sat behind the revisit_interval gate in tick().

The decoupling is conditional, and deliberately so: a detection does not open
a tracking session (that is the revisit/commitment flow's decision), but once
one is open its movement updates run on the track interval, whatever the
revisit gate is doing.
"""
import sys

sys.path.insert(0, '.')

from tests.ptz_harness import (build, commanded, face_at, open_session, tick)

OFF_CENTRE = -0.44          # far outside the framing comfort zone


# ── An open session: decoupled from the revisit gate ──

def test_an_open_session_aims_before_the_revisit_gate_opens():
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)
    assert ctrl._last_revisit == 1000.0, "gate just closed — 8s until it opens"

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))

    assert servo.pan_to.called, \
        "an open session must aim on the track interval, not after the revisit gate"


def test_tracking_leaves_the_revisit_clock_alone():
    """Aim adjustments must not consume idle/revisit cadence."""
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))

    assert ctrl._last_revisit == 1000.0, "tracking must not reset the revisit clock"


def test_tracking_still_honours_the_track_interval():
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))
    assert servo.pan_to.call_count == 1

    tick(ctrl, servo, 1002.6, face_at(OFF_CENTRE))        # only 1.0s later

    assert servo.pan_to.call_count == 1, \
        "one command per _track_interval at most"


def test_tracking_resumes_once_the_track_interval_elapses():
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))
    assert servo.pan_to.call_count == 1

    tick(ctrl, servo, 1003.2, face_at(OFF_CENTRE))

    assert servo.pan_to.call_count == 2


def test_no_command_while_the_camera_is_moving():
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)
    servo.moving = True

    ctrl.tick(1001.6, faces=face_at(OFF_CENTRE), objects=[])

    assert not commanded(servo)


def test_no_command_without_a_face_or_person():
    """Objects alone (a desk, a cup) are not a tracking target."""
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    ctrl.tick(1001.6, faces=[], objects=[
        {"class_name": "cup", "confidence": 0.8,
         "bbox": {"x": 0, "y": 0, "width": 40, "height": 40}}])

    assert not commanded(servo)


# ── Idle / revisit: still on the 8s gate ──

def test_idle_still_returns_at_the_revisit_gate():
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0       # gate closed, no session
    ctrl._started_at = 0.0

    ctrl.tick(1000.0, faces=[], objects=[])

    assert not commanded(servo)
    assert ctrl._started_at == 0.0, \
        "with no detections tick must still stop at the revisit gate"


def test_idle_proceeds_once_the_revisit_interval_elapses():
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0 - 8.0   # gate open
    ctrl._started_at = 0.0              # startup clock not started yet

    ctrl.tick(1000.0, faces=[], objects=[])

    assert ctrl._started_at == 1000.0, \
        "the revisit path must still open on its 8s cadence"


def test_revisit_interval_is_unchanged():
    ctrl, _ = build()
    assert ctrl.revisit_interval == 8.0
    assert ctrl._track_interval == 1.5
