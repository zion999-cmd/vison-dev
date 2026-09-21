"""Tests for L2 frame differencing gatekeeper."""
import numpy as np
import pytest
import sys
sys.path.insert(0, '.')
from runtime.perception.frame_diff import FrameDiff


class TestFrameDiff:
    def test_first_frame_always_changed(self):
        diff = FrameDiff(threshold=25, min_pixels=500)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        assert diff.changed(frame)

    def test_identical_frame_not_changed(self):
        diff = FrameDiff(threshold=25, min_pixels=500)
        frame = np.full((480, 640, 3), 128, dtype=np.uint8)
        assert diff.changed(frame.copy())
        assert not diff.changed(frame.copy())

    def test_significant_change_detected(self):
        diff = FrameDiff(threshold=25, min_pixels=10)
        assert diff.changed(np.zeros((200, 200, 3), dtype=np.uint8))
        assert diff.changed(np.full((200, 200, 3), 255, dtype=np.uint8))

    def test_small_change_below_threshold(self):
        diff = FrameDiff(threshold=100, min_pixels=500)
        diff.changed(np.zeros((200, 200, 3), dtype=np.uint8))
        # frame all-0 vs frame all-10: diff=10 per channel, threshold=100 → not triggered
        assert not diff.changed(np.full((200, 200, 3), 10, dtype=np.uint8))

    def test_change_below_min_pixels(self):
        diff = FrameDiff(threshold=10, min_pixels=100000)
        diff.changed(np.zeros((200, 200, 3), dtype=np.uint8))
        # 200*200/4 = 10000 sampled pixels, < 100000 min_pixels
        assert not diff.changed(np.full((200, 200, 3), 255, dtype=np.uint8))

    def test_reset_forgets_reference(self):
        diff = FrameDiff()
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        diff.changed(frame)
        diff.reset()
        assert diff.changed(frame)

    def test_frame_geometry_change_does_not_crash(self):
        """A camera can renegotiate resolution mid-stream (4:3 → 16:9).

        `changed()` samples with a stride slice, so the cached reference must
        not be subtracted from a differently sized frame — that raised a numpy
        broadcast error which propagated out of the perception loop.
        """
        diff = FrameDiff(threshold=25, min_pixels=10)
        assert diff.changed(np.zeros((480, 640, 3), dtype=np.uint8))
        # Same width, shorter frame: sampled 180x320 against cached 240x320.
        assert diff.changed(np.zeros((360, 640, 3), dtype=np.uint8))

    def test_recovers_after_geometry_change(self):
        """Detection resumes normally on the new geometry."""
        diff = FrameDiff(threshold=25, min_pixels=10)
        diff.changed(np.zeros((480, 640, 3), dtype=np.uint8))
        diff.changed(np.zeros((360, 640, 3), dtype=np.uint8))
        assert not diff.changed(np.zeros((360, 640, 3), dtype=np.uint8))
        assert diff.changed(np.full((360, 640, 3), 255, dtype=np.uint8))

    def test_motion_level_not_stale_after_geometry_change(self):
        """The reseed frame reports no motion instead of the old geometry's."""
        diff = FrameDiff(threshold=25, min_pixels=10)
        diff.changed(np.zeros((480, 640, 3), dtype=np.uint8))
        diff.changed(np.full((480, 640, 3), 255, dtype=np.uint8))
        assert diff.motion_level > 0  # a real change was measured at 4:3
        diff.changed(np.zeros((360, 640, 3), dtype=np.uint8))
        assert diff.motion_level == 0.0
