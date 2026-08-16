# P0002: Layered Attention Architecture

**Status**: Implemented
**Date**: 2026-05–2026-06
**Source**: chat_history.txt, chat_history2.txt
**Depends on**: P0001

---

## Objective

建立从 Perception 到 Cognition 的多层注意力体系，每一层回答一个独立问题，层与层之间职责严格分离。

## Background

P0001 定义了 Attention Engine 的概念，但单层 attention 无法处理"新东西 vs 熟悉东西 vs 重要的东西"这些不同维度。需要将注意力分解为多个独立层，每层有明确职责。

## Architecture

```
Detection (YOLO + YuNet + VAD)
    ↓
┌────────────────────────────────────────────┐
│ Layer         │ Timescale   │ Question      │
├───────────────┼─────────────┼───────────────┤
│ Interest      │ seconds     │ What changed? │
│ Curiosity     │ minutes     │ Worth revisit?│
│ Familiarity   │ min–hours   │ Seen enough?  │
│ Role          │ innate      │ Who matters?  │
│ Importance    │ hours–days  │ What causes   │
│ (Phase 7A)    │             │ downstream    │
│               │             │ events?       │
└────────────────────────────────────────────┘
    ↓
PTZ Revisit (sweep → stay → track → explore)
```

## Design

### P1 → P2 → P3 优先级

**P1: Stable Presence** — 系统先学会"安静地存在"：
- BehaviorState.ACCOMPANYING（人已存在 >20s，novelty <0.2）
- 降低 attention_gain ×0.4，允许 attention drift
- "偶尔看看别的地方"极大提升真实感

**P2: Environment-aware Runtime** — 世界进入系统：
- YOLO 真正接入（语义检测）
- Object Focus（新物体可以成为 focus target）
- Sound Saliency（不只是 VAD，而是声音"异常"检测）
- Multi-modal Attention Fusion（视觉+声音联合提升 importance）

**P3: Ambient Rhythm** — 表演层（后做）：
- ambient tick, thought refresh, random scan
- 本质是表演层，不是世界建模层

### 好奇心公式

```
curiosity = interest × uncertainty × freshness × (1−familiarity) × role − movement_cost
```

每个因子独立可调，职责不重叠。

### Architecture Fix > Parameter Fix

核心原则：如果一个问题需要调整阈值才能解决，先怀疑架构缺失。Familiarity 层就是这样被发现的——不是因为 curiosity 参数不对，而是缺少 habituation 机制。

## Boundaries

### Included
- 5 层分离的 attention 模块
- 每层独立计算，组合排序
- PTZ 决策基于复合 curiosity score

### NOT Included
- P3 (Ambient Rhythm) — 表演层，等世界建模层稳定后
- Memory / Soul / Emotion — 长期系统
- Value Engine — 等 Importance 数据积累后

## Success Criteria

1. 5 层全部独立运行，职责不重叠
2. Curiosity 自然迁移（不盯着同一个目标）
3. PTZ 出现主动探索行为
4. Familiarity 不失控（分布健康，不全为 0 或全为 1）
