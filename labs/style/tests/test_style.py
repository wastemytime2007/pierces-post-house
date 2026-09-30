"""Hermetic tests for the style profile: measurements checked against videos of known construction."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audio"))

import style_profile as sp  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
COLORS = ["0x101820", "0xe8e0d0", "0x203050", "0xf0d880", "0x402060", "0xc0f0e0"]      # alternating dark and light: the scene score is luma-weighted


def _video(path: Path, segs: list[tuple[float, str]], size="320x180", vol: float | None = 0.5, extra_vf: str = "", gated: bool = False) -> Path:
    """Shots of flat colour, one after another (every change is a cut), with a tone under it."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for secs, col in segs:
        cmd += ["-f", "lavfi", "-t", str(secs), "-i", f"color=c={col}:s={size}:r=30"]
    n = len(segs)
    if vol is not None:
        af = f"volume='if(lt(mod(t,3),2),{vol},0)':eval=frame" if gated else f"volume={vol}"         # gated: 2s of tone then 1s of silence, repeating
        cmd += ["-f", "lavfi", "-t", str(sum(s for s, _ in segs)), "-i", f"sine=frequency=330:sample_rate=48000,{af}"]
    g = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0" + (f",{extra_vf}" if extra_vf else "") + "[v]"
    cmd += ["-filter_complex", g, "-map", "[v]"] + (["-map", f"{n}:a", "-c:a", "aac"] if vol is not None else [])
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _shots(n: int, each: float) -> list[tuple[float, str]]:
    return [(each, COLORS[i % len(COLORS)]) for i in range(n)]


def test_cuts_shots_and_pace_are_measured(tmp_path):
    fast = sp.profile(_video(tmp_path / "fast.mp4", _shots(12, 2.0)))
    slow = sp.profile(_video(tmp_path / "slow.mp4", _shots(4, 6.0)))
    assert fast["rhythm"]["cuts"] == 11 and fast["rhythm"]["cuts_per_min"] == pytest.approx(27.5, abs=1.0) and fast["rhythm"]["avg_shot_sec"] == pytest.approx(2.0, abs=0.1)
    assert slow["rhythm"]["cuts"] == 3 and slow["rhythm"]["avg_shot_sec"] == pytest.approx(6.0, abs=0.2) and slow["rhythm"]["first_cut_sec"] == pytest.approx(6.0, abs=0.1)
    assert fast["rhythm"]["pace_trend"] == "steady"


def test_a_pace_that_speeds_up_is_called_accelerating(tmp_path):
    segs = [(4.0, COLORS[0]), (4.0, COLORS[1]), (4.0, COLORS[2])] + [(1.0, COLORS[i % 6]) for i in range(12)]
    assert sp.profile(_video(tmp_path / "a.mp4", segs))["rhythm"]["pace_trend"] == "accelerating"


def test_orientation_size_and_audio_are_read(tmp_path):
    v = sp.profile(_video(tmp_path / "v.mp4", _shots(3, 3.0), size="180x320"))
    h = sp.profile(_video(tmp_path / "h.mp4", _shots(3, 3.0), vol=None))
    assert v["orientation"] == "vertical" and v["width"] == 180 and v["has_audio"] and h["orientation"] == "horizontal" and not h["has_audio"] and h["audio"]["lufs"] is None


def test_brightness_colour_and_the_palette_reflect_the_picture(tmp_path):
    dark = sp.profile(_video(tmp_path / "d.mp4", [(6.0, "0x101018")]))
    bright = sp.profile(_video(tmp_path / "b.mp4", [(6.0, "0xe8e8d8")]))
    red = sp.profile(_video(tmp_path / "r.mp4", [(6.0, "0xd02020")]))
    assert bright["picture"]["brightness"] > dark["picture"]["brightness"] + 0.5
    assert red["picture"]["saturation"] > 0.7 and red["picture"]["palette"][0].startswith("#e0") and int(red["picture"]["palette"][0][3:5], 16) < 100


def test_motion_energy_separates_a_moving_picture_from_a_still_one(tmp_path):
    still = sp.profile(_video(tmp_path / "s.mp4", [(6.0, "0x406080")]))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", "6", "-i", "testsrc2=size=320x180:rate=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "m.mp4")]
    subprocess.run(cmd, check=True)
    moving = sp.profile(tmp_path / "m.mp4")
    assert moving["picture"]["motion_energy"] > 5 * max(still["picture"]["motion_energy"], 1e-4)


def test_loudness_is_measured(tmp_path):
    loud = sp.profile(_video(tmp_path / "l.mp4", _shots(2, 4.0), vol=0.5))
    quiet = sp.profile(_video(tmp_path / "q.mp4", _shots(2, 4.0), vol=0.05))
    assert loud["audio"]["lufs"] - quiet["audio"]["lufs"] == pytest.approx(20.0, abs=2.0)


STRIPES = "geq=lum='if(between(Y,H*0.66,H*0.9),if(mod(floor(X/4),2),235,20),lum(X,Y))':cb=128:cr=128"


def test_text_like_detail_in_the_lower_third_is_picked_up(tmp_path):
    plain = sp.profile(_video(tmp_path / "p.mp4", [(6.0, "0x406080")]))
    banded = sp.profile(_video(tmp_path / "c.mp4", [(6.0, "0x406080")], extra_vf=STRIPES))
    assert banded["picture"]["lower_third_activity"] > 3 * max(plain["picture"]["lower_third_activity"], 0.3) and banded["picture"]["lower_third_heavy_frames"] > 0.9


def _pair(tmp_path, ref_segs, our_segs, **kw):
    return sp.profile(_video(tmp_path / "ref.mp4", ref_segs, **kw.get("ref", {}))), sp.profile(_video(tmp_path / "our.mp4", our_segs, **kw.get("our", {})))


def test_identical_videos_are_close_on_everything_and_suggest_nothing(tmp_path):
    a = sp.profile(_video(tmp_path / "a.mp4", _shots(6, 3.0)))
    c = sp.compare(a, a)
    assert c["suggested_notes"] == [] and all(r["verdict"] in ("CLOSE", "SAME", "UNRELIABLE") for r in c["rows"])
    assert {r["metric"] for r in c["rows"] if r["verdict"] == "UNRELIABLE"} <= {"pauses per minute", "longest pause (s)"}     # a continuous tone has no quiet floor to find pauses in


def test_a_slower_cut_is_told_to_cut_more_and_hold_shots_shorter_in_the_right_direction(tmp_path):
    ref, ours = _pair(tmp_path, _shots(12, 2.0), _shots(4, 6.0))
    c = sp.compare(ref, ours)
    by = {r["metric"]: r for r in c["rows"]}
    assert by["visible cuts per minute"]["verdict"] == "OURS LOWER" and by["average visible shot (s)"]["verdict"] == "OURS HIGHER"
    text = " ".join(n["suggestion"] for n in c["suggested_notes"])
    assert "More cuts would bring the pace closer" in text and "Shorter shots would match it" in text
    flipped = sp.compare(ours, ref)
    text2 = " ".join(n["suggestion"] for n in flipped["suggested_notes"])
    assert "Fewer cuts would bring the pace closer" in text2 and "Longer shots would match it" in text2


def test_a_louder_reference_is_told_to_bring_the_cut_up(tmp_path):
    ref, ours = _pair(tmp_path, _shots(4, 3.0), _shots(4, 3.0), ref={"vol": 0.5}, our={"vol": 0.05})
    c = sp.compare(ref, ours)
    assert any("It could be brought up." in n["suggestion"] for n in c["suggested_notes"])


def test_unlike_formats_are_flagged_and_a_missing_audio_track_is_not_measured_not_failed(tmp_path):
    ref, ours = _pair(tmp_path, _shots(4, 3.0), _shots(4, 3.0), ref={"size": "180x320"}, our={"vol": None})
    c = sp.compare(ref, ours)
    by = {r["metric"]: r for r in c["rows"]}
    assert by["orientation"]["verdict"] == "DIFFERENT" and "unlike formats" in by["orientation"]["note"]
    assert by["loudness (LUFS)"]["verdict"] == "NOT MEASURED" and by["loudness range (LU)"]["verdict"] == "NOT MEASURED"
    assert all("loudness" not in n["metric"] for n in c["suggested_notes"])


def test_the_report_and_json_are_written_and_say_nothing_was_changed(tmp_path):
    ref = _video(tmp_path / "ref.mp4", _shots(12, 2.0))
    our = _video(tmp_path / "our.mp4", _shots(4, 6.0))
    out = tmp_path / "out"
    p = subprocess.run([sys.executable, str(HERE / "style_profile.py"), "--reference", str(ref), "--ours", str(our), "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    page = (out / "style_report.html").read_text()
    assert "nothing was changed" in page and "Measured differences" in page and page.count("data:image/jpeg;base64,") >= 10 and "Not measured" in page
    data = json.loads((out / "style.json").read_text())
    assert data["comparison"]["suggested_notes"] and data["reference"]["rhythm"]["shots"] == 12


def test_an_unreadable_video_is_refused(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    p = subprocess.run([sys.executable, str(HERE / "style_profile.py"), "--reference", str(bad), "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert p.returncode == 1 and "could not read a video" in p.stderr


def test_pauses_are_counted_and_a_continuous_tone_is_not_trusted_for_them(tmp_path):
    gated = sp.profile(_video(tmp_path / "g.mp4", _shots(2, 12.0), gated=True))
    steady = sp.profile(_video(tmp_path / "s.mp4", _shots(2, 12.0)))
    assert gated["audio"]["pauses_reliable"] and gated["audio"]["pauses_per_min"] == pytest.approx(20.0, abs=3.0) and gated["audio"]["longest_pause_sec"] == pytest.approx(1.0, abs=0.15)
    assert not steady["audio"]["pauses_reliable"]


def test_how_many_of_our_real_cuts_were_seen_is_reported_and_low_recall_makes_the_pace_rows_unreliable(tmp_path):
    # the picture changes only at 6s and 12s; the timeline says there were cuts every 3s (jump cuts the picture cannot see)
    ours = sp.profile(_video(tmp_path / "o.mp4", _shots(4, 6.0)), true_cuts=[3.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0])
    assert ours["rhythm"]["actual_cuts"] == 7 and ours["rhythm"]["actual_cuts_per_min"] == pytest.approx(17.5, abs=0.3) and ours["rhythm"]["detection_recall"] == pytest.approx(3 / 7, abs=0.01)
    ref = sp.profile(_video(tmp_path / "r.mp4", _shots(12, 2.0)))
    c = sp.compare(ref, ours)
    by = {r["metric"]: r for r in c["rows"]}
    assert by["visible cuts per minute"]["verdict"] == "UNRELIABLE" and "found only 43%" in by["visible cuts per minute"]["note"]
    assert by["average visible shot (s)"]["verdict"] == "UNRELIABLE" and by["pace trend"]["verdict"] == "UNRELIABLE"
    assert not any("cuts" in n["metric"] for n in c["suggested_notes"])                        # no pace advice is given from a measure known to miss this cut's cuts
    assert by["actual cuts per minute (ours, from the timeline)"]["ours"] == pytest.approx(17.5, abs=0.3)


def test_full_recall_keeps_the_pace_rows_reliable(tmp_path):
    ours = sp.profile(_video(tmp_path / "o.mp4", _shots(4, 6.0)), true_cuts=[6.0, 12.0, 18.0])
    assert ours["rhythm"]["detection_recall"] == 1.0
    c = sp.compare(sp.profile(_video(tmp_path / "r.mp4", _shots(12, 2.0))), ours)
    assert {r["metric"]: r["verdict"] for r in c["rows"]}["visible cuts per minute"] == "OURS LOWER"


def test_pause_differences_give_a_suggestion_in_the_right_direction(tmp_path):
    tight = sp.profile(_video(tmp_path / "t.mp4", _shots(2, 12.0), vol=0.5))
    loose = sp.profile(_video(tmp_path / "l.mp4", _shots(2, 12.0), gated=True))
    tight["audio"].update({"pauses_reliable": True, "pauses_per_min": 2.0, "longest_pause_sec": 0.4})         # as a tightly edited reference with a quiet floor would measure
    c = sp.compare(tight, loose)
    text = " ".join(n["suggestion"] for n in c["suggested_notes"])
    assert "Tightening more of the pauses would match it." in text
