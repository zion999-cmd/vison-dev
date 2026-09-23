# Current State

> 当前开发进度和状态。每次会话结束更新。

## 版本

0.1.0

## 基线状态

| 项 | 值 |
|----|-----|
| Code regression | **281 passed, 0 failed**（`conda run -n vision-dev python -m pytest -q`） |
| Hardware baseline | **NOT validated** —— 当前所有结论均来自单元测试与受控复现，未在实机运行验证 |
| 分支 | `fix/frame-diff-and-dead-code`（未 merge、未 push） |

已发现问题的完整登记（FIXED / OPEN-CONFIRMED / VERIFY / DEFERRED）见 [known_issues.md](known_issues.md)。

## 已完成

- [x] L1-L6 感知管线（Camera → Detection → SceneState → Attention → Memory → Cognition）
- [x] Layered Attention（Interest + Curiosity + Familiarity + Role）
- [x] Entity 系统（HSV signature matching, CANDIDATE→ACTIVE→LOST→FORGOTTEN）
- [x] PTZ 控制（Arduino SG90, sweep→stay→track→explore）
- [x] Importance Phase 7A（Observatory only, 纯记录无公式）
- [x] Phase 7B: Entity Grounding & Signal Purification（Quality Gate + Merge + Noise Detection）
- [x] Phase 7C: Stability Analysis（5 metrics, auto daily report）— verdict **UNSTABLE** (0/5)
- [x] P0008: Observation Intent Engine（LLM 动态 Mission Role, Persona≠Prompt, Role 可组合化）
- [x] P0008.1: Commitment / Dwell Policy（HOLD/SWITCH/RELEASE arbiter，Curiosity 与 Commitment 职责分离）— implementation done，**hardware A/B/C validation pending**
- [x] Baseline Integrity 维护（BI-01~BI-09）：帧所有权、观测有效性契约（OBSERVED / NOT OBSERVED / STALE）、objects 与 anchor_novelty 的 partial-update 语义、离开防抖、reset 语义 — 代码层完成，`281 passed`，**hardware baseline NOT validated**
- [x] 仓库安全重建：config.py 移出 git（密钥），新增 config.example.py，远端为干净单 commit 历史

## 进行中

无

## 下一步

0. [ ] Known-Issue Triage：对 [known_issues.md](known_issues.md) 各条做 FIX / VERIFY / RECORD / DEFER 分类，仅 FIX 类进入下一轮修复
1. [ ] P0008.1 Hardware validation: Pending（Scenario A/B/C：person 20min HOLD / 新物体 SWITCH / 离开 RELEASE）
2. [ ] ChatGPT 审查 P0008.1 代码
3. [ ] Mission Playground — 同一房间切换 5 个 Persona，对比注意力分布
4. [ ] Persona Divergence 指标（Jensen-Shannon Distance between persona attention distributions）
5. [ ] P0009: Scene Graph（Entity 空间关系）
6. [ ] P0010: Event Discovery（Temporal Scene）
7. [ ] P0011: Value Engine（per-event, not per-entity）

## 阻塞

- **Baseline Integrity Gate 未通过**：仍有 OPEN-CONFIRMED 行为缺陷未修复（见 [known_issues.md](known_issues.md)），
  其中 4 项直接影响 P0008.1 长测的自变量。硬件长测暂不启动。
- **Hardware baseline smoke test = INCONCLUSIVE**：硬件在位（Arduino `/dev/tty.usbserial-A600J5V6`、
  摄像头索引 0/1 可打开），但 Test A/B/C 需要用户的物理动作（进入画面/静止/离开/放置物体），
  本轮未执行。审计发现仪器覆盖不足的项已由诊断探针补上（见下）。
- 诊断探针（`OBS` / `SCENE` / `ANCHOR lookup` / `ANCHOR observe` / `ATTENTION new_object`）
  已就位，**全部为 DEBUG、文件-only、行为中立**，仅用于 Hardware Baseline Smoke Test 的证据采集；
  这不是新的 runtime capability。等用户执行实机 smoke test。
