# Prompt Templates

> 可复用的 Prompt 模板，用于各方向的 Agent 交接。复制 → 填空 → 发送。

---

## 模板索引

| 模板 | 发送方 → 接收方 | 使用场景 |
|------|----------------|---------|
| [A1](#a1-chatgpt--claude-code-实现交接) | ChatGPT → Claude Code | 研究/规划完成，开始实现 |
| [A2](#a2-claude-code--chatgpt-审查交接) | Claude Code → ChatGPT | 实现完成，请求独立审查 |
| [B1](#b1-chatgpt-生成-handoff) | ChatGPT 内部 | 让 ChatGPT 生成结构化 handoff |
| [B2](#b2-claude-code-生成-handoff) | Claude Code 内部 | 让 Claude Code 生成结构化 handoff |
| [C1](#c1-研究任务--chatgpt) | 用户 → ChatGPT | 启动研究/规划任务 |
| [C2](#c2-实现任务--claude-code) | 用户 → Claude Code | 启动实现任务 |
| [D1](#d1-双-agent-审查模式) | 用户 → 两者 | 一个实现，一个独立审查 |

---

## A1: ChatGPT → Claude Code（实现交接）

### 在 ChatGPT 中使用

```
基于我们刚才的讨论，请生成一份结构化的开发交接文档。

需要包含：
1. 本次讨论的所有设计决策和理由
2. 确定的数据结构（接口/类型定义）
3. 文件创建/修改清单（路径 + 用途）
4. 实现顺序（先做什么，后做什么，依赖关系）
5. 需要注意的边界条件和约束
6. 测试要点（关键测试用例）

输出格式：
- 第一部分：context/handoff.md 的内容（Markdown）
- 第二部分：context/current_state.md 的更新内容
- 第三部分：context/decisions.md 的新 ADR 条目（如有）

注意：
- 不要包含已经写在 CLAUDE.md 中的架构信息
- 不要包含通用代码规范（Claude Code 已经读取 CLAUDE.md）
- 专注于"这次新增了什么"
```

### 在 Claude Code 中使用

```
请读取项目的 CLAUDE.md 和 context/ 目录下的文件，加载项目上下文。

加载完后，读取 context/handoff.md 中 ChatGPT 留下的交接文档。

确认你理解了：
- 当前项目阶段
- 本次要实现什么
- 设计决策是什么
- 实现顺序是什么

然后开始实现。从 [第一项任务] 开始。
```

---

## A2: Claude Code → ChatGPT（审查交接）

### 在 Claude Code 中使用

```
请生成一份代码审查用的交接摘要。

格式：
## 实现摘要
- 实现了什么
- 关键设计选择
- 与计划的偏差（如有）

## 需要审查的关键点
1. [最需要检查的地方]
2. [可能有问题的设计]
3. [安全隐患点]

## 关键文件
- [文件路径]: [一句话说明]
- ...

## 测试情况
- 新增测试: X 个
- 覆盖率变化: 从 Y% 到 Z%

输出为 Markdown，我将粘贴给 ChatGPT 进行独立审查。
```

### 在 ChatGPT 中使用

```
请审查以下实现。这是上下文：

[粘贴 Claude Code 生成的审查摘要]

项目架构参考（附件）:
- CLAUDE.md
- context/decisions.md

请从以下维度审查：
1. 是否遵循了项目的设计哲学？
2. 是否有安全隐患？
3. 是否有未处理的边界条件？
4. 代码组织是否合理？
5. 测试覆盖是否充分？

输出：审查结论 + 按严重程度排列的问题清单。
```

---

## B1: ChatGPT 生成 Handoff

```
请基于我们本次对话，生成一份给 Claude Code 的开发交接文档。

格式要求：
- 500-1000 字
- 使用 context/handoff.md 的标准格式
- 包含：做了什么、为什么、下一步、阻塞、关键上下文
- 不要包含 CLAUDE.md 中已有的架构信息

输出纯 Markdown，我会直接复制到项目中。
```

### B1 变体：快速版（对话较短时）

```
请用 5-10 句话总结我们本次讨论的：
1. 达成了什么结论
2. 下一步要做什么
3. 有什么需要注意的
```

---

## B2: Claude Code 生成 Handoff

```
请更新 context/handoff.md，生成本次会话的交接摘要。

按照项目约定的 handoff 格式：
- 本次会话做了什么（新增/修改/删除）
- 设计决策和理由
- 当前阻塞
- 下一步任务
- 给下一个 Agent 的关键上下文

同时更新 context/current_state.md：
- 标记已完成的条目
- 添加新的进行中条目
- 更新阻塞状态
- 调整下一步优先级

最后，用 3-5 句话口述摘要，我会确认。
```

---

## C1: 研究任务 → ChatGPT

```
我需要在项目 [项目名] 中实现 [功能描述]。

项目背景：
- 项目类型: [Web 应用 / CLI 工具 / 库 / ...]
- 技术栈: [TypeScript, React, ...]
- 项目哲学: [简述核心约束]

请帮我：
1. 研究有哪些开源实现可以参考
2. 分析在这个项目架构中应该如何设计
3. 生成一份包含设计决策、数据结构和实现计划的文档

请不要写实现代码。重点是设计和规划。

结束后，请生成一份给 Claude Code 的 handoff。
```

---

## C2: 实现任务 → Claude Code

```
请读取 CLAUDE.md 和 context/ 目录加载项目上下文。

本次任务: [任务描述]

参考 context/handoff.md 中 ChatGPT 的设计方案。

实现要求：
- 遵循项目 File Standards（max 800 lines/file, max 50 lines/function）
- 遵循 Immutable data patterns
- 所有外部输入用 Zod 验证
- 写测试（AAA pattern，覆盖率 ≥ 80%）

实现顺序：
1. [第一步]
2. [第二步]
3. [第三步]

每完成一步，运行测试确认通过后，再继续下一步。
```

---

## D1: 双 Agent 审查模式

### 第一步：在 Claude Code 中实现

标准实现流程。

### 第二步：Claude Code 生成审查包

使用模板 A2。

### 第三步：在 ChatGPT 中独立审查

使用模板 A2 的 ChatGPT Prompt。

### 第四步：在 Claude Code 中处理审查结果

```
ChatGPT 的独立审查发现了以下问题。请逐个处理：

[粘贴 ChatGPT 的审查结果]

对于每个问题：
- CRITICAL: 立即修复
- HIGH: 修复并说明理由
- MEDIUM: 评估后决定是否修复
- LOW: 记录到 context/decisions.md 作为已知权衡

修复完后，更新 context/handoff.md。
```

---

## 模板使用原则

### 1. 填空，不要照抄

模板中的 `[项目名]`、`[功能描述]` 等占位符必须替换为具体内容。
模板是骨架，具体项目上下文是血肉。

### 2. 渐进式：从简到详

- 小型任务（<1 小时）：使用快速版模板（B1 变体）
- 中型任务（1-4 小时）：使用标准模板（A1, A2）
- 大型任务（>4 小时）：使用完整模板 + 多次中途 handoff

### 3. 自解释：模板本身就是文档

一个好的模板 Prompt 应该：
- 明确告诉 Agent 输出什么
- 明确告诉 Agent 不输出什么
- 明确输出格式
- 明确使用场景

### 4. 演进：持续优化模板

每次使用模板后，如果发现：
- 模板遗漏了重要信息 → 添加
- 模板包含了冗余信息 → 删除
- 模板格式不够清晰 → 重构

回到这个文件，更新模板。模板是活的。
