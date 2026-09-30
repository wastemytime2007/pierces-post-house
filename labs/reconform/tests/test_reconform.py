"""Hermetic tests for putting layers back on a revised cut. The caption and audio builders are faked."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
LABS = HERE.parent
for sub in ("review_loop", "overlay", "audio", "captions"):
    sys.path.insert(0, str(LABS / sub))
sys.path.insert(0, str(LABS.parent / "safety_net"))
sys.path.insert(0, str(HERE))

import apply_ops  # noqa: E402
import layers as ly  # noqa: E402
import place_audio as pa  # noqa: E402
import place_overlay as po  # noqa: E402
import reconform as rf  # noqa: E402
import timeline  # noqa: E402
from build_review import build  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", LABS / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
LEAD, T_IN = 0.5, 0.5


def _alpha_mov(path: Path, secs: float, size="320x180") -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red@0.8:s=320x60:r=60000/1001:d={secs:.3f},format=yuva444p10le,pad=320:180:0:0:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(path)], check=True)


def _wav(path: Path, secs: float, hz: int, vol: float) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={secs:.3f}:sample_rate=48000", "-af", f"volume={vol}",
                    "-ac", "2", "-c:a", "pcm_s16le", str(path)], check=True)


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("reconform")
    vid, lav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.3", "-af", "lowpass=f=3000", str(lav)], check=True)
    cam = d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return d, vid, lav


def _overlay_folder(root: Path, anchor_sec: float = 22.0) -> Path:
    f = root / "overlay"
    f.mkdir()
    _alpha_mov(f / "overlay.mov", 2.0)
    (f / "placement.json").write_text(json.dumps({
        "overlay_path": str(f / "overlay.mov"), "duration_sec": 2.0, "place_overlay_on_timeline_at_sec": 0.0,
        "anchor": {"source": "a.mp4", "source_sec": anchor_sec, "lead_sec": LEAD}, "geometry": {"t_in": T_IN}}))
    return f


def _captions_folder(root: Path, start: float, end: float) -> Path:
    f = root / "captions"
    f.mkdir(exist_ok=True)
    _alpha_mov(f / "captions.mov", end - start)
    (f / "captions.json").write_text(json.dumps({"window": {"start": start, "end": end}, "style": "pill", "groups": [{"text": "x"}]}))
    (f / "placement.json").write_text(json.dumps({"kind": "captions", "place_overlay_on_timeline_at_sec": start, "duration_sec": end - start,
                                                  "overlay": "captions.mov", "overlay_path": str((f / "captions.mov").resolve())}))
    return f


def _audio_folder(root: Path, start: float, end: float, callout_sec: float) -> Path:
    f = root / "audio"
    (f / "generated").mkdir(parents=True, exist_ok=True)
    _wav(f / "music_stem.wav", end - start, 440, 0.3)
    _wav(f / "sfx_clip.wav", 1.0, 1500, 0.5)
    clips = [{"kind": "music", "name": "music_stem.wav", "path": str(f / "music_stem.wav"), "start_sec": start, "duration_sec": end - start},
             {"kind": "sfx", "name": "sfx_clip.wav", "path": str(f / "sfx_clip.wav"), "start_sec": callout_sec, "duration_sec": 1.0}]
    (f / "placement.json").write_text(json.dumps({"kind": "audio", "clips": clips}))
    (f / "audio.json").write_text(json.dumps({
        "window": {"start": start, "end": end}, "music_db_rel_speech": -5.0, "duck_db": 12.0, "sfx_below_speech_peak_db": 6.0, "callout_sec": callout_sec,
        "generated": {"sfx": {"prompt": "a pop", "ms": 1200, "cached": True}, "music": {"prompt": "warm", "ms": 8000, "cached": True}}, "clips": clips}))
    return f


def _fake_captions(clean, out, start, end, style, avoid):
    out.mkdir(parents=True, exist_ok=True)
    _alpha_mov(out / "captions.mov", end - start)
    (out / "captions.json").write_text(json.dumps({"window": {"start": start, "end": end}, "style": style, "groups": [{"text": "a"}, {"text": "b"}]}))
    (out / "placement.json").write_text(json.dumps({"kind": "captions", "place_overlay_on_timeline_at_sec": start, "duration_sec": end - start,
                                                    "overlay": "captions.mov", "overlay_path": str((out / "captions.mov").resolve())}))
    (out / "cut_1080.mp4").write_bytes(b"x")
    (out / "captions_preview.mp4").write_bytes(b"x")
    _fake_captions.calls.append((start, end, style, [a.name for a in avoid]))


_fake_captions.calls = []


def _fake_audio(clean, out, meta, base, preview, start, end, callout, old_audio):
    p = json.loads((callout / "placement.json").read_text())
    t = p["place_overlay_on_timeline_at_sec"] + p["geometry"]["t_in"]
    _wav_folder = out
    (out / "generated").mkdir(parents=True, exist_ok=True)
    _wav(out / "music_stem.wav", end - start, 440, 0.3)
    _wav(out / "sfx_clip.wav", 1.0, 1500, 0.5)
    clips = [{"kind": "music", "name": "music_stem.wav", "path": str((out / "music_stem.wav").resolve()), "start_sec": start, "duration_sec": end - start},
             {"kind": "sfx", "name": "sfx_clip.wav", "path": str((out / "sfx_clip.wav").resolve()), "start_sec": t, "duration_sec": 1.0}]
    (out / "placement.json").write_text(json.dumps({"kind": "audio", "clips": clips}))
    _fake_audio.calls.append((start, end, t, meta["generated"]["music"]["ms"], str(old_audio.name)))


_fake_audio.calls = []


@pytest.fixture()
def world(tmp_path, cut_media):
    """A layered V3 cut (callout, captions, music, effect) and its revised XML after removing 1 s at 2-3 s."""
    _d, vid, lav = cut_media
    base = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    base.write_text(base.read_text().replace("<audio><track>", "<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format><track>", 1))
    ov = _overlay_folder(tmp_path)
    x1 = tmp_path / "x1.xml"
    info = po.place(base, x1, ov)
    callout = info["start"] / info["fps"] + T_IN
    cap = _captions_folder(tmp_path, 0.0, 6.0)
    x2 = tmp_path / "x2.xml"
    po.place(x1, x2, cap)
    au = _audio_folder(tmp_path, 0.0, 6.0, callout)
    layered = tmp_path / "cut_layers_v3.xml"
    pa.place(x2, layered, au)
    return {"tmp": tmp_path, "base": base, "layered": layered, "overlay": ov, "captions": cap, "audio": au, "callout": callout}


def _revise(world, start, end):
    cut = timeline.load_cut(world["layered"])
    out = world["tmp"] / "cut_layers_v4.xml"
    notes = [{"timeline_sec": (start + end) / 2, "text": f"cut {int(start)} to {int(end)} seconds, dead air"}]
    changes, delta = apply_ops.apply_ops(world["layered"], out, cut, [{"note": 1, "op": "remove_range", "start": start, "end": end, "why": "stated"}], notes)
    assert changes[0].applied
    return out, delta


def test_stripping_the_layers_leaves_exactly_the_cut(world):
    clean = world["tmp"] / "clean.xml"
    n = rf.strip_layers(world["layered"], clean)
    assert n >= 6                                                      # overlay, captions, and both channels of music and effect
    assert ly.find_layers(clean) == []
    a, b = timeline.load_cut(world["base"]), timeline.load_cut(clean)
    assert [(c.tl_start, c.tl_end, c.src_in, c.src_out) for c in a.video] == [(c.tl_start, c.tl_end, c.src_in, c.src_out) for c in b.video]
    ta = timeline._seq_for_cut(__import__("xml.etree.ElementTree", fromlist=["x"]).parse(world["base"]).getroot())
    tb = timeline._seq_for_cut(__import__("xml.etree.ElementTree", fromlist=["x"]).parse(clean).getroot())
    for kind in ("video", "audio"):
        assert po._ser(ta.findall(f"media/{kind}/track")) == po._ser(tb.findall(f"media/{kind}/track"))     # not one element of the cut differs


def test_layers_come_back_whole_and_anchored_after_a_revision_that_cut_through_them(world, capsys):
    revised, delta = _revise(world, 2.0, 3.0)
    assert ly.layer_warnings(ly.find_layers(world["layered"]), ly.find_layers(revised))          # the revision did cut through them
    _fake_captions.calls.clear(); _fake_audio.calls.clear()
    out = world["tmp"] / "rebuilt"
    final, ledger = rf.reconform(revised, [world["overlay"]], world["captions"], world["audio"], out, _fake_captions, _fake_audio)
    assert all(e["ok"] for e in ledger), ledger

    clean_cut = timeline.load_cut(final)
    layers = {l.name: l for l in ly.find_layers(final)}
    assert sorted(layers) == ["captions.mov", "music_stem.wav", "overlay.mov", "sfx_clip.wav"]
    assert len(ly.find_layers(final)) == 4                              # one piece each: none cut through
    # the callout sits on the frame it was drawn on, wherever the revision moved it
    want = po.anchor_time(clean_cut, {"source": "a.mp4", "source_sec": 22.0}) - LEAD
    assert layers["overlay.mov"].start == pytest.approx(want, abs=1.5 / clean_cut.fps)
    assert layers["overlay.mov"].start == pytest.approx(world["callout"] - T_IN - 1.0, abs=0.05)          # it moved earlier by the removed second
    # the effect is with the callout, the music and captions cover the shortened window
    assert layers["sfx_clip.wav"].start == pytest.approx(layers["overlay.mov"].start + T_IN, abs=1.5 / clean_cut.fps)
    assert layers["music_stem.wav"].start == 0.0 and layers["music_stem.wav"].end == pytest.approx(5.0, abs=0.06)
    assert layers["captions.mov"].end == pytest.approx(5.0, abs=0.06)
    # the builders were asked for the shortened window, with the old settings, avoiding the re-placed callout
    assert _fake_captions.calls[0][:3] == (0.0, pytest.approx(5.0, abs=0.06), "pill") and _fake_captions.calls[0][3] == ["overlay_1"]
    assert _fake_audio.calls[0][3] == 8000 and _fake_audio.calls[0][4] == "audio"                       # the original music length and cache: nothing regenerated
    rows = rf.check_result(final, revised, {"t_in": T_IN})
    assert all(ok for _n, ok, _d in rows), rows
    assert "cut through" in [d for n, _ok, d in rows if n == "info: REVISION-CUT-THROUGH"][0]


def test_the_rebuilt_result_builds_a_verified_review_page(world, capsys):
    revised, _ = _revise(world, 2.0, 3.0)
    final, _ledger = rf.reconform(revised, [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "rebuilt", _fake_captions, _fake_audio)
    capsys.readouterr()
    build(final, world["tmp"] / "page", height=180)
    text = capsys.readouterr().out
    assert "[FAIL]" not in text and "AUDIO-LAYER-IN-THE-MIX (sfx_clip.wav)" in text and "VISIBLE (overlay.mov)" in text


def test_a_callout_whose_frame_was_cut_out_is_reported_dropped_and_the_effect_goes_with_it(world):
    # the callout is drawn on source 22.0s, which sits at 12.0s on the cut; remove 11 to 13 seconds
    revised, _ = _revise(world, 11.0, 13.0)
    final, ledger = rf.reconform(revised, [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "rebuilt", _fake_captions, _fake_audio)
    by = {e["layer"]: e for e in ledger}
    assert by["callout 1"]["ok"] is False and "dropped" in by["callout 1"]["detail"] and "not in this cut" in by["callout 1"]["detail"]
    assert by["music and effect"]["ok"] is False and "no callout is left" in by["music and effect"]["detail"]
    assert by["captions"]["ok"] is True
    assert sorted({l.name for l in ly.find_layers(final)}) == ["captions.mov"]


def test_a_revision_that_leaves_almost_no_window_is_reported_not_rebuilt(world):
    revised, _ = _revise(world, 0.5, 6.0)                              # takes out 5.5 of the 6 seconds the layers covered, leaving half a second
    final, ledger = rf.reconform(revised, [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "rebuilt", _fake_captions, _fake_audio)
    by = {e["layer"]: e for e in ledger}
    assert by["captions"]["ok"] is False and "less than a second" in by["captions"]["detail"]


def test_stripping_that_would_change_the_cut_is_refused(world, monkeypatch):
    revised, _ = _revise(world, 2.0, 3.0)
    real = rf.strip_layers

    def damaging(a, b):
        n = real(a, b)
        text = b.read_text()
        b.write_text(text.replace("<start>0</start>", "<start>1</start>", 1))
        return n
    monkeypatch.setattr(rf, "strip_layers", damaging)
    with pytest.raises(rf.ReconformError, match="stripping the layers changed the cut"):
        rf.reconform(revised, [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "rebuilt", _fake_captions, _fake_audio)


def test_a_replaced_effects_folder_is_found_by_file_name_when_its_path_is_new():
    """replace_sfx writes a new folder holding music_stem.wav at a new path; the XML still points at the old one.
    Matching by path alone said the audio window was gone and dropped the music and effect from the result."""
    layer = ly.Layer("audio", "music_stem.wav", "/old/audio/music_stem.wav", 0.0, 21.2, 3)
    other = ly.Layer("audio", "sfx_clip.wav", "/old/audio/sfx_clip.wav", 13.2, 14.4, 5)
    assert rf.kept_end([layer, other], Path("/new/audio/music_stem.wav")) == 21.2              # same name, new folder
    assert rf.kept_end([layer, other], Path("/old/audio/music_stem.wav")) == 21.2              # exact path still wins
    assert rf.kept_end([layer, other], Path("/new/audio/nothing.wav")) is None                 # a file that is not there is still not there


# ---- leave a graphic out, apply an earlier caption fix again, bleep by default from the command line ----

def _fake_captions_fx(clean, out, start, end, style, avoid, fixes=None):
    _fake_captions_fx.fixes.append(fixes)
    _fake_captions(clean, out, start, end, style, avoid)


_fake_captions_fx.fixes = []


def test_a_graphic_named_to_be_dropped_is_left_out_and_said_so(world):
    out = world["tmp"] / "dropped"
    final, ledger = rf.reconform(world["layered"], [world["overlay"]], world["captions"], world["audio"], out, _fake_captions, _fake_audio, drop=[world["overlay"]])
    assert any("left out on request" in e["detail"] for e in ledger)
    assert sorted(l.name for l in ly.find_layers(final)) == ["captions.mov"]                          # no callout; with no callout to time it to, the effect and music are not rebuilt either


def test_a_caption_fix_made_earlier_is_applied_again_to_the_rebuilt_captions(world):
    cj = json.loads((world["captions"] / "captions.json").read_text())
    cj["fixes"] = [{"at": 1.0, "text": "the fixed words", "was": "x", "note": 3}]
    (world["captions"] / "captions.json").write_text(json.dumps(cj))
    _fake_captions_fx.fixes.clear()
    rf.reconform(world["layered"], [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "fx", _fake_captions_fx, _fake_audio)
    assert _fake_captions_fx.fixes == [[{"at": 1.0, "text": "the fixed words", "was": "x", "note": 3}]]
    _fake_captions_fx.fixes.clear()
    cj["fixes"] = []
    (world["captions"] / "captions.json").write_text(json.dumps(cj))
    rf.reconform(world["layered"], [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "nofx", _fake_captions_fx, _fake_audio)
    assert _fake_captions_fx.fixes == [None]                                                          # no fixes: the builder is called exactly as before


def test_a_bleeped_cut_is_unbleeped_before_rebuilding_and_the_bleep_step_runs_last(world):
    import bleep as bp
    bleeped = world["tmp"] / "bleeped_v4.xml"
    bp.mute_spans(world["layered"], bleeped, [(3.0, 3.5)])
    before = timeline.load_cut(world["layered"])
    assert sum(a.tl_end - a.tl_start for a in timeline.load_cut(bleeped).audio) < sum(a.tl_end - a.tl_start for a in before.audio) - 0.4
    seen = {}

    def fake_bleep(xml, out):
        seen["audio"] = sum(a.tl_end - a.tl_start for a in timeline.load_cut(xml).audio)            # what the bleep step is handed: the speech, whole again
        return {"xml": None, "spans": [], "hits": [], "suspects": [{"word": "what", "bleeped": False, "start": 1, "end": 2}], "words_heard": 12, "rows": []}
    final, ledger = rf.reconform(bleeped, [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "again", _fake_captions, _fake_audio, bleep_step=fake_bleep)
    assert seen["audio"] == pytest.approx(sum(a.tl_end - a.tl_start for a in before.audio), abs=0.05)
    b = next(e for e in ledger if e["layer"] == "bleep")
    assert "nothing bleeped" in b["detail"] and "check by ear" in b["detail"] and ledger[-1] is b        # the last step, and it reports what it could not decide
    assert sum(a.tl_end - a.tl_start for a in timeline.load_cut(final).audio) == pytest.approx(seen["audio"], abs=0.05)


def test_a_bleep_that_fails_its_checks_stops_the_reconform(world):
    def bad_bleep(xml, out):
        return {"xml": str(xml), "spans": [(1, 2)], "hits": [{"word": "x", "start": 1}], "suspects": [], "words_heard": 1, "rows": [("SPANS-SILENT", False, "still loud")]}
    with pytest.raises(rf.ReconformError, match="the bleep failed its checks"):
        rf.reconform(world["layered"], [world["overlay"]], world["captions"], world["audio"], world["tmp"] / "bad", _fake_captions, _fake_audio, bleep_step=bad_bleep)
