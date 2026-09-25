# Handoff: 2026-09-25 — P0008.1 实验基线冻结

> 上一份 handoff（2026-08-16，P0008.1 实现期）已被本文件取代；其内容在 git 历史中可查。
> 完整问题登记见 [known_issues.md](known_issues.md)，当前状态见 [current_state.md](current_state.md)。

## 一句话

**P0008.1 现状作为下一阶段的实验基线被冻结**。已知缺陷**一律保留、未修**：冻结不是"整理仓库"，是"把当前位置存下来"。

## 恢复点

| 项 | 值 |
|----|-----|
| 分支 | `fix/frame-diff-and-dead-code` |
| HEAD | `a4f20ff`（decision cadence 修复）|
| 上一提交 | `cf7c36a`（BI-19）、`8f4e49d`（BI-18 崩溃修复）|
| 全套测试 | **347 passed, 0 failed**（`conda run -n vision-dev python -m pytest -q`）|
| 相对 master | 领先 18 个 commit，落后 0；`master` = `origin/master` = `c1fe056` |
| 本分支 upstream | **无** —— 尚未推到远端；推送目标需先确定，不要凭猜设置 upstream |

## 环境

- conda env `vision-dev`；`config.py` 是 git-ignored，从 `config.example.py` 拷贝，密钥只走环境变量。
- 硬件：Arduino SG90 云台在 `/dev/tty.usbserial-A600J5V6`（pan 10–165，tilt 95–170），摄像头 index 0。
- 跑测试/运行必须走 conda env。

## 这一轮之后，什么已经被实机确认

1. **tracking session 能长期保持**：单 session 连续 720s，57 次仲裁全 HOLD。
2. **Gentle Framing 在硬件上工作**：小动作不动、真移动才追、跟随中按观测率刷新 goal。
3. **三个时间尺度分离**（这是本轮最实质的架构结果）：
   - session — 只由 revisit/commitment 流程建立，检测本身不启动跟踪；
   - decision — 8s `revisit_interval` 闸门，实测 **4.3 次/分**；
   - execution — engaged follow 按观测率刷新，实测 **300 次/分 @ 5FPS**。
4. **BI-19**（不用自己动作之前的画面做修正 + face 参考点一致性）实机通过：0 条基于陈旧帧的修正、0 次 face/person 参考切换。
5. **BI-20**（决策节奏）测试 + 实机通过：HOLD 与 `Revisit [pick]` 1:1，无帧率泄漏。

## 什么仍然未解决（**下一阶段不要顺手修，除非排进计划**）

- **multi-person framing 没有稳定 target identity**：逐帧 argmax 选脸 → 两人同框时每帧翻转 → 2Hz 摇动（实机 t=476 两秒四次反向）。L4 也没有稳定身份（72 次 `[FOCUS]`、70 个各出现一次的 id、0 次 RELEASE）；framing 完全不消费 Entity 签名身份。
- **challenger 结构性缺席**：57/57 = 0.00（54/57 次 pick 根本没有候选目标）。
- **commitment 饱和使 SWITCH 不可达**：score 1.00 vs 阈值 1.15。
- **RELEASE / reacquire 链从未被实机触发**（最长无目标间隙 2s）。
- **Arduino / PTZ degraded mode**（无硬件时 `moving` 永久为 True）。
- **Startup Lifecycle 未实现**：现在的启动是隐式的 60s `startup_phase` + 定时 sweep，没有独立的初始化阶段，也没有"建立房间视觉基线"这一步。
- 其余 OPEN-CONFIRMED / VERIFY / DEFERRED 条目见 ledger。

## 下一阶段方向（用户已定）

**启动期视觉环境建立 / initialization** —— 不是继续调 Commitment。

## 下一阶段不要做的事

不要调 Commitment 阈值、不要动 target identity / challenger / SWITCH 公式 / RELEASE 语义、不要动 Gentle Framing 的 start/aim/gain 与 ±15°/±8°、不要动 Motion Layer 与 1.5s/8s 节奏 —— 这些是**冻结基线的一部分**，改动会让本基线的实机结论失效。需要它们时先开新的任务并重新实机验证。

## 已知的文档小瑕疵（记录、未修）

`known_issues.md` 里 `## OPEN-CONFIRMED` 标题出现两次（早前编辑留下的重复），纯排版问题，冻结期内未动。
