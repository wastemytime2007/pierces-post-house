"""fillers.py: every um and uh in a cut, for the whole-cut pass (Ryan, 2026-10-11: "where there's an um or uh cut that audio and footage out")."""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import fillers  # noqa: E402
import ops  # noqa: E402
import words as words_mod  # noqa: E402
from timeline import Cut, VideoClip  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    fillers._CACHE.clear()

    def voice(path, start, dur):                                                  # voiced sound all the way through
        n = int(dur * ops.SR)
        t = start + np.arange(n) / ops.SR
        return (0.2 * np.sin(2 * np.pi * 700.0 * t)).astype(np.float32)
    monkeypatch.setattr(ops, "_pcm", voice)


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 20.0)
    cut.video += [VideoClip(1, 0.0, 10.0, "/a.mp4", 100.0, 110.0), VideoClip(2, 10.0, 20.0, "/a.mp4", 110.0, 120.0)]
    return cut


def _prompted(monkeypatch, rows):
    monkeypatch.setattr(words_mod, "words_prompted", lambda path, start, dur: [words_mod.W(t, a, b) for t, a, b in rows])


def test_a_filler_is_found_by_both_passes_and_cut_with_a_short_margin(monkeypatch):
    _prompted(monkeypatch, [("So", 103.0, 103.3), ("um,", 104.0, 104.4), ("we", 104.6, 104.8)])
    found = fillers.find(_cut())
    assert len(found) == 1 and found[0]["words"] == ["um"]
    assert abs(found[0]["start"] - 3.9) < 0.05 and abs(found[0]["end"] - 4.48) < 0.05      # 4.0-4.4 s with 0.10 s before and 0.08 s after


def test_a_filler_that_runs_over_a_cut_point_is_cut_across_it(monkeypatch):
    _prompted(monkeypatch, [("were,", 109.0, 109.5), ("um,", 109.8, 110.3), ("but", 110.6, 110.8)])    # the cut point is at source 110 s
    found = fillers.find(_cut())
    assert len(found) == 1 and found[0]["start"] < 10.0 < found[0]["end"]


def test_a_window_where_whisper_loops_has_no_fillers_taken_from_it(monkeypatch):
    rows = [("um,", 101.0 + 0.5 * i, 101.3 + 0.5 * i) for i in range(10)]                    # a loop: ten fillers in one window
    _prompted(monkeypatch, rows)
    assert fillers.find(_cut()) == []


def test_a_filler_over_silence_is_not_cut(monkeypatch):
    monkeypatch.setattr(ops, "_pcm", lambda path, start, dur: np.zeros(int(dur * ops.SR), dtype=np.float32) + 1e-5)
    _prompted(monkeypatch, [("um,", 104.0, 104.4)])
    assert fillers.find(_cut()) == []
