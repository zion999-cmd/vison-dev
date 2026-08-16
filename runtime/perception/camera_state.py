"""
CameraState — shared state for PTZ-aware perception.

When the camera moves (ego motion), the entire scene shifts.
All perception modules must know this to avoid "motion pollution":
- Motion detection: pause during camera movement
- Attention: don't confuse camera pan with world change
- Spatial memory: track approximate pan/tilt for future mapping

Singleton — one instance shared across the runtime.
"""

import time, threading, logging
from typing import Optional

logger = logging.getLogger("CameraState")


# Approximate calibration: degrees per second at given speed
# Measured: speed=3 → ~500px in 1000ms at 640px ≈ 80° FOV
# → ~400°/s at speed=3, ~200°/s at speed=1, ~600°/s at speed=5
# EZVIZ speed: 1=slow, 2=medium, 3=fast (per official API docs).
# Degrees/sec calibrated from measurement (mostly used as fallback).
_DEG_PER_SEC = {1: 20, 2: 40, 3: 70}
# Direction → (pan_delta, tilt_delta) in degrees per second
_DIR_DELTA = {
    0: (0, -1),   # up
    1: (0, +1),   # down
    2: (-1, 0),   # left
    3: (+1, 0),   # right
}


class CameraState:
    """Tracks approximate camera pose and movement state.

    Pan/tilt are dead-reckoned from PTZ commands (no position sensor).
    Accuracy degrades over time but is sufficient for:
    - Knowing WHETHER the camera is moving (ego motion flag)
    - Roughly WHICH direction (for attention compensation)
    - Approximate WHERE it's looking (for spatial memory)
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Pose (dead-reckoned, degrees, 0=home)
        self.pan = 0.0
        self.tilt = 0.0

        # Movement state
        self._moving = False
        self._move_started = 0.0
        self._last_stop = 0.0
        self._current_direction: Optional[int] = None
        self._move_speed = 0

        # Mechanical limit tracking (set when API returns limit message)
        self._tilt_up_hit = False
        self._tilt_down_hit = False
        self._pan_left_hit = False
        self._pan_right_hit = False

        # Direction → limit flag mapping
        self._limit_map = {0: "_tilt_up_hit", 1: "_tilt_down_hit",
                           2: "_pan_left_hit", 3: "_pan_right_hit"}

    # ── Public properties (thread-safe) ──

    @property
    def moving(self) -> bool:
        with self._lock:
            if not self._moving:
                return False
            # Safety timeout: if moving > 30s, the PTZ worker likely crashed.
            # Force-reset to prevent RevisitController from deadlocking.
            if time.time() - self._move_started > 30.0:
                self._moving = False
                self._last_stop = time.time()
                logger.warning("CameraState: force-cleared stuck moving flag (%.0fs)",
                               time.time() - self._move_started)
                return False
            return True

    @property
    def moving_since(self) -> float:
        """Seconds since movement started (0 if not moving)."""
        with self._lock:
            if not self._moving:
                return 0.0
            return time.time() - self._move_started

    @property
    def last_move_ago(self) -> float:
        """Seconds since last movement stopped."""
        with self._lock:
            if self._moving:
                return 0.0
            if self._last_stop == 0.0:
                return float("inf")
            return time.time() - self._last_stop

    @property
    def settling(self) -> bool:
        """True if camera stopped recently (RTSP buffer still flushing)."""
        return not self.moving and self.last_move_ago < 2.5

    @property
    def direction(self) -> Optional[int]:
        with self._lock:
            return self._current_direction

    # ── Commands (called by PTZ controller) ──

    def start_move(self, direction: int, speed: int = 3,
                   expected_duration: float = 0.0, expected_delta: float = 0.0):
        """Record that a PTZ movement started.

        Args:
            direction: 0=up, 1=down, 2=left, 3=right
            speed: 1-7
            expected_duration: how long the move will last (seconds).
            expected_delta: expected degrees of movement (takes priority
                over duration-based computation — avoids calibration mismatch).
        """
        with self._lock:
            self._moving = True
            self._move_started = time.time()
            self._current_direction = direction
            self._move_speed = speed
            self._expected_duration = expected_duration
            self._expected_delta = expected_delta

    def stop_move(self):
        """Record that PTZ movement stopped. Updates dead-reckoned pose."""
        with self._lock:
            if self._moving:
                delta = _DIR_DELTA.get(self._current_direction, (0, 0))
                if self._expected_delta:
                    # Explicit delta from caller (e.g. Scanner) — no calibration drift
                    self.pan += delta[0] * abs(self._expected_delta)
                    self.tilt += delta[1] * abs(self._expected_delta)
                else:
                    # Fallback: compute from duration (e.g. RevisitController)
                    deg_per_sec = _DEG_PER_SEC.get(self._move_speed, 400)
                    degrees = deg_per_sec * self._expected_duration
                    self.pan += delta[0] * degrees
                    self.tilt += delta[1] * degrees

            self._moving = False
            self._last_stop = time.time()
            self._current_direction = None
            self._move_speed = 0

    def mark_limit(self, direction: int):
        """Record that the camera hit a mechanical limit in this direction."""
        attr = self._limit_map.get(direction)
        if attr:
            with self._lock:
                setattr(self, attr, True)

    def clear_limit(self, direction: int):
        """Clear limit flag when moving away from it."""
        attr = self._limit_map.get(direction)
        if attr:
            with self._lock:
                setattr(self, attr, False)

    def at_limit(self, direction: int) -> bool:
        """Check if camera is at the mechanical limit for this direction."""
        attr = self._limit_map.get(direction)
        if attr:
            with self._lock:
                return getattr(self, attr)
        return False

    def reset_pose(self):
        """Reset dead-reckoned pose to zero (e.g., after homing)."""
        with self._lock:
            self.pan = 0.0
            self.tilt = 0.0
            # Reset limit tracking on homing
            self._tilt_up_hit = False
            self._tilt_down_hit = False
            self._pan_left_hit = False
            self._pan_right_hit = False

    def __repr__(self):
        with self._lock:
            return (f"CameraState(pan={self.pan:.0f}°, tilt={self.tilt:.0f}°, "
                    f"moving={self._moving}, dir={self._current_direction})")


# Module-level singleton
_state = CameraState()


def get_camera_state() -> CameraState:
    """Get the shared CameraState singleton."""
    return _state
