"""punch_in.py: a closer shot on every other clip of a jump cut (Ryan, 2026-10-08)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import framing  # noqa: E402
import punch_in as pi  # noqa: E402
from timeline import Cut, VideoClip  # noqa: E402

DIMS = lambda _p: (7680, 4320)  # noqa: E731     the Septic cut's camera file


def _cut(rows):
    """rows: (seconds long, file, source x of the mid-frame point, scale)"""
    cut = Cut("s", 30.0, 1080, 1920, sum(r[0] for r in rows))
    t = 0.0
    for i, (d, f, x, s) in enumerate(rows, start=1):
        cut.video.append(VideoClip(i, t, t + d, f, 100.0 + t, 100.0 + t + d, (s, framing.horiz_for_person(x, s, 7680, 1080), 0.0)))
        t += d
    return cut


def test_every_other_clip_of_one_person_is_punched_in_and_a_new_person_starts_wide():
    """The Septic cut: Bob for three clips, Mitch for six, one camera; each clip centred on that moment's face, so one person drifts a few percent."""
    cut = _cut([(5, "/c.mp4", 0.31, 44.44), (4, "/c.mp4", 0.28, 44.44), (6, "/c.mp4", 0.29, 44.44),
                (4, "/c.mp4", 0.70, 44.44), (5, "/c.mp4", 0.70, 44.44), (5, "/c.mp4", 0.74, 44.44), (6, "/c.mp4", 0.72, 44.44)])
    rows = pi.plan(cut, DIMS)
    assert [r["idx"] for r in rows] == [2, 5, 7]
    assert all(abs(r["scale"] - 44.44 * pi.PUNCH) < 0.01 for r in rows)
    for r in rows:                                                                 # the same person stays in mid-frame
        assert abs(framing.x_for_horiz(r["horiz"], r["scale"], 7680) - r["x"]) < 0.002


def test_the_tight_shot_keeps_the_top_of_the_picture_so_no_head_is_cut_off():
    """Scaling about the middle cut off the top of Mitch's head (his head touches the top of the wide shot)."""
    cut = _cut([(5, "/c.mp4", 0.7, 44.44), (5, "/c.mp4", 0.7, 44.44)])
    r = pi.plan(cut, DIMS)[0]
    s = r["scale"] / 100.0
    top = 4320 / 2 - r["vert"] * 4320 / s - 1920 / (2 * s)                         # render_preview's geometry
    assert abs(top) < 1.0                                                          # the wide shot starts at row 0, and so does the tight one


def test_different_files_different_people_and_gaps_are_not_jump_cuts():
    cut = _cut([(5, "/a.mp4", 0.3, 44.44), (5, "/b.mp4", 0.3, 44.44), (5, "/b.mp4", 0.7, 44.44)])
    assert pi.plan(cut, DIMS) == []


def test_running_it_twice_changes_nothing_more(tmp_path):
    cut = _cut([(5, "/c.mp4", 0.3, 44.44), (5, "/c.mp4", 0.3, 44.44), (5, "/c.mp4", 0.3, 44.44)])
    first = pi.plan(cut, DIMS)
    assert [r["idx"] for r in first] == [2]
    c2 = cut.video[1]
    cut.video[1] = VideoClip(c2.idx, c2.tl_start, c2.tl_end, c2.src_path, c2.src_in, c2.src_out, (first[0]["scale"], first[0]["horiz"], first[0]["vert"]))
    assert pi.plan(cut, DIMS) == []


def test_a_split_punched_clip_is_never_punched_closer():
    """A revised cut can split a punched clip in two; the second half must not go to 115% of 115%."""
    s = 44.44 * pi.PUNCH
    cut = _cut([(5, "/c.mp4", 0.3, 44.44), (3, "/c.mp4", 0.3, s), (3, "/c.mp4", 0.3, s)])
    assert pi.plan(cut, DIMS) == []
