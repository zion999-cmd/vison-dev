"""
PTZ Revisit Consumer — closes the Active Observation loop.

    Interest → CuriosityQueue → PTZ → YOLO/Face/Motion → confirm/fail

Key invariants:
- NO VLM calls on first revisit (only YOLO + Face + Motion)
- Movement cost prevents 150° swings for marginal score differences
- Consecutive failures trigger obsession dampening (prevents 门口执念症)
- Entity locations update on confirm; regions are fixed anchors
"""

import math, time, logging, threading
from typing import Optional

from runtime.interest.verifier import VLMVerifier, Verdict
from runtime.commitment.engine import CommitmentEngine, Decision, PRESENCE_WINDOW
from runtime.commitment.telemetry import CommitmentTelemetry
from runtime.perception.ptz_motion import PtzMotion

logger = logging.getLogger("Interest.Revisit")

# Classes that YOLO commonly hallucinates on blank walls.
# An anchor with ONLY these (≤2 classes, all from this set) is likely a wall.
_SPARSE_AND_SUSPECT_CLASSES = {
    "cell phone", "mouse", "toothbrush", "book", "keyboard",
    "remote", "clock", "handbag", "cup", "bottle",
}

# ── Gentle framing: three independent knobs, not one coupled band ──
# A tracked target is kept in frame, not driven to the middle of it. The three
# quantities below are deliberately separate values, because one band cannot do
# all three jobs: making the comfort zone large would then also force a late
# trigger AND a weak correction AND a target that comes to rest near the edge.
# That is exactly what the first cut did — a 192 px trigger, half the
# correction, and a 96 px residual — which on hardware reads as "it does not
# react, and when it does it barely moves".
#
#   start_offset — nothing moves below it, so small head and body movement is
#                  free. Crossing it engages a follow.
#   aim_offset   — where that follow takes the target, i.e. the residual it
#                  leaves. Not the centre, and clearly inside the start offset.
#   gain         — how much of the remaining excess one update removes. 1.0 is
#                  the largest value that cannot overshoot the aim point and
#                  the value at which the aim offset is actually reached, which
#                  is also what brings the ±15°/±8° safety clamps back to life.
FRAMING_START_X = 0.15      # normalised |dx| that engages a pan follow
FRAMING_AIM_X = 0.06        # normalised |dx| a pan follow drives to
FRAMING_START_Y = 0.20      # normalised |dy| that engages a tilt follow
FRAMING_AIM_Y = 0.08        # normalised |dy| a tilt follow drives to
FRAMING_GAIN = 1.0          # fraction of the excess removed per update

# Measured on the SG90s: ~8 ms per degree, both axes, plus ~15 ms fixed
# overhead. Used to know when the camera's own last move has settled, so the
# next correction is computed from a frame that postdates it.
SERVO_SEC_PER_DEG = 0.008


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
        # Sole movement writer: every pan/tilt move below goes through it.
        self._motion = PtzMotion(servo_ptz)
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

        # Gentle framing (keep-in-frame). Tuning sits here with the rest of the
        # revisit cadence, and each value can be overridden per instance.
        self._last_track = 0.0
        self._track_interval = 1.5      # seconds between framing updates
        self._cam_fov_h = 55.0          # horizontal FOV degrees
        self._framing_start_x = FRAMING_START_X
        self._framing_aim_x = FRAMING_AIM_X
        self._framing_start_y = FRAMING_START_Y
        self._framing_aim_y = FRAMING_AIM_Y
        self._framing_gain = FRAMING_GAIN
        self._framing_pan = False       # a pan follow is running (hysteresis)
        self._framing_tilt = False      # a tilt follow is running (hysteresis)
        self._last_track_move = 0.0     # travel time of the last emitted correction
        self._last_track_hit = 0.0      # last time a target was in view (presence signal)
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

    def tick(self, now: float, faces=None, objects=None, frame=None,
             frame_age: float = 0.0):
        """Call periodically from main loop. Non-blocking.

        Args:
            faces: pre-computed face detections (avoids re-inference)
            objects: pre-computed YOLO detections (avoids re-inference)
            frame: current BGR frame (for confirmations, avoids camera.read() races)
            frame_age: seconds since this frame was captured. The framing loop
                needs it to know whether an observation postdates its own last
                move — see _acquire_track_target.

        Picks between:
        - Entity targets (InterestEngine): things we've seen before
        - Spatial anchors (AnchorManager): places worth checking
        """
        # Cache pre-computed data for _track_target() / VLM check / confirmations
        self._cached_faces = faces
        self._cached_objects = objects
        self._cached_frame = frame
        # How old this frame was when we saw it (the loop's detection time).
        self._frame_age = frame_age

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

        # ── Movement updates for an open tracking session ──
        # A session is opened by the revisit/commitment flow further down,
        # never by a detection: seeing a face or a person is not by itself a
        # reason to start following it. Once one is open, though, its framing
        # updates must not wait out the revisit gate — the camera has to answer
        # in _track_interval, not in revisit_interval. _framing_update keeps
        # its own 1.5s interval and its own "camera busy" guard and does
        # nothing when no face/person is in view, so this only shortens the
        # cadence while a target is actually being framed. Idle, revisit and
        # sweep still run on the revisit cadence below.
        if self._tracking_session_active(now):
            self._framing_update(now)

        # Advance pending movement every frame, not only when the revisit gate
        # opens — otherwise a rate-limited move would stall between decisions.
        self._motion.step(now)

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
                            # The anchor is a false positive (flat interest /
                            # sparse suspect classes / VLM said trivial). Its
                            # interest was already zeroed above, and that is
                            # what ends the stay — it drops out of the stay
                            # candidates on its own.
                            #
                            # It must NOT clear the commitment. That belongs to
                            # the person being watched: "this anchor is boring"
                            # is not "this person left". Clearing it here killed
                            # the tracking session mid-walk on hardware
                            # (2026-09-24 t=170s) and left the camera pointed at
                            # nothing for 16s while the person stayed in frame.
                            # The commitment still ends on its own terms —
                            # lost / stale / timeout in `decide()`.
                            self._attn_end("suppressed")
                            # fall through to target selection
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
                                self._motion.wear_protect(120)
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
            # Explore at level, not at tracking tilt — but only when nothing
            # has been tracked recently. Levelling the tilt while a target is
            # still around yanks it away from the follow, and the ~29° it then
            # has to re-correct through a ±8° clamp saturates for 3-4 commands
            # every cycle. Detection misses leave multi-second gaps inside a
            # follow, so the motion layer's short ownership hold is not enough
            # on its own; use the existing presence window.
            if now - self._last_track_hit >= PRESENCE_WINDOW:
                self._motion.explore(now, tilt=95)
            self._turn(direction, abs(deg), now)
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
                # Same guard as the sweep: only level the tilt when nothing
                # has been tracked recently (see the sweep path above).
                if now - self._last_track_hit >= PRESENCE_WINDOW:
                    self._motion.explore(now, tilt=95)
                # ── Prefer returning to best anchor (skips boring idle loops) ──
                if (self._best_anchor_interest > 0.25
                        and abs(self._best_anchor_pan - pan) > 15):
                    d_pan = self._best_anchor_pan - pan
                    direction = 'left' if d_pan < 0 else 'right'
                    deg = min(abs(int(d_pan)), 60)
                    self._turn(direction, deg, now)
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
                self._turn(direction, deg, now)
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
                self._turn(direction, abs(int(d_pan)), now)
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
                self._turn(direction, abs(int(d_pan)), now)
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

    # ── Tracking session: establishment, then gentle framing ──

    def _tracking_session_active(self, now: float) -> bool:
        """True while the revisit/commitment flow holds an open tracking session.

        A session is opened by that flow — `_track_target` is the only caller of
        CommitmentEngine.begin — and it lives as long as its target is still
        being seen. A detection on its own never opens one: deciding to follow
        someone is a decision, not something the detector hands over.
        """
        return (self._commitment_engine.has_commitment
                and (now - self._last_track_hit) < PRESENCE_WINDOW)

    def _track_target(self, now: float):
        """Open or refresh the tracking session, and frame its target once.

        Called by the revisit/commitment flow and nowhere else — this is the
        only place a session is established.
        """
        target = self._acquire_track_target(now)
        if target is None:
            return
        self._commitment_engine.begin("person", now)
        self._aim(now, target)

    def _framing_update(self, now: float):
        """One keep-in-frame update for a session that is already open.

        Refreshes the commitment instead of establishing it: a target we are
        actively framing is by definition still present, and without the
        refresh the commitment would go stale mid-frame and drop a person
        standing right in front of the camera.
        """
        target = self._acquire_track_target(now)
        if target is None:
            return
        self._commitment_engine.confirm(now)
        self._aim(now, target)

    def _following(self) -> bool:
        """True while a follow is engaged on either axis.

        Engagement is edge-triggered by the spatial hysteresis in
        `_framing_step`: a target inside the start offset never engages, so a
        faster motion update cannot make the camera react to bbox jitter — it
        only applies to a follow that is already correcting. That separation is
        what lets the execution cadence differ from the decision cadence.
        """
        return self._framing_pan or self._framing_tilt

    def _acquire_track_target(self, now: float):
        """The face or person to frame, as (cx, cy, label), or None.

        Also records the presence signal (`_last_track_hit`) that the session
        gate and the sweep's tilt-levelling guard both read. The face bbox is
        preferred over the YOLO person bbox — it is the more precise of the two.

        Two cadences are deliberately separate here. The revisit cadence gates
        the *decision* — may a follow start? — and applies while nothing is
        engaged. An engaged follow refreshes its motion goal on every valid
        observation: capping that at _track_interval limited the chase to one
        correction per 1.5s, i.e. 10 deg/s pan and 5.33 deg/s tilt, which a
        walking person outruns (2026-09-24 17:28, t=228-240s: the target moved
        at 10.7 deg/s median while the camera could answer only 9.8 deg/s in
        1.71s steps, leaving a 200px median trailing error).

        Updating faster is safe by construction: PtzMotion coalesces (latest
        goal wins), never queues, and still rate-limits to MAX_STEP_DEG per
        frame, so a faster goal cannot become a backlog of relative nudges.
        """
        if self._servo_ptz is None or self._servo_ptz.moving:
            return None

        # The decision cadence (1.5s) gates whether a follow may start. An
        # engaged follow refreshes its goal on every *fresh* observation — and
        # fresh means the frame was captured after the camera's own last move
        # finished. Without that, the loop answers a view of the world it has
        # already changed, computes the same correction again, and runs away:
        # on hardware 2026-09-25 07:57 the pan crossed 165°→37° in eight
        # consecutive -15° frames while the measured dx rose 0.16→0.48 (a 128°
        # pan must move dx by -2.3), ending pinned at its mechanical limit
        # repeating a saturated no-op command.
        if self._following():
            if now - self._frame_age < self._last_track + self._last_track_move:
                return None
        elif now - self._last_track < self._track_interval:
            return None

        # Use pre-computed detections from main loop (avoids duplicate ONNX inference)
        faces = self._cached_faces or []
        objects = self._cached_objects or []

        # The person box, if any (largest = closest to the camera).
        person = None
        if objects:
            persons = [o for o in objects if o.get("class_name") == "person"]
            if persons:
                person = max(persons, key=lambda o:
                             o["bbox"]["width"] * o["bbox"]["height"])

        # Priority: a face that belongs to that person > the person box itself.
        # The face bbox is the more precise reference, but only while it is part
        # of the body being framed: a face box outside the person box is either
        # a false positive or somebody else, and alternating between two
        # references that disagree by most of a frame is what slammed the pan
        # between its limits (2026-09-25 07:57: face dx=-0.27 against person
        # dx=+0.45 within the same second). With no person box, the best face is
        # all there is.
        best_cx, best_cy, label = None, None, ""
        if faces:
            inside = [f for f in faces
                      if person is not None and self._face_within_person(f, person)]
            if inside:
                candidate = max(inside, key=lambda f: f.get("confidence", 0))
            elif person is None:
                candidate = max(faces, key=lambda f: f.get("confidence", 0))
            else:
                candidate = None
            if candidate is not None:
                b = candidate.get("bbox", {})
                if b:
                    best_cx = b.get("x", 0) + b.get("width", 0) / 2
                    best_cy = b.get("y", 0) + b.get("height", 0) / 2
                    label = "face"

        # No usable face — the person bbox gives the exact position.
        if best_cx is None and person is not None:
            best_cx = person.get("center_x", 0)
            best_cy = person.get("center_y", 0)
            label = "person"

        if best_cx is None:
            return None

        # Presence signal: a target is in view right now, so the session is
        # live and the anchor counts as "has life" for its stay tier.
        self._last_track_hit = now
        return best_cx, best_cy, label

    def _aim(self, now: float, target):
        """Frame the target: drive it back to the aim offset, no further.

        Hardware: p0=rightmost, p180=leftmost.
        Target on LEFT of frame → turn LEFT (pan increase → 180).
        Target on RIGHT of frame → turn RIGHT (pan decrease → 0).
        """
        best_cx, best_cy, label = target

        # Need frame dimensions for offset calc — use fixed camera resolution
        h, w = 480, 640
        cx, cy = w / 2, h / 2

        # Offset from centre (−1..+1, negative=left side of frame)
        dx = (best_cx - cx) / w
        dy = (best_cy - cy) / h

        step_x, self._framing_pan = self._framing_step(
            dx, self._framing_start_x, self._framing_aim_x, self._framing_pan)
        step_y, self._framing_tilt = self._framing_step(
            dy, self._framing_start_y, self._framing_aim_y, self._framing_tilt)

        # Proportional: distance from the aim point → angular delta via FOV,
        # scaled by the gain. Pan: dx negative = left side → pan INCREASE toward
        # 180 (hence -step_x). Tilt: dy positive = below centre → tilt INCREASE
        # toward 180 (look down). At gain 1.0 the delta is the whole excess, so
        # one update lands the target on the aim point.
        pan_delta = int(round(-step_x * self._cam_fov_h * self._framing_gain))
        tilt_delta = int(round(step_y * self._cam_fov_h * (h / w) * self._framing_gain))

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

        self._last_track = now
        # Pan and tilt are two servos: the move lasts as long as the longer one.
        self._last_track_move = max(abs(pan_delta), abs(tilt_delta)) * SERVO_SEC_PER_DEG
        logger.info("Framing %s: dx=%.2f dy=%.2f → pan%+d tilt%+d (pan=%d tilt=%d)",
                    label, dx, dy, pan_delta, tilt_delta,
                    self._servo_ptz.pan, current_tilt)

        # Attention span: framing hit
        self._attn_hit()

        # Framing owns both axes for TRACK_HOLD_SEC after this request, so
        # exploration cannot turn away mid-follow. The layer walks the
        # setpoints in rate-limited steps.
        self._motion.track(
            now,
            pan=self._servo_ptz.pan + pan_delta if pan_delta else None,
            tilt=self._servo_ptz.tilt + tilt_delta if tilt_delta else None,
        )

    @staticmethod
    def _face_within_person(face, person) -> bool:
        """Is this face's centre inside that person's box?

        The face is the more precise reference for framing, but only when it is
        part of the person being framed.
        """
        f, p = face.get("bbox") or {}, person.get("bbox") or {}
        if not f or not p:
            return False
        cx = f.get("x", 0) + f.get("width", 0) / 2
        cy = f.get("y", 0) + f.get("height", 0) / 2
        return (p.get("x", 0) <= cx <= p.get("x", 0) + p.get("width", 0)
                and p.get("y", 0) <= cy <= p.get("y", 0) + p.get("height", 0))

    @staticmethod
    def _framing_step(offset: float, start: float, aim: float,
                      following: bool):
        """Signed distance from the aim point for one axis, plus follow state.

        Nothing moves below the start offset, so small head or body movement
        leaves the camera still. Crossing it engages a follow, which then stays
        engaged — correcting on every update — until the target is back at the
        aim offset. The band between the two is the hysteresis: a target that
        drifts past the aim point but not past the start offset is left alone,
        so the camera does not hunt back and forth across one boundary.

        What comes back is the distance from the aim point — not from the
        centre, and not from the start offset. The gain scales it into a
        correction, so the two roles stay separate: the gain decides how hard
        the camera pushes, the aim offset decides where it stops pushing.
        """
        if abs(offset) > (aim if following else start):
            return math.copysign(abs(offset) - aim, offset), True
        return 0.0, False

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

    def _turn(self, direction: str, degrees: int, now: float):
        """Turn camera left or right by N degrees via servo pan.

        Exploration move: dropped while tracking holds the axes, and walked
        in rate-limited steps by the motion layer rather than issued as one
        long sweep.
        """
        if direction == 'left':
            delta = -abs(degrees)
        else:
            delta = abs(degrees)
        self._motion.explore_pan_by(delta, now)

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
