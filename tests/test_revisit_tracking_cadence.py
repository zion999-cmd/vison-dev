"""Tracking cadence must not be bound by the revisit cadence.

Hardware evidence (runtime_20260924_071532.log): PTZ commands were issued
every ~8-9s while tracking, though _track_interval is 1.5s — every
_track_target() call site sat behind the revisit_interval gate in tick().

Three cadences are now separate, and the tests below pin each one:

  session   — opened only by the revisit/commitment flow, never by a detection
  decision  — _track_interval (1.5s): may a follow start? Measured from the last
              emitted correction, so after a follow ends the controller looks
              again at most 1.5s later
  execution — a follow that is already engaged refreshes its motion goal on
              every valid observation. Capping this at 1.5s is what limited the
              chase to 10 deg/s pan / 5.33 deg/s tilt and let a walking person
              outrun the camera (runtime_20260924_172825.log, segment t=228-240s:
              target 10.7 deg/s median, demand 9.8 deg/s, 1.71s updates,
              200px median / 309px peak trailing error).
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


def test_an_engaged_follow_updates_faster_than_the_decision_cadence():
    """The decision cadence must not cap the motion update of a live follow."""
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))        # engages the follow
    assert servo.pan_to.call_count == 1

    tick(ctrl, servo, 1002.6, face_at(OFF_CENTRE))        # only 1.0s later

    assert servo.pan_to.call_count == 2, \
        "an engaged follow must refresh its goal at the observation rate"


def test_the_decision_cadence_still_gates_re_engagement():
    """After a follow ends, the 1.5s decision cadence still applies."""
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)

    tick(ctrl, servo, 1001.6, face_at(OFF_CENTRE))        # engage; _last_track set
    tick(ctrl, servo, 1002.0, face_at(-0.04))             # inside the aim zone -> stop
    assert not ctrl._following()
    servo.pan_to.reset_mock()

    tick(ctrl, servo, 1002.6, face_at(OFF_CENTRE))        # 1.0s after the last move
    assert not commanded(servo), \
        "inside the cadence window the decision is not re-taken"

    tick(ctrl, servo, 1003.4, face_at(OFF_CENTRE))        # 1.8s after the last move
    assert servo.pan_to.called, "past the cadence the decision is taken again"


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


# ── The decision body must not run at frame rate under a live commitment ──

def test_an_active_commitment_does_not_arbitrate_at_frame_rate():
    """A live commitment must not turn the decision body into a per-frame loop.

    Hardware 2026-09-25: 21 Revisit [pick] events and 21 Commitment HOLD blocks
    inside 10 seconds. The active-commitment path returned through
    `_track_target(now)` without advancing `_last_revisit`, so the Revisit gate
    never re-armed and the whole decision body — Commitment arbitration and its
    HOLD telemetry included — ran once per frame. Every decision count in the
    telemetry was therefore a frame count, not a decision count.
    """
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)        # the stay path establishes the session
    ctrl._anchor_manager = None              # nothing to stay at → the commitment path
    ctrl._last_revisit = 0.0
    before = ctrl.commitment_telemetry.hold_count

    for i in range(40):                      # 40 frames × 0.2s = 8s: inside the gate
        tick(ctrl, servo, 1000.2 + i * 0.2, face_at(0.0))

    held = ctrl.commitment_telemetry.hold_count - before
    assert held == 1, \
        f"the decision body ran {held} times in 8s — it must run once per revisit gate"

    tick(ctrl, servo, 1008.4, face_at(0.0))  # past the gate
    assert ctrl.commitment_telemetry.hold_count - before == 2, \
        "and it must run again once the cadence lapses"


def test_following_still_updates_faster_than_the_decision_cadence():
    """The other two timescales stay independent: arming the decision gate must
    not throttle an active follow (23b7f5b)."""
    ctrl, servo = build()
    open_session(ctrl, servo, 1000.0)
    ctrl._anchor_manager = None
    ctrl._last_revisit = 0.0

    tick(ctrl, servo, 1000.2, face_at(-0.30))    # crosses start_offset → follow engages
    assert ctrl._following(), "crossing the start offset must engage a follow"
    assert servo.pan_to.call_count == 1
    held = ctrl.commitment_telemetry.hold_count

    for i in range(3):                           # 0.4s apart: inside the 8s decision window
        tick(ctrl, servo, 1000.6 + i * 0.4, face_at(-0.40))

    assert servo.pan_to.call_count == 4, \
        "the follow must refresh its motion goal on every observation, decision cadence or not"
    assert ctrl.commitment_telemetry.hold_count == held, \
        "and none of those motion updates may re-run the decision body"
