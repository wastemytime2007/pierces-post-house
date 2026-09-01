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
# proxy for global motion (both are downscaled from the same frames, and
# motion survives a lossy re-encode), while it should NOT agree for the
# signals a lossy re-encode directly corrupts (sharpness, audio peaks).
#
# FINDING (do not silently "fix" by loosening the fixtures): on these
# safety-net fixtures the sharpness side of this gate does NOT reproduce
# the design doc's expectation. testsrc2 (the synthetic pattern every
# fixture is built from — see safety_net/fixtures/generate_fixtures.py) is
# a smooth, low-frequency gradient pattern with very little high-frequency
# detail for a CRF-28 encode to destroy, and both the original fixtures
# (already CRF 30) and the 540p proxy (CRF 28) end up upscaled to the same
# 960x540 analysis plane from the same 640x360 source, so there is little
# room for lossy compression to show up as a median-lapvar gap. Measured:
# stable.mp4 original median lapvar 121.6 vs proxy 118.5 (ratio 1.03) — a
# 3% difference, not the "sharpness score becomes a bitrate meter" effect
# the design doc measured on real 4K daylight footage. The audio side DOES
# reproduce (measured peak dBFS differs by several dB, well above a 1 dB
# floor), and the motion side agrees tightly as expected. This is reported
# as a real finding for the Architect, not adjusted to force a match: the
# design's claim was measured on real handheld footage with actual texture,
# and a smooth synthetic test pattern at 640x360 is not that. The gate
# still asserts what is actually true on these fixtures.
# ---------------------------------------------------------------------------

@pytest.mark.tier2
@pytest.mark.parametrize("source", [STABLE, AROLL])
def test_proxy_vs_source_motion_agrees_tightly(ffmpeg_bin, source, tmp_path):
    proxy_path = tmp_path / f"{source.stem}.proxy.mp4"
    _make_proxy(ffmpeg_bin, source, proxy_path)

    orig = extract_signals(source, tmp_path / "orig", decode="software")
    proxy = extract_signals(proxy_path, tmp_path / "proxy", decode="software")

    do = np.load(orig.npz_path)
    dp = np.load(proxy.npz_path)

    tx_diff = abs(np.median(do["tx"]) - np.median(dp["tx"]))
    ty_diff = abs(np.median(do["ty"]) - np.median(dp["ty"]))
    assert tx_diff < 0.5 and ty_diff < 0.5, (
        f"global motion should agree tightly between original and proxy decode: "
        f"tx_diff={tx_diff}, ty_diff={ty_diff}"
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
    assert (tmp_path / "stable.mp4.signals.npz").exists()
    assert (tmp_path / "stable.mp4.signals.json").exists()


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
