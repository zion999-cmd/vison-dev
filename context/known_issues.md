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
| Code regression | **281 passed**, 0 failed（`conda run -n vision-dev python -m pytest -q`） |
| Hardware baseline | **NOT validated** —— 本会话所有结论均来自单元测试与受控复现，未在实机运行验证 |
| 分支 | `fix/frame-diff-and-dead-code` |
| 已提交前序 | `73f8ac4` `21d7d47` `ad77f22` |

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

---

## OPEN-CONFIRMED

### `desk_changed` 在"桌上无物体"时永久卡住
- **证据**：复现 —— latch 后 50 帧仍为 `True`；novelty 在 baseline 变空后被冻结在 0.480（`anchor.py:167-172` 的 "first observation" 提前返回使衰减分支永不执行），而 `main.py` 的 novelty 读取要求 `objects` 非空 → 永远传 `None` → 无释放路径。
- **影响**：attention 每帧发 `new_object`（0.65 基数 ≥ 有效阈值 0.42）→ 200 条 L5 EpisodicMemory 被重复条目淹没；`presence.novelty` 被钉在 0.3。
- **归属**：BI-03 使其**可达**，BI-07/Batch 1 移除了原先（偶然的）清除路径。
- **修复需要决策**：novelty 在 baseline 为空时的语义（AnchorManager 侧），不是本地修正。

### `AnchorManager` 的 pan 网格与 novelty 查找网格不一致
- **证据**：代码确认 —— `main.py:131` 构造 `AnchorManager(pan_spacing=20, tilt_spacing=15)`，而 `main.py:358` 的 novelty 查找把当前 pan snap 到 **30°** 网格。anchors 存在 20° 网格上，故仅在 60 的倍数附近（约 27% 的 pan 位置）可能命中。
- **影响**：anchor-novelty 路径在大部分 pan 位置静默失效 → `desk_changed` 很难 latch。
- **归属**：既存（两个常量均在 HEAD）。

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

以下 OPEN-CONFIRMED 项会直接影响长测的自变量，建议在进入长测前决定：

1. `desk_changed` 永久卡住 → L5 记忆被重复 `new_object` 淹没
2. attention 电平触发 → 重复 `human_face` 进入 L5
3. `_last_track_hit` 静止期不刷新 → 可能主动离开一个不动的人
4. `AnchorManager` 20°/30° 网格不一致 → novelty 路径大部分 pan 位置失效

**本文件不预设修复顺序；分类与优先级由 Known-Issue Triage 决定。**
