"""
Mission Telemetry — measures whether Mission Role changes Runtime behavior.

Does NOT answer "did the LLM return valid JSON?"
Answers:         "did the LLM change what the robot actually does?"

Seven log types:
  1. Mission Refresh      — every LLM call, full detail
  2. Mission Influence     — intrinsic vs mission vs effective for top classes
  3. Curiosity Delta       — top-N ranking before/after mission update
  4. PTZ Decision Attribution — why PTZ chose a target (role + mission breakdown)
  5. Mission Expire        — TTL expiry confirmation
  6. Provider Compare      — rule vs LLM weight differences
  7. Persona Signature     — attention distribution snapshot (every 30 min)

Aggregate:
  Mission Effectiveness    — refresh count, curiosity changed%, PTZ changed%, ignored%

All output goes through standard logging (logger "Role.MissionTelemetry") so it
appears in both console and file logs. No separate file I/O.
"""

import time
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("Role.MissionTelemetry")

# Separator for visual grouping in logs
_SEP = "─" * 55
_DSEP = "=" * 55


class MissionTelemetry:
    """Collects and logs mission effectiveness metrics.

    Hooks into Runtime events:
      - on_mission_refresh(mission, context, persona, provider)
      - on_curiosity_ranking(before, after)
      - on_ptz_decision(target, breakdown)
      - on_mission_expire()
      - periodic_flush()

    Thread-safe: all methods use atomic counters and lock-free reads.
    Logging is called from the main thread only.
    """

    def __init__(self):
        # ── Effectiveness counters ──
        self.refresh_count: int = 0
        self.curiosity_changed_count: int = 0
        self.ptz_target_changed_count: int = 0
        self.mission_ignored_count: int = 0
        self.expire_count: int = 0
        self.fallback_used_count: int = 0

        # ── State ──
        self._last_curiosity_top: List[str] = []
        self._last_ptz_target: str = ""
        self._last_flush: float = 0.0
        self._last_persona_snapshot: float = 0.0
        self._provider_name: str = "unknown"
        self._persona_name: str = "unknown"

    # ═══════════════════════════════════════════════════════
    # 1. Mission Refresh
    # ═══════════════════════════════════════════════════════

    def log_mission_refresh(
        self,
        persona_name: str,
        provider_name: str,
        ttl_sec: float,
        context_summary: str,
        weights: Dict[str, float],
        reason: str,
    ):
        """Called immediately after MissionRoleCache.update()."""
        self.refresh_count += 1
        self._persona_name = persona_name
        self._provider_name = provider_name

        # Format weights as a column
        weight_lines = _format_weight_column(weights)

        logger.info(
            "\n%s\n"
            "Mission Refresh #%d\n"
            "%s\n"
            "  Persona      : %s\n"
            "  Provider     : %s\n"
            "  TTL          : %.0fs\n"
            "\n"
            "  Observation Summary:\n"
            "%s\n"
            "\n"
            "  Mission Role:\n"
            "%s\n"
            "\n"
            "  Reason:\n"
            "    %s\n"
            "%s",
            _DSEP,
            self.refresh_count,
            _DSEP,
            persona_name,
            provider_name,
            ttl_sec,
            _indent(context_summary, 4),
            weight_lines or "    (empty — no boosts)",
            reason or "(none)",
            _DSEP,
        )

    # ═══════════════════════════════════════════════════════
    # 2. Mission Influence
    # ═══════════════════════════════════════════════════════

    def log_mission_influence(
        self,
        intrinsic_weights: Dict[str, float],
        mission_weights: Dict[str, float],
        top_n: int = 8,
    ):
        """Log intrinsic vs mission vs effective for top affected classes."""
        # Collect all classes affected by mission
        all_classes = set(intrinsic_weights.keys()) | set(mission_weights.keys())

        # Compute effective = intrinsic + mission (clamped to 1.0)
        rows = []
        for cls in all_classes:
            intr = intrinsic_weights.get(cls, 0.2)  # default intrinsic
            mis = mission_weights.get(cls, 0.0)
            eff = min(1.0, intr + mis)
            delta = eff - intr
            if abs(delta) < 0.01:
                continue  # skip unchanged
            rows.append((cls, intr, mis, eff))

        if not rows:
            self.mission_ignored_count += 1
            logger.info("Mission Influence: (no class weights changed by mission)")
            return

        # Sort by absolute delta
        rows.sort(key=lambda r: abs(r[3] - r[1]), reverse=True)
        rows = rows[:top_n]

        lines = []
        for cls, intr, mis, eff in rows:
            clamped_mark = " (clamped)" if eff >= 1.0 and intr < 1.0 else ""
            lines.append(
                f"    {cls:<18} intrinsic={intr:.2f}  mission={mis:+.2f}  → effective={eff:.2f}{clamped_mark}"
            )

        logger.info(
            "\n%s\nMission Influence\n%s\n%s\n%s",
            _SEP, _SEP,
            "\n".join(lines),
            _SEP,
        )

    # ═══════════════════════════════════════════════════════
    # 3. Curiosity Ranking Delta
    # ═══════════════════════════════════════════════════════

    def log_curiosity_delta(
        self,
        before: List[Tuple[str, float]],
        after: List[Tuple[str, float]],
    ):
        """Log top-N curiosity ranking change after mission update.

        Args:
            before: [(class_name, curiosity_score), ...] before mission refresh
            after:  [(class_name, curiosity_score), ...] after mission refresh
        """
        before_ranking = {name: i for i, (name, _) in enumerate(before[:10])}
        after_ranking = {name: i for i, (name, _) in enumerate(after[:10])}

        # Find changes
        changed = []
        all_names = set(before_ranking.keys()) | set(after_ranking.keys())
        for name in all_names:
            before_pos = before_ranking.get(name, None)
            after_pos = after_ranking.get(name, None)

            if before_pos is None and after_pos is not None:
                changed.append((name, None, after_pos + 1, "new"))
            elif before_pos is not None and after_pos is None:
                changed.append((name, before_pos + 1, None, "dropped"))
            elif before_pos != after_pos:
                delta = before_pos - after_pos  # positive = moved UP
                changed.append((name, before_pos + 1, after_pos + 1, f"{delta:+d}"))

        if not changed:
            return  # no ranking change

        self.curiosity_changed_count += 1

        # Format ranking columns
        before_lines = [f"  {i+1:>2}. {name} ({score:.2f})" for i, (name, score) in enumerate(before[:10])]
        after_lines = [f"  {i+1:>2}. {name} ({score:.2f})" for i, (name, score) in enumerate(after[:10])]

        delta_lines = []
        for name, before_pos, after_pos, label in sorted(changed, key=lambda x: abs(int(x[3]) if x[3] not in ("new", "dropped") else 10), reverse=True):
            if before_pos is None:
                delta_lines.append(f"  + {name} → #{after_pos} (new)")
            elif after_pos is None:
                delta_lines.append(f"  − {name} was #{before_pos} (dropped)")
            else:
                direction = "↑" if int(label) > 0 else "↓"
                delta_lines.append(f"  {direction} {name}: #{before_pos} → #{after_pos} ({label})")

        logger.info(
            "\n%s\nCuriosity Ranking Delta\n%s\n"
            "Before                    After\n"
            "%-25s %s\n"
            "%s\n"
            "Changed:\n%s\n"
            "%s",
            _SEP, _SEP,
            "\n".join(before_lines[:5]) if before_lines else "(empty)",
            "\n".join(after_lines[:5]) if after_lines else "(empty)",
            "─" * 25,
            "\n".join(delta_lines) if delta_lines else "  (no change)",
            _SEP,
        )

    # ═══════════════════════════════════════════════════════
    # 4. PTZ Decision Attribution
    # ═══════════════════════════════════════════════════════

    def log_ptz_decision(
        self,
        target_name: str,
        interest: float,
        curiosity: float,
        role_intrinsic: float,
        mission_boost: float,
        familiarity: float,
        movement_cost: float,
        decision: str,
    ):
        """Called when RevisitController makes a PTZ decision.

        Breaks down the curiosity formula to show exactly WHY this target
        was chosen — and whether mission_role contributed.
        """
        effective_role = min(1.0, role_intrinsic + mission_boost)
        raw = interest + effective_role  # simplified curiosity display

        mission_line = ""
        if mission_boost > 0:
            mission_line = f"\n  mission      +{mission_boost:.2f}  ← MISSION BOOST"
        elif mission_boost < 0:
            mission_line = f"\n  mission      {mission_boost:.2f}  ← MISSION PENALTY"

        logger.info(
            "\n%s\nPTZ Decision\n%s\n"
            "  Target       : %s\n"
            "\n"
            "  interest     %6.2f\n"
            "  curiosity    %6.2f\n"
            "  role         %6.2f  (intrinsic)%s\n"
            "  familiarity  %6.2f\n"
            "  move_cost    %6.2f\n"
            "\n"
            "  Decision     : %s\n"
            "%s",
            _SEP, _SEP,
            target_name,
            interest,
            curiosity,
            role_intrinsic,
            mission_line,
            familiarity,
            movement_cost,
            decision.upper(),
            _SEP,
        )

        # Track if mission actually changed the PTZ target
        if self._last_ptz_target and self._last_ptz_target != target_name:
            self.ptz_target_changed_count += 1
        self._last_ptz_target = target_name

    # ═══════════════════════════════════════════════════════
    # 5. Mission Expire
    # ═══════════════════════════════════════════════════════

    def log_mission_expire(self):
        """Called when MissionRole TTL expires."""
        self.expire_count += 1
        logger.info(
            "\n%s\n"
            "Mission Expired\n"
            "%s\n"
            "  Effective Mission cleared.\n"
            "  Fallback: Intrinsic Role only.\n"
            "%s",
            _DSEP, _DSEP, _DSEP,
        )

    # ═══════════════════════════════════════════════════════
    # 6. Provider Compare
    # ═══════════════════════════════════════════════════════

    def log_provider_compare(
        self,
        rule_weights: Dict[str, float],
        llm_weights: Dict[str, float],
    ):
        """Compare rule (persona defaults) vs LLM weights."""
        all_classes = set(rule_weights.keys()) | set(llm_weights.keys())

        added = []
        increased = []
        reduced = []
        removed = []
        unchanged = []

        for cls in sorted(all_classes):
            rw = rule_weights.get(cls, 0.0)
            lw = llm_weights.get(cls, 0.0)

            if rw == 0 and lw > 0:
                added.append((cls, lw))
            elif rw > 0 and lw == 0:
                removed.append((cls, rw))
            elif lw > rw + 0.02:
                increased.append((cls, rw, lw))
            elif lw < rw - 0.02:
                reduced.append((cls, rw, lw))
            else:
                unchanged.append((cls, lw))

        sections = []
        if added:
            sections.append("  LLM Added:\n" + "\n".join(
                f"    + {cls:<18} → {lw:.2f}" for cls, lw in added))
        if removed:
            sections.append("  LLM Removed:\n" + "\n".join(
                f"    − {cls:<18} was {rw:.2f}" for cls, rw in removed))
        if increased:
            sections.append("  LLM Increased:\n" + "\n".join(
                f"    ↑ {cls:<18} {rw:.2f} → {lw:.2f}" for cls, rw, lw in increased))
        if reduced:
            sections.append("  LLM Reduced:\n" + "\n".join(
                f"    ↓ {cls:<18} {rw:.2f} → {lw:.2f}" for cls, rw, lw in reduced))

        if not sections:
            logger.info("Provider Compare: rule and LLM weights are identical")
            return

        logger.info(
            "\n%s\nMission Difference (Rule → LLM)\n%s\n"
            "%s\n"
            "%s",
            _SEP, _SEP,
            "\n".join(sections),
            _SEP,
        )

    # ═══════════════════════════════════════════════════════
    # 7. Persona Signature
    # ═══════════════════════════════════════════════════════

    def log_persona_signature(
        self,
        persona_name: str,
        class_distribution: List[Tuple[str, float]],
    ):
        """Periodic attention distribution snapshot (every ~30 min).

        Args:
            class_distribution: [(class_name, share%), ...] sorted by share desc
        """
        self._last_persona_snapshot = time.time()

        total = sum(s for _, s in class_distribution)
        lines = []
        for cls, share in class_distribution[:10]:
            pct = share / max(total, 0.01) * 100
            bar = "█" * int(pct / 2)
            lines.append(f"  {cls:<18} {pct:5.1f}%  {bar}")

        others = sum(s for _, s in class_distribution[10:])
        if others > 0:
            pct = others / max(total, 0.01) * 100
            lines.append(f"  {'(others)':<18} {pct:5.1f}%  {'░' * int(pct / 2)}")

        logger.info(
            "\n%s\nPersona Signature: %s\n%s\n%s\n%s",
            _DSEP, persona_name, _DSEP,
            "\n".join(lines),
            _DSEP,
        )

    # ═══════════════════════════════════════════════════════
    # Mission Effectiveness (aggregate)
    # ═══════════════════════════════════════════════════════

    def log_effectiveness(self):
        """Periodic aggregate: did the mission actually change behavior?"""
        now = time.time()
        elapsed = now - self._last_flush
        self._last_flush = now

        total = max(self.refresh_count, 1)
        curiosity_pct = self.curiosity_changed_count / total * 100
        ptz_pct = self.ptz_target_changed_count / total * 100
        ignored_pct = self.mission_ignored_count / total * 100

        logger.info(
            "\n%s\n"
            "Mission Effectiveness (%.0f min)\n"
            "%s\n"
            "  Mission Refresh      : %d\n"
            "  Curiosity Changed     : %d (%.0f%%)\n"
            "  PTZ Target Changed    : %d (%.0f%%)\n"
            "  Mission Ignored       : %d (%.0f%%)\n"
            "  Expired               : %d\n"
            "  Fallback Used         : %d\n"
            "%s",
            _DSEP,
            elapsed / 60,
            _DSEP,
            self.refresh_count,
            self.curiosity_changed_count, curiosity_pct,
            self.ptz_target_changed_count, ptz_pct,
            self.mission_ignored_count, ignored_pct,
            self.expire_count,
            self.fallback_used_count,
            _DSEP,
        )

        # Reset counters for next period
        self.refresh_count = 0
        self.curiosity_changed_count = 0
        self.ptz_target_changed_count = 0
        self.mission_ignored_count = 0
        self.expire_count = 0
        self.fallback_used_count = 0

    def periodic_flush(self, force: bool = False):
        """Call from main loop. Flushes effectiveness every 30 min."""
        now = time.time()
        if force or now - self._last_flush > 1800.0:  # 30 min
            if self.refresh_count > 0:
                self.log_effectiveness()

    def periodic_persona_signature(
        self,
        persona_name: str,
        class_distribution: List[Tuple[str, float]],
        force: bool = False,
    ):
        """Call from main loop. Logs persona signature every 30 min."""
        now = time.time()
        if force or now - self._last_persona_snapshot > 1800.0:
            self.log_persona_signature(persona_name, class_distribution)


# ── Helpers ──

def _format_weight_column(weights: Dict[str, float]) -> str:
    """Format weights as aligned columns."""
    if not weights:
        return ""
    items = sorted(weights.items(), key=lambda x: abs(x[1]), reverse=True)
    lines = []
    for cls, w in items:
        if abs(w) < 0.005:
            continue
        sign = "+" if w > 0 else ("−" if w < 0 else " ")
        lines.append(f"    {cls:<18} {sign}{abs(w):.2f}")
    return "\n".join(lines)


def _indent(text: str, spaces: int) -> str:
    """Indent each line of text by N spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.split("\n"))
