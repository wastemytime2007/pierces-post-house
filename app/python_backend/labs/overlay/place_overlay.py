#!/usr/bin/env python3
"""Add a built overlay to an export XML as a clip on a new video track above the cut.

    python3 labs/overlay/place_overlay.py <export.xml> <overlay folder> --out <new.xml>

The overlay folder is make_overlay.py's output, rendered with --xml so it matches this
sequence's size and frame rate (one frame per frame, 100%, nothing to conform). It is placed
by the note's source frame, not by a remembered time, so it lands right on any later version
of the cut. Nothing else in the XML changes; the result is refused unless every check passes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import timeline  # noqa: E402
import verify_export  # noqa: E402
from posthouse.benchmark import _decode_pathurl  # noqa: E402

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


class PlaceError(Exception):
    pass


def probe_mov(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames,pix_fmt:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    j = json.loads(p.stdout)
    s = j["streams"][0]
    num, den = (int(x) for x in s["r_frame_rate"].split("/"))
    fps = num / den
    frames = int(s["nb_frames"]) if str(s.get("nb_frames", "")).isdigit() else round(float(j["format"]["duration"]) * fps)
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": fps, "frames": frames, "pix_fmt": s["pix_fmt"]}


def seq_spec(seq: ET.Element) -> dict:
    tb, ntsc = int(float(seq.findtext("rate/timebase"))), (seq.findtext("rate/ntsc") or "FALSE").strip().upper() == "TRUE"
    return {"width": int(seq.findtext("media/video/format/samplecharacteristics/width")),
            "height": int(seq.findtext("media/video/format/samplecharacteristics/height")),
            "timebase": tb, "ntsc": ntsc, "fps": tb * 1000 / 1001 if ntsc else float(tb)}


def anchor_time(cut: timeline.Cut, anchor: dict) -> float:
    """Timeline seconds of the frame the note was left on, found by its source position in THIS cut."""
    for c in cut.video:
        if c.name == anchor["source"] and c.src_in - 0.02 <= anchor["source_sec"] <= c.src_out + 0.02:
            return c.tl_start + (anchor["source_sec"] - c.src_in)
    raise PlaceError(f"the frame this overlay was made for ({anchor['source']} at {anchor['source_sec']}s) is not in this cut")


def E(parent: ET.Element, tag: str, text=None, **attrib) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib)
    if text is not None:
        el.text = str(text)
    return el


def place(xml_in: Path, xml_out: Path, folder: Path) -> dict:
    pl = json.loads((folder / "placement.json").read_text())
    mov = Path(pl["overlay_path"])
    if not mov.exists():
        raise PlaceError(f"the overlay file is missing: {mov}")
    root = ET.parse(xml_in).getroot()
    seq = timeline._seq_for_cut(root)
    sp, mv = seq_spec(seq), probe_mov(mov)
    if (mv["width"], mv["height"]) != (sp["width"], sp["height"]):
        raise PlaceError(f"the overlay is {mv['width']}x{mv['height']} but the sequence is {sp['width']}x{sp['height']}; "
                         "re-render it with make_overlay.py --xml <this export>")
    if abs(mv["fps"] - sp["fps"]) > 0.01:
        raise PlaceError(f"the overlay is {mv['fps']:.3f} fps but the sequence is {sp['fps']:.3f}; re-render it with --xml")
    if "a" not in mv["pix_fmt"].replace("yuv", ""):
        raise PlaceError("the overlay has no alpha channel")

    cut = timeline.load_cut(xml_in)
    anchor = pl.get("anchor")
    if anchor:                                       # a callout for one frame: follow that frame
        t_anchor = anchor_time(cut, anchor)
        start = round((t_anchor - anchor["lead_sec"]) * sp["fps"])
    else:                                            # a layer for a stretch of the cut (captions): the time it was made for
        t_anchor = None
        start = round(pl["place_overlay_on_timeline_at_sec"] * sp["fps"])
    n = mv["frames"]
    cut_frames = round(cut.zone_end * sp["fps"])
    if start + n > cut_frames and start + n - cut_frames <= 1:
        # the overlay is rendered to a whole number of frames, which can be one more than the cut's length (2026-10-09: a 1560-frame title on a 1559-frame cut refused the revision):
        # the final frame is blank by then, so the overlay is trimmed to the cut rather than refused
        n = cut_frames - start
    if start < 0 or start + n > cut_frames:
        raise PlaceError("the overlay would run outside the cut")

    ids = {el.get("id") for el in root.iter() if el.get("id")}
    k = 1
    while f"overlay-clipitem-{k}" in ids or f"overlay-file-{k}" in ids:
        k += 1
    video = seq.find("media/video")
    track = E(video, "track")
    ci = E(track, "clipitem", id=f"overlay-clipitem-{k}")
    E(ci, "name", mov.name)
    E(ci, "enabled", "TRUE")
    E(ci, "duration", n)
    r = E(ci, "rate"); E(r, "timebase", sp["timebase"]); E(r, "ntsc", "TRUE" if sp["ntsc"] else "FALSE")
    E(ci, "start", start); E(ci, "end", start + n); E(ci, "in", 0); E(ci, "out", n)
    E(ci, "alphatype", "straight")
    f = E(ci, "file", id=f"overlay-file-{k}")
    E(f, "name", mov.name)
    E(f, "pathurl", "file://localhost" + quote(str(mov.resolve()), safe="/"))
    fr = E(f, "rate"); E(fr, "timebase", sp["timebase"]); E(fr, "ntsc", "TRUE" if sp["ntsc"] else "FALSE")
    E(f, "duration", n)
    sc = E(E(E(f, "media"), "video"), "samplecharacteristics")
    scr = E(sc, "rate"); E(scr, "timebase", sp["timebase"]); E(scr, "ntsc", "TRUE" if sp["ntsc"] else "FALSE")
    E(sc, "width", sp["width"]); E(sc, "height", sp["height"]); E(sc, "anamorphic", "FALSE")
    E(sc, "pixelaspectratio", "square"); E(sc, "fielddominance", "none")
    E(track, "enabled", "TRUE"); E(track, "locked", "FALSE")

    ET.indent(root, space="\t")
    xml_out.write_text(HEADER + ET.tostring(root, encoding="unicode") + "\n")
    return {"start": start, "frames": n, "fps": sp["fps"], "anchor_sec": t_anchor, "lead": anchor["lead_sec"] if anchor else None, "mov": mov,
            "requested_sec": pl.get("place_overlay_on_timeline_at_sec")}


def _canon(e: ET.Element):
    return (e.tag, tuple(sorted(e.attrib.items())), (e.text or "").strip(), tuple(_canon(c) for c in e))


def _ser(els) -> list:
    """Structure with whitespace ignored, so re-indenting does not read as a change."""
    return [_canon(e) for e in els]


def verify_placed(xml_in: Path, xml_out: Path, info: dict, folder: Path) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    a, b = ET.parse(xml_in).getroot(), ET.parse(xml_out).getroot()
    sa, sb = timeline._seq_for_cut(a), timeline._seq_for_cut(b)
    ta, tb_ = sa.findall("media/video/track"), sb.findall("media/video/track")

    same = (_ser(ta) == _ser(tb_[:len(ta)]) and _ser(sa.findall("media/audio/track")) == _ser(sb.findall("media/audio/track"))
            and _ser(sa.findall("marker")) == _ser(sb.findall("marker")))
    rows.append(("REST-UNCHANGED", same, "every existing video track, audio track and marker is structurally identical to the input (whitespace ignored)" if same else "the existing timeline changed"))
    new = tb_[len(ta):]
    rows.append(("ONE-NEW-CLIP", len(new) == 1 and len(new[0].findall("clipitem")) == 1, f"{len(new)} new track(s), {sum(len(t.findall('clipitem')) for t in new)} clip(s)"))

    ci = new[0].find("clipitem")
    s, e, i, o = (int(ci.findtext(x)) for x in ("start", "end", "in", "out"))
    fdur = int(ci.find("file").findtext("duration"))
    rows.append(("FRAME-EXACT", (e - s) == (o - i) == fdur == info["frames"] and i == 0, f"start {s}, {e - s} frames on the timeline = {o - i} in the file = {fdur} in its definition"))

    cut2 = timeline.load_cut(xml_out)
    fps = info["fps"]
    anchor = json.loads((folder / "placement.json").read_text()).get("anchor")
    if anchor:
        t2 = anchor_time(cut2, anchor)
        lands = (s + round(info["lead"] * fps)) / fps
        rows.append(("ANCHOR-LINES-UP", abs(lands - t2) <= 1.5 / fps, f"the callout enters at {lands:.3f}s; the frame it was drawn on is at {t2:.3f}s in the output"))
    else:
        rows.append(("PLACED-AT-REQUESTED-TIME", abs(s / fps - info["requested_sec"]) <= 1.0 / fps,
                     f"starts at {s / fps:.3f}s; it was made for {info['requested_sec']:.3f}s"))
    rows.append(("INSIDE-THE-CUT", e <= round(cut2.zone_end * fps), f"ends at {e / fps:.2f}s; the cut ends at {cut2.zone_end:.2f}s"))
    path = _decode_pathurl(ci.find("file").findtext("pathurl"))[0]
    rows.append(("FILE-REACHABLE", Path(path) == info["mov"].resolve() and Path(path).exists(), path))
    import export_gate
    rows.append(export_gate.row(xml_out, xml_in))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    try:
        info = place(a.xml, a.out, a.folder)
    except (PlaceError, timeline.TimelineError, KeyError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    rows = verify_placed(a.xml, a.out, info, a.folder)
    bad = 0
    for name, ok, detail in rows:
        bad += ok is False
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if bad:
        print(f"\n{bad} check(s) FAILED. {a.out.name} is NOT verified, do not use it.")
        return 1
    print(f"\n{a.out}\noverlay on V{len(ET.parse(a.out).getroot().find('.//sequence').findall('media/video/track'))} "
          f"from {info['start'] / info['fps']:.2f}s for {info['frames'] / info['fps']:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
