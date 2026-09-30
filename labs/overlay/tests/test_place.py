"""Hermetic tests for placing an overlay in an export XML. Synthetic media only."""
import importlib.util
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import place_overlay as po  # noqa: E402
import timeline  # noqa: E402

# reuse the synthetic cut builder from the review_loop tests
_spec = importlib.util.spec_from_file_location("rl_tests", HERE.parent / "review_loop" / "tests" / "test_revise.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _mov(path: Path, size="320x180", rate="60000/1001", pix="yuva444p10le", secs=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x00000000:s={size}:r={rate}:d={secs},format={pix}",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", pix, str(path)], check=True)


@pytest.fixture(scope="module")
def cut_media(tmp_path_factory):
    d = tmp_path_factory.mktemp("place")
    vid, lav = d / "a.mp4", d / "lav.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=60:c=white:r=48000:a=0.5", "-af",
                    "volume=enable='between(t,10,12)':volume=0", str(lav)], check=True)
    cam = d / "cam.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "5.005", "-i", str(lav), str(cam)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-i", str(cam), "-t", "55",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(vid)], check=True)
    return d, vid, lav


def _folder(tmp_path, anchor_source_sec, lead=0.5, size="320x180", rate="60000/1001", pix="yuva444p10le"):
    f = tmp_path / "ov"
    f.mkdir()
    mov = f / "overlay.mov"
    _mov(mov, size, rate, pix)
    (f / "placement.json").write_text(json.dumps({
        "overlay_path": str(mov), "duration_sec": 2.0,
        "anchor": {"source": "a.mp4", "source_sec": anchor_source_sec, "lead_sec": lead}}))
    return f


def test_places_the_overlay_on_a_new_track_at_the_notes_frame(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    folder = _folder(tmp_path, anchor_source_sec=22.0)               # inside clip 2 (source 20-30s)
    out = tmp_path / "placed.xml"
    info = po.place(xml, out, folder)
    rows = po.verify_placed(xml, out, info, folder)
    assert not [(n, d) for n, ok, d in rows if ok is False], rows

    cut = timeline.load_cut(xml)
    clip2 = cut.video[1]
    want = clip2.tl_start + (22.0 - clip2.src_in) - 0.5
    root = ET.parse(out).getroot()
    tracks = timeline._seq_for_cut(root).findall("media/video/track")
    assert len(tracks) == 2
    ci = tracks[1].find("clipitem")
    assert int(ci.findtext("start")) == round(want * info["fps"])
    assert int(ci.findtext("out")) - int(ci.findtext("in")) == int(ci.findtext("end")) - int(ci.findtext("start"))
    assert ci.findtext("alphatype") == "straight"
    assert out.read_text().startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')


def test_placement_follows_the_frame_when_the_cut_is_re_timed(tmp_path, cut_media):
    _d, vid, lav = cut_media
    a = rl.make_xml(tmp_path / "a.xml", vid, lav)
    b = rl.make_xml(tmp_path / "b.xml", vid, lav, first_len=270)        # clip 1 shorter, everything after it earlier
    folder = _folder(tmp_path, anchor_source_sec=22.0)
    ia, ib = po.place(a, tmp_path / "pa.xml", folder), po.place(b, tmp_path / "pb.xml", folder)
    shift_sec = (600 - 270) / (60000 / 1001)
    assert (ia["start"] - ib["start"]) / ia["fps"] == pytest.approx(shift_sec, abs=0.03)


def test_refuses_an_overlay_that_is_not_the_sequences_size(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    with pytest.raises(po.PlaceError, match="re-render it with make_overlay.py --xml"):
        po.place(xml, tmp_path / "o.xml", _folder(tmp_path, 22.0, size="640x360"))


def test_refuses_an_overlay_at_the_wrong_frame_rate(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    with pytest.raises(po.PlaceError, match="fps"):
        po.place(xml, tmp_path / "o.xml", _folder(tmp_path, 22.0, rate="30"))


def test_refuses_an_overlay_without_alpha(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    with pytest.raises(po.PlaceError, match="no alpha"):
        po.place(xml, tmp_path / "o.xml", _folder(tmp_path, 22.0, pix="yuv444p10le"))


def test_refuses_when_the_frame_is_no_longer_in_the_cut(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    with pytest.raises(po.PlaceError, match="is not in this cut"):
        po.place(xml, tmp_path / "o.xml", _folder(tmp_path, 12.0))     # source 12s is in no clip


def test_refuses_to_run_past_the_end_of_the_cut(tmp_path, cut_media):
    _d, vid, lav = cut_media
    xml = rl.make_xml(tmp_path / "cut.xml", vid, lav)
    with pytest.raises(po.PlaceError, match="outside the cut"):
        po.place(xml, tmp_path / "o.xml", _folder(tmp_path, 39.9, lead=0.2))   # clip 3 ends at source 40s
