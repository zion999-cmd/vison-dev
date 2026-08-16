"""
Commitment Telemetry — observes HOLD/SWITCH/RELEASE behavior.

Answers "did the Dwell Policy change what the Runtime actually does?" —
not "is the formula correct?". Decision reasons are drawn from a fixed
enum (no free-form natural language).

All output goes through standard logging (logger "Commitment.Telemetry"),
same pattern as mission_telemetry.py — no separate file I/O.
"""

import logging
from collections import Counter

from runtime.commitment.engine import compute_commitment, CommitmentState, Decision

logger = logging.getLogger("Commitment.Telemetry")

_SEP = "─" * 50


class CommitmentTelemetry:
    """Collects and logs commitment effectiveness metrics.

    Hooks (called from CommitmentEngine):
      - on_start(state)
      - on_decision(state, decision, reason, challenger_curiosity, now)
    """

    def __init__(self):
        self.start_count = 0
        self.hold_count = 0
        self.switch_count = 0
        self.release_count = 0
        self.reasons = Counter()

    # ── Event hooks ──

    def on_start(self, state: CommitmentState):
        self.start_count += 1
        logger.info(
            "Commitment Start: class=%s role=%.2f mission=%.2f",
            state.target_class, state.intrinsic_role, state.mission_boost,
        )

    def on_decision(self, state: CommitmentState, decision: Decision,
                    reason: str, challenger_curiosity: float, now: float):
        if decision == Decision.HOLD:
            self.hold_count += 1
        elif decision == Decision.SWITCH:
            self.switch_count += 1
        elif decision == Decision.RELEASE:
            self.release_count += 1
        self.reasons[reason] += 1

        duration = now - state.started_at
        score = compute_commitment(state, now)
        logger.info(
            "\n%s\n"
            "Commitment %s\n"
            "%s\n"
            "  class        : %s\n"
            "  duration     : %.0fs\n"
            "  commitment   : %.2f (peak %.2f)\n"
            "  challenger   : %.2f\n"
            "  reason       : %s\n"
            "%s",
            _SEP, decision.value.upper(), _SEP,
            state.target_class, duration, score, state.peak_commitment,
            challenger_curiosity, reason, _SEP,
        )

    # ── Aggregate ──

    def log_effectiveness(self):
        """Periodic aggregate of HOLD/SWITCH/RELEASE ratios."""
        total = self.hold_count + self.switch_count + self.release_count
        if total == 0:
            return
        logger.info(
            "\n%s\n"
            "Commitment Effectiveness\n"
            "%s\n"
            "  spans    : %d\n"
            "  hold     : %d (%.0f%%)\n"
            "  switch   : %d (%.0f%%)\n"
            "  release  : %d (%.0f%%)\n"
            "  reasons  : %s\n"
            "%s",
            _SEP, _SEP,
            self.start_count,
            self.hold_count, self.hold_count / total * 100,
            self.switch_count, self.switch_count / total * 100,
            self.release_count, self.release_count / total * 100,
            dict(self.reasons), _SEP,
        )
