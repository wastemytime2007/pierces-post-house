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
