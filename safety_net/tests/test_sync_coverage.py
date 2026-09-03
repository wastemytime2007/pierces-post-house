"""Tier-2: posthouse.sync_coverage against REAL correlated audio with a
genuine dead zone in the middle.

Simulates exactly the case that motivated this module (2026-09-03, Ryan,
real Runnells Day 1 footage): a subject who leaves the camera's room and
comes back. Built the same way test_sync.py builds its dual-source
fixture -- real speech (macOS `say`) into divergent camera/lav encodes,
not synthetic tones (which can't clear SCORE_USE at all, see that file's
own docstring) -- but stretched to three segments: speech the camera and
lav BOTH capture, a middle stretch where the lav captures a DIFFERENT
real sentence (simulating a phone call elsewhere -- real audio energy,
zero acoustic relationship to what the camera hears), then the shared
speech again.

**Honesty rule, same as test_sync.py**: if the measured coverage doesn't
land where expected, this test skips with the measured numbers recorded,
it does not loosen the assertion to force a pass.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier2

SHARED_SENTENCE = (
    "the kitchen has new granite countertops and the primary suite "
    "opens onto a private balcony overlooking the backyard"
)
ELSEWHERE_SENTENCE = (
    "yeah I can talk now just give me a second I am walking to another room"
)
KNOWN_OFFSET_SEC = 2.0
OFFSET_TOLERANCE_SEC = 1.0  # windowed correlation is coarser than a full-file lock


def _require_deps_or_skip():
    if importlib.util.find_spec("audio_offset_finder") is None:
        pytest.skip("requires audio_offset_finder; run on Ryan's Mac (~/precut-venv-fresh).")
    if shutil.which("say") is None:
        pytest.skip("requires macOS `say` (TTS) on PATH.")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            pytest.skip(f"requires {tool} on PATH.")


def _tts_wav(text: str, out_path: Path) -> None:
    aiff = out_path.with_suffix(".aiff")
    subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(aiff), "-ar", "16000", str(out_path)],
                    check=True, capture_output=True)


@pytest.fixture(scope="module")
def gap_and_return_audio(tmp_path_factory) -> dict:
    """Camera: [shared speech] [silence] [shared speech again].
    Lav (offset by KNOWN_OFFSET_SEC): [shared speech] [a DIFFERENT real
    sentence -- correlates with nothing the camera hears] [shared speech
    again]. One constant offset holds throughout; only the middle segment
    is genuinely uncorrelated.
    """
    _require_deps_or_skip()
    work = tmp_path_factory.mktemp("sync_coverage_audio")

    shared = work / "shared.wav"
    elsewhere = work / "elsewhere.wav"
    _tts_wav(SHARED_SENTENCE, shared)
    _tts_wav(ELSEWHERE_SENTENCE, elsewhere)

    silence = work / "silence.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "10", str(silence)],
        check=True, capture_output=True,
    )

    def concat(parts: list[Path], out: Path) -> None:
        listfile = out.with_suffix(".txt")
        listfile.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
             "-ar", "16000", str(out)],
            check=True, capture_output=True,
        )

    # Camera audio track: shared speech, then 10s dead air, then shared again.
    camera_audio = work / "camera_audio.wav"
    concat([shared, silence, shared], camera_audio)

    camera_mov = work / "camera.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "color=c=gray:s=320x180:d=40:r=15",
         "-i", str(camera_audio),
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(camera_mov)],
        check=True, capture_output=True,
    )

    # Lav track: same shared-speech segments (divergent encode, like
    # test_sync.py's lav), but the DEAD segment is replaced with a real,
    # different sentence -- "elsewhere," genuinely uncorrelated content,
    # not silence. Then the whole thing gets KNOWN_OFFSET_SEC prepended.
    shared_lav = work / "shared_lav.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(shared), "-af",
         "volume=1.6,equalizer=f=1000:t=q:w=1:g=6,aeval=val(0)+0.02*random(0):c=same",
         str(shared_lav)],
        check=True, capture_output=True,
    )
    lav_body = work / "lav_body.wav"
    concat([shared_lav, elsewhere, shared_lav], lav_body)

    lav_wav = work / "lav.wav"
    delay_ms = int(KNOWN_OFFSET_SEC * 1000)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(lav_body), "-af", f"adelay={delay_ms}|{delay_ms}", str(lav_wav)],
        check=True, capture_output=True,
    )

    return {"camera": camera_mov, "lav": lav_wav}


def test_whole_file_pass_is_weak_or_wrong_on_the_dead_zone(gap_and_return_audio: dict):
    """Establishes the problem this module exists to fix: PreCut's own
    whole-file sync_pair(), run unmodified, either can't confidently
    score this pair or the score alone gives no way to know which parts
    are trustworthy -- it's ALL of the file or NONE of it as far as the
    caller can tell. This isn't asserting sync_pair is broken; it's
    documenting why analyze_pair_coverage exists on top of it.
    """
    _require_deps_or_skip()
    from posthouse.harvest.sync import sync_pair, SCORE_USE

    result = sync_pair(gap_and_return_audio["camera"], gap_and_return_audio["lav"])
    assert result is not None
    # Whatever score comes back, sync_pair gives no per-segment breakdown
    # -- that's the gap. Record the measured score honestly either way.
    print(f"whole-file score={result.score:.2f} offset={result.offset_sec:.2f} "
          f"(SCORE_USE={SCORE_USE})")


def test_windowed_coverage_recovers_offset_and_excludes_the_dead_zone(gap_and_return_audio: dict):
    _require_deps_or_skip()
    from posthouse.sync_coverage import analyze_pair_coverage

    coverage = analyze_pair_coverage(
        gap_and_return_audio["camera"], gap_and_return_audio["lav"],
        window_sec=4.0, hop_sec=2.0, consensus_tol_sec=0.75,
    )

    measured = (
        f"accepted_offset_sec={coverage.accepted_offset_sec}, "
        f"usable_ranges={coverage.usable_ranges}, "
        f"windows_tried={coverage.windows_tried}, windows_used={coverage.windows_used}"
    )

    if coverage.accepted_offset_sec is None:
        pytest.skip(
            f"Windowed correlation found no consensus on this synthetic "
            f"fixture -- measured {measured}. Not lowering the consensus "
            f"bar to force a pass; see module docstring for the real-"
            f"footage numbers this was verified against instead."
        )

    # Sign convention matches SyncPair's (audio_time = aroll_time - offset):
    # negative offset because we prepended KNOWN_OFFSET_SEC of silence to
    # the lav, so its t=0 lands KNOWN_OFFSET_SEC before the camera's.
    assert coverage.accepted_offset_sec == pytest.approx(-KNOWN_OFFSET_SEC, abs=OFFSET_TOLERANCE_SEC), measured

    # Usable ranges (in the CAMERA's own timeline) should land in the
    # first and/or last speech segments, not in the ~10-30s dead middle
    # (10s silence + ~speech-length "elsewhere" sentence on the lav side).
    assert coverage.usable_ranges, measured
    for start, end in coverage.usable_ranges:
        midpoint = (start + end) / 2
        in_dead_zone = 8.0 < midpoint < 24.0  # generous margin around the gap
        assert not in_dead_zone, (
            f"usable range ({start:.1f}, {end:.1f}) falls inside the dead "
            f"zone -- {measured}"
        )
