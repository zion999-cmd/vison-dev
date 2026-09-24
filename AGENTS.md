# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

See [README.md](README.md) for a high-level project overview.

## Setup

```bash
conda activate vision-dev          # env: /usr/local/Caskroom/miniconda/base/envs/vision-dev
cp config.example.py config.py     # REQUIRED — config.py is git-ignored, absent from a fresh clone
```

`config.py` is git-ignored (`.gitignore:9`), so a fresh clone has no copy at all — hence the `cp` above. Secrets are read from env vars there (`DASHSCOPE_API_KEY`, `GATEWAY_QWEN_API_KEY`, `EZVIZ_*`) — never commit it. Run commands must go through the conda env; the system Python has no pytest/onnxruntime.

## Run & Test

```bash
conda activate vision-dev
python runtime/main.py                     # full pipeline (L1-L6 + PTZ)
pytest -q                                  # full suite
pytest tests/test_commitment.py -q         # single file
pytest tests/test_commitment.py -q -k switch   # single test by keyword
```

**No Arduino → startup still succeeds, but the pipeline runs degraded.** `ServoPTZ.start()` logs an error and returns `False`; no worker thread is ever started, so nothing drains `_queue`, and `moving` (`_moving or len(_queue) > 0`) then latches `True` for the rest of the process. That pins `ego_motion` on permanently: the frame-diff gate is skipped, `focus.reset_tracking()` runs every frame so focus never persists, and `camera_settled` is never true so anchors are never observed. A missing mic is benign (VAD only).

**The suite is expected to be fully green.** There are no knowingly-failing tests — treat any failure as a real regression, not as known drift.

## Architecture

### L1-L6 Pipeline (main loop in `runtime/main.py`)

```
L1: Camera → frame (cv2.VideoCapture, 640x480)
L2: FrameDiff (motion gate) → YuNet ONNX (faces) + YOLO11n ONNX (80 classes) + Silero VAD (voice)
L3: SceneState — Idle → Focus → Alert → Sleep, ~3s debounce
L4: AttentionEngine — dynamic weighted scoring + decay + self-evolving weights
     → IntentionEngine — 8 intentions (emergency/speaking/looking/approaching/gesturing/using_desk/leaving/ambient)
L5: EpisodicMemory — rolling buffer, 200 entries, low-importance compression
L6: CognitionTrigger — sparse VLM/LLM calls, cooling + queue dedup
```

### Layered Attention (above L4, below L6)

Positioned between attention and cognition. Each layer answers one question:

| Layer | File | Timescale | Question |
|-------|------|-----------|----------|
| Interest | `runtime/interest/engine.py` | seconds | "What changed?" |
| Curiosity | `runtime/interest/engine.py` | minutes | "What's worth revisiting?" |
| Familiarity | `runtime/familiarity/engine.py` | min–hours | "Have I seen this enough?" |
| Role | `runtime/role/engine.py` | innate | "Should I care about this class?" |
| Commitment (P0008.1) | `runtime/commitment/engine.py` | seconds–minutes | "Should I keep watching the current target?" (HOLD/SWITCH/RELEASE) |
| Importance (Phase 7A) | `runtime/importance/stats_db.py` | hours–days | "What actually causes events?" (OBSERVE only, no formula) |

**Curiosity formula:** `interest × uncertainty × freshness × (1−familiarity) × role − movement_cost`

### Entity System (identity > location)

Entities persist across frames via visual signature (HSV histogram of bbox: avg_H, avg_S, avg_V, w, h). NOT tied to PTZ coordinates.

```
CANDIDATE (5 sightings to promote) → ACTIVE → LOST (30 misses) → FORGOTTEN (150 misses)
```

- `runtime/interest/entity.py` — Entity dataclass + lifecycle fields
- `runtime/interest/entity_registry.py` — signature matching + registry
- `runtime/perception/detection.py` — unified `Detection` dataclass (future: YOLO World embedding)

### PTZ Control (Arduino SG90, serial)

- **Serial:** `p<angle>` / `t<angle>` over 115200 baud
- **Pan:** 10–165° (p0=right, p180=left, p90=center)
- **Tilt:** 95–170° (t95=level/horizon, t170=look down)
- **Limits were reduced** from 180→170 (tilt) and 170→165 (pan) — SG90 servos degrade when held at mechanical limits. Holding at 180° caused potentiometer wear and position drift ("不停旋转" failure).
- Arduino auto-resets on serial connect (Duemilanove), needs ~2s settle
- `runtime/perception/servo_ptz.py` — serial worker thread with response parsing
- `runtime/interest/revisit.py` — main PTZ decision controller (sweep → stay → explore → track)

### PTZ Tracking (gentle framing in revisit.py)

A **tracking session** is opened only by the revisit/commitment flow — never by
a detection. `_track_target()` (the sole caller of `CommitmentEngine.begin()`)
establishes one from the stay-at-anchor path; `_framing_update()` then aims it
on the 1.5s `_track_interval`, decoupled from the 8s revisit gate. Seeing a face
or a person is not by itself a reason to follow it. An
anchor-level judgement (flat interest / sparse classes / VLM "trivial") ends the
stay but never clears the commitment: that belongs to the person being watched
and ends only on lost / stale / timeout.

- Face bbox preferred over YOLO person bbox
- **Framed, not centred**, through three independent knobs: `start_offset`
  (nothing moves below it), `aim_offset` (where a triggered follow takes the
  target — the residual it leaves) and `gain` (how much of the remaining excess
  one update removes). One threshold cannot serve all three roles: a large
  comfort zone would then also force a late trigger and a weak correction, which
  is how this first shipped — a 192px trigger, half the correction and a 96px
  residual
- Current values: pan start 0.15 / aim 0.06, tilt start 0.20 / aim 0.08, gain
  1.0 — the largest gain that cannot overshoot the aim point, and the value at
  which the ±15°/±8° safety clamps become live again
- `pan_delta = -step_x × FOV × gain`, `tilt_delta = step_y × FOV × h/w × gain`
- Tilt fatigue: >155° weakens downward push (gain 1.0→0.25)
- Tilt recovery: >150° for 90s → auto pull back to 120°
- Presence signal (`_last_track_hit < 15s`) extends stay duration, and is what
  keeps an open session (and its commitment) alive

### Revisit Controller States

1. **Sweep** (first 60s): alternating left/right `[30, -45, 45, -45, 60, -60, 45, -30]` at 8s intervals, tilt=95
2. **Stay** at interesting anchor: hot=300s, moderate=120s, idle=30s max
3. **Explore** turn: random left/right, return to best anchor if interest>0.25
4. **Track target**: proportional pan/tilt adjustments at 1.5s intervals

### Two independent LLM paths (both optional, different config keys)

| Path | Config keys | Role |
|------|-------------|------|
| L6 cognition | `TEXT_API_*` (text), `VLM_BACKENDS` (rotating list) | explains a triggered frame; rotates backends, so a dead one doesn't stall the pipeline |
| P0008 Mission Role | `MISSION_LLM_*`, `MISSION_ROLE_PROVIDER` | advisor weights + TTL, refreshed on expiry |

These are separate backends — configuring one does not configure the other. `MISSION_ROLE_PROVIDER = "none"` plus unreachable backends is a supported configuration: the runtime runs with zero LLM.

## Key Design Principles

- **No VLM/LLM in core layers** — YOLO + YuNet + HSV only. VLM is for L6 cognition trigger and optional anchor verification only
- **Architecture fix > parameter fix** — prefer restructuring over tuning constants
- **Entity identity > YOLO label** — visual signature matching, not class-name matching
- **Observe before defining** — Phase 7A Importance Observatory records what entities cause downstream events with NO value formula. Value Engine is Phase 7B+
- **Single-threaded inference** — all cv2.VideoCapture reads and ONNX inferences happen on the main thread. Background threads (Timer, serial worker) must never touch camera or ONNX
- **Immutable patterns** — always return new objects, never mutate in place
- **LLM is Advisor, not Controller** (P0008) — LLM outputs class-level mission_role weights with TTL. It never controls PTZ, entities, or runtime state. Remove LLM → system still works.
- **Persona ≠ Prompt** (P0008) — Persona is YAML config, LLM prompt template is fixed. New robot identity = new YAML, not new prompt engineering.
- **Curiosity vs Commitment** (P0008.1) — Curiosity competes for the next look; Commitment protects the current look. Familiarity is NOT a negative input to Commitment.

## Explicitly Deferred

These are NOT to be implemented now. They belong to future phases:
- **Value Engine** (Phase 7B+) — formula for entity importance, not just observation
- **Memory / Soul / Emotion / Preference / Reward Model** — long-term systems
- **Vector DB / Knowledge Graph** — infrastructure not needed yet
- **YOLO World / CLIP / GroundingDINO** — open-vocabulary detectors (embedding field in `Detection` is reserved)

## Development Methodology

This project follows the AI Agent Interaction Methods documented in `interaction/`. Key protocols:

- **Proposal-Driven Development** — ChatGPT designs (Proposal), Codex implements. See `interaction/proposal-driven-development.md`
- **Handoff Protocol** — structured session summaries when switching between AI tools. See `interaction/handoff-protocol.md`
- **Context Sync** — all agents read from the same project files (pull model, not push). See `interaction/context-sync.md`

See `interaction/README.md` for the full methodology index.

`DEVELOPMENT.md` holds the mandatory per-change workflow (code review → tests → docs → commit).

**`CLAUDE.md` is a near-verbatim copy of this file** — they differ only in the title and which agent is named as implementer. Any change to architecture here must be mirrored there in the same commit (see commit `ec10994`).

## Context Files

Agent 在开始工作前应按顺序读取：
1. `AGENTS.md` — 本文件，项目整体架构和约定
2. `context/current_state.md` — 当前进度和状态
3. `context/handoff.md` — 上一次会话的交接摘要
4. `context/decisions.md` — 架构决策记录（有疑问时读）

## Logs

- `logs/runtime_*.log` — main log (rotates at 5MB, keeps 3 backups)
- `logs/importance_candidates.log` — top interaction-density entities every ~30 min (JSON)
- `logs/entity_stats.db` — SQLite cross-session entity statistics
- `logs/telemetry/` — behavioral snapshots per minute

## Project File Map

```
config.py              — all constants (FPS, thresholds, API keys, servo port); GIT-IGNORED, copy from config.example.py
context/               — project memory: current_state, handoff, decisions (shared by all agents)
proposals/             — ChatGPT-generated design proposals (P000X-xxx.md)
data/
    personas/           — P0008 Persona YAML configs (companion, security, reception, patrol, pet)
runtime/
  main.py              — PerceptionRuntime: init + main loop + all wiring
  perception/          — L1-L2: capture, face/object/VAD detection, servo PTZ
  scene/               — L3: state machine with debounce
  attention/           — L4: scoring + decay + weight evolution
  intention/           — intention inference (8 types)
  focus/               — persistent focus with inertia
  presence/            — identity continuity + novelty + expression change
  behavior/            — IDLE→OBSERVE→TRACKING→ENGAGED→ACCOMPANYING
  memory/              — L5: episodic memory (rolling buffer)
  cognition/           — L6: VLM/LLM trigger with multi-backend polling
  interest/            — layered attention: interest, curiosity, entity, anchor, revisit, verifier
  familiarity/         — session-level habituation formula
  role/                — innate class priority weights + P0008 MissionRole (LLM advisor, TTL cache)
  commitment/          — P0008.1 Commitment/Dwell Policy (HOLD/SWITCH/RELEASE arbiter)
  importance/          — Phase 7A entity stats + Phase 7B quality gate (entity_quality.py)
  telemetry/           — minute-level telemetry + session logging
  eventbus/            — event bus (lightweight pub/sub)
  utils/               — logging config, model loader, vision API client
models/                — ONNX model files (YuNet, YOLO11n, Silero VAD)
tests/                 — pytest unit tests
scripts/               — one-off diagnostic/calibration scripts
doc/                   — architecture docs + documentation index (see doc/README.md)
interaction/           — AI Agent interaction methodology (ChatGPT ↔ Codex workflow)
```
