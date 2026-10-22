"""Stills from the person's own footage (app/python_backend/thumbs.py): the project list shows a frame of each project, ideas show a storyboard, sequences show their own frame.
Synthetic media only. A missing file, a macOS "._" sidecar or an unplayable file gives None, never an error."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture()
def clip(tmp_path, monkeypatch):
    import thumbs
    monkeypatch.setattr(thumbs, "_cache_dir", lambda: tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    v = tmp_path / "Interview.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)], check=True)
    return v


def test_frame_is_a_jpeg_data_uri_and_is_cached(clip, tmp_path):
    import thumbs
    uri = thumbs.frame_data_uri(clip, 1.0, 160)
    assert uri and uri.startswith("data:image/jpeg;base64,")
    assert len(list((tmp_path / "cache").glob("*.jpg"))) == 1
    assert thumbs.frame_data_uri(clip, 1.0, 160) == uri                      # second time comes from the cache
    assert len(list((tmp_path / "cache").glob("*.jpg"))) == 1


def test_missing_sidecar_and_non_video_give_none(clip, tmp_path):
    import thumbs
    assert thumbs.frame_data_uri(tmp_path / "nope.mp4") is None
    side = tmp_path / "._Interview.mp4"
    side.write_bytes(b"\x00\x05\x16\x07 AppleDouble")
    assert thumbs.frame_data_uri(side) is None
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    assert thumbs.frame_data_uri(notes) is None


def test_a_broken_video_gives_none_not_an_error(tmp_path, monkeypatch):
    import thumbs
    monkeypatch.setattr(thumbs, "_cache_dir", lambda: tmp_path / "cache2")
    (tmp_path / "cache2").mkdir()
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video at all")
    assert thumbs.frame_data_uri(bad) is None


def test_project_poster_uses_the_interview_footage(clip, tmp_path):
    import thumbs
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"sources": [
        {"kind": "broll", "root_path": str(tmp_path / "missing"), "files": {}},
        {"kind": "aroll", "root_path": str(tmp_path), "files": {str(clip): {}}},
    ]}))
    assert thumbs.project_poster(proj, 160).startswith("data:image/jpeg;base64,")


def test_project_with_no_footage_has_no_poster(tmp_path):
    import thumbs
    proj = tmp_path / "empty"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"sources": []}))
    assert thumbs.project_poster(proj) is None
    assert thumbs.project_poster(tmp_path / "no_such_project") is None
