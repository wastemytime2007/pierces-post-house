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


def test_extend_end_and_a_trim_of_the_same_clip_end_is_refused(tmp_path, media):
    xml = make_xml(tmp_path / "short.xml", *media, first_len=270)
    with pytest.raises(timeline.TimelineError, match="also being trimmed"):
        _run(xml, tmp_path, [{"note": 1, "op": "extend_end", "clip": 1, "max_sec": 1.0},
                              {"note": 2, "op": "trim_end", "clip": 1, "seconds": 0.5}])


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
