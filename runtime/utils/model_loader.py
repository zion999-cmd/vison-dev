"""
ONNX model loader with auto-download for Phase 1 models.

Supported models:
  - YuNet (face detection)    ~85 KB
  - Silero VAD (voice)        ~1.7 MB
  - YOLOv8-nano (objects)     ~6 MB
"""

import logging
import urllib.request
from pathlib import Path
from typing import Optional

import onnxruntime as ort
import requests as _requests

logger = logging.getLogger(__name__)

MODELS_DIR = Path("models")
DEFAULT_PROVIDERS = ["CPUExecutionProvider"]

_MODEL_URLS = {
    "yunet": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "silero_vad": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
    "yolov8n": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx",
    "yolov8s": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8s.onnx",
}


def _download(url: str, dest: Path) -> None:
    """Download a file, trying requests first (better SSL), then urllib."""
    logger.info("Downloading %s → %s ...", Path(url).name, dest)
    try:
        resp = _requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception:
        logger.debug("requests download failed, trying urllib...")
        urllib.request.urlretrieve(url, dest)
    logger.info("Downloaded %s (%.1f KB)", dest.name, dest.stat().st_size / 1024)


def ensure_model(model_name: str, url: Optional[str] = None) -> Path:
    """
    Ensure a model file exists locally. Downloads if missing.

    Args:
        model_name: key in _MODEL_URLS, or a custom filename.
        url: override download URL.

    Returns:
        Path to the local model file.
    """
    MODELS_DIR.mkdir(exist_ok=True)

    if model_name in _MODEL_URLS:
        url = _MODEL_URLS[model_name]
        fname = Path(url).name
    else:
        fname = model_name

    dest = MODELS_DIR / fname
    if not dest.exists():
        if url:
            _download(url, dest)
        else:
            raise FileNotFoundError(
                f"Model '{fname}' not found at {dest} and no download URL provided."
            )
    return dest


def load_session(model_path: str, providers: Optional[list] = None) -> ort.InferenceSession:
    """
    Load an ONNX model into an InferenceSession.

    Args:
        model_path: path or model name (auto-downloaded if needed).
        providers: ONNX execution providers. Defaults to CPU.

    Returns:
        Configured InferenceSession ready for inference.
    """
    path = ensure_model(model_path) if not Path(model_path).exists() else Path(model_path)
    providers = providers or DEFAULT_PROVIDERS
    logger.info("Loading ONNX model: %s", path.name)
    session = ort.InferenceSession(str(path), providers=providers)
    logger.debug("  Inputs: %s", [i.name for i in session.get_inputs()])
    logger.debug("  Outputs: %s", [o.name for o in session.get_outputs()])
    return session
