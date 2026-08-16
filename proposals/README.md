# Proposals

> ChatGPT 生成的设计文档存档。一个 Proposal = 一个开发阶段/能力的完整设计。
>
> 格式：`P000X-xxx.md`（独立阶段）或 `P000X.Y-xxx.md`（子步骤）。
>
> 状态流转：Proposed → Accepted → Implemented。
>
> 详见 [interaction/proposal-driven-development.md](../interaction/proposal-driven-development.md)

---

## 提案索引

| 编号 | 标题 | 状态 | 来源 |
|------|------|------|------|
| [P0001](P0001-project-philosophy-and-runtime-vision.md) | 项目哲学 & Runtime 架构愿景 | Implemented | chat_history.txt |
| [P0002](P0002-layered-attention-architecture.md) | Layered Attention 多层注意力架构 | Implemented | chat_history.txt, chat_history2.txt |
| [P0003](P0003-environment-model-and-spatial-anchors.md) | 环境模型 & 空间锚点 | Implemented | chat_history3.txt |
| [P0004](P0004-entity-centric-architecture.md) | Entity-Centric 架构重构 | Implemented | chat_history4.txt |
| [P0005](P0005-familiarity-engine.md) | Familiarity Engine 习惯化系统 | Implemented | chat_history5.txt, chat_history5.1.txt |
| [P0006](P0006-role-engine.md) | Role Engine 先天优先权重 | Implemented | chat_history5.txt |
| [P0007](P0007-importance-observatory-phase-7a.md) | Importance Observatory Phase 7A | Implemented | chat_history6.txt |
| [P0007B](P0007B-Entity-Grounding-&-Signal-Purification.md) | Entity Grounding & Signal Purification | Implemented | 2026-07-03 |

## 架构演进路线

```
P0001: 项目哲学
  ↓
P0002: Layered Attention（5层分离）
  ↓
P0003: 环境模型 & 空间锚点
  ↓
P0004: Entity-Centric 重构（Region → Entity）
  ↓
P0005: Familiarity Engine（习惯化）
  ↓
P0006: Role Engine（先天偏置）
  ↓
P0007: Importance Observatory（Phase 7A, 观测阶段）
  ↓
P0007B: Entity Grounding & Signal Purification（Phase 7B, 信号提纯）
  ↓
Phase 7B+: Value Engine（未来，基于干净信号）
```
