"""The project list says where each project stands (transcribed, stories found, XMLs sent). Cheap, and never raises on odd folders."""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _app_project():
    """The app's own project.py, loaded by path: other tests put a different module named `project` on the path (PreCut's), and a plain import would find that one."""
    spec = importlib.util.spec_from_file_location("posthouse_app_project", REPO / "app" / "python_backend" / "project.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                 # dataclasses look their own module up while it is being executed
    spec.loader.exec_module(mod)
    return mod


def test_summary_counts_transcripts_stories_and_exports(tmp_path):
    project = _app_project()
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "idea_a.json").write_text("{}")
    (tmp_path / "plans" / "idea_b.json").write_text("{}")
    (tmp_path / "plans" / "._idea_a.json").write_bytes(b"\x00\x05sidecar")     # macOS sidecar: not a story
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "cut.xml").write_text("<xmeml/>")
    (tmp_path / "exports" / "._cut.xml").write_bytes(b"\x00")
    d = {"sources": [{"kind": "aroll", "files": {"a.mp4": {"transcript_status": "done"}, "b.mp4": {"transcript_status": "pending"}}},
                     {"kind": "broll", "files": {"c.mp4": {"transcript_status": "done"}}}]}
    assert project.project_summary(tmp_path, d) == {"transcribed": 1, "stories": 2, "exports": 1}


def test_exports_saved_elsewhere_are_counted_from_the_apps_own_record(tmp_path):
    project = _app_project()
    saved = tmp_path / "Desktop" / "trimmed.xml"
    saved.parent.mkdir()
    saved.write_text("<xmeml/>")
    (tmp_path / "review_exports.json").write_text(json.dumps([{"path": str(saved), "time": 1}, {"path": str(tmp_path / "gone.xml"), "time": 2}]))
    assert project.project_summary(tmp_path, {"sources": []})["exports"] == 1       # the file that no longer exists is not counted


def test_summary_never_raises(tmp_path):
    project = _app_project()
    assert project.project_summary(tmp_path / "missing", {}) == {"transcribed": 0, "stories": 0, "exports": 0}
    assert project.project_summary(tmp_path, {"sources": None}) == {"transcribed": 0, "stories": 0, "exports": 0}
