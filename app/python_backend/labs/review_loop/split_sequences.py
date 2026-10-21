"""Open one XML that holds many sequences (Ryan's `SEQS` bin exported from Premiere in one go) and work on them without exporting each sequence as its own XML.

    python3 labs/review_loop/split_sequences.py <many.xml>                     # list the sequences
    python3 labs/review_loop/split_sequences.py <many.xml> --out <folder>      # write one XML per sequence

Why it is not just "delete the other sequences": an xmeml file defines each <file> (its path, size, rate, audio channels) ONCE, in the first clip that uses it, and every later clip carries only `<file id="..."/>`.
In Ryan's real export (`runnells_days.xml`, 185 sequences) there are 14,491 <file> elements and only 2,888 of them say where the footage is. Likewise a sequence used inside another (a nested sequence) is
defined once and referenced after. So a sequence cut out on its own must have every file and every nested sequence it uses written out in full inside it, or the importer finds a reference to nothing.
That is what `extract` does. The other sequences, the footage bins and every clip the chosen sequence does not use are left behind, so each piece is small (one cut, not a 46 MB project).

A sequence is listed when it is a top-level sequence (not one used inside a clip), has at least one video clip, and is not one of the helper timelines ("All Synced ..."). The listing says which bin it sat in
(Ryan's `SEQS/Shoot Days`), how many clips and how long. Nothing is judged or changed in the picture.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


class SplitError(Exception):
    """Could not split; the message says why in words."""


def _load(path: Path) -> ET.Element:
    raw = Path(path).read_bytes().decode("utf-8")
    return ET.fromstring(raw.replace("<!DOCTYPE xmeml>", "", 1).split("?>", 1)[-1] if raw.lstrip().startswith("<?xml") else raw.replace("<!DOCTYPE xmeml>", "", 1))


def _parents(root: ET.Element) -> dict:
    return {c: p for p in root.iter() for c in p}


def _nested_ids(root: ET.Element) -> set:
    """Python ids of the <sequence> elements that sit inside a clip (a nested sequence): those are used, not listed."""
    return {id(s) for ci in root.iter("clipitem") for s in ci.iter("sequence")}


def _bin_path(el: ET.Element, parents: dict) -> str:
    out, n = [], el
    while n in parents:
        n = parents[n]
        if n.tag == "bin":
            out.append((n.findtext("name") or "").strip())
    return "/".join(reversed(out))


def _fps(seq: ET.Element) -> float:
    r = seq.find("rate")
    if r is None:
        return 30.0
    tb = int(r.findtext("timebase") or 30)
    return tb * 1000 / 1001 if (r.findtext("ntsc") or "").upper() == "TRUE" else float(tb)


def _clip_count(seq: ET.Element) -> int:
    return sum(len(t.findall("clipitem")) for t in seq.findall("media/video/track"))


def is_helper(name: str) -> bool:
    return name.strip().lower().startswith("all synced")


def list_sequences(root: ET.Element) -> list[dict]:
    parents = _parents(root)
    nested = _nested_ids(root)
    rows = []
    for s in root.iter("sequence"):
        if id(s) in nested or s.find("media") is None:
            continue
        name = (s.findtext("name") or "").strip()
        n = _clip_count(s)
        if not name or n == 0 or is_helper(name):
            continue
        fps = _fps(s)
        dur = int(s.findtext("duration") or 0)
        rows.append({"id": s.get("id"), "name": name, "bin": _bin_path(s, parents), "clips": n, "seconds": round(dur / fps, 1), "fps": round(fps, 3)})
    return rows


def _defs(root: ET.Element) -> tuple[dict, dict]:
    """The full definition of every file (the one with a path) and of every sequence (the one with media), by id."""
    files: dict[str, ET.Element] = {}
    for f in root.iter("file"):
        fid = f.get("id")
        if fid and fid not in files and f.find("pathurl") is not None:
            files[fid] = f
    seqs: dict[str, ET.Element] = {}
    for s in root.iter("sequence"):
        sid = s.get("id")
        if sid and sid not in seqs and s.find("media") is not None:
            seqs[sid] = s
    return files, seqs


def _inline_nested(seq: ET.Element, seq_defs: dict, depth: int = 0) -> None:
    """Every sequence used inside a clip of `seq` is written out in full (once, however many clips use it)."""
    if depth > 12:
        raise SplitError("sequences nested more than twelve deep: not a real project")
    done: set[str] = set()
    for ci in list(seq.iter("clipitem")):
        for ns in list(ci.findall("sequence")):
            sid = ns.get("id")
            if ns.find("media") is not None:
                _inline_nested(ns, seq_defs, depth + 1)
                done.add(sid)
                continue
            full = seq_defs.get(sid)
            if full is None:
                raise SplitError(f"a clip uses sequence {sid!r} but the XML never defines it")
            if sid in done:
                continue                                             # a later use is a reference again
            new = copy.deepcopy(full)
            ns.clear()
            ns.attrib.update(new.attrib)
            ns.text, ns.tail = new.text, ns.tail
            for ch in list(new):
                ns.append(ch)
            _inline_nested(ns, seq_defs, depth + 1)
            done.add(sid)


def _inline_files(seq: ET.Element, file_defs: dict) -> list[str]:
    """The first <file> of each id in the sequence is written in full; returns the ids it could not find a definition for."""
    seen: set[str] = set()
    missing: list[str] = []
    for f in seq.iter("file"):
        fid = f.get("id")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        if f.find("pathurl") is not None:
            continue
        full = file_defs.get(fid)
        if full is None:
            missing.append(fid)
            continue
        new = copy.deepcopy(full)
        for ch in list(new):
            f.append(ch)
    return missing


def extract(root: ET.Element, seq_id: str, project_name: str | None = None) -> ET.Element:
    """A new <xmeml> that holds only sequence `seq_id`, with every file and nested sequence it uses defined inside it."""
    file_defs, seq_defs = _defs(root)
    src = seq_defs.get(seq_id)
    if src is None:
        raise SplitError(f"no sequence with id {seq_id!r} in this XML")
    seq = copy.deepcopy(src)
    _inline_nested(seq, seq_defs)
    missing = _inline_files(seq, file_defs)
    if missing:
        raise SplitError(f"{len(missing)} file(s) used by {(seq.findtext('name') or seq_id)!r} are never defined in the XML (first: {missing[0]})")
    out = ET.Element("xmeml", {"version": root.get("version", "4")})
    project = ET.SubElement(out, "project")
    ET.SubElement(project, "name").text = project_name or (root.findtext("project/name") or "")
    children = ET.SubElement(project, "children")
    b = ET.SubElement(children, "bin")
    ET.SubElement(b, "name").text = "Seq"
    bc = ET.SubElement(b, "children")
    bc.append(seq)
    return out


def safe_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^\w .,'()&+-]+", "_", name).strip(" ._")[:70] or "sequence"
    out, n = base, 2
    while out.lower() in taken:
        out = f"{base} ({n})"
        n += 1
    taken.add(out.lower())
    return out


def write_xml(el: ET.Element, path: Path) -> None:
    ET.indent(el, space="\t")
    Path(path).write_text(HEADER + ET.tostring(el, encoding="unicode") + "\n", encoding="utf-8")


def split_all(xml: Path, out_dir: Path, only: list[str] | None = None, root: ET.Element | None = None) -> list[dict]:
    """One XML per listed sequence in `out_dir`; returns [{"id", "name", "xml", ...}]. A second run overwrites the same files."""
    root = root if root is not None else _load(xml)
    rows = list_sequences(root)
    if only is not None:
        rows = [r for r in rows if r["id"] in only]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    done = []
    for r in rows:
        path = out_dir / f"{safe_name(r['name'], taken)}.xml"
        write_xml(extract(root, r["id"], project_name=r["name"]), path)
        done.append({**r, "xml": str(path)})
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    try:
        root = _load(a.xml)
        rows = split_all(a.xml, a.out, root=root) if a.out else list_sequences(root)
    except (SplitError, ET.ParseError, OSError) as e:
        print(f"REFUSING: {e}")
        return 1
    for r in rows:
        print(f"{r['clips']:>4} clips {r['seconds']:>7.1f}s  [{r['bin']}]  {r['name']}" + (f"  ->  {r['xml']}" if "xml" in r else ""))
    print(f"{len(rows)} sequence(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
