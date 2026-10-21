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

PUNCH = 1.20                 # how much closer the punched-in clip is (Ryan, 2026-10-21: "20% punch in is good"; it was 1.15)
SOFT_MAX_SCALE = 115.0       # percent. Premiere's Scale 100 is one source pixel per output pixel; above it the picture is enlarged. Up to 115 is minimal softness: accepted, because a crop that looks noticeably different beats one so close to its neighbour that it still feels like a jump cut (Ryan, 2026-10-21). Above it the footage is degraded: refused.
MIN_PUNCH = 1.10             # a punch smaller than this (a resolution limit squeezed it) looks too much like the clip next to it, so it is skipped and reported instead of faked (sources: 10 to 30% is the working range)
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


def plan(cut, dims_fn, factor: float = PUNCH, skipped: list | None = None) -> list[dict]:
    """Which V1 clips to punch in on, and how: [{"idx", "scale", "horiz", "vert", "was_scale", "x"}] (idx is the clip's 1-based place on V1).

    The factor is judged against the source's resolution: the new scale is held to SOFT_MAX_SCALE, so a source with room (a RED original) gets the full factor and a 4K clip in a portrait cut gets less. A clip whose
    allowed factor falls under MIN_PUNCH is not punched; it is added to `skipped` ([{"idx", "why"}]) when a list is given."""
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
            new_scale = min(scale * factor, SOFT_MAX_SCALE)
            if new_scale / scale < MIN_PUNCH:
                if skipped is not None:
                    skipped.append({"idx": c.idx, "why": f"the source is {int(sw)}x{int(sh)}: a closer shot would enlarge it to {scale * factor:.0f}% (the most allowed is {SOFT_MAX_SCALE:.0f}%), and what is left would look too much like the clip before it"})
            else:
                rows.append({"idx": c.idx, **zoom_row(c, new_scale, dims_fn, out_w, out_h)})
        prev = (c, x)
    return rows


def zoom_row(c, new_scale: float, dims_fn, out_w: int, out_h: int) -> dict:
    """The framing of clip `c` at `new_scale` percent: the same person stays in mid-frame and the TOP of the picture stays on the same row of the source (so a closer shot never cuts off a head:
    scaling about the middle did, on the Septic cut). {"scale", "horiz", "vert", "was_scale", "x"}."""
    scale, horiz, vert = c.motion
    sw, sh = dims_fn(c.src_path)
    x = framing.x_for_horiz(horiz, scale, sw)
    s1, s2p = scale / 100.0, new_scale / 100.0
    top = min(max(sh / 2 - vert * sh / s1 - out_h / (2 * s1), 0.0), max(sh - out_h / s1, 0.0))     # the source row at the top of the shot as it is (render_preview's geometry)
    cy2 = min(max(top + out_h / (2 * s2p), out_h / (2 * s2p)), sh - out_h / (2 * s2p))             # the new shot starts on the same row, kept inside the source
    return {"scale": round(new_scale, 4), "horiz": framing.horiz_for_person(x, new_scale, sw, out_w), "vert": round((sh / 2 - cy2) * s2p / sh, 6), "was_scale": scale, "x": round(x, 4)}


def widest_scale(cut_w: int, cut_h: int, src_w: float, src_h: float) -> float:
    """The smallest scale (percent) at which the frame is still filled by the source: wider than this would show black."""
    return max(cut_w / src_w, cut_h / src_h) * 100.0


def punch_in(xml_in: Path, xml_out: Path, dims_fn=None, factor: float = PUNCH, skipped: list | None = None) -> list[dict]:
    """Writes xml_out with the punch-ins; returns what was punched (empty: nothing was a jump cut, and xml_out is a copy)."""
    if dims_fn is None:
        import render_preview
        dims_fn = render_preview.source_dims
    cut = timeline.load_cut(Path(xml_in))
    rows = plan(cut, dims_fn, factor, skipped)
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
    skipped: list = []
    rows = punch_in(args.xml, args.out, factor=args.factor, skipped=skipped)
    for s in skipped:
        print(f"clip {s['idx']}: not punched in, {s['why']}")
    for r in rows:
        print(f"clip {r['idx']}: {r['was_scale']:g}% -> {r['scale']:g}%")
    rows_ok = check(args.xml, args.out, rows)
    for n, ok, d in rows_ok:
        print(f"[{'PASS' if ok else 'FAIL'}] {n}  {d}")
    return 0 if all(ok for _n, ok, _d in rows_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
