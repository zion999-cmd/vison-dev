# 视觉感知运行时开发文档

## Vision Perception Runtime

> 从 Chat Bot 到 Embodied Runtime —— 高频感知 + 低频认知架构

---

## 一、核心思想

### 1.1 真正的智能不是一个模型

而是**一个长期演化的系统**。

很多人做机器人做到后面会突然意识到：真正难的不是 LLM，而是**"持续存在"**。

### 1.2 两条路线

**错误路线**——编码整个世界：

```
if user_enter_room:
    ...
if cup_move:
    ...
```

这是业务逻辑程序，不是智能体。

**正确路线**——写成长机制：

```
attention_weights = {
    "human_voice": 0.9,
    "face_detected": 0.8,
    "large_motion": 0.7,
    "object_change": 0.4
}
```

你不是在处理所有情况，而是在维护**关注机制**。行为会从 Runtime 中浮现。

### 1.3 关键洞察

**持续感知** 与 **认知触发** 分离：

```
Signal Detection Loop（一直运行） ≠ LLM/VLM（按需运行）
```

生物大部分时间是安静的——世界稳定时沉默，只有变化才进入认知。"持续沉默"也是智能的重要部分。

### 1.4 错误路径（避免）

```
while True:
    frame = cam.read()
    result = vlm.analyze(frame)    ❌ 每帧调用大模型
```

问题：token 成本、延迟、上下文污染，全部失控。**大模型不需要每帧理解世界。**

---

## 二、系统架构总览

### 2.1 物理拓扑

```
┌─────────────────────┐         ┌──────────────────────────────────┐
│   ESP32（采集终端）   │  推流    │          Server（Runtime Kernel）  │
│                     │ ──────→ │                                  │
│  - 摄像头           │ 视频流   │  L1: Stream Ingest               │
│  - 麦克风           │ 音频流   │  L2: Signal Detection            │
│  - WiFi             │         │  L3: Scene State + State Machine  │
│                     │         │  L4: Attention Engine             │
│  仅采集，不做       │         │  L5: Episodic Memory              │
│  任何本地推理       │         │  L6: Cognition Trigger → VLM/LLM  │
└─────────────────────┘         └──────────────────────────────────┘
```

**ESP32 的定位**：纯粹的传感器，只负责采集和推流。运行时大脑在服务器端，ESP32 是可替换的感官。

开发阶段：Mac 本地摄像头 / EZVIZ 云台摄像头 (RTSP) 作为 L1 输入源。

### 2.2 六层 Runtime Pipeline

| 层级 | 名称 | 频率 | 说明 |
|------|------|------|------|
| L1 | **Stream Ingest** | 持续 | RTSP/本地摄像头 + 麦克风。支持 EZVIZ 360° 云台摄像头 (RTSP 直连) |
| L2 | **Signal Detection** | 5-15 FPS | 帧差分 + 轻量 ONNX 模型（人脸/物体检测）+ VAD |
| L3 | **Scene State** | 持续维护 | 当前世界状态 + 状态机（Idle/Focus/Alert/Sleep） |
| L4 | **Attention Engine** | 持续维护 | 动态加权评分 + 注意力衰减 + 权重自演化 |
| L5 | **Episodic Memory** | 持续维护 | 滚动情景记忆 + 压缩 |
| L6 | **Cognition Trigger** | 阈值触发 | importance > threshold → 稀疏调用 LLM/VLM |

### 2.3 运行时状态机

```
┌─────────────┐
│  Idle       │  环境长时间无变化，低注意力，沉默
└──────┬──────┘
       │ 有人进入 / 运动 / 声音
       ▼
┌─────────────┐
│  Focus      │  用户交互中，高注意力，低触发阈值
└──────┬──────┘
       │ 用户离开 / 超时
       ▼
┌─────────────┐
│  Idle       │  恢复空闲
└──────┬──────┘
       │ 异常事件（摔倒、大声音等）
       ▼
┌─────────────┐
│  Alert      │  异常事件，最高敏感度
└─────────────┘

  Sleep State（夜间/无人）：降低检测频率，提高触发阈值
```

状态影响注意力乘数：

| 状态 | 乘数 | 效果 |
|------|------|------|
| Idle | ×1.0 | 基准 |
| Focus | ×0.7 | 更敏感（阈值降低） |
| Alert | ×0.5 | 非常敏感 |
| Sleep | ×2.0 | 更迟钝（阈值提高） |

---

## 三、L2 检测层设计

### 3.1 为什么不用 OpenCV 传统视觉

当前项目是 Runtime Kernel 的验证版。ESP32 只做采集推流，所有处理在服务器端。

OpenCV 传统方案的问题：
- MOG2 背景分割：需要长时间学习背景，场景变化后误报多
- Haar Cascade 人脸检测：精度低，侧脸、遮挡、光照变化容易漏
- 轮廓差分：无法区分物体类别，"猫跳过"和"人走过"一样

服务器有 GPU，应该用更好的模型。

### 3.2 L2 方案：帧差分 + 轻量 ONNX 模型

| 检测类型 | 方案 | 模型大小 | 推理速度 | 能力 |
|----------|------|----------|----------|------|
| **画面变化** | numpy/PIL 帧差分 | 0 | <1ms | "变没变" |
| **人脸检测** | YuNet ONNX | ~85KB | ~5ms GPU | 多人脸、侧脸、关键点 |
| **物体检测** | YOLOv8-nano ONNX | ~6MB | ~10ms GPU | 80类通用物体 |
| **语音活动** | Silero VAD ONNX | ~1.7MB | ~3ms CPU | 是否有人说话 |

**帧差分是第一道门卫**——两张相邻帧做 `absdiff`，超过阈值才唤醒后续 ONNX 模型。大部分时间画面静止，帧差分直接跳过，不浪费 GPU 推理。

对比：

| 维度 | OpenCV 方案 | ONNX 方案 |
|------|------------|-----------|
| 人脸精度 | ~70%（Haar） | ~95%（YuNet） |
| 物体分类 | 无 | 80 类 |
| 推理硬件 | CPU only | GPU 原生支持 |
| 误报率 | 高 | 低 |
| 依赖体积 | opencv-python ~50MB | onnxruntime ~20MB |

### 3.3 帧差分实现（纯 numpy，零依赖）

```python
def frame_changed(prev: np.ndarray, curr: np.ndarray,
                  threshold: int = 25, min_pixels: int = 500) -> bool:
    """两张相邻帧是否有显著变化。最廉价的门卫。"""
    diff = np.abs(curr.astype(np.int16) - prev.astype(np.int16))
    motion_mask = (diff > threshold).any(axis=2)
    return np.count_nonzero(motion_mask) > min_pixels
```

---

## 四、核心数据结构

### 4.1 Scene State（场景状态）

这是"当前世界状态"，不是长期记忆。

```python
scene = {
    "people": [],              # [{bbox, landmarks, track_id}]
    "objects": [],             # [{class_name, bbox, confidence}]
    "motion_level": 0.0,       # 0.0 ~ 1.0（指数平滑）
    "voice_activity": False,
    "last_event": None,
    "attention_targets": [],   # 当前关注目标
    "user_present": False,
    "user_speaking": False,
    "desk_changed": False,
    "last_motion_time": 0.0,
}
```

**Scene State ≠ Memory**。State 是"现在"，Memory 是"过去发生了什么"。

### 4.2 Attention Weights（DNA 级注意力先验）

这不是写死的业务规则，而是**"什么值得关注"的重要性偏置**。很像动物的 DNA——不知道"杯子是什么"，但知道大运动值得注意、声音值得注意、人脸值得注意。

```python
BASE_WEIGHTS = {
    "human_face":      0.9,
    "voice_detected":  0.95,
    "large_motion":    0.6,
    "new_object":      0.5,
    "person_entered":  0.85,
    "person_left":     0.5,
    "sustained_gaze":  0.7,
    "background_noise": 0.1,
}
```

### 4.3 Dynamic Attention（动态注意力竞争）

事件数量不固定——可变注意力 Top-K：

```python
def score_events(events, weights, state_multiplier):
    scored = []
    for event in events:
        base = weights.get(event["type"], 0.1)
        bonus = context_bonus(event)       # 上下文加成
        time_bonus = time_of_day_bonus()   # 时段加成
        score = min(1.0, base + bonus + time_bonus) * state_multiplier
        scored.append({"type": event["type"], "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:TOP_K]
```

### 4.4 Attention Decay（注意力衰减）

模拟生物注意力——没有持续刺激则慢慢消散：

```python
def decay(prev_score, delta_t):
    return prev_score * (0.95 ** delta_t)
```

### 4.5 角色驱动的注意力

不同角色 = 不同的注意力偏置。角色决定的是**关注什么**，而不是具体动作：

| 角色 | 高权重事件 | 行为倾向 |
|------|-----------|----------|
| 迎宾 | 人靠近、注视、门口区域 | 主动欢迎、引导 |
| 安保 | 异常运动、深夜活动、限制区域 | 警告、跟踪、上报 |
| 陪伴 | 用户情绪、长时间沉默、语气变化 | 主动聊天、安慰 |

这不是写死 `if person_enter: say("欢迎光临")`，而是**目标驱动注意力**：
- 目标 → 决定关注什么
- 注意力 → 决定什么触发认知
- 认知 → LLM 自主决策行为

---

## 五、运行流程

### 5.1 主循环

```python
while True:
    # L1: 接收流（开发阶段：本地摄像头；生产：ESP32推流）
    frame = ingest.read_video()
    audio = ingest.read_audio()

    # L2: Signal Detection
    if frame_diff(prev, frame):          # 门卫：画面变了吗？
        faces = face_detector.detect(frame)      # ONNX 人脸
        objects = object_detector.detect(frame)  # ONNX 物体
    else:
        faces, objects = [], []

    voice = vad.detect(audio)             # Silero VAD

    # L3: Scene State + State Machine
    scene.update(people=faces, motion=has_motion,
                 voice=voice, objects=objects)

    # L4: Attention
    events = extract_events(scene)
    scored = attention.score(events, scene.state_multiplier)

    # L5: Memory（重要事件记录）
    for ev in scored:
        if ev["score"] >= effective_threshold:
            memory.push(ev)

    # L6: Cognition Trigger（稀疏）
    for ev in scored:
        if ev["score"] >= effective_threshold:
            cognition.push(scene.snapshot(), frame if needs_vision else None)

    # 关键：没变化时不输出任何东西
    prev = frame
```

### 5.2 认知线程（低频、独立）

```python
# 独立线程，不阻塞感知循环
while True:
    task = cognition_queue.pop()
    context = memory.get_context()

    if task.needs_vision:
        result = vlm.analyze(task.frame, prompt=..., context=context)
    else:
        result = llm.chat(prompt=..., context=context)
```

### 5.3 触发条件

仅在以下情况才调用 LLM/VLM：

1. **有人进入画面** → VLM 描述
2. **桌面出现新物体** → VLM 识别
3. **用户注视摄像头** → attention event
4. **大幅度运动** → 判断是否异常
5. **用户说话** → LLM 分析意图

---

## 六、记忆架构

### 6.1 你真正需要的不是"几万行 memory"

而是**记忆结构（Memory Architecture）**，不是记忆内容。

### 6.2 记忆分层

```
Episodic Memory（情景记忆）
  ├── 刚刚发生了什么
  └── 时间线事件

Semantic Memory（语义记忆）
  ├── 用户喜欢什么
  └── 抽象后的经验

Procedural Memory（程序记忆）
  ├── 如何完成任务
  └── 行为模式

World State（世界状态）
  └── 当前环境（不持久化）
```

### 6.3 关键原则

- **Memory Compression > Memory Size** —— 人类不记住每一帧，只提取模式
- **Pattern Formation**：系统从经验中学习关联，而非硬编码
  - 例如：系统发现"用户拿起杯子 ≈ 准备离开"，不是 `if cup_pickup:`，而是自己形成关联
- **Experience Replay**：一天几百万帧，压缩成几个重要经验
- **权重变化 > 规则增加**：成长不是规则变多，而是权重漂移

### 6.4 事件记录格式

```python
{
    "time": "20:31",
    "event": "user_near_desk",
    "context": "desk",
    "intention": "using_desk",
    "importance": 0.65,
}
```

---

## 七、意图推断

### 7.1 Intention Engine

介于 L4（Attention）和 L6（Cognition）之间。从场景状态 + 注意力事件推断用户在做什么。

```
场景上下文 + 注意力事件 → 意图推断（规则链） → 意图 + 优先级 → 认知调度
```

### 7.2 意图类型 & 优先级

| 意图 | 触发条件 | 认知优先级 |
|------|---------|-----------|
| emergency | 极端运动 + 无人 | 1.0（最高） |
| speaking | 语音 + 人脸 | 0.9 |
| looking_at_camera | 人脸 + 低运动 + 持续注视 | 0.8 |
| approaching | 用户刚出现 | 0.7 |
| gesturing | 人脸 + 高运动 | 0.6 |
| using_desk | 物体变化 + 中等运动 | 0.4 |
| leaving | 用户消失 | 0.3 |
| ambient | 无特别事件 | 0.1 |

---

## 八、从"识别图像"到"维护一个持续世界"

### 8.1 你经历的几个阶段

**第一阶段（很多人停在这里）**：
```
输入 → AI → 输出
本质：工具调用
```

**第二阶段（本项目）**：
```
环境 → 持续感知 → 状态变化 → attention → 事件 → cognition
本质：持续运行体（Persistent Runtime）
```

### 8.2 真正的"被动感"来自

- **大部分时间沉默**：没变化时不输出任何东西
- **State Transition**：IDLE → PERSON_DETECTED → INTERACTION → PERSON_LEFT → IDLE
- **Attention Persistence**：用户进入后 focus=user，即使下一帧没变化，系统仍维持关注状态
- **Temporal Continuity**：系统不是"一帧一帧"，而是"持续存在"

### 8.3 类比生物神经系统

| 生物系统 | Runtime 对应 | 特性 |
|----------|-------------|------|
| 低级反射（视觉皮层、听觉皮层） | Signal Detection (L2) | 快速、毫秒级、持续 |
| 高级认知（前额叶） | LLM/VLM Cognition (L6) | 慢速、昂贵、稀疏 |
| 注意力竞争 | Attention Engine (L4) | 动态选择关注目标 |
| DNA（先天偏置） | Attention Weights | 给定注意力先验 |
| Experience（后天经验） | Memory + 权重演化 | 长期学习 |

生物不是 `if else` 生存，而是**注意力竞争**。各种感知输入竞争注意力，最高优先级获得认知资源。

---

## 九、项目目录结构

```
vision-dev/
├── config.py                     # 配置
├── requirements.txt              # 依赖
├── vision-dev-doc.md             # 本架构文档
├── README.md
└── runtime/
    ├── main.py                   # 主入口 & 主循环
    ├── ingest/
    │   ├── stream.py             # L1: 流接收（本地摄像头 / EZVIZ RTSP / ESP32 推流）
│   ├── ptz_control.py         # L1: 云台控制 (已废弃, 被 scripts/ 替代)
│   ├── ptz_server.py          # L1: WebSocket PTZ 中继 (备用)
│   ├── ptz_bridge.py          # L1: Playwright PTZ 桥 (已废弃)
│   ├── face_tracker_v7.py     # 🆕 人脸追踪 + PTZ 跟随 (主线)
│   ├── face_tracker_ws.py     # WebSocket 版追踪 (已废弃)
│   ├── calibrate_ptz.py       # 🆕 PTZ 步长自动校准
│   └── ptz_test.html          # PTZ Web SDK 测试页
    │   └── codec.py              # 解码
    ├── perception/
    │   ├── frame_diff.py         # L2: 帧差分（门卫）
    │   ├── face_detection.py     # L2: 人脸检测 ONNX（YuNet）
    │   ├── object_detection.py   # L2: 物体检测 ONNX（YOLO-nano）
    │   └── vad.py                # L2: 语音活动检测（Silero VAD）
    ├── scene/
    │   └── state.py              # L3: 场景状态 + 状态机
    ├── attention/
    │   └── engine.py             # L4: 注意力引擎
    ├── intention/
    │   └── engine.py             # 意图推断引擎
    ├── memory/
    │   └── episodic.py           # L5: 情景记忆
    ├── cognition/
    │   └── trigger.py            # L6: 认知触发 + LLM/VLM 调用
    ├── eventbus/
    │   └── bus.py                # 事件总线
    └── utils/
        ├── vision_api.py         # VLM API 封装
        └── model_loader.py       # ONNX 模型加载 & 预热
```

### 开发阶段 vs 生产阶段的差异

| 模块 | 开发阶段（Mac 本地） | 生产阶段（ESP32 + Server） |
|------|---------------------|--------------------------|
| L1 输入 | `cv2.VideoCapture(0)` | 网络推流（WebRTC/RTSP） |
| L2 人脸 | YuNet ONNX | 同左 |
| L2 物体 | YOLO-nano ONNX | 同左 |
| L2 语音 | 本地麦克风 | 同左（音频流） |
| L3-L6 | 完全一致 | 完全一致 |
| LLM/VLM | 本地 zero-token 代理 | 同左或云端 API |

**关键设计**：L2 以上全部不变，只有 L1 的输入源从本地设备切换为网络推流。

---

## 十、MVP 路线图

### Phase 1：Runtime Kernel 验证 ✅ 已完成

- [x] Mac 本地摄像头采集
- [x] 帧差分过滤器（numpy）
- [x] 人脸检测 ONNX（YuNet）
- [x] 物体检测 ONNX（YOLO11）
- [x] 语音活动检测（Silero VAD）
- [x] Scene State 维护 + 状态机（Idle/Focus/Alert/Sleep）+ 离开防抖 + Emergency 时间累积
- [x] Attention Engine（固定权重 + 衰减 + 凝视会话跟踪）
- [x] Intention Engine（8 种意图推断）
- [x] Episodic Memory（滚动缓冲 + 压缩）
- [x] Event Bus

### Phase 2：认知接入 ✅ 已完成

- [x] Cognition Trigger → LLM/VLM 管道
- [x] 事件队列 + 独立认知线程
- [x] 多后端 VLM 轮询（zero-token + DashScope）
- [x] 认知节流（冷却 + 去重 + 重要性门）

### Phase 3：内部行为循环 ✅ 已完成

- [x] Focus Manager（持久化焦点 + 惯性 + 切换阈值）
- [x] Presence Tracker（身份跟踪 + Novelty 门控 + 表情检测）
- [x] Behavior System（6 状态：SCAN/OBSERVE/REST/TRACKING/ENGAGED/ACCOMPANYING）
- [x] Self-narrative（内部独白，不调用 LLM）
- [x] Telemetry（每分钟生命体征 + 高层事件时间线）
- [x] 3.5 小时稳定运行验证

### Phase 4：多源输入 & 云台控制 ✅ 已完成（支线）

- [x] EZVIZ 360° 云台摄像头 (C6C) RTSP 直连拉流
- [x] 本地 RTSP + ffmpeg 低延迟解码 (nobuffer/low_delay)
- [x] PTZ 云台控制 (云端 API start/stop, 实测延迟 ~2.5s)
- [x] PTZ 步长自动校准 (ORB 特征匹配, 最小有效 ~80ms≈10°)
- [x] YOLO 人体检测替换 YuNet 人脸 (转动时更鲁棒)
- [x] 追踪算法: 校准换算步长 + 单步决策 + 深度观察(5s) + 防振荡 + 对角移动
- [x] 4 级搜索策略 (Predict→Spiral→Zigzag→Panorama 360°+抬头)
- [x] 多线程架构 (帧读取/PTZ指令独立线程, 主线程不阻塞)
- [x] 长时间运行监控 (FPS/延迟/PTZ队列健康检查)
- [x] 音频流接入 (本地麦克风 + Silero VAD)
- [ ] ESP32 推流 (由 RTSP 直连替代, 暂缓)

### Phase 5：Active Observation ✅ Phase 5.1 完成

**Active Observation Pipeline**:
```
Perception → Attention → Interest → Curiosity Queue → PTZ → Observe Again
                ↑                        ↓
                └── global vigilance ────┘
```

**已完成**:
- [x] Interest Engine — 实体级兴趣追踪 (entity/region, 衰减, 刷新, 回访反馈)
- [x] Curiosity Queue — 不确定性驱动 (interest × uncertainty × freshness - movement_cost)
- [x] World Anchor — entity vs region 分离, 连续失败防执念 (5次→遗忘)
- [x] RevisitController — Interest→PTZ→YOLO→confirm 闭环 (零 LLM)
- [x] CameraState — ego motion 感知 (FrameDiff 暂停, 死推算 Pose)
- [x] Behavioral Telemetry — 长期运行注意力生态观察 (30min 快照, 熵, 区域统计)
- [x] **Spatial Anchor System** — 空间锚点 + 环境基线 + 变化检测
- [x] **Environment Scanner** — 启动时全景扫描建基线 (6×3=18 观测点)

**Spatial Anchor 关键设计**:
```
YOLO + CameraState.pan/tilt → Anchor(p=30,t=0) → baseline={chair,desk}
→ 变化检测 (新增/消失/不变) → novelty signal
→ Interest 绑定到 anchors, NOT 实体
→ 复杂度 O(anchors), 不随实体数量增长
```

**Environment Scanner**:
- 360° 水平 6 位 × 3 高度 = 18 观测点
- 每点 1.5s 驻留 → ~30s 扫完
- 模仿动物行为: 进入新空间 → 环顾四周 → 建立心智地图

**待做**:
- [ ] 长测：24 小时行为生态观察 (Attention Geography 涌现)
- [ ] 注意力权重自演化
- [ ] Experience Replay
- [ ] 角色系统
- [ ] Spatial Memory (涌现地理 → 命名锚点)

## 支线：360° 云台摄像头 & 人脸追踪

> 从 Mac 内置摄像头升级到 EZVIZ C6C 360° 云台摄像头，并实现 PTZ 人脸跟随。

### 硬件

- **型号**: EZVIZ C6C (CS-C6C-3H3WFRV), 360° 水平 / 90° 垂直
- **连接**: 局域网 RTSP (需在 App 中开启 RTSP + Hik-SDK 本地服务)
- **RTSP URL**: `rtsp://admin:{验证码}@{IP}:554/h264/ch1/main/av_stream`

### 视频采集

替换 `CameraCapture` → `EZVIZCapture`, 接口兼容，`STREAM_TYPE = "ezviz"`。

```
RTSP → ffmpeg subprocess → BGR24 stdout → numpy frame
```

- 640x480 @ 5 FPS, rtsp_transport=tcp
- ffmpeg 退出码 0 = 正常 HLS 分段 → 快速重启；非零 = 完整重连

### PTZ 云台控制

经过多种方案尝试（云 API 失败、Playwright + EZUIKit 可行但重、WebSocket Bridge 验证通过），最终简化：

```
Python requests.post() → EZVIZ 云 API ptz/start → sleep(duration) → ptz/stop
```

- 需先建立 ezopen 会话（EZUIKit WebSocket）才能激活 API
- 最小步长校准: 80ms @ speed=3 ≈ 10° (ORB 特征匹配自动测量)
- 方向: 0↑ 1↓ 2← 3→ | 速度: 1-7

### 人脸追踪

`scripts/face_tracker_v7.py` — 多线程，主线程帧+检测+显示，PTZ 线程独立发指令。

**追踪参数**:
| 参数 | 值 | 说明 |
|------|-----|------|
| 步长 | 100ms + offset×0.3 (max 350ms) | 自适应: 偏离大→大步, 近→小步 |
| 观察期 | 3.0s | RTSP 缓冲延迟, 等待画面刷新 |
| 反向锁 | 5.0s | 禁止立即回头, 防振荡 |
| 死区 | ±15% | 脸在中间不推 |
| 最大步数 | 3 | 同方向最多 3 步 |

**搜索策略** (逐级升级):
1. 🔮 Predict — 3 次近距预测, 300ms/次
2. 🌀 Spiral — 6 次螺旋扩大, 半径 0.2→1.2s
3. ⚡ Zigzag — 之字扫描, 700ms/次
4. 🌐 Panorama — 全景横扫, 1.5s/次

**运行**: `python scripts/face_tracker_v7.py`

### 校准工具

`scripts/calibrate_ptz.py` — ORB 特征匹配自动测量不同步长的场景位移，确定最小有效步长。

### 备选方案 (已废弃)

- `scripts/ptz_server.py` — WebSocket 中继, Python→Chrome→EZUIKit→摄像头的 PTZ 桥, 已验证可用但维护成本高
- `scripts/ptz_bridge.py` — Playwright 无头浏览器桥, 因 EZUIKit localStorage/IndexedDB 权限问题废弃
- `scripts/ptz_control.py` — 原始云 API PTZ 封装, 后被 v7 的内联方式替代



---

## 十一、核心原则总结

```
高频感知 + 低频认知
持续状态 > 回答能力
世界状态一直存在 —— 即使没有人和它说话
ESP32 只是传感器，Runtime Kernel 才是核心
Attention Scheduling > Perception Ability
Memory Compression > Memory Size
ONNX 轻量模型做筛选，VLM 做理解
权重变化 > 规则增加 —— 成长不是代码变多，是偏置漂移
不是处理所有情况，而是维护关注机制
从"小动物级智能"开始
```

---

## 十二、为什么这个架构不同

传统 Chat Bot：

```
用户输入 → 回答
prompt → response
没有持续存在，没有环境状态，没有注意力流
```

本架构（Embodied Runtime）：

```
世界状态一直存在 → 事件发生 → 按需认知 → 行动/反馈
持续感知 → 状态变化 → attention spike → cognition → 回应
```

> 真正的智能不是一个模型，而是一个长期演化的系统。
> "生命感"来自持续性，而不是参数量。

---

*文档版本：v0.2*
*基于 2026-05-27 架构讨论重构*
