import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import conform_music as cm


def kick_track(path: Path, bpm=120.0, seconds=40.0, intro_beats=8, sr=44100, jitter=0.0):
    """Kicks on every beat: soft for the first `intro_beats`, then loud (the drop)."""
    y = np.zeros(int(seconds * sr), dtype="float32")
    ibi = 60.0 / bpm
    rng = np.random.default_rng(0)
    k = 0
    while 0.1 + k * ibi < seconds - 0.5:
        i = int((0.1 + k * ibi + rng.uniform(-jitter, jitter)) * sr)
        n = int(0.12 * sr)
        tt = np.arange(n) / sr
        y[i:i + n] += ((0.15 if k < intro_beats else 0.8) * np.sin(2 * np.pi * (90 - 300 * tt) * tt) * np.exp(-tt / 0.04)).astype("float32")
        h = i + int(0.5 * ibi * sr)                                                  # a hi-hat on the half-beat, as a real groove has
        m = int(0.03 * sr)
        if h + m < len(y):
            y[h:h + m] += (0.3 * rng.standard_normal(m) * np.exp(-np.arange(m) / (0.008 * sr))).astype("float32") * (0.0 if k < intro_beats else 1.0)
        k += 1
    sf.write(str(path), y, sr)
    return path


def test_the_beat_grid_and_the_drop_are_found_on_a_quantised_track(tmp_path):
    g = cm.fit_grid(kick_track(tmp_path / "k.wav"))
    assert abs(g["bpm"] - 120.0) < 0.5 and g["grid_err_ms"] < 20
    assert abs(cm.find_drop(g["strength"]) - 8) <= 1                                        # the soft intro is 8 beats, the groove starts there


def test_a_track_whose_beats_wander_is_refused_not_conformed(tmp_path):
    with pytest.raises(cm.ConformError, match="steady grid|fewer than 8"):
        cm.fit_grid(kick_track(tmp_path / "j.wav", jitter=0.12))


def test_the_plan_puts_whole_or_half_beats_between_events_with_small_stretches():
    p = cm.plan([1.0, 1.5, 2.55, 4.85, 7.35], ibi=0.5)
    assert len(p["segments"]) == 4 and p["worst_stretch"] <= cm.MAX_STRETCH and p["over_limit"] == []
    assert all(s["half_beats"] >= 1 for s in p["segments"])
    # events a beat apart at the track's tempo need no stretch at all
    q = cm.plan([0.0, 0.5, 1.0, 2.0], ibi=0.5, search=(1.0, 1.0))
    assert [s["half_beats"] for s in q["segments"]] == [2, 2, 4] and q["worst_stretch"] < 1e-9


def test_a_gap_that_cannot_be_filled_without_a_big_stretch_is_reported_not_hidden():
    p = cm.plan([0.0, 0.3, 5.0], ibi=0.5, search=(1.0, 1.0))                                # 0.3 s is a bit over one half-beat of 0.25 s: 20% off
    assert p["over_limit"] == [0] and p["worst_stretch"] > cm.MAX_STRETCH


def test_after_conforming_a_beat_lands_on_every_event_and_the_music_stops_after_the_last(tmp_path):
    events = [1.0, 1.55, 2.65, 4.9, 7.3]
    out = tmp_path / "c.wav"
    rep = cm.conform(kick_track(tmp_path / "k.wav"), events, total=12.0, out=out)
    assert all(h["offset_ms"] is not None and abs(h["offset_ms"]) <= 33 for h in rep["hits"]), rep["hits"]       # within one video frame (33 ms)
    x, sr = sf.read(str(out))
    assert abs(len(x) / sr - 12.0) < 0.01
    quiet = np.abs(x[int(9.5 * sr):]).max()
    assert quiet < 0.01                                                                     # it stopped: the last scene is bare
    assert np.abs(x[int(0.2 * sr):int(0.8 * sr)]).max() < 0.01                              # nothing before the first event


def test_events_within_a_few_frames_of_each_other_are_one_event_and_the_merge_is_reported():
    kept, dropped = cm.merge_close([1.164, 1.168, 1.7, 1.75, 4.0])
    assert kept == [1.164, 1.7, 4.0] and dropped == [(1.168, 1.164), (1.75, 1.7)]
    p = cm.plan([1.164, 1.168, 1.73, 2.29, 7.24], ibi=0.545)
    assert p["events"] == [1.164, 1.73, 2.29, 7.24] and p["merged"] == [(1.168, 1.164)]
    assert all(s["to"] - s["from"] >= cm.MIN_EVENT_GAP for s in p["segments"])              # no segment squeezes a half-beat into a few milliseconds
    assert p["worst_stretch"] < 0.5


def test_the_run_tail_starts_with_the_reel_plays_to_the_end_and_fades_only_over_the_last_second_and_a_half(tmp_path):
    events = [0.0, 1.0, 2.55, 5.0]
    out = tmp_path / "r.wav"
    rep = cm.conform(kick_track(tmp_path / "k.wav", intro_beats=0, seconds=40.0), events, total=9.0, out=out, tail="run")
    assert all(h["offset_ms"] is not None and abs(h["offset_ms"]) <= 33 for h in rep["hits"]), rep["hits"]
    x, sr = sf.read(str(out))
    assert abs(len(x) / sr - 9.0) < 0.01
    rms = lambda a, b: float(np.sqrt((x[int(a * sr):int(b * sr)] ** 2).mean()))                # noqa: E731
    assert rms(0.0, 0.5) > 0.05                                                              # the music is already playing in the first half second
    assert rms(6.0, 7.0) > 0.05 and rms(7.0, 7.4) > 0.5 * rms(6.0, 7.0)                       # it plays on well past the last event and up to the fade
    assert rms(8.6, 9.0) < 0.35 * rms(6.0, 7.0)                                              # and fades out at the very end
