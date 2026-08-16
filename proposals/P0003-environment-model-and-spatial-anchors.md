# P0003: Environment Model & Spatial Anchors

**Status**: Implemented (evolved into Entity System, see P0004)
**Date**: 2026-06
**Source**: chat_history3.txt
**Depends on**: P0002

---

## Objective

让系统先认识环境，再关注变化。避免"把所有 YOLO 检测到的物体全塞进 Interest Engine"导致的信息爆炸。

## Background

当前 Interest 系统将所有 YOLO 实体直接进入 Interest，导致：
- 静态物体（显示器、键盘、椅子）uncertainty ≈ 0
- 但 Interest 仍可能触发
- 实体数量无上限增长

需要从"实体驱动"转向"环境基线驱动"。

## Design

### Emergent Geography vs Handcrafted Geography

**不推荐**：硬编码 door_region, desk_region, window_region
**推荐**：RegionAnchor(pan, tilt) → 系统自己学出哪个位置有价值

```python
class RegionAnchor:
    id
    pan, tilt
    visit_count
    interest, uncertainty
    last_seen
```

### Environment → Baseline → Novelty → Interest

```
第一次巡逻: Region A 看到 {monitor, keyboard}
→ 记录 baseline

第二次巡逻: Region A 看到 {monitor, keyboard, cup}
→ cup 是新增 → Novelty ↑ → Interest ↑
```

### YOLO 实体 → 空间锚点（方案 B）

YOLO 检测到的实体不直接作为锚点，而是**生成观察位置**：

```
monitor @ pan=15, tilt=-5 → anchor_001 (tags=["monitor"])
chair @ pan=-40, tilt=0 → anchor_002 (tags=["chair"])
```

实体变化和空间位置分离：杯子被拿走，cup_1 失效，但 anchor 位置仍可回访。

### 复杂度控制

| 驱动方式 | 复杂度 |
|---------|--------|
| 实体驱动 | ≈ 实体数量（无上限） |
| 区域驱动 | ≈ 区域数量（恒定，如 12 个 PTZ 观察位） |

## Boundaries

### Included
- RegionAnchor 数据结构
- 环境 Baseline 建立
- PTZ 位置 → 锚点映射
- 变化检测 vs 基线比较

### NOT Included
- "门""窗""桌面"等硬编码语义标签
- 跨房间导航（移动机器人阶段）
- 3D 空间建模

## Success Criteria

1. 系统先巡扫环境建立 baseline
2. 只有 baseline 变化时才产生 interest
3. 实体复杂度不随运行时间线性增长

## Note

此 Proposal 的设计思想后来被 P0004 (Entity-Centric Architecture) 吸收和超越。Region Anchor 的概念演化为 Entity 的 `last_observed_pose` 字段——位置成为 Entity 的属性而非主键。详见 P0004。
