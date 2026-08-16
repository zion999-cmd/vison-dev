"""Tests for L6 cognition trigger cooldown logic."""
import sys
import time
sys.path.insert(0, '.')
from runtime.cognition.trigger import CognitionTrigger, CognitionTask, _EVENT_COOLDOWNS, _DEFAULT_COOLDOWN, _COGNITION_EXCLUDED


class StubMemory:
    def get_state_context(self, max_events=5):
        return "No recent events."


class StubAPI:
    def chat(self, msg):
        return "mock response"
    def analyze_frame(self, frame, prompt):
        return "mock vision response"
    def close(self):
        pass


class TestCognitionTrigger:
    def test_first_task_accepted(self):
        trigger = CognitionTrigger(memory=StubMemory())
        trigger._text_api = StubAPI()
        trigger._vision_api = StubAPI()
        task = CognitionTask(
            event_type="human_face", event_detail="test", score=0.8,
            scene_snapshot={}, intention="looking", priority=0.8,
        )
        # Force time to be in the past
        trigger._last_trigger_time = 0.0
        trigger.push_task(task)
        # Task should be in queue
        assert not trigger._queue.empty()

    def test_global_throttle_blocks(self):
        trigger = CognitionTrigger(memory=StubMemory())
        trigger._text_api = StubAPI()
        trigger._vision_api = StubAPI()
        trigger._last_trigger_time = time.time()  # just now
        task = CognitionTask(
            event_type="human_face", event_detail="test", score=0.8,
            scene_snapshot={}, intention="looking", priority=0.8,
        )
        trigger.push_task(task)
        # Should be rejected by global throttle
        assert trigger._queue.empty()

    def test_event_cooldown_blocks_same_type(self):
        trigger = CognitionTrigger(memory=StubMemory())
        trigger._text_api = StubAPI()
        trigger._vision_api = StubAPI()
        trigger._last_trigger_time = 0.0
        # Record a recent trigger for human_face
        trigger._last_event_time["human_face"] = time.time()
        task = CognitionTask(
            event_type="human_face", event_detail="test", score=0.8,
            scene_snapshot={}, intention="looking", priority=0.8,
        )
        trigger.push_task(task)
        assert trigger._queue.empty()

    def test_different_event_types_dont_block_each_other(self):
        trigger = CognitionTrigger(memory=StubMemory())
        trigger._text_api = StubAPI()
        trigger._vision_api = StubAPI()
        trigger._last_trigger_time = 0.0
        trigger._last_event_time["human_face"] = time.time()  # face blocked
        task = CognitionTask(
            event_type="voice_detected", event_detail="test", score=0.8,
            scene_snapshot={}, intention="speaking", priority=0.9,
        )
        trigger.push_task(task)
        assert not trigger._queue.empty()

    def test_start_and_stop(self):
        trigger = CognitionTrigger(memory=StubMemory())
        trigger._text_api = StubAPI()
        trigger._vision_api = StubAPI()
        trigger.start()
        assert trigger._is_running
        trigger.stop()
        assert not trigger._is_running

    def test_event_cooldowns_have_sensible_values(self):
        assert _EVENT_COOLDOWNS["human_face"] > _EVENT_COOLDOWNS["voice_detected"]
        assert _EVENT_COOLDOWNS["gaze_started"] >= 30.0
        assert "gaze_maintained" in _COGNITION_EXCLUDED
        assert "large_motion" in _COGNITION_EXCLUDED
        assert _DEFAULT_COOLDOWN > 0
