"""
Commitment Engine — protects the current observation target.

    Curiosity  → "is a NEW target worth looking at?"
    Commitment → "is the CURRENT target still worth watching?"

Curiosity and commitment are different questions. The curiosity formula
(interest × uncertainty × freshness × (1−familiarity) × role − cost) collapses
to ~0 for a continuously-present, familiar person — which is correct for
"should I re-explore them?", but wrong for "should I keep watching them?".

P0008.1 adds a lightweight arbiter that keeps the current target held (HOLD)
unless a challenger is clearly stronger (SWITCH) or the target is gone /
stale / over-stayed (RELEASE).

Key invariants:
- Familiarity is deliberately NOT a negative input here. Familiar ≠ unworthy
  of companionship.
- No LLM, no new perception models. Only consumes existing Runtime signals
  (role, mission, track presence).
- No persistence, no cross-session state. Cleared on switch/release.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger("Commitment")

# ── Tuning (module-level, single explainable knobs) ──
# ROLE_WEIGHT scales the role component so a "present person" lands ~0.8,
# leaving headroom for a top-curiosity challenger (~1.0) to still SWITCH
# (success criterion #4 / Scenario B).
ROLE_WEIGHT = 0.6
SWITCH_MARGIN = 0.15        # challenger must beat commitment by this to switch
PRESENCE_WINDOW = 15.0      # s: a recent confirm counts as "present"
PRESENCE_WEIGHT = 0.20      # bonus granted while present
STALE_TIMEOUT = 60.0        # s without confirm → disengagement reaches full
SAFETY_MAX_DWELL = 1800.0   # s: hard cap on one span (30 min, anti-死盯)

# Fallback intrinsic role when no RoleEngine is wired (tests / degraded mode).
DEFAULT_INTRINSIC_ROLE = 0.2


class Decision(Enum):
    HOLD = "hold"
    SWITCH = "switch"
    RELEASE = "release"


@dataclass
class CommitmentState:
    """Lightweight state for the current observation target.

    v1 is class-scoped (target_class, e.g. "person") rather than entity-id
    scoped — the tracking path operates on face/person bboxes, not the entity
    registry. Binding to entity_id is a later refinement.
    """

    target_class: str
    started_at: float
    last_confirmed_at: float
    intrinsic_role: float = 0.0       # raw intrinsic role weight (0..1)
    mission_boost: float = 0.0        # raw mission boost (0..~0.35)
    peak_commitment: float = 0.0      # highest score observed this span


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_commitment(state: CommitmentState, now: float) -> float:
    """Commitment score for the current target.

    role + mission + presence − disengagement, clamped to [0, 1].
    NOT the curiosity formula; familiarity is deliberately absent.
    """
    role = ROLE_WEIGHT * state.intrinsic_role
    mission = state.mission_boost
    since_confirm = now - state.last_confirmed_at
    presence = PRESENCE_WEIGHT if since_confirm < PRESENCE_WINDOW else 0.0
    disengagement = min(1.0, since_confirm / STALE_TIMEOUT)
    return _clamp01(role + mission + presence - disengagement)


def _hold_reason(state: CommitmentState, now: float) -> str:
    """Dominant component keeping us on this target (for telemetry)."""
    role = ROLE_WEIGHT * state.intrinsic_role
    mission = state.mission_boost
    presence = PRESENCE_WEIGHT if (now - state.last_confirmed_at) < PRESENCE_WINDOW else 0.0
    best = max(
        ("hold_role", role),
        ("hold_mission", mission),
        ("hold_presence", presence),
        key=lambda kv: kv[1],
    )
    return best[0]


def decide(
    state: CommitmentState,
    challenger_curiosity: float,
    status: Optional[str],
    now: float,
) -> Tuple[Decision, str]:
    """Arbitrate: HOLD / SWITCH / RELEASE, with an explainable reason.

    status is the current target's lifecycle: "active", "lost", "forgotten",
    or None (no bound target). Decision order matches the proposal:
    release first (lost/stale/timeout), then switch (with hysteresis), else hold.
    """
    elapsed = now - state.started_at
    since_confirm = now - state.last_confirmed_at

    # 1. Target is gone.
    if status is None or status in ("lost", "forgotten"):
        return Decision.RELEASE, "release_lost"
    # 2. Present but unconfirmed too long.
    if since_confirm > STALE_TIMEOUT:
        return Decision.RELEASE, "release_stale"
    # 3. Hard safety cap — no target is watched forever.
    if elapsed > SAFETY_MAX_DWELL:
        return Decision.RELEASE, "release_timeout"

    # 4. A clearly-stronger challenger may take over (hysteresis margin).
    score = compute_commitment(state, now)
    if challenger_curiosity > score + SWITCH_MARGIN:
        return Decision.SWITCH, "switch_challenger"

    # 5. Keep watching.
    return Decision.HOLD, _hold_reason(state, now)


class CommitmentEngine:
    """Stateful arbiter over the current observation target.

    Usage (from RevisitController):
        engine.begin("person", now)                     # establish on track
        engine.confirm(now)                             # refresh on each hit
        decision, reason = engine.arbitrate(challenger, status, now)
    """

    def __init__(self, role_engine=None, telemetry=None):
        self._role_engine = role_engine
        self._telemetry = telemetry
        self._state: Optional[CommitmentState] = None

    @property
    def state(self) -> Optional[CommitmentState]:
        return self._state

    @property
    def has_commitment(self) -> bool:
        return self._state is not None

    def begin(self, target_class: str, now: float) -> CommitmentState:
        """Establish a commitment to target_class, or refresh an existing one."""
        if self._state is None or self._state.target_class != target_class:
            self._state = CommitmentState(
                target_class=target_class,
                started_at=now,
                last_confirmed_at=now,
                intrinsic_role=self._intrinsic_role(target_class),
                mission_boost=self._mission_boost(target_class),
            )
            if self._telemetry is not None:
                self._telemetry.on_start(self._state)
        else:
            self._state.last_confirmed_at = now
        return self._state

    def confirm(self, now: float):
        """Record a fresh confirmation of the current target."""
        if self._state is not None:
            self._state.last_confirmed_at = now

    def reset(self):
        """Force-clear the current commitment (e.g. false-positive target)."""
        self._state = None

    def arbitrate(
        self,
        challenger_curiosity: float,
        status: Optional[str],
        now: float,
    ) -> Tuple[Decision, str]:
        """Decide HOLD/SWITCH/RELEASE. Clears state on RELEASE."""
        if self._state is None:
            return Decision.RELEASE, "release_lost"

        decision, reason = decide(self._state, challenger_curiosity, status, now)

        score = compute_commitment(self._state, now)
        if score > self._state.peak_commitment:
            self._state.peak_commitment = score

        if self._telemetry is not None:
            self._telemetry.on_decision(
                self._state, decision, reason, challenger_curiosity, now
            )

        if decision == Decision.RELEASE:
            self._state = None

        return decision, reason

    def _intrinsic_role(self, class_name: str) -> float:
        if self._role_engine is not None:
            return self._role_engine.intrinsic_weight(class_name)
        return DEFAULT_INTRINSIC_ROLE

    def _mission_boost(self, class_name: str) -> float:
        if self._role_engine is not None:
            return self._role_engine.mission_boost(class_name)
        return 0.0
