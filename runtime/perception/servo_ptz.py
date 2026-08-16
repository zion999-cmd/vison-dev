"""
Perception - L1: SG90 Servo PTZ Controller (Arduino via Serial)

Arduino firmware accepts:
  p<angle>  — Pan to angle (0-180°, 90=center)
  t<angle>  — Tilt to angle (0-180°, 90=center)
  m0        — Center both axes
  h         — Help

Speed: smoothMove ~8ms/degree.
Arduino resets on serial connect (Duemilanove auto-reset), need 2s settle.
"""

import logging
import threading
import time
from typing import Optional

import serial

logger = logging.getLogger("L1.ServoPTZ")

# Pan angle limits (hardware can do 0-180, but keep margin).
# SG90 servos degrade when held at mechanical limits — leave 10-15° buffer.
PAN_MIN = 10
PAN_MAX = 165
PAN_CENTER = 90

# Tilt angle limits (hardware: 95-180).
# 180 is the mechanical hard stop. Holding there causes potentiometer wear,
# gear stripping, and position drift (same failure as pan "不停旋转").
# 170 gives 10° safety margin.
TILT_MIN = 95
TILT_MAX = 170
TILT_CENTER = 95  # init at min (camera faces forward)


class ServoPTZ:
    """Arduino SG90 pan/tilt controller over serial."""

    def __init__(self, port: str = "/dev/tty.usbserial-A600J5V6",
                 baud: int = 115200):
        self._port = port
        self._baud = baud
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._pan = PAN_CENTER
        self._tilt = TILT_CENTER
        self._queue = []  # pending (pan, tilt) tuples
        self._moving = False
        self._worker: Optional[threading.Thread] = None
        self._running = False

    # ── Public API ──

    @property
    def pan(self) -> int:
        with self._lock:
            return self._pan

    @property
    def tilt(self) -> int:
        with self._lock:
            return self._tilt

    @property
    def moving(self) -> bool:
        return self._moving or len(self._queue) > 0

    def start(self) -> bool:
        """Open serial connection and start worker thread.

        Waits for Arduino ready message to ensure bootloader is done.
        If first command arrives during bootloader phase, it's consumed
        as programming data, causing position drift (tracked vs actual).
        """
        try:
            self._serial = serial.Serial(self._port, self._baud, timeout=2)
            # Wait for Arduino to finish auto-reset and bootloader (~2s)
            # then read the "云台就绪" ready message
            deadline = time.time() + 5.0
            while time.time() < deadline:
                line = self._serial.readline().decode("utf-8", errors="replace")
                if "就绪" in line:
                    break
                time.sleep(0.1)
            self._serial.reset_input_buffer()  # discard remaining help text
            # Immediately drive servos to safe position after auto-reset.
            # During bootloader, servos may twitch to arbitrary angles without
            # a valid PWM signal. This ensures they're at known-safe positions
            # before any higher-level commands arrive.
            # Use synchronous writes (not queued) to guarantee ordering.
            self._serial.write(f"p{PAN_CENTER}\n".encode())
            self._serial.flush()
            time.sleep(0.5)  # let Arduino process pan before tilt
            self._serial.write(f"t{TILT_CENTER}\n".encode())
            self._serial.flush()
            self._running = True
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
            logger.info("ServoPTZ: connected to %s", self._port)
            return True
        except Exception as e:
            logger.error("ServoPTZ: failed to open %s: %s", self._port, e)
            return False

    def stop(self):
        """Close serial connection."""
        self._running = False
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        logger.info("ServoPTZ: stopped")

    def pan_to(self, angle: int):
        """Queue pan to absolute angle (0-180). Clamped to safe range."""
        angle = max(PAN_MIN, min(PAN_MAX, int(angle)))
        self._queue.append(("pan", angle))

    def tilt_to(self, angle: int):
        """Queue tilt to absolute angle (0-180). Clamped to safe range."""
        angle = max(TILT_MIN, min(TILT_MAX, int(angle)))
        self._queue.append(("tilt", angle))

    def center(self):
        """Queue return to center."""
        self._queue.append(("pan", PAN_CENTER))
        self._queue.append(("tilt", TILT_CENTER))

    def pan_relative(self, delta: int):
        """Queue relative pan move. Positive=right, negative=left."""
        with self._lock:
            target = self._pan + delta
        self.pan_to(target)

    # ── Internal ──

    def _worker_loop(self):
        """Process queued commands sequentially."""
        while self._running:
            if not self._queue:
                time.sleep(0.05)
                continue
            cmd, value = self._queue.pop(0)
            self._moving = True
            try:
                if cmd == "pan":
                    actual = self._command(f"p{value}\n", 'Pan')
                    if actual is not None:
                        with self._lock:
                            self._pan = actual
                elif cmd == "tilt":
                    actual = self._command(f"t{value}\n", 'Tilt')
                    if actual is not None:
                        with self._lock:
                            self._tilt = actual
            except Exception as e:
                logger.error("ServoPTZ: command failed: %s", e)
            self._moving = False

    def _command(self, data: str, expected_prefix: str) -> Optional[int]:
        """Send command, read Arduino response to get actual position.

        Arduino responds e.g. 'Pan → 120\r\n' after smoothMove completes.
        Read this to get ground-truth position instead of guessing.
        """
        with self._lock:
            if not self._serial or not self._serial.is_open:
                return None
            self._serial.reset_input_buffer()  # discard any stale data
            self._serial.write(data.encode())
            self._serial.flush()
        # Wait for Arduino's smoothMove to finish (8ms/deg + margin)
        # Parse angle from response like "Pan → 120"
        deadline = time.time() + 5.0
        while time.time() < deadline:
            with self._lock:
                if self._serial.in_waiting:
                    line = self._serial.readline().decode("utf-8", errors="replace")
                    for part in line.split():
                        try:
                            angle = int(part)
                            logger.info("PTZ %s: %d (from '%s')", expected_prefix.lower(), angle, line.strip())
                            return angle
                        except ValueError:
                            continue
            time.sleep(0.02)
        logger.warning("ServoPTZ: no response for %s", data.strip())
        return None
