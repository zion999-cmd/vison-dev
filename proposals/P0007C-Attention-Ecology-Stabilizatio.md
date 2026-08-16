# P0007C: Attention Ecology Stabilization (Observation Protocol)

**Status**: Proposed  
**Date**: 2026-07-03  
**Depends on**: P0007B  

---

## Objective

Phase 7C 不引入任何新功能或新机制，仅定义一套**标准化观测协议（Observation Protocol）**，用于验证 Phase 7A + 7B 构建的 Attention System 是否已经进入稳定态。

核心问题：

> 在当前 Entity / Interest / Curiosity / Role / Importance / Quality Gate 体系下，系统是否已经形成“稳定注意力生态结构”。

---

## Background

Phase 7A/7B 已完成：

- Entity Grounding（去噪 + merge + lifecycle）
- Importance Signal（interaction-driven）
- Quality Gate（ACTIVE + confidence + seen_count）
- Cross-session stats persistence

但缺少：

> 对系统输出“稳定性”的统一评估标准

目前只能“看日志”，无法“判断是否收敛”。

---

## Architecture

本阶段**不修改 runtime 架构**，仅增加：


Existing System (UNCHANGED)
↓
Telemetry Layer (OBSERVATION ONLY)
↓
Stability Metrics + Reports


---

## Design

### 1. Observation Scope

只观察以下数据源（禁止修改逻辑）：

- entity_stats.db
- importance_candidates.log
- registry snapshot (ACTIVE entities only)

---

### 2. Core Metrics（必须实现）

#### 2.1 Top-K Stability (Primary Metric)

计算每日 Top-K Entity overlap：


stability_k = |TopK_day_n ∩ TopK_day_n+1| / K


建议：

- K = 10 / 20

输出：

- daily stability curve

---

#### 2.2 Entity Survival Rate

定义：


survival(entity) = active_days / total_observation_days


输出：

- survival histogram
- long-tail vs short-lived entity ratio

---

#### 2.3 Noise Persistence Index

观察：

- YOLO误检类别是否仍进入 top ranking


noise_ratio = noisy_entities_in_topK / K


目标：

- 趋近 0

---

#### 2.4 Interaction Concentration

判断系统是否“集中注意力”：


concentration = entropy(importance_distribution)


输出：

- entropy curve over time

---

### 3. Structural Emergence Check (关键)

判断是否出现“结构化注意力生态”：

观察是否出现：

- stable head entities（长期稳定 top 3-5）
- stable tail entities（长期低重要但持续存在）
- clear mid-tier fluctuation band

---

### 4. Reporting (每24h)

生成报告：


logs/attention_stability_report.json


内容包括：

- top_k stability
- survival distribution
- noise ratio
- entropy trend
- notable entity transitions

---

## Boundaries

### Included

- 只做 metrics / analysis / reporting
- 不修改 runtime logic
- 不改变 entity / interest / importance 行为
- 不引入 Value / Memory / LLM policy

---

### NOT Included (CRITICAL)

- ❌ Value Engine
- ❌ Memory system
- ❌ LLM control loop
- ❌ new scoring rules
- ❌ runtime behavior modification
- ❌ attention policy changes

---

## Success Criteria

Phase 7C 成功的标志：

1. Top-10 entity overlap ≥ 60%（跨2~3天）
2. noise_ratio → 接近 0（clock/train类消失）
3. entropy 曲线趋于稳定（不再随机震荡）
4. entity survival 出现明显长尾分布
5. 系统结构表现为稳定三层：
   - stable core (persistent entities)
   - dynamic mid-layer
   - transient noise (已被压制)

---

## Key Principle

> Phase 7C does not improve the system.
> Phase 7C determines whether the system is real.

---

## Outcome

Phase 7C 完成后，将产生唯一结论：

- YES: Attention Ecology is stable → proceed to Value
- NO: return to grounding/debug phase

---

End of P0007C