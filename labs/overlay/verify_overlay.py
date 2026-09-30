#!/usr/bin/env python3
"""Check a built overlay folder against what it claims, from the rendered files themselves.

    python3 labs/overlay/verify_overlay.py <folder>

  MOV-ALPHA          the layer is ProRes with a real alpha channel, 1920x1080, right length
  TRANSPARENT-BEFORE / TRANSPARENT-AFTER   nothing is drawn before the callout enters or after it leaves
  BOX / LABEL / ARROW   each element is present, and the box is hollow
  POSITION           the box sits where the drawing was made (its centre, within 6px)
  PREVIEW-AUDIO      the preview's audio is bit-identical to the untouched clip's
  FOOTAGE-UNTOUCHED  away from the callout the preview equals the original clip
  CALLOUT-VISIBLE    over the label the preview differs strongly from the original
Exit 0 = all passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 1920, 1080


def frame(path: Path, t: float, pix: str, w: int = W, h: int = H) -> np.ndarray:
    ch = 4 if pix == "rgba" else 3
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix, "-"],
                       capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.uint8).reshape(h, w, ch)


def pcm(path: Path) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "2", "-ar", "48000", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    j = json.loads(p.stdout)
    return {**j["streams"][0], "duration": float(j["format"]["duration"])}


def main() -> int:
    d = Path(sys.argv[1])
    pl = json.loads((d / "placement.json").read_text())
    cfg, total = pl["geometry"], pl["duration_sec"]
    mov, preview, excerpt = d / "overlay.mov", d / "overlay_preview.mp4", d / "excerpt.mp4"
    rows: list[tuple[str, bool, str]] = []

    rend = pl.get("render", {"width": W, "height": H, "fps": 30.0})
    rw, rh, rfps = rend["width"], rend["height"], rend["fps"]
    s = rw / W                                           # layout coordinates are in 1920x1080 space
    S = lambda v: int(round(v * s))                      # noqa: E731

    m = probe(mov)
    num, den = (int(x) for x in m["r_frame_rate"].split("/"))
    rows.append(("MOV-ALPHA", m["codec_name"] == "prores" and "a" in m["pix_fmt"].replace("yuv", "") and (m["width"], m["height"]) == (rw, rh)
                 and abs(m["duration"] - total) < 0.1 and abs(num / den - rfps) < 0.01,
                 f"{m['codec_name']} {m['pix_fmt']} {m['width']}x{m['height']} {num / den:.3f}fps {m['duration']:.2f}s (wanted {rw}x{rh} {rfps:.3f}fps {total:.2f}s)"))

    a_before = frame(mov, cfg["t_in"] * 0.4, "rgba", rw, rh)[..., 3]
    a_after = frame(mov, total - 0.04, "rgba", rw, rh)[..., 3]
    rows.append(("TRANSPARENT-BEFORE", int(a_before.max()) <= 2, f"max alpha {int(a_before.max())} at {cfg['t_in'] * 0.4:.2f}s"))
    rows.append(("TRANSPARENT-AFTER", int(a_after.max()) <= 2, f"max alpha {int(a_after.max())} at {total - 0.04:.2f}s"))

    t_mid = cfg["t_in"] + 1.6
    mid = frame(mov, t_mid, "rgba", rw, rh)
    al = mid[..., 3]
    b, lb, ar = cfg["box"], cfg["label"], cfg["arrow"]                    # 1080p layout, used for the preview
    sb, sl = {k: S(v) for k, v in b.items()}, {k: S(v) for k, v in lb.items()}   # the same, at render size
    cy, cx = sb["y"] + sb["h"] // 2, sb["x"] + sb["w"] // 2
    edges = {"left": al[cy, sb["x"] + S(3)], "right": al[cy, sb["x"] + sb["w"] - S(3)],
             "top": al[sb["y"] + S(3), sb["x"] + sb["w"] // 4], "bottom": al[sb["y"] + sb["h"] - S(3), sb["x"] + sb["w"] // 4]}
    rows.append(("BOX", min(int(v) for v in edges.values()) >= 200 and int(al[cy, cx]) == 0,
                 f"edge alpha {min(int(v) for v in edges.values())}+, centre {int(al[cy, cx])} (hollow)"))
    lcx, lcy = sl["x"] + sl["w"] // 2, sl["y"] + sl["h"] // 2
    navy = tuple(int(v) for v in mid[sl["y"] + S(8), sl["x"] + S(300), :3])
    rows.append(("LABEL", int(al[lcy, lcx]) >= 200 and navy != (0, 0, 0), f"alpha {int(al[lcy, lcx])} at centre; navy at top edge {navy}"))
    tx, ty = ar["tip"]
    mx, my = S(ar["x"] + (tx - ar["x"]) * 0.5), S(ar["y"] + (ty - ar["y"]) * 0.5)
    rows.append(("ARROW", int(al[my, mx]) >= 200 and int(al[S(100), rw - S(100)]) == 0,
                 f"alpha {int(al[my, mx])} at its midpoint, empty corner {int(al[S(100), rw - S(100)])}"))

    reg = pl["region"]
    lo = max(0, sb["x"] - S(30))
    row = np.where(al[cy, lo:sb["x"] + sb["w"] + S(30)] > 128)[0] + lo
    got_cx = (int(row.min()) + int(row.max())) / 2 / s if len(row) else -1      # back in 1920 space
    want_cx = (reg["x0"] + reg["x1"]) / 2 * W
    rows.append(("POSITION", abs(got_cx - want_cx) <= 6, f"box centre x {got_cx:.0f}px vs the drawing's {want_cx:.0f}px (1920 space)"))

    pv = probe(preview)
    rows.append(("PREVIEW-FORMAT", pv["codec_name"] == "h264" and abs(pv["duration"] - total) < 0.15, f"{pv['codec_name']} {pv['duration']:.2f}s"))
    rows.append(("PREVIEW-AUDIO", np.array_equal(pcm(preview), pcm(excerpt)) and len(pcm(preview)) > 0, "preview audio bit-identical to the untouched clip"))

    e_b, p_b = frame(excerpt, cfg["t_in"] * 0.4, "rgb24").astype(np.int16), frame(preview, cfg["t_in"] * 0.4, "rgb24").astype(np.int16)
    e_m, p_m = frame(excerpt, t_mid, "rgb24").astype(np.int16), frame(preview, t_mid, "rgb24").astype(np.int16)
    d_before, d_top = float(np.abs(e_b - p_b).mean()), float(np.abs(e_m[:120] - p_m[:120]).mean())
    rows.append(("FOOTAGE-UNTOUCHED", d_before < 2.5 and d_top < 2.5, f"mean diff {d_before:.2f} before the callout, {d_top:.2f} in an untouched strip during it"))
    d_label = float(np.abs(e_m[lb["y"]:lb["y"] + lb["h"], lb["x"]:lb["x"] + lb["w"]] - p_m[lb["y"]:lb["y"] + lb["h"], lb["x"]:lb["x"] + lb["w"]]).mean())
    rows.append(("CALLOUT-VISIBLE", d_label > 30, f"mean diff {d_label:.0f} over the label"))

    w = max(len(n) for n, _, _ in rows)
    bad = 0
    for n, ok, detail in rows:
        bad += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {n.ljust(w)}  {detail}")
    print("\nAll checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
