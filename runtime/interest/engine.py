"""
Interest Engine — persistence of attention across time.

Positioned between Attention and Memory:
    Perception → Attention → Interest → Familiarity → Memory → Personality

Interest bridges the gap between "what's happening now" and "what matters":
- Novelty alone fades instantly — Interest lingers.
- A target seen once gets a baseline interest that decays slowly.
- If interesting enough, the system revisits the target later.
- Confirmed revisits reinforce interest.  Failed revisits accelerate decay.

This is what makes the system seem like it "cares" — it remembers
what was worth looking at, and looks again.
"""

import time, math, threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class InterestTarget:
    """A thing the system has noticed and may want to check again.

    Two types:
      - "entity": a specific thing (person_17, cup_3). Moves. ID-based.
      - "region": a spatial area (door, desk). Fixed location. Name-based.

    This distinction matters because regions have fixed pan/tilt coordinates
    while entities' locations are approximate and may change.
    """

    target_id: str
    target_type: str = "entity"  # "entity" or "region"
    category: str = "object"     # "person", "object", "region"
    location: Tuple[float, float] = (0.0, 0.0)  # (pan_deg, tilt_deg)

    # State
    interest: float = 0.5
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_revisited: float = 0.0
    revisit_count: int = 0
    confirm_count: int = 0
    novelty: float = 0.0

    # Feedback counters — prevent obsession loops
    consecutive_fails: int = 0     # times revisited and found NOTHING
    consecutive_successes: int = 0 # times revisited and confirmed
    max_consecutive_fails: int = 5 # after this many, start dampening interest

    def age(self) -> float:
        """Seconds since first noticed."""
        return time.time() - self.first_seen

    def since_seen(self) -> float:
        """Seconds since last observed."""
        return time.time() - self.last_seen

    def since_revisit(self) -> float:
        """Seconds since last deliberate revisit."""
        if self.revisit_count == 0:
            return float("inf")
        return time.time() - self.last_revisited

    def since_confirmed(self) -> float:
        """Seconds since last confirmed observation.

        If never seen, returns inf (max uncertainty).
        Important: this is based on when we LAST SAW the target,
        not when we first noticed it.  A target seen 10s ago has
        low uncertainty; one not seen for 5min has high.
        """
        return time.time() - self.last_seen

    @property
    def curiosity_score(self) -> float:
        """How urgently should we look at this target right now?"""
        return CuriosityQueue.score(self)


# ── Engine ──

class CuriosityQueue:
    """Ranked queue of what to look at next.

    priority = interest × uncertainty × freshness - movement_cost

    Movement cost prevents the camera from swinging 150° for a marginally
    higher score — it creates natural, efficient scanning behaviour.
    """

    @staticmethod
    def score(target: "InterestTarget", current_pan: float = 0.0,
              current_tilt: float = 0.0) -> float:
        """Curiosity score with movement cost built in."""
        # Uncertainty grows with time since last confirmed
        since_confirm = target.since_confirmed()
        uncertainty = 1.0 - math.exp(-since_confirm / 180.0)

        # Freshness: only deprioritize very recently seen (<10s)
        dt = target.since_seen()
        freshness = min(1.0, dt / 10.0) if dt < 60 else 1.0

        raw = target.interest * uncertainty * freshness

        # Movement cost: angular distance normalised to [0, 0.3]
        pan_dist = abs(target.location[0] - current_pan)
        tilt_dist = abs(target.location[1] - current_tilt)
        # ~360° max pan, ~90° max tilt
        cost = min(0.3, (pan_dist / 360.0 + tilt_dist / 90.0) * 0.15)

        # Obsession dampening: repeated failures reduce score
        if target.consecutive_fails >= target.max_consecutive_fails:
            cost += 0.2  # significant penalty
        elif target.consecutive_fails >= 3:
            cost += 0.05 * (target.consecutive_fails - 2)

        return max(0.0, raw - cost)

    @classmethod
    def rank(cls, targets: List["InterestTarget"], top_n: int = 5,
             current_pan: float = 0.0, current_tilt: float = 0.0) -> List["InterestTarget"]:
        """Sort by curiosity score (with movement cost), filter near-zero."""
        scored = [(t, cls.score(t, current_pan, current_tilt)) for t in targets]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [t for t, s in scored[:top_n] if s > 0.01]

    # ── Entity-based scoring (same formula, Entity input) ──

    @staticmethod
    def score_entity(entity, current_pan: float = 0.0,
                     current_tilt: float = 0.0) -> float:
        """Curiosity score for Entity objects.

        Formula: interest × uncertainty × freshness × (1−fam) × role − cost

        Role weight is innate (person=1.0, chair=0.1). It ensures that even
        a familiar person outranks a novel chair — "who matters" precedes
        "what's new".
        """
        since = entity.since_last_seen
        uncertainty = 1.0 - math.exp(-since / 90.0)
        dt = since
        freshness = min(1.0, dt / 10.0) if dt < 60 else 1.0

        # Familiarity suppression: 0 (novel) → 1 (seen many times today)
        fam = getattr(entity, 'familiarity_score', 0.0)
        # Role: innate priority (person=1.0, chair=0.1)
        role = getattr(entity, 'role_weight', 0.2)

        raw = entity.interest * uncertainty * freshness * (1.0 - fam) * role

        pan_dist = abs(entity.last_pan - current_pan)
        tilt_dist = abs(entity.last_tilt - current_tilt)
        cost = min(0.3, (pan_dist / 360.0 + tilt_dist / 90.0) * 0.15)

        if entity.consecutive_fails >= entity.max_consecutive_fails:
            cost += 0.2
        elif entity.consecutive_fails >= 3:
            cost += 0.05 * (entity.consecutive_fails - 2)

        return max(0.0, raw - cost)

    @classmethod
    def rank_entities(cls, entities, top_n: int = 5,
                      current_pan: float = 0.0,
                      current_tilt: float = 0.0):
        """Sort entities by curiosity score (with movement cost)."""
        scored = [(e, cls.score_entity(e, current_pan, current_tilt)) for e in entities]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [(e, s) for e, s in scored[:top_n] if s > 0.01]


class InterestEngine:
    """Manages persistent interests and drives revisit decisions.

    Architecture invariant:
        Attention.priority = novelty * 0.7 + interest_bonus() * 0.3

    The interest bonus makes previously-seen targets "glow" in attention
    space.  Over time, this creates a natural scanning rhythm.
    """

    def __init__(self):
        self._targets: Dict[str, InterestTarget] = {}
        self._lock = threading.Lock()

        # Tuning
        self.decay_rate = 0.92       # per minute: interest *= decay_rate
        self.initial_interest = 0.4  # baseline for new targets
        self.novelty_boost = 0.3     # extra interest per unit of novelty
        self.confirm_gain = 0.15     # interest gain when revisit succeeds
        self.fail_penalty = 0.3      # interest drop when revisit fails
        self.min_interest = 0.05     # below this → forget
        self.max_interest = 1.0

    # ── Public API ──

    def see(
        self,
        target_id: str,
        target_type: str = "entity",
        category: str = "object",
        location: Tuple[float, float] = (0.0, 0.0),
        novelty: float = 0.0,
    ):
        """Record that a target was observed. Updates or creates interest.

        Args:
            target_id: Unique ID (e.g., "person_17", "door_region")
            target_type: "entity" (moves) or "region" (fixed location)
            category: "person", "object", "region"
            location: Approximate (pan_deg, tilt_deg)
            novelty: How novel/interesting this observation was [0,1]
        """
        with self._lock:
            if target_id in self._targets:
                t = self._targets[target_id]
                # If we deliberately revisited and found it → confirm
                if t.revisit_count > t.confirm_count:
                    t.confirm_count += 1
                    t.interest = min(self.max_interest,
                                     t.interest + self.confirm_gain)
                    t.consecutive_successes += 1
                    t.consecutive_fails = 0
                t.last_seen = time.time()
                # Only update location for entities (regions are fixed)
                if t.target_type == "entity":
                    t.location = location
            else:
                t = InterestTarget(
                    target_id=target_id,
                    target_type=target_type,
                    category=category,
                    location=location,
                    interest=self.initial_interest + novelty * self.novelty_boost,
                    novelty=novelty,
                )
                self._targets[target_id] = t

    def decay(self):
        """Age all interests. Call periodically (e.g., every minute)."""
        now = time.time()
        with self._lock:
            dead = []
            for tid, t in self._targets.items():
                minutes = (now - t.last_seen) / 60.0
                if minutes > 0:
                    t.interest *= self.decay_rate ** minutes
                if t.interest < self.min_interest:
                    dead.append(tid)
            for tid in dead:
                del self._targets[tid]

    def should_revisit(self, target_id: str) -> bool:
        """Check if it's time to deliberately look at this target again.

        Higher interest → shorter revisit interval.
        """
        with self._lock:
            t = self._targets.get(target_id)
            if t is None:
                return False
            # Interval scales inversely with interest:
            #   interest=0.9 → ~40s, interest=0.3 → ~3.5min, interest=0.1 → ~8min
            interval = 10 + (1.0 - t.interest) * 300  # seconds
            # Revisit if: haven't looked recently AND target was seen recently
            return t.since_revisit() > interval and t.since_seen() < interval

    def record_revisit(self, target_id: str):
        """Mark that we deliberately looked at this target."""
        with self._lock:
            t = self._targets.get(target_id)
            if t:
                t.last_revisited = time.time()
                t.revisit_count += 1

    def record_revisit_failed(self, target_id: str):
        """Revisit didn't find the target — reduce interest + track obsession."""
        with self._lock:
            t = self._targets.get(target_id)
            if t:
                t.consecutive_fails += 1
                t.consecutive_successes = 0
                # Progressive dampening: each failure hurts more
                penalty = self.fail_penalty * (1.0 + t.consecutive_fails * 0.1)
                t.interest = max(self.min_interest,
                                 t.interest - min(penalty, 0.5))

    def get_interest_bonus(self, target_id: str) -> float:
        """Interest contribution to attention priority. [0, 1]"""
        with self._lock:
            t = self._targets.get(target_id)
            return t.interest if t else 0.0

    def top_interests(self, n: int = 5) -> List[InterestTarget]:
        """Most interesting targets, sorted. For patrol/scan decisions."""
        self.decay()  # age first
        with self._lock:
            sorted_targets = sorted(
                self._targets.values(),
                key=lambda t: t.interest,
                reverse=True,
            )
            return sorted_targets[:n]

    def next_revisit(self, current_pan: float = 0.0,
                     current_tilt: float = 0.0) -> Optional[InterestTarget]:
        """The single most curious target to re-examine.

        Uses CuriosityQueue: interest × uncertainty × freshness - movement_cost.
        """
        self.decay()
        with self._lock:
            ranked = CuriosityQueue.rank(
                list(self._targets.values()), top_n=3,
                current_pan=current_pan, current_tilt=current_tilt,
            )
            return ranked[0] if ranked else None

    def curiosity_queue(self, top_n: int = 5, current_pan: float = 0.0,
                        current_tilt: float = 0.0) -> List[InterestTarget]:
        """Ranked targets by urgency (with movement cost)."""
        self.decay()
        with self._lock:
            return CuriosityQueue.rank(
                list(self._targets.values()), top_n=top_n,
                current_pan=current_pan, current_tilt=current_tilt,
            )

    @property
    def target_count(self) -> int:
        with self._lock:
            return len(self._targets)

    def get(self, target_id: str) -> Optional[InterestTarget]:
        with self._lock:
            return self._targets.get(target_id)

    def forget(self, target_id: str):
        with self._lock:
            self._targets.pop(target_id, None)

    def __repr__(self):
        with self._lock:
            targets = ", ".join(
                f"{t.target_id}({t.interest:.2f})"
                for t in sorted(self._targets.values(),
                                key=lambda x: x.interest, reverse=True)[:5]
            )
            return f"InterestEngine({len(self._targets)} targets: {targets})"
