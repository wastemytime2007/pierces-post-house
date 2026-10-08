"""Hermetic tests for notes -> operations -> revised XML. Synthetic media only.

Media: a 60s noise 'lav' with an exact 2s silence at 10-12s, and a 30fps video whose
camera audio is that same noise shifted 5.005s earlier, so lav-to-camera offset is known.
Cut: three 10s clips, then a 36s gap, then a two-clip selects pool.
"""
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import apply_ops  # noqa: E402
import ops as opsmod  # noqa: E402
import revise  # noqa: E402
import timeline  # noqa: E402
from build_review import build  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
FPS = 60 * 1000 / 1001
SEQ = "<rate><timebase>60</timebase><ntsc>TRUE</ntsc></rate>"


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("rev")
    lav, vid = d / "lav.wav", d / "a.mp4"
    _ff("-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.5", "-af", "volume=enable='between(t,10,12)':volume=0", str(lav))
    cam = d / "cam.wav"
    _ff("-ss", "5.005", "-i", str(lav), str(cam))
    _ff("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-i", str(cam), "-t", "55", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid))
    return vid, lav


def _ci(cid, name, s, e, i, o, fid, body="", enabled="TRUE", audio=False):
    st = "<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>" if audio else ""
    return (f'<clipitem id="{cid}"><name>{name}</name><enabled>{enabled}</enabled><duration>{e - s}</duration>{SEQ}'
            f"<start>{s}</start><end>{e}</end><in>{i}</in><out>{o}</out><masterclipid>m-{fid}</masterclipid>"
            + (body or f'<file id="{fid}"/>') + st + "</clipitem>")


def make_xml(path: Path, vid: Path, lav: Path, first_len: int = 600, pool_adjacent: bool = False) -> Path:
    vb = (f'<file id="f1"><name>a.mp4</name><pathurl>file://localhost{vid}</pathurl>'
          "<rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate><duration>1650</duration></file>")
    lb = (f'<file id="f2"><name>lav.wav</name><pathurl>file://localhost{lav}</pathurl>'
          "<rate><timebase>30</timebase><ntsc>TRUE</ntsc></rate><duration>1798</duration></file>")
    n = first_len
    cut = [(0, n, 0, n // 2), (n, n + 600, 600, 900), (n + 600, n + 1200, 900, 1200)]
    lavs = [(0, n, 300, 300 + n), (n, n + 600, 1499, 2099), (n + 600, n + 1200, 2098, 2698)]
    pool = [(4000, 4600, 1350, 1500), (4600, 5200, 1500, 1650)]
    if pool_adjacent:                       # a leftover that begins exactly where clip 1 ends (src 4.5s)
        pool = [(4000, 4600, n // 2, n // 2 + 300), (4600, 5200, 1500, 1650)]
    v = "".join(_ci(f"v{n}", "a.mp4", s, e, i, o, "f1", vb if n == 0 else "") for n, (s, e, i, o) in enumerate(cut + pool))
    cam = "".join(_ci(f"c{n}", "a.mp4", s, e, i, o, "f1", enabled="FALSE", audio=True) for n, (s, e, i, o) in enumerate(cut))
    la = "".join(_ci(f"s1-sync-lav.wav-{n}", "lav.wav", s, e, i, o, "f2", lb if n == 0 else "", audio=True) for n, (s, e, i, o) in enumerate(lavs))
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="s"><name>Rev Test</name>'
        f"{SEQ}<media><video><format><samplecharacteristics><width>320</width><height>180</height></samplecharacteristics></format>"
        f"<track>{v}</track></video><audio><track>{cam}</track><track>{la}<enabled>TRUE</enabled></track></audio></media>"
        "<marker><name>brief</name><in>0</in><out>-1</out></marker></sequence></xmeml>")
    return path


@pytest.fixture()
def xml(tmp_path, media):
    return make_xml(tmp_path / "cut.xml", *media)


def _run(xml, tmp_path, ops, notes=None):
    cut = timeline.load_cut(xml)
    notes = notes or [{"timeline_sec": 5.5, "text": "x"}] * 10
    out = tmp_path / "v2.xml"
    changes, removed = apply_ops.apply_ops(xml, out, cut, ops, notes)
    return cut, out, changes, removed


def _items(path, kind, track=0):
    seq = ET.parse(path).getroot().find("sequence")
    return [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out")))
            for c in seq.findall(f"media/{kind}/track")[track].findall("clipitem")]


def test_removing_the_middle_of_a_clip_splits_it_and_ripples(tmp_path, xml):
    cut, out, changes, removed = _run(xml, tmp_path, [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}])
    r0, r1 = round(3 * FPS), round(5 * FPS)
    v = _items(out, "video")
    assert v[0] == (0, r0, 0, round(r0 * 0.5))                              # k = 0.5 for a 30fps file in a 60fps sequence
    assert v[1] == (r0, r0 + (600 - r1), round(r1 * 0.5), round(r1 * 0.5) + round((600 - r1) * 0.5))
    shift = r1 - r0
    assert v[2][:2] == (600 - shift, 1200 - shift)                          # later clips ripple left
    lavs = _items(out, "audio", 1)
    assert lavs[0] == (0, r0, 300, 300 + r0)                                # lav is in sequence frames, k = 1
    assert lavs[1][2] == 300 + r1 and lavs[1][3] == 900
    assert _items(out, "video")[-2:] == _items(xml, "video")[-2:]           # selects pool untouched
    assert removed == pytest.approx(-2.0, abs=0.02)


def test_result_loads_with_unique_ids_and_one_file_body_each(tmp_path, xml):
    cut, out, *_ = _run(xml, tmp_path, [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0}])
    root = ET.parse(out).getroot()
    ids = [c.get("id") for c in root.iter("clipitem")]
    assert len(ids) == len(set(ids))
    bodies = [f.get("id") for f in root.iter("file") if len(list(f)) > 0]
    assert sorted(bodies) == ["f1", "f2"]
    assert timeline.load_cut(out).zone_end == pytest.approx(cut.zone_end - 2.0, abs=0.05)
    assert out.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')


def test_dropping_the_clip_that_held_the_file_body_keeps_the_definition(tmp_path, xml):
    _cut, out, *_ = _run(xml, tmp_path, [{"note": 1, "op": "drop_clip", "clip": 1}])
    root = ET.parse(out).getroot()
    assert sorted(f.get("id") for f in root.iter("file") if len(list(f)) > 0) == ["f1", "f2"]
    assert len(timeline.load_cut(out).video) == 2


def test_refuses_to_remove_the_whole_cut(tmp_path, xml):
    with pytest.raises(timeline.TimelineError, match="whole cut"):
        _run(xml, tmp_path, [{"note": 1, "op": "remove_range", "start": 0.0, "end": 29.9}])


def test_tighten_pause_removes_the_measured_silence(tmp_path, xml):
    cut, out, changes, removed = _run(xml, tmp_path, [{"note": 1, "op": "tighten_pause", "at": 5.5}])
    assert changes[0].applied
    s, e = changes[0].removed
    assert 4.9 < s < 5.3 and 6.7 < e < 7.1          # silence is at 4.995-6.995 on the timeline, 0.15s kept
    assert removed == pytest.approx(-(2.0 - 0.15), abs=0.15)


def test_tighten_pause_where_there_is_no_pause_says_so_and_changes_nothing(tmp_path, xml):
    cut, out, changes, removed = _run(xml, tmp_path, [{"note": 1, "op": "tighten_pause", "at": 18.0}])
    assert not changes[0].applied and "no pause found" in changes[0].summary
    assert removed == 0
    assert _items(out, "video") == _items(xml, "video")


def test_full_revise_checks_pass_and_v2_page_builds(tmp_path, xml):
    cut, out, changes, removed = _run(xml, tmp_path, [{"note": 1, "op": "remove_range", "start": 3.0, "end": 5.0},
                                                       {"note": 2, "op": "tighten_pause", "at": 5.5}])
    rows = revise.verify(cut, xml, out, removed)
    failed = [(n, d) for n, ok, d in rows if ok is False]
    assert not failed, failed
    page = build(out, tmp_path / "review", 180, changes={"v1_duration": cut.zone_end, "v2_duration": cut.zone_end + removed,
                                                          "items": [{"note": c.note, "note_time": 1, "note_text": "t", "applied": c.applied,
                                                                     "summary": c.summary, "v2_time": c.v2_time, "why": ""} for c in changes]})
    assert '"changes"' in page.read_text()


def _fake(reply):
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=reply)])))


def test_interpreter_refuses_to_invent_times_and_accounts_for_every_note(xml):
    cut = timeline.load_cut(xml)
    notes = [
        {"timeline_sec": 5.5, "text": "awkward pause, tighten this"},
        {"timeline_sec": 12.0, "text": "wrong angle, use the wider shot"},
        {"timeline_sec": 14.0, "text": "trim 2 seconds off the start of this clip"},
        {"timeline_sec": 16.0, "text": "make it warmer"},
        {"timeline_sec": 22.0, "text": "trim the end"},
    ]
    reply = "```json\n" + json.dumps([
        {"note": 1, "op": "tighten_pause", "at": 5.5, "why": "pause"},
        {"note": 2, "op": "drop_clip", "clip": 2, "why": "wrong angle"},          # note never says remove
        {"note": 3, "op": "trim_start", "clip": 2, "seconds": 2, "why": "stated"},
        {"note": 5, "op": "trim_end", "clip": 3, "seconds": 1.5, "why": "guessed"},  # note states no amount
        {"note": 99, "op": "drop_clip", "clip": 1},                                  # no such note
    ]) + "\n```"
    got = opsmod.interpret(cut, notes, client=_fake(reply))
    by_note = {o["note"]: o for o in got}
    assert sorted(by_note) == [1, 2, 3, 4, 5]
    assert by_note[1]["op"] == "tighten_pause"
    assert by_note[2]["op"] == "unsupported" and "does not say to remove" in by_note[2]["reason"]
    assert by_note[3]["op"] == "trim_start" and by_note[3]["seconds"] == 2
    assert by_note[4]["op"] == "unsupported" and "no operation" in by_note[4]["reason"]
    assert by_note[5]["op"] == "unsupported" and "no amount" in by_note[5]["reason"]


def test_interpreter_reply_that_is_not_json_raises(xml):
    with pytest.raises(ValueError):
        opsmod.interpret(timeline.load_cut(xml), [{"timeline_sec": 1, "text": "x"}], client=_fake("sorry, I can't"))


# ---- measured operations: extend_end and start_at_words ------------------------------

import words as words_mod  # noqa: E402


def test_extend_end_measures_the_decay_and_grows_the_cut(tmp_path, media):
    # clip 1's lav ends at 9.51s; the noise is loud until it goes silent at 10.0s
    xml = make_xml(tmp_path / "short.xml", *media, first_len=270)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0}])
    assert changes[0].applied
    g = round(delta * FPS)
    assert 0.4 < delta < 0.7                                           # about 0.49s of sound left, plus a 0.04s pad
    v, lavs = _items(out, "video"), _items(out, "audio", 1)
    assert v[0] == (0, 270 + g, 0, 135 + round(g * 0.5))               # the clip grows at its end, video at k = 0.5
    assert lavs[0] == (0, 270 + g, 300, 570 + g)                       # lav grows by the same amount
    assert v[1][0] == 270 + g and lavs[1][0] == 270 + g                # everything after ripples right
    assert timeline.load_cut(out).zone_end == pytest.approx(cut.zone_end + delta, abs=0.05)
    rows = revise.verify(cut, xml, out, delta, extra_out=changes[0].check["ext"])
    assert not [(n, d) for n, ok, d in rows if ok is False]
    quiet = revise.verify_render(changes, out)
    assert quiet and all(ok for _n, ok, _d in quiet)


def test_extending_into_footage_the_pool_holds_trims_the_pool_so_it_never_repeats_the_cut(tmp_path, media):
    xml = make_xml(tmp_path / "adj.xml", *media, first_len=270, pool_adjacent=True)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0}])
    g = round(delta * FPS)
    before, after = _items(xml, "video")[-2], _items(out, "video")[-2]
    assert after[0] == before[0] and after[3] == before[3]             # same place on the timeline, same source out
    assert after[2] == before[2] + round(g * 0.5) and after[1] == before[1] - g   # front-trimmed by exactly what the cut gained
    assert "selects-pool" in changes[0].summary
    rows = revise.verify(cut, xml, out, delta, extra_out=changes[0].check["ext"])
    assert not [(n, d) for n, ok, d in rows if ok is False], rows
    # without the pool trim the same edit would have failed verify_export's XML-POOL-NOT-IN-CUT


def test_extend_end_refuses_when_the_sound_never_stops(tmp_path, xml):
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0}])
    assert not changes[0].applied and "keeps going" in changes[0].summary
    assert delta == 0


def test_extend_end_and_a_trim_of_the_same_clip_end_sets_the_extension_aside_and_makes_the_trim(tmp_path, media):
    xml = make_xml(tmp_path / "short.xml", *media, first_len=270)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0},
                                                    {"note": 2, "op": "trim_end", "clip": 1, "seconds": 0.5}])
    assert not changes[0].applied and "also being trimmed" in changes[0].summary                  # the conflicting fix is left, with its reason
    assert changes[1].applied and delta < 0                                                       # the trim, which was fine, is made; the revision is not thrown away


def _fake_words(monkeypatch, rows):
    def fake(path, start, dur):
        return [words_mod.W(t, start + a, start + b) for t, a, b in rows]
    monkeypatch.setattr(words_mod, "words_in", fake)


def test_start_at_words_trims_to_the_named_words_and_says_what_it_dropped(tmp_path, xml, monkeypatch):
    # clip 2's lav starts at 25.007s; the window starts 0.6s earlier
    rows = [("and", 0.2, 0.7), ("then", 0.7, 0.85), ("step", 0.85, 1.05), ("on", 1.05, 1.2), ("the", 1.2, 1.3),
            ("tile", 1.3, 1.5), ("up", 1.5, 1.7), ("with", 1.7, 1.8), ("the", 1.8, 1.9), ("spacer.", 1.9, 2.4)]
    _fake_words(monkeypatch, rows)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "start_at_words", "clip": 2, "words": "stepping the tile up with the spacer"}])
    c = changes[0]
    assert c.applied and 'dropped "and then"' in c.summary
    assert 0.13 < -delta < 0.26                      # 'step' starts 0.25s into the clip; the cut lands up to 0.12s before it
    assert c.check["kind"] == "seam_text"
    assert timeline.load_cut(out).video[1].src_in > cut.video[1].src_in + 0.13


def test_start_at_words_reports_what_it_heard_when_the_words_are_not_there(tmp_path, xml, monkeypatch):
    _fake_words(monkeypatch, [("completely", 0.2, 0.8), ("different", 0.8, 1.2), ("speech", 1.2, 1.6)])
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "start_at_words", "clip": 2, "words": "stepping the tile up"}])
    assert not changes[0].applied and "completely different speech" in changes[0].summary
    assert delta == 0


def test_find_phrase_copes_with_whisper_splitting_a_word():
    ws = [words_mod.W(t, i * 0.2, i * 0.2 + 0.2) for i, t in enumerate(
        ["and", "then", "step", "on", "the", "tile", "up", "with", "the", "spacer."])]
    assert words_mod.find_phrase(ws, "stepping the tile up with the spacer")[0] == 2
    assert words_mod.find_phrase(ws, "something entirely unrelated here") is None


def test_replace_sfx_takes_its_sound_from_the_note_and_refuses_an_invented_one(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 14, "text": "I don't like the sound effect here, make it sound more like something being highlighted on a piece of paper"},
             {"timeline_sec": 14, "text": "I don't like the sound effect here, make it sound different"},
             {"timeline_sec": 14, "text": "make the shot look more like a movie"},
             {"timeline_sec": 14, "text": "the sound effect is too loud"}]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "replace_sfx", "sound": "something being highlighted on a piece of paper"},
        {"note": 2, "op": "replace_sfx", "sound": "a deep cinematic boom with reverb"},          # not in the note
        {"note": 3, "op": "replace_sfx", "sound": "a movie"},                                     # note is not about a sound
        {"note": 4, "op": "replace_sfx", "sound": ""},
    ], notes, cut)}
    assert got[1]["op"] == "replace_sfx" and got[1]["sound"] == "something being highlighted on a piece of paper"
    assert got[2]["op"] == "unsupported" and "refusing to invent a sound" in got[2]["reason"]
    assert got[3]["op"] == "unsupported" and "does not talk about a sound effect" in got[3]["reason"]
    assert got[4]["op"] == "unsupported" and "no usable description" in got[4]["reason"]


def test_extend_graphic_needs_a_graphic_a_wish_for_longer_and_never_invents_an_amount(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 14, "text": "the text bubble only shows for half a second, have it sit on the screen a little longer"},
             {"timeline_sec": 14, "text": "keep the arrow up for two more seconds"},
             {"timeline_sec": 14, "text": "the text bubble should stay up longer"},
             {"timeline_sec": 14, "text": "the shot is too short, make it longer"},
             {"timeline_sec": 14, "text": "make the text bubble bigger"},
             {"timeline_sec": 14, "text": "keep the arrow up for five more seconds"}]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "extend_graphic", "seconds": None},
        {"note": 2, "op": "extend_graphic", "seconds": 2},
        {"note": 3, "op": "extend_graphic", "seconds": 3},                        # note states no number
        {"note": 4, "op": "extend_graphic", "seconds": None},                     # not about a graphic
        {"note": 5, "op": "extend_graphic", "seconds": None},                     # about a graphic, but not about time
        {"note": 6, "op": "extend_graphic", "seconds": 99},                       # not believable
    ], notes, cut)}
    assert got[1]["op"] == "extend_graphic" and got[1]["seconds"] is None
    assert got[2]["op"] == "extend_graphic" and got[2]["seconds"] == 2.0
    assert got[3]["op"] == "unsupported" and "refusing to invent one" in got[3]["reason"]
    assert got[4]["op"] == "unsupported" and "does not talk about an on-screen graphic" in got[4]["reason"]
    assert got[5]["op"] == "unsupported" and "does not ask for it to stay longer" in got[5]["reason"]
    assert got[6]["op"] == "unsupported" and "not a believable" in got[6]["reason"]


def test_edit_callout_takes_its_words_from_the_note_and_removes_only_what_is_asked(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 14, "text": 'change the text bubble to say "Cardboard spacer"'},
             {"timeline_sec": 14, "text": "drop the small line under the text bubble title"},
             {"timeline_sec": 14, "text": "change the text bubble to say something punchy"},
             {"timeline_sec": 14, "text": "change the text bubble"},
             {"timeline_sec": 14, "text": "change the text bubble title to spacer"},
             {"timeline_sec": 14, "text": "get rid of the text bubble"},
             {"timeline_sec": 14, "text": "make the text bubble bigger"}]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "edit_callout", "title": "Cardboard spacer", "subtitle": None, "remove_subtitle": False},
        {"note": 2, "op": "edit_callout", "title": None, "subtitle": None, "remove_subtitle": True},
        {"note": 3, "op": "edit_callout", "title": "Quick Tip: Spacers", "subtitle": None, "remove_subtitle": False},      # wording the note never gave
        {"note": 4, "op": "edit_callout", "title": None, "subtitle": None, "remove_subtitle": False},                      # nothing asked
        {"note": 5, "op": "edit_callout", "title": "spacer", "subtitle": "a cardboard tab", "remove_subtitle": True},      # remove and set together
        {"note": 6, "op": "edit_callout", "title": None, "subtitle": None, "remove_subtitle": True},                       # no mention of the second line
        {"note": 7, "op": "edit_callout", "title": "Bigger", "subtitle": None, "remove_subtitle": False},                  # about size, not words
    ], notes, cut)}
    assert got[7]["op"] == "unsupported" and "does not ask to change or remove any words" in got[7]["reason"]
    assert got[1]["op"] == "edit_callout" and got[1]["title"] == "Cardboard spacer" and got[1]["remove_subtitle"] is False
    assert got[2]["op"] == "edit_callout" and got[2]["remove_subtitle"] is True and got[2]["title"] is None
    assert got[3]["op"] == "unsupported" and "not in the note" in got[3]["reason"]
    assert got[4]["op"] == "unsupported" and "no change to the callout's words" in got[4]["reason"]
    assert got[5]["op"] == "unsupported"
    assert got[6]["op"] == "unsupported" and "second line" in got[6]["reason"]


def test_an_edit_callout_note_is_reported_as_not_applied_on_the_timeline(tmp_path, xml):
    cut = timeline.load_cut(xml)
    changes, _s, _i = apply_ops.plan(cut, [{"note": 1, "op": "edit_callout", "title": "X", "subtitle": None, "remove_subtitle": False}], [{"timeline_sec": 14, "text": "change it to X"}])
    assert len(changes) == 1 and changes[0].applied is False and "graphics step" in changes[0].summary


def test_an_extend_graphic_note_is_reported_as_not_applied_on_the_timeline(tmp_path, xml):
    cut = timeline.load_cut(xml)
    changes, _s, _i = apply_ops.plan(cut, [{"note": 1, "op": "extend_graphic", "seconds": None}], [{"timeline_sec": 14, "text": "keep the arrow up longer"}])
    assert len(changes) == 1 and changes[0].applied is False and "graphics step" in changes[0].summary


def test_a_sound_effect_note_is_reported_as_not_applied_on_the_timeline(tmp_path, xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 14, "text": "make the sound effect sound like a paper tap"}]
    changes, _spans, _ins = apply_ops.plan(cut, [{"note": 1, "op": "replace_sfx", "sound": "a paper tap"}], notes)
    assert len(changes) == 1 and changes[0].applied is False and "audio step" in changes[0].summary


def test_validate_refuses_target_words_not_in_the_note_and_clamps_max_sec(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 5, "clip": 1, "text": "start this clip at stepping the tile up"},
             {"timeline_sec": 5, "clip": 1, "text": "the last word is cut off"}]
    got = opsmod.validate([
        {"note": 1, "op": "start_at_words", "clip": 2, "words": "invented words here"},
        {"note": 2, "op": "extend_end", "max_sec": 9},
    ], notes, cut)
    assert got[0]["op"] == "unsupported" and "not in the note" in got[0]["reason"]
    assert got[1]["op"] == "extend_end" and got[1]["clip"] == 1 and got[1]["max_sec"] == 1.5


# ---- notes left on a whole timeline element (a box on the review page's map) ----

def _on(lane, text, start=14.0, end=16.0, **extra):
    return {"timeline_sec": round((start + end) / 2, 2), "text": text, "target": {"lane": lane, "label": "x", "start": start, "end": end, **extra}}


def test_a_note_on_a_callout_or_sfx_element_needs_no_word_naming_it(xml):
    cut = timeline.load_cut(xml)
    notes = [_on("Callout", "make this longer"), _on("SFX", "something more like a soft paper rustle"), _on("Callout", "say Cardboard spacer instead")]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "extend_graphic", "seconds": None},
        {"note": 2, "op": "replace_sfx", "sound": "soft paper rustle"},
        {"note": 3, "op": "edit_callout", "title": "Cardboard spacer"}], notes, cut)}
    assert [got[i]["op"] for i in (1, 2, 3)] == ["extend_graphic", "replace_sfx", "edit_callout"]
    bare = {o["note"]: o for o in opsmod.validate([{"note": 1, "op": "extend_graphic", "seconds": None}], [{"timeline_sec": 15, "text": "make this longer"}], cut)}
    assert bare[1]["op"] == "unsupported" and "does not talk about an on-screen graphic" in bare[1]["reason"]   # same words, no element: still refused


def test_an_op_that_does_not_act_on_the_lane_the_note_was_left_on_is_reported(xml):
    cut = timeline.load_cut(xml)
    notes = [_on("SFX", "make this longer"), _on("Callout", "remove this"), _on("Card", "make this longer, the text bubble"),
             _on("Captions", "tighten this pause"), _on("Music", "louder")]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "extend_graphic", "seconds": None}, {"note": 2, "op": "drop_clip", "clip": 1},
        {"note": 3, "op": "extend_graphic", "seconds": None}, {"note": 4, "op": "tighten_pause", "at": 15}, {"note": 5, "op": "tighten_pause", "at": 15}], notes, cut)}
    assert all(got[i]["op"] == "unsupported" for i in range(1, 6))
    assert "left on a SFX element" in got[1]["reason"] and "left on a Callout element" in got[2]["reason"]
    assert "left on a Card element" in got[3]["reason"] and "left on a Captions element" in got[4]["reason"] and "music bed" in got[5]["reason"]


def test_a_note_on_a_clip_is_about_that_clip_and_cannot_be_turned_on_another(xml):
    cut = timeline.load_cut(xml)
    notes = [_on("Clips", "the end is cut off", 10.0, 20.0, clip=2), _on("Clips", "remove this shot", 10.0, 20.0, clip=2)]
    got = {o["note"]: o for o in opsmod.validate([{"note": 1, "op": "extend_end"}, {"note": 2, "op": "drop_clip", "clip": 3}], notes, cut)}
    assert got[1]["op"] == "extend_end" and got[1]["clip"] == 2                     # the clip comes from the element, not the playhead
    assert got[2]["op"] == "unsupported" and "left on clip 2, not clip 3" in got[2]["reason"]
    assert opsmod.validate([{"note": 1, "op": "drop_clip", "clip": 2}], notes[1:], cut)[0]["op"] == "drop_clip"


def test_the_interpreter_is_told_which_element_a_note_was_left_on(xml):
    cut = timeline.load_cut(xml)
    p = opsmod.build_prompt(cut, [_on("SFX", "different"), {"timeline_sec": 3, "text": "plain"}])
    assert 'left ON the SFX element "x", 14.0-16.0s' in p and p.count("left ON") == 1


def test_sfx_and_callout_are_found_by_the_element_not_the_playhead():
    sys.path.insert(0, str(HERE.parent / "audio"))
    sys.path.insert(0, str(HERE.parent / "overlay"))
    import change_callout as cc
    import layers as ly
    import replace_sfx as rs
    clips = [{"kind": "sfx", "name": "a", "start_sec": 4.0, "duration_sec": 1.0}, {"kind": "sfx", "name": "b", "start_sec": 4.6, "duration_sec": 1.0}]
    assert rs.sfx_for_note(clips, 4.3)["name"] == "a"                                       # by time: nearest start
    assert rs.sfx_for_note(clips, 4.3, {"lane": "SFX", "start": 4.6})["name"] == "b"        # by element: the one named
    assert rs.sfx_for_note(clips, 4.6, {"lane": "Callout", "start": 4.6}) is None
    layers = [ly.Layer("video", "overlay.mov", "/x/overlay.mov", 1.0, 5.0, 1), ly.Layer("video", "overlay2.mov", "/x/overlay2.mov", 3.0, 7.0, 1)]
    assert cc.callout_under(layers, 4.0, {"lane": "Callout", "start": 3.0}).name == "overlay2.mov"
    assert cc.callout_under(layers, 4.0, {"lane": "Callout", "start": 1.0}).name == "overlay.mov"
    assert cc.callout_under(layers, 4.0, {"lane": "SFX", "start": 1.0}) is None


# ---- remove a graphic, change a caption, and never turn a graphic note into deleting footage ----

def test_a_note_about_a_card_becomes_remove_graphic_and_never_drops_the_footage_under_it(xml):
    cut = timeline.load_cut(xml)
    note5 = "why is this here? Go ahead and remove it. I like the idea of cards when appropriate but this makes no sense"
    notes = [{"timeline_sec": 30.54, "text": note5},
             {"timeline_sec": 14, "text": "remove this shot", "target": {"lane": "Clips", "label": "clip 2", "start": 10.0, "end": 20.0, "clip": 2}},
             {"timeline_sec": 14, "text": "take the text bubble out", "target": {"lane": "Callout", "label": "callout", "start": 12.0, "end": 16.0}},
             {"timeline_sec": 14, "text": "remove it", "target": {"lane": "Clips", "label": "clip 2", "start": 10.0, "end": 20.0, "clip": 2}}]
    got = opsmod.validate([{"note": 1, "op": "remove_graphic"}, {"note": 1, "op": "drop_clip", "clip": 1}, {"note": 2, "op": "drop_clip", "clip": 2},
                           {"note": 3, "op": "remove_graphic"}, {"note": 4, "op": "remove_graphic"}], notes, cut)
    by = [(o["note"], o["op"]) for o in got]
    assert (1, "remove_graphic") in by and (2, "drop_clip") in by and (3, "remove_graphic") in by              # the graphic note, the footage note (clip shot), the callout note
    drop1 = next(o for o in got if o["note"] == 1 and o["op"] != "remove_graphic")
    assert drop1["op"] == "unsupported" and "would delete footage" in drop1["reason"]                           # "remove it ... cards" never drops clip 1
    bad = next(o for o in got if o["note"] == 4)
    assert bad["op"] == "unsupported" and "left on a Clips element" in bad["reason"]                            # a clip box is not a graphic
    plain = opsmod.validate([{"note": 1, "op": "remove_graphic"}], [{"timeline_sec": 5, "text": "tighten the pause"}], cut)
    assert plain[0]["op"] == "unsupported" and "does not talk about an on-screen graphic" in plain[0]["reason"]


def test_a_caption_fix_takes_its_words_only_from_the_note_and_only_for_a_captions_note(xml):
    cut = timeline.load_cut(xml)
    cap = {"lane": "Captions", "label": "And that's why I determined this", "start": 17.25, "end": 18.93}
    notes = [{"timeline_sec": 18.09, "text": 'Fix the caption to say "and that\'s how i determined"', "target": cap},
             {"timeline_sec": 18.09, "text": "the caption is wrong", "target": cap},
             {"timeline_sec": 5.0, "text": 'change the subtitle to "totally different words"'},
             {"timeline_sec": 6.0, "text": 'say "hello there"'},
             {"timeline_sec": 18.09, "text": 'Fix the caption to say "and that\'s how i determined"', "target": dict(cap, lane="Callout")}]
    got = {o["note"]: o for o in opsmod.validate([
        {"note": 1, "op": "edit_caption", "text": "and that's how i determined"},
        {"note": 2, "op": "edit_caption", "text": "something I invented"},
        {"note": 3, "op": "edit_caption", "text": "totally different words"},
        {"note": 4, "op": "edit_caption", "text": "hello there"},
        {"note": 5, "op": "edit_caption", "text": "and that's how i determined"}], notes, cut)}
    assert got[1]["op"] == "edit_caption" and got[1]["text"] == "and that's how i determined"
    assert got[2]["op"] == "unsupported" and "not in the note" in got[2]["reason"]
    assert got[3]["op"] == "edit_caption"                                                                      # no element, but the note says subtitle
    assert got[4]["op"] == "unsupported" and "does not talk about a caption" in got[4]["reason"]
    assert got[5]["op"] == "unsupported" and "left on a Callout element" in got[5]["reason"]


# ------------------------------------------------------------------ a defect the version already had is not blamed on the revision

def _overlapping_pool(xml, tmp_path):
    import xml.etree.ElementTree as ET
    t = ET.parse(xml)
    seq = t.getroot().find("sequence")
    pool = [c for c in seq.find("media/video/track").findall("clipitem") if int(c.findtext("start")) >= 3 * FPS * 10]
    a, b = pool[0], pool[1]
    b.find("in").text = str(int(a.findtext("in")) + 5)                                   # the second leftover starts inside the first
    b.find("out").text = str(int(b.findtext("in")) + int(b.findtext("end")) - int(b.findtext("start")))
    out = tmp_path / "overlap.xml"
    t.write(out, encoding="UTF-8", xml_declaration=True)
    return out


def test_a_pool_defect_already_in_the_version_is_reported_but_does_not_block_the_revision(xml, tmp_path):
    import revise
    bad = _overlapping_pool(xml, tmp_path)
    cut = timeline.load_cut(bad)
    rows = {n: (ok, d) for n, ok, d in revise.verify(cut, bad, bad, 0.0)}
    ok, detail = rows["verify_export XML-POOL-NO-DUPLICATES"]
    assert ok is None and "ALREADY FAILING" in detail                                    # said plainly, not hidden, and not counted against the revision


def test_a_pool_defect_the_revision_itself_makes_still_blocks_it(xml, tmp_path):
    import revise
    bad = _overlapping_pool(xml, tmp_path)
    cut = timeline.load_cut(xml)
    rows = {n: ok for n, ok, _d in revise.verify(cut, xml, bad, 0.0)}
    assert rows["verify_export XML-POOL-NO-DUPLICATES"] is False


def test_the_shared_export_gate_does_not_blame_a_tool_for_a_defect_its_input_already_had(xml, tmp_path):
    import export_gate
    bad = _overlapping_pool(xml, tmp_path)
    name, ok, detail = export_gate.row(bad, bad)
    assert ok is None and "already failed" in detail                                      # reported in words, not hidden, and not counted against the tool
    assert export_gate.row(bad, xml)[1] is False                                           # a defect the tool introduced still fails it
    assert export_gate.row(bad)[1] is False                                                # and with no input to compare against every check is held, as before
    assert export_gate.row(xml, xml)[1] is True


def test_two_fixes_that_would_put_back_the_same_footage_keep_the_first_and_set_the_second_aside(xml):
    """Ryan's run, 2026-10-08: a join put back the 0.77 s between clips 1 and 2, and a second fix started clip 2 earlier into that same stretch; together the cut showed 732-734 s twice and the
    whole revision was refused (XML-CUT-NO-OVERLAP). Now the second is set aside with its reason and the rest are made."""
    from types import SimpleNamespace
    cut = timeline.load_cut(xml)
    path = cut.video[0].src_path
    a = SimpleNamespace(idx=1, tl_start=0.0, tl_end=10.0, src_path=path, src_in=100.0, src_out=110.0)
    b = SimpleNamespace(idx=2, tl_start=10.0, tl_end=20.0, src_path=path, src_in=112.0, src_out=122.0)
    fake = SimpleNamespace(video=[a, b] + cut.video[2:], fps=cut.fps, zone_end=cut.zone_end)
    changes = [apply_ops.Change(1, "extend_end", True, "joined clip 1 to clip 2"), apply_ops.Change(2, "extend_start", True, "started clip 2 earlier")]
    ins = [(10.0, 2.0, 0, 1, "end"), (10.0 + 2.0, 1.0, 1, 2, "front")]
    kept = apply_ops._set_aside_conflicts(xml, fake, changes, ins, [], round(cut.zone_end * cut.fps), cut.fps)
    assert kept == ins[:1]
    assert changes[0].applied and not changes[1].applied and "footage clip 1 also shows" in changes[1].summary


def test_an_extension_that_only_meets_its_neighbour_is_not_a_clash(xml):
    from types import SimpleNamespace
    cut = timeline.load_cut(xml)
    path = cut.video[0].src_path
    a = SimpleNamespace(idx=1, tl_start=0.0, tl_end=10.0, src_path=path, src_in=100.0, src_out=110.0)
    b = SimpleNamespace(idx=2, tl_start=10.0, tl_end=20.0, src_path=path, src_in=112.0, src_out=122.0)
    fake = SimpleNamespace(video=[a, b] + cut.video[2:], fps=cut.fps, zone_end=cut.zone_end)
    changes = [apply_ops.Change(1, "extend_end", True, "joined")]
    ins = [(10.0, 2.0, 0, 1, "end")]                                                # 110 to 112 exactly: it meets clip 2, it does not overlap it
    assert apply_ops._set_aside_conflicts(xml, fake, changes, ins, [], round(cut.zone_end * cut.fps), cut.fps) == ins and changes[0].applied


# ------------------------------------------------------------------ the AI editor makes every fix its reviewer asks for that the footage allows (Ryan, 2026-10-08)

def test_end_at_words_ends_the_clip_right_after_the_named_words_and_says_what_it_dropped(tmp_path, xml, monkeypatch):
    # the fake words are timed from the start of whatever window is asked for; end_at_words asks for the last 8 s of the clip
    rows = [("so", 4.0, 4.2), ("we", 4.2, 4.4), ("wait", 4.4, 4.8), ("until", 4.8, 5.1), ("we're", 5.1, 5.3), ("under", 5.3, 5.6), ("contract.", 5.6, 6.1), ("lead", 6.6, 6.9), ("time", 6.9, 7.3)]
    _fake_words(monkeypatch, rows)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "end_at_words", "clip": 2, "words": "until we're under contract"}])
    c = changes[0]
    assert c.applied and 'dropped "lead time"' in c.summary and "contract" in c.summary
    assert 1.6 < -delta < 2.1                                         # the clip's last 8 s window: 'contract.' ends at 6.1 s, the clip at 8 s; the cut lands within 0.25 s after the word
    assert timeline.load_cut(out).video[1].src_out < cut.video[1].src_out - 1.6


def test_end_at_words_refuses_words_it_cannot_hear_and_changes_nothing(tmp_path, xml, monkeypatch):
    _fake_words(monkeypatch, [("completely", 0.2, 0.8), ("different", 0.8, 1.2)])
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "end_at_words", "clip": 2, "words": "under contract"}])
    assert not changes[0].applied and "could not find" in changes[0].summary and delta == 0


def test_move_clip_plays_a_clip_earlier_with_its_voice_and_the_length_is_unchanged(tmp_path, xml):
    import revise
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "move_clip", "clip": 3, "before": 1}])
    assert changes[0].applied and delta == 0
    new = timeline.load_cut(out)
    assert [round(v.src_in, 2) for v in new.video] == [round(cut.video[i].src_in, 2) for i in (2, 0, 1)]      # 3, 1, 2
    assert abs(new.zone_end - cut.zone_end) < 1e-6
    rows = {n: ok for n, ok, _d in revise.verify(cut, xml, out, 0.0)}
    for name in ("RIPPLE-LENGTH", "CONTIGUOUS", "NO-NEW-FOOTAGE", "LAV-SYNC-PRESERVED", "verify_export XML-CUT-NO-OVERLAP"):
        assert rows[name] is not False, (name, rows[name])                                                        # the voice moved with its picture and stayed in sync
    assert _items(out, "video")[-2:] == _items(xml, "video")[-2:]                                                 # the selects pool is untouched


def test_move_clip_of_a_clip_that_already_plays_first_does_nothing_and_says_so(tmp_path, xml):
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "move_clip", "clip": 1, "before": 3}])
    assert not changes[0].applied and "already plays before" in changes[0].summary


def test_validate_takes_the_new_fixes_from_the_reviewer_and_from_a_note(xml):
    cut = timeline.load_cut(xml)
    notes = [{"timeline_sec": 12.0, "text": "AI: x", "clip": 2, "suggested_op": {"op": "end_at_words", "clip": 2, "words": "until we're under contract"}},
             {"timeline_sec": 14.0, "text": "AI: y", "clip": 3, "suggested_op": {"op": "move_clip", "clip": 3, "before": 1}}]
    got = opsmod.validate(opsmod.from_suggestions(notes, cut), notes, cut)
    assert [(o["op"], o.get("words"), o.get("before")) for o in got] == [("end_at_words", "until we're under contract", None), ("move_clip", None, 1)]
    said = [{"timeline_sec": 3.0, "text": "Trim the out-point to end at 'until we're under contract'"}, {"timeline_sec": 3.0, "text": "make it shorter"}]
    raw = [{"note": 1, "op": "end_at_words", "clip": 1, "words": "until we're under contract"}, {"note": 2, "op": "move_clip", "clip": 2, "before": 1}]
    got = opsmod.validate(raw, said, cut)
    assert got[0]["op"] == "end_at_words" and got[1]["op"] == "unsupported"                                       # "make it shorter" does not ask for the order to change


def test_a_sliver_of_the_other_speaker_at_the_start_of_a_clip_does_not_name_the_whole_clip():
    """The run on 2026-10-08: a clip started 0.1 s earlier, so it opened on 0.1 s of Mitch before Bob's line; the clip was framed on Mitch with Mitch's recorder live under Bob's words."""
    import speakers
    pieces = speakers.pieces_for_cut([(0.0, 0.1, "Mitch"), (0.1, 1.7, "Bob"), (1.7, 2.2, "Mitch")], 2.2, fps=30.0)
    assert pieces[0][2] == "Bob" and pieces[0][0] == 0


def test_a_tail_of_the_pool_is_given_up_to_a_clip_that_starts_earlier_but_a_whole_pool_clip_is_not(tmp_path, xml):
    """Starting a clip earlier into footage the selects pool holds used to be refused outright; now the pool clip's END is taken off, as an end extension already takes its front."""
    import xml.etree.ElementTree as ET
    seq = ET.parse(xml).getroot().find("sequence")
    cut = timeline.load_cut(xml)
    zone_f = round(cut.zone_end * cut.fps)
    groups = {}
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            for c in track.findall("clipitem"):
                if int(c.findtext("start")) >= zone_f:
                    groups.setdefault((int(c.findtext("start")), int(c.findtext("end"))), []).append((kind, c))
    v = next(el for (_s, _e), els in groups.items() for kind, el in els if kind == "video")
    fid = v.find("file").get("id")
    (s, e) = next(k for k, els in groups.items() if any(el is v for _kd, el in els))
    k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
    a, b = int(v.findtext("in")) / (k * cut.fps), int(v.findtext("out")) / (k * cut.fps)
    assert apply_ops._pool_tail_ok(groups, fid, b - 1.0, b - 0.05, cut.fps)           # reaches only the pool clip's last second: its tail can go
    assert not apply_ops._pool_tail_ok(groups, fid, a - 0.5, b + 0.5, cut.fps)          # would swallow the whole pool clip: refused
    end_before = int(v.findtext("end"))
    took = apply_ops._trim_pool_tail(groups, fid, b - 1.0, b - 0.05, cut.fps)
    assert 0.9 < took < 1.1 and int(v.findtext("end")) == end_before - round(1.0 * cut.fps)


def test_a_clip_followed_by_an_earlier_part_of_the_recording_may_still_finish_its_word():
    """Ryan, 2026-10-08: "Why cant it drag the clip out to finish the sentence?" Clip 3 (750-756 s) was followed by clip 4 from 579 s; the guard compared only that 579 < 756 and refused.
    Only footage another clip actually shows blocks an extension."""
    from types import SimpleNamespace
    c = lambda i, a, b: SimpleNamespace(idx=i, src_path="/cam.mp4", src_in=a, src_out=b)
    cut = SimpleNamespace(video=[c(1, 727.0, 734.0), c(2, 734.1, 738.0), c(3, 749.6, 755.8), c(4, 579.2, 590.8)])
    assert opsmod._footage_user(cut, 3, 755.8, 756.0) is None                                 # nothing in the cut shows 755.8-756.0
    assert opsmod._footage_user(cut, 1, 733.9, 734.3).idx == 2                                 # a real neighbour's footage still blocks
    assert opsmod._footage_user(cut, 4, 590.8, 591.0) is None


def test_a_seam_moves_with_its_clip_so_its_own_check_listens_in_the_right_place(tmp_path, xml, monkeypatch):
    """The run on 2026-10-08: in one pass a fix started clip 4 at "So with the septic" and another moved a clip earlier; the start fix's seam time was the one from before the move, its check
    heard another clip there ("you're probably on a septic") and undid it, and the off-camera question came back."""
    rows = [("and", 0.2, 0.7), ("then", 0.7, 0.85), ("step", 0.85, 1.05), ("on", 1.05, 1.2), ("the", 1.2, 1.3), ("tile", 1.3, 1.5)]
    _fake_words(monkeypatch, rows)
    cut, out, changes, delta = _run(xml, tmp_path, [{"note": 1, "op": "start_at_words", "clip": 3, "words": "step on the tile"},
                                                    {"note": 2, "op": "move_clip", "clip": 3, "before": 1}])
    start, move = changes
    assert start.applied and move.applied
    new = timeline.load_cut(out)
    assert abs(start.v2_time - new.video[0].tl_start) < 1.5 / cut.fps                     # clip 3 now plays first, so its seam is at the start of the cut, not where clip 3 used to be
    assert round(new.video[0].src_in, 1) > round(cut.video[2].src_in, 1)                   # and it does start later, at the named words
