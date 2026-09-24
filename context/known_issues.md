# Known Issues Ledger

> 本文件登记所有已发现问题。**状态说明：**
>
> | 状态 | 含义 |
> |------|------|
> | `FIXED` | 已修复并有回归测试/受控复现证据 |
> | `OPEN-CONFIRMED` | 已复现或已由代码路径确认的行为缺陷，尚未修复 |
> | `VERIFY` | 有风险但证据不足，需先做受控复现再决定 |
> | `DEFERRED` | 重构/优化建议或已规划到后续批次，不属于当前 bug |
>
> **编号约定**：`BI-nn` = 本会话 baseline-integrity 工作项顺序编号。
> `BI-01`~`BI-03` 为早前已提交项，`BI-04`~`BI-09` 为本次 checkpoint commit。

---

## 当前基线状态

| 项 | 值 |
|----|-----|
| Code regression | **336 passed**, 0 failed（`conda run -n vision-dev python -m pytest -q`） |
| Hardware baseline | **PARTIAL**：2026-09-24 07:15 那次 Test A 通过 / Test B **未覆盖** / Test C 暴露 BI-10、BI-11（两条已修，待复验）；**PTZ 部分 PASSED** —— 2026-09-24 14:03 实机 A/B 对照，见 BI-14 |
| 实机运行 | 5 次：07:15:32（13m14s，3873 帧）、14:03:34 / 14:11:47（各 ~7m，A/B 对照）、15:52:58（7m26s，走动 A/B，**判定 INVALID**）、17:28:25（5m43s，BI-15 验证） |
| 分支 | `fix/frame-diff-and-dead-code`（未 merge、未 push） |
| 已提交前序 | `73f8ac4` `21d7d47` `ad77f22` `7e7a5ff` `f4d8879` `92a99bb` `0e061e9` `feec2bc` `94dd360` `557ec9f` `c9593a8` |

---

## FIXED

### BI-01 — FrameDiff 帧几何变化导致感知循环崩溃
- **证据**：线上栈逐字复现 `ValueError: operands could not be broadcast together with shapes (180,320,3) (240,320,3)`；修复后回归测试转绿。
- **影响**：相机中途重协商分辨率（4:3→16:9）时整个 runtime 退出。此前必须在 PTZ 静止时才会暴露（`ego_motion` 会跳过帧差）。
- **提交**：`73f8ac4`

### BI-02 — 清理死代码：scan 模式与 desk-change 字段
- **证据**：对 HEAD 版本做 32 组场景×参数×reset 差分追踪，`mode`/`has_focus`/`changed`/`recent` 长度输出完全一致。
- **影响**：`FocusManager._check_scan` 两分支都赋 `"idle"`；`idle_scan_time` 无人读取；`_obj_change_count` 只写不读；`_novelty_count` 用 `getattr` 惰性创建。均无外部调用方（仓库内 + `~/Workspace` 其他项目零引用）。
- **提交**：`21d7d47`

### BI-03 — `anchor_novelty` 的 partial-update 语义
- **证据**：RED —— 按生产"两调用/帧"模式写的测试在修复前失败；并证明旧实现下 `_novelty_count >= 1` 在检查点恒为假，故 latch 不可达（与 novelty 取值无关）。
- **影响**：`SceneState.update()` 六个参数中五个是 partial，唯独 `anchor_novelty` 默认 `0.0`，省略即走 else 分支清计数器——生产每帧 u2 都破坏 u1 的累积。
- **提交**：`ad77f22`

### BI-04 — FrameDiff 缓存的参考帧是调用方内存的 view
- **证据**：`np.shares_memory(_prev, caller_frame) == True`；A/B 对照 —— view-backed 在内容完全相同的干净帧上返回 `changed=True, motion=0.098`，copy-backed 返回 `False, 0.000`。
- **影响**：`frame[::2,::2]` 是 stride view，`SHOW_PREVIEW=True` 时 preview overlay 原地绘制写穿缓存 → 运动闸门几乎恒开 → YOLO/YuNet 每帧全跑 + 幻影 `motion_level` 流入 `SceneState`。这也解释了此前"YOLO 好像一直在跑"的一部分现象（YOLOv8s 算力过重是独立因素）。
- **状态**：本 commit

### BI-05 — `objects=[]` 与 `None` 语义塌缩
- **证据**：RED→GREEN；`test_explicit_empty_objects_clears_state` / `test_objects_none_preserves_previous_observation`。
- **影响**：`if objects:` 真值守卫使"物体全部消失"无法表达，物体列表永久残留。新契约：`None`=未观测（保留）、`[]`=观测到零（清空）。调用点同步修正为未观测时传 `None`。
- **状态**：本 commit

### BI-06 — `FrameDiff.reset()` 残留上一观测的 motion
- **证据**：RED→GREEN；契约确认 —— `reset()` 无任何生产调用方（`revisit.py:243` 是 `CommitmentEngine.reset()`），docstring 无保留 `_motion_level` 的意图，非"有意保留"。
- **影响**：reset 后第一帧会报出 reset 前测量的 motion 值，流入 `SceneState` 平滑与紧急升级。
- **状态**：本 commit

### BI-07 — 检测跳过被解释为"目标消失"
- **证据**：端到端复现（静止的人）：frame 1 检测运行 → `user_present=True`/FOCUS/focus=tracking；frame 2 闸门跳过 → `user_present=False`/focus=lost；超过 3.0s/2.5s → **FOCUS→IDLE，焦点被释放**。RED：去掉观测门后 3 个测试失败，而"真实离开"测试两种情况都通过（证明修复未以"永不释放"为代价）。
- **影响**：直接破坏 P0008.1 长测的被测场景。修复后 `FocusManager` / `PresenceTracker` **无需改动**（前者的通用回退分支接受 attention 从保留 `people` 派生的 `human_face`；后者的 `elif user_present:` 分支本就不衰减）。
- **状态**：本 commit

### BI-08 — 观测有效期（STALE 事实）与 config 模板同步
- **证据**：闸门对亚阈值漂移永不重开（实测 72 灰阶 × 100 采样像素 < `MIN_PIXELS=500` → `changed=False`）；强制重观测使 anchor novelty 0.480→0.299，`desk_changed` 约 20s 后自行清除。
- **影响**：原先没有周期性重检机制，"保留的观测"可被无限期当成仍然有效 —— 缓慢离开的人无法被发现。新增 `OBSERVATION_MAX_AGE_SEC=2.0`，由 `FrameDiff.observation_stale()`/`mark_observed()` 表达。
- **附带修复**：`OBSERVATION_MAX_AGE_SEC` 最初只加进 git-ignored 的 `config.py`，导致新克隆按 CLAUDE.md `cp config.example.py config.py` 后 **ImportError 无法启动**（code review 捕获）。已补 `config.example.py`，并加 AST 校验：runtime 请求的 39 个 config 名字全部存在。
- **状态**：本 commit

### BI-09 — 离开防抖被观测语义卡死
- **证据**：复现 —— 离开帧之后全是闸门关闭帧时，过了 999s 状态仍为 `focus`，转移记录只有 `idle→focus`，`user_left` **永不触发**。变异测试：把检查塞回守卫内 → 新测试精确失败。
- **影响**：防抖是**时间驱动**的，却被关在 `if people is not None:` 里。修复后 `user_left` 正常触发（原本要等 30s 的 `_STATE_TIMEOUTS` 兜底，且 attention 会在更敏感的 FOCUS 档多停留 10 倍时间）。
- **状态**：本 commit

### BI-10 — `desk_changed` 永久卡死（novelty 生命周期）
- **HARDWARE OBSERVED**（`runtime_20260924_071532.log`，3873 帧 / 13m14s）：`desk_changed` @07:18:18(f=791) 置 True 后**到运行结束（10.5 分钟）再无下降沿**；同期 `ATTENTION new_object` 共 **3695 次**，约 **5 次/秒（≈每帧）**
- **根因（两处）**：
  1. `AnchorManager.observe` 的 "first observation" 提前返回以 `not anchor.baseline_objects` 为条件 —— baseline 合法变空后会**永远**重入该分支并 return，`else` 衰减分支不可达，novelty 冻结在峰值（实测冻结于 0.480）；顺带使 `_empty_streak`/`barren` 也永不置位
  2. `main.py` 的 novelty 读取要求 `objects` 非空 —— 桌上无物体时永远传 `None`，即使 novelty 衰减了也到不了消费者
- **修复**：`SpatialAnchor` 增加 `observed_once` 标志，提前返回只用于真正的首次观测；novelty 读取条件由 `objects and not ego_motion` 改为 `detection_ran and not ego_motion`（空检测是**有效观测**）
- **未采用**：不给 `desk_changed` 加人工 timeout、不改 attention threshold、不做 `new_object` 去重、**保留"物体消失本身可产生 novelty"语义**
- **证据（测试）**：`test_stable_empty_eventually_decays`、`test_object_appearing_raises_novelty`、`test_desk_changed_latches_then_clears_on_a_settled_desk` 在修复前失败、修复后通过
- **状态**：**FIXED（代码）—— 待实机复验**

### BI-11 — Anchor 空间网格不一致
- **HARDWARE OBSERVED**（同上日志）：`ANCHOR lookup` **322 hit / 1777 miss = 15.3% 命中率**；miss 全部集中在 `snapped=90`（1283 次）与 `snapped=150`（428 次）—— 正是 20° 网格与 30° 网格的中点（80/100 与 90、140/160 与 150 各差 10°），只有 0/60/120 两网格重合才命中
- **根因**：`AnchorManager(pan_spacing=20)`，而调用方 `main.py` 自行实现 `round(pan/30)*30` 的 snap + `abs(...) < 1` 容差匹配
- **修复**：`AnchorManager` 新增 `snap()` / `lookup()` 作为**唯一** canonical 网格语义；删除调用方自实现的 30° snap 与容差循环，改为 `lookup()`（dict 键精确命中，**未扩大容差**）
- **未改变**：novelty 算法、阈值、衰减率
- **证据（测试）**：`TestCanonicalGrid` 三个用例；`test_lookup_resolves_poses_the_30_degree_snap_missed` 直接断言 `snap(90,92) == (80,90)` 且 `lookup(90,92)` 命中
- **状态**：**FIXED（代码）—— 待实机复验**（实机验证指标：下一次运行的 `ANCHOR lookup` 命中率应显著高于 15.3%）

### BI-12 — PTZ 外层 8s gate 压制内层 1.5s tracking
- **HARDWARE OBSERVED**（`runtime_20260924_071532.log`）：PTZ 命令全程仅 **53 条**（27 pan + 26 tilt）/794s ≈ 4/min；跟踪期间隔 **~8–9s**，其后出现 **114s / 162s / 255s** 空档
- **根因（代码 + 节奏一致）**：`revisit_interval = 8.0`，`tick()` 早退，而 `_track_target` 的**全部**调用点都在该闸门之后 → 内层 `_track_interval = 1.5` 被完全支配
- **修复**：`tick()` 在 revisit 闸门之前先跑跟踪更新；`_track_target` 保留自己的 1.5s 间隔与 camera-busy 守卫。**两个间隔参数值均未改动**，idle / revisit / sweep 仍在 8s 节奏
- **证据（实机）**：tracking 命令间隔 median **8s → 2s**，≤4s 占比 14% → 76%
- **提交**：`557ec9f`

### BI-13 — 两个调度器直接写同一轴 + bang-bang 大步长
- **HARDWARE OBSERVED**：tracking 把 tilt 收敛到目标（95→103→111→114→115），而探索路径每 ~8s 把它复位到 95 → tilt 从未保持收敛，每个周期首条命令撞上 ±8° clamp，舵机被压下去又立刻抬回来。**tracking 命令饱和率 tilt 25% vs pan 9%**
- **根因**：`revisit.py` 的跟踪路径与探索路径各自直接调用 `servo.pan_to`/`tilt_to`，没有仲裁；且一次意图作为单条命令下发（60° 扫视 = 60° bang-bang）
- **修复**：新增 `perception/ptz_motion.py` 作为**唯一**运动写入者 —— 所有权仲裁（TRACK > EXPLORE，WEAR_PROTECT 可抢占）、`MAX_STEP_DEG=20` 限速步进、latest-setpoint-wins（不排队、无积压）。探索的 tilt→95 复位额外用既有的 presence window 守卫
- **未改变**：1.5s / 8s 节奏、±15°/±8° clamp、Arduino 固件、目标选择
- **证据（测试）**：`tests/test_ptz_motion.py` 8 个用例；`test_revisit_writes_movement_only_through_the_motion_layer` 直接断言 `revisit.py` 不再出现 `_servo_ptz.pan_to(` / `tilt_to(` / `pan_relative(` / `center(`
- **提交**：`c9593a8`（实机：无 bang-bang 反转、无积压）

### BI-14 — tracking session 由"看到脸"启动 + center-lock 逐帧微动
- **根因（两条）**：
  1. `tick()` 的 `if faces or objects: self._track_target(now)`（BI-12 的解耦手段）**无守卫** —— 检测到人脸/人体即启动跟踪，不需要任何"决定要跟"的上游判断。实机表现为 startup sweep 期间每张脸都被交给跟踪，跟踪与 sweep 每 8s 争夺 tilt
  2. 旧 dead zone 是 `abs(dx) < 0.06 and abs(dy) < 0.06`（**与**逻辑），gain 1.0 把目标推向画面中心 → 只要有一个轴偏移超过 6%，另一个轴哪怕偏移 1% 也会下发命令
- **修复**：
  - 删除无守卫 fast path。**tracking session** 由现有 revisit/commitment 流程建立（`_track_target` 是 `CommitmentEngine.begin` 的唯一调用者），**检测本身不再启动跟踪**；session 打开后 movement update 仍按 1.5s 节奏、不受 8s 闸门限制（BI-12 的成果保留）
  - `_track_target` 拆为 建立（`_track_target`）/ 逐帧（`_framing_update`，只 `confirm` 不 `begin`）/ 取目标（`_acquire_track_target`）/ 修正（`_aim` + `_keep_in_frame`）
  - center-lock → **keep-in-frame**：舒适区（|dx|≤0.15, |dy|≤0.20）不动，外边界（0.30）启动跟随，内边界停止（迟滞带），修正量 = `gain(0.5) × 超出内边界的部分`，**瞄准内边界而非中心**
- **未改变**：target selection / Focus / Attention / Commitment 语义 / Interest / Curiosity / Entity 关联 / 感知 / 1.5s 与 8s 参数 / Arduino 固件
- **证据（实机 A/B，同日同一场景，各 7 分钟，1773 vs 1644 个目标观测）**：

  | 指标 | 新（keep-in-frame） | 旧（HEAD，center-lock + fast path） |
  |---|---|---|
  | 目标在舒适区内时下发命令的帧 | **1 / 1155（0.09%）** | **154 / 1574（9.8%）** |
  | 命令发生时的 \|dy\|（tilt 命令） | min 0.22 / median 0.30 | min 0.06 / median **0.09** |
  | tilt 步长达到 ±8° clamp | **0 / 6** | 3 / 14（21%） |
  | session 窗口（t>65s）舵机行程 | 0.5s / 356s | 0.9s / 356s |
  | session 窗口 pan 累计度数 | 7° | 66° |

  逐条对照验收项：小幅头部/身体移动（\|dx\| 0.00–0.18、\|dy\| 0.01–0.27 共 1522 帧）→ **0 条命令**；接近边缘才跟随 → |dx|=0.39 时 pan+7°（旧代码为 25° 撞 clamp），\|dy\|=0.35 时 tilt 95→98→101→105→108→110→111（+3/+3/+4/+3/+2/+1，逐步衰减）；回到安全区即停 → 停在 dy=0.22，**未追到中心**，其后 339s 零命令；不再自扰动 → session 内 0.5s 行程；≤1.5s 更新能力 → 命令间隔 1.3–1.9s（即 1.5s 节奏 + 帧/队列延迟）
- **证据（测试）**：`tests/test_ptz_gentle_framing.py` 11 例、`test_revisit_tracking_cadence.py` 改写为 session 契约、`test_ptz_motion.py` 的 `test_the_startup_sweep_is_the_only_writer_while_detections_come_in`（旧代码下 3 项失败）
- **状态**：**FIXED（代码 + 实机验证）** —— 注：上表只证明了"不该动时不动"（自扰动），**没有**测量跟随响应，这正是随后 BI-16 存在的原因；其参数取值已被 BI-16 取代。表本身有效（那两段运行中 session 全程存活）。

### BI-15 — anchor-level 判断错误销毁 person-level commitment
- **HARDWARE OBSERVED**（`runtime_20260924_155258.log`，forensics 于 `context/` 之上）：t=170s `Flat interest: anchor_80_90 stuck at 0.100 for 89s — likely empty wall, moving on` → `should_leave` 分支调用 `self._commitment_engine.reset()` → **person commitment 被 anchor 判断销毁**。随后 16s 内 97 帧里有 92 帧目标远在舒适区之外（|dx|>0.15）而相机毫无动作，explore 分支把相机朝**人所在的反方向**转了 30°（90→60）；t=186 重新建立 session 后立刻用一条 +15° 命令补回来。
- **根因**：ownership crossing。`reset()` 的语义是"当前 target 是假阳性"，而触发它的是**锚点**的新鲜度判断（flat interest / 稀疏可疑类 / VLM trivial）。锚点无聊 ≠ 人离开了。三个触发分支本身已经把 `anchor.interest` 归零（VLM 另加 `suppressed`），这才是让锚点退出 stay 候选的机制。
- **修复**：删除该 `reset()` 调用。commitment 只能由它自己的语义结束（`decide()` 的 lost / stale / timeout / SWITCH）。未改动 Commitment、acquisition、Framing、Motion Layer、任何参数。
- **证据（测试）**：`tests/test_anchor_leave_ownership.py` 7 例 —— 三个触发分支各一例（flat / sparse / VLM）证明 active commitment 存活；一例证明存活的 session 仍在收图（断言 `pan+10` 是 framing 修正而非 explore 转向）；两例证明 anchor 自己的 leave 行为不变（interest 归零、stay 结束、不重新进入）；一例证明人离开时 commitment 仍能正常 RELEASE（不是永生）。
- **证据（实机，`runtime_20260924_172825.log`，343s）**：`Flat interest` 触发**两次**（t=213 `anchor_120_90`、t=333 `anchor_80_90`）；结果 `Commitment Start` **仅 1 次**（t=80）、`RELEASE`/`SWITCH` **0 次**、`Revisit [turn]`（explore 转向）**0 次**。第一次 flat-interest 之后 35s 内仍发生 **35 条 framing 命令**（pan 达 ±15°、tilt 达 ±8°），即"锚点被判无聊 → 相机继续跟随人"。锚点自身的 leave 也正常：`anchor_120_90`（hot 0.775）被放弃，相机转到 `anchor_80_90` 停留。
- **状态**：**FIXED（代码 + 实机验证）**

### BI-17 — 1.5s 同时限制 decision 与 active-follow motion update
- **来源**：用户实机感受"追不上"→ chase-capacity audit（见 BI-16 的实机数据）。实测量：`_acquire_track_target` 的 1.5s 节流同时闸住两个调用者（`_framing_update` 逐帧执行路径与 `_track_target` 建立路径），且 `_last_track` 只在**发出修正**时推进 → 语义是"两次有效修正之间至少 1.5s"，即 motion goal 更新率 = 0.67Hz。而 `PtzMotion.step()` 本就每帧运行、每轴支持 20°/帧 = 100°/s —— 执行侧的能力远大于它被允许使用的。
- **后果（实机 `runtime_20260924_172825.log`）**：追赶上限 = ±15°clamp × 1.5s = **10°/s pan、5.33°/s tilt**；目标角速度 median 0.8 / p90 **15.8** / peak 29.5°/s → **19% 的行走时间超过 pan 上限**。98 个 4s 窗口中 24 个目标 >10°/s，相机实测最多只跑到 8°/s，trailing 中位 200px、峰值 309px（距画面边缘 11px）。而且 10°/s 只在目标已经偏离 213px 时才达到（修正量正比于超出 aim 的部分）。
- **修复**：新增 `_following()`（`_framing_pan or _framing_tilt`），节流改为 `if not self._following() and now - self._last_track < self._track_interval`。即 **decision 节奏**（1.5s，follow 未建立时决定"要不要开始跟"）与 **execution 节奏**（follow 已建立 → 每个有效观测刷新 motion goal）分离。hysteresis、start/aim/gain、±15°/±8°、1.5s 数值、Motion Layer、固件全部未动。
- **为什么不会重新引入 bbox jitter → PTZ jitter**：follow 的建立是空间滞后的边沿触发（|offset| > start_offset，96px），bbox 噪声（YuNet 实测 ±13–26px）越不过去；且 follow 在目标回到 aim 点（38px）时立即退出。闭环仿真（真实 controller + 模拟走动 + 噪声）：静止目标 + 噪声 0.02/0.04 → **命令数 0**；移动目标加噪声反而命令数更少（噪声让修正更早落进 aim 区、follow 更早退出）。
- **证据（测试）**：`tests/test_ptz_gentle_framing.py` 新增 3 例（engaged follow 按观测节奏更新、rapid updates 一观测一命令且无 backlog、follow 停止后 sub-threshold 抖动不得重启）；`tests/test_revisit_tracking_cadence.py` 两例改写为新契约（engaged follow 不受 1.5s 限制 / 1.5s 仍约束 follow 结束后的重新决策）。全套 336 passed。
- **证据（实机 `runtime_20260924_175145.log`，423s，用户持续走动）**：

  | 指标 | 旧（17:28） | 新（17:51） |
  |---|---|---|
  | framing 修正数 | 34 | **117** |
  | trailing error p90 / max | **225px / 309px** | **91px / 266px** |
  | 舵机行程 | 5.7s（1.7%） | 5.8s（**1.4%**） |
  | ego_motion 帧 | 3.0% | 3.1% |
  | 首个命令延迟（运动起始后） | 1.37s | **0.94s** |
  | A-B-A 反转 | 0 / 48 | 1 / 59 |

  同速率对比（最快 4s 窗口，目标 11.6–13.2°/s）：相机达 **12.25–17.0°/s**（旧代码在 10.74°/s 目标下只能给 9.83°/s 且已顶到上限），trailing 中位 **68–94px**（旧 S2 为 200px）。
- **自扰动复核（关键）**：117 条修正中，**|dx| ≤ aim(0.06) 的 pan 修正 = 0 条，|dy| ≤ aim(0.08) 的 tilt 修正 = 0 条**；修正频率随目标自身角速度单调上升（几乎静止 0.08/s → 慢 0.50/s → 走动 1.87/s → 快 2.67/s），是"追踪者"而非"抖动者"的特征。**未出现高频自扰动。**
- **遗留（本轮冻结项，非缺陷）**：tilt 轴更紧 —— 29/110 条 tilt 修正在 ±8° clamp 饱和（pan 仅 2/37）。原因：8°/41.25 = 0.194 归一化偏移即饱和，而 pan 是 15/55 = 0.273。tilt 需要 ~2 次修正才能完成一次大偏移（同一方向、<1s 的成对修正 56 次，即收敛而非抖动）。
- **状态**：**FIXED（代码 + 实机验证）**

### BI-16 — Gentle Framing 用单一阈值同时决定 trigger / correction / residual
- **来源**：用户实机反馈"不会追踪 / 追得太慢"驱动的 forensics（见本文件 BI-14 条目的实机数据）。几何量：触发点 192px vs 557ec9f 的 38px；单次修正 4–8° vs 15°；残余 96px；可持续跟随速度 6.7°/s 且只在画面边缘才达到；±15°/±8° clamp 变成死代码（最大修正仅 9.6°/6.2°）。
- **根因**：`_keep_in_frame(offset, outer, inner, following)` 让 `inner` 同时决定"修正起点"和"最终残余"，`outer` 决定触发，gain 0.5 决定幅度 —— 一个大舒适区必然同时导致**晚触发 + 弱修正 + 大残余**。
- **修复**：拆成三个独立控制量 —— `start_offset`（何时开始，pan 0.15 / tilt 0.20）、`aim_offset`（追到哪里 = 残余，pan 0.06 / tilt 0.08）、`gain`（1.0 = 不过冲 aim 点的最大增益，也是让 clamp 重新有效的值）。触发点 192→**96px**，残余 96→**38px**，可持续跟随恢复 10°/s（pan）/ 5.3°/s（tilt），clamp 在 |dx|>213px 后生效。
- **未改变**：Motion Layer 的 slew/仲裁、1.5s/8s、acquisition、face/person 参考、任何其他参数。
- **证据（测试）**：`tests/test_ptz_gentle_framing.py` 重写为新契约（17 例），含三个旋钮相互独立的用例（改 start 不改变同 offset 的修正量；gain 线性缩放修正量；修正永不过冲 aim 点）。
- **证据（实机，`runtime_20260924_172825.log`）**：|dx| 达 0.43–0.48 的走动期间相机给出 34 条 framing 命令（17 + 35 两个活跃窗口，含 ±15°/±8° clamp 命令）；|dx| 中位数 0.02 的静止段**零命令**。**注意：原本设计的"新旧参数同场 A/B"（`/tmp/ptz_walk_walk1.jsonl`）已判定 INVALID** —— 两段 session 状态不同（含 16s 无 session 窗口与一次 explore 转向），且新参数在 phase A 内**从未被触发**（chosen |dx| 超阈值 0 帧），旧参数仅触发 1 次。因此本条不声称 A/B 结论，只声称几何修正 + 上述实机跟随行为。
- **状态**：**FIXED（代码 + 实机验证）**

---

## OPEN-CONFIRMED

---

## OPEN-CONFIRMED

### tracking session 只能由"停在合格 anchor 上"这一条路径开启
- **证据**：`_track_target` 的 5 个调用点中，4 个在 `_commitment_holds()` 之后，而 `_commitment_holds` 要求 `has_commitment` 已为真 —— 它们只能**刷新**已有 session，不能建立。唯一能建立的是 stay-at-anchor 分支，前置条件为：非 startup 窗口、anchor 在 pan±15° 内、非 barren、非 suppressed、`interest > 0.08`、`baseline_objects` 非空。
- **影响**：站在相机前但当前不在"值得停留的锚点"上的人**完全不会被跟随**（旧代码的 fast path 跟随任何一张脸）。这是本轮"检测不得启动跟踪"的直接后果，但实际闸门是 anchor-stay 启发式，比"由 revisit/commitment 流程决定"更严。
- **实机观察**：14:03 那次运行在 `Revisit [stay]: anchor_80_90` 出现后 1 秒内建立 session 并全程保持 —— 桌面场景下可达，但需要用户先建立 anchor。
- **归属**：本轮 diff 引入的**有意**收窄，需产品决策而非 bug 修复。

### `target is None` + `_commitment_holds` 分支不更新 `_last_revisit`
- **证据**（代码 + 实机）：`if self._commitment_holds(now): self._track_target(now); return` 没有 `self._last_revisit = now`。承诺存在时该分支每帧进入 → 8s 闸门实质失效 → `Revisit [pick]` 与 `Commitment.Telemetry` 各约 **5 行/秒**（14:03 运行 351 行 / 150s）。
- **影响**：日志噪音（使长测的轮转窗口更紧张，见"日志轮转"条）；行为等价（`_track_target` 自身有 1.5s 节流）。
- **补充（BI-15 之后更常见）**：该路径现在是"commitment 存活但没有 stay 锚点"时的常态入口。实机 `runtime_20260924_172825.log` 中它每帧打印 `Revisit [pick]: ... staying=no → explore`，但下一行实际走的是 `_commitment_holds` → HOLD → framing（相机并未 explore）。**日志标签与真实分支不符**，会误导基于日志的判断。
- **归属**：**既存**（P0008.1 commitment 引入时即有），非本轮 diff 引入 → 只登记，本轮不修。

### presence 变陈旧会让 session 静默休眠（不是 RELEASE）
- **证据（实机 `runtime_20260924_172825.log`）**：t=113–205s 出现 **83.8s 连续无任何 face/person 检测**（该窗口 92s 内仅 74 个检测帧 = 16% 覆盖）。期间 `_last_track_hit` 变陈旧 → `_tracking_session_active()` 为假 → framing 完全停止；`_commitment_holds` 未被调用（stay 分支提前 return），因此**既不 RELEASE 也不重新建立**。t≈198 检测恢复后由 stay 分支的 `_track_target` 重新点火。
- **影响**：人一旦长时间不在检测范围内（或检测连续漏检），相机会静默停摆，且没有任何"重新找回来"的路径，直到 stay 闸门恰好再次找到目标。与"acquisition 过窄"同源但触发条件不同（不是 reset，而是 presence 陈旧）。
- **状态**：未修复（属 acquisition / presence 机制，本轮明确不含）。

### 人脸 bbox 优先于人体 bbox，会因偏移的人脸框而平移
- **证据**（实机 14:04:47）：`Framing face: dx=-0.39 dy=0.02 → pan+7`，而同一时刻 person bbox 在 `dx=-0.06` —— 人脸框落在画面左缘，相机因此平移 7°。
- **影响**：轻微、偶发。新代码只移动 7°，旧代码同条件下移动 25°（撞 ±15° clamp）。属既存启发式。
- **归属**：**既存**，非本轮 diff 引入 → 只登记。

### `_last_track_hit` 不再由"裸检测"刷新，连带改变两个消费者
- **证据**（代码路径）：删除 fast path 后，`_last_track_hit` 只在 session 打开时刷新。两个消费者因此改为随 session 变化：stay 档位判定的 `has_life`（`now - _last_track_hit < 15`）与 sweep/explore 的 tilt→95 守卫。
- **影响**：有 session 时两者行为不变（实机全程保持 session）；无 session 时"画面里有人"不再抬高 stay 档位。因 session 会在第一个 stay 周期内建立，实机未观察到功能断裂。
- **归属**：本轮 diff 的连带效应，未构成 bug，登记以备长测时解释档位变化。

### `Revisit [stay]` 日志中的 `track=1790229886s ago`
- **证据**（实机）：`_last_track_hit` 未初始化时为 `0.0`，日志直接输出 `now - 0.0`（绝对 epoch 秒）。
- **影响**：纯观感。**归属**：既存，非本轮 diff 引入。

### `RevisitController._last_track_hit` 在静止期不刷新
- **证据**：代码路径 —— `revisit.py:610` 是唯一写入点，位于 `_track_target` 内，而该函数在 `faces`/`objects` 皆空时提前返回；`revisit.py:227` 以 `< 15.0` 判 `has_life`。
- **影响**：长时间静止后 `has_life` 转假 → stay 预算从 120s 降到 30s 档 → 可能离开一个**就在眼前但不动的人**。直接落在 P0008.1 长测要观察的行为上。涉及 Commitment/stay 策略，明确禁改。
- **未做端到端复现。**

### attention 事件为电平触发
- **证据**：`main.py:415-424` 对每个 `score >= ATTENTION_THRESHOLD(0.6) × multiplier` 的事件每帧 push；`human_face` 0.68 ≥ FOCUS 档 0.42，故**有人可见时每帧推一条**。
- **影响**：L5 200 条滚动缓冲被重复条目主导，真实的新事件被挤出。
- **归属**：既存设计 —— BI-01 之前闸门恒开、检测每帧都跑，因此该行为在"有人可见"的常态下一直存在；BI-07 只是把它延伸到静止期。
- **修复需要决策**：改为边沿触发，或在记忆层去重。

### `servo_ptz.moving` 无硬件时永久为 True
- **证据**：代码路径 —— `servo_ptz.py:68` 为 `_moving or len(_queue) > 0`，而 `_running` 仅在串口成功打开后置位（`servo_ptz.py:98`）；`start()` 失败路径没有消费者排空 `_queue`。
- **影响**：`ego_motion` 永久为真 → 帧差闸门被跳过、`focus.reset_tracking()` 每帧执行、`camera_settled` 永远为假导致 anchor 永不观测。
- **已由 CLAUDE.md / AGENTS.md 记录。** 已归入 Batch 2（涉及硬件 failure-state machine，风险面较大）。

### `anchor.novelty` 的量纲与被比较的阈值不匹配
- **证据**（代码审计）：`anchor.novelty` 只在 `AnchorManager.observe` 内更新，其余时间以约 10%/min 衰减（`anchor.py:197-200`），是**分钟级累积量**；而 `SceneState` 用它做**逐事件**的 latch 判定（`anchor_novelty > 0.3`，连续两帧）。
- **影响**：相机重新访问某个锚点时读到的可能是数分钟前记下的高值 → 两帧即可 latch `desk_changed`，即使那两帧什么都没变；反之真实变化若落在已衰减到 0.3 以下的锚点上则被忽略。
- **状态**：未修复，未复现（VERIFY→倾向 OPEN-CONFIRMED）。与已登记的 `desk_changed` 卡死项同源。

### 日志轮转窗口 vs 多小时 long-run
- **证据**（实测推算）：插桩后 OBS 行约 130 B × 5 FPS ≈ **56 MB/天**；另测得既有日志体积 ≈ **16 MB/h**（`runtime_20260921_133236.log` = 3.5 MB / 13 min），主要来自 `servo_ptz.py:185` 的每命令 INFO 行。轮转配置为 5 MB × 3 backups（`logging_config.py`）。
- **影响**：**对本次 smoke test（每项 3–5 分钟）无影响**，远在窗口内。但对计划中的多小时 long-run，窗口仅约 1 小时，早期证据会被轮转掉。
- **状态**：未修复。可选缓解：提高 `LOG_FILE_MAX_BYTES`，或把 OBS 探针改为边沿触发（每次 gate 结果变化才输出一行，仍可凭行内 `f=` 重建区间），或按阶段手动归档 `latest.log`。

### 观测仪器覆盖不足 —— Test A 部分项与 Test C 全部无法观测
- **证据**（代码审计，非实机）：`runtime/main.py` 的 `logger.*` 调用中，`has_changed` / `detection_ran` / `observation_stale`（forced observation）/ `user_present` / `desk_changed` / `anchor_novelty` 的提及次数**均为 0**；`runtime/interest/anchor.py` **没有任何 logger 调用**；`runtime/attention/engine.py` 只记录权重演化与 gaze 转变，不记录 `new_object` 事件。
- **影响**：
  - **Test A 第 1 / 2 / 4 项**（静止时 detection 不应每帧恒跑、stale 后应周期性重检、周期重检仍能确认 person）无法从日志观测
  - **Test C 全部**（anchor hit/lookup、anchor novelty、desk_changed、new_object attention event、novelty 恢复、desk_changed 恢复）无法从日志观测
  - 即：即使接上硬件跑完整流程，也无法为这些量产出 OBSERVED 级证据
- **当前可观测的**（对照用）：

  | 量 | 输出点 |
  |---|---|
  | SceneState 状态转变（含 trigger） | `scene/state.py:106` |
  | Focus lock / release | `focus/manager.py:203,210` |
  | gaze started / lost | `attention/engine.py:163,169` |
  | PTZ 每条命令响应 | `perception/servo_ptz.py:185` |
  | Revisit stay / leave 决策（tier/int/objs） | `interest/revisit.py:254-278` |
  | Commitment 决策块 | `commitment/telemetry.py:41,58` |
  | 每 60s 状态行 | `main.py:480` → `1022,1028` |
  | 每分钟汇总 | `logs/telemetry/vitals_*.log` |

- **状态**：**已由诊断探针解决**（`chore(runtime): instrument observation baseline`）。全部为 `logger.debug`，文件 handler 本就是 DEBUG（`logging_config.py:102,128`），控制台仍停在 `LOG_LEVEL`，因此不刷屏。新增探针：

  | 探针 | 输出 | 频率 |
  |---|---|---|
  | `OBS` | `ran` / `reason`(motion,ego_motion,stale) / `changed` / `ego` / `stale` / face、object 计数 | 每帧 DEBUG |
  | `SCENE` | `user_present` / `desk_changed` | **仅边沿** |
  | `ANCHOR lookup` | `hit`/`miss` + snapped 网格 vs stored 网格 + novelty | 仅查找发生时 |
  | `ANCHOR observe` | 传入 `observe()` 的 objects 数与 pan/tilt | 仅观测发生时 |
  | `ATTENTION new_object` | score / detail / intention | 仅事件实际产生时 |

  `ran=false` = NOT OBSERVED；`ran=true` 且计数为 0 = OBSERVED with zero results —— 两者可区分。

  **仍不可观测**：`FrameDiff` 逐帧 `motion_level`、detector 的置信度分布、anchor 被跳过未观测的帧。日志体积影响见下一条。

---

## VERIFY

### `_draw_overlay` 污染 revisit 的缓存帧 —— **已测，影响有界**
- **证据**：以真实 `_compute_signature` / `_signature_distance`（匹配阈值 60.0）受控测量：

  | bbox 边长 | 100 | 80 | 60 | 40 | 30 | 20 | 12 | 8 |
  |---|---|---|---|---|---|---|---|---|
  | 签名距离 | 12.3 | 15.4 | 20.7 | 31.5 | 42.5 | **65.3** | 113.0 | 138.5 |

- **结论**：污染真实存在，但**只在 bbox ≤ ~20px 的远处小目标上超阈值**。常规 40–100px 的人脸/人体距离 12–43，不会导致再识别失败。原先"实体会因签名不匹配被 FORGOTTEN"的无条件断言**不成立**。
- **未验证部分**：VLM 验证路径（`revisit.py:209-212`）收到带 UI 覆盖的帧 —— 需要外部模型，未测。
- **建议**：`RECORD` 或低优先 FIX（显示层不应修改供感知使用的图像，但严重度有限）。

### `_draw_overlay` 同样污染 entity registry 之外的消费路径
- 见上条；`main.py:530` 把 overlay 后的帧传给 `revisit_controller.tick(frame=...)`，而 `entity_registry.process_frame`（`main.py:435`）用的是干净帧 → 存储签名与验证签名来自不同像素源。**已是上条的同一问题。**

### FrameDiff 2-D / 通道数变化
- **证据**：仅代码路径 —— 形状守卫会先 reseed，但下一帧同形状的 2-D 帧会死在 `motion_mask = (diff > threshold).any(axis=2)`。**未复现。**
- **说明**：`cv2.VideoCapture` 实际总是返回 3 通道，触发概率低。

### `FocusTarget` 原地修改
- **证据**：`focus/manager.py:98-101` 直接改 `self.current.last_seen` / `.attention_score` / `.bbox`，违反 CLAUDE.md 声明的 immutability 原则。
- **未验证**：是否产生 observable aliasing（`recent` 中的引用、telemetry 快照是否会观察到变化）。**未复现。**

### `AnchorManager.observe([])` 的空列表安全性 —— **已测，确认为真**
- **证据**（受控）：baseline `{cup}` → `observe([])` × 4 → novelty **0.000 → 0.480**（伪造"物体消失"）；baseline 变空后 `observe([])` 走提前返回，novelty 冻结在 0.480，永不衰减。
- **结论**：恒等号成立，且第二条是 `desk_changed` 永久卡住的根因。**建议并入上方 OPEN-CONFIRMED 项。**
- **注**：main.py 侧已用 `detection_ran` 门控，闸门跳过帧不再调用 `observe`；但"检测运行且确实没物体"时仍会调用，这是**有效负观测**，语义正确。

---

## DEFERRED

| 项 | 说明 |
|---|---|
| B2 `set_intention()` 重构 | `main.py` 单帧两次 `SceneState.update()` 的结构问题；属重构，非 correctness fix |
| `_check_timeout()` 一帧执行两次 | 分析为绝对时间戳比较，无已知行为差异 |
| `_last_mode` / `_last_target_id` | write-only 字段，技术债 |
| A→B→A 帧形状抖动 | 每帧返回 `changed=True`、闸门失去意义并刷日志；已记为 observation，不加 hysteresis |
| `config.py BASE_WEIGHTS` (0.5) vs engine `BASE_WEIGHTS` (0.65) | 配置漂移；`config.py` 那份无人 import，**生效来源是 `attention/engine.py:15` 的 0.65** |
| `EnvironmentScanner` (`runtime/interest/scanner.py`) 孤儿模块 | 约 120 行，最后改动为初始提交 `952d41d`；master 的 `main.py` 仅留注释 "No separate scanner step" |
| novelty 块内 `from runtime.interest.anchor import SpatialAnchor` 死导入 | `main.py` 每帧重复执行一次无用 import |
| `focus/manager.py` 未使用的 `field` 导入 | 同上，dead-code cleanup 已排除 |
| P0008.1 Hardware Long-Run Validation | Scenario A/B/C（person 20min HOLD / 新物体 SWITCH / 离开 RELEASE）仍未实机验证 |

---

## 关于 P0008.1 长测的前置提示

以下项会直接影响长测的自变量：

1. ~~`desk_changed` 永久卡住 → L5 记忆被重复 `new_object` 淹没~~ —— **BI-10 已修（待实机复验）**
2. attention 电平触发 → 重复 `human_face` 进入 L5（OPEN-CONFIRMED，既存设计；实机 `ATTENTION new_object` 与 `human_face` 均≈每帧）
3. `_last_track_hit` 静止期不刷新 → 可能主动离开一个不动的人（OPEN-CONFIRMED；BI-14 后又多了一层 session 依赖，见对应条目）
4. ~~`AnchorManager` 20°/30° 网格不一致~~ —— **BI-11 已修（待实机复验）**
5. ~~PTZ 外层 8s gate 压制 1.5s tracking~~ —— **BI-12/13/14 已修 + 实机验证**；新的自变量是 **session 只在合格 anchor 上开启**（OPEN-CONFIRMED，需产品决策）

**Test B（真实离开）仍为 Pending Validation** —— 2026-09-24 那次运行用户在末尾 5.7 分钟持续在场（`faces=1` × 1152 帧），没有发生离开，离开路径未获实机验证。

**本文件不预设修复顺序；分类与优先级由 Known-Issue Triage 决定。**
