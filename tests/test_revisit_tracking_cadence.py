"""Tracking cadence must not be bound by the revisit cadence.

Hardware evidence (runtime_20260924_071532.log): PTZ commands were issued
every ~8-9s while tracking, though _track_interval is 1.5s — every
_track_target() call site sat behind the revisit_interval gate in tick().
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from runtime.interest.revisit import RevisitController

# 640x480 frame, centre (320, 240). This face centre is at (40, 240):
# dx = -0.44, far outside the 6% dead zone, so a pan command is expected.
FACE_OFF_CENTRE = [{"bbox": {"x": 0, "y": 200, "width": 80, "height": 80},
                    "confidence": 0.9}]


def build():
    servo = MagicMock()
    servo.moving = False
    servo.pan = 90
    servo.tilt = 100
    ctrl = RevisitController(interest_engine=MagicMock(), servo_ptz=servo,
                             camera_state=MagicMock())
    return ctrl, servo


def commanded(servo):
    return servo.pan_relative.called or servo.tilt_to.called


# ── Tracking: decoupled from the revisit gate ──

def test_tracking_does_not_wait_out_the_revisit_interval():
    ctrl, servo = build()
    now = 1000.0
    ctrl._last_revisit = now          # gate just closed — 8s until it opens

    ctrl.tick(now, faces=FACE_OFF_CENTRE, objects=[])

    assert servo.pan_relative.called, \
        "a visible target must be aimed at on the track interval, not after the revisit gate"


def test_tracking_leaves_the_revisit_clock_alone():
    """Aim adjustments must not consume idle/revisit cadence."""
    ctrl, servo = build()
    now = 1000.0
    ctrl._last_revisit = now

    ctrl.tick(now, faces=FACE_OFF_CENTRE, objects=[])

    assert ctrl._last_revisit == now, "tracking must not reset the revisit clock"


def test_tracking_still_honours_the_track_interval():
    # Keep the revisit gate closed so only the tracking path can command.
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0
    ctrl.tick(1000.0, faces=FACE_OFF_CENTRE, objects=[])
    assert servo.pan_relative.call_count == 1

    servo.moving = False
    ctrl.tick(1001.0, faces=FACE_OFF_CENTRE, objects=[])   # only 1.0s later

    assert servo.pan_relative.call_count == 1, \
        "one command per _track_interval at most"


def test_tracking_resumes_once_the_track_interval_elapses():
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0
    ctrl.tick(1000.0, faces=FACE_OFF_CENTRE, objects=[])
    assert servo.pan_relative.call_count == 1

    servo.moving = False
    ctrl.tick(1001.6, faces=FACE_OFF_CENTRE, objects=[])

    assert servo.pan_relative.call_count == 2


def test_no_command_while_the_camera_is_moving():
    ctrl, servo = build()
    servo.moving = True

    ctrl.tick(1000.0, faces=FACE_OFF_CENTRE, objects=[])

    assert not commanded(servo)


def test_no_command_without_a_face_or_person():
    """Objects alone (a desk, a cup) are not a tracking target."""
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0
    ctrl.tick(1000.0, faces=[], objects=[{"class_name": "cup", "confidence": 0.8}])
    assert not commanded(servo)


# ── Idle / revisit: still on the 8s gate ──

def test_idle_still_returns_at_the_revisit_gate():
    ctrl, servo = build()
    now = 1000.0
    ctrl._last_revisit = now          # gate closed

    ctrl.tick(now, faces=[], objects=[])

    assert not commanded(servo)
    assert ctrl._started_at == 0.0, \
        "with no detections tick must still stop at the revisit gate"


def test_idle_proceeds_once_the_revisit_interval_elapses():
    ctrl, servo = build()
    now = 1000.0
    ctrl._last_revisit = now - 8.0    # gate open

    ctrl.tick(now, faces=[], objects=[])

    assert ctrl._started_at == now, \
        "the revisit path must still open on its 8s cadence"


def test_revisit_interval_is_unchanged():
    ctrl, _ = build()
    assert ctrl.revisit_interval == 8.0
    assert ctrl._track_interval == 1.5
