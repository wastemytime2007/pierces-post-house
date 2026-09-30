"""Hermetic tests for the project index: folders are classified from the records the tools leave, nothing is moved."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import project as pj  # noqa: E402


def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "Reviews"
    _w(r / "P v1 - review" / "review.html", "<html></html>")
    _w(r / "P v1 - review" / "timeline.json", {"clips": [{}, {}, {}], "duration": 74.2, "beatmap": [{"name": "Cuts"}, {"name": "SFX"}], "layers": [{}, {}]})
    _w(r / "P v1 - revise" / "ops.json", [])
    _w(r / "P v1 - revise" / "changes.json", {"from_label": "V2", "to_label": "V3", "items": [{"applied": True}, {"applied": False}], "layer_warnings": ["x lost 0.5s"]})
    _w(r / "P v1 - qa" / "qa.json", {"notes": [{"status": "VERIFIED"}, {"status": "VERIFIED"}, {"status": "NOT DONE"}], "unrequested": []})
    _w(r / "P v1 - qa" / "qa_report.html", "<html></html>")
    _w(r / "P v1 - notes (stand-in)" / "review_notes.json", {"notes": [{"text": "x"}], "_stand_in": "written by Claude"})
    _w(r / "P v1 - ryan notes" / "ryan_notes_on_v2.json", {"notes": [{"text": "a"}, {"text": "b"}]})
    _w(r / "P v1 - callout" / "placement.json", {"title": "The spacer", "subtitle": "a tab", "geometry": {"t_in": 1}, "anchor": {"source": "a.mp4", "source_sec": 335.6},
                                                 "place_overlay_on_timeline_at_sec": 12.98, "duration_sec": 5.0})
    _w(r / "P v1 - callout" / "overlay_preview.mp4", "x")
    _w(r / "P v1 - image card" / "placement.json", {"kind": "image_card", "image": "/x/shot.png", "anchor": {"source": "a.mp4", "source_sec": 579.4}, "caption": "The spacer", "hold_sec": 2.2})
    _w(r / "P v1 - audio" / "audio.json", {"window": {"start": 0, "end": 22}, "callout_sec": 13.97, "generated": {}, "music_reference": None})
    _w(r / "P v1 - audio" / "audio_preview.mp4", "x")
    _w(r / "P v1 - layers" / "cut_v3.xml", '<xmeml><file id="overlay-file-1"/></xmeml>')
    _w(r / "P v1 - layers" / "review" / "review.html", "<html></html>")
    _w(r / "P v1 - layers" / "review" / "timeline.json", {"clips": [{}], "duration": 10, "beatmap": []})
    _w(r / "Some other project" / "review.html", "<html></html>")
    for i, d in enumerate(sorted(p for p in r.iterdir() if p.is_dir())):
        for f in d.rglob("*"):
            os.utime(f, (1_700_000_000 + i * 100, 1_700_000_000 + i * 100))
    return r


def test_only_the_projects_folders_are_indexed_newest_first_and_each_is_classified(root):
    es = pj.scan(root, "P v1")
    assert len(es) == 9 and all(e["folder"].startswith("P v1") for e in es)
    assert [e["modified"] for e in es] == sorted((e["modified"] for e in es), reverse=True)
    kind = {e["folder"]: e["kind"] for e in es}
    assert kind["P v1 - review"] == "review_page" and kind["P v1 - revise"] == "revision" and kind["P v1 - qa"] == "qa" and kind["P v1 - callout"] == "callout"
    assert kind["P v1 - image card"] == "image_card" and kind["P v1 - audio"] == "audio" and kind["P v1 - notes (stand-in)"] == "notes" and kind["P v1 - ryan notes"] == "notes"


def test_each_entry_says_what_the_records_say(root):
    by = {e["folder"]: e for e in pj.scan(root, "P v1")}
    assert "V2 -> V3: 1 of 2 notes applied on the timeline; 1 layer warning(s)" in by["P v1 - revise"]["facts"][0]
    assert "3 notes: 2 verified, 1 not done" in by["P v1 - qa"]["facts"][0] and "nothing changed that was not asked for" in by["P v1 - qa"]["facts"][0]
    assert '"The spacer" / "a tab" at 12.98s for 5.0s, anchored to a.mp4 at 335.6s' in by["P v1 - callout"]["facts"][0]
    assert "shot.png" in by["P v1 - image card"]["facts"][0] and "anchored at 579.4s" in by["P v1 - image card"]["facts"][0]
    assert "effect at 13.97s" in by["P v1 - audio"]["facts"][0] and "from a prompt" in by["P v1 - audio"]["facts"][0]
    assert "3 clips, 1:14.2; lanes: Cuts, SFX; layers: 2" in by["P v1 - review"]["facts"][0]


def test_notes_written_by_claude_are_flagged_and_ryans_are_not(root):
    by = {e["folder"]: e for e in pj.scan(root, "P v1")}
    assert by["P v1 - notes (stand-in)"]["stand_in"] and "stand-in, NOT by Ryan" in by["P v1 - notes (stand-in)"]["facts"][0]
    assert not by["P v1 - ryan notes"]["stand_in"] and "(from Ryan)" in by["P v1 - ryan notes"]["facts"][0]


def test_links_are_relative_to_the_index_and_url_encoded_and_layered_xml_is_labelled(root):
    by = {e["folder"]: e for e in pj.scan(root, "P v1")}
    labels = {l["label"]: l["path"] for l in by["P v1 - layers"]["links"]}
    assert labels["XML (with layers)"] == "P v1 - layers/cut_v3.xml" and labels["Review page"] == "P v1 - layers/review/review.html"
    page = pj.render(pj.scan(root, "P v1"), "P v1", root)
    assert 'href="P%20v1%20-%20layers/cut_v3.xml"' in page


def test_the_newest_layered_cut_qa_and_page_are_picked_out(root):
    lt = pj.latest(pj.scan(root, "P v1"))
    assert lt["layered_cut"]["folder"] == "P v1 - layers" and lt["qa"]["folder"] == "P v1 - qa" and lt["review_page"]["folder"] in ("P v1 - layers", "P v1 - review")


def test_folder_names_cannot_inject_markup_into_the_page(tmp_path):
    r = tmp_path / "R"
    _w(r / "P <script>alert(1)</script>" / "notes.json", {"notes": []})
    page = pj.render(pj.scan(r, "P"), "P", r)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_the_tool_writes_two_files_moves_nothing_and_says_what_it_found(root):
    before = sorted(p.relative_to(root) for p in root.rglob("*"))
    p = subprocess.run([sys.executable, str(HERE / "project.py"), "--root", str(root), "--name", "P v1"], capture_output=True, text=True)
    assert p.returncode == 0 and "9 folders indexed in place" in p.stdout and "stand-in notes flagged in 1 folder(s)" in p.stdout
    after = sorted(p_.relative_to(root) for p_ in root.rglob("*"))
    assert [a for a in after if a not in before] == [Path("P v1 - project index.html"), Path("P v1 - project.json")] and all(b in after for b in before)
    data = json.loads((root / "P v1 - project.json").read_text())
    assert data["project"] == "P v1" and len(data["folders"]) == 9


def test_a_missing_root_and_a_name_with_no_folders_are_refused(tmp_path):
    p = subprocess.run([sys.executable, str(HERE / "project.py"), "--root", str(tmp_path / "nope"), "--name", "P"], capture_output=True, text=True)
    assert p.returncode == 1 and "is not a folder" in p.stderr
    (tmp_path / "R").mkdir()
    p = subprocess.run([sys.executable, str(HERE / "project.py"), "--root", str(tmp_path / "R"), "--name", "Nothing"], capture_output=True, text=True)
    assert p.returncode == 1 and "no folders starting with" in p.stderr
