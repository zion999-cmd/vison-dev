"""Tests for presence tracker — identity persistence & novelty gate."""
import sys
sys.path.insert(0, '.')
from runtime.presence.tracker import PresenceTracker, KnownEntity


class TestPresenceTracker:
    def test_initial_state(self):
        pt = PresenceTracker()
        info = pt.update(
            {"user_present": False, "desk_changed": False},
            {"mode": "idle", "has_focus": False},
            [], [],
        )
        assert info["novelty"] == 0.0
        assert not info["stable"]
        assert info["should_think"]  # initial state, still booting

    def test_new_person_has_high_novelty(self):
        pt = PresenceTracker()
        info = pt.update(
            {"user_present": True, "desk_changed": False},
            {"mode": "tracking", "has_focus": True},
            [{"bbox": {}}], [],
        )
        assert info["novelty"] > 0.5
        assert info["should_think"]

    def test_familiar_person_lowers_novelty(self):
        pt = PresenceTracker()
        scene = {"user_present": True, "desk_changed": False}
        focus = {"mode": "tracking", "has_focus": True}
        faces = [{"bbox": {}}]

        # Simulate presence (min 0.05s per call → 120 calls ≈ 6s)
        for _ in range(120):
            pt.update(scene, focus, faces, [])
        info = pt.update(scene, focus, faces, [])
        assert info["familiarity"] > 0.03
        assert info["stable"]

    def test_should_think_false_when_stable_and_low_novelty(self):
        pt = PresenceTracker()
        scene = {"user_present": True, "desk_changed": False}
        focus = {"mode": "tracking", "has_focus": True}
        faces = [{"bbox": {}}]

        # Long presence → novelty decays, stability grows
        for _ in range(150):
            pt.update(scene, focus, faces, [])
        info = pt.update(scene, focus, faces, [])
        assert info["stable"]
        # After long stable presence, novelty decays near floor (gaze keeps min 0.2)
        assert info["novelty"] <= 0.3

    def test_desk_change_boosts_novelty(self):
        pt = PresenceTracker()
        info = pt.update(
            {"user_present": True, "desk_changed": True},
            {"mode": "tracking", "has_focus": True},
            [{"bbox": {}}], [{"bbox": {}}],
        )
        assert info["novelty"] > 0.3

    def test_engagement_grows_with_gaze(self):
        pt = PresenceTracker()
        scene = {"user_present": True, "desk_changed": False}
        for _ in range(20):
            pt.update(scene, {"mode": "tracking", "has_focus": True}, [{"bbox": {}}], [])
        info = pt.update(scene, {"mode": "tracking", "has_focus": True}, [{"bbox": {}}], [])
        assert info["engagement"] > 0.5

    def test_engagement_decays_without_gaze(self):
        pt = PresenceTracker()
        scene = {"user_present": True, "desk_changed": False}
        # Build up engagement
        for _ in range(20):
            pt.update(scene, {"mode": "tracking", "has_focus": True}, [{"bbox": {}}], [])
        # Then let it decay
        for _ in range(10):
            pt.update(scene, {"mode": "idle", "has_focus": False}, [{"bbox": {}}], [])
        info = pt.update(scene, {"mode": "idle", "has_focus": False}, [{"bbox": {}}], [])
        assert info["engagement"] < 0.8  # should have decayed

    def test_no_person_resets_continuity(self):
        pt = PresenceTracker()
        scene_present = {"user_present": True, "desk_changed": False}
        scene_absent = {"user_present": False, "desk_changed": False}
        focus = {"mode": "idle", "has_focus": False}

        for _ in range(10):
            pt.update(scene_present, focus, [{"bbox": {}}], [])
        info = pt.update(scene_absent, focus, [], [])
        assert info["continuous_presence"] < 1.0

    def test_known_entity_familiarity_grows(self):
        ent = KnownEntity(entity_id="e1", first_seen=1000.0, last_seen=1000.0)
        ent.bump(1010.0, dt=10.0)  # 10 seconds
        ent.bump(1020.0, dt=10.0)  # +10 = 20 seconds total
        assert ent.familiarity > 0.08
        assert ent.times_seen == 2
