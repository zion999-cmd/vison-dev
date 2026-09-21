"""
Perception - L2: Frame Differencing (the gatekeeper)

Pure numpy implementation — no OpenCV needed.
Cheapest possible check: "did the frame change?"

Replaces MOG2 background subtraction from motion.py.
"""

import logging
from typing import Optional

import numpy as np
from config import FRAME_DIFF_THRESHOLD, FRAME_DIFF_MIN_PIXELS

logger = logging.getLogger("L2.FrameDiff")


class FrameDiff:
    """Detect whether the frame changed since last check. Cheapest gatekeeper in L2."""

    def __init__(
        self,
        threshold: int = FRAME_DIFF_THRESHOLD,
        min_pixels: int = FRAME_DIFF_MIN_PIXELS,
    ):
        self.threshold = threshold
        self.min_pixels = min_pixels
        self._prev: Optional[np.ndarray] = None
        self._motion_level: float = 0.0

    def changed(self, frame_bgr: np.ndarray) -> bool:
        """
        Return True if the frame is significantly different from the previous.
        Also updates self.motion_level with the actual change ratio.
        """
        small = frame_bgr[::2, ::2]

        # Frames are sampled by stride, so the cached reference keeps the
        # camera's geometry. A capture device can renegotiate resolution
        # mid-stream (e.g. 4:3 → 16:9 after another app grabs the camera),
        # which made the subtraction below broadcast-fail and kill the loop.
        # Treat the new geometry as "changed" and reseed.
        if self._prev is None or self._prev.shape != small.shape:
            if self._prev is not None:
                logger.warning(
                    "Frame geometry changed %s → %s — reseeding reference frame",
                    self._prev.shape, small.shape,
                )
                # Nothing comparable to measure against, so don't keep
                # reporting the previous geometry's motion level.
                self._motion_level = 0.0
            self._prev = small
            return True

        diff = np.abs(small.astype(np.int16) - self._prev.astype(np.int16))
        motion_mask = (diff > self.threshold).any(axis=2)
        changed_pixels = np.count_nonzero(motion_mask)
        total_pixels = motion_mask.size
        self._motion_level = min(1.0, (changed_pixels / max(total_pixels, 1)) * 3)

        self._prev = small
        return changed_pixels > self.min_pixels

    @property
    def motion_level(self) -> float:
        return self._motion_level

    def reset(self) -> None:
        """Forget the reference frame (e.g., after scene change)."""
        self._prev = None
