"""Tests for posthouse.cull.signals — the Phase 4 slice-1 signal extractor.

Per ``docs/design/PHASE4_CULL_DESIGN.md`` §5 "Slice 1", this module is
tested against: ordering assertions on the five safety-net fixtures
(non-overfittable sign checks, not scores); the frame-count invariant;
determinism (same file twice -> byte-identical npz); and the
proxy-vs-source agreement gate ROADMAP §4 requires. ``posthouse`` is
imported directly as a sibling top-level package, per the same
"run pytest from the repo root" convention every other posthouse test
file uses (``safety_net/run_safety_net.sh``).

No fixture requires PRECUT_ROOT or the PreCut checkout — signals.py has
no PreCut dependency (design §5: "Nothing from PreCut").

Whole-file ``tier2`` (code review, 2026-09-01): every test here either
calls ``extract_signals`` (needs real numpy for its array math, real
ffmpeg/ffprobe for the decode) or drives ``_iter_gray_frames`` directly
with real numpy frame buffers. Under conftest's numpy stub (installed
only when numpy is genuinely absent, i.e. a cloud sandbox) these used to
crash with an ``AttributeError`` on the first ``np.zeros``/``np.fft``
call instead of skipping cleanly — the same convention
``test_transcribe.py`` already uses for a whole module that needs real
heavy deps.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from posthouse.cull.signals import (
    SignalsError,
    SignalsValidationError,
    extract_signals,
    sha256_file,
)

pytestmark = pytest.mark.tier2

FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"

STABLE = FIXTURES_MEDIA / "stable.mp4"
SHAKY = FIXTURES_MEDIA / "shaky.mp4"
BLURRED = FIXTURES_MEDIA / "blurred.mp4"
UNDEREXPOSED = FIXTURES_MEDIA / "underexposed.mp4"
OVEREXPOSED = FIXTURES_MEDIA / "overexposed.mp4"
AROLL = FIXTURES_MEDIA / "AROLL_01.MOV"

# Proxy encode: EXACTLY PreCut's proxy_manager._encode_proxy args (read from
# ~/precut-checkout/python_backend/proxy_manager.py on Ryan's Mac), so the
# proxy-vs-source gate below tests the real proxy convention, not a stand-in.
_PROXY_CRF = 28
_PROXY_HEIGHT = 540


def _make_proxy(ffmpeg: str, source: Path, dest: Path) -> Path:
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-c:v", "libx264", "-preset", "fast", "-crf", str(_PROXY_CRF),
        "-vf", f"scale=-2:{_PROXY_HEIGHT}",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


@pytest.fixture(scope="module")
def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        pytest.skip("ffmpeg not on PATH")
    return found


# ---------------------------------------------------------------------------
# Ordering assertions (sign checks, not scores — design §5 slice 1, §3.2 #4)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fixture_signals(tmp_path_factory) -> dict:
    """Extract signals once for all five fixtures, shared by every ordering
    test below. Software decode: deterministic across machines regardless
    of hwaccel availability, and these clips are 4s each so it costs
    nothing to force it."""
    out = tmp_path_factory.mktemp("cull_signals_fixtures")
    names = {
        "stable": STABLE, "shaky": SHAKY, "blurred": BLURRED,
        "underexposed": UNDEREXPOSED, "overexposed": OVEREXPOSED,
    }
    results = {}
    for name, path in names.items():
        result = extract_signals(path, out, decode="software")
        results[name] = {"result": result, "npz": np.load(result.npz_path)}
    return results


@pytest.mark.tier2
def test_shaky_has_higher_motion_residual_than_stable(fixture_signals):
    stable_resid = np.median(fixture_signals["stable"]["npz"]["resid"])
    shaky_resid = np.median(fixture_signals["shaky"]["npz"]["resid"])
    assert shaky_resid > stable_resid, (
        f"shaky ({shaky_resid}) must show a higher fit residual than "
        f"stable ({stable_resid}) — shaky.mp4 shakes via oscillating "
        f"rotation, which a rigid-body similarity fit should not perfectly "
        f"absorb (edge/black-fill artifacts at frame corners)."
    )


@pytest.mark.tier2
def test_shaky_has_higher_hf_energy_than_stable(fixture_signals):
    stable_hf = np.median(fixture_signals["stable"]["npz"]["hf_energy"])
    shaky_hf = np.median(fixture_signals["shaky"]["npz"]["hf_energy"])
    assert shaky_hf > stable_hf, (
        f"shaky ({shaky_hf}) must show higher high-frequency motion energy "
        f"than stable ({stable_hf})."
    )


@pytest.mark.tier2
def test_blurred_has_lower_median_lapvar_than_stable(fixture_signals):
    stable_lapvar = np.median(fixture_signals["stable"]["npz"]["lapvar"])
    blurred_lapvar = np.median(fixture_signals["blurred"]["npz"]["lapvar"])
    assert blurred_lapvar < stable_lapvar, (
        f"blurred ({blurred_lapvar}) must show lower Laplacian variance "
        f"than stable ({stable_lapvar})."
    )


@pytest.mark.tier2
def test_underexposed_has_highest_clipped_low_fraction(fixture_signals):
    p90s = {
        name: np.percentile(data["npz"]["clip_low"], 90)
        for name, data in fixture_signals.items()
    }
    winner = max(p90s, key=p90s.get)
    assert winner == "underexposed", f"expected underexposed to lead clip_low p90, got {p90s}"


@pytest.mark.tier2
def test_overexposed_has_highest_clipped_high_fraction(fixture_signals):
    p90s = {
        name: np.percentile(data["npz"]["clip_high"], 90)
        for name, data in fixture_signals.items()
    }
    winner = max(p90s, key=p90s.get)
    assert winner == "overexposed", f"expected overexposed to lead clip_high p90, got {p90s}"


# ---------------------------------------------------------------------------
# Frame-count invariant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [STABLE, SHAKY, BLURRED, UNDEREXPOSED, OVEREXPOSED, AROLL])
def test_analysed_frames_within_2_of_duration_times_fps(path, tmp_path):
    result = extract_signals(path, tmp_path, decode="software")
    expected = round(result.duration_sec * result.fps)
    assert abs(result.analysed_frames - expected) <= 2, (
        f"{path.name}: analysed {result.analysed_frames} vs expected ~{expected}"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_file_twice_is_byte_identical(tmp_path):
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    r1 = extract_signals(STABLE, out1, decode="software")
    r2 = extract_signals(STABLE, out2, decode="software")

    npz1 = r1.npz_path.read_bytes()
    npz2 = r2.npz_path.read_bytes()
    assert npz1 == npz2, "two runs on the same file must produce byte-identical npz files"

    header1 = json.loads(r1.json_path.read_text())
    header2 = json.loads(r2.json_path.read_text())
    header1.pop("created_at")
    header2.pop("created_at")
    assert header1 == header2, "headers must match apart from created_at"


# ---------------------------------------------------------------------------
# Audio: presence and the explicit "no audio stream" marker
# ---------------------------------------------------------------------------

def test_aroll_with_audio_yields_audio_windows(tmp_path):
    result = extract_signals(AROLL, tmp_path, decode="software")
    assert result.has_audio is True
    data = np.load(result.npz_path)
    assert "audio_peak_dbfs" in data
    assert data["audio_peak_dbfs"].size > 0
    assert "audio_rms_dbfs" in data
    assert "audio_clip_run" in data

    header = json.loads(result.json_path.read_text())
    assert header["audio"]["present"] is True


def test_silent_source_gets_explicit_no_audio_marker_not_a_crash(tmp_path):
    result = extract_signals(STABLE, tmp_path, decode="software")
    assert result.has_audio is False
    data = np.load(result.npz_path)
    assert "audio_peak_dbfs" not in data, "a silent source must not carry fabricated audio arrays"

    header = json.loads(result.json_path.read_text())
    assert header["audio"] == {"present": False, "note": "no audio stream"}


# ---------------------------------------------------------------------------
# Sidecar sha256: recorded, and a mismatch (staleness) is detectable
# ---------------------------------------------------------------------------

def test_sidecar_records_source_sha256(tmp_path):
    result = extract_signals(STABLE, tmp_path, decode="software")
    assert result.sha256 == sha256_file(STABLE)
    header = json.loads(result.json_path.read_text())
    assert header["source"]["sha256"] == result.sha256


def test_stale_sidecar_is_detectable_via_sha256_mismatch(tmp_path):
    """Simulates a source file changing after its sidecar was written (the
    scenario CULLS.md §6 calls a stale sidecar): the recorded sha256 no
    longer matches the file on disk, which is exactly the signal a later
    reader (culls.json's writer) needs to refuse a stale sidecar."""
    working_copy = tmp_path / "clip.mp4"
    shutil.copyfile(STABLE, working_copy)
    result = extract_signals(working_copy, tmp_path / "out", decode="software")
    recorded_sha = json.loads(result.json_path.read_text())["source"]["sha256"]

    # "Change" the source file (simulating late-footage replacement).
    shutil.copyfile(SHAKY, working_copy)
    current_sha = sha256_file(working_copy)

    assert recorded_sha != current_sha, "a changed source must be detectable against the sidecar"


# ---------------------------------------------------------------------------
# Proxy-vs-source agreement gate (ROADMAP §4 requires it)
#
# This is the evidence for the no-proxies rule, not a formality: analysis on
# an original decode should agree with the same analysis on a CRF-28 540p
# proxy for coarse, median-level global motion, while it should NOT agree
# for the signals a lossy re-encode directly corrupts (sharpness, audio
# peaks) or for per-frame motion detail (see
# test_proxy_vs_source_per_frame_motion_does_not_agree below).
#
# FINDING (do not silently "fix" by loosening the fixtures): on these
# safety-net fixtures the sharpness side of this gate does NOT reproduce
# the design doc's original §1.4 expectation. testsrc2 (the synthetic
# pattern every fixture is built from — see
# safety_net/fixtures/generate_fixtures.py) is a smooth, low-frequency
# gradient pattern with very little high-frequency detail for a CRF-28
# encode to destroy, and both the original fixtures (already CRF 30) and
# the 540p proxy (CRF 28) end up upscaled to the same 960x540 analysis
# plane from the same 640x360 source, so there is little room for lossy
# compression to show up as a median-lapvar gap. Measured: stable.mp4
# original median lapvar 121.6 vs proxy 118.5 (ratio 1.03) — a 3%
# difference, not the "sharpness score becomes a bitrate meter" effect §1.4
# hypothesized before any proxy-vs-original measurement existed. The audio
# side DOES reproduce on this fixture (measured peak dBFS differs by
# several dB, well above a 1 dB floor).
#
# The actual real-footage measurement (code review, 2026-09-01: an earlier
# version of this comment said the sharpness effect "was measured on real
# 4K footage in the design doc" and that "the motion side agrees tightly as
# expected" — both overstated, and the second is now contradicted by
# test_proxy_vs_source_per_frame_motion_does_not_agree below) lives in
# design §1.9, added AFTER slice 1 shipped, on the real benchmark clip
# against PreCut's own proxy: sharpness absolute LEVEL drops (proxy median
# 30% lower) but its per-frame SHAPE survives (r = 0.983) — the reverse of
# a clean "sharpness is corrupted, motion is fine" story — while the motion
# RESIDUAL, the stability signal this module's shake detection depends on,
# correlates only r = 0.544 (tx 0.92, ty 0.74). Cite §1.9's numbers, not a
# vague "measured on real footage" claim, for what proxies actually do to
# this signal set. This is reported as a real finding for the Architect,
# not adjusted to force a match: a smooth synthetic test pattern at 640x360
# cannot reproduce a real-footage compression effect, but the gate still
# asserts what is actually true on these fixtures.
# ---------------------------------------------------------------------------

def test_proxy_vs_source_per_frame_motion_does_not_agree(ffmpeg_bin, tmp_path):
    """Was ``test_proxy_vs_source_motion_agrees_tightly`` (code review,
    2026-09-01: vacuous-test finding). That version compared MEDIANS of
    tx/ty on STABLE/AROLL — both near-zero on a locked-off shot regardless
    of what the proxy did to the signal, so the assertion (``diff < 0.5``)
    could not fail no matter how badly a proxy re-encode corrupted
    per-frame motion. It was evidence of nothing.

    This version measures what design §1.9's real-footage addendum found
    (motion RESIDUAL correlates only r=0.544 original-vs-proxy; tx 0.92,
    ty 0.74) and reproduces the same shape on ``shaky.mp4``, the one
    fixture whose motion is not near-zero: per-frame PEARSON CORRELATION
    of tx/ty/roll/resid between the original decode and a CRF-28 540p
    proxy of it. Measured on this fixture: tx corr 0.85, ty corr 0.28,
    roll corr 0.45, resid corr 0.63 — ty and roll (the axes this fixture's
    shake mostly lives on) do NOT survive proxy compression per-frame, even
    though degraded-but-present tx correlation might look like partial
    agreement. This is the evidence for the no-proxies rule (design §1.9):
    a proxy can be USED for a coarse "is there motion at all" read but must
    never be trusted for the per-frame residual this module's shake/defect
    signal is built from.
    """
    proxy_path = tmp_path / "shaky.proxy.mp4"
    _make_proxy(ffmpeg_bin, SHAKY, proxy_path)

    orig = extract_signals(SHAKY, tmp_path / "orig", decode="software")
    proxy = extract_signals(proxy_path, tmp_path / "proxy", decode="software")

    do = np.load(orig.npz_path)
    dp = np.load(proxy.npz_path)
    n = min(do["tx"].size, dp["tx"].size)
    assert n > 10, "need enough frames for a meaningful correlation"

    def _corr(key: str) -> float:
        a, b = do[key][:n], dp[key][:n]
        return float(np.corrcoef(a, b)[0, 1])

    ty_corr = _corr("ty")
    roll_corr = _corr("roll")
    assert ty_corr < 0.6, (
        f"ty per-frame correlation between original and proxy should be "
        f"weak (measured ~0.28 on this fixture), not tight agreement; "
        f"got {ty_corr} — if this rises, re-check whether the no-proxies "
        f"rule's motion evidence still holds."
    )
    assert roll_corr < 0.6, (
        f"roll per-frame correlation between original and proxy should be "
        f"weak (measured ~0.45 on this fixture); got {roll_corr}."
    )


@pytest.mark.tier2
def test_proxy_vs_source_audio_peak_disagrees(ffmpeg_bin, tmp_path):
    proxy_path = tmp_path / "AROLL_01.proxy.mp4"
    _make_proxy(ffmpeg_bin, AROLL, proxy_path)

    orig = extract_signals(AROLL, tmp_path / "orig", decode="software")
    proxy = extract_signals(proxy_path, tmp_path / "proxy", decode="software")

    do = np.load(orig.npz_path)
    dp = np.load(proxy.npz_path)

    peak_diff_db = abs(np.max(do["audio_peak_dbfs"]) - np.max(dp["audio_peak_dbfs"]))
    assert peak_diff_db > 1.0, (
        f"AAC re-encode of the proxy should measurably shift sample peak "
        f"dBFS vs the original track; measured only {peak_diff_db} dB "
        f"difference — the no-proxies rule's audio evidence did not "
        f"reproduce on this fixture (report, don't loosen)."
    )


@pytest.mark.tier2
def test_proxy_vs_source_sharpness_finding_recorded(ffmpeg_bin, tmp_path):
    """Documents the measured (non-)disagreement rather than asserting a
    result that does not hold on this fixture — see the module-level
    FINDING comment above. This test exists so the measurement is captured
    by the suite (and would flag loudly if it swung wildly), not to prove
    the design doc's real-footage sharpness claim, which this fixture
    cannot reproduce."""
    proxy_path = tmp_path / "stable.proxy.mp4"
    _make_proxy(ffmpeg_bin, STABLE, proxy_path)

    orig = extract_signals(STABLE, tmp_path / "orig", decode="software")
    proxy = extract_signals(proxy_path, tmp_path / "proxy", decode="software")

    do = np.load(orig.npz_path)
    dp = np.load(proxy.npz_path)

    orig_lapvar = float(np.median(do["lapvar"]))
    proxy_lapvar = float(np.median(dp["lapvar"]))
    ratio = orig_lapvar / proxy_lapvar if proxy_lapvar > 1e-9 else float("inf")

    # Measured ~1.03 on this fixture (see FINDING above) — assert it stays
    # in a sane, non-crashing range rather than a specific disagreement
    # ratio that would falsely claim the real-footage effect reproduced.
    assert 0.5 < ratio < 5.0, f"unexpected lapvar ratio {ratio} (orig={orig_lapvar}, proxy={proxy_lapvar})"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_missing_source_file_reports_and_raises(tmp_path):
    with pytest.raises(SignalsValidationError) as excinfo:
        extract_signals(tmp_path / "does_not_exist.mp4", tmp_path, decode="software")
    assert any("does not exist" in p for p in excinfo.value.problems)


def test_bad_decode_mode_reports_and_raises(tmp_path):
    with pytest.raises(SignalsValidationError) as excinfo:
        extract_signals(STABLE, tmp_path, decode="nonsense")
    assert any("decode must be one of" in p for p in excinfo.value.problems)


def test_multiple_problems_all_listed(tmp_path):
    with pytest.raises(SignalsValidationError) as excinfo:
        extract_signals(tmp_path / "nope.mp4", tmp_path, decode="nonsense")
    assert len(excinfo.value.problems) >= 2


# ---------------------------------------------------------------------------
# CLI: non-zero exit, every problem printed
# ---------------------------------------------------------------------------

def test_cli_exits_nonzero_on_missing_source(tmp_path):
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.cull.signals", "extract",
         str(tmp_path / "nope.mp4"), "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert result.returncode != 0
    assert "does not exist" in result.stderr


@pytest.mark.tier2
def test_cli_succeeds_and_prints_timing(tmp_path):
    import sys
    result = subprocess.run(
        [sys.executable, "-m", "posthouse.cull.signals", "extract",
         str(STABLE), "--out", str(tmp_path), "--decode", "software"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "realtime_factor" in result.stdout
    sha12 = sha256_file(STABLE)[:12]
    assert (tmp_path / f"stable.mp4.{sha12}.signals.npz").exists()
    assert (tmp_path / f"stable.mp4.{sha12}.signals.json").exists()


# ---------------------------------------------------------------------------
# Deadlock regression (code review reproduced the hang on slice 1)
# ---------------------------------------------------------------------------

_FAKE_FFMPEG = r'''#!/usr/bin/env python3
"""Stand-in ffmpeg: floods stderr past the 64 KB pipe buffer BEFORE writing
any frames, then emits N gray frames on stdout. With stderr=PIPE and a
consumer that only drains stderr after reading stdout, this deadlocks."""
import os, sys
n_frames = int(os.environ["FAKE_FRAMES"]); frame_bytes = int(os.environ["FAKE_FRAME_BYTES"])
sys.stderr.write("warning: noisy decode\n" * 12000)   # ~260 KB
sys.stderr.flush()
out = sys.stdout.buffer
for _ in range(n_frames):
    out.write(b"\x80" * frame_bytes)
out.flush()
sys.exit(0)
'''


def _fake_ffmpeg(tmp_path, monkeypatch, n_frames: int):
    from posthouse.cull import signals as S
    script = tmp_path / "fake_ffmpeg.py"
    script.write_text(_FAKE_FFMPEG)
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_FRAMES", str(n_frames))
    monkeypatch.setenv("FAKE_FRAME_BYTES", str(S.ANALYSIS_FRAME_BYTES))
    return str(script)


def _run_with_timeout(fn, seconds: float):
    import threading
    result: dict = {}
    def target():
        try:
            result["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - surfaced to the test
            result["error"] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"decode did not finish within {seconds}s: DEADLOCK"
    if "error" in result:
        raise result["error"]
    return result["value"]


def test_decode_does_not_deadlock_when_ffmpeg_floods_stderr(tmp_path, monkeypatch):
    """Code review reproduced a hang: stdout was read frame by frame while
    stderr was only drained afterwards, so a chatty ffmpeg blocked on a
    full stderr pipe while we blocked on stdout. Real footage, not
    fixtures, is where stderr chatter accumulates. stderr now goes to a
    temp file, which has no buffer limit."""
    from posthouse.cull import signals as S
    fake = _fake_ffmpeg(tmp_path, monkeypatch, n_frames=5)
    cmd = S._video_decode_cmd(fake, tmp_path / "whatever.mp4", hwaccel=False)
    frames = _run_with_timeout(lambda: list(S._iter_gray_frames(cmd)), seconds=20)
    assert len(frames) == 5
    assert frames[0][1].shape == (S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH)


def test_decode_consumer_stopping_early_kills_ffmpeg_without_error(tmp_path, monkeypatch):
    """Breaking out of the frame iterator early must terminate ffmpeg
    cleanly, not close its stdout under it and then misreport the EPIPE
    exit as a decode failure."""
    from posthouse.cull import signals as S
    fake = _fake_ffmpeg(tmp_path, monkeypatch, n_frames=50)
    cmd = S._video_decode_cmd(fake, tmp_path / "whatever.mp4", hwaccel=False)
    def take_two():
        got = []
        for idx, frame in S._iter_gray_frames(cmd):
            got.append(idx)
            if len(got) == 2:
                break
        return got
    assert _run_with_timeout(take_two, seconds=20) == [0, 1]


# ---------------------------------------------------------------------------
# Code review, 2026-09-01: remaining findings on the slice-1 extractor
# ---------------------------------------------------------------------------

# --- #1 CRITICAL: memory — streaming decode+analysis, not materialize-then-analyze

def test_streaming_decode_keeps_at_most_two_frames_alive(monkeypatch):
    """An earlier version wrapped ``_iter_gray_frames`` in ``list(...)``
    before any analysis ran, materializing every decoded 518,400-byte frame
    at once — ~9.3 GB for a 10-minute clip, ~30 GB for the 33-minute
    Runnells clip (OOM). ``_run_decode_and_analyze`` must consume the
    generator directly and never retain more than the current frame (plus,
    briefly, the previous frame's cached block spectra — much smaller than
    a raw frame, and never a raw frame itself). Proven with a counting fake
    generator: every synthetic frame is wrapped so its collection is
    observable via ``weakref.finalize`` (CPython refcounting frees it the
    instant the last reference drops, no ``gc.collect()`` needed for this
    acyclic case), and the maximum number simultaneously alive is asserted
    directly rather than inferred from wall-clock RSS."""
    import weakref
    from posthouse.cull import signals as S

    alive = {"count": 0, "max": 0}

    def _tracked_frame():
        frame = np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
        alive["count"] += 1
        alive["max"] = max(alive["max"], alive["count"])

        def _on_collected(counter=alive):
            counter["count"] -= 1

        weakref.finalize(frame, _on_collected)
        return frame

    n_frames = 300

    def fake_iter_gray_frames(cmd):
        for i in range(n_frames):
            yield i, _tracked_frame()

    monkeypatch.setattr(S, "_iter_gray_frames", fake_iter_gray_frames)

    positions = [
        (x + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_WIDTH / 2.0, y + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_HEIGHT / 2.0)
        for (y, x) in S._block_centers()
    ]
    arrs, count = S._run_decode_and_analyze(["fake-cmd"], positions, capacity_hint=8)

    assert count == n_frames
    assert arrs.count == n_frames
    assert alive["max"] <= 2, (
        f"at most 2 decoded frames should ever be alive at once during "
        f"streaming decode+analysis; saw {alive['max']} alive simultaneously "
        f"(memory regression — see module docstring's 'Decode' section)."
    )


def test_streaming_analysis_grows_preallocated_arrays_past_probe_estimate(monkeypatch):
    """A probe's frame-count estimate (``nb_frames``, or duration*fps) can
    undershoot the real decoded count — a missing/wrong ``nb_frames`` tag
    must not crash a long decode. ``_SignalArrays`` starts at the probe's
    estimate and grows (never truncates) past it."""
    from posthouse.cull import signals as S

    def fake_iter_gray_frames(cmd):
        for i in range(20):
            yield i, np.full((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), 128, dtype=np.uint8)

    monkeypatch.setattr(S, "_iter_gray_frames", fake_iter_gray_frames)
    positions = [
        (x + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_WIDTH / 2.0, y + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_HEIGHT / 2.0)
        for (y, x) in S._block_centers()
    ]
    # Deliberately under-sized capacity hint (probe under-estimated).
    arrs, count = S._run_decode_and_analyze(["fake-cmd"], positions, capacity_hint=3)

    assert count == 20
    trimmed = arrs.trim()
    assert trimmed["lapvar"].shape == (20,)
    # A uniform gray frame has zero Laplacian variance everywhere.
    assert np.allclose(trimmed["lapvar"], 0.0)


def test_decode_and_analyze_discards_partial_hardware_pass_on_mid_stream_failure(monkeypatch):
    """A hardware attempt that fails PART WAY through (some frames already
    analysed) must be discarded entirely, not merged with or left
    alongside the software retry's arrays — code review, 2026-09-01: a
    partial hardware pass held in memory alongside a full software retry
    would double memory use for no benefit, since the retry starts over at
    frame 0 regardless."""
    from posthouse.cull import signals as S

    def fake_video_decode_cmd(ffmpeg, source_path, hwaccel, hwdownload_format="nv12"):
        return ["hw"] if hwaccel else ["sw"]

    def fake_iter_gray_frames(cmd):
        if cmd == ["hw"]:
            def _hw_gen():
                yield 0, np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
                yield 1, np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
                raise S.SignalsError("simulated mid-stream hardware failure")
            return _hw_gen()

        def _sw_gen():
            for i in range(5):
                yield i, np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
        return _sw_gen()

    monkeypatch.setattr(S, "_video_decode_cmd", fake_video_decode_cmd)
    monkeypatch.setattr(S, "_iter_gray_frames", fake_iter_gray_frames)
    monkeypatch.setattr(S, "_videotoolbox_available", lambda: True)

    probe = S.ProbeInfo(
        duration_sec=1.0, fps=5.0, width=960, height=540,
        nb_frames=5, has_audio=False, pix_fmt="yuv420p",
    )
    positions = [
        (x + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_WIDTH / 2.0, y + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_HEIGHT / 2.0)
        for (y, x) in S._block_centers()
    ]
    arrs, mode = S._decode_and_analyze(Path("fake.mp4"), "auto", probe, positions)

    assert mode == "software"
    assert arrs.count == 5, "the software retry's full 5 frames, not the hardware attempt's partial 2"


def test_decode_and_analyze_falls_back_to_software_on_zero_hardware_frames(monkeypatch):
    """The other fallback trigger: a hardware attempt that exits cleanly
    but yields zero frames (design §1.1's "never crash" fallback, exercised
    directly rather than only via a real ProRes source)."""
    from posthouse.cull import signals as S

    def fake_video_decode_cmd(ffmpeg, source_path, hwaccel, hwdownload_format="nv12"):
        return ["hw"] if hwaccel else ["sw"]

    def fake_iter_gray_frames(cmd):
        if cmd == ["hw"]:
            return iter(())
        def _sw_gen():
            for i in range(4):
                yield i, np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
        return _sw_gen()

    monkeypatch.setattr(S, "_video_decode_cmd", fake_video_decode_cmd)
    monkeypatch.setattr(S, "_iter_gray_frames", fake_iter_gray_frames)
    monkeypatch.setattr(S, "_videotoolbox_available", lambda: True)

    probe = S.ProbeInfo(
        duration_sec=1.0, fps=4.0, width=960, height=540,
        nb_frames=4, has_audio=False, pix_fmt="yuv420p",
    )
    positions = [
        (x + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_WIDTH / 2.0, y + S.BLOCK_SIZE / 2.0 - S.ANALYSIS_HEIGHT / 2.0)
        for (y, x) in S._block_centers()
    ]
    arrs, mode = S._decode_and_analyze(Path("fake.mp4"), "auto", probe, positions)

    assert mode == "software"
    assert arrs.count == 4


# --- #2 CRITICAL: phase-correlation sign inversion

def test_phase_correlate_sign_convention_matches_docstring():
    """``_phase_correlate(prev, cur)`` must return the shift of ``cur``
    RELATIVE TO ``prev`` — a rightward/downward content shift between
    frames is a POSITIVE (dx, dy). An earlier version built the
    cross-power spectrum as ``fa * conj(fb)`` (prev * conj(cur)), which is
    the textbook formula for "the shift that would move cur onto prev,"
    exactly the negative of this. Verified with ``np.roll``: shifting a
    textured block right 5 and down 3 must read back as (+5, +3), not
    (-5, -3)."""
    from posthouse.cull import signals as S
    rng = np.random.default_rng(0)
    prev = (rng.random((S.BLOCK_SIZE, S.BLOCK_SIZE)) * 255).astype(np.uint8)
    cur = np.roll(np.roll(prev, 5, axis=1), 3, axis=0)  # right 5, down 3

    dx, dy, _confidence = S._phase_correlate(prev, cur)
    assert round(dx) == 5, f"expected dx=+5 (rightward shift), got {dx}"
    assert round(dy) == 3, f"expected dy=+3 (downward shift), got {dy}"


def test_fit_similarity_log_scale_sign_convention_zoom_in_is_positive():
    """Per the model documented in ``_fit_similarity`` (module docstring,
    "Global motion"): ``dx_i = tx + log_scale*px_i - roll*py_i``. A push-in
    (zoom in / magnification increasing) moves content OUTWARD from the
    frame center between frames — the same sign as each block's own
    position — so log_scale must come out positive for a synthetic shift
    field of exactly that shape, and roll must come out ~0 for a pure
    zoom with no rotation component."""
    from posthouse.cull import signals as S
    positions = [(50.0, 0.0), (-50.0, 0.0), (0.0, 50.0), (0.0, -50.0), (30.0, -40.0)]
    k = 0.05  # synthetic magnification rate
    shifts = [(k * px, k * py) for (px, py) in positions]
    confidences = [1.0] * len(positions)

    tx, ty, log_scale, roll, resid, mean_peak = S._fit_similarity(positions, shifts, confidences)
    assert log_scale > 0, f"zoom-in shift field must fit a positive log_scale, got {log_scale}"
    assert abs(roll) < 1e-9, f"a pure zoom has no rotation component, got roll={roll}"
    assert resid < 1e-6


def test_fit_similarity_roll_sign_convention_is_self_consistent():
    """Same model: a pure-rotation shift field (``dx_i = -k*py_i``,
    ``dy_i = k*px_i``) must fit a roll of the SAME sign as the injected
    ``k`` — this pins the fit's own sign convention (which this module
    calls positive roll) so a future change cannot silently flip it
    without a test noticing, independent of what "clockwise" means for any
    particular camera."""
    from posthouse.cull import signals as S
    positions = [(50.0, 0.0), (-50.0, 0.0), (0.0, 50.0), (0.0, -50.0), (30.0, -40.0)]
    k = 0.03
    shifts = [(-k * py, k * px) for (px, py) in positions]
    confidences = [1.0] * len(positions)

    tx, ty, log_scale, roll, resid, mean_peak = S._fit_similarity(positions, shifts, confidences)
    assert roll > 0, f"expected positive roll for k={k}, got {roll}"
    assert abs(log_scale) < 1e-9
    assert resid < 1e-6


def test_correlate_spectra_matches_fft2_reference_and_rfft2_speedup_path():
    """``_correlate_spectra``/``_block_spectrum`` use ``rfft2``/``irfft2``
    (code review, 2026-09-01 perf finding) instead of ``fft2``/``ifft2``.
    Confirms the rfft2-based path used by the streaming per-frame loop
    gives the same peak as a direct ``_phase_correlate`` call on the same
    blocks (the two must agree since one wraps the other)."""
    from posthouse.cull import signals as S
    rng = np.random.default_rng(7)
    prev = (rng.random((S.BLOCK_SIZE, S.BLOCK_SIZE)) * 255).astype(np.uint8)
    cur = np.roll(prev, 2, axis=1)

    dx1, dy1, conf1 = S._phase_correlate(prev, cur)
    fa = S._block_spectrum(prev)
    fb = S._block_spectrum(cur)
    dx2, dy2, conf2 = S._correlate_spectra(fa, fb, prev.shape)

    assert dx1 == dx2 and dy1 == dy2 and conf1 == conf2


# --- #3: int16 histogram wrap

def test_exposure_stats_histogram_all_black_frame_no_int16_wrap():
    """A 518,400-pixel all-black frame concentrates its entire population
    into bin 0 — over int16's 32,767 ceiling. An earlier version cast the
    histogram to int16, which wraps that count to a negative number
    (measured: -5888). int32 has no such ceiling for any analysis-plane
    size this module uses."""
    from posthouse.cull import signals as S
    black = np.zeros((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), dtype=np.uint8)
    _mean, _std, _clip_low, _clip_high, hist = S._exposure_stats(black)

    assert hist.dtype == np.int32
    assert hist[0] == S.ANALYSIS_WIDTH * S.ANALYSIS_HEIGHT == 518400
    assert hist[0] > 0, "must not have wrapped negative"
    assert int(hist.sum()) == S.ANALYSIS_WIDTH * S.ANALYSIS_HEIGHT


def test_exposure_stats_histogram_all_white_frame_no_int16_wrap():
    from posthouse.cull import signals as S
    white = np.full((S.ANALYSIS_HEIGHT, S.ANALYSIS_WIDTH), 255, dtype=np.uint8)
    _mean, _std, _clip_low, _clip_high, hist = S._exposure_stats(white)

    assert hist.dtype == np.int32
    assert hist[-1] == S.ANALYSIS_WIDTH * S.ANALYSIS_HEIGHT == 518400
    assert hist[-1] > 0
    assert int(hist.sum()) == S.ANALYSIS_WIDTH * S.ANALYSIS_HEIGHT


# --- #4: decode_mode misreported for codecs VideoToolbox cannot accelerate

def test_prores_source_is_not_falsely_recorded_as_hwaccel(ffmpeg_bin, tmp_path):
    """ffmpeg used to exit 0 and still emit frames for ProRes under plain
    ``-hwaccel videotoolbox`` — VideoToolbox silently falls back to its own
    software decoder underneath, so the sidecar would record
    ``hwaccel_videotoolbox`` for a run that used no hardware at all.
    ``-hwaccel_output_format videotoolbox_vld`` + an explicit
    ``hwdownload`` makes this fail loudly instead (measured: ffmpeg 8.1
    exits 234 with zero frames), which now correctly routes to the
    software fallback."""
    prores_path = tmp_path / "tiny_prores.mov"
    subprocess.run(
        [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=1",
         "-c:v", "prores_ks", "-profile:v", "0", str(prores_path)],
        check=True, capture_output=True,
    )
    result = extract_signals(prores_path, tmp_path / "out", decode="auto")
    assert result.decode_mode == "software", (
        f"VideoToolbox cannot hardware-decode ProRes; got decode_mode="
        f"{result.decode_mode!r} instead of the honest software fallback."
    )
    assert result.analysed_frames > 0


def test_h264_source_uses_hwaccel_when_available(ffmpeg_bin, tmp_path):
    """The positive case: a codec VideoToolbox genuinely accelerates must
    still be recorded as hardware, so the ProRes fix above isn't just
    forcing software unconditionally."""
    from posthouse.cull import signals as S
    if not S._videotoolbox_available():
        pytest.skip("VideoToolbox not available on this machine")
    h264_path = tmp_path / "tiny_h264.mp4"
    subprocess.run(
        [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(h264_path)],
        check=True, capture_output=True,
    )
    result = extract_signals(h264_path, tmp_path / "out", decode="auto")
    assert result.decode_mode == "hwaccel_videotoolbox"
    assert result.analysed_frames > 0


# --- #5: has_audio vs a stream that decodes to zero samples

def test_audio_stream_present_but_zero_samples_gets_distinct_note_not_a_crash(monkeypatch, tmp_path):
    """A stream ffprobe reports as present but that decodes to zero
    samples (a packetless audio track, or a degenerate mux — the reviewer
    confirmed this is real and reproducible) used to be written as
    ``{"present": true, "n_windows": 0}`` with empty ``audio_*`` arrays: a
    fabricated-looking measurement, not an honest "there is nothing here."
    ``present`` must be decided AFTER decode, from ``samples.size > 0``,
    never from the ffprobe stream check alone.

    Every container format tried (mp4, mov, mkv) refuses to keep a
    genuinely zero-packet audio track at mux time — the muxer drops the
    track entirely rather than write it empty, which itself is consistent
    with "present but zero samples" being a decode-time condition, not a
    container-metadata one. Reproduced here by monkeypatching the audio
    decode step to return exactly what ``_extract_audio_signals`` documents
    a packetless track produces (``None``) while the probe still reports a
    real audio stream, on a source (``AROLL_01.MOV``) that genuinely has
    one."""
    from posthouse.cull import signals as S
    monkeypatch.setattr(S, "_extract_audio_signals", lambda source_path, ffmpeg: None)

    result = extract_signals(AROLL, tmp_path, decode="software")
    assert result.has_audio is False, (
        "present must be decided from decoded samples, not the ffprobe "
        "stream check alone"
    )

    data = np.load(result.npz_path)
    assert "audio_peak_dbfs" not in data
    assert "audio_rms_dbfs" not in data
    assert "audio_clip_run" not in data

    header = json.loads(result.json_path.read_text())
    assert header["audio"] == {
        "present": False,
        "note": "audio stream present but decoded to zero samples",
    }


def test_extract_audio_signals_returns_none_for_zero_byte_decode(tmp_path):
    """Unit-level guard: a decode that produces zero bytes on stdout must
    return ``None``, never call ``np.max``/``np.mean`` on an empty array."""
    from posthouse.cull import signals as S
    fake = tmp_path / "fake_ffmpeg_audio.py"
    fake.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n")
    fake.chmod(0o755)

    result = S._extract_audio_signals(Path("whatever.mp4"), str(fake))
    assert result is None


# --- #8: sidecar filename collision across same-basename sources

def test_same_basename_sources_in_one_out_dir_get_distinct_sidecars(tmp_path):
    """DJI card rollover produces ``100MEDIA/DJI_0006.MP4`` and
    ``101MEDIA/DJI_0006.MP4`` — two different files with the same
    basename. Writing both into one flat ``out_dir`` under the old
    ``<basename>.signals.npz`` naming would silently overwrite one
    sidecar with the other via ``os.replace``."""
    card_a = tmp_path / "100MEDIA"
    card_b = tmp_path / "101MEDIA"
    card_a.mkdir()
    card_b.mkdir()
    shutil.copyfile(STABLE, card_a / "DJI_0006.MP4")
    shutil.copyfile(SHAKY, card_b / "DJI_0006.MP4")
    out = tmp_path / "out"

    r1 = extract_signals(card_a / "DJI_0006.MP4", out, decode="software")
    r2 = extract_signals(card_b / "DJI_0006.MP4", out, decode="software")

    assert r1.npz_path != r2.npz_path
    assert r1.json_path != r2.json_path
    assert r1.npz_path.exists()
    assert r2.npz_path.exists()
    assert r1.sha256 != r2.sha256


def test_sidecar_paths_helper_matches_extract_signals_output(tmp_path):
    """``sidecar_paths()`` is the one place the naming rule lives; slice 3
    calls it to find an already-written sidecar rather than reconstructing
    the pattern, so it must agree exactly with what ``extract_signals``
    actually wrote."""
    from posthouse.cull.signals import sidecar_paths
    result = extract_signals(STABLE, tmp_path, decode="software")
    npz_path, json_path = sidecar_paths(STABLE, tmp_path)
    assert npz_path == result.npz_path
    assert json_path == result.json_path
    # Also works when the caller already has the sha256 (skips re-hashing).
    npz_path2, json_path2 = sidecar_paths(STABLE, tmp_path, sha256=result.sha256)
    assert npz_path2 == result.npz_path


# --- #9: shared utilities and perf

def test_signals_reuses_shared_util_helpers_not_a_local_reimplementation():
    from posthouse import _util
    from posthouse.cull import signals as S
    assert S.atomic_write_bytes is _util.atomic_write_bytes
    assert S.now_iso is _util.now_iso


def test_block_centers_is_cached():
    """Depends only on module constants — recomputing it every frame was
    pure waste (code review, 2026-09-01 perf finding)."""
    from posthouse.cull import signals as S
    assert S._block_centers() is S._block_centers()


def test_windowed_audio_stats_vectorized_clip_run_matches_reference_loop():
    """``_windowed_audio_stats``'s clip-run computation is a
    cumulative-run trick over the fixed ``AUDIO_WINDOW_SAMPLES`` columns
    (code review, 2026-09-01 perf finding: the original was a Python
    ``for`` loop over ``n_windows``, which scales with clip length).
    Verified against the original per-window ``np.diff``-of-run-boundaries
    reference implementation on synthetic data with several clip runs."""
    from posthouse.cull import signals as S

    def _reference_clip_run(windows: np.ndarray) -> np.ndarray:
        n_windows = windows.shape[0]
        clipped_mask = np.abs(windows) >= S.AUDIO_CLIP_THRESHOLD
        clip_run = np.zeros(n_windows, dtype=np.float32)
        for i in range(n_windows):
            row = clipped_mask[i]
            if not row.any():
                continue
            changes = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
            starts = np.flatnonzero(changes == 1)
            ends = np.flatnonzero(changes == -1)
            clip_run[i] = float(np.max(ends - starts)) if len(starts) else 0.0
        return clip_run

    rng = np.random.default_rng(5)
    windows = (rng.random((30, S.AUDIO_WINDOW_SAMPLES)).astype(np.float32) * 0.5)
    windows[3, 100:115] = 1.0
    windows[7, 0:5] = 1.0
    windows[7, 500:503] = 1.0
    windows[15, :] = 1.0  # whole window clipped

    _peak, _rms, clip_run = S._windowed_audio_stats(windows)
    expected = _reference_clip_run(windows)
    assert np.array_equal(clip_run, expected)


def test_extract_audio_signals_streams_in_chunks_not_one_slurp(tmp_path, monkeypatch):
    """Code review, 2026-09-01 perf finding: the original implementation
    read the entire decoded PCM stream in one ``subprocess.run(...)`` call
    before computing anything — ~691 MB/hour of mono float32 at 48kHz.
    Confirms the chunk size is bounded (not "however much ffmpeg wrote")
    by forcing several chunk boundaries with a small ``_AUDIO_CHUNK_BYTES``
    and checking the result is identical to the unchunked reference
    windowing — i.e. chunking is invisible to the output."""
    from posthouse.cull import signals as S

    rng = np.random.default_rng(9)
    n_windows = 50
    samples = (rng.random(n_windows * S.AUDIO_WINDOW_SAMPLES).astype(np.float32) - 0.5)

    fake = tmp_path / "fake_ffmpeg_audio_stream.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "data = " + repr(samples.tobytes()) + "\n"
        "sys.stdout.buffer.write(data)\n"
        "sys.exit(0)\n"
    )
    fake.chmod(0o755)

    monkeypatch.setattr(S, "_AUDIO_CHUNK_BYTES", S.AUDIO_WINDOW_SAMPLES * 4 * 7)  # force many chunk boundaries
    result = S._extract_audio_signals(Path("whatever.mp4"), str(fake))
    assert result is not None
    peak_dbfs, rms_dbfs, clip_run = result

    windows = samples.reshape(n_windows, S.AUDIO_WINDOW_SAMPLES)
    expected_peak, expected_rms, expected_clip = S._windowed_audio_stats(windows)
    assert np.array_equal(peak_dbfs, expected_peak)
    assert np.array_equal(rms_dbfs, expected_rms)
    assert np.array_equal(clip_run, expected_clip)
