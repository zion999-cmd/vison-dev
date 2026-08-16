#!/usr/bin/env python3
"""
EZVIZ PTZ Bridge — local PTZ control via Playwright + EZUIKit Web SDK.

Uses a headless Chromium browser to load the EZUIKit player.
PTZ commands go through the ezopen protocol (which works!) not the cloud API.

Why this works: the EZUIKit JS library uses ezopen:// WebSocket protocol
for PTZ control. The cloud API doesn't move the camera, but ezopen does.

Usage:
    from scripts.ptz_bridge import PTZBridge
    ptz = PTZBridge()
    ptz.start()
    ptz.right(0.5)   # pan right 0.5s
    ptz.up(0.3)      # tilt up 0.3s
    ptz.stop()
"""

import time, threading, logging
from playwright.sync_api import sync_playwright

logger = logging.getLogger("PTZ.Bridge")

HTML_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>body{margin:0;background:#000}</style></head>
<body>
<div id="player" style="width:640px;height:360px"></div>
<script src="https://cdn.jsdelivr.net/npm/ezuikit-js@8/ezuikit.js"></script>
<script>
let player = null;
let playerReady = false;

function initPlayer(token, serial) {
    try {
        player = new EZUIKit.EZUIKitPlayer({
            id: 'player',
            accessToken: token,
            url: 'ezopen://open.ys7.com/' + serial + '/1.hd.live',
            template: 'simple',
            width: 640, height: 360,
            autoplay: true,
            audio: false,
            staticPath: 'https://cdn.jsdelivr.net/npm/ezuikit-js@8/ezuikit_static',
        });
        player.on('ready', () => { playerReady = true; });
    } catch(e) { console.error('init error:', e); }
}

function startMove(direction, speed) {
    if (!player) return false;
    try {
        player.startMove(direction, speed);
        return true;
    } catch(e) { console.error('move error:', e); return false; }
}

function stopAllMove() {
    if (!player) return;
    try { player.stopMove(); } catch(e) {}
}
</script>
</body></html>
"""


class PTZBridge:
    """Local PTZ control via headless browser + EZUIKit Web SDK.

    Drop-in replacement for EZVIZPTZ — same interface, local protocol.
    """

    def __init__(self, token="", serial="", headless=True):
        self._token = token
        self._serial = serial
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._page = None
        self._ready = False

    def start(self):
        """Launch headless browser and load EZUIKit player."""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
        self._page = self._browser.new_page()

        # Load HTML with player
        self._page.set_content(HTML_PAGE)
        self._page.wait_for_load_state("domcontentloaded")

        # Init player
        self._page.evaluate(f"""initPlayer('{self._token}', '{self._serial}')""")

        # Wait for player ready (max 15s)
        deadline = time.time() + 15
        while time.time() < deadline:
            ready = self._page.evaluate("playerReady")
            if ready:
                self._ready = True
                logger.info("PTZ Bridge: player ready")
                return True
            time.sleep(0.5)

        logger.error("PTZ Bridge: player init timeout")
        return False

    # ── PTZ Commands ──

    def _move(self, direction: str, duration: float, speed: int = 2):
        """Start PTZ movement, auto-stop after duration."""
        if not self._ready:
            return
        self._page.evaluate(f"startMove('{direction}', {speed})")
        timer = threading.Timer(duration, self._stop_all)
        timer.daemon = True
        timer.start()

    def _stop_all(self):
        if self._ready:
            self._page.evaluate("stopAllMove()")

    def up(self, duration=0.5, speed=2):
        self._move("up", duration, speed)

    def down(self, duration=0.5, speed=2):
        self._move("down", duration, speed)

    def left(self, duration=0.5, speed=2):
        self._move("left", duration, speed)

    def right(self, duration=0.5, speed=2):
        self._move("right", duration, speed)

    def zoom_in(self, duration=0.5, speed=2):
        self._move("zoomIn", duration, speed)

    def zoom_out(self, duration=0.5, speed=2):
        self._move("zoomOut", duration, speed)

    def stop(self):
        self._stop_all()

    def close(self):
        """Shut down browser."""
        self._ready = False
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info("PTZ Bridge: closed")


# ── Quick test ──
if __name__ == "__main__":
    import sys; sys.path.insert(0, '.')
    from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print("Starting PTZ Bridge (headless browser)...")

    ptz = PTZBridge(token=EZVIZ_ACCESS_TOKEN, serial=EZVIZ_DEVICE_SERIAL)
    if not ptz.start():
        print("FAILED to start bridge")
        sys.exit(1)

    print("Testing PTZ: right 0.5s...")
    ptz.right(0.5)
    time.sleep(2)

    print("Testing PTZ: left 0.5s...")
    ptz.left(0.5)
    time.sleep(2)

    print("Done! Check if camera moved.")
    ptz.close()
