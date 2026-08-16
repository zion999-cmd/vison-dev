"""
Environment Scanner — initial panoramic sweep to build spatial baseline.

On startup, the PTZ does a 360° horizontal sweep at multiple tilt levels.
At each position, YOLO detects objects and records them as baseline at
the corresponding SpatialAnchor.  After the scan, the system knows
"what's normally where" — and changes from this baseline drive interest.

Mimics animal behavior: enter a new space → look around → build mental map.
"""

import time, logging, threading
from typing import Callable, Optional

logger = logging.getLogger("Interest.Scanner")


class EnvironmentScanner:
    """Performs initial panoramic scan to populate AnchorManager.

    Scan pattern: 360° horizontal sweep in 60° steps (6 positions),
    at 3 tilt levels (up, center, down).  Total: 18 observation points.
    ~30 seconds total (1.5s per position).
    """

    def __init__(self, ptz_queue, camera_state, object_detector,
                 anchor_manager, frame_reader):
        self._ptz_queue = ptz_queue
        self._camera_state = camera_state
        self._object_detector = object_detector  # YOLO
        self._anchor_manager = anchor_manager
        self._get_frame = frame_reader           # () -> (frame, ts) or None

        # Scan parameters (tilt range ±60°, pan range ±340° per C6C specs)
        self.pan_steps = list(range(-120, 121, 40))  # -120,-80,-40,0,40,80,120
        self.tilt_levels = [0]                         # pan only — no tilt variation
        self.dwell_time = 4.0     # seconds per position (RTSP lag 1-3s)

        self._running = False

    def scan(self):
        """Run full environmental scan. Blocks until complete. Returns anchor count.

        Scan pattern: alternates direction per tilt row to avoid 360° wrap.
        Row 1: left→right, Row 2: right→left.
        """
        self._running = True

        total_positions = len(self.pan_steps) * len(self.tilt_levels)
        logger.info("Environment scan starting (%d pan × %d tilt = %d positions)",
                     len(self.pan_steps), len(self.tilt_levels), total_positions)

        observations = 0
        for i, tilt in enumerate(self.tilt_levels):
            # Alternate direction per row to avoid big wrap-around
            pan_sequence = self.pan_steps if i % 2 == 0 else list(reversed(self.pan_steps))
            for pan in pan_sequence:
                if not self._running:
                    break

                # Turn camera to target position
                self._move_to(pan, tilt)

                # Wait for RTSP to settle
                time.sleep(self.dwell_time)

                # Observe
                frame_data = self._get_frame()
                if frame_data is not None and frame_data[0] is not None:
                    objects = self._object_detector.detect(frame_data[0])
                    self._anchor_manager.observe(
                        objects=objects,
                        pan=self._camera_state.pan,
                        tilt=self._camera_state.tilt,
                    )
                    observations += 1
                    classes = {o.get("class_name", "?") for o in objects}
                    logger.debug("  Scan: pan=%+d° tilt=%+d° → %s",
                                 int(self._camera_state.pan),
                                 int(self._camera_state.tilt),
                                 ", ".join(sorted(classes)[:5]) if classes else "empty")

            if not self._running:
                break

        # Return to center
        self._move_to(0, 0)

        anchor_count = self._anchor_manager.anchor_count
        logger.info("Environment scan complete: %d observations, %d anchors",
                     observations, anchor_count)
        self._running = False
        return anchor_count

    def scan_async(self):
        """Start scan in background thread."""
        t = threading.Thread(target=self.scan, daemon=True)
        t.start()
        return t

    def _move_to(self, target_pan: float, target_tilt: float):
        """Turn camera toward (pan, tilt). Blocks briefly for large moves."""
        d_pan = target_pan - self._camera_state.pan
        d_tilt = target_tilt - self._camera_state.tilt

        # Horizontal: speed=2 (medium), calibrated ~40°/s actual
        # Using 15°/s conservative — ensures camera actually reaches target
        if abs(d_pan) > 10:
            direction = 3 if d_pan > 0 else 2
            duration = max(0.5, abs(d_pan) / 15.0)  # conservative: 15°/s
            self._ptz_queue.put({
                "direction": direction, "duration": duration, "speed": 2,
                "skip_reckon": True,
            })
            time.sleep(duration + 2.0)  # extra margin
            self._camera_state.pan += d_pan

        # Tilt disabled — pan-only scan

    def stop(self):
        self._running = False
