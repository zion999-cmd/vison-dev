"""Gentle framing: keep-in-frame with hysteresis, not centre-lock.

Centre-lock (the old 6% dead zone + gain 1.0) drove the target to the middle
of the frame on every adjustment: any head movement, however small, produced a
servo command, and a target that drifted near an edge was yanked all the way
back. On hardware that shows up as continuous small movement of the PTZ while
the person is basically still — and every one of those moves blurs the frame
and disturbs the detector that is feeding the loop.

Gentle framing replaces it with a large comfort zone, an outer edge that starts
a follow, an inner edge that stops it, and a correction that removes only part
of the excess and aims at the inner edge rather than the centre.
"""
import sys

sys.path.insert(0, '.')

from tests.ptz_harness import (build, commanded, face_at, open_session, tick)


# ── Comfort zone: the camera stays still ──

def test_small_head_movement_does_not_move_the_camera():
    ctrl, servo = build()
    open_session(ctrl, servo)

    for i, dx in enumerate((0.05, -0.10, 0.20, -0.28)):
        tick(ctrl, servo, 1000.2 + i * 0.4, face_at(dx))

    assert not commanded(servo), \
        "movement inside the comfort zone must leave the PTZ still"


def test_activity_inside_the_comfort_zone_never_moves_the_camera():
    ctrl, servo = build()
    open_session(ctrl, servo)

    for i in range(12):                      # drift back and forth across it
        dx = 0.28 if i % 2 else -0.28
        tick(ctrl, servo, 1000.2 + i * 0.4, face_at(dx))

    assert not commanded(servo)


# ── Outer edge: a follow starts, gradually ──

def test_the_outer_edge_starts_a_gradual_follow():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.45))

    assert servo.pan_to.call_count == 1
    delta = servo.pan_to.call_args[0][0] - 90
    assert 0 < delta <= 12, (
        f"a follow must pull the target back into frame gradually, "
        f"not jump toward the centre (moved {delta}°)")


def test_the_tilt_follows_only_at_the_vertical_edge():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(0.0, 0.25))
    assert not servo.tilt_to.called, "vertical offset inside the zone is ignored"

    tick(ctrl, servo, 1002.0, face_at(0.0, 0.45))
    assert servo.tilt_to.called
    delta = servo.tilt_to.call_args[0][0] - 100
    assert 0 < delta <= 8


# ── Inner edge: the follow stops, and does not chase the centre ──

def test_the_follow_stops_inside_the_frame_and_does_not_centre():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.45))
    assert servo.pan_to.call_count == 1
    moved_to = servo.pan_to.call_args[0][0]

    # The camera moved, so the target is now back near the frame edge.
    servo.pan = moved_to
    tick(ctrl, servo, 1002.0, face_at(-0.10))

    assert servo.pan_to.call_count == 1, \
        "once the target is inside the frame the camera must stop, not centre it"


def test_a_running_follow_keeps_correcting_between_the_two_edges():
    """The gap between the inner and outer edges is the hysteresis band."""
    ctrl, servo = build()
    open_session(ctrl, servo)

    # Between the edges with nothing running → still.
    tick(ctrl, servo, 1000.2, face_at(-0.22))
    assert not commanded(servo)

    # Start a follow, then present the same offset → it keeps going.
    tick(ctrl, servo, 1002.0, face_at(-0.45))
    assert servo.pan_to.call_count == 1
    tick(ctrl, servo, 1004.0, face_at(-0.22))
    assert servo.pan_to.call_count == 2, \
        "a follow already running must keep correcting between the two edges"

    # Back inside the inner edge → the follow stops.
    tick(ctrl, servo, 1006.0, face_at(-0.10))
    assert servo.pan_to.call_count == 2

    # And it stays stopped at an offset that is inside the outer edge.
    tick(ctrl, servo, 1008.0, face_at(-0.22))
    assert servo.pan_to.call_count == 2, \
        "below the outer edge a stopped camera must not start hunting"


# ── Session lifetime ──

def test_the_session_is_not_opened_by_a_detection_alone():
    ctrl, servo = build()
    ctrl._last_revisit = 1000.0        # revisit gate closed for the whole test

    for i in range(10):
        tick(ctrl, servo, 1000.0 + i * 0.4, face_at(-0.45))

    assert not commanded(servo), \
        "seeing a face is not by itself a reason to start following it"
    assert not ctrl._commitment_engine.has_commitment


def test_the_session_ends_when_the_target_stops_being_seen():
    ctrl, servo = build()
    open_session(ctrl, servo)

    assert ctrl._tracking_session_active(1010.0)

    tick(ctrl, servo, 1016.0, [])

    assert not ctrl._tracking_session_active(1016.0), \
        "an open session must not outlive its target"


def test_a_detector_gap_does_not_end_the_session():
    """YuNet misses a third of the frames a person is visible in."""
    ctrl, servo = build()
    open_session(ctrl, servo)

    for i in range(5):                 # ~2.4s with nothing detected
        tick(ctrl, servo, 1000.5 + i * 0.6, [])

    assert ctrl._tracking_session_active(1002.9)


def test_objects_alone_are_never_framed():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, [])
    ctrl.tick(1000.4, faces=[], objects=[
        {"class_name": "cup", "confidence": 0.9,
         "bbox": {"x": 0, "y": 0, "width": 40, "height": 40}}])

    assert not commanded(servo)


# ── Tunability ──

def test_the_comfort_zone_is_configurable():
    ctrl, servo = build()
    ctrl._framing_outer_x = 0.10
    ctrl._framing_inner_x = 0.05
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.20))

    assert servo.pan_to.called, \
        "an offset outside the configured outer edge must start a follow"
