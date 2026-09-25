"""Shared harness for the PTZ revisiting tests.

The framing tests and the cadence tests both need a controller, a servo mock
and — since this phase — a way to open a real tracking session. Sessions are
opened by the revisit/commitment flow and by nothing else, so the tests have to
drive that flow rather than hand the controller a target.
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from runtime.interest.revisit import RevisitController

FRAME_W, FRAME_H = 640, 480


def face_at(dx: float, dy: float = 0.0, size: int = 64):
    """A face bbox whose centre sits at (dx, dy) in normalised frame offsets.

    dx/dy use the same convention the framing code does: 0.0 is frame centre,
    negative is left/above, and one unit is the full frame width/height.
    """
    cx = FRAME_W / 2 + dx * FRAME_W
    cy = FRAME_H / 2 + dy * FRAME_H
    return [{"bbox": {"x": cx - size / 2, "y": cy - size / 2,
                      "width": size, "height": size},
             "confidence": 0.9}]


def person_at(dx: float, dy: float = 0.0, size: int = 120):
    """A YOLO person detection centred at (dx, dy) in normalised offsets."""
    cx = FRAME_W / 2 + dx * FRAME_W
    cy = FRAME_H / 2 + dy * FRAME_H
    return {"class_name": "person", "confidence": 0.9,
            "center_x": cx, "center_y": cy,
            "bbox": {"x": cx - size / 2, "y": cy - size / 2,
                     "width": size, "height": size}}


class Anchor:
    """Only the fields the stay path reads off a SpatialAnchor."""

    def __init__(self, pan=90.0, interest=0.30, objects=("cup", "bottle")):
        self.anchor_id = "test-anchor"
        self.pan = pan
        self.interest = interest
        self.baseline_objects = set(objects)
        self.barren = False
        self.suppressed = False


class AnchorManager:
    def __init__(self, anchors=None):
        self._anchors = [Anchor()] if anchors is None else anchors

    def all_anchors(self):
        return self._anchors


def build(now: float = 1000.0, servo=None):
    """A controller with the revisit gate open and an anchor worth staying at —
    everything the stay path needs. There is no startup window to get past any
    more: startup is the visual-environment bootstrap's lifecycle, and when a
    survey owns the axes it does so through the Motion Layer."""
    if servo is None:
        servo = MagicMock()
        servo.moving = False
        servo.pan = 90
        servo.tilt = 100
    ctrl = RevisitController(interest_engine=MagicMock(), servo_ptz=servo,
                             camera_state=MagicMock())
    ctrl._engine.next_revisit.return_value = None
    ctrl._last_revisit = 0.0           # revisit gate open
    ctrl._anchor_manager = AnchorManager()
    return ctrl, servo


def open_session(ctrl, servo, now: float = 1000.0):
    """Drive the real revisit/commitment flow once into a tracking session."""
    ctrl.tick(now, faces=face_at(0.0), objects=[])
    assert ctrl._commitment_engine.has_commitment, \
        "the revisit/commitment flow failed to open a session"
    servo.moving = False
    servo.pan_to.reset_mock()
    servo.tilt_to.reset_mock()


def tick(ctrl, servo, now: float, faces, objects=(), frame_age: float = 0.0):
    """One frame with the camera free, so only framing may command.

    frame_age is how old the frame was when the controller saw it — the time
    the loop spent detecting. Zero means "seen the instant it was captured".
    """
    servo.moving = False
    ctrl.tick(now, faces=faces, objects=list(objects), frame_age=frame_age)


def commanded(servo) -> bool:
    """Any emitted movement: the motion layer writes via pan_to/tilt_to."""
    return servo.pan_to.called or servo.tilt_to.called
