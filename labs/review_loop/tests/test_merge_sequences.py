"""Many finished sequences come back to Premiere as one XML. Every source numbers its own ids the same way, so a naive paste would point a clip in one cut at a file from another."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import merge_sequences as ms  # noqa: E402


def _xml(name: str, media: str, with_second_use: bool = True) -> str:
    """One self-contained cut whose ids (sequence-1, file-1, masterclip-1, clipitem-1...) are the same in every file."""
    second = f'<clipitem id="clipitem-2"><name>b</name><start>100</start><end>200</end><in>0</in><out>100</out><masterclipid>masterclip-1</masterclipid><file id="file-1"/></clipitem>' if with_second_use else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4"><project><name>{name}</name><children>
<bin><name>Seq</name><children>
<sequence id="sequence-1"><name>{name}</name><duration>200</duration><rate><timebase>30</timebase></rate><media><video><track>
<clipitem id="clipitem-1"><name>a</name><start>0</start><end>100</end><in>0</in><out>100</out><masterclipid>masterclip-1</masterclipid>
<file id="file-1"><name>{media}</name><pathurl>file://localhost/Volumes/Shoot/{media}</pathurl></file>
<link><linkclipref>clipitem-2</linkclipref></link></clipitem>{second}
</track></video></media></sequence>
</children></bin>
<bin><name>Footage</name><children><clip id="masterclip-1"><name>{media}</name><file id="file-1"/></clip></children></bin>
</children></project></xmeml>"""


def _write(tmp_path, name, media, **kw):
    p = tmp_path / f"{name}.xml"
    p.write_text(_xml(name, media, **kw))
    return p


def test_two_files_with_the_same_ids_merge_without_crossing_wires(tmp_path):
    a = _write(tmp_path, "Story A", "A001.mov")
    b = _write(tmp_path, "Story B", "B777.mov")
    res = ms.merge([a, b], tmp_path / "all.xml", "Both")
    assert res["sequences"] == ["Story A", "Story B"]
    assert all(ok for _n, ok, _d in ms.check(Path(res["xml"]), 2)), ms.check(Path(res["xml"]), 2)
    root = ms._load(Path(res["xml"]))
    files = {f.get("id"): f.findtext("pathurl") for f in root.iter("file") if f.find("pathurl") is not None}
    seq_files = {}
    for s in root.iter("sequence"):
        if s.find("media") is None:
            continue
        refs = {files[c.find("file").get("id")] for c in s.iter("clipitem")}
        seq_files[s.findtext("name")] = refs
    assert seq_files == {"Story A": {"file://localhost/Volumes/Shoot/A001.mov"}, "Story B": {"file://localhost/Volumes/Shoot/B777.mov"}}


def test_text_references_are_namespaced_too(tmp_path):
    a = _write(tmp_path, "Story A", "A001.mov")
    b = _write(tmp_path, "Story B", "B777.mov")
    res = ms.merge([a, b], tmp_path / "all.xml")
    root = ms._load(Path(res["xml"]))
    masters = {m.text for m in root.iter("masterclipid")}
    assert masters == {"m1-masterclip-1", "m2-masterclip-1"}
    links = {m.text for m in root.iter("linkclipref")}
    assert links == {"m1-clipitem-2", "m2-clipitem-2"}


def test_same_named_bins_are_combined_not_duplicated(tmp_path):
    a = _write(tmp_path, "Story A", "A001.mov")
    b = _write(tmp_path, "Story B", "B777.mov")
    root = ms._load(Path(ms.merge([a, b], tmp_path / "all.xml")["xml"]))
    bins = [x.findtext("name") for x in root.find("project/children") if x.tag == "bin"]
    assert bins == ["Seq", "Footage"]
    assert len([s for s in root.find("project/children/bin/children") if s.tag == "sequence"]) == 2


def test_sources_are_never_changed_or_overwritten(tmp_path):
    a = _write(tmp_path, "Story A", "A001.mov")
    before = a.read_text()
    ms.merge([a], tmp_path / "one.xml")
    assert a.read_text() == before
    with pytest.raises(ms.MergeError):
        ms.merge([a], a)


def test_one_source_is_a_valid_merge(tmp_path):
    a = _write(tmp_path, "Story A", "A001.mov")
    res = ms.merge([a], tmp_path / "one.xml")
    assert res["sequences"] == ["Story A"] and all(ok for _n, ok, _d in ms.check(Path(res["xml"]), 1))


def test_a_file_that_is_not_an_export_says_so(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_text("<xmeml><sequence/></xmeml>")
    with pytest.raises(ms.MergeError):
        ms.merge([bad], tmp_path / "out.xml")
    nope = tmp_path / "nope.xml"
    nope.write_text("not xml at all")
    with pytest.raises(ms.MergeError):
        ms.merge([nope], tmp_path / "out.xml")


def test_check_catches_a_collision(tmp_path):
    """The checker is not a rubber stamp: two sources merged WITHOUT namespacing would be flagged."""
    a = _write(tmp_path, "Story A", "A001.mov")
    b = _write(tmp_path, "Story B", "B777.mov")
    ra, rb = ms._load(a), ms._load(b)
    ra.find("project/children").extend(list(rb.find("project/children")))              # a naive paste
    naive = tmp_path / "naive.xml"
    import xml.etree.ElementTree as ET
    naive.write_text(ms.HEADER + ET.tostring(ra, encoding="unicode"))
    rows = {n: ok for n, ok, _d in ms.check(naive, 2)}
    assert rows["IDS-DEFINED-ONCE"] is False and rows["EVERY-ID-NAMESPACED"] is False
