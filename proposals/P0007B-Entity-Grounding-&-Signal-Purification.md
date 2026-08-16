# Phase 7B: Entity Grounding & Signal Purification

## Status
Implemented (2026-07-03)

## Objective

Phase 7A 已验证：Interaction / Event / Diversity 可以形成稳定的 Importance signal。

但当前 signal 受到两类污染：

1. YOLO detection noise（误检实体进入统计，如 clock / train）
2. Entity fragmentation（同一物体被拆成多个 entity）

Phase 7B 的目标是：

> 将 Importance 计算限制在“可信 Entity 空间”，构建干净的统计基础。

不引入 Value、不引入 Memory、不改变 Interest / Curiosity / Role 逻辑。

---

## Background

当前系统：

Detection → Entity → Interaction → Importance

问题：

- detection 层存在 false positive
- entity registry 未做严格合并
- importance 统计基于 noisy entity set

导致：

- Top entity 偶尔被误检污染
- density ranking 不稳定
- cross-session stats 偏移

---

## Architecture Changes

新增 Entity Quality Layer：

Detection
   ↓
Entity Association
   ↓
Entity Registry (ENHANCED)
   ↓
Entity Quality Gate (NEW)
   ↓
Importance Engine (UNCHANGED)

---

## Design

### 1. Entity Quality Gate

新增 gating 规则：

Entity 进入 Importance 前必须满足：

- seen_count >= MIN_SEEN_THRESHOLD (建议: 3~5)
- confidence_score >= CONF_THRESHOLD (来自 detection avg confidence)
- lifecycle != CANDIDATE

伪代码：

```python
def is_valid_for_importance(entity):
    return (
        entity.seen_count >= 3 and
        entity.avg_confidence >= 0.5 and
        entity.state == "ACTIVE"
    )

### 2. Entity Merge (Lightweight)

目标：减少 fragmentation，不引入 embedding 模型。

Merge 条件：

visual_signature similarity >= threshold
label一致 或 label在alias集合中

规则：

if is_similar(entity_a, entity_b):
    merge(entity_a, entity_b)

merge策略：

interaction_count 累加
seen_count 累加
event_types union
keep higher confidence entity as primary

### 3. Noise Entity Suppression

引入轻量 noise filtering：

规则：

entity.seen_count < 2 且 confidence < 0.5 → 不进入 registry
low stability entity 不参与 importance ranking

定义：

noise_entity = (seen_count < 2 and avg_confidence < 0.5)

### 4. ACTIVE Entity Constraint

Importance 只计算 ACTIVE entity：

Lifecycle constraint：

CANDIDATE → 可见但不参与 importance
ACTIVE → 可参与 importance
LOST / FORGOTTEN → 不参与

### 5. Importance Input Filter

修改 importance pipeline：

ONLY ACTIVE + VALID entities
→ importance calculation
→ ranking
Metrics (No new Value logic)

新增 debug metrics：

1. Noise Ratio
noise_ratio = rejected_entities / total_entities
2. Merge Rate
merge_rate = merged_entities / total_entities
3. Importance Stability Index
top_k_overlap(day_n, day_n+1)
Directory Changes
Modify
entity.py
add avg_confidence
add merge()
add is_valid()
entity_registry.py
add merge logic
add quality filter
importance_engine.py
add entity filter gate
detection.py
ensure confidence propagation
Add
entity_quality.py
gating + noise detection logic
Success Criteria
Top 10 Importance entities become stable over 12–24h
YOLO mis-detections (clock/train) disappear from top list
Duplicate entities reduced > 30%
Importance ranking becomes smoother (less jitter)
No change required in Interest / Curiosity / Role system
NOT INCLUDED
No Value Engine
No Memory system
No LLM integration
No reward modeling
No vector database
No architecture change in Interest / Curiosity / Role
Key Principle

Importance can only emerge from clean entity grounding.

Phase 7B is not about intelligence.

It is about removing noise so intelligence can be observed.