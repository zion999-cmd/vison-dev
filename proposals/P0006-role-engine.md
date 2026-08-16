# P0006: Role Engine — Innate Priority Weights

**Status**: Implemented
**Date**: 2026-06
**Source**: chat_history5.txt
**Depends on**: P0002, P0005

---

## Objective

给系统"先天偏置"——角色决定关注什么。新增 Role Engine，让不同角色（家用助手、巡检机器人、迎宾）有不同的 Attention Profile。

## Background

Familiarity 解决"我看腻了吗？"，但还有一个问题："我应该关心谁？"

新人不一定比主人更重要。Familiarity 无法区分"陌生人"和"主人"——两者 familiarity 都低，但价值完全不同。需要 Role（先天偏置）来补充。

## Architecture

```
Role（先天）: 我应该关心谁？
    ↓
Interest: 发生变化了吗？
    ↓
Curiosity: 值得再看吗？
    ↓
Familiarity: 已经看腻了吗？
    ↓
Value（后天）: 值得长期记住吗？
```

## Design

### Role Profile 配置化

```yaml
# home_assistant_role.yaml
home_assistant:
  person: 1.0
  voice: 0.9
  pet: 0.8
  package: 0.6
  chair: 0.1
  wall: 0.0

security:
  person: 0.9      # 陌生人
  motion: 1.0      # 异常运动
  night_activity: 1.0
  restricted_area: 1.0
```

### 加权逻辑

```python
effective_interest = interest × role_weight
```

Role 不改变 Interest 本身，而是调节 Interest 在 Curiosity 排序中的权重。

### 31 COCO 类别优先级

系统内建 31 个 COCO 类别的默认 role weight。person=1.0 最高，furniture 类（chair/bench/couch）=0.1，background（wall/floor）≈0。

## Boundaries

### Included
- `runtime/role/engine.py`
- YAML 配置文件（home_assistant, security, companion 预设）
- 31 COCO 类别默认权重
- `effective_interest = interest × role_weight`

### NOT Included
- Value Engine（这是"先天"，不是"后天学习"）
- Personal Preference（个体学习，Phase 7B+）
- 动态 Role 切换（单次 Runtime 一个 Role）

## Success Criteria

1. Person consistently outranks furniture（已验证：person fam=0.86 > chair fam=0.00）
2. wall/floor 类实体 interest 被压制接近 0
3. 切换 Role profile 可以改变关注行为
