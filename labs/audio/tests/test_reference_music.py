"""Hermetic tests for matching music to a reference: measurement, description, closeness and take-picking.
Synthetic music of known tempo and character; ElevenLabs is faked."""
import shutil
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))

import make_audio as ma  # noqa: E402
import reference_music as rm  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
SR = 44100


def _track(path: Path, bpm: float, secs: float = 24.0, hz: float = 440.0, vol=None, hats: bool = False, seed: int = 0) -> Path:
    """A click-and-tone track: a short decaying tone on every beat (and optionally hi-hat noise on the off-beats)."""
    rng = np.random.default_rng(seed)
    n = int(secs * SR)
    x = np.zeros(n)
    beat = 60 / bpm
    t = np.arange(int(0.12 * SR)) / SR
    tone = np.sin(2 * np.pi * hz * t) * np.exp(-t * 35)
    k = 0
    while k * beat < secs - 0.15:
        i = int(k * beat * SR)
        x[i:i + len(tone)] += tone
        if hats:
            j = int((k + 0.5) * beat * SR)
            x[j:j + 2000] += rng.standard_normal(min(2000, n - j)) * np.exp(-np.arange(min(2000, n - j)) / 400) * 0.4
        k += 1
    if vol is not None:
        x *= vol(np.arange(n) / SR)
    x = np.clip(x * 0.5, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


@pytest.mark.parametrize("bpm", [80, 100, 120, 140])
def test_tempo_is_measured_from_the_audio(tmp_path, bpm):
    f = rm.analyze(_track(tmp_path / "t.wav", bpm))
    assert f["bpm"] == pytest.approx(bpm, rel=0.03), f


def test_brightness_and_rhythmic_density_order_correctly(tmp_path):
    dark = rm.analyze(_track(tmp_path / "d.wav", 90, hz=220))
    bright = rm.analyze(_track(tmp_path / "b.wav", 90, hz=4500))
    assert bright["tone_centre_hz"] > 2 * dark["tone_centre_hz"]
    sparse = rm.analyze(_track(tmp_path / "s.wav", 60))
    busy = rm.analyze(_track(tmp_path / "y.wav", 150))
    assert busy["onsets_per_sec"] > 1.8 * sparse["onsets_per_sec"]


def test_dynamics_spread_separates_steady_from_swelling_music(tmp_path):
    steady = rm.analyze(_track(tmp_path / "a.wav", 100))
    swell = rm.analyze(_track(tmp_path / "b.wav", 100, vol=lambda t: 0.15 + 0.85 * (np.sin(2 * np.pi * t / 8) > 0)))
    assert swell["dynamic_spread_db"] > steady["dynamic_spread_db"] + 6


def test_an_unreadable_or_tiny_file_is_refused(tmp_path):
    tiny = tmp_path / "tiny.wav"
    with wave.open(str(tiny), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(b"\x00\x00" * 1000)
    with pytest.raises(rm.ReferenceError, match="under 3 seconds"):
        rm.analyze(tiny)
    with pytest.raises(rm.ReferenceError):
        rm.analyze(tmp_path / "missing.wav")


FEATS = {"bpm": 128.0, "tone_centre_hz": 2000, "onsets_per_sec": 4.2, "dynamic_spread_db": 6.0, "stereo_width": 0.6, "low_end_fraction": 0.3}


def test_the_prompt_is_built_only_from_the_measurements_and_names_nothing():
    p = rm.build_prompt(FEATS)
    assert "about 128 BPM" in p and "upbeat and driving" in p and "bright, crisp tone" in p and "busy, rhythmic texture" in p
    assert "wide stereo" in p and "strong low end" in p and p.startswith("instrumental background music") and "no vocals" in p and "speaking voice" in p
    assert rm.build_prompt(FEATS) == p                                             # the same measurements always give the same words
    assert "speaking voice" not in rm.build_prompt(FEATS, for_voiceover=False)
    calm = rm.build_prompt({**FEATS, "bpm": 62, "tone_centre_hz": 80, "onsets_per_sec": 0.8, "dynamic_spread_db": 2.0, "stereo_width": 0.1, "low_end_fraction": 0.05})
    assert "slow and unhurried" in calm and "very dark, bass-heavy tone" in calm and "sparse, spacious rhythm" in calm and "very steady dynamics" in calm and "light low end" in calm


def test_closeness_needs_the_tempo_and_two_of_the_other_three_and_counts_half_or_double_time():
    assert rm.closeness(FEATS, FEATS)["passed"]
    assert rm.closeness(FEATS, {**FEATS, "bpm": 64.5})["passed"] and rm.closeness(FEATS, {**FEATS, "bpm": 255.0})["passed"]        # half and double time
    assert not rm.closeness(FEATS, {**FEATS, "bpm": 110.0})["passed"]                                                            # 14% off
    off = rm.closeness(FEATS, {**FEATS, "tone_centre_hz": 6500, "onsets_per_sec": 12.0})                                              # tempo right, tone and density wrong
    assert not off["passed"] and off["checks"]["tempo"] and not off["checks"]["brightness"] and not off["checks"]["rhythmic_density"]
    assert rm.closeness(FEATS, {**FEATS, "tone_centre_hz": 6500})["passed"]                                                          # one miss among the others is allowed
    no_dyn = rm.closeness(FEATS, {**FEATS, "dynamic_spread_db": 20}, dynamics=False)
    assert "dynamics" not in no_dyn["checks"] and no_dyn["passed"]


def test_takes_stop_at_the_first_close_one_and_each_take_is_a_distinct_generation(tmp_path):
    calls, feats_by_file = [], {}

    def fake_generate(kind, prompt, seconds, cache, salt=""):
        calls.append(salt)
        f = tmp_path / f"take{len(calls)}.mp3"
        f.write_bytes(b"x")
        feats_by_file[str(f)] = {**FEATS, "bpm": 90.0} if len(calls) < 3 else dict(FEATS)
        return f, {"cached": False, "prompt": prompt, "ms": int(seconds * 1000), "salt": salt, "file": f.name}
    mp3, res = rm.pick_best(FEATS, "p", 23.0, tmp_path, tries=4, generate=fake_generate, analyse=lambda p: feats_by_file[str(p)])
    assert calls == ["", "take2", "take3"] and res["chosen_take"] == 3 and res["passed"] and mp3.name == "take3.mp3"      # stopped at 3 of 4


def test_when_no_take_is_close_enough_the_closest_is_kept_and_the_failure_is_reported(tmp_path):
    bpms = iter([100.0, 92.0, 98.0])
    made = {}

    def fake_generate(kind, prompt, seconds, cache, salt=""):
        f = tmp_path / f"t{len(made)}.mp3"
        f.write_bytes(b"x")
        made[str(f)] = {**FEATS, "bpm": next(bpms)}
        return f, {"cached": False, "prompt": prompt, "ms": 1000, "salt": salt, "file": f.name}
    mp3, res = rm.pick_best(FEATS, "p", 23.0, tmp_path, tries=3, generate=fake_generate, analyse=lambda p: made[str(p)])
    assert not res["passed"] and len(res["takes"]) == 3 and res["chosen_take"] == 1 and mp3.name == "t0.mp3"               # 100 BPM is the nearest to 128


def test_a_different_salt_is_a_different_cached_generation_with_the_same_prompt(tmp_path, monkeypatch):
    sent = []

    class R:
        def read(self):
            return b"ID3" + b"x" * 2000
    monkeypatch.setattr(ma.urllib.request, "urlopen", lambda req, timeout=0: (sent.append(req.data), R())[1])
    monkeypatch.setattr(ma, "load_key", lambda: "k")
    a, _ = ma.generate("music", "same prompt", 10.0, tmp_path)
    b, _ = ma.generate("music", "same prompt", 10.0, tmp_path, salt="take2")
    c, ic = ma.generate("music", "same prompt", 10.0, tmp_path, salt="take2")
    assert a != b and b == c and ic["cached"] and len(sent) == 2 and sent[0] == sent[1]                                # two generations, identical request body
    assert ic["file"] == b.name


def test_a_library_is_ranked_by_closeness_with_its_measurements_cached(tmp_path):
    lib = tmp_path / "lib"
    for name, bpm in (("Slow One", 80), ("Match", 120), ("Fast One", 160)):
        (lib / name).mkdir(parents=True)
        _track(lib / name / f"{name}.wav", bpm)
    ref = rm.analyze(_track(tmp_path / "ref.wav", 121))
    cache = tmp_path / "m.json"
    rows = rm.rank_library(ref, lib, cache)
    assert [r["track"] for r in rows][0] == "Match" and rows[0]["closeness"]["passed"] and len(rows) == 3
    assert not rows[-1]["closeness"]["passed"] or rows[-1]["track"] != "Match"
    calls = []
    again = rm.rank_library(ref, lib, cache, analyse=lambda p: calls.append(p) or rm.analyze(p))                       # cached: nothing is measured again
    assert calls == [] and [r["track"] for r in again] == [r["track"] for r in rows]
    assert [r["track"] for r in rm.rank_library(ref, lib, cache, top=1)] == ["Match"]


def test_a_library_ignores_non_audio_files_and_skips_unreadable_tracks(tmp_path):
    lib = tmp_path / "lib"
    (lib / "A").mkdir(parents=True)
    (lib / "A" / "notes.txt").write_text("hi")
    (lib / "A" / ".hidden.wav").write_bytes(b"x")
    (lib / "B").mkdir()
    (lib / "B" / "broken.mp3").write_bytes(b"not audio at all")
    (lib / "C").mkdir()
    _track(lib / "C" / "good.wav", 100)
    rows = rm.rank_library(rm.analyze(_track(tmp_path / "ref.wav", 100)), lib)
    assert [r["track"] for r in rows] == ["C"]


def test_a_folder_with_several_versions_labels_each_one(tmp_path):
    lib = tmp_path / "lib"
    (lib / "Song").mkdir(parents=True)
    _track(lib / "Song" / "Song - full.wav", 100)
    _track(lib / "Song" / "Song - short.wav", 100, secs=12)
    rows = rm.rank_library(rm.analyze(_track(tmp_path / "ref.wav", 100)), lib)
    assert sorted(r["track"] for r in rows) == ["Song [Song - full]", "Song [Song - short]"]


def test_the_prompt_asks_for_the_tempo_folded_into_a_range_generators_hold(tmp_path):
    assert rm.prompt_tempo(174) == 87 and rm.prompt_tempo(300) == 75 and rm.prompt_tempo(60) == 60 and rm.prompt_tempo(100) == 100 and rm.prompt_tempo(140) == 140
    p = rm.build_prompt({**FEATS, "bpm": 174.0})
    assert "about 87 BPM" in p and "174" not in p                                     # the half-time equivalent; a measured half or double time still counts as a match
    assert rm.closeness({**FEATS, "bpm": 174.0}, {**FEATS, "bpm": 86.0})["checks"]["tempo"]


def test_a_quiet_fade_in_does_not_count_as_a_dynamic_swing(tmp_path):
    steady = rm.analyze(_track(tmp_path / "s.wav", 100, secs=24))
    faded = rm.analyze(_track(tmp_path / "f.wav", 100, secs=24, vol=lambda t: np.minimum(1.0, t / 3.0) * np.minimum(1.0, (24 - t) / 3.0)))
    assert abs(faded["dynamic_spread_db"] - steady["dynamic_spread_db"]) < 3.0          # head and tail windows are ignored


def test_a_few_quiet_bright_moments_do_not_drag_the_tone_up(tmp_path):
    dark = rm.analyze(_track(tmp_path / "d.wav", 90, hz=150, hats=False))
    sparse_hats = rm.analyze(_track(tmp_path / "h.wav", 90, hz=150, hats=True))
    bright = rm.analyze(_track(tmp_path / "b.wav", 90, hz=4500))
    assert sparse_hats["tone_centre_hz"] < 0.5 * bright["tone_centre_hz"] and sparse_hats["tone_centre_hz"] < 3 * dark["tone_centre_hz"]


def test_the_tone_words_step_up_with_where_the_energy_sits():
    tone = lambda hz: rm.describe({**FEATS, "tone_centre_hz": hz})["tone"]                     # noqa: E731
    assert [tone(h) for h in (60, 150, 300, 900, 2000, 5000)] == ["very dark, bass-heavy tone", "warm, bass-weighted tone", "warm tone", "balanced tone", "bright, crisp tone", "very bright, airy tone"]
