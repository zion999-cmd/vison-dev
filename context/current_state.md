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
- [x] Phase 7C: Stability Analysis（5 metrics, auto daily report）— verdict **UNSTABLE** (0/5)
- [x] P0008: Observation Intent Engine（LLM 动态 Mission Role, Persona≠Prompt, Role 可组合化）
- [x] P0008.1: Commitment / Dwell Policy（HOLD/SWITCH/RELEASE arbiter，Curiosity 与 Commitment 职责分离）
- [x] 仓库安全重建：config.py 移出 git（密钥），新增 config.example.py，远端为干净单 commit 历史

## 进行中

无

## 下一步

1. [ ] P0008.1 场景验证（Scenario A/B/C：person 20min HOLD / 新物体 SWITCH / 离开 RELEASE，需接摄像头）
2. [ ] ChatGPT 审查 P0008.1 代码
3. [ ] Mission Playground — 同一房间切换 5 个 Persona，对比注意力分布
4. [ ] Persona Divergence 指标（Jensen-Shannon Distance between persona attention distributions）
5. [ ] P0009: Scene Graph（Entity 空间关系）
6. [ ] P0010: Event Discovery（Temporal Scene）
7. [ ] P0011: Value Engine（per-event, not per-entity）

## 阻塞

无
