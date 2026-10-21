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
    assert [s["name"] for s in r["steps"] if s["name"] != "punch-in"] == ["captions", "music", "bleep"]
    assert all(ok for _n, ok, _d in r["checks"] if ok is not None), r["checks"]


class DriftingTakes(Fake):
    """conform_music refuses the first `bad` takes: their beats do not sit on a steady grid (2026-10-09, which failed the whole finish)."""
    def __init__(self, bad):
        super().__init__()
        self.bad = bad

    def __call__(self, cmd, **kw):
        if Path(cmd[1]).name == "conform_music.py" and self.calls.count("conform_music.py") < self.bad:
            self.calls.append("conform_music.py")
            return subprocess.CompletedProcess(cmd, 1, "", "ConformError: the beats do not sit on a steady grid (31 ms off): not quantised, so not conformed")
        return super().__call__(cmd, **kw)


def test_a_take_whose_beats_drift_is_replaced_by_another_take(xml, tmp_path):
    f = DriftingTakes(bad=1)
    r = fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep)
    assert f.calls.count("score_music.py") == 2 and f.calls.count("conform_music.py") == 2
    music = next(s for s in r["steps"] if s["name"] == "music")
    assert music["done"] and "take 2" in music["summary"]


def test_when_no_take_can_be_cut_to_the_beat_the_last_is_laid_as_it_is_and_the_rest_of_the_finish_is_kept(xml, tmp_path, monkeypatch):
    laid = []
    monkeypatch.setattr(fc, "lay_untimed", lambda track, zone, out: (laid.append((Path(track).name, round(zone, 2))), Path(out).write_bytes(b"")))
    f = DriftingTakes(bad=5)
    r = fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep)
    assert f.calls.count("score_music.py") == fc.MUSIC_TAKES and len(laid) == 1
    music = next(s for s in r["steps"] if s["name"] == "music")
    assert music["done"] and "NOT cut to the beat" in music["summary"]
    assert [s["name"] for s in r["steps"] if s["name"] != "punch-in"] == ["captions", "music", "bleep"] and Path(r["xml"]).is_file()


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
    assert r["steps"][1]["name"] == "captions" and r["steps"][1]["done"] is False and "already" in r["steps"][1]["summary"]


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
        return ({"title": {"small": "Topic"}, "lower_thirds": [{"name": n} for n in spec_labels]}, list(times), [])
    return fn


def test_graphics_come_before_the_captions_and_the_captions_keep_clear_of_them(xml, tmp_path):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", music=False, bleep=False, graphics=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub())
    assert [s["name"] for s in r["steps"] if s["name"] != "punch-in"] == ["graphics", "captions"]
    assert f.calls.index("make_title.py") < f.calls.index("make_captions.py")
    assert "--avoid" in f.caption_args and str(tmp_path / "out" / "graphics") in f.caption_args
    assert "Bob and Mitch" in r["steps"][1]["summary"] and "lower thirds" in r["steps"][1]["summary"]


@pytest.fixture()
def whoosh(tmp_path):
    """A real 2 s audio file standing in for the library's whoosh, and the profile the picker would have measured for it."""
    f = tmp_path / "Fake - Whoosh.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=2:r=48000:a=0.3", "-ac", "2", str(f)], check=True)
    return lambda: {"file": str(f), "name": "Fake - Whoosh.wav", "sec": 2.0, "peak_at": 0.8, "body_sec": 0.9}


def test_a_sound_effect_comes_on_with_each_graphic_started_early_so_its_peak_lands_on_the_wipe(xml, tmp_path, whoosh):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=True, sfx=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub(), choose_fn=whoosh)
    assert "--sfx-at" in f.audio_args and "--no-sfx" not in f.audio_args
    assert "--sfx-file" in f.audio_args and "--sfx-below-mean-db" in f.audio_args                    # a real file, levelled by how loud it plays
    assert f.audio_args[f.audio_args.index("--sfx-at") + 1] == "0"                                    # its peak is 0.8 s in and lands 0.2 s into the wipe: 0.05 s minus 0.6 s starts at 0
    meta = json.loads((tmp_path / "out" / "audio" / "placement.json").read_text())
    assert [c["start_sec"] for c in meta["clips"] if c["kind"] == "sfx"] == [0.0, 2.4, 8.9]          # the others start 0.6 s before their graphic (3.0 s and 9.5 s in the stub)
    assert (tmp_path / "out" / "audio_in" / "sfx_graphic.wav").is_file()
    m = next(s for s in r["steps"] if s["name"] == "music")["summary"]
    assert "a sound effect on each graphic (3)" in m and "Fake - Whoosh" in m


def test_two_effects_never_land_on_top_of_each_other(xml, tmp_path, whoosh):
    f = FakeG()
    fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=True, sfx=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub(times=(0.05, 2.7, 3.2)), choose_fn=whoosh)
    meta = json.loads((tmp_path / "out" / "audio" / "placement.json").read_text())
    starts = [c["start_sec"] for c in meta["clips"] if c["kind"] == "sfx"]
    assert all(b - a >= fc.SFX_MAX_SEC for a, b in zip(starts, starts[1:])), starts


def test_with_no_real_whoosh_in_the_library_it_says_so_and_lets_make_audio_choose(xml, tmp_path):
    f = FakeG()
    r = fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=True, sfx=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub(), choose_fn=lambda: None)
    assert "--sfx-file" not in f.audio_args and "--sfx-at" in f.audio_args
    assert "no real whoosh was found" in next(s for s in r["steps"] if s["name"] == "music")["summary"]


def test_the_picker_takes_a_whoosh_that_builds_and_holds_and_skips_a_faint_click_and_the_generated_store(tmp_path):
    (tmp_path / "Air - Short Whoosh.wav").write_bytes(b"")
    (tmp_path / "Generated - soft pop whoosh.mp3").write_bytes(b"")
    (tmp_path / "Switch - Click.wav").write_bytes(b"")
    (tmp_path / "Rope - Long Whoosh.wav").write_bytes(b"")
    profiles = {"Air - Short Whoosh.wav": {"sec": 2.6, "peak_at": 0.8, "body_sec": 0.8}, "Rope - Long Whoosh.wav": {"sec": 4.8, "peak_at": 2.8, "body_sec": 2.6},
                "Generated - soft pop whoosh.mp3": {"sec": 1.2, "peak_at": 0.0, "body_sec": 0.1}, "Switch - Click.wav": {"sec": 1.2, "peak_at": 0.3, "body_sec": 0.2}}
    import sfx_library
    pick = fc.choose_graphic_sfx([tmp_path], profile=lambda p: profiles[p.name])
    assert pick["name"] == "Air - Short Whoosh.wav"                       # the rope's peak is 2.8 s in and the generated pop is faint and starts at its peak


def test_a_swapped_effect_is_replaced_by_the_next_best_whoosh_and_never_chosen_again(tmp_path):
    """Ryan, 2026-10-12: a note asking for a different sound effect: the one in use is skipped and the next best is taken."""
    for n in ("Air - Short Whoosh.wav", "Rope - Whoosh.wav"):
        (tmp_path / n).write_bytes(b"")
    profiles = {"Air - Short Whoosh.wav": {"sec": 2.6, "peak_at": 0.7, "body_sec": 1.5}, "Rope - Whoosh.wav": {"sec": 2.9, "peak_at": 0.9, "body_sec": 1.0}}
    first = fc.choose_graphic_sfx([tmp_path], profile=lambda p: profiles[p.name])
    second = fc.choose_graphic_sfx([tmp_path], profile=lambda p: profiles[p.name], exclude=(first["name"],))
    assert first["name"] == "Air - Short Whoosh.wav" and second["name"] == "Rope - Whoosh.wav"
    assert fc.choose_graphic_sfx([tmp_path], profile=lambda p: profiles[p.name], exclude=(first["name"], second["name"])) is None


def test_a_revision_keeps_the_effect_the_cut_already_has_unless_it_was_asked_to_swap_it(tmp_path):
    """Adding a folder to the effects library changed which effect a fresh finish picks; a revision must not change the cut's effect by itself."""
    for n in ("Air - Short Whoosh.wav", "Rope - Whoosh.wav"):
        (tmp_path / n).write_bytes(b"")
    profiles = {"Air - Short Whoosh.wav": {"sec": 2.6, "peak_at": 0.7, "body_sec": 1.5}, "Rope - Whoosh.wav": {"sec": 2.9, "peak_at": 0.9, "body_sec": 1.0}}
    prof = lambda p: profiles[p.name]   # noqa: E731
    assert fc.choose_graphic_sfx([tmp_path], profile=prof)["name"] == "Air - Short Whoosh.wav"                              # the better one when nothing is kept
    assert fc.choose_graphic_sfx([tmp_path], profile=prof, prefer="Rope - Whoosh")["name"] == "Rope - Whoosh.wav"            # the cut's own effect stays, named by its summary without the extension
    assert fc.choose_graphic_sfx([tmp_path], profile=prof, prefer="Rope - Whoosh", exclude=("Rope - Whoosh.wav",))["name"] == "Air - Short Whoosh.wav"   # asked to swap it: not kept


def test_the_effect_is_levelled_by_how_loud_it_plays_not_by_its_first_instant_click(tmp_path):
    import make_audio as ma
    speech = tmp_path / "speech.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=3:r=48000:a=0.1:seed=1", "-ac", "1", str(speech)], check=True)
    click = tmp_path / "click.wav"                                  # a 20 ms burst at full scale, then silence
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=0.02:r=48000:a=1.0:seed=2", "-af", "apad=whole_dur=1.2", "-ac", "1", str(click)], check=True)
    whoosh_f = tmp_path / "whoosh.wav"                              # half a second of sustained noise
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=0.8:r=48000:a=0.3:seed=3", "-ac", "1", str(whoosh_f)], check=True)
    assert ma.volume(click)["peak"] > ma.volume(whoosh_f)["peak"] + 5                    # by PEAK the click is far louder...
    assert ma.loud_part_db(whoosh_f) > ma.loud_part_db(click) + 3                        # ...but the whoosh is what plays louder, and that is what sets the level
    out = tmp_path / "o.wav"
    ma.build_sfx_clip(whoosh_f, speech, out, 6.0, 6.0)
    s, e = ma.volume(speech), ma.volume(out)
    assert e["peak"] <= s["peak"] - 4.0 + 0.6                                            # held under the speech's peak
    assert ma.loud_part_db(out) <= s["mean"] - 6.0 + 1.0                                 # and never louder than asked (it may be quieter where the peak cap holds a spiky effect down)


def test_effects_without_graphics_are_not_invented(xml, tmp_path):
    f = FakeG()
    fc.run(xml, tmp_path / "out", captions=False, bleep=False, graphics=False, sfx=True, runner=f, bleep_fn=f.bleep, choose_fn=lambda: None)
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
    labels = spec["lower_thirds"]
    assert [l["name"] for l in labels] == ["Bob", "Mitch"]
    assert times[0] == fc.SFX_AT_START and len(times) == 3
    assert times[1] >= fc.TITLE_HOLD + fc.TAG_AFTER_TITLE - 1e-6                                       # Bob's tag waits for the title to go
    assert not notes


def test_a_turn_under_a_second_earns_no_name_tag_and_the_note_says_so(xml):
    spec, times, notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=fake_analyse({1: [(0.0, 5.0, "Bob")], 2: [(0.0, 0.6, "Mitch")]}))
    assert [l["name"] for l in spec["lower_thirds"]] == ["Bob"] and any("Mitch" in n for n in notes)


def test_without_a_recordings_folder_the_title_still_comes_and_the_tags_are_left_out_with_a_reason(xml):
    spec, times, notes = fc.graphics_plan(_cut(xml), None, None)
    assert spec["title"] and "lower_thirds" not in spec and times == [fc.SFX_AT_START] and "recordings" in notes[0]


def test_a_failed_look_at_who_talks_costs_the_name_tags_only(xml):
    def boom(*a, **k):
        raise RuntimeError("both people's recorders could not be found")
    spec, times, notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=boom)
    assert spec["title"] and "lower_thirds" not in spec and times == [fc.SFX_AT_START] and "could not be found" in notes[0]


def test_name_tags_sit_above_the_captions_band_so_the_captions_stay_at_the_bottom(xml):
    spec, _times, _notes = fc.graphics_plan(_cut(xml), "/mics", None, analyse=fake_analyse({1: [(0.0, 5.0, "Bob")]}))
    assert spec["lt_y"] == fc.TAG_Y and fc.TAG_Y < 0.65                                 # the wallpaper reel's own 0.69 would collide with the captions


def test_the_call_to_action_is_the_ideas_own_words_up_to_its_first_condition():
    assert fc.short_cta("DM or comment 'septic' if you're selling a house you think might have one, or follow for more.") == "DM or comment 'septic'"
    assert fc.short_cta("Follow for more.") == "Follow for more"
    assert fc.short_cta("Tell us in the comments below what you would have done with all three of these houses") == ""     # too long for one line: nothing is shortened by rewording
    assert fc.short_cta("") == ""


def test_the_call_to_action_goes_over_the_end_of_the_cut_in_the_name_tag_box():
    """A plain white label sat on the white logo of Mitch's shirt and pushed the captions up over his face (2026-10-08)."""
    from timeline import Cut, VideoClip
    cut = Cut("s", 30.0, 1080, 1920, 20.0)
    cut.video += [VideoClip(1, 0.0, 12.0, "/c.mp4", 100.0, 112.0), VideoClip(2, 12.0, 20.0, "/c.mp4", 200.0, 208.0)]
    spec = {"title": {}}
    assert fc.add_cta(spec, cut, "DM or comment 'septic' if you're selling") == ""
    lt = spec["lower_thirds"][-1]
    assert lt["name"] == "DM or comment 'septic'" and lt["anchor"] == {"source": "c.mp4", "source_sec": 205.0} and "labels" not in spec
    short = Cut("s", 30.0, 1080, 1920, 13.0)
    short.video += [VideoClip(1, 0.0, 12.0, "/c.mp4", 100.0, 112.0), VideoClip(2, 12.0, 13.0, "/c.mp4", 200.0, 201.0)]
    assert "too short" in fc.add_cta({}, short, "Follow for more")


def _gfx_spec():
    """A title card, name tags for Bob and Mitch and the call to action, as the finish makes them, with an effect time on the title and on each name tag."""
    spec = {"title": {"anchor": {}, "small": "The septic you don't replace yet", "big": "", "builds": [0.0, 0.2, 0.4], "hold": 2.4},
            "lower_thirds": [{"anchor": {}, "name": "Bob", "sub": "Builder / Founder", "until": 3.2}, {"anchor": {}, "name": "Mitch", "sub": "Operator / CEO", "until": 3.2},
                             {"anchor": {}, "name": "DM or comment 'septic'", "sub": "", "until": 3.0}]}
    return spec, [0.1, 2.7, 17.5]


CTA = "DM or comment 'septic' if you're selling"


def test_a_note_can_change_the_words_of_the_title_a_name_tag_and_the_call_to_action():
    """Ryan, 2026-10-12: anything the editor made, it must be able to change from a note."""
    spec, times = _gfx_spec()
    spec, times, notes = fc.apply_graphics_edits(spec, times, [{"target": "title", "do": "text", "text": "Septic timing"}, {"target": "name:Bob", "do": "text", "text": "Bob, founder"},
                                                                {"target": "cta", "do": "text", "text": "DM us 'septic'"}, {"target": "name:Mitch", "do": "sub", "text": ""}], CTA)
    assert spec["title"]["small"] == "Septic timing" and notes == []
    assert [l["name"] for l in spec["lower_thirds"]] == ["Bob, founder", "Mitch", "DM us 'septic'"] and spec["lower_thirds"][1]["sub"] == ""


def test_an_edit_still_finds_a_tag_after_its_words_were_changed():
    spec, times = _gfx_spec()
    edits = [{"target": "name:Bob", "do": "text", "text": "Bob, founder"}, {"target": "name:Bob", "do": "longer", "seconds": 2.0}]      # the second edit names Bob by his first name
    spec, times, notes = fc.apply_graphics_edits(spec, times, edits, CTA)
    assert notes == [] and spec["lower_thirds"][0]["name"] == "Bob, founder" and spec["lower_thirds"][0]["until"] == 5.2


def test_taking_a_graphic_out_takes_its_sound_effect_with_it():
    spec, times = _gfx_spec()
    spec, times, _n = fc.apply_graphics_edits(spec, times, [{"target": "name:Bob", "do": "remove"}], CTA)
    assert [l["name"] for l in spec["lower_thirds"]] == ["Mitch", "DM or comment 'septic'"] and times == [0.1, 17.5]       # Bob's effect (2.7 s) is gone, the others stay on their graphics
    spec, times, _n = fc.apply_graphics_edits(spec, times, [{"target": "title", "do": "remove"}], CTA)
    assert "title" not in spec and times == [17.5]
    spec, times, _n = fc.apply_graphics_edits(spec, times, [{"target": "cta", "do": "remove"}, {"target": "name:Mitch", "do": "remove"}], CTA)
    assert "lower_thirds" not in spec and times == []                                                                     # nothing left: the finish skips the graphics layer


def test_a_graphic_can_be_kept_up_longer_or_ended_sooner_but_not_below_a_readable_time():
    spec, times = _gfx_spec()
    spec, _t, _n = fc.apply_graphics_edits(spec, times, [{"target": "title", "do": "longer"}, {"target": "cta", "do": "shorter", "seconds": 9.0}, {"target": "name:Bob", "do": "longer", "seconds": 1.0}], CTA)
    assert spec["title"]["hold"] == 2.4 + fc.GFX_LONGER_DEFAULT and spec["lower_thirds"][2]["until"] == 1.6 and spec["lower_thirds"][0]["until"] == 4.2


def test_an_edit_that_matches_no_graphic_says_so():
    spec, times = _gfx_spec()
    _s, _t, notes = fc.apply_graphics_edits(spec, times, [{"target": "name:Nobody", "do": "remove"}], CTA)
    assert notes == ["there is no 'Nobody' graphic to change"]


def test_a_music_track_shorter_than_a_lengthened_cut_is_extended_not_refused(tmp_path):
    """The first finish's track is kept by every rebuild; a revision that puts a line back makes the cut longer than the track and the fit refused, losing every layer (V3's replay, 2026-10-13)."""
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "m.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=5", str(src)], check=True)
    out = fc.extend_track(src, 14.0, tmp_path / "ext.wav")
    import timeline
    assert timeline.probe_duration(str(out)) >= 13.5
    assert timeline.probe_duration(str(out)) < 20



def test_keep_going_leaves_a_failed_layer_off_and_keeps_the_ones_that_worked(xml, tmp_path):
    """2026-10-21: the music service ran out of credits and no sequence of a batch of eleven kept anything. With keep_going the captions and the bleep stay, the music says why it is missing."""
    out = tmp_path / "out"
    f = Fake(fail_on="score_music.py")
    r = fc.run(xml, out, runner=f, bleep_fn=f.bleep, keep_going=True)
    by = {s["name"]: s for s in r["steps"]}
    assert by["captions"]["done"] and by["bleep"]["done"], "what worked is kept"
    assert by["music"]["done"] is False and by["music"]["failed"] is True and by["music"]["summary"].startswith("not added: ")
    assert Path(r["xml"]).is_file(), "a finished version is written"
    assert all(ok for _n, ok, _d in r["checks"] if ok is not None), r["checks"]


def test_without_keep_going_one_failure_still_keeps_nothing_as_before(xml, tmp_path):
    f = Fake(fail_on="score_music.py")
    with pytest.raises(fc.FinishError):
        fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep)


def test_keep_going_with_every_layer_failing_says_so_rather_than_writing_an_empty_version(xml, tmp_path):
    f = Fake(fail_on="make_captions.py")
    with pytest.raises(fc.FinishError, match="nothing could be added: captions not added"):
        fc.run(xml, tmp_path / "out", music=False, bleep=False, runner=f, bleep_fn=f.bleep, keep_going=True)


def test_a_failed_graphics_step_does_not_stop_the_captions_which_then_have_nothing_to_avoid(xml, tmp_path):
    class NoTitle(FakeG):
        def __call__(self, cmd, **kw):
            if Path(cmd[1]).name == "make_title.py":
                return subprocess.CompletedProcess(cmd, 1, "", "HyperFrames check FAILED:\n  1 error(s), 1 warning(s), 0 info(s)")
            return super().__call__(cmd, **kw)
    f = NoTitle()
    r = fc.run(xml, tmp_path / "out", music=False, bleep=False, graphics=True, runner=f, bleep_fn=f.bleep, graphics_fn=plan_stub(), keep_going=True)
    by = {s["name"]: s for s in r["steps"]}
    assert by["graphics"]["failed"] and "render check" in by["graphics"]["summary"]
    assert by["captions"]["done"] and "--avoid" not in f.caption_args, "no title was placed, so the captions keep clear of nothing"


def test_the_reasons_are_in_plain_words():
    q = fc.FinishError('The music failed, so nothing was kept. The tool said:\nmake_audio.AudioError: ElevenLabs music refused (401): {"detail":{"code":"quota_exceeded","message":"This request exceeds your quota of 47052. You have 191 credits remaining, while 450 credits are required for this request."}}')
    assert fc.why(q) == "the music service is out of credits (191 left, this cut needs 450): top up the ElevenLabs account and run it again"
    h = fc.FinishError("The captions failed, so nothing was kept. The tool said:\nHyperFrames check FAILED:\n  49 error(s), 0 warning(s), 6 info(s)")
    assert "render check refused it (49 error(s)" in fc.why(h)


def test_a_file_slug_becomes_a_readable_topic_and_a_real_title_is_left_alone():
    """WK_08_mitch_if_it_is_someone_s_home_never_cut_corners was one 2,474 px word in a 1,080 px frame."""
    assert fc.topic_of("WK_08_mitch_if_it_is_someone_s_home_never_cut_corners") == "If it is someone's home never cut corners"
    assert fc.topic_of("WK_07_mitch_why_you_can_t_just_hire_out_the_quality") == "Why you can't just hire out the quality"
    assert fc.topic_of("WK_04_bob_fixing_a_water_heater_the_inspector_missed") == "Fixing a water heater the inspector missed"
    assert fc.topic_of("LF_01_bob_what_running_a_crew_really_takes") == "What running a crew really takes"
    assert fc.topic_of("Keeping the Rustic Without Going Cheap") == "Keeping the Rustic Without Going Cheap"
    assert fc.topic_of("A gruff contractor snaps at the camera crew, apologizes") == "A gruff contractor snaps at the camera crew, apologizes"
    assert " " in fc.topic_of("WK_06_mitch_why_country_insurance_cost_20_more")



def _wav(path, seconds):
    import numpy as np
    import soundfile as sf
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.zeros(int(seconds * 8000), dtype="float32"), 8000)
    return path


def test_when_music_cannot_be_generated_an_already_generated_bed_is_used_and_the_step_says_which_and_why(xml, tmp_path):
    """Ryan, 2026-10-21, credits gone: "use already generated music or music from the Artlist folder when we run out"."""
    bed = tmp_path / "score_abc.mp3"
    bed.write_bytes(b"")
    f = Fake(fail_on="score_music.py")
    picked = []

    def pick(zone, name):
        picked.append((zone, name))
        return bed, "an already generated music bed (score_abc.mp3)"
    r = fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep, music_fallback=True, fallback_fn=pick)
    music = next(s for s in r["steps"] if s["name"] == "music")
    assert music["done"] and "already generated music bed (score_abc.mp3), because the music could not be generated" in music["summary"]
    assert "pretend failure in score_music.py" in music["summary"] and "nothing was kept" not in music["summary"], music["summary"]
    assert f.calls.count("score_music.py") == 1, "generation was tried once, not retried take after take"
    assert "conform_music.py" in f.calls and "place_audio.py" in f.calls, "the bed is fitted to the cuts and placed like any music"
    assert picked and picked[0][1], "the pick is told the cut's length and name"


def test_without_the_fallback_a_failed_generation_still_fails_the_step_as_before(xml, tmp_path):
    f = Fake(fail_on="score_music.py")
    with pytest.raises(fc.FinishError, match="The music"):
        fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep, fallback_fn=lambda z, n: (tmp_path / "x.mp3", "x"))


def test_when_nothing_is_long_enough_the_layer_is_reported_not_added_and_the_rest_is_kept(xml, tmp_path):
    f = Fake(fail_on="score_music.py")
    r = fc.run(xml, tmp_path / "out", runner=f, bleep_fn=f.bleep, music_fallback=True, fallback_fn=lambda z, n: None, keep_going=True)
    by = {s["name"]: s for s in r["steps"]}
    assert by["music"]["failed"] and by["captions"]["done"] and by["bleep"]["done"]


def test_the_picker_prefers_a_generated_bed_long_enough_and_spreads_cuts_across_them(tmp_path):
    cache = tmp_path / "cache"
    for n, secs in (("a", 12), ("b", 40), ("c", 41), ("d", 60), ("e", 9)):
        _wav(cache / f"score_{n}.mp3".replace(".mp3", ".wav"), secs)
        (cache / f"score_{n}.wav").rename(cache / f"score_{n}.mp3")                 # the picker reads score_*.mp3; ffprobe reads the wav inside whatever the name says
    a = fc.fallback_music(30.0, "WK_01_bob_x", cache=cache, library=tmp_path / "none")
    assert a and a[0].name in ("score_b.mp3", "score_c.mp3", "score_d.mp3") and "already generated" in a[1], "only beds with room for the cut (30 s + 4) qualify"
    assert fc.fallback_music(30.0, "WK_01_bob_x", cache=cache, library=tmp_path / "none")[0] == a[0], "the same cut always gets the same bed"
    names = {fc.fallback_music(30.0, f"WK_{i:02d}_cut", cache=cache, library=tmp_path / "none")[0].name for i in range(1, 30)}
    assert len(names) >= 2, "a batch of shorts is not all one track"


def test_with_no_long_enough_generated_bed_the_artlist_library_is_used_instrumentals_first_one_file_per_folder(tmp_path):
    cache = tmp_path / "cache"
    _wav(cache / "score_short.wav", 10)
    (cache / "score_short.wav").rename(cache / "score_short.mp3")
    lib = tmp_path / "Music"
    _wav(lib / "Artist - Sung Song" / "Artist - Sung Song.wav", 120)
    _wav(lib / "Artist - Sung Song" / "Artist - Sung Song (1).wav", 120)
    _wav(lib / "Band - Run - Instrumental version" / "Band - Run - Instrumental version.wav", 120)
    _wav(lib / "Tiny - Clip" / "Tiny - Clip.wav", 15)
    got = {fc.fallback_music(30.0, f"cut {i}", cache=cache, library=lib)[1] for i in range(12)}
    assert got == {"music from your Artlist library (Band - Run - Instrumental version)"}, "the instrumental, never the sung one, never one too short"
    only_sung = tmp_path / "Music2"
    _wav(only_sung / "Artist - Sung Song" / "Artist - Sung Song.wav", 120)
    assert fc.fallback_music(30.0, "cut", cache=cache, library=only_sung)[1] == "music from your Artlist library (Artist - Sung Song)", "a sung song is better than no music"
    assert fc.fallback_music(300.0, "a long one", cache=cache, library=lib) is None, "nothing long enough: None, so the layer is reported not added"
    assert fc.fallback_music(30.0, "cut", cache=tmp_path / "nope", library=tmp_path / "nope") is None


def test_the_reason_skips_the_framing_line_and_takes_the_tools_own_words():
    e = fc.FinishError("The music failed, so nothing was kept. The tool said:\nREFUSING: pretend failure in score_music.py\nmore detail")
    assert fc.why(e) == "pretend failure in score_music.py"
    assert fc.why(fc.FinishError("plain words, no framing")) == "plain words, no framing"
