# Context Sync: 多 Agent 上下文一致性

> 如何让 ChatGPT、Claude Code、Hermes、Codex 等所有 Agent 读取相同的项目记忆，
> 不重复传递，不产生分歧。

---

## 核心原则

### 推式 vs 拉式上下文

```
❌ 推式（Push）: 每次切换把全部上下文推给下一个 Agent
   问题: token 浪费，信息衰减，维护负担重

✅ 拉式（Pull）: 上下文写成文件存在项目里，Agent 自己读取
   优势: 单一事实来源，Agent 只读需要的部分
```

### 上下文分层

```
Layer 1: 静态（几乎不变）
  CLAUDE.md, PROJECT.md, philosophy.md, engineering_philosophy.md
  → Agent 每次都读，形成基础认知

Layer 2: 半静态（阶段性变化）
  context/decisions.md, context/architecture_snapshot.md
  → 有更新时读

Layer 3: 动态（每次会话变化）
  context/current_state.md, context/handoff.md, context/status.json
  → 每次切换必读
```

---

## 项目记忆最小化设置

如果你的项目还没有 `context/` 目录，从这几个文件开始：

### 必需的 3 个文件

```
project/
├── CLAUDE.md              # 项目指令文件（Agent 的入口）
└── context/
    ├── current_state.md   # 当前进度
    ├── decisions.md       # 架构决策记录
    └── handoff.md         # 上一次会话的交接摘要
```

### CLAUDE.md 最小模板

```markdown
# CLAUDE.md

## Project Identity
[项目名] 是 [一句话描述]。

## Architecture
[架构图或文字描述]

## Tech Stack
- Runtime: [Node.js / Python / ...]
- Database: [SQLite / PostgreSQL / ...]
- Testing: [Vitest / pytest / ...]

## Directory Layout
[关键目录和用途]

## Key Design Principles
1. [原则 1]
2. [原则 2]
3. [原则 3]

## File Standards
- Max 800 lines per file
- Max 50 lines per function
- [其他约定]

## Context Files
Agent 在开始工作前应读取：
1. context/current_state.md  — 当前进度
2. context/handoff.md        — 上次会话摘要
3. context/decisions.md      — 架构决策（有疑问时读）
```

### current_state.md 最小模板

```markdown
# Current State

## 版本
0.1.0

## 已完成
- [x] 项目初始化

## 进行中
- [ ] [当前任务]

## 下一步
1. [下一步任务 1]
2. [下一步任务 2]

## 阻塞
无
```

### decisions.md 最小模板

```markdown
# Architecture Decision Records

## ADR-001: [决策标题]
- 日期: 2026-07-03
- 状态: 已采纳
- 决策: [做了什么决定]
- 理由: [为什么]
- 替代方案: [考虑过但没选的方案]
```

### handoff.md 最小模板

```markdown
# Handoff: [日期]

## 当前状态
- 分支: main
- 测试: pass

## 本次做了什么
[3-5 句话]

## 下一步
1. [任务]
```

---

## 上下文同步工作流

### 每次会话开始：Agent 必须读取

```
1. CLAUDE.md                      — 理解项目结构
2. context/current_state.md       — 了解当前进度
3. context/handoff.md             — 了解上次做了什么
4. context/decisions.md（可选）    — 有架构疑问时
```

所有 Agent（ChatGPT、Claude Code、Hermes、Codex）在开始工作前，都应该执行这个读取序列。
对于 ChatGPT（不能直接读文件），由用户将文件内容粘贴或作为附件上传。

### 每次会话结束：Agent 必须更新

```
1. context/handoff.md             — 写会话摘要（必须）
2. context/current_state.md       — 更新进度状态（必须）
3. context/decisions.md           — 新决策条目（如有）
4. context/status.json            — 机器可读状态（可选）
```

---

## ChatGPT 特殊处理

ChatGPT 不能直接读写本地文件。使用以下变通方案：

### ChatGPT 读取上下文

```
用户操作：
1. 将 CLAUDE.md 和 context/handoff.md 拖拽为附件
2. 发送 Prompt：
   "请阅读附件的 CLAUDE.md 和 handoff.md，理解项目上下文。
    然后读取 current_state.md（我接下来粘贴）。
    完成后，用 3-5 句话总结你理解的项目状态。"
```

### ChatGPT 写入上下文

```
用户操作：
1. 让 ChatGPT 用模板 B1 生成 Markdown
2. 复制输出内容
3. 手动写入对应文件（或让 Claude Code 写入）

或者：

1. 切换到 Claude Code
2. 发送："将以下内容写入 context/handoff.md: [粘贴 ChatGPT 输出]"
```

---

## 上下文冲突解决

当两个 Agent 产生的上下文不一致时：

### 场景 1：Claude Code 的实现与 ChatGPT 的计划不同

```
解决：在 context/decisions.md 中记录偏差和理由
"ADR-005: 偏离原计划，使用方案 B 而非方案 A
 理由: 实现时发现方案 A 与现有架构冲突，方案 B 更简洁"
```

### 场景 2：两个 Agent 更新了同一个文件

```
解决：Git 优先。Git 是最终的冲突仲裁者。
- 如果 git diff 显示冲突 → 人工裁决
- 如果 git diff 是干净的 → 以仓库状态为准
```

### 场景 3：handoff.md 与 actual code 不一致

```
解决：代码优先。代码是最终的真相来源。
- 实际代码 > handoff.md 的描述
- 发现不一致时，更新 handoff.md 使其与代码一致
```

---

## 上下文文件维护纪律

### 文件大小限制

| 文件 | 最大大小 | 超过后怎么办 |
|------|---------|------------|
| `current_state.md` | 200 行 | 将旧的"已完成"移到 `history.md` |
| `decisions.md` | 50 条 ADR | 将过时的标记为"已废弃" |
| `handoff.md` | 100 行 | 每次会话覆盖（不是追加） |

### 更新频率

| 文件 | 更新频率 | 谁更新 |
|------|---------|--------|
| `current_state.md` | 每次会话 | 会话结束时的 Agent |
| `handoff.md` | 每次会话 | 会话结束时的 Agent |
| `decisions.md` | 每次设计决策 | 做决策的 Agent |
| `CLAUDE.md` | 架构变化时 | 手动 / Claude Code |
| `status.json` | 每周 | Claude Code（自动化） |

### 谁来负责？

默认规则：
- **文件在哪个 Agent 结束会话，那个 Agent 负责更新上下��**
- 如果 ChatGPT 结束（不能写文件），由用户手动转移内容或在下次 Claude Code 会话中写入

---

## 反模式

### ❌ 上下文繁殖

```
错误：为每个 Agent 维护单独的上下文副本
  context/
    chatgpt-context.md     ← 不需要
    claude-context.md      ← 不需要
    hermes-context.md      ← 不需要
正确：一份上下文，所有 Agent 共用
```

### ❌ 过度详细

```
错误：handoff.md 写到 3000 字，包含每行代码的解释
正确：500-1000 字，聚焦关键决策和异常，代码细节读代码本身
```

### ❌ 上下文腐化

```
错误：handoff.md 还是 3 周前的内容
正确：每次会话必须更新 handoff.md，过期的上下文比没有上下文更危险
```

### ❌ 只写“做了什么”，不写“还有什么没做”

```
错误：handoff 只列已完成任务
正确：明确列出"尝试了但没成功"和"还没开始但很重要"的项
```
