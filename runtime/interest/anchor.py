"""
Spatial Anchor System — "where things are" not "what things are".

Animals don't track every object.  They know their territory:
  - This spot usually has a chair
  - That spot usually has a monitor
  - If something CHANGES at a spot → interesting

Architecture:
    YOLO detection + CameraState.pan/tilt
        → SpatialAnchor (a location in pan/tilt space)
        → baseline: set of object classes normally seen here
        → change detection: new/removed objects → novelty signal
        → Interest binds to anchors, NOT entities

Complexity: O(anchors), not O(entities).  A room has ~10-20 anchors.
"""

import time, math, threading
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class SpatialAnchor:
    """A place in the room that's been observed and may be worth checking again.

    NOT a "door" or "desk" — just "the spot at pan=-90, tilt=0".
    The system discovers what's there through repeated observation.
    """

    anchor_id: str
    pan: float
    tilt: float

    # Baseline — what classes are NORMALLY here
    baseline_objects: Set[str] = field(default_factory=set)
    _pending_objects: Dict[str, int] = field(default_factory=dict)  # object → sighting count (for adding)
    _absent_objects: Dict[str, int] = field(default_factory=dict)   # object → absence count (for removing)

    # Observation history
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_visited: float = 0.0
    visit_count: int = 0

    # Interest — driven by CHANGES from baseline
    interest: float = 0.3       # base interest in any observed location
    uncertainty: float = 0.0    # grows with time since last visit
    novelty: float = 0.0        # last detected change magnitude
    barren: bool = False        # anchor confirmed empty → skip revisits (temporary)
    barren_at: float = 0.0      # when barren was set (for auto-revive)
    suppressed: bool = False    # VLM confirmed static/irrelevant → skip (temporary, auto-revive)
    suppressed_at: float = 0.0  # when suppressed was set (for auto-revive)
    _empty_streak: int = 0      # consecutive observations with zero objects
    _last_change_classes: Set[str] = field(default_factory=set)  # for habituation
    _same_change_streak: int = 0  # consecutive times same classes changed
    common_objects: List[str] = field(default_factory=list)  # top classes seen here

    def age(self) -> float:
        return time.time() - self.first_seen

    def since_seen(self) -> float:
        return time.time() - self.last_seen

    def since_visited(self) -> float:
        if self.visit_count == 0:
            return float("inf")
        return time.time() - self.last_visited

    @property
    def curiosity_score(self) -> float:
        """How urgently should we look at this anchor?

        Formula: interest × uncertainty × freshness - cost
        Barren anchors (confirmed empty) return 0 — not worth revisiting.
        """
        # Barren is temporary — auto-revive after timeout to give second chance
        if self.barren:
            if time.time() - self.barren_at > _BARREN_TIMEOUT:
                self.barren = False
                self._empty_streak = 0
                self.interest = 0.2  # reset to low interest, let observation drive it
            else:
                return 0.0

        if self.suppressed:
            if time.time() - self.suppressed_at > _SUPPRESSED_TIMEOUT:
                self.suppressed = False
                self.interest = 0.15  # low interest, let observation re-evaluate
                self._empty_streak = 0
            else:
                return 0.0

        # Uncertainty: grows over time since last visit (tau=5min for regions)
        since = self.since_visited()
        uncertainty = 1.0 - math.exp(-since / 300.0)

        # Freshness: don't re-check immediately after visiting
        dt = self.since_visited()
        freshness = min(1.0, dt / 15.0) if dt < 60 else 1.0

        return max(0.0, self.interest * uncertainty * freshness)


# ── Anchor Manager ──

# Barren anchors auto-revive after this many seconds.
# Gives wall/corner positions a second chance — if a person walks there,
# the anchor is no longer empty and interest recovers naturally.
_BARREN_TIMEOUT = 600.0  # 10 minutes

# Suppressed anchors auto-revive after this many seconds.
# VLM can falsely flag interesting areas as "wall/obstruction" especially
# with low-quality models. Auto-revive gives them a second look later.
_SUPPRESSED_TIMEOUT = 1800.0  # 30 minutes

# COCO classes that don't appear indoors — filter from baseline to avoid
# YOLO misdetections (e.g. "traffic light" on a wall) becoming permanent
_INDOOR_FILTER = {
    "traffic light", "fire hydrant", "stop sign", "parking meter",
    "airplane", "boat", "train", "truck", "car", "motorcycle", "bus",
    "street sign", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "frisbee", "skis",
    "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket",
}


class AnchorManager:
    """Creates and maintains spatial anchors from perception + camera pose.

    Anchors are spaced ~30° apart in pan, ~15° in tilt — granular enough
    to distinguish different areas but coarse enough to avoid explosion.
    """

    def __init__(self, pan_spacing: float = 30.0, tilt_spacing: float = 15.0):
        self._anchors: Dict[str, SpatialAnchor] = {}
        self._lock = threading.Lock()
        self._pan_spacing = pan_spacing
        self._tilt_spacing = tilt_spacing

    # ── Public API ──

    def observe(
        self,
        objects: List[Dict],    # YOLO detections with "class_name"
        pan: float,
        tilt: float,
    ):
        """Process one frame of YOLO detections at current camera pose.

        Creates or updates the anchor nearest to (pan, tilt).
        Detects changes from baseline → updates novelty/interest.
        """
        # Build class→max_confidence map (if multiple detections of same class)
        class_conf: Dict[str, float] = {}
        for obj in objects:
            cname = obj.get("class_name", "unknown")
            conf = float(obj.get("confidence", 0.5))
            if cname not in _INDOOR_FILTER:
                class_conf[cname] = max(class_conf.get(cname, 0.0), conf)
        current_classes = set(class_conf.keys())

        with self._lock:
            anchor = self._get_or_create_anchor_locked(pan, tilt)

            # First observation: establish baseline
            if not anchor.baseline_objects:
                anchor.baseline_objects = current_classes.copy()
                anchor.common_objects = sorted(current_classes)[:5]
                anchor.last_seen = time.time()
                return  # no change to detect yet

            # Detect changes from baseline
            appeared = current_classes - anchor.baseline_objects
            disappeared = anchor.baseline_objects - current_classes

            # ── Novelty: decays over time, spikes on changes ──
            now = time.time()
            minutes_since_seen = (now - anchor.last_seen) / 60.0 if anchor.last_seen > 0 else 0

            # Habituation: if the same classes keep appearing/disappearing,
            # it's probably a YOLO misdetection flickering — not real change.
            change_classes = appeared | disappeared
            if change_classes == anchor._last_change_classes and change_classes:
                anchor._same_change_streak += 1
            else:
                anchor._same_change_streak = 0
            anchor._last_change_classes = change_classes

            if appeared or disappeared:
                total = len(anchor.baseline_objects | current_classes)
                change_mag = (len(appeared) + len(disappeared)) / max(total, 1)
                # Habituation penalty: same change repeated → 50% less novelty per repeat
                habituation = max(0.1, 1.0 - 0.5 * anchor._same_change_streak)
                anchor.novelty = min(1.0, anchor.novelty + change_mag * 0.3 * habituation)
            else:
                # Decay: novelty fades at ~10%/min when nothing changes
                anchor.novelty *= max(0.05, 0.90 ** max(minutes_since_seen, 0.5))

            # ── Interest: very slow growth (with habituation), steady decay ──
            if appeared or disappeared:
                habituation = max(0.1, 1.0 - 0.5 * anchor._same_change_streak)
                anchor.interest = min(1.0, anchor.interest + 0.03 * len(change_classes) * habituation)
            else:
                # Decay 8% per observation when nothing changes
                anchor.interest = max(0.1, anchor.interest * 0.88)

            # ── Baseline updates with hysteresis in BOTH directions ──
            # Add: confidence-weighted accumulation, need ≥2.0 cumulative (≈ 3×0.67)
            for obj in appeared:
                conf = class_conf.get(obj, 0.5)
                anchor._pending_objects[obj] = anchor._pending_objects.get(obj, 0.0) + conf
                if anchor._pending_objects[obj] >= 2.0:
                    anchor.baseline_objects.add(obj)
                    anchor._pending_objects.pop(obj, None)

            # Remove: require 3 consecutive absences (new — prevents flicker)
            for obj in disappeared:
                anchor._absent_objects[obj] = anchor._absent_objects.get(obj, 0) + 1
                if anchor._absent_objects[obj] >= 3:
                    anchor.baseline_objects.discard(obj)
                    anchor._absent_objects.pop(obj, None)

            # Reset absence counter for objects that reappeared
            for obj in list(anchor._absent_objects.keys()):
                if obj in current_classes:
                    anchor._absent_objects.pop(obj, None)

            # ── Barren detection: empty anchor after repeated confirmations ──
            if current_classes or anchor.baseline_objects:
                anchor._empty_streak = 0
                anchor.barren = False
            else:
                anchor._empty_streak += 1
                if anchor._empty_streak >= 3:
                    if not anchor.barren:  # first time barren
                        anchor.barren_at = time.time()
                    anchor.barren = True

            # Update metadata
            anchor.last_seen = time.time()
            anchor.common_objects = sorted(current_classes)[:5]

    def mark_visited(self, anchor_id: str):
        """Record that we deliberately turned the camera to this anchor."""
        with self._lock:
            a = self._anchors.get(anchor_id)
            if a:
                a.last_visited = time.time()
                a.visit_count += 1

    def get_curiosity_targets(self, top_n: int = 10) -> List[SpatialAnchor]:
        """Anchors ranked by curiosity score (for PTZ decisions).
        Excludes empty-wall anchors with no baseline objects and low interest."""
        with self._lock:
            scored = [(a, a.curiosity_score) for a in self._anchors.values()
                      if not (not a.baseline_objects and a.interest < 0.15)]
            scored.sort(key=lambda x: x[1], reverse=True)
            return [a for a, s in scored[:top_n] if s > 0.01]

    def next_anchor(self, current_pan: float = 0.0,
                    cooldown_s: float = 60.0,
                    min_step: float = 45.0) -> Optional[SpatialAnchor]:
        """The anchor most worth looking at right now.

        Args:
            current_pan: current camera pan position (for distance penalty)
            cooldown_s: skip anchors visited within this many seconds
            min_step: minimum angular distance (°) from current_pan
        """
        candidates = self.get_curiosity_targets(20)
        if not candidates:
            return None

        now = time.time()
        best = None
        best_score = -1.0
        for a in candidates:
            # Cooldown: don't revisit too soon
            if a.last_visited > 0 and (now - a.last_visited) < cooldown_s:
                continue

            # Minimum step: prevent micro-oscillation between neighbors
            dist = abs(a.pan - current_pan)
            if dist < min_step:
                continue

            # Distance penalty: prefer closer anchors, but mildly
            penalty = min(0.2, dist / 360.0 * 0.3)
            score = a.curiosity_score - penalty
            if score > best_score:
                best = a
                best_score = score

        # Fallback: if all candidates are on cooldown, relax cooldown
        if best is None:
            for a in candidates:
                dist = abs(a.pan - current_pan)
                if dist < 10:
                    continue  # at least skip the exact same one
                penalty = min(0.2, dist / 360.0 * 0.3)
                score = a.curiosity_score - penalty
                if score > best_score:
                    best = a
                    best_score = score

        return best

    def all_anchors(self) -> List[SpatialAnchor]:
        with self._lock:
            return list(self._anchors.values())

    @property
    def anchor_count(self) -> int:
        with self._lock:
            return len(self._anchors)

    def unexplored_target(self, current_pan: float, current_tilt: float = 0.0
                          ) -> Optional[Tuple[float, float]]:
        """Suggest an unexplored pan/tilt to look at — exploration drive.

        Returns None if all nearby grid cells have anchors.
        """
        with self._lock:
            occupied = {(a.pan, a.tilt) for a in self._anchors.values()}

        # Search pan positions at init tilt (tilt range 95-180)
        p_spacing = int(self._pan_spacing)
        tilt_center = 95.0
        candidates = []
        for p in range(10, 171, p_spacing):
            p_f = float(p)
            if (p_f, tilt_center) not in occupied:
                dist = abs(p_f - current_pan)
                candidates.append((p_f, tilt_center, dist))

        if not candidates:
            return None

        # Prefer nearby unexplored cells (shorter PTZ moves)
        candidates.sort(key=lambda x: x[2])
        # Pick from top 5 closest, but with some randomness to avoid patterns
        import random
        pool = candidates[:min(5, len(candidates))]
        pick = random.choice(pool)
        return (pick[0], pick[1])

    # ── Internal ──

    def _snap(self, value: float, spacing: float) -> float:
        """Snap to nearest grid point, normalized to [-180, 180]."""
        v = round(value / spacing) * spacing
        if v > 180:
            v -= 360
        elif v <= -180:
            v += 360
        return v

    def _anchor_key(self, pan: float, tilt: float) -> str:
        snapped_pan = self._snap(pan, self._pan_spacing)
        snapped_tilt = self._snap(tilt, self._tilt_spacing)
        return f"anchor_{int(snapped_pan)}_{int(snapped_tilt)}"

    def _get_or_create_anchor_locked(self, pan: float, tilt: float) -> SpatialAnchor:
        """Must be called with self._lock held."""
        key = self._anchor_key(pan, tilt)
        if key not in self._anchors:
            self._anchors[key] = SpatialAnchor(
                anchor_id=key,
                pan=self._snap(pan, self._pan_spacing),
                tilt=self._snap(tilt, self._tilt_spacing),
            )
        return self._anchors[key]
