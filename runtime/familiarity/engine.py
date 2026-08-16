"""
Familiarity Engine — session-level habituation.

    "Have I seen this thing enough times to stop being curious?"

This is NOT Value (how important is it?) and NOT Memory (should I remember it?).
It answers a simpler question: is this entity still novel, or have I habituated?

Architecture position:
    Interest   → "what changed?"        (seconds)
    Curiosity  → "what's worth revisiting?" (minutes)
    Familiarity → "have I seen this enough?" (minutes–hours)
    Role       → "what should I care about?" (innate)
    Value      → "what should I remember?"   (days)

Key invariant: Familiarity ≠ Value. A chair seen 500× is familiar (0.95)
but has low value (0.1). A family member seen 5000× is familiar (1.0) AND
high value (1.0). Familiarity only answers "seen before?", not "important?".
"""

import math
import logging
from typing import List
from collections import Counter

logger = logging.getLogger("Familiarity")


class FamiliarityEngine:
    """Updates and queries entity familiarity scores.

    Usage:
        engine = FamiliarityEngine()
        engine.update(entity)          # call after entity.mark_seen()
        score = engine.score(entity)   # 0=new, 1=completely habituated
        dist = engine.distribution(entities)  # for telemetry
    """

    # Familiarity formula parameters
    _LOG_BASE = 1.0   # log(count + 1) smooths early growth

    def update(self, entity) -> float:
        """Recompute familiarity score for an entity. Returns new score."""
        count = max(entity.session_seen_count, 0)
        # 0 at first sight, asymptotically approaches 1.
        # count=0 → 0.0, count=10 → 0.49, count=100 → 0.68, count=1000 → 0.82
        score = 1.0 - 1.0 / (math.log(count + 1) + self._LOG_BASE)
        # Guard: count=0 should give exactly 0 for clarity
        if count == 0:
            score = 0.0
        entity.familiarity_score = max(0.0, min(1.0, score))
        return entity.familiarity_score

    def score(self, entity) -> float:
        """Get current familiarity (0=new, 1=familiar)."""
        if entity.familiarity_score == 0.0 and entity.session_seen_count > 0:
            # Stale score — recompute
            return self.update(entity)
        return entity.familiarity_score

    def suppression_factor(self, entity) -> float:
        """(1 - familiarity) — multiply into curiosity to suppress familiar entities."""
        return 1.0 - self.score(entity)

    def distribution(self, entities: List) -> dict:
        """Bucket entities by familiarity for telemetry.
        Returns {bucket: count} where bucket is "0.0-0.2", "0.2-0.4", etc.
        """
        buckets = Counter()
        for e in entities:
            s = self.score(e)
            if s < 0.2:
                buckets["0.0-0.2"] += 1
            elif s < 0.4:
                buckets["0.2-0.4"] += 1
            elif s < 0.6:
                buckets["0.4-0.6"] += 1
            elif s < 0.8:
                buckets["0.6-0.8"] += 1
            else:
                buckets["0.8-1.0"] += 1
        return dict(sorted(buckets.items()))

    def log_distribution(self, entities: List, interval_s: float = 300.0):
        """Log familiarity distribution if enough time has passed."""
        now = __import__("time").time()
        if not hasattr(self, '_last_dist_log'):
            self._last_dist_log = 0.0
        if now - self._last_dist_log < interval_s:
            return
        self._last_dist_log = now

        dist = self.distribution(entities)
        total = sum(dist.values())
        if total == 0:
            return

        # Top familiar / least familiar
        ranked = sorted(entities, key=lambda e: self.score(e), reverse=True)
        top = [(e.class_name or e.entity_type, f"{self.score(e):.2f}")
               for e in ranked[:5]]
        bottom = [(e.class_name or e.entity_type, f"{self.score(e):.2f}")
                  for e in ranked[-5:]]

        logger.info("Familiarity dist (N=%d): %s | top=%s | novel=%s",
                    total,
                    " ".join(f"{k}:{v}" for k, v in dist.items()),
                    top, bottom)
