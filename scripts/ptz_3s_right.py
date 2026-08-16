#!/usr/bin/env python3
"""PTZ 测试: 右转 3s, speed=7"""
import sys, os, time, requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EZVIZ_ACCESS_TOKEN, EZVIZ_DEVICE_SERIAL

P = {"accessToken": EZVIZ_ACCESS_TOKEN, "deviceSerial": EZVIZ_DEVICE_SERIAL, "channelNo": 1}

print("右转 3s speed=7 ...")
r = requests.post("https://open.ys7.com/api/lapp/device/ptz/start",
                   data={**P, "direction": 3, "speed": 7}, timeout=8)
print(f"start: {r.json()}")
time.sleep(3.0)
for d in range(4):
    requests.post("https://open.ys7.com/api/lapp/device/ptz/stop",
                  data={**P, "direction": d}, timeout=5)
print("停止。镜头转了多少度？")
