"""Punch in on every other clip of a jump cut: XML in, XML out.

Ryan, 2026-10-08: "on each cut if its the same person talking from the same clip, then every other clip should be scaled up a bit with a crop reframe so its not just a bunch of jump cuts."

Two clips that follow each other on V1, from the same camera file, framed the same (the same point of the source in mid-frame, the same scale) are a jump cut. In each run of such clips the 2nd,
4th, ... are scaled up by PUNCH (a closer shot of the same person), with the horizontal position recomputed so the same point of the source stays in mid-frame (follow_speaker's rule, kept
inside the source). Vertically the top edge of the picture stays on the same row of the source, so the shot gets closer from the top down: scaling about the middle cut off the top of Mitch's
head on the Septic cut (his head already touches the top of the wide shot). That vertical move uses reframe_xml's vertical unit, which no Premiere export has confirmed yet (labs/reframe/reframe_xml.py):
the preview follows the same rule, so if Premiere shows the tight shot moved the wrong way, the rule is what is wrong. Running it again changes nothing: a punched clip no longer has its neighbour's scale, so it no longer forms a jump cut with it.

    python3 labs/review_loop/punch_in.py <cut.xml> --out <punched.xml> [--factor 1.15]
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import framing  # noqa: E402
import follow_speaker as fs  # noqa: E402
import timeline  # noqa: E402

PUNCH = 1.15                 # how much closer the punched-in clip is
SAME_X = 0.08                # two clips whose mid-frame points are this close (fraction of the source width) show the same person: each clip is centred on that moment's face, so one person drifts up to 4% from clip to clip (Septic cut), and two people stand about 40% apart
SAME_SCALE = 0.1             # percentage points


def _set_motion(el: ET.Element, scale: float, horiz: float, vert: float) -> None:
    for f in el.findall("filter"):
        if f.findtext("effect/name") == "Basic Motion":
            for prm in f.findall("effect/parameter"):
                if prm.findtext("parameterid") == "scale":
                    prm.find("value").text = f"{scale:.4f}".rstrip("0").rstrip(".")
                elif prm.findtext("parameterid") == "center":
                    prm.find("value/horiz").text = "0" if abs(horiz) < 1e-9 else f"{horiz:.6f}"
                    prm.find("value/vert").text = "0" if abs(vert) < 1e-9 else f"{vert:.6f}"


def plan(cut, dims_fn, factor: float = PUNCH) -> list[dict]:
    """Which V1 clips to punch in on, and how: [{"idx", "scale", "horiz", "vert", "was_scale", "x"}] (idx is the clip's 1-based place on V1)."""
    out_w, out_h = cut.width or 1080, getattr(cut, "height", 0) or 1920
    clips = [c for c in cut.video if c.motion]
    widest: dict[str, float] = {}
    for c in clips:
        widest[c.src_path] = min(widest.get(c.src_path, c.motion[0]), c.motion[0])
    rows, run_pos, prev = [], 0, None
    for c in clips:
        scale, horiz, vert = c.motion
        sw, sh = dims_fn(c.src_path)
        x = framing.x_for_horiz(horiz, scale, sw)
        jump = (prev is not None and prev[0].src_path == c.src_path and abs(prev[0].tl_end - c.tl_start) < 1.5 / cut.fps
                and abs(prev[1] - x) < SAME_X and abs(prev[0].motion[0] - scale) < SAME_SCALE)
        run_pos = run_pos + 1 if jump else 0
        if run_pos % 2 == 1 and scale <= widest[c.src_path] * 1.01:          # only a clip still at the widest framing: a revised cut that splits a punched clip is never punched closer again
            s2 = scale * factor
            s1, s2p = scale / 100.0, s2 / 100.0
            top = min(max(sh / 2 - vert * sh / s1 - out_h / (2 * s1), 0.0), max(sh - out_h / s1, 0.0))   # the source row at the top of the wide shot (render_preview's geometry)
            cy2 = min(max(top + out_h / (2 * s2p), out_h / (2 * s2p)), sh - out_h / (2 * s2p))        # the tight shot starts on the same row, kept inside the source
            rows.append({"idx": c.idx, "scale": round(s2, 4), "horiz": framing.horiz_for_person(x, s2, sw, out_w), "vert": round((sh / 2 - cy2) * s2p / sh, 6),
                         "was_scale": scale, "x": round(x, 4)})
        prev = (c, x)
    return rows


def punch_in(xml_in: Path, xml_out: Path, dims_fn=None, factor: float = PUNCH) -> list[dict]:
    """Writes xml_out with the punch-ins; returns what was punched (empty: nothing was a jump cut, and xml_out is a copy)."""
    if dims_fn is None:
        import render_preview
        dims_fn = render_preview.source_dims
    cut = timeline.load_cut(Path(xml_in))
    rows = plan(cut, dims_fn, factor)
    tree = ET.parse(xml_in)
    seq = tree.getroot().find(".//sequence")
    zone_f = round(cut.zone_end * cut.fps)
    items = sorted([c for c in seq.find("media/video/track").findall("clipitem") if c.findtext("in") is not None and int(c.findtext("start")) < zone_f],
                   key=lambda c: int(c.findtext("start")))
    for r in rows:
        el = items[r["idx"] - 1]
        _set_motion(el, r["scale"], r["horiz"], r["vert"])
    tree.write(xml_out, encoding="UTF-8", xml_declaration=True)
    return rows


def check(xml_in: Path, xml_out: Path, rows: list[dict]) -> list[tuple[str, bool, str]]:
    """Read back: only the punched clips changed, and only their scale and horizontal position; every clip keeps its footage and place."""
    a, b = timeline.load_cut(Path(xml_in)), timeline.load_cut(Path(xml_out))
    want = {r["idx"]: r for r in rows}
    same_cut = len(a.video) == len(b.video) and all(abs(p.tl_start - q.tl_start) < 1e-3 and abs(p.tl_end - q.tl_end) < 1e-3 and p.src_path == q.src_path
                                                    and abs(p.src_in - q.src_in) < 1e-3 for p, q in zip(a.video, b.video))
    bad = []
    for p, q in zip(a.video, b.video):
        if q.idx in want:
            if not q.motion or abs(q.motion[0] - want[q.idx]["scale"]) > 0.01 or abs(q.motion[2] - want[q.idx]["vert"]) > 1e-5:
                bad.append(f"clip {q.idx} is not at {want[q.idx]['scale']}%")
        elif p.motion != q.motion:
            bad.append(f"clip {q.idx} changed although it was not punched in")
    return [("PUNCH-SAME-CUT", same_cut, "every clip keeps its footage and its place on the timeline" if same_cut else "the clips changed, which a punch-in must never do"),
            ("PUNCH-WRITTEN", not bad, f"{len(rows)} clip(s) punched in, the rest untouched" if not bad else "; ".join(bad[:4]))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--factor", type=float, default=PUNCH)
    args = ap.parse_args()
    rows = punch_in(args.xml, args.out, factor=args.factor)
    for r in rows:
        print(f"clip {r['idx']}: {r['was_scale']:g}% -> {r['scale']:g}%")
    rows_ok = check(args.xml, args.out, rows)
    for n, ok, d in rows_ok:
        print(f"[{'PASS' if ok else 'FAIL'}] {n}  {d}")
    return 0 if all(ok for _n, ok, _d in rows_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
