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
    """A revised cut can split a punched clip in two; the second half must not go to 120% of 120%."""
    s = 44.44 * pi.PUNCH
    cut = _cut([(5, "/c.mp4", 0.3, 44.44), (3, "/c.mp4", 0.3, s), (3, "/c.mp4", 0.3, s)])
    assert pi.plan(cut, DIMS) == []


def test_the_factor_is_judged_on_the_source_a_red_original_gets_all_of_it_a_4k_clip_gets_what_it_can_spare():
    """Ryan, 2026-10-21: avoid overscaling when the footage would degrade, accept minimal softness for a crop that looks different."""
    red = pi.plan(_cut([(5, "/c.mp4", 0.3, 44.44), (5, "/c.mp4", 0.3, 44.44)]), lambda _p: (7680, 4320))
    assert abs(red[0]["scale"] - 44.44 * pi.PUNCH) < 0.01, "plenty of resolution: the full 20%"
    uhd = pi.plan(_cut([(5, "/c.mp4", 0.3, 88.9), (5, "/c.mp4", 0.3, 88.9)]), lambda _p: (3840, 2160))
    assert abs(uhd[0]["scale"] - 88.9 * pi.PUNCH) < 0.01, "a 4K clip fills the portrait frame at 89%: 20% closer is 107%, 7% enlarged, minimal, so it gets the full punch"
    skipped: list = []
    held = pi.plan(_cut([(5, "/c.mp4", 0.3, 100.0), (5, "/c.mp4", 0.3, 100.0)]), lambda _p: (3840, 2160), skipped=skipped)
    assert held[0]["scale"] == pi.SOFT_MAX_SCALE and not skipped, "a clip already at native size is held to the softness limit: 115%, not 120%"
    assert held[0]["scale"] / 100.0 >= pi.MIN_PUNCH, "and it is still a visibly different shot"


def test_a_source_already_enlarged_is_not_punched_closer_and_the_reason_is_given():
    """A 1080p proxy filling a portrait frame is at 178%: already degraded, so no closer shot, and the person is told why."""
    skipped: list = []
    rows = pi.plan(_cut([(5, "/c.mp4", 0.3, 177.8), (5, "/c.mp4", 0.3, 177.8)]), lambda _p: (1920, 1080), skipped=skipped)
    assert rows == [] and [s["idx"] for s in skipped] == [2]
    assert "1920x1080" in skipped[0]["why"] and "115%" in skipped[0]["why"]


def test_a_punch_the_limit_would_shrink_under_ten_percent_is_skipped_not_faked():
    skipped: list = []
    rows = pi.plan(_cut([(5, "/c.mp4", 0.3, 106.0), (5, "/c.mp4", 0.3, 106.0)]), lambda _p: (3840, 2160), skipped=skipped)       # 115 / 106 = 1.085: too little change to hide a cut
    assert rows == [] and len(skipped) == 1
