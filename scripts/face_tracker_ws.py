#!/usr/bin/env python3
"""
Face Tracker v6 — WebSocket PTZ Bridge.

Architecture:
  RTSP → ffmpeg → BGR24 frames → FaceDetector → offset → WebSocket → Chrome → EZUIKit → Camera

Requires: ptz_server.py running and Chrome open at http://localhost:8765
"""

import sys, os, time, cv2, json, asyncio, struct
from collections import deque
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from config import FRAME_WIDTH, FRAME_HEIGHT, EZVIZ_RTSP_URL
from runtime.perception.ezviz_capture import EZVIZCapture
from runtime.perception.face_detection import FaceDetector

# ── Tuning ──
DEAD_ZONE = 0.15            # ±15% = good enough
NUDGE_DURATION = 0.15       # seconds per nudge
OBSERVE_TIME = 1.5           # seconds between nudges
MAX_NUDGES = 4              # stop after 4 nudges in same direction
SPEED = 2                   # PTZ speed

capture = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
face_detector = FaceDetector()

_last_nudge = 0
_last_direction = None
_nudge_count = 0

history = deque(maxlen=5)


async def send_ptz(action, direction=None, speed=SPEED):
    """Send PTZ command via WebSocket to the Chrome bridge."""
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', 8765)
        # WS handshake
        key = os.urandom(16)
        import base64
        key_b64 = base64.b64encode(key).decode()
        writer.write(
            f'GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n'
            f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {key_b64}\r\n'
            f'Sec-WebSocket-Version: 13\r\n\r\n'.encode()
        )
        await writer.drain()
        resp = await reader.read(4096)
        if b'101' not in resp:
            writer.close(); return False

        # Build masked frame
        cmd = {"action": action}
        if direction:
            cmd["direction"] = direction
            cmd["speed"] = speed
        payload = json.dumps(cmd).encode()
        mask_key = os.urandom(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask_key + masked
        writer.write(frame)
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()
        return True
    except Exception:
        return False


def face_center(bbox):
    return ((bbox["x"] + bbox["width"] / 2) / FRAME_WIDTH,
            (bbox["y"] + bbox["height"] / 2) / FRAME_HEIGHT)


async def main_loop():
    global _last_nudge, _last_direction, _nudge_count

    print("Face Tracker v6 — WebSocket PTZ Bridge")
    if not capture.start():
        print("ERROR: camera failed"); return
    print("Ready!\n")

    fc = 0
    try:
        while True:
            result = capture.read()
            if result is None: continue
            frame, ts = result; fc += 1

            faces = face_detector.detect(frame)
            largest = max(faces, key=lambda f: f["bbox"]["width"] * f["bbox"]["height"]) if faces else None

            # Decision every 4 frames
            if fc % 4 == 0 and largest:
                now = time.time()
                if now - _last_nudge < OBSERVE_TIME:
                    continue

                cx, cy = face_center(largest["bbox"])
                ox, oy = cx - 0.5, cy - 0.5

                # In dead zone?
                if abs(ox) < DEAD_ZONE and abs(oy) < DEAD_ZONE:
                    _nudge_count = 0
                    if fc % 60 == 0:
                        print(f"[{fc:4d}] face@({cx:.2f},{cy:.2f}) · centered ✓")
                    continue

                # Safety stop
                if _nudge_count >= MAX_NUDGES:
                    if fc % 30 == 0:
                        print(f"[{fc:4d}] STOPPED — max nudges. face@({cx:.2f},{cy:.2f})")
                    continue

                # Pick dominant axis
                if abs(ox) > abs(oy):
                    direction = 'right' if ox > 0 else 'left'
                    sym = "→" if ox > 0 else "←"
                    # Reset counter if direction changed
                    if direction != _last_direction:
                        _nudge_count = 0
                else:
                    direction = 'down' if oy > 0 else 'up'
                    sym = "↓" if oy > 0 else "↑"
                    if direction != _last_direction:
                        _nudge_count = 0

                # Send PTZ
                print(f"[{fc:4d}] face@({cx:.2f},{cy:.2f}) off=({ox:+.2f},{oy:+.2f}) "
                      f"→ {sym} #{_nudge_count+1}/{MAX_NUDGES}")
                await send_ptz('move', direction, SPEED)
                await asyncio.sleep(NUDGE_DURATION)
                await send_ptz('stop')

                _last_nudge = time.time()
                _last_direction = direction
                _nudge_count += 1

            # Display
            chx, chy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
            cv2.line(frame, (chx - 50, chy), (chx + 50, chy), (0, 0, 255), 1)
            cv2.line(frame, (chx, chy - 50), (chx, chy + 50), (0, 0, 255), 1)
            dz = int(DEAD_ZONE * FRAME_WIDTH)
            cv2.rectangle(frame, (chx - dz, chy - dz), (chx + dz, chy + dz), (100, 100, 100), 1)

            for f in faces:
                x, y, w, h = f["bbox"]["x"], f["bbox"]["y"], f["bbox"]["width"], f["bbox"]["height"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if largest:
                x, y, w, h = largest["bbox"]["x"], largest["bbox"]["y"], largest["bbox"]["width"], largest["bbox"]["height"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 3)

            cv2.putText(frame, f"Face Tracker v6 | ws bridge | nudges:{_nudge_count}", (10, 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            cv2.imshow("Face Tracker v6", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        capture.release(); cv2.destroyAllWindows()
        print("Done.")


if __name__ == "__main__":
    import os
    asyncio.run(main_loop())
