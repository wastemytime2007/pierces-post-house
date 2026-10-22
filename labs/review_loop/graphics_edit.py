"""Move and scale the on-screen graphics: XML in, XML out (Ryan, 2026-10-21: "a graphic that was created doesn't sit in the right place ... being able to scale it down or move it to a different position without having to say it in the chat").

Every graphic the tools placed (the overlay, the title, each name tag, the captions, a callout) is its own clip on its own track, a full-frame layer the size of the sequence. Moving or scaling one is Premiere's
own Basic Motion on that clip, in the exact structure Premiere writes (labs/reframe/reframe_xml.py), so it can be adjusted or undone in Premiere's Effect Controls too, and nothing is re-rendered.

    describe(xml, out_dir)   what each graphic is, where it sits in the frame (measured from its own pixels, not from layout arithmetic) and how it is moved now
    apply(xml_in, xml_out, edits)   the edits written as Basic Motion; edits are {"id", "dx", "dy", "scale"}: dx and dy as fractions of the frame (right and down are positive), scale in percent (100 = as made)
    check(...)  read back: only the edited clips changed, and each says what it was asked to

The maths, from Ryan's Premiere export (Position 1530, 960, Scale 118 gave center.horiz 0.2578): Premiere scales a clip about its centre and `center` is where that centre lands, as a fraction of the clip's width
from the frame's middle. A graphic is scaled about ITS OWN middle so it grows where it stands: with c the middle of the graphic's box, s the scale and d the move, the clip's centre must land at
P = c + d - s * (c - frame middle), and horiz = (P.x - W/2) / W, vert = (P.y - H/2) / H. Only scale and centre are used, never the anchor point. The vertical unit is assumed to follow the horizontal one (no vertical move
has been exported from Premiere yet): the first vertical move Ryan makes is its test, and the readout in Effect Controls settles it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reframe"))

import layers as layers_mod  # noqa: E402
import timeline  # noqa: E402

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
SAMPLES = 6                    # frames looked at along a graphic's span to find where it draws (a caption moves from line to line)
ALPHA_MIN = 24                 # a pixel counts as drawn above this alpha


class GraphicsError(Exception):
    pass


# ------------------------------------------------------------------ the maths

def to_motion(c: tuple[float, float], W: float, H: float, dx: float, dy: float, scale: float) -> tuple[float, float, float]:
    """(scale percent, center.horiz, center.vert) that moves a graphic whose box is centred at c (pixels) by dx, dy (fractions of the frame) and scales it about that middle."""
    s = scale / 100.0
    px = c[0] + dx * W - s * (c[0] - W / 2)
    py = c[1] + dy * H - s * (c[1] - H / 2)
    return scale, (px - W / 2) / W, (py - H / 2) / H


def from_motion(c: tuple[float, float], W: float, H: float, motion: tuple[float, float, float] | None) -> tuple[float, float, float]:
    """The inverse: (dx, dy, scale) of a clip's Basic Motion for a graphic centred at c. No motion is (0, 0, 100)."""
    if not motion:
        return 0.0, 0.0, 100.0
    scale, horiz, vert = motion
    s = scale / 100.0
    px, py = W / 2 + horiz * W, H / 2 + vert * H
    return (px - c[0] + s * (c[0] - W / 2)) / W, (py - c[1] + s * (c[1] - H / 2)) / H, scale


# ------------------------------------------------------------------ measuring a graphic from its own pixels

def _frames(mov: Path, times: list[float], w: int, h: int) -> list[np.ndarray]:
    out = []
    for t in times:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(mov), "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=area,format=rgba", "-f", "rawvideo", "-"], capture_output=True).stdout
        out.append(np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4) if len(raw) == w * h * 4 else np.zeros((h, w, 4), dtype=np.uint8))
    return out


def measure(mov: Path, span: float, w: int = 270, h: int = 480) -> tuple[list[float] | None, np.ndarray | None]:
    """Where the graphic draws, as [x0, y0, x1, y1] fractions of the frame (the union over SAMPLES frames along its span), and the frame with the most drawn pixels (for a picture of it). (None, None) when it never draws."""
    times = [span * (k + 0.5) / SAMPLES for k in range(SAMPLES)]
    best, best_n, box = None, 0, None
    for im in _frames(mov, times, w, h):
        drawn = im[..., 3] > ALPHA_MIN
        n = int(drawn.sum())
        if n == 0:
            continue
        ys, xs = np.where(drawn)
        b = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]
        box = b if box is None else [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
        if n > best_n:
            best, best_n = im, n
    return box, best


def label_of(layer: layers_mod.Layer) -> str:
    """What a graphic says, for the list: the title's words, the name tag's name, 'Captions', 'Overlay'."""
    stem = layer.name.rsplit(".", 1)[0].replace("Graphic - ", "")
    try:
        pl = json.loads((Path(layer.path).parent / "placement.json").read_text())
    except (OSError, ValueError):
        return stem
    plan = pl.get("plan") or {}
    if stem == "Overlay":
        return "Overlay (navy tint and lines)"
    if plan.get("title") and plan["title"].get("small"):
        return "Title: " + " ".join(str(plan["title"][k]) for k in ("small", "big", "joke") if plan["title"].get(k))[:60]
    if plan.get("lower_thirds"):
        lt = plan["lower_thirds"][0]
        return ("Call to action: " if lt.get("key") == "cta" else "Name tag: ") + (lt["name"] + (" · " + lt["sub"] if lt.get("sub") else ""))[:60]
    if plan.get("labels"):
        return "Label: " + plan["labels"][0].get("text", "")[:50]
    return stem[:60]


def describe(xml: Path, out_dir: Path | None = None, W: int | None = None, H: int | None = None) -> list[dict]:
    """Every video graphic the tools placed on the cut: [{id, name, label, track, start, end, box [x0,y0,x1,y1] (fractions of the frame), dx, dy, scale, picture}].
    `picture` is a PNG of the graphic as it draws (in out_dir, named by clip id) for the page to show while it is dragged; None without out_dir."""
    root = ET.parse(xml).getroot()
    seq = timeline._seq_for_cut(root)
    fw = W or int(seq.findtext("media/video/format/samplecharacteristics/width"))
    fh = H or int(seq.findtext("media/video/format/samplecharacteristics/height"))
    out = []
    for l in layers_mod.find_layers(xml):
        if l.kind != "video":
            continue
        box, im = measure(Path(l.path), max(l.end - l.start, 0.2))
        if box is None:
            continue
        c = ((box[0] + box[2]) / 2 * fw, (box[1] + box[3]) / 2 * fh)
        dx, dy, scale = from_motion(c, fw, fh, l.motion)
        pic = None
        if out_dir is not None and im is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            pic = f"{l.cid}.png"
            write_picture(Path(l.path), max(l.end - l.start, 0.2), out_dir / pic)
        out.append({"id": l.cid, "name": l.name, "label": label_of(l), "track": l.track, "start": round(l.start, 3), "end": round(l.end, 3), "box": [round(v, 4) for v in box],
                    "dx": round(dx, 5), "dy": round(dy, 5), "scale": round(scale, 3), "picture": pic, "_mov": l.path, "_size": [fw, fh]})
    return out


def write_picture(mov: Path, span: float, dest: Path, h: int = 540) -> None:
    """A PNG (with alpha) of the graphic at the moment it draws most, for the page to show while it is dragged."""
    best_t, best_n = span / 2, -1
    for k in range(SAMPLES):
        t = span * (k + 0.5) / SAMPLES
        (im,) = _frames(mov, [t], 90, 160)
        n = int((im[..., 3] > ALPHA_MIN).sum())
        if n > best_n:
            best_t, best_n = t, n
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{best_t:.3f}", "-i", str(mov), "-frames:v", "1", "-vf", f"scale=-2:{h}:flags=lanczos,format=rgba", str(dest)], capture_output=True)
    if r.returncode != 0 or not dest.is_file():
        raise GraphicsError(f"could not make a picture of {mov.name}: {r.stderr[-200:]!r}")


# ------------------------------------------------------------------ writing the motion

def _clip(root: ET.Element, cid: str) -> ET.Element:
    seq = timeline._seq_for_cut(root)
    for ci in seq.iterfind("media/video/track/clipitem"):
        if ci.get("id") == cid:
            return ci
    raise GraphicsError(f"no graphic with id {cid!r} on the cut")


def apply(xml_in: Path, xml_out: Path, edits: list[dict], centers: dict[str, tuple[float, float]], W: float, H: float) -> list[dict]:
    """Write xml_out with each edit as Basic Motion on its clip. `centers` maps a clip id to the middle of its box in pixels. An edit that puts a graphic back (dx = dy = 0, scale 100) removes the motion."""
    import reframe_xml as rx
    tree = ET.parse(xml_in)
    root = tree.getroot()
    done = []
    for e in edits:
        ci = _clip(root, e["id"])
        for old in ci.findall("filter"):
            if old.findtext("effect/name") == "Basic Motion":
                ci.remove(old)
        dx, dy, scale = float(e.get("dx", 0)), float(e.get("dy", 0)), float(e.get("scale", 100))
        if not (0.05 * 100 <= scale <= 400):
            raise GraphicsError(f"scale {scale:g}% is outside 5 to 400%")
        if abs(dx) < 1e-6 and abs(dy) < 1e-6 and abs(scale - 100) < 1e-6:
            done.append({"id": e["id"], "removed": True})
            continue
        s, horiz, vert = to_motion(centers[e["id"]], W, H, dx, dy, scale)
        ci.insert(list(ci).index(ci.find("file")) + 1, rx.motion_filter(s, horiz, vert))        # straight after <file>, where Premiere writes it
        done.append({"id": e["id"], "scale": s, "horiz": horiz, "vert": vert})
    ET.indent(root, space="\t")
    Path(xml_out).write_text(HEADER + ET.tostring(root, encoding="unicode") + "\n")
    return done


def _canon(e: ET.Element, skip: set[str]):
    return (e.tag, tuple(sorted(e.attrib.items())), (e.text or "").strip(), tuple(_canon(c, skip) for c in e if c.tag not in skip))


def check(xml_in: Path, xml_out: Path, edits: list[dict], centers: dict[str, tuple[float, float]], W: float, H: float) -> list[tuple[str, bool, str]]:
    """Read the output back: every clip but the edited ones is exactly as it was (the edited ones differ only in their Basic Motion), and each edited clip's motion is what was asked for."""
    a, b = ET.parse(xml_in).getroot(), ET.parse(xml_out).getroot()
    sa, sb = timeline._seq_for_cut(a), timeline._seq_for_cut(b)
    ids = {e["id"] for e in edits}
    same = True
    for ca, cb in zip(sa.iterfind("media/video/track/clipitem"), sb.iterfind("media/video/track/clipitem")):
        same &= _canon(ca, {"filter"} if ca.get("id") in ids else set()) == _canon(cb, {"filter"} if ca.get("id") in ids else set())
    same &= len(list(sa.iterfind("media/video/track/clipitem"))) == len(list(sb.iterfind("media/video/track/clipitem")))
    same &= [_canon(t, set()) for t in sa.findall("media/audio/track")] == [_canon(t, set()) for t in sb.findall("media/audio/track")]
    rows = [("GRAPHICS-REST-UNCHANGED", same, "every other clip, every audio track: exactly as it was" if same else "something other than the edited graphics changed")]
    bad = []
    for e in edits:
        m = timeline.motion_of(_clip(b, e["id"]))
        want = None if (abs(float(e.get("dx", 0))) < 1e-6 and abs(float(e.get("dy", 0))) < 1e-6 and abs(float(e.get("scale", 100)) - 100) < 1e-6) else to_motion(centers[e["id"]], W, H, float(e.get("dx", 0)), float(e.get("dy", 0)), float(e.get("scale", 100)))
        if (m is None) != (want is None) or (m and want and (abs(m[0] - want[0]) > 0.01 or abs(m[1] - want[1]) > 1e-5 or abs(m[2] - want[2]) > 1e-5)):
            bad.append(e["id"])
    rows.append(("GRAPHICS-MOTION-WRITTEN", not bad, f"{len(edits)} graphic(s) moved or scaled as asked" if not bad else f"not as asked: {bad}"))
    return rows
