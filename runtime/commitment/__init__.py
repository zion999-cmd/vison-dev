"""Commitment / Dwell Policy (P0008.1).

Separates "worth re-exploring" (curiosity) from "worth continuing to watch"
(commitment). The CommitmentEngine arbitrates between the current target and
a challenger, emitting only HOLD / SWITCH / RELEASE.
"""

from runtime.commitment.engine import (
    CommitmentEngine,
    CommitmentState,
    Decision,
    compute_commitment,
    decide,
)

__all__ = [
    "CommitmentEngine",
    "CommitmentState",
    "Decision",
    "compute_commitment",
    "decide",
]
