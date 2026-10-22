"""An export nobody gave a place goes in the project's own exports folder, under a name that says what and when, and never over an earlier one."""
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _tools():
    # Loaded by path: other tests put PreCut's modules on the path, and a plain import could find a different one
    spec = importlib.util.spec_from_file_location("posthouse_app_creator_tools", REPO / "app" / "python_backend" / "creator_tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["posthouse_app_creator_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


WHEN = datetime(2026, 10, 22, 9, 5)


def test_goes_in_the_exports_folder_with_a_safe_readable_name(tmp_path):
    out = _tools().default_export_path(tmp_path / "exports", "Runnells: Wall/Removal", 2, WHEN)
    assert out.parent == tmp_path / "exports"
    assert out.name == "Runnells_Wall_Removal_2seq_2026-10-22_0905.xml"


def test_never_overwrites_an_earlier_export(tmp_path):
    tools = _tools()
    first = tools.default_export_path(tmp_path, "P", 1, WHEN)
    first.write_text("x")
    second = tools.default_export_path(tmp_path, "P", 1, WHEN)
    assert second != first and second.suffix == ".xml" and not second.exists()
    second.write_text("y")
    assert tools.default_export_path(tmp_path, "P", 1, WHEN) not in (first, second)


def test_an_empty_name_still_gives_a_name(tmp_path):
    assert _tools().default_export_path(tmp_path, "", 1, WHEN).name.startswith("export_1seq_")
