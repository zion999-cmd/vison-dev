# Vision Perception Runtime

高频感知 + 低频认知的视觉感知运行时。从"持续识别"升级为"持续存在的环境驱动型 Runtime"。

## 架构

```
L1: Camera (cv2.VideoCapture, 640x480, 5FPS)
    ↓
L2: Detection — FrameDiff (motion gate) → YuNet ONNX (faces) + YOLOv8n ONNX (80 classes) + Silero VAD (voice)
    ↓
L3: SceneState — Idle → Focus → Alert → Sleep, ~3s debounce
    ↓
L4: AttentionEngine — dynamic weighted scoring + decay + self-evolving weights
     → IntentionEngine — 8 intentions
    ↓
L5: EpisodicMemory — rolling buffer, 200 entries, low-importance compression
    ↓
L6: CognitionTrigger — sparse VLM/LLM calls, cooling + queue dedup
```

### Layered Attention (L4-L6 之间)

| 层 | 时间尺度 | 问题 |
|----|---------|------|
| Interest | seconds | "What changed?" |
| Curiosity | minutes | "What's worth revisiting?" |
| Familiarity | min–hours | "Have I seen this enough?" |
| Role | innate | "Should I care about this class?" |
| Importance (Phase 7A) | hours–days | "What actually causes events?" |

**Curiosity 公式:** `interest × uncertainty × freshness × (1−familiarity) × role − movement_cost`

### Entity 系统 (identity > location)

HSV 直方图签名匹配，不依赖 PTZ 坐标：
```
CANDIDATE (5 sightings) → ACTIVE → LOST (30 misses) → FORGOTTEN (150 misses)
```

### PTZ 控制 (Arduino SG90, serial)

- Pan 10–165°, Tilt 95–170°（降低极限防止舵机磨损）
- 状态机：sweep → stay → track → explore
- Face bbox 优先，proportional 追踪

## 运行

```bash
conda activate vision-dev
python runtime/main.py     # L1-L6 全管线 + PTZ
pytest -q                  # 全部测试必须通过
```

## 已完成系统

| 系统 | 说明 |
|------|------|
| L1-L6 感知管线 | Camera → Detection → SceneState → Attention → Memory → Cognition |
| Layered Attention | Interest + Curiosity + Familiarity + Role + Importance (Phase 7A) |
| Entity 系统 | HSV signature matching + 生命周期管理 |
| PTZ 控制 | Arduino SG90 servo, sweep/stay/track/explore |
| Episodic Memory | 滚动缓冲，200 entries，低重要性压缩 |
| Behavior System | IDLE→OBSERVE→TRACKING→ENGAGED→ACCOMPANYING |

## 核心原则

- **No VLM/LLM in core layers** — YOLO + YuNet + HSV only
- **Architecture fix > parameter fix** — 优先重构结构，而非调参
- **Entity identity > YOLO label** — 视觉签名匹配，非类别名匹配
- **Observe before defining** — Phase 7A 仅观测，不定义 Value 公式
- **Single-threaded inference** — 主线程独占 camera + ONNX
- **Immutable patterns** — 始终返回新对象

## 文档导航

| 你想… | 读这个 |
|--------|-------|
| 了解系统架构 | [doc/architecture.md](doc/architecture.md) |
| 浏览全部文档 | [doc/README.md](doc/README.md) |
| 了解设计演进 | [proposals/](proposals/) (P0001–P0007) |
| 了解开发方法论 | [interaction/README.md](interaction/README.md) |
| Claude Code 开始工作 | [CLAUDE.md](CLAUDE.md) → [context/current_state.md](context/current_state.md) → [context/handoff.md](context/handoff.md) |
