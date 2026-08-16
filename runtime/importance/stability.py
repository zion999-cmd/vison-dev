"""
Stability Analyzer — Observation Protocol for Attention Ecology.

Phase 7C: determines whether the attention system has reached a stable
ecological structure. Read-only — no modifications to runtime logic.

Reads from:
  - importance_candidates.log (per-30min top-density snapshots)
  - entity_stats.db (cross-session entity statistics)

Computes 5 metrics:
  1. Top-K Stability    — day-over-day entity overlap ratio
  2. Entity Survival    — lifespan distribution histogram
  3. Noise Index        — fraction of known-noisy classes in top-K
  4. Concentration      — entropy of importance distribution
  5. Structural Check   — head / mid / tail tier emergence

Outputs:
  logs/attention_stability_report.json

Usage:
  python runtime/importance/stability.py          # on-demand analysis
  analyzer = StabilityAnalyzer(); analyzer.run()  # from runtime
"""

import json
import math
import os
import sqlite3
import logging
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("Stability")

# ── Known noisy YOLO classes (Phase 7B noise catalog) ──
# These are COCO classes that YOLO hallucinates on blank walls / indoor scenes.
# They should NOT appear in stable top-K rankings.
KNOWN_NOISY_CLASSES = {
    "clock", "train", "mouse", "remote", "cell phone",
    "toothbrush", "keyboard", "book", "tv", "bottle",
    "cup", "bowl", "sink", "refrigerator", "motorcycle",
    "bicycle", "surfboard", "umbrella",
}

# Classes that are genuinely expected in indoor home scenes.
# These appearing in top-K is a healthy signal.
EXPECTED_CLASSES = {
    "person", "chair", "couch", "dining table", "bed",
    "laptop", "backpack", "handbag", "suitcase", "cat", "dog",
}


class StabilityAnalyzer:
    """Read-only analysis of attention ecology stability.

    All methods are pure functions over existing data files.
    No runtime state is modified.
    """

    def __init__(self, project_root: Optional[str] = None):
        if project_root is None:
            project_root = os.path.join(os.path.dirname(__file__), "..", "..")
        self._root = os.path.abspath(project_root)
        self._candidates_path = os.path.join(
            self._root, "logs", "importance_candidates.log")
        self._db_path = os.path.join(
            self._root, "logs", "entity_stats.db")
        self._report_path = os.path.join(
            self._root, "logs", "attention_stability_report.json")

    # ═══════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════

    def run(self) -> Dict:
        """Run full stability analysis. Returns the report dict."""
        logger.info("StabilityAnalyzer: starting analysis...")

        # Parse importance_candidates.log into daily buckets
        daily_data = self._parse_daily_candidates()

        if len(daily_data) < 2:
            logger.warning("Need at least 2 days of data for stability analysis "
                           "(got %d days)", len(daily_data))
            return {"error": "insufficient_data", "days": len(daily_data)}

        days = sorted(daily_data.keys())

        report = {
            "generated_at": datetime.now().isoformat(),
            "observation_days": len(days),
            "date_range": {"start": days[0], "end": days[-1]},
            "metrics": {
                "top_k_stability": self._compute_top_k_stability(daily_data, days, k=10),
                "entity_survival": self._compute_survival_rate(),
                "noise_index": self._compute_noise_index(daily_data, days, k=10),
                "concentration": self._compute_concentration(daily_data, days),
                "structural_emergence": self._check_structure(daily_data, days),
            },
            "verdict": None,  # populated below
        }

        # ── Overall verdict ──
        report["verdict"] = self._evaluate_verdict(report["metrics"])

        # Write report
        self._write_report(report)
        logger.info("StabilityAnalyzer: report written to %s", self._report_path)

        return report

    # ═══════════════════════════════════════════════════════════════
    # Data parsing
    # ═══════════════════════════════════════════════════════════════

    def _parse_daily_candidates(self) -> Dict[str, List[Dict]]:
        """Parse importance_candidates.log into {date: [entries]}.

        Each entry is a single snapshot's top_density list.
        """
        daily: Dict[str, List[Dict]] = defaultdict(list)

        if not os.path.exists(self._candidates_path):
            logger.warning("importance_candidates.log not found at %s",
                           self._candidates_path)
            return dict(daily)

        with open(self._candidates_path) as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = entry.get("ts", 0)
                if not ts:
                    continue

                day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                for candidate in entry.get("top_density", []):
                    daily[day].append(candidate)

        return dict(daily)

    # ═══════════════════════════════════════════════════════════════
    # Metric 1: Top-K Stability
    # ═══════════════════════════════════════════════════════════════

    def _compute_top_k_stability(self, daily_data: Dict[str, List[Dict]],
                                  days: List[str], k: int = 10) -> Dict:
        """Day-over-day top-K class overlap ratio.

        Uses class_name as the stable identity (entity_ids are per-session).
        For each day, aggregates all snapshots, ranks classes by total density,
        then computes Jaccard overlap between consecutive days.
        """
        daily_topk: Dict[str, set] = {}

        for day in days:
            entries = daily_data[day]
            # Aggregate by class: sum of density across all snapshots
            class_density = defaultdict(float)
            for e in entries:
                cls = e.get("class", "unknown")
                class_density[cls] += e.get("density", 0)

            # Top-K classes by total density
            ranked = sorted(class_density.items(), key=lambda x: x[1], reverse=True)
            daily_topk[day] = {cls for cls, _ in ranked[:k]}

        # Day-over-day overlap
        overlaps = []
        for i in range(len(days) - 1):
            d1, d2 = days[i], days[i + 1]
            top1 = daily_topk[d1]
            top2 = daily_topk[d2]
            if not top1 or not top2:
                overlaps.append({"from": d1, "to": d2, "overlap": None, "ratio": None})
                continue
            intersection = top1 & top2
            ratio = len(intersection) / k
            overlaps.append({
                "from": d1,
                "to": d2,
                "overlap": sorted(intersection),
                "ratio": round(ratio, 3),
            })

        avg_ratio = sum(o["ratio"] for o in overlaps if o["ratio"] is not None)
        n_valid = sum(1 for o in overlaps if o["ratio"] is not None)
        avg_ratio = round(avg_ratio / n_valid, 3) if n_valid > 0 else None

        # Latest day's top-K for reference
        latest_topk = {}
        if days:
            latest = daily_topk.get(days[-1], set())
            # Get density scores for latest top-K
            class_density = defaultdict(float)
            for e in daily_data.get(days[-1], []):
                class_density[e.get("class", "unknown")] += e.get("density", 0)
            latest_topk = {
                cls: round(class_density[cls], 2)
                for cls in latest
            }

        return {
            "k": k,
            "daily_overlaps": overlaps,
            "average_ratio": avg_ratio,
            "trend": "stable" if (avg_ratio or 0) >= 0.6 else "unstable",
            "latest_top_k": latest_topk,
            "latest_top_k_list": sorted(latest_topk.keys()),
        }

    # ═══════════════════════════════════════════════════════════════
    # Metric 2: Entity Survival Rate
    # ═══════════════════════════════════════════════════════════════

    def _compute_survival_rate(self) -> Dict:
        """Entity lifespan distribution from entity_stats.db.

        survival = last_seen - first_seen (seconds)
        Buckets: <1h, 1-6h, 6-24h, 24-72h, 72h+
        """
        if not os.path.exists(self._db_path):
            return {"error": "entity_stats.db not found"}

        conn = sqlite3.connect(self._db_path)
        rows = conn.execute(
            "SELECT class_name, first_seen, last_seen, seen_total, "
            "interaction_total FROM entity_stats"
        ).fetchall()
        conn.close()

        if not rows:
            return {"error": "no entities in database"}

        lifespans: List[float] = []
        class_lifespans: Dict[str, List[float]] = defaultdict(list)
        total_entities = len(rows)

        for class_name, first_seen, last_seen, seen, interactions in rows:
            lifespan_s = last_seen - first_seen
            if lifespan_s <= 0:
                continue
            lifespans.append(lifespan_s)
            class_lifespans[class_name].append(lifespan_s)

        if not lifespans:
            return {"error": "no valid lifespans"}

        # Buckets (in hours)
        buckets = [
            ("<1h", 0, 1),
            ("1-6h", 1, 6),
            ("6-24h", 6, 24),
            ("24-72h", 24, 72),
            ("72h+", 72, float("inf")),
        ]

        histogram = {}
        for label, lo, hi in buckets:
            lo_s, hi_s = lo * 3600, hi * 3600
            count = sum(1 for l in lifespans if lo_s <= l < hi_s)
            histogram[label] = {
                "count": count,
                "ratio": round(count / len(lifespans), 3),
            }

        # Long-tail check: entities lasting >24h vs short-lived
        long_lived = sum(1 for l in lifespans if l > 86400)
        short_lived = sum(1 for l in lifespans if l <= 3600)

        # Per-class average lifespan (top 10 by avg lifespan)
        class_avg = {}
        for cls, ls in class_lifespans.items():
            if len(ls) >= 2:  # at least 2 entities for meaningful avg
                class_avg[cls] = {
                    "count": len(ls),
                    "avg_hours": round(sum(ls) / len(ls) / 3600, 1),
                    "max_hours": round(max(ls) / 3600, 1),
                }

        top_survivors = sorted(
            class_avg.items(),
            key=lambda x: x[1]["avg_hours"], reverse=True
        )[:10]

        return {
            "total_entities": total_entities,
            "entities_with_lifespan": len(lifespans),
            "histogram": histogram,
            "long_lived_ratio": round(long_lived / len(lifespans), 3),
            "short_lived_ratio": round(short_lived / len(lifespans), 3),
            "long_tail_present": long_lived > 0 and (long_lived / len(lifespans)) > 0.05,
            "top_survivor_classes": dict(top_survivors),
        }

    # ═══════════════════════════════════════════════════════════════
    # Metric 3: Noise Persistence Index
    # ═══════════════════════════════════════════════════════════════

    def _compute_noise_index(self, daily_data: Dict[str, List[Dict]],
                              days: List[str], k: int = 10) -> Dict:
        """Fraction of known-noisy classes appearing in daily top-K.

        Target: noise_ratio → 0 (noisy classes excluded from top rankings).
        """
        daily_noise = []

        for day in days:
            entries = daily_data[day]
            class_density = defaultdict(float)
            for e in entries:
                class_density[e.get("class", "unknown")] += e.get("density", 0)

            ranked = sorted(class_density.items(), key=lambda x: x[1], reverse=True)
            top_classes = {cls for cls, _ in ranked[:k]}

            noisy_in_top = top_classes & KNOWN_NOISY_CLASSES
            expected_in_top = top_classes & EXPECTED_CLASSES

            daily_noise.append({
                "day": day,
                "noisy_classes": sorted(noisy_in_top),
                "noise_count": len(noisy_in_top),
                "noise_ratio": round(len(noisy_in_top) / k, 3),
                "expected_classes": sorted(expected_in_top),
                "expected_count": len(expected_in_top),
            })

        avg_noise = sum(d["noise_ratio"] for d in daily_noise) / len(daily_noise)
        trending_down = all(
            daily_noise[i]["noise_ratio"] >= daily_noise[i + 1]["noise_ratio"]
            for i in range(len(daily_noise) - 1)
            if daily_noise[i + 1]["noise_ratio"] > 0
        ) if len(daily_noise) >= 2 else None

        return {
            "daily": daily_noise,
            "average_noise_ratio": round(avg_noise, 3),
            "trending_down": trending_down,
            "target_met": avg_noise < 0.2,  # <20% noise in top-K
            "noisy_classes_tracked": sorted(KNOWN_NOISY_CLASSES),
        }

    # ═══════════════════════════════════════════════════════════════
    # Metric 4: Interaction Concentration (Entropy)
    # ═══════════════════════════════════════════════════════════════

    def _compute_concentration(self, daily_data: Dict[str, List[Dict]],
                                days: List[str]) -> Dict:
        """Normalized entropy of importance distribution per day.

        High entropy = flat distribution (no focus).
        Low entropy = concentrated on few classes (focused attention).
        Neither is "correct" — we track the TREND toward stability.
        """
        daily_entropy = []

        for day in days:
            entries = daily_data[day]
            class_interactions = defaultdict(float)
            for e in entries:
                class_interactions[e.get("class", "unknown")] += e.get("interactions", 0)

            total = sum(class_interactions.values())
            if total == 0 or len(class_interactions) < 1:
                daily_entropy.append({
                    "day": day, "entropy": None, "norm_entropy": None,
                    "num_classes": len(class_interactions),
                })
                continue

            # Shannon entropy: H = -Σ p_i × log(p_i)
            n = len(class_interactions)
            entropy = 0.0
            for count in class_interactions.values():
                p = count / total
                if p > 0:
                    entropy -= p * math.log(p)

            # Normalized: H / log(N) — 0=fully concentrated, 1=perfectly flat
            norm_entropy = entropy / math.log(n) if n > 1 else 0.0

            daily_entropy.append({
                "day": day,
                "entropy": round(entropy, 4),
                "norm_entropy": round(norm_entropy, 4),
                "num_classes": n,
            })

        valid = [d["norm_entropy"] for d in daily_entropy if d["norm_entropy"] is not None]
        avg_entropy = round(sum(valid) / len(valid), 4) if valid else None

        # Check if entropy has stabilized (no large swings in recent days)
        stable = False
        if len(valid) >= 3:
            recent = valid[-3:]
            variation = max(recent) - min(recent)
            stable = variation < 0.2

        return {
            "daily": daily_entropy,
            "average_norm_entropy": avg_entropy,
            "entropy_stable": stable,
            "interpretation": (
                "focused" if (avg_entropy or 1.0) < 0.4
                else "moderate" if (avg_entropy or 1.0) < 0.7
                else "flat"
            ),
        }

    # ═══════════════════════════════════════════════════════════════
    # Metric 5: Structural Emergence Check
    # ═══════════════════════════════════════════════════════════════

    def _check_structure(self, daily_data: Dict[str, List[Dict]],
                          days: List[str]) -> Dict:
        """Check whether a three-tier attention ecology has emerged.

        Expected stable structure:
          - Stable core (head): top 3-5 classes, persistent across days
          - Dynamic mid-layer: fluctuates, moderate importance
          - Transient noise tail: suppressed, rarely in top rankings

        This is the most important metric — it signals that the attention
        system has self-organized into a functional ecology.
        """
        if len(days) < 2:
            return {"error": "need at least 2 days"}

        # Aggregate all days into global class ranking
        class_total_density = defaultdict(float)
        class_total_interactions = defaultdict(int)
        class_days_appeared = defaultdict(set)

        for day in days:
            for e in daily_data[day]:
                cls = e.get("class", "unknown")
                class_total_density[cls] += e.get("density", 0)
                class_total_interactions[cls] += e.get("interactions", 0)
                class_days_appeared[cls].add(day)

        ranked = sorted(class_total_density.items(),
                        key=lambda x: x[1], reverse=True)
        total_density = sum(d for _, d in ranked)
        n_classes = len(ranked)

        if n_classes < 5:
            return {"error": f"too few classes ({n_classes}) for structure analysis"}

        # Cumulative density to find natural breakpoints
        cumulative = 0.0
        tiers = {"head": [], "mid": [], "tail": []}
        for cls, density in ranked:
            cumulative += density
            pct = cumulative / total_density

            # Head: top classes accounting for first 50% of density
            # Mid: next 40% (50%–90%)
            # Tail: last 10%
            if pct <= 0.50:
                tier = "head"
            elif pct <= 0.90:
                tier = "mid"
            else:
                tier = "tail"
            tiers[tier].append({
                "class": cls,
                "density": round(density, 2),
                "interactions": class_total_interactions[cls],
                "days_present": len(class_days_appeared[cls]),
                "persistence": round(len(class_days_appeared[cls]) / len(days), 2),
            })

        # Check structural criteria
        head_classes = {t["class"] for t in tiers["head"]}
        head_persistence = (
            sum(t["persistence"] for t in tiers["head"]) / len(tiers["head"])
            if tiers["head"] else 0
        )
        tail_noise_ratio = (
            len([t for t in tiers["tail"]
                 if t["class"] in KNOWN_NOISY_CLASSES]) / len(tiers["tail"])
            if tiers["tail"] else 0
        )

        structure_emerged = (
            len(tiers["head"]) >= 2
            and head_persistence >= 0.5
            and len(tiers["tail"]) >= len(tiers["head"]) * 0.5
        )

        return {
            "total_classes": n_classes,
            "tiers": {
                "head": {
                    "count": len(tiers["head"]),
                    "classes": tiers["head"][:5],
                    "avg_persistence": round(head_persistence, 2),
                },
                "mid": {
                    "count": len(tiers["mid"]),
                    "classes": tiers["mid"][:5],
                },
                "tail": {
                    "count": len(tiers["tail"]),
                    "classes": tiers["tail"][:5],
                    "noise_ratio": round(tail_noise_ratio, 2),
                },
            },
            "structure_emerged": structure_emerged,
            "assessment": (
                "three-tier ecology detected"
                if structure_emerged
                else "structure not yet stabilized — more observation needed"
            ),
        }

    # ═══════════════════════════════════════════════════════════════
    # Verdict
    # ═══════════════════════════════════════════════════════════════

    def _evaluate_verdict(self, metrics: Dict) -> Dict:
        """Aggregate all metrics into a single go/no-go verdict.

        Per P0007C success criteria:
          1. Top-10 overlap ≥ 60% (across 2-3 days)
          2. noise_ratio → 0
          3. entropy stable (no wild swings)
          4. long-tail survival distribution
          5. three-tier structure emerged
        """
        checks = []

        # Check 1: Top-K stability
        topk = metrics.get("top_k_stability", {})
        avg_ratio = topk.get("average_ratio")
        if avg_ratio is not None:
            ok = avg_ratio >= 0.6
            checks.append({
                "criterion": "top_k_stability",
                "target": "≥ 0.60",
                "actual": avg_ratio,
                "passed": ok,
            })
        else:
            checks.append({
                "criterion": "top_k_stability",
                "target": "≥ 0.60",
                "actual": None,
                "passed": False,
                "note": "insufficient data",
            })

        # Check 2: Noise ratio
        noise = metrics.get("noise_index", {})
        avg_noise = noise.get("average_noise_ratio")
        if avg_noise is not None:
            ok = avg_noise < 0.20
            checks.append({
                "criterion": "noise_ratio",
                "target": "< 0.20",
                "actual": avg_noise,
                "passed": ok,
            })
        else:
            checks.append({
                "criterion": "noise_ratio",
                "target": "< 0.20",
                "actual": None,
                "passed": False,
                "note": "insufficient data",
            })

        # Check 3: Entropy stability
        conc = metrics.get("concentration", {})
        checks.append({
            "criterion": "entropy_stable",
            "target": "variation < 0.2",
            "actual": conc.get("average_norm_entropy"),
            "passed": conc.get("entropy_stable", False),
        })

        # Check 4: Long-tail survival
        survival = metrics.get("entity_survival", {})
        long_tail = survival.get("long_tail_present", False)
        checks.append({
            "criterion": "long_tail_survival",
            "target": ">5% entities live >24h",
            "actual": survival.get("long_lived_ratio"),
            "passed": long_tail,
        })

        # Check 5: Three-tier structure
        structure = metrics.get("structural_emergence", {})
        emerged = structure.get("structure_emerged", False)
        checks.append({
            "criterion": "three_tier_structure",
            "target": "head ≥2 classes + persistence ≥50%",
            "actual": structure.get("tiers", {}).get("head", {}).get("count", 0),
            "passed": emerged,
        })

        passed = sum(1 for c in checks if c["passed"])
        total = len(checks)

        return {
            "checks": checks,
            "passed": passed,
            "total": total,
            "score": round(passed / total, 2),
            "verdict": "STABLE" if passed >= 4 else (
                "CONVERGING" if passed >= 2 else "UNSTABLE"
            ),
            "recommendation": (
                "Proceed to Value Engine (Phase 7B+)"
                if passed >= 4
                else "Continue observation — attention ecology not yet stable"
                if passed >= 2
                else "Return to grounding phase — fundamental instability detected"
            ),
        }

    # ═══════════════════════════════════════════════════════════════
    # Output
    # ═══════════════════════════════════════════════════════════════

    def _write_report(self, report: Dict):
        """Write the stability report to logs/attention_stability_report.json."""
        os.makedirs(os.path.dirname(self._report_path), exist_ok=True)
        with open(self._report_path, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# Standalone execution
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    analyzer = StabilityAnalyzer()
    report = analyzer.run()
    verdict = report.get("verdict", {})
    print(f"\nVerdict: {verdict.get('verdict', 'UNKNOWN')} "
          f"({verdict.get('passed', 0)}/{verdict.get('total', 0)} checks passed)")
    print(f"Recommendation: {verdict.get('recommendation', 'N/A')}")
