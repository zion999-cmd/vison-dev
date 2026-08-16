# P0004: Entity-Centric Architecture Refactor

**Status**: Implemented
**Date**: 2026-06
**Source**: chat_history4.txt
**Depends on**: P0003

---

## Objective

将系统从 Region-centric Runtime 升级为 Entity-centric Runtime。Interest 附着在实体(Entity)上而非 PTZ 位置上。为未来移动机器人（底盘移动 + PTZ 转动）做准备。

## Background

P0003 以 Region/PTZ position 为中心：`InterestTarget(region_id, pan, tilt, score)`。这在固定摄像头阶段成立，但不适用于：
- PTZ 漂移和云台误差
- 未来移动机器人（无绝对世界坐标）

核心洞察：**Interest 不应该附着在位置上，应该附着在实体上。PTZ 只是观察工具，不是世界模型。**

## Design

### Detection ≠ Entity

新增 Entity Association 层：

```
Detection (YOLO/YuNet)
    ↓
Entity Association (HSV signature matching)
    ↓
Entity Registry
    ↓
Interest Engine
```

### Entity 数据结构

```python
Entity:
    entity_id          # 身份主键
    entity_type        # YOLO label（附属信息）
    visual_signature   # HSV histogram matching
    last_bbox
    first_seen, last_seen
    seen_count
    interest_score, uncertainty_score
    consecutive_misses
    status             # CANDIDATE → ACTIVE → LOST → FORGOTTEN
```

### Entity 生命周期

```
CANDIDATE (5 sightings to promote)
    ↓
ACTIVE
    ↓
LOST (30 misses)
    ↓
FORGOTTEN (150 misses)
```

### PTZ 角色调整

| 旧角色 | 新角色 |
|--------|--------|
| PTZ = 定位系统 | PTZ = 主动视觉搜索器 |
| `revisit(region)` | `revisit(entity)` → search entity's last observed pose |

Revisit 是 Search + Confirm，不是 MoveToCoordinate。

### 兼容未来移动机器人

Interest、Memory、Curiosity 不得依赖固定坐标，必须依赖 Entity Identity。pan/tilt 仅作为 `last_observed_pose` 辅助信息。

## Boundaries

### Included
- Entity dataclass + 生命周期
- EntityRegistry（signature matching + merge）
- Detection → Entity association pipeline
- Curiosity queue 基于 Entity 排序

### NOT Included
- CLIP/YOLO World embedding（embedding 字段预留，暂用 HSV）
- Vector DB 存储
- 跨摄像头 Entity 匹配

## Success Criteria

1. Entity 跨越 PTZ 运动保持身份连续
2. Interest/Curiosity 不依赖 pan/tilt 坐标
3. Entity 生命周期正确流转（CANDIDATE→ACTIVE→LOST→FORGOTTEN）
4. 系统准备好未来接入移动底盘
