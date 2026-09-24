"""The curiosity turn paths must reach the motion layer, not raise TypeError.

Crash 2026-09-25 07:35:49 (frames=3104), on hardware, after tracking was lost:

    Commitment RELEASE
    Revisit [leave]: anchor_20_105 tier=idle int=0.130 ... → boring, move on
    Revisit [pick]: entity=ent_5fb39d01(c=0.19,...) → entity
    Revisit [entity]: ent_5fb39d01 (score=0.194, d_pan=-5° → left 5°)
    TypeError: RevisitController._turn() missing 1 required positional argument: 'now'

_turn() gained a `now` argument when it was rerouted through the motion layer
(c9593a8), and two of its five call sites were missed: the entity and legacy
target-turn branches. Neither branch had any test, so the crash waited for the
first time curiosity re-acquired a target after a lost session — the exact
sequence the user hit.

Both branches are covered below, and the structural guard in
test_revisit_call_sites.py covers the rest of the file.
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from tests.ptz_harness import build, face_at, tick


class _Entity:
    """Only the fields the curiosity ranking and the turn path read."""

    def __init__(self, entity_id="ent-1", last_pan=60.0, last_tilt=100.0,
                 interest=0.6, since_last_seen=120.0):
        self.entity_id = entity_id
        self.class_name = "chair"
        self.last_pan = last_pan
        self.last_tilt = last_tilt
        self.interest = interest
        self.since_last_seen = since_last_seen
        self.familiarity_score = 0.0
        self.role_weight = 1.0
        self.consecutive_fails = 0
        self.max_consecutive_fails = 3
        self.confirm_count = 1
        self.seen_count = 5
        self.entity_type = "object"
        self.curiosity_score = 0.0
        self.last_seen = 1000.0

    def record_revisit_attempt(self):
        pass


class _EntityRegistry:
    def __init__(self, entities):
        self._entities = entities

    def top_entities(self, n):
        return self._entities[:n]


def no_session_controller():
    """A controller with nothing to stay at and no commitment: the state the
    stack is in right after tracking is lost and the stay goes boring."""
    ctrl, servo = build()
    ctrl._anchor_manager = None          # no stay → fall through to target selection
    assert not ctrl._commitment_engine.has_commitment
    return ctrl, servo


def test_the_entity_turn_path_reaches_the_motion_layer():
    ctrl, servo = no_session_controller()
    ctrl._entity_registry = _EntityRegistry([_Entity(last_pan=60.0)])   # d_pan = -30°

    tick(ctrl, servo, 1000.2, face_at(0.0))

    assert ctrl._motion.pending(), "the entity turn must be queued with the motion layer"
    assert ctrl._last_move == 1000.2, "and it must consume the movement clock"

    tick(ctrl, servo, 1000.4, face_at(0.0))     # the motion layer emits it

    assert servo.pan_to.called, "the queued turn must reach the servo"
    assert servo.pan_to.call_args[0][0] == 70, "30° left, walked in ≤20° steps"


def test_the_legacy_turn_path_reaches_the_motion_layer():
    ctrl, servo = no_session_controller()
    legacy = MagicMock()
    legacy.curiosity_score = 0.5
    legacy.location = (60.0, 100.0)                                     # d_pan = -30°
    legacy.target_id = "legacy-1"
    ctrl._engine.next_revisit.return_value = legacy

    tick(ctrl, servo, 1000.2, face_at(0.0))

    assert ctrl._motion.pending(), "the legacy turn must be queued with the motion layer"

    tick(ctrl, servo, 1000.4, face_at(0.0))

    assert servo.pan_to.called
    assert servo.pan_to.call_args[0][0] == 70


def test_a_small_pan_offset_does_not_turn():
    """|d_pan| <= 3 is deliberately below the turn threshold."""
    ctrl, servo = no_session_controller()
    ctrl._entity_registry = _EntityRegistry([_Entity(last_pan=88.0)])   # d_pan = -2°

    tick(ctrl, servo, 1000.2, face_at(0.0))
    tick(ctrl, servo, 1000.4, face_at(0.0))

    assert not servo.pan_to.called
