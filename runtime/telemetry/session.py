"""
Session Event Log — high-level timeline, NOT per-frame spam.

Records only significant events:
  - Focus switches
  - Cognition triggers
  - State & behavior transitions
  - Expression changes
  - Emergency events
"""

import time
import logging
from pathlib import Path

logger = logging.getLogger("Telemetry")

SESSION_DIR = Path("logs/telemetry")


class SessionLog:
    """Writes high-level timeline events to a session file."""

    def __init__(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._file = SESSION_DIR / f"timeline_{ts}.log"
        self._last_state = ""
        self._last_behavior = ""

    def _write(self, category: str, detail: str):
        ts = time.strftime("%H:%M:%S")
        with open(self._file, "a") as f:
            f.write(f"{ts} [{category}] {detail}\n")

    # ── Public API ──

    def focus_switch(self, from_type: str, to_type: str, score: float):
        self._write("FOCUS", f"{from_type} → {to_type} (score={score:.2f})")

    def cognition(self, event_type: str, intent: str, is_vlm: bool = False):
        mode = "VLM" if is_vlm else "LLM"
        self._write("COGNITION", f"{mode}:{event_type} intent={intent}")

    def state_change(self, from_s: str, to_s: str, trigger: str):
        self._write("STATE", f"{from_s} → {to_s} ({trigger})")

    def behavior_change(self, from_s: str, to_s: str):
        if to_s != self._last_behavior:
            self._write("BEHAVIOR", f"{from_s} → {to_s}")
            self._last_behavior = to_s

    def expression(self):
        self._write("EXPRESSION", "change detected")

    def emergency(self):
        self._write("EMERGENCY", "triggered")

    def accompanying_enter(self, continuous: float):
        self._write("ACCOMPANYING", f"entered after {continuous:.0f}s")

    def accompanying_exit(self):
        self._write("ACCOMPANYING", "exited")
