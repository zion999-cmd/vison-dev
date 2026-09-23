"""Observation-validity contract across the motion gate.

The FrameDiff motion gate skips ONNX detection on frames with no pixel
change. Such a frame carries no observation at all, which must not be
confused with an observation that found nothing — otherwise a person
sitting still is reported as gone.

These tests drive the production call order from runtime/main.py rather
than isolated setters:

    scene.update(people=..., objects=..., anchor_novelty=...)
    attention.score_events(scene.get())
    scene.update(intention=...)
    focus.update(events, scene_state, faces, objects)
"""
import sys

import numpy as np

sys.path.insert(0, '.')

from runtime.attention.engine import AttentionEngine
from runtime.focus.manager import FocusManager
from runtime.perception.frame_diff import FrameDiff
from runtime.presence.tracker import PresenceTracker
from runtime.scene.state import SceneState

FACE = [{"bbox": {"x": 280, "y": 180, "width": 80, "height": 80}, "confidence": 0.9}]
CUP = [{"class_name": "cup", "confidence": 0.7}]


def frame(scene, attention, focus, presence, faces, objects, detection_ran, anchor_novelty=None):
    """One main-loop iteration's perception path, in production order.

    detection_ran=False models a frame the motion gate skipped: main.py
    passes no observation for people/objects/novelty on those frames.
    """
    observed = detection_ran
    scene.update(
        people=faces if observed else None,
        motion_level=0.0,
        objects=objects if observed else None,
        voice_activity=False,
        anchor_novelty=anchor_novelty,
    )
    raw_state = scene.get()
    attention_ctx = dict(raw_state)
    attention_ctx["state_multiplier"] = scene.attention_multiplier
    scored_events = attention.score_events(attention_ctx)

    scene.update(intention="ambient")
    scene_state = scene.get()
    focus_info = focus.update(scored_events, scene_state, faces, objects)
    presence_info = presence.update(scene_state, focus_info, faces, objects)
    return scene_state, focus_info, presence_info


def build():
    # lost_timeout=0.0 makes the distinction sharp: any frame that is read
    # as "target gone" releases immediately, so survival cannot be an
    # artifact of the 2.5s window.
    return SceneState(), AttentionEngine(), FocusManager(lost_timeout=0.0), PresenceTracker()


# ── A. Static person: skipped detection must not release focus ──

def test_static_person_survives_skipped_detection_frames():
    scene, attention, focus, presence = build()
    state, info, _ = frame(scene, attention, focus, presence, FACE, CUP, True, anchor_novelty=0.5)
    assert info["has_focus"] and info["mode"] == "tracking"
    assert state["user_present"] is True

    for _ in range(12):  # 2.4s at 5 FPS — well past lost_timeout
        state, info, presence_info = frame(
            scene, attention, focus, presence, [], [], detection_ran=False)

    assert info["has_focus"], "skipping detection must not release focus"
    assert info["mode"] == "tracking"
    assert state["user_present"] is True, "a skipped frame is not a person leaving"
    assert state["runtime_state"] == "focus", "FOCUS must not decay to IDLE"
    assert presence._person_continuous > 0.0, "a skipped frame must not decay presence"


def test_skipped_detection_does_not_start_leave_debounce():
    scene, attention, focus, presence = build()
    frame(scene, attention, focus, presence, FACE, CUP, True)
    frame(scene, attention, focus, presence, [], [], detection_ran=False)
    assert scene._user_vanished_at == 0.0


# ── B. Real departure: an actual empty observation still works ──

def test_real_departure_is_still_detected():
    scene, attention, focus, presence = build()
    frame(scene, attention, focus, presence, FACE, CUP, True)
    assert scene.user_present is True

    # Detection RUNS and reports nothing — a genuine observation of zero.
    state, info, _ = frame(scene, attention, focus, presence, [], [], detection_ran=True)

    assert state["user_present"] is False, "an empty observation must update presence"
    assert not info["has_focus"], "an empty observation must release focus"
    assert scene._user_vanished_at > 0.0, "leave debounce must start"

    # …and the debounce still completes into IDLE.
    scene._user_vanished_at -= 999
    frame(scene, attention, focus, presence, [], [], detection_ran=True)
    assert scene.runtime_state_name == "idle"


def test_departure_completes_when_later_frames_are_gate_closed():
    """The frames after a departure are still, so they carry no observation.

    The leave debounce is time-based: a missing observation must not stall
    it, or FOCUS only ever leaves through the 30s state timeout and the
    user_left trigger never appears.
    """
    scene, attention, focus, presence = build()
    frame(scene, attention, focus, presence, FACE, CUP, True)
    # Departure frame: detection ran, found nobody.
    frame(scene, attention, focus, presence, [], [], detection_ran=True)
    assert scene._user_vanished_at > 0.0

    scene._user_vanished_at -= 999  # past STATE_DEBOUNCE_LEAVE
    for _ in range(5):              # every later frame is gate-closed
        frame(scene, attention, focus, presence, [], [], detection_ran=False)

    assert scene.runtime_state_name == "idle"
    assert any(t["trigger"] == "user_left" for t in scene._transition_events)


# ── C. Objects: skip preserves, valid empty clears ──

def test_skipped_detection_preserves_objects():
    scene, attention, focus, presence = build()
    frame(scene, attention, focus, presence, FACE, CUP, True)
    assert scene.get()["objects"] == CUP

    state, _, _ = frame(scene, attention, focus, presence, [], [], detection_ran=False)
    assert state["objects"] == CUP, "a skipped frame must not empty the desk"


def test_valid_empty_observation_clears_objects():
    scene, attention, focus, presence = build()
    frame(scene, attention, focus, presence, FACE, CUP, True)
    state, _, _ = frame(scene, attention, focus, presence, FACE, [], detection_ran=True)
    assert state["objects"] == []


# ── D. Anchor novelty: skip submits no observation ──

def test_skipped_frame_submits_no_novelty_observation():
    """None (not observed) must not reset the streak the way 0.0 does."""
    scene = SceneState()
    scene.update(anchor_novelty=0.5)   # streak = 1
    scene.update(anchor_novelty=None)  # skipped frame — streak must survive
    scene.update(anchor_novelty=0.5)   # streak still >= 1 → latch
    assert scene.get()["desk_changed"] is True


def test_explicit_zero_novelty_still_resets_the_streak():
    scene = SceneState()
    scene.update(anchor_novelty=0.5)
    scene.update(anchor_novelty=0.0)
    scene.update(anchor_novelty=0.5)
    assert scene.get()["desk_changed"] is False


# ── E. Long stillness: what actually recovers detection ──

def test_gate_alone_cannot_recover_from_sub_threshold_drift():
    """Why an observation needs a bound.

    FrameDiff only reopens on a change above threshold. A real departure
    produces one; slow drift never does, so the gate can stay closed
    indefinitely and the gate itself can never notice a slowly-departing
    person. Recovery is the observation-validity bound in main.py
    (observation_stale → forced re-detection), not the gate.
    """
    diff = FrameDiff()
    static = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert diff.changed(static)

    for _ in range(10):  # long stillness → gate stays closed
        assert not diff.changed(static.copy())

    departing = static.copy()
    departing[100:400, 150:450] = 200  # a person leaving: large region changes
    assert diff.changed(departing), "a real departure must reopen the gate"

    sub_threshold = FrameDiff()
    calm = np.full((480, 640, 3), 128, dtype=np.uint8)
    sub_threshold.changed(calm)
    drift = calm.copy()
    # A real change (72 grey levels, above FRAME_DIFF_THRESHOLD=25) confined
    # to 20x20 px → 100 sampled pixels, below FRAME_DIFF_MIN_PIXELS=500.
    drift[0:20, 0:20] = 200
    assert not sub_threshold.changed(drift), "small-but-real change never reopens the gate"
