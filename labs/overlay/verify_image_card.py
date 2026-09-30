#!/usr/bin/env python3
"""Check a built image-card folder against what it claims, from the rendered .mov and the source image.

    python3 labs/overlay/verify_image_card.py <folder>

  MOV-ALPHA            ProRes with real alpha at the sequence's size and frame rate, right length
  TRANSPARENT-BEFORE / TRANSPARENT-AFTER   nothing is drawn before the card enters or after it leaves
  SOURCE-UNCHANGED     the image file is byte-identical to what the card was made from
  MARGIN               the card (and caption) sit at least 40px inside the frame
  CARD-EDGES           the opaque card covers exactly the planned rectangle
  BORDER               the brand-blue border is there on all four sides
  IMAGE-PIXELS         the image inside the border matches the source image (mean difference)
  NOT-CROPPED          all four corners and the edge midpoints of the source image are visible where they should be
  HIGHLIGHT            an orange ring on all four sides of the planned box, hollow (the image shows through), inside the image
  CAPTION              the navy strip is there with light text on it
Exit 0 = every check passed.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 1920, 1080
BLUE, ORANGE, NAVY = (3, 145, 216), (244, 105, 11), (3, 52, 89)


def frame(path: Path, t: float, w: int, h: int) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True)
    a = np.frombuffer(p.stdout, dtype=np.uint8)
    if a.size != w * h * 4:
        raise ValueError(f"could not read a {w}x{h} frame at {t:.2f}s from {path.name}")
    return a.reshape(h, w, 4)


def source_rgb(image: Path, w: int, h: int) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(image), "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=bicubic", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).reshape(h, w, 3).astype(int)


def probe(path: Path) -> dict:
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate:format=duration",
                                   "-of", "json", str(path)], capture_output=True, text=True).stdout)
    return {**j["streams"][0], "duration": float(j["format"]["duration"])}


def near(px, want, tol=28) -> bool:
    return all(abs(int(a) - b) <= tol for a, b in zip(px[:3], want))


def main() -> int:
    d = Path(sys.argv[1])
    pl = json.loads((d / "placement.json").read_text())
    g, total = pl["geometry"], pl["duration_sec"]
    mov, image = d / "card.mov", Path(pl["image"])
    rend = pl["render"]
    rw, rh, rfps = rend["width"], rend["height"], rend["fps"]
    s = rw / W
    S = lambda v: int(round(v * s))                                     # noqa: E731
    rows: list[tuple[str, bool, str]] = []

    m = probe(mov)
    num, den = (int(x) for x in m["r_frame_rate"].split("/"))
    rows.append(("MOV-ALPHA", m["codec_name"] == "prores" and "a" in m["pix_fmt"].replace("yuv", "") and (m["width"], m["height"]) == (rw, rh)
                 and abs(m["duration"] - total) < 0.1 and abs(num / den - rfps) < 0.01,
                 f"{m['codec_name']} {m['pix_fmt']} {m['width']}x{m['height']} {num / den:.3f}fps {m['duration']:.2f}s (wanted {rw}x{rh} {rfps:.3f}fps {total:.2f}s)"))

    t_in, t_out = g["t_in"], g["t_out"]
    if t_in > 0.12:
        a = int(frame(mov, t_in * 0.4, rw, rh)[..., 3].max())
        rows.append(("TRANSPARENT-BEFORE", a <= 2, f"max alpha {a} at {t_in * 0.4:.2f}s, before the card enters"))
    a = int(frame(mov, total - 0.04, rw, rh)[..., 3].max())
    rows.append(("TRANSPARENT-AFTER", a <= 2, f"max alpha {a} at {total - 0.04:.2f}s, after the card has left"))
    rows.append(("SOURCE-UNCHANGED", image.exists() and hashlib.sha1(image.read_bytes()).hexdigest() == pl["image_sha1"], "the image file is byte-identical to what the card was made from"))

    fr, im, hl, cap, b = g["frame"], g["image"], g.get("highlight"), g.get("caption"), g["border"]
    bottom = cap["y"] + cap["h"] if cap else fr["y"] + fr["h"]
    margin = min(fr["x"], fr["y"], W - (fr["x"] + fr["w"]), H - bottom)
    rows.append(("MARGIN", margin >= 40, f"the card sits {margin}px inside the frame at its nearest edge (need 40)"))

    t = min(t_in + 1.1, t_out - 0.1)
    f = frame(mov, t, rw, rh)
    solid = f[..., 3] > 200
    ys, xs = np.where(solid)
    tol = S(6)
    if len(xs) == 0:
        rows.append(("CARD-EDGES", False, f"nothing is drawn at {t:.2f}s"))
    else:
        got = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        want = (S(fr["x"]), S(fr["y"]), S(fr["x"] + fr["w"]), S(bottom))
        rows.append(("CARD-EDGES", all(abs(a_ - b_) <= tol for a_, b_ in zip(got, want)), f"opaque card spans {got} (planned {want}) at {t:.2f}s"))

    rgb = f[..., :3].astype(int)
    mid = lambda x0, y0, x1, y1: ((x0 + x1) // 2, (y0 + y1) // 2)        # noqa: E731
    ring = [(fr["x"] + fr["w"] / 2, fr["y"] + b / 2), (fr["x"] + fr["w"] / 2, fr["y"] + fr["h"] - b / 2),
            (fr["x"] + b / 2, fr["y"] + fr["h"] / 2), (fr["x"] + fr["w"] - b / 2, fr["y"] + fr["h"] / 2)]
    hits = [near(f[S(y), S(x)], BLUE) and f[S(y), S(x), 3] >= 250 for x, y in ring]
    rows.append(("BORDER", all(hits), f"blue border on {sum(hits)} of 4 sides (top, bottom, left, right: {hits})"))

    iw, ih = S(im["w"]), S(im["h"])
    ix, iy = S(im["x"]), S(im["y"])
    src = source_rgb(image, iw, ih)
    act = rgb[iy:iy + ih, ix:ix + iw]
    mask = np.ones((ih, iw), dtype=bool)
    mask[:S(8)], mask[-S(8):], mask[:, :S(8)], mask[:, -S(8):] = False, False, False, False      # the image's own rounded corners
    if hl:
        hx, hy, hw, hh = S(hl["x"]) - ix, S(hl["y"]) - iy, S(hl["w"]), S(hl["h"])
        band = S(14)
        mask[max(0, hy - band):hy + hh + band, max(0, hx - band):hx + band + 0] = False
        mask[max(0, hy - band):hy + hh + band, max(0, hx + hw - band):hx + hw + band] = False
        mask[max(0, hy - band):hy + band, max(0, hx - band):hx + hw + band] = False
        mask[max(0, hy + hh - band):hy + hh + band, max(0, hx - band):hx + hw + band] = False
    diff = float(np.abs(act - src)[mask].mean())
    rows.append(("IMAGE-PIXELS", diff <= 14.0, f"the image inside the border matches the source: mean difference {diff:.1f} of 255 (limit 14)"))

    p = S(20)
    spots = {"top-left": (S(12), S(12)), "top-right": (iw - S(12) - p, S(12)), "bottom-left": (S(12), ih - S(12) - p), "bottom-right": (iw - S(12) - p, ih - S(12) - p),
             "top-middle": (iw // 2 - p // 2, S(12)), "bottom-middle": (iw // 2 - p // 2, ih - S(12) - p), "left-middle": (S(12), ih // 2 - p // 2), "right-middle": (iw - S(12) - p, ih // 2 - p // 2)}
    bad = []
    for name, (x0, y0) in spots.items():
        if hl and (hx - band < x0 + p and x0 < hx + hw + band and hy - band < y0 + p and y0 < hy + hh + band):
            continue                                                  # the highlight ring sits on this patch
        if float(np.abs(act[y0:y0 + p, x0:x0 + p] - src[y0:y0 + p, x0:x0 + p]).mean()) > 22:
            bad.append(name)
    rows.append(("NOT-CROPPED", not bad, "all four corners and edge midpoints of the source are where they should be" if not bad else f"these parts of the image do not match the source: {', '.join(bad)}"))

    if hl:
        rx, ry, rw_, rh_ = S(hl["x"]), S(hl["y"]), S(hl["w"]), S(hl["h"])
        sides = [(rx + rw_ // 2, ry + S(3)), (rx + rw_ // 2, ry + rh_ - S(3)), (rx + S(3), ry + rh_ // 2), (rx + rw_ - S(3), ry + rh_ // 2)]
        ok = [near(f[y, x], ORANGE, 34) and f[y, x, 3] >= 250 for x, y in sides]
        inside = (hl["x"] >= im["x"] and hl["y"] >= im["y"] and hl["x"] + hl["w"] <= im["x"] + im["w"] and hl["y"] + hl["h"] <= im["y"] + im["h"])
        ci = S(24)
        cx0, cy0, cx1, cy1 = rx + ci, ry + ci, rx + rw_ - ci, ry + rh_ - ci
        hollow = cx1 > cx0 and cy1 > cy0 and float(np.abs(rgb[cy0:cy1, cx0:cx1] - src[cy0 - iy:cy1 - iy, cx0 - ix:cx1 - ix]).mean()) <= 14
        rows.append(("HIGHLIGHT", all(ok) and inside and hollow, f"orange ring on {sum(ok)} of 4 sides, inside the image: {inside}, hollow (image shows through): {hollow}"))

    if cap:
        cx, cy, cw, ch = S(cap["x"]), S(cap["y"]), S(cap["w"]), S(cap["h"])
        bg = f[cy + S(10), cx + cw - S(16)]
        light = int(((rgb[cy:cy + ch, cx:cx + cw] > 200).all(axis=2) & (f[cy:cy + ch, cx:cx + cw, 3] > 200)).sum())
        rows.append(("CAPTION", near(bg, NAVY, 16) and bg[3] >= 235 and light >= 150 * s * s,
                     f"navy strip {tuple(int(v) for v in bg[:3])} alpha {int(bg[3])}, {light} light text pixels on it"))

    wd = max(len(n) for n, _, _ in rows)
    bad = 0
    for n, ok, detail in rows:
        bad += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {n.ljust(wd)}  {detail}")
    print("\nAll checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
