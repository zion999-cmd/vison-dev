"""
Idle Behavior System — internal activity loop.

Behavior states:
  IDLE_SCAN      — rotating attention between areas
  IDLE_OBSERVE   — weak attention, person was here recently
  IDLE_REST      — deep idle, low sensitivity
  TRACKING       — person present, following
  ENGAGED        — person gazing, high engagement
  ACCOMPANYING   — person present > 20s, low novelty, stable: quiet companionship
"""

import time
import random
import logging
from typing import Dict, List

logger = logging.getLogger("Behavior")


class IdleBehaviorManager:
    """Maintains internal behavior state. No LLM calls — purely internal."""

    def __init__(self):
        self.state: str = "idle_scan"
        self._last_state: str = ""
        self._last_state_time: float = 0.0

        # Scan behavior
        self._scan_areas: List[str] = ["room", "desk", "door", "window", "shelf"]
        self._scan_idx: int = 0
        self._scan_interval: float = 4.0
        self._last_scan_switch: float = 0.0

        # Interest tracking
        self.interest: float = 0.0
        self._interest_decay: float = 0.98

        # Sensitivity (dynamic, lowered in rest and accompanying)
        self.sensitivity: float = 1.0
        self._rest_threshold: float = 300.0
        self._last_person_seen: float = 0.0

        # Accompanying entry conditions
        self._accompany_continuous: float = 20.0   # seconds of presence to enter
        self._accompany_novelty_max: float = 0.35  # must be below this (desk/expression bumps are 0.22-0.3)
        self._drift_timer: float = 0.0             # for attention drift during accompanying

        # Self-narrative
        self.thought: str = "booting..."
        self._last_thought_time: float = 0.0
        self._thought_interval: float = 8.0
        self._thoughts: Dict[str, List[str]] = {
            "idle_scan": [
                "scanning the room", "nothing unusual", "room quiet",
                "all clear", "just watching",
            ],
            "idle_observe": [
                "someone was here earlier", "keeping an eye out",
                "door area...", "still here, just quiet", "waiting",
            ],
            "idle_rest": [
                "room empty for a while", "everything stable",
                "quiet hours", "environment unchanged",
            ],
            "tracking": [
                "someone's here", "watching", "paying attention", "present",
            ],
            "engaged": [
                "they're looking this way", "interaction possible",
                "engaged", "attention on me",
            ],
            "accompanying": [
                "still here together", "quiet presence",
                "room feels calm", "just being",
                "no need to think",
            ],
        }

    # ── Public API ──

    def update(
        self,
        scene_state: Dict,
        focus_info: Dict,
        presence_info: Dict,
        frame_count: int,
    ) -> Dict:
        """
        Returns dict for overlay: {state, thought, scan_area, interest, sensitivity, drift_ok}
        """
        now = time.time()
        user_present = scene_state.get("user_present", False)
        is_gazing = focus_info.get("mode") == "tracking" and presence_info.get("engagement", 0) > 0.6
        person_was_recent = presence_info.get("familiarity", 0) > 0.1
        novelty = presence_info.get("novelty", 0)
        motion = scene_state.get("motion_level", 0)
        continuous = presence_info.get("continuous_presence", 0)

        # Track last person seen (do this BEFORE state check)
        if user_present:
            self._last_person_seen = now

        # ── Determine behavior state ──

        # If currently accompanying, relax user_present to prevent flicker-exit
        _user_stable = user_present or (self.state == "accompanying" and now - self._last_person_seen < 1.5)

        # Accompanying: person stable > 20s, low novelty, low motion
        # Once entered, sticky — only exit if user truly gone for 3s+
        _acc_enter = (_user_stable and continuous >= self._accompany_continuous and
                      novelty < self._accompany_novelty_max and motion < 0.3)
        _acc_stay = (self.state == "accompanying" and
                     (now - self._last_person_seen) < 3.0 and
                     continuous >= self._accompany_continuous)

        if _acc_enter or _acc_stay:
            new_state = "accompanying"
        elif user_present and is_gazing:
            new_state = "engaged"
        elif user_present and focus_info.get("has_focus"):
            new_state = "tracking"
        elif person_was_recent and continuous > 0:
            new_state = "idle_observe"
        elif not user_present and self.interest < 0.05:
            idle_duration = now - self._last_person_seen if self._last_person_seen > 0 else 0
            new_state = "idle_rest" if idle_duration > self._rest_threshold else "idle_scan"
        else:
            new_state = "idle_scan"

        # Hysteresis: minimum 1s per state
        if new_state != self.state and self._state_stable_for(now, 1.0):
            self._last_state = self.state
            self.state = new_state
            self._last_state_time = now
            logger.debug("Behavior: %s → %s", self._last_state, new_state)

        # ── Interest ──
        if user_present:
            self.interest = min(1.0, self.interest + 0.05)
        else:
            self.interest *= self._interest_decay

        # ── Scan ──
        scan_area = self._scan_areas[self._scan_idx]
        if now - self._last_scan_switch > self._scan_interval:
            self._scan_idx = (self._scan_idx + 1) % len(self._scan_areas)
            self._last_scan_switch = now
            scan_area = self._scan_areas[self._scan_idx]

        # ── Sensitivity ──
        if self.state == "accompanying":
            target = 0.4
        elif self.state == "idle_rest":
            target = 0.5
        else:
            target = 1.0

        if self.sensitivity > target:
            self.sensitivity = max(target, self.sensitivity - 0.02)
        else:
            self.sensitivity = min(target, self.sensitivity + 0.02)

        # ── Attention drift during accompanying ──
        drift_ok = False
        if self.state == "accompanying":
            self._drift_timer += 0.2  # ~frame duration
            if self._drift_timer > 6.0:  # every 6s, allow attention to wander
                drift_ok = True
                self._drift_timer = 0.0

        # ── Self-narrative ──
        if now - self._last_thought_time > self._thought_interval:
            pool = self._thoughts.get(self.state, self._thoughts["idle_scan"])
            self.thought = random.choice(pool)
            self._last_thought_time = now

        return {
            "state": self.state,
            "thought": self.thought,
            "scan_area": scan_area,
            "interest": round(self.interest, 2),
            "sensitivity": round(self.sensitivity, 2),
            "drift_ok": drift_ok,
        }

    def _state_stable_for(self, now: float, min_duration: float) -> bool:
        return now - self._last_state_time >= min_duration
