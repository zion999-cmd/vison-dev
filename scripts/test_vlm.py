#!/usr/bin/env python3
"""测试 VLM API 是否正常"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
from runtime.utils.vision_api import VisionAPI

print("=" * 50)
print("VLM API 测试")
print("=" * 50)

# 1. 初始化
print("\n1. 初始化 VisionAPI...")
api = VisionAPI()
print(f"   backends: {len(api._backends)}")
for b in api._backends:
    print(f"   - {b.get('model', '?')} @ {b.get('base_url', '?')}")

# 2. 抓一帧
print("\n2. 获取测试帧...")
from config import EZVIZ_RTSP_URL, STREAM_TYPE
if STREAM_TYPE == "ezviz":
    from runtime.perception.ezviz_capture import EZVIZCapture
    cam = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
else:
    from runtime.perception.capture import CameraCapture
    cam = CameraCapture()

if not cam.start():
    print("   FAIL: camera start failed")
    sys.exit(1)
time.sleep(2)
result = cam.read()
if result is None:
    print("   FAIL: no frame")
    cam.release()
    sys.exit(1)
frame, ts = result
print(f"   frame: {frame.shape}, ts={ts:.3f}")

# 3. 调用 VLM (两次, 测试两条线路)
prompt = "请用一句话简短描述你看到的场景。只回复中文，不超过20字。"

for i in range(2):
    backend = api._backends[api._idx]
    print(f"\n3.{i+1} 调用 VLM — 线路: {backend['model']} ...")
    t0 = time.time()
    resp = api.analyze_frame(frame, prompt)
    elapsed = time.time() - t0
    if resp:
        print(f"   ✓ 响应 ({elapsed:.1f}s): {resp[:200]}")
    else:
        print(f"   ✗ 返回 None ({elapsed:.1f}s)")

cam.release()
print("\n完成。")
