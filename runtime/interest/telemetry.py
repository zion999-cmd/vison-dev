"""
Behavioral Telemetry — observe the Interest ecology over long runs.

Logs:
- Interest distribution snapshot (every 30 min)
- Revisit attempts + success/fail ratio
- Focus entropy (concentration metric)
- Curiosity queue state
- Obsession warnings

Output: logs/telemetry/behavior_YYYYMMDD_HHMMSS.log
"""

import time, json, os, math, logging, threading
from typing import Dict, List

logger = logging.getLogger("Interest.Telemetry")


class BehavioralTelemetry:
    """Non-intrusive observer of the Active Observation ecology.

    Captures both InterestEngine targets AND AnchorManager spatial anchors.
    Even when InterestEngine is empty, the AnchorManager data reveals the
    true Attention Geography — where the system IS looking vs. what it CARES about.
    """

    def __init__(self, interest_engine, anchor_manager=None, log_dir="logs/telemetry"):
        self._engine = interest_engine
        self._anchor_manager = anchor_manager
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._path = os.path.join(log_dir, f"behavior_{timestamp}.log")

        # Counters
        self._revisit_attempts = 0
        self._revisit_successes = 0
        self._revisit_failures = 0
        self._lock = threading.Lock()

        self._log("=== Behavioral Telemetry Started ===")
        self._log(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    def record_revisit(self, target_id: str, success: bool):
        with self._lock:
            self._revisit_attempts += 1
            if success:
                self._revisit_successes += 1
            else:
                self._revisit_failures += 1

    def snapshot(self):
        """Periodic snapshot of the entire Attention ecology.

        Always writes output — even if InterestEngine is empty, the
        AnchorManager spatial anchors reveal the true Attention Geography.
        """
        # ── Anchor data (ALWAYS captured) ──
        anchors = []
        if self._anchor_manager:
            anchors = self._anchor_manager.all_anchors()

        anchor_rows = []
        for a in anchors:
            anchor_rows.append({
                "id": a.anchor_id,
                "pan": a.pan,
                "tilt": a.tilt,
                "visits": a.visit_count,
                "interest": round(a.interest, 4),
                "novelty": round(a.novelty, 4),
                "uncertainty": round(1.0 - math.exp(-a.since_visited() / 300.0), 4),
                "last_visit_ago_s": round(a.since_visited(), 0) if a.visit_count > 0 else None,
                "baseline_objects": sorted(a.baseline_objects),
                "common_objects": a.common_objects,
            })

        # Sort by interest descending
        anchor_rows.sort(key=lambda x: x["interest"], reverse=True)

        dist = {
            "anchor_count": len(anchors),
            "anchors": anchor_rows,
        }

        # ── Interest Engine data (if any) ──
        targets = self._engine.top_interests(20)
        total_targets = self._engine.target_count

        interests = [t.interest for t in targets]
        dist["interest_targets"] = total_targets
        dist["mean_interest"] = sum(interests) / len(interests) if interests else 0
        dist["max_interest"] = max(interests) if interests else 0
        dist["min_interest"] = min(interests) if interests else 0

        # Focus entropy: how concentrated is attention?
        # Low entropy = obsessed with one target
        # High entropy = evenly distributed
        total = sum(interests)
        if total > 0:
            probs = [i / total for i in interests]
            entropy = -sum(p * math.log(p) for p in probs if p > 0)
            normalized_entropy = entropy / math.log(len(probs)) if len(probs) > 1 else 0
            dist["focus_entropy"] = round(normalized_entropy, 4)
        else:
            dist["focus_entropy"] = 0.0

        # Top targets
        top = []
        for t in targets[:5]:
            top.append({
                "id": t.target_id,
                "type": t.target_type,
                "interest": round(t.interest, 4),
                "uncertainty": round(1.0 - math.exp(-t.since_confirmed() / 180.0), 4),
                "fails": t.consecutive_fails,
                "age_min": round(t.age() / 60, 1),
            })
        dist["top5"] = top

        # Revisit stats
        with self._lock:
            attempts = self._revisit_attempts
            successes = self._revisit_successes
            failures = self._revisit_failures

        if attempts > 0:
            dist["revisit_rate"] = round(successes / attempts, 3)
        else:
            dist["revisit_rate"] = None

        dist["revisit_total"] = attempts

        # ── Attention Geography report (from anchors, not InterestEngine) ──
        report_lines = []
        if anchor_rows:
            # Focus entropy from anchor interests
            a_interests = [a["interest"] for a in anchor_rows if a["interest"] > 0]
            if a_interests:
                total = sum(a_interests)
                probs = [i / total for i in a_interests]
                entropy = -sum(p * math.log(p) for p in probs if p > 0)
                n = len(probs)
                dist["focus_entropy"] = round(entropy / math.log(n), 4) if n > 1 else 0

            report_lines = ["===== ATTENTION GEOGRAPHY ====="]
            report_lines.append(f"Anchors: {len(anchors)} | "
                                f"Avg interest: {sum(a['interest'] for a in anchor_rows)/len(anchor_rows):.3f} | "
                                f"Entropy: {dist.get('focus_entropy', 'N/A')}")
            report_lines.append(f"{'Anchor':<20s} {'Visits':>6s} {'Int':>6s} {'Nov':>6s} {'Objects'}")
            report_lines.append("-" * 65)
            for a in anchor_rows[:12]:
                objects = ",".join(a["common_objects"][:3]) if a["common_objects"] else "-"
                report_lines.append(
                    f"{a['id']:<20s} {a['visits']:>6d} {a['interest']:>6.3f} "
                    f"{a['novelty']:>6.3f} {objects}"
                )
            dist["_report"] = "\n".join(report_lines)
        else:
            report_lines = ["===== ATTENTION GEOGRAPHY =====", "(no anchors yet)"]
            dist["_report"] = "\n".join(report_lines)

        # Warnings
        warnings = []
        if dist["focus_entropy"] < 0.3 and total_targets > 3:
            warnings.append("LOW_ENTROPY: attention too concentrated")
        if dist["focus_entropy"] > 0.9:
            warnings.append("HIGH_ENTROPY: attention too scattered")
        if dist["revisit_rate"] is not None and dist["revisit_rate"] > 0.9:
            warnings.append("HIGH_SUCCESS: only looking at confirmed targets")
        if dist["revisit_rate"] is not None and dist["revisit_rate"] < 0.05 and attempts > 5:
            warnings.append("LOW_SUCCESS: system wandering aimlessly")
        if dist["max_interest"] < 0.1 and total_targets > 0:
            warnings.append("CURIOSITY_DEATH: all interests near zero")
        for t in targets:
            if t.consecutive_fails >= 3 and t.interest > 0.3:
                warnings.append(f"OBSESSION: {t.target_id} fails={t.consecutive_fails} but interest={t.interest:.2f}")
        if warnings:
            dist["warnings"] = warnings

        # Log JSON + human-readable report
        self._log(json.dumps(dist, ensure_ascii=False))
        if "_report" in dist:
            for line in dist["_report"].split("\n"):
                self._log(line)

    def _log(self, msg: str):
        try:
            with open(self._path, "a") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except OSError:
            pass
