# Handoff: 2026-08-16

## 当前状态

- 分支: master（远端已重建为干净单 commit，见下）
- 测试: 244 pass / 4 pre-existing flaky（camera_state×2, focus_manager, scene_state）

## 本次会话做了什么

### 1. Git 安全清理（密钥）

- 远端 `zion999-cmd/vison-dev` 原包含 `config.py` 明文密钥（EZVIZ / dashscope / gateway qwen），已删除远端并重建
- `config.py` 加入 `.gitignore`（本地保留），新增 `config.example.py`（密钥走环境变量）
- 完整旧历史备份在 `/tmp/vision-dev-backup.bundle`（420K）
- 当前远端为单条干净 commit（历史中无 config.py）
- 提醒：若旧仓库曾 public，密钥仍建议轮换

### 2. P0008.1: Commitment / Dwell Policy

解决 **Commitment Gap**：Runtime 会"决定看什么"，但不会"决定看多久"。注意力 span 平均 ~24s（explore 42% + switched 41%），根因是 Curiosity 公式的 freshness/uncertainty/(1−familiarity) 对持续在场的人塌缩为 0。设计见 `proposals/P0008.1-commitment-dwell-policy.md`。

**新文件：**
- `runtime/commitment/engine.py` — CommitmentState, Decision(HOLD/SWITCH/RELEASE), CommitmentEngine（compute_commitment + decide）
- `runtime/commitment/telemetry.py` — CommitmentTelemetry（start/hold/switch/release）
- `runtime/commitment/__init__.py`
- `tests/test_commitment.py` — 19 tests

**修改文件：**
- `runtime/interest/revisit.py` — 3 处仲裁钩子（anchor-stay 超时 / 切换 / 探索）+ `_commitment_holds()` + `_track_target` 里建立 commitment
- `runtime/main.py` — 传入 role_engine，30min 周期 flush commitment telemetry
- `CLAUDE.md` / `doc/README.md` — 登记模块

### 架构原则（P0008.1 建立）

1. **Curiosity vs Commitment** — Curiosity 竞争"下一个看什么"，Commitment 保护"当前看什么"
2. **熟悉 ≠ 不值得陪伴** — Familiarity 不作为 Commitment 的负项
3. **commitment_score = role + mission + presence − disengagement**（与 Curiosity 公式完全分离）
4. **仲裁输出仅 HOLD/SWITCH/RELEASE**，SWITCH 需 challenger_curiosity > commitment + SWITCH_MARGIN（迟滞）

## 当前阻塞

无

## 下一步任务

1. [ ] P0008.1 场景验证（Scenario A/B/C，需接摄像头实测）
2. [ ] ChatGPT 审查 P0008.1 代码
3. [ ] Mission Playground / Persona Divergence
4. [ ] P0009: Scene Graph

## 关键上下文

- `PERSONA` / `MISSION_ROLE_PROVIDER` 在 `config.py`（已 gitignore，新克隆需 `cp config.example.py config.py`）
- Commitment 是 **class-scoped**（target_class="person"），entity_id 绑定为后续优化
- Commitment 常量在 `runtime/commitment/engine.py` 模块级（SWITCH_MARGIN=0.15, SAFETY_MAX_DWELL=1800 等）
- 4 个 pre-existing flaky 测试与本次改动无关
- 历史设计决策见 `proposals/` + `context/decisions.md`
