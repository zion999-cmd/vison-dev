"""
PTZ Revisit Consumer — closes the Active Observation loop.

    Interest → CuriosityQueue → PTZ → YOLO/Face/Motion → confirm/fail

Key invariants:
- NO VLM calls on first revisit (only YOLO + Face + Motion)
- Movement cost prevents 150° swings for marginal score differences
- Consecutive failures trigger obsession dampening (prevents 门口执念症)
- Entity locations update on confirm; regions are fixed anchors
"""

import time, logging, threading
from typing import Optional

from runtime.interest.verifier import VLMVerifier, Verdict
from runtime.commitment.engine import CommitmentEngine, Decision, PRESENCE_WINDOW
from runtime.commitment.telemetry import CommitmentTelemetry

logger = logging.getLogger("Interest.Revisit")

# Classes that YOLO commonly hallucinates on blank walls.
# An anchor with ONLY these (≤2 classes, all from this set) is likely a wall.
_SPARSE_AND_SUSPECT_CLASSES = {
    "cell phone", "mouse", "toothbrush", "book", "keyboard",
    "remote", "clock", "handbag", "cup", "bottle",
}


class RevisitController:
    """Drives PTZ to re-examine curiosity targets.

    Integration point: called from the main loop or a dedicated thread.
    Uses the existing PTZ command queue and CameraState for position.
    """

    # Sweep sequence: alternating left/right. Net sum zero (no drift).
    # Servo pan range: 10°–170°, center 90°.
    _SWEEP_SEQUENCE = [30, -45, 45, -45, 60, -60, 45, -30]

    def __init__(self, interest_engine, servo_ptz, camera_state,
                 object_detector=None, face_detector=None, frame_reader=None,
                 anchor_manager=None, entity_registry=None,
                 on_decision=None, role_engine=None):
        self._engine = interest_engine
        self._servo_ptz = servo_ptz
        self._camera_state = camera_state
        self._object_detector = object_detector
        self._face_detector = face_detector
        self._get_frame = frame_reader
        self._anchor_manager = anchor_manager
        self._entity_registry = entity_registry
        self._on_decision = on_decision  # callable(dict) for PTZ telemetry

        # Tuning
        self.revisit_interval = 8.0     # USB no RTSP lag, can move faster
        self.confirm_duration = 0.5     # instant frame after servo move
        self._confirm_settle = 1.0      # small settle after PTZ (was 3.0 for RTSP)
        self.min_novelty_for_vlm = 0.5
        self._staying_at_anchor = None

        self._last_revisit = 0.0
        self._last_move = 0.0
        self._started_at = 0.0
        self._staying_since = 0.0
        self._max_stay = 300.0
        self._vlm_empty_check_s = 90.0
        self._last_vlm_check = 0.0
        self._last_interest_value = None  # detect flat interest (false positive wall)
        self._flat_interest_since = 0.0
        self._best_anchor_pan = 90.0
        self._best_anchor_interest = 0.0
        self._lock = threading.Lock()

        # Pending confirmations (processed on main thread — no Timer threads
        # that would race on cv2.VideoCapture / ONNX inference).
        self._pending_entity = None     # (entity, deadline)
        self._pending_legacy = None     # (target, deadline)

        # Target tracking (adapted from face_tracker_v7 logic)
        self._last_track = 0.0
        self._track_interval = 1.5      # seconds between track adjustments
        self._track_dead_zone = 0.06    # 6% of frame (ignore tiny offsets)
        self._track_gain = 1.0          # proportional: move 100% of offset
        self._cam_fov_h = 55.0          # horizontal FOV degrees
        self._last_track_dir = None     # for direction lockout
        self._track_nudge_count = 0     # nudges this observe cycle
        self._last_track_hit = 0.0      # last time tracking found a target (presence signal)
        self._last_tilt_recovery = 0.0   # last time we pulled tilt back from extreme

        # Sweep exploration state
        self._sweep_idx = 0
        self._last_turn_direction = None

        # VLM verifier
        self._verifier = VLMVerifier()
        self._verify_threshold = 0.6

        # ── Attention Span tracking ──
        self._attn_target: str = ""          # what we're currently attending to
        self._attn_started: float = 0.0       # when current attention span began
        self._attn_peak_interest: float = 0.0  # peak interest during this span
        self._attn_hits: int = 0              # times target was confirmed
        self._attn_last_hit: float = 0.0       # last time target was seen

        # ── P0008.1: Commitment / Dwell Policy ──
        self.commitment_telemetry = CommitmentTelemetry()
        self._commitment_engine = CommitmentEngine(
            role_engine=role_engine, telemetry=self.commitment_telemetry)

    def tick(self, now: float, faces=None, objects=None, frame=None):
        """Call periodically from main loop. Non-blocking.

        Args:
            faces: pre-computed face detections (avoids re-inference)
            objects: pre-computed YOLO detections (avoids re-inference)
            frame: current BGR frame (for confirmations, avoids camera.read() races)

        Picks between:
        - Entity targets (InterestEngine): things we've seen before
        - Spatial anchors (AnchorManager): places worth checking
        """
        # Cache pre-computed data for _track_target() / VLM check / confirmations
        self._cached_faces = faces
        self._cached_objects = objects
        self._cached_frame = frame

        # ── Process pending confirmations (main-thread, no Timer races) ──
        if self._pending_entity:
            entity, deadline = self._pending_entity
            if now >= deadline:
                self._pending_entity = None
                self._confirm_entity(entity)
        if self._pending_legacy:
            target, deadline = self._pending_legacy
            if now >= deadline:
                self._pending_legacy = None
                self._confirm(target)

        if now - self._last_revisit < self.revisit_interval:
            return

        if self._servo_ptz.moving:
            return  # camera is busy

        # Track startup time for initial explore phase
        if self._started_at == 0.0:
            self._started_at = now

        # ── Stay check: if already looking at something interesting, don't move ──
        startup_phase = (now - self._started_at) < 60.0
        if not startup_phase and self._anchor_manager:
            for a in self._anchor_manager.all_anchors():
                if abs(a.pan - self._servo_ptz.pan) < 15:
                    # Has objects AND interest → stay. Empty anchor → don't stay.
                    if (not a.barren and not a.suppressed
                            and a.interest > 0.08
                            and a.baseline_objects):
                        # Track which anchor we're staying at.
                        # Reset timer on: first stay, switch anchors, or
                        # after a reset (max stay / VLM suppress / PTZ move).
                        if self._staying_since == 0.0 or self._staying_at_anchor != a.anchor_id:
                            self._staying_at_anchor = a.anchor_id
                            self._staying_since = now
                        stayed = now - self._staying_since

                        # VLM verify: staying long at anchor — is this worth watching?
                        # Always check after 90s, even with objects. YOLO false
                        # positives (e.g. 'mouse' on walls) would otherwise
                        # block the check and lock the camera on empty scenes.
                        should_leave = False
                        # Track flat interest: perfectly constant interest = wall
                        if self._last_interest_value is not None and a.interest == self._last_interest_value:
                            if self._flat_interest_since == 0.0:
                                self._flat_interest_since = now
                        else:
                            self._flat_interest_since = 0.0
                        self._last_interest_value = a.interest

                        if stayed > self._vlm_empty_check_s:
                            # Flat interest for 60s+ → empty wall with false positives.
                            # Real scenes have fluctuating interest; flat = YOLO
                            # detecting the same hallucination every frame.
                            if (self._flat_interest_since > 0
                                    and now - self._flat_interest_since > 60.0):
                                logger.info("Flat interest: %s stuck at %.3f for %.0fs — "
                                            "likely empty wall, moving on",
                                            a.anchor_id, a.interest,
                                            now - self._flat_interest_since)
                                a.interest = 0.0
                                self._staying_since = 0.0
                                should_leave = True
                            # Non-VLM heuristic: if anchor has ≤2 baseline objects
                            # that are known false-positive classes, auto-leave
                            # at 120s. Doesn't need VLM or network.
                            elif (len(a.baseline_objects) <= 2
                                    and _SPARSE_AND_SUSPECT_CLASSES & a.baseline_objects == a.baseline_objects
                                    and stayed > 120.0):
                                logger.info("VLM skip: %s has only %s — likely false "
                                            "positives, moving on",
                                            a.anchor_id, a.baseline_objects)
                                a.interest = 0.0
                                self._staying_since = 0.0
                                should_leave = True
                            # VLM check (if available): after 90s + 120s between checks
                            elif now - self._last_vlm_check > 120.0:
                                self._last_vlm_check = now
                                vlm_objects = self._cached_objects or []
                                vlm_frame = self._cached_frame
                                if vlm_frame is not None:
                                    verdict, desc = self._verifier.verify(
                                        vlm_frame, a.anchor_id, vlm_objects)
                                    if verdict == Verdict.TRIVIAL:
                                        logger.info("VLM verify: %s is %s — "
                                                    "suppressing, moving on",
                                                    a.anchor_id, desc)
                                        a.suppressed = True
                                        a.suppressed_at = now
                                        a.interest = 0.0
                                        self._staying_since = 0.0
                                        should_leave = True

                        # Progressive max stay: interest (novelty) + tracking (presence).
                        # Interest floor is 0.10 — an anchor with a person in
                        # baseline has low novelty but IS worth watching.
                        obj_count = len(a.baseline_objects)
                        has_life = (now - self._last_track_hit) < 15.0  # tracked recently

                        if a.interest >= 0.30 and obj_count >= 3:
                            max_stay = self._max_stay       # 300s — changing + varied
                        elif a.interest >= 0.15 or has_life:
                            max_stay = 120.0                 # 2min — moderate or person present
                        else:
                            max_stay = 30.0                  # idle — nothing here

                        # Remember best anchor for quick return
                        if a.interest > self._best_anchor_interest:
                            self._best_anchor_interest = a.interest
                            self._best_anchor_pan = a.pan

                        if should_leave:
                            # False positive (VLM/flat-interest) — never hold it.
                            self._commitment_engine.reset()
                            self._attn_end("suppressed")
                            pass  # fall through to target selection
                        elif stayed > max_stay:
                            # P0008.1: a present person keeps us here past the
                            # novelty-based stay timeout (the Commitment Gap).
                            if self._commitment_holds(now):
                                self._staying_since = now
                                self._track_target(now)
                                return
                            if max_stay <= 30.0:
                                logger.info("Revisit [leave]: %s tier=idle int=%.3f objs=%d "
                                            "track=%.0fs ago → boring, move on",
                                            a.anchor_id, a.interest, obj_count,
                                            now - self._last_track_hit)
                                a.interest = 0.05
                                self._attn_end("idle_timeout")
                            elif max_stay <= 120.0:
                                logger.info("Revisit [leave]: %s tier=moderate int=%.3f objs=%d "
                                            "track=%.0fs ago → %.0f/120s, move on",
                                            a.anchor_id, a.interest, obj_count,
                                            now - self._last_track_hit, stayed)
                                a.interest *= 0.7
                                self._attn_end("moderate_timeout")
                            else:
                                logger.info("Revisit [leave]: %s tier=hot int=%.3f objs=%d "
                                            "track=%.0fs ago → %.0f/300s maxed, move on",
                                            a.anchor_id, a.interest, obj_count,
                                            now - self._last_track_hit, stayed)
                                a.interest *= 0.5
                                self._attn_end("hot_timeout")
                            self._staying_since = 0.0
                            # fall through to target selection
                        else:
                            tier = "hot" if max_stay >= 300 else ("moderate" if max_stay >= 120 else "idle")
                            logger.info("Revisit [stay]: %s tier=%s int=%.3f objs=%d "
                                        "track=%.0fs ago → staying %.0f/%.0fs",
                                        a.anchor_id, tier, a.interest, obj_count,
                                        now - self._last_track_hit, stayed, max_stay)
                            self._notify_decision(
                                target_name=a.anchor_id,
                                decision=f"stay_{tier}",
                                interest=a.interest,
                            )
                            self._last_revisit = now
                            # Tilt recovery: prolonged high tilt (>90s above 150°) →
                            # pull back to 120° to prevent servo limit wear.
                            # SG90 potentiometers degrade when held at extremes.
                            if (self._servo_ptz.tilt > 150
                                    and now - self._last_tilt_recovery > 90.0):
                                logger.info("Tilt recovery: %d°→120° (was high for %.0fs)",
                                            self._servo_ptz.tilt,
                                            now - self._last_tilt_recovery)
                                self._servo_ptz.tilt_to(120)
                                self._last_tilt_recovery = now
                            # Track target while staying — keep person/object centered
                            self._track_target(now)
                            return
                    if not a.baseline_objects and a.interest < 0.15:
                        # Empty wall — mark barren immediately, move on
                        a.barren = True
                        a.barren_at = now
                    break

        # ── Discovery: sweep exploration with alternating left/right turns ──
        # New system has no position feedback — use duration-based sweeps.
        # Sweep sequence: alternating left/right, increasing degrees
        startup_phase = (now - self._started_at) < 60.0
        if startup_phase and now - self._last_move > 8.0:
            deg = self._SWEEP_SEQUENCE[self._sweep_idx % len(self._SWEEP_SEQUENCE)]
            self._sweep_idx += 1
            direction = 'left' if deg < 0 else 'right'
            self._servo_ptz.tilt_to(95)  # explore at level, not tracking tilt
            self._turn(direction, abs(deg))
            self._last_turn_direction = direction
            self._last_move = now
            self._staying_since = 0.0
            self._staying_at_anchor = None
            self._last_revisit = now
            logger.info("Revisit [sweep]: turn %s %d° [sweep %d]",
                        direction, abs(deg), self._sweep_idx)
            return

        # ── Target selection: entity first, then turn-based explore ──
        target = None
        target_source = None

        # 1. Entity targets
        entities = []
        if self._entity_registry:
            from runtime.interest.engine import CuriosityQueue
            raw = self._entity_registry.top_entities(10)
            entities = CuriosityQueue.rank_entities(
                raw, top_n=5,
                current_pan=self._servo_ptz.pan,
                current_tilt=self._servo_ptz.tilt,
            )
        best_entity = None
        entity_score = 0.0
        for e, s in entities:
            if e.consecutive_fails > 0:
                continue  # skip previously-failed entities
            best_entity = e
            entity_score = s
            break

        # 2. Legacy targets
        legacy = self._engine.next_revisit(
            current_pan=self._servo_ptz.pan,
            current_tilt=self._servo_ptz.tilt,
        )
        legacy_score = legacy.curiosity_score if legacy else 0

        # ── Pick winner ──
        staying = self._staying_since > 0 and (now - self._staying_since) > 15
        # Entity must have been seen at least 3 times. Single-sighting
        # entities from sweeps are YOLO noise that push PTZ to walls.
        entity_credible = (best_entity is not None)  # familiarity handles suppression now
        if best_entity and entity_score > 0.02 and entity_credible:
            if not staying or best_entity.confirm_count > 0 or entity_score > 0.20:
                target = best_entity
                target_source = "entity"
        if target is None and legacy and legacy_score > 0.01:
            target = legacy
            target_source = "legacy"

        # Decision chain: show all candidates + winner + rejection reason
        if best_entity:
            fam = getattr(best_entity, 'familiarity_score', 0.0)
            role = getattr(best_entity, 'role_weight', 0.2)
            e_label = f"{best_entity.entity_id}(c={entity_score:.2f},fam={fam:.2f},role={role:.1f},seen={best_entity.seen_count})"
        else:
            e_label = "none"
        l_label = f"{legacy.target_id}(s={legacy_score:.2f})" if legacy else "none"

        # Determine rejection reason when entity was considered but not picked
        reject = ""
        if target_source is None and best_entity is not None:
            if entity_score <= 0.02:
                reject = f" (c={entity_score:.2f} below threshold)"
            elif staying and best_entity.confirm_count == 0 and entity_score <= 0.20:
                reject = " (staying override)"

        logger.info("Revisit [pick]: entity=%s legacy=%s staying=%s → %s%s",
                    e_label, l_label,
                    "yes" if staying else "no",
                    target_source or "explore", reject)

        # ── Curiosity ecology: top absent entities every 2 min ──
        if self._entity_registry and now - getattr(self, '_last_ecology_log', 0) > 120.0:
            self._last_ecology_log = now
            raw = self._entity_registry.top_entities(5)
            if raw:
                lines = []
                for e in raw:
                    fam = getattr(e, 'familiarity_score', 0.0)
                    role = getattr(e, 'role_weight', 0.2)
                    since = now - e.last_seen
                    lines.append(f"{e.class_name or e.entity_type}(c={e.curiosity_score:.2f},fam={fam:.2f},role={role:.1f},since={since:.0f}s)")
                logger.info("Curiosity top5: %s", " | ".join(lines))
            else:
                logger.info("Curiosity top5: (empty — nothing to revisit yet)")

        # No entity/legacy target → try best anchor first, then explore
        if target is None:
            # P0008.1: no curiosity target — hold if a person is still present.
            if self._commitment_holds(now):
                self._track_target(now)
                return
            if now - self._last_move > 20.0:
                pan = self._servo_ptz.pan
                # Exploration should be level (t95), not at tracking tilt
                self._servo_ptz.tilt_to(95)
                # ── Prefer returning to best anchor (skips boring idle loops) ──
                if (self._best_anchor_interest > 0.25
                        and abs(self._best_anchor_pan - pan) > 15):
                    d_pan = self._best_anchor_pan - pan
                    direction = 'left' if d_pan < 0 else 'right'
                    deg = min(abs(int(d_pan)), 60)
                    self._turn(direction, deg)
                    self._last_move = now
                    self._staying_since = 0.0
                    self._staying_at_anchor = None
                    self._last_revisit = now
                    logger.info("Revisit [return]: best anchor pan=%.0f int=%.3f → %s %d°",
                                self._best_anchor_pan, self._best_anchor_interest,
                                direction, deg)
                    self._best_anchor_interest *= 0.3  # decay to prevent ping-pong
                    return

                # ── Explore turn ──
                if pan < 50:
                    direction = 'right'  # drifting left, pull back
                elif pan > 130:
                    direction = 'left'   # drifting right, pull back
                else:
                    import random
                    direction = random.choice(['left', 'right'])
                # Pick degrees from sweep sequence, cycling
                deg = abs(self._SWEEP_SEQUENCE[self._sweep_idx % len(self._SWEEP_SEQUENCE)])
                self._sweep_idx += 1
                self._turn(direction, deg)
                self._last_turn_direction = direction
                self._last_move = now
                self._staying_since = 0.0
                self._staying_at_anchor = None
                self._last_revisit = now
                logger.info("Revisit [turn]: no entity, %s %d° (pan=%d) [sweep %d]",
                            direction, deg, pan, self._sweep_idx)
                self._notify_decision(
                    target_name=f"explore_{direction}",
                    decision="explore",
                )
                return

            self._last_revisit = now
            return

        # ── P0008.1: don't switch to a challenger unless it clearly beats our hold ──
        challenger = entity_score if target_source == "entity" else legacy_score
        if self._commitment_holds(now, challenger_curiosity=challenger):
            self._track_target(now)
            return

        # ── Execute movement ──
        if target_source == "entity":
            tid = target.entity_id
            score = entity_score
            # Estimate angle delta from servo pan (accurate, not dead-reckoned)
            d_pan = target.last_pan - self._servo_ptz.pan
            direction = 'left' if d_pan < 0 else 'right'
            logger.info("Revisit [entity]: %s (score=%.3f, d_pan=%.0f° → %s %d°)",
                        tid, score, d_pan, direction, abs(int(d_pan)))
            self._notify_decision(
                target_name=f"{target.class_name or '?'} ({tid})",
                decision="track_entity",
                interest=getattr(target, 'interest', 0),
                curiosity=score,
                role_intrinsic=getattr(target, 'role_weight', 0.2),
                familiarity=getattr(target, 'familiarity_score', 0),
            )
            if abs(d_pan) > 3:
                self._turn(direction, abs(int(d_pan)))
                self._last_turn_direction = direction
                self._last_move = now
                self._staying_since = 0.0
                self._staying_at_anchor = None
            target.record_revisit_attempt()
            self._pending_entity = (target, now + self.confirm_duration + self._confirm_settle)
        elif target_source == "legacy":
            tid = target.target_id
            score = legacy_score
            loc = target.location
            d_pan = loc[0] - self._servo_ptz.pan
            direction = 'left' if d_pan < 0 else 'right'
            logger.info("Revisit [legacy]: %s (score=%.3f, d_pan=%.0f° → %s %d°)",
                        tid, score, d_pan, direction, abs(int(d_pan)))
            if abs(d_pan) > 3:
                self._turn(direction, abs(int(d_pan)))
                self._last_turn_direction = direction
                self._last_move = now
                self._staying_since = 0.0
                self._staying_at_anchor = None
            self._engine.record_revisit(target.target_id)
            self._pending_legacy = (target, now + self.confirm_duration + self._confirm_settle)

        self._last_revisit = now

    def _confirm_entity(self, entity):
        """After PTZ settles at entity's last position, match + confirm.

        Uses cached frame + detections from main thread (no camera.read()
        or ONNX inference races).
        """
        frame = self._cached_frame
        objects = self._cached_objects or []

        if frame is None:
            was_active = entity.is_active
            entity.record_revisit_fail()
            if was_active and not entity.is_active:
                logger.info("Entity %s FORGOTTEN (no cached frame, %d fails)",
                            entity.entity_id, entity.consecutive_fails)
            return

        # Try to match any detection to this entity
        from runtime.interest.entity_registry import _compute_signature, _signature_distance
        matched = False
        for det in objects:
            sig = _compute_signature(frame, det.get("bbox"))
            if entity.visual_signature and sig:
                dist = _signature_distance(sig, entity.visual_signature)
                if dist < 60.0:  # same threshold as registry
                    matched = True
                    # Update entity position from servo
                    entity.last_pan = self._servo_ptz.pan
                    entity.last_tilt = self._servo_ptz.tilt
                    entity.last_bbox = det.get("bbox")
                    break

        if matched:
            entity.record_revisit_success()
            entity.interest = min(1.0, entity.interest + 0.15)
            logger.info("Entity %s confirmed at pan=%d", entity.entity_id,
                        self._servo_ptz.pan)
            self._attn_hit(entity.interest)
        else:
            was_active = entity.is_active
            entity.record_revisit_fail()
            entity.interest = max(0.02, entity.interest * 0.85)
            logger.info("Entity %s NOT found at pan=%.0f (fails=%d)",
                        entity.entity_id, self._servo_ptz.pan,
                        entity.consecutive_fails)
            if was_active and not entity.is_active:
                logger.info("Entity %s FORGOTTEN after %d consecutive fails",
                            entity.entity_id, entity.consecutive_fails)
                self._attn_end("lost")

    def _track_target(self, now: float):
        """During active stay, track target — adjust PTZ to center it smoothly.

        Uses YOLO bbox + Face bbox to compute exact angular offset.
        Bbox center → pixel offset → FOV-proportional angle delta → servo move.

        Hardware: p0=rightmost, p180=leftmost.
        Target on LEFT of frame → turn LEFT (pan increase → 180).
        Target on RIGHT of frame → turn RIGHT (pan decrease → 0).
        """
        if self._servo_ptz is None or self._servo_ptz.moving:
            return
        if now - self._last_track < self._track_interval:
            return

        # Use pre-computed detections from main loop (avoids duplicate ONNX inference)
        faces = self._cached_faces or []
        objects = self._cached_objects or []

        # Need frame dimensions for offset calc — use fixed camera resolution
        h, w = 480, 640
        cx, cy = w / 2, h / 2

        # Priority: face > YOLO person (largest bbox = closest)
        best_cx, best_cy, label = None, None, ""

        # 1. Face detection — most precise for centering
        if faces:
            b = max(faces, key=lambda f: f.get("confidence", 0)).get("bbox", {})
            if b:
                best_cx = b.get("x", 0) + b.get("width", 0) / 2
                best_cy = b.get("y", 0) + b.get("height", 0) / 2
                label = "face"

        # 2. YOLO person — bbox gives exact position, smooth tracking
        if best_cx is None and objects:
            persons = [o for o in objects if o.get("class_name") == "person"]
            if persons:
                # Pick largest person bbox (closest to camera, most reliable)
                p = max(persons, key=lambda o:
                        o["bbox"]["width"] * o["bbox"]["height"])
                best_cx = p.get("center_x", 0)
                best_cy = p.get("center_y", 0)
                label = "person"

        if best_cx is None:
            return

        # Presence signal: we saw a person/face right now → anchor is "live"
        self._last_track_hit = now

        # P0008.1: establish/refresh commitment to the person being tracked.
        self._commitment_engine.begin("person", now)

        # Offset from center (−1..+1, negative=left side of frame)
        dx = (best_cx - cx) / w
        dy = (best_cy - cy) / h

        # Dead zone: skip tiny offsets to prevent micro-oscillation
        if abs(dx) < self._track_dead_zone and abs(dy) < self._track_dead_zone:
            return
        self._last_track = now

        # Proportional: bbox offset → angular delta via FOV
        # Pan:  dx negative = left side → pan INCREASE toward 180 (correct with -dx)
        # Tilt: dy positive = below center → tilt INCREASE toward 180 (look down)
        pan_delta_raw = -dx * self._cam_fov_h * self._track_gain
        tilt_delta_raw = dy * self._cam_fov_h * (h / w) * self._track_gain

        # Round to integer for servo (no minimum clamp — dead zone suffices)
        pan_delta = int(round(pan_delta_raw))
        tilt_delta = int(round(tilt_delta_raw))

        # Safety clamp: max 15° pan, 8° tilt per adjustment
        pan_delta = max(-15, min(15, pan_delta))
        tilt_delta = max(-8, min(8, tilt_delta))

        # Tilt fatigue: when tilt is already high, dampen further downward
        # pushes. Holding at the mechanical limit (was 180°, now 170°) for
        # minutes causes SG90 potentiometer wear and position drift —
        # the same failure mode as the pan servo "不停旋转".
        # 155°→170°: downward gain linearly decays from 1.0→0.25.
        current_tilt = self._servo_ptz.tilt
        if tilt_delta > 0 and current_tilt > 155:
            fatigue = max(0.25, (170 - current_tilt) / 15.0)
            damped = int(round(tilt_delta * fatigue))
            if damped != tilt_delta:
                logger.info("Track tilt fatigue: %d°→%d° (gain=%.2f, was %+d now %+d)",
                            current_tilt, current_tilt + damped, fatigue,
                            tilt_delta, damped)
                tilt_delta = damped

        if pan_delta == 0 and tilt_delta == 0:
            return

        logger.info("Track %s: dx=%.2f dy=%.2f → pan%+d tilt%+d (pan=%d tilt=%d)",
                    label, dx, dy, pan_delta, tilt_delta,
                    self._servo_ptz.pan, current_tilt)

        # Attention span: tracking hit
        self._attn_hit()

        if pan_delta != 0:
            self._servo_ptz.pan_relative(pan_delta)
        if tilt_delta != 0:
            self._servo_ptz.tilt_to(self._servo_ptz.tilt + tilt_delta)

    # ── Attention Span ──

    def _attn_begin(self, target: str, interest: float = 0):
        """Record the start of an attention span."""
        now = time.time()
        # If switching targets, log the previous span first
        if self._attn_target and self._attn_target != target:
            self._attn_end("switched")
        self._attn_target = target
        self._attn_started = now
        self._attn_peak_interest = interest
        self._attn_hits = 1 if interest > 0 else 0
        self._attn_last_hit = now if interest > 0 else 0

    def _attn_hit(self, interest: float = 0):
        """Record a successful confirmation of the current target."""
        if not self._attn_target:
            return
        now = time.time()
        self._attn_hits += 1
        self._attn_last_hit = now
        if interest > self._attn_peak_interest:
            self._attn_peak_interest = interest

    def _attn_end(self, reason: str = ""):
        """Log the completed attention span."""
        if not self._attn_target:
            return
        now = time.time()
        duration = now - self._attn_started
        if duration < 1.0 and reason not in ("switched",):
            self._attn_target = ""
            return  # skip sub-second spans (noise)

        since_last = now - self._attn_last_hit if self._attn_last_hit > 0 else duration
        logger.info(
            "\n%s\n"
            "Attention Span\n"
            "%s\n"
            "  Target       : %s\n"
            "  Duration     : %.0fs\n"
            "  Hits         : %d\n"
            "  Peak interest: %.2f\n"
            "  Last seen    : %.0fs ago\n"
            "  Reason       : %s\n"
            "%s",
            "─" * 45, "─" * 45,
            self._attn_target, duration, self._attn_hits,
            self._attn_peak_interest, since_last, reason or "?",
            "─" * 45,
        )
        self._attn_target = ""

    # ── Commitment (P0008.1) ──

    def _commitment_holds(self, now: float, challenger_curiosity: float = 0.0) -> bool:
        """True if the CommitmentEngine says HOLD (keep the current target).

        status reflects whether a person is currently present (fresh track hit).
        """
        if not self._commitment_engine.has_commitment:
            return False
        status = "active" if (now - self._last_track_hit) < PRESENCE_WINDOW else "lost"
        decision, _reason = self._commitment_engine.arbitrate(
            challenger_curiosity, status, now)
        return decision == Decision.HOLD

    def _notify_decision(self, target_name: str, decision: str,
                         interest: float = 0, curiosity: float = 0,
                         role_intrinsic: float = 0, familiarity: float = 0):
        """Notify telemetry callback of a PTZ decision (if registered)."""
        # ── Attention span tracking ──
        if decision.startswith("track") or decision.startswith("stay"):
            if self._attn_target != target_name:
                self._attn_begin(target_name, interest)
        elif decision == "explore":
            self._attn_end("explore")

        if self._on_decision is None:
            return
        try:
            self._on_decision({
                "target": target_name,
                "decision": decision,
                "interest": interest,
                "curiosity": curiosity,
                "role_intrinsic": role_intrinsic,
                "familiarity": familiarity,
                "pan": self._servo_ptz.pan,
                "tilt": self._servo_ptz.tilt,
            })
        except Exception:
            pass  # telemetry must never crash the main loop

    def _turn(self, direction: str, degrees: int):
        """Turn camera left or right by N degrees via servo pan."""
        if direction == 'left':
            delta = -abs(degrees)
        else:
            delta = abs(degrees)
        self._servo_ptz.pan_relative(delta)

    def _confirm_anchor(self, anchor):
        """After PTZ settles at an anchor point, observe and update baseline.

        If novelty is high, triggers VLM verification to judge whether
        the detected change is worth tracking or just a wall/shadow/etc.
        """
        frame_data = self._get_frame()
        if frame_data is None or frame_data[0] is None:
            return
        frame = frame_data[0]
        objects = []
        if self._object_detector:
            objects = self._object_detector.detect(frame)

        # ── VLM Verification (low-frequency, high-novelty only) ──
        if (not anchor.suppressed
                and anchor.novelty > self._verify_threshold
                and anchor.visit_count >= 3):
            verdict, description = self._verifier.verify(
                frame, anchor.anchor_id, objects)
            if verdict == Verdict.TRIVIAL:
                # Wall, shadow, reflection, etc. — ignore (auto-revive after timeout)
                anchor.suppressed = True
                anchor.suppressed_at = time.time()
                anchor.interest = 0.0
                logger.info("VLM suppressed %s: %s (was interest=%.2f)",
                            anchor.anchor_id, description, anchor.interest)
            elif verdict == Verdict.INTERESTING:
                # Genuine discovery — boost interest
                anchor.interest = min(1.0, anchor.interest + 0.15)
                logger.info("VLM confirmed %s: %s (interest→%.2f)",
                            anchor.anchor_id, description, anchor.interest)
            # UNCERTAIN: no action, keep observing

        self._anchor_manager.observe(
            objects=objects,
            pan=self._servo_ptz.pan,
            tilt=self._servo_ptz.tilt,
        )
        logger.debug("Anchor %s confirmed: %d objects, novelty=%.2f, suppressed=%s",
                     anchor.anchor_id, len(objects), anchor.novelty, anchor.suppressed)

    def _confirm(self, target):
        """After PTZ settles, check cached detections: is the target still there?

        Uses cached YOLO + Face from main thread. NO camera.read() or ONNX races.
        """
        detections = self._cached_objects or []
        cached_faces = self._cached_faces or []

        found = False

        # Check via cached object detections
        if target.category == "person":
            persons = [d for d in detections if d.get("class_name") == "person"]
            found = len(persons) > 0
        elif target.category == "object":
            objects = [d for d in detections
                      if d.get("class_name") not in ("person",)]
            found = len(objects) > 0
        else:
            found = len(detections) > 0  # region: anything detected

        # Check via cached face detections
        if not found:
            found = len(cached_faces) > 0

        # Update interest
        if found:
            self._engine.see(
                target.target_id,
                target_type=target.target_type,
                category=target.category,
                location=target.location,
                novelty=0.3,
            )
            logger.info("Revisit confirmed: %s (interest=%.2f)",
                        target.target_id, target.interest)
        else:
            self._engine.record_revisit_failed(target.target_id)
            logger.info("Revisit failed: %s (interest=%.2f, fails=%d)",
                        target.target_id, target.interest,
                        target.consecutive_fails)

            if target.consecutive_fails >= target.max_consecutive_fails:
                logger.info("Forgetting %s — %d consecutive failures",
                            target.target_id, target.consecutive_fails)
                self._engine.forget(target.target_id)

    @property
    def busy(self) -> bool:
        return self._servo_ptz.moving
