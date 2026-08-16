"""
Perception - L1: Camera Frame Capture
Uses local macOS camera via OpenCV
"""

import cv2
import time
import logging
from typing import Optional, Tuple
from config import CAMERA_INDEX, FRAME_WIDTH, FRAME_HEIGHT

logger = logging.getLogger("L1.Capture")


class CameraCapture:
    """MacBook camera capture at target FPS."""

    def __init__(self, camera_index: int = CAMERA_INDEX):
        self.cap = None
        self.camera_index = camera_index
        self.is_running = False
        self._last_frame_time = 0.0

    def start(self) -> bool:
        """Open camera. Returns True on success."""
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            logger.error("Failed to open camera (index %d)", self.camera_index)
            logger.error(
                "macOS: grant Terminal camera permission in System Settings > Privacy & Security > Camera"
            )
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, 30)  # request 30, we'll sample at 5

        self.is_running = True
        logger.info(
            "Camera opened: %dx%d",
            int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        return True

    def read(self) -> Optional[Tuple]:
        """Read next frame. Returns (frame_bgr, timestamp) or None."""
        if not self.is_running or self.cap is None:
            return None

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Camera read failed")
            return None

        timestamp = time.time()
        self._last_frame_time = timestamp
        return frame, timestamp

    def release(self):
        """Release camera."""
        self.is_running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        logger.info("Camera released")
