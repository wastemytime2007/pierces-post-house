import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "review_loop"))
import sync_audio as sa


def _speechlike(seconds, seed):
    """Band-limited noise with a speech-like burst pattern: enough structure for correlation, no lucky repetition."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sa.SR)
    x = rng.standard_normal(n)
    env = np.repeat(rng.random(n // 400 + 1), 400)[:n] ** 2
    return (x * env).astype(np.float32)


def _find(cam, moment):
    n = sa.next_fast(len(cam) + len(moment))
    return sa.gcc_phat(sa._fft.rfft(cam.astype(np.float32), n), n, moment)


def test_a_moment_is_found_at_the_right_offset_even_when_the_other_microphone_sounds_different():
    cam = _speechlike(120, 1)
    start = 47.35
    i = int(start * sa.SR)
    moment = cam[i:i + 20 * sa.SR].copy()
    rng = np.random.default_rng(2)
    moment = (0.3 * moment + 0.05 * rng.standard_normal(len(moment))).astype(np.float32)              # different level, plus its own noise
    moment = np.convolve(moment, np.array([1.0, 0.6, 0.3, 0.1], dtype=np.float32), mode="same")        # and a different room
    off, score = _find(cam, moment)
    assert off == pytest.approx(start, abs=0.01) and score > sa.MIN_SCORE


def test_unrelated_audio_scores_low_so_it_is_not_synced():
    cam = _speechlike(120, 3)
    moment = _speechlike(20, 4)
    off, score = _find(cam, moment)
    assert score < sa.MIN_SCORE


def test_the_decision_needs_a_strong_peak_and_a_clear_winner_between_cameras():
    assert sa.decide({"A": (10.0, 30.0), "B": (3.0, 5.0)})["synced"] is True
    weak = sa.decide({"A": (10.0, 5.0), "B": (3.0, 4.0)})
    assert weak["synced"] is False and "too weak" in weak["reason"]
    tie = sa.decide({"A": (10.0, 30.0), "B": (3.0, 28.0)})
    assert tie["synced"] is False and "about equally" in tie["reason"]
    assert sa.decide({})["synced"] is False


def test_wav_loading_round_trips(tmp_path):
    x = (np.sin(np.linspace(0, 200, 8000)) * 0.5)
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    y = sa.load_wav(p)
    assert len(y) == 8000 and np.allclose(x, y, atol=1e-3)
