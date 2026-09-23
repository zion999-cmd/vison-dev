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

    def test_cached_reference_does_not_alias_caller_frame(self):
        """FrameDiff must own the previous-frame observation it compares against."""
        diff = FrameDiff(threshold=25, min_pixels=10)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        diff.changed(frame)
        assert not np.shares_memory(diff._prev, frame)

    def test_caller_mutation_after_call_does_not_forge_a_change(self):
        """Caller mutation after the call must not alter the cached frame.

        Preview rendering draws onto the caller's frame in place after
        changed() has cached it. While _prev was a strided view, those
        overlays mutated the cache, so the next untouched frame looked
        changed and reported phantom motion.
        """
        diff = FrameDiff(threshold=25, min_pixels=10)
        frame_a = np.full((480, 640, 3), 128, dtype=np.uint8)
        assert diff.changed(frame_a)

        # Simulate the preview overlay drawing in place on the caller's frame.
        frame_a[100:200, 100:200] = 0

        # A fresh frame with the content changed() originally observed.
        unchanged = np.full((480, 640, 3), 128, dtype=np.uint8)
        assert not diff.changed(unchanged)
        assert diff.motion_level == 0.0

    def test_reset_clears_motion_level(self):
        """reset() forgets the observation, so its motion measurement too."""
        diff = FrameDiff(threshold=25, min_pixels=10)
        diff.changed(np.zeros((200, 200, 3), dtype=np.uint8))
        diff.changed(np.full((200, 200, 3), 255, dtype=np.uint8))
        assert diff.motion_level > 0  # a real measurement exists
        diff.reset()
        assert diff.motion_level == 0.0

    def test_observation_goes_stale_after_max_age(self):
        """A retained observation must not be believed indefinitely."""
        diff = FrameDiff(observation_max_age=2.0)
        diff.mark_observed(now=1000.0)
        assert not diff.observation_stale(now=1001.9)
        assert diff.observation_stale(now=1002.0)

    def test_fresh_observation_defers_staleness(self):
        diff = FrameDiff(observation_max_age=2.0)
        diff.mark_observed(now=1000.0)
        diff.mark_observed(now=1001.5)  # re-observed before expiry
        assert not diff.observation_stale(now=1003.0)
        assert diff.observation_stale(now=1003.5)

    def test_reset_makes_the_observation_stale(self):
        """Forgetting the observation must force a fresh one."""
        diff = FrameDiff(observation_max_age=2.0)
        diff.mark_observed(now=1000.0)
        assert not diff.observation_stale(now=1001.0)
        diff.reset()
        assert diff.observation_stale(now=1001.0)
