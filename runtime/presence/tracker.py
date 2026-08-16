"""
Presence - Identity persistence & novelty gate.

Transforms raw detection events into a stable social presence model.
When the same person stays in frame, the system should NOT re-think
— it should enter stable_presence. Only novelty triggers cognition.

Core concepts:
  - Familiarity: how well the system "knows" the current person
  - Novelty: how much has actually changed since last cognition
  - Engagement: how actively the user is interacting with the system
"""

import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("Presence")


@dataclass
class KnownEntity:
    entity_id: str
    first_seen: float
    last_seen: float
    total_seen_seconds: float = 0.0
    familiarity: float = 0.0         # 0=new, 1=old friend
    times_seen: int = 0

    def bump(self, now: float, dt: float = 0.0):
        self.total_seen_seconds += dt
        self.last_seen = now
        self.times_seen += 1
        # Familiarity grows with exposure, capped at 0.95
        self.familiarity = min(0.95, self.total_seen_seconds / 120.0)


class PresenceTracker:
    """
    Tracks "who is here" across frames, computing novelty and stability.

    When novelty is low (same person, stable scene), cognition is gated off.
    """

    def __init__(self):
        self._known: Dict[str, KnownEntity] = {}
        self._current_entity_id: str = ""
        self._person_continuous: float = 0.0   # seconds of continuous presence
        self._last_present: float = 0.0
        self._scene_stable_since: float = 0.0

        # Output state
        self.stable: bool = False
        self.novelty: float = 0.0       # 0=completely familiar, 1=totally new
        self.engagement: float = 0.0    # 0=passive, 1=actively interacting
        self.familiarity: float = 0.0
        self.should_think: bool = True  # the gate
        self._last_landmarks: list = []  # for expression change detection

    # ── Public API ──

    def update(
        self,
        scene_state: Dict,
        focus_info: Dict,
        faces: List[Dict],
        objects: List[Dict],
    ) -> Dict:
        """
        Update presence model. Called every frame.

        Returns snapshot dict: {stable, novelty, engagement, familiarity, should_think}
        """
        now = time.time()
        user_present = scene_state.get("user_present", False)
        is_gazing = focus_info.get("mode") in ("tracking",) and focus_info.get("has_focus")

        # ── Identity tracking ──
        if user_present and faces:
            # Simple heuristic: if a person was seen recently, it's the same one
            entity = self._find_or_create_entity(now)
            dt = max(0.05, now - max(self._last_present, entity.last_seen - 0.2))
            entity.bump(now, dt)
            self._current_entity_id = entity.entity_id
            self._person_continuous += dt
            self._last_present = now
        elif user_present:
            # Voice or motion but no face — person might still be there
            self._last_present = now
        else:
            # Briefly absent — decay continuity, don't reset instantly
            self._person_continuous = max(0.0, self._person_continuous - 0.5)
            if self._person_continuous <= 0.0:
                self._current_entity_id = ""

        # ── Engagement ──
        if is_gazing:
            self.engagement = min(1.0, self.engagement + 0.05)  # ramp up
        else:
            self.engagement = max(0.0, self.engagement - 0.02)  # decay

        # ── Stability & Familiarity ──
        entity = self._known.get(self._current_entity_id)
        self.familiarity = entity.familiarity if entity else 0.0
        continuous_presence = self._person_continuous

        if continuous_presence > 5.0:
            self.stable = True
        elif continuous_presence < 2.0:
            self.stable = False

        # ── Novelty ──
        # Novelty spikes on meaningful change, then decays.
        # Uses "was" tracking to detect transitions, not sustained states.

        just_arrived = user_present and self._person_continuous < 1.5
        desk_just_changed = scene_state.get("desk_changed") and self.novelty < 0.3
        gaze_just_spiked = is_gazing and self.engagement > 0.7 and self.novelty < 0.3

        if just_arrived:
            self.novelty = 0.8
        elif desk_just_changed:
            self.novelty = max(self.novelty, 0.3)
        elif gaze_just_spiked:
            self.novelty = max(self.novelty, 0.2)
        else:
            # Nothing new — decay novelty
            self.novelty = max(0.0, self.novelty - 0.02)

        # ── Expression change detection (EMA-smoothed landmarks) ──
        if faces and faces[0].get("landmarks"):
            curr = faces[0]["landmarks"]
            # Smooth landmarks with EMA to filter jitter
            if self._last_landmarks and len(self._last_landmarks) == len(curr):
                smoothed = []
                for prev_lm, cur_lm in zip(self._last_landmarks, curr):
                    smoothed.append({
                        "x": prev_lm["x"] * 0.7 + cur_lm["x"] * 0.3,
                        "y": prev_lm["y"] * 0.7 + cur_lm["y"] * 0.3,
                    })
                mouth_change = self._mouth_movement(self._last_landmarks, smoothed)
                if mouth_change > 0.12:
                    self.novelty = max(self.novelty, 0.22)
                    logger.debug("Expression change detected (mouth=%.4f)", mouth_change)
                self._last_landmarks = smoothed
            else:
                self._last_landmarks = curr

        # ── The Gate ──
        # Only think when there's actually something new to process
        self.should_think = self.novelty > 0.3 or not self.stable

        return {
            "stable": self.stable,
            "novelty": round(self.novelty, 2),
            "engagement": round(self.engagement, 2),
            "familiarity": round(self.familiarity, 2),
            "should_think": self.should_think,
            "entity_id": self._current_entity_id,
            "continuous_presence": round(self._person_continuous, 1),
        }

    # ── Internals ──

    @staticmethod
    def _mouth_movement(prev: list, curr: list) -> float:
        """Compute normalized mouth landmark movement (0~1 scale)."""
        if len(prev) < 5 or len(curr) < 5:
            return 0.0
        # Mouth corners are landmarks 3 (right) and 4 (left)
        prev_r, prev_l = prev[3], prev[4]
        curr_r, curr_l = curr[3], curr[4]
        # Mouth width change
        prev_w = ((prev_r["x"] - prev_l["x"])**2 + (prev_r["y"] - prev_l["y"])**2) ** 0.5
        curr_w = ((curr_r["x"] - curr_l["x"])**2 + (curr_r["y"] - curr_l["y"])**2) ** 0.5
        # Eye distance for normalization
        prev_eye = ((prev[0]["x"] - prev[1]["x"])**2 + (prev[0]["y"] - prev[1]["y"])**2) ** 0.5
        curr_eye = ((curr[0]["x"] - curr[1]["x"])**2 + (curr[0]["y"] - curr[1]["y"])**2) ** 0.5
        if curr_eye < 1 or prev_eye < 1:
            return 0.0
        return abs(curr_w / curr_eye - prev_w / prev_eye)

    def _find_or_create_entity(self, now: float) -> KnownEntity:
        """Find existing entity or create a new one."""
        # Simple: if we had a recent entity (< 30s gap), it's the same person
        if self._current_entity_id and self._current_entity_id in self._known:
            last = self._known[self._current_entity_id].last_seen
            if now - last < 30.0:
                return self._known[self._current_entity_id]

        # If there's any recent entity (< 30s), reuse it
        best = None
        for eid, ent in self._known.items():
            if now - ent.last_seen < 30.0:
                if best is None or ent.last_seen > best.last_seen:
                    best = ent

        if best:
            return best

        # New entity
        eid = f"entity_{int(now)}"
        ent = KnownEntity(entity_id=eid, first_seen=now, last_seen=now)
        self._known[eid] = ent
        logger.info("New entity: %s", eid)
        return ent
