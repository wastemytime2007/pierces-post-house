#!/usr/bin/env python3
"""A screenshot or photo as a framed card with an optional highlight, rendered as a transparent layer.

    PRECUT_ROOT=~/precut-checkout python3 labs/overlay/make_image_card.py <image> --out "<folder>" \
        --xml "<the export it will go in>" --anchor-source "<source file name>" --anchor-sec <seconds in it> \
        [--highlight x0,y0,x1,y1] [--caption "..."] [--position center|left|right] [--hold 3.5] [--lead 0.5]

The image is shown whole (never cropped), in a brand-blue border with a soft shadow, and a hollow orange
highlight box can be drawn around what matters (as fractions of the image: 0,0 top-left, 1,1 bottom-right).
A caption strip can sit below. It is rendered by HyperFrames to a transparent ProRes 4444 .mov at the target
sequence's own size and frame rate. The card is anchored to a source frame, exactly like a callout, so it
can be re-placed on any later cut (labs/reconform treats it as one). Nothing is invented: the image, the
highlight and the words are all inputs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE))

import change_callout as cc  # noqa: E402  (timeline_dict)
import make_overlay as mo  # noqa: E402  (hf, sequence_spec, gsap_file, locate, constants)
import timeline  # noqa: E402

BORDER = 10
MAX_W, MAX_H = 1000, 560
CAP_H, CAP_GAP, CAP_PX = 96, 18, 44
CENTRE = {"left": 640, "center": 960, "right": 1280}
FADE_OUT, TAIL = 0.35, 0.35


class CardError(Exception):
    pass


def parse_box(s: str) -> dict:
    try:
        x0, y0, x1, y1 = (float(v) for v in s.split(","))
    except ValueError:
        raise CardError("--highlight is four numbers: x0,y0,x1,y1 as fractions of the image")
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise CardError("the highlight must sit inside the image: 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1")
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def layout(iw: int, ih: int, highlight: dict | None, caption: str | None, position: str = "center") -> dict:
    """Card geometry in 1920x1080 layout space: the whole image, never cropped, fitted inside MAX_W x MAX_H."""
    if iw < 50 or ih < 50:
        raise CardError(f"the image is {iw}x{ih}, too small to show")
    if position not in CENTRE:
        raise CardError(f"position must be one of {sorted(CENTRE)}")
    scale = min(MAX_W / iw, MAX_H / ih, 1.5)
    w, h = round(iw * scale), round(ih * scale)
    cap_total = CAP_GAP + CAP_H if caption else 0
    fw, fh = w + 2 * BORDER, h + 2 * BORDER
    x = round(CENTRE[position] - fw / 2)
    y = round((1080 - (fh + cap_total)) / 2 - 10)
    cfg = {"frame": {"x": x, "y": y, "w": fw, "h": fh}, "image": {"x": x + BORDER, "y": y + BORDER, "w": w, "h": h},
           "border": BORDER, "scale": round(scale, 4), "highlight": None, "caption": None}
    if highlight:
        cfg["highlight"] = {"x": round(cfg["image"]["x"] + highlight["x0"] * w), "y": round(cfg["image"]["y"] + highlight["y0"] * h),
                            "w": round((highlight["x1"] - highlight["x0"]) * w), "h": round((highlight["y1"] - highlight["y0"]) * h)}
    if caption:
        cfg["caption"] = {"x": x, "y": y + fh + CAP_GAP, "w": fw, "h": CAP_H, "px": CAP_PX, "text": caption}
    return cfg


def image_size(path: Path) -> tuple[int, int]:
    s = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                       capture_output=True, text=True).stdout.strip()
    try:
        w, h = s.split("x")
        return int(w), int(h)
    except ValueError:
        raise CardError(f"could not read an image from {path}")


def to_png(src: Path, dst: Path) -> None:
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", str(dst)], capture_output=True, text=True)
    if p.returncode:
        raise CardError(f"could not read the image: {p.stderr.strip()[-200:]}")


def timing(anchor_source: str, anchor_sec: float, timeline_d: dict, lead: float, hold: float) -> dict:
    clip, t_anchor = mo.locate({"source": anchor_source, "source_sec": anchor_sec}, timeline_d)
    start = t_anchor - lead
    if start < clip["start"]:
        start, lead = clip["start"], t_anchor - clip["start"]
    total = lead + hold + FADE_OUT + TAIL
    if start + total > clip["end"]:
        hold = clip["end"] - start - lead - FADE_OUT - TAIL
        total = lead + hold + FADE_OUT + TAIL
        if hold < 1.5:
            raise CardError("not enough of this shot is left after that frame for a card that can be read")
    return {"start": round(start, 3), "lead": round(lead, 3), "hold": round(hold, 3), "total": round(total, 3), "clip": clip}


def write_project(proj: Path, cfg: dict, png: Path, total: float) -> None:
    proj.mkdir(parents=True, exist_ok=True)
    uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    html = (HERE / "card_template.html").read_text().replace("__DUR__", str(total)).replace("__IMG__", uri).replace(
        "/*__CFG__*/null", json.dumps(cfg).replace("</", "<\\/"))
    (proj / "index.html").write_text(html)
    (proj / "gsap.min.js").write_bytes(mo.gsap_file().read_bytes())
    (proj / "meta.json").write_text(json.dumps({"id": "card", "name": "card"}))
    (proj / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True}}, indent=2))


def build_card(image: Path, out: Path, xml: Path, anchor_source: str, anchor_sec: float, *, highlight: dict | None = None,
               caption: str | None = None, position: str = "center", hold: float = 3.5, lead: float = 0.5, render=None,
               spec_of=mo.sequence_spec) -> tuple[int, dict | None]:
    spec = spec_of(xml)
    iw, ih = image_size(image)
    cfg = layout(iw, ih, highlight, caption, position)
    tm = timing(anchor_source, anchor_sec, cc.timeline_dict(timeline.load_cut(xml)), lead, hold)
    cfg.update({"t_in": tm["lead"], "t_out": round(tm["lead"] + tm["hold"], 3), "fade_out": FADE_OUT, "total": tm["total"]})
    out.mkdir(parents=True, exist_ok=True)
    png = out / "image.png"
    to_png(image, png)
    proj, mov = out / "hyperframes_project", out / "card.mov"
    write_project(proj, cfg, png, tm["total"])
    print(f"card: {iw}x{ih} image at x{cfg['scale']:.2f}; on the cut from {tm['start']:.2f}s for {tm['total']:.2f}s (anchor frame at {tm['start'] + tm['lead']:.2f}s)")
    if render is None:
        chk = mo.hf(proj, "check")
        if "Check passed" not in chk.stdout:
            print("HyperFrames check FAILED:\n" + "\n".join(chk.stdout.splitlines()[-12:]), file=sys.stderr)
            return 1, None
        print("HyperFrames check passed")
        rargs = ["render", "--format", "mov", "--fps", spec["fps_arg"], "-o", str(mov)]
        if spec["resolution"]:
            rargs += ["--resolution", spec["resolution"]]
        r = mo.hf(proj, *rargs)
        if r.returncode != 0 or not mov.exists():
            print("render FAILED:\n" + (r.stdout + r.stderr)[-1500:], file=sys.stderr)
            return 1, None
    else:
        render(proj, mov, spec, cfg)
    fr = cfg["frame"]
    g = dict(cfg)
    g.update({"box": {"x": fr["x"], "y": fr["y"], "w": fr["w"], "h": fr["h"]},
              "label": cfg["caption"] and {k: cfg["caption"][k] for k in ("x", "y", "w", "h")} or {"x": fr["x"], "y": fr["y"], "w": 0, "h": 0},
              "arrow": {"x": fr["x"], "y": fr["y"], "len": 0, "angle": 0, "tip": [fr["x"], fr["y"]]}})
    placement = {"kind": "image_card", "place_overlay_on_timeline_at_sec": tm["start"], "duration_sec": tm["total"], "overlay": mov.name,
                 "overlay_path": str(mov.resolve()),
                 "render": {"width": spec["width"], "height": spec["height"], "fps": spec["fps"], "fps_arg": spec["fps_arg"]},
                 "anchor": {"source": anchor_source, "source_sec": anchor_sec, "lead_sec": tm["lead"]},
                 "image": str(image.resolve()), "image_sha1": hashlib.sha1(image.read_bytes()).hexdigest(), "image_size": [iw, ih],
                 "highlight_fraction": highlight, "caption": caption, "position": position, "hold_sec": tm["hold"],
                 "hyperframes": mo.HF_VERSION, "gsap": mo.GSAP_VERSION, "geometry": g}
    (out / "placement.json").write_text(json.dumps(placement, indent=2))
    v = subprocess.run([sys.executable, str(HERE / "verify_image_card.py"), str(out)], capture_output=True, text=True)
    print(v.stdout.rstrip())
    if v.returncode != 0:
        print(v.stderr, file=sys.stderr)
        return 1, placement
    print(f"\nplace {mov.name} at {tm['start']:.2f}s on the timeline (or reconform --overlay \"{out}\")")
    return 0, placement


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--xml", type=Path, required=True, help="the export the card will be placed in; renders at its size and frame rate")
    ap.add_argument("--anchor-source", required=True, help="file name of the footage the card is tied to (as the XML names it)")
    ap.add_argument("--anchor-sec", type=float, required=True, help="the moment in that file the card appears at")
    ap.add_argument("--highlight", help="x0,y0,x1,y1 as fractions of the image")
    ap.add_argument("--caption")
    ap.add_argument("--position", default="center", choices=sorted(CENTRE))
    ap.add_argument("--hold", type=float, default=3.5)
    ap.add_argument("--lead", type=float, default=0.5, help="seconds the card is on screen before the anchor moment")
    a = ap.parse_args()
    try:
        hl = parse_box(a.highlight) if a.highlight else None
        code, _p = build_card(a.image, a.out, a.xml, a.anchor_source, a.anchor_sec, highlight=hl, caption=a.caption, position=a.position, hold=a.hold, lead=a.lead)
    except (CardError, mo.OverlayError, timeline.TimelineError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
