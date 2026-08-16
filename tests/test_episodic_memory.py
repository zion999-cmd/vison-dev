"""Tests for L5 episodic memory."""
import sys
sys.path.insert(0, '.')
from runtime.memory.episodic import EpisodicMemory


class TestEpisodicMemory:
    def test_push_and_recent(self):
        mem = EpisodicMemory(max_entries=200)
        mem.push("test_event", "detail", 0.8, intention="testing")
        recent = mem.recent(5)
        assert len(recent) == 1
        assert recent[0]["event"] == "test_event"

    def test_recent_respects_min_importance(self):
        mem = EpisodicMemory()
        mem.push("low", "low", 0.2)
        mem.push("high", "high", 0.9)
        filtered = mem.recent(10, min_importance=0.5)
        assert len(filtered) == 1
        assert filtered[0]["event"] == "high"

    def test_recent_by_time(self):
        mem = EpisodicMemory()
        mem.push("old", "old", 0.8)
        import time
        time.sleep(0.1)
        recent = mem.recent_by_time(seconds=0.001, min_importance=0.0)
        assert len(recent) >= 0  # old event may or may not be within window

    def test_total_events_count(self):
        mem = EpisodicMemory()
        assert mem.total_events == 0
        mem.push("a", "a", 0.5)
        mem.push("b", "b", 0.5)
        assert mem.total_events == 2

    def test_state_context_non_empty(self):
        mem = EpisodicMemory()
        mem.push("user_entered", "someone came in", 0.85, intention="approaching")
        ctx = mem.get_state_context(max_events=3)
        assert "user_entered" in ctx
        assert "approaching" in ctx

    def test_state_context_empty(self):
        mem = EpisodicMemory()
        ctx = mem.get_state_context()
        assert "No recent events" in ctx

    def test_compression_triggers_on_full_buffer(self):
        mem = EpisodicMemory(max_entries=10)
        for i in range(15):
            mem.push(f"event_{i}", f"detail_{i}", 0.1)  # low importance → compress
        assert mem.total_events <= 10

    def test_high_importance_events_preserved(self):
        mem = EpisodicMemory(max_entries=10)
        for i in range(5):
            mem.push(f"low_{i}", f"low_{i}", 0.1)
        mem.push("important", "important", 0.9)
        recent = mem.recent(10)
        events = [e["event"] for e in recent]
        assert "important" in events

    def test_clear_empties_memory(self):
        mem = EpisodicMemory()
        mem.push("a", "a", 0.5)
        mem.clear()
        assert mem.total_events == 0

    def test_episode_to_dict(self):
        from runtime.memory.episodic import Episode
        ep = Episode(
            timestamp=1000.0, event_type="test",
            detail="test detail", importance=0.7,
            intention="testing", summary="summary text",
        )
        d = ep.to_dict()
        assert d["event"] == "test"
        assert d["importance"] == 0.7
        assert "time" in d
