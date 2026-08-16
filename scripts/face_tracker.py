#!/usr/bin/env python3
"""
Face Tracker v5 — "nudge toward center" strategy.

No calibration. No prediction. No PID.
When face is off-center for long enough, give one TINY nudge.
Wait. Observe. Repeat only if still off-center.

This trades speed for stability — it will NEVER swing past the target.
"""

import sys, os, time, cv2
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from config import FRAME_WIDTH, FRAME_HEIGHT, EZVIZ_RTSP_URL
from runtime.perception.ezviz_capture import EZVIZCapture
from runtime.perception.face_detection import FaceDetector
from runtime.perception.ptz_control import EZVIZPTZ

# ── Very conservative tuning ──
DEAD_ZONE = 0.12            # center ±12%
NUDGE_DURATION = 0.06       # one nudge = 60ms of PTZ — barely moves
OBSERVE_TIME = 2.5          # wait 2.5s after each nudge to see result
MAX_NUDGES_PER_SIDE = 3     # never nudge more than 3x in same direction
LOST_RESET_TIME = 3.0       # if face gone >3s, reset nudge counter

capture = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
face_detector = FaceDetector()
ptz = EZVIZPTZ()

_last_nudge_time = 0
_last_direction = None
_nudge_count = 0            # consecutive nudges in same direction
_face_lost_at = None


def face_center(bbox):
    return ((bbox["x"] + bbox["width"] / 2) / FRAME_WIDTH,
            (bbox["y"] + bbox["height"] / 2) / FRAME_HEIGHT)


def nudge(direction):
    global _last_nudge_time, _last_direction, _nudge_count
    if direction == 0: ptz.up(NUDGE_DURATION, speed=1)
    elif direction == 1: ptz.down(NUDGE_DURATION, speed=1)
    elif direction == 2: ptz.left(NUDGE_DURATION, speed=1)
    elif direction == 3: ptz.right(NUDGE_DURATION, speed=1)
    _last_nudge_time = time.time()
    if direction == _last_direction:
        _nudge_count += 1
    else:
        _nudge_count = 1
    _last_direction = direction


def main():
    global _face_lost_at, _nudge_count, _last_nudge_time, _last_direction
    print("Face Tracker v5 — nudge strategy")
    if not capture.start():
        print("ERROR: camera failed"); return
    print(f"Nudge: {NUDGE_DURATION*1000:.0f}ms | Observe: {OBSERVE_TIME}s | Dead: ±{int(DEAD_ZONE*100)}%")
    print("Ready!\n")

    fc = 0
    try:
        while True:
            result = capture.read()
            if result is None: continue
            frame, ts = result; fc += 1

            faces = face_detector.detect(frame)
            largest = max(faces, key=lambda f: f["bbox"]["width"] * f["bbox"]["height"]) if faces else None

            # Track face loss
            if largest is None:
                if _face_lost_at is None:
                    _face_lost_at = time.time()
            else:
                _face_lost_at = None

            # Decision every 6 frames (~1.2s)
            if fc % 6 == 0 and largest:
                now = time.time()

                # Must wait OBSERVE_TIME since last nudge
                if now - _last_nudge_time < OBSERVE_TIME:
                    continue

                cx, cy = face_center(largest["bbox"])
                ox, oy = cx - 0.5, cy - 0.5

                # In dead zone? Great, reset nudge counter
                if abs(ox) < DEAD_ZONE and abs(oy) < DEAD_ZONE:
                    _nudge_count = 0
                    if fc % 60 == 0:
                        print(f"[{fc:4d}] face@({cx:.2f},{cy:.2f}) · centered ✓", flush=True)
                    continue

                # Too many nudges? Stop (safety)
                if _nudge_count >= MAX_NUDGES_PER_SIDE:
                    print(f"[{fc:4d}] face@({cx:.2f},{cy:.2f}) — STOPPED (max {MAX_NUDGES_PER_SIDE} nudges)"
                          f"  ⚠ face may be lost from frame!", flush=True)
                    continue

                # Reset nudge count if face was recently lost
                if _face_lost_at and now - _face_lost_at < LOST_RESET_TIME:
                    _nudge_count = 0

                # Pick direction (dominant axis only)
                if abs(ox) > abs(oy):
                    direction = 3 if ox > 0 else 2
                    sym = "→" if ox > 0 else "←"
                else:
                    direction = 1 if oy > 0 else 0
                    sym = "↓" if oy > 0 else "↑"

                print(f"[{fc:4d}] face@({cx:.2f},{cy:.2f}) off=({ox:+.2f},{oy:+.2f}) "
                      f"→ nudge {sym} #{_nudge_count+1}/{MAX_NUDGES_PER_SIDE}", flush=True)
                nudge(direction)

            # Display
            chx, chy = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
            cv2.line(frame, (chx - 50, chy), (chx + 50, chy), (0, 0, 255), 1)
            cv2.line(frame, (chx, chy - 50), (chx, chy + 50), (0, 0, 255), 1)
            dz = int(DEAD_ZONE * FRAME_WIDTH)
            cv2.rectangle(frame, (chx - dz, chy - dz), (chx + dz, chy + dz), (100, 100, 100), 1)

            # Status bar
            status = "centered" if largest and abs(face_center(largest["bbox"])[0] - 0.5) < DEAD_ZONE else \
                     "tracking" if largest else "LOST!"
            color = (0, 255, 0) if status == "centered" else (0, 255, 255) if status == "tracking" else (0, 0, 255)
            cv2.putText(frame, f"v5 | {status} | nudges:{_nudge_count}", (10, 20),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            for f in faces:
                x, y, w, h = f["bbox"]["x"], f["bbox"]["y"], f["bbox"]["width"], f["bbox"]["height"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if largest:
                x, y, w, h = largest["bbox"]["x"], largest["bbox"]["y"], largest["bbox"]["width"], largest["bbox"]["height"]
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 255), 3)

            cv2.imshow("Face Tracker v5", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        ptz.stop(); capture.release(); cv2.destroyAllWindows()
        print("Done.")


if __name__ == "__main__":
    main()
