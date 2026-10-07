"""finish_cut: the order the tools run in, what it refuses to do twice, that a failed step keeps nothing, and that the picture is never touched.

The tools themselves (captions, music, bleep) have their own tests; here a fake runner stands in for them: each 'places' by copying the XML it was handed to the --out it was given, so what is under test is
the chain, not the layers.
"""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import test_revise as tr  # noqa: E402
from test_revise import media, xml  # noqa: E402,F401  (fixtures)

import finish_cut as fc  # noqa: E402
import timeline  # noqa: E402

pytestmark = tr.pytestmark


class Fake:
    def __init__(self, fail_on=None, make_bleep=True):
        self.calls, self.fail_on, self.make_bleep = [], fail_on, make_bleep

    def bleep(self, xml, out, requests):
        self.calls.append("bleep")
        self.requests = requests
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        if not self.make_bleep:
            return {"xml": None, "spans": [], "hits": [], "rows": []}
        (out / Path(xml).name).write_text(Path(xml).read_text())
        return {"xml": str(out / Path(xml).name), "spans": [(1.0, 1.3)], "hits": [{"word": "darn", "start": 1.0}], "rows": [("SOME-CHECK", True, "ok")]}

    def __call__(self, cmd, **kw):
        script = Path(cmd[1]).name
        args = [str(a) for a in cmd[2:]]
        self.calls.append(script)
        if script == self.fail_on:
            return subprocess.CompletedProcess(cmd, 1, "", "REFUSING: pretend failure in " + script)
        if script in ("place_overlay.py", "place_audio.py"):
            Path(args[args.index("--out") + 1]).write_text(Path(args[0]).read_text())
        elif script == "make_captions.py":
            Path(args[args.index("--out") + 1]).mkdir(parents=True, exist_ok=True)
        elif script == "score_music.py":
            Path(args[args.index("--out") + 1]).write_bytes(b"")
        elif script == "conform_music.py":
            Path(args[args.index("--out") + 1]).write_bytes(b"")
        elif script == "make_audio.py":
            Path(args[args.index("--out") + 1]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def no_reference(monkeypatch):
    monkeypatch.setattr(fc, "reference_track", lambda: None)
    import build_review
    monkeypatch.setattr(build_review, "build", lambda xml, folder, height=540: Path(folder).mkdir(parents=True, exist_ok=True))


def test_captions_then_music_then_bleep_in_that_order_and_the_next_version_is_written(xml, tmp_path):
    f = Fake()
    r = fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep)
    order = [c for c in f.calls if c in ("make_captions.py", "place_overlay.py", "score_music.py", "conform_music.py", "make_audio.py", "place_audio.py", "bleep")]
    assert order == ["make_captions.py", "place_overlay.py", "score_music.py", "conform_music.py", "make_audio.py", "place_audio.py", "bleep"]      # the bleep LAST, captions before it
    assert Path(r["xml"]).name == fc.version_name(Path(xml)) and Path(r["xml"]).is_file()
    assert [s["name"] for s in r["steps"]] == ["captions", "music", "bleep"]
    assert all(ok for _n, ok, _d in r["checks"] if ok is not None), r["checks"]


def test_music_is_a_bed_with_no_effect_unless_one_is_asked_for(xml, tmp_path):
    seen = []
    base = Fake()

    def spy(cmd, **kw):
        if Path(cmd[1]).name == "make_audio.py":
            seen.append([str(a) for a in cmd[2:]])
        return base(cmd, **kw)
    fc.run(xml, tmp_path / "a", captions=False, bleep=False, runner=spy)
    assert "--no-sfx" in seen[0] and "bed" in seen[0] and "--sfx-at" not in seen[0]
    seen.clear()
    fc.run(xml, tmp_path / "b", captions=False, bleep=False, sfx_at=4.0, runner=spy)
    assert "--sfx-at" in seen[0] and "--no-sfx" not in seen[0]


def test_a_failing_step_stops_the_run_names_the_step_and_keeps_nothing(xml, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(fc.FinishError) as e:
        f = Fake(fail_on="score_music.py")
        fc.run(xml, out, runner=f, bleep_fn=f.bleep)
    assert "The music" in str(e.value) and "pretend failure" in str(e.value)
    assert not list(out.glob("*_v*.xml"))                                           # no finished version was written


def test_layers_already_on_the_cut_are_not_made_twice(xml, tmp_path):
    t = ET.parse(xml)
    seq = t.getroot().find("sequence")
    ET.SubElement(seq.find("media/video"), "track").append(ET.fromstring(
        '<clipitem id="ov1"><name>captions.mov</name><enabled>TRUE</enabled><start>0</start><end>30</end><in>0</in><out>30</out><file id="overlay-file-1"/></clipitem>'))     # a caption layer is already placed
    layered = tmp_path / "layered_v2.xml"
    t.write(layered, encoding="UTF-8", xml_declaration=True)
    f = Fake()
    r = fc.run(layered, tmp_path / "out", music=False, runner=f, bleep_fn=f.bleep)
    assert "make_captions.py" not in f.calls
    assert r["steps"][0]["name"] == "captions" and r["steps"][0]["done"] is False and "already" in r["steps"][0]["summary"]


def test_nothing_to_do_is_said_plainly(xml, tmp_path):
    with pytest.raises(fc.FinishError) as e:
        fc.run(xml, tmp_path / "out", captions=False, music=False, bleep=False, runner=Fake())
    assert "nothing" in str(e.value).lower()


def test_a_bleep_that_finds_nothing_is_a_done_step_with_that_said(xml, tmp_path):
    f = Fake(make_bleep=False)
    r = fc.run(xml, tmp_path / "out", captions=True, music=False, runner=f, bleep_fn=f.bleep)
    b = next(s for s in r["steps"] if s["name"] == "bleep")
    assert b["done"] and "nothing was bleeped" in b["summary"]


def test_the_picture_check_fails_if_the_clips_were_changed(xml, tmp_path):
    t = ET.parse(xml)
    seq = t.getroot().find("sequence")
    ci = seq.findall("media/video/track/clipitem")[0]
    ci.find("in").text = str(int(ci.findtext("in")) + 3)
    ci.find("out").text = str(int(ci.findtext("out")) + 3)
    moved = tmp_path / "moved.xml"
    t.write(moved, encoding="UTF-8", xml_declaration=True)
    rows = {n: ok for n, ok, _d in fc.check(Path(xml), moved, 10.0)}
    assert rows["PICTURE-UNCHANGED"] is False


def test_a_bleep_that_fails_its_own_checks_keeps_nothing(xml, tmp_path):
    f = Fake()

    def bad(xml_, out, requests):
        r = f.bleep(xml_, out, requests)
        r["rows"] = [("BLEEP-SILENCES-THE-WORD", False, "still audible")]
        return r
    with pytest.raises(fc.FinishError) as e:
        fc.run(xml, tmp_path / "out", captions=False, music=False, runner=f, bleep_fn=bad)
    assert "BLEEP-SILENCES-THE-WORD" in str(e.value)


def test_note_requests_reach_the_bleep(xml, tmp_path):
    f = Fake()
    fc.run(xml, tmp_path / "out", captions=False, music=False, runner=f, bleep_fn=f.bleep, bleep_requests=[{"kind": "exact", "start": 2.0, "end": 2.3}])
    assert f.requests == [{"kind": "exact", "start": 2.0, "end": 2.3}]


def test_a_rebuild_takes_the_layers_off_first_reuses_the_music_and_keeps_the_caption_fixes(xml, tmp_path):
    t = ET.parse(xml)
    seq = t.getroot().find("sequence")
    track = seq.find("media/video/track")
    layer = ET.fromstring('<clipitem id="ov1"><name>captions.mov</name><enabled>TRUE</enabled><start>0</start><end>30</end><in>0</in><out>30</out><file id="overlay-file-1"/></clipitem>')
    new_track = ET.SubElement(seq.find("media/video"), "track")
    new_track.append(layer)
    layered = tmp_path / "layered_v3.xml"
    t.write(layered, encoding="UTF-8", xml_declaration=True)
    music = tmp_path / "kept_music.wav"
    music.write_bytes(b"")
    seen = {"calls": [], "captions": None}
    base = Fake()

    def spy(cmd, **kw):
        seen["calls"].append(Path(cmd[1]).name)
        if Path(cmd[1]).name == "make_captions.py":
            args = [str(a) for a in cmd[2:]]
            seen["captions"] = "--fixes" in args and json.loads(Path(args[args.index("--fixes") + 1]).read_text() if Path(args[args.index("--fixes") + 1]).exists() else "[]")
        return base(cmd, **kw)
    r = fc.run(layered, tmp_path / "out", rebuild=True, music_file=music, caption_fixes=[{"find": "teh", "replace": "the"}], runner=spy, bleep_fn=base.bleep, final_name="layered_v3.xml")
    assert "make_captions.py" in seen["calls"] and "score_music.py" not in seen["calls"]            # captions are made again, the music is not generated again
    assert Path(r["xml"]).name == "layered_v3.xml"
    assert r["music_raw"] == str(music)
    assert (Path(r["folder"]) / "finish.json").is_file()
    assert all(ok for _n, ok, _d in r["checks"] if ok is not None), r["checks"]


def test_a_take_whose_tone_cannot_be_matched_is_used_as_generated_and_the_step_says_so(xml, tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"")
    monkeypatch.setattr(fc, "reference_track", lambda: ref)
    import reference_music
    import tone_match
    monkeypatch.setattr(reference_music, "analyze", lambda p: {"tone_centre_hz": 178})

    def refuse(*a, **k):
        raise RuntimeError("a 6 dB low-shelf cut does not bring the track's tone centre (110 Hz) to 85% of the reference's: use a different take")
    monkeypatch.setattr(tone_match, "match_tone", refuse)
    f = Fake()
    r = fc.run(xml, tmp_path / "out", captions=False, bleep=False, runner=f, bleep_fn=f.bleep)
    m = next(s for s in r["steps"] if s["name"] == "music")
    assert m["done"] and "could not be matched" in m["summary"] and "used as generated" in m["summary"]


def test_a_take_that_does_not_match_the_reference_is_mixed_anyway_and_the_step_says_so_with_the_numbers(xml, tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"")
    monkeypatch.setattr(fc, "reference_track", lambda: ref)
    import reference_music
    import tone_match
    monkeypatch.setattr(reference_music, "analyze", lambda p: {"tone_centre_hz": 178})
    monkeypatch.setattr(tone_match, "match_tone", lambda track, feats, out: {"tone_before": 170, "tone_after": 175})
    base = Fake()
    made = []

    def spy(cmd, **kw):
        if Path(cmd[1]).name == "make_audio.py":
            args = [str(a) for a in cmd[2:]]
            made.append("--music-reference" in args)
            if "--music-reference" in args:
                return subprocess.CompletedProcess(cmd, 1, "  [FAIL] REFERENCE-MATCH    tempo 2% off, brightness x0.62, rhythmic density x0.53\n", "")
        return base(cmd, **kw)
    r = fc.run(xml, tmp_path / "out", captions=False, bleep=False, runner=spy, bleep_fn=base.bleep)
    assert made == [True, False]                                                       # tried against the reference, then mixed without it
    m = next(s for s in r["steps"] if s["name"] == "music")
    assert m["done"] and "does NOT match the reference" in m["summary"] and "brightness x0.62" in m["summary"]


# ------------------------------------------------------------------ graphics (title card, name tags) and the effect on each

class FakeG(Fake):
    def __call__(self, cmd, **kw):
        script = Path(cmd[1]).name
        args = [str(a) for a in cmd[2:]]
        if script == "make_title.py":
            self.calls.append(script)
            Path(args[args.index("--out") + 1]).mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if script == "make_audio.py":
            out = Path(args[args.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            meta = {"clips": [{"kind": "music", "name": "music_stem.wav", "path": "/x/m.wav", "start_sec": 0.0, "duration_sec": 30.0},
                              {"kind": "sfx", "name": "sfx_clip.wav", "path": "/x/s.wav", "start_sec": float(args[args.index("--sfx-at") + 1]) if "--sfx-at" in args else 0.0, "duration_sec": 1.0}]}
            for n in ("placement.json", "audio.json"):
                (out / n).write_text(json.dumps(meta))
            self.calls.append(script)
            self.audio_args = args
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if script == "make_captions.py":
            self.caption_args = args
        return super().__call__(cmd, **kw)


def plan_stub(spec_labels=("Bob", "Mitch"), times=(0.05, 3.0, 9.5)):
    def fn(cut, mic_dir, cache):
        return ({"title": {"small": "Topic"}, "labels": [{"text": n} for n in spec_labels]}, list(times), [])
    return fn


def test_graphics_come_before_the_captions_and_the_captions_keep_clear_of_them(xml, tmp_path):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", music=False, bleep=False, graphics=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub())
    assert [s["name"] for s in r["steps"]] == ["graphics", "captions"]
    assert f.calls.index("make_title.py") < f.calls.index("make_captions.py")
    assert "--avoid" in f.caption_args and str(tmp_path / "out" / "graphics") in f.caption_args
    assert "Bob and Mitch" in r["steps"][0]["summary"]


def test_a_sound_effect_comes_on_with_each_graphic_on_one_effect_file(xml, tmp_path):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=True, sfx=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub())
    assert "--sfx-at" in f.audio_args and f.audio_args[f.audio_args.index("--sfx-at") + 1] == "0.05" and "--no-sfx" not in f.audio_args
    meta = json.loads((tmp_path / "out" / "audio" / "placement.json").read_text())
    assert [c["start_sec"] for c in meta["clips"] if c["kind"] == "sfx"] == [0.05, 3.0, 9.5]            # on the title, and on each name tag
    assert "a sound effect on each graphic (3)" in next(s for s in r["steps"] if s["name"] == "music")["summary"]


def test_effects_without_graphics_are_not_invented(xml, tmp_path):
    f = FakeG()
    fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=False, sfx=True, runner=f, bleep_fn=f.bleep)
    assert "--no-sfx" in f.audio_args


def test_effects_say_so_when_the_music_step_that_places_them_is_off(xml, tmp_path):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", captions=False, music=False, bleep=False, graphics=True, sfx=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub())
    s = next(s for s in r["steps"] if s["name"] == "sfx")
    assert not s["done"] and "music is off" in s["summary"]


def _cut(xml):
    return timeline.load_cut(xml)


def fake_analyse(runs_by_clip):
    def analyse(path, rows, mic_dir, cache=None, progress=None):
        return {"people": {"Bob": 0.3, "Mitch": 0.7}, "evidence": "x", "clips": {r["idx"]: {"runs": runs_by_clip.get(r["idx"], []), "pieces": []} for r in rows}}
    return analyse


def test_name_tags_come_on_at_each_persons_first_real_turn_and_after_the_title(xml):
    cut = _cut(xml)
    spec, times, notes = fc.graphics_plan(cut, "/mics", None, analyse=fake_analyse({1: [(0.0, 5.0, "Bob")], 2: [(0.0, 0.5, "Mitch"), (0.5, 6.0, "Mitch")]}))
    assert spec["title"]["small"] == cut.sequence_name.strip() and spec["title"]["big"] == ""         # the words are the sequence's own, nothing invented
    labels = spec["labels"]
    assert [l["text"] for l in labels] == ["Bob", "Mitch"]
    assert times[0] == fc.SFX_AT_START and len(times) == 3
    assert times[1] >= fc.TITLE_HOLD + fc.TAG_AFTER_TITLE - 1e-6                                       # Bob's tag waits for the title to go
    assert not notes


def test_a_turn_under_a_second_earns_no_name_tag_and_the_note_says_so(xml):
    spec, times, notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=fake_analyse({1: [(0.0, 5.0, "Bob")], 2: [(0.0, 0.6, "Mitch")]}))
    assert [l["text"] for l in spec["labels"]] == ["Bob"] and any("Mitch" in n for n in notes)


def test_without_a_recordings_folder_the_title_still_comes_and_the_tags_are_left_out_with_a_reason(xml):
    spec, times, notes = fc.graphics_plan(_cut(xml), None, None)
    assert spec["title"] and "labels" not in spec and times == [fc.SFX_AT_START] and "recordings" in notes[0]


def test_a_failed_look_at_who_talks_costs_the_name_tags_only(xml):
    def boom(*a, **k):
        raise RuntimeError("both people's recorders could not be found")
    spec, times, notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=boom)
    assert spec["title"] and "labels" not in spec and times == [fc.SFX_AT_START] and "could not be found" in notes[0]


def test_name_tags_sit_above_the_captions_band_so_the_captions_stay_at_the_bottom(xml):
    spec, _times, _notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=fake_analyse({1: [(0.0, 5.0, "Bob")]}))
    assert spec["label_y"] == fc.TAG_Y and fc.TAG_Y < 0.65                                 # the wallpaper reel's own 0.69 would collide with the captions
