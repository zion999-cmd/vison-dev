"""Tests for CameraState — ego motion tracking."""
import sys, time
import pytest
sys.path.insert(0, '.')
from runtime.perception.camera_state import CameraState, get_camera_state


class TestCameraState:
    def test_initial_state(self):
        cs = CameraState()
        assert cs.moving is False
        assert cs.pan == 0.0
        assert cs.tilt == 0.0
        assert cs.direction is None

    def test_move_flags(self):
        cs = CameraState()
        cs.start_move(3, speed=3)  # right
        assert cs.moving is True
        assert cs.direction == 3
        assert cs.moving_since >= 0

    def test_stop_updates_pose(self):
        """Pose advances from the caller's declared expected_delta."""
        cs = CameraState()
        cs.start_move(3, speed=3, expected_delta=30.0)  # right, 30°
        cs.stop_move()
        assert cs.moving is False
        assert cs.pan == 30.0  # moved right → pan positive
        assert cs.tilt == 0.0

    def test_elapsed_time_does_not_drive_pose(self):
        """Regression guard: wall-clock must not feed dead reckoning.

        stop_move() used to derive degrees from elapsed time while callers
        (Scanner) also advanced the pose themselves — the same movement was
        counted twice. With no expected_delta declared, the caller has told us
        nothing, so the pose must not move at all.
        """
        cs = CameraState()
        cs.start_move(3, speed=3)  # no delta declared
        time.sleep(0.05)
        cs.stop_move()
        assert cs.pan == 0.0

    def test_opposite_direction_moves_pan_back(self):
        cs = CameraState()
        cs.start_move(3, speed=3, expected_delta=30.0)  # right
        cs.stop_move()
        cs.start_move(2, speed=3, expected_delta=30.0)  # left
        cs.stop_move()
        assert cs.pan == 0.0

    def test_vertical_directions_move_tilt(self):
        cs = CameraState()
        cs.start_move(0, speed=3, expected_delta=15.0)  # up
        cs.stop_move()
        assert cs.tilt == -15.0
        cs.start_move(1, speed=3, expected_delta=15.0)  # down
        cs.stop_move()
        assert cs.tilt == 0.0

    def test_duration_fallback_scales_with_duration(self):
        """With no expected_delta, degrees come from speed × expected_duration."""
        one = CameraState()
        one.start_move(3, speed=3, expected_duration=1.0)
        one.stop_move()
        two = CameraState()
        two.start_move(3, speed=3, expected_duration=2.0)
        two.stop_move()
        assert one.pan > 0
        assert two.pan == pytest.approx(one.pan * 2)

    def test_expected_delta_takes_priority_over_duration(self):
        cs = CameraState()
        cs.start_move(3, speed=3, expected_duration=10.0, expected_delta=5.0)
        cs.stop_move()
        assert cs.pan == 5.0

    def test_settling(self):
        cs = CameraState()
        assert cs.settling is False
        cs.start_move(2, speed=3)
        cs.stop_move()
        assert cs.settling is True  # just stopped

    def test_singleton(self):
        cs1 = get_camera_state()
        cs2 = get_camera_state()
        assert cs1 is cs2

    def test_reset_pose(self):
        cs = CameraState()
        cs.start_move(3, speed=3, expected_delta=30.0)
        cs.stop_move()
        assert cs.pan > 0
        cs.reset_pose()
        assert cs.pan == 0.0
        assert cs.tilt == 0.0

    def test_reset_pose_clears_limit_flags(self):
        """Homing resets mechanical-limit tracking along with the pose."""
        cs = CameraState()
        cs.mark_limit(3)  # right
        assert cs.at_limit(3) is True
        cs.reset_pose()
        assert cs.at_limit(3) is False

    def test_last_move_ago(self):
        cs = CameraState()
        assert cs.last_move_ago > 0  # never moved
        cs.start_move(3, speed=3)
        assert cs.last_move_ago == 0.0  # currently moving
        cs.stop_move()
        assert 0 <= cs.last_move_ago < 0.5  # just stopped

    def test_repr(self):
        cs = CameraState()
        r = repr(cs)
        assert "CameraState" in r
        assert "pan" in r
