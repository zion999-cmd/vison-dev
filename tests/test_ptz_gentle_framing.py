"""Gentle framing: three independent knobs, not one coupled threshold.

The first cut of this used a single band to decide three things at once: when
to start moving, how big the correction was, and where the target came to rest.
Hardware caught it — a 192 px trigger, a 0.5 gain and a target parked 96 px off
centre, i.e. "it does not react, and when it does it barely moves".

The three roles are now separate knobs:

    start_offset — nothing moves below it (small head/body movement is free)
    aim_offset   — where a triggered correction takes the target: the residual
                   it leaves. Not the centre, not the start offset.
    gain         — how much of the remaining excess one update removes. 1.0 is
                   the largest value that cannot overshoot the aim point, and
                   the value at which the aim offset is actually reached.

A single threshold cannot serve all three: a large comfort zone would then also
force a late trigger and a weak correction, which is exactly what happened.
"""
import math
import sys

sys.path.insert(0, '.')

from tests.ptz_harness import (build, commanded, face_at, open_session, tick)

# The contract, as numbers rather than as references to the module.
START_X, AIM_X = 0.15, 0.06     # pan: engage past 0.15, drive to 0.06
START_Y, AIM_Y = 0.20, 0.08     # tilt
GAIN = 1.0
FOV_H = 55.0                    # degrees across the frame width
H_W = 480 / 640
ROUNDING = 0.5 / FOV_H          # the servo takes whole degrees


def pan_law(dx, gain=GAIN):
    """The pan correction the controller should emit for this signed offset.

    Pan increases as the target moves left (dx negative), so the sign flips.
    """
    excess = math.copysign(max(0.0, abs(dx) - AIM_X), dx)
    return max(-15, min(15, -int(round(excess * FOV_H * gain))))


def tilt_law(dy, gain=GAIN):
    """Tilt increases as the target moves below centre, so the sign does not."""
    excess = math.copysign(max(0.0, abs(dy) - AIM_Y), dy)
    return max(-8, min(8, int(round(excess * FOV_H * H_W * gain))))


def pan_command(servo, base=90):
    assert servo.pan_to.call_count == 1, "expected exactly one pan command"
    return servo.pan_to.call_args[0][0] - base


# ── The knobs are three, and independent ──

def test_the_three_knobs_are_distinct_values():
    ctrl, _ = build()
    assert ctrl._framing_start_x == START_X
    assert ctrl._framing_aim_x == AIM_X
    assert ctrl._framing_start_y == START_Y
    assert ctrl._framing_aim_y == AIM_Y
    assert ctrl._framing_gain == GAIN

    assert AIM_X < START_X and AIM_Y < START_Y, \
        "the aim offset must sit clearly inside the start offset"
    assert GAIN <= 1.0, "a gain above 1 overshoots the aim point"


def test_start_offset_only_decides_when_to_move():
    """Moving the start offset must not change the correction for a given
    offset — it is the trigger, not the magnitude and not the resting point."""
    ctrl, servo = build()
    open_session(ctrl, servo)
    tick(ctrl, servo, 1000.2, face_at(-0.20))
    baseline = pan_command(servo)

    ctrl2, servo2 = build()
    ctrl2._framing_start_x = 0.05            # engages much earlier
    open_session(ctrl2, servo2)
    tick(ctrl2, servo2, 1000.2, face_at(-0.20))

    assert pan_command(servo2) == baseline, \
        "the start offset must not scale the correction"


def test_gain_only_decides_how_hard_the_correction_pushes():
    ctrl, servo = build()
    ctrl._framing_gain = 0.5                 # half the excess per update
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.20))

    assert pan_command(servo) == pan_law(-0.20, gain=0.5) == 4


# ── Comfort zone: the camera stays still ──

def test_small_head_movement_does_not_move_the_camera():
    ctrl, servo = build()
    open_session(ctrl, servo)

    for i, (dx, dy) in enumerate(((0.05, 0.0), (-0.10, 0.04),
                                  (0.14, -0.05), (-0.13, 0.10))):
        tick(ctrl, servo, 1000.2 + i * 0.4, face_at(dx, dy))

    assert not commanded(servo), \
        "movement inside the start offset must leave the PTZ still"


def test_activity_inside_the_comfort_zone_never_moves_the_camera():
    ctrl, servo = build()
    open_session(ctrl, servo)

    for i in range(12):                      # drift back and forth across it
        dx = 0.14 if i % 2 else -0.14
        tick(ctrl, servo, 1000.2 + i * 0.4, face_at(dx))

    assert not commanded(servo)


def test_each_axis_has_its_own_start_offset():
    """Below the pan start nothing pans, even while the tilt is correcting."""
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(0.10, 0.30))

    assert not servo.pan_to.called, "0.10 is inside the pan start offset"
    assert servo.tilt_to.called, "0.30 is past the tilt start offset"


# ── Triggered: a prompt, decisive correction ──

def test_crossing_the_start_offset_removes_the_whole_excess_over_the_aim():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.20))

    assert pan_command(servo) == pan_law(-0.20) == 8, \
        "one update should take the target from the start offset to the aim offset"


def test_the_tilt_correction_matches_the_same_law():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(0.0, 0.28))

    assert servo.tilt_to.call_args[0][0] - 100 == tilt_law(0.28) == 8


def test_the_safety_clamp_is_reachable_again():
    """The first cut could never reach its own clamps: the largest correction
    was 9.6 deg pan / 6.2 deg tilt, so there was no headroom to catch up."""
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.45))

    assert pan_command(servo) == 15, "a large displacement must reach the pan clamp"


def test_the_correction_never_overshoots_the_aim_point():
    """No A-to-B-to-A: correcting past the aim point is what makes a camera
    oscillate around a target."""
    for dx in (0.16, 0.20, 0.25, 0.30, 0.40, 0.45, -0.20, -0.35):
        ctrl, servo = build()
        open_session(ctrl, servo)
        tick(ctrl, servo, 1000.2, face_at(dx))

        moved = abs(pan_command(servo)) / FOV_H      # fraction of the width
        residual = abs(dx) - moved
        assert residual >= AIM_X - ROUNDING, (
            f"dx={dx}: correction {moved:.3f} left {residual:.3f}, "
            f"past the aim offset {AIM_X} (a whole-degree servo may overshoot "
            f"by up to {ROUNDING:.3f})")


# ── Hysteresis between the two offsets ──

def test_a_running_follow_keeps_correcting_between_the_two_offsets():
    ctrl, servo = build()
    open_session(ctrl, servo)

    # Between the offsets with nothing running → still.
    tick(ctrl, servo, 1000.2, face_at(-0.10))
    assert not commanded(servo)

    # Engage the follow, then come back inside the start offset → it continues
    # to the aim offset rather than stopping dead at the start offset.
    tick(ctrl, servo, 1002.0, face_at(-0.45))
    assert servo.pan_to.call_count == 1
    tick(ctrl, servo, 1004.0, face_at(-0.10))
    assert servo.pan_to.call_count == 2, \
        "a running follow must keep correcting between the two offsets"

    # At the aim offset → the follow ends.
    tick(ctrl, servo, 1006.0, face_at(-0.04))
    assert servo.pan_to.call_count == 2

    # And it stays ended at an offset inside the start offset.
    tick(ctrl, servo, 1008.0, face_at(-0.10))
    assert servo.pan_to.call_count == 2, \
        "inside the start offset a stopped camera must not start hunting"


def test_the_follow_stops_at_the_aim_offset_not_at_the_centre():
    ctrl, servo = build()
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.45))
    assert servo.pan_to.call_count == 1

    # Settled inside the aim offset → nothing further, and no drive to centre.
    tick(ctrl, servo, 1002.0, face_at(-0.04))

    assert servo.pan_to.call_count == 1, \
        "once the target is inside the aim offset the camera must stop"


# ── Session lifetime (unchanged by this fix) ──

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

def test_the_offsets_are_configurable():
    ctrl, servo = build()
    ctrl._framing_start_x = 0.30             # a much larger comfort zone
    open_session(ctrl, servo)

    tick(ctrl, servo, 1000.2, face_at(-0.20))
    assert not servo.pan_to.called, "inside the configured start offset"

    tick(ctrl, servo, 1002.0, face_at(-0.40))
    assert pan_law(-0.40) == 15 and servo.pan_to.called
