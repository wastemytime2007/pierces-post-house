"""Tests for posthouse.cull.classify -- Phase 4 slice 2, per-frame motion
classification.

Per ``docs/design/PHASE4_CULL_DESIGN.md`` §5 "Slice 2", this module is
tested against: synthetic clips with KNOWN ground truth (the only place a
classifier can be checked exactly), a direction test pinning the binding
sign convention, a hysteresis test, the ordering-style safety-net fixture
sanity checks (``stable.mp4`` predominantly static, ``shaky.mp4``
predominantly shake), a sidecar round-trip / idempotency suite, and a
REPORT (not an assertion of exact boundaries) against the real benchmark
clip's two hand-verified windows.

Synthetic clips are generated with a small numpy-driven frame source
piped into ffmpeg as raw grayscale video, NOT ffmpeg's crop/zoompan
filter expressions directly. This is a deliberate, tested departure from
"crop with a moving x/y expression" read literally: on this machine
(ffmpeg 8.1), driving ``crop``'s ``x``/``y`` via the frame-number
variable ``n`` on a looped single PNG produced non-monotonic, periodic
per-frame displacement (verified directly: measured frame-to-frame shift
oscillated between roughly -2.7px and -7.2px for a filter graph asking
for a constant 8px/frame ramp, and ``zoompan`` produced literally zero
inter-frame difference for an image loop under this pytest's ffmpeg,
confirmed with ``ffmpeg -f framemd5`` showing distinct per-frame hashes
existed but the crop position did not track them as expected). Rather
than fight an ffmpeg filter-graph quirk, this module synthesizes exact,
known per-frame pixel offsets and scale factors directly in numpy against
a fixed, richly-textured procedural canvas (a sum of sinusoidal gratings
at different frequencies/orientations plus bounded per-pixel noise, so
every 256x256 phase-correlation block in the 3x3 grid has real texture to
lock onto), and pipes exact frames to ffmpeg only for H.264 encoding --
the encode is the only thing ffmpeg does here. This gives per-frame
ground truth with certainty, which is what design §5 slice 2 actually
asks for ("these have known ground truth, unlike any real footage, and
they are the only place a classifier can be checked exactly").

No fixture requires PRECUT_ROOT or the PreCut checkout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from posthouse.cull.classify import (
    STATE_ID,
    STATE_NAMES,
    ClassifyError,
    ClassifyParams,
    _hysteresis_smooth,
    _run_length_encode,
    classify_sidecar,
)
from posthouse.cull.signals import extract_signals, sidecar_paths

pytestmark = pytest.mark.tier2

FIXTURES_MEDIA = Path(__file__).parent.parent / "fixtures" / "media"
STABLE = FIXTURES_MEDIA / "stable.mp4"
SHAKY = FIXTURES_MEDIA / "shaky.mp4"

# The real benchmark clip (design doc header; ROADMAP §4). Report-only --
# skipped if not present, since it lives on Ryan's Mac and is never
# committed to the coordination repo.
REAL_CLIP = Path(
    "/Volumes/RDOSS_2025/SoldFast 2026/10050 NE University Ave Runnells/"
    "First Walkthrough After Taking Over/Osmo/DJI_20260430075045_0006_D.MP4"
)

FPS = 30
FRAMES = 120
OUT_W, OUT_H = 640, 360
CANVAS_W, CANVAS_H = 2200, 1400


# ---------------------------------------------------------------------------
# Synthetic clip generation: exact, known per-frame content offsets/scale.
# ---------------------------------------------------------------------------

def _make_canvas() -> np.ndarray:
    """A fixed, richly-textured procedural grayscale canvas -- deterministic
    (fixed seed), large enough that every synthetic clip's crop/zoom window
    stays inside it for its whole run. Multiple sinusoidal gratings at
    different frequencies/orientations plus bounded noise, so every
    256x256 analysis block in signals.py's 3x3 grid has real texture."""
    rng = np.random.default_rng(0)
    y, x = np.mgrid[0:CANVAS_H, 0:CANVAS_W]
    img = (
        128
        + 60 * np.sin(x / 13.0) * np.cos(y / 17.0)
        + 40 * np.sin(x / 5.0 + y / 7.0)
        + 30 * np.sin(y / 3.3)
    )
    noise = rng.integers(-20, 20, size=(CANVAS_H, CANVAS_W))
    return np.clip(img + noise, 0, 255).astype(np.uint8)


_CANVAS = _make_canvas()


def _write_synthetic(
    ffmpeg: str, out: Path, ox_fn, oy_fn, scale_fn=None, angle_fn=None, frames: int = FRAMES,
) -> Path:
    """Write ``frames`` raw grayscale frames, each an exact numpy-computed
    view of ``_CANVAS`` at frame ``i``, into ffmpeg's stdin for H.264
    encoding. Exactly one of ``scale_fn``/``angle_fn`` may be given (zoom
    or rotation); otherwise a plain translated crop is used."""
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "gray",
        "-s", f"{OUT_W}x{OUT_H}", "-r", str(FPS), "-i", "-",
        "-frames:v", str(frames),
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        if angle_fn is not None:
            from PIL import Image
            big = Image.fromarray(_CANVAS)
            cx, cy = (CANVAS_W - OUT_W) // 2, (CANVAS_H - OUT_H) // 2
            for i in range(frames):
                rotated = big.rotate(angle_fn(i), resample=Image.BICUBIC, center=(CANVAS_W / 2, CANVAS_H / 2))
                frame = np.array(rotated)[cy:cy + OUT_H, cx:cx + OUT_W]
                proc.stdin.write(frame.astype(np.uint8).tobytes())
        elif scale_fn is not None:
            from PIL import Image
            for i in range(frames):
                s = scale_fn(i)
                cw = max(64, min(int(OUT_W / s), CANVAS_W))
                ch = max(36, min(int(OUT_H / s), CANVAS_H))
                cx, cy = (CANVAS_W - cw) // 2, (CANVAS_H - ch) // 2
                region = _CANVAS[cy:cy + ch, cx:cx + cw]
                frame = np.array(Image.fromarray(region).resize((OUT_W, OUT_H), Image.BILINEAR))
                proc.stdin.write(frame.astype(np.uint8).tobytes())
        else:
            for i in range(frames):
                ox = max(0, min(CANVAS_W - OUT_W, int(round(ox_fn(i)))))
                oy = max(0, min(CANVAS_H - OUT_H, int(round(oy_fn(i)))))
                frame = _CANVAS[oy:oy + OUT_H, ox:ox + OUT_W]
                proc.stdin.write(frame.astype(np.uint8).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed writing {out}")
    return out


def _cx() -> float:
    return (CANVAS_W - OUT_W) / 2.0


def _cy() -> float:
    return (CANVAS_H - OUT_H) / 2.0


@pytest.fixture(scope="module")
def ffmpeg_bin() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        pytest.skip("ffmpeg not on PATH")
    return found


@pytest.fixture(scope="module")
def synthetic_media(ffmpeg_bin, tmp_path_factory) -> dict:
    """One clip per known motion class, built once for the whole module."""
    out = tmp_path_factory.mktemp("cull_classify_synthetic")
    media = {}
    # pan_right: crop window moves RIGHT across canvas -> content moves LEFT
    # -> dx < 0 -> pan_right (module docstring's binding sign convention).
    media["pan_right"] = _write_synthetic(
        ffmpeg_bin, out / "pan_right.mp4", lambda i: 200 + 8 * i, lambda i: _cy()
    )
    # pan_left: window moves LEFT -> content moves RIGHT -> dx > 0 -> pan_left.
    media["pan_left"] = _write_synthetic(
        ffmpeg_bin, out / "pan_left.mp4", lambda i: 1160 - 8 * i, lambda i: _cy()
    )
    # tilt_down: window moves DOWN -> content moves UP -> dy < 0 -> tilt_down.
    media["tilt_down"] = _write_synthetic(
        ffmpeg_bin, out / "tilt_down.mp4", lambda i: _cx(), lambda i: 200 + 8 * i
    )
    # tilt_up: window moves UP -> content moves DOWN -> dy > 0 -> tilt_up.
    media["tilt_up"] = _write_synthetic(
        ffmpeg_bin, out / "tilt_up.mp4", lambda i: _cx(), lambda i: 840 - 8 * i
    )
    # push_in/pull_out/roll use a shorter clip (40 frames) at a faster,
    # geometric (constant-per-frame-ratio) rate than a first attempt used --
    # see classify.py's "Calibration finding" docstring section: a slower
    # additive zoom/rotation measured a signal too close to the safety-net
    # fixtures' own noise ceiling to separate cleanly. A geometric zoom
    # (constant scale RATIO per frame, not a constant scale increment) keeps
    # the measured log_scale constant across the whole clip instead of
    # drifting, which is what "known ground truth" requires here.
    push_frames = 40
    push_total_zoom = 2.5  # total zoom achieved over push_frames
    push_rate = push_total_zoom ** (1.0 / push_frames)
    media["push_in"] = _write_synthetic(
        ffmpeg_bin, out / "push_in.mp4", None, None,
        scale_fn=lambda i: push_rate ** i, frames=push_frames,
    )
    media["pull_out"] = _write_synthetic(
        ffmpeg_bin, out / "pull_out.mp4", None, None,
        scale_fn=lambda i: (1.0 / push_rate) ** i, frames=push_frames,
    )
    roll_frames = 40
    media["roll"] = _write_synthetic(
        ffmpeg_bin, out / "roll.mp4", None, None,
        angle_fn=lambda i: 2.0 * i, frames=roll_frames,
    )
    media["shake"] = _write_synthetic(
        ffmpeg_bin, out / "shake.mp4",
        lambda i: _cx() + 18 * np.sin(2 * np.pi * 6 * i / FPS),
        lambda i: _cy() + 14 * np.cos(2 * np.pi * 7 * i / FPS),
    )
    media["static"] = _write_synthetic(
        ffmpeg_bin, out / "static.mp4", lambda i: _cx(), lambda i: _cy()
    )
    return media


@pytest.fixture(scope="module")
def synthetic_classified(synthetic_media, tmp_path_factory) -> dict:
    """Extract signals and classify each synthetic clip once, shared by every
    class-dominance assertion below."""
    out = tmp_path_factory.mktemp("cull_classify_synthetic_sidecars")
    results = {}
    for name, path in synthetic_media.items():
        sig = extract_signals(path, out, decode="software")
        results[name] = classify_sidecar(sig.npz_path)
    return results


def _dominant_class(result) -> str:
    fractions = result.class_fractions()
    return max(fractions, key=fractions.get)


# ---------------------------------------------------------------------------
# Synthetic clips with known ground truth (design §5 slice 2's requirement)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("pan_right", "pan_right"),
        ("pan_left", "pan_left"),
        ("tilt_down", "tilt_down"),
        ("tilt_up", "tilt_up"),
        ("push_in", "push_in"),
        ("pull_out", "pull_out"),
        ("roll", "roll"),
        ("shake", "shake"),
        ("static", "static"),
    ],
)
def test_synthetic_clip_dominant_class_matches_ground_truth(synthetic_classified, name, expected):
    result = synthetic_classified[name]
    dominant = _dominant_class(result)
    fractions = result.class_fractions()
    assert dominant == expected, (
        f"{name}: expected dominant class {expected!r}, got {dominant!r} "
        f"(fractions: {fractions})"
    )
    # Not just "won the argmax by a hair" -- a real majority of frames.
    assert fractions[expected] >= 0.5, (
        f"{name}: {expected!r} only covers {fractions[expected]:.2f} of frames"
    )


# ---------------------------------------------------------------------------
# Direction test: the binding sign convention (design §0's 2026-09-01
# sign-pin correction; module docstring's camera-vs-content mapping).
# ---------------------------------------------------------------------------

def test_direction_convention_pan_left_and_pan_right_are_opposite_labels(synthetic_classified):
    left = synthetic_classified["pan_left"]
    right = synthetic_classified["pan_right"]
    assert _dominant_class(left) == "pan_left"
    assert _dominant_class(right) == "pan_right"
    assert _dominant_class(left) != _dominant_class(right)


def test_direction_convention_tilt_up_and_tilt_down_are_opposite_labels(synthetic_classified):
    up = synthetic_classified["tilt_up"]
    down = synthetic_classified["tilt_down"]
    assert _dominant_class(up) == "tilt_up"
    assert _dominant_class(down) == "tilt_down"
    assert _dominant_class(up) != _dominant_class(down)


def test_direction_convention_matches_dx_dy_sign(synthetic_classified):
    """The raw sign, independent of the cost-function machinery: a
    pan_right clip's smoothed tx must be negative (content moved left);
    a pan_left clip's must be positive (content moved right). This pins
    the physical convention itself, not just the label names."""
    import numpy as np

    with np.load(synthetic_classified["pan_right"].npz_path) as npz:
        tx_right = npz["tx_norm_src_width"]
    with np.load(synthetic_classified["pan_left"].npz_path) as npz:
        tx_left = npz["tx_norm_src_width"]
    assert np.median(tx_right[5:]) < 0, "camera panning right must read content dx < 0"
    assert np.median(tx_left[5:]) > 0, "camera panning left must read content dx > 0"


# ---------------------------------------------------------------------------
# Hysteresis: a single outlier frame inside a long run must not survive.
# ---------------------------------------------------------------------------

def test_hysteresis_kills_single_frame_outlier_in_static_run():
    labels = np.zeros(60, dtype=np.int8)
    labels[30] = STATE_ID["shake"]  # one injected outlier frame
    smoothed = _hysteresis_smooth(labels, ClassifyParams().hysteresis_window_frames)
    rle = _run_length_encode(smoothed, fps=30.0)
    assert len(rle) == 1, f"a single outlier frame must not open its own run, got {rle}"
    assert rle[0]["state"] == "static"


def test_hysteresis_preserves_a_genuine_multi_frame_run():
    labels = np.zeros(60, dtype=np.int8)
    labels[20:40] = STATE_ID["pan_left"]  # a real 20-frame run, not an outlier
    smoothed = _hysteresis_smooth(labels, ClassifyParams().hysteresis_window_frames)
    rle = _run_length_encode(smoothed, fps=30.0)
    states = [r["state"] for r in rle]
    assert "pan_left" in states, f"a genuine 20-frame run must survive, got {rle}"


# ---------------------------------------------------------------------------
# Safety-net fixtures: ordering-style sanity checks, not tuned thresholds.
# ---------------------------------------------------------------------------

def test_stable_fixture_predominantly_static(tmp_path):
    sig = extract_signals(STABLE, tmp_path, decode="software")
    result = classify_sidecar(sig.npz_path)
    assert _dominant_class(result) == "static", result.class_fractions()


def test_shaky_fixture_predominantly_shake(tmp_path):
    sig = extract_signals(SHAKY, tmp_path, decode="software")
    result = classify_sidecar(sig.npz_path)
    assert _dominant_class(result) == "shake", result.class_fractions()


# ---------------------------------------------------------------------------
# Sidecar round-trip: idempotent, preserves pre-existing arrays, refuses a
# mismatched source, updates the npz sha256 correctly.
# ---------------------------------------------------------------------------

def test_classify_twice_is_byte_identical_npz(tmp_path):
    sig = extract_signals(STABLE, tmp_path, decode="software")
    r1 = classify_sidecar(sig.npz_path)
    npz_bytes_1 = sig.npz_path.read_bytes()
    r2 = classify_sidecar(sig.npz_path)
    npz_bytes_2 = sig.npz_path.read_bytes()
    assert npz_bytes_1 == npz_bytes_2, "classifying an already-classified sidecar twice must be byte-identical"
    assert np.array_equal(r1.state, r2.state)


def test_preexisting_arrays_survive_classification(tmp_path):
    sig = extract_signals(STABLE, tmp_path, decode="software")
    with np.load(sig.npz_path) as npz:
        original = {k: npz[k].copy() for k in npz.files}
    classify_sidecar(sig.npz_path)
    with np.load(sig.npz_path) as npz:
        after = {k: npz[k] for k in npz.files}
    for key, value in original.items():
        assert key in after, f"pre-existing array {key!r} was dropped"
        assert np.array_equal(value, after[key]), f"pre-existing array {key!r} was mutated"
    assert "state" in after, "classification must add a state array"


def test_npz_sha256_header_updated_after_classification(tmp_path):
    sig = extract_signals(STABLE, tmp_path, decode="software")
    result = classify_sidecar(sig.npz_path)
    header = json.loads(sig.json_path.read_text())
    import hashlib
    actual_sha = hashlib.sha256(sig.npz_path.read_bytes()).hexdigest()
    assert header["npz_sha256"] == actual_sha, "header npz_sha256 must match the npz as classification left it"


def test_classify_adds_rle_and_provenance_block(tmp_path):
    sig = extract_signals(STABLE, tmp_path, decode="software")
    classify_sidecar(sig.npz_path)
    header = json.loads(sig.json_path.read_text())
    assert "classify" in header
    assert header["classify"]["state_names"] == list(STATE_NAMES)
    assert isinstance(header["classify"]["rle"], list) and header["classify"]["rle"]
    assert "state" in header["columns"]


def test_mismatched_source_sidecar_is_refused(tmp_path):
    out1 = tmp_path / "one"
    out2 = tmp_path / "two"
    sig_stable = extract_signals(STABLE, out1, decode="software")
    sig_shaky = extract_signals(SHAKY, out2, decode="software")

    # Copy shaky's sidecar pair to where stable's would be looked up by
    # source path, so the filename says "stable" but the content is
    # shaky's -- this must be refused, not silently classified.
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    npz_path, json_path = sidecar_paths(STABLE, fake_dir)
    npz_path.write_bytes(sig_shaky.npz_path.read_bytes())
    json_path.write_bytes(sig_shaky.json_path.read_bytes())

    with pytest.raises(ClassifyError):
        classify_sidecar(STABLE, out_dir=fake_dir)


def test_sidecar_not_found_is_a_clear_error(tmp_path):
    missing_source = tmp_path / "does_not_exist.mp4"
    missing_source.write_bytes(b"not a real video")
    with pytest.raises(Exception):
        classify_sidecar(missing_source, out_dir=tmp_path)


# ---------------------------------------------------------------------------
# The real clip: REPORT, not a boundary assertion (design §5 slice 2 /
# the Phase 4 slice-2 brief: "assert only that the pan window is dominated
# by a pan class and the tilt window by a tilt class").
# ---------------------------------------------------------------------------

def _frac_in_window(a: float, b: float, rle: list) -> dict:
    tot: dict = {}
    for run in rle:
        s, e = max(a, run["sec_in"]), min(b, run["sec_out"])
        if e > s:
            tot[run["state"]] = tot.get(run["state"], 0.0) + (e - s)
    return tot


@pytest.mark.skipif(not REAL_CLIP.exists(), reason="real benchmark clip not present on this machine")
def test_real_clip_hand_verified_windows_report(tmp_path, capsys):
    sig = extract_signals(REAL_CLIP, tmp_path, decode="auto")
    result = classify_sidecar(sig.npz_path)

    pan_window = _frac_in_window(14.98, 18.85, result.rle)
    tilt_window = _frac_in_window(19.19, 21.29, result.rle)

    print("PAN window (14.98-18.85s) state coverage (sec):", pan_window)
    print("TILT window (19.19-21.29s) state coverage (sec):", tilt_window)
    print("Overall class distribution:", result.class_fractions())

    pan_dominant = max(pan_window, key=pan_window.get)
    tilt_dominant = max(tilt_window, key=tilt_window.get)

    assert pan_dominant in ("pan_left", "pan_right"), (
        f"pan window not dominated by a pan class: {pan_window}"
    )
    assert tilt_dominant in ("tilt_up", "tilt_down"), (
        f"tilt window not dominated by a tilt class: {tilt_window}"
    )
