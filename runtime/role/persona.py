"""
Persona — loadable robot identity configuration.

Persona is NOT a prompt. It's a structured YAML config that defines
what the robot should care about. The LLM prompt template is fixed;
only the persona data changes.

The YAML has three layers (designed for forward compatibility):
  Layer 1: mission          — why this robot exists           (parsed today)
  Layer 2: role_weights     — what P0008 uses today            (parsed today)
  Layer 3: attention_policy, interaction_policy,
           observation_style, summary_focus
                            — reserved for P0009/P0010/Memory  (stored, not acted on)

New robot identity = new YAML. Format is stable; future phases grow into it
without changing the format.

Usage:
    from runtime.role.persona import load_persona
    persona = load_persona("companion")
    print(persona.name, persona.display_name, persona.goals)
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("Role.Persona")

# Path to persona YAML directory, relative to project root
_PERSONA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "personas"
)


@dataclass
class Persona:
    """A robot identity — immutable config, loaded from YAML.

    NOT a prompt. NOT a runtime state. Just configuration.

    Layer 1 (mission): always parsed.
    Layer 2 (role_weights): P0008 active use.
    Layer 3 (policies, observation_style, summary_focus): stored for future phases.
    """

    name: str = "default"
    display_name: str = ""
    description: str = ""

    # ── Layer 1: Mission ──
    goals: List[str] = field(default_factory=list)       # flattened primary+secondary
    avoid: List[str] = field(default_factory=list)

    # ── Layer 2: Role Weights (P0008 active) ──
    mission_role: Dict[str, float] = field(default_factory=dict)

    # ── Layer 3: Reserved for future phases ──
    attention_policy: Dict[str, str] = field(default_factory=dict)
    interaction_policy: Dict = field(default_factory=dict)
    observation_style: Dict = field(default_factory=dict)
    summary_focus: List[str] = field(default_factory=list)

    # ── Limits ──
    limits: Dict = field(default_factory=dict)

    # ── Backward-compat aliases ──

    @property
    def constraints(self) -> List[str]:
        """Backward compat: old name for 'avoid'."""
        return self.avoid

    @property
    def goals_text(self) -> str:
        return "\n".join(f"- {g}" for g in self.goals) if self.goals else "(none)"

    @property
    def avoid_text(self) -> str:
        return "\n".join(f"- {a}" for a in self.avoid) if self.avoid else "(none)"

    @property
    def constraints_text(self) -> str:
        """Backward compat: old prompt templates use this name."""
        return self.avoid_text

    @property
    def max_llm_weight(self) -> float:
        return float(self.limits.get("max_llm_weight", 0.35))

    @property
    def mission_ttl_sec(self) -> int:
        return int(self.limits.get("mission_ttl_sec", 300))


# ── YAML loader ──

def load_persona(name: str, persona_dir: Optional[str] = None) -> Persona:
    """Load a persona from a YAML file.

    Supports both old and new YAML formats. Fields are parsed from the
    appropriate layer; unrecognized fields are silently preserved in the
    dataclass for future phases.

    Args:
        name: Persona name (without .yaml extension), e.g. "companion"
        persona_dir: Optional override for persona directory

    Returns:
        Persona instance. Returns a default Persona if file not found.
    """
    try:
        import yaml
    except ImportError:
        logger.error("PyYAML is required to load personas. Install: pip install pyyaml")
        return Persona(name=name)

    directory = persona_dir or _PERSONA_DIR
    path = os.path.join(directory, f"{name}.yaml")

    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("Persona '%s' not found at %s — using defaults", name, path)
        return Persona(name=name)
    except Exception as e:
        logger.error("Failed to load persona '%s': %s", name, e)
        return Persona(name=name)

    # ── Parse mission (Layer 1) ──
    mission = data.get("mission", {})
    if isinstance(mission, dict):
        # New format: mission.primary_goal + mission.secondary_goal + mission.avoid
        primary = _to_list(mission.get("primary_goal", []))
        secondary = _to_list(mission.get("secondary_goal", []))
        goals = primary + secondary
        avoid = _to_list(mission.get("avoid", []))
    else:
        goals = []
        avoid = []

    # Fallback: old flat format (top-level goals/constraints)
    if not goals:
        goals = _to_list(data.get("goals", []))
    if not avoid:
        avoid = _to_list(data.get("constraints", []))

    # ── Parse role_weights (Layer 2) ──
    # Accept both "role_weights" (new) and "mission_role" (old) keys
    mission_role = data.get("role_weights") or data.get("mission_role") or {}
    if not isinstance(mission_role, dict):
        mission_role = {}

    # ── Parse limits ──
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        limits = {}

    return Persona(
        name=data.get("name", name),
        display_name=data.get("display_name", data.get("name", name)),
        description=data.get("description", ""),
        goals=goals,
        avoid=avoid,
        mission_role=mission_role,
        attention_policy=data.get("attention_policy", {}),
        interaction_policy=data.get("interaction_policy", {}),
        observation_style=data.get("observation_style", {}),
        summary_focus=_to_list(data.get("summary_focus", [])),
        limits=limits,
    )


def _to_list(value) -> List[str]:
    """Coerce a value to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


def list_personas(persona_dir: Optional[str] = None) -> List[str]:
    """List available persona names (without .yaml extension)."""
    directory = persona_dir or _PERSONA_DIR
    try:
        files = os.listdir(directory)
        return sorted(
            f.replace(".yaml", "")
            for f in files
            if f.endswith(".yaml") or f.endswith(".yml")
        )
    except FileNotFoundError:
        return []
