"""Tests for Entity Quality Gate (Phase 7B)."""
import sys, time
sys.path.insert(0, '.')
import pytest
from runtime.interest.entity import Entity, EntityStatus
from runtime.importance.entity_quality import (
    is_valid_for_importance,
    is_noise,
    merge_entities,
    EntityQualityScanner,
    MIN_SEEN_THRESHOLD,
    CONF_THRESHOLD,
)


def _make_entity(class_name="person", seen=5, confidence=0.8,
                 status=EntityStatus.ACTIVE, interest=0.5,
                 interactions=3, event_types=None, tags=None):
    """Factory for test entities."""
    e = Entity(
        class_name=class_name,
        avg_confidence=confidence,
        status=status,
        interest=interest,
        tags=tags or [],
    )
    e.seen_count = seen
    e.interaction_count = interactions
    e.event_types = event_types or set()
    return e


class TestQualityGate:
    """Entity must pass all gates before entering Importance."""

    def test_valid_entity_passes(self):
        e = _make_entity(seen=10, confidence=0.8, status=EntityStatus.ACTIVE)
        assert is_valid_for_importance(e) is True

    def test_candidate_rejected(self):
        e = _make_entity(seen=10, confidence=0.8, status=EntityStatus.CANDIDATE)
        assert is_valid_for_importance(e) is False

    def test_lost_rejected(self):
        e = _make_entity(seen=10, confidence=0.8, status=EntityStatus.LOST)
        assert is_valid_for_importance(e) is False

    def test_low_confidence_rejected(self):
        e = _make_entity(seen=10, confidence=0.3, status=EntityStatus.ACTIVE)
        assert is_valid_for_importance(e) is False

    def test_low_count_rejected(self):
        e = _make_entity(seen=2, confidence=0.8, status=EntityStatus.ACTIVE)
        assert is_valid_for_importance(e) is False

    def test_bare_minimum_passes(self):
        e = _make_entity(
            seen=MIN_SEEN_THRESHOLD, confidence=CONF_THRESHOLD,
            status=EntityStatus.ACTIVE,
        )
        assert is_valid_for_importance(e) is True

    def test_forgotten_rejected(self):
        e = _make_entity(seen=50, confidence=0.9, status=EntityStatus.FORGOTTEN)
        assert is_valid_for_importance(e) is False

    def test_new_entity_defaults_rejected(self):
        """A brand-new entity with defaults should not pass."""
        e = Entity(class_name="unknown")
        assert is_valid_for_importance(e) is False


class TestNoiseDetection:
    """Low-confidence, low-count entities = noise."""

    def test_noise_detected(self):
        e = _make_entity(seen=1, confidence=0.3)
        assert is_noise(e) is True

    def test_confident_but_few_not_noise(self):
        e = _make_entity(seen=1, confidence=0.8)
        assert is_noise(e) is False

    def test_many_but_low_conf_not_noise(self):
        e = _make_entity(seen=10, confidence=0.3)
        assert is_noise(e) is False

    def test_stable_entity_not_noise(self):
        e = _make_entity(seen=50, confidence=0.9)
        assert is_noise(e) is False


class TestEntityMerge:
    """Merge fragmented entities."""

    def test_merge_accumulates_counts(self):
        primary = _make_entity(seen=10, interactions=5, confidence=0.8)
        secondary = _make_entity(seen=3, interactions=2, confidence=0.6)
        result = merge_entities(primary, secondary)
        assert result.interaction_count == 7
        assert result.seen_count == 13

    def test_merge_union_event_types(self):
        primary = _make_entity(event_types={"tracking", "cognition"})
        secondary = _make_entity(event_types={"speech", "tracking"})
        result = merge_entities(primary, secondary)
        assert result.event_types == {"tracking", "cognition", "speech"}

    def test_merge_weighted_confidence(self):
        primary = _make_entity(seen=10, confidence=0.8)
        secondary = _make_entity(seen=3, confidence=0.5)
        # Weighted avg: (0.8*10 + 0.5*3) / 13 = (8+1.5)/13 ≈ 0.731
        result = merge_entities(primary, secondary)
        assert abs(result.avg_confidence - 0.731) < 0.01

    def test_merge_keeps_higher_confidence_signature(self):
        primary = _make_entity(confidence=0.5, seen=5)
        secondary = _make_entity(confidence=0.9, seen=10)
        primary.visual_signature = (10.0, 50.0, 100.0, 80.0, 120.0)
        secondary.visual_signature = (20.0, 60.0, 110.0, 90.0, 130.0)
        result = merge_entities(primary, secondary)
        # secondary has higher confidence → its signature kept
        assert result.visual_signature == secondary.visual_signature

    def test_merge_preserves_secondary_class_as_tag(self):
        primary = _make_entity(class_name="person")
        secondary = _make_entity(class_name="customer")
        result = merge_entities(primary, secondary)
        assert "customer" in result.tags

    def test_merge_duplicate_tag_not_added(self):
        primary = _make_entity(class_name="person", tags=["customer"])
        secondary = _make_entity(class_name="customer")
        result = merge_entities(primary, secondary)
        assert result.tags.count("customer") == 1

    def test_merge_max_interest(self):
        primary = _make_entity(interest=0.3)
        secondary = _make_entity(interest=0.7)
        result = merge_entities(primary, secondary)
        assert result.interest == 0.7

    def test_merge_max_familiarity(self):
        primary = _make_entity()
        secondary = _make_entity()
        primary.familiarity_score = 0.3
        secondary.familiarity_score = 0.8
        result = merge_entities(primary, secondary)
        assert result.familiarity_score == 0.8


class TestEntityQualityScanner:
    """Batch scanner for merge candidates and stats."""

    def test_filter_valid_entities(self):
        scanner = EntityQualityScanner()
        entities = [
            _make_entity(class_name="person", seen=10, confidence=0.8,
                         status=EntityStatus.ACTIVE),
            _make_entity(class_name="chair", seen=2, confidence=0.3,
                         status=EntityStatus.ACTIVE),
            _make_entity(class_name="cup", seen=5, confidence=0.9,
                         status=EntityStatus.CANDIDATE),
        ]
        valid = scanner.filter_valid(entities)
        assert len(valid) == 1
        assert valid[0].class_name == "person"

    def test_compute_noise_ratio(self):
        scanner = EntityQualityScanner()
        entities = [
            _make_entity(seen=1, confidence=0.2),  # noise
            _make_entity(seen=10, confidence=0.9),  # not noise
            _make_entity(seen=1, confidence=0.3),   # noise
            _make_entity(seen=50, confidence=0.9),  # not noise
        ]
        ratio = scanner.compute_noise_ratio(entities)
        assert ratio == 0.5

    def test_compute_noise_ratio_empty(self):
        scanner = EntityQualityScanner()
        assert scanner.compute_noise_ratio([]) == 0.0

    def test_find_merge_candidates_same_class(self):
        from runtime.interest.entity_registry import _signature_distance

        scanner = EntityQualityScanner()
        e1 = _make_entity(class_name="person", seen=10, confidence=0.8)
        e2 = _make_entity(class_name="person", seen=8, confidence=0.7)
        # Very similar signatures
        e1.visual_signature = (15.0, 80.0, 120.0, 60.0, 150.0)
        e2.visual_signature = (17.0, 82.0, 118.0, 62.0, 148.0)
        e1.first_seen = time.time() - 60  # old enough
        e2.first_seen = time.time() - 60

        candidates = scanner.find_merge_candidates(
            [e1, e2], _signature_distance,
        )
        assert len(candidates) == 1
        # primary is the one with higher seen_count
        assert candidates[0][0] == e1  # seen=10 > seen=8
        assert candidates[0][1] == e2

    def test_find_merge_candidates_different_class_no_merge(self):
        from runtime.interest.entity_registry import _signature_distance

        scanner = EntityQualityScanner()
        e1 = _make_entity(class_name="person", seen=10, confidence=0.8)
        e2 = _make_entity(class_name="chair", seen=10, confidence=0.8)
        e1.visual_signature = (15.0, 80.0, 120.0, 60.0, 150.0)
        e2.visual_signature = (17.0, 82.0, 118.0, 62.0, 148.0)
        e1.first_seen = time.time() - 60
        e2.first_seen = time.time() - 60

        candidates = scanner.find_merge_candidates(
            [e1, e2], _signature_distance,
        )
        assert len(candidates) == 0

    def test_find_merge_candidates_fresh_entities_not_merged(self):
        from runtime.interest.entity_registry import _signature_distance

        scanner = EntityQualityScanner()
        e1 = _make_entity(class_name="person", seen=5)
        e2 = _make_entity(class_name="person", seen=3)
        e1.visual_signature = (15.0, 80.0, 120.0, 60.0, 150.0)
        e2.visual_signature = (17.0, 82.0, 118.0, 62.0, 148.0)
        # Just created — too fresh to merge
        e1.first_seen = time.time() - 1
        e2.first_seen = time.time() - 1

        candidates = scanner.find_merge_candidates(
            [e1, e2], _signature_distance,
        )
        assert len(candidates) == 0

    def test_compute_merge_rate(self):
        scanner = EntityQualityScanner()
        rate = scanner.compute_merge_rate(merge_count=3, entity_count=10)
        assert rate == 0.3


class TestEntityUpdateConfidence:
    """Entity.update_confidence EMA computation."""

    def test_initial_confidence(self):
        e = Entity(class_name="person")
        e.seen_count = 1
        e.update_confidence(0.9)
        assert e.avg_confidence == 0.9

    def test_ema_update(self):
        e = Entity(class_name="person")
        e.seen_count = 1
        e.update_confidence(0.9)  # initial
        e.mark_seen()  # seen_count → 2
        e.update_confidence(0.6)  # EMA: 0.3*0.6 + 0.7*0.9 = 0.81
        assert abs(e.avg_confidence - 0.81) < 0.01

    def test_multiple_ema_updates(self):
        e = Entity(class_name="person")
        e.seen_count = 1
        e.update_confidence(0.8)
        for _ in range(5):
            e.mark_seen()
            e.update_confidence(0.6)  # converges toward 0.6
        # After many updates, should be closer to 0.6
        assert 0.6 < e.avg_confidence < 0.75


class TestEntityIsValidForImportance:
    """Entity.is_valid_for_importance() convenience method."""

    def test_active_entity_is_valid(self):
        e = Entity(class_name="person", avg_confidence=0.8)
        e.seen_count = 10
        e.status = EntityStatus.ACTIVE
        assert e.is_valid_for_importance() is True

    def test_candidate_is_invalid(self):
        e = Entity(class_name="person", avg_confidence=0.8)
        e.seen_count = 10
        e.status = EntityStatus.CANDIDATE
        assert e.is_valid_for_importance() is False

    def test_low_confidence_is_invalid(self):
        e = Entity(class_name="clock", avg_confidence=0.3)
        e.seen_count = 10
        e.status = EntityStatus.ACTIVE
        assert e.is_valid_for_importance() is False
