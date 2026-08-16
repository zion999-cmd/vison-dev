#!/usr/bin/env python3
"""
Vision Perception Runtime
=======================
High-frequency perception + low-frequency cognition.

Architecture:
    L1: Camera Loop (5 FPS)
    L2: Signal Detection (frame diff → ONNX face + object + VAD)
    L3: Scene State + State Machine (Idle/Focus/Alert/Sleep)
    L4: Attention Engine (scoring + decay + self-evolving weights)
        → Intention Inference (what is the user doing?)
    L5: Episodic Memory (event timeline)
    L6: Cognition Trigger (sparse LLM/VLM)

Usage:
    python runtime/main.py  (from project root)
"""

import os
import sys
import time
import logging
import threading
from typing import Dict, Optional

# Ensure project root is in path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from runtime.utils.logging_config import setup_logging
setup_logging()  # must be called before any logger is created

import cv2
import numpy as np

from config import (
    PERCEPTION_FPS, SHOW_PREVIEW, ATTENTION_THRESHOLD,
    ATTENTION_EVOLVE_INTERVAL, STREAM_TYPE,
    SERVO_SERIAL_PORT, SERVO_BAUD,
    PERSONA, MISSION_ROLE_PROVIDER, MISSION_ROLE_REFRESH_SEC,
)

# ── L1 ──
from runtime.perception.capture import CameraCapture
from runtime.perception.camera_state import get_camera_state
from runtime.perception.servo_ptz import ServoPTZ

# ── L2: Signal Detection ──
from runtime.perception.frame_diff import FrameDiff
from runtime.perception.face_detection import FaceDetector
from runtime.perception.object_detection import ObjectDetector
from runtime.perception.vad import VoiceActivityDetector, AudioCapture

# ── L3-L6 ──
from runtime.scene.state import SceneState
from runtime.attention.engine import AttentionEngine
from runtime.intention.engine import IntentionEngine
from runtime.memory.episodic import EpisodicMemory
from runtime.cognition.trigger import CognitionTrigger, CognitionTask
from runtime.eventbus.bus import EventBus
from runtime.focus.manager import FocusManager
from runtime.presence.tracker import PresenceTracker
from runtime.behavior.idle import IdleBehaviorManager
from runtime.telemetry.collector import MinuteCollector
from runtime.telemetry.session import SessionLog
from runtime.interest.engine import InterestEngine
from runtime.interest.revisit import RevisitController
from runtime.interest.telemetry import BehavioralTelemetry
from runtime.interest.anchor import AnchorManager
from runtime.interest.entity_registry import EntityRegistry
from runtime.familiarity.engine import FamiliarityEngine
from runtime.role.engine import RoleEngine
from runtime.role.persona import load_persona
from runtime.role.mission import (
    MissionRole, MissionRoleCache, ObservationContext,
    create_mission_provider,
)
from runtime.role.mission_telemetry import MissionTelemetry
from runtime.importance.stats_db import EntityStatsDB
from runtime.importance.stability import StabilityAnalyzer

logger = logging.getLogger(__name__)


def _intrinsic_snapshot(role_engine, registry) -> dict:
    """Build {class_name: intrinsic_weight} for active entity classes."""
    weights = {}
    for e in registry.all_entities():
        if not e.is_active:
            continue
        cn = e.class_name or ""
        if cn and cn not in weights:
            weights[cn] = role_engine.intrinsic_weight(e)
    return weights


class PerceptionRuntime:
    """Main runtime orchestrating L1-L6 pipeline."""

    def __init__(self):
        # L1: Capture
        self.camera = CameraCapture()

        # Servo PTZ (Arduino SG90, serial)
        self.servo_ptz = ServoPTZ(port=SERVO_SERIAL_PORT, baud=SERVO_BAUD)

        # Camera state (PTZ pose + ego motion flag)
        self.camera_state = get_camera_state()

        # L2: Signal Detection
        self.frame_diff = FrameDiff()
        self.face = FaceDetector()
        self.object_detector = ObjectDetector()
        self.vad: Optional[VoiceActivityDetector] = None
        self.audio: Optional[AudioCapture] = None
        self._audio_enabled = False
        self._voice_active = False

        # L3: Scene + State Machine
        self.scene = SceneState()

        # L4: Attention
        self.attention = AttentionEngine()
        self.interest_engine = InterestEngine()
        self.attention.set_interest_engine(self.interest_engine)

        # Spatial anchor system (region baseline)
        # pan_spacing=20 for servo 0-180° range (was 30 for EZVIZ 360°)
        self.anchor_manager = AnchorManager(pan_spacing=20, tilt_spacing=15)

        # Role Engine (innate priority weights)
        self.role_engine = RoleEngine()

        # P0008: Mission Role — dynamic observation priorities from LLM
        self.mission_cache = MissionRoleCache()
        self.role_engine.mission_cache = self.mission_cache
        self.mission_provider = create_mission_provider(MISSION_ROLE_PROVIDER)
        self._persona = load_persona(PERSONA)
        self._last_mission_refresh = 0.0
        self._mission_refresh_thread: Optional[threading.Thread] = None
        self.mission_telemetry = MissionTelemetry()
        self._curiosity_before_refresh: list = []  # snapshot for delta tracking

        # Entity Registry (entity-centric interest)
        self.entity_registry = EntityRegistry(role_engine=self.role_engine)

        # Familiarity Engine (session-level habituation)
        self.familiarity_engine = FamiliarityEngine()

        # Entity Stats DB (cross-session importance observatory)
        self.entity_stats_db = EntityStatsDB()

        # Stability Analyzer (Phase 7C: read-only observation protocol)
        self.stability_analyzer = StabilityAnalyzer()
        self._last_stability_check = 0.0

        # Revisit controller (will be initialised after other modules)
        self.revisit_controller = None
        self.behavior_telemetry = None

        # Intention (between L4 and L6)
        self.intention = IntentionEngine()

        # L5: Memory
        self.memory = EpisodicMemory()

        # L6: Cognition
        self.cognition = CognitionTrigger(memory=self.memory)

        # Event bus
        self.bus = EventBus()

        # Focus system + Presence tracking
        self.focus = FocusManager()
        self.presence = PresenceTracker()
        self.behavior = IdleBehaviorManager()
        self.telemetry = MinuteCollector()
        self.session = SessionLog()

        self._is_running = False
        self._frame_count = 0
        self._last_focus_id = ""
        self._last_state = ""
        self._last_behavior = ""

    # ══════════════════════════════════════════════════
    # Lifecycle
    # ══════════════════════════════════════════════════

    def start(self) -> bool:
        logger.info("=" * 50)
        logger.info("Vision Perception Runtime starting...")
        logger.info("Persona: %s | Mission provider: %s",
                    self._persona.name, MISSION_ROLE_PROVIDER)
        logger.info("L2: FrameDiff → YuNet ONNX + Silero VAD")
        logger.info("Behavior: SCAN → OBSERVE → TRACKING → ENGAGED → REST")
        logger.info("=" * 50)

        if not self.camera.start():
            logger.error("Camera failed. Grant permission: System Settings > Privacy > Camera > Terminal")
            return False

        self._init_audio()
        self.cognition.start()
        self.entity_stats_db.open()
        self._is_running = True
        self._loop()
        return True

    def stop(self):
        self._is_running = False
        self.cognition.stop()
        self.camera.release()
        self.servo_ptz.stop()
        self.entity_stats_db.close()
        if self.audio:
            self.audio.stop()
        cv2.destroyAllWindows()
        logger.info("Runtime stopped")

    def _init_audio(self):
        """Try to initialise microphone + VAD. Gracefully skip if unavailable."""
        try:
            self.audio = AudioCapture()
            if self.audio.start():
                self.vad = VoiceActivityDetector()
                self._audio_enabled = True
                logger.info("Audio capture + VAD enabled")
            else:
                logger.warning("Audio not available — voice detection disabled")
        except Exception as e:
            logger.warning("Audio init failed (%s) — voice detection disabled", e)

    def _start_ptz_worker(self):
        """Start servo PTZ (Arduino SG90)."""
        if not self.servo_ptz.start():
            logger.error("Servo PTZ failed to start — continuing without PTZ")
        else:
            logger.info("Servo PTZ ready (pan=%d, tilt=%d)",
                        self.servo_ptz.pan, self.servo_ptz.tilt)

    # ══════════════════════════════════════════════════
    # Main Loop
    # ══════════════════════════════════════════════════

    def _loop(self):
        interval = 1.0 / PERCEPTION_FPS

        # Log exit reason even for silent failures (SIGHUP, USB unplug, sleep)
        import atexit, signal, time as _time
        def _on_exit():
            logger.info("Runtime exiting — frames=%d", self._frame_count)
        atexit.register(_on_exit)
        # SIGTERM: double-tap to confirm (gateway/proxy restarts send stray SIGTERM
        # on macOS; one tap = warn, two taps within 30s = actual shutdown).
        # SIGHUP: IGNORED entirely (fires on proxy/VPN restarts, network changes).
        self._first_sigterm_at = 0.0
        def _on_sigterm(signum, frame):
            now = _time.time()
            if self._first_sigterm_at > 0 and now - self._first_sigterm_at < 30.0:
                logger.info("Runtime received second SIGTERM — shutting down (frames=%d)",
                            self._frame_count)
                self._is_running = False
            else:
                self._first_sigterm_at = now
                logger.warning("Runtime received stray SIGTERM — ignored (send again "
                             "within 30s to confirm shutdown, frames=%d)", self._frame_count)
        signal.signal(signal.SIGTERM, _on_sigterm)
        try:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        except AttributeError:
            pass  # Windows doesn't have SIGHUP

        # Init servo PTZ
        self._start_ptz_worker()
        # Init revisit controller + behavioral telemetry
        self.behavior_telemetry = BehavioralTelemetry(
            self.interest_engine, anchor_manager=self.anchor_manager)
        self.revisit_controller = RevisitController(
            interest_engine=self.interest_engine,
            servo_ptz=self.servo_ptz,
            camera_state=self.camera_state,
            object_detector=self.object_detector,
            face_detector=self.face,
            frame_reader=self.camera.read,
            anchor_manager=self.anchor_manager,
            entity_registry=self.entity_registry,
            on_decision=self._on_ptz_decision,
            role_engine=self.role_engine,
        )

        # Anchors are built organically by the RevisitController's explore mode
        # (first 3 minutes). No separate scanner step — one source of exploration.
        logger.info("Perception loop running at %d FPS", PERCEPTION_FPS)

        while self._is_running:
            loop_start = time.time()

            try:
                # ── L1: Capture ──
                result = self.camera.read()
            except Exception as e:
                logger.error("Camera read failed: %s — exiting", e)
                break
            if result is None:
                time.sleep(interval)
                continue

            frame, timestamp = result
            self._frame_count += 1

            # ── L2: Signal Detection ──
            # Ego motion guard: frame diff is polluted by camera movement
            ego_motion = self.servo_ptz.moving
            if ego_motion:
                has_changed = False
                motion_level = 0.0
            else:
                has_changed = self.frame_diff.changed(frame)
                motion_level = self.frame_diff.motion_level

            # YOLO is single-frame stateless — works fine during motion.
            # Frame diff is what gets polluted by PTZ (handled above).
            if has_changed or ego_motion:
                faces = self.face.detect(frame)
                objects = self.object_detector.detect(frame)
            else:
                faces = []
                objects = []

            # PTZ started moving → clear tracking state so old bboxes don't
            # persist at wrong positions after the camera pans away.
            if ego_motion and self._frame_count > 1:
                self.focus.reset_tracking()

            # Voice detection
            self._poll_audio()

            # ── L3: Scene State + State Machine ──
            # Get current anchor's novelty for genuine change detection
            current_anchor_novelty = 0.0
            if objects and not ego_motion:
                # Find anchor at current camera position
                from runtime.interest.anchor import SpatialAnchor
                pan = self.servo_ptz.pan
                tilt = self.servo_ptz.tilt
                snapped_pan = round(pan / 30.0) * 30.0
                snapped_tilt = round(tilt / 15.0) * 15.0
                for a in self.anchor_manager.all_anchors():
                    if abs(a.pan - snapped_pan) < 1 and abs(a.tilt - snapped_tilt) < 1:
                        current_anchor_novelty = a.novelty
                        break

            self.scene.update(
                people=faces,
                motion_level=motion_level,
                objects=objects,
                voice_activity=self._voice_active,
                anchor_novelty=current_anchor_novelty,
            )
            raw_state = self.scene.get()

            # ── L4: Attention + Intention ──
            attention_ctx = dict(raw_state)
            attention_ctx["state_multiplier"] = self.scene.attention_multiplier
            scored_events = self.attention.score_events(attention_ctx)

            intention_result = self.intention.infer(raw_state, scored_events)
            self.scene.update(intention=intention_result["intention"])
            scene_state = self.scene.get()

            # ── Focus System + Presence ──
            focus_info = self.focus.update(scored_events, scene_state, faces, objects)
            if focus_info["changed"] and focus_info["has_focus"]:
                self.session.focus_switch("?", focus_info["target_type"], focus_info["score"])
                self.telemetry.record_focus_switch("?", focus_info["target_type"])

            presence_info = self.presence.update(scene_state, focus_info, faces, objects)
            behavior_info = self.behavior.update(scene_state, focus_info, presence_info, self._frame_count)

            # Session log: state & behavior transitions
            runtime_s = scene_state.get("runtime_state", "?")
            if runtime_s != self._last_state:
                self.session.state_change(self._last_state, runtime_s, "transition")
                # ── Importance: state transition caused by entity ──
                if runtime_s in ("focus", "engaged"):
                    self._credit_entity("state_change")
                self._last_state = runtime_s
            if behavior_info["state"] != self._last_behavior:
                self.session.behavior_change(self._last_behavior, behavior_info["state"])
                # ── Importance: behavior change caused by entity ──
                beh = behavior_info["state"]
                if beh == "tracking":
                    self._credit_entity("tracking")
                elif beh == "engaged":
                    self._credit_entity("engaged")
                self._last_behavior = behavior_info["state"]

            # Telemetry: behavior state & novelty
            self.telemetry.record_behavior(behavior_info["state"])
            # Telemetry: novelty
            self.telemetry.record_novelty(presence_info["novelty"])

            # ── L5: Memory ──
            for ev in scored_events:
                effective = ATTENTION_THRESHOLD * self.scene.attention_multiplier
                if ev["score"] >= effective:
                    self.memory.push(
                        event_type=ev["type"],
                        detail=ev.get("detail", ""),
                        importance=ev["score"],
                        intention=intention_result["intention"],
                        state_snapshot=scene_state,
                    )

            # ── Weight evolution ──
            if self._frame_count % ATTENTION_EVOLVE_INTERVAL == 0:
                self.attention.evolve_weights()

            # ── Spatial Anchor observation ──
            # Skip during PTZ motion AND settling (RTSP buffer still shows
            # old-angle frames for 2-3s after camera stops).
            camera_settled = not self.servo_ptz.moving
            if camera_settled and (objects or self._frame_count % 5 == 0):
                self.anchor_manager.observe(
                    objects=objects,
                    pan=self.servo_ptz.pan,
                    tilt=self.servo_ptz.tilt,
                )

            # ── Entity Registry ──
            if objects:
                self.entity_registry.process_frame(
                    frame=result[0],
                    detections=objects,
                    pan=self.servo_ptz.pan,
                    tilt=self.servo_ptz.tilt,
                )
                # Update familiarity for all active entities (session habituation)
                for e in self.entity_registry.all_entities():
                    if e.is_active:
                        self.familiarity_engine.update(e)

            # ── Familiarity telemetry (every 5 min) ──
            if self._frame_count % 1500 == 0:  # every ~5 min at 5 FPS
                active = [e for e in self.entity_registry.all_entities() if e.is_active]
                if active:
                    self.familiarity_engine.log_distribution(active)

            # ── Importance Observatory (every ~30 min at 5 FPS) ──
            if self._frame_count % 9000 == 0:
                self._log_importance_candidates()
                self.entity_stats_db.save_all(
                    list(self.entity_registry.all_entities()))

            # ── Interest: driven by anchor CHANGES, not raw YOLO labels ──
            # The AnchorManager detects what's new/disappeared from baseline.
            # InterestTargets are created organically when something CHANGES,
            # not from every object label the YOLO sees.
            # Decay interests every ~60s (300 frames at 5fps)
            if self._frame_count % 300 == 0:
                self.interest_engine.decay()

            # ── L6: Cognition Trigger ──
            for event in scored_events:
                effective = ATTENTION_THRESHOLD * self.scene.attention_multiplier
                if event["score"] >= effective:
                    self.telemetry.record_attention_above_threshold()

                    # Excluded events (noise)
                    if event["type"] in ("large_motion", "gaze_maintained", "gaze_lost", "background_noise"):
                        continue

                    # Behavior gate: in accompanying, no cognition for human_face
                    if behavior_info["state"] == "accompanying" and event["type"] == "human_face":
                        self.telemetry.record_suppressed()
                        continue
                    # Presence gate
                    if event["type"] == "human_face" and (not presence_info["should_think"] or len(faces) == 0):
                        self.telemetry.record_suppressed()
                        continue

                    needs_vision = event["type"] in ("human_face", "new_object", "gaze_started", "user_entered")

                    task = CognitionTask(
                        event_type=event["type"],
                        event_detail=event.get("detail", ""),
                        score=event["score"],
                        intention=intention_result["intention"],
                        priority=intention_result["priority"],
                        scene_snapshot=scene_state,
                        frame=frame.copy() if needs_vision else None,
                        timestamp=timestamp,
                    )
                    # Only record if actually queued
                    qsize_before = self.cognition._queue.qsize()
                    self.cognition.push_task(task)
                    if self.cognition._queue.qsize() > qsize_before:
                        self.telemetry.record_cognition(event["type"], was_vlm=needs_vision)
                        self.session.cognition(event["type"], intention_result["intention"], is_vlm=needs_vision)
                        # ── Importance: cognition triggered by entity ──
                        self._credit_entity("cognition")

            # ── Visualization ──
            if SHOW_PREVIEW:
                frame = self._draw_overlay(
                    frame, faces, objects, scored_events, scene_state, intention_result, focus_info, behavior_info
                )
                cv2.imshow("Vision Perception Runtime", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    logger.info("User pressed Q to quit")
                    break

            # ── Status log ──
            if self._frame_count % 10 == 0:
                self._log_status(scene_state, scored_events, intention_result)

            # ── Curiosity Revisit (non-blocking, uses cached detections) ──
            if self.revisit_controller:
                self.revisit_controller.tick(time.time(), faces=faces,
                                             objects=objects, frame=frame)

            # ── Behavioral snapshot every ~30 min ──
            if self.behavior_telemetry and self._frame_count % 9000 == 0:
                self.behavior_telemetry.snapshot()

            # ── Stability analysis every ~24h (Phase 7C observation protocol) ──
            if time.time() - self._last_stability_check > 86400.0:
                self.stability_analyzer.run()
                self._last_stability_check = time.time()

            # ── P0008: Mission Role refresh (background, non-blocking) ──
            self._maybe_refresh_mission()
            self._check_curiosity_delta()

            # ── P0008 Telemetry: periodic snapshots every ~30 min ──
            if self._frame_count % 9000 == 0:
                self.mission_telemetry.periodic_flush()
                self._log_persona_signature()
                if self.revisit_controller:
                    self.revisit_controller.commitment_telemetry.log_effectiveness()

            # ── Telemetry flush ──
            self.telemetry.maybe_flush(time.time())

            # ── Framerate control ──
            elapsed = time.time() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _credit_entity(self, event_type: str):
        """Credit interaction to the best-matching active entity.

        Finds an entity recently seen whose class matches the expected
        source of this event type (person for tracking/speech/cognition,
        any for generic state changes). Calls entity.record_interaction().

        This feeds the Importance Observatory (Phase 7A). No value formula
        — just record which entities cause downstream events.
        """
        now = time.time()
        best = None
        best_score = -1.0

        # Prefer person-class entities for human-centric events
        prefer_person = event_type in ("tracking", "engaged", "cognition", "speech")

        for e in self.entity_registry.all_entities():
            if not e.is_active:
                continue
            if now - e.last_seen > 5.0:
                continue  # too stale — not the cause

            # Scoring: prefer recently seen, person-class, higher interest
            score = (5.0 - (now - e.last_seen)) * 0.2  # recency
            if prefer_person and e.class_name == "person":
                score += 1.0
            score += e.interest * 0.3  # higher interest = more likely causal

            if score > best_score:
                best = e
                best_score = score

        if best:
            best.record_interaction(event_type)

    # ══════════════════════════════════════════════════
    # P0008: Mission Role (Observation Intent)
    # ══════════════════════════════════════════════════

    def _maybe_refresh_mission(self):
        """Check if mission role needs refresh, spawn background thread if so.

        Non-blocking: the LLM call runs in a daemon thread. The main loop
        continues with the current (or expired) mission weights until the
        new ones arrive. If LLM fails, MissionRole.empty() is cached —
        Runtime continues with intrinsic weights only.
        """
        now = time.time()

        # Guard: don't spawn a new refresh if one is already running
        if (self._mission_refresh_thread
                and self._mission_refresh_thread.is_alive()):
            return

        # Check for expired mission (log once)
        was_active = self.mission_cache.is_active
        if was_active and self.mission_cache.get().is_expired:
            self.mission_telemetry.log_mission_expire()

        # Refresh when: (a) first run, (b) mission expired, (c) periodic
        mission = self.mission_cache.get()
        needs_refresh = (
            not self.mission_cache.is_active
            or mission.is_expired
            or (now - self._last_mission_refresh) > MISSION_ROLE_REFRESH_SEC
        )

        if not needs_refresh:
            return

        # Build context + capture "before" state on main thread
        context = self._build_observation_context()
        before_ranking = self._snapshot_curiosity_ranking()

        self._last_mission_refresh = now
        persona = self._persona
        provider = self.mission_provider
        cache = self.mission_cache
        role_engine = self.role_engine
        registry = self.entity_registry
        telemetry = self.mission_telemetry

        def _refresh():
            try:
                mission = provider.generate(persona, context)
                cache.update(mission)
                # Propagate new weights to existing entities
                role_engine.refresh_entities(registry)

                # ── Telemetry: Mission Refresh ──
                telemetry.log_mission_refresh(
                    persona_name=persona.display_name or persona.name,
                    provider_name=provider.provider_name,
                    ttl_sec=mission.ttl_remaining,
                    context_summary=context.summary_text(),
                    weights=mission.weights,
                    reason=mission.reason,
                )

                # ── Telemetry: Mission Influence ──
                telemetry.log_mission_influence(
                    intrinsic_weights=_intrinsic_snapshot(role_engine, registry),
                    mission_weights=mission.weights,
                )

                # ── Telemetry: Provider Compare (skip when LLM returned empty) ──
                if (provider.provider_name == "llm"
                        and persona.mission_role
                        and mission.weights):  # skip if LLM failed (empty weights)
                    telemetry.log_provider_compare(
                        rule_weights=persona.mission_role,
                        llm_weights=mission.weights,
                    )

            except Exception as e:
                logger.error("MissionRole refresh failed: %s", e)
                # Cache empty result with 60s backoff TTL to prevent
                # refresh storms when LLM is unavailable. Without this,
                # the empty mission expires immediately (TTL=0) and the
                # main loop spawns a new refresh every cycle.
                import time as _time
                cache.update(MissionRole(
                    weights={},
                    expires_at=_time.time() + 60,
                    reason=f"refresh error: {e}",
                    provider_name=provider.provider_name,
                ))
                telemetry.fallback_used_count += 1

        self._mission_refresh_thread = threading.Thread(
            target=_refresh, daemon=True, name="mission-refresh"
        )
        self._mission_refresh_thread.start()

        # ── Telemetry: Curiosity Delta (after thread completes, in a follow-up) ──
        self._curiosity_before_refresh = before_ranking
        self._pending_curiosity_check = True

    def _snapshot_curiosity_ranking(self) -> list:
        """Capture top-N curiosity ranking for delta tracking."""
        from runtime.interest.engine import CuriosityQueue
        raw = self.entity_registry.top_entities(10)
        ranked = CuriosityQueue.rank_entities(
            raw, top_n=10,
            current_pan=self.servo_ptz.pan,
            current_tilt=self.servo_ptz.tilt,
        )
        return [(e.class_name or e.entity_id, s) for e, s in ranked]

    def _check_curiosity_delta(self):
        """After mission refresh, compare curiosity ranking. Call from main loop."""
        if not getattr(self, '_pending_curiosity_check', False):
            return
        # Only check after refresh thread has finished
        if self._mission_refresh_thread and self._mission_refresh_thread.is_alive():
            return
        self._pending_curiosity_check = False

        after_ranking = self._snapshot_curiosity_ranking()
        if self._curiosity_before_refresh and after_ranking:
            self.mission_telemetry.log_curiosity_delta(
                self._curiosity_before_refresh, after_ranking
            )
        self._curiosity_before_refresh = []

    def _on_ptz_decision(self, decision: dict):
        """Callback from RevisitController — log PTZ decision attribution."""
        target_name = decision.get("target", "?")
        interest = decision.get("interest", 0)
        curiosity = decision.get("curiosity", 0)
        role_intrinsic = decision.get("role_intrinsic", 0)
        familiarity = decision.get("familiarity", 0)
        decision_type = decision.get("decision", "?")

        # Get mission boost from role engine
        mission_boost = 0.0
        try:
            # Find matching entity to get class_name for mission lookup
            for e in self.entity_registry.all_entities():
                if e.is_active and (e.class_name or e.entity_id) in target_name:
                    mission_boost = self.role_engine.mission_boost(e)
                    break
        except Exception:
            pass

        # Estimate movement cost from pan distance (rough)
        move_cost = 0.0

        self.mission_telemetry.log_ptz_decision(
            target_name=target_name,
            interest=interest,
            curiosity=curiosity,
            role_intrinsic=role_intrinsic,
            mission_boost=mission_boost,
            familiarity=familiarity,
            movement_cost=move_cost,
            decision=decision_type,
        )

    def _log_persona_signature(self):
        """Periodic attention distribution snapshot."""
        entities = self.entity_registry.all_entities()
        active = [e for e in entities if e.is_active]
        if not active:
            return

        # Aggregate interest by class
        class_interest: dict = {}
        for e in active:
            cn = e.class_name or "unknown"
            class_interest[cn] = class_interest.get(cn, 0) + e.interest

        dist = sorted(class_interest.items(), key=lambda x: x[1], reverse=True)
        self.mission_telemetry.log_persona_signature(
            persona_name=self._persona.display_name or self._persona.name,
            class_distribution=dist,
        )

    def _build_observation_context(self) -> ObservationContext:
        """Build a snapshot of current environment for the LLM advisor.

        Purposefully limited — enough context to make informed suggestions,
        NOT enough to control the Runtime.
        """
        # Active entity summary
        entities = self.entity_registry.all_entities()
        active = [e for e in entities if e.is_active]

        # Class-level summary
        class_counts: Dict[str, dict] = {}
        for e in active:
            cn = e.class_name or "unknown"
            if cn not in class_counts:
                class_counts[cn] = {"class": cn, "count": 0, "total_interest": 0.0}
            class_counts[cn]["count"] += 1
            class_counts[cn]["total_interest"] += e.interest

        active_summary = []
        for cn, info in sorted(class_counts.items(),
                               key=lambda x: x[1]["count"], reverse=True):
            active_summary.append({
                "class": cn,
                "count": info["count"],
                "avg_interest": round(info["total_interest"] / info["count"], 2),
            })

        # Top importance classes (cross-session)
        top_importance = []
        try:
            summary = self.entity_stats_db.load_summary()
            for item in summary.get("by_class", [])[:10]:
                top_importance.append({
                    "class": item["class"],
                    "interactions": item["interactions"],
                    "sessions": item["sessions"],
                })
        except Exception:
            pass

        # Runtime state
        scene = self.scene.get()
        runtime_state = scene.get("runtime_state", "idle")

        return ObservationContext(
            runtime_state=runtime_state,
            active_classes=[a["class"] for a in active_summary],
            active_summary=active_summary,
            top_importance=top_importance,
            total_entities=len(entities),
            active_count=len(active),
        )

    def _log_importance_candidates(self):
        """Write importance observatory data to log (Phase 7A).

        Outputs: Top Interaction Density, Top Event Diversity, Top Interaction Count.
        No value formula — just observes which entities cause downstream events.
        """
        import json, os
        entities = [e for e in self.entity_registry.all_entities()
                    if e.is_active or e.interaction_count > 0]
        if not entities:
            return

        # Sort by three metrics
        by_density = sorted(entities, key=lambda e: e.importance_density, reverse=True)[:10]
        by_diversity = sorted(entities, key=lambda e: e.event_diversity, reverse=True)[:10]
        by_count = sorted(entities, key=lambda e: e.interaction_count, reverse=True)[:10]

        def _summary(e):
            return {
                "entity": e.entity_id,
                "class": e.class_name or e.entity_type,
                "role": e.role_weight,
                "seen": e.seen_count,
                "interactions": e.interaction_count,
                "density": round(e.importance_density, 3),
                "event_diversity": e.event_diversity,
                "event_types": sorted(e.event_types),
                "breakdown": {
                    "tracking": e.tracking_count,
                    "state_transitions": e.state_transition_count,
                    "cognition": e.cognition_trigger_count,
                    "speech": e.speech_related_count,
                },
            }

        report = {
            "ts": time.time(),
            "frame": self._frame_count,
            "top_density": [_summary(e) for e in by_density],
            "top_diversity": [_summary(e) for e in by_diversity],
            "top_count": [_summary(e) for e in by_count],
        }

        # Write to importance_candidates.log
        log_path = os.path.join(os.path.dirname(__file__), "..", "logs",
                                "importance_candidates.log")
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(report) + "\n")
        except Exception:
            pass

        # Summary to main log
        top3 = by_density[:3]
        lines = [f"{e.class_name or e.entity_type}(d={e.importance_density:.2f},div={e.event_diversity})"
                 for e in top3]
        logger.info("Importance top3 (density): %s", " | ".join(lines))

    def _poll_audio(self):
        """Read one audio chunk and update VAD state."""
        if not self._audio_enabled or self.audio is None or self.vad is None:
            return
        try:
            chunk = self.audio.read()
            if chunk is not None:
                was_active = self._voice_active
                self._voice_active = self.vad.is_speech(chunk)
                if self._voice_active and not was_active:
                    self._credit_entity("speech")
        except Exception:
            self._voice_active = False

    # ══════════════════════════════════════════════════
    # Visualization
    # ══════════════════════════════════════════════════

    def _draw_overlay(
        self,
        frame: np.ndarray,
        faces: list,
        objects: list,
        events: list,
        state: dict,
        intention: dict,
        focus_info: dict,
        behavior_info: dict,
    ) -> np.ndarray:
        h, w = frame.shape[:2]

        # Draw faces + objects
        frame = self.face.draw_faces(frame, faces)
        frame = self.object_detector.draw_objects(frame, objects)

        # Highlight focus target
        focus_bbox = focus_info.get("bbox")
        if focus_bbox:
            cv2.rectangle(frame,
                          (focus_bbox["x"], focus_bbox["y"]),
                          (focus_bbox["x"] + focus_bbox["width"], focus_bbox["y"] + focus_bbox["height"]),
                          (0, 255, 255), 3)

        # Top bar: state + focus mode
        runtime_s = state.get("runtime_state", "?").upper()
        focus_mode = focus_info.get("mode", "?").upper()
        focus_label = focus_info.get("label", "")

        mode_colors = {
            "tracking": (0, 255, 255),   # yellow
            "lost": (0, 165, 255),       # orange
            "scanning": (255, 255, 0),   # cyan
            "idle": (200, 200, 200),     # grey
        }
        mc = mode_colors.get(focus_mode.lower(), (255, 255, 255))
        voice_indicator = " 🎤" if self._voice_active else ""
        cv2.putText(frame, f"FOCUS: [{focus_mode}] {focus_label} ({focus_info.get('score', 0):.2f}){voice_indicator}",
                    (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, mc, 2)

        # Behavior state (second line)
        beh_state = behavior_info.get("state", "?").upper()
        beh_colors = {
            "accompanying": (200, 255, 200),  # soft green
            "idle_scan": (180, 180, 255),
            "tracking": (255, 255, 180),
            "engaged": (255, 200, 200),
        }
        bc = beh_colors.get(beh_state.lower(), (180, 180, 255))
        drift_mark = " ~" if behavior_info.get("drift_ok") else ""
        cv2.putText(frame, f"BEHAVIOR: {beh_state}{drift_mark} (s={behavior_info.get('sensitivity', 0):.1f})",
                    (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.4, bc, 1)

        # Self-narrative (third line, subtle)
        thought = behavior_info.get("thought", "")
        cv2.putText(frame, f'"{thought}"',
                    (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (130, 130, 130), 1)

        # Counts
        y0 = 58
        cv2.putText(frame, f"Faces: {len(faces)}  Objects: {len(objects)}",
                    (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

        # Attention scores
        y = y0 + 15
        for ev in events:
            effective = ATTENTION_THRESHOLD * self.scene.attention_multiplier
            triggered = ev["score"] >= effective
            color = (0, 255, 255) if triggered else (100, 100, 100)
            prefix = "🔥" if triggered else "  "
            cv2.putText(frame, f"{prefix}{ev['type']}: {ev['score']:.2f}",
                        (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
            y += 14

        # Memory count
        cv2.putText(frame, f"Mem: {self.memory.total_events}", (10, h - 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 100, 100), 1)

        # Motion bar
        bar_x, bar_y = 10, h - 30
        bar_w = 150
        bar_h = 12
        motion = state.get("motion_level", 0)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
        motion_color = (0, 255, 0) if not self.servo_ptz.moving else (0, 165, 255)
        cv2.rectangle(frame, (bar_x, bar_y), (bar_x + int(bar_w * motion), bar_y + bar_h), motion_color, -1)
        ego_tag = " [PTZ]" if self.servo_ptz.moving else ""
        cv2.putText(frame, f"Motion: {motion:.2f}{ego_tag}", (bar_x, bar_y - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)

        return frame

    # ══════════════════════════════════════════════════
    # Status Logging
    # ══════════════════════════════════════════════════

    def _log_status(self, state: dict, events: list, intention: dict):
        """Periodic status. Only logs summaries, not per-event spam."""
        triggered = [ev for ev in events
                     if ev["score"] >= ATTENTION_THRESHOLD * self.scene.attention_multiplier]
        p = self.presence
        logger.info("Frames: %d | State=%s | Intent=%s | Voice=%s | 🔥=%d",
                    self._frame_count,
                    state.get("runtime_state", "?"),
                    intention.get("label", "?"),
                    self._voice_active,
                    len(triggered))
        logger.info("Presence: stable=%s novelty=%.2f familiarity=%.2f think=%s | Focus=%s Mem=%d",
                    p.stable, p.novelty, p.familiarity, p.should_think,
                    self.focus.mode, self.memory.total_events)


def main():
    runtime = PerceptionRuntime()
    try:
        if not runtime.start():
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
