"""
Telemetry Collector — per-minute vital signs of the Runtime.

Tracks:
  - Focus: switch count, target type distribution
  - Cognition: VLM calls, LLM calls, suppressed events
  - Behavior: state distribution
  - Presence: novelty avg/peak, attention events

Outputs one summary line per minute to telemetry log.
"""

import time
import logging
from pathlib import Path
from collections import Counter
from typing import Dict, List

logger = logging.getLogger("Telemetry")

TELEMETRY_DIR = Path("logs/telemetry")


class MinuteCollector:
    """Aggregates runtime metrics per minute, writes summary, resets."""

    def __init__(self):
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._file = TELEMETRY_DIR / f"vitals_{ts}.log"

        self._reset()

    def _reset(self):
        self._minute_start = time.time()
        self._focus_switches = 0
        self._focus_types: Counter = Counter()
        self._vlm_calls = 0
        self._llm_calls = 0
        self._suppressed = 0
        self._cog_types: Counter = Counter()
        self._behavior_ticks: Counter = Counter()
        self._novelty_sum = 0.0
        self._novelty_peak = 0.0
        self._novelty_samples = 0
        self._attention_above_threshold = 0
        self._emergency = 0

    # ── Called from main loop ──

    def record_focus_switch(self, from_type: str, to_type: str):
        self._focus_switches += 1
        self._focus_types[to_type] += 1

    def record_cognition(self, event_type: str, was_vlm: bool):
        if was_vlm:
            self._vlm_calls += 1
        else:
            self._llm_calls += 1
        self._cog_types[event_type] += 1

    def record_suppressed(self):
        self._suppressed += 1

    def record_behavior(self, state: str):
        self._behavior_ticks[state] += 1

    def record_novelty(self, novelty: float):
        self._novelty_sum += novelty
        self._novelty_samples += 1
        if novelty > self._novelty_peak:
            self._novelty_peak = novelty

    def record_attention_above_threshold(self):
        self._attention_above_threshold += 1

    def record_emergency(self):
        self._emergency += 1

    # ── Per-minute flush ──

    def maybe_flush(self, now: float) -> bool:
        """If a minute has passed, write summary and reset. Returns True if flushed."""
        if now - self._minute_start < 60.0:
            return False

        # Compute averages
        nov_avg = self._novelty_sum / max(self._novelty_samples, 1)
        focus_dist = dict(self._focus_types.most_common(3)) if self._focus_types else {"none": 0}
        top_behavior = self._behavior_ticks.most_common(1)
        top_behavior_str = top_behavior[0][0] if top_behavior else "?"
        top_cog = self._cog_types.most_common(3) if self._cog_types else []

        line = (
            f"minute|switches={self._focus_switches}|"
            f"focus={focus_dist}|"
            f"vlm={self._vlm_calls}|llm={self._llm_calls}|"
            f"suppressed={self._suppressed}|"
            f"nov_avg={nov_avg:.2f}|nov_peak={self._novelty_peak:.2f}|"
            f"attn={self._attention_above_threshold}|"
            f"behavior={top_behavior_str}|"
            f"cognition={top_cog}|"
            f"emergency={self._emergency}"
        )

        with open(self._file, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")

        logger.debug("Telemetry flushed: %s", line)
        self._reset()
        return True
