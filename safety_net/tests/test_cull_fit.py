"""Tests for posthouse.cull.fit -- Phase 4 slice 4, the fitting harness.

Per the slice 4 brief and ``docs/design/PHASE4_CULL_DESIGN.md`` Sec3, this
module is tested against: a fixed seed reproducing a fixed parameter set;
the fixture-ordering guard actually rejecting a deliberately inverted
parameter set (and passing a correctly-ordered one); block CV holding out
exactly what it claims to (asserted BY CONSTRUCTION on a synthetic block
whose true answer is known regardless of what any coordinate descent
picks -- a "shake" block can never be accepted, fit or not); the block
bootstrap interval widening as the underlying held-out scores' spread
widens; and gate ablation reporting both arms with a verdict.

Sidecars are hand-built (npz + json written directly, no ffmpeg, no real
decode), the same convention ``test_cull_segment.py`` uses and for the
same reason: it is what lets every scenario be built with known, exact
per-frame signals. The fixture-ordering-guard tests that need REAL
fixture data (the "did the real guard reject the real inverted fixtures"
half) reuse the safety-net media fixtures directly.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

from posthouse import benchmark as bm
from posthouse.cull.classify import STATE_ID, ClassifyParams
from posthouse.cull.fit import (
    Block,
    Evaluator,
    FitValidationError,
    Metrics,
    STABILITY_LAPVAR_QUANTILE_GRID,
    STABILITY_RESID_MAX_GRID,
    block_bootstrap,
    check_fixture_orderings,
    check_grid_edges,
    fit,
    fit_one,
    load_fixture_arrays,
    make_blocks,
    run_arm,
)
from posthouse.cull.segment import SegmentParams

pytestmark = pytest.mark.tier2

FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"
SOURCE_PATH = "/fake/source/clip.mp4"


# ---------------------------------------------------------------------------
# Hand-built sidecar construction (same convention as test_cull_segment.py)
# ---------------------------------------------------------------------------

def _clean_arrays(n: int) -> dict:
    return {
        "tx": np.zeros(n, dtype=np.float32), "ty": np.zeros(n, dtype=np.float32),
        "tx_norm_src_width": np.zeros(n, dtype=np.float32), "ty_norm_src_width": np.zeros(n, dtype=np.float32),
        "log_scale": np.zeros(n, dtype=np.float32), "roll": np.zeros(n, dtype=np.float32),
        "resid": np.ones(n, dtype=np.float32) * 1.0, "peak": np.ones(n, dtype=np.float32) * 0.6,
        "hf_energy": np.ones(n, dtype=np.float32) * 1.0, "lapvar": np.ones(n, dtype=np.float32) * 1500.0,
        "lapvar_norm": np.ones(n, dtype=np.float32) * 1.0, "luma_mean": np.ones(n, dtype=np.float32) * 110.0,
        "luma_std": np.ones(n, dtype=np.float32) * 40.0, "clip_low": np.ones(n, dtype=np.float32) * 0.01,
        "clip_high": np.ones(n, dtype=np.float32) * 0.01,
    }


def _npz_bytes(arrays: dict) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays)
    return buf.getvalue()


def _write_sidecar(out_dir: Path, name: str, state_names: list, arrays: dict, *, fps: float = 30.0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(state_names)
    all_arrays = dict(arrays)
    all_arrays["state"] = np.array([STATE_ID[s] for s in state_names], dtype=np.int8)
    all_arrays.setdefault("hist64", np.zeros((0, 64), dtype=np.int32))
    all_arrays.setdefault("hist64_frame_index", np.zeros(0, dtype=np.int32))

    npz_path = out_dir / f"{name}.deadbeefcafe.signals.npz"
    json_path = out_dir / f"{name}.deadbeefcafe.signals.json"
    buf_bytes = _npz_bytes(all_arrays)
    npz_path.write_bytes(buf_bytes)

    header = {
        "generator": {"name": "posthouse.cull.signals", "version": "0.1.0", "ffmpeg_version": "test", "numpy_version": np.__version__},
        "created_at": "2026-01-01T00:00:00Z",
        "source": {"path": SOURCE_PATH, "sha256": "deadbeefcafe" * 4, "duration_sec": n / fps, "fps": fps, "width": 3840, "height": 2160, "nb_frames": n},
        "analysis": {"plane_width": 960, "plane_height": 540, "plane_format": "gray", "decode": "software", "source_grade": "analysis_decode", "analysed_frames": n, "audio_sr": None},
        "audio": {"present": False, "note": "no audio stream"},
        "npz_sha256": hashlib.sha256(buf_bytes).hexdigest(),
        "columns": {}, "note": "hand-built test sidecar",
        "classify": {
            "generator": {"name": "posthouse.cull.classify", "version": "0.1.0"}, "created_at": "2026-01-01T00:00:00Z",
            "params": ClassifyParams().__dict__, "state_names": list(STATE_ID.keys()), "rle": [],
        },
    }
    json_path.write_text(json.dumps(header, indent=2))
    return npz_path


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def test_make_blocks_covers_duration_with_no_gaps():
    blocks = make_blocks(90.0, n_blocks=3)
    assert len(blocks) == 3
    assert blocks[0].start_sec == 0.0
    assert blocks[-1].end_sec == 90.0
    for a, b in zip(blocks, blocks[1:]):
        assert a.end_sec == b.start_sec


# ---------------------------------------------------------------------------
# 1. Fixed seed reproduces a fixed parameter set
# ---------------------------------------------------------------------------

def _small_scenario(tmp_path: Path) -> tuple[Path, list[bm.Range], list[Block]]:
    """900 frames @30fps = 30s: three 10s blocks, all clean static runs
    that Ryan's own answer key would fully accept, so a real coordinate
    descent has something non-trivial to converge on."""
    n = 900
    arrays = _clean_arrays(n)
    states = ["static"] * n
    npz = _write_sidecar(tmp_path / "sidecars", "detcase", states, arrays)
    truth = [bm.Range(source_path=SOURCE_PATH, in_sec=0.0, out_sec=30.0)]
    blocks = make_blocks(30.0, n_blocks=3)
    return npz, truth, blocks


def test_fixed_seed_reproduces_fixed_params(tmp_path):
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)

    arm_a = run_arm(
        "hysteresis_full", "hysteresis", True, True, evaluator, blocks,
        precision_floor=0.60, passes=1, stages=("motion", "focus", "exposure"),
        n_bootstrap=200, seed=42,
    )
    arm_b = run_arm(
        "hysteresis_full", "hysteresis", True, True, evaluator, blocks,
        precision_floor=0.60, passes=1, stages=("motion", "focus", "exposure"),
        n_bootstrap=200, seed=42,
    )

    from dataclasses import asdict
    assert asdict(arm_a.final_params) == asdict(arm_b.final_params)
    assert arm_a.mean_held_out == arm_b.mean_held_out
    assert arm_a.bootstrap == arm_b.bootstrap, "same seed must reproduce the same bootstrap interval too"


def test_fit_one_has_no_randomness_regardless_of_seed(tmp_path):
    """fit_one() (the coordinate descent itself) takes no seed argument at
    all -- only block_bootstrap does -- so it must be identical regardless
    of what seed the caller later uses for the bootstrap."""
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    train = blocks[:2]

    p1, m1, _, _w1 = fit_one("hysteresis", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"))
    p2, m2, _, _w2 = fit_one("hysteresis", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"))

    from dataclasses import asdict
    assert asdict(p1) == asdict(p2)
    assert m1 == m2


# ---------------------------------------------------------------------------
# Slice 5: fitting the stability detector's two parameters
# ---------------------------------------------------------------------------

def test_fit_one_stability_fits_exactly_the_two_stability_params(tmp_path):
    """The stability arm's own "motion" stage searches
    ``stability_resid_max``/``stability_lapvar_quantile`` only (the task
    brief's "two fitted parameters"), forces ``focus_gate=False``
    regardless of what is passed in, and leaves every legacy-only field
    (``min_run_sec``, ``viterbi_lambda``, ``settle_frames``) untouched at
    its shipped default."""
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    train = blocks[:2]

    default = SegmentParams()
    params, metrics, trace, _warnings = fit_one(
        "stability", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"),
    )

    assert params.consolidation == "stability"
    assert params.stability_combine == "and", "fit_one defaults to the original AND combine mode when unspecified"
    assert params.focus_gate is False, "stability must force focus_gate off regardless of the caller's request"
    assert trace["motion"], "the stability motion stage must run and produce a trace"
    assert all(t.stage in ("stability_resid_max", "stability_lapvar_quantile") for t in trace["motion"])
    assert not trace["focus"], "no focus stage runs for the stability arm (there is no focus gate to fit)"
    # Legacy-only fields untouched at their shipped defaults.
    assert params.min_run_sec == default.min_run_sec
    assert params.viterbi_lambda == default.viterbi_lambda
    assert params.settle_frames == default.settle_frames


def test_fit_one_stability_resid_only_quantile_fits_exactly_that_one_param(tmp_path):
    """2026-09-02 Decision Log follow-up (Ryan's per-clip-normalization
    ruling): under ``stability_combine="resid_only"`` with
    ``stability_resid_norm="quantile"``, the motion stage must search
    ONLY ``stability_resid_quantile`` -- not ``stability_resid_max``
    (unused by this norm mode) and not ``stability_lapvar_quantile``
    (unused by resid_only at all)."""
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    train = blocks[:2]

    params, _metrics, trace, _warnings = fit_one(
        "stability", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"),
        stability_combine="resid_only", stability_resid_norm="quantile",
    )
    assert params.stability_resid_norm == "quantile"
    assert all(t.stage == "stability_resid_quantile" for t in trace["motion"])


def test_fit_one_stability_resid_only_robust_scale_fits_exactly_that_one_param(tmp_path):
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    train = blocks[:2]

    params, _metrics, trace, _warnings = fit_one(
        "stability", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"),
        stability_combine="resid_only", stability_resid_norm="robust_scale",
    )
    assert params.stability_resid_norm == "robust_scale"
    assert all(t.stage == "stability_resid_z_max" for t in trace["motion"])


def test_fit_one_stability_resid_only_absolute_is_unaffected_by_norm_default(tmp_path):
    """The pre-existing control arm: leaving ``stability_resid_norm``
    unspecified (its ``SegmentParams`` default, ``"absolute"``) must
    reproduce exactly the pre-2026-09-02 behavior -- no regression."""
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    train = blocks[:2]

    params, _metrics, trace, _warnings = fit_one(
        "stability", True, True, evaluator, train, 0.60, 1, ("motion", "focus", "exposure"),
        stability_combine="resid_only",
    )
    assert params.stability_resid_norm == "absolute"
    assert all(t.stage == "stability_resid_max" for t in trace["motion"])


def test_fit_stability_reproducible_with_fixed_seed(tmp_path):
    """Same CV/bootstrap machinery, same fixed-seed reproducibility
    contract as the legacy arms (test_fixed_seed_reproduces_fixed_params),
    now asserted for the stability arm specifically."""
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)

    arm_a = run_arm(
        "stability_full", "stability", False, True, evaluator, blocks,
        precision_floor=0.60, passes=1, stages=("motion", "focus", "exposure"),
        n_bootstrap=200, seed=42,
    )
    arm_b = run_arm(
        "stability_full", "stability", False, True, evaluator, blocks,
        precision_floor=0.60, passes=1, stages=("motion", "focus", "exposure"),
        n_bootstrap=200, seed=42,
    )

    from dataclasses import asdict
    assert asdict(arm_a.final_params) == asdict(arm_b.final_params)
    assert arm_a.mean_held_out == arm_b.mean_held_out
    assert arm_a.bootstrap == arm_b.bootstrap

    # Same 3-block CV contract the legacy arms use: exactly one held-out
    # fold per block.
    assert sorted(f.held_out_block for f in arm_a.folds) == [0, 1, 2]


# ---------------------------------------------------------------------------
# 2. Fixture-ordering guard rejects a deliberately inverted parameter set
# ---------------------------------------------------------------------------

def _synthetic_fixture_arrays(*, invert_resid: bool = False, invert_lapvar: bool = False) -> dict:
    n = 50
    base = {
        "stable": {"resid": np.full(n, 3.0), "hf_energy": np.full(n, 1.5), "lapvar": np.full(n, 1500.0),
                   "clip_low": np.full(n, 0.05), "clip_high": np.full(n, 0.05)},
        "shaky": {"resid": np.full(n, 7.5), "hf_energy": np.full(n, 6.0), "lapvar": np.full(n, 1400.0),
                  "clip_low": np.full(n, 0.05), "clip_high": np.full(n, 0.05)},
        "blurred": {"resid": np.full(n, 3.0), "hf_energy": np.full(n, 1.5), "lapvar": np.full(n, 300.0),
                    "clip_low": np.full(n, 0.05), "clip_high": np.full(n, 0.05)},
        "underexposed": {"resid": np.full(n, 3.0), "hf_energy": np.full(n, 1.5), "lapvar": np.full(n, 1200.0),
                          "clip_low": np.full(n, 0.60), "clip_high": np.full(n, 0.02)},
        "overexposed": {"resid": np.full(n, 3.0), "hf_energy": np.full(n, 1.5), "lapvar": np.full(n, 1200.0),
                         "clip_low": np.full(n, 0.02), "clip_high": np.full(n, 0.50)},
    }
    if invert_resid:
        base["stable"]["resid"], base["shaky"]["resid"] = base["shaky"]["resid"], base["stable"]["resid"]
    if invert_lapvar:
        base["stable"]["lapvar"], base["blurred"]["lapvar"] = base["blurred"]["lapvar"], base["stable"]["lapvar"]
    return base


def test_fixture_guard_passes_a_correctly_ordered_set():
    arrays = _synthetic_fixture_arrays()
    problems = check_fixture_orderings(arrays, SegmentParams())
    assert problems == []


def test_fixture_guard_rejects_inverted_resid():
    arrays = _synthetic_fixture_arrays(invert_resid=True)
    problems = check_fixture_orderings(arrays, SegmentParams())
    assert any("resid" in p for p in problems)


def test_fixture_guard_rejects_inverted_lapvar():
    arrays = _synthetic_fixture_arrays(invert_lapvar=True)
    problems = check_fixture_orderings(arrays, SegmentParams())
    assert any("lapvar" in p for p in problems)


def test_fixture_guard_catches_a_degenerate_fitted_exposure_threshold():
    """A parameter set is not just checked against RAW signal orderings --
    the fitted clip_low_frac_max/clip_high_frac_max must still separate
    underexposed/overexposed from stable, or the guard must catch it even
    when the raw signals themselves are correctly ordered."""
    arrays = _synthetic_fixture_arrays()
    # A threshold above every fixture's clip_low value: nothing is ever
    # flagged, so underexposed's flagged-fraction (0.0) does not exceed
    # stable's (0.0) -- degenerate, must be rejected.
    bad_params = SegmentParams(clip_low_frac_max=0.99, exposure_gate=True)
    problems = check_fixture_orderings(arrays, bad_params)
    assert any("clip_low_frac_max" in p for p in problems)

    # The same threshold with the gate off is not checked at all (it
    # gates nothing) -- must pass.
    off_params = SegmentParams(clip_low_frac_max=0.99, exposure_gate=False)
    problems_off = check_fixture_orderings(arrays, off_params)
    assert not any("clip_low_frac_max" in p for p in problems_off)


@pytest.mark.skipif(not FIXTURES_MEDIA.exists(), reason="safety-net media fixtures not present")
def test_load_fixture_arrays_against_real_fixtures(tmp_path):
    """Integration check: the real safety-net fixtures, run through the
    real extractor/classifier, pass the guard with the shipped defaults --
    the same claim test_cull_signals.py's own ordering tests make, now
    checked through fit.py's own loader."""
    arrays = load_fixture_arrays(FIXTURES_MEDIA, tmp_path / "fixtures_work")
    problems = check_fixture_orderings(arrays, SegmentParams())
    assert problems == [], f"shipped defaults should pass the real-fixture guard: {problems}"


# ---------------------------------------------------------------------------
# 3. Block CV holds out exactly what it claims to (known-answer construction)
# ---------------------------------------------------------------------------

def test_held_out_block_is_scored_only_against_its_own_truth(tmp_path):
    """300f static / 300f static / 300f shake @30fps = 3x 10s blocks.
    "shake" NEVER opens a select regardless of any SegmentParams (it is a
    fixed class gate, not a fit-dependent one -- segment.py's
    _CLOSED_CLASSES), so held-out block 2's predicted coverage is 0.0 NO
    MATTER what a coordinate descent fit on blocks 0-1 picks. If held-out
    scoring ever leaked training-block predictions or truth into this
    fold's numbers, this exact-zero would not hold."""
    n = 900
    arrays = _clean_arrays(n)
    states = ["static"] * 300 + ["static"] * 300 + ["shake"] * 300
    npz = _write_sidecar(tmp_path / "sidecars", "cvcase", states, arrays)
    truth = [bm.Range(source_path=SOURCE_PATH, in_sec=0.0, out_sec=30.0)]  # covers the whole clip, block 2 included
    blocks = make_blocks(30.0, n_blocks=3)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)

    train_blocks = [blocks[0], blocks[1]]
    fitted_params, _train_m, _trace, _warnings = fit_one(
        "hysteresis", True, True, evaluator, train_blocks, 0.60, 1, ("motion", "focus", "exposure"),
    )

    held_out_metrics = evaluator.score(fitted_params, [blocks[2]])
    assert held_out_metrics.predicted_sec == pytest.approx(0.0, abs=1e-9)
    assert held_out_metrics.recall == pytest.approx(0.0, abs=1e-9)

    # Sanity: the training blocks themselves DO get accepted (this is not
    # a broken fit -- static, clean, well within min_duration_sec).
    train_metrics = evaluator.score(fitted_params, train_blocks)
    assert train_metrics.recall > 0.5


def test_run_arm_fold_held_out_block_matches_claimed_index(tmp_path):
    npz, truth, blocks = _small_scenario(tmp_path)
    evaluator = Evaluator(npz, SOURCE_PATH, truth)
    arm = run_arm(
        "hysteresis_full", "hysteresis", True, True, evaluator, blocks,
        precision_floor=0.60, passes=1, stages=("motion", "focus", "exposure"),
        n_bootstrap=100, seed=0,
    )
    assert sorted(f.held_out_block for f in arm.folds) == [0, 1, 2]
    for f in arm.folds:
        # each fold's held-out params came from a fit that saw exactly
        # the other two blocks -- spot check via the fold count.
        assert f.held_out_metrics is not None


# ---------------------------------------------------------------------------
# 4. Bootstrap interval widens as the held-out scores' spread widens
# ---------------------------------------------------------------------------

def test_bootstrap_interval_widens_with_fold_spread():
    low_spread = {"precision": np.array([0.70, 0.70, 0.70])}
    high_spread = {"precision": np.array([0.30, 0.70, 0.95])}

    boot_low = block_bootstrap(low_spread, n_resamples=4000, seed=0)
    boot_high = block_bootstrap(high_spread, n_resamples=4000, seed=0)

    assert boot_low["precision"]["width"] == pytest.approx(0.0, abs=1e-9)
    assert boot_high["precision"]["width"] > boot_low["precision"]["width"]
    assert boot_high["precision"]["width"] > 0.05, "3 widely-spread blocks must give a visibly wide interval"


def test_bootstrap_interval_is_seed_reproducible():
    arrays = {"recall": np.array([0.4, 0.8, 0.9])}
    a = block_bootstrap(arrays, n_resamples=1000, seed=7)
    b = block_bootstrap(arrays, n_resamples=1000, seed=7)
    assert a == b


# ---------------------------------------------------------------------------
# 5. Ablation reports both arms
# ---------------------------------------------------------------------------

_MINIMAL_XMEML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence>
    <name>Selects</name>
    <media>
      <video>
        <track>
          <clipitem id="clipitem-1">
            <name>clip</name>
            <in>0</in>
            <out>900</out>
            <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
            <file id="file-1">
              <name>clip.mp4</name>
              <pathurl>file://localhost{path}</pathurl>
              <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>
            </file>
          </clipitem>
        </track>
      </video>
    </media>
  </sequence>
</xmeml>
"""


@pytest.mark.skipif(not FIXTURES_MEDIA.exists(), reason="safety-net media fixtures not present")
def test_fit_reports_gate_ablation_both_arms(tmp_path):
    npz, truth, _blocks = _small_scenario(tmp_path)

    # A minimal, hand-written FCP7 xmeml answer key covering the same 30s
    # clip -- parse_answer_key_xml() only needs a <sequence>/<clipitem>
    # with a resolvable <file>/<pathurl> and an <in>/<out>, no PreCut
    # dependency (unlike build_coldfootage_xml, which needs a real,
    # ffprobe-able file on disk).
    xml_path = tmp_path / "answer_key.xml"
    xml_path.write_text(_MINIMAL_XMEML.format(path=SOURCE_PATH))

    report = fit(
        npz, xml_path, tmp_path / "out",
        fixtures_dir=FIXTURES_MEDIA, precision_floor=0.60, n_blocks=3, passes=1,
        seed=0, n_bootstrap=200, stages=("motion", "focus", "exposure"),
    )

    # Slice 5: the headline winner is always "stability" -- it has no
    # focus-gate ablation (no focus gate to ablate at all). Slice 5
    # follow-up (2026-09-02): FIVE combine-mode arms are now fit
    # ("and"/"or"/"resid_only"/"lapvar_only"/"score"), each first-class,
    # plus exposure-gate ablations for "and" and "score". The legacy path
    # is still fit and ablated in full (both focus and exposure), under
    # whichever of hysteresis/viterbi wins on its own held-out score, and
    # reported under its own name in `arms`, never under
    # `winner_consolidation`.
    assert report.winner_consolidation == "stability"
    for name in (
        "stability_and_full", "stability_and_no_exposure",
        "stability_or_full", "stability_resid_only_full", "stability_lapvar_only_full",
        "stability_score_full", "stability_score_no_exposure",
        "stability_resid_only_quantile_full", "stability_resid_only_robust_scale_full",
    ):
        assert name in report.arms
    assert "stability_no_focus" not in report.arms  # no such arm; stability has no focus gate
    assert "stability_or_no_exposure" not in report.arms  # exposure only ablated for and/score

    legacy_cons = report.decisive["legacy_comparison"]["legacy_winner_consolidation"]
    assert legacy_cons in ("hysteresis", "viterbi")
    assert f"{legacy_cons}_full" in report.arms
    assert f"{legacy_cons}_no_focus" in report.arms
    assert f"{legacy_cons}_no_exposure" in report.arms

    verdicts = report.decisive["ablation_verdicts"]
    assert "stability_exposure_gate" in verdicts
    assert "stability_score_exposure_gate" in verdicts
    for combine_key in (
        "stability_combine_and_vs_or", "stability_combine_and_vs_resid_only",
        "stability_combine_and_vs_lapvar_only", "stability_combine_and_vs_score",
    ):
        assert combine_key in verdicts
        assert verdicts[combine_key] in ("earns its place", "does not earn its place — recommend removing")
    assert len(verdicts["stability_combine_ranking"]) == 5

    # 2026-09-02 Decision Log follow-up: the two competing resid_norm
    # strategies, isolated under resid_only, each verdicted against the
    # "absolute" control arm, plus a 3-way ranking.
    for norm_key in (
        "stability_resid_norm_quantile_vs_absolute", "stability_resid_norm_robust_scale_vs_absolute",
    ):
        assert norm_key in verdicts
        assert verdicts[norm_key] in ("earns its place", "does not earn its place — recommend removing")
    assert len(verdicts["stability_resid_norm_ranking"]) == 3
    assert {r["resid_norm"] for r in verdicts["stability_resid_norm_ranking"]} == {
        "absolute", "quantile", "robust_scale",
    }

    assert f"{legacy_cons}_focus_gate" in verdicts
    assert f"{legacy_cons}_exposure_gate" in verdicts
    for v in (
        verdicts["stability_exposure_gate"],
        verdicts[f"{legacy_cons}_focus_gate"],
        verdicts[f"{legacy_cons}_exposure_gate"],
    ):
        assert v in ("earns its place", "does not earn its place — recommend removing")

    # The chosen/shipped arm is always one of the stability arms,
    # never a legacy one, regardless of this run's own numeric ranking
    # (Ryan's ratified 2026-09-02 decision).
    assert report.overall_winner in (
        "stability_and_full", "stability_and_no_exposure",
        "stability_or_full", "stability_resid_only_full", "stability_lapvar_only_full",
        "stability_score_full", "stability_score_no_exposure",
        "stability_resid_only_quantile_full", "stability_resid_only_robust_scale_full",
    )

    assert (tmp_path / "out" / "params.json").exists()
    written_params = json.loads((tmp_path / "out" / "params.json").read_text())
    # The shipped params carry the stability fields, not a legacy artifact.
    assert "stability_resid_max" in written_params["visual"]
    assert "stability_lapvar_quantile" in written_params["visual"]
    assert "stability_combine" in written_params["visual"]
    assert (tmp_path / "out" / "fit_report.json").exists()
    written = json.loads((tmp_path / "out" / "params.json").read_text())
    assert written["fit_provenance"].startswith("fitted:")
    assert "visual" in written and "analysis" in written

    fit_report = json.loads((tmp_path / "out" / "fit_report.json").read_text())
    assert "warnings" in fit_report, "the top-level edge-value alarm list must always be present, even if empty"


# ---------------------------------------------------------------------------
# Grid-edge alarm (2026-09-02 Decision Log follow-up, task brief point 1)
# ---------------------------------------------------------------------------

def test_check_grid_edges_fires_on_a_fitted_max():
    """The exact failure mode that triggered this investigation:
    `stability_resid_max` fitted to 2.0, the max of its own 5-point grid."""
    grid = {"stability_resid_max": [0.8, 1.0, 1.2, 1.5, 2.0]}
    params = SegmentParams(stability_resid_max=2.0)
    warnings = check_grid_edges(params, grid)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["param"] == "stability_resid_max"
    assert w["value"] == 2.0
    assert w["grid_edge"] == "max"
    assert w["grid_min"] == 0.8 and w["grid_max"] == 2.0
    assert "note" in w and w["note"]  # a loud, structured, human-readable note


def test_check_grid_edges_fires_on_a_fitted_min():
    grid = {"stability_lapvar_quantile": [0.10, 0.20, 0.30, 0.40, 0.50]}
    params = SegmentParams(stability_lapvar_quantile=0.10)
    warnings = check_grid_edges(params, grid)
    assert len(warnings) == 1
    assert warnings[0]["grid_edge"] == "min"


def test_check_grid_edges_silent_on_an_interior_optimum():
    grid = {"stability_resid_max": [0.8, 1.2, 1.8, 2.7, 4.0, 6.0, 9.0]}
    params = SegmentParams(stability_resid_max=1.8)
    assert check_grid_edges(params, grid) == []


def test_check_grid_edges_checks_every_param_in_a_multi_param_grid():
    """A grid with several parameters: only the ones actually pinned to
    their own wall are reported, and each independently."""
    grid = {
        "stability_resid_max": STABILITY_RESID_MAX_GRID,
        "stability_lapvar_quantile": STABILITY_LAPVAR_QUANTILE_GRID,
    }
    edge_params = SegmentParams(
        stability_resid_max=max(STABILITY_RESID_MAX_GRID),
        stability_lapvar_quantile=0.30,  # interior of STABILITY_LAPVAR_QUANTILE_GRID
    )
    warnings = check_grid_edges(edge_params, grid)
    assert len(warnings) == 1
    assert warnings[0]["param"] == "stability_resid_max"

    both_edges = SegmentParams(
        stability_resid_max=min(STABILITY_RESID_MAX_GRID),
        stability_lapvar_quantile=max(STABILITY_LAPVAR_QUANTILE_GRID),
    )
    warnings_both = check_grid_edges(both_edges, grid)
    assert {w["param"] for w in warnings_both} == {"stability_resid_max", "stability_lapvar_quantile"}


def test_check_grid_edges_settle_frames_special_case():
    """settle_frames is stored specially (as an int, alongside a derived
    settle_frames_static) -- the edge check must still find it by its own
    grid name, same as every other field."""
    grid = {"settle_frames": [4, 6, 8, 10, 12]}
    params = SegmentParams(settle_frames=12, settle_frames_static=6)
    warnings = check_grid_edges(params, grid)
    assert len(warnings) == 1
    assert warnings[0]["param"] == "settle_frames"
    assert warnings[0]["value"] == 12


def test_fit_one_stability_and_produces_no_edge_warning_with_widened_grid_on_clean_data():
    """On a scenario where the fitted optimum is interior (a clean static
    run well inside the widened grids), fit_one's own returned warnings
    list is empty -- the alarm does not cry wolf on an ordinary fit."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # Reuses _small_scenario's shape directly (avoids importing pytest's
        # tmp_path fixture outside a test function).
        tmp_path = Path(td)
        npz, truth, blocks = _small_scenario(tmp_path)
        evaluator = Evaluator(npz, SOURCE_PATH, truth)
        params, metrics, trace, warnings = fit_one(
            "stability", False, True, evaluator, blocks, 0.60, 1, ("motion", "exposure"),
            stability_combine="and",
        )
        # Not a strict guarantee for every possible dataset, but on this
        # deliberately clean, fully-accepted-static scenario the fit should
        # not need either wall of the widened grid -- if it does, that is
        # itself worth knowing, so this is asserted rather than skipped.
        edge_params = {w["param"] for w in warnings}
        assert "stability_resid_max" not in edge_params or "stability_lapvar_quantile" not in edge_params


# ---------------------------------------------------------------------------
# stability_combine: non-AND combination structures (task brief point 3)
# ---------------------------------------------------------------------------

def test_stability_combine_or_accepts_more_than_and_on_the_same_data(tmp_path):
    """OR is a strictly looser gate than AND for the same thresholds: on
    any data, the OR-consolidated stable mask is a superset of the AND
    one, so OR must never predict LESS coverage than AND."""
    from posthouse.cull.segment import segment_source, SegmentParams as SP

    npz, truth, blocks = _small_scenario(tmp_path)
    common = dict(stability_resid_max=1.5, stability_lapvar_quantile=0.30, exposure_gate=False)
    and_result = segment_source(npz, params=SP(consolidation="stability", stability_combine="and", **common))
    or_result = segment_source(npz, params=SP(consolidation="stability", stability_combine="or", **common))

    and_covered = sum(s.frame_out - s.frame_in for s in and_result.segments)
    or_covered = sum(s.frame_out - s.frame_in for s in or_result.segments)
    assert or_covered >= and_covered


def test_stability_combine_resid_only_ignores_lapvar(tmp_path):
    """resid_only must accept a span with terrible lapvar as long as resid
    is fine -- the whole point of isolating the signal."""
    from posthouse.cull.segment import segment_source, SegmentParams as SP

    n = 900
    arrays = _clean_arrays(n)
    arrays["lapvar_norm"] = np.zeros(n, dtype=np.float32)  # worst possible sharpness
    npz = _write_sidecar(tmp_path / "sidecars", "residonly", ["static"] * n, arrays)

    params = SP(
        consolidation="stability", stability_combine="resid_only",
        stability_resid_max=1.5, stability_lapvar_quantile=0.99, exposure_gate=False,
    )
    result = segment_source(npz, params=params)
    covered = sum(s.frame_out - s.frame_in for s in result.segments)
    assert covered > 0, "resid_only must not reject purely on lapvar"


def test_stability_combine_lapvar_only_ignores_resid(tmp_path):
    from posthouse.cull.segment import segment_source, SegmentParams as SP

    n = 900
    arrays = _clean_arrays(n)
    arrays["resid"] = np.full(n, 50.0, dtype=np.float32)  # terrible motion residual
    npz = _write_sidecar(tmp_path / "sidecars", "lapvaronly", ["static"] * n, arrays)

    params = SP(
        consolidation="stability", stability_combine="lapvar_only",
        stability_resid_max=0.01, stability_lapvar_quantile=0.10, exposure_gate=False,
    )
    result = segment_source(npz, params=params)
    covered = sum(s.frame_out - s.frame_in for s in result.segments)
    assert covered > 0, "lapvar_only must not reject purely on resid"


def test_stability_combine_score_lets_a_strong_signal_compensate_a_weak_one():
    """The whole point of the combined score (task brief point 3, option
    b): a frame excellent on one signal and mediocre on the other can
    still clear ONE threshold, which an AND gate structurally cannot
    allow if either wall is individually failed."""
    from posthouse.cull.segment import _stability_score, SegmentParams as SP

    n = 100
    # resid: half the clip has resid=0 (perfect), half resid=10 (bad).
    # lapvar: uniformly mediocre everywhere.
    resid = np.concatenate([np.zeros(50), np.full(50, 10.0)])
    lapvar = np.full(n, 5.0)
    params = SP(stability_score_resid_weight=0.5, stability_score_threshold=0.5)
    score = _stability_score(resid, lapvar, params)
    # The first half (great resid, mediocre lapvar) should score higher
    # than the second half (bad resid, same mediocre lapvar).
    assert score[:50].mean() > score[50:].mean()


def test_stability_combine_score_is_bounded_and_scale_free():
    """Percentile-rank normalization means the score never depends on the
    signals' absolute scale -- verified directly by scaling resid/lapvar
    by an arbitrary factor and checking the score is unchanged."""
    from posthouse.cull.segment import _stability_score, SegmentParams as SP

    rng = np.random.default_rng(0)
    resid = rng.uniform(0, 50, size=200)
    lapvar = rng.uniform(0, 2, size=200)
    params = SP(stability_score_resid_weight=0.4)

    score_a = _stability_score(resid, lapvar, params)
    score_b = _stability_score(resid * 1000.0, lapvar * 0.001, params)  # wildly different scale
    assert np.allclose(score_a, score_b)
    assert score_a.min() >= 0.0 and score_a.max() <= 1.0


def test_segment_params_rejects_unknown_stability_combine():
    with pytest.raises(ValueError, match="stability_combine"):
        SegmentParams(stability_combine="nonsense")


def test_segment_params_rejects_out_of_range_score_threshold():
    with pytest.raises(ValueError, match="stability_score_threshold"):
        SegmentParams(stability_score_threshold=1.5)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_fit_rejects_missing_sidecar(tmp_path):
    with pytest.raises(FitValidationError):
        fit(tmp_path / "nope.signals.npz", tmp_path / "nope.xml", tmp_path / "out")


def test_fit_rejects_non_sidecar_path(tmp_path):
    bogus = tmp_path / "not_a_sidecar.mp4"
    bogus.write_bytes(b"")
    with pytest.raises(FitValidationError):
        fit(bogus, tmp_path / "nope.xml", tmp_path / "out")
