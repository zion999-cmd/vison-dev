"""
Cognition - L6: Trigger Layer
Sparse LLM/VLM calls when attention threshold + intention justify it.
Runs in a separate thread to avoid blocking perception loop.
"""

import time
import logging
import threading
from queue import Queue
from typing import Optional, Dict

from config import ATTENTION_THRESHOLD
from runtime.utils.vision_api import TextAPI, VisionAPI

logger = logging.getLogger("L6.Cognition")

# Per-event-type cooldown (seconds).
# Vision events (VLM) get longer cooldowns.
# Events NOT listed here never trigger cognition (attention reinforcement only).
_EVENT_COOLDOWNS = {
    "human_face": 15.0,         # VLM
    "gaze_started": 60.0,       # VLM — "user started looking" is rare & important
    "new_object": 15.0,         # VLM
    "user_entered": 30.0,       # VLM — "someone appeared" worth a look
    "voice_detected": 10.0,     # LLM — speaking is meaningful
    "background_change": 60.0,
}
# Events that never trigger cognition (attention reinforcement only)
_COGNITION_EXCLUDED = {"gaze_maintained", "gaze_lost", "large_motion", "background_noise"}
_DEFAULT_COOLDOWN = 30.0


class CognitionTask:
    """A task for the cognition thread."""
    def __init__(
        self,
        event_type: str,
        event_detail: str,
        score: float,
        scene_snapshot: Dict,
        intention: str = "",
        priority: float = 0.5,
        frame: Optional = None,
        timestamp: Optional[float] = None,
    ):
        self.event_type = event_type
        self.event_detail = event_detail
        self.score = score
        self.intention = intention
        self.priority = priority
        self.scene_snapshot = scene_snapshot
        self.frame = frame
        self.timestamp = timestamp or time.time()


class CognitionTrigger:
    """Sparse cognition trigger with async processing."""

    def __init__(self, memory=None):
        self._queue: Queue = Queue(maxsize=10)
        self._text_api = TextAPI()
        self._vision_api = VisionAPI()
        self._memory = memory
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._last_trigger_time = 0.0
        self._min_interval = 3.0  # minimum seconds between any two triggers
        self._last_event_time: Dict[str, float] = {}  # per-event-type cooldown
        self._last_scene_fingerprint: str = ""        # cognitive importance gate
        self._same_context_count: int = 0

    def start(self):
        """Start the cognition worker thread."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="cognition")
        self._worker_thread.start()
        logger.info("Cognition thread started")

    def stop(self):
        """Stop the cognition worker."""
        self._is_running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=3)
        self._text_api.close()
        self._vision_api.close()
        logger.info("Cognition thread stopped")

    def push_task(self, task: CognitionTask):
        """Push a cognition task (non-blocking). Respects per-event cooldowns and queue dedup."""
        now = time.time()

        # Exclude non-cognitive events
        if task.event_type in _COGNITION_EXCLUDED:
            return

        # Global throttle
        if now - self._last_trigger_time < self._min_interval:
            logger.debug("Trigger throttled (global, %.1fs since last)", now - self._last_trigger_time)
            return

        # Per-event-type throttle
        cooldown = _EVENT_COOLDOWNS.get(task.event_type, _DEFAULT_COOLDOWN)
        last = self._last_event_time.get(task.event_type, 0.0)
        if now - last < cooldown:
            logger.debug("Trigger throttled (%s, %.1fs since last, cooldown=%.0fs)",
                         task.event_type, now - last, cooldown)
            return

        # Cognitive Importance Gate: suppress if context unchanged
        fingerprint = self._scene_fingerprint(task)
        if fingerprint == self._last_scene_fingerprint:
            self._same_context_count += 1
            if self._same_context_count >= 2:
                logger.debug("Trigger suppressed (same context x%d)", self._same_context_count)
                return
        else:
            self._same_context_count = 0
            self._last_scene_fingerprint = fingerprint

        # Queue dedup: skip if same event_type already queued
        if any(t.event_type == task.event_type for t in list(self._queue.queue)):
            logger.debug("Trigger skipped (%s already queued)", task.event_type)
            return

        try:
            self._queue.put_nowait(task)
            logger.info(
                ">>> COGNITION: %s (intent=%s, score=%.2f, pri=%.2f)",
                task.event_type, task.intention, task.score, task.priority
            )
            self._last_trigger_time = now
            self._last_event_time[task.event_type] = now
        except Exception:
            logger.warning("Cognition queue full, dropping task")

    def _scene_fingerprint(self, task: CognitionTask) -> str:
        """Scene fingerprint — fuzzy enough to group similar frames."""
        s = task.scene_snapshot
        # Round motion to 0.2 granularity, only track presence (not count) for faces/objects
        return (
            f"p{1 if s.get('people') else 0}"
            f"o{1 if s.get('objects') else 0}"
            f"m{round(s.get('motion_level',0) * 5)}"  # 0.2 steps
            f"v{1 if s.get('voice_activity') else 0}"
        )

    def _worker_loop(self):
        """Background worker that processes cognition tasks."""
        while self._is_running:
            try:
                task = self._queue.get(timeout=1.0)
                self._process_task(task)
            except Exception:
                continue

    def _process_task(self, task: CognitionTask):
        """Process a single cognition task with LLM/VLM."""
        logger.info("━" * 55)
        logger.info("Processing cognition task: %s", task.event_type)
        logger.info("Intention: %s | Score: %.2f | Priority: %.2f",
                    task.intention, task.score, task.priority)

        # Build context from memory
        memory_context = ""
        if self._memory:
            memory_context = self._memory.get_state_context(max_events=5)
            logger.info("Memory context: %s", memory_context)

        # Decide: vision analysis or text only?
        needs_vision = task.event_type in ("human_face", "new_object", "gaze_started", "user_entered") and task.frame is not None
        result = None

        if needs_vision:
            logger.info("→ Calling VLM (vision analysis)...")
            prompt = (
                f"You are a smart home camera. "
                f"Event: {task.event_type} ({task.event_detail}). "
                f"User intention: {task.intention}. "
                f"Runtime state: {task.scene_snapshot.get('runtime_state', '?')}. "
                f"{memory_context} "
                "Describe what you see in this image in Chinese. "
                "Be concise: who is there, what are they doing, any notable objects."
            )
            result = self._vision_api.analyze_frame(task.frame, prompt=prompt)
            if result:
                logger.info("VLM Result: %s", result)

        if not result:
            logger.info("→ Calling LLM (text analysis)...")
            prompt = (
                f"You are a smart home monitor analyzing an event. "
                f"Event: {task.event_type} ({task.event_detail}). "
                f"User intention: {task.intention}. "
                f"Runtime state: {task.scene_snapshot.get('runtime_state', '?')}. "
                f"Scene: people={len(task.scene_snapshot.get('people',[]))}, "
                f"motion={task.scene_snapshot.get('motion_level',0):.2f}. "
                f"{memory_context} "
                "Provide a brief analysis in Chinese of what might be happening."
            )
            result = self._text_api.chat(prompt)
            if result:
                logger.info("LLM Result: %s", result)

        logger.info("━" * 55)
