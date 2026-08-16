
# Workflow Patterns: 多 Agent 协作模式

> 经过验证的多 AI Agent 协作模式，适用于不同的开发场景。

---

## Pattern 1: Planner-Implementer（最常用）

```
ChatGPT (Plan) → Claude Code (Implement) → ChatGPT (Review)
```

### 适用场景
- 新功能开发
- 有明确设计需求的任务
- 需要权衡多种方案

### 流程

```
Phase 1: ChatGPT 规划
  输入: 需求描述 + 项目上下文
  输出: 设计方案、数据结构、文件清单、实现顺序
  产物: context/handoff.md (给 Claude Code)

Phase 2: Claude Code 实现
  输入: context/handoff.md
  输出: 实现代码 + 测试
  产物: 代码 + context/handoff.md (给 ChatGPT 审查)

Phase 3: ChatGPT 独立审查
  输入: context/handoff.md + 关键代码文件
  输出: 审查结论、问题清单
  产物: 审查报告
```

### 优势
- 规划和实现分离 → 每个 Agent 做最擅长的事
- ChatGPT 的独立审查 → 发现实现偏差
- 每阶段有明确产物 → 可追溯

### 关键点
- ChatGPT 不能只给想法，必须给出**具体的文件清单和接口定义**
- Claude Code 实现时如果偏离计划，必须**记录原因**
- 审查必须独立，ChatGPT 不能"放水"因为它自己做的规划

---

## Pattern 2: Implement-Review（快速迭代）

```
Claude Code (Implement) → ChatGPT (Review) → Claude Code (Fix)
```

### 适用场景
- Bug 修复
- 小型功能增强
- 不需要重设计的任务

### 流程

```
Phase 1: Claude Code 实现
  直接写代码，不需要前置规划

Phase 2: ChatGPT 审查
  独立审查代码质量、安全隐患、边界条件

Phase 3: Claude Code 修复
  根据审查结果修复问题
```

### 优势
- 快速，没有规划开销
- 独立审查捕获盲点

### 关键点
- 只适用于**范围明确**的小任务
- 如果是模糊的需求，先用 Pattern 1

---

## Pattern 3: Research-Port（技术调研 + 移植）

```
ChatGPT (Research) → Claude Code (Port/Adapt) → ChatGPT (Verify)
```

### 适用场景
- 需要使用不熟悉的技术或库
- 从开源项目移植方案
- 技术选型

### 流程

```
Phase 1: ChatGPT 研究
  - 搜索 GitHub / npm / PyPI 找相关实现
  - 评估 3-5 个候选方案（安全、扩展性、相关性、实现难度）
  - 推荐最佳方案并说明理由
  产物: research.md + context/handoff.md

Phase 2: Claude Code 移植
  - 根据研究结果，fork/port/wrap 选定的方案
  - 适配项目架构
  - 写测试
  产物: 代码

Phase 3: ChatGPT 验证
  - 对照原始方案，确认核心逻辑被正确移植
  - 确认适配过程中没有丢失关键功能
  - 确认许可合规
```

### 优势
- 用 ChatGPT 的搜索能力补充 Claude Code
- 研究有记录 → 可回溯决策理由

---

## Pattern 4: Parallel Review（并行审查）

```
                    ┌─→ ChatGPT (安全审查)
Claude Code (Impl) ─┼─→ ChatGPT (性能审查)
                    └─→ ChatGPT (代码质量)
                         ↓
                    Claude Code (合成 + 修复)
```

### 适用场景
- 关键基础设施代码
- 安全敏感功能（认证、支付、用户数据）
- 发布前的最终检查

### 流程

```
Phase 1: Claude Code 实现完成后
  生成审查摘要

Phase 2: 并行审查（3 个独立 ChatGPT 会话）
  会话 A: 仅审查安全性（OWASP Top 10, secrets, input validation）
  会话 B: 仅审查性能（N+1 queries, missing pagination, caching）
  会话 C: 仅审查代码质量（naming, structure, error handling, test coverage）

Phase 3: Claude Code 合成
  合并 3 份审查报告，去重，按严重程度排序
  逐项修复
```

### 优势
- 每个审查员专注一个维度 → 更深入
- 并行执行 → 更快
- 独立审查 → 交叉验证

### 关键点
- 为每个审查员提供**不同的审查 Prompt**（聚焦不同维度）
- 审查员之间不应交流（保持独立性）

---

## Pattern 5: Loop Completion（业务循环驱动）

```
Claude Code (Loop Analysis) → ChatGPT (Loop Design) → Claude Code (Implement Loop)
```

### 适用场景
- AgentFabric 风格的项目
- 以业务循环为中心（不是功能为中心）

### 流程

```
Phase 1: Claude Code 分析当前循环
  - 当前哪些循环是完整的？
  - 哪些循环断裂了？
  - 断裂点在哪里？
  产物: loop_analysis.md

Phase 2: ChatGPT 设计循环
  - 基于分析，设计应该先完成哪个循环
  - 循环的 Data Flow 是什么？
  - 循环的 Contract 是什么？
  产物: loop_design.md + context/handoff.md

Phase 3: Claude Code 实现循环
  - 按 Business Loop → Data Flow → Contract → Workspace → Widget 的顺序
  - 先让数据跑通，再加 UI
```

### 优势
- 对齐工程哲学中的"Loop Driven Development"
- 每完成一个循环就有可见的业务价值

---

## Pattern 6: Daily Sync（日常开发节奏）

```
上午:  Claude Code (Implementation)
中午:  更新 context/handoff.md
下午:  Claude Code (Continue)
傍晚:  更新 context/handoff.md
次日:  任何 Agent 读取 handoff.md 继续
```

### 适用场景
- 持续的日常开发
- 同一 Agent 跨会话
- 不同 Agent 跨天

### 节奏

```
每个工作块结束时（无论是否切换 Agent）：
1. 记录"做到了哪里"
2. 记录"接下来做什么"
3. 记录"遇到了什么问题"
4. 确保测试通过

下个工作块开始时：
1. 读取 handoff.md
2. 读取 current_state.md
3. 验证状态一致
4. 继续工作
```

### 关键点
- 即使不切换 Agent（Claude Code → Claude Code），也要写 handoff
- 原因是：下次会话的 Claude Code 不会记得上次做了什么
- Handoff 是给自己（未来的自己）的留言

---

## 选择指南

| 场景 | 推荐 Pattern | 理由 |
|------|-------------|------|
| 新功能开发 | Pattern 1 (Planner-Implementer) | 需要设计和审查 |
| Bug 修复 | Pattern 2 (Implement-Review) | 范围小，不需要规划 |
| 技术调研 | Pattern 3 (Research-Port) | ChatGPT 搜索能力强 |
| 安全关键代码 | Pattern 4 (Parallel Review) | 需要多维度深度审查 |
| AgentFabric 风格项目 | Pattern 5 (Loop Completion) | 对齐工程哲学 |
| 日常开发 | Pattern 6 (Daily Sync) | 最小开销，最大连续性 |

## 组合使用

多个 Pattern 可以组合。例如：

```
大型功能开发 =
  Pattern 3 (Research-Port)  → 研究技术方案
  + Pattern 1 (Planner-Implementer) → 设计和实现
  + Pattern 4 (Parallel Review) → 发布前审查

日常Bug修复 =
  Pattern 6 (Daily Sync) → 维护上下文
  + Pattern 2 (Implement-Review) → 快速修复
```
