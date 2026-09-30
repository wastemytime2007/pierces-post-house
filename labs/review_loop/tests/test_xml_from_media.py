"""A finished video becomes a one-clip edit the whole pipeline can open; the export check's coarse-slab rule is skipped for that (and only that) shape."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bleep"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import bleep as bl  # noqa: E402
import export_gate as eg  # noqa: E402
import timeline  # noqa: E402
import xml_from_media as xm  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    d = tmp_path_factory.mktemp("whole")
    v = d / "Finished Video.mp4"                                                              # a space in the name, like the real files
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=12:c=white:r=48000:a=0.3", "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=12",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)], check=True)
    return v


def test_a_finished_video_becomes_one_video_clip_and_one_audio_clip_at_its_own_size_and_rate(video, tmp_path):
    p = xm.make(video, tmp_path / "w.xml", "Finished Video")
    c = timeline.load_cut(p)
    assert len(c.video) == 1 and len(c.audio) == 1 and (c.width, c.height) == (180, 320) and c.fps == pytest.approx(30.0)
    assert c.zone_end == pytest.approx(12.0, abs=0.05) and c.audio[0].src_path == str(video) and c.audio[0].tl_end == pytest.approx(c.zone_end, abs=0.05)
    assert eg.is_whole_file(p)
    with pytest.raises(xm.MediaError, match="is not there"):
        xm.make(tmp_path / "nope.mp4", tmp_path / "x.xml")


def test_the_coarse_slab_rule_is_skipped_for_a_marked_whole_file_xml_and_kept_for_anything_else(video, tmp_path):
    p = xm.make(video, tmp_path / "w.xml")
    name, ok, detail = eg.row(p)
    assert ok is True and "CUT-GRANULARITY does not apply" in detail
    unmarked = tmp_path / "u.xml"
    unmarked.write_text(p.read_text().replace(eg.WHOLE_FILE_MARKER, "some other marker"))
    name, ok, detail = eg.row(unmarked)
    assert ok is False and "CUT-GRANULARITY" in detail                                        # negative control: the same shape without the marker is held to the rule


def test_the_bleep_pipeline_runs_end_to_end_on_a_converted_video_and_every_check_passes(video, tmp_path):
    p = xm.make(video, tmp_path / "w.xml")
    r = bl.bleep(p, tmp_path / "out", words_of=lambda w: [("Hello", 1.0, 1.4), ("Shit,", 5.0, 5.4)], check_transcript=False)
    assert r["xml"] and len(r["spans"]) == 1 and r["spans"][0] == pytest.approx((4.92, 5.52), abs=0.02)
    bad = [(n, d) for n, ok, d in r["rows"] if ok is False]
    assert not bad, bad
    assert any(n == "verify_export" and ok is True for n, ok, _d in r["rows"])
