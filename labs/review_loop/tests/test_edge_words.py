"""A cut's edge is placed by the words at that edge, even when the reviewer's whole quote does not match the audio.

Ryan, 2026-10-15: 'could not find "oh i feel it right here too yep" near the end of clip 4; the audio there reads: "it right here too. Yep. That's a very simple one."' and the same for a clip start: the first
words of the quote were a transcript's, the audio read them differently, and the fix was refused although the words that place the cut were all there.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
import timeline  # noqa: E402


def W(text, a, b):
    return opsmod.words_mod.W(text, a, b)


def _cut():
    return timeline.Cut("t", 30.0, 1080, 1920, 10.0, [timeline.VideoClip(1, 0.0, 10.0, "/x/a.mp4", 100.0, 110.0)], [])


def test_the_end_is_found_by_the_last_words_when_the_whole_quote_does_not_match(monkeypatch):
    ws = [W("It", 108.0, 108.2), W("right", 108.2, 108.5), W("here", 108.5, 108.8), W("too.", 108.8, 109.1), W("Yep.", 109.4, 109.8), W("That's", 109.9, 110.2)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    monkeypatch.setattr(opsmod.words_mod, "valley", lambda path, lo, hi: (lo + hi) / 2)
    m = opsmod.locate_end(_cut(), 1, "oh i feel it right here too yep")
    assert "reason" not in m and m["at_word"].strip() == "Yep." and 0.05 < m["trim"] < 0.4
    assert "reason" in opsmod.locate_end(_cut(), 1, "we bought three houses in a subdivision")                 # words that are not there at all are still refused


def test_the_start_is_found_by_the_first_words_when_the_whole_quote_does_not_match(monkeypatch):
    ws = [W("Right", 100.0, 100.3), W("so", 101.0, 101.2), W("with", 101.2, 101.4), W("the", 101.4, 101.5), W("septic", 101.5, 101.9), W("systems", 101.9, 102.3), W("we", 102.4, 102.5)]
    monkeypatch.setattr(opsmod.words_mod, "words_in", lambda path, start, dur: ws)
    monkeypatch.setattr(opsmod.words_mod, "valley", lambda path, lo, hi: (lo + hi) / 2)
    m = opsmod.locate_start(_cut(), 1, "so with the septic systems we are going to wait until the closing")
    assert "reason" not in m and 0.5 < m["trim"] < 1.2                                                           # the full sentence was never said that way: its first words place the start
    assert "reason" in opsmod.locate_start(_cut(), 1, "completely different sentence about roofing")


def test_a_short_phrase_has_no_shorter_variants():
    assert opsmod._edge_variants("right here too", end=True) == ["right here too"]
    assert opsmod._edge_variants("a b c d e f g", end=True) == ["a b c d e f g", "c d e f g", "d e f g", "e f g"]
    assert opsmod._edge_variants("a b c d e f g", end=False) == ["a b c d e f g", "a b c d e", "a b c d", "a b c"]


def test_a_fix_whose_words_cannot_be_found_removes_the_clip_the_same_note_calls_garbled_and_says_to_remove(monkeypatch):
    """2026-10-15: 'This line is garbled and trails off mid-thought ... Remove it' carried start_at_words "should they just add a disc to the"; the audio read "I'm going to bet it, get your cake", the words were
    not found and the note was left. The note asked for the clip to go."""
    import apply_ops
    cut = timeline.Cut("t", 30.0, 1080, 1920, 10.0, [timeline.VideoClip(1, 0.0, 5.0, "/x/a.mp4", 100.0, 105.0), timeline.VideoClip(2, 5.0, 10.0, "/x/a.mp4", 200.0, 205.0)], [])
    monkeypatch.setattr(apply_ops, "locate_start", lambda *a, **k: {"reason": 'could not find "should they just add a disc to the" near the start of clip 2; the audio there reads: "I\'m going to bet it"'})
    notes = [{"timeline_sec": 6.0, "text": "AI: This line is garbled and trails off mid-thought. It does not follow from what comes before. Remove it so the clip before runs into the close."}]
    changes, spans, _ins = apply_ops.plan(cut, [{"note": 1, "op": "start_at_words", "clip": 2, "words": "should they just add a disc to the", "trusted": True}], notes)
    assert changes[0].applied and "dropped" in changes[0].summary and [(s, e) for s, e, _i in spans] == [(5.0, 10.0)]
    notes[0]["text"] = "AI: The sentence starts late. Start the clip a little later."                    # a note that does not call the clip garbled keeps the refusal
    changes, spans, _ins = apply_ops.plan(cut, [{"note": 1, "op": "start_at_words", "clip": 2, "words": "should they just add a disc to the", "trusted": True}], notes)
    assert not changes[0].applied and not spans
    notes[0]["text"] = "AI: This line is garbled. Remove it."                                              # a note a person typed (not trusted) is never turned into a removal
    changes, spans, _ins = apply_ops.plan(cut, [{"note": 1, "op": "start_at_words", "clip": 2, "words": "should they just add a disc to the"}], notes)
    assert not changes[0].applied and not spans


def test_a_cut_inside_a_word_is_found_from_the_word_timing_when_the_loudness_sees_no_onset(monkeypatch):
    """2026-10-15, wall project: "the start lands inside the word 'particularly'" was refused with "the sound has not begun before the start" (the soft first sound is under the loudness threshold)."""
    import ops
    from types import SimpleNamespace as W
    monkeypatch.setattr(ops.words_mod, "words_in", lambda path, start, dur: [W(text="so", start=99.0, end=99.3), W(text="particularly", start=99.8, end=100.6)])
    assert ops._word_across("/x/a.wav", 100.0, 3.0).text == "particularly"
    assert ops._word_across("/x/a.wav", 99.5, 3.0) is None                              # between words: nothing is cut off
    assert ops._word_across("/x/a.wav", 100.58, 3.0) is None                            # within 30 ms of the word's end: not inside it


def test_a_near_word_heard_at_a_seam_passes_the_looser_word_match_and_a_different_word_does_not():
    """2026-10-15, wall project: the seam read "that's the same" for "Okay. That's the seam." and the whole fix was thrown away."""
    import words as words_mod
    from types import SimpleNamespace as W
    ws = [W(text="that's", start=1.0, end=1.2), W(text="the", start=1.2, end=1.3), W(text="same.", start=1.3, end=1.6)]
    assert words_mod.find_phrase(ws, "that's the seam") is None                           # the default match is unchanged
    assert words_mod.find_phrase(ws, "that's the seam", word_sim=0.7)[0] == 0
    assert words_mod.find_phrase(ws, "that's the sink", word_sim=0.7) is None
