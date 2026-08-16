"""
Entity Quality Gate — filter, merge, and validate entities before Importance.

Phase 7B: removes noise so importance signal can emerge cleanly.

    Detection → Entity Association → Entity Registry
        → Entity Quality Gate (NEW)
        → Importance Engine (UNCHANGED)

Three responsibilities:
1. Quality Gate — reject low-confidence / low-count / non-ACTIVE entities
2. Lightweight Merge — fuse fragmented duplicates (no embedding model)
3. Noise Detection — flag and suppress unstable entities

Metrics (observational, no decisions based on them yet):
    noise_ratio = rejected / total
    merge_rate  = merged / total
"""

import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from runtime.interest.entity import Entity, EntityStatus

logger = logging.getLogger("Importance.EntityQuality")


# ── Thresholds ──

MIN_SEEN_THRESHOLD = 3       # entity must have been seen >= this many times
CONF_THRESHOLD = 0.5          # avg detection confidence must be >= this
MERGE_SIG_DISTANCE = 40.0     # signature distance below which two entities may merge
MERGE_MIN_AGE = 10.0          # both entities must be at least this old (seconds) before merging


# ── Quality Gate ──

def is_valid_for_importance(entity: Entity) -> bool:
    """Entity must pass all gates before entering Importance calculations.

    Conditions (all required):
        - seen_count >= MIN_SEEN_THRESHOLD (not a flash-in-the-pan)
        - avg_confidence >= CONF_THRESHOLD (not a likely mis-detection)
        - status == ACTIVE (lifecycle gate)
    """
    return (
        entity.seen_count >= MIN_SEEN_THRESHOLD
        and entity.avg_confidence >= CONF_THRESHOLD
        and entity.status == EntityStatus.ACTIVE
    )


def is_noise(entity: Entity) -> bool:
    """Detect likely noise entities that shouldn't even enter the registry."""
    return (
        entity.seen_count < 2
        and entity.avg_confidence < CONF_THRESHOLD
    )


# ── Merge Engine ──

def _signature_key(entity: Entity) -> str:
    """Stable key for coarse bucketing (class-based)."""
    return entity.class_name or "unknown"


def _should_merge(e1: Entity, e2: Entity, sig_distance: float) -> bool:
    """Decide whether two entities are the same thing fragmented.

    Conditions:
        1. Signature distance < MERGE_SIG_DISTANCE
        2. Same class_name OR either has compatible aliases
        3. Both entities are at least MERGE_MIN_AGE old (avoid merging fresh candidates)
    """
    if sig_distance > MERGE_SIG_DISTANCE:
        return False

    now = time.time()
    if (now - e1.first_seen) < MERGE_MIN_AGE:
        return False
    if (now - e2.first_seen) < MERGE_MIN_AGE:
        return False

    # Same class → merge
    if e1.class_name and e2.class_name and e1.class_name == e2.class_name:
        return True

    # Compatible aliases (labels accumulated over time)
    e1_labels = {e1.class_name} | (set(e1.tags) if e1.tags else set())
    e2_labels = {e2.class_name} | (set(e2.tags) if e2.tags else set())
    if e1_labels & e2_labels:
        return True

    return False


def merge_entities(primary: Entity, secondary: Entity) -> Entity:
    """Merge secondary into primary's statistics.

    Returns primary (mutated in-place for registry simplicity, but conceptually
    the merged result). Secondary SHOULD be marked FORGOTTEN by the caller.

    Merge strategy:
        - interaction_count, seen_count: accumulated
        - event_types: union
        - avg_confidence: weighted average
        - keep higher-confidence entity's visual_signature
        - append secondary class_name to primary.tags if different
    """
    # Weighted average confidence (before counts are accumulated)
    p_seen_pre = primary.seen_count
    s_seen_pre = secondary.seen_count
    total_seen = p_seen_pre + s_seen_pre
    if total_seen > 0:
        primary.avg_confidence = (
            primary.avg_confidence * p_seen_pre
            + secondary.avg_confidence * s_seen_pre
        ) / total_seen

    # Accumulate counts
    primary.interaction_count += secondary.interaction_count
    primary.seen_count += secondary.seen_count
    primary.tracking_count += secondary.tracking_count
    primary.state_transition_count += secondary.state_transition_count
    primary.cognition_trigger_count += secondary.cognition_trigger_count
    primary.speech_related_count += secondary.speech_related_count

    # Union event types
    primary.event_types = primary.event_types | secondary.event_types

    # Keep higher-confidence signature
    if secondary.avg_confidence > primary.avg_confidence:
        primary.visual_signature = secondary.visual_signature
        primary.last_bbox = secondary.last_bbox
        primary.last_pan = secondary.last_pan
        primary.last_tilt = secondary.last_tilt

    # Preserve secondary class_name as tag if different
    if secondary.class_name and secondary.class_name != primary.class_name:
        if secondary.class_name not in primary.tags:
            primary.tags.append(secondary.class_name)

    # Session familiarity: take max (seen more = more familiar)
    primary.session_seen_count = max(
        primary.session_seen_count, secondary.session_seen_count
    )
    primary.familiarity_score = max(
        primary.familiarity_score, secondary.familiarity_score
    )

    # Interest: take max (entity that's interesting in either form stays interesting)
    primary.interest = max(primary.interest, secondary.interest)
    primary.uncertainty = min(primary.uncertainty, secondary.uncertainty)

    logger.info(
        "Merged %s (%s, seen=%d) into %s (%s, seen=%d)",
        secondary.entity_id, secondary.class_name, secondary.seen_count,
        primary.entity_id, primary.class_name, primary.seen_count,
    )

    return primary


# ── Batch Quality Scanner ──

class EntityQualityScanner:
    """Scans entity registry for merge candidates and noise.

    Stateless — operates on a snapshot of entities.
    """

    def __init__(self):
        self._total_processed: int = 0
        self._total_rejected: int = 0
        self._total_merged: int = 0

    def find_merge_candidates(
        self,
        entities: List[Entity],
        sig_distance_fn,
    ) -> List[Tuple[Entity, Entity]]:
        """Find pairs of entities that should be merged.

        Uses coarse bucketing by class_name to avoid O(n²) comparisons.
        """
        # Bucket by class_name
        buckets: Dict[str, List[Entity]] = {}
        for e in entities:
            if not e.is_active:
                continue
            key = _signature_key(e)
            buckets.setdefault(key, []).append(e)

        candidates: List[Tuple[Entity, Entity]] = []
        seen_pairs: Set[Tuple[str, str]] = set()

        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            for i in range(len(bucket)):
                for j in range(i + 1, len(bucket)):
                    e1, e2 = bucket[i], bucket[j]
                    pair_key = (min(e1.entity_id, e2.entity_id),
                                max(e1.entity_id, e2.entity_id))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    if e1.visual_signature is None or e2.visual_signature is None:
                        continue

                    sig_dist = sig_distance_fn(
                        e1.visual_signature, e2.visual_signature
                    )
                    if _should_merge(e1, e2, sig_dist):
                        # Keep the one with higher seen_count as primary
                        if e1.seen_count >= e2.seen_count:
                            candidates.append((e1, e2))
                        else:
                            candidates.append((e2, e1))

        return candidates

    def compute_noise_ratio(self, entities: List[Entity]) -> float:
        """Compute rejected / total ratio for observability."""
        total = len(entities)
        if total == 0:
            return 0.0
        rejected = sum(1 for e in entities if is_noise(e))
        self._total_processed = total
        self._total_rejected = rejected
        return rejected / total

    def compute_merge_rate(self, merge_count: int, entity_count: int) -> float:
        """Compute merged / total ratio."""
        if entity_count == 0:
            return 0.0
        self._total_merged = merge_count
        return merge_count / entity_count

    def filter_valid(self, entities: List[Entity]) -> List[Entity]:
        """Return only entities that pass the quality gate."""
        return [e for e in entities if is_valid_for_importance(e)]

    @property
    def noise_ratio(self) -> float:
        return self._total_rejected / max(self._total_processed, 1)

    @property
    def merge_rate(self) -> float:
        return self._total_merged / max(self._total_processed, 1)
