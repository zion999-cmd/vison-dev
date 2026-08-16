"""
Perception - L2: Face Detection via YuNet ONNX (OpenCV FaceDetectorYN)

YuNet is a lightweight face detector (~230KB ONNX model).
OpenCV's FaceDetectorYN handles all multi-scale decoding + NMS internally.

Accuracy ~95%, supports side faces, partial occlusion, 5-point landmarks.
Replaces OpenCV Haar Cascade.
"""

import logging
from typing import List, Dict

import cv2

from config import FACE_CONFIDENCE_THRESHOLD
from runtime.utils.model_loader import ensure_model

logger = logging.getLogger("L2.Face")

_FACE_MODEL = "yunet"
_INPUT_SIZE = (640, 640)


class FaceDetector:
    """Face detection using YuNet via OpenCV FaceDetectorYN."""

    def __init__(self, model: str = _FACE_MODEL, score_threshold: float = FACE_CONFIDENCE_THRESHOLD):
        self.score_threshold = score_threshold
        path = str(ensure_model(model))
        self._detector = cv2.FaceDetectorYN_create(
            model=path,
            config="",
            input_size=_INPUT_SIZE,
            score_threshold=score_threshold,
            nms_threshold=0.3,
            top_k=5000,
        )
        logger.info("FaceDetector (YuNet) initialised: %s", path)

    def detect(self, frame_bgr) -> List[Dict]:
        """
        Detect faces in a BGR frame.

        Returns list of {bbox, confidence, landmarks, center_x, center_y}.
        """
        h, w = frame_bgr.shape[:2]
        self._detector.setInputSize((w, h))

        _, detections = self._detector.detect(frame_bgr)
        # detections shape: [N, 15] — [x1,y1,w,h, score, 5*landmarks_x_y]

        result = []
        if detections is None or len(detections) == 0:
            return result

        for det in detections:
            score = float(det[4])
            if score < self.score_threshold:
                continue

            x, y, bw, bh = int(det[0]), int(det[1]), int(det[2]), int(det[3])

            landmarks = []
            for i in range(5):
                lx = int(det[5 + i * 2])
                ly = int(det[5 + i * 2 + 1])
                landmarks.append({"x": lx, "y": ly})

            result.append({
                "bbox": {"x": x, "y": y, "width": bw, "height": bh},
                "confidence": round(score, 3),
                "landmarks": landmarks,
                "center_x": int(x + bw / 2),
                "center_y": int(y + bh / 2),
            })

        return result

    def draw_faces(self, frame_bgr, faces: List[Dict]):
        """Draw face bounding boxes and landmarks for visualization."""
        for face in faces:
            b = face["bbox"]
            cv2.rectangle(frame_bgr, (b["x"], b["y"]),
                          (b["x"] + b["width"], b["y"] + b["height"]),
                          (0, 255, 0), 2)
            cv2.putText(frame_bgr, f"{face['confidence']:.2f}",
                        (b["x"], b["y"] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
            for lm in face.get("landmarks", []):
                cv2.circle(frame_bgr, (lm["x"], lm["y"]), 2, (0, 255, 255), -1)
        return frame_bgr
