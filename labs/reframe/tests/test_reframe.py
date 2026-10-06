import copy
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reframe_xml as rf

FIXTURE = Path(__file__).resolve().parents[3] / "safety_net/fixtures/premiere_motion/carpet_sub_01_scale118_center_0.257812.xml"


def _premiere_filter() -> ET.Element:
    root = ET.fromstring(FIXTURE.read_text()[FIXTURE.read_text().index("<xmeml"):])
    return [f for f in root.iter("filter") if f.findtext("effect/name") == "Basic Motion"][0]


def _shape(f: ET.Element):
    """Tags, parameter ids and attributes, in order: what Premiere needs to see, without the numbers."""
    return [(p.findtext("parameterid"), p.findtext("name"), tuple(sorted(p.attrib.items())), [c.tag for c in p]) for p in f.findall("effect/parameter")], [c.tag for c in f.find("effect")]


def test_the_written_motion_block_has_exactly_the_structure_premiere_wrote():
    mine = rf.motion_filter(118, 0.257812)
    assert _shape(mine) == _shape(_premiere_filter())
    assert mine.findtext("effect/parameter/value") == "118" and mine.find("effect/parameter[2]/value").text == "0"


def test_the_rule_reproduces_premieres_own_export_both_ways():
    """Position 1530, scale 118 on a 3840-wide clip in a 1080 sequence is center.horiz 0.2578125; the source pixel in mid-frame is then 1081."""
    f = _premiere_filter()
    vals = {p.findtext("parameterid"): p for p in f.findall("effect/parameter")}
    horiz, scale = float(vals["center"].findtext("value/horiz")), float(vals["scale"].findtext("value"))
    assert horiz == pytest.approx((1530 - 1080 / 2) / 3840, abs=1e-6)
    x_s = rf.subject_for_horiz(horiz, scale)
    assert x_s == pytest.approx(1081.0, abs=0.1)
    assert rf.horiz_for_subject(x_s, 118) == pytest.approx(horiz, abs=1e-9)             # and back, exactly
    assert rf.horiz_for_subject(1081.0, 118) == pytest.approx(horiz, abs=1e-5)          # a whole-pixel subject is within a hundred-thousandth of what Premiere wrote


def test_subject_and_horiz_round_trip_at_any_scale():
    for x in (700, 1221, 1920, 2554, 3100):
        for s in (88.89, 100, 123.5):
            assert rf.subject_for_horiz(rf.horiz_for_subject(x, s), s) == pytest.approx(x, abs=1e-6)
    assert rf.horiz_for_subject(1920, 100) == 0                                          # the centre of the clip needs no move
    assert rf.horiz_for_subject(1000, 100) > 0 and rf.horiz_for_subject(3000, 100) < 0   # a subject left of centre pushes the clip right, and the reverse


def test_a_frame_that_would_show_beyond_the_clip_is_refused():
    assert rf.window_inside(2554, 88.89, 3840, 2160, 1080, 1920)[0]                      # full height, right-hand speaker
    assert rf.window_inside(1221, 123.5, 3840, 2160, 1080, 1920)[0]                      # punched in, left-hand speaker
    assert not rf.window_inside(300, 88.89, 3840, 2160, 1080, 1920)[0]                   # too far left: the left edge of the clip shows
    assert not rf.window_inside(1920, 70.0, 3840, 2160, 1080, 1920)[0]                   # scale too small to fill the height


def _xml(tmp_path: Path) -> Path:
    def clip(i, s, e, name, ci_in=100):
        return (f"<clipitem id='v{i}'><name>{name}</name><enabled>TRUE</enabled><duration>{e - s}</duration><start>{s}</start><end>{e}</end><in>{ci_in}</in><out>{ci_in + e - s}</out>"
                f"<masterclipid>m1</masterclipid><file id='f1'><name>{name}</name></file></clipitem>")

    def mic(i, s, e, name):
        return f"<clipitem id='a{i}'><name>{name}</name><enabled>TRUE</enabled><start>{s}</start><end>{e}</end><in>0</in><out>{e - s}</out><file id='f{i}'/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>"
    x = (f"<xmeml><sequence><name>t</name><media><video><format><samplecharacteristics><width>3840</width><height>2160</height></samplecharacteristics></format>"
         f"<track>{clip(1, 0, 90, 'cam.mp4')}{clip(2, 90, 150, 'cam.mp4', 300)}</track></video>"
         f"<audio><track>{clip(3, 0, 90, 'cam.mp4')}{clip(4, 90, 150, 'cam.mp4', 300)}</track>"
         f"<track>{mic(5, 0, 90, 'Mitch.WAV')}{mic(6, 90, 150, 'Mitch.WAV')}</track><track>{mic(7, 0, 90, 'Bob.WAV')}{mic(8, 90, 150, 'Bob.WAV')}</track></audio></media></sequence></xmeml>")
    p = tmp_path / "in.xml"
    p.write_text(x)
    return p


PLAN = {"source_width": 3840, "source_height": 2160,
        "pieces": [{"start": 0, "end": 90, "person": "Mitch", "subject_x": 2554, "scale": 88.89}, {"start": 90, "end": 150, "person": "Bob", "subject_x": 1221, "scale": 123.5}],
        "mics": {"Mitch.WAV": "Mitch", "Bob.WAV": "Bob"}}


def test_reframe_sets_the_vertical_size_adds_one_motion_per_picture_clip_after_the_file_and_keeps_only_the_speakers_mic(tmp_path):
    src, out = _xml(tmp_path), tmp_path / "out.xml"
    info = rf.reframe(src, out, PLAN)
    assert info["framed"] == 2 and info["mic_clips_muted"] == 2
    root = ET.parse(out).getroot()
    seq = root.find("sequence")
    assert (seq.findtext("media/video/format/samplecharacteristics/width"), seq.findtext("media/video/format/samplecharacteristics/height")) == ("1080", "1920")
    for ci in seq.find("media/video/track").findall("clipitem"):
        kids = [c.tag for c in ci]
        assert kids.index("filter") == kids.index("file") + 1 and kids.count("filter") == 1
    en = {(c.findtext("name"), int(c.findtext("start"))): c.findtext("enabled") for t in seq.findall("media/audio/track")[1:] for c in t.findall("clipitem")}
    assert en == {("Mitch.WAV", 0): "TRUE", ("Mitch.WAV", 90): "FALSE", ("Bob.WAV", 0): "FALSE", ("Bob.WAV", 90): "TRUE"}     # Mitch talks first, then Bob
    assert out.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')
    rows = {n: g for n, g, _ in rf.verify(src, out, PLAN)}
    assert all(rows.values()), rows


def test_reframe_refuses_a_clip_with_no_plan_row_and_a_plan_row_with_no_clip(tmp_path):
    src = _xml(tmp_path)
    short = copy.deepcopy(PLAN)
    short["pieces"] = short["pieces"][:1]
    with pytest.raises(rf.ReframeError, match="no row in the plan"):
        rf.reframe(src, tmp_path / "o.xml", short)
    extra = copy.deepcopy(PLAN)
    extra["pieces"].append({"start": 150, "end": 200, "person": "Bob", "subject_x": 1221, "scale": 123.5})
    with pytest.raises(rf.ReframeError, match="without a picture clip"):
        rf.reframe(src, tmp_path / "o.xml", extra)


def test_verify_catches_a_wrong_framing_a_wrong_microphone_and_a_changed_clip(tmp_path):
    src, out = _xml(tmp_path), tmp_path / "out.xml"
    rf.reframe(src, out, PLAN)
    tree = ET.parse(out)
    v = tree.getroot().find("sequence/media/video/track/clipitem")
    v.find("filter/effect/parameter[3]/value/horiz").text = "0.5"                          # moved the framing off the subject
    a = tree.getroot().findall("sequence/media/audio/track")[1].find("clipitem")
    a.find("enabled").text = "FALSE"                                                       # Mitch is talking in the first piece and his mic is off
    tree.getroot().find("sequence/media/video/track/clipitem[2]/in").text = "301"          # and a clip's in point changed
    tree.write(out)
    rows = {n: g for n, g, _ in rf.verify(src, out, PLAN)}
    assert not rows["EVERY-CLIP-FRAMED"] and not rows["ONLY-THE-SPEAKER-LIVE"] and not rows["REST-UNCHANGED"]


def test_the_vertical_rule_mirrors_the_horizontal_one_and_round_trips():
    for y in (700, 900, 1080, 1300):
        for s in (88.89, 123.5):
            assert rf.subject_y_for_vert(rf.vert_for_subject(y, s), s) == pytest.approx(y, abs=1e-6)
    assert rf.vert_for_subject(1080, 100) == 0                                              # the clip's own centre needs no move
    assert rf.vert_for_subject(900, 123.5) > 0                                              # a window moved UP the source puts the picture LOWER on screen: the clip is pushed down (positive)
    assert rf.expected_position_y(900, 123.5, 1920) == pytest.approx(960 + 180 * 1.235, abs=0.01)


def test_a_lowered_clip_is_written_decoded_back_and_flagged_as_resting_on_an_assumption(tmp_path):
    src, out = _xml(tmp_path), tmp_path / "out.xml"
    import copy
    plan = copy.deepcopy(PLAN)
    plan["pieces"][1]["subject_y"] = 900
    rf.reframe(src, out, plan)
    root = ET.parse(out).getroot()
    f = root.findall(".//video/track/clipitem")[1].find("filter")
    vert = float(f.find("effect/parameter[3]/value/vert").text)
    assert vert == pytest.approx(rf.vert_for_subject(900, 123.5), abs=1e-6) and vert > 0
    first = root.findall(".//video/track/clipitem")[0].find("filter")
    assert first.find("effect/parameter[3]/value/vert").text == "0"                          # a row with no subject_y stays centred
    rows = {n: (g, d) for n, g, d in rf.verify(src, out, plan)}
    assert rows["EVERY-CLIP-FRAMED"][0] and rows["FRAME-INSIDE-THE-CLIP"][0]
    assert "info: VERTICAL-UNIT-ASSUMED" in rows and "Position y 1182" in rows["info: VERTICAL-UNIT-ASSUMED"][1]


def test_a_lowered_window_that_would_leave_the_top_of_the_clip_is_refused():
    assert rf.window_inside(1221, 123.5, 3840, 2160, 1080, 1920, subject_y=900)[0]
    assert not rf.window_inside(1221, 123.5, 3840, 2160, 1080, 1920, subject_y=700)[0]      # 700 - 777 is above the top of the picture
