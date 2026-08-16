# Current State

> 当前开发进度和状态。每次会话结束更新。

## 版本

0.1.0

## 已完成

- [x] L1-L6 感知管线（Camera → Detection → SceneState → Attention → Memory → Cognition）
- [x] Layered Attention（Interest + Curiosity + Familiarity + Role）
- [x] Entity 系统（HSV signature matching, CANDIDATE→ACTIVE→LOST→FORGOTTEN）
- [x] PTZ 控制（Arduino SG90, sweep→stay→track→explore）
- [x] Importance Phase 7A（Observatory only, 纯记录无公式）
- [x] Phase 7B: Entity Grounding & Signal Purification（Quality Gate + Merge + Noise Detection）
- [x] Phase 7C: Stability Analysis（5 metrics, auto daily report）
- [x] P0008: Observation Intent Engine（LLM 动态 Mission Role, Persona≠Prompt, Role 可组合化）
- [x] proposals/ — 9 个 Proposal（P0001–P0007C + P0008）

## 进行中

无

## 下一步

1. [ ] Mission Playground — 同一房间切换 5 个 Persona，对比注意力分布
2. [ ] Persona Divergence 指标（Jensen-Shannon Distance between persona attention distributions）
3. [ ] P0009: Scene Graph（Entity 空间关系）
4. [ ] P0010: Event Discovery（Temporal Scene）
5. [ ] P0011: Value Engine（per-event, not per-entity）

## 阻塞

无
