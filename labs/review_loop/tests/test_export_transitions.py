"""The Review tab's Open in Premiere with click-a-cut choices: the page's {timeline_sec, direction} list becomes an XML with Ryan's whoosh under each chosen cut (and a marker),
the export check runs on that file, and nothing is opened in these tests."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "app" / "python_backend"
sys.path.insert(0, str(BACKEND))


@pytest.fixture()
def demo(tmp_path):
    subprocess.run([sys.executable, str(REPO / "labs" / "uxp_transitions" / "make_demo_input.py"), str(tmp_path)], check=True, capture_output=True)
    return tmp_path / "2 - three cuts, no transition, no sound.xml"


def test_chosen_cuts_become_an_xml_with_whoosh_and_markers(demo, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_NO_OPEN", "1")
    import creator_tools as ct
    original = demo.read_bytes()
    res = ct.export_xml(str(demo), True, None, None, [{"timeline_sec": 100 / 29.97, "direction": "left"}, {"timeline_sec": 290 / 29.97 + 0.05, "direction": "up"}])
    assert demo.read_bytes() == original, "the version's own XML is never touched"
    out = Path(res["xml"])
    assert out.name == "2 - three cuts, no transition, no sound - with transitions.xml"
    assert res["transitions"]["cuts"] == 2 and res["transitions"]["left"] == 1
    text = out.read_text()
    assert "<name>PH transition</name>" in text and "<name>PH Push Up</name>" in text
    assert "<in>185</in><out>-1</out>" not in text, "the cut that was not chosen gets nothing"


def test_a_choice_that_matches_no_cut_is_refused_not_dropped_silently(demo, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_NO_OPEN", "1")
    import creator_tools as ct
    with pytest.raises(ct.ToolError):
        ct.export_xml(str(demo), True, None, None, [{"timeline_sec": 2.0, "direction": "left"}])


def test_no_choices_exports_the_version_as_before(demo, monkeypatch):
    monkeypatch.setenv("POSTHOUSE_NO_OPEN", "1")
    import creator_tools as ct
    res = ct.export_xml(str(demo), True, None, None, None)
    assert Path(res["xml"]) == demo and "transitions" not in res
