# Vision Dev — Documentation Index

> 项目文档入口。按模块和管线组织，保持索引更新。

---

## 架构文档

| 文件 | 内容 | 适用场景 |
|------|------|---------|
| [architecture.md](architecture.md) | 系统架构总览：L1-L6 管线、Layered Attention、Curiosity 公式、Entity 生命周期 | 了解系统设计、新模块接入 |

## 开发指南

| 文件 | 内容 | 适用场景 |
|------|------|---------|
| [../interaction/README.md](../interaction/README.md) | AI Agent 交互方法论总览 | 多 AI 工具协作开发 |
| [../proposals/](../proposals/) | 9 个历史 Proposal（P0001–P0007C + P0008） | 了解设计演进和架构决策 |

### interaction/ 文件索引

| 文件 | 内容 | 适用场景 |
|------|------|---------|
| [../interaction/handoff-protocol.md](../interaction/handoff-protocol.md) | Agent 交接协议：结构化会话摘要 | 每次切换 AI 工具 |
| [../interaction/workflow-patterns.md](../interaction/workflow-patterns.md) | 6 种多 Agent 协作模式 | 规划阶段、任务分解 |
| [../interaction/context-sync.md](../interaction/context-sync.md) | 多 Agent 上下文一致性：推式 vs 拉式 | 项目初始化 + 定期维护 |
| [../interaction/prompt-templates.md](../interaction/prompt-templates.md) | 可复用 Prompt 模板（A1-D1） | 准备发送给下一个 Agent |
| [../interaction/chatgpt-system-prompt.md](../interaction/chatgpt-system-prompt.md) | ChatGPT 系统提示词配置 | 首次使用前（必须） |

## 管线文档

### L1-L6 感知管线

```
L1: Camera → L2: Detection (YOLO+YuNet+VAD) → L3: SceneState
→ L4: AttentionEngine + IntentionEngine → L5: EpisodicMemory → L6: CognitionTrigger
```

### Layered Attention（L4-L6 之间）

| 层 | 文件 | 时间尺度 | 问题 |
|----|------|---------|------|
| Interest | `runtime/interest/engine.py` | seconds | "What changed?" |
| Curiosity | `runtime/interest/engine.py` | minutes | "What's worth revisiting?" |
| Familiarity | `runtime/familiarity/engine.py` | min–hours | "Have I seen this enough?" |
| Role | `runtime/role/engine.py` | innate | "Should I care about this class?" |
| Mission Role | `runtime/role/mission.py` | minutes (TTL) | "What should I prioritize NOW?" (P0008) |
| Importance | `runtime/importance/stats_db.py` | hours–days | "What actually causes events?" |
| Quality Gate | `runtime/importance/entity_quality.py` | per-entity | "Is this signal clean?" (Phase 7B) |

### P0008: Observation Intent

| 文件 | 职责 |
|------|------|
| `runtime/role/mission.py` | MissionRole dataclass, MissionRoleCache (TTL), MissionRoleProvider ABC, ObservationContext |
| `runtime/role/mission_llm.py` | LLM-based MissionRoleProvider (fixed prompt template) + RuleMissionProvider (non-LLM fallback) |
| `runtime/role/persona.py` | Persona dataclass + YAML loader + list_personas() |
| `data/personas/*.yaml` | 5 Persona configs: companion, security, reception, patrol, pet |

**架构原则：**
- LLM 是 Advisor，不是 Controller — 只输出 class-level mission_role 权重
- Mission 有 TTL，过期自动失效 — 去掉 LLM，Runtime 仍正常工作
- Persona 是 config，不是 Prompt — LLM prompt template 永远固定
- EffectiveRole = IntrinsicRole + MissionRole（clamped [0,1]）

### PTZ 控制

| 文件 | 职责 |
|------|------|
| `runtime/perception/servo_ptz.py` | Arduino SG90 串口控制、响应解析 |
| `runtime/interest/revisit.py` | PTZ 决策：sweep → stay → track → explore |

---

## 数据文件约定

- 分析报告 → `doc/operations/reports/`
- 开发手册/指南 → `doc/operations/`
- 数据文件 (JSON/CSV) → `data/`

## 维护规则

1. **新模块**：在本文件添加模块说明和文件索引
2. **新脚本**：在对应管线章节登记脚本用途
3. **分析报告**：输出到 `doc/operations/reports/`，在 `doc/operations/README.md` 中登记
4. **开发手册/指南**：输出到 `doc/operations/`，更新索引
5. **数据文件**：输出到 `data/`，不放入 `doc/`

---

## 相关入口

- [CLAUDE.md](../CLAUDE.md) — 项目指令文件（Claude Code 入口）
- [interaction/](../interaction/) — AI Agent 交互方法论（完整协议 + 模板）
- [context/](../context/) — 项目记忆（current_state, handoff, decisions）
- [proposals/](../proposals/) — Proposal 设计文档存档
