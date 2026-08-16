#!/usr/bin/env python3
"""PTZ 转速校准 — 手动测量实际角速度"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time, requests
from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

URL = "https://open.ys7.com/api/lapp/device/ptz/start"
STOP = "https://open.ys7.com/api/lapp/device/ptz/stop"
PARAMS = {"accessToken": EZVIZ_ACCESS_TOKEN, "deviceSerial": EZVIZ_DEVICE_SERIAL, "channelNo": 1}

DIR_MAP = {0: "上", 1: "下", 2: "左", 3: "右"}


def ptz(direction: int, duration: float, speed: int = 7):
    """Move PTZ for `duration` seconds at given speed, then stop."""
    r = requests.post(URL, data={**PARAMS, "direction": direction, "speed": speed}, timeout=8)
    print(f"  ▶ {DIR_MAP[direction]} speed={speed} {duration}s → {r.json()['msg']}")
    time.sleep(duration)
    for d in range(4):
        try:
            requests.post(STOP, data={**PARAMS, "direction": d}, timeout=5)
        except Exception:
            pass
    print(f"  ■ 已停止")


def main():
    print("=" * 50)
    print("PTZ 转速校准工具")
    print("=" * 50)
    print()
    print("接下来会依次测试不同时长和速度。")
    print("请观察摄像头镜头实际转动的角度，记录下来。")
    print()

    tests = [
        # (方向, 时长, 速度, 标签)
        (3, 1.0, 7, "右转 1s speed=7"),
        (2, 1.0, 7, "左转 1s speed=7 (回原位)"),
        (3, 2.0, 7, "右转 2s speed=7"),
        (2, 2.0, 7, "左转 2s speed=7 (回原位)"),
        (3, 5.0, 7, "右转 5s speed=7"),
        (2, 5.0, 7, "左转 5s speed=7 (回原位)"),
    ]

    for direction, duration, speed, label in tests:
        input(f"\n按 Enter 执行: {label} ...")
        ptz(direction, duration, speed)
        deg = input("  实际转动角度（估算）: ")
        print(f"  → {label}: 实际 {deg}°  (理论 {duration}s @ speed={speed})")

    print("\n" + "=" * 50)
    print("校准完成。请把上面记录的角度告诉我，我来调整 scanner 参数。")
    print("=" * 50)


if __name__ == "__main__":
    main()
