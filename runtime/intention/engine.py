"""
Intention - Inference Engine
Infers user intent from recent attention events + scene state.
Runs as a cheap heuristic layer between L4 (Attention) and L5 (Cognition).
"""

import time
import logging
from typing import List, Dict, Optional
from enum import Enum

logger = logging.getLogger("Intention")


class UserIntention(Enum):
    NONE = "none"
    APPROACHING = "approaching"        # user appearing/moving toward camera
    LOOKING_AT_CAMERA = "looking"      # sustained face, low motion
    LEAVING = "leaving"                # user present → gone
    SPEAKING = "speaking"              # voice + face
    GESTURING = "gesturing"            # face + high motion
    USING_DESK = "using_desk"          # objects changed, motion moderate
    EMERGENCY = "emergency"            # extreme motion / anomaly
    AMBIENT = "ambient"                # nothing specific


# Intention → how much it should boost cognition priority
INTENTION_PRIORITY = {
    UserIntention.EMERGENCY: 1.0,
    UserIntention.SPEAKING: 0.9,
    UserIntention.LOOKING_AT_CAMERA: 0.8,
    UserIntention.APPROACHING: 0.7,
    UserIntention.GESTURING: 0.6,
    UserIntention.USING_DESK: 0.4,
    UserIntention.LEAVING: 0.3,
    UserIntention.AMBIENT: 0.1,
    UserIntention.NONE: 0.0,
}


class IntentionEngine:
    """Infers user intention from scene + event context."""

    def __init__(self):
        self._last_intention = UserIntention.NONE
        self._user_was_present = False
        self._last_user_time = 0.0
        self._intention_history: List[Dict] = []

    def infer(self, scene_state: Dict, attention_events: List[Dict]) -> Dict:
        """
        Infer user intention from current state + recent attention events.

        Returns:
            {
                "intention": UserIntention,
                "confidence": float (0~1),
                "priority": float (0~1) for cognition scheduling,
                "label": str  (human-readable)
            }
        """
        state = scene_state
        now = time.time()
        user_present = state.get("user_present", False)
        motion = state.get("motion_level", 0)
        voice = state.get("voice_activity", False)
        desk_changed = state.get("desk_changed", False)
        event_types = [e["type"] for e in attention_events]

        intention = UserIntention.AMBIENT
        confidence = 0.3

        # ── Rule chain (ordered by priority) ──

        # 1. Emergency: extreme motion + no user
        if motion > 0.8 and not user_present and "large_motion" in event_types:
            intention = UserIntention.EMERGENCY
            confidence = 0.85

        # 2. Speaking: voice + face present
        elif voice and user_present:
            intention = UserIntention.SPEAKING
            confidence = 0.8

        # 3. Approaching: user just appeared
        elif user_present and not self._user_was_present:
            intention = UserIntention.APPROACHING
            confidence = 0.75

        # 4. Looking: face + low motion
        elif user_present and motion < 0.15 and "sustained_gaze" in event_types:
            intention = UserIntention.LOOKING_AT_CAMERA
            confidence = 0.7

        # 5. Gesturing: face + higher motion
        elif user_present and motion > 0.3:
            intention = UserIntention.GESTURING
            confidence = 0.6

        # 6. Using desk: desk changed + moderate motion
        elif desk_changed and 0.1 < motion < 0.5:
            intention = UserIntention.USING_DESK
            confidence = 0.5

        # 7. Leaving: user was present, now gone
        elif not user_present and self._user_was_present:
            intention = UserIntention.LEAVING
            confidence = 0.8

        # 8. Still present → just ambient interaction
        elif user_present:
            intention = UserIntention.AMBIENT
            confidence = 0.3

        # Track state
        if user_present:
            self._last_user_time = now
        self._user_was_present = user_present
        self._last_intention = intention

        priority = INTENTION_PRIORITY.get(intention, 0.0)

        result = {
            "intention": intention.value,
            "confidence": round(confidence, 2),
            "priority": priority,
            "label": intention.value,
        }

        # Log on change
        self._intention_history.append({
            "time": now,
            "intention": intention.value,
            "confidence": confidence,
        })
        if len(self._intention_history) > 200:
            self._intention_history = self._intention_history[-100:]

        if self._intention_history[-1]["intention"] != (
            self._intention_history[-2]["intention"] if len(self._intention_history) > 1 else None
        ):
            logger.info("🧠 Intention: %s (conf=%.2f)", intention.value, confidence)

        return result

    @property
    def last_goodbye_time(self) -> float:
        """When did the user last leave the scene."""
        return self._last_user_time
