#!/usr/bin/env python3
"""PTZ 速度校准 —— 用限位测量实际转速
限位码: 60002=上, 60003=下, 60004=左, 60005=右
Speed: 1=slow, 2=medium, 3=fast
Tilt 物理范围: ±60° (120° 全程)
"""
import sys, os, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

P = {"accessToken": EZVIZ_ACCESS_TOKEN, "deviceSerial": EZVIZ_DEVICE_SERIAL, "channelNo": 1}
URL = "https://open.ys7.com/api/lapp/device/ptz"
LIMIT_CODES = ("60002", "60003", "60004", "60005")
LIMIT_NAME = {"60002": "上限", "60003": "下限", "60004": "左限", "60005": "右限"}
DIR_NAME = {0: "上", 1: "下", 2: "左", 3: "右"}

def move(direction, duration, speed):
    r = requests.post(f"{URL}/start", data={**P, "direction": direction, "speed": speed}, timeout=8)
    resp = r.json()
    code = resp.get("code", "")
    msg = resp.get("msg", "")
    time.sleep(duration)
    for d in range(4):
        try:
            requests.post(f"{URL}/stop", data={**P, "direction": d}, timeout=5)
        except:
            pass
    return code, msg

# ── Tilt 校准 ──
print("=" * 55)
print("Tilt 校准 (speed=1 slow)")
print("=" * 55)

# Step 1: go to lower limit
print("\n低头到下极限...")
for i in range(20):
    code, msg = move(1, 1.0, 1)
    print(f"  [{i+1}] down 1s → {code} {msg}")
    if code in LIMIT_CODES:
        print(f"  ✓ 到{LIMIT_NAME.get(code,code)}, 马达~{(i+1)*1}s")
        break

# Step 2: up to upper limit, measure time
print("\n抬头到上极限 (计时)...")
t0 = time.time()
motor_s = 0
for i in range(40):
    code, msg = move(0, 0.5, 1)
    motor_s += 0.5
    print(f"  [{i+1}] up 0.5s → {code} {msg}  (累计{motor_s:.1f}s)")
    if code in LIMIT_CODES:
        elapsed = time.time() - t0
        print(f"\n  ✓ 下限→上限: 马达={motor_s:.1f}s, 墙钟={elapsed:.1f}s")
        # Tilt range is 120° (per C6C specs)
        print(f"  tilt speed ≈ {120/motor_s:.0f}°/s (马达时间)")
        break

# Step 3: back down to verify
print("\n低头回下极限 (验证)...")
t0 = time.time()
motor_s2 = 0
for i in range(40):
    code, msg = move(1, 0.5, 1)
    motor_s2 += 0.5
    print(f"  [{i+1}] down 0.5s → {code} {msg}  (累计{motor_s2:.1f}s)")
    if code in LIMIT_CODES:
        elapsed = time.time() - t0
        print(f"\n  ✓ 上限→下限: 马达={motor_s2:.1f}s, 墙钟={elapsed:.1f}s")
        break

# Pan 无限位，不做全量程测试
print("\nPan 无机械限位 (360° 连续旋转), 跳过。")

print("\n完成。把 tilt speed=1 的全程秒数告诉我。")
