"""The project list says where each project stands (transcribed, stories found, XMLs sent). Cheap, and never raises on odd folders."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))


def test_summary_counts_transcripts_stories_and_exports(tmp_path):
    import project
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


def test_summary_never_raises(tmp_path):
    import project
    assert project.project_summary(tmp_path / "missing", {}) == {"transcribed": 0, "stories": 0, "exports": 0}
    assert project.project_summary(tmp_path, {"sources": None}) == {"transcribed": 0, "stories": 0, "exports": 0}
