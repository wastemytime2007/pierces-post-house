#!/usr/bin/env python3
"""A review note with a drawing in, a transparent callout overlay out.

    python3 labs/overlay/make_overlay.py <review_notes.json> --note 3 \
        --review-dir "<folder with timeline.json>" --out "<folder>" \
        [--title "The spacer"] [--subtitle "A piece of cardboard pulled off the box"] [--open]

The overlay is rendered by HyperFrames (Apache 2.0) to a transparent ProRes 4444 .mov: a
separate layer to place over the untouched footage, not a re-render of it. A preview .mp4 is
made by compositing that .mov over the clip, so the preview shows exactly what the layer
does. Text wording is an input, not a decision: the defaults are Claude's, drawn from the
note, and easy to change.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HF_VERSION = "0.8.93"
GSAP_VERSION = "3.15.0"
W, H, FPS = 1920, 1080, 30
LEAD, HOLD, FADE_OUT, TAIL = 1.0, 3.3, 0.35, 0.35
ARROW_HEAD = 34          # px the head adds beyond the line
LABEL_W = 880
AVAIL_W = LABEL_W - 80   # text width inside the label's padding


class OverlayError(Exception):
    pass


def locate(note: dict, timeline: dict) -> tuple[dict, float]:
    """The clip a note's frame lives in on THIS timeline, and its time here. Matched by source
    position, so it works whichever earlier version the note was written on."""
    for c in timeline["clips"]:
        if c["source"] == note["source"] and c["src_in"] - 0.02 <= note["source_sec"] <= c["src_out"] + 0.02:
            return c, c["start"] + (note["source_sec"] - c["src_in"])
    raise OverlayError(f"the frame this note was left on ({note['source']} at {note['source_sec']}s) is not in this cut")


def union_bbox(shapes: list[dict]) -> dict:
    boxes = [s["bbox"] for s in shapes if s.get("bbox")]
    if not boxes:
        raise OverlayError("this note has no drawing to place the callout from")
    return {"x0": min(b["x0"] for b in boxes), "y0": min(b["y0"] for b in boxes),
            "x1": max(b["x1"] for b in boxes), "y1": max(b["y1"] for b in boxes)}


def layout(bbox: dict, title: str, subtitle: str) -> dict:
    """Pixel geometry for the box, the label and the arrow between them (1920x1080)."""
    pad_x, pad_y = 0.012 * W, 0.012 * H
    bx, by = bbox["x0"] * W - pad_x, bbox["y0"] * H - pad_y
    bw, bh = max(120, (bbox["x1"] - bbox["x0"]) * W + 2 * pad_x), max(80, (bbox["y1"] - bbox["y0"]) * H + 2 * pad_y)
    bx, by = max(10, min(bx, W - bw - 10)), max(10, min(by, H - bh - 10))
    cx = bx + bw / 2

    title_px = 56 if len(title) * 56 * 0.62 <= AVAIL_W else max(34, int(AVAIL_W / (len(title) * 0.62)))
    sub_px = 36 if len(subtitle) * 36 * 0.56 <= AVAIL_W else max(24, int(AVAIL_W / (max(len(subtitle), 1) * 0.56)))
    if len(title) * title_px * 0.62 > AVAIL_W + 1 or len(subtitle) * sub_px * 0.56 > AVAIL_W + 1:
        raise OverlayError("that text is too long for one label; shorten the title or subtitle")
    lh = round(44 + title_px * 1.1 + ((8 + sub_px * 1.15) if subtitle else 0))

    gap, margin = 150, 50
    below = by + bh + gap + lh <= H - margin
    ly = by + bh + gap if below else max(margin, by - gap - lh)
    lx = max(margin, min(round(cx - 560), W - LABEL_W - margin))

    tip_x = max(bx + 30, min(cx, bx + bw - 30))
    tip_y = by + bh + 6 if below else by - 6
    tail_y = ly if below else ly + lh
    tail_x = max(lx + 100, min(tip_x - 220, lx + LABEL_W - 100))
    dx, dy = tip_x - tail_x, tip_y - tail_y
    length = math.hypot(dx, dy)
    return {
        "box": {"x": round(bx), "y": round(by), "w": round(bw), "h": round(bh)},
        "label": {"x": lx, "y": round(ly), "w": LABEL_W, "h": lh},
        "arrow": {"x": round(tail_x), "y": round(tail_y), "len": round(length - ARROW_HEAD, 1),
                  "angle": round(math.degrees(math.atan2(dy, dx)), 2), "tip": [round(tip_x), round(tip_y)]},
        "title": title, "subtitle": subtitle, "title_px": title_px, "subtitle_px": sub_px,
    }


def plan(note: dict, timeline: dict, title: str, subtitle: str) -> dict:
    clip, t_note = locate(note, timeline)
    lead, hold = LEAD, HOLD
    start = t_note - lead
    if start < clip["start"]:
        start, lead = clip["start"], t_note - clip["start"]
    total = lead + hold + FADE_OUT + TAIL
    if start + total > clip["end"]:
        hold = clip["end"] - start - lead - FADE_OUT - TAIL
        total = lead + hold + FADE_OUT + TAIL
        if hold < 1.5:
            raise OverlayError("not enough of this shot is left after the note for a callout that can be read")
    cfg = layout(union_bbox(note.get("shapes", [])), title, subtitle)
    cfg.update({"t_in": round(lead, 3), "t_out": round(lead + hold, 3), "fade_out": FADE_OUT, "total": round(total, 3)})
    return {"clip": clip, "t_note": t_note, "start": round(start, 3), "total": round(total, 3), "cfg": cfg}


def cut_excerpt(p: dict, timeline: dict, out: Path) -> None:
    clip, start, total = p["clip"], p["start"], p["total"]
    vsrc = clip["src_in"] + (start - clip["start"])
    piece = next((a for a in timeline.get("audio", []) if abs(a["start"] - clip["start"]) < 0.05), None)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{vsrc:.4f}", "-t", f"{total:.4f}", "-i", clip["source_path"]]
    if piece:
        cmd += ["-ss", f"{piece['src_in'] + (start - piece['start']):.4f}", "-t", f"{total:.4f}", "-i", piece["source_path"],
                "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-vf", f"scale={W}:{H},fps={FPS}", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(out)]
    subprocess.run(cmd, check=True)


def gsap_file() -> Path:
    cached = Path(tempfile.gettempdir()) / f"gsap-{GSAP_VERSION}.min.js"
    if not cached.exists():
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["npm", "pack", f"gsap@{GSAP_VERSION}", "--pack-destination", d], check=True, capture_output=True)
            with tarfile.open(next(Path(d).glob("gsap-*.tgz"))) as t:
                cached.write_bytes(t.extractfile("package/dist/gsap.min.js").read())
    return cached


def write_project(proj: Path, cfg: dict) -> None:
    proj.mkdir(parents=True, exist_ok=True)
    html = (HERE / "overlay_template.html").read_text().replace("__DUR__", str(cfg["total"])).replace(
        "/*__CFG__*/null", json.dumps(cfg).replace("</", "<\\/"))
    (proj / "index.html").write_text(html)
    shutil.copy(gsap_file(), proj / "gsap.min.js")
    (proj / "meta.json").write_text(json.dumps({"id": "overlay", "name": "overlay"}))
    (proj / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True}}, indent=2))


def hf(proj: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HYPERFRAMES_SKIP_SKILLS="1", HYPERFRAMES_NO_TELEMETRY="1")
    return subprocess.run(["npx", "--yes", f"hyperframes@{HF_VERSION}", *args], cwd=proj, capture_output=True, text=True, env=env)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notes", type=Path)
    ap.add_argument("--note", type=int, required=True, help="1-based note number")
    ap.add_argument("--review-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--title", default="The spacer")
    ap.add_argument("--subtitle", default="A piece of cardboard pulled off the box")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    try:
        notes = json.loads(a.notes.read_text())["notes"]
        note = notes[a.note - 1]
        timeline = json.loads((a.review_dir / "timeline.json").read_text())
        p = plan(note, timeline, a.title, a.subtitle)
    except (OverlayError, IndexError, KeyError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1

    a.out.mkdir(parents=True, exist_ok=True)
    proj = a.out / "hyperframes_project"
    excerpt, mov, preview = a.out / "excerpt.mp4", a.out / "overlay.mov", a.out / "overlay_preview.mp4"
    print(f"note {a.note}: V-time {p['t_note']:.2f}s in clip {p['clip']['idx']}; excerpt {p['start']:.2f}s for {p['total']:.2f}s")
    cut_excerpt(p, timeline, excerpt)
    write_project(proj, p["cfg"])

    chk = hf(proj, "check")
    tail = "\n".join(chk.stdout.splitlines()[-12:])
    if "Check passed" not in chk.stdout:
        print("HyperFrames check FAILED:\n" + tail, file=sys.stderr)
        return 1
    print("HyperFrames check passed")
    r = hf(proj, "render", "--format", "mov", "--fps", str(FPS), "-o", str(mov))
    if r.returncode != 0 or not mov.exists():
        print("render FAILED:\n" + (r.stdout + r.stderr)[-1500:], file=sys.stderr)
        return 1
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(excerpt), "-i", str(mov), "-filter_complex",
                    "[0:v][1:v]overlay=format=auto:shortest=1[v]", "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", str(preview)], check=True)

    placement = {"place_overlay_on_timeline_at_sec": p["start"], "duration_sec": p["total"], "overlay": mov.name,
                 "note": a.note, "note_text": note.get("text", ""), "title": a.title, "subtitle": a.subtitle,
                 "hyperframes": HF_VERSION, "gsap": GSAP_VERSION, "geometry": p["cfg"],
                 "region": union_bbox(note.get("shapes", []))}
    (a.out / "placement.json").write_text(json.dumps(placement, indent=2))
    v = subprocess.run([sys.executable, str(HERE / "verify_overlay.py"), str(a.out)], capture_output=True, text=True)
    print(v.stdout.rstrip())
    if v.returncode != 0:
        print(v.stderr, file=sys.stderr)
        return 1
    print(f"\nplace {mov.name} at {p['start']:.2f}s on the timeline; preview: {preview}")
    if a.open:
        subprocess.run(["open", str(preview)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
