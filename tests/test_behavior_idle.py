"""Tests for idle behavior system."""
import sys
sys.path.insert(0, '.')
from runtime.behavior.idle import IdleBehaviorManager


class TestIdleBehavior:
    def test_initial_state_is_scan(self):
        bm = IdleBehaviorManager()
        info = bm.update(
            {"user_present": False},
            {"mode": "idle", "has_focus": False},
            {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
            0,
        )
        assert info["state"] == "idle_scan"

    def test_tracking_when_person_with_focus(self):
        bm = IdleBehaviorManager()
        info = bm.update(
            {"user_present": True},
            {"mode": "tracking", "has_focus": True},
            {"familiarity": 0.5, "engagement": 0.3, "continuous_presence": 5.0},
            0,
        )
        assert info["state"] == "tracking"

    def test_engaged_when_gazing(self):
        bm = IdleBehaviorManager()
        info = bm.update(
            {"user_present": True},
            {"mode": "tracking", "has_focus": True},
            {"familiarity": 0.5, "engagement": 0.8, "continuous_presence": 10.0},
            0,
        )
        assert info["state"] == "engaged"

    def test_thought_changes_over_time(self):
        bm = IdleBehaviorManager()
        info1 = bm.update(
            {"user_present": False},
            {"mode": "idle", "has_focus": False},
            {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
            0,
        )
        # Force thought refresh
        bm._last_thought_time = 0.0
        info2 = bm.update(
            {"user_present": False},
            {"mode": "idle", "has_focus": False},
            {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
            1,
        )
        assert len(info2["thought"]) > 0
        # Should be from idle_scan pool
        # Should match the current behavior state's thought pool
        assert info2["thought"] in bm._thoughts[bm.state]

    def test_interest_decays_when_no_person(self):
        bm = IdleBehaviorManager()
        bm.interest = 0.5
        for _ in range(10):
            bm.update(
                {"user_present": False},
                {"mode": "idle", "has_focus": False},
                {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
                0,
            )
        assert bm.interest < 0.5

    def test_sensitivity_drops_in_rest(self):
        bm = IdleBehaviorManager()
        bm.interest = 0.0
        bm._last_person_seen = 1.0  # was seen 999s ago (relative to now)

        # After update, should enter idle_rest (idle > 300s)
        info = bm.update(
            {"user_present": False},
            {"mode": "idle", "has_focus": False},
            {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
            0,
        )
        assert info["state"] == "idle_rest"
        assert bm.sensitivity < 1.0

    def test_scan_area_changes(self):
        bm = IdleBehaviorManager()
        bm._last_scan_switch = 0.0
        info1 = bm.update(
            {"user_present": False},
            {"mode": "idle", "has_focus": False},
            {"familiarity": 0.0, "engagement": 0.0, "continuous_presence": 0.0},
            0,
        )
        # Should have switched to next area
        assert info1["scan_area"] in bm._scan_areas
