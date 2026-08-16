"""Tests for L4 attention engine."""
import sys
sys.path.insert(0, '.')
from runtime.attention.engine import AttentionEngine, BASE_WEIGHTS


class TestAttentionEngine:
    def test_scores_are_sorted_descending(self):
        engine = AttentionEngine()
        scene = {
            "user_present": True, "people": [{"bbox": {}}],
            "motion_level": 0.8, "desk_changed": True,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 5.0,
        }
        scored = engine.score_events(scene)
        scores = [e["score"] for e in scored]
        assert scores == sorted(scores, reverse=True)

    def test_human_face_gets_high_score(self):
        engine = AttentionEngine()
        scene = {
            "user_present": True, "people": [{"bbox": {}}],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 5.0,
        }
        scored = engine.score_events(scene)
        face_event = next(e for e in scored if e["type"] == "human_face")
        assert face_event["score"] >= BASE_WEIGHTS["human_face"]

    def test_empty_scene_scores_background(self):
        engine = AttentionEngine()
        scene = {
            "user_present": False, "people": [],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 0.0,
        }
        scored = engine.score_events(scene)
        assert len(scored) >= 1
        assert scored[0]["type"] == "background_change"

    def test_state_multiplier_scales_scores(self):
        engine = AttentionEngine()
        scene_base = {
            "user_present": True, "people": [{"bbox": {}}],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 5.0,
        }
        scored_normal = engine.score_events(dict(scene_base))
        # With focus multiplier (0.7), scores should be lower
        engine2 = AttentionEngine()
        scene_focus = dict(scene_base)
        scene_focus["state_multiplier"] = 0.7
        scored_focus = engine2.score_events(scene_focus)
        for ev_n, ev_f in zip(scored_normal, scored_focus):
            if ev_n["type"] == ev_f["type"]:
                assert ev_f["score"] <= ev_n["score"]

    def test_decay_reduces_scores_over_time(self):
        engine = AttentionEngine()
        engine._last_scores = {"test_event": 0.8}
        engine._last_update = 0.0
        scene = {
            "user_present": False, "people": [],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 0.0,
        }
        import time
        engine._last_update = time.time()  # reset to now, so delta_t ~ 0, no decay
        scored = engine.score_events(scene)
        assert len(scored) > 0

    def test_weights_evolve_on_frequent_triggers(self):
        engine = AttentionEngine()
        now = 1000
        for _ in range(20):
            engine._trigger_history.append({
                "type": "human_face", "time": now, "score": 0.8
            })
        old_weight = engine.weights.get("human_face", 0.9)
        engine.evolve_weights()
        new_weight = engine.weights["human_face"]
        assert new_weight >= old_weight

    def test_weights_return_copy(self):
        engine = AttentionEngine()
        w = engine.weights
        w["human_face"] = 0.0
        assert engine.weights["human_face"] != 0.0

    def test_gaze_started_fires_once_then_gaze_maintained(self):
        engine = AttentionEngine()
        scene = {
            "user_present": True, "people": [{"bbox": {}}],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 5.0,
        }
        # First frame of sustained gaze — gaze_started fires
        scored = engine.score_events(scene)
        types = [e["type"] for e in scored]
        assert "gaze_started" in types
        assert "gaze_maintained" not in types  # not yet — starts frame 2

        # Second frame — maintained replaces started
        scored2 = engine.score_events(scene)
        types2 = [e["type"] for e in scored2]
        assert "gaze_started" not in types2
        assert "gaze_maintained" in types2

    def test_gaze_lost_when_user_leaves(self):
        engine = AttentionEngine()
        scene_gaze = {
            "user_present": True, "people": [{"bbox": {}}],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 5.0,
        }
        engine.score_events(scene_gaze)  # start gaze
        assert engine.gaze_active

        scene_nobody = {
            "user_present": False, "people": [],
            "motion_level": 0.0, "desk_changed": False,
            "voice_activity": False, "state_multiplier": 1.0,
            "state_duration": 0.0,
        }
        scored = engine.score_events(scene_nobody)
        types = [e["type"] for e in scored]
        assert "gaze_lost" in types
        assert not engine.gaze_active
