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


# ---- "have the graphic fade out here" ----

def _long(f):
    pl = json.loads((f / "placement.json").read_text())
    pl["geometry"] = {"t_in": 0.5, "t_out": 5.5, "fade_out": 0.35, "total": 6.2}                  # a callout that holds 5.0 s
    (f / "placement.json").write_text(json.dumps(pl))


def _end_note(layer, t_after_start):
    return {"timeline_sec": round(layer.start + t_after_start, 2), "text": "have the graphic fade out here",
            "target": {"lane": "Callout", "label": "callout", "start": round(layer.start, 2), "end": round(layer.end, 2)}}


def test_a_note_can_end_the_callout_at_its_moment(placed, tmp_path):
    xml, f = placed
    _long(f)
    lay = ly.find_layers(xml)[0]
    rec = Recorder()
    led = _apply(xml, [{"note": 1, "op": "end_graphic"}], [_end_note(lay, 3.5)], tmp_path, build=rec)
    assert led[0]["applied"] and led[0]["end_at_sec"] == round(lay.start + 3.5, 2)
    assert rec.calls[0]["hold"] == pytest.approx(3.0, abs=0.02)                              # note moment - layer start - the 0.5 s lead-in
    assert led[0]["now_hold_sec"] == pytest.approx(3.0, abs=0.02) and "fades out from" in led[0]["reason"]


def test_ending_a_callout_too_early_or_after_it_already_ended_is_refused_with_the_reason(placed, tmp_path):
    xml, f = placed
    _long(f)
    lay = ly.find_layers(xml)[0]
    rec = Recorder()
    early = _apply(xml, [{"note": 1, "op": "end_graphic"}], [_end_note(lay, 1.2)], tmp_path, build=rec)
    assert not early[0]["applied"] and "needed to read it" in early[0]["reason"] and not rec.calls
    late = _apply(xml, [{"note": 1, "op": "end_graphic"}], [_end_note(lay, 9.0)], tmp_path, build=rec)
    assert not late[0]["applied"] and "already fades" in late[0]["reason"] and not rec.calls


def test_ending_and_extending_the_same_callout_in_one_run_is_reported_not_guessed(placed, tmp_path):
    xml, f = placed
    _long(f)
    lay = ly.find_layers(xml)[0]
    n = _end_note(lay, 3.5)
    led = _apply(xml, [{"note": 1, "op": "end_graphic"}, {"note": 2, "op": "extend_graphic", "seconds": 2}], [n, dict(n, text="keep it longer")], tmp_path, build=Recorder())
    assert led[0]["applied"] and not led[1]["applied"] and "one change to the hold" in led[1]["reason"] or "another note" in led[1]["reason"]


def test_the_vocabulary_ends_a_callout_only_for_a_note_about_ending_it():
    sys.path.insert(0, str(HERE.parent / "review_loop"))
    import ops as opsmod
    cut = type("C", (), {"video": [object()], "zone_end": 70.0})()
    target = {"lane": "Callout", "label": "callout", "start": 12.18, "end": 18.68}
    notes = [{"timeline_sec": 15.43, "text": "have the graphic fade out here", "target": target},
             {"timeline_sec": 15.43, "text": "make this bigger", "target": target},
             {"timeline_sec": 15.43, "text": "fade out here", "target": dict(target, lane="SFX")},
             {"timeline_sec": 15.43, "text": "let the text bubble disappear here"}]
    got = {o["note"]: o for o in opsmod.validate([{"note": i, "op": "end_graphic"} for i in (1, 2, 3, 4)], notes, cut)}
    assert got[1]["op"] == "end_graphic" and got[4]["op"] == "end_graphic"
    assert got[2]["op"] == "unsupported" and "does not ask for the graphic to end" in got[2]["reason"]
    assert got[3]["op"] == "unsupported" and "left on a SFX element" in got[3]["reason"]


def test_qa_checks_the_callout_really_ends_at_the_notes_moment(monkeypatch):
    sys.path.insert(0, str(HERE.parent / "qa"))
    import qa_pass as qa
    mk = lambda s, e: ly.Layer("video", "overlay.mov", "/x/overlay.mov", s, e, 1)                     # noqa: E731
    note = {"timeline_sec": 15.43, "text": "fade out here"}
    monkeypatch.setattr(qa, "_callout_pair", lambda *a: (None, (mk(12.18, 18.68), mk(12.18, 15.9))))
    assert qa.check_end_graphic({"note": 1, "op": "end_graphic"}, note, ("b", "a")).status == qa.VERIFIED
    monkeypatch.setattr(qa, "_callout_pair", lambda *a: (None, (mk(12.18, 18.68), mk(12.18, 18.68))))       # negative control: nothing changed
    assert qa.check_end_graphic({"note": 1, "op": "end_graphic"}, note, ("b", "a")).status == qa.FAILED
    monkeypatch.setattr(qa, "_callout_pair", lambda *a: (None, (mk(12.18, 18.68), mk(12.18, 14.0))))         # ended far too early
    assert qa.check_end_graphic({"note": 1, "op": "end_graphic"}, note, ("b", "a")).status == qa.FAILED


# ---- taking a graphic out ----

def test_remove_graphic_finds_the_callout_or_card_a_note_is_about_and_nothing_else(tmp_path):
    sys.path.insert(0, str(HERE))
    import remove_graphic as rg
    mk = lambda name, s, e, folder: ly.Layer("video", name, str(tmp_path / folder / name), s, e, 1)             # noqa: E731
    layers = [mk("overlay.mov", 12.0, 18.0, "c"), mk("card.mov", 29.5, 33.3, "k"), mk("captions.mov", 0.0, 21.0, "p")]
    # by moment: the note says "remove it" at 30.54 and no box was clicked
    lay, why = rg.find(layers, {"timeline_sec": 30.54})
    assert lay.name == "card.mov" and why == ""
    lay, why = rg.find(layers, {"timeline_sec": 5.0})
    assert lay is None and "no callout or card is on screen" in why                                           # captions are on screen at 5 s; they are not removable this way
    # by element: the box that was clicked names it, whatever the moment
    lay, _ = rg.find(layers, {"timeline_sec": 15.0, "target": {"lane": "Callout", "start": 12.0}})
    assert lay.name == "overlay.mov"
    lay, why = rg.find(layers, {"timeline_sec": 10.0, "target": {"lane": "Captions", "start": 0.0}})
    assert lay is None and "cannot remove" in why
    # two graphics at once is ambiguous and is reported, not guessed
    two = layers + [mk("card2.mov", 29.0, 31.0, "k2")]
    lay, why = rg.find(two, {"timeline_sec": 30.54})
    assert lay is None and "click the one to remove" in why


def test_remove_graphic_writes_the_folder_to_leave_out_and_reports_the_rest(tmp_path, monkeypatch):
    sys.path.insert(0, str(HERE))
    import remove_graphic as rg
    lay = ly.Layer("video", "card.mov", str(tmp_path / "card" / "card.mov"), 29.5, 33.3, 1)
    monkeypatch.setattr(rg.ly, "find_layers", lambda x: [lay])
    led = rg.apply([{"note": 1, "op": "remove_graphic"}, {"note": 2, "op": "remove_graphic"}, {"note": 3, "op": "tighten_pause"}],
                   [{"timeline_sec": 30.5, "text": "remove it"}, {"timeline_sec": 30.6, "text": "remove it too"}, {"timeline_sec": 1, "text": "x"}], Path("x.xml"))
    assert [e["applied"] for e in led] == [True, False] and led[0]["folder"] == str(tmp_path / "card") and "already removes" in led[1]["reason"]


def test_qa_checks_the_graphic_is_really_gone_and_the_others_are_still_there():
    sys.path.insert(0, str(HERE.parent / "qa"))
    import qa_pass as qa
    mk = lambda name, s, e: ly.Layer("video", name, "/x/" + name, s, e, 1)                                      # noqa: E731
    before = [mk("overlay.mov", 12.0, 18.0), mk("card.mov", 29.5, 33.3)]
    note = {"timeline_sec": 30.54, "text": "remove it"}
    o = {"note": 1, "op": "remove_graphic"}
    assert qa.check_remove_graphic(o, note, (before, [mk("overlay.mov", 12.0, 18.0)])).status == qa.VERIFIED
    assert qa.check_remove_graphic(o, note, (before, before)).status == qa.FAILED                              # negative control: nothing was removed
    assert qa.check_remove_graphic(o, note, (before, [])).status == qa.FAILED                                  # the callout was lost too
    assert qa.check_remove_graphic(o, {"timeline_sec": 5.0, "text": "remove it"}, (before, before)).status == qa.NOT_DONE


def test_qa_checks_the_caption_reads_the_new_words(tmp_path):
    sys.path.insert(0, str(HERE.parent / "qa"))
    import qa_pass as qa
    cap = tmp_path / "captions"
    cap.mkdir()
    groups = [{"text": "And that's how I determined", "show_start": 17.25, "show_end": 18.93, "words": []}]
    (cap / "captions.json").write_text(json.dumps({"groups": groups}))
    layer = ly.Layer("video", "captions.mov", str(cap / "captions.mov"), 0.0, 21.0, 1)
    note = {"timeline_sec": 18.09, "text": "fix the caption"}
    o = {"note": 3, "op": "edit_caption", "text": "and that's how i determined"}
    assert qa.check_edit_caption(o, note, ([layer], [layer])).status == qa.VERIFIED
    groups[0]["text"] = "And that's why I determined this"                                                 # negative control: the line was not changed
    (cap / "captions.json").write_text(json.dumps({"groups": groups}))
    assert qa.check_edit_caption(o, note, ([layer], [layer])).status == qa.FAILED
    assert qa.check_edit_caption(o, dict(note, timeline_sec=40.0), ([layer], [layer])).status == qa.FAILED      # no line at that moment
    assert qa.check_edit_caption(o, note, ([layer], [])).status == qa.FAILED                                  # no captions layer at all


def test_qa_checks_a_bleep_sits_where_the_note_pointed(tmp_path):
    sys.path.insert(0, str(HERE.parent / "qa"))
    import qa_pass as qa
    (tmp_path / "bleeps").mkdir()
    (tmp_path / "bleep.json").write_text("{}")
    b = ly.Layer("audio", "bleep_1.wav", str(tmp_path / "bleeps" / "bleep_1.wav"), 28.8, 29.4, 3)
    o = {"note": 4, "op": "bleep_word"}
    clip = {"timeline_sec": 29.86, "text": "bleep the curse word", "target": {"lane": "Clips", "label": "clip 8", "start": 26.43, "end": 33.3, "clip": 8}}
    assert qa.check_bleep_word(o, clip, ([], [b])).status == qa.VERIFIED
    assert qa.check_bleep_word(o, clip, ([], [])).status == qa.NOT_DONE                                        # negative control: nothing was bleeped
    far = dict(clip, target=dict(clip["target"], start=40.0, end=45.0), timeline_sec=42.0)
    assert qa.check_bleep_word(o, far, ([], [b])).status == qa.NOT_DONE                                       # a bleep elsewhere does not count
    (tmp_path / "bleep.json").unlink()
    assert qa.check_bleep_word(o, clip, ([], [b])).status == qa.UNMEASURED


def test_a_bleep_note_becomes_bleep_word_and_points_the_bleep_at_its_stretch():
    sys.path.insert(0, str(HERE.parent / "review_loop"))
    sys.path.insert(0, str(HERE.parent / "bleep"))
    import bleep as bl
    import ops as opsmod
    cut = type("C", (), {"video": [object()], "zone_end": 70.0})()
    clip = {"lane": "Clips", "label": "clip 8", "start": 26.43, "end": 33.3, "clip": 8}
    notes = [{"timeline_sec": 29.86, "text": "Lets bleep the curse word here", "target": clip},
             {"timeline_sec": 10.0, "text": "bleep the swear word there"},
             {"timeline_sec": 12.0, "text": "make it louder", "target": clip},
             {"timeline_sec": 12.0, "text": "bleep the curse word", "target": {"lane": "SFX", "label": "sound effect", "start": 13.18, "end": 14.38}}]
    got = opsmod.validate([{"note": 1, "op": "bleep_word"}, {"note": 2, "op": "bleep_word"}, {"note": 3, "op": "bleep_word"}, {"note": 4, "op": "bleep_word"}], notes, cut)
    assert [o["op"] for o in got] == ["bleep_word", "bleep_word", "unsupported", "unsupported"]
    assert "does not ask for a word to be bleeped" in got[2]["reason"] and "left on a SFX element" in got[3]["reason"]
    win = bl.windows_from_notes(got, notes)
    assert win == [(26.43, 33.3), (9.0, 11.0)]                                                               # the clip's stretch; else a second either side of the moment


def test_the_sound_effect_check_reads_the_recorded_prompt_when_the_folder_was_rebuilt_and_still_fails_a_wrong_one(tmp_path):
    """A folder rebuilt by reconform carries no 'replaced by note N' block, only the prompt the effect was generated from."""
    import wave
    import numpy as np
    sys.path.insert(0, str(HERE.parent / "qa"))
    sys.path.insert(0, str(HERE.parent / "audio"))
    import qa_pass as qa

    def folder(name, hz, prompt, replaced=None):
        d = tmp_path / name
        d.mkdir()
        x = (np.sin(2 * np.pi * hz * np.arange(48000) / 48000) * 9000).astype("<i2")
        with wave.open(str(d / "sfx_clip.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000); w.writeframes(x.tobytes())
        meta = {"generated": {"sfx": {"prompt": prompt}}}
        if replaced:
            meta["replaced"] = replaced
        (d / "audio.json").write_text(json.dumps(meta))
        return d
    o = {"note": 1, "op": "replace_sfx", "sound": "a bell ding sound"}
    old = folder("old", 300, "a soft pop")
    rebuilt = folder("rebuilt", 2200, "a bell ding sound, short and subtle")
    assert qa.check_replace_sfx(o, {}, {"before": old, "after": rebuilt}).status == qa.VERIFIED
    wrong = folder("wrong", 2200, "a soft whoosh, short and subtle")                                         # a different sound, but not from this note's words
    assert qa.check_replace_sfx(o, {}, {"before": old, "after": wrong}).status == qa.FAILED
    same = folder("same", 300, "a bell ding sound")                                                          # the right words but the sound never changed
    assert qa.check_replace_sfx(o, {}, {"before": old, "after": same}).status == qa.FAILED
    other_note = folder("other", 2200, "a bell ding sound", replaced={"now": "a bell ding sound", "note": 4})
    assert qa.check_replace_sfx(o, {}, {"before": old, "after": other_note}).status == qa.FAILED            # replace_sfx's own record still has to name this note
    mine = folder("mine", 2200, "x", replaced={"now": "a bell ding sound, short", "note": 1})
    assert qa.check_replace_sfx(o, {}, {"before": old, "after": mine}).status == qa.VERIFIED
