# Handoff: 2026-07-14

## 当前状态

- 分支: master
- 测试: 66 new (P0008) + 158 existing = 224 pass / 4 pre-existing flaky

## 本次会话做了什么

### P0008: Observation Intent Engine

Implemented. See `proposals/P0008-Observation-Intent-Engine.md`.

**新文件：**
- `runtime/role/persona.py` — Persona dataclass + YAML loader
- `runtime/role/mission.py` — MissionRole dataclass, MissionRoleCache (TTL), MissionRoleProvider ABC, ObservationContext
- `runtime/role/mission_llm.py` — LLMMissionProvider (fixed prompt template) + RuleMissionProvider (non-LLM fallback)
- `data/personas/companion.yaml` — 陪伴机器人 Persona
- `data/personas/security.yaml` — 安防机器人 Persona
- `data/personas/reception.yaml` — 前台接待 Persona
- `data/personas/patrol.yaml` — 巡检机器人 Persona
- `data/personas/pet.yaml` — 宠物模式 Persona
- `tests/test_mission_role.py` — 66 tests, full chain coverage

**修改文件：**
- `runtime/role/engine.py` — `get_weight()` → EffectiveRole = IntrinsicRole + MissionRole (clamped [0,1])，新增 `mission_cache`、`refresh_entities()`、`intrinsic_weight()`、`mission_boost()`
- `runtime/main.py` — 连接 MissionRoleCache → RoleEngine，后台线程刷新 MissionRole，`_build_observation_context()`
- `config.py` — 新增 `PERSONA`, `MISSION_ROLE_PROVIDER`, `MISSION_ROLE_REFRESH_SEC`
- `CLAUDE.md` — 新增 P0008 设计原则（LLM is Advisor, Persona ≠ Prompt）+ 文件映射
- `doc/README.md` — Mission Role 层文档 + P0008 模块索引
- `context/current_state.md` — 更新开发进度

### 架构原则（本次建立，后续 P9-P12 必须遵守）

1. **LLM 是 Advisor，不是 Controller** — LLM 仅输出 `{"mission_role": {"person": 0.35}}`，不控制 PTZ、Entity、Runtime
2. **Mission 可失效** — TTL 机制，过期自动归零。去掉 LLM → EffectiveRole = IntrinsicRole → 系统正常工作
3. **Persona 是 Config，不是 Prompt** — Prompt template 永远固定。新机器人 = new_persona.yaml

## 当前阻塞

无

## 下一步任务

1. [ ] 运行 `python runtime/main.py` 验证 MissionRole 刷新（需要 LLM backend）
2. [ ] Mission Playground: 同一房间，切换 5 个 Persona，观察注意力分布变化
3. [ ] 观察 `MissionRoleCache` 日志，确认 LLM 输出质量
4. [ ] P0009: Scene Graph — Entity 空间关系
5. [ ] P0010: Event Discovery — Temporal Scene

## 关键上下文

- `EffectiveRole = IntrinsicRole + MissionRole`，clamped [0,1]
- `MissionRoleProvider` 有三种：`llm` (TextAPI), `rule` (persona defaults), `none` (空)
- 切换 Persona: 修改 `config.py` 中的 `PERSONA = "security"` 等
- 切换 Provider: 修改 `config.py` 中的 `MISSION_ROLE_PROVIDER = "rule"`
- MissionRole 刷新在后台 daemon 线程执行，不阻塞主循环
- `RuleMissionProvider` 无需 LLM，直接用 persona YAML 中的 `mission_role` 默认权重
- `data/personas/` 目录可自由添加新 YAML，`list_personas()` 自动发现
- 历史设计决策见 `proposals/`
