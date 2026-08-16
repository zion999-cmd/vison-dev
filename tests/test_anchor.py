"""Tests for Spatial Anchor system."""
import sys, time
sys.path.insert(0, '.')
import pytest
from runtime.interest.anchor import SpatialAnchor, AnchorManager


class TestSpatialAnchor:
    def test_initial_baseline_empty(self):
        a = SpatialAnchor(anchor_id="test_0_0", pan=0, tilt=0)
        assert len(a.baseline_objects) == 0
        assert a.interest == 0.3

    def test_curiosity_grows_with_time(self):
        a = SpatialAnchor(anchor_id="stale", pan=30, tilt=0)
        a.visit_count = 1  # has been visited before
        a.last_visited = time.time() - 600  # 10 min ago
        a.interest = 0.5
        score = a.curiosity_score
        assert score > 0.1  # should be curious about stale anchor

    def test_just_visited_has_low_curiosity(self):
        a = SpatialAnchor(anchor_id="fresh", pan=60, tilt=0)
        a.visit_count = 1  # has been visited
        a.last_visited = time.time()
        a.interest = 0.9
        score = a.curiosity_score
        assert score < 0.3  # just visited, not curious yet

    def test_since_visited_no_visits(self):
        a = SpatialAnchor(anchor_id="new", pan=0, tilt=0)
        assert a.since_visited() > 0  # infinity-like, never visited


class TestAnchorManager:
    def test_creates_anchor_from_observation(self):
        mgr = AnchorManager()
        objects = [{"class_name": "chair"}, {"class_name": "person"}]
        mgr.observe(objects, pan=0.0, tilt=0.0)
        assert mgr.anchor_count == 1
        a = mgr.all_anchors()[0]
        assert "chair" in a.baseline_objects
        assert "person" in a.baseline_objects

    def test_multiple_observations_same_anchor(self):
        mgr = AnchorManager()
        mgr.observe([{"class_name": "chair"}], pan=5.0, tilt=2.0)
        mgr.observe([{"class_name": "chair"}], pan=10.0, tilt=5.0)
        # Both snap to same anchor (30° spacing)
        assert mgr.anchor_count == 1

    def test_different_positions_create_different_anchors(self):
        mgr = AnchorManager()
        mgr.observe([{"class_name": "chair"}], pan=0.0, tilt=0.0)
        mgr.observe([{"class_name": "monitor"}], pan=90.0, tilt=0.0)
        assert mgr.anchor_count == 2

    def test_baseline_change_detection(self):
        mgr = AnchorManager()
        # First observation: establish baseline
        mgr.observe([{"class_name": "chair"}, {"class_name": "desk"}],
                    pan=0.0, tilt=0.0)
        a = mgr.all_anchors()[0]
        assert a.novelty == 0.0  # no change yet

        # Second: cup appears → novelty
        mgr.observe([{"class_name": "chair"}, {"class_name": "desk"},
                     {"class_name": "cup"}],
                    pan=0.0, tilt=0.0)
        a = mgr.all_anchors()[0]
        assert a.novelty > 0.0  # change detected
        assert a.interest > 0.3  # interest increased

    def test_mark_visited(self):
        mgr = AnchorManager()
        mgr.observe([{"class_name": "chair"}], pan=0.0, tilt=0.0)
        a = mgr.all_anchors()[0]
        assert a.visit_count == 0
        mgr.mark_visited(a.anchor_id)
        assert a.visit_count == 1

    def test_curiosity_targets_ranking(self):
        mgr = AnchorManager()
        mgr.observe([{"class_name": "chair"}], pan=0.0, tilt=0.0)
        mgr.observe([{"class_name": "monitor"}], pan=90.0, tilt=0.0)
        # Age one anchor to make it more curious
        stale = mgr.all_anchors()[0]
        stale.last_visited = time.time() - 900  # 15 min
        stale.interest = 0.7
        targets = mgr.get_curiosity_targets(3)
        assert len(targets) >= 1
        assert targets[0].anchor_id == stale.anchor_id  # stalest first

    def test_next_anchor(self):
        mgr = AnchorManager()
        mgr.observe([{"class_name": "chair"}], pan=60.0, tilt=0.0)
        a = mgr.all_anchors()[0]
        a.last_visited = time.time() - 600
        a.interest = 0.8
        n = mgr.next_anchor(current_pan=0.0)
        assert n is not None

    def test_snap_grid(self):
        mgr = AnchorManager(pan_spacing=30, tilt_spacing=15)
        assert mgr._snap(14.0, 30) == 0.0    # round(14/30)=round(0.47)=0
        assert mgr._snap(47.0, 30) == 60.0   # round(47/30)=round(1.57)=2
        assert mgr._snap(-22.0, 30) == -30.0 # round(-22/30)=round(-0.73)=-1
