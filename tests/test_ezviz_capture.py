"""Tests for EZVIZ RTSP capture."""
import sys
sys.path.insert(0, '.')
import pytest
from runtime.perception.ezviz_capture import EZVIZCapture


class TestEZVIZCapture:
    def test_init_with_empty_url(self):
        cap = EZVIZCapture(rtsp_url="")
        assert cap.start() is False  # should fail gracefully without URL

    def test_init_with_url(self):
        cap = EZVIZCapture(rtsp_url="rtsp://admin:pwd@10.0.0.1:554/stream")
        assert "rtsp://" in cap._rtsp_url
        assert cap._width == 640
        assert cap._height == 480

    def test_is_running_defaults_false(self):
        cap = EZVIZCapture(rtsp_url="rtsp://example.com/stream")
        assert cap.is_running is False

    def test_repr(self):
        cap = EZVIZCapture(rtsp_url="rtsp://admin:pwd@10.0.0.1:554/stream")
        r = repr(cap)
        assert "EZVIZCapture" in r
        assert "rtsp://" in r
