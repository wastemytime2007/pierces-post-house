import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_audio as ma
import reference_music as rm
import tone_match as tm


def _dark_track(path: Path) -> Path:
    """A strong 100 Hz bass with a weaker 900 Hz part: dark, but with something above to bring forward."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=100:sample_rate=48000:duration=6", "-f", "lavfi", "-i", "sine=frequency=900:sample_rate=48000:duration=6",
                    "-filter_complex", "[0:a]volume=1.0[a];[1:a]volume=0.25[b];[a][b]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo", str(path)], check=True)
    return path


def test_a_dark_track_is_cut_only_as_far_as_needed_and_the_result_is_re_measured(tmp_path):
    src = _dark_track(tmp_path / "dark.wav")
    base = rm.analyze(src)["tone_centre_hz"]
    ref = {"tone_centre_hz": round(base * 1.3)}                                           # a reference 1.3 times brighter
    out = tmp_path / "m.wav"
    r = tm.match_tone(src, ref, out, target=0.85, analyse=rm.analyze)
    assert r["cut_db"] > 0 and r["tone_after"] > r["tone_before"] and r["ratio_after"] >= 0.85
    assert rm.analyze(out)["tone_centre_hz"] == r["tone_after"]                           # what it says is what the file measures
    step_back = tmp_path / "back.wav"
    tm.eq_copy(src, step_back, r["cut_db"] - 1.0)
    assert rm.analyze(step_back)["tone_centre_hz"] < ref["tone_centre_hz"] * 0.85         # one dB less would not have been enough: it is the least cut that works


def test_a_track_already_bright_enough_is_not_touched(tmp_path):
    src = _dark_track(tmp_path / "dark.wav")
    base = rm.analyze(src)["tone_centre_hz"]
    r = tm.match_tone(src, {"tone_centre_hz": base}, tmp_path / "m.wav", analyse=rm.analyze)
    assert r["cut_db"] == 0.0 and r["tone_after"] == r["tone_before"]


def test_a_track_too_dark_for_the_allowed_cut_is_refused_not_pushed_further(tmp_path):
    src = _dark_track(tmp_path / "dark.wav")
    base = rm.analyze(src)["tone_centre_hz"]
    with pytest.raises(ma.AudioError, match="use a different take"):
        tm.match_tone(src, {"tone_centre_hz": base * 20}, tmp_path / "m.wav", max_cut_db=3.0, analyse=rm.analyze)
