# Current State

> 当前开发进度和状态。每次会话结束更新。

## 版本

0.1.0

## 基线状态

| 项 | 值 |
|----|-----|
| Code regression | **340 passed, 0 failed**（`conda run -n vision-dev python -m pytest -q`） |
| Hardware baseline | **PARTIAL**：观测有效性部分仍需复验（Test B 未覆盖 / BI-10、BI-11 已修待复验）；**PTZ 部分 PASSED**（2026-09-24 14:03 实机 A/B 对照，见 BI-14） |
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
- [x] L6 文字 LLM 迁移到 Hermes 默认 provider（volcengine-plan / ark-code-latest，key 只走环境变量）；删除不可用的 DashScope VLM 后端
- [x] PTZ：跟踪节奏与 8s revisit gate 解耦（BI-12）、PTZ Motion Layer 单写入者 + 仲裁 + 限速（BI-13）、Gentle Framing keep-in-frame（BI-14）、三旋钮解耦 start/aim/gain（BI-16）、anchor 判断不再销毁 person commitment（BI-15）、1.5s decision 节奏与 active-follow motion update 解耦（BI-17，追赶上限由 10°/s 解除）—— 均实机验证

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

- **Baseline Integrity Gate 未通过**：仍有 OPEN-CONFIRMED 行为缺陷未修复（见 [known_issues.md](known_issues.md)）。
  硬件长测暂不启动。
- **需要产品决策（非 bug）**：BI-14 之后，tracking session 只能由"停在合格 anchor 上"这一条路径开启 ——
  站在相机前但不在合格锚点上的人完全不会被跟随。这是"检测不得启动跟踪"的必然结果，
  但实际闸门比"由 revisit/commitment 流程决定"更严，是否接受由用户拍板。
- **Hardware baseline smoke test = PARTIAL**：硬件在位（Arduino `/dev/tty.usbserial-A600J5V6`、
  摄像头索引 0）。PTZ 部分 2026-09-24 已做完整 A/B 实机对照并通过；
  Test A/B/C 中依赖"进入画面/离开/放置物体"的项仍需用户的物理动作，本轮未执行。
- 诊断探针（`OBS` / `SCENE` / `ANCHOR lookup` / `ANCHOR observe` / `ATTENTION new_object`）
  已就位，**全部为 DEBUG、文件-only、行为中立**，仅用于 Hardware Baseline Smoke Test 的证据采集；
  这不是新的 runtime capability。
