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


class TestCanonicalGrid:
    """The manager is the only definition of the pan/tilt grid.

    Hardware evidence: a caller-side 30° snap against this 20° grid hit only
    322 of 2099 novelty lookups, because every pose between the two grids
    (e.g. snapped=90 against anchors at 80/100) resolved to nothing.
    """

    def test_lookup_resolves_poses_the_30_degree_snap_missed(self):
        mgr = AnchorManager(pan_spacing=20, tilt_spacing=15)
        mgr.observe([{"class_name": "cup", "confidence": 0.9}], pan=78, tilt=92)
        assert mgr.anchor_count == 1

        # round(90/20)=4 → cell 80, so 90 is the same cell as 78. The old
        # caller-side snap computed round(90/30)*30 = 90 and found nothing.
        assert mgr.snap(90, 92) == (80.0, 90.0)
        found = mgr.lookup(90, 92)
        assert found is not None
        assert found.anchor_id == "anchor_80_90"

    def test_lookup_matches_a_direct_creation_key(self):
        mgr = AnchorManager(pan_spacing=20, tilt_spacing=15)
        mgr.observe([{"class_name": "cup", "confidence": 0.9}], pan=41, tilt=93)
        # 41 → 40, 93 → 90: lookup must agree with the creation grid.
        assert mgr.snap(41, 93) == (40.0, 90.0)
        assert mgr.lookup(41, 93).anchor_id == mgr.lookup(38, 97).anchor_id

    def test_lookup_returns_none_for_unobserved_cells(self):
        mgr = AnchorManager(pan_spacing=20, tilt_spacing=15)
        mgr.observe([{"class_name": "cup", "confidence": 0.9}], pan=78, tilt=92)
        assert mgr.lookup(-120, 92) is None


class TestNoveltyLifecycle:
    CUP = [{"class_name": "cup", "confidence": 0.9}]

    def _mgr(self):
        return AnchorManager(pan_spacing=20, tilt_spacing=15)

    def test_object_appearing_raises_novelty(self):
        mgr = self._mgr()
        mgr.observe([], pan=0, tilt=90)          # baseline: nothing here
        before = mgr.lookup(0, 90).novelty
        mgr.observe(self.CUP, pan=0, tilt=90)    # a cup appears
        assert mgr.lookup(0, 90).novelty > before

    def test_object_disappearing_raises_novelty(self):
        """Disappearance must stay a novelty source in its own right."""
        mgr = self._mgr()
        mgr.observe(self.CUP, pan=0, tilt=90)    # baseline: {cup}
        before = mgr.lookup(0, 90).novelty
        mgr.observe([], pan=0, tilt=90)          # the cup leaves
        assert mgr.lookup(0, 90).novelty > before

    def test_stable_nonempty_eventually_decays(self):
        mgr = self._mgr()
        mgr.observe(self.CUP, pan=0, tilt=90)
        mgr.observe([], pan=0, tilt=90)          # cup leaves → novelty rises
        peak = mgr.lookup(0, 90).novelty
        assert peak > 0
        for _ in range(60):                      # cup is back and stays
            mgr.observe(self.CUP, pan=0, tilt=90)
        assert mgr.lookup(0, 90).novelty < peak

    def test_stable_empty_eventually_decays(self):
        """Regression: an emptied baseline re-entered the first-observation
        path on every observation and returned early, so novelty froze at its
        peak forever and the anchor could never be marked barren."""
        mgr = self._mgr()
        mgr.observe(self.CUP, pan=0, tilt=90)
        for _ in range(4):                       # cup leaves; baseline empties
            mgr.observe([], pan=0, tilt=90)
        anchor = mgr.lookup(0, 90)
        assert anchor.baseline_objects == set()
        peak = anchor.novelty
        assert peak > 0

        for _ in range(60):                      # stable, and empty
            mgr.observe([], pan=0, tilt=90)

        anchor = mgr.lookup(0, 90)
        assert anchor.novelty < peak, "a settled empty anchor must decay"
        assert anchor.observed_once is True
        assert anchor.barren is True, "empty-anchor detection must be reachable"
