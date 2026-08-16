"""
Tests for P0008: Mission Role System

Tests the full chain: Persona → MissionRoleCache → RoleEngine → effective weights.
LLM provider tests use mock TextAPI; Rule provider tests are integration.
"""

import time
import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from runtime.role.persona import Persona, load_persona, list_personas
from runtime.role.mission import (
    MissionRole, MissionRoleCache, MissionRoleProvider,
    ObservationContext, create_mission_provider,
)
from runtime.role.engine import RoleEngine, DEFAULT_ROLE_PROFILE
from runtime.role.mission_llm import (
    LLMMissionProvider, RuleMissionProvider, _MISSION_PROMPT,
)
from runtime.interest.entity import Entity, EntityStatus


# ═══════════════════════════════════════════════════════════
# Persona
# ═══════════════════════════════════════════════════════════

class TestPersona:
    def test_load_companion(self):
        persona = load_persona("companion")
        assert persona.name == "companion"
        assert persona.display_name == "Companion Robot"
        assert len(persona.description) > 0
        # Layer 1: mission
        assert "accompany_people" in persona.goals
        assert "stare_at_static_background" in persona.avoid
        # Layer 2: role_weights
        assert "person" in persona.mission_role
        # person intrinsic=1.0 already, mission_role adds 0.0 (no boost needed)
        assert persona.mission_role["person"] == 0.0
        # Layer 3: reserved fields
        assert persona.attention_policy.get("human_presence_bias") == "high"
        assert persona.interaction_policy.get("follow_human_if_possible") is True
        assert "human_activity" in persona.observation_style.get("preferred_scene", [])
        assert "environment_change" in persona.summary_focus
        # Limits
        assert persona.max_llm_weight == 0.35
        assert persona.mission_ttl_sec == 300

    def test_load_security(self):
        persona = load_persona("security")
        assert persona.name == "security"
        assert any("intruder" in g for g in persona.goals)

    def test_load_reception(self):
        persona = load_persona("reception")
        assert persona.name == "reception"
        assert persona.mission_role.get("person", 0) > 0.4  # high person priority

    def test_load_patrol(self):
        persona = load_persona("patrol")
        assert persona.name == "patrol"
        assert "systematic_environment_scan" in persona.goals

    def test_load_pet(self):
        persona = load_persona("pet")
        assert persona.name == "pet"
        # Pet persona should prioritize animals over people
        assert persona.mission_role.get("cat", 0) > persona.mission_role.get("person", 0)

    def test_load_missing_persona(self):
        persona = load_persona("nonexistent")
        assert persona.name == "nonexistent"
        assert persona.goals == []
        assert persona.mission_role == {}

    def test_list_personas(self):
        personas = list_personas()
        assert "companion" in personas
        assert "security" in personas
        assert "reception" in personas
        assert "patrol" in personas
        assert "pet" in personas

    def test_goals_text(self):
        persona = Persona(name="test", goals=["goal_a", "goal_b"])
        text = persona.goals_text
        assert "- goal_a" in text
        assert "- goal_b" in text

    def test_avoid_text(self):
        persona = Persona(avoid=["c1", "c2"])
        text = persona.avoid_text
        assert "- c1" in text
        assert "- c2" in text

    def test_constraints_alias(self):
        """constraints is a backward-compat alias for avoid."""
        persona = Persona(avoid=["c1"])
        assert persona.constraints == ["c1"]

    def test_empty_avoid_text(self):
        persona = Persona()
        assert persona.avoid_text == "(none)"


# ═══════════════════════════════════════════════════════════
# MissionRole
# ═══════════════════════════════════════════════════════════

class TestMissionRole:
    def test_empty(self):
        mr = MissionRole.empty()
        assert mr.weights == {}
        assert mr.get("person") == 0.0
        assert mr.is_expired

    def test_is_expired(self):
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 300,
        )
        assert not mr.is_expired

    def test_is_expired_past(self):
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() - 1,
        )
        assert mr.is_expired

    def test_ttl_remaining(self):
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 100,
        )
        remaining = mr.ttl_remaining
        assert 0 < remaining <= 100

    def test_ttl_remaining_expired(self):
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() - 100,
        )
        assert mr.ttl_remaining == 0.0

    def test_get_present(self):
        mr = MissionRole(weights={"person": 0.5, "chair": 0.1})
        assert mr.get("person") == 0.5
        assert mr.get("chair") == 0.1

    def test_get_missing(self):
        mr = MissionRole(weights={"person": 0.5})
        assert mr.get("bottle") == 0.0

    def test_to_dict(self):
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 300,
            reason="test",
            provider_name="test",
        )
        d = mr.to_dict()
        assert d["weights"] == {"person": 0.5}
        assert d["reason"] == "test"
        assert d["provider_name"] == "test"
        assert d["ttl_remaining"] > 0


# ═══════════════════════════════════════════════════════════
# MissionRoleCache
# ═══════════════════════════════════════════════════════════

class TestMissionRoleCache:
    def test_get_empty_initially(self):
        cache = MissionRoleCache()
        mr = cache.get()
        assert mr.weights == {}
        assert mr.is_expired

    def test_not_active_initially(self):
        cache = MissionRoleCache()
        assert not cache.is_active

    def test_update_and_get(self):
        cache = MissionRoleCache()
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 300,
            provider_name="test",
        )
        cache.update(mr)
        assert cache.is_active
        result = cache.get()
        assert result.weights == {"person": 0.5}
        assert not result.is_expired

    def test_get_weight(self):
        cache = MissionRoleCache()
        mr = MissionRole(
            weights={"person": 0.5, "chair": 0.3},
            expires_at=time.time() + 300,
        )
        cache.update(mr)
        assert cache.get_weight("person") == 0.5
        assert cache.get_weight("chair") == 0.3
        assert cache.get_weight("bottle") == 0.0

    def test_expiry(self):
        cache = MissionRoleCache()
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 0.01,  # 10ms TTL
            provider_name="test",
        )
        cache.update(mr)
        assert cache.is_active
        time.sleep(0.02)  # wait for expiry
        assert not cache.is_active
        result = cache.get()
        assert result.weights == {}

    def test_get_weight_after_expiry(self):
        cache = MissionRoleCache()
        mr = MissionRole(
            weights={"person": 0.5},
            expires_at=time.time() + 0.01,
            provider_name="test",
        )
        cache.update(mr)
        time.sleep(0.02)
        assert cache.get_weight("person") == 0.0

    def test_update_count(self):
        cache = MissionRoleCache()
        assert cache.update_count == 0
        cache.update(MissionRole(weights={"a": 0.1}, expires_at=time.time() + 300))
        assert cache.update_count == 1
        cache.update(MissionRole(weights={"b": 0.2}, expires_at=time.time() + 300))
        assert cache.update_count == 2

    def test_summary(self):
        cache = MissionRoleCache()
        mr = MissionRole(
            weights={"person": 0.5, "chair": 0.3},
            expires_at=time.time() + 300,
            reason="test summary",
            provider_name="test",
        )
        cache.update(mr)
        summary = cache.summary
        assert "person" in summary
        # summary shows top weights and TTL; reason is available via cache.get().reason
        assert "300s" in summary or "ttl" in summary.lower()

    def test_summary_empty(self):
        cache = MissionRoleCache()
        assert "empty" in cache.summary


# ═══════════════════════════════════════════════════════════
# RoleEngine with Mission Cache
# ═══════════════════════════════════════════════════════════

class TestRoleEngineWithMission:
    def test_intrinsic_only(self):
        engine = RoleEngine()
        assert engine.get_weight("person") == 1.0
        assert engine.get_weight("chair") == 0.1
        assert engine.get_weight("unknown_class") == 0.2

    def test_intrinsic_weight_method(self):
        engine = RoleEngine()
        assert engine.intrinsic_weight("person") == 1.0

    def test_mission_boost_no_cache(self):
        engine = RoleEngine()
        assert engine.mission_boost("person") == 0.0

    def test_effective_with_mission(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)
        # chair: intrinsic 0.1 + mission 0.5 = 0.6
        assert engine.get_weight("chair") == 0.6

    def test_effective_with_mission_clamped(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"person": 0.5},  # person intrinsic is already 1.0
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)
        # 1.0 + 0.5 = 1.5, clamped to 1.0
        assert engine.get_weight("person") == 1.0

    def test_mission_expired(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 0.01,
        ))
        engine = RoleEngine(mission_cache=cache)
        time.sleep(0.02)
        # Back to intrinsic only
        assert engine.get_weight("chair") == 0.1

    def test_mission_boost_present(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"bottle": 0.4},
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)
        assert engine.mission_boost("bottle") == 0.4
        assert engine.mission_boost("nonexistent") == 0.0

    def test_set_mission_cache_after_init(self):
        engine = RoleEngine()
        assert engine.get_weight("chair") == 0.1
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 300,
        ))
        engine.mission_cache = cache
        assert engine.get_weight("chair") == 0.6

    def test_effective_interest(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)
        entity = Entity(
            class_name="chair",
            interest=0.8,
            status=EntityStatus.ACTIVE,
        )
        # 0.8 * 0.6 = 0.48
        assert engine.effective_interest(entity) == pytest.approx(0.48)

    def test_refresh_entities(self):
        cache = MissionRoleCache()
        engine = RoleEngine(mission_cache=cache)

        # Create a mock registry with entities
        e1 = Entity(
            class_name="chair", interest=0.5, status=EntityStatus.ACTIVE,
            role_weight=engine.get_weight("chair"),
        )
        e2 = Entity(
            class_name="person", interest=0.8, status=EntityStatus.ACTIVE,
            role_weight=engine.get_weight("person"),
        )

        mock_registry = Mock()
        mock_registry.all_entities.return_value = [e1, e2]

        # Before mission update
        assert e1.role_weight == 0.1  # intrinsic only
        assert e2.role_weight == 1.0

        # Update mission
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 300,
        ))

        # Refresh entities
        updated = engine.refresh_entities(mock_registry)
        assert updated == 1  # only chair changed
        assert e1.role_weight == 0.6  # 0.1 + 0.5
        assert e2.role_weight == 1.0  # unchanged (already at max)

    def test_refresh_entities_inactive_skipped(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"chair": 0.5},
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)

        e1 = Entity(
            class_name="chair", interest=0.5,
            status=EntityStatus.FORGOTTEN,
            role_weight=0.1,
        )
        mock_registry = Mock()
        mock_registry.all_entities.return_value = [e1]

        updated = engine.refresh_entities(mock_registry)
        assert updated == 0  # FORGOTTEN entity not updated
        assert e1.role_weight == 0.1  # unchanged

    def test_profile_summary_with_mission(self):
        cache = MissionRoleCache()
        cache.update(MissionRole(
            weights={"bottle": 0.4},
            expires_at=time.time() + 300,
        ))
        engine = RoleEngine(mission_cache=cache)
        summary = engine.profile_summary()
        assert summary["mission_active"] is True
        assert "bottle" in summary["mission_weights"]


# ═══════════════════════════════════════════════════════════
# RuleMissionProvider
# ═══════════════════════════════════════════════════════════

class TestRuleMissionProvider:
    def test_generate_from_persona_defaults(self):
        provider = RuleMissionProvider(ttl=60)
        persona = load_persona("companion")
        context = ObservationContext()
        mission = provider.generate(persona, context)
        assert mission.provider_name == "rule"
        assert "person" in mission.weights
        assert "person" in mission.weights
        # companion has person=0.0 in mission_role (intrinsic already 1.0)
        assert mission.weights["person"] == 0.0
        assert not mission.is_expired

    def test_expires_at_from_persona_limits(self):
        provider = RuleMissionProvider()
        persona = load_persona("companion")
        mission = provider.generate(persona, context=ObservationContext())
        remaining = mission.ttl_remaining
        # companion has limits.mission_ttl_sec=300
        assert 0 < remaining <= 300

    def test_empty_persona_defaults(self):
        provider = RuleMissionProvider()
        persona = Persona(name="empty")  # no mission_role
        mission = provider.generate(persona, context=ObservationContext())
        assert mission.weights == {}
        assert mission.is_expired


# ═══════════════════════════════════════════════════════════
# LLMMissionProvider (JSON parsing)
# ═══════════════════════════════════════════════════════════

class TestLLMMissionProvider:
    def test_parse_valid_json(self):
        provider = LLMMissionProvider()
        raw = json.dumps({
            "mission_role": {"person": 0.5, "chair": 0.2},
            "expires_sec": 300,
            "reason": "companion mode",
        })
        mission = provider._parse(raw)
        assert mission.weights == {"person": 0.5, "chair": 0.2}
        assert mission.reason == "companion mode"
        assert not mission.is_expired

    def test_parse_json_with_markdown_fence(self):
        provider = LLMMissionProvider()
        raw = '```json\n{"mission_role": {"person": 0.4}, "expires_sec": 120, "reason": "test"}\n```'
        mission = provider._parse(raw)
        assert mission.weights == {"person": 0.4}

    def test_parse_json_without_json_tag(self):
        provider = LLMMissionProvider()
        raw = '```\n{"mission_role": {"person": 0.3}, "expires_sec": 200, "reason": "no tag"}\n```'
        mission = provider._parse(raw)
        assert mission.weights == {"person": 0.3}

    def test_parse_json_with_extra_text(self):
        provider = LLMMissionProvider()
        raw = 'Based on the persona, here are the weights:\n{"mission_role": {"person": 0.5}, "expires_sec": 300, "reason": "ok"}'
        mission = provider._parse(raw)
        assert mission.weights == {"person": 0.5}

    def test_parse_weights_clamped(self):
        provider = LLMMissionProvider()
        raw = json.dumps({
            "mission_role": {"person": 1.5, "chair": -0.2},
            "expires_sec": 300,
            "reason": "clamp test",
        })
        mission = provider._parse(raw)
        assert mission.weights["person"] == 1.0  # clamped from 1.5
        assert mission.weights["chair"] == 0.0   # clamped from -0.2

    def test_parse_ttl_clamped(self):
        provider = LLMMissionProvider()
        # TTL too short
        raw = json.dumps({
            "mission_role": {"person": 0.5},
            "expires_sec": 10,
            "reason": "",
        })
        mission = provider._parse(raw)
        # Clamped to min 60
        remaining = mission.ttl_remaining
        assert remaining >= 59  # 60 - epsilon

        # TTL too long
        raw = json.dumps({
            "mission_role": {"person": 0.5},
            "expires_sec": 9999,
            "reason": "",
        })
        mission = provider._parse(raw)
        remaining = mission.ttl_remaining
        assert remaining <= 600  # clamped to max 600

    def test_parse_invalid_json(self):
        provider = LLMMissionProvider()
        mission = provider._parse("not json at all")
        assert mission.weights == {}
        # Either "JSON parse failed" (regex found no JSON) or "no JSON in output"
        assert "JSON" in mission.reason or "json" in mission.reason.lower()

    def test_parse_missing_mission_role(self):
        provider = LLMMissionProvider()
        raw = json.dumps({"expires_sec": 300, "reason": "no role"})
        mission = provider._parse(raw)
        assert mission.weights == {}

    def test_parse_empty_mission_role(self):
        provider = LLMMissionProvider()
        raw = json.dumps({"mission_role": {}, "expires_sec": 300, "reason": "empty"})
        mission = provider._parse(raw)
        assert mission.weights == {}

    def test_parse_non_dict_weights(self):
        provider = LLMMissionProvider()
        raw = json.dumps({
            "mission_role": "not a dict",
            "expires_sec": 300,
            "reason": "",
        })
        mission = provider._parse(raw)
        assert mission.weights == {}

    def test_parse_invalid_weights_skipped(self):
        provider = LLMMissionProvider()
        raw = json.dumps({
            "mission_role": {
                "person": 0.5,
                "bad_value": "not_a_number",
                "chair": 0.3,
            },
            "expires_sec": 300,
            "reason": "",
        })
        mission = provider._parse(raw)
        # bad_value should be skipped, others kept
        assert "person" in mission.weights
        assert "chair" in mission.weights
        assert "bad_value" not in mission.weights

    def test_provider_name(self):
        provider = LLMMissionProvider()
        assert provider.provider_name == "llm"

    def test_generate_no_api_returns_empty(self):
        """Without a running LLM server, generate should fail gracefully."""
        provider = LLMMissionProvider(
            base_url="http://127.0.0.1:19999/v1",  # nothing listening here
            model="test-model",
            api_key="test",
        )
        persona = load_persona("companion")
        context = ObservationContext()
        # Will try to connect and fail (connection refused)
        mission = provider.generate(persona, context)
        # Should return empty MissionRole, not raise
        assert mission.weights == {}


# ═══════════════════════════════════════════════════════════
# ObservationContext
# ═══════════════════════════════════════════════════════════

class TestObservationContext:
    def test_default(self):
        ctx = ObservationContext()
        assert ctx.runtime_state == "idle"
        assert ctx.active_classes == []
        assert ctx.total_entities == 0

    def test_summary_text_empty(self):
        ctx = ObservationContext()
        text = ctx.summary_text()
        assert "none" in text

    def test_summary_text_with_data(self):
        ctx = ObservationContext(
            runtime_state="focus",
            active_summary=[
                {"class": "person", "count": 2, "avg_interest": 0.8},
                {"class": "chair", "count": 1, "avg_interest": 0.3},
            ],
            top_importance=[
                {"class": "person", "interactions": 50, "sessions": 3},
            ],
            total_entities=5,
            active_count=3,
        )
        text = ctx.summary_text()
        assert "focus" in text
        assert "person(×2)" in text
        assert "50ev" in text

    def test_to_dict(self):
        ctx = ObservationContext(
            runtime_state="alert",
            active_classes=["person", "chair"],
            total_entities=10,
        )
        d = ctx.to_dict()
        assert d["runtime_state"] == "alert"
        assert "person" in d["active_classes"]


# ═══════════════════════════════════════════════════════════
# Provider Factory
# ═══════════════════════════════════════════════════════════

class TestCreateProvider:
    def test_create_llm(self):
        provider = create_mission_provider("llm")
        assert provider.provider_name == "llm"

    def test_create_rule(self):
        provider = create_mission_provider("rule")
        assert provider.provider_name == "rule"

    def test_create_none(self):
        provider = create_mission_provider("none")
        assert provider.provider_name == "null"
        mission = provider.generate(Persona(), ObservationContext())
        assert mission.weights == {}

    def test_create_unknown(self):
        provider = create_mission_provider("unknown_type")
        assert provider.provider_name == "null"


# ═══════════════════════════════════════════════════════════
# Prompt Template
# ═══════════════════════════════════════════════════════════

class TestPromptTemplate:
    def test_prompt_is_not_empty(self):
        assert len(_MISSION_PROMPT) > 100

    def test_prompt_has_fixed_structure(self):
        """Prompt template must be fixed — no hardcoded persona-specific text."""
        assert "{persona_display}" in _MISSION_PROMPT
        assert "{persona_description}" in _MISSION_PROMPT
        assert "{persona_goals}" in _MISSION_PROMPT
        assert "{persona_avoid}" in _MISSION_PROMPT
        assert "{environment_summary}" in _MISSION_PROMPT
        assert "{attention_policy}" in _MISSION_PROMPT
        assert "{max_llm_weight}" in _MISSION_PROMPT

    def test_prompt_formats_with_persona(self):
        from runtime.role.mission_llm import _format_attention_policy
        persona = Persona(
            name="test",
            display_name="Test Bot",
            description="A test persona",
            goals=["goal1"],
            avoid=["avoid1"],
            attention_policy={"human_bias": "high"},
            limits={"max_llm_weight": 0.3, "mission_ttl_sec": 200},
        )
        ctx = ObservationContext(runtime_state="idle")
        text = _MISSION_PROMPT.format(
            persona_display=persona.display_name or persona.name,
            persona_description=persona.description,
            persona_goals=persona.goals_text,
            persona_avoid=persona.avoid_text,
            attention_policy=_format_attention_policy(persona.attention_policy),
            max_llm_weight=persona.max_llm_weight,
            mission_ttl_sec=persona.mission_ttl_sec,
            environment_summary=ctx.summary_text(),
        )
        assert "Test Bot" in text
        assert "A test persona" in text
        assert "goal1" in text
        assert "avoid1" in text
        assert "Human Bias: high" in text
        assert "0.3" in text
        # Should NOT contain any other persona's name
        assert "companion" not in text
        assert "security" not in text
