# P0001: Project Philosophy & Runtime Architecture Vision

**Status**: Implemented
**Date**: 2026-05
**Source**: chat_history.txt

---

## Objective

定义项目的核心哲学和 Runtime 架构方向：从"工具调用型 AI"升级为"持续存在的环境驱动型 Runtime"。

## Background

项目启动时的关键认知：真正的智能体不是"更强的模型"，而是"持续存在"——维护世界状态、注意力流、时间连续性。ESP32 摄像头 + 本地感知 + 注意力引擎 + 稀疏认知触发。

## Architecture

```
Camera (5FPS)
    ↓
Perception Loop
    ↓
Scene State (world_state)
    ↓
Delta Detection (只关注变化)
    ↓
Attention Engine (动态权重竞争)
    ↓
Event Trigger (importance > threshold)
    ↓
LLM/VLM (稀疏调用)
```

## Design

### 核心理念：Attention Prior（注意力先验）

不写死规则（`if person_enter: say("欢迎")`），而是定义"什么值得关注"：

```python
attention_weights = {
    "human_face": 0.9,
    "voice_detected": 0.95,
    "large_motion": 0.6,
    "new_object": 0.4,
    "background_change": 0.1
}
```

系统行为从 attention competition 中浮现，而非从 if-else 中执行。

### 四种 Runtime State

- **Idle State**: 低注意力，环境稳定
- **Focus State**: 用户交互中
- **Alert State**: 异常事件
- **Sleep State**: 环境长期无变化

### "被动感"的来源

不是"一直识别"，而是"一直等待值得关注的变化"。
系统大部分时间沉默，只有世界变化时才反应。

### 开发策略

先在本地（USB Camera + Mic）验证 Runtime 架构，再接入 ESP32/MCP。
Runtime Core 是核心，硬件只是外设。

## Boundaries

### Included
- 持续感知循环（5FPS）
- Scene State 维护
- Delta Detection（变化检测）
- Attention Engine（动态权重 + 衰减）
- Event-driven 认知触发

### NOT Included
- 直接接 ESP32（先本地验证）
- MCP 协议（Runtime 验证后再加）
- 完整人格/情感系统
- 复杂对话能力

## Success Criteria

1. 系统持续运行，大部分时间沉默
2. 只有世界变化时才输出事件
3. 出现"等待感"和"关注迁移"行为
