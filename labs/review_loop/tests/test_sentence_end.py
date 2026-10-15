"""A note that says a sentence is cut off, where the sound at the cut is only quiet: the editor finishes the sentence from the words.

Ryan, 2026-10-12/13 (typed notes, both refused): "This sentence gets cut off" -> "the audio is already quiet at the cut (-48 dB, room level), so nothing looks cut off", and "He starts with 'It doesnt
make sense to put' and then you cut him off before finishing what he's saying" -> "the sound has already decayed at the cut". A pause inside a sentence is quiet; the words are what say it is unfinished.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
import timeline  # noqa: E402


def _cut():
    v = [timeline.VideoClip(1, 0.0, 4.0, "/x/a.mp4", 100.0, 104.0), timeline.VideoClip(2, 4.0, 8.0, "/x/a.mp4", 200.0, 204.0)]
    a = [timeline.AudioClip(0.0, 4.0, "/x/lav.wav", 150.0, 154.0, 1), timeline.AudioClip(4.0, 8.0, "/x/lav.wav", 250.0, 254.0, 1)]
    return timeline.Cut("t", 30.0, 1080, 1920, 8.0, v, a)


def W(text, a, b):
    return opsmod.words_mod.W(text, a, b)


@pytest.fixture()
def quiet(monkeypatch):
    monkeypatch.setattr(opsmod.words_mod, "valley", lambda path, lo, hi: (lo + hi) / 2)
    monkeypatch.setattr(opsmod, "_pcm", lambda path, start, dur: np.full(int(8000 * dur), 0.001, dtype=np.float32))
    monkeypatch.setattr(opsmod, "level_db", lambda *a, **k: -45.0)


def test_a_sentence_stopped_at_a_pause_is_finished_from_the_words(monkeypatch, quiet):
    # lav clock: clip 1 ends at 154.0 s; "it doesn't make sense to put" then a pause, and the sentence ends 1.4 s later
    ws = [W("It", 152.2, 152.4), W("doesn't", 152.4, 152.8), W("make", 152.8, 153.0), W("sense", 153.0, 153.3), W("to", 153.3, 153.5), W("put", 153.5, 153.9),
          W("a", 154.6, 154.7), W("new", 154.7, 154.9), W("one.", 154.9, 155.3)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    m = opsmod.measure_sentence_end(_cut(), 1)
    assert m["ext"] == pytest.approx(155.3 + 0.125 - 154.0, abs=0.01)
    assert "a new one." in m["sentence"] and m["path"] == "/x/lav.wav"


def test_a_clip_that_already_ends_a_sentence_is_left_alone(monkeypatch, quiet):
    ws = [W("That", 152.0, 152.4), W("is", 152.4, 152.6), W("it.", 152.6, 153.4), W("Next", 154.3, 154.6)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    assert "finished sentence" in opsmod.measure_sentence_end(_cut(), 1)["reason"]


def test_no_end_of_the_sentence_within_reach_is_said_plainly(monkeypatch, quiet):
    ws = [W("so", 152.0, 152.4), W("then", 152.4, 153.9), W("and", 160.0, 160.3)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    assert "no end of that sentence" in opsmod.measure_sentence_end(_cut(), 1)["reason"]


def test_it_does_not_run_into_footage_the_next_clip_shows(monkeypatch, quiet):
    cut = _cut()
    cut.video[1].src_in, cut.video[1].src_out = 104.5, 108.0                        # clip 2 plays the very footage the sentence would need
    ws = [W("so", 152.0, 152.4), W("then", 153.5, 153.9), W("we", 154.6, 154.9), W("go.", 154.9, 155.4)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    assert "next clip's footage" in opsmod.measure_sentence_end(cut, 1)["reason"]


def test_the_plan_uses_it_for_a_note_when_the_sound_alone_shows_no_cut_but_not_for_the_reviewers_own_measured_fix(monkeypatch, quiet):
    import apply_ops
    cut = _cut()
    monkeypatch.setattr(apply_ops, "measure_tail", lambda cut, idx, mx: {"reason": "the audio is already quiet at the cut (-48 dB, room level), so nothing looks cut off"})
    ws = [W("so", 152.0, 152.4), W("then", 153.5, 153.9), W("we", 154.6, 154.9), W("go.", 154.9, 155.4)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    changes, spans, ins = apply_ops.plan(cut, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0}], [{"timeline_sec": 3.5, "text": "this sentence gets cut off"}])
    assert changes[0].applied and "finish its sentence" in changes[0].summary and ins and ins[0][3] == 1 and ins[0][4] == "end"
    changes, spans, ins = apply_ops.plan(cut, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0, "trusted": True}], [{"timeline_sec": 3.5, "text": "AI: x"}])
    assert not changes[0].applied and not ins                                       # an AI-measured fix keeps its measured limits


def test_the_speaker_switch_notes_ryan_typed_are_taken_as_follow_the_speaker():
    cut = _cut()
    for text in ("You’re just jumping to mitch at random times. Not when hes actually responding. And you arent switching to his source audio correctly either.",
                 "you cut to bob when mitch is talking", "wrong mic under him", "the cut goes to him when he isn't speaking"):
        got = opsmod.validate([{"note": 1, "op": "follow_speaker", "clip": "all"}], [{"timeline_sec": 3.0, "text": text, "clip": 1}], cut)[0]
        if text.startswith("the cut goes"):
            continue                                                                  # not claimed: nothing in it names a person or a recorder
        assert got["op"] == "follow_speaker", text
    assert opsmod.validate([{"note": 1, "op": "follow_speaker", "clip": "all"}], [{"timeline_sec": 3.0, "text": "make the title bigger", "clip": 1}], cut)[0]["op"] == "unsupported"
