P0008: Observation Intent Engine

Status: Proposed
Date: 2026-07-13
Depends on: P0007C

Objective

Phase 7 已完成了机器人视觉 Runtime 的基础闭环：

Perception
Entity Registry
Attention
Interest
Curiosity
Familiarity
Role
Importance
Quality Gate

系统已经能够自主形成稳定的注意力生态。

Phase 8 不让 LLM 接管 Runtime，而是引入 Observation Intent（观察意图）。

LLM 的职责不是替代 Attention，而是根据机器人当前角色、环境和任务，周期性地产生一组高层观察目标，为 Runtime 提供可动态调整的先验。

Background

目前 Runtime 的 Role 为固定权重，例如：

person = 1.0
chair  = 0.1
bottle = 0.05

这种设计能够保证系统稳定运行，但缺少任务上下文。

例如：

陪伴机器人关注人和互动；
巡检机器人关注设备和异常；
安防机器人关注门窗和陌生人。

这些差异不应修改 Runtime，而应来自高层策略。

LLM 已具备丰富的世界知识和先验经验，可以生成观察策略，但不应直接控制 PTZ 或底层行为。

Architecture
                  Persona / Mission
                         │
                         ▼
                Observation Intent
                    (LLM, low frequency)
                         │
                         ▼
               Mission Role Cache
                         │
Intrinsic Role ──────────┤
                         ▼
                 Effective Role
                         │
                         ▼
Interest → Curiosity → PTZ → Perception
Design
1. Observation Intent

新增 Observation Intent 模块。

输入：

当前 Persona
当前环境摘要
当前 Importance Top-K
当前 ACTIVE Entity 列表
最近 Interaction 摘要（可选）

输出：

{
  "mission_role": {
    "person": 0.4,
    "pet": 0.3,
    "phone": 0.2
  },
  "expires_sec": 300,
  "reason": "Companion robot should prioritize human interaction."
}

LLM 不输出动作。

LLM 不输出 PTZ 指令。

LLM 不输出 Entity ID。

仅输出类别级观察偏置（Mission Role）。

2. Mission Role Cache

新增 Runtime Cache：

MissionRole

特点：

有 TTL（默认 5 分钟）
自动过期
可重新生成
不持久化
3. Effective Role

Runtime 使用：

EffectiveRole =
IntrinsicRole
+
MissionRole

Curiosity 不需要修改公式。

仅使用 EffectiveRole 进行计算。

4. Observation Trigger

LLM 不持续运行。

触发条件建议：

Runtime 启动
Persona 改变
环境发生明显变化（可选）
Mission Role TTL 到期
人工请求刷新

默认刷新周期：

300 秒
5. Prompt Contract

LLM Prompt 必须满足：

输入：

Persona
Environment Summary
Importance Summary
ACTIVE Entity Summary

输出：

严格 JSON：

{
  "mission_role": {
    "<class_name>": 0.0-1.0
  },
  "expires_sec": 300
}

禁止：

输出自然语言控制
输出 PTZ 指令
输出推理过程
输出 Runtime 修改建议
Boundaries
Included
Observation Intent 模块
Mission Role Cache
Prompt Contract
Runtime Role 合并
TTL 管理
Persona 配置接口
NOT Included (CRITICAL)
❌ 不开发 Value Engine
❌ 不开发 Memory
❌ 不开发 Soul
❌ 不开发 Emotion
❌ 不修改 Interest Engine
❌ 不修改 Curiosity Engine
❌ 不修改 Importance Engine
❌ 不允许 LLM 控制 PTZ
❌ 不允许 LLM 控制 Runtime 状态机
❌ 不允许 LLM 修改 Entity 数据
Success Criteria
Runtime 在不修改任何核心逻辑的情况下，可根据不同 Persona 自动调整观察偏好。
更换 Persona 后，仅 Mission Role 发生变化，其余 Runtime 保持一致。
LLM 输出仅影响 Effective Role，不直接产生行为控制。
Observation Intent 可定期刷新，并在 TTL 到期后自动失效。
相同环境下，"陪伴机器人"、"巡检机器人"、"安防机器人"能够表现出不同但可解释的注意力分布。
设计原则

Runtime 决定"怎么观察"，LLM 决定"为什么观察"。

这是 P0008 唯一需要坚持的边界。