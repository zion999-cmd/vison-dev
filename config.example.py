"""
Vision Perception Runtime - Configuration (EXAMPLE TEMPLATE)

Copy this file to `config.py` and fill in your own values:

    cp config.example.py config.py

`config.py` is git-ignored — never commit real keys. Secrets should be
read from environment variables (recommended):

    export EZVIZ_APP_KEY=...
    export EZVIZ_APP_SECRET=...
    export EZVIZ_ACCESS_TOKEN=...
    export EZVIZ_RTSP_URL=rtsp://user:pass@192.168.x.x:554/...
    export DASHSCOPE_API_KEY=sk-...
    export GATEWAY_QWEN_API_KEY=sk-...
"""

import os

# ═══════════════════════════════════════════════════════════════
# L1: Stream Ingest
# ═══════════════════════════════════════════════════════════════

# --- 开发阶段：本地摄像头 ---
CAMERA_INDEX = 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

# --- 生产阶段：ESP32 推流 ---
STREAM_URL = "rtsp://esp32.local:8554/stream"
# "local" (cv2.VideoCapture USB/内置摄像头) | "ezviz" (萤石云) | "rtsp" | "webrtc"
STREAM_TYPE = "local"
AUDIO_DEVICE_INDEX = 0            # 本地麦克风（开发阶段）

# --- EZVIZ 云摄像头 (已停用) ---
EZVIZ_RTSP_URL = os.environ.get("EZVIZ_RTSP_URL", "")   # rtsp://user:pass@host:554/...
EZVIZ_APP_KEY = os.environ.get("EZVIZ_APP_KEY", "")
EZVIZ_APP_SECRET = os.environ.get("EZVIZ_APP_SECRET", "")
EZVIZ_ACCESS_TOKEN = os.environ.get("EZVIZ_ACCESS_TOKEN", "")
EZVIZ_DEVICE_SERIAL = "F04465701"

# --- Servo PTZ (Arduino SG90 两轴云台) ---
SERVO_SERIAL_PORT = "/dev/tty.usbserial-A600J5V6"   # 替换为你的串口设备
SERVO_BAUD = 115200

# --- 通用 ---
PERCEPTION_FPS = 5
SHOW_PREVIEW = True

# ═══════════════════════════════════════════════════════════════
# L2: Signal Detection
# ═══════════════════════════════════════════════════════════════

# Frame diff（门卫——画面变了吗）
FRAME_DIFF_THRESHOLD = 25          # 像素强度差异阈值
FRAME_DIFF_MIN_PIXELS = 500        # 最少变化像素数

# ONNX 模型路径
FACE_DETECTION_MODEL = "models/yunet.onnx"
FACE_CONFIDENCE_THRESHOLD = 0.5
OBJECT_DETECTION_MODEL = "models/yolov8n.onnx"
OBJECT_CONFIDENCE_THRESHOLD = 0.5
VAD_MODEL = "models/silero_vad.onnx"
VAD_THRESHOLD = 0.5

# --- 备用：轮廓差分（YOLO 不可用时的 fallback，object_detection.py 使用）---
OBJECT_DIFF_THRESHOLD = 30         # 轮廓差分阈值
OBJECT_MIN_AREA = 1000             # 轮廓差分最小面积

# ═══════════════════════════════════════════════════════════════
# L3: Scene / State Machine
# ═══════════════════════════════════════════════════════════════

SCENE_UPDATE_INTERVAL = 0.2        # seconds
STATE_FOCUS_TIMEOUT = 30.0         # 无用户交互 → IDLE
STATE_ALERT_TIMEOUT = 60.0         # 无异常 → IDLE
STATE_DEBOUNCE_LEAVE = 3.0         # 用户离开防抖——确认离开 N 秒后才切换状态

# ═══════════════════════════════════════════════════════════════
# L4: Attention Engine
# ═══════════════════════════════════════════════════════════════

ATTENTION_THRESHOLD = 0.6
ATTENTION_DECAY_FACTOR = 0.95      # per second
ATTENTION_TOP_K = 3
ATTENTION_EVOLVE_INTERVAL = 100    # 权重自演化间隔（帧数）

# DNA 级注意力先验
BASE_WEIGHTS = {
    "human_face":       0.9,
    "voice_detected":   0.95,
    "large_motion":     0.6,
    "new_object":       0.5,
    "person_entered":   0.85,
    "person_left":      0.5,
    "sustained_gaze":   0.7,
    "background_noise": 0.1,
}

# ═══════════════════════════════════════════════════════════════
# L5: Memory
# ═══════════════════════════════════════════════════════════════

MEMORY_MAX_ENTRIES = 200
MEMORY_CONTEXT_EVENTS = 5          # 注入 LLM 上下文的事件数

# ═══════════════════════════════════════════════════════════════
# L6: Cognition / API
# ═══════════════════════════════════════════════════════════════

# --- 文字 LLM（oc2api，本地推理）---
TEXT_API_BASE = "http://127.0.0.1:31498/v1"
TEXT_API_KEY = "oc2api-local"
TEXT_MODEL = "opencode/deepseek-v4-flash-free"

# --- 视觉 VLM（多后端轮询）---
VLM_BACKENDS = [
    {"base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""), "model": "qwen3.6-plus"},
    {"base_url": "http://127.0.0.1:3001",
        "api_key": os.environ.get("GATEWAY_QWEN_API_KEY", ""), "model": "qwen-web/qwen-chat"},
]

COGNITION_MIN_INTERVAL = 3.0       # 两次触发最小间隔（秒）

# ═══════════════════════════════════════════════════════════════
# Intention Engine
# ═══════════════════════════════════════════════════════════════

INTENTION_PRIORITY = {
    "emergency": 1.0,
    "speaking": 0.9,
    "looking": 0.8,
    "approaching": 0.7,
    "gesturing": 0.6,
    "using_desk": 0.4,
    "leaving": 0.3,
    "ambient": 0.1,
    "none": 0.0,
}

# ═══════════════════════════════════════════════════════════════
# P0008: Observation Intent (Mission Role via LLM)
# ═══════════════════════════════════════════════════════════════

# Which persona to load (name without .yaml extension)
# Available: companion, security, reception, patrol, pet
PERSONA = "companion"

# Mission Role provider: "llm" | "rule" | "none"
# - llm: TextAPI generates dynamic weights (requires LLM backend)
# - rule: uses persona's default mission_role weights (no LLM)
# - none: no mission role (intrinsic only, runtime works fine)
MISSION_ROLE_PROVIDER = "llm"

# ── Mission LLM backend (separate from L6 cognition LLM) ──
# Uses OpenAI-compatible /v1/chat/completions endpoint.
# Supported backends: Ollama (http://localhost:11434/v1), oc2api, vLLM, etc.
MISSION_LLM_BASE = "http://localhost:11434/v1"
MISSION_LLM_MODEL = "gemma4:cloud"
MISSION_LLM_API_KEY = "ollama"  # Ollama doesn't require auth, but some clients need a placeholder

# Seconds between mission role refresh attempts (only when expired)
MISSION_ROLE_REFRESH_SEC = 300

# ═══════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════

LOG_LEVEL = "INFO"                  # console level: DEBUG, INFO, WARNING, ERROR
LOG_DIR = "logs"                    # log file directory
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024  # 5MB per file
LOG_FILE_BACKUP_COUNT = 3           # keep 3 rotated files
