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

    def test_desk_changed_flag(self):
        ss = SceneState()
        ss.update(objects=[{"class_name": "cup"}])
        ss.update(objects=[{"class_name": "book"}])  # class changed 2x → triggers
        state = ss.get()
        assert state["desk_changed"] is True

    def test_get_returns_all_fields(self):
        ss = SceneState()
        state = ss.get()
        assert "runtime_state" in state
        assert "state_duration" in state
        assert "mode" in state
        assert state["runtime_state"] == "idle"
