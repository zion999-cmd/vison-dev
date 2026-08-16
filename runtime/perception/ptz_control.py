"""
EZVIZ PTZ (Pan-Tilt-Zoom) Control via Open Platform API.

Uses cloud API for control commands while video streams locally via RTSP.
PTZ commands are lightweight HTTP calls — no impact on video latency.

Usage:
    from runtime.perception.ptz_control import EZVIZPTZ
    ptz = EZVIZPTZ()
    ptz.up(0.5)          # pan up for 0.5s
    ptz.left(1.0)        # pan left for 1s
    ptz.zoom_in(0.3)     # zoom in for 0.3s
"""

import time, logging, threading
import requests
from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

logger = logging.getLogger("PTZ")

_API_BASE = "https://open.ys7.com/api/lapp/device/ptz"

# Direction constants
UP, DOWN, LEFT, RIGHT = 0, 1, 2, 3
ZOOM_IN, ZOOM_OUT = 8, 9


class EZVIZPTZ:
    """Simple PTZ controller for EZVIZ cameras."""

    def __init__(self, token=EZVIZ_ACCESS_TOKEN, serial=EZVIZ_DEVICE_SERIAL,
                 channel=1, default_speed=2):
        self._token = token
        self._serial = serial
        self._channel = channel
        self._speed = default_speed

    # ── Core ──

    def _start(self, direction: int, speed: int = None):
        if speed is None:
            speed = self._speed
        try:
            r = requests.post(f"{_API_BASE}/start", data={
                "accessToken": self._token, "deviceSerial": self._serial,
                "channelNo": self._channel, "direction": direction,
                "speed": speed,
            }, timeout=5)
            return r.json().get("code") == "200"
        except Exception:
            return False

    def _stop(self, direction: int = None):
        data = {"accessToken": self._token, "deviceSerial": self._serial,
                "channelNo": self._channel}
        if direction is not None:
            data["direction"] = direction
        try:
            r = requests.post(f"{_API_BASE}/stop", data=data, timeout=5)
            return r.json().get("code") == "200"
        except Exception:
            return False

    def _move(self, direction: int, duration: float, speed: int = None):
        """Move for `duration` seconds then auto-stop (uses thread timer)."""
        self._start(direction, speed)
        timer = threading.Timer(duration, self._stop, args=[direction])
        timer.daemon = True
        timer.start()

    # ── Convenience ──

    def up(self, duration=0.5, speed=None):
        self._move(UP, duration, speed)

    def down(self, duration=0.5, speed=None):
        self._move(DOWN, duration, speed)

    def left(self, duration=0.5, speed=None):
        self._move(LEFT, duration, speed)

    def right(self, duration=0.5, speed=None):
        self._move(RIGHT, duration, speed)

    def zoom_in(self, duration=0.5, speed=None):
        self._move(ZOOM_IN, duration, speed)

    def zoom_out(self, duration=0.5, speed=None):
        self._move(ZOOM_OUT, duration, speed)

    def stop(self):
        self._stop()
