"""A note asking for jump cuts to be cropped or punched in is reported as the finish step's job, not as "not supported" (2026-10-09)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ops as opsmod  # noqa: E402
from timeline import Cut, VideoClip  # noqa: E402


def _cut():
    cut = Cut("s", 30.0, 1080, 1920, 10.0)
    cut.video.append(VideoClip(1, 0.0, 5.0, "/c.mp4", 100.0, 105.0))
    cut.video.append(VideoClip(2, 5.0, 10.0, "/c.mp4", 105.0, 110.0))
    return cut


def test_a_jump_cut_punch_in_note_is_a_punch_in_op():
    note = {"note": 1, "text": "Make sure every other cut is cropped/reframed to avoid jump cuts", "timeline_sec": 1.0, "clip": 1, "shapes": []}
    assert opsmod.validate([{"note": 1, "op": "punch_in"}], [note], _cut())[0]["op"] == "punch_in"


def test_a_punch_in_op_on_a_note_that_does_not_ask_for_one_is_refused():
    note = {"note": 2, "text": "Make the title bigger", "timeline_sec": 1.0, "clip": 1, "shapes": []}
    assert opsmod.validate([{"note": 2, "op": "punch_in"}], [note], _cut())[0]["op"] == "unsupported"
