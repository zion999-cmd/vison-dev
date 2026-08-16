"""Tests for CameraState — ego motion tracking."""
import sys, time
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
        cs = CameraState()
        cs.start_move(3, speed=3)  # right at ~400°/s
        time.sleep(0.1)
        cs.stop_move()
        assert cs.moving is False
        assert cs.pan > 0  # moved right → pan positive

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
        cs.start_move(3, speed=3)
        time.sleep(0.05)
        cs.stop_move()
        assert cs.pan > 0
        cs.reset_pose()
        assert cs.pan == 0.0

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
