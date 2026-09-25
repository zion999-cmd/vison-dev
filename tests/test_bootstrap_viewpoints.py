"""Bootstrap viewpoint layout and visual evidence.

The layout is derived from the PTZ's real travel limits and the camera's
horizontal FOV, with a documented overlap requirement — it is not the old
`_SWEEP_SEQUENCE` degree deltas (which were relative turns tuned for a timed
sweep, not coverage of anything).

The visual evidence is deliberately the cheapest thing that can support the
future question "does this view look materially different from the baseline?".
P0008.2 does not answer that question, and does not introduce a semantic model
to help: no YOLO, no VLM, no embedding network.
"""
import sys

import numpy as np

sys.path.insert(0, '.')

from runtime.environment.viewpoint import (ViewSignature, bootstrap_viewpoints,
                                           material_difference)
from runtime.perception.servo_ptz import PAN_MAX, PAN_MIN, TILT_MIN

FOV_H = 55.0            # the same documented FOV the framing loop uses


# ── Layout ──

def test_the_layout_spans_the_usable_pan_travel():
    views = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, FOV_H)
    pans = [v.pan for v in views]

    assert pans[0] == PAN_MIN and pans[-1] == PAN_MAX, \
        "the bootstrap must cover the PTZ's whole usable travel, not a subset"
    assert pans == sorted(pans), "the survey must be deterministic and monotonic"
    assert all(PAN_MIN <= p <= PAN_MAX for p in pans)
    assert all(v.tilt == TILT_MIN for v in views), \
        "v1 surveys the level horizon row only"


def test_the_layout_is_deterministic():
    a = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, FOV_H)
    b = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, FOV_H)
    assert [v.pose for v in a] == [v.pose for v in b]


def test_the_layout_keeps_neighbouring_views_overlapping():
    """Centres are at most fov*(1-overlap) apart, so adjacent views share field."""
    views = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, FOV_H, overlap=0.30)
    pans = [v.pan for v in views]
    gaps = [b - a for a, b in zip(pans, pans[1:])]

    assert max(gaps) <= FOV_H * 0.70 + 1.0, \
        f"gap {max(gaps)}° exceeds the overlap requirement at FOV {FOV_H}°"


def test_a_wider_field_needs_fewer_viewpoints():
    narrow = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, 30.0)
    wide = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, 90.0)
    assert len(wide) < len(narrow), "coverage count must follow the field of view"


def test_each_view_has_a_stable_identity():
    views = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, FOV_H)
    keys = [v.key for v in views]
    assert len(set(keys)) == len(keys), "two viewpoints must not share an identity"
    assert [v.index for v in views] == list(range(len(views)))


# ── Visual evidence ──

def frame(bgr):
    return np.full((480, 640, 3), bgr, dtype=np.uint8)


def noisy(bgr, sigma=3):
    rng = np.random.default_rng(0)
    base = np.full((480, 640, 3), bgr, dtype=np.float32)
    return np.clip(base + rng.normal(0, sigma, base.shape), 0, 255).astype(np.uint8)


def test_the_same_view_has_zero_distance():
    sig = ViewSignature.from_frame(frame((40, 40, 40)))
    assert sig.distance(sig) == 0.0


def test_a_retaken_view_stays_close():
    """Sensor noise and small motion must not read as a different room."""
    a = ViewSignature.from_frame(frame((40, 40, 40)))
    b = ViewSignature.from_frame(noisy((40, 40, 40)))
    assert a.distance(b) < material_difference()


def test_a_different_view_reads_as_different():
    dark = ViewSignature.from_frame(frame((30, 30, 30)))
    bright = ViewSignature.from_frame(frame((220, 220, 220)))
    assert dark.distance(bright) > material_difference()


def test_the_signature_is_a_plain_vector():
    """The contract must survive being replaced by a stronger descriptor, and be
    inspectable without the object — so it is a list of floats, not a model."""
    sig = ViewSignature.from_frame(frame((40, 40, 40)))
    assert isinstance(sig.values, list)
    assert sig.values and all(isinstance(v, float) for v in sig.values)
    assert all(0.0 <= v <= 1.0 for v in sig.values)


def test_a_signature_round_trips_through_its_plain_form():
    sig = ViewSignature.from_frame(frame((60, 120, 180)))
    again = ViewSignature(sig.values)
    assert again.distance(sig) == 0.0


def test_a_two_stop_layout_still_spans_the_travel():
    """A lens wide enough to need only two stops must still get both ends, not
    one pose in the middle."""
    views = bootstrap_viewpoints(PAN_MIN, PAN_MAX, TILT_MIN, 250.0)
    assert len(views) == 2
    assert [v.pan for v in views] == [PAN_MIN, PAN_MAX]
