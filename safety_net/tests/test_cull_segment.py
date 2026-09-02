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
    _run_pipeline,
    boundary_hit_fraction,
    segment_source,
    write_culls,
)

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

    params = SegmentParams(min_run_sec=0.01)
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

    out_dir = tmp_path / "out"
    result = write_culls(npz, manifest_path, out_dir, params=SegmentParams(min_run_sec=0.01))

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
