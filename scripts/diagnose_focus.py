#!/usr/bin/env python3
"""诊断脚本：当 yellow tracking box 锁定 target 时，截取框内图像并用 VLM 识别"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
from config import EZVIZ_RTSP_URL
from runtime.perception.ezviz_capture import EZVIZCapture
from runtime.perception.object_detection import ObjectDetector
from runtime.utils.vision_api import VisionAPI

print("=" * 55)
print("Focus 诊断 — 截取检测框 + VLM 识别")
print("=" * 55)

# Init
cam = EZVIZCapture(rtsp_url=EZVIZ_RTSP_URL)
detector = ObjectDetector()
vlm = VisionAPI()

if not cam.start():
    print("FAIL: camera")
    sys.exit(1)
time.sleep(2)

print("\n连续抓取 5 帧，对每帧的 highest-confidence detection 做 VLM 识别...\n")

for i in range(5):
    result = cam.read()
    if result is None:
        continue
    frame, ts = result
    objects = detector.detect(frame)

    if not objects:
        print(f"  [{i+1}] 无检测")
        continue

    # 找到最高置信度的检测
    best = max(objects, key=lambda o: o.get("confidence", 0))
    label = best["class_name"]
    conf = best["confidence"]
    bbox = best.get("bbox", {})

    # 裁剪
    h, w = frame.shape[:2]
    x = max(0, int(bbox.get("x", 0)))
    y = max(0, int(bbox.get("y", 0)))
    bw = min(int(bbox.get("width", w)), w - x)
    bh = min(int(bbox.get("height", h)), h - y)
    crop = frame[y:y+bh, x:x+bw] if bw > 0 and bh > 0 else frame

    # 保存裁剪图
    crop_path = f"/tmp/focus_crop_{i+1}.jpg"
    cv2.imwrite(crop_path, crop)

    # VLM 识别
    prompt = f"YOLO认为这是'{label}'({conf:.2f})。请简短描述你实际看到了什么。只回复中文，不超过15字。"
    resp = vlm.analyze_frame(crop, prompt)

    print(f"  [{i+1}] YOLO: {label} {conf:.2f} → VLM: {resp[:80] if resp else 'N/A'}")
    print(f"        crop saved: {crop_path}")

cam.release()
print("\n完成。查看 /tmp/focus_crop_*.jpg 确认 YOLO 到底看到了什么。")
