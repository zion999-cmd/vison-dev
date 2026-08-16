"""Tests for the Commitment / Dwell Policy (P0008.1).

Covers the CommitmentEngine's scoring and the HOLD/SWITCH/RELEASE arbiter,
mapped to the proposal's Validation Scenarios A/B/C.
"""

from runtime.commitment.engine import (
    CommitmentEngine,
    CommitmentState,
    Decision,
    compute_commitment,
    decide,
    SWITCH_MARGIN,
    STALE_TIMEOUT,
    SAFETY_MAX_DWELL,
    PRESENCE_WINDOW,
    ROLE_WEIGHT,
)


def _person_state(now, last_confirmed_at=None, intrinsic_role=1.0, mission_boost=0.0):
    """A CommitmentState for a present, high-role target (person)."""
    return CommitmentState(
        target_class="person",
        started_at=now - 10.0,
        last_confirmed_at=now if last_confirmed_at is None else last_confirmed_at,
        intrinsic_role=intrinsic_role,
        mission_boost=mission_boost,
    )


# ── compute_commitment ──

def test_present_high_role_person_scores_high():
    now = 1000.0
    state = _person_state(now)
    score = compute_commitment(state, now)
    # role 0.6 + presence 0.2 = 0.8, no disengagement
    assert score == 0.8


def test_familiarity_is_not_an_input():
    # A "familiar" person is represented here only by role + presence; there is
    # no familiarity field in CommitmentState, so a familiar high-role person
    # still scores high (this is the whole point of P0008.1).
    now = 1000.0
    state = _person_state(now)
    assert not hasattr(state, "familiarity")
    assert compute_commitment(state, now) > 0.5


def test_disengagement_grows_with_time_since_confirm():
    now = 1000.0
    stale_state = _person_state(now, last_confirmed_at=now - STALE_TIMEOUT)
    # disengagement = 1.0 → score drops below a present person
    assert compute_commitment(stale_state, now) < 0.4


def test_presence_window_grants_bonus_only_when_recent():
    now = 1000.0
    recent = _person_state(now, last_confirmed_at=now - 5.0)
    just_outside = _person_state(now, last_confirmed_at=now - (PRESENCE_WINDOW + 1.0))
    assert compute_commitment(recent, now) > compute_commitment(just_outside, now)


def test_mission_boost_adds_to_commitment():
    now = 1000.0
    base = _person_state(now, mission_boost=0.0)
    boosted = _person_state(now, mission_boost=0.3)
    assert compute_commitment(boosted, now) > compute_commitment(base, now)


def test_low_role_target_scores_low():
    now = 1000.0
    chair = _person_state(now, intrinsic_role=0.1)  # chair-like
    assert compute_commitment(chair, now) < 0.4


# ── decide ──

def test_hold_when_challenger_below_margin():
    now = 1000.0
    state = _person_state(now)
    score = compute_commitment(state, now)
    decision, reason = decide(state, challenger_curiosity=score + SWITCH_MARGIN - 0.01,
                              status="active", now=now)
    assert decision == Decision.HOLD
    assert reason in ("hold_role", "hold_mission", "hold_presence")


def test_switch_when_challenger_clearly_above():
    now = 1000.0
    state = _person_state(now)
    score = compute_commitment(state, now)
    decision, reason = decide(state, challenger_curiosity=score + SWITCH_MARGIN + 0.01,
                              status="active", now=now)
    assert decision == Decision.SWITCH
    assert reason == "switch_challenger"


def test_hysteresis_no_immediate_switch_on_tie():
    # challenger just barely above score, but below margin → HOLD (no jitter)
    now = 1000.0
    state = _person_state(now)
    score = compute_commitment(state, now)
    decision, _ = decide(state, challenger_curiosity=score + 0.01, status="active", now=now)
    assert decision == Decision.HOLD


def test_release_on_lost():
    now = 1000.0
    state = _person_state(now)
    decision, reason = decide(state, challenger_curiosity=0.0, status="lost", now=now)
    assert decision == Decision.RELEASE
    assert reason == "release_lost"


def test_release_on_stale():
    now = 1000.0
    state = _person_state(now, last_confirmed_at=now - (STALE_TIMEOUT + 1.0))
    decision, reason = decide(state, challenger_curiosity=0.0, status="active", now=now)
    assert decision == Decision.RELEASE
    assert reason == "release_stale"


def test_release_on_safety_timeout():
    now = 1000.0
    state = _person_state(now)
    state.started_at = now - (SAFETY_MAX_DWELL + 1.0)
    decision, reason = decide(state, challenger_curiosity=0.0, status="active", now=now)
    assert decision == Decision.RELEASE
    assert reason == "release_timeout"


# ── Proposal Validation Scenarios ──

def test_scenario_a_person_persists_is_held():
    # A person continuously present and confirmed → HOLD, never explore/switch.
    now = 1000.0
    state = _person_state(now)
    for _ in range(20):  # 20 confirmation ticks
        state.last_confirmed_at = now
        decision, _ = decide(state, challenger_curiosity=0.0, status="active", now=now)
        assert decision == Decision.HOLD


def test_scenario_b_new_object_switches_only_if_strong():
    # A high-curiosity challenger interrupts; a weak one does not.
    now = 1000.0
    state = _person_state(now)
    score = compute_commitment(state, now)
    weak = decide(state, challenger_curiosity=0.3, status="active", now=now)
    strong = decide(state, challenger_curiosity=score + SWITCH_MARGIN + 0.05,
                    status="active", now=now)
    assert weak[0] == Decision.HOLD
    assert strong[0] == Decision.SWITCH


def test_scenario_c_person_leaves_releases():
    now = 1000.0
    state = _person_state(now, last_confirmed_at=now - (STALE_TIMEOUT + 1.0))
    decision, reason = decide(state, challenger_curiosity=0.0, status="lost", now=now)
    assert decision == Decision.RELEASE


# ── CommitmentEngine lifecycle ──

class _FakeRoleEngine:
    def intrinsic_weight(self, cls):
        return 1.0 if cls == "person" else 0.2

    def mission_boost(self, cls):
        return 0.0


def test_engine_begin_uses_role_engine_for_person():
    engine = CommitmentEngine(role_engine=_FakeRoleEngine())
    now = 1000.0
    state = engine.begin("person", now)
    assert state.intrinsic_role == 1.0
    assert engine.has_commitment


def test_engine_confirm_refreshes():
    engine = CommitmentEngine(role_engine=_FakeRoleEngine())
    now = 1000.0
    engine.begin("person", now)
    engine.confirm(now + 50.0)
    assert engine.state.last_confirmed_at == now + 50.0


def test_engine_arbitrate_holds_and_releases():
    engine = CommitmentEngine(role_engine=_FakeRoleEngine())
    now = 1000.0
    engine.begin("person", now)
    # Valid present person → HOLD
    decision, reason = engine.arbitrate(challenger_curiosity=0.0, status="active", now=now)
    assert decision == Decision.HOLD
    # Person lost → RELEASE, and state is cleared
    decision, reason = engine.arbitrate(challenger_curiosity=0.0, status="lost", now=now)
    assert decision == Decision.RELEASE
    assert reason == "release_lost"
    assert not engine.has_commitment


def test_engine_no_role_engine_falls_back_to_default():
    engine = CommitmentEngine()  # no role engine
    now = 1000.0
    state = engine.begin("person", now)
    assert state.intrinsic_role == 0.2
