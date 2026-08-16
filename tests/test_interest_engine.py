"""Tests for Interest Engine."""
import sys, time
sys.path.insert(0, '.')
import pytest
from runtime.interest.engine import InterestEngine, InterestTarget, CuriosityQueue


class TestInterestTarget:
    def test_initial_values(self):
        t = InterestTarget(target_id="door", category="region")
        assert t.target_id == "door"
        assert t.interest == 0.5
        assert t.revisit_count == 0
        assert t.age() < 1.0

    def test_since_seen(self):
        t = InterestTarget(target_id="x", category="object")
        time.sleep(0.01)
        assert t.since_seen() > 0


class TestInterestEngine:
    def test_see_creates_target(self):
        e = InterestEngine()
        e.see("box", category="object")
        t = e.get("box")
        assert t is not None
        assert t.category == "object"
        assert 0 < t.interest <= 1.0

    def test_see_updates_existing(self):
        e = InterestEngine()
        e.see("box", category="object")
        first = e.get("box").last_seen
        time.sleep(0.01)
        e.see("box", category="object")
        assert e.get("box").last_seen > first

    def test_novelty_boosts_interest(self):
        e = InterestEngine()
        e.see("boring", novelty=0.0)
        e.see("exciting", novelty=0.8)
        assert e.get("exciting").interest > e.get("boring").interest

    def test_decay_reduces_interest(self):
        e = InterestEngine()
        e.see("test_target", category="object")
        t = e.get("test_target")
        # Simulate aging by modifying last_seen
        t.last_seen = time.time() - 120  # 2 minutes ago
        e.decay()
        assert e.get("test_target").interest < 0.5  # should have decayed

    def test_decay_removes_below_threshold(self):
        e = InterestEngine()
        e.see("fading", category="object")
        t = e.get("fading")
        t.last_seen = time.time() - 3600  # 1 hour ago
        t.interest = 0.04  # below minimum
        e.decay()
        assert e.get("fading") is None

    def test_revisit_confirm_boosts_interest(self):
        e = InterestEngine()
        e.see("box", category="object")
        before = e.get("box").interest
        e.record_revisit("box")
        e.see("box", category="object")  # confirm
        assert e.get("box").interest > before

    def test_revisit_fail_reduces_interest(self):
        e = InterestEngine()
        e.see("box", category="object")
        e.record_revisit("box")
        e.record_revisit_failed("box")
        assert e.get("box").interest < 0.5

    def test_should_revisit_high_interest(self):
        e = InterestEngine()
        e.see("urgent", category="person")
        e.record_revisit("urgent")  # sets revisit_count > 0
        t = e.get("urgent")
        t.interest = 0.9
        t.last_revisited = time.time() - 60  # 1 min since last revisit
        t.last_seen = time.time()
        assert e.should_revisit("urgent") is True

    def test_should_revisit_low_interest(self):
        e = InterestEngine()
        e.see("meh", category="object")
        e.record_revisit("meh")
        t = e.get("meh")
        t.interest = 0.1
        t.last_revisited = time.time() - 30  # just checked
        t.last_seen = time.time()
        assert e.should_revisit("meh") is False  # too low interest, interval=280s > 30s

    def test_top_interests_sorts_correctly(self):
        e = InterestEngine()
        e.see("a", category="object")
        e.see("b", category="person")
        e.see("c", category="region")
        e.get("a").interest = 0.3
        e.get("b").interest = 0.9
        e.get("c").interest = 0.5
        tops = e.top_interests(3)
        assert tops[0].target_id == "b"
        assert tops[1].target_id == "c"
        assert tops[2].target_id == "a"

    def test_next_revisit(self):
        e = InterestEngine()
        e.see("stale", category="object")
        t = e.get("stale")
        t.interest = 0.8
        t.last_seen = time.time() - 300  # 5 min stale → high uncertainty
        n = e.next_revisit()
        assert n is not None
        assert n.target_id == "stale"

    def test_forget(self):
        e = InterestEngine()
        e.see("tmp", category="object")
        e.forget("tmp")
        assert e.get("tmp") is None

    def test_target_count(self):
        e = InterestEngine()
        e.see("a", category="object")
        e.see("b", category="object")
        assert e.target_count == 2
        e.forget("a")
        assert e.target_count == 1

    def test_repr(self):
        e = InterestEngine()
        e.see("box", category="object")
        r = repr(e)
        assert "InterestEngine" in r
        assert "box" in r


class TestCuriosityQueue:
    def test_uncertainty_grows_with_time(self):
        t = InterestTarget(target_id="x", category="object")
        t.last_seen = time.time() - 300  # 5 min ago
        score = t.curiosity_score
        t2 = InterestTarget(target_id="y", category="object")
        t2.last_seen = time.time()  # just now
        assert t.curiosity_score > t2.curiosity_score  # stale = higher

    def test_high_interest_high_uncertainty_wins(self):
        t1 = InterestTarget(target_id="stale_door", category="region")
        t1.interest = 0.5
        t1.last_seen = time.time() - 300  # 5 min unchecked → high uncertainty

        t2 = InterestTarget(target_id="fresh_cup", category="object")
        t2.interest = 0.9
        t2.last_seen = time.time() - 3    # just checked → low uncertainty

        # Door scores higher: moderate interest × high uncertainty (0.5 × 0.81)
        # > cup: high interest × near-zero uncertainty (0.9 × 0.017)
        assert t1.curiosity_score > t2.curiosity_score

    def test_curiosity_queue_ranks_correctly(self):
        e = InterestEngine()
        e.see("door", category="region"); e.get("door").interest = 0.5
        e.see("cup", category="object"); e.get("cup").interest = 0.9
        e.see("person", category="person"); e.get("person").interest = 0.7

        # Age them differently
        e.get("door").last_seen = time.time() - 300    # 5min stale → high uncertainty
        e.get("cup").last_seen = time.time() - 3        # fresh → low uncertainty
        e.get("person").last_seen = time.time() - 60    # 1min medium

        queue = e.curiosity_queue(5)
        # Door should be #1: moderate interest + high uncertainty
        assert queue[0].target_id == "door"
        # Person #2: good interest + medium uncertainty
        # Cup filtered out: high interest but near-zero uncertainty (score < 0.01)
        assert len(queue) == 2  # cup filtered out by score threshold

    def test_since_confirmed(self):
        t = InterestTarget(target_id="x", category="object")
        time.sleep(0.01)
        assert t.since_confirmed() > 0
        assert t.since_confirmed() < 1.0


class TestWorldAnchor:
    def test_entity_vs_region_type(self):
        """Entities move (location updates), regions are fixed."""
        e = InterestEngine()
        e.see("person_17", target_type="entity", category="person",
              location=(10.0, 5.0))
        e.see("door", target_type="region", category="region",
              location=(150.0, 0.0))
        assert e.get("person_17").target_type == "entity"
        assert e.get("door").target_type == "region"

    def test_entity_location_updates(self):
        e = InterestEngine()
        e.see("cup_3", target_type="entity", location=(20.0, 0.0))
        e.see("cup_3", target_type="entity", location=(45.0, -5.0))
        assert e.get("cup_3").location == (45.0, -5.0)

    def test_region_location_fixed(self):
        """Regions should NOT update location — they're fixed anchors."""
        e = InterestEngine()
        e.see("door", target_type="region", location=(150.0, 0.0))
        e.see("door", target_type="region", location=(999.0, 999.0))
        # Region location stays at the initial anchor
        assert e.get("door").location == (150.0, 0.0)


class TestMovementCost:
    def test_nearby_target_scores_higher(self):
        """Same interest + uncertainty, but one is closer."""
        t1 = InterestTarget(target_id="near", category="region",
                            location=(10.0, 0.0))
        t1.interest = 0.5
        t1.last_seen = time.time() - 300

        t2 = InterestTarget(target_id="far", category="region",
                            location=(200.0, 0.0))
        t2.interest = 0.5
        t2.last_seen = time.time() - 300

        # With camera at pan=0, "near" should score higher
        s1 = CuriosityQueue.score(t1, current_pan=0.0)
        s2 = CuriosityQueue.score(t2, current_pan=0.0)
        assert s1 > s2

    def test_high_interest_overcomes_distance(self):
        """Very interesting far target can beat boring near target."""
        t1 = InterestTarget(target_id="boring_near", category="region",
                            location=(5.0, 0.0))
        t1.interest = 0.1
        t1.last_seen = time.time() - 600  # very stale

        t2 = InterestTarget(target_id="urgent_far", category="region",
                            location=(300.0, 0.0))
        t2.interest = 0.9
        t2.last_seen = time.time() - 600  # equally stale

        s1 = CuriosityQueue.score(t1, current_pan=0.0)
        s2 = CuriosityQueue.score(t2, current_pan=0.0)
        assert s2 > s1  # urgent_far wins despite distance


class TestRevisitFeedback:
    def test_consecutive_fails_dampen(self):
        e = InterestEngine()
        e.see("door", target_type="region", category="region")
        initial = e.get("door").interest
        for _ in range(4):
            e.record_revisit("door")
            e.record_revisit_failed("door")
        # After 4 failures, interest should be significantly lower
        assert e.get("door").interest < initial * 0.5

    def test_fail_then_success_resets_counters(self):
        e = InterestEngine()
        e.see("door", target_type="region", category="region")
        e.record_revisit("door")
        e.record_revisit_failed("door")
        assert e.get("door").consecutive_fails == 1
        # Now succeed
        e.record_revisit("door")
        e.see("door", target_type="region", category="region")  # confirm
        assert e.get("door").consecutive_fails == 0
        assert e.get("door").consecutive_successes == 1

    def test_max_fails_triggers_obsession_penalty(self):
        e = InterestEngine()
        e.see("obsession", target_type="region", category="region")
        t = e.get("obsession")
        t.interest = 0.8
        for _ in range(6):
            e.record_revisit("obsession")
            e.record_revisit_failed("obsession")
        # After exceeding max_consecutive_fails (5), score should be heavily dampened
        score = CuriosityQueue.score(t, current_pan=0.0)
        assert score < 0.2  # heavily penalised
