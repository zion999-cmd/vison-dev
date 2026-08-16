"""
LLM-based MissionRoleProvider — generates dynamic observation priorities.

Key design:
- Prompt template is FIXED (never changes per persona)
- Persona data is injected as structured fields
- LLM output is strictly validated JSON
- Any failure → MissionRole.empty() (graceful degradation)

Also includes RuleMissionProvider — a non-LLM fallback that uses
persona's default mission_role weights directly.
"""

import json
import logging
from typing import Dict, Optional

import requests

from runtime.role.persona import Persona
from runtime.role.mission import (
    MissionRole,
    MissionRoleProvider,
    ObservationContext,
)

logger = logging.getLogger("Role.MissionLLM")

# ── Fixed Prompt Template ──
# This template NEVER changes. Persona-specific behavior comes from
# the persona data injected into it, not from prompt engineering.

_MISSION_PROMPT = """You are an observation strategy advisor for a robot vision system. Your role is to suggest class-level attention weights — you do NOT control the robot.

## Persona
Name: {persona_display}
Description: {persona_description}

### Goals
{persona_goals}

### Avoid
{persona_avoid}

### Attention Policy
{attention_policy}

## Limits
- Maximum weight for any single class: {max_llm_weight}
- Suggested TTL: {mission_ttl_sec}s (can adjust based on environment stability)

## Current Environment
{environment_summary}

## Available Classes
You may assign weights to ANY of these YOLO classes (and ONLY these):
person, bicycle, car, motorcycle, airplane, bus, train, truck, boat,
traffic light, fire hydrant, stop sign, parking meter, bench, bird,
cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe, backpack,
umbrella, handbag, tie, suitcase, frisbee, skis, snowboard, sports ball,
kite, baseball bat, baseball glove, skateboard, surfboard, tennis racket,
bottle, wine glass, cup, fork, knife, spoon, bowl, banana, apple,
sandwich, orange, broccoli, carrot, hot dog, pizza, donut, cake, chair,
couch, potted plant, bed, dining table, toilet, tv, laptop, mouse,
remote, keyboard, cell phone, microwave, oven, toaster, sink,
refrigerator, book, clock, vase, scissors, teddy bear, hair drier,
toothbrush

## Task
Based on the persona and environment, suggest observation priority weights for entity classes.

## Rules
- Weights are ADDITIVE to intrinsic priority (they don't replace it):
  person intrinsic=1.0, chair=0.1, cat/dog=0.8, backpack=0.5, cup=0.05, etc.
- Most classes have intrinsic 0.2 (default). Your weight adds on top.
- Never exceed the max weight limit ({max_llm_weight}) for any single class.
- Boost classes in the persona's goals; suppress classes in its avoid list.
- expires_sec: 60-600. Shorter = more responsive, longer = more stable.
- If the environment is empty or you're unsure, return an empty mission_role {{}}
- The attention_policy describes qualitative biases — translate them into weights.

## Output Format
Return ONLY valid JSON. No markdown, no explanation outside the JSON:
{{"mission_role": {{"class_name": 0.0-{max_llm_weight}}}, "expires_sec": {mission_ttl_sec}, "reason": "..."}}"""


def _format_attention_policy(policy: dict) -> str:
    """Format attention_policy dict for the prompt template."""
    if not policy:
        return "(no specific policy)"
    lines = []
    for k, v in policy.items():
        label = k.replace("_", " ").title()
        lines.append(f"- {label}: {v}")
    return "\n".join(lines)


class LLMMissionProvider(MissionRoleProvider):
    """Generate mission roles using an LLM.

    Uses its own HTTP session to an OpenAI-compatible endpoint
    (Ollama, oc2api, vLLM, etc.). Does NOT share the L6 cognition TextAPI.

    Config (config.py):
        MISSION_LLM_BASE   — base URL (e.g. http://localhost:11434/v1)
        MISSION_LLM_MODEL  — model name (e.g. gemma4:cloud)
        MISSION_LLM_API_KEY — API key (placeholder for Ollama)
    """

    def __init__(self, base_url: str = "", model: str = "", api_key: str = ""):
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self._session: Optional[requests.Session] = None
        self._call_count = 0
        self._last_error: str = ""

    @property
    def provider_name(self) -> str:
        return "llm"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_error(self) -> str:
        return self._last_error

    def _ensure_session(self) -> bool:
        """Lazy-init HTTP session. Returns False if config is missing."""
        if self._session is not None:
            return True

        # Load config if not passed explicitly
        base_url = self._base_url
        model = self._model
        api_key = self._api_key

        if not base_url or not model:
            try:
                from config import MISSION_LLM_BASE, MISSION_LLM_MODEL, MISSION_LLM_API_KEY
                base_url = base_url or MISSION_LLM_BASE
                model = model or MISSION_LLM_MODEL
                api_key = api_key or MISSION_LLM_API_KEY
            except ImportError:
                pass

        if not base_url or not model:
            self._last_error = "MISSION_LLM_BASE or MISSION_LLM_MODEL not configured"
            logger.warning(self._last_error)
            return False

        self._base_url = base_url.rstrip("/")
        self._model = model
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        logger.info("MissionRole LLM: %s → %s", self._base_url, self._model)
        return True

    def generate(self, persona: Persona, context: ObservationContext) -> MissionRole:
        """Call LLM to generate mission role weights.

        Returns MissionRole.empty() on ANY failure — never raises.
        """
        if not self._ensure_session():
            return MissionRole.empty(self._last_error)

        # Build prompt from fixed template + persona data
        prompt = _MISSION_PROMPT.format(
            persona_display=persona.display_name or persona.name,
            persona_description=persona.description,
            persona_goals=persona.goals_text,
            persona_avoid=persona.avoid_text,
            attention_policy=_format_attention_policy(persona.attention_policy),
            max_llm_weight=persona.max_llm_weight,
            mission_ttl_sec=persona.mission_ttl_sec,
            environment_summary=context.summary_text(),
        )

        try:
            self._call_count += 1
            logger.info("MissionRole LLM call #%d (persona=%s, state=%s, entities=%d, model=%s)",
                        self._call_count, persona.name,
                        context.runtime_state, context.total_entities,
                        self._model)

            payload = {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = self._session.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]

            if raw is None:
                self._last_error = "LLM returned None"
                logger.warning("MissionRole LLM returned None — using empty (60s backoff)")
                import time as _time
                return MissionRole(
                    weights={},
                    expires_at=_time.time() + 60,
                    reason="LLM returned None",
                    provider_name="llm",
                )

            mission = self._parse(raw)
            if not mission.weights:
                self._last_error = f"LLM returned non-actionable response: {raw[:100]}"
            return mission

        except Exception as e:
            self._last_error = str(e)
            logger.warning("MissionRole LLM failed (%s): %s — using empty (60s backoff)",
                          self._model, e)
            import time as _time
            return MissionRole(
                weights={},
                expires_at=_time.time() + 60,  # backoff: don't retry immediately
                reason=f"LLM error: {e}",
                provider_name="llm",
            )

    def _parse(self, text: str) -> MissionRole:
        """Parse and validate LLM JSON output.

        Robust against:
        - Markdown code fences
        - Trailing text
        - Missing fields
        - Invalid weight values
        """
        # Strip markdown code fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove opening fence
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract JSON object from text
            import re
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    logger.warning("MissionRole: cannot parse LLM output: %.100s", text)
                    return MissionRole.empty("JSON parse failed")
            else:
                logger.warning("MissionRole: no JSON found in output: %.100s", text)
                return MissionRole.empty("no JSON in output")

        # Extract and validate fields
        weights = data.get("mission_role", {})
        if not isinstance(weights, dict):
            logger.warning("MissionRole: mission_role is not a dict")
            return MissionRole.empty("invalid mission_role type")

        # Validate weights: must be floats 0.0–1.0
        validated: Dict[str, float] = {}
        for cls, w in weights.items():
            if not isinstance(cls, str):
                continue
            try:
                fw = float(w)
                validated[cls] = max(0.0, min(1.0, fw))
            except (ValueError, TypeError):
                continue

        # TTL: default 300s, clamp to [60, 600]
        expires_sec = data.get("expires_sec", 300)
        try:
            expires_sec = max(60, min(600, int(expires_sec)))
        except (ValueError, TypeError):
            expires_sec = 300

        reason = str(data.get("reason", ""))[:200]

        logger.info(
            "MissionRole parsed: %d weights, TTL=%ds, top=%s",
            len(validated), expires_sec,
            sorted(validated.items(), key=lambda x: x[1], reverse=True)[:5],
        )

        import time
        return MissionRole(
            weights=validated,
            expires_at=time.time() + expires_sec,
            reason=reason,
            provider_name="llm",
        )


# ── Rule-based Provider (no LLM) ──

class RuleMissionProvider(MissionRoleProvider):
    """Non-LLM provider that uses persona's default mission_role weights.

    This is useful for:
    - Testing without LLM dependency
    - Fallback when LLM is unavailable
    - Personas with simple, static priorities
    """

    def __init__(self, ttl: int = 300):
        self._ttl = ttl

    @property
    def provider_name(self) -> str:
        return "rule"

    def generate(self, persona: Persona, context: ObservationContext) -> MissionRole:
        """Return persona's default mission_role weights."""
        if not persona.mission_role:
            logger.info("RuleMissionProvider: no default weights in persona '%s'",
                       persona.name)
            return MissionRole.empty("no defaults in persona")

        import time
        ttl = persona.mission_ttl_sec if persona.mission_ttl_sec else self._ttl
        return MissionRole(
            weights=dict(persona.mission_role),
            expires_at=time.time() + ttl,
            reason=f"persona defaults ({persona.name})",
            provider_name="rule",
        )
