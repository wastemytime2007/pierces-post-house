"""The review page shows the layers placed on a cut. Synthetic media only."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "overlay"))
sys.path.insert(0, str(HERE.parent / "audio"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import layers as ly  # noqa: E402
import place_audio as pa  # noqa: E402
import place_overlay as po  # noqa: E402
import timeline  # noqa: E402
from build_review import build  # noqa: E402

_spec = importlib.util.spec_from_file_location("rl_tests", Path(__file__).with_name("test_revise.py"))
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("layers")
    vid, lav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.3", "-af", "lowpass=f=3000", str(lav)], check=True)
    cam = d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return d, vid, lav


def _overlay_folder(root: Path) -> Path:
    f = root / "ov"
    f.mkdir()
    # a solid red, half-transparent bar across the top third for 2 s: unmistakable when composited
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red@0.8:s=320x60:r=60000/1001:d=2,format=yuva444p10le,pad=320:180:0:0:color=black@0.0",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(f / "overlay.mov")], check=True)
    (f / "placement.json").write_text(json.dumps({"overlay_path": str(f / "overlay.mov"), "duration_sec": 2.0,
                                                  "anchor": {"source": "a.mp4", "source_sec": 22.0, "lead_sec": 0.5}}))
    return f


def _audio_folder(root: Path, sfx_at: float) -> Path:
    f = root / "au"
    f.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5:sample_rate=48000", "-af", "volume=0.3", "-ac", "2", "-c:a", "pcm_s16le", str(f / "music_stem.wav")], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1500:duration=1:sample_rate=48000", "-af", "volume=0.5", "-ac", "2", "-c:a", "pcm_s16le", str(f / "sfx_clip.wav")], check=True)
    (f / "placement.json").write_text(json.dumps({"kind": "audio", "clips": [
        {"kind": "music", "name": "music_stem.wav", "path": str(f / "music_stem.wav"), "start_sec": 0.0, "duration_sec": 5.0},
        {"kind": "sfx", "name": "sfx_clip.wav", "path": str(f / "sfx_clip.wav"), "start_sec": sfx_at, "duration_sec": 1.0}]}))
    return f


@pytest.fixture()
def layered(tmp_path, cut_media):
    _d, vid, lav = cut_media
    base = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    base.write_text(base.read_text().replace("<audio><track>", "<audio><format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format><track>", 1))
    with_overlay = tmp_path / "with_overlay.xml"
    po.place(base, with_overlay, _overlay_folder(tmp_path))
    full = tmp_path / "full.xml"
    pa.place(with_overlay, full, _audio_folder(tmp_path, sfx_at=9.0))
    return base, full


def test_placed_music_and_effects_are_not_mistaken_for_the_cuts_speech(layered):
    base, full = layered
    a, b = timeline.load_cut(base), timeline.load_cut(full)
    assert len(b.audio) == len(a.audio) and {x.name for x in b.audio} == {x.name for x in a.audio}
    assert [round(x.tl_start, 3) for x in b.audio] == [round(x.tl_start, 3) for x in a.audio]


def test_the_layers_are_read_back_from_the_xml_in_order(layered):
    _base, full = layered
    got = ly.find_layers(full)
    assert [(l.kind, l.name) for l in got] == [("video", "overlay.mov"), ("audio", "music_stem.wav"), ("audio", "sfx_clip.wav")]
    assert got[1].start == 0.0 and got[2].start == pytest.approx(9.0, abs=0.02)
    assert ly.find_layers(_base_of(full)) == []


def _base_of(full):
    return full.with_name("cut.xml")


def test_a_cut_with_no_layers_gets_no_full_preview(layered, tmp_path):
    base, _full = layered
    page = build(base, tmp_path / "plain", height=180)
    data = json.loads((tmp_path / "plain" / "timeline.json").read_text())
    assert "preview_full" not in data and "layers" not in data and not (tmp_path / "plain" / "preview_full.mp4").exists()
    assert 'id="layersBtn"' in page.read_text() and "preview_full" not in page.read_text().split("const DATA =")[1].split(";\n")[0]


def test_the_page_carries_a_verified_full_preview_and_the_layer_list(layered, tmp_path, capsys):
    _base, full = layered
    page = build(full, tmp_path / "rev", height=180)
    out = capsys.readouterr().out
    assert "[FAIL]" not in out and "VISIBLE (overlay.mov)" in out and "AUDIO-LAYER-IN-THE-MIX (sfx_clip.wav)" in out
    data = json.loads((tmp_path / "rev" / "timeline.json").read_text())
    assert data["preview_full"] == "preview_full.mp4" and [l["name"] for l in data["layers"]] == ["overlay.mov", "music_stem.wav", "sfx_clip.wav"]
    assert (tmp_path / "rev" / "preview.mp4").exists() and (tmp_path / "rev" / "preview_full.mp4").exists()
    assert "preview_full.mp4" in page.read_text()


def test_verification_notices_a_composite_that_is_missing_a_layer(layered, tmp_path):
    _base, full = layered
    build(full, tmp_path / "rev", height=180)
    rev = tmp_path / "rev"
    layers = ly.find_layers(full)
    cut = timeline.load_cut(full)
    # a "full" preview made with no layers at all is just the clean one: the layers must be reported as absent
    shutil.copy(rev / "preview.mp4", rev / "not_really_full.mp4")
    rows = {n: ok for n, ok, _d in ly.verify(rev / "preview.mp4", rev / "not_really_full.mp4", layers, cut.zone_end)}
    assert rows["VISIBLE (overlay.mov)"] is False
    assert rows["AUDIO-LAYER-IN-THE-MIX (music_stem.wav)"] is False and rows["AUDIO-LAYER-IN-THE-MIX (sfx_clip.wav)"] is False


def test_a_layer_file_that_has_gone_missing_is_refused(layered):
    _base, full = layered
    text = full.read_text().replace("sfx_clip.wav", "gone_missing.wav")
    bad = full.with_name("bad.xml")
    bad.write_text(text)
    with pytest.raises(timeline.TimelineError, match="layer file not found"):
        ly.find_layers(bad)


def test_a_revision_that_ripples_the_cut_moves_the_layers_with_it_and_the_page_still_verifies(layered, tmp_path, capsys):
    import apply_ops
    _base, full = layered
    cut = timeline.load_cut(full)
    before = {l.name: l for l in ly.find_layers(full)}
    notes = [{"timeline_sec": 2.5, "text": "cut 2 to 3 seconds, dead air"}]
    ops = [{"note": 1, "op": "remove_range", "start": 2.0, "end": 3.0, "why": "stated"}]
    out = tmp_path / "revised.xml"
    changes, delta = apply_ops.apply_ops(full, out, cut, ops, notes)
    assert changes[0].applied and delta == pytest.approx(-1.0, abs=0.05)

    by = {}
    for l in ly.find_layers(out):
        by.setdefault(l.name, []).append(l)
    assert by["overlay.mov"][0].start == pytest.approx(before["overlay.mov"].start - 1.0, abs=0.05)      # after the cut: it moves earlier with the picture
    assert by["sfx_clip.wav"][0].start == pytest.approx(before["sfx_clip.wav"].start - 1.0, abs=0.05)
    music = sorted(by["music_stem.wav"], key=lambda l: l.start)                                          # spans the cut: two pieces, the second skips the removed second
    assert len(music) == 2
    assert music[0].start == 0.0 and music[0].end == pytest.approx(2.0, abs=0.05) and music[0].src_in == 0.0
    assert music[1].start == pytest.approx(2.0, abs=0.05) and music[1].src_in == pytest.approx(3.0, abs=0.05) and music[1].src_out == pytest.approx(5.0, abs=0.05)

    warn = ly.layer_warnings(ly.find_layers(full), ly.find_layers(out))
    assert len(warn) == 1 and warn[0].startswith("music_stem.wav lost 1.00s") and "re-placed" in warn[0]     # only the layer the cut ran through
    assert ly.layer_warnings(ly.find_layers(full), ly.find_layers(full)) == []

    build(out, tmp_path / "rev2", height=180)
    text = capsys.readouterr().out
    assert "[FAIL]" not in text and "AUDIO-LAYER-IN-THE-MIX (music_stem.wav 0.0-2.0s)" in text and "AUDIO-LAYER-IN-THE-MIX (music_stem.wav 2.0-4.0s)" in text


def test_the_beatmap_has_a_lane_per_kind_of_decision_in_a_fixed_order(layered):
    _base, full = layered
    lanes = ly.beatmap(timeline.load_cut(full), ly.find_layers(full))
    assert [l["name"] for l in lanes] == ["Cuts", "Callout", "Music", "SFX"]
    by = {l["name"]: l for l in lanes}
    assert by["Cuts"]["kind"] == "ticks" and len(by["Cuts"]["items"]) == 2                   # three clips, two seams
    assert by["Callout"]["kind"] == "blocks" and by["Callout"]["items"][0]["end"] > by["Callout"]["items"][0]["start"]
    assert by["SFX"]["items"][0]["start"] == pytest.approx(9.0, abs=0.02)
    starts = [i["start"] for i in by["Cuts"]["items"]]
    assert starts == sorted(starts)


def test_captions_expand_to_one_block_per_line_offset_for_a_split_piece(tmp_path):
    (tmp_path / "captions.json").write_text(json.dumps({"groups": [
        {"text": "early", "show_start": 0.5, "show_end": 1.5}, {"text": "kept one", "show_start": 4.0, "show_end": 5.0},
        {"text": "kept two", "show_start": 6.0, "show_end": 7.0}, {"text": "past it", "show_start": 12.0, "show_end": 13.0}]}))
    piece = ly.Layer("video", "captions.mov", str(tmp_path / "captions.mov"), start=2.0, end=7.0, track=1, src_in=3.0, src_out=8.0)
    cut = type("C", (), {"video": []})()
    (lane,) = ly.beatmap(cut, [piece])[1:] or [None]
    assert lane["name"] == "Captions"
    assert [(b["label"], b["start"], b["end"]) for b in lane["items"]] == [("kept one", 3.0, 4.0), ("kept two", 5.0, 6.0)]     # file time 4.0 sits at 3.0 on the timeline


def test_the_revisions_applied_edits_get_their_own_lane_and_unapplied_ones_do_not(layered):
    _base, full = layered
    items = [{"note": 1, "applied": True, "v2_time": 4.0, "summary": "removed 2s of dead air"}, {"note": 2, "applied": False, "v2_time": None, "summary": "not done"}]
    lanes = ly.beatmap(timeline.load_cut(full), ly.find_layers(full), items)
    edits = next(l for l in lanes if l["name"] == "Edits")
    assert [i["label"] for i in edits["items"]] == ["note 1: removed 2s of dead air"] and lanes[1]["name"] == "Edits"


def test_an_image_card_gets_its_own_lane_between_the_callout_and_the_captions():
    mk = lambda name: ly.Layer("video", name, "/x/" + name, 1.0, 4.0, 1)                     # noqa: E731
    assert ly.lane_name(mk("card.mov")) == "Card" and ly.lane_name(mk("overlay.mov")) == "Callout" and ly.lane_name(mk("captions.mov")) == "Captions"
    cut = type("C", (), {"video": []})()
    lanes = ly.beatmap(cut, [mk("captions.mov"), mk("card.mov"), mk("overlay.mov")])
    assert [l["name"] for l in lanes] == ["Cuts", "Callout", "Card", "Captions"]


def test_a_cut_with_no_layers_still_gets_a_cuts_lane_and_the_page_carries_the_beatmap(layered, tmp_path):
    base, _full = layered
    page = build(base, tmp_path / "plain", height=180)
    data = json.loads((tmp_path / "plain" / "timeline.json").read_text())
    assert [l["name"] for l in data["beatmap"]] == ["Cuts"]
    assert 'id="beatmap"' in page.read_text() and '"beatmap"' in page.read_text()
