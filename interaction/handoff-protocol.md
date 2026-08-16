# Handoff Protocol: ChatGPT ↔ Claude Code

> 核心交接协议。每次在 AI 编码工具之间切换时，遵循此协议。

---

## 协议总览

```
┌──────────┐    Handoff Packet    ┌─────────────┐
│  ChatGPT  │ ──────────────────> │  Claude Code │
│  (Plan)   │ <────────────────── │  (Implement) │
└──────────┘    Handoff Packet    └─────────────┘

Handoff Packet = context/handoff.md + context/current_state.md
```

交接不是"传递所有上下文"——上下文太大，token 太贵。
交接是**告诉下一个 Agent 从哪里开始**。

---

## Phase 1: 发送方 —— 生成 Handoff

### 步骤 1：确定交接原因

在生成 handoff 之前，明确**为什么**切换：

| 原因 | 典型场景 |
|------|---------|
| 能力边界 | ChatGPT 需要写文件但做不到 → Claude Code |
| 成本优化 | 大规模文件编辑更适合 Claude Code |
| 研究 vs 实现 | ChatGPT 做完研究规划 → Claude Code 实现 |
| 审查验证 | Claude Code 实现完 → ChatGPT 独立审查 |
| 模型优势 | 利用各模型的独特优势 |

### 步骤 2：更新项目记忆

发送方在交接前必须更新：

```
context/handoff.md        ← 本次会话摘要（必须）
context/current_state.md  ← 更新进行中/已完成/阻塞（必须）
context/decisions.md      ← 有新决策时更新
context/status.json       ← 机器可读状态（可选）
```

### 步骤 3：写出 Handoff Packet

`context/handoff.md` 格式（500-1000 字）：

```markdown
# Handoff: [日期] — [发送方] → [接收方]

## 当前状态
- 分支: feature/xxx
- 最后提交: abc1234
- 测试: 42 pass / 0 fail
- 覆盖率: 87%

## 本次会话做了什么
### 新增
- xxx 模块：做什么的，为什么加

### 修改
- xxx 文件：改了什么，为什么改

### 删除
- xxx：为什么删除

## 当前阻塞
- 问题描述
- 已尝试的方案
- 建议的下一步

## 下一步任务
1. [ ] 任务 A — 优先级高
2. [ ] 任务 B — 依赖任务 A 完成
3. [ ] 任务 C — 可并行

## 关键上下文（给下一个 Agent）
- 需要知道的设计决策
- 需要注意的约束条件
- 相关文件的路径和用途
- 不要重复尝试的方案

## 交接检查清单
- [ ] current_state.md 已更新
- [ ] decisions.md 已更新（如有新决策）
- [ ] 所有测试通过
- [ ] 没有未提交的临时文件
- [ ] CLAUDE.md / PROJECT.md 反映最新架构
```

---

## Phase 2: 接收方 —— 加载 Handoff

### 步骤 1：读取入口

接收方 Agent 的第一条指令：

```
请按顺序读取以下文件，加载项目上下文：
1. CLAUDE.md（或 PROJECT.md）  — 项目整体架构和约定
2. context/current_state.md   — 当前进度和状态
3. context/handoff.md         — 上一次会话的交接摘要

读取完后，用 3-5 句话总结你理解的项目状态，确认无误后开始工作。
```

### 步骤 2：验证状态一致性

接收方应快速验证 handoff 中的声明是否与实际代码一致：

```bash
git log --oneline -5          # 确认最后提交
npm test                       # 确认测试通过
npm run typecheck              # 确认类型检查通过
```

如果发现不一致，**先报告差异再开始工作**，不要假设 handoff 是完全准确的。

### 步骤 3：确认理解后开始

接收方理解上下文后，回复格式：

```
已加载上下文。我理解：
- 当前阶段：[Phase X]
- 进行中：[任务描述]
- 阻塞：[阻塞描述]
- 下一步：[下一步描述]

我将从 [具体任务] 开始。确认？
```

---

## Phase 3: 交接检查清单

### 发送方检查清单

- [ ] `context/handoff.md` 已更新（500-1000 字）
- [ ] `context/current_state.md` 已更新（版本、已完成、进行中、阻塞）
- [ ] `context/decisions.md` 已更新（新 ADR 条目）
- [ ] 所有测试通过（`npm test`）
- [ ] 类型检查通过（`npm run typecheck`）
- [ ] 没有未跟踪的临时文件（`git status` 干净或只有预期文件）
- [ ] Handoff 中包含"不要重复尝试的方案"
- [ ] Handoff 中包含"关键上下文"

### 接收方检查清单

- [ ] 已读取 CLAUDE.md
- [ ] 已读取 context/current_state.md
- [ ] 已读取 context/handoff.md
- [ ] 已验证 git 状态一致
- [ ] 已验证测试通过
- [ ] 已验证类型检查通过
- [ ] 已确认理解当前状态（口头回复给用户）
- [ ] 已明确下一步做什么

---

## 反模式：不要这样做

### ❌ 粘贴整个对话

```
错误：把 ChatGPT 的整个对话粘贴给 Claude Code
原因：大量冗余信息，token 浪费，关键信息淹没在噪音中
正确：提炼成 500-1000 字的结构化 handoff
```

### ❌ 零上下文切换

```
错误："Claude，继续做刚才 ChatGPT 在做的事"
原因：Claude Code 不知道"刚才"是什么
正确：至少提供 handoff.md 或一段结构化摘要
```

### ❌ 重复传递相同信息

```
错误：每次切换都把项目架构从头解释一遍
原因：CLAUDE.md 已经包含架构信息，只需让 Agent 读取它
正确：Handoff 只包含"增量"——这次会话新增了什么
```

### ❌ 只传递"做了什么"，不传递"为什么"

```
错误："创建了 utils/math.ts"
正确："创建了 utils/math.ts，从 metrics/ 提取出来的公共数学函数，
      因为 metrics/ 中有 3 个文件都在重复实现加权平均"
```

---

## 紧急交接

当需要紧急切换且没有时间写完整 handoff 时，使用最小化版本：

```markdown
# Quick Handoff: [日期时间]

## 当前状态
- 正在做: [一句话]
- 阻塞于: [一句话 / 无]
- 测试: [pass/fail/unknown]

## 下一步
1. [最紧急的一件事]

## ⚠️ 注意事项
- [最重要的一个警告]
```

这个最小版本可以在 2 分钟内写完，足以让下一个 Agent 继续工作。
