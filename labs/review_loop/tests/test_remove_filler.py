"""'REMOVE THIS UM' (Ryan, 2026-10-10): the editor refused it with "No operation finds a filler word by listening". remove_filler finds the
filler word in the transcript near the note's moment and cuts its measured span."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
import words as words_mod  # noqa: E402
from timeline import AudioClip, Cut, VideoClip  # noqa: E402


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 10.0)
    cut.video.append(VideoClip(1, 0.0, 10.0, "/a.mp4", 100.0, 110.0))
    cut.audio.append(AudioClip(0.0, 10.0, "/lav.wav", 100.0, 110.0, 1))
    return cut


def _fake(monkeypatch, rows):
    def fake(path, start, dur):
        return [words_mod.W(t, start + a, start + b) for t, a, b in rows]
    monkeypatch.setattr(words_mod, "words_in", fake)


def _note(text, at):
    return {"note": 1, "text": text, "timeline_sec": at, "clip": 1, "shapes": []}


def test_remove_this_um_cuts_the_measured_um(monkeypatch):
    _fake(monkeypatch, [("So", 0.5, 0.8), ("we", 0.8, 0.9), ("um", 1.2, 1.5), ("bought", 1.6, 2.0)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note('REMOVE THIS "UM"', 1.3)], _cut())
    assert out[0]["op"] == "remove_range"
    assert abs(out[0]["start"] - 1.2) < 0.05 and abs(out[0]["end"] - 1.5) < 0.05


def test_a_filler_word_far_from_the_note_is_not_cut(monkeypatch):
    _fake(monkeypatch, [("um", 8.0, 8.3)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("remove this um", 1.0)], _cut())
    assert out[0]["op"] == "unsupported" and "no filler word" in out[0]["reason"]


def test_a_note_that_does_not_ask_for_a_filler_is_refused(monkeypatch):
    _fake(monkeypatch, [("um", 1.2, 1.5)])
    out = opsmod.validate([{"note": 1, "op": "remove_filler"}], [_note("the picture is too dark", 1.3)], _cut())
    assert out[0]["op"] == "unsupported"
