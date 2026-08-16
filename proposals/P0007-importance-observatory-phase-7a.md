# P0007: Importance Observatory — Phase 7A

**Status**: Implemented (OBSERVE only, no Value formula)
**Date**: 2026-06
**Source**: chat_history6.txt
**Depends on**: P0002, P0004, P0005, P0006

---

## Objective

建立 Importance Observatory——观察"什么实体会持续引发后续行为"，收集统计数据。不是 Value Engine，不定义价值公式。先观察，再建模。

## Background

系统已完成 Interest → Curiosity → Familiarity → Role 四层。下一步的自然问题是"什么值得长期记住？"

但直接设计 Value Engine 有风险：
- 没有价值来源（没有任务目标/用户反馈/奖励机制）
- 容易出现"伪 Value"（本质只是 Familiarity 的长期版本）
- 真正的 Value 应该来自"因果密度"而非"出现频率"

因此 Phase 7A 先不开发 Value，而是收集 Interaction 统计数据。

## Core Insight

### Value ≠ Seen Often

| 度量 | 含义 | 问题 |
|------|------|------|
| seen_count | 出现次数 | 墙比人出现得多 |
| interaction_density | 每次出现引发的后续事件数 | 人 >> 墙 |
| event_diversity | 引发的不同事件类型数 | 人触发 15 种，椅子触发 1 种 |

**Value = Predictive Relevance**（预测相关性），不是 Seen Often。

### 因果密度

```
person: seen=500, interactions=1800, density=3.6
package: seen=8, interactions=41, density=5.1
chair: seen=2000, interactions=3, density=0.001
```

package 的 density 可能比 person 更高——因为它稀有且每次出现都引发关注。这才是 Value 的轮廓。

## Design

### Entity 扩展

```python
interaction_count: int = 0
event_types: set[str]
state_transition_count: int = 0
cognition_trigger_count: int = 0
speech_related_count: int = 0
tracking_count: int = 0
```

每次实体引发后续行为时递增对应计数器。

### Interaction Density

```python
importance_density = interaction_count / max(seen_count, 1)
```

仅用于统计输出，禁止用于 Runtime 决策。这是 OBSERVE，不是 DECIDE。

### Telemetry

- `importance_candidates.log` — 每 30 分钟输出 Top Interaction Density / Top Event Diversity / Top Interaction Count
- `entity_stats.db` — SQLite 跨 Session 统计持久化

### Entity Identity > YOLO Label

Entity 是主对象，label 只是属性。未来 label 从 chair → office chair → furniture 变化时，Entity 仍保持连续。

```python
Entity: ent_a1b2c3
  label: chair        # 可变
  aliases: ["office chair", "black chair"]  # 可积累
```

### YOLO World 预留

新增 Detector Interface 统一 YOLOs / YOLO World 输出：

```python
class Detector:
    def detect(frame) -> Detection

Detection:
    bbox, confidence, label
    embedding: Optional[np.ndarray]  # YOLO World 预留
```

## Boundaries

### Included
- Entity interaction_count / event_types / downstream event tracking
- importance_candidates.log（每 30 分钟）
- entity_stats.db（SQLite 跨 Session）
- Detector Interface（YOLO World 预留）
- importance_density 统计（仅日志，不参与决策）

### NOT Included
- **Value Formula** — 如 `value = a*frequency + b*duration + c*novelty`
- **"Top Value" 输出** — 当前没有 Value，只输出 "Top Interaction Density"
- Memory / Soul / Preference / Emotion / Reward Model
- Vector DB / Knowledge Graph
- Agent Planning

## Success Criteria

1. 24 小时运行后，importance_candidates.log 中出现有意义的排序
2. person / package / pet 自然浮现在 density 前列
3. chair / wall / monitor 在 density 上接近 0
4. entity_stats.db 跨 Session 持久化正常
5. 数据收集完成后，Value Engine 的设计不再需要拍脑袋

## Note

这是当前项目最"年轻"的模块。Phase 7A 成功后，Phase 7B+ 才进入真正的 Value Engine——届时基于真实数据定义公式，而非凭空设计。
