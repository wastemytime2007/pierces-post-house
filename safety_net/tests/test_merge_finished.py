"""Sending the finished batch back to Premiere as one XML: each sequence is export-checked first, only those that pass are merged, the rest are named, and the result is read back before it is reported."""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))
sys.path.insert(0, str(REPO / "labs" / "review_loop" / "tests"))


def _make(tmp_path, name):
    from test_merge_sequences import _write
    d = tmp_path / "Seqs" / f"{name}_v2 - revised"
    d.mkdir(parents=True)
    p = d / f"{name}_v2.xml"
    p.write_text(_write(tmp_path, name, f"{name}.mov").read_text())
    return p


def test_only_sequences_that_pass_the_check_are_merged_and_the_rest_are_named(tmp_path, monkeypatch):
    import creator_tools as ct
    a, b, c = (_make(tmp_path, n) for n in ("Story A", "Story B", "Story C"))
    monkeypatch.setattr(ct, "export_xml", lambda xml, open_in_premiere=True, **k: {"verified": "Story B" not in xml, "failed": ["AUDIO-ENABLED"] if "Story B" in xml else []})
    res = ct.merge_finished([str(a), str(b), str(c)], name="Batch")
    assert res["included"] == 2 and res["sequences"] == ["Story A", "Story C"]
    assert res["excluded"] == [{"name": "Story B_v2", "failed": ["AUDIO-ENABLED"]}]
    assert Path(res["xml"]).parent == tmp_path / "Seqs"                          # beside the sequences' folders, never inside or over one of them
    assert Path(res["xml"]).name.startswith("All finished (")


def test_nothing_passing_is_an_error_that_says_why(tmp_path, monkeypatch):
    import creator_tools as ct
    a = _make(tmp_path, "Story A")
    monkeypatch.setattr(ct, "export_xml", lambda xml, open_in_premiere=True, **k: {"verified": False, "failed": ["ZONE-GAP"]})
    with pytest.raises(ct.ToolError) as e:
        ct.merge_finished([str(a)])
    assert "ZONE-GAP" in str(e.value)


def test_a_missing_xml_is_left_out_not_a_crash(tmp_path, monkeypatch):
    import creator_tools as ct
    a = _make(tmp_path, "Story A")
    real = ct.export_xml

    def fake(xml, open_in_premiere=True, **k):
        if not Path(xml).is_file():
            raise ct.ToolError("that XML is not there")
        return {"verified": True, "failed": []}

    monkeypatch.setattr(ct, "export_xml", fake)
    res = ct.merge_finished([str(a), str(tmp_path / "gone.xml")])
    assert res["included"] == 1 and res["excluded"][0]["name"] == "gone"


def test_open_xml_refuses_anything_that_is_not_an_xml(tmp_path, monkeypatch):
    import creator_tools as ct
    monkeypatch.setenv("POSTHOUSE_NO_OPEN", "1")
    t = tmp_path / "notes.txt"
    t.write_text("x")
    with pytest.raises(ct.ToolError):
        ct.open_xml(str(t))
    x = tmp_path / "a.xml"
    x.write_text("<xmeml/>")
    assert ct.open_xml(str(x))["opened"] is False                                # tests open nothing
