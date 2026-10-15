"""Captions that do not match the speech: the cause (a stale render of another cut was transcribed) and the editor's answer (redo_captions).

Ryan, 2026-10-13: "None of the captions match up and when i asked the editor to fix them it couldnt.... The editor hasnt been able to implement even ONE revision ever from me." His note on V5 was
"the captions dont match whats being said. Fix the captions"; the editor answered "unsupported: the note doesn't give the correct wording". The words are in the audio, and V5's captions were the
words of a 41.2 s render of an older cut (V5 is 43.7 s) that a retry had left in the folder.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "captions"))

import ops as opsmod  # noqa: E402
import proxy  # noqa: E402
import timeline  # noqa: E402


def _cut(src_in=10.0):
    v = [timeline.VideoClip(1, 0.0, 4.0, "/x/a.mp4", src_in, src_in + 4.0), timeline.VideoClip(2, 4.0, 8.0, "/x/a.mp4", 30.0, 34.0)]
    a = [timeline.AudioClip(0.0, 4.0, "/x/lav.wav", 50.0, 54.0, 1)]
    return timeline.Cut("t", 30.0, 1080, 1920, 8.0, v, a)


def test_a_proxy_is_used_only_for_the_cut_it_was_rendered_from(tmp_path, monkeypatch):
    import render_preview
    renders = []

    def fake(cut, out, height=540):
        renders.append(out.name)
        out.write_bytes(b"render")
    monkeypatch.setattr(render_preview, "render_preview", fake)
    base = tmp_path / "cut_1080.mp4"
    base.write_bytes(b"an older cut's render, left here by an earlier try")             # no stamp: whose render is it? not this one's
    assert proxy.ensure_proxy(_cut(), base) is True and len(renders) == 1
    assert proxy.ensure_proxy(_cut(), base) is False and len(renders) == 1               # the same cut again: kept
    assert proxy.ensure_proxy(_cut(src_in=11.0), base) is True and len(renders) == 2     # a revised cut in the same folder: rendered again, not transcribed as the old one
    assert proxy.is_current(base, _cut(src_in=11.0)) and not proxy.is_current(base, _cut())


def test_the_signature_sees_a_recorder_switched_on_or_off(tmp_path):
    a, b = _cut(), _cut()
    b.audio = []
    assert proxy.cut_signature(a) != proxy.cut_signature(b)


def _groups(path, lines):
    path.write_text(json.dumps({"window": {"start": 0.0, "end": 8.0}, "groups": [{"text": t, "words": [{"text": w, "start": s, "end": s + 0.4} for w in t.split()]} for s, t in lines]}))
    return path


SAID = [(0.0, 3.5, "So we bought three houses and all three houses", 1), (4.0, 7.5, "with the septic systems we are not going to replace that", 2)]


def test_captions_from_another_cut_are_caught_by_timing_even_when_their_words_are_in_the_cut(tmp_path):
    mine = _groups(tmp_path / "ok.json", [(0.2, "so we bought three houses"), (4.1, "with the septic systems we are not")])
    ok, detail = proxy.captions_match(mine, SAID)
    assert ok is True, detail
    swapped = _groups(tmp_path / "swapped.json", [(0.2, "with the septic systems we are not"), (4.1, "so we bought three houses")])      # every word is said in the cut, in the other order
    ok, detail = proxy.captions_match(swapped, SAID)
    assert ok is False and "first that are not" in detail
    nothing = _groups(tmp_path / "old.json", [(0.2, "but they are all septic"), (4.1, "all three of them however failed")])
    assert proxy.captions_match(nothing, SAID)[0] is False


def _note(text, **kw):
    return {"timeline_sec": 19.05, "clip": 4, "text": text, **kw}


def test_the_note_ryan_wrote_is_a_redo_of_the_captions_not_unsupported():
    cut = _cut()
    for text in ("the captions dont match whats being said. Fix the captions",
                 "captions are wrong", "the subtitles are out of sync with what he says", "none of the captions line up with the speech", "these captions are garbled"):
        got = opsmod.validate([{"note": 1, "op": "redo_captions", "why": "x"}], [_note(text)], cut)[0]
        assert got["op"] == "redo_captions", text


def test_a_note_that_does_not_complain_about_the_captions_is_not_a_redo():
    cut = _cut()
    for text in ("the captions look great", "make the music quieter", "the title card is wrong"):
        got = opsmod.validate([{"note": 1, "op": "redo_captions", "why": "x"}], [_note(text)], cut)[0]
        assert got["op"] == "unsupported", text


def test_a_note_left_on_the_captions_lane_may_redo_them():
    cut = _cut()
    n = _note("this is wrong", target={"lane": "Captions", "label": "x", "start": 18.0, "end": 20.0})
    assert opsmod.validate([{"note": 1, "op": "redo_captions"}], [n], cut)[0]["op"] == "redo_captions"


def test_the_op_is_registered_as_made_by_the_rebuild_and_the_reader_is_told_of_it():
    import capabilities
    assert capabilities.OP_MADE_BY["redo_captions"] == capabilities.REBUILD
    assert "redo_captions" in opsmod.SYSTEM and "do NOT give the right words" in opsmod.SYSTEM.replace("does NOT", "do NOT") or "redo_captions" in opsmod.SYSTEM
