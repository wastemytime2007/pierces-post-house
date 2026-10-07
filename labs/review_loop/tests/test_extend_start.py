"""extend_start: a clip that starts in the middle of a sound starts earlier, where the sound begins, and everything else follows.

Same synthetic media as test_revise (a 60 s noise 'lav' with an exact 2 s silence at 10-12 s). Clip 2 is moved to begin 0.4 s after the silence ends, so its first sound starts 0.4 s before the cut.
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "qa"))

import apply_ops  # noqa: E402
import ops as opsmod  # noqa: E402
import revise  # noqa: E402
import test_revise as tr  # noqa: E402
import timeline  # noqa: E402
from test_revise import FPS, media, xml  # noqa: E402,F401  (fixtures)

pytestmark = tr.pytestmark


def _after_silence(tmp_path, media):
    """Clip 1 is camera 0-4.5 s; clip 2 starts at lav 12.4 s (camera 7.395 s), 0.4 s after the silence ends."""
    x = tr.make_xml(tmp_path / "s.xml", *media, first_len=270)
    t = x.read_text()
    assert t.count("<in>600</in><out>900</out>") == 2 and t.count("<in>1499</in><out>2099</out>") == 1
    x.write_text(t.replace("<in>600</in><out>900</out>", "<in>222</in><out>522</out>").replace("<in>1499</in><out>2099</out>", "<in>743</in><out>1343</out>"))
    return x


def test_extend_start_finds_where_the_sound_begins_and_grows_the_clip_at_its_front(tmp_path, media):
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}])
    assert changes[0].applied, changes[0].summary
    g = round(delta * FPS)
    assert 0.35 < delta < 0.6                                                  # 0.4 s of sound before the cut, plus a 0.04 s pad
    v, lavs = tr._items(out, "video"), tr._items(out, "audio", 1)
    assert v[0] == (0, 270, 0, 135)                                            # clip 1 untouched
    assert v[1] == (270, 870 + g, 222 - round(g * 0.5), 522)                   # the clip starts at the same timeline frame, reads earlier source, and is longer by g
    assert lavs[1] == (270, 870 + g, 743 - g, 1343)                            # the lav under it grows by the same amount at its front
    assert v[2][0] == 870 + g and lavs[2][0] == 870 + g                        # everything after ripples right
    assert timeline.load_cut(out).zone_end == pytest.approx(cut.zone_end + delta, abs=0.05)
    rows = revise.verify(cut, x, out, delta, extra_in=changes[0].check["ext"])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows
    quiet = revise.verify_render(changes, out)
    assert quiet and all(ok for _n, ok, _d in quiet) and quiet[0][0].endswith("START-IN-QUIET")


def test_qa_measures_the_earlier_start_from_the_two_xmls_and_fails_a_start_that_did_not_move(tmp_path, media):
    import qa_pass
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}])
    new = timeline.load_cut(out)
    o = {"note": 1, "op": "extend_start", "clip": 2}
    item = {"applied": True, "summary": "x"}
    good = qa_pass.check_op(o, item, {"timeline_sec": 5.0, "text": "t"}, cut, new, {}, None, {})
    assert good.status == qa_pass.VERIFIED and "starts" in good.detail and "earlier in its source" in good.detail
    unchanged = qa_pass.check_op(o, item, {"timeline_sec": 5.0, "text": "t"}, cut, cut, {}, None, {})
    assert unchanged.status == qa_pass.FAILED


def test_without_the_allowance_the_footage_check_would_catch_new_footage_in_front(tmp_path, media):
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}])
    rows = dict((n, ok) for n, ok, _d in revise.verify(cut, x, out, delta))                  # extra_in left at 0
    assert rows["NO-NEW-FOOTAGE"] is False                                                     # negative control: the check does see it


def test_a_start_extension_and_the_neighbours_end_extension_meet_without_a_gap(tmp_path, media):
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0}, {"note": 2, "op": "extend_start", "clip": 2, "max_sec": 1.0}])
    assert all(c.applied for c in changes), [c.summary for c in changes]
    g_end, g_front = round(changes[0].check["ext"] * FPS), round(changes[1].check["ext"] * FPS)
    v = tr._items(out, "video")
    assert v[0][1] == 270 + g_end and v[1][0] == 270 + g_end                                   # contiguous
    assert v[1][1] == 870 + g_end + g_front
    rows = revise.verify(cut, x, out, delta, extra_out=changes[0].check["ext"], extra_in=changes[1].check["ext"])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows


def test_extend_start_says_why_when_it_cannot(tmp_path, media):
    x = tr.make_xml(tmp_path / "plain.xml", *media, first_len=270)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 1, "max_sec": 1.0}])
    assert not changes[0].applied and "keeps going" in changes[0].summary and delta == 0       # clip 1 starts inside noise that runs on for seconds
    y = _after_silence(tmp_path, media)
    quiet = tr._run(y, tmp_path, [{"note": 1, "op": "extend_start", "clip": 1, "max_sec": 4.0}])[2][0]
    assert not quiet.applied                                                                    # clip 1's start is not mid-sound after the silence: nothing sensible to extend into


def test_a_start_that_another_note_trims_is_refused(tmp_path, media):
    x = _after_silence(tmp_path, media)
    with pytest.raises(timeline.TimelineError, match="start .* also being trimmed"):
        tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}, {"note": 2, "op": "trim_start", "clip": 2, "seconds": 0.5}])


def test_validate_keeps_a_reviewers_own_fix_as_it_is_and_clamps_the_interpreters(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 12.0, "text": "AI: clip 2 starts in the middle of a sound", "clip": 2,
              "suggested_op": {"op": "extend_start", "clip": 2, "max_sec": 3.0}},
             {"timeline_sec": 14.0, "text": "AI: clip 3 is cut", "clip": 3, "suggested_op": {"op": "drop_clip", "clip": 3}},
             {"timeline_sec": 15.0, "text": "AI: x", "clip": 3, "suggested_op": {"op": "reframe_vertical", "clip": 3, "direction": "lower"}},
             {"timeline_sec": 16.0, "text": "make it better", "clip": 1}]
    got = opsmod.validate(opsmod.from_suggestions(notes, cut), notes, cut)
    by = {o["note"]: o for o in got}
    assert by[1]["op"] == "extend_start" and by[1]["max_sec"] == 3.0 and by[1]["trusted"] is True
    assert by[2]["op"] == "drop_clip" and by[2]["clip"] == 3                                    # a drop the note's wording never asked for: allowed only because it is the reviewer's own fix
    assert by[3]["op"] == "unsupported" and "not one the editor makes" in by[3]["reason"]
    assert by[4]["op"] == "unsupported"                                                          # a note with no fix is accounted for, not guessed at
    far = opsmod.validate([{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 9.0, "trusted": True}], notes, cut)[0]
    assert far["max_sec"] == 4.0                                                                  # even a trusted look-ahead is bounded
    plain = opsmod.validate([{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 9.0}], notes, cut)[0]
    assert plain["max_sec"] == 1.5                                                                # the interpreter's is bounded tighter


def test_the_pool_is_compared_with_the_cut_in_the_time_base_the_clips_were_read_in():
    """A 29.97 fps file in a 30 fps sequence (the app's own export): the pool clip starts 0.4 s AFTER the cut's last clip ends. Converted at 30 fps it looked 0.37 s inside it."""
    import xml.etree.ElementTree as ET
    el = ET.fromstring('<clipitem id="p"><start>6091</start><end>6577</end><in>23481</in><out>23967</out><file id="f1"/></clipitem>')
    groups = {(6091, 6577): [("video", el)]}
    old_out = 783.08                                                                  # the clip's source end, read at the file's 29.97 fps
    assert apply_ops._trim_pool(groups, "f1", old_out, old_out + 0.09, 30.0, 1 / 29.97) == 0.0         # the pool starts at 23481 / 29.97 = 783.48 s: no overlap
    assert el.findtext("in") == "23481" and el.findtext("end") == "6577"                               # untouched
    assert apply_ops._pool_overlaps(groups, "f1", 783.0, 783.2, 30.0, 1 / 29.97) is False
    assert apply_ops._pool_overlaps(groups, "f1", 783.3, 783.7, 30.0, 1 / 29.97) is True
    assert apply_ops._trim_pool(groups, "f1", old_out, old_out + 0.09, 30.0) > 0.3                       # negative control (last: it edits the clip): the sequence-rate conversion trims it for nothing


def test_qa_still_measures_an_end_extension_on_a_clip_that_also_started_earlier():
    """The loop fixes a clip's start and its end in one round; the end check used to look for a piece starting where the old one did and reported FAILED."""
    import qa_pass
    from timeline import Cut, VideoClip
    old = Cut("x", 30.0, 1080, 1920, 10.0, video=[VideoClip(1, 0.0, 10.0, "/nowhere.mp4", 10.0, 20.0)])
    new = Cut("x", 30.0, 1080, 1920, 10.7, video=[VideoClip(1, 0.0, 10.7, "/nowhere.mp4", 9.6, 20.1)])
    row = qa_pass.check_op({"note": 1, "op": "extend_end", "clip": 1}, {"applied": True, "summary": "x"}, {"timeline_sec": 9.0, "text": "t"}, old, new, {}, None, {})
    assert row.status == qa_pass.VERIFIED and "runs 0.10s longer" in row.detail
    start = qa_pass.check_op({"note": 2, "op": "extend_start", "clip": 1}, {"applied": True, "summary": "x"}, {"timeline_sec": 0.5, "text": "t"}, old, new, {}, None, {})
    assert start.status == qa_pass.VERIFIED and "0.40s earlier" in start.detail
