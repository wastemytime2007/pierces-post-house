"""The notes reader sees the caption lines around a note, and a caption edit may use their words (Ryan, 2026-10-12: it was blind to them, so "move the 23 to the next caption" replaced a whole line)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
from timeline import Cut, VideoClip  # noqa: E402

LINES = {"previous": "so the plan is", "this": "this septic, it costs $23", "next": "000 is the quote we"}
TEXT = "Make the 23 in this caption move over to the next caption with the 000 so it says $23,000"


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 10.0)
    cut.video.append(VideoClip(1, 0.0, 10.0, "/a.mp4", 100.0, 110.0))
    return cut


def _note(lines=LINES, text=TEXT):
    return {"timeline_sec": 4.0, "clip": 1, "text": text, "shapes": [], "caption_lines": lines}


def test_the_prompt_shows_the_captions_around_each_note():
    p = opsmod.build_prompt(_cut(), [_note()])
    assert 'previous line "so the plan is" | this line "this septic, it costs $23" | next line "000 is the quote we"' in p


def test_a_caption_edit_may_use_the_words_of_the_lines_around_the_note_for_this_line_and_the_next():
    ops = [{"note": 1, "op": "edit_caption", "text": "this septic, it costs", "line": "this"}, {"note": 1, "op": "edit_caption", "text": "$23,000 is the quote we", "line": "next"}]
    got = opsmod.validate(ops, [_note()], _cut())
    assert [(o["op"], o["line"], o["text"]) for o in got] == [("edit_caption", "this", "this septic, it costs"), ("edit_caption", "next", "$23,000 is the quote we")]


def test_words_that_are_in_neither_the_note_nor_the_captions_are_still_refused_and_so_is_a_missing_neighbour():
    invented = [{"note": 1, "op": "edit_caption", "text": "this septic, it costs a fortune and a half", "line": "this"}]
    assert opsmod.validate(invented, [_note()], _cut())[0]["op"] == "unsupported"
    none_next = _note({"previous": "a b", "this": "this septic", "next": ""})
    assert opsmod.validate([{"note": 1, "op": "edit_caption", "text": "this septic", "line": "next"}], [none_next], _cut())[0]["op"] == "unsupported"
    assert opsmod.validate([{"note": 1, "op": "edit_caption", "text": "this septic", "line": "sideways"}], [_note()], _cut())[0]["op"] == "unsupported"
