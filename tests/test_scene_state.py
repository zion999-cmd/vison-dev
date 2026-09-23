"""Tests for L3 scene state machine."""
import sys
sys.path.insert(0, '.')
from runtime.scene.state import SceneState, RuntimeState


class TestSceneState:
    def test_initial_state_is_idle(self):
        ss = SceneState()
        assert ss.runtime_state == RuntimeState.IDLE

    def test_user_entered_transitions_to_focus(self):
        ss = SceneState()
        ss.update(people=[{"bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}])
        assert ss.runtime_state == RuntimeState.FOCUS

    def test_user_left_transitions_to_idle_after_debounce(self):
        import time
        ss = SceneState()
        ss.update(people=[{"bbox": {}}])  # enter
        assert ss.runtime_state == RuntimeState.FOCUS
        ss.update(people=[])  # leave — debounce started
        assert ss.runtime_state == RuntimeState.FOCUS  # still focus (debouncing)
        # Simulate debounce expiry
        ss._user_vanished_at -= 999  # make it look like 999s ago
        ss.update(people=[])  # debounce expired → transition
        assert ss.runtime_state == RuntimeState.IDLE

    def test_leave_debounce_cancelled_on_reappear(self):
        ss = SceneState()
        ss.update(people=[{"bbox": {}}])  # enter → focus
        ss.update(people=[])              # vanish → start debounce
        assert ss._user_vanished_at > 0   # timer running
        ss.update(people=[{"bbox": {}}])  # reappear → cancel
        assert ss._user_vanished_at == 0
        assert ss.runtime_state == RuntimeState.FOCUS

    def test_no_transition_when_stable(self):
        ss = SceneState()
        ss.update(motion_level=0.3)
        assert ss.runtime_state == RuntimeState.IDLE  # small motion, no transition

    def test_motion_smoothing(self):
        ss = SceneState()
        ss.update(motion_level=0.0)
        ss.update(motion_level=1.0)
        state = ss.get()
        assert 0.25 < state["motion_level"] < 0.35  # 0.0*0.7 + 1.0*0.3 = 0.3

    def test_user_present_flag(self):
        ss = SceneState()
        assert ss.user_present is False
        ss.update(people=[{"bbox": {}}])
        assert ss.user_present is True

    def test_attention_multiplier_by_state(self):
        ss = SceneState()
        assert ss.attention_multiplier == 1.0  # IDLE
        ss.update(people=[{"bbox": {}}])  # → FOCUS
        assert ss.attention_multiplier == 0.7

    def test_desk_changed_requires_sustained_anchor_novelty(self):
        """desk_changed tracks the anchor baseline (anchor_novelty), not the
        object list, and needs 2 consecutive novel frames before it latches."""
        ss = SceneState()
        ss.update(anchor_novelty=0.5)
        assert ss.get()["desk_changed"] is False  # first novel frame — not yet
        ss.update(anchor_novelty=0.5)
        assert ss.get()["desk_changed"] is True   # sustained novelty → latched

    def test_desk_changed_auto_clears_when_novelty_drops(self):
        ss = SceneState()
        ss.update(anchor_novelty=0.5)
        ss.update(anchor_novelty=0.5)
        assert ss.get()["desk_changed"] is True
        ss.update(anchor_novelty=0.1)
        assert ss.get()["desk_changed"] is False

    def test_desk_changed_is_driven_by_novelty_not_object_churn(self):
        """Object churn must not be sufficient for desk_changed, but the same
        object stream plus real novelty must still latch — otherwise churn
        made the flag flicker on every misclassification."""
        ss = SceneState()
        churn = [
            [{"class_name": "cup"}],
            [{"class_name": "book"}],
            [{"class_name": "cup"}, {"class_name": "book"}],
            [{"class_name": "phone"}],
        ]
        for objects in churn:
            ss.update(objects=objects, anchor_novelty=0.0)
        assert ss.get()["desk_changed"] is False  # churn alone never latches

        ss.update(objects=churn[0], anchor_novelty=0.5)
        ss.update(objects=churn[1], anchor_novelty=0.5)
        assert ss.get()["desk_changed"] is True  # novelty still drives it

    def test_desk_changed_latches_under_production_call_pattern(self):
        """Regression: main.py calls update() twice per frame.

        u1 carries anchor_novelty; u2 follows with intention only. Treating
        u2's omitted anchor_novelty as an explicit 0.0 cleared _novelty_count
        on every frame, so the two-frame latch was unreachable in production
        even with sustained novelty.
        """
        ss = SceneState()

        # Frame N: u1 (novelty) then u2 (intention only), as main.py does.
        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.5)
        ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is False  # first frame only counts

        # Frame N+1
        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.5)
        ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is True  # latched despite u2

    def test_intention_only_update_does_not_clear_latched_flag(self):
        """A call without a novelty observation must not clear the latch."""
        ss = SceneState()
        ss.update(anchor_novelty=0.5)
        ss.update(anchor_novelty=0.5)
        assert ss.get()["desk_changed"] is True
        ss.update(intention="ambient")  # no observation this call
        assert ss.get()["desk_changed"] is True

    def test_explicit_zero_novelty_clears_the_streak(self):
        """None means "no observation"; 0.0 means "observed zero" and resets
        the streak, so one later high frame must not latch on its own."""
        ss = SceneState()
        ss.update(anchor_novelty=0.5)   # streak = 1
        ss.update(anchor_novelty=0.0)   # explicit zero → streak reset
        ss.update(anchor_novelty=0.5)   # streak = 1 again, not a latch
        assert ss.get()["desk_changed"] is False

    def test_explicit_zero_novelty_prevents_latch_under_production_pattern(self):
        """Counter-example to the latch test: an explicit 0.0 observation
        resets the streak, so the two-frame latch never completes."""
        ss = SceneState()
        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.5)
        ss.update(intention="ambient")

        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.0)
        ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is False

        # The 0.0 must have reset the streak: one high frame is not enough.
        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.5)
        ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is False

    def test_latched_flag_clears_under_production_pattern(self):
        """Once latched, a later explicit 0.0 observation still clears it,
        even though every frame ends with an intention-only call."""
        ss = SceneState()
        for _ in range(2):  # two frames latch it
            ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.5)
            ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is True

        ss.update(objects=[{"class_name": "cup"}], anchor_novelty=0.0)
        ss.update(intention="ambient")
        assert ss.get()["desk_changed"] is False

    def test_objects_none_preserves_previous_observation(self):
        """None means 'no object observation supplied' — state is preserved."""
        ss = SceneState()
        ss.update(objects=[{"class_name": "cup"}])
        ss.update(objects=None)
        assert ss.get()["objects"] == [{"class_name": "cup"}]

    def test_explicit_empty_objects_clears_state(self):
        """[] means 'observed zero objects' and must be representable."""
        ss = SceneState()
        ss.update(objects=[{"class_name": "cup"}])
        ss.update(objects=[])
        assert ss.get()["objects"] == []

    def test_get_returns_all_fields(self):
        ss = SceneState()
        state = ss.get()
        assert "runtime_state" in state
        assert "state_duration" in state
        assert "mode" in state
        assert state["runtime_state"] == "idle"
