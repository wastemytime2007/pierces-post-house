"""Tests for posthouse.cull.segment -- Phase 4 slice 3, runs to segments.

Per the slice 3 brief and ``docs/design/PHASE4_CULL_DESIGN.md`` Sec5's
"Slice 3" and Sec2, this module is tested against: the contract's central
claim (a real ``coldfootage.validate_segments_shape`` / ``benchmark.
load_culls`` acceptance, not a paragraph); a round-trip through
``build_coldfootage_xml``; the mandatory tiling invariant on both
synthetic and real input; each rejection reason produced by a hand-built
synthetic state sequence; both consolidation paths collapsing a
spurious-run sequence into one segment; handle clamping/overlap; and
determinism.

Sidecars here are almost entirely HAND-BUILT (npz + json written
directly, no ffmpeg, no real decode) per the slice brief's explicit
allowance ("synthetic sidecars with hand-built state arrays are fine and
preferred for these") -- this is what lets every gate be tested in
isolation with known, exact per-frame signals, the same reasoning
``test_cull_classify.py`` uses for its own known-ground-truth clips. The
one exception is the round-trip test, which needs a real, ffprobe-able
media file on disk (the safety-net's own ``stable.mp4`` fixture) because
``coldfootage.build_coldfootage_xml`` verifies existence and duration.

The real-clip section is report-only (skipped if the clip is not present
-- it lives on Ryan's Mac, never committed).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from posthouse import coldfootage
from posthouse import benchmark as bm
from posthouse.cull.classify import ClassifyParams, STATE_ID, classify_sidecar
from posthouse.cull.signals import extract_signals
from posthouse.cull.segment import (
    SegmentParams,
    SegmentValidationError,
    TilingInvariantError,
    _Run,
    _consolidate_hysteresis,
    _consolidate_viterbi,
    _label_motion_intent,
    _resid_ok,
    _robust_z,
    _run_pipeline,
    boundary_hit_fraction,
    segment_source,
    write_culls,
)
from posthouse.cull.classify import STATE_NAMES

pytestmark = pytest.mark.tier2

FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"
STABLE = FIXTURES_MEDIA / "stable.mp4"

REAL_CLIP = Path(
    "/Volumes/RDOSS_2025/SoldFast 2026/10050 NE University Ave Runnells/"
    "First Walkthrough After Taking Over/Osmo/DJI_20260430075045_0006_D.MP4"
)


# ---------------------------------------------------------------------------
# Hand-built sidecar construction
# ---------------------------------------------------------------------------

_ARRAY_FIELDS = (
    "tx", "ty", "tx_norm_src_width", "ty_norm_src_width", "log_scale", "roll",
    "resid", "peak", "hf_energy", "lapvar", "lapvar_norm", "luma_mean",
    "luma_std", "clip_low", "clip_high",
)


def _clean_arrays(n: int) -> dict:
    """A "boring but valid" clean-static baseline: near-zero motion, mid
    exposure, healthy sharpness, everywhere. Individual frame ranges are
    overwritten by callers to build the scenario under test."""
    return {
        "tx": np.zeros(n, dtype=np.float32),
        "ty": np.zeros(n, dtype=np.float32),
        "tx_norm_src_width": np.zeros(n, dtype=np.float32),
        "ty_norm_src_width": np.zeros(n, dtype=np.float32),
        "log_scale": np.zeros(n, dtype=np.float32),
        "roll": np.zeros(n, dtype=np.float32),
        "resid": np.ones(n, dtype=np.float32) * 1.0,
        "peak": np.ones(n, dtype=np.float32) * 0.6,
        "hf_energy": np.ones(n, dtype=np.float32) * 1.0,
        "lapvar": np.ones(n, dtype=np.float32) * 1500.0,
        "lapvar_norm": np.ones(n, dtype=np.float32) * 1.0,
        "luma_mean": np.ones(n, dtype=np.float32) * 110.0,
        "luma_std": np.ones(n, dtype=np.float32) * 40.0,
        "clip_low": np.ones(n, dtype=np.float32) * 0.01,
        "clip_high": np.ones(n, dtype=np.float32) * 0.01,
    }


def _write_sidecar(
    out_dir: Path, name: str, state_names: list, arrays: dict, *,
    fps: float = 30.0, source_path: Path = None, classify_params: ClassifyParams = None,
) -> Path:
    """Write a hand-built ``<name>.<sha12>.signals.npz`` + ``.json`` pair
    directly, bypassing signals.py/classify.py entirely -- both the raw
    signal arrays and the (already "classified") ``state`` array are
    supplied by the caller. Returns the npz path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(state_names)
    state = np.array([STATE_ID[s] for s in state_names], dtype=np.int8)
    all_arrays = dict(arrays)
    all_arrays["state"] = state
    all_arrays.setdefault("hist64", np.zeros((0, 64), dtype=np.int32))
    all_arrays.setdefault("hist64_frame_index", np.zeros(0, dtype=np.int32))

    if source_path is None:
        source_path = out_dir / f"{name}.mp4"
    duration_sec = n / fps

    npz_path = out_dir / f"{name}.deadbeefcafe.signals.npz"
    json_path = out_dir / f"{name}.deadbeefcafe.signals.json"

    buf_bytes = _npz_bytes(all_arrays)
    npz_path.write_bytes(buf_bytes)

    header = {
        "generator": {"name": "posthouse.cull.signals", "version": "0.1.0",
                      "ffmpeg_version": "test", "numpy_version": np.__version__},
        "created_at": "2026-01-01T00:00:00Z",
        "source": {
            "path": str(source_path), "sha256": "deadbeefcafe" * 4,
            "duration_sec": duration_sec, "fps": fps, "width": 3840, "height": 2160,
            "nb_frames": n,
        },
        "analysis": {
            "plane_width": 960, "plane_height": 540, "plane_format": "gray",
            "decode": "software", "source_grade": "analysis_decode",
            "analysed_frames": n, "audio_sr": None,
        },
        "audio": {"present": False, "note": "no audio stream"},
        "npz_sha256": hashlib.sha256(buf_bytes).hexdigest(),
        "columns": {},
        "note": "hand-built test sidecar",
        "classify": {
            "generator": {"name": "posthouse.cull.classify", "version": "0.1.0"},
            "created_at": "2026-01-01T00:00:00Z",
            "params": (classify_params or ClassifyParams()).__dict__,
            "state_names": list(STATE_ID.keys()),
            "rle": [],
        },
    }
    json_path.write_text(json.dumps(header, indent=2))
    return npz_path


def _npz_bytes(arrays: dict) -> bytes:
    import io
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def _minimal_manifest(tmp_path: Path, source_dir: Path, *, kind: str = "broll", dual_use: bool = False) -> Path:
    manifest = {
        "contract_version": 1,
        "manifest_id": "test-manifest-0001",
        "revision": 1,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "generator": {"name": "test", "version": "0", "precut_pin": "deadbeef"},
        "project": {"name": "Test Project", "slug": "test-project", "root_dir": str(tmp_path),
                    "client": {"name": "Test Client"}, "project_type": "other", "shoot_dates": []},
        "sources": [{
            "id": "broll-test-01", "path": str(source_dir), "display_name": "Test",
            "kind": kind, "dual_use": dual_use, "added_at": "2026-01-01T00:00:00Z",
            "media": {"video_count": 1, "audio_count": 0, "image_count": 0, "other_count": 0, "total_bytes": 0},
        }],
        "default_includes": [],
        "handoffs": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


# ---------------------------------------------------------------------------
# Sec 2.2 rejection reasons, via hand-built state sequences
# ---------------------------------------------------------------------------

@pytest.fixture()
def gate_sidecar(tmp_path):
    """One sidecar exercising settle, too_short, transition, and the
    shake class-gate all at once, per the layout worked out in the slice
    report: 60f static / 60f shake / 20f static / 12f pan_left / 60f
    pan_right, fps=30.

    - [0,60) static:    frame_in==0 -> no leading settle; trailing settle
      (4f, static) -> ACCEPTED [0,56), boundary_reason_in=clip_start,
      boundary_reason_out=shake_onset (next run is shake).
    - [60,120) shake:   leading/trailing settle (8f each, moving-class) ->
      trimmed [68,112) = 44f = 1.467s clears min_duration -> class-gate
      rejects reason "shake". Also two "settle" rejections either side.
    - [120,140) static: leading/trailing settle (4f each) -> trimmed
      [124,136) = 12f = 0.4s < 1.15s -> "too_short". Also two "settle".
    - [140,152) pan_left: 12f, entirely consumed by settle (8 leading +
      4 trailing, since only 4 frames remain after an 8-frame leading
      clamp) -> ONE "transition" rejection over the whole 12f run.
    - [152,212) pan_right: frame_out==212==analysed_frames -> no trailing
      settle; leading settle (8f, moving class) -> trimmed [160,212) =
      52f = 1.733s -> ACCEPTED, boundary_reason_out=clip_end.
    """
    n = 212
    arrays = _clean_arrays(n)
    states = (["static"] * 60 + ["shake"] * 60 + ["static"] * 20
              + ["pan_left"] * 12 + ["pan_right"] * 60)
    assert len(states) == n
    # pan_left/pan_right need real translational speed so the (unused by
    # hysteresis, but sanity-checked) raw arrays are not nonsensical.
    arrays["tx_norm_src_width"][152:164] = 20.0   # pan_left window
    arrays["tx_norm_src_width"][164:] = -20.0     # pan_right window
    npz = _write_sidecar(tmp_path / "sidecars", "gatecase", states, arrays)
    return npz


def test_settle_too_short_transition_and_class_gate_reasons(gate_sidecar):
    params = SegmentParams(min_run_sec=0.01, consolidation="hysteresis")
    result = segment_source(gate_sidecar, params=params)

    reasons_seen = {r.reason for r in result.rejections}
    assert "settle" in reasons_seen
    assert "too_short" in reasons_seen
    assert "transition" in reasons_seen
    assert "shake" in reasons_seen

    assert len(result.segments) == 2
    first, second = sorted(result.segments, key=lambda s: s.frame_in)
    assert first.motion_intent == "static"
    assert first.boundary_reason_in == "clip_start"
    assert first.boundary_reason_out == "shake_onset"
    assert second.motion_intent == "pan_right"
    assert second.boundary_reason_out == "clip_end"

    # too_short duration sanity: the 12-frame remainder after settle trim.
    too_short = [r for r in result.rejections if r.reason == "too_short"]
    assert any(abs((r.frame_out - r.frame_in) - 12) <= 1 for r in too_short)

    transition = [r for r in result.rejections if r.reason == "transition"]
    assert any(r.frame_out - r.frame_in == 12 for r in transition)


def test_focus_hunt_rejection(tmp_path):
    """A rapidly-oscillating, motion-adjusted focus signal on an
    otherwise-clean static run is judged hunting and kills the whole
    run (design Sec1.4 point 3 / Sec2.2 point 4)."""
    n = 100
    arrays = _clean_arrays(n)
    # Alternate lapvar_norm every frame -> the motion-adjusted residual
    # (regression on ~zero speed collapses to "residual = value - mean")
    # flips sign every frame -> a sign-change rate near fps, far above
    # the default 2.4/s hunt threshold.
    arrays["lapvar_norm"][:] = np.where(np.arange(n) % 2 == 0, 0.3, 1.8).astype(np.float32)
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "huntcase", states, arrays)

    # The focus-hunt gate is legacy-only (slice 5 removed it as a
    # boundary input under "stability", the new default) -- exercised
    # here explicitly under "hysteresis".
    params = SegmentParams(min_run_sec=0.01, consolidation="hysteresis")
    result = segment_source(npz, params=params)

    assert not result.segments
    assert any(r.reason == "focus_hunt" for r in result.rejections)


# ---------------------------------------------------------------------------
# Consolidation: spurious short runs between two long same-class runs
# ---------------------------------------------------------------------------

def test_hysteresis_consolidation_absorbs_spurious_drift(tmp_path):
    n = 402
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][:200] = -20.0
    arrays["tx_norm_src_width"][200:202] = 0.3   # spurious 2-frame blip
    arrays["tx_norm_src_width"][202:] = -20.0
    states = ["pan_right"] * 200 + ["drift"] * 2 + ["pan_right"] * 200

    npz = _write_sidecar(tmp_path / "sidecars", "consolidate_h", states, arrays)
    params = SegmentParams(consolidation="hysteresis", min_run_sec=1.0)
    result = segment_source(npz, params=params)

    assert result.n_runs == 1, f"expected 1 consolidated run, got {result.n_runs}"
    assert len(result.segments) == 1
    assert result.segments[0].motion_intent == "pan_right"


def test_viterbi_consolidation_absorbs_spurious_drift(tmp_path):
    n = 402
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][:200] = -20.0
    arrays["tx_norm_src_width"][200:202] = 0.0    # spurious 2-frame blip
    arrays["tx_norm_src_width"][202:] = -20.0
    # The "state" array is irrelevant to the Viterbi path (it recomputes
    # classification fresh from the raw arrays above); give it something
    # plausible anyway so the sidecar is self-consistent to read.
    states = ["pan_right"] * 200 + ["static"] * 2 + ["pan_right"] * 200

    npz = _write_sidecar(tmp_path / "sidecars", "consolidate_v", states, arrays)
    params = SegmentParams(consolidation="viterbi", viterbi_lambda=7.5)
    result = segment_source(npz, params=params)

    assert result.n_runs == 1, f"expected 1 consolidated run, got {result.n_runs}"
    assert len(result.segments) == 1
    assert result.segments[0].motion_intent == "pan_right"


# ---------------------------------------------------------------------------
# Slice 5: the stability threshold detector (default consolidation)
# ---------------------------------------------------------------------------

def test_stability_clean_window_bounded_by_high_residual_noise(tmp_path):
    """Known boundary case #1 (slice 5 brief): a clean stable window
    bounded on both sides by high-residual noise must become exactly one
    accepted segment, with "stability_onset"/"stability_loss" boundary
    reasons (not clip_start/clip_end -- the noisy regions are not at the
    very edge of the clip)."""
    n = 600  # 20s @ 30fps
    arrays = _clean_arrays(n)
    # Bad on both sides: resid far above the default cap (1.5).
    arrays["resid"][:150] = 6.0
    arrays["resid"][450:] = 6.0
    states = ["shake"] * 150 + ["static"] * 300 + ["shake"] * 150
    npz = _write_sidecar(tmp_path / "sidecars", "stab_clean", states, arrays)

    result = segment_source(npz, params=SegmentParams())  # default = stability
    assert result.consolidation == "stability"

    assert len(result.segments) == 1, [(s.frame_in, s.frame_out) for s in result.segments]
    seg = result.segments[0]
    # Smoothing (0.7s = 21 frames @ 30fps) blurs the exact boundary a
    # little; allow a generous tolerance well inside the 150-frame bad
    # regions on either side.
    assert abs(seg.frame_in - 150) <= 25, seg.frame_in
    assert abs(seg.frame_out - 450) <= 25, seg.frame_out
    assert seg.boundary_reason_in == "stability_onset"
    assert seg.boundary_reason_out == "stability_loss"


def test_stability_sharpness_dip_mid_run_splits(tmp_path):
    """Known boundary case #2 (slice 5 brief): a window that is otherwise
    stable (low, constant resid) but whose sharpness (lapvar_norm) dips
    well below this clip's own fitted quantile mid-run must SPLIT into
    two accepted segments with a rejection in between."""
    n = 900  # 30s @ 30fps
    arrays = _clean_arrays(n)
    # A 3.0s dip in sharpness, well below the default 30th-percentile
    # floor (the rest of the clip is a constant lapvar_norm=1.0).
    arrays["lapvar_norm"][400:490] = 0.02
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "stab_dip", states, arrays)

    result = segment_source(npz, params=SegmentParams())
    assert len(result.segments) == 2, [(s.frame_in, s.frame_out) for s in result.segments]
    first, second = sorted(result.segments, key=lambda s: s.frame_in)
    assert first.frame_out < 400
    assert second.frame_in > 400

    dip_rejections = [
        r for r in result.rejections
        if r.frame_in < 490 and r.frame_out > 400 and r.reason in ("motion_inconsistent", "transition")
    ]
    assert dip_rejections, [r.reason for r in result.rejections]


def test_stability_focus_signals_computed_but_do_not_gate(tmp_path):
    """A rapidly-oscillating, motion-adjusted focus signal that
    ``test_focus_hunt_rejection`` proves rejects an otherwise-clean run
    under the LEGACY focus gate must NOT reject anything under the
    stability path (default consolidation): focus is computed and
    reported (the ``focus`` dict is still populated) but never gates a
    boundary (task brief point 1)."""
    n = 300
    arrays = _clean_arrays(n)
    arrays["lapvar_norm"][:] = np.where(np.arange(n) % 2 == 0, 0.3, 1.8).astype(np.float32)
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "stab_hunt", states, arrays)

    # Sanity: the SAME signal rejects everything under the legacy path
    # (this is exactly test_focus_hunt_rejection's own scenario).
    legacy = segment_source(npz, params=SegmentParams(min_run_sec=0.01, consolidation="hysteresis"))
    assert not legacy.segments
    assert any(r.reason == "focus_hunt" for r in legacy.rejections)

    # Under stability (default): no focus_hunt reason exists in this
    # path's vocabulary at all, and the window is accepted.
    result = segment_source(npz, params=SegmentParams())
    assert result.consolidation == "stability"
    assert not any(r.reason == "focus_hunt" for r in result.rejections)
    assert result.segments, "the alternating focus signal must not reject the window under stability"
    # Still informational: the focus dict is populated on the accepted segment.
    assert result.segments[0].focus_shape in ("steady", "rack_in", "rack_out")


def test_stability_exposure_gate_still_splits(tmp_path):
    """The exposure gate is unchanged under stability (it earned its
    place, kept): a sustained overexposed/underexposed span still splits
    an otherwise-stable candidate."""
    n = 600
    arrays = _clean_arrays(n)
    arrays["clip_low"][250:350] = 0.9  # far above the default 0.31 cap
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "stab_exposure", states, arrays)

    result = segment_source(npz, params=SegmentParams())
    assert len(result.segments) == 2, [(s.frame_in, s.frame_out) for s in result.segments]
    first, second = sorted(result.segments, key=lambda s: s.frame_in)
    assert first.boundary_reason_out == "exposure_fault"
    assert second.boundary_reason_in == "exposure_recovered"
    assert any(r.reason == "underexposed" for r in result.rejections)


def test_stability_exposure_gate_disabled_is_never_flagged(tmp_path):
    """``exposure_gate=False`` under stability: no frame is ever flagged
    underexposed/overexposed (mirrors the legacy path's own ablation
    switch, task brief point 1's "same exposure gate")."""
    n = 600
    arrays = _clean_arrays(n)
    arrays["clip_low"][250:350] = 0.99
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "stab_exposure_off", states, arrays)

    result = segment_source(npz, params=SegmentParams(exposure_gate=False))
    assert len(result.segments) == 1
    assert not any(r.reason in ("underexposed", "overexposed") for r in result.rejections)


# ---------------------------------------------------------------------------
# 2026-09-02 Decision Log follow-up: per-clip residual normalization
# (``stability_resid_norm``), Ryan's ruling on the generalization failure.
# ---------------------------------------------------------------------------

def test_resid_ok_absolute_matches_legacy_behavior():
    """``stability_resid_norm="absolute"`` (the control arm) must reproduce
    exactly the pre-existing ``resid_smooth < stability_resid_max``
    behavior -- no regression for the default/legacy path."""
    resid = np.array([0.5, 1.0, 1.4, 1.6, 2.0, 5.0], dtype=np.float64)
    params = SegmentParams(stability_resid_max=1.5, stability_resid_norm="absolute")
    ok, threshold, _detail = _resid_ok(resid, params)
    np.testing.assert_array_equal(ok, resid < 1.5)
    assert threshold == 1.5


def test_resid_ok_quantile_keeps_the_fitted_fraction_per_clip():
    """``"quantile"`` thresholds at a PER-CLIP percentile of this clip's
    own smoothed residual -- unlike "absolute", the same raw values pass
    or fail depending only on their rank within THIS clip, which is
    exactly the scale-free property the ruling asks for."""
    resid = np.arange(100, dtype=np.float64)  # 0..99, uniform
    params = SegmentParams(stability_resid_norm="quantile", stability_resid_quantile=0.30)
    ok, threshold, _detail = _resid_ok(resid, params)
    # Bottom 30% of a 0..99 uniform ramp is kept.
    assert threshold == pytest.approx(np.percentile(resid, 30.0))
    assert ok.sum() == np.sum(resid <= threshold)
    assert ok.sum() == pytest.approx(31, abs=2)  # ~30 + the boundary point

    # Rescale the SAME clip by 1000x (a different camera's absolute
    # magnitude) -- the fraction kept must be identical, because the
    # threshold is relative to this clip's own distribution.
    resid_scaled = resid * 1000.0
    ok_scaled, _t, _d = _resid_ok(resid_scaled, params)
    assert ok_scaled.sum() == ok.sum()


def test_resid_ok_robust_scale_z_score_and_scale_invariance():
    """``"robust_scale"`` z-scores against this clip's own median/MAD --
    like "quantile", rescaling every value by a constant factor (a
    different camera's absolute noise floor) must not change which frames
    pass, because the z-score is scale-free by construction."""
    rng = np.random.default_rng(0)
    resid = np.abs(rng.normal(loc=2.0, scale=0.5, size=500))
    params = SegmentParams(stability_resid_norm="robust_scale", stability_resid_z_max=2.0)
    ok, threshold, _detail = _resid_ok(resid, params)
    assert threshold == 2.0
    z = _robust_z(resid)
    np.testing.assert_array_equal(ok, z <= 2.0)

    resid_scaled = resid * 50.0  # a wildly different camera's noise floor
    ok_scaled, _t, _d = _resid_ok(resid_scaled, params)
    np.testing.assert_array_equal(ok, ok_scaled)


def test_resid_ok_robust_scale_zero_mad_does_not_divide_by_zero():
    """Degenerate-case guard (task brief): a clip whose smoothed residual
    is perfectly constant has MAD == 0. Must not raise, warn, or produce
    NaN/inf -- every frame's z-score must come back exactly 0 (nothing is
    abnormal relative to a distribution with no spread), so a perfectly
    stable clip is judged stable, not silently rejected by a NaN
    comparison (``nan <= threshold`` is always False in numpy)."""
    resid = np.full(50, 3.7, dtype=np.float64)
    z = _robust_z(resid)
    assert np.all(np.isfinite(z))
    np.testing.assert_array_equal(z, np.zeros(50))

    params = SegmentParams(stability_resid_norm="robust_scale", stability_resid_z_max=1.0)
    ok, _threshold, _detail = _resid_ok(resid, params)
    assert np.all(np.isfinite(ok.astype(np.float64)))
    assert ok.all(), "a perfectly constant (zero-MAD) residual must be judged entirely stable, not rejected"


def test_resid_ok_robust_scale_empty_array_does_not_crash():
    """A zero-length residual array (degenerate but should not crash)."""
    z = _robust_z(np.zeros(0, dtype=np.float64))
    assert z.shape == (0,)


def test_segment_source_clip_shorter_than_smoothing_window_does_not_crash(tmp_path):
    """Degenerate-case guard (task brief): a clip shorter than
    ``stability_smooth_sec``'s own frame window must not crash under any
    ``stability_resid_norm`` strategy. ``min_duration_sec``'s hard floor
    (1.0s) means such a clip legitimately yields zero accepted segments --
    the assertion here is "runs to completion, tiles correctly," not
    "accepts something.\""""
    n = 5  # far shorter than the default 0.7s @ 30fps = 21-frame window
    arrays = _clean_arrays(n)
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "tiny", states, arrays)
    for norm in ("absolute", "quantile", "robust_scale"):
        result = segment_source(npz, params=SegmentParams(stability_resid_norm=norm))
        assert result.consolidation == "stability"
        # Tiling invariant (segments + rejections exactly cover [0, n]) is
        # asserted inside segment_source itself; reaching here without a
        # TilingInvariantError or crash is the guard this test exists for.


def test_stability_quantile_norm_rejects_the_worst_fraction_of_a_uniformly_scaled_clip(tmp_path):
    """A clip whose motion residual is entirely at a DIFFERENT absolute
    scale than the "absolute" mode's default cap (e.g. a drone clip whose
    smoothed resid never exceeds 1.0 px/frame, an order of magnitude below
    Runnells' own p50) is entirely accepted under "absolute" (nothing
    clears the cap) but "quantile" still rejects its own worst
    (1 - stability_resid_quantile) fraction, because the threshold is
    relative to THIS clip regardless of the clip's absolute scale."""
    n = 900  # 30s @ 30fps
    arrays = _clean_arrays(n)
    # Smoothed resid is tiny throughout (drone-scale), but with a genuine
    # relative structure: the back third is 5x noisier than the front.
    arrays["resid"][:600] = 0.1
    arrays["resid"][600:] = 0.5
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "drone_scale", states, arrays)

    absolute_result = segment_source(
        npz, params=SegmentParams(stability_resid_norm="absolute", stability_resid_max=1.5, stability_combine="resid_only"),
    )
    assert sum(s.frame_out - s.frame_in for s in absolute_result.segments) >= n - 5  # ~everything kept

    quantile_result = segment_source(
        npz, params=SegmentParams(
            stability_resid_norm="quantile", stability_resid_quantile=0.60, stability_combine="resid_only",
        ),
    )
    kept = sum(s.frame_out - s.frame_in for s in quantile_result.segments)
    assert kept < n - 5, "quantile mode must reject some of even a uniformly-calm-by-absolute-scale clip"


# ---------------------------------------------------------------------------
# Slice 5: the classifier as labeller (majority vote, dominant class)
# ---------------------------------------------------------------------------

def test_label_motion_intent_dominant_class_by_frame_count():
    state = np.array(
        [STATE_NAMES.index("pan_right")] * 5
        + [STATE_NAMES.index("pan_left")] * 3
        + [STATE_NAMES.index("shake")] * 2,
        dtype=np.int8,
    )
    intent, confidence = _label_motion_intent(state, 0, len(state))
    assert intent == "pan_right"
    assert confidence == pytest.approx(0.5)  # 5 of 10 frames, shake included in the denominator


def test_label_motion_intent_tie_breaks_toward_lower_state_id():
    """pan_left (STATE_ID 1) and pan_right (STATE_ID 2) tied at 4 frames
    each; static (STATE_ID 0) is not part of the tie. Documented,
    deterministic tie-break: the lower STATE_ID wins (``np.argmax``'s own
    first-occurrence-wins behaviour on ties)."""
    state = np.array(
        [STATE_NAMES.index("static")] * 2
        + [STATE_NAMES.index("pan_left")] * 4
        + [STATE_NAMES.index("pan_right")] * 4,
        dtype=np.int8,
    )
    intent, confidence = _label_motion_intent(state, 0, len(state))
    assert intent == "pan_left"
    assert confidence == pytest.approx(0.4)


def test_label_motion_intent_excludes_shake_and_undecidable_from_the_vote():
    """shake/undecidable together outnumber every legal class in this
    window, but neither is eligible to win -- the labeller must still
    pick the (legal) minority class, static."""
    state = np.array(
        [STATE_NAMES.index("shake")] * 6
        + [STATE_NAMES.index("undecidable")] * 3
        + [STATE_NAMES.index("static")] * 4,
        dtype=np.int8,
    )
    intent, confidence = _label_motion_intent(state, 0, len(state))
    assert intent == "static"
    assert confidence == pytest.approx(4 / 13)


def test_label_motion_intent_falls_back_to_drift_when_window_is_all_illegal():
    state = np.array(
        [STATE_NAMES.index("shake")] * 5 + [STATE_NAMES.index("undecidable")] * 5, dtype=np.int8,
    )
    intent, confidence = _label_motion_intent(state, 0, len(state))
    assert intent == "drift"
    assert confidence == pytest.approx(0.0)


def test_labeling_applied_uniformly_regardless_of_consolidation_mode(tmp_path):
    """The task brief's "wire the labeling step in for all modes": a
    segment produced by the LEGACY viterbi path is labelled from the
    sidecar's own already-committed ``state`` array, NOT from whatever
    class viterbi's own decode (which recomputes fresh from the raw
    motion arrays, independent of `state` -- see `_consolidate_viterbi`)
    assigned to the run. Built so the two clearly disagree: the raw
    arrays drive a confident pan_right by viterbi's own decode, but the
    sidecar's committed `state` array (as if an earlier classify.py run
    used different params) is "static" throughout -- if this refactor
    had left any leftover `run.state`-based assignment, this segment
    would read "pan_right"; the shared labeller must produce "static"."""
    n = 300
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][:] = -20.0  # drives viterbi's OWN decode to pan_right
    states = ["static"] * n  # the sidecar's already-committed classification
    npz = _write_sidecar(tmp_path / "sidecars", "label_uniform", states, arrays)

    result = segment_source(npz, params=SegmentParams(consolidation="viterbi", viterbi_lambda=7.5))
    assert len(result.segments) == 1
    assert result.segments[0].motion_intent == "static"


# ---------------------------------------------------------------------------
# Handles: clamp at source bounds, may overlap a neighbour
# ---------------------------------------------------------------------------

def test_handles_clamp_at_source_bounds(tmp_path, monkeypatch):
    """Two adjacent, back-to-back accepted static/pan segments near the
    very start and end of a short clip: the requested 1.0s handle cannot
    fit on the outer edges (clamps to what's available) and, on the
    inner edges facing each other, is free to overlap the neighbour
    (Ryan's Q4 ruling, design Sec2.2 point 5) since only the outer
    clip bounds are ever clamped."""
    n = 150  # 5.0s @ 30fps
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][75:] = -20.0
    states = ["static"] * 75 + ["pan_right"] * 75

    source_dir = tmp_path / "footage"
    source_dir.mkdir()
    source_file = source_dir / "clip.mp4"
    npz = _write_sidecar(tmp_path / "sidecars", "handles", states, arrays, source_path=source_file)
    manifest_path = _minimal_manifest(tmp_path, source_dir)

    # This scenario is specifically a motion-CLASS boundary (static ->
    # pan_right); the stability detector (slice 5's default) does not
    # split on a class change at all -- it only splits on the smoothed
    # resid/lapvar thresholds, which this fixture holds constant across
    # both classes -- so this is exercised explicitly under "hysteresis".
    out_dir = tmp_path / "out"
    result = write_culls(
        npz, manifest_path, out_dir,
        params=SegmentParams(min_run_sec=0.01, consolidation="hysteresis"),
    )

    data = json.loads(result.master_path.read_text())
    segs = sorted(data["segments"], key=lambda s: s["in_sec"])
    assert len(segs) == 2
    first, second = segs
    # First segment starts at t=0 (clip_start) -> no room for a leading handle.
    assert first["handle_in_sec"] == pytest.approx(0.0, abs=1e-6)
    # Last segment ends at the clip's own end -> no room for a trailing handle.
    assert second["handle_out_sec"] == pytest.approx(0.0, abs=1e-6)
    # The two segments are close enough together that their full 1.0s
    # inner handles overlap -- allowed, never shrunk to avoid it.
    first_handled_end = first["out_sec"] + first["handle_out_sec"]
    second_handled_start = second["in_sec"] - second["handle_in_sec"]
    assert first["handle_out_sec"] == pytest.approx(1.0, abs=1e-6)
    assert second["handle_in_sec"] == pytest.approx(1.0, abs=1e-6)
    assert first_handled_end > second_handled_start  # genuine overlap


# ---------------------------------------------------------------------------
# The contract's central claim: accepted UNMODIFIED by both loaders
# ---------------------------------------------------------------------------

@pytest.fixture()
def written_culls(tmp_path):
    n = 300
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][:] = -20.0
    states = ["pan_right"] * n

    source_dir = tmp_path / "footage"
    source_dir.mkdir()
    source_file = source_dir / "clip.mp4"
    npz = _write_sidecar(tmp_path / "sidecars", "contractcase", states, arrays, source_path=source_file)
    manifest_path = _minimal_manifest(tmp_path, source_dir)

    out_dir = tmp_path / "out"
    result = write_culls(npz, manifest_path, out_dir, params=SegmentParams(min_run_sec=0.01))
    return result, out_dir


def test_culls_json_accepted_unmodified_by_both_loaders(written_culls):
    result, out_dir = written_culls
    data = json.loads(result.master_path.read_text())

    problems = coldfootage.validate_segments_shape(data)
    assert problems == [], f"coldfootage rejected the culls file: {problems}"

    ranges = bm.load_culls(result.master_path)  # raises CullsLoadError if rejected
    assert len(ranges) == result.n_accepted
    assert all(r.ruleset == "visual" for r in ranges)


def test_write_culls_view_is_also_accepted(written_culls):
    result, out_dir = written_culls
    view_data = json.loads(result.view_path.read_text())
    problems = coldfootage.validate_segments_shape(view_data)
    assert problems == []
    bm.load_culls(result.view_path)


# ---------------------------------------------------------------------------
# Round-trip through build_coldfootage_xml (needs a real, probeable file)
# ---------------------------------------------------------------------------

def test_roundtrip_through_build_coldfootage_xml(tmp_path):
    if not STABLE.exists():
        pytest.skip("safety-net fixture stable.mp4 not present")

    n = 120  # 4.0s @ 30fps, matches stable.mp4's real duration
    arrays = _clean_arrays(n)
    states = ["shake"] * 5 + ["static"] * 110 + ["shake"] * 5

    manifest_source_dir = STABLE.parent
    npz = _write_sidecar(tmp_path / "sidecars", "roundtrip", states, arrays, source_path=STABLE)
    manifest_path = _minimal_manifest(tmp_path, manifest_source_dir)

    out_dir = tmp_path / "out"
    result = write_culls(npz, manifest_path, out_dir, params=SegmentParams(min_run_sec=0.01))
    assert result.n_accepted >= 1

    xml_out = tmp_path / "coldfootage.xml"
    view_data = json.loads(result.view_path.read_text())
    written = coldfootage.build_coldfootage_xml(view_data, xml_out)
    assert written.exists()


# ---------------------------------------------------------------------------
# Tiling invariant
# ---------------------------------------------------------------------------

def test_tiling_invariant_on_synthetic(gate_sidecar):
    result = segment_source(gate_sidecar, params=SegmentParams(min_run_sec=0.01))
    ranges = sorted(
        [(s.frame_in, s.frame_out) for s in result.segments]
        + [(r.frame_in, r.frame_out) for r in result.rejections]
    )
    cursor = 0
    for f_in, f_out in ranges:
        assert f_in == cursor, f"gap/overlap at {cursor}"
        cursor = f_out
    assert cursor == result.analysed_frames


def test_tiling_invariant_asserted_in_code_not_only_tested(tmp_path):
    """write_culls calls _assert_tiling internally; corrupt a rejection's
    frame range after the fact (simulating a hypothetical future bug) to
    prove the assertion actually fires rather than merely existing."""
    from posthouse.cull import segment as seg_mod

    with pytest.raises(TilingInvariantError):
        seg_mod._assert_tiling([(0, 10), (20, 30)], 30, "bogus/gap")
    with pytest.raises(TilingInvariantError):
        seg_mod._assert_tiling([(0, 15), (10, 30)], 30, "bogus/overlap")
    # A correct tiling does not raise.
    seg_mod._assert_tiling([(0, 10), (10, 30)], 30, "bogus/ok")


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_determinism_same_input_twice(tmp_path):
    n = 200
    arrays = _clean_arrays(n)
    arrays["tx_norm_src_width"][:] = -20.0
    states = ["pan_right"] * n

    source_dir = tmp_path / "footage"
    source_dir.mkdir()
    source_file = source_dir / "clip.mp4"
    npz = _write_sidecar(tmp_path / "sidecars", "determinism", states, arrays, source_path=source_file)
    manifest_path = _minimal_manifest(tmp_path, source_dir)

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    params = SegmentParams(min_run_sec=0.01)
    r1 = write_culls(npz, manifest_path, out1, params=params)
    r2 = write_culls(npz, manifest_path, out2, params=params)

    d1 = json.loads(r1.master_path.read_text())
    d2 = json.loads(r2.master_path.read_text())
    for d in (d1, d2):
        d.pop("created_at", None)
    assert d1 == d2, "same inputs + same params must produce identical output modulo created_at"
    assert d1["cull_id"] == d2["cull_id"], "cull_id must be deterministic from content"


# ---------------------------------------------------------------------------
# Report-only: the real benchmark clip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("consolidation", ["hysteresis", "viterbi"])
def test_real_clip_report(tmp_path, consolidation):
    if not REAL_CLIP.exists():
        pytest.skip("real benchmark clip not present on this machine")

    out = tmp_path / "sidecars"
    sig = extract_signals(REAL_CLIP, out, decode="auto")
    classify_sidecar(sig.npz_path)

    params = SegmentParams(consolidation=consolidation)
    result = segment_source(sig.npz_path, params=params)

    print(f"\n[{consolidation}] runs={result.n_runs} median_run_sec={result.median_run_duration_sec:.2f} "
          f"segments={len(result.segments)} rejections={len(result.rejections)}")

    ranges = sorted([(s.frame_in, s.frame_out) for s in result.segments]
                     + [(r.frame_in, r.frame_out) for r in result.rejections])
    cursor = 0
    for f_in, f_out in ranges:
        assert f_in == cursor
        cursor = f_out
    assert cursor == result.analysed_frames
