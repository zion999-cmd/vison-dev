# Proposal-Driven Development: ChatGPT → Claude Code

> Proposal 是 ChatGPT 写给 Claude Code 的**开发设计文档**。
> 比 handoff.md 更详细，只针对 ChatGPT → Claude Code 方向。
> 一个 Proposal = 一个开发阶段/能力的完整设计思想。

---

## 什么是 Proposal？

Proposal 是 ChatGPT 生成的**阶段性开发设计**，回答：

> "这个阶段要构建什么能力？怎么做？边界在哪里？做完的标准是什么？"

```
ChatGPT (设计思想)               Claude Code (实现)
     │                                │
     │  P0001-agentfabric-reposition  │
     ├──────────────────────────────> │  重新定位项目
     │                                │
     │  P0002-workspace-ia            │
     ├──────────────────────────────> │  构建工作区信息架构
     │                                │
     │  P0003-Trust-UI                │
     ├──────────────────────────────> │  实现信任 UI 系统
     │                                │
     │  P0004-runtime-control-plane   │
     ├──────────────────────────────> │  定义执行契约
     │                                │
     │  P0005-Business-Data-Foundation│
     ├──────────────────────────────> │  建立数据基础层
     │  P0005.1-jd-connector          │
     ├──────────────────────────────> │  京东连接器（验证）
     │  P0005.2-discovery-architecture│
     ├──────────────────────────────> │  发现驱动架构（扩展）
     │  P0005.3-capability-generator  │
     └──────────────────────────────> │  能力生成器（自动化）
```

---

## Proposal vs Handoff：关键区别

| 维度 | Proposal | Handoff |
|------|----------|---------|
| **粒度** | 开发阶段/能力 | 单次会话 |
| **详细程度** | 完整架构、目录结构、数据流、完成标准 | 500-1000 字会话摘要 |
| **方向** | ChatGPT → Claude Code（单向） | 双向（ChatGPT ↔ Claude Code） |
| **生命周期** | 永久积累（项目历史） | 每次会话覆盖 |
| **包含** | 背景、目标、架构、边界、排除项、完成标准 | 做了什么、下一步、阻塞、关键上下文 |
| **文件命名** | `proposals/P0001-xxx.md` | `context/handoff.md` |
| **状态** | Proposed → Accepted → Implemented | 无状态（总是最新） |
| **何时读** | 开始新开发阶段时 | 每次会话开始时 |

### 层次关系

```
Proposal (P0005)
  └── 定义整体架构和边界
        │
        ├── Handoff #1  会话摘要：今天建立了 Evidence Store
        ├── Handoff #2  会话摘要：今天实现了 JD Parser
        ├── Handoff #3  会话摘要：今天完成了 Normalizer 集成
        └── Handoff #4  会话摘要：P0005.1 完成，测试通过
```

**Proposal 是地图，Handoff 是路标。** Proposal 告诉你"这座城市的规划是什么"，Handoff 告诉你"今天走到了哪条街"。

---

## Proposal 的标准结构

一个完整的 Proposal 包含以下部分：

### 必须包含

```markdown
# PXXXX: [一句话标题]

**Status**: Proposed | Accepted | Implemented
**Date**: YYYY-MM-DD
**Depends on**: PXXXX (可选)

---

## Objective / 目标
[3-5 句话说明这个 Proposal 要达成什么]

## Background / Motivation / 背景
[为什么需要这个能力？当前状态是什么？]

## Architecture / 架构
[ASCII 图或文字描述：这个能力在系统中的位置]

## Design / 设计
[核心设计决策、数据结构、流程]

## Boundaries / 边界
### Included (包含)
- [明确列出做什么]

### NOT Included (不包含)
- [明确列出不做什么——这是最关键的部分]

## Directory Structure / 文件结构
[需要创建/修改的文件清单]

## Success Criteria / 完成标准
1. [可验证的标准]
2. [不是"代码写完了"，而是"能力跑通了"]
```

### 可选但推荐

```markdown
## Relationship / 与其他 Proposal 的关系
[这个 Proposal 依赖哪些？被哪些依赖？]

## Migration / 迁移计划
[如果需要从旧代码迁移]

## Risks / 风险
[已知风险和缓解方案]
```

---

## Proposal 的编号规则

```
P0001              ← 独立的开发阶段
P0002              ← 独立的开发阶段
P0003              ← 独立的开发阶段
P0004              ← 独立的开发阶段
P0005              ← 父 Proposal：定义整体架构
  P0005.1          ← 子 Proposal：验证第一步
  P0005.2          ← 子 Proposal：验证第二步（平台无关性）
  P0005.3          ← 子 Proposal：自动化/扩展
```

### 何时用父 Proposal vs 子 Proposal

| 场景 | 使用 |
|------|------|
| 新的独立能力 | `P000X` |
| 在已有 Proposal 的架构内，实现一个子步骤 | `P000X.Y` |
| 对已有 Proposal 的修正/补充 | `P000X.Y` |
| 修复 Bug（不涉及设计变更） | 不需要 Proposal，直接修 |

### 子 Proposal 的核心价值：验证

子 Proposal 不是"把大任务拆小"。子 Proposal 是**每一步验证上一层假设**：

```
P0005:  定义 Business Data Pipeline 架构（假设：平台无关的数据层可行）
  ↓
P0005.1: 实现 JD 连接器 → 验证 Pipeline 链路是否跑通
  ↓
P0005.2: 接入 Tmall → 验证 Pipeline 是否真的平台无关
  ↓
P0005.3: 自动化能力生成 → 验证是否能从 Discovery 自动生成 Connector
```

每一步验证上一步的假设。如果 P0005.1 跑不通，P0005 的架构需要修正。

---

## Proposal 的生命周期

```
Proposed ──────> Accepted ──────> Implemented
   │                 │                  │
   │                 │                  └── 代码已合并，测试通过
   │                 └── 设计方案已确认，可以开始实现
   └── ChatGPT 生成，等待人工审核确认
```

### 状态转换规则

1. **Proposed**: ChatGPT 生成 → 保存到 `proposals/` → 等待人工确认
2. **Accepted**: 人工审核后确认 → Claude Code 可以开始实现
3. **Implemented**: 代码合并 + 测试通过 → 更新状态

同一次开发迭代中，可能有多个 Proposal 处于不同状态：
- P0005: Accepted（正在实现）
- P0005.1: Implemented（已完成）
- P0005.2: Proposed（等待确认）
- P0006: Proposed（下一阶段规划）

---

## Key Pattern: "NOT Included" Is The Most Important Section

Proposal 中最重要的部分不是"做什么"，而是**"明确不做什么"**。

### 为什么？

AI 实现时会自然地"多做"——它看到相关的东西就想顺便做了。
"明确不做什么"是**防越界护栏**。

### 示例

来自 P0005：
```
P0005 不包括:

不要开发:
  Metrics
  Decision
  Skill
  Experience
  Review
全部不碰。
```

来自 P0004：
```
Runtime must never:
  select business skill
  modify execution plan
  replace policy
  change execution order
```

### 写法

好的"不包含"是**具体的、可验证的**：
- ✅ "不实现 Metrics 计算——只负责数据获取和存证"
- ✅ "不修改 existing normalizer——复用现有 normalizeSignal()"
- ❌ "不做过早优化"（太模糊）
- ❌ "保持简单"（太模糊）

---

## Proposal 与项目记忆的关系

```
proposals/                    ← ChatGPT 输出（设计思想，只增不改）
    P0001-xxx.md
    P0002-xxx.md
    ...

context/                      ← 所有 Agent 维护（项目状态，持续更新）
    current_state.md           ← 当前在实现哪个 Proposal
    decisions.md               ← 实现过程中做的 ADR
    handoff.md                 ← 每次会话的摘要

CLAUDE.md                     ← 项目指令（Claude Code 入口）
```

### 实现过程中的交互

```
1. 用户确认 Proposal → 状态改为 Accepted
2. Claude Code 读取 Proposal + CLAUDE.md
3. Claude Code 开始实现
4. 每次会话结束时，Claude Code 更新 context/handoff.md
5. 遇到设计偏差 → 记录到 context/decisions.md
6. Proposal 完成 → 状态改为 Implemented
```

---

## ChatGPT 生成 Proposal 的 Prompt 模板

### 启动一个新的开发阶段

```
我需要在项目 [项目名] 中启动一个新的开发阶段。

项目背景：
- 项目类型: [描述]
- 技术栈: [列出]
- 当前状态: [已完成的能力 / 进行中的 Proposal]
- 项目哲学: [关键约束]

我想实现的能力：[一句话描述]

请生成一份 Proposal（P000X），包含：

1. **Objective**: 这个阶段要达成什么？
2. **Background**: 为什么需要这个能力？当前状态是什么？
3. **Architecture**: 这个能力在系统中的位置（ASCII 图）
4. **Design**: 核心设计决策、数据流、关键数据结构
5. **Boundaries**: 
   - 明确包含什么
   - **明确不包含什么**（这是最重要的部分）
6. **Directory Structure**: 需要创建/修改的文件
7. **Success Criteria**: 可验证的完成标准（不是"代码写完"，而是"能力跑通"）
8. **Dependencies**: 依赖哪些已有 Proposal？

格式要求：
- 使用 AgentFabric Proposal 标准格式
- 特别关注"边界"——明确画出不越界的线
- 如果有子步骤，说明每个子步骤验证什么假设

输出纯 Markdown，我会保存到 proposals/P000X-xxx.md。
```

### 为已有 Proposal 生成子步骤

```
基于 P0005 的架构，请生成 P0005.1。

P0005 定义了 [父 Proposal 的核心架构]。

P0005.1 的目标是 [实现第一个具体实例]，验证 [父 Proposal 的假设]。

要求：
- 继承 P0005 的架构约束
- 具体到文件级别（哪些文件创建/修改）
- 明确复用 P0005 的哪些部分，不碰哪些部分
- 验证完成后，确认了什么假设
```

---

## Claude Code 读取 Proposal 的 Prompt 模板

```
请读取以下文件加载上下文：
1. CLAUDE.md
2. proposals/P000X-xxx.md（本次要实现的 Proposal）
3. context/current_state.md

重点关注 Proposal 中的：
- Boundaries / NOT Included（不要越界）
- Directory Structure（文件清单）
- Success Criteria（完成标准）

开始实现前，请用 3-5 句话总结：
- 你要实现什么？
- 边界在哪里（不能碰什么）？
- 第一步做什么？
```

---

## 反模式

### ❌ Proposal 写成实现手册

```
错误：Proposal 中写具体代码
  "在 acquisition/cdp-client.ts 第 42 行，使用 page.route('**/szgateway...')"
正确：Proposal 中写设计意图
  "通过 CDP 拦截 SPA 的 API 调用，捕获 raw response body"
原因：实现细节留给 Claude Code，Proposal 保持设计级别
```

### ❌ Proposal 越界指导

```
错误：Proposal 规定 Claude Code 应该用哪个函数、怎么命名变量
原因：这是实现细节，Claude Code 读 CLAUDE.md 已经知道命名规范
正确：Proposal 定义"能力的边界"，Claude Code 决定"实现的细节"
```

### ❌ 没有 "NOT Included" 的 Proposal

```
错误：Proposal 只写了要做什么
正确：Proposal 必须明确不做什么
原因：没有边界的设计文档 = 没有护栏的高速路 = AI 会开到哪里去不知道
```

### ❌ Proposal 永远停留在 Proposed

```
错误：Proposal 生成后不更新状态
正确：Accepted → 开始实现 → Implemented → 归档
原因：状态是给下一个 Agent 的信号——"这个可以开始做了" vs "这个还在讨论"
```

---

## 一句话总结

> **Handoff 告诉 Claude Code "今天发生了什么"。**
> **Proposal 告诉 Claude Code "这个阶段要构建什么"。**
>
> Handoff 是状态同步。Proposal 是设计传递。
> 两者互补，不能互相替代。
