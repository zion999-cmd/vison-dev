"""
Scene - L3: State Machine + Scene State
Tracks runtime state: Idle / Focus / Alert / Sleep
"""

import time
import logging
from enum import Enum
from typing import List, Dict, Optional
from config import SCENE_UPDATE_INTERVAL, STATE_DEBOUNCE_LEAVE

logger = logging.getLogger("L3.Scene")


class RuntimeState(Enum):
    IDLE = "idle"
    FOCUS = "focus"
    ALERT = "alert"
    SLEEP = "sleep"


# Transition rules
_STATE_TIMEOUTS = {
    RuntimeState.FOCUS: 30.0,   # no user for 30s → back to IDLE
    RuntimeState.ALERT: 60.0,   # no anomaly for 60s → back to IDLE
}

_TRANSITIONS = {
    RuntimeState.IDLE: {
        "user_entered": RuntimeState.FOCUS,
        "large_motion": RuntimeState.FOCUS,
        "sustained_gaze": RuntimeState.FOCUS,
        "emergency": RuntimeState.ALERT,
        "sleep_time": RuntimeState.SLEEP,
    },
    RuntimeState.FOCUS: {
        "user_left": RuntimeState.IDLE,
        "emergency": RuntimeState.ALERT,
        "timeout": RuntimeState.IDLE,
    },
    RuntimeState.ALERT: {
        "user_safe": RuntimeState.FOCUS,
        "timeout": RuntimeState.IDLE,
    },
    RuntimeState.SLEEP: {
        "wake": RuntimeState.IDLE,
    },
}


class SceneState:
    """Current scene state + runtime state machine."""

    def __init__(self):
        self._state = {
            "people": [],
            "objects": [],
            "motion_level": 0.0,
            "voice_activity": False,
            "last_event": None,
            "attention_targets": [],
            "user_present": False,
            "user_speaking": False,
            "desk_changed": False,
            "last_motion_time": 0.0,
            "intention": "none",
        }
        self._runtime_state = RuntimeState.IDLE
        self._state_enter_time = time.time()
        self._last_update = time.time()
        self._transition_events: List[Dict] = []
        self._user_vanished_at: float = 0.0  # debounce timer for user_left
        self._emergency_counter: float = 0.0  # temporal escalation
        self._obj_change_count: int = 0       # stability counter for desk_changed

    # ── State Machine ──

    @property
    def runtime_state(self) -> RuntimeState:
        return self._runtime_state

    @property
    def runtime_state_name(self) -> str:
        return self._runtime_state.value

    def _try_transition(self, event_type: str) -> bool:
        """Attempt a state transition. Returns True if transition happened."""
        current = self._runtime_state
        if current not in _TRANSITIONS:
            return False

        target = _TRANSITIONS[current].get(event_type)
        if target is None:
            return False

        old = current
        self._runtime_state = target
        self._state_enter_time = time.time()
        entry = {
            "time": time.time(),
            "from": old.value,
            "to": target.value,
            "trigger": event_type,
        }
        self._transition_events.append(entry)
        logger.info("🔄 State: %s → %s (trigger: %s)", old.value, target.value, event_type)
        return True

    def _check_timeout(self):
        """Check if current state should timeout."""
        if self._runtime_state not in _STATE_TIMEOUTS:
            return
        elapsed = time.time() - self._state_enter_time
        if elapsed > _STATE_TIMEOUTS[self._runtime_state]:
            self._try_transition("timeout")

    def update(
        self,
        people: Optional[List[Dict]] = None,
        motion_level: Optional[float] = None,
        objects: Optional[List[Dict]] = None,
        voice_activity: Optional[bool] = None,
        intention: Optional[str] = None,
        anchor_novelty: float = 0.0,
    ):
        """Update scene state + drive state machine."""
        now = time.time()
        triggered = False

        if people is not None:
            prev_present = self._state["user_present"]
            self._state["people"] = people
            self._state["user_present"] = len(people) > 0

            if not prev_present and self._state["user_present"]:
                # User appeared → clear debounce, transition immediately
                self._user_vanished_at = 0.0
                self._try_transition("user_entered")
                triggered = True
            elif prev_present and not self._state["user_present"]:
                # User vanished → start debounce timer, don't transition yet
                if self._user_vanished_at == 0.0:
                    self._user_vanished_at = now
            elif not prev_present and not self._state["user_present"]:
                # Still gone — check if debounce expired
                if self._user_vanished_at > 0.0 and (now - self._user_vanished_at) >= STATE_DEBOUNCE_LEAVE:
                    self._try_transition("user_left")
                    self._user_vanished_at = 0.0
                    triggered = True

        if motion_level is not None:
            if motion_level > 0.1:
                self._state["last_motion_time"] = now
            # Exponential smoothing
            self._state["motion_level"] = round(
                self._state["motion_level"] * 0.7 + motion_level * 0.3, 3
            )
            # Emergency: temporal escalation — accumulate, don't react to single spike
            if self._state["motion_level"] > 0.95:
                self._emergency_counter += 1
            else:
                self._emergency_counter *= 0.8  # decay
            if self._emergency_counter > 10:  # ~2 seconds of sustained extreme motion
                if self._try_transition("emergency"):
                    self._emergency_counter = 0
                    triggered = True

        if objects is not None:
            if objects:
                self._state["objects"] = objects

        # desk_changed: transient flag — only true when anchor baseline
        # actually changed (AnchorManager detected new/disappeared objects).
        # Auto-clears when novelty drops back.
        if anchor_novelty > 0.3:
            if getattr(self, '_novelty_count', 0) >= 1:
                self._state["desk_changed"] = True
                self._novelty_count = 0
            else:
                self._novelty_count = getattr(self, '_novelty_count', 0) + 1
        else:
            self._novelty_count = 0
            self._state["desk_changed"] = False  # auto-clear when no real change

        if voice_activity is not None:
            self._state["voice_activity"] = voice_activity

        if intention is not None:
            self._state["intention"] = intention

        # Check state timeout
        if not triggered:
            self._check_timeout()

        self._last_update = now

    def set_event(self, event: Dict):
        """Record the last significant event."""
        self._state["last_event"] = event

    def set_attention_targets(self, targets: List[Dict]):
        """Update attention targets."""
        self._state["attention_targets"] = targets

    # ── Getters ──

    def get(self) -> Dict:
        """Get current scene state snapshot."""
        s = dict(self._state)
        s["runtime_state"] = self._runtime_state.value
        s["state_duration"] = round(time.time() - self._state_enter_time, 1)
        s["mode"] = self._infer_mode()
        return s

    def _infer_mode(self) -> str:
        """Derive an operational mode hint from the raw state."""
        if self._runtime_state == RuntimeState.ALERT:
            return "alert"
        if self._state["user_present"]:
            return "interaction"
        if self._state["motion_level"] < 0.05:
            return "quiet"
        return "monitoring"

    @property
    def user_present(self) -> bool:
        return self._state["user_present"]

    @property
    def motion_level(self) -> float:
        return self._state["motion_level"]

    @property
    def attention_multiplier(self) -> float:
        """State-based attention modifier. FOCUS lowers threshold (more sensitive)."""
        modifiers = {
            RuntimeState.IDLE: 1.0,
            RuntimeState.FOCUS: 0.7,   # more sensitive in focus
            RuntimeState.ALERT: 0.5,   # very sensitive in alert
            RuntimeState.SLEEP: 2.0,   # less sensitive in sleep
        }
        return modifiers.get(self._runtime_state, 1.0)

    def __repr__(self) -> str:
        s = self._state
        return (
            f"Scene[{self._runtime_state.value}] "
            f"people={len(s['people'])}, "
            f"motion={s['motion_level']:.2f}, "
            f"intent={s['intention']}"
        )
