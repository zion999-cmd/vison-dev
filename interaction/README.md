# AI Agent Interaction Methods

> 跨 AI 编码代理的实用开发交互方法论——ChatGPT ↔ Claude Code 协作工作流。

---

## 这套方法论解决什么问题？

现代开发中，我们常常在多个 AI 编码工具之间切换：
- **ChatGPT**：擅长研究、规划、探索、生成初始设计
- **Claude Code**：擅长实现、文件操作、Git 工作流、测试
- 还有 Hermes、Codex、Cursor、Copilot 等

问题是：**每次切换工具，上下文丢失，工作断裂。**

这套方法论提供了一组协议、模板和工作流模式，让你在多个 AI 工具之间无缝切换，保持开发连续性。

## 核心理念

源自 AgentFabric 的工程哲学：

1. **Single Source of Truth** — 所有 Agent 读取同一份项目记忆，不做重复传递
2. **Contracts Before Code** — 在写代码之前，先定义契约（输入/输出/职责/所有权）
3. **Loop Driven Development** — 业务循环是产品，功能只是接口
4. **Handoff over Continuation** — 显式交接优于隐式继承，下一个 Agent 应该能独立启动

## 两层交接体系：Proposal + Handoff

这套方法论区分两个层次的 Agent 间信息传递：

```
ChatGPT ── Proposal ──────> Claude Code    （设计传递：这个阶段要构建什么）
   │                            │
   └── Handoff ───────────────> │            （状态同步：今天做到了哪里）
                                │
Claude Code ── Handoff ────> ChatGPT         （状态同步：实现了什么，请求审查）
```

| | Proposal | Handoff |
|------|----------|---------|
| **粒度** | 开发阶段/能力 | 单次会话 |
| **方向** | ChatGPT → Claude Code（单向） | 双向 |
| **详细度** | 完整架构、数据流、边界、完成标准 | 500-1000 字摘要 |
| **生命周期** | 永久积累 | 每次覆盖 |
| **文件位置** | `proposals/P000X-xxx.md` | `context/handoff.md` |
| **类比** | 建筑设计图 | 施工日志 |

## 目录

| 文件 | 用途 | 什么时候读 |
|------|------|-----------|
| [chatgpt-system-prompt.md](chatgpt-system-prompt.md) | ChatGPT 系统提示词配置：让它知道自己的角色 | **首次使用前（必须）** |
| [proposal-driven-development.md](proposal-driven-development.md) | ChatGPT → Claude Code 设计传递：Proposal 怎么写、怎么用 | 启动新开发阶段前 |
| [handoff-protocol.md](handoff-protocol.md) | ChatGPT ↔ Claude Code 核心交接协议 | 每次切换工具前 |
| [prompt-templates.md](prompt-templates.md) | 可复用的 Prompt 模板（含 Proposal 生成模板） | 准备发送给下一个 Agent 时 |
| [context-sync.md](context-sync.md) | 多 Agent 上下文一致性维护 | 项目初始化 + 定期维护 |
| [workflow-patterns.md](workflow-patterns.md) | 常见多 Agent 协作模式 | 规划阶段、任务分解时 |

## 快速开始

### 第零步：配置 ChatGPT 系统提示词（一次性）

```
将 chatgpt-system-prompt.md 中的配置复制到：
- ChatGPT Settings → Custom Instructions（全局），或
- ChatGPT Project → Instructions（项目级，推荐）

这一步告诉 ChatGPT：你是设计者，不是实现者。
没有这一步，ChatGPT 的输出 Claude Code 用不了。
```

### 启动新开发阶段：ChatGPT → Proposal → Claude Code

```
1. 在 ChatGPT 中：
   "请基于项目上下文，生成一份 Proposal (P000X)，
    包含目标、架构、边界、文件结构、完成标准。
    特别关注 NOT Included（明确不做什么）。"

2. ChatGPT 输出 → 保存到 proposals/P000X-xxx.md
   人工审核确认 → Status 改为 Accepted

3. 在 Claude Code 中：
   "请读取 CLAUDE.md 和 proposals/P000X-xxx.md。
    重点关注 Boundaries 和 Success Criteria。
    开始实现。"
```

### 日常开发：ChatGPT ↔ Claude Code via Handoff

```
从 ChatGPT 切到 Claude Code:
  1. ChatGPT: "请生成一份 handoff 摘要"
  2. 写入 context/handoff.md
  3. Claude Code: "请读取 context/handoff.md，继续开发"

从 Claude Code 切到 ChatGPT:
  1. Claude Code: "请更新 context/handoff.md"
  2. 粘贴到 ChatGPT 新对话
  3. 附带 context/ 目录的关键文件
```

## 适用项目类型

这套方法论适用于具有以下特征的项目：

- 使用 `context/` 目录作为项目记忆（如 AgentFabric）
- 有 CLAUDE.md / PROJECT.md 作为项目指令文件
- 多个 AI 工具参与开发
- 需要保持架构决策和设计意图的连续性

没有 `context/` 目录？参考 [context-sync.md](context-sync.md) 中的最小化设置指南。

## 维护

这套方法论本身是活的文档。每当你发现一个新的有效交互模式，更新对应的文件。

## 项目文档索引

本方法论已登记到项目文档系统。详见 [doc/README.md](../doc/README.md) 的"开发指南"章节。

相关入口：
- [CLAUDE.md](../CLAUDE.md) — 项目指令和技术约定
- [doc/architecture.md](../doc/architecture.md) — 系统架构总览
