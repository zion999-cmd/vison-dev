"""An anchor-level judgement must not tear down a person-level commitment.

Hardware 2026-09-24 15:5x (runtime_20260924_155258.log), t=170s:

    Flat interest: anchor_80_90 stuck at 0.100 for 89s — likely empty wall,
    moving on

The anchor was indeed boring. The person standing in front of the camera was
not — but the `should_leave` branch called CommitmentEngine.reset() anyway, so
the tracking session died at t=170 and only came back at t=186 when the camera
happened to re-qualify an anchor. In between, 92 of 97 detection frames had the
target far outside the framing comfort zone and the camera did nothing, and the
explore path turned it 30° away from the user.

Anchor boredom and target departure are different questions. `should_leave` may
end the stay — it already zeroes the anchor's interest, which is what actually
takes the anchor out of the stay candidates. Clearing the commitment is not its
call: that belongs to the person being watched, and it still ends on its own
terms (lost / stale / timeout / the existing release semantics).
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from runtime.interest.verifier import Verdict
from tests.ptz_harness import (Anchor, AnchorManager, build, face_at,
                               open_session, tick)

STAY_AGE = 200.0          # > _vlm_empty_check_s (90s) and > the 120s sparse branch
FLAT_AGE = 100.0          # > the 60s flat-interest debounce


def primed(interest=0.30, objects=("cup", "bottle")):
    """A controller holding a session with a person in view, sitting at an
    anchor that is about to be judged boring."""
    ctrl, servo = build()
    anchor = Anchor(interest=interest, objects=objects)
    ctrl._anchor_manager = AnchorManager([anchor])
    open_session(ctrl, servo, 1000.0)
    assert ctrl._commitment_engine.has_commitment, "precondition: a live session"
    return ctrl, servo, anchor


def make_boring(ctrl, flat=True):
    """Move the anchor to the brink of the should_leave branch, with the
    revisit gate open so the stay check is actually reached."""
    ctrl._staying_since = 1000.0 - STAY_AGE
    ctrl._flat_interest_since = (1000.0 - FLAT_AGE) if flat else 0.0
    ctrl._last_revisit = 0.0


# ── The ownership contract ──

def test_a_flat_interest_anchor_does_not_reset_a_live_person_commitment():
    ctrl, servo, anchor = primed()
    make_boring(ctrl)

    tick(ctrl, servo, 1000.2, face_at(0.0))

    assert anchor.interest == 0.0, "the anchor must still be judged boring"
    assert ctrl._commitment_engine.has_commitment, \
        "an anchor going boring must not clear the person commitment"


def test_a_sparse_class_anchor_does_not_reset_a_live_person_commitment():
    """The same branch, reached through the sparse-suspect-class heuristic."""
    ctrl, servo, anchor = primed(objects=("cup",))
    make_boring(ctrl, flat=False)

    tick(ctrl, servo, 1000.2, face_at(0.0))

    assert anchor.interest == 0.0
    assert ctrl._commitment_engine.has_commitment


def test_a_vlm_trivial_anchor_does_not_reset_a_live_person_commitment():
    """The same branch, reached through the verifier calling the anchor trivial."""
    ctrl, servo, anchor = primed(objects=("cup", "bottle", "chair"))
    make_boring(ctrl, flat=False)
    ctrl._last_vlm_check = 0.0
    ctrl._verifier = MagicMock()
    ctrl._verifier.verify.return_value = (Verdict.TRIVIAL, "a blank wall")

    servo.moving = False
    ctrl.tick(1000.2, faces=face_at(0.0), objects=[], frame=object())

    assert anchor.suppressed, "the anchor must still be suppressed"
    assert anchor.interest == 0.0
    assert ctrl._commitment_engine.has_commitment, \
        "a VLM judgement about the anchor must not clear the person commitment"


def test_the_surviving_session_keeps_framing_the_person():
    """The commitment is not merely present — it still does its job."""
    ctrl, servo, anchor = primed()
    make_boring(ctrl)
    tick(ctrl, servo, 1000.2, face_at(0.0))
    servo.pan_to.reset_mock()

    tick(ctrl, servo, 1002.0, face_at(-0.25))

    assert ctrl._tracking_session_active(1002.0), "the session must still be open"
    assert servo.pan_to.call_count == 1
    assert servo.pan_to.call_args[0][0] - 90 == 10, \
        "the move must be the framing correction for dx=-0.25, not an explore turn"


# ── The anchor's own leave behaviour is unchanged ──

def test_the_left_anchor_still_ends_its_stay():
    ctrl, servo, anchor = primed()
    make_boring(ctrl)

    tick(ctrl, servo, 1000.2, face_at(0.0))

    assert anchor.interest == 0.0
    assert ctrl._staying_since == 0.0, "the stay on that anchor must end"


def test_the_left_anchor_is_not_entered_again():
    ctrl, servo, anchor = primed()
    make_boring(ctrl)
    tick(ctrl, servo, 1000.2, face_at(0.0))

    for i in range(4):
        tick(ctrl, servo, 1002.0 + i * 0.4, face_at(0.0))

    assert ctrl._staying_since == 0.0, \
        "an anchor with no interest left must not become a stay candidate again"
    assert anchor.interest == 0.0


# ── The commitment is not immortal: it still ends by its own semantics ──

def test_presence_loss_still_releases_the_commitment():
    ctrl, servo, anchor = primed()
    make_boring(ctrl)
    tick(ctrl, servo, 1000.2, face_at(0.0))
    assert ctrl._commitment_engine.has_commitment

    tick(ctrl, servo, 1030.0, [])      # nothing seen for well past PRESENCE_WINDOW

    assert not ctrl._commitment_engine.has_commitment, \
        "losing the person must still release the commitment"
