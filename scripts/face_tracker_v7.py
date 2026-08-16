#!/usr/bin/env python3
"""
Face Tracker v7 — Optimized latency.

- Frame reader thread: aggressively drains ffmpeg, keeps only latest frame
- Main thread: always processes the freshest frame
- PTZ thread: parallel stop, session reuse
"""

import sys, os, time, cv2, requests, threading, queue
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from config import FRAME_WIDTH, FRAME_HEIGHT, EZVIZ_RTSP_URL, EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL
from runtime.perception.ezviz_capture import EZVIZCapture
from runtime.perception.object_detection import ObjectDetector
from runtime.perception.camera_state import get_camera_state

TOKEN = EZVIZ_ACCESS_TOKEN
SERIAL = EZVIZ_DEVICE_SERIAL
API = "https://open.ys7.com/api/lapp/device/ptz"

# ── Parameters ──
DEAD_ZONE = 0.12
OBSERVE_TIME = 5.0          # long settle — prevents oscillation
MAX_NUDGES = 1              # ONE nudge, then must wait for next OBSERVE
REVERSE_LOCKOUT = 6.0       # no reversal for 6s
SPEED = 3
SEARCH_SPEED = 5
SEARCH_PAUSE = 2.0
DIRS = {0: "↑", 1: "↓", 2: "←", 3: "→"}

capture = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
object_detector = ObjectDetector()

# ── Latest frame (thread-safe) ──
_latest_frame = None
_latest_ts = 0.0
_frame_lock = threading.Lock()

def frame_reader():
    """Aggressively drain ffmpeg, keep only latest frame for main thread."""
    global _latest_frame, _latest_ts
    read_count = 0; t0 = time.time()
    while not _stop_event.is_set():
        r = capture.read()
        if r is not None:
            with _frame_lock:
                _latest_frame = r[0]
                _latest_ts = r[1]
            read_count += 1
            # Every 5 min
            if read_count % 1500 == 0:
                elapsed = time.time() - t0
                fps = read_count / elapsed if elapsed > 0 else 0
                print(f"  [reader] {fps:.1f} fps, {read_count} frames", flush=True)
                t0 = time.time(); read_count = 0

def get_latest_frame():
    with _frame_lock:
        return _latest_frame, _latest_ts


# ── PTZ Thread ──
_cmd_queue = queue.Queue()
_stop_event = threading.Event()

def ptz_worker():
    session = requests.Session()
    session.headers.update({"Connection": "keep-alive"})
    cs = get_camera_state()
    while not _stop_event.is_set():
        try:
            cmd = _cmd_queue.get(timeout=1)
        except queue.Empty:
            continue
        direction, duration, speed = cmd["direction"], cmd["duration"], cmd.get("speed", SPEED)
        cs.start_move(direction, speed)
        try:
            session.post(f"{API}/start", data={
                "accessToken": TOKEN, "deviceSerial": SERIAL,
                "channelNo": 1, "direction": direction, "speed": speed,
            }, timeout=8)
        except Exception as e:
            print(f"  ⚠ PTZ start error: {e}", flush=True)
            cs.stop_move()
            continue
        time.sleep(duration)
        # Parallel stop with error suppression
        threads = []
        for d in range(4):
            def _stop(dd=d):
                try:
                    session.post(f"{API}/stop", data={
                        "accessToken": TOKEN, "deviceSerial": SERIAL,
                        "channelNo": 1, "direction": dd,
                    }, timeout=8)
                except Exception:
                    pass
            t = threading.Thread(target=_stop, daemon=True)
            t.start(); threads.append(t)
        for t in threads: t.join(timeout=5)
        cs.stop_move()

def queue_ptz(direction, duration, speed=SPEED):
    _cmd_queue.put({"direction": direction, "duration": duration, "speed": speed})


# ── Search Strategies ──
class SearchStrategy:
    def __init__(self):
        self._stage = 0; self._stage_count = 0
        self._last_cx = 0.5; self._last_cy = 0.5
        self._spiral_radius = 0; self._zigzag_row = 0; self._zigzag_dir = 0

    def reset(self, cx, cy):
        self._stage = 0; self._stage_count = 0
        self._last_cx = cx; self._last_cy = cy
        self._spiral_radius = 0; self._zigzag_row = 0

    def next(self):
        self._stage_count += 1
        if self._stage == 0 and self._stage_count > 3: self._stage = 1; self._stage_count = 0
        elif self._stage == 1 and self._stage_count > 5: self._stage = 2; self._stage_count = 0
        elif self._stage == 2 and self._stage_count > 6: self._stage = 3; self._stage_count = 0

        dur = 0.3 + self._stage * 0.2; dur = min(dur, 1.5)
        if self._stage == 0:
            direction = 3 if self._last_cx < 0.5 else 2
            label = f"🔮 predict {DIRS[direction]}"
        elif self._stage == 1:
            self._spiral_radius = min(1.5, self._spiral_radius + 0.2)
            direction = [3, 1, 2, 0][self._stage_count % 4]
            label = f"🌀 spiral {DIRS[direction]}"
        elif self._stage == 2:
            if self._stage_count % 3 == 0:
                self._zigzag_dir = 2 if self._zigzag_dir == 3 else 3
                self._zigzag_row += 1
            direction = 1 if self._stage_count % 3 == 0 else self._zigzag_dir
            label = f"⚡ zigzag {DIRS[direction]}"
        else:
            # Full coverage: left sweep → right sweep → tilt down → tilt up → repeat
            cycle = self._stage_count % 6
            if cycle < 2:
                direction, dur = 2, 2.5  # left sweep ~500°
            elif cycle < 4:
                direction, dur = 3, 2.5  # right sweep
            elif cycle == 4:
                direction, dur = 1, 1.0  # tilt down
            else:
                direction, dur = 0, 1.0  # tilt up
            label = f"🌐 panorama {DIRS[direction]} {int(dur*1000)}ms"
        return direction, dur, label


# ── Main ──
def face_center(bbox):
    return ((bbox["x"] + bbox["width"] / 2) / FRAME_WIDTH,
            (bbox["y"] + bbox["height"] / 2) / FRAME_HEIGHT)


def main():
    print("Face Tracker v7 — Low Latency")
    if not capture.start(): print("ERROR"); return

    # Start threads
    reader = threading.Thread(target=frame_reader, daemon=True); reader.start()
    ptz_thread = threading.Thread(target=ptz_worker, daemon=True); ptz_thread.start()

    # Wait for first frame
    for _ in range(30):
        if get_latest_frame()[0] is not None: break
        time.sleep(0.1)

    # Stop any lingering PTZ
    for d in range(4):
        try:
            requests.post(f"{API}/stop", data={
                "accessToken": TOKEN, "deviceSerial": SERIAL, "channelNo": 1, "direction": d,
            }, timeout=3)
        except: pass

    search = SearchStrategy()
    print(f"Observe: {OBSERVE_TIME}s | Dead: ±{int(DEAD_ZONE*100)}% | Calibrated nudge")
    print("Strategies: Predict → Spiral → Zigzag → Panorama")
    print("Ready!\n")

    fc = 0
    last_decision = 0; last_dir = None; nudge_count = 0
    face_lost_at = None; last_face_pos = (0.5, 0.5)
    lost_streak = 0; found_streak = 0
    diag_t0 = time.time(); diag_fc = 0  # diagnostic: FPS counter

    try:
        while True:
            r = get_latest_frame()
            if r[0] is None: time.sleep(0.01); continue
            frame, ts = r; fc += 1
            now = time.time()
            diag_fc += 1

            # Every 5 min, log frame rate and latency
            if fc % 1500 == 0:
                elapsed = now - diag_t0
                fps = diag_fc / elapsed if elapsed > 0 else 0
                lag = now - ts
                qsize = _cmd_queue.qsize()
                print(f"[{fc:4d}] DIAG: {fps:.1f} fps, lag={lag:.1f}s, nudge={nudge_count}, ptz_q={qsize}", flush=True)
                if qsize > 5:
                    print(f"  ⚠ PTZ queue growing ({qsize}) — worker may be stuck!", flush=True)
                diag_t0 = now; diag_fc = 0

            detections = object_detector.detect(frame)
            persons = [d for d in detections if d.get("class_name") == "person"]
            largest = max(persons, key=lambda d: d["bbox"]["width"] * d["bbox"]["height"]) if persons else None

            status = ""; sym = "·"

            if largest is None:
                lost_streak += 1; found_streak = 0
                if lost_streak >= 5 and face_lost_at is None:
                    face_lost_at = now
                    search.reset(*last_face_pos)
                    last_decision = 0  # allow search immediately
                    print(f"[{fc:4d}] LOST")
                elif face_lost_at is not None and now - face_lost_at > 1.5 and now - last_decision > SEARCH_PAUSE:
                    direction, duration, label = search.next()
                    print(f"[{fc:4d}] {label} ({duration*1000:.0f}ms)")
                    queue_ptz(direction, duration, SEARCH_SPEED)
                    last_decision = now; last_dir = direction
                status = f"SEARCH {['PREDICT','SPIRAL','ZIGZAG','PANORAMA'][search._stage]}"
            else:
                found_streak += 1; lost_streak = 0
                was_lost = face_lost_at is not None
                if found_streak >= 5:  # need 5 consecutive frames
                    if face_lost_at is not None:
                        face_lost_at = None
                        last_decision = 0; nudge_count = 0; last_dir = None
                        print(f"[{fc:4d}] PERSON FOUND")
                else:
                    continue  # not confirmed yet — skip tracking

                cx, cy = face_center(largest["bbox"])
                last_face_pos = (cx, cy)
                ox, oy = cx - 0.5, cy - 0.5

                if now - last_decision >= OBSERVE_TIME:
                    if abs(ox) < DEAD_ZONE and abs(oy) < DEAD_ZONE:
                        nudge_count = 0
                        search.reset(cx, cy)
                        if fc % 60 == 0: print(f"[{fc:4d}] ({cx:.2f},{cy:.2f}) · centered ✓")
                        status = "centered ✓"
                    elif nudge_count < MAX_NUDGES:
                        # Calibrated: offset × 640px / 500px_per_sec = seconds
                        # Cap at [100ms, 350ms]
                        h_dur = max(0.10, min(0.35, abs(ox) * 640 / 500)) if abs(ox) > DEAD_ZONE else 0
                        v_dur = max(0.10, min(0.35, abs(oy) * 480 / 400)) if abs(oy) > DEAD_ZONE else 0
                        chosen = []
                        if h_dur > 0:
                            d = 3 if ox > 0 else 2
                            if d == {0:1,1:0,2:3,3:2}.get(last_dir) and now - last_decision < REVERSE_LOCKOUT:
                                pass
                            else:
                                chosen.append((d, h_dur))
                        if v_dur > 0:
                            d = 1 if oy > 0 else 0
                            if d == {0:1,1:0,2:3,3:2}.get(last_dir) and now - last_decision < REVERSE_LOCKOUT:
                                pass
                            else:
                                chosen.append((d, v_dur))
                        if not chosen:
                            nudge_count = 0
                            continue
                        if chosen[0][0] != last_dir:
                            nudge_count = 0
                        syms = "".join(DIRS[d] for d, _ in chosen)
                        durs = ",".join(f"{dur*1000:.0f}ms" for _, dur in chosen)
                        print(f"[{fc:4d}] ({cx:.2f},{cy:.2f}) off=({ox:+.2f},{oy:+.2f}) → {syms} #{nudge_count+1} ({durs})")
                        for d, dur in chosen:
                            queue_ptz(d, dur)
                        last_decision = now; last_dir = chosen[0][0]; nudge_count += 1
                        status = f"track {syms}"
                    else:
                        if fc % 60 == 0: print(f"[{fc:4d}] MAX ({cx:.2f},{cy:.2f})")
                        # Reset after 5s stuck — don't give up forever
                        if now - last_decision > 5.0:
                            nudge_count = 0
                        status = "max ⚠"

            # Display (every 2nd frame to save CPU)
            if fc % 2 == 0:
                chx, chy = FRAME_WIDTH//2, FRAME_HEIGHT//2
                cv2.line(frame, (chx-50,chy), (chx+50,chy), (0,0,255), 1)
                cv2.line(frame, (chx,chy-50), (chx,chy+50), (0,0,255), 1)
                dz = int(DEAD_ZONE * FRAME_WIDTH)
                cv2.rectangle(frame, (chx-dz,chy-dz), (chx+dz,chy+dz), (100,100,100), 1)
                for d in persons:
                    x, y, w, h = d["bbox"]["x"], d["bbox"]["y"], d["bbox"]["width"], d["bbox"]["height"]
                    cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
                if largest:
                    x, y, w, h = largest["bbox"]["x"], largest["bbox"]["y"], largest["bbox"]["width"], largest["bbox"]["height"]
                    cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,255), 3)
                color = (0,255,0) if "centered" in status else (0,255,255) if "track" in status else (0,0,255)
                cv2.putText(frame, f"v7 | {status}", (10,20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                cv2.imshow("Face Tracker v7", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'): break

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        _stop_event.set()
        capture.release(); cv2.destroyAllWindows()
        print("Done.")


if __name__ == "__main__":
    main()
