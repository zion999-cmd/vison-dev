"""
Perception - L2: Object Detection

Primary: YOLO11n ONNX (80 classes, CPU inference).
Fallback: contour-based frame differencing (no classification, but works).

Replaces contour-based object_diff.py.
"""

import logging
from typing import List, Dict, Tuple, Optional

import cv2
import numpy as np

from config import (
    OBJECT_CONFIDENCE_THRESHOLD,
    OBJECT_DIFF_THRESHOLD, OBJECT_MIN_AREA,
)
from runtime.utils.model_loader import ensure_model, load_session

logger = logging.getLogger("L2.ObjectDet")

_YOLO_MODEL_KEY = "yolo11n"  # YOLO11 nano — CPU inference fits the 5 FPS budget
_INPUT_SIZE = (640, 640)
_IOU_THRESHOLD = 0.45

_COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]


# ── YOLO post-processing utils ──

def _letterbox(frame: np.ndarray, target: Tuple[int, int]) -> Tuple[np.ndarray, float, int, int]:
    h, w = frame.shape[:2]
    tw, th = target
    scale = min(tw / w, th / h)
    nw, nh = int(w * scale), int(h * scale)
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
    pad_w = (tw - nw) // 2
    pad_h = (th - nh) // 2
    canvas[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized
    return canvas, scale, pad_w, pad_h


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    order = np.argsort(scores)[::-1]
    keep = []
    while len(order) > 0:
        i = order[0]
        keep.append(i)
        if len(order) == 1:
            break
        ious = _box_iou(boxes[i:i + 1], boxes[order[1:]])[0]
        order = order[1:][ious < iou_threshold]
    return np.array(keep, dtype=int)


def _box_iou(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    x1 = np.maximum(boxes_a[:, 0:1], boxes_b[:, 0])
    y1 = np.maximum(boxes_a[:, 1:2], boxes_b[:, 1])
    x2 = np.minimum(boxes_a[:, 2:3], boxes_b[:, 2])
    y2 = np.minimum(boxes_a[:, 3:4], boxes_b[:, 3])
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area_a = (boxes_a[:, 2] - boxes_a[:, 0]) * (boxes_a[:, 3] - boxes_a[:, 1])
    area_b = (boxes_b[:, 2] - boxes_b[:, 0]) * (boxes_b[:, 3] - boxes_b[:, 1])
    return inter / (area_a[:, None] + area_b - inter + 1e-9)


# ── Fallback contour-based detector ──

class _ContourDetector:
    """Fallback: pixel-diff + contour detection. No classification, but works without models."""

    def __init__(self, diff_threshold: int = OBJECT_DIFF_THRESHOLD, min_area: int = OBJECT_MIN_AREA):
        self.diff_threshold = diff_threshold
        self.min_area = min_area
        self._reference_gray: Optional[np.ndarray] = None

    def detect(self, frame_bgr: np.ndarray) -> List[Dict]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)

        if self._reference_gray is None:
            self._reference_gray = gray.copy()
            return []

        diff = cv2.absdiff(gray, self._reference_gray)
        _, thresh = cv2.threshold(diff, self.diff_threshold, 255, cv2.THRESH_BINARY)

        kernel = np.ones((5, 5), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        changes = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area:
                continue
            x, y, w, h = cv2.boundingRect(cnt)
            changes.append({
                "class_name": "changed",
                "confidence": 0.5,
                "bbox": {"x": x, "y": y, "width": w, "height": h},
                "center_x": int(x + w / 2),
                "center_y": int(y + h / 2),
            })

        if len(changes) == 0:
            self._reference_gray = cv2.addWeighted(self._reference_gray, 0.95, gray, 0.05, 0)

        return changes

    def reset(self) -> None:
        self._reference_gray = None


# ── Main detector ──

class ObjectDetector:
    """
    Object detection with YOLO11n ONNX (primary) or contour diff (fallback).

    If the YOLO ONNX model is not available locally, falls back to
    contour-based detection that detects "something changed" without
    classifying it.
    """

    def __init__(self, score_threshold: float = OBJECT_CONFIDENCE_THRESHOLD):
        self.score_threshold = score_threshold
        self._yolo: Optional["ort.InferenceSession"] = None
        self._input_name: Optional[str] = None
        self._fallback = _ContourDetector()

        self._init_yolo()

    def _init_yolo(self) -> None:
        """Try to load YOLO11n ONNX. Fall back to contour detector if unavailable."""
        try:
            import onnxruntime as ort
            path = ensure_model(_YOLO_MODEL_KEY)
            self._yolo = load_session(str(path))
            self._input_name = self._yolo.get_inputs()[0].name
            logger.info("ObjectDetector: %s ONNX loaded (%s)", _YOLO_MODEL_KEY, path.name)
        except Exception as e:
            logger.warning("YOLO model not available (%s), using contour fallback", e)

    def detect(self, frame_bgr: np.ndarray) -> List[Dict]:
        """
        Detect objects in a BGR frame. Uses YOLO if available, else contour diff.

        Returns list of {class_name, confidence, bbox, center_x, center_y}.
        """
        if self._yolo is not None and self._input_name is not None:
            return self._detect_yolo(frame_bgr)
        return self._fallback.detect(frame_bgr)

    def _detect_yolo(self, frame_bgr: np.ndarray) -> List[Dict]:
        import onnxruntime as ort
        h, w = frame_bgr.shape[:2]

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        letter, scale, pad_w, pad_h = _letterbox(rgb, _INPUT_SIZE)
        blob = letter.astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))
        blob = np.expand_dims(blob, axis=0)

        outputs = self._yolo.run(None, {self._input_name: blob})
        preds = np.squeeze(outputs[0], axis=0)  # [84, 8400]

        bbox_raw = preds[:4, :].T
        scores_raw = preds[4:, :].T

        class_ids = np.argmax(scores_raw, axis=1)
        scores = np.max(scores_raw, axis=1)

        mask = scores > self.score_threshold
        bbox_raw, scores, class_ids = bbox_raw[mask], scores[mask], class_ids[mask]

        if len(bbox_raw) == 0:
            return []

        cx, cy, bw, bh = bbox_raw[:, 0], bbox_raw[:, 1], bbox_raw[:, 2], bbox_raw[:, 3]
        x1 = cx - bw / 2
        y1 = cy - bh / 2
        x2 = cx + bw / 2
        y2 = cy + bh / 2
        boxes = np.stack([x1, y1, x2, y2], axis=1)

        keep = _nms(boxes, scores, _IOU_THRESHOLD)

        result = []
        for idx in keep:
            ox1 = (boxes[idx, 0] - pad_w) / scale
            oy1 = (boxes[idx, 1] - pad_h) / scale
            ox2 = (boxes[idx, 2] - pad_w) / scale
            oy2 = (boxes[idx, 3] - pad_h) / scale

            ox1, oy1 = max(0, int(ox1)), max(0, int(oy1))
            ox2, oy2 = min(w, int(ox2)), min(h, int(oy2))

            result.append({
                "class_name": _COCO_CLASSES[class_ids[idx]],
                "confidence": round(float(scores[idx]), 3),
                "bbox": {"x": ox1, "y": oy1, "width": ox2 - ox1, "height": oy2 - oy1},
                "center_x": int((ox1 + ox2) / 2),
                "center_y": int((oy1 + oy2) / 2),
            })

        return result

    def draw_objects(self, frame_bgr: np.ndarray, objects: List[Dict]) -> np.ndarray:
        for obj in objects:
            b = obj["bbox"]
            cv2.rectangle(frame_bgr, (b["x"], b["y"]),
                          (b["x"] + b["width"], b["y"] + b["height"]),
                          (255, 0, 0), 2)
            label = f"{obj['class_name']} {obj['confidence']:.2f}"
            cv2.putText(frame_bgr, label, (b["x"], b["y"] - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)
        return frame_bgr
