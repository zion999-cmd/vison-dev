"""
Perception - L1: EZVIZ Camera Capture (RTSP direct, local network).

RTSP must be enabled in EZVIZ App: 我的 → 工具 → 局域网设备预览 →
扫描 → 选择设备 → 设置 → 更多设置 → 本地服务开关 → 勾选 RTSP → 保存.

Architecture:
    RTSP URL → ffmpeg subprocess → BGR24 stdout → numpy frames

No cloud API calls.  ffmpeg handles RTSP natively with TCP transport.
"""

import time, signal, logging, subprocess, re, os, select
from typing import Optional, Tuple
import numpy as np
from config import FRAME_WIDTH, FRAME_HEIGHT, PERCEPTION_FPS

logger = logging.getLogger("L1.EZVIZ")


def _find_ffmpeg():
    for c in ("ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        try:
            subprocess.run([c, "-version"], capture_output=True, timeout=5)
            return c
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


class EZVIZCapture:
    """RTSP capture from EZVIZ camera via ffmpeg subprocess.

    Interface matches CameraCapture for drop-in replacement.
    """

    def __init__(self, rtsp_url: str = "", width=FRAME_WIDTH, height=FRAME_HEIGHT, fps=PERCEPTION_FPS):
        self._rtsp_url = rtsp_url
        self._width = width
        self._height = height
        self._fps = fps
        self._frame_bytes = self._width * self._height * 3

        self._ffmpeg_path: Optional[str] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._is_running = False
        self._consecutive_failures = 0

    # ── Public API ──

    def start(self) -> bool:
        if not self._rtsp_url:
            logger.error("EZVIZ: no RTSP URL configured.")
            return False
        self._ffmpeg_path = _find_ffmpeg()
        if not self._ffmpeg_path:
            logger.error("EZVIZ: ffmpeg not found.")
            return False

        logger.info("EZVIZ: connecting RTSP (%dx%d @ %d fps)",
                     self._width, self._height, self._fps)

        if not self._start_ffmpeg():
            return False

        # Let ffmpeg buffer initial frames
        time.sleep(2)

        self._is_running = True
        self._consecutive_failures = 0
        logger.info("EZVIZ: RTSP stream opened")
        return True

    def read(self) -> Optional[Tuple[np.ndarray, float]]:
        if not self._is_running or self._ffmpeg_proc is None:
            return None

        if self._ffmpeg_proc.poll() is not None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= 5:
                logger.warning("EZVIZ: ffmpeg exited (code=%s), restarting...",
                               self._ffmpeg_proc.returncode)
                self._restart_ffmpeg()
            return None

        # Read one frame — block if pipe is empty
        try:
            raw = self._ffmpeg_proc.stdout.read(self._frame_bytes)
        except Exception:
            self._consecutive_failures += 1
            return None

        if not raw or len(raw) < self._frame_bytes:
            self._consecutive_failures += 1
            return None

        if len(raw) > self._frame_bytes:
            raw = raw[:self._frame_bytes]

        # Drain any buffered old frames — keep only the latest
        try:
            fd = self._ffmpeg_proc.stdout.fileno()
            while True:
                r, _, _ = select.select([fd], [], [], 0)
                if not r:
                    break
                chunk = self._ffmpeg_proc.stdout.read(self._frame_bytes)
                if len(chunk) >= self._frame_bytes:
                    raw = chunk[:self._frame_bytes]  # keep latest
                else:
                    break
        except Exception:
            pass

        frame = np.frombuffer(raw, dtype=np.uint8).reshape(
            (self._height, self._width, 3)).copy()
        self._consecutive_failures = 0
        return frame, time.time()

    def release(self):
        self._is_running = False
        if self._ffmpeg_proc:
            p = self._ffmpeg_proc
            self._ffmpeg_proc = None
            try:
                p.send_signal(signal.SIGTERM)
                p.wait(timeout=3)
            except Exception:
                p.kill()
        logger.info("EZVIZ: capture released")

    # ── ffmpeg ──

    def _start_ffmpeg(self) -> bool:
        vf = f"scale={self._width}:{self._height}"
        cmd = [
            self._ffmpeg_path, "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self._rtsp_url,
            "-vf", vf,
            "-r", str(self._fps),
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-c:v", "rawvideo",
            "-",
        ]
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return True
        except OSError as e:
            logger.error("EZVIZ: ffmpeg failed: %s", e)
            return False

    def _restart_ffmpeg(self):
        """Restart ffmpeg — drain old pipe to prevent frame corruption."""
        import threading
        self._consecutive_failures = 0

        def _restart():
            if self._ffmpeg_proc:
                try:
                    self._ffmpeg_proc.kill()
                except Exception:
                    pass
                # Close old stdout to discard partial frame data (prevents 花屏)
                try:
                    self._ffmpeg_proc.stdout.close()
                except Exception:
                    pass
                self._ffmpeg_proc = None
            self._restart_count = getattr(self, '_restart_count', 0) + 1
            backoff = min(5.0, 1.0 * (2 ** self._restart_count))
            time.sleep(backoff)
            if self._start_ffmpeg():
                self._restart_count = 0
                logger.info("EZVIZ: ffmpeg restarted (backoff=%.0fs)", backoff)
            else:
                logger.error("EZVIZ: ffmpeg restart failed")

        t = threading.Thread(target=_restart, daemon=True)
        t.start()

    def __repr__(self):
        return f"EZVIZCapture(rtsp={self._rtsp_url[:40]}..., running={self._is_running})"

    @property
    def is_running(self):
        return self._is_running
