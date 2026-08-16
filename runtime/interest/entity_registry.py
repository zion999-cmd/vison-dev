"""
EntityRegistry — the bridge between YOLO detections and persistent entities.

    Detection (per-frame, fleeting)
        → Entity Association (visual + spatial match)
        → Entity (cross-frame, persistent)
        → Interest Engine

Responsibilities:
1. Match new detections to existing entities (visual signature + proximity)
2. Create CANDIDATE entities for unmatched detections
3. Promote CANDIDATE → ACTIVE after enough confirmations
4. Mark missed entities LOST → FORGOTTEN
5. Provide query interface for InterestEngine and RevisitController

This is the 'Entity Association' layer from the architecture doc.
"""

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from runtime.interest.entity import Entity, EntityStatus
from runtime.importance.entity_quality import (
    EntityQualityScanner,
    merge_entities,
    is_valid_for_importance,
    is_noise,
)

logger = logging.getLogger("Interest.EntityRegistry")

# Matching thresholds
_SIGNATURE_DIST_THRESHOLD = 60.0    # max HSV distance for same entity
_SPATIAL_RADIUS = 45.0              # max pan degrees for same entity

# HSV histogram distance weights (H more important than S, V)
_H_WEIGHT = 2.0
_S_WEIGHT = 0.5
_V_WEIGHT = 0.5
_SIZE_WEIGHT = 0.3


def _normalize_pan(pan: float) -> float:
    """Normalize pan angle to [-180, 180]."""
    while pan > 180:
        pan -= 360
    while pan <= -180:
        pan += 360
    return pan


def _pan_distance(pan1: float, pan2: float) -> float:
    """Shortest angular distance between two pan angles."""
    d = abs(pan1 - pan2)
    return min(d, 360 - d)


class EntityRegistry:
    """Manages entity lifecycle and detection-to-entity association."""

    def __init__(self, role_engine=None):
        self._entities: Dict[str, Entity] = {}
        self._lock = threading.Lock()
        self._role_engine = role_engine  # for setting role_weight on new entities

    # ── Public API ──

    def process_frame(
        self,
        frame: np.ndarray,
        detections: List[Dict],
        pan: float,
        tilt: float,
    ):
        """Process one frame of detections. Updates entity states.

        Args:
            frame: BGR image (for visual signature extraction)
            detections: YOLO output [{class_name, confidence, bbox}, ...]
            pan, tilt: current camera pose
        """
        if not detections:
            # No detections → all active entities get a miss
            with self._lock:
                for e in self._entities.values():
                    if e.is_active:
                        e.mark_missed()
            return

        # Compute visual signatures for each detection
        detection_sigs = []
        for det in detections:
            sig = _compute_signature(frame, det.get("bbox"))
            detection_sigs.append((det, sig))

        with self._lock:
            matched_entities: set = set()

            for det, sig in detection_sigs:
                best_entity = self._find_best_match(
                    sig, det.get("class_name", ""), pan, tilt, matched_entities
                )
                if best_entity:
                    self._update_entity(best_entity, det, sig, pan, tilt)
                    matched_entities.add(best_entity.entity_id)
                else:
                    self._create_candidate(det, sig, pan, tilt)

            # Any active entity not matched → mark missed
            for e in list(self._entities.values()):
                if (e.is_active
                        and e.entity_id not in matched_entities):
                    e.mark_missed()

            # Cleanup forgotten entities (keep max 200)
            forgotten = [
                eid for eid, e in self._entities.items()
                if e.status == EntityStatus.FORGOTTEN
            ]
            if len(forgotten) > 50:
                for eid in forgotten[:20]:
                    del self._entities[eid]

    def top_entities(self, n: int = 10) -> List[Entity]:
        """Entities ranked by curiosity score (for RevisitController).

        Excludes entities seen within the last 5s — those are the current
        frame's detections. Everything else is fair game for curiosity.
        """
        now = __import__("time").time()
        with self._lock:
            active = [
                e for e in self._entities.values()
                if e.is_active
                and e.interest > 0.01
                and (now - e.last_seen) >= 5.0  # exclude current-frame only
            ]
            active.sort(key=lambda e: e.curiosity_score, reverse=True)
            return active[:n]

    def all_entities(self) -> List[Entity]:
        with self._lock:
            return list(self._entities.values())

    @property
    def entity_count(self) -> int:
        with self._lock:
            return len(self._entities)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for e in self._entities.values() if e.is_active)

    def get(self, entity_id: str) -> Optional[Entity]:
        with self._lock:
            return self._entities.get(entity_id)

    def forget(self, entity_id: str):
        with self._lock:
            e = self._entities.get(entity_id)
            if e:
                e.status = EntityStatus.FORGOTTEN
                e.interest = 0.0

    # ── Phase 7B: Quality Gate & Merge ──

    def compact(self) -> Dict:
        """Run merge pass over active entities. Call periodically (~every 5 min).

        Returns stats dict for telemetry:
            {"merged": int, "noise_ratio": float, "merge_rate": float}
        """
        scanner = EntityQualityScanner()
        with self._lock:
            entities = list(self._entities.values())
            total = len(entities)

            # Step 1: Merge fragmented entities
            candidates = scanner.find_merge_candidates(
                entities,
                _signature_distance,
            )
            merged_count = 0
            for primary, secondary in candidates:
                if (primary.is_active and secondary.is_active
                        and primary.entity_id != secondary.entity_id):
                    merge_entities(primary, secondary)
                    secondary.status = EntityStatus.FORGOTTEN
                    secondary.interest = 0.0
                    merged_count += 1

            # Step 2: Compute metrics
            noise_ratio = scanner.compute_noise_ratio(entities)
            merge_rate = scanner.compute_merge_rate(merged_count, total)

        logger.info(
            "EntityRegistry compact: %d entities, %d merged, "
            "noise=%.3f, merge_rate=%.3f",
            total, merged_count, noise_ratio, merge_rate,
        )
        return {
            "merged": merged_count,
            "noise_ratio": noise_ratio,
            "merge_rate": merge_rate,
        }

    def valid_for_importance(self) -> List[Entity]:
        """Return only entities that pass the Phase 7B quality gate.

        Used by Importance Engine (stats_db) to filter before saving.
        """
        with self._lock:
            return [
                e for e in self._entities.values()
                if is_valid_for_importance(e)
            ]

    # ── Internal ──

    def _find_best_match(
        self,
        sig: Tuple[float, ...],
        class_name: str,
        pan: float,
        tilt: float,
        exclude: set,
    ) -> Optional[Entity]:
        """Find the best matching entity for a detection signature."""
        best = None
        best_dist = float("inf")

        for e in self._entities.values():
            if not e.is_active:
                continue
            if e.entity_id in exclude:
                continue
            if e.visual_signature is None:
                # No signature → match by class + proximity only
                if class_name and e.class_name == class_name:
                    spatial_dist = _pan_distance(e.last_pan, pan) + abs(e.last_tilt - tilt)
                    if spatial_dist < _SPATIAL_RADIUS:
                        return e  # immediate match
                continue

            # Signature distance
            sig_dist = _signature_distance(sig, e.visual_signature)
            if sig_dist > _SIGNATURE_DIST_THRESHOLD:
                continue

            # Class bonus: same class → closer match
            if class_name and e.class_name == class_name:
                sig_dist *= 0.6

            # Spatial proximity bonus
            spatial_dist = _pan_distance(e.last_pan, pan) + abs(e.last_tilt - tilt)
            if spatial_dist > _SPATIAL_RADIUS:
                continue
            sig_dist += spatial_dist * 0.3

            if sig_dist < best_dist:
                best = e
                best_dist = sig_dist

        return best

    def _update_entity(
        self,
        entity: Entity,
        detection: Dict,
        sig: Tuple[float, ...],
        pan: float,
        tilt: float,
    ):
        """Update entity with new detection."""
        entity.mark_seen()
        new_class = detection.get("class_name", "")
        if new_class and new_class != entity.class_name:
            entity.class_name = new_class
            # Recompute role_weight when class changes (YOLO reclassification)
            if self._role_engine:
                entity.role_weight = self._role_engine.get_weight(new_class)
        entity.last_bbox = detection.get("bbox")
        entity.last_pan = _normalize_pan(pan)
        entity.last_tilt = tilt
        # Blend visual signature (EMA with alpha=0.3)
        entity.visual_signature = _blend_signature(
            entity.visual_signature, sig, alpha=0.3
        ) if entity.visual_signature else sig
        # Update detection confidence (Phase 7B: quality gate)
        conf = detection.get("confidence", 0.0)
        entity.update_confidence(conf)

    def _create_candidate(
        self,
        detection: Dict,
        sig: Tuple[float, ...],
        pan: float,
        tilt: float,
    ):
        """Create a new CANDIDATE entity from an unmatched detection."""
        class_name = detection.get("class_name", "")
        entity = Entity(
            entity_type="object",
            class_name=class_name,
            visual_signature=sig,
            last_pan=_normalize_pan(pan),
            last_tilt=tilt,
            last_bbox=detection.get("bbox"),
            status=EntityStatus.CANDIDATE,
            interest=0.4 + detection.get("confidence", 0.5) * 0.4,  # 0.6-0.8
            role_weight=(self._role_engine.get_weight(class_name)
                         if self._role_engine else 0.2),
            avg_confidence=detection.get("confidence", 0.0),  # Phase 7B: quality gate
        )
        self._entities[entity.entity_id] = entity
        logger.debug("New candidate: %s (%s) at pan=%.0f conf=%.2f",
                     entity.entity_id, entity.class_name, pan, entity.avg_confidence)


# ── Visual Signature Utilities ──

def _compute_signature(frame: np.ndarray, bbox: Optional[Dict]) -> Tuple[float, ...]:
    """Extract lightweight visual signature from bbox crop.
    Returns (avg_H, avg_S, avg_V, bbox_w, bbox_h).
    """
    import cv2
    if bbox is None or frame is None:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    h, w = frame.shape[:2]
    x = max(0, int(bbox.get("x", 0)))
    y = max(0, int(bbox.get("y", 0)))
    bw = min(int(bbox.get("width", w)), w - x)
    bh = min(int(bbox.get("height", h)), h - y)

    if bw <= 0 or bh <= 0:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    crop = frame[y:y+bh, x:x+bw]
    if crop.size == 0:
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    avg_h = float(np.mean(hsv[:, :, 0]))
    avg_s = float(np.mean(hsv[:, :, 1]))
    avg_v = float(np.mean(hsv[:, :, 2]))

    return (avg_h, avg_s, avg_v, float(bw), float(bh))


def _signature_distance(
    sig1: Tuple[float, ...],
    sig2: Tuple[float, ...],
) -> float:
    """Weighted distance between two visual signatures."""
    h1, s1, v1, w1, hh1 = sig1
    h2, s2, v2, w2, hh2 = sig2

    h_dist = min(abs(h1 - h2), 180 - abs(h1 - h2))  # circular H
    return (
        _H_WEIGHT * h_dist
        + _S_WEIGHT * abs(s1 - s2)
        + _V_WEIGHT * abs(v1 - v2)
        + _SIZE_WEIGHT * (abs(w1 - w2) + abs(hh1 - hh2))
    )


def _blend_signature(
    old: Optional[Tuple[float, ...]],
    new: Tuple[float, ...],
    alpha: float = 0.3,
) -> Tuple[float, ...]:
    """EMA blend of old and new signatures (handles circular H)."""
    if old is None:
        return new
    h1, s1, v1, w1, hh1 = old
    h2, s2, v2, w2, hh2 = new
    # Circular mean for H
    h_diff = h2 - h1
    if h_diff > 180:
        h2 -= 360
    elif h_diff < -180:
        h2 += 360
    h = h1 + alpha * (h2 - h1)
    if h < 0:
        h += 360
    elif h >= 360:
        h -= 360
    return (
        h,
        s1 + alpha * (s2 - s1),
        v1 + alpha * (v2 - v1),
        w1 + alpha * (w2 - w1),
        hh1 + alpha * (hh2 - hh1),
    )
