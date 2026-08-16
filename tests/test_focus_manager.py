"""Tests for focus manager — persistent attention target."""
import sys
sys.path.insert(0, '.')
from runtime.focus.manager import FocusManager, FocusTarget


class TestFocusManager:
    def test_initial_state_is_idle(self):
        fm = FocusManager()
        info = fm.update([], {}, [], [])
        assert info["mode"] == "idle"
        assert not info["has_focus"]

    def test_locks_onto_high_score_face(self):
        fm = FocusManager()
        events = [{"type": "human_face", "score": 0.8}]
        faces = [{"bbox": {"x": 0, "y": 0, "width": 100, "height": 100}}]
        info = fm.update(events, {}, faces, [])
        assert info["has_focus"]
        assert info["mode"] == "tracking"
        assert info["target_type"] == "person"
        assert info["changed"]

    def test_keeps_focus_on_same_target(self):
        fm = FocusManager()
        events = [{"type": "human_face", "score": 0.8}]
        faces = [{"bbox": {}}]
        fm.update(events, {}, faces, [])
        info = fm.update(events, {}, faces, [])
        assert info["has_focus"]
        assert not info["changed"]  # no change

    def test_does_not_switch_for_small_increase(self):
        fm = FocusManager()
        fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        # New candidate with 1.2x score — below 1.5x threshold
        info = fm.update([{"type": "human_face", "score": 0.95}], {}, [{"bbox": {}}], [])
        assert info["has_focus"]
        assert not info["changed"]

    def test_switches_for_much_more_important_target(self):
        fm = FocusManager()
        fm.update([{"type": "human_face", "score": 0.5}], {}, [{"bbox": {}}], [])
        # A much more important DIFFERENT event appears
        info = fm.update([{"type": "human_face", "score": 0.5}, {"type": "gaze_started", "score": 0.9}], {},
                         [{"bbox": {}}], [{"bbox": {}, "class_name": "person"}])
        assert info["has_focus"]
        assert info["changed"]

    def test_same_target_score_increase_not_a_switch(self):
        """Same person with higher confidence → update in place, not a switch."""
        fm = FocusManager()
        fm.update([{"type": "human_face", "score": 0.5}], {}, [{"bbox": {}}], [])
        info = fm.update([{"type": "human_face", "score": 0.9}], {}, [{"bbox": {}}], [])
        assert info["has_focus"]
        assert not info["changed"]  # same target, just better confidence

    def test_enters_lost_mode_when_target_disappears(self):
        fm = FocusManager()
        fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        info = fm.update([], {}, [], [])  # target gone
        assert info["mode"] == "lost"

    def test_recovers_focus_when_target_reappears(self):
        fm = FocusManager()
        fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        fm.update([], {}, [], [])  # lost
        info = fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        assert info["mode"] == "tracking"

    def test_releases_after_lost_timeout(self):
        fm = FocusManager(lost_timeout=0.0)  # immediate release
        fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        info = fm.update([], {}, [], [])  # lost, timeout=0 → release
        assert not info["has_focus"]
        assert info["mode"] in ("idle", "scanning")

    def test_recent_targets_recorded_on_release(self):
        fm = FocusManager(lost_timeout=0.0)
        fm.update([{"type": "human_face", "score": 0.8}], {}, [{"bbox": {}}], [])
        fm.update([], {}, [], [])
        assert len(fm.recent) == 1

    def test_ignores_background_events(self):
        fm = FocusManager()
        info = fm.update([{"type": "background_change", "score": 0.1}], {}, [], [])
        assert not info["has_focus"]

    def test_scan_mode_after_long_idle(self):
        fm = FocusManager(idle_scan_time=0.0)  # immediate scan
        info = fm.update([], {}, [], [])
        assert info["mode"] == "scanning"
