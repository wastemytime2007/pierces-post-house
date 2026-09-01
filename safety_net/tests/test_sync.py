"""Tier-2: posthouse.harvest.sync against REAL correlated dual-source audio.

This is the attempt to close the last open Tier-2 gap named in
ROADMAP.md's Decision Log and safety_net/README.md "Scoped out": lav
sync needs genuine correlated audio, because synthetic sine-tone audio
cannot clear `SCORE_USE`. Synthetic here means "generated," not
"uncorrelated" — real speech (macOS `say`) into two divergent encodes of
the SAME underlying audio: a "camera" track and a "lav" track offset by
a known amount, with different gain/EQ and added noise so the lav is not
a bit-identical copy.

**Honesty rule for this file (from the task brief): if the measured score
does not clear SCORE_USE, this test must not lower the threshold or
weaken the assertion.** It must skip/xfail with the measured number
recorded in the skip reason. As measured during this build: offset
recovered -1.504s against a known -1.5s (4ms error) and score 11.55
against SCORE_USE=10.0 — real speech clears the floor. See
posthouse/harvest/DEFERRED.md and the session report for the exact
transcript; this docstring is not re-asserting a number that could go
stale, the test below measures it fresh every run.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier2

SENTENCE = (
    "the kitchen has new granite countertops and the primary suite "
    "opens onto a private balcony overlooking the backyard"
)
KNOWN_OFFSET_SEC = 1.5
TOLERANCE_SEC = 0.15


def _require_deps_or_skip():
    if importlib.util.find_spec("audio_offset_finder") is None:
        pytest.skip(
            "sync test requires audio_offset_finder; run on Ryan's Mac "
            "(~/precut-venv-fresh)."
        )
    if shutil.which("say") is None:
        pytest.skip("sync test requires macOS `say` (TTS) on PATH.")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"sync test requires {tool} on PATH.")


@pytest.fixture(scope="module")
def dual_source_audio(tmp_path_factory) -> dict:
    """Build a "camera" MOV and a "lav" WAV that are the SAME speech,
    known-offset, and NOT bit-identical (different gain/EQ + noise)."""
    _require_deps_or_skip()
    work = tmp_path_factory.mktemp("sync_audio")

    speech_aiff = work / "speech.aiff"
    speech_wav = work / "speech.wav"
    subprocess.run(
        ["say", "-v", "Samantha", "-o", str(speech_aiff), SENTENCE],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(speech_aiff), "-ar", "16000", str(speech_wav)],
        check=True, capture_output=True,
    )

    camera_mov = work / "camera.mov"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=8:r=30",
            "-i", str(speech_wav),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(camera_mov),
        ],
        check=True, capture_output=True,
    )

    lav_wav = work / "lav.wav"
    delay_ms = int(KNOWN_OFFSET_SEC * 1000)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(speech_wav),
            "-af",
            f"adelay={delay_ms}|{delay_ms},volume=1.6,"
            f"equalizer=f=1000:t=q:w=1:g=6,"
            f"aeval=val(0)+0.02*random(0):c=same",
            str(lav_wav),
        ],
        check=True, capture_output=True,
    )

    return {"camera": camera_mov, "lav": lav_wav}


def test_sync_pairs_recovers_known_offset_and_score(dual_source_audio: dict):
    _require_deps_or_skip()
    from posthouse.harvest.sync import SCORE_USE, sync_pairs

    result = sync_pairs([dual_source_audio["camera"]], [dual_source_audio["lav"]])

    assert result.score_threshold == SCORE_USE
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.error is None, f"sync_pair failed: {pair.error}"

    # Report the measured numbers unconditionally — this is the honest
    # finding either way (see module docstring).
    measured = f"offset_sec={pair.offset_sec:.3f} score={pair.score:.2f} SCORE_USE={SCORE_USE}"

    if pair.score < SCORE_USE:
        pytest.skip(
            f"Real correlated TTS audio did NOT clear SCORE_USE — measured "
            f"{measured}. Threshold intentionally NOT lowered per the task "
            f"brief; this is a real, documented finding, not a bug in the "
            f"test. See posthouse/harvest/DEFERRED.md."
        )

    assert pair.passed_threshold, measured
    # offset_sec sign convention (audio_sync.SyncPair docstring): negative
    # means the audio file's t=0 is BEFORE the A-roll's — exactly what
    # prepending KNOWN_OFFSET_SEC of silence to the lav produces.
    assert pair.offset_sec == pytest.approx(-KNOWN_OFFSET_SEC, abs=TOLERANCE_SEC), measured


def test_sync_pairs_never_drops_a_pair_regardless_of_score(dual_source_audio: dict, tmp_path: Path):
    _require_deps_or_skip()
    from posthouse.harvest.sync import sync_pairs

    # An uncorrelated / silent "audio" file should score low, but the
    # wrapper's contract (ROADMAP Phase 4 open item) is: return it,
    # flagged, never drop it and never silently include it.
    silent = tmp_path / "silent.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "3", str(silent)],
        check=True, capture_output=True,
    )

    result = sync_pairs([dual_source_audio["camera"]], [dual_source_audio["lav"], silent])

    assert len(result.pairs) == 2, "sync_pairs must return every pair, never drop one"
    by_lav = {p.lav_path: p for p in result.pairs}
    assert str(silent) in by_lav
    silent_pair = by_lav[str(silent)]
    # Whatever the silent pair's score turns out to be, it must be present
    # and honestly flagged — never silently coerced to passed_threshold.
    assert silent_pair.passed_threshold == (
        silent_pair.score is not None and silent_pair.score >= result.score_threshold
    )
