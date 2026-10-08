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


def test_a_start_that_another_note_trims_is_set_aside_and_the_trim_is_made(tmp_path, media):
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}, {"note": 2, "op": "trim_start", "clip": 2, "seconds": 0.5}])
    assert not changes[0].applied and "also being trimmed" in changes[0].summary
    assert changes[1].applied and delta < 0


def test_two_fixes_that_extend_the_same_edge_make_one_and_say_the_other_adds_nothing(tmp_path, media):
    x = _after_silence(tmp_path, media)
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_start", "clip": 2, "max_sec": 1.0}, {"note": 2, "op": "extend_start", "clip": 2, "max_sec": 1.0}])
    assert changes[0].applied and not changes[1].applied and "already" in changes[1].summary


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


def _adjacent(tmp_path, media):
    """Clip 1 is camera 0-4.5 s and clip 2 starts at 4.8 s of the same file: 0.3 s of the recording was cut out between them, and the sound at both sides of that stretch is running."""
    x = tr.make_xml(tmp_path / "adj2.xml", *media, first_len=270)
    t = x.read_text()
    assert t.count("<in>600</in><out>900</out>") == 2 and t.count("<in>1499</in><out>2099</out>") == 1
    x.write_text(t.replace("<in>600</in><out>900</out>", "<in>144</in><out>444</out>").replace("<in>1499</in><out>2099</out>", "<in>588</in><out>1188</out>"))
    return x


def test_when_an_extension_would_run_into_the_next_clip_the_reviewers_fix_puts_the_gap_back_as_a_join(tmp_path, media):
    x = _adjacent(tmp_path, media)
    plain = tr._run(x, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 3.0}])[2][0]
    assert not plain.applied and "next clip's footage" in plain.summary                                  # an interpreter's own note stops here, as before
    cut, out, changes, delta = tr._run(x, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 3.0, "trusted": True}])
    assert changes[0].applied and changes[0].summary.startswith("joined clip 1 to clip 2"), changes[0].summary
    assert delta == pytest.approx(0.3, abs=0.03)
    g = round(delta * FPS)
    v, lavs = tr._items(out, "video"), tr._items(out, "audio", 1)
    assert v[0] == (0, 270 + g, 0, 135 + round(g * 0.5)) and lavs[0][1] == 270 + g                       # the first clip runs on to where the second begins in the source
    assert v[1][0] == 270 + g
    new = timeline.load_cut(out)
    assert new.video[0].src_out == pytest.approx(new.video[1].src_in, abs=0.02)                          # contiguous in the source: no cut
    rows = revise.verify(cut, x, out, delta, extra_out=changes[0].check["ext"])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows


def test_the_second_note_about_the_same_seam_adds_nothing_and_qa_sees_the_join_from_either_side(tmp_path, media):
    import qa_pass
    x = _adjacent(tmp_path, media)
    ops = [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 3.0, "trusted": True}, {"note": 2, "op": "extend_start", "clip": 2, "max_sec": 3.0, "trusted": True}]
    cut, out, changes, delta = tr._run(x, tmp_path, ops)
    assert all(c.applied for c in changes) and "already joined" in changes[1].summary and delta == pytest.approx(0.3, abs=0.03)       # the gap went in once, not twice
    new = timeline.load_cut(out)
    item = {"applied": True, "summary": "x"}
    a = qa_pass.check_op({"note": 1, "op": "extend_end", "clip": 1}, item, {"timeline_sec": 4.0, "text": "t"}, cut, new, {}, None, {})
    b = qa_pass.check_op({"note": 2, "op": "extend_start", "clip": 2}, item, {"timeline_sec": 5.0, "text": "t"}, cut, new, {}, None, {})
    assert a.status == qa_pass.VERIFIED and b.status == qa_pass.VERIFIED and "joined" in b.detail
    # the other order: the start note comes first and joins from the front; the end note is then satisfied
    cut2, out2, changes2, delta2 = tr._run(x, tmp_path, [ops[1] | {"note": 1}, ops[0] | {"note": 2}])
    assert all(c.applied for c in changes2) and "already joined" in changes2[1].summary and delta2 == pytest.approx(0.3, abs=0.03)
    new2 = timeline.load_cut(out2)
    assert qa_pass.check_op({"note": 1, "op": "extend_start", "clip": 2}, item, {"timeline_sec": 5.0, "text": "t"}, cut2, new2, {}, None, {}).status == qa_pass.VERIFIED
    assert qa_pass.check_op({"note": 2, "op": "extend_end", "clip": 1}, item, {"timeline_sec": 4.0, "text": "t"}, cut2, new2, {}, None, {}).status == qa_pass.VERIFIED


def test_a_gap_that_is_too_long_or_in_different_footage_is_not_joined(tmp_path, media):
    x = tr.make_xml(tmp_path / "far.xml", *media, first_len=270)                                         # clip 2 is 15 s of the recording after clip 1
    cut = timeline.load_cut(x)
    assert "too much to put back" in opsmod.measure_join(cut, 1, "next")["reason"]
    assert "no clip next to it" in opsmod.measure_join(cut, 1, "prev")["reason"]
    y = _adjacent(tmp_path, media)
    cut2 = timeline.load_cut(y)
    assert opsmod.measure_join(cut2, 1, "next")["ext"] == pytest.approx(0.3, abs=0.02) and opsmod.measure_join(cut2, 2, "prev")["other"] == 1
    cut2.video[1].src_in = cut2.video[0].src_out                                                          # already contiguous
    assert "already run on" in opsmod.measure_join(cut2, 1, "next")["reason"]


def test_a_measured_fix_may_reach_and_remove_more_than_the_ordinary_guards_allow(xml, monkeypatch):
    """The off-camera voice at the start of a clip is longer than the 6.6 s the start search looks at and the 3 s it may remove unconfirmed; the check that measured it passes both, sized to the stretch."""
    import ops as opsmod
    import timeline
    import words as words_mod
    cut = timeline.load_cut(xml)
    seen = {}

    def fake_words(path, start, dur):
        seen["dur"] = dur
        return [words_mod.W(t, start + a, start + b) for t, a, b in (("tell", 0.7, 0.9), ("me", 0.9, 1.0), ("about", 1.0, 1.3), ("so", 7.6, 7.8), ("with", 7.8, 7.95), ("the", 7.95, 8.05), ("septic", 8.05, 8.4))]
    monkeypatch.setattr(words_mod, "words_in", fake_words)
    monkeypatch.setattr(words_mod, "valley", lambda path, a, b: b)
    refused = opsmod.locate_start(cut, 2, "so with the septic")                                      # the ordinary guards: not found in 6.6 s
    assert "reason" in refused and seen["dur"] == 6.6
    ok = opsmod.locate_start(cut, 2, "so with the septic", reach=10.0, max_trim=8.0)
    assert "trim" in ok and 6.5 < ok["trim"] < 8.0 and seen["dur"] == 10.6


def test_validate_bounds_a_reach_that_came_with_a_trusted_fix(xml):
    import ops as opsmod
    import timeline
    cut = timeline.load_cut(xml)
    note = {"timeline_sec": 12.0, "clip": 2, "text": "AI: x", "suggested_op": {"op": "start_at_words", "clip": 2, "words": "so with the septic", "reach": 400.0, "max_trim": 999.0}}
    got = opsmod.validate(opsmod.from_suggestions([note], cut), [note], cut)
    assert got[0]["op"] == "start_at_words" and got[0]["reach"] == 30.0 and got[0]["max_trim"] == 30.0            # bounded, whatever was handed in
    plain = {"timeline_sec": 12.0, "clip": 2, "text": "AI: x", "suggested_op": {"op": "start_at_words", "clip": 2, "words": "so with the septic"}}
    got = opsmod.validate(opsmod.from_suggestions([plain], cut), [plain], cut)
    assert "reach" not in got[0] and "max_trim" not in got[0]                                                      # an ordinary fix keeps the ordinary guards
