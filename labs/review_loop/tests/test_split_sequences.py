"""split_sequences: one XML with many sequences in, one self-contained XML per sequence out (Ryan, 2026-10-21: no exporting each sequence from Premiere on its own)."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import split_sequences as ss  # noqa: E402

RATE = "<rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>"


def _file(fid, full):
    return (f'<file id="{fid}"><name>{fid}.mp4</name><pathurl>file://localhost/media/{fid}.mp4</pathurl>{RATE}<duration>3000</duration></file>' if full else f'<file id="{fid}"/>')


def _clip(cid, fid, start, end, full):
    return f'<clipitem id="{cid}"><name>{cid}</name><start>{start}</start><end>{end}</end><in>0</in><out>{end - start}</out>{_file(fid, full)}</clipitem>'


def _seq(sid, name, clips, extra_media=""):
    return (f'<sequence id="{sid}"><name>{name}</name><duration>600</duration>{RATE}<media><video><track>{"".join(clips)}</track></video>{extra_media}</media></sequence>')


def _doc(*bins):
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><project><name>days</name><children>' + "".join(bins) + "</children></project></xmeml>"


def _bin(name, *children):
    return f"<bin><name>{name}</name><children>{''.join(children)}</children></bin>"


@pytest.fixture()
def many(tmp_path):
    nested_full = _seq("seq-n", "Nested Sequence 01", [_clip("n1", "f9", 0, 100, True)])
    nested_stub = '<sequence id="seq-n"/>'
    a = _seq("seq-a", "Day 1", [_clip("a1", "f1", 0, 100, True), _clip("a2", "f2", 100, 200, True), f'<clipitem id="a3"><name>nest</name><start>200</start><end>300</end><in>0</in><out>100</out>{nested_full}</clipitem>'])
    b = _seq("seq-b", "Day 2", [_clip("b1", "f1", 0, 100, False), _clip("b2", "f3", 100, 200, True), f'<clipitem id="b3"><name>nest</name><start>200</start><end>300</end><in>0</in><out>100</out>{nested_stub}</clipitem>'])
    helper = _seq("seq-h", "All Synced A-Roll", [_clip("h1", "f1", 0, 100, False)])
    empty = _seq("seq-e", "Empty", [])
    p = tmp_path / "days.xml"
    p.write_text(_doc(_bin("SEQS", _bin("Shoot Days", a, b), helper, empty)))
    return p


def test_lists_real_cuts_only_with_their_bin_not_helpers_empties_or_nested(many):
    rows = ss.list_sequences(ss._load(many))
    assert [(r["name"], r["bin"], r["clips"]) for r in rows] == [("Day 1", "SEQS/Shoot Days", 3), ("Day 2", "SEQS/Shoot Days", 3)]
    assert rows[0]["seconds"] == 20.0 and rows[0]["fps"] == 30.0


def test_a_sequence_whose_files_were_defined_in_another_sequence_comes_out_complete(many):
    """Day 2 uses file f1 by reference only: its definition lives in Day 1's first clip. Cut out on its own it must carry it."""
    root = ss._load(many)
    out = ss.extract(root, "seq-b")
    defined = {f.get("id") for f in out.iter("file") if f.find("pathurl") is not None}
    used = {f.get("id") for f in out.iter("file")}
    assert used <= defined and {"f1", "f3", "f9"} <= used, f"every file the sequence uses is defined inside it: used {used}, defined {defined}"
    assert [s.findtext("name") for s in out.iter("sequence")] == ["Day 2", "Nested Sequence 01"], "the nested sequence it uses is written out in full, and Day 1 is left behind"
    assert out.find(".//sequence[@id='seq-n']").find("media") is not None


def test_the_source_is_untouched_and_the_output_loads_again(many, tmp_path):
    before = many.read_bytes()
    done = ss.split_all(many, tmp_path / "out")
    assert many.read_bytes() == before
    assert sorted(Path(d["xml"]).name for d in done) == ["Day 1.xml", "Day 2.xml"]
    again = ss.list_sequences(ss._load(Path(done[1]["xml"])))
    assert [r["name"] for r in again] == ["Day 2"], "each piece lists as one sequence"
    text = Path(done[1]["xml"]).read_text()
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')


def test_an_undefined_file_or_sequence_is_refused_in_words_not_written_broken(tmp_path):
    p = tmp_path / "broken.xml"
    p.write_text(_doc(_bin("Seqs", _seq("seq-x", "X", [_clip("x1", "f404", 0, 100, False)]))))
    with pytest.raises(ss.SplitError, match="never defined"):
        ss.extract(ss._load(p), "seq-x")
    with pytest.raises(ss.SplitError, match="no sequence with id"):
        ss.extract(ss._load(p), "nope")


def test_same_named_sequences_get_different_files_and_odd_characters_are_made_safe():
    taken: set[str] = set()
    assert ss.safe_name("12. Tiling the kitchen:bathroom", taken) == "12. Tiling the kitchen_bathroom"
    assert ss.safe_name("Day", taken) == "Day" and ss.safe_name("day", taken) == "day (2)"


def test_the_apps_two_commands_list_then_extract_beside_the_source(many, monkeypatch):
    """What the Review tab's picker calls: list_sequences, then extract_sequences into '<name> - seqs/'."""
    sys.path.insert(0, str(HERE.parent.parent / "app" / "python_backend"))
    import creator_tools as ct
    rows = ct.list_sequences(str(many))["sequences"]
    assert [r["name"] for r in rows] == ["Day 1", "Day 2"]
    res = ct.extract_sequences(str(many), [rows[1]["id"]])
    assert Path(res["folder"]).name == "days - seqs" and Path(res["folder"]).parent == many.parent
    assert [Path(s["xml"]).name for s in res["sequences"]] == ["Day 2.xml"] and Path(res["sequences"][0]["xml"]).is_file()
    with pytest.raises(ct.ToolError):
        ct.extract_sequences(str(many), ["nope"])
    with pytest.raises(ct.ToolError):
        ct.list_sequences(str(many.parent / "missing.xml"))
