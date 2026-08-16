#!/usr/bin/env python3
"""
PTZ WebSocket Bridge — Python controls PTZ via user's normal Chrome.

Architecture:
    Python (tracker) → WebSocket → Chrome page → EZUIKit → Camera PTZ

Start this server, then open http://localhost:8765 in Chrome.
The page initializes EZUIKit player. Python sends PTZ commands via WebSocket.

Usage:
    python scripts/ptz_server.py          # start WebSocket server
    # Open http://localhost:8765 in Chrome
    # Then use PTZBridge class from Python
"""

import asyncio, json, os, time, threading, logging
from http import HTTPStatus
import webbrowser

logger = logging.getLogger("PTZ.Server")

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8">
<style>
  body { font-family: sans-serif; margin: 0; background: #1a1a2e; color: #eee; }
  #status { padding: 10px; font-size: 14px; }
  #player { width: 640px; height: 360px; border: 2px solid #333; margin: 10px; }
  .note { font-size: 12px; color: #888; margin: 10px; }
</style></head>
<body>
<div id="status">🔌 Connecting...</div>
<div id="player"></div>
<div class="note">WebSocket PTZ Bridge — keep this page open</div>

<script src="https://openstatic.ys7.com/ezuikit_js/v9.0.5/ezuikit.js"></script>
<script>
const STATUS = document.getElementById('status');
let player = null;
let ws = null;
let playerReady = false;

function log(msg) { STATUS.textContent = msg; console.log(msg); }

const TOKEN = '__TOKEN__';
const SERIAL = '__SERIAL__';

try {
    player = new EZUIKit.EZUIKitPlayer({
        id: 'player', accessToken: TOKEN,
        url: 'ezopen://open.ys7.com/' + SERIAL + '/1.live',
        template: 'pcLive', width: 640, height: 400,
        autoplay: true, audio: false,
        staticPath: 'https://openstatic.ys7.com/ezuikit_js/v9.0.5/ezuikit_static',
    });
    player.on('ready', () => {
        playerReady = true;
        log('✅ Player ready — WebSocket connected');
        connectWS();
    });
    player.on('error', (e) => { log('❌ Player error: ' + JSON.stringify(e)); });
    log('⏳ Initializing player...');
} catch(e) { log('❌ Init error: ' + e.message); }

// WebSocket to Python
function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(proto + '://' + location.host + '/ws');
    ws.onopen = () => { log('✅ WebSocket connected — ready for PTZ'); };
    ws.onmessage = (e) => {
        const cmd = JSON.parse(e.data);
        if (cmd.action === 'move') {
            player.startMove(cmd.direction, cmd.speed || 2);
            log('→ ' + cmd.direction + ' (speed=' + (cmd.speed||2) + ')');
        } else if (cmd.action === 'stop') {
            player.stopMove();
            log('■ stop');
        }
    };
    ws.onclose = () => { log('⚠ WebSocket closed — reconnecting...'); setTimeout(connectWS, 2000); };
    ws.onerror = () => { log('⚠ WebSocket error'); };
}
</script>
</body></html>
"""


# ── WebSocket Server ──

class PTZBridge:
    """Python-side PTZ controller. Sends commands via WebSocket to browser."""

    def __init__(self, token="", serial="", port=8765):
        self._token = token
        self._serial = serial
        self._port = port
        self._ws_clients = set()
        self._server = None
        self._loop = None
        self._thread = None
        self._ready = False

    def start(self):
        """Start WebSocket server in background thread."""
        page_html = HTML_PAGE.replace('__TOKEN__', self._token).replace('__SERIAL__', self._serial)

        async def handle_http(reader, writer):
            request = (await reader.read(4096)).decode(errors='replace')
            if 'Upgrade: websocket' in request:
                await self._handle_ws(reader, writer, request)
            else:
                body = page_html.encode()
                writer.write(
                    f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                )
                await writer.drain()
                writer.close()

        async def run_server():
            self._loop = asyncio.get_event_loop()
            for port in [self._port, self._port + 1, self._port + 2]:
                try:
                    self._server = await asyncio.start_server(handle_http, '127.0.0.1', port)
                    self._port = port
                    break
                except OSError:
                    continue
            logger.info(f"PTZ Bridge: http://127.0.0.1:{self._port}")
            async with self._server:
                await self._server.serve_forever()

        self._thread = threading.Thread(target=lambda: asyncio.run(run_server()), daemon=True)
        self._thread.start()
        time.sleep(0.5)
        return True

    # ── WebSocket framing helpers ──

    @staticmethod
    def _ws_frame(payload: bytes, masked: bool = False) -> bytes:
        """Build a proper WebSocket text frame (RFC 6455)."""
        opcode = 0x81  # text frame, FIN
        length = len(payload)
        header = bytes([opcode])

        mask_bit = 0x80 if masked else 0x00
        if length < 126:
            header += bytes([mask_bit | length])
        elif length < 65536:
            header += bytes([mask_bit | 126]) + length.to_bytes(2, 'big')
        else:
            header += bytes([mask_bit | 127]) + length.to_bytes(8, 'big')

        if masked:
            import os
            mask_key = os.urandom(4)
            masked_payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            return header + mask_key + masked_payload
        return header + payload

    @staticmethod
    def _ws_unmask(frame: bytes) -> bytes:
        """Unmask a client→server WebSocket frame."""
        if len(frame) < 6:
            return frame
        mask_bit = frame[1] & 0x80
        if not mask_bit:
            return frame  # already unmasked
        payload_len = frame[1] & 0x7f
        if payload_len == 126:
            mask_start = 4
        elif payload_len == 127:
            mask_start = 10
        else:
            mask_start = 2
        mask_key = frame[mask_start:mask_start + 4]
        payload_start = mask_start + 4
        payload = frame[payload_start:]
        return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    async def _handle_ws(self, reader, writer, request):
        """WebSocket handshake + message relay."""
        import re, hashlib, base64
        key_match = re.search(r'Sec-WebSocket-Key: (.+)\r\n', request)
        if not key_match:
            writer.close(); return
        key = key_match.group(1).strip()
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()

        writer.write(
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode()
        )
        await writer.drain()

        self._ws_clients.add(writer)
        self._ready = True
        logger.info("PTZ Bridge: browser connected")

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                # Unmask and relay to all OTHER clients
                payload = self._ws_unmask(data)
                if payload:
                    frame = self._ws_frame(payload, masked=False)
                    for w in list(self._ws_clients):
                        if w is not writer:
                            try:
                                w.write(frame)
                                await w.drain()
                            except Exception:
                                self._ws_clients.discard(w)
        except Exception:
            pass
        finally:
            self._ws_clients.discard(writer)
            self._ready = bool(self._ws_clients)
            writer.close()

    def _send(self, data: dict):
        """Send PTZ command to all connected browsers (thread-safe)."""
        payload = json.dumps(data).encode()
        frame = self._ws_frame(payload, masked=False)

        async def _do_send():
            dead = set()
            for w in list(self._ws_clients):
                try:
                    w.write(frame)
                    await w.drain()
                except Exception:
                    dead.add(w)
            self._ws_clients -= dead
            if not self._ws_clients:
                self._ready = False

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(_do_send(), self._loop)

    # ── PTZ Commands (fire-and-forget) ──

    def up(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "up", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def down(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "down", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def left(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "left", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def right(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "right", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def zoom_in(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "zoomIn", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def zoom_out(self, duration=0.5, speed=2):
        self._send({"action": "move", "direction": "zoomOut", "speed": speed})
        if duration:
            threading.Timer(duration, lambda: self._send({"action": "stop"})).start()

    def stop(self):
        self._send({"action": "stop"})

    @property
    def ready(self):
        return self._ready


# ── Quick test ──
if __name__ == "__main__":
    import sys; sys.path.insert(0, '.')
    from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ptz = PTZBridge(token=EZVIZ_ACCESS_TOKEN, serial=EZVIZ_DEVICE_SERIAL)
    ptz.start()

    print("\nOpen this URL in Chrome:")
    print("  → http://localhost:8765")
    print("\nWaiting for browser to connect...")

    for i in range(30):
        if ptz.ready:
            print("Browser connected! Testing PTZ...")
            ptz.right(0.5, speed=2)
            time.sleep(1.5)
            ptz.left(0.5, speed=2)
            time.sleep(1.5)
            print("Done — check camera!")
            break
        time.sleep(1)
        if i == 5:
            print("  still waiting — open http://localhost:8765 in Chrome")
    else:
        print("Timeout — browser not connected")

    print("\nServer running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Done.")
