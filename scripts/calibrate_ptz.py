#!/usr/bin/env python3
"""Auto-calibrate PTZ minimum step using ORB feature matching."""

import sys, os, time, cv2, requests
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from config import EZVIZ_RTSP_URL
from runtime.perception.ezviz_capture import EZVIZCapture

TOKEN = "at.8rin761wdcmoiek731tfcr1d6h7ub01a-3uvhpbn963-1pw9t9p-11z2togvq"
SERIAL = "F04465701"

def ptz_nudge(duration_ms, direction):
    dur = duration_ms / 1000.0
    requests.post("https://open.ys7.com/api/lapp/device/ptz/start", data={
        "accessToken": TOKEN, "deviceSerial": SERIAL,
        "channelNo": 1, "direction": direction, "speed": 3,
    }, timeout=5)
    time.sleep(dur)
    for d in range(4):
        try:
            requests.post("https://open.ys7.com/api/lapp/device/ptz/stop", data={
                "accessToken": TOKEN, "deviceSerial": SERIAL,
                "channelNo": 1, "direction": d,
            }, timeout=3)
        except: pass

def capture_frame(cap):
    """Drain RTSP buffer and get a fresh frame."""
    for _ in range(8):
        cap.read()
        time.sleep(0.2)
    r = cap.read()
    return r[0] if r else None

def measure_displacement(img1, img2):
    """Use ORB to find matched features and measure average horizontal shift."""
    orb = cv2.ORB_create(2000)
    kp1, des1 = orb.detectAndCompute(img1, None)
    kp2, des2 = orb.detectAndCompute(img2, None)
    if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
        return 0, 0, 0

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(des1, des2)
    if len(matches) < 5:
        return 0, 0, 0

    dx_sum, dy_sum = 0, 0
    for m in matches:
        dx = kp2[m.trainIdx].pt[0] - kp1[m.queryIdx].pt[0]
        dy = kp2[m.trainIdx].pt[1] - kp1[m.queryIdx].pt[1]
        dx_sum += dx; dy_sum += dy

    n = len(matches)
    return abs(dx_sum / n), abs(dy_sum / n), n


capture = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
if not capture.start():
    print("ERROR: camera"); sys.exit(1)

print("=== Auto-Calibrate PTZ Minimum Step ===\n")
print("Step 1: finding baseline displacement (no PTZ movement)...")
ref = capture_frame(capture)
time.sleep(1)
same = capture_frame(capture)
noise_dx, noise_dy, noise_n = measure_displacement(ref, same)
print(f"  Noise level: dx={noise_dx:.1f}px dy={noise_dy:.1f}px ({noise_n} matches)\n")

print("Step 2: Testing different nudge durations...\n")
# Test right, then left at each duration, use the larger displacement
results = []
for ms in [500, 300, 200, 150, 120, 100, 80, 60, 50, 40]:
    # Right nudge
    ref = capture_frame(capture)
    ptz_nudge(ms, 3)  # right
    time.sleep(3)     # wait for RTSP
    after = capture_frame(capture)
    dx_r, dy_r, n_r = measure_displacement(ref, after)

    # Left nudge (return to position)
    ref2 = capture_frame(capture)
    ptz_nudge(ms, 2)  # left
    time.sleep(3)
    after2 = capture_frame(capture)
    dx_l, dy_l, n_l = measure_displacement(ref2, after2)

    dx = max(dx_r, dx_l)
    matches = max(n_r, n_l)
    signal = dx > (noise_dx * 1.5)
    results.append((ms, dx, matches, signal))
    bar = "█" * int(dx / 5) if dx > 0 else ""
    tag = "✅ MOVED" if signal else "  (noise)"
    print(f"  {ms:4d}ms → dx={dx:6.1f}px matches={matches:3d} {bar} {tag}")

print("\n=== Summary ===")
print(f"{'Duration':>8s}  {'Displacement':>12s}  {'Effective':>10s}")
for ms, dx, n, signal in results:
    print(f"  {ms:4d}ms     {dx:6.1f}px          {'YES' if signal else 'noise'}")

# Find minimum effective duration
effective = [(ms, dx) for ms, dx, n, s in results if s]
if effective:
    min_ms, min_dx = effective[-1]  # smallest that still works
    print(f"\nMinimum effective step: {min_ms}ms (~{min_dx:.0f}px @ 640px = ~{min_dx/640*100:.1f}% of frame)")
    # Rough estimate: 80° FOV / 640px
    deg = min_dx / 640 * 80
    print(f"Estimated angular step: ~{deg:.1f}°")
else:
    print("\nNo effective steps found — try larger durations")

capture.release()
