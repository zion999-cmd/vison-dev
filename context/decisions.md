# Architecture Decision Records

> 实现过程中做的架构决策。按时间顺序记录。

## ADR-001: Entity 系统使用 HSV 直方图签名匹配

- 日期: 2026-06
- 状态: 已采纳
- 决策: Entity 身份识别使用 HSV 直方图（avg_H, avg_S, avg_V, w, h）而非 YOLO label
- 理由: 同一类别的不同个体（如两个人）需要区分身份，YOLO label 做不到。HSV 签名不受 PTZ 坐标变化影响。
- 替代方案: 考虑过 CLIP embedding 匹配，但增加了推理延迟，在当前阶段过度。

## ADR-002: VLM/LLM 仅在 L6 使用

- 日期: 2026-06
- 状态: 已采纳
- 决策: YOLO + YuNet + HSV 在核心层，VLM 仅用于 L6 cognition trigger 和可选的 anchor verification
- 理由: 核心层需要实时性（FPS），VLM/LLM 调用需要秒级延迟。将 VLM 限制在低频触发层保证主循环不受影响。
- 替代方案: 考虑过每个 entity 都用 VLM 描述，但延迟和成本不可接受。

## ADR-003: Importance Phase 7A 仅观测，不定义公式

- 日期: 2026-06
- 状态: 已采纳
- 决策: Phase 7A 仅记录"哪些 entity 引发了哪些下游事件"，不定义重要性公式
- 理由: 在充分数据积累之前定义公式可能过早优化。先观测再建模。
- 替代方案: 直接设计 Value Engine 公式——但有陷入过早设计的风险。

## ADR-004: SG90 舵机角度限制从 180→170(tilt) / 170→165(pan)

- 日期: 2026-06
- 状态: 已采纳
- 决策: 降低舵机极限角度，避免在机械端点长时间保持导致电位器磨损和位置漂移
- 理由: 在 180° 保持时出现"不停旋转"故障，降低后问题消失。
- 替代方案: 考虑更换数字舵机，但当前 SG90 限制角度后运行稳定，无需更换。

## ADR-005: 采用 interaction/ 多 Agent 交互方法论

- 日期: 2026-07
- 状态: 已采纳
- 决策: 项目采用 Proposal-Driven Development + Handoff Protocol 作为标准开发流程。ChatGPT 负责设计（Proposal），Claude Code 负责实现。每次会话结束时更新 context/handoff.md。
- 理由: 项目开发中频繁在 ChatGPT 和 Claude Code 之间切换，需要结构化的上下文传递机制避免信息丢失。Proposal 提供设计护栏（特别是 NOT Included 边界），Handoff 提供会话连续性。
- 替代方案: 考虑过直接粘贴聊天记录（信息噪音大）或完全在一个工具中开发（各工具擅长领域不同，无法替代）。

## ADR-006: Phase 7B Entity Quality Gate — 分离信号与噪声

- 日期: 2026-07
- 状态: 已采纳
- 决策: Importance 计算前增加 Entity Quality Gate。只允许 seen≥3、avg_confidence≥0.5、status=ACTIVE 的实体进入 Importance。新增轻量级 merge 引擎减少 entity fragmentation（不引入 embedding 模型）。
- 理由: Phase 7A 运行后发现 YOLO 误检（clock/train）进入 Importance ranking，entity fragmentation 导致 signal 不稳定。Quality gate 将 Importance 限制在"可信 Entity 空间"。
- 替代方案: 考虑过直接加 Value formula 过滤——但这会引入未经验证的价值假设。先做信号提纯，再做价值定义。

## ADR-007: P0008 Role 可组合化 — Intrinsic + Mission 双层架构

- 日期: 2026-07
- 状态: 已采纳
- 决策: Role 从固定的 IntrinsicRole 改为 EffectiveRole = IntrinsicRole + MissionRole（clamped [0,1]）。MissionRole 由 MissionRoleProvider 抽象接口提供，当前实现有 LLMProvider 和 RuleProvider（及 null provider），未来可扩展 Cloud/Human/Learning provider。
- 理由:
  - Role 不再是 Runtime 固定的一部分，而是可组合的。Runtime 和"认知策略"彻底解耦。
  - LLM 是 Advisor 不是 Controller——只输出 class-level 权重，不控制 PTZ/Entity/Runtime。
  - Mission 有 TTL，过期自动归零。去掉 LLM → EffectiveRole = IntrinsicRole，系统完全不受影响。
  - Persona 是 YAML config 不是 Prompt——新增机器人身份只需新建 YAML，无需改 Runtime 或 prompt。
- 替代方案: 考虑过直接在 Curiosity 公式中加 Mission 偏置——但这会污染核心公式。双层 Role 架构保持了 Curiosity 公式不变（仅 role 变量从 intrinsic 变为 effective）。
