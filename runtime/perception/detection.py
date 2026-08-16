"""
Detection — unified output format for all object detectors.

Future-proofing for YOLO World / GroundingDINO / CLIP / SigLIP:
    embedding is None for YOLO-v8, but will carry feature vectors
    for open-vocabulary detectors.

Current detectors:
    - YOLOv8s ONNX (runtime/perception/object_detection.py)
    - Contour fallback (same file)
    - Face detector (runtime/perception/face_detection.py) — returns dicts

All detectors should eventually output Detection objects.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class Detection:
    """One detected object/region in a frame.

    Minimal interface — add fields as detectors evolve.
    """
    bbox: Dict[str, int]          # {"x": int, "y": int, "width": int, "height": int}
    label: str = ""               # class name: "person", "chair", etc.
    confidence: float = 0.0       # detection confidence [0, 1]
    center_x: int = 0             # bbox center x (pixels)
    center_y: int = 0             # bbox center y (pixels)
    embedding: Optional[List[float]] = None  # visual feature vector (YOLO World, CLIP, etc.)

    @classmethod
    def from_yolo(cls, detection_dict: Dict) -> "Detection":
        """Convert legacy YOLO dict output to Detection."""
        bbox = detection_dict.get("bbox", {})
        return cls(
            bbox=bbox,
            label=detection_dict.get("class_name", ""),
            confidence=detection_dict.get("confidence", 0.0),
            center_x=detection_dict.get("center_x", 0),
            center_y=detection_dict.get("center_y", 0),
        )

    def to_legacy_dict(self) -> Dict:
        """Backward-compatible dict for existing consumers."""
        return {
            "class_name": self.label,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "center_x": self.center_x,
            "center_y": self.center_y,
            # embedding intentionally excluded from legacy format
        }


def detections_to_legacy(detections: List[Detection]) -> List[Dict]:
    """Batch convert Detection objects to legacy dict format."""
    return [d.to_legacy_dict() for d in detections]


def legacy_to_detections(dicts: List[Dict]) -> List[Detection]:
    """Batch convert legacy dicts to Detection objects."""
    return [Detection.from_yolo(d) for d in dicts]
