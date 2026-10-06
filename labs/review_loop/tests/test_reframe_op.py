"""reframe_vertical: a note like "lower this shot so his head isn't cropped" moves the clip's Basic Motion, nothing else.

Reuses test_revise's synthetic media and cut (a 320x180 source in a 320x180 sequence); every video clip is given a 150% punch-in so there is 60 px of picture to move through.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "reframe"))
sys.path.insert(0, str(HERE.parents[1] / "qa"))

import reframe_xml as rx  # noqa: E402
import test_revise as tr  # noqa: E402
from test_revise import FPS, media, xml  # noqa: E402,F401  (fixtures)

import apply_ops  # noqa: E402
import ops as opsmod  # noqa: E402
import revise  # noqa: E402
import timeline  # noqa: E402

pytestmark = tr.pytestmark


def _motion(scale, vert):
    return (f"<filter><effect><name>Basic Motion</name><parameter><parameterid>scale</parameterid><value>{scale}</value></parameter>"
            f"<parameter><parameterid>center</parameterid><value><horiz>0</horiz><vert>{vert}</vert></value></parameter></effect></filter>")


@pytest.fixture()
def framed(tmp_path, xml):
    """The test cut with a 150% punch-in, centred, on the three cut clips (not the pool)."""
    t = ET.parse(xml)
    seq = t.getroot().find("sequence")
    for ci in seq.findall("media/video/track/clipitem")[:3]:
        ci.append(ET.fromstring(_motion(150, 0)))
    out = tmp_path / "framed.xml"
    t.write(out, encoding="UTF-8", xml_declaration=True)
    return out


def _verts(path):
    seq = ET.parse(path).getroot().find("sequence")
    return [timeline.motion_of(ci) for ci in seq.findall("media/video/track/clipitem")]


NOTES = [{"timeline_sec": 12.0, "clip": 2, "text": "Lower this shot so his head isn't cropped off at the top"},
         {"timeline_sec": 12.0, "clip": 2, "text": "Raise this shot a bit"},
         {"timeline_sec": 12.0, "clip": 2, "text": "Make the pause shorter"}]


def test_validate_takes_the_direction_only_from_the_notes_own_words(framed):
    cut = timeline.load_cut(framed)
    ops = [{"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"},
           {"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "raise"},       # the note says lower
           {"note": 2, "op": "reframe_vertical", "clip": 2, "direction": "raise"},
           {"note": 3, "op": "reframe_vertical", "clip": 2, "direction": "lower"},      # says nothing about framing
           {"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "sideways"},
           {"note": 1, "op": "reframe_vertical", "clip": 99, "direction": "lower"}]
    got = [o for o in opsmod.validate(ops, NOTES, cut) if o["op"] == "reframe_vertical"]
    assert [(o["note"], o["direction"]) for o in got] == [(1, "lower"), (2, "raise")]


def test_lowering_a_shot_moves_only_that_clips_vertical_position_down_and_changes_no_timing(tmp_path, framed):
    cut = timeline.load_cut(framed)
    out = tmp_path / "v2.xml"
    changes, delta = apply_ops.apply_ops(framed, out, cut, [{"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"}], NOTES)
    assert changes[0].applied and delta == 0
    before, after = _verts(framed), _verts(out)
    assert after[0] == before[0] and after[2] == before[2]                            # other clips untouched
    assert after[1][0] == 150 and after[1][2] > 0                                     # same scale, picture lower on screen (positive = down, the unconfirmed rule)
    step = apply_ops.REFRAME_STEP * (180 / 1.5)                                       # 12% of the 120 px window, in source px
    y0, y1 = rx.subject_y_for_vert(0, 150, 180), rx.subject_y_for_vert(after[1][2], 150, 180)
    assert y0 - y1 == pytest.approx(step, abs=0.1)                                    # the window moved up the source by one step
    assert tr._items(out, "video") == tr._items(framed, "video")                       # no timing change
    assert "Position y should now read" in changes[0].summary


def test_an_overlay_clip_on_a_second_video_track_does_not_shift_which_clip_is_moved(tmp_path, framed):
    """The real cut has captions/graphics on V2, one starting at frame 0. Counting clips across both tracks moved cut clip 3 when the note was about clip 4."""
    t = ET.parse(framed)
    seq = t.getroot().find("sequence")
    v1 = seq.find("media/video/track")
    ov = ET.fromstring('<track><clipitem id="ov1"><name>caption</name><enabled>TRUE</enabled><start>0</start><end>100</end><in>0</in><out>100</out><file id="ovf"/></clipitem></track>')
    v1.addnext(ov) if hasattr(v1, "addnext") else seq.find("media/video").insert(list(seq.find("media/video")).index(v1) + 1, ov)
    both = tmp_path / "two_tracks.xml"
    t.write(both, encoding="UTF-8", xml_declaration=True)
    cut = timeline.load_cut(both)
    out = tmp_path / "v2.xml"
    changes, _ = apply_ops.apply_ops(both, out, cut, [{"note": 1, "op": "reframe_vertical", "clip": 3, "direction": "lower"}], NOTES)
    assert changes[0].applied
    after = [c.motion for c in timeline.load_cut(out).video]
    before = [c.motion for c in cut.video]
    assert after[2][2] > 0 and after[0] == before[0] and after[1] == before[1]         # clip 3 moved, 1 and 2 did not


def test_raising_goes_the_other_way_and_the_edge_of_the_picture_stops_it(tmp_path, framed):
    cut = timeline.load_cut(framed)
    ops = [{"note": 2, "op": "reframe_vertical", "clip": 2, "direction": "raise"}]
    out = tmp_path / "up.xml"
    changes, _ = apply_ops.apply_ops(framed, out, cut, ops, NOTES)
    assert _verts(out)[1][2] < 0
    cur = out
    for n in range(12):                                                                # keep raising until it cannot go further
        nxt = tmp_path / f"up{n}.xml"
        cur_cut = timeline.load_cut(cur)
        changes, _ = apply_ops.apply_ops(cur, nxt, cur_cut, ops, NOTES)
        if not changes[0].applied:
            assert "already at the bottom edge" in changes[0].summary
            break
        cur = nxt
    else:
        pytest.fail("never reached the edge")
    y = rx.subject_y_for_vert(_verts(cur)[1][2], 150, 180)
    assert y == pytest.approx(180 - (180 / 1.5) / 2, abs=0.5)                          # window flush with the bottom of the picture


def test_a_clip_with_no_motion_is_reported_not_guessed(tmp_path, xml):
    cut = timeline.load_cut(xml)
    changes, delta = apply_ops.apply_ops(xml, tmp_path / "v2.xml", cut, [{"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"}], NOTES)
    assert not changes[0].applied and "nothing to move" in changes[0].summary and delta == 0


def test_revise_reads_the_reframe_back_from_the_revised_xml(tmp_path, framed):
    cut = timeline.load_cut(framed)
    out = tmp_path / "v2.xml"
    changes, _ = apply_ops.apply_ops(framed, out, cut, [{"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"}], NOTES)
    rows = revise.verify_motion(cut, changes, timeline.load_cut(out))
    assert len(rows) == 1 and rows[0][1] is True
    rows_bad = revise.verify_motion(cut, changes, cut)                                 # the old XML does not carry the move
    assert rows_bad[0][1] is False


def test_qa_measures_the_reframe_from_the_two_xmls_and_fails_a_move_the_wrong_way(tmp_path, framed):
    import qa_pass
    cut = timeline.load_cut(framed)
    out = tmp_path / "v2.xml"
    apply_ops.apply_ops(framed, out, cut, [{"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"}], NOTES)
    new = timeline.load_cut(out)
    item = {"applied": True, "summary": "x"}
    o = {"note": 1, "op": "reframe_vertical", "clip": 2, "direction": "lower"}
    good = qa_pass.check_op(o, item, NOTES[0], cut, new, {}, None, {})
    wrong = qa_pass.check_op({**o, "direction": "raise"}, item, NOTES[0], cut, new, {}, None, {})
    assert good.status == qa_pass.VERIFIED and wrong.status == qa_pass.FAILED
