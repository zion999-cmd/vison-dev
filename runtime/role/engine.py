"""
Role Engine — innate priority weights + mission role integration.

    "What should I care about?" — BEFORE any observation.

Architecture position:
    Interest     → "what changed?"        (seconds)
    Curiosity    → "what's worth revisiting?" (minutes)
    Familiarity  → "have I seen this enough?" (minutes–hours)
    Role         → "what should I care about?" (innate + mission)
    Value        → "what should I remember?"   (days)

Effective Role = IntrinsicRole + MissionRole

IntrinsicRole is先天 (innate): person=1.0, chair=0.1 — never changes.
MissionRole is动态 (dynamic): from LLM/Rule/Cloud/Human, with TTL.
When MissionRole expires or LLM fails, MissionRole=0 — Runtime unaffected.

Key invariant: remove the LLM, and the system still works.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("Role")

# ── Default role profile (home observation) ──
# Weights are multiplicative: effective_interest = interest × role_weight.
# 1.0 = innate high priority, 0.0 = never care.
DEFAULT_ROLE_PROFILE: Dict[str, float] = {
    # People — innately most interesting
    "person": 1.0,

    # Animals / pets
    "cat": 0.8,
    "dog": 0.8,

    # Personal / portable items (could be left behind, moved, stolen)
    "backpack": 0.5,
    "handbag": 0.5,
    "suitcase": 0.5,
    "umbrella": 0.3,

    # Routine indoor objects — low priority
    "chair": 0.1,
    "couch": 0.1,
    "dining table": 0.1,
    "tv": 0.1,
    "laptop": 0.1,
    "keyboard": 0.05,
    "mouse": 0.05,
    "book": 0.05,
    "clock": 0.05,
    "cup": 0.05,
    "bottle": 0.05,
    "bowl": 0.05,
    "cell phone": 0.05,
    "remote": 0.05,
    "vase": 0.05,
    "potted plant": 0.05,
    "sink": 0.05,
    "refrigerator": 0.05,
    "microwave": 0.05,
    "oven": 0.05,
    "toaster": 0.05,
    "bed": 0.05,
    "toilet": 0.05,
    "bench": 0.05,
}

# Default for any class not in the profile.
# Slightly below person, above furniture: unknown things are worth checking.
_DEFAULT_WEIGHT = 0.2


class RoleEngine:
    """Assigns priority weights to entity classes.

    EffectiveRole = IntrinsicRole + MissionRole (clamped to [0, 1]).

    IntrinsicRole is先天 (innate, fixed weights per class).
    MissionRole is动态 (from MissionRoleCache, TTL-gated, may be empty).

    Usage:
        engine = RoleEngine()                              # intrinsic only
        engine = RoleEngine(mission_cache=cache)           # intrinsic + mission
        w = engine.get_weight(entity)                      # effective weight
        engine.refresh_entities(registry)                  # after mission change
    """

    def __init__(self, profile: Optional[Dict[str, float]] = None,
                 mission_cache=None):
        self._weights: Dict[str, float] = dict(DEFAULT_ROLE_PROFILE)
        if profile:
            self._weights.update(profile)
        self._default = _DEFAULT_WEIGHT

        # Mission cache — optional, set after init or via constructor
        self._mission_cache = mission_cache  # MissionRoleCache or None

        logger.info("RoleEngine: %d class weights loaded (default=%.2f, mission=%s)",
                    len(self._weights), self._default,
                    "enabled" if mission_cache else "disabled")

    # ── Mission cache ──

    @property
    def mission_cache(self):
        return self._mission_cache

    @mission_cache.setter
    def mission_cache(self, cache):
        """Set or replace the mission cache reference."""
        self._mission_cache = cache
        if cache:
            logger.info("RoleEngine: mission cache connected")

    # ── Weight calculation ──

    def get_weight(self, entity_or_class) -> float:
        """Get effective role weight = intrinsic + mission, clamped to [0, 1].

        Accepts an Entity object or a class name string.
        When mission cache is None or mission is expired, returns intrinsic only.
        """
        # ── Intrinsic weight ──
        if isinstance(entity_or_class, str):
            class_name = entity_or_class
        else:
            class_name = getattr(entity_or_class, 'class_name', '') or ''

        class_name = class_name.lower().strip()
        intrinsic = self._weights.get(class_name, self._default) if class_name else self._default

        # ── Mission weight (0 if no cache or expired) ──
        mission = 0.0
        if self._mission_cache and class_name:
            mission = self._mission_cache.get_weight(class_name)

        return min(1.0, intrinsic + mission)

    def intrinsic_weight(self, entity_or_class) -> float:
        """Get intrinsic weight only (no mission boost). For introspection."""
        if isinstance(entity_or_class, str):
            class_name = entity_or_class
        else:
            class_name = getattr(entity_or_class, 'class_name', '') or ''

        class_name = class_name.lower().strip()
        return self._weights.get(class_name, self._default) if class_name else self._default

    def mission_boost(self, entity_or_class) -> float:
        """Get mission-only contribution (0 if no mission cache)."""
        if isinstance(entity_or_class, str):
            class_name = entity_or_class
        else:
            class_name = getattr(entity_or_class, 'class_name', '') or ''

        class_name = class_name.lower().strip()
        if self._mission_cache and class_name:
            return self._mission_cache.get_weight(class_name)
        return 0.0

    def effective_interest(self, entity) -> float:
        """entity.interest × effective role_weight."""
        return entity.interest * self.get_weight(entity)

    # ── Entity refresh ──

    def refresh_entities(self, registry) -> int:
        """Update role_weight on all entities after mission role changes.

        Called after MissionRoleCache.update() to propagate new weights
        to existing entities. Only modifies entities whose effective weight
        actually changed.

        Args:
            registry: EntityRegistry instance

        Returns:
            Number of entities updated.
        """
        updated = 0
        for entity in registry.all_entities():
            if not entity.is_active:
                continue
            new_weight = self.get_weight(entity)
            if new_weight != entity.role_weight:
                entity.role_weight = new_weight
                updated += 1
        if updated:
            logger.info("RoleEngine: refreshed %d entity weights (mission=%s)",
                       updated, self._mission_cache.summary if self._mission_cache else "none")
        return updated

    # ── Introspection ──

    def profile_summary(self) -> dict:
        """Return weight distribution for telemetry."""
        high = {k: v for k, v in self._weights.items() if v >= 0.8}
        mid = {k: v for k, v in self._weights.items() if 0.3 <= v < 0.8}
        low = {k: v for k, v in self._weights.items() if v < 0.3}
        mission = {}
        if self._mission_cache:
            mission = self._mission_cache.get().weights
        return {
            "high_priority": list(high.keys()),
            "mid_priority": list(mid.keys()),
            "low_priority_count": len(low),
            "default_weight": self._default,
            "mission_active": self._mission_cache.is_active if self._mission_cache else False,
            "mission_weights": mission,
        }
