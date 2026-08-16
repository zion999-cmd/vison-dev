"""
Focus - Focus Manager: persistent attention target.

Maintains "what the system is currently looking at" across frames.
Not re-deciding every frame — keeps focus with inertia and hysteresis.

Key principles:
  - Focus persistence: don't switch targets every frame
  - Inertia: maintain focus 2-3s after target disappears
  - Switch threshold: only change if new target > current * 1.5
  - Scan mode: when idle too long, simulate attention drift
  - Recent targets: short-term memory for "刚才那个人" continuity
"""

import time
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from collections import deque

logger = logging.getLogger("Focus")


@dataclass
class FocusTarget:
    target_id: str
    target_type: str          # "person", "object", "area"
    label: str = ""
    attention_score: float = 0.0
    bbox: Optional[Dict] = None
    locked_at: float = 0.0
    last_seen: float = 0.0


class FocusManager:
    """
    Persistent focus system. Maintains a current attention target
    with inertia — doesn't jump to every new stimulus.
    """

    def __init__(
        self,
        switch_ratio: float = 1.5,
        decay_rate: float = 0.95,
        lost_timeout: float = 2.5,
        idle_scan_time: float = 10.0,
    ):
        self.switch_ratio = switch_ratio       # new_score must be > current * ratio to switch
        self.decay_rate = decay_rate            # per-frame score decay when target is lost
        self.lost_timeout = lost_timeout        # seconds before giving up on lost target
        self.idle_scan_time = idle_scan_time    # seconds idle before entering scan mode

        self.current: Optional[FocusTarget] = None
        self.mode: str = "idle"                 # "idle" | "tracking" | "lost" | "scanning"
        self.recent: deque = deque(maxlen=5)

        self._last_mode: str = "idle"
        self._last_target_id: str = ""
        self._idle_since: float = time.time()

    def reset_tracking(self):
        """Clear tracking state — called when PTZ moves, so old bboxes
        don't persist at wrong positions in the new camera view."""
        self.current = None
        self.mode = "idle"
        self._last_target_id = ""
        self._idle_since = time.time()

    # ── Public API ──

    def update(
        self,
        scored_events: List[Dict],
        scene_state: Dict,
        faces: List[Dict],
        objects: List[Dict],
    ) -> Dict:
        """
        Update focus state. Called every frame.

        Returns a dict for overlay/logging:
          {mode, target_id, target_type, label, score, has_focus, changed}
        """
        now = time.time()
        changed = False

        # Find the best candidate from current frame
        candidate = self._best_candidate(scored_events, faces, objects, now)

        if self.current is None:
            # No focus — lock onto best candidate if available
            if candidate is not None and candidate.attention_score > 0.3:
                self._lock(candidate)
                changed = True
            else:
                self._check_scan(now)
        else:
            # Has focus — check if target still exists
            still_there = self._find_current_in_frame(scored_events, faces, objects)

            if still_there:
                # Target present — update score + bbox, stay locked
                self.current.last_seen = now
                self.current.attention_score = still_there["score"]
                if still_there.get("bbox"):
                    self.current.bbox = still_there["bbox"]
                self.mode = "tracking"
                self._idle_since = now
            else:
                # Target lost — decay, but don't immediately drop
                self.mode = "lost"
                elapsed = now - self.current.last_seen
                if elapsed > self.lost_timeout:
                    # Given up — maybe switch to new candidate
                    if candidate is not None and self._should_switch(candidate):
                        self._lock(candidate)
                        changed = True
                    else:
                        self._release()
                        changed = True
                        self._check_scan(now)
                else:
                    # Still hoping target comes back — decay score
                    self.current.attention_score *= self.decay_rate

            # Even if target still there, check if something much more important appeared
            if self.current is not None and candidate is not None and self._should_switch(candidate):
                self._lock(candidate)
                changed = True

        return self._snapshot(changed)

    # ── Internals ──

    def _best_candidate(self, events, faces, objects, now) -> Optional[FocusTarget]:
        """Pick the single best attention target from current frame."""
        best_score = 0.0
        best = None

        for ev in events:
            if ev["type"] in ("background_change", "background_noise",
                              "user_entered", "gaze_lost", "large_motion",
                              "voice_detected"):
                continue
            score = ev.get("score", 0)
            if score > best_score:
                bbox = None
                label = ev["type"]
                if ev["type"] in ("human_face", "gaze_started", "gaze_maintained") and faces:
                    bbox = faces[0].get("bbox")
                    label = "person"
                elif ev["type"] == "new_object" and objects:
                    bbox = objects[0].get("bbox")
                    label = objects[0].get("class_name", "object")

                target_type = "person" if ev["type"] in ("human_face", "gaze_started", "gaze_maintained") else "object"

                best_score = score
                best = FocusTarget(
                    target_id=f"{target_type}_{int(now * 1000)}",
                    target_type=target_type,
                    label=label,
                    attention_score=score,
                    bbox=bbox,
                    locked_at=now,
                    last_seen=now,
                )

        return best

    def _find_current_in_frame(self, events, faces, objects) -> Optional[Dict]:
        """Check if current focus target is still visible. Returns updated info or None."""
        if self.current is None:
            return None

        ttype = self.current.target_type
        if ttype == "person" and faces:
            for ev in events:
                if ev["type"] in ("human_face", "gaze_started", "gaze_maintained", "user_entered"):
                    return {"score": ev.get("score", self.current.attention_score),
                            "bbox": faces[0].get("bbox")}
        if ttype == "object" and objects:
            for ev in events:
                if ev["type"] == "new_object":
                    return {"score": ev.get("score", self.current.attention_score),
                            "bbox": objects[0].get("bbox")}
        # Generic: check if any event matches
        for ev in events:
            if ev["type"] != "background_change":
                # Check if this event type matches our current focus
                if ttype == "person" and ev["type"] in ("human_face", "gaze_started", "gaze_maintained"):
                    return {"score": ev["score"]}
                if ttype == "object" and ev["type"] == "new_object":
                    return {"score": ev["score"]}

        return None

    def _should_switch(self, candidate: FocusTarget) -> bool:
        """Decide whether a new candidate deserves to steal focus."""
        if self.current is None:
            return True
        # Only switch if significantly more important
        return candidate.attention_score > self.current.attention_score * self.switch_ratio

    def _lock(self, target: FocusTarget):
        """Lock onto a new target."""
        old_id = self.current.target_id if self.current else "none"
        self.current = target
        self.mode = "tracking"
        self._idle_since = target.locked_at
        logger.info("[FOCUS] %s → %s (score=%.2f, type=%s)",
                    old_id, target.target_id, target.attention_score, target.target_type)

    def _release(self):
        """Release current focus."""
        if self.current:
            self.recent.append(self.current)
            logger.info("[FOCUS] %s released → recent=%d",
                        self.current.target_id, len(self.recent))
        self.current = None
        self.mode = "idle"

    def _check_scan(self, now: float):
        """Enter scan mode if idle too long."""
        idle_dur = now - self._idle_since
        if idle_dur > self.idle_scan_time:
            self.mode = "idle"  # no forced scan — RevisitController handles exploration
        else:
            self.mode = "idle"

    def _snapshot(self, changed: bool) -> Dict:
        """Build the return dict. Track delta for logging."""
        result = {
            "mode": self.mode,
            "target_id": self.current.target_id if self.current else "",
            "target_type": self.current.target_type if self.current else "",
            "label": self.current.label if self.current else "",
            "score": round(self.current.attention_score, 2) if self.current else 0.0,
            "has_focus": self.current is not None,
            "changed": changed,
            "bbox": self.current.bbox if self.current else None,
        }

        return result
