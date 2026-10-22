"""Many finished sequences, one XML back to Premiere.

    python3 labs/review_loop/merge_sequences.py <a.xml> <b.xml> ... --out <all.xml>

Each finished sequence is its own self-contained xmeml (that is what `split_sequences` and Get Creative produce). Importing them one by one into Premiere makes one project import per cut; this writes ONE xmeml
that holds every one of them, in the order given, so the whole batch comes back in a single import.

Why it is not just "paste the sequences together": every file numbers its own things the same way (`sequence-10`, `file-1`, `masterclip-1`, `clipitem-3`). Put two together and ids collide, and a clip in the
second cut would point at a file from the first. So every id in each source is rewritten with a prefix that belongs to that source (`m1-`, `m2-`...), and so is every place an id is used as a reference:
the `id` attributes (including the bare `<file id="..."/>` references) and the text of `<masterclipid>` and `<linkclipref>`. Nothing is guessed: the output is read back and checked (`check`), and the sources are never touched.
"""
from __future__ import annotations

import argparse
import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
REF_TEXT_TAGS = ("masterclipid", "linkclipref")


class MergeError(Exception):
    """Could not merge; the message says why in words."""


def _load(path: Path) -> ET.Element:
    raw = Path(path).read_bytes().decode("utf-8")
    raw = raw.replace("<!DOCTYPE xmeml>", "", 1)
    if raw.lstrip().startswith("<?xml"):
        raw = raw.split("?>", 1)[-1]
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise MergeError(f"{Path(path).name} is not readable XML: {exc}") from exc


def _prefix_ids(root: ET.Element, prefix: str) -> None:
    """Every id, and every reference to one, gets this source's prefix (in place)."""
    for el in root.iter():
        if "id" in el.attrib:
            el.set("id", prefix + el.get("id"))
        if el.tag in REF_TEXT_TAGS and el.text and el.text.strip():
            el.text = prefix + el.text.strip()


def _children(root: ET.Element) -> ET.Element:
    ch = root.find("project/children")
    if ch is None:
        raise MergeError("this XML has no project with children (it is not a Premiere export)")
    return ch


def merge(sources: list[Path], out: Path, name: str | None = None) -> dict:
    """Write `out` holding every sequence of every source (never overwrites a source). Returns {"xml", "sequences": [names], "sources": n}."""
    sources = [Path(s) for s in sources]
    out = Path(out)
    if len(sources) < 1:
        raise MergeError("nothing to merge")
    if out.resolve() in {s.resolve() for s in sources}:
        raise MergeError("the output would overwrite one of the sources")
    roots = [_load(s) for s in sources]
    base = roots[0]
    _prefix_ids(base, "m1-")
    base_children = _children(base)
    for k, other in enumerate(roots[1:], start=2):
        _prefix_ids(other, f"m{k}-")
        for item in list(_children(other)):
            twin = next((b for b in base_children if b.tag == "bin" and item.tag == "bin" and (b.findtext("name") or "") == (item.findtext("name") or "")), None)
            if twin is not None:
                twin_children = twin.find("children")
                if twin_children is None:
                    twin_children = ET.SubElement(twin, "children")
                inner = item.find("children")
                for el in (list(inner) if inner is not None else []):
                    twin_children.append(copy.deepcopy(el))
            else:
                base_children.append(copy.deepcopy(item))
    if name:
        pn = base.find("project/name")
        if pn is not None:
            pn.text = name
    ET.indent(base, space="\t")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + ET.tostring(base, encoding="unicode") + "\n", encoding="utf-8")
    seqs = [s.findtext("name") or "" for s in base.iter("sequence") if s.find("media") is not None and not _inside_clip(base, s)]
    return {"xml": str(out), "sequences": seqs, "sources": len(sources)}


def _inside_clip(root: ET.Element, seq: ET.Element) -> bool:
    return any(seq is s for ci in root.iter("clipitem") for s in ci.iter("sequence"))


def check(path: Path, expect_sequences: int) -> list[tuple[str, bool, str]]:
    """Read the merged file back. Rows are (name, ok, detail); every one must be ok."""
    root = _load(path)
    rows: list[tuple[str, bool, str]] = []
    top = [s for s in root.iter("sequence") if s.find("media") is not None and not _inside_clip(root, s)]
    rows.append(("SEQUENCE-COUNT", len(top) == expect_sequences, f"{len(top)} found, {expect_sequences} expected"))
    names = [s.findtext("name") or "" for s in top]
    dup_names = sorted({n for n in names if names.count(n) > 1})
    rows.append(("SEQUENCE-NAMES-UNIQUE", not dup_names, ", ".join(dup_names) or "all different"))
    defined: dict[str, int] = {}
    for el in root.iter():
        if el.tag in ("sequence", "clipitem", "file", "masterclip", "clip") and "id" in el.attrib and len(el):
            defined[el.get("id")] = defined.get(el.get("id"), 0) + 1
    clashes = sorted(i for i, n in defined.items() if n > 1 and not i.startswith(("overlay-",)))
    rows.append(("IDS-DEFINED-ONCE", not clashes, f"{len(clashes)} defined twice: {', '.join(clashes[:4])}" if clashes else f"{len(defined)} ids, each defined once"))
    dangling = sorted({el.get("id") for el in root.iter("file") if "id" in el.attrib and not len(el) and el.get("id") not in defined})
    rows.append(("FILE-REFERENCES-RESOLVE", not dangling, f"{len(dangling)} point at nothing: {', '.join(dangling[:4])}" if dangling else "every file reference has its definition"))
    leaked = [el.get("id") for el in root.iter() if "id" in el.attrib and not el.get("id").startswith("m")]
    rows.append(("EVERY-ID-NAMESPACED", not leaked, f"{len(leaked)} ids without a source prefix" if leaked else "every id carries its source's prefix"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("xmls", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", default=None)
    a = ap.parse_args()
    try:
        res = merge([Path(x) for x in a.xmls], Path(a.out), a.name)
    except MergeError as exc:
        print(f"MERGE FAILED: {exc}")
        return 1
    rows = check(Path(res["xml"]), len(res["sequences"]))
    for n, ok, d in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n:<26} {d}")
    print(f"{len(res['sequences'])} sequences from {res['sources']} files -> {res['xml']}")
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
