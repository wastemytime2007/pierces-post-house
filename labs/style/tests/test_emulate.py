"""Hermetic tests for emulating a reference video: the aspect choice, the crop, the colour LUT, the pause targets,
and a full run on synthetic videos of known construction. ElevenLabs and Whisper are faked."""
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audio"))

import emulate as em  # noqa: E402
import style_profile as sp  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _vid(path: Path, size: str, vf: str = "", secs: float = 12.0, audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", str(secs), "-i", f"testsrc2=s={size}:r=30"]
    if audio:
        cmd += ["-f", "lavfi", "-t", str(secs), "-i", "sine=frequency=330:sample_rate=48000"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_every_aspect_is_emulated_unless_you_say_otherwise():
    assert em.pick_aspects() == em.ASPECTS and em.pick_aspects("all") == em.ASPECTS
    assert em.pick_aspects("music,color") == ["music", "color"]
    assert em.pick_aspects(["color", "music"]) == ["music", "color"]                 # in the fixed working order
    assert "music" not in em.pick_aspects("all", ["music"]) and len(em.pick_aspects("all", ["music"])) == len(em.ASPECTS) - 1
    with pytest.raises(em.EmulateError, match="unknown aspect"):
        em.pick_aspects("colour")


def test_the_crop_is_the_largest_window_of_the_target_shape_and_follows_the_focus():
    assert em.crop_box(960, 540, "vertical") == (304, 540, 328, 0)
    assert em.crop_box(960, 540, "vertical", 0.0)[2] == 0 and em.crop_box(960, 540, "vertical", 1.0)[2] == 960 - 304
    cw, ch, x, y = em.crop_box(1080, 1920, "horizontal")
    assert (cw, x) == (1080, 0) and abs(cw / ch - 16 / 9) < 0.01 and y == round((1920 - ch) / 2)
    assert em.crop_box(3840, 2160, "vertical")[0] % 2 == 0


def test_lab_conversion_round_trips_and_white_is_white():
    rng = np.random.default_rng(3)
    rgb = rng.random((500, 3))
    assert np.abs(em.lab_to_srgb(em.srgb_to_lab(rgb)) - rgb).max() < 1e-3
    L = em.srgb_to_lab(np.array([1.0, 1.0, 1.0]))
    assert abs(L[0] - 100) < 0.1 and abs(L[1]) < 0.1 and abs(L[2]) < 0.1


def test_the_lut_moves_our_colour_toward_the_reference_by_the_strength_asked_and_not_beyond():
    rng = np.random.default_rng(4)
    ours = rng.random((40, 20, 20, 3)) * 255 * 0.5 + 60                   # dull, mid
    ref = np.clip(rng.random((40, 20, 20, 3)) * 255 * 1.0 + 0, 0, 255) ** 1.0 * 0.9                   # brighter and wider
    rs, os_ = em.lab_stats(ref), em.lab_stats(ours)
    g = np.arange(33) / 32
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    grid = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    assert np.abs(em.transfer_lut(rs, os_, 0.0) - grid).max() < 1e-3                                  # strength 0 changes nothing
    assert np.abs(em.transfer_lut(os_, os_, 1.0) - grid).max() < 2e-2                                 # ours to ours changes nothing
    lut = em.transfer_lut(rs, os_, 0.7)

    def apply(fr):                                                                                    # nearest-node lookup is enough to see the direction
        idx = np.clip(np.rint(fr / 255 * 32).astype(int), 0, 32)
        return lut[idx[..., 2] * 33 * 33 + idx[..., 1] * 33 + idx[..., 0]] * 255
    after = em.lab_stats(apply(ours))
    assert abs(after["mean"][0] - rs["mean"][0]) < abs(os_["mean"][0] - rs["mean"][0])                # brightness moved toward the reference
    assert after["std"][0] > os_["std"][0]                                                            # and its spread


def test_the_cube_file_has_the_size_the_rows_and_red_changing_fastest(tmp_path):
    g = np.arange(3) / 2
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    lut = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    f = tmp_path / "x.cube"
    em.write_cube(f, lut, 3, "t")
    lines = [ln for ln in f.read_text().splitlines() if ln and ln[0].isdigit()]
    assert "LUT_3D_SIZE 3" in f.read_text() and len(lines) == 27
    assert lines[0] == "0.000000 0.000000 0.000000" and lines[1] == "0.500000 0.000000 0.000000" and lines[3] == "0.000000 0.500000 0.000000"


def test_pause_profiles_come_from_word_timing_and_only_unusually_long_gaps_are_targeted():
    ref = [(i * 0.5, i * 0.5 + 0.4) for i in range(40)]                                  # gaps of 0.1 s
    ours = [(i * 0.5, i * 0.5 + 0.4) for i in range(20)] + [(10.0 + 2.0 + i * 0.5, 12.4 + i * 0.5) for i in range(20)]      # one 2.1 s gap in the middle
    rp, op = em.gap_profile(ref, 20.0), em.gap_profile(ours, 25.0)
    assert rp["reliable"] and rp["p95_gap_sec"] <= 0.11 and op["longest_gap_sec"] > 2.0
    t = em.tighten_targets(ours, max(rp["p95_gap_sec"], em.MIN_TIGHTEN_SEC))
    assert len(t) == 1 and 10.0 < t[0]["at"] < 12.5 and t[0]["gap"] > 2.0
    assert em.gap_profile(ref[:5], 3.0)["reliable"] is False                              # too few words to call it a rhythm


def test_cuts_go_through_revise_and_report_what_was_applied_not_what_was_asked(tmp_path):
    ours = [(i * 0.5, i * 0.5 + 0.4) for i in range(20)] + [(12.0 + i * 0.5, 12.4 + i * 0.5) for i in range(20)]
    ref = [(i * 0.5, i * 0.5 + 0.4) for i in range(40)]
    seen = {}

    def fake_revise(xml, notes, ops, rdir):
        seen["ops"] = json.loads(Path(ops).read_text())
        seen["notes"] = json.loads(Path(notes).read_text())["notes"]
        (rdir / "changes.json").write_text(json.dumps({"items": [{"note": 1, "applied": True, "removed": [10.3, 11.9]}]}))
        _vid(rdir / "preview.mp4", "160x90")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    base = _vid(tmp_path / "ours.mp4", "160x90", secs=25.0)
    r = em.do_cuts({"words": ref, "duration": 20.0}, base, tmp_path / "x.xml", tmp_path / "cuts", words_of=lambda p: ours, revise=fake_revise)
    assert r["status"] == "PLANNED" and r["applied"] == 1 and r["removed_sec"] == 1.6
    assert [o["op"] for o in seen["ops"]] == ["tighten_pause"] and seen["notes"][0]["_style_generated"] is True       # flagged, never passed off as Ryan's note
    assert "more cuts per minute" in r["not_emulated"]
    bad = em.do_cuts({"words": ref, "duration": 20.0}, base, tmp_path / "x.xml", tmp_path / "cuts2", words_of=lambda p: ours,
                     revise=lambda *a: SimpleNamespace(returncode=1, stdout="", stderr="REFUSING"))
    assert bad["status"] == "FAILED" and bad["revised_preview"] is None                # a failed revise is never reported as emulated


def test_a_full_run_makes_our_horizontal_plain_cut_vertical_graded_and_under_new_music(tmp_path):
    ref = _vid(tmp_path / "ref.mp4", "90x160", "eq=brightness=-0.12:contrast=1.35:saturation=1.7")                # vertical, dark and punchy
    ours = _vid(tmp_path / "ours.mp4", "320x180")                                                                   # horizontal, plain
    calls = []

    def fake_generate(kind, prompt, seconds, cache, salt=""):
        calls.append((kind, prompt))
        cache.mkdir(parents=True, exist_ok=True)
        mp3 = cache / f"{len(calls)}.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=220:sample_rate=44100", str(mp3)], check=True)
        return mp3, {"cached": False, "prompt": prompt, "ms": int(seconds * 1000), "file": mp3.name, "salt": salt}
    res = em.emulate({"emulate": "all", "skip": ["cuts"]}, ref, tmp_path / "x.xml", ours, tmp_path / "out", words_of=lambda p: [], generate=fake_generate)
    A = res["aspects"]
    assert res["selected"] == [a for a in em.ASPECTS if a != "cuts"] and A["cuts"]["status"] == "NOT SELECTED"
    assert A["orientation"]["status"] == "EMULATED" and "vertical" in A["orientation"]["measured"]
    fp = res["emulated_profile"]
    assert fp["orientation"] == "vertical" and fp["height"] == 1080 and "Auto Reframe" in A["orientation"]["what"]                                                  # measured from the file, not assumed
    assert A["color"]["status"] == "EMULATED" and Path(A["color"]["cube"]).exists()
    assert sum(A["color"]["gap_after"][k] < A["color"]["gap_before"][k] for k in A["color"]["gap_before"]) >= 2
    assert A["music"]["status"] in ("EMULATED", "GENERATED, NOT CLOSE ENOUGH") and all(k == "music" for k, _ in calls) and calls
    assert A["music"]["voice_included"] is False and "no spoken words" in A["music"]["note"]                        # no words found: all of it is music
    for a in ("text", "graphics", "sfx"):
        assert A[a]["status"] == "MEASURED ONLY"
    assert subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", res["emulated_preview"]], capture_output=True, text=True).stdout.strip() == "audio"
    page = em.render_report(res, tmp_path / "out").read_text()
    assert all(em.PLAIN[a] in page for a in em.ASPECTS) and "MEASURED ONLY" in page and "NOT SELECTED" in page and 'src="emulated_preview.mp4"' in page


def test_picking_only_some_aspects_leaves_the_rest_untouched_and_a_dry_run_spends_nothing(tmp_path):
    ref = _vid(tmp_path / "ref.mp4", "90x160")
    ours = _vid(tmp_path / "ours.mp4", "320x180")
    res = em.emulate({"emulate": "music"}, ref, tmp_path / "x.xml", ours, tmp_path / "out", dry=True, words_of=lambda p: [], generate=lambda *a, **k: pytest.fail("generated in a dry run"))
    assert res["aspects"]["music"]["status"] == "PLANNED" and "prompt" in res["aspects"]["music"]
    assert all(res["aspects"][a]["status"] == "NOT SELECTED" for a in em.ASPECTS if a != "music") and "emulated_preview" not in res


def test_a_music_track_you_give_wins_over_the_reference_videos_music(tmp_path):
    track = tmp_path / "mine.wav"
    with wave.open(str(track), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes((np.sin(np.arange(22050 * 4) * 0.05) * 9000).astype("<i2").tobytes())
    ref = _vid(tmp_path / "ref.mp4", "90x160")
    p, src = em.music_source({"from": str(track)}, ref, tmp_path / "o", words_of=lambda w: pytest.fail("the reference video was read"))
    assert p == track and "the music track you gave" in src["source"]
    with pytest.raises(em.EmulateError, match="is not there"):
        em.music_source({"from": str(tmp_path / "nope.mp3")}, ref, tmp_path / "o")


def test_a_colour_change_that_does_not_bring_us_closer_is_not_called_emulated(tmp_path):
    """Negative control: strength 0 is an identity LUT, so the measured gap cannot shrink and the status must say so."""
    ref = _vid(tmp_path / "ref.mp4", "90x160", "eq=brightness=-0.12:contrast=1.35:saturation=1.7")
    ours = _vid(tmp_path / "ours.mp4", "320x180")
    res = em.emulate({"emulate": "color", "color_strength": 0.0}, ref, tmp_path / "x.xml", ours, tmp_path / "out", words_of=lambda p: [])
    assert res["aspects"]["color"]["status"] == "TRIED, NOT CLOSER"
