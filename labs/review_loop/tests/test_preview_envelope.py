"""The sound of a cut, measured from its own rendered preview, for the waveform lane under the picture.

Before this, the envelope was only written when a cut had been bleeped, so no review page on Ryan's disk had one (checked, 2026-10-22: 0 of them) and the lane would never have drawn.
"""
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from build_review import preview_envelope  # noqa: E402

pytest.importorskip("numpy")


def _make(path: Path, seconds: float = 2.0, loud: bool = True) -> Path:
    """A tiny video with a tone (or silence), so nothing here depends on real footage."""
    src = f"sine=frequency=440:duration={seconds}" if loud else f"anullsrc=r=44100:cl=mono:d={seconds}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}",
                    "-f", "lavfi", "-i", src, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def test_a_hundred_points_a_second_and_loud_sound_reads_loud(tmp_path):
    env = preview_envelope(_make(tmp_path / "tone.mp4", 2.0))
    assert env is not None
    assert 180 <= len(env) <= 220                      # 100 a second, give or take the encoder's own padding
    assert all(0 <= x <= 100 for x in env)
    assert max(env) > 60                               # a 440 Hz tone is not quiet


def test_silence_reads_quiet(tmp_path):
    env = preview_envelope(_make(tmp_path / "quiet.mp4", 2.0, loud=False))
    assert env is None or max(env) < 10


def test_a_missing_or_unreadable_preview_never_fails_a_build(tmp_path):
    assert preview_envelope(tmp_path / "nope.mp4") is None
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    assert preview_envelope(broken) is None


def test_a_video_with_no_sound_at_all_gives_nothing_rather_than_raising(tmp_path):
    p = tmp_path / "mute.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True, capture_output=True, timeout=120)
    assert preview_envelope(p) is None
