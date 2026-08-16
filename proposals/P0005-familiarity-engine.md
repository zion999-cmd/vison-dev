# P0005: Familiarity Engine — Habituation System

**Status**: Implemented
**Date**: 2026-06
**Source**: chat_history5.txt, chat_history5.1.txt
**Depends on**: P0002, P0004

---

## Objective

让系统学会"这个东西我已经看腻了"。新增 Familiarity Engine，解决 Interest→Curiosity 中缺少 habituation（习惯化）机制的问题。

## Background

当前系统已知"什么变了"（Interest）和"什么值得再看"（Curiosity），但缺少"这个东西是不是已经非常熟悉了？"。导致椅子、显示器等静态物体持续消耗注意力——不是因为它们重要，而是因为缺少 habituation。

## Design

### Familiarity ≠ Value

| 实体 | familiarity | value | 说明 |
|------|------------|-------|------|
| 主人 | 1.0 | 1.0 | 天天看，但很重要 |
| 椅子 | 1.0 | 0.1 | 天天看，不重要 |
| 陌生人 | 0 | ? | 第一次见，价值未知 |
| 快递箱 | 0 | ? | 第一次见，价值未知 |

Familiarity 单独建模，不要混入 Value。

### Entity 扩展

```python
session_seen_count: int = 0
first_seen_in_session: float
last_seen_at: float
familiarity_score: float = 0.0
```

### Familiarity 计算公式

```python
familiarity = 1 - 1 / (math.log(session_seen_count + 1) + 1)
```

特点：第 1 次=0，10 次≈0.49，100 次≈0.68，1000 次≈0.82——增长逐渐变慢，符合习惯化过程。

### Curiosity 公式修改

```python
# 旧
score = interest × uncertainty × freshness

# 新
score = interest × uncertainty × freshness × (1 − familiarity)
```

效果：新实体优先级自然升高，反复出现的老实体自然下降，无需硬规则。

### 职责分离

- **Interest** 继续负责"发生变化"
- **Familiarity** 负责"已经看过很多次"
- **Curiosity** 消费两者的结果

### Session 范围

Runtime 启动视为新 Session，全部 familiarity 清零。暂不跨 Session 持久化。

## Boundaries

### Included
- `runtime/familiarity/engine.py`
- Entity 新增 session_seen_count / familiarity_score
- Curiosity 公式加入 (1−familiarity) 因子
- Familiarity Distribution telemetry（0~0.2, 0.2~0.4, ...分桶统计）

### NOT Included
- Memory / Value Engine / Vector DB
- 跨 Session 持久化
- 硬规则（如 `if familiarity > x: stop_revisit()`）

## Success Criteria

1. 运行 30-60 分钟，Top Familiar Entities 为静态物体（显示器、椅子、键盘）
2. 新出现的人保持低 Familiarity
3. Curiosity Queue 自然偏向新实体
4. PTZ 不再反复回访已观察数百次的目标
5. Familiarity Distribution 健康（不全部 0 或全部 >0.9）
