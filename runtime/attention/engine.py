"""
Attention - L4: Attention Engine
Dynamic weighted scoring + decay + weight self-evolution.
"""

import time
import logging
from typing import List, Dict, Optional
from config import ATTENTION_THRESHOLD, ATTENTION_DECAY_FACTOR, ATTENTION_TOP_K

logger = logging.getLogger("L4.Attention")


# DNA-level attention priors
BASE_WEIGHTS = {
    "human_face": 0.9,
    "voice_detected": 0.95,
    "large_motion": 0.6,
    "new_object": 0.65,       # world changes — important for environment awareness
    "background_change": 0.1,
    "user_entered": 0.85,
    "gaze_started": 0.85,       # "user started looking at me" — high salience
    "gaze_maintained": 0.3,     # attention reinforcement only, no cognition
    "gaze_lost": 0.5,           # user stopped looking — state transition
}

# Which event types each hour-bucket boosts
_HOUR_PATTERNS: Dict[str, List[int]] = {
    "human_face": list(range(8, 23)),
    "voice_detected": list(range(8, 23)),
    "large_motion": list(range(6, 24)),
    "new_object": list(range(9, 22)),
}


class AttentionEngine:
    """Dynamic attention scoring with decay, self-evolution, and gaze session tracking."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self._weights = dict(weights or BASE_WEIGHTS)
        self._last_scores: Dict[str, float] = {}
        self._last_update = time.time()
        self._trigger_history: List[Dict] = []  # for evolution
        self._gaze_active = False               # gaze session state
        self._interest_engine = None            # set via set_interest_engine()

    def set_interest_engine(self, engine):
        """Wire InterestEngine — provides global vigilance signal."""
        self._interest_engine = engine

    @property
    def interest_vigilance(self) -> float:
        """Global attention boost from active interests. [0, 1]

        When the system has things it 'cares about', overall attention
        sensitivity increases slightly.  This creates a natural rhythm:
        quiet room → lower attention; something remembered → higher.
        """
        if not self._interest_engine:
            return 0.0
        tops = self._interest_engine.top_interests(3)
        if not tops:
            return 0.0
        return min(0.3, tops[0].interest * 0.3)  # max 30% boost

    # ── Public API ──

    def score_events(self, scene_state: Dict) -> List[Dict]:
        """
        Score events extracted from scene state.
        Returns scored events sorted by importance (descending).
        """
        now = time.time()
        delta_t = now - self._last_update
        self._last_update = now

        multiplier = scene_state.get("state_multiplier", 1.0)

        events = self._extract_events(scene_state)
        scored = []

        for event in events:
            base = self._weights.get(event["type"], 0.1)
            bonus = self._context_bonus(event, scene_state)
            time_bonus = self._time_bonus(event)
            score = min(1.0, (base + bonus) * multiplier + time_bonus)

            prev = self._last_scores.get(event["type"], 0.0)
            score = max(score, self._decay(prev, delta_t))

            event["score"] = round(score, 3)
            scored.append(event)

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:ATTENTION_TOP_K]

        for e in top:
            self._last_scores[e["type"]] = e["score"]

        for e in top:
            if e["score"] >= self._get_effective_threshold(scene_state):
                self._trigger_history.append({
                    "type": e["type"],
                    "time": now,
                    "score": e["score"],
                })

        return top

    @property
    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    @property
    def gaze_active(self) -> bool:
        return self._gaze_active

    def evolve_weights(self):
        """Self-evolve attention weights based on recent trigger patterns."""
        if len(self._trigger_history) < 5:
            return

        now = time.time()
        recent = [t for t in self._trigger_history if now - t["time"] < 1800]

        if len(recent) < 5:
            return

        from collections import Counter
        counts = Counter(t["type"] for t in recent)
        total = len(recent)

        for event_type, count in counts.items():
            ratio = count / total
            if ratio > 0.3 and event_type in self._weights:
                old = self._weights[event_type]
                new = min(0.98, old + 0.02)
                if new != old:
                    self._weights[event_type] = new
                    logger.info("⚡ Weight evolved: %s %.2f → %.2f (freq=%.0f%%)",
                                event_type, old, new, ratio * 100)

        self._trigger_history = recent[-100:]

    # ── Private ──

    def _get_effective_threshold(self, state: Dict) -> float:
        mul = state.get("state_multiplier", 1.0)
        return ATTENTION_THRESHOLD * mul

    def _extract_events(self, state: Dict) -> List[Dict]:
        """Extract discrete events from scene state. Gaze uses session tracking."""
        events = []

        if state.get("user_present") and len(state.get("people", [])) > 0:
            events.append({"type": "human_face", "detail": "faces"})

            # Gaze session: emit gaze_started on transition, gaze_maintained per frame
            is_gazing = self._is_sustained(state)
            if is_gazing and not self._gaze_active:
                self._gaze_active = True
                events.append({"type": "gaze_started", "detail": "user_began_looking"})
                logger.info("👁 gaze_started")
            elif is_gazing:
                events.append({"type": "gaze_maintained", "detail": "gaze_ongoing"})
            elif not is_gazing and self._gaze_active:
                self._gaze_active = False
                events.append({"type": "gaze_lost", "detail": "user_stopped_looking"})
                logger.info("👁 gaze_lost")

        else:
            # User not present → reset gaze
            if self._gaze_active:
                self._gaze_active = False
                events.append({"type": "gaze_lost", "detail": "user_gone"})

        if state.get("motion_level", 0) > 0.4:
            events.append({"type": "large_motion", "detail": f"motion_level={state['motion_level']}"})

        if state.get("desk_changed"):
            events.append({"type": "new_object", "detail": f"objects={len(state.get('objects', []))}"})

        if state.get("voice_activity"):
            events.append({"type": "voice_detected", "detail": "voice_activity"})

        if state.get("user_present") and self._last_scores.get("user_entered", 0) < 0.1:
            events.append({"type": "user_entered", "detail": "user_came_into_view"})

        if not events:
            events.append({"type": "background_change", "detail": "ambient"})

        return events

    def _context_bonus(self, event: Dict, state: Dict) -> float:
        """Compute context-based bonus for an event."""
        bonus = 0.0
        if event["type"] == "human_face":
            if state.get("voice_activity"):
                bonus += 0.2
            if state.get("motion_level", 0) > 0.3:
                bonus += 0.1
        if event["type"] == "gaze_maintained":
            # Slight boost during focus state — reinforces attention
            if state.get("runtime_state") == "focus":
                bonus += 0.05
        if event["type"] == "large_motion" and state.get("runtime_state") == "focus":
            bonus += 0.15
        if event["type"] == "new_object":
            if state.get("voice_activity"):
                bonus += 0.2  # object + sound = multi-modal salience
        return bonus

    def _time_bonus(self, event: Dict) -> float:
        hour = time.localtime().tm_hour
        peak_hours = _HOUR_PATTERNS.get(event["type"], [])
        if hour in peak_hours:
            return 0.05
        return 0.0

    def _decay(self, prev_score: float, delta_t: float) -> float:
        decay = ATTENTION_DECAY_FACTOR ** delta_t
        return prev_score * decay

    def _is_sustained(self, state: Dict) -> bool:
        """User has been continuously present for > 2 seconds."""
        return state.get("user_present", False) and state.get("state_duration", 0) > 2.0

    @property
    def threshold(self) -> float:
        return ATTENTION_THRESHOLD
