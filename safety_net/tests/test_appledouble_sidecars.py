"""macOS writes "._<name>" AppleDouble sidecars next to files on external drives (Ryan's RDOSS_2025). They are binary, so reading one as an idea or an export raised
UnicodeDecodeError ("can't decode byte 0xb0 in position 37") every time the Ideas tab opened (2026-10-21). Real files still list; sidecars are skipped; a damaged file does not stop the list."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))

SIDECAR = b"\x00\x05\x16\x07\x00\x02\x00\x00Mac OS X        \x00\x02\x00\x00\x00\t\x00\x00\x002\x00\x00\x0e\xb0\x00\x00\x00\x02"


class FakeProject:
    def __init__(self, plans):
        self._plans = plans

    def plans_dir(self):
        return self._plans


def _idea(plans: Path, name: str, idea_id: str):
    (plans / name).write_text(json.dumps({"idea_id": idea_id, "kind": "concept", "created_at": 1.0, "data": {"concept": "x"}}))


def test_list_ideas_skips_sidecars_and_damaged_files(tmp_path):
    import producer
    plans = tmp_path / "plans"
    plans.mkdir()
    _idea(plans, "idea_a.json", "a")
    (plans / "._idea_a.json").write_bytes(SIDECAR)
    (plans / "idea_bad.json").write_bytes(SIDECAR)            # a damaged real file must not stop the list either
    ids = [i["idea_id"] for i in producer.list_ideas(FakeProject(plans))]
    assert ids == ["a"]


def test_list_exports_skips_sidecar_xml(tmp_path):
    import creator_tools
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "cut.xml").write_text("<xmeml/>")
    (tmp_path / "exports" / "._cut.xml").write_bytes(SIDECAR)
    names = [e["name"] for e in creator_tools.list_exports(tmp_path)]
    assert names == ["cut.xml"]
