"""
Memory - Episodic Memory
Event timeline with automatic summarization for context continuity.
Not long-term storage — keeps a rolling window of recent events.
"""

import time
import logging
from typing import List, Dict, Optional, Callable
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("Memory")


@dataclass
class Episode:
    """A single episodic memory entry."""
    timestamp: float
    event_type: str
    detail: str
    importance: float  # 0~1
    intention: str = ""
    summary: str = ""
    state_snapshot: Dict = field(default_factory=dict)

    @property
    def age(self) -> float:
        return time.time() - self.timestamp

    def to_dict(self) -> Dict:
        return {
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "ts": self.timestamp,
            "event": self.event_type,
            "detail": self.detail,
            "importance": self.importance,
            "intention": self.intention,
            "summary": self.summary,
        }


class EpisodicMemory:
    """
    Rolling episodic memory buffer.
    Keeps recent high-importance events in detail, compresses old ones.
    """

    def __init__(self, max_entries: int = 200):
        self._episodes: deque = deque(maxlen=max_entries)
        self._max_entries = max_entries
        self._important_count = 0

    def push(
        self,
        event_type: str,
        detail: str,
        importance: float,
        intention: str = "",
        state_snapshot: Optional[Dict] = None,
        summary: str = "",
    ):
        """Record an episodic memory entry."""
        episode = Episode(
            timestamp=time.time(),
            event_type=event_type,
            detail=detail,
            importance=importance,
            intention=intention,
            summary=summary or f"{event_type}: {detail}",
            state_snapshot=state_snapshot or {},
        )
        self._episodes.append(episode)
        if importance > 0.5:
            self._important_count += 1
        logger.debug("📝 Memory: %s (imp=%.2f)", event_type, importance)

        # Auto-compress if too many entries
        if len(self._episodes) >= self._max_entries:
            self._compress()

    def recent(self, n: int = 10, min_importance: float = 0.0) -> List[Dict]:
        """Get the N most recent episodes above minimum importance."""
        result = []
        for ep in reversed(self._episodes):
            if ep.importance >= min_importance:
                result.append(ep.to_dict())
                if len(result) >= n:
                    break
        return result

    def recent_by_time(self, seconds: float = 60.0, min_importance: float = 0.0) -> List[Dict]:
        """Get episodes within the last N seconds."""
        now = time.time()
        cutoff = now - seconds
        result = []
        for ep in reversed(self._episodes):
            if ep.timestamp >= cutoff and ep.importance >= min_importance:
                result.append(ep.to_dict())
        return result

    def get_state_context(self, max_events: int = 5) -> str:
        """
        Build a compact text summary for LLM context injection.
        Returns something like:
          "Recent events: user_entered (30s ago), speaking (15s ago), ..."
        """
        now = time.time()
        recent = list(self._episodes)[-max_events:]
        if not recent:
            return "No recent events."

        parts = []
        for ep in reversed(recent):
            ago = int(now - ep.timestamp)
            if ago < 3:
                when = "just now"
            elif ago < 60:
                when = f"{ago}s ago"
            else:
                when = f"{ago // 60}m ago"
            parts.append(f"{ep.event_type} [{ep.intention}] ({when})")

        return "Recent: " + ", ".join(parts)

    @property
    def total_events(self) -> int:
        return len(self._episodes)

    def _compress(self):
        """Compress old low-importance episodes into summaries."""
        # Keep all episodes with importance > 0.5
        # Summarize contiguous blocks of low-importance ones
        kept = deque(maxlen=self._max_entries)
        low_bucket: List[Episode] = []

        for ep in self._episodes:
            if ep.importance > 0.5 or len(kept) < 20:
                # Flush low bucket before adding important
                if low_bucket:
                    self._summarize_bucket(low_bucket, kept)
                    low_bucket = []
                kept.append(ep)
            else:
                low_bucket.append(ep)

        if low_bucket:
            self._summarize_bucket(low_bucket, kept)

        self._episodes = kept
        logger.debug("Memory compressed: %d entries", len(kept))

    def _summarize_bucket(self, bucket: List[Episode], kept: deque):
        """Summarize a bucket of low-importance episodes into one entry."""
        if not bucket:
            return
        types = [e.event_type for e in bucket]
        most_common = max(set(types), key=types.count) if types else "ambient"
        summary = Episode(
            timestamp=bucket[0].timestamp,
            event_type=f"({most_common}x{len(bucket)})",
            detail=f"{len(bucket)} low-importance events",
            importance=0.1,
            summary=f"{len(bucket)} events: {', '.join(types[:5])}...",
        )
        kept.append(summary)

    def clear(self):
        """Clear all memory."""
        self._episodes.clear()
        self._important_count = 0
        logger.info("Memory cleared")
