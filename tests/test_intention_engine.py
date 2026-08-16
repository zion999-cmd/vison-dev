"""Tests for intention inference engine."""
import sys
sys.path.insert(0, '.')
from runtime.intention.engine import IntentionEngine, UserIntention, INTENTION_PRIORITY


class TestIntentionEngine:
    def test_ambient_when_nothing_happens(self):
        engine = IntentionEngine()
        result = engine.infer(
            {"user_present": False, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
            [],
        )
        assert result["intention"] == UserIntention.AMBIENT.value

    def test_approaching_when_user_appears(self):
        engine = IntentionEngine()
        events = [{"type": "human_face", "score": 0.8}]
        result = engine.infer(
            {"user_present": True, "motion_level": 0.2, "voice_activity": False, "desk_changed": False},
            events,
        )
        assert result["intention"] == UserIntention.APPROACHING.value
        assert result["confidence"] > 0.5

    def test_speaking_when_voice_and_face(self):
        engine = IntentionEngine()
        events = [{"type": "voice_detected", "score": 0.9}]
        result = engine.infer(
            {"user_present": True, "motion_level": 0.1, "voice_activity": True, "desk_changed": False},
            events,
        )
        assert result["intention"] == UserIntention.SPEAKING.value

    def test_leaving_when_user_disappears(self):
        engine = IntentionEngine()
        # First, user is present
        engine.infer(
            {"user_present": True, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
            [{"type": "human_face", "score": 0.8}],
        )
        # Then, user leaves
        result = engine.infer(
            {"user_present": False, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
            [],
        )
        assert result["intention"] == UserIntention.LEAVING.value

    def test_priority_assigned_to_result(self):
        engine = IntentionEngine()
        result = engine.infer(
            {"user_present": False, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
            [],
        )
        assert "priority" in result
        assert isinstance(result["priority"], float)

    def test_label_matches_intention(self):
        engine = IntentionEngine()
        result = engine.infer(
            {"user_present": False, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
            [],
        )
        assert result["label"] == result["intention"]

    def test_emergency_on_extreme_motion_no_user(self):
        engine = IntentionEngine()
        events = [{"type": "large_motion", "score": 0.9}]
        result = engine.infer(
            {"user_present": False, "motion_level": 0.9, "voice_activity": False, "desk_changed": False},
            events,
        )
        assert result["intention"] == UserIntention.EMERGENCY.value
        assert result["priority"] == INTENTION_PRIORITY[UserIntention.EMERGENCY]

    def test_intention_history_truncated(self):
        engine = IntentionEngine()
        for _ in range(250):
            engine.infer(
                {"user_present": False, "motion_level": 0.0, "voice_activity": False, "desk_changed": False},
                [],
            )
        assert len(engine._intention_history) <= 200
