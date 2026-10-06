"""Make an exporter XML vertical by reframing the ORIGINAL clips in Premiere's own terms: standalone, XML in and XML out, nothing in app/.

    python3 labs/reframe/reframe_xml.py <export.xml> <plan.json> --out <vertical.xml> [--width 1080 --height 1920]

Why this and not baked crops: the clips stay the camera originals (handles intact, re-framable in Premiere); only a Basic Motion scale and position is added to each picture clip, in the exact structure Premiere writes
(`safety_net/fixtures/premiere_motion/`, an export Ryan made on 2026-10-06 with its Effect Controls readout). The sequence is set to the vertical size.

The rule, from that export (Position 1530.0, 960.0, Scale 118, Anchor 1920, 1080 gave `center.horiz` 0.2578125): `horiz = (Position.x - sequence_width / 2) / source_width`, positive to the right, and Position is where the
clip's own centre lands. To put source pixel `subject_x` in the middle of the vertical frame at scale `s`: `Position.x - sequence_width / 2 = (source_width / 2 - subject_x) * s`, so `horiz = (source_width / 2 - subject_x) * s / source_width`.
`center.vert` is always 0 here: its normaliser has not been confirmed (no vertical move has been exported), and a scale of at least `sequence_height / source_height` (88.9% for 2160-high video into 1920) already fills the
frame top to bottom with the clip centred.

plan.json: {"source_width": 3840, "source_height": 2160,
            "pieces": [{"start": <timeline frame>, "end": <timeline frame>, "person": "Mitch", "subject_x": 2554, "scale": 88.89}, ...],
            "mics": {"wknd_Mitch4.WAV": "Mitch", "wknd_Bob3.WAV": "Bob"}}        (mics is optional: with it only the speaker's microphone is left enabled in each piece)
A piece's start and end are the picture clip's start and end on the timeline.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DEFAULT_W, DEFAULT_H = 1080, 1920

# Premiere's Basic Motion block, as it wrote it (structure and parameter order unchanged); only `scale` and `center.horiz` are filled in.
MOTION_TEMPLATE = """<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype><pproBypass>false</pproBypass>
<parameter authoringApp="PremierePro"><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale}</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>rotation</parameterid><name>Rotation</name><valuemin>-8640</valuemin><valuemax>8640</valuemax><value>0</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>center</parameterid><name>Center</name><value><horiz>{horiz}</horiz><vert>0</vert></value></parameter>
<parameter authoringApp="PremierePro"><parameterid>centerOffset</parameterid><name>Anchor Point</name><value><horiz>0</horiz><vert>0</vert></value></parameter>
<parameter authoringApp="PremierePro"><parameterid>antiflicker</parameterid><name>Anti-flicker Filter</name><valuemin>0.0</valuemin><valuemax>1.0</valuemax><value>0</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>leftcrop</parameterid><name>Left</name><valuemin>0.0</valuemin><valuemax>100.0</valuemax><value>0</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>topcrop</parameterid><name>Top</name><valuemin>0.0</valuemin><valuemax>100.0</valuemax><value>0</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>rightcrop</parameterid><name>Right</name><valuemin>0.0</valuemin><valuemax>100.0</valuemax><value>0</value></parameter>
<parameter authoringApp="PremierePro"><parameterid>bottomcrop</parameterid><name>Bottom</name><valuemin>0.0</valuemin><valuemax>100.0</valuemax><value>0</value></parameter>
</effect></filter>"""


class ReframeError(Exception):
    pass


def horiz_for_subject(subject_x: float, scale_pct: float, source_w: float = 3840) -> float:
    """`center.horiz` that puts source pixel `subject_x` in the middle of the frame at `scale_pct` percent."""
    return (source_w / 2 - subject_x) * (scale_pct / 100.0) / source_w


def subject_for_horiz(horiz: float, scale_pct: float, source_w: float = 3840) -> float:
    """The inverse: which source pixel a `center.horiz` puts in the middle of the frame."""
    return source_w / 2 - horiz * source_w / (scale_pct / 100.0)


def window_inside(subject_x: float, scale_pct: float, source_w: float, source_h: float, out_w: int, out_h: int) -> tuple[bool, str]:
    """The frame is a window onto the clip: out_w/s wide, out_h/s high, centred on (subject_x, source_h / 2). It must lie inside the clip, or the edge of the picture shows."""
    s = scale_pct / 100.0
    half_w, half_h = out_w / s / 2, out_h / s / 2
    ok = subject_x - half_w >= -0.5 and subject_x + half_w <= source_w + 0.5 and source_h / 2 - half_h >= -0.5
    return ok, f"window x {subject_x - half_w:.0f} to {subject_x + half_w:.0f} of {source_w:.0f}, height {2 * half_h:.0f} of {source_h:.0f}"


def motion_filter(scale_pct: float, horiz: float) -> ET.Element:
    return ET.fromstring(MOTION_TEMPLATE.format(scale=f"{scale_pct:.4f}".rstrip("0").rstrip("."), horiz=f"{horiz:.6f}"))


def _seq(root: ET.Element) -> ET.Element:
    seqs = [s for s in root.iter("sequence") if s.find("media/video/track") is not None and s.find("media/audio") is not None]
    if not seqs:
        raise ReframeError("no sequence with picture and audio tracks in this XML")
    return seqs[0]


def reframe(xml_in: Path, xml_out: Path, plan: dict, out_w: int = DEFAULT_W, out_h: int = DEFAULT_H) -> dict:
    """Write `xml_out`: the sequence set to out_w x out_h, a Basic Motion on every picture clip of the cut, and (when plan has `mics`) only the speaker's microphone enabled in each piece."""
    sw, sh = float(plan["source_width"]), float(plan["source_height"])
    tree = ET.parse(xml_in)
    root = tree.getroot()
    seq = _seq(root)
    rows = {(int(p["start"]), int(p["end"])): p for p in plan["pieces"]}
    problems = []
    for p in plan["pieces"]:
        ok, why = window_inside(p["subject_x"], p["scale"], sw, sh, out_w, out_h)
        if not ok:
            problems.append(f"piece at frame {p['start']} ({p['person']}): the frame would show beyond the clip ({why})")
    if problems:
        raise ReframeError("; ".join(problems))
    sc = seq.find("media/video/format/samplecharacteristics")
    sc.find("width").text, sc.find("height").text = str(out_w), str(out_h)
    framed, seen = 0, set()
    for ci in seq.find("media/video/track").findall("clipitem"):
        key = (int(ci.findtext("start")), int(ci.findtext("end")))
        if key not in rows:
            raise ReframeError(f"the picture clip at frames {key} has no row in the plan")
        p = rows[key]
        for old in ci.findall("filter"):
            if old.findtext("effect/name") == "Basic Motion":
                ci.remove(old)
        f = motion_filter(p["scale"], horiz_for_subject(p["subject_x"], p["scale"], sw))
        ci.insert(list(ci).index(ci.find("file")) + 1, f)                       # straight after <file>, where Premiere writes it
        framed += 1
        seen.add(key)
    if seen != set(rows):
        raise ReframeError(f"plan rows without a picture clip: {sorted(set(rows) - seen)[:3]}")
    muted = 0
    mics = plan.get("mics")
    if mics:
        by_start = {int(p["start"]): p for p in plan["pieces"]}
        for track in seq.findall("media/audio/track"):
            for ci in track.findall("clipitem"):
                person = mics.get(ci.findtext("name"))
                if person is None:
                    continue
                row = by_start.get(int(ci.findtext("start")))
                if row is not None and person != row["person"]:
                    ci.find("enabled").text = "FALSE"
                    muted += 1
    ET.indent(root, space="\t")
    xml_out.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")
    return {"framed": framed, "mic_clips_muted": muted, "width": out_w, "height": out_h}


def _canon(e: ET.Element, skip: frozenset | set = frozenset()):
    return (e.tag, tuple(sorted(e.attrib.items())), (e.text or "").strip(), tuple(_canon(c, skip) for c in e if c.tag not in skip))


def verify(xml_in: Path, xml_out: Path, plan: dict, out_w: int = DEFAULT_W, out_h: int = DEFAULT_H) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    a, b = _seq(ET.parse(xml_in).getroot()), _seq(ET.parse(xml_out).getroot())
    sw, sh = float(plan["source_width"]), float(plan["source_height"])
    w = b.findtext("media/video/format/samplecharacteristics/width")
    h = b.findtext("media/video/format/samplecharacteristics/height")
    rows.append(("VERTICAL-SEQUENCE", (w, h) == (str(out_w), str(out_h)), f"sequence is {w}x{h}"))
    by = {(int(p["start"]), int(p["end"])): p for p in plan["pieces"]}
    bad, n = [], 0
    for ci in b.find("media/video/track").findall("clipitem"):
        f = [x for x in ci.findall("filter") if x.findtext("effect/name") == "Basic Motion"]
        p = by.get((int(ci.findtext("start")), int(ci.findtext("end"))))
        if len(f) != 1 or p is None:
            bad.append(ci.findtext("name"))
            continue
        vals = {x.findtext("parameterid"): x for x in f[0].findall("effect/parameter")}
        scale = float(vals["scale"].findtext("value"))
        horiz, vert = float(vals["center"].findtext("value/horiz")), float(vals["center"].findtext("value/vert"))
        x_back = subject_for_horiz(horiz, scale, sw)
        if abs(scale - p["scale"]) > 0.01 or abs(x_back - p["subject_x"]) > 2 or vert != 0:
            bad.append(f"{ci.findtext('name')} (scale {scale}, centres source x {x_back:.0f}, wanted {p['scale']} and {p['subject_x']})")
        n += 1
    rows.append(("EVERY-CLIP-FRAMED", not bad and n == len(by), f"{n} picture clips each carry one Basic Motion that puts the intended pixel mid-frame (decoded back from the file, within 2 px)" if not bad else f"wrong: {bad[:3]}"))
    inside = [(p["start"], window_inside(p["subject_x"], p["scale"], sw, sh, out_w, out_h)) for p in plan["pieces"]]
    rows.append(("FRAME-INSIDE-THE-CLIP", all(ok for _s, (ok, _w) in inside), "no piece shows beyond the edge of its clip" if all(ok for _s, (ok, _w) in inside) else f"frames show beyond the clip: {[s for s, (ok, _w) in inside if not ok][:3]}"))
    pic_same = _canon(a.find("media/video/track"), {"filter"}) == _canon(b.find("media/video/track"), {"filter"})
    aud_same = [_canon(t, {"enabled"}) for t in a.findall("media/audio/track")] == [_canon(t, {"enabled"}) for t in b.findall("media/audio/track")]
    rows.append(("REST-UNCHANGED", pic_same and aud_same, "every picture clip's start, end, in, out and file, and every audio clip's, are as exported (only the motion was added and microphones switched off)" if pic_same and aud_same
                 else f"something other than the motion and the microphone switches changed (picture {'same' if pic_same else 'DIFFERENT'}, audio {'same' if aud_same else 'DIFFERENT'})"))
    if plan.get("mics"):
        wrong = []
        by_start = {int(p["start"]): p for p in plan["pieces"]}
        for t in b.findall("media/audio/track"):
            for ci in t.findall("clipitem"):
                person = plan["mics"].get(ci.findtext("name"))
                row = by_start.get(int(ci.findtext("start")))
                if person and row and ((person == row["person"]) != (ci.findtext("enabled") == "TRUE")):
                    wrong.append((ci.findtext("name"), row["start"]))
        rows.append(("ONLY-THE-SPEAKER-LIVE", not wrong, "in every piece only the person talking has their microphone enabled" if not wrong else f"wrong: {wrong[:3]}"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("plan", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    try:
        info = reframe(a.xml, a.out, plan, a.width, a.height)
    except ReframeError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    rows = verify(a.xml, a.out, plan, a.width, a.height)
    sys.path.insert(0, str(REPO / "safety_net"))
    r = subprocess.run([sys.executable, str(REPO / "safety_net/verify_export.py"), str(a.out)], capture_output=True, text=True)
    rows.append(("verify_export", r.returncode == 0, "all applicable checks pass" if r.returncode == 0 else (r.stdout + r.stderr)[-300:]))
    ok = True
    print(f"{info['framed']} picture clips framed, {info['mic_clips_muted']} microphone clips muted -> {a.out}")
    for name, good, why in rows:
        print(f"  [{'PASS' if good else 'FAIL'}] {name:24} {why}")
        ok = ok and bool(good)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
