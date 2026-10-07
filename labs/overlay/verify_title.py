#!/usr/bin/env python3
"""Check a rendered title/labels layer against its plan, by decoding frames from the .mov (not from the HTML that made it).

    python3 labs/overlay/verify_title.py <folder with title.mov and placement.json>

  LAYER-FORMAT      ProRes with real alpha, the sequence's size and frame rate, exactly the cut's number of frames
  NOTHING-WHERE-NOTHING-IS-DUE   fully transparent before the first element, between elements and after the last
  TITLE-FIELD       the navy field covers most of the frame once the title is on, and is gone when it clears
  TITLE-BUILDS      the small white line, then the big orange word, then the orange joke each appear at their own build (their band empty before, filled after)
  LABEL-BUILDS      each label is empty just before it starts, fills word by word (the white pixels in its band grow with every word), and is gone when it clears
Exit 0 = all applicable checks passed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SCALE = 4


def probe(path: Path) -> dict:
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=width,height,pix_fmt,r_frame_rate,nb_read_frames,codec_name", "-of", "json", str(path)],
                                  capture_output=True, text=True).stdout)["streams"][0]
    n, d = (int(x) for x in j["r_frame_rate"].split("/"))
    return {"w": j["width"], "h": j["height"], "pix": j["pix_fmt"], "fps": n / d, "frames": int(j["nb_read_frames"]), "codec": j["codec_name"]}


def frame_at(path: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.4f}", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w // SCALE}:{h // SCALE}:flags=area,format=rgba", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h // SCALE, w // SCALE, 4).astype(int)


def band(img: np.ndarray, y0: float, y1: float) -> np.ndarray:
    H = img.shape[0] * SCALE
    return img[int(max(y0, 0) / SCALE):int(min(y1, H) / SCALE)]


def white(px: np.ndarray) -> int:
    return int(((px[..., 0] > 225) & (px[..., 1] > 225) & (px[..., 2] > 225) & (px[..., 3] > 200)).sum())


def orange(px: np.ndarray) -> int:
    return int(((px[..., 0] > 200) & (px[..., 1] > 60) & (px[..., 1] < 160) & (px[..., 2] < 90) & (px[..., 3] > 200)).sum())


def coverage(img: np.ndarray, min_alpha: int = 8) -> float:
    return float((img[..., 3] > min_alpha).mean())


def main() -> int:
    d = Path(sys.argv[1])
    pl = json.loads((d / "placement.json").read_text())
    mov = d / pl["overlay"]
    W, H = pl["render"]["width"], pl["render"]["height"]
    plan, g = pl["plan"], pl["layout"]
    rows: list[tuple[str, bool | None, str]] = []

    pr = probe(mov)
    want_frames = round(pl["duration_sec"] * pl["render"]["fps"])
    ok = (pr["w"], pr["h"]) == (W, H) and abs(pr["fps"] - pl["render"]["fps"]) < 0.01 and "a" in pr["pix"].replace("yuv", "") and abs(pr["frames"] - want_frames) <= 1 and pr["codec"] == "prores"
    rows.append(("LAYER-FORMAT", ok, f"{pr['codec']} {pr['pix']} {pr['w']}x{pr['h']} at {pr['fps']:.3f}, {pr['frames']} frames (wanted {W}x{H}, {pl['render']['fps']:.3f}, {want_frames})"))

    spans = []                                                       # (on, off) of everything that can be visible
    if plan["title"]:
        spans.append((plan["title"]["on"][0], plan["title"]["off"]))
    spans += [(lb["on"], lb["off"]) for lb in plan["labels"]]

    def free(t: float) -> bool:
        return all(not (a - 0.02 <= t <= b + 0.02) for a, b in spans) and 0 <= t < pl["duration_sec"] - 0.05

    gaps = [0.05] + [b + 0.1 for a, b in spans] + [a - 0.08 for a, b in spans] + [(spans[i][1] + spans[i + 1][0]) / 2 for i in range(len(spans) - 1) if spans[i + 1][0] - spans[i][1] > 0.3]
    gaps = sorted({round(t, 3) for t in gaps if free(t)})
    seen = [(t, coverage(frame_at(mov, t, W, H))) for t in gaps]
    rows.append(("NOTHING-WHERE-NOTHING-IS-DUE", all(c < 0.0005 for _t, c in seen) and bool(seen), f"{len(seen)} moments with nothing due, worst coverage {max((c for _t, c in seen), default=0) * 100:.3f}%"))

    if plan["title"]:
        t = plan["title"]
        on, off = t["on"], t["off"]
        f1 = frame_at(mov, on[0] + 0.12, W, H)
        field = float((f1[..., 3] > 150).mean())
        rows.append(("TITLE-FIELD", field > 0.7, f"the navy field covers {field * 100:.0f}% of the frame at {on[0] + 0.12:.2f}s" + ("" if field > 0.7 else " (wanted over 70%)")))
        fb =[frame_at(mov, x + 0.12, W, H) for x in on]
        bands = {"small": (g["small_y"], g["small_px"]), "big": (g["big_y"], g["big_px"]), "joke": (g["joke_y"], g["joke_px"])}

        def count(im, which):
            y, px = bands[which]
            b = band(im, y - px * 0.1, y + px * 1.15)
            return white(b) if which == "small" else orange(b)
        pre = frame_at(mov, on[0] - 0.08, W, H) if on[0] - 0.08 >= 0 and free(on[0] - 0.08) else None
        small_now, big_before, big_now, joke_before, joke_now = count(fb[0], "small"), count(fb[0], "big"), count(fb[1], "big"), count(fb[1], "joke"), count(fb[2], "joke")
        builds_ok = True                                                  # each line the spec has comes on at its own build; a line the spec leaves empty must draw nothing
        builds_ok &= small_now > 20 if t["small"] else small_now < 5
        builds_ok &= (big_before < 5 and big_now > 100) if t["big"] else big_now < 5
        builds_ok &= (joke_before < 5 and joke_now > 20) if t["joke"] else joke_now < 5
        rows.append(("TITLE-BUILDS", builds_ok, f"white small line {small_now}px at build 1; orange big word {big_before}px before build 2 and {big_now}px after; orange joke {joke_before}px before build 3 and {joke_now}px after"))
        after = off + 0.1
        if free(after):
            rows.append(("TITLE-CLEARS", coverage(frame_at(mov, after, W, H)) < 0.0005, f"fully transparent {after:.2f}s, just after the title clears at {off:.2f}s"))

    for i, lb in enumerate(plan["labels"], 1):
        y0, y1 = g["label_y"] - 0.2 * g["label_px"], g["label_y"] + 4.2 * g["label_px"]
        x0, x1 = 0, g["label_x"] + g["label_w"] + 40
        counts = []
        for tw in lb["word_times"]:
            im = frame_at(mov, tw + 0.08, W, H)
            counts.append(white(band(im, y0, y1)[:, : (x1 // SCALE)]))
        grows = all(counts[k + 1] > counts[k] for k in range(len(counts) - 1)) and counts[0] > 5
        pre_t = lb["on"] - 0.06
        pre = coverage(frame_at(mov, pre_t, W, H)) if free(pre_t) else None
        rows.append((f"LABEL-BUILDS {i} ({lb['text']})", grows and (pre is None or pre < 0.0005), f"white pixels per word {counts}" + (f"; empty {lb['on'] - pre_t:.2f}s before it starts" if pre is not None else "")))
        after = lb["off"] + 0.08
        if free(after):
            rows.append((f"LABEL-CLEARS {i}", coverage(frame_at(mov, after, W, H)) < 0.0005, f"fully transparent at {after:.2f}s, just after it clears at {lb['off']:.2f}s"))

    w = max(len(n) for n, _, _ in rows)
    bad = 0
    for n, ok, why in rows:
        print(f"  [{'PASS' if ok else 'FAIL' if ok is False else 'SKIP'}] {n:{w}}  {why}")
        bad += ok is False
    print("\nAll applicable checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
