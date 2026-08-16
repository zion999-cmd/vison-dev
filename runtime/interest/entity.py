"""
Entity — the core unit of interest.  NOT a detection; NOT a region.

    Detection (YOLO frame)
        → Entity Association (visual signature match)
        → Entity Registry
        → Interest Engine

An Entity persists across frames and camera movements. Its identity is
carried by visual signature, not by pan/tilt coordinates. This is the
foundation for future mobile robots where world coordinates are unstable.

Lifecycle:
    CANDIDATE → ACTIVE → LOST → FORGOTTEN
"""

import time, uuid, math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple, List, Dict


class EntityStatus(Enum):
    CANDIDATE = "candidate"   # just detected, not yet confirmed
    ACTIVE = "active"         # confirmed entity, being tracked
    LOST = "lost"             # missing for N frames, might reappear
    FORGOTTEN = "forgotten"   # gone too long, removed from active pool


@dataclass
class Entity:
    """A persistent thing in the world — discovered through observation."""

    entity_id: str = field(default_factory=lambda: f"ent_{uuid.uuid4().hex[:8]}")
    entity_type: str = "unknown"   # "person", "object", "unknown"
    class_name: str = ""           # YOLO class: "cup", "chair", etc.

    # ── Visual identity ──
    # Lightweight signature for re-identification: (avg_h, avg_s, avg_v, bbox_w, bbox_h)
    visual_signature: Optional[Tuple[float, ...]] = None

    # ── Spatial context (NOT primary key) ──
    last_pan: float = 0.0
    last_tilt: float = 0.0
    last_bbox: Optional[Dict] = None   # {x, y, width, height}

    # ── Temporal ──
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    seen_count: int = 1
    consecutive_misses: int = 0

    # ── Detection confidence (for quality gate) ──
    avg_confidence: float = 0.0     # EMA of detection confidences across sightings

    # ── Role (innate priority) ──
    role_weight: float = 0.2     # 1.0=person, 0.1=chair — innate "should I care?"

    # ── Familiarity (session-level habituation) ──
    session_seen_count: int = 0        # sightings in current session (throttled)
    first_seen_in_session: float = field(default_factory=time.time)
    familiarity_score: float = 0.0     # 0=new, 1=completely familiar
    _last_familiarity_tick: float = 0.0  # throttle: don't count every frame

    # ── Importance (observatory, NOT value) ──
    # Counts downstream events this entity CAUSED — not how often it was seen.
    # Predictive relevance: an entity that triggers tracking/cognition/speech
    # is more important than one that just sits there.
    interaction_count: int = 0       # total downstream events caused
    event_types: set = field(default_factory=set)  # unique event types triggered
    tracking_count: int = 0          # → tracking events
    state_transition_count: int = 0  # → state changes (idle→focus etc.)
    cognition_trigger_count: int = 0 # → cognition (LLM/VLM) triggers
    speech_related_count: int = 0    # → voice/speech events

    # ── Interest (carried by entity, not by region) ──
    interest: float = 0.4         # base interest
    uncertainty: float = 0.0      # grows when not seen
    novelty: float = 0.0          # recent change magnitude

    # ── Revisit tracking ──
    revisit_count: int = 0
    confirm_count: int = 0
    consecutive_fails: int = 0
    consecutive_successes: int = 0
    max_consecutive_fails: int = 5

    # ── Lifecycle ──
    status: EntityStatus = EntityStatus.CANDIDATE

    # ── Metadata ──
    tags: List[str] = field(default_factory=list)  # informational labels

    # ── Properties ──

    @property
    def age(self) -> float:
        return time.time() - self.first_seen

    @property
    def since_last_seen(self) -> float:
        return time.time() - self.last_seen

    @property
    def is_active(self) -> bool:
        return self.status in (EntityStatus.ACTIVE, EntityStatus.CANDIDATE)

    @property
    def curiosity_score(self) -> float:
        """Entity-level curiosity — same formula, entity-scoped."""
        if self.status == EntityStatus.FORGOTTEN:
            return 0.0

        # Uncertainty: grows with time since last seen (tau=3min for entities)
        since = self.since_last_seen
        uncertainty = 1.0 - math.exp(-since / 90.0)

        # Freshness: deprioritize very recently seen
        dt = self.since_last_seen
        freshness = min(1.0, dt / 10.0) if dt < 60 else 1.0

        return max(0.0, self.interest * uncertainty * freshness)

    # ── Methods ──

    def record_interaction(self, event_type: str):
        """Record that this entity caused a downstream event.

        NOT called per-frame. Called only when the entity triggers a
        meaningful subsequent action: tracking start, state change,
        cognition trigger, speech detection, etc.

        This is the seed data for Importance Observatory (Phase 7A).
        No value formula — just observe.
        """
        self.interaction_count += 1
        self.event_types.add(event_type)
        # Sub-counters for telemetry breakdown
        if event_type == "tracking":
            self.tracking_count += 1
        elif event_type in ("state_change", "engaged", "focus"):
            self.state_transition_count += 1
        elif event_type == "cognition":
            self.cognition_trigger_count += 1
        elif event_type in ("speech", "voice"):
            self.speech_related_count += 1

    @property
    def importance_density(self) -> float:
        """interactions per sighting — predictive relevance indicator."""
        return self.interaction_count / max(self.seen_count, 1)

    @property
    def event_diversity(self) -> int:
        """How many different event types this entity triggers."""
        return len(self.event_types)

    def promote(self):
        """CANDIDATE → ACTIVE after enough confirmations."""
        if self.status == EntityStatus.CANDIDATE and self.seen_count >= 5:
            self.status = EntityStatus.ACTIVE

    def mark_seen(self):
        """Entity was observed in current frame."""
        now = time.time()
        self.last_seen = now
        self.seen_count += 1
        self.consecutive_misses = 0

        # Session familiarity: throttled to ~0.5 Hz (once per 2s).
        # Per-frame counting would make everything familiar in <2 min.
        if now - self._last_familiarity_tick >= 2.0:
            self.session_seen_count += 1
            self._last_familiarity_tick = now

        # Frequent sightings → slight interest growth (habituation-resistant)
        if self.seen_count > 10:
            self.interest = min(1.0, self.interest + 0.01)
        self.promote()

    def mark_missed(self):
        """Entity was NOT observed in current frame."""
        self.consecutive_misses += 1
        if self.consecutive_misses >= 30:
            self.status = EntityStatus.LOST
        if self.consecutive_misses >= 150:
            self.status = EntityStatus.FORGOTTEN

    def record_revisit_attempt(self):
        self.revisit_count += 1

    def record_revisit_success(self):
        self.confirm_count += 1
        self.consecutive_successes += 1
        self.consecutive_fails = 0

    def record_revisit_fail(self):
        self.consecutive_fails += 1
        self.consecutive_successes = 0
        # Auto-forget after too many consecutive revisit failures.
        # This prevents infinite re-targeting when the entity is gone.
        if self.consecutive_fails >= self.max_consecutive_fails:
            self.status = EntityStatus.FORGOTTEN
            self.interest = 0.0

    # ── Quality Gate (Phase 7B) ──

    # Thresholds (module-level overridable)
    QUALITY_MIN_SEEN: int = 3       # class attribute, not instance
    QUALITY_MIN_CONF: float = 0.5

    def update_confidence(self, detection_confidence: float):
        """EMA-update avg_confidence with each new detection."""
        alpha = 0.3
        if self.seen_count == 1:
            self.avg_confidence = detection_confidence
        else:
            self.avg_confidence = (
                alpha * detection_confidence
                + (1 - alpha) * self.avg_confidence
            )

    def is_valid_for_importance(self) -> bool:
        """Entity must pass quality gate before entering Importance.

        Conditions (Phase 7B):
            - seen_count >= QUALITY_MIN_SEEN (not a flash detection)
            - avg_confidence >= QUALITY_MIN_CONF (not a likely false positive)
            - status == ACTIVE (lifecycle gate — not CANDIDATE/LOST/FORGOTTEN)
        """
        return (
            self.seen_count >= self.QUALITY_MIN_SEEN
            and self.avg_confidence >= self.QUALITY_MIN_CONF
            and self.status == EntityStatus.ACTIVE
        )

    def __hash__(self):
        return hash(self.entity_id)

    def __repr__(self):
        return (f"Entity({self.entity_id}, {self.class_name}, "
                f"{self.status.value}, int={self.interest:.2f})")
