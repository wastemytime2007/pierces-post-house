"""Hermetic tests for keeping a callout on screen longer. The render is faked; synthetic media only."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import change_callout as hc  # noqa: E402
import layers as ly  # noqa: E402
import place_overlay as po  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", HERE.parent / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
BOX = {"x0": 0.33, "y0": 0.48, "x1": 0.5, "y1": 0.6}


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("hold")
    vid, lav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.3", "-af", "lowpass=f=3000", str(lav)], check=True)
    cam = d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return d, vid, lav


@pytest.fixture()
def placed(tmp_path, cut_media):
    """A cut with a callout placed on it: on screen from about 11.5s to 13.5s."""
    _d, vid, lav = cut_media
    base = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    f = tmp_path / "callout"
    f.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red@0.8:s=320x60:r=60000/1001:d=2,format=yuva444p10le,pad=320:180:0:0:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(f / "overlay.mov")], check=True)
    (f / "placement.json").write_text(json.dumps({
        "overlay_path": str(f / "overlay.mov"), "duration_sec": 2.0, "place_overlay_on_timeline_at_sec": 11.5,
        "anchor": {"source": "a.mp4", "source_sec": 22.0, "lead_sec": 0.5}, "note": 3, "note_text": "arrow here",
        "title": "The spacer", "subtitle": "a piece of cardboard", "region": BOX,
        "geometry": {"t_in": 0.5, "t_out": 1.5, "fade_out": 0.35, "total": 2.2}}))
    xml = tmp_path / "layers_v3.xml"
    po.place(base, xml, f)
    return xml, f


class Recorder:
    def __init__(self, gain=None):
        self.calls, self.gain = [], gain

    def __call__(self, note, timeline, out, title, subtitle, spec, note_no, hold=None):
        self.calls.append({"note": note, "hold": hold, "title": title, "subtitle": subtitle, "clips": len(timeline["clips"]), "note_no": note_no})
        out.mkdir(parents=True, exist_ok=True)
        got = hold if self.gain is None else 1.0 + self.gain
        return 0, {"hold": got, "hold_wanted": hold}


def _apply(xml, ops, notes, tmp_path, **kw):
    spec = {"width": 320, "height": 180, "fps": 59.94, "fps_arg": "60000/1001", "resolution": None}      # the synthetic cut is not a size real callouts render at
    return hc.apply(ops, notes, xml, tmp_path / "new", build=kw.pop("build"), spec_of=lambda _x: spec, **kw)


def test_the_callout_on_screen_at_the_notes_moment_is_the_one_found(placed):
    xml, _f = placed
    layers = ly.find_layers(xml)
    mid = (layers[0].start + layers[0].end) / 2
    assert hc.callout_under(layers, mid).name == "overlay.mov"
    assert hc.callout_under(layers, layers[0].end + 1.0) is None and hc.callout_under(layers, layers[0].start - 1.0) is None


def test_a_note_without_an_amount_gets_the_stated_default_and_says_so(placed, tmp_path):
    xml, _f = placed
    rec = Recorder()
    t = (ly.find_layers(xml)[0].start + ly.find_layers(xml)[0].end) / 2
    (e,) = _apply(xml, [{"note": 1, "op": "extend_graphic", "seconds": None}], [{"timeline_sec": t, "text": "keep the bubble up longer"}], tmp_path, build=rec)
    assert e["applied"] and e["asked_extra_sec"] == hc.DEFAULT_EXTRA_SEC and "default step" in e["amount_from"] and "the note gave no amount" in e["amount_from"]
    assert rec.calls[0]["hold"] == pytest.approx(1.0 + hc.DEFAULT_EXTRA_SEC)                       # was 1.5 - 0.5 = 1.0s of hold
    assert rec.calls[0]["title"] == "The spacer" and rec.calls[0]["note"]["region"] == BOX           # same words, same drawn region
    assert rec.calls[0]["note"]["source"] == "a.mp4" and rec.calls[0]["note"]["source_sec"] == 22.0  # same anchor frame
    assert (tmp_path / "new" / "hold_change.json").exists()


def test_a_stated_amount_is_used_as_stated(placed, tmp_path):
    xml, _f = placed
    rec = Recorder()
    t = ly.find_layers(xml)[0].start + 0.8
    (e,) = _apply(xml, [{"note": 1, "op": "extend_graphic", "seconds": 2.0}], [{"timeline_sec": t, "text": "two more seconds"}], tmp_path, build=rec)
    assert e["applied"] and e["amount_from"] == "the note" and rec.calls[0]["hold"] == pytest.approx(3.0) and e["gained_sec"] == pytest.approx(2.0)


def test_a_change_the_shot_has_no_room_for_is_reported_not_applied(placed, tmp_path):
    xml, _f = placed
    t = ly.find_layers(xml)[0].start + 0.8
    (e,) = _apply(xml, [{"note": 1, "op": "extend_graphic", "seconds": 2.0}], [{"timeline_sec": t, "text": "two more seconds"}], tmp_path, build=Recorder(gain=0.1))
    assert not e["applied"] and "under the 0.3s minimum" in e["reason"] and not (tmp_path / "new" / "hold_change.json").exists()


def test_a_partial_gain_is_applied_and_says_how_much_fit(placed, tmp_path):
    xml, _f = placed
    t = ly.find_layers(xml)[0].start + 0.8
    (e,) = _apply(xml, [{"note": 1, "op": "extend_graphic", "seconds": 2.0}], [{"timeline_sec": t, "text": "two more seconds"}], tmp_path, build=Recorder(gain=0.8))
    assert e["applied"] and e["gained_sec"] == pytest.approx(0.8) and "the shot ends" in e["reason"]


def test_a_note_when_no_callout_is_on_screen_and_a_second_note_for_the_same_callout(placed, tmp_path):
    xml, _f = placed
    lay = ly.find_layers(xml)[0]
    ops = [{"note": 1, "op": "extend_graphic", "seconds": None}, {"note": 2, "op": "extend_graphic", "seconds": None}, {"note": 3, "op": "extend_graphic", "seconds": None}]
    notes = [{"timeline_sec": lay.start + 0.8, "text": "a"}, {"timeline_sec": lay.start + 1.2, "text": "b"}, {"timeline_sec": 3.0, "text": "c"}]
    rec = Recorder()
    ledger = _apply(xml, ops, notes, tmp_path, build=rec)
    assert [e["applied"] for e in ledger] == [True, False, False] and len(rec.calls) == 1
    assert "one extension per callout per run" in ledger[1]["reason"] and "no callout is on screen at 3.00s" in ledger[2]["reason"]


def _edit(note, **kw):
    return {"note": note, "op": "edit_callout", "title": kw.get("title"), "subtitle": kw.get("subtitle"), "remove_subtitle": kw.get("remove_subtitle", False)}


def test_new_words_and_a_removed_second_line_are_rendered_once_with_the_old_hold(placed, tmp_path):
    xml, _f = placed
    lay = ly.find_layers(xml)[0]
    rec = Recorder()
    notes = [{"timeline_sec": lay.start + 0.8, "text": "call it Cardboard spacer and drop the small line"}]
    (e,) = _apply(xml, [_edit(1, title="Cardboard spacer", remove_subtitle=True)], notes, tmp_path, build=rec)
    assert e["applied"] and len(rec.calls) == 1
    assert rec.calls[0]["title"] == "Cardboard spacer" and rec.calls[0]["subtitle"] == "" and rec.calls[0]["hold"] == pytest.approx(1.0)      # the hold is untouched
    assert 'title "The spacer" -> "Cardboard spacer"' in e["reason"] and "second line removed" in e["reason"]
    assert (tmp_path / "new" / "hold_change.json").exists()


def test_a_subtitle_change_keeps_the_title_and_a_longer_hold_in_the_same_render(placed, tmp_path):
    xml, _f = placed
    lay = ly.find_layers(xml)[0]
    rec = Recorder()
    notes = [{"timeline_sec": lay.start + 0.8, "text": "longer"}, {"timeline_sec": lay.start + 0.9, "text": "change the small line to cardboard off the box"}]
    ops = [{"note": 1, "op": "extend_graphic", "seconds": 2.0}, _edit(2, subtitle="cardboard off the box")]
    ledger = _apply(xml, ops, notes, tmp_path, build=rec)
    assert [e["applied"] for e in ledger] == [True, True] and len(rec.calls) == 1                       # one render carries both changes
    assert rec.calls[0]["title"] == "The spacer" and rec.calls[0]["subtitle"] == "cardboard off the box" and rec.calls[0]["hold"] == pytest.approx(3.0)


def test_two_notes_for_the_same_words_apply_the_first_and_report_the_second(placed, tmp_path):
    xml, _f = placed
    lay = ly.find_layers(xml)[0]
    notes = [{"timeline_sec": lay.start + 0.8, "text": "a"}, {"timeline_sec": lay.start + 0.9, "text": "b"}]
    rec = Recorder()
    ledger = _apply(xml, [_edit(1, title="First"), _edit(2, title="Second")], notes, tmp_path, build=rec)
    assert [e["applied"] for e in ledger] == [True, False] and "already changed this callout's title" in ledger[1]["reason"]
    assert rec.calls[0]["title"] == "First"


def test_words_that_already_read_that_way_say_so(placed, tmp_path):
    xml, _f = placed
    lay = ly.find_layers(xml)[0]
    (e,) = _apply(xml, [_edit(1, title="The spacer")], [{"timeline_sec": lay.start + 0.8, "text": "call it The spacer"}], tmp_path, build=Recorder())
    assert e["applied"] and "nothing to change" in e["reason"]


def test_other_operations_are_ignored(placed, tmp_path):
    xml, _f = placed
    assert _apply(xml, [{"note": 1, "op": "tighten_pause", "at": 3.0}], [{"timeline_sec": 3.0, "text": "x"}], tmp_path, build=Recorder()) == []


def test_a_callout_whose_placement_record_is_missing_cannot_be_rebuilt(placed, tmp_path):
    xml, f = placed
    (f / "placement.json").unlink()
    t = ly.find_layers(xml)[0].start + 0.8
    (e,) = _apply(xml, [{"note": 1, "op": "extend_graphic", "seconds": None}], [{"timeline_sec": t, "text": "longer"}], tmp_path, build=Recorder())
    assert not e["applied"] and "placement.json is not beside" in e["reason"]


def test_the_timeline_handed_to_the_renderer_is_the_target_cuts_own(placed):
    xml, _f = placed
    import timeline
    td = hc.timeline_dict(timeline.load_cut(xml))
    assert [c["idx"] for c in td["clips"]] == [1, 2, 3] and set(td["clips"][0]) >= {"idx", "start", "end", "source", "source_path", "src_in", "src_out"}
    assert td["audio"] and set(td["audio"][0]) >= {"start", "end", "source_path", "src_in"}
