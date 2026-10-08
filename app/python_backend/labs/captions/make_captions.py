#!/usr/bin/env python3
"""Captions from a cut's own audio, as a transparent layer over untouched footage.

    PRECUT_ROOT=~/precut-checkout python3 labs/captions/make_captions.py \
        --xml "<export.xml>" --out "<folder>" --start 0 --end 22 \
        [--avoid "<overlay folder>"] [--style pill|plain] [--open]

The words come from transcribing the cut's rendered audio (PreCut's own Transcriber), so timing
is what a viewer hears across every seam. Captions are the speech verbatim: no rewording, and no
em or en dashes (they read as machine-processed in a transcribed quote). Each caption group is
placed at the bottom, or at the top when an overlay callout would collide with it. The layer is
rendered by HyperFrames as transparent ProRes 4444 at the sequence's own size and frame rate.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "overlay"))
sys.path.insert(0, str(HERE.parent / "review_loop"))
import make_overlay as mo  # noqa: E402  (hf(), gsap_file(), sequence_spec(), constants)
import timeline  # noqa: E402
import words as words_mod  # noqa: E402

W, H = 1920, 1080                      # layout space; the render scales it
FONT, PADX, PADY, MARGIN, MAXW = 58, 30, 14, 90, 1500
GROUP_WORDS, GROUP_CHARS = 6, 34
# The canvas a layout is drawn on. Landscape is the original. Portrait (a 1080x1920 reel): bigger type, narrower lines of at most two rows, and the bottom margin kept clear of the platform's own buttons and captions.
LAYOUTS = {"landscape": {"W": 1920, "H": 1080, "FONT": 58, "PADX": 30, "PADY": 14, "MARGIN": 90, "MAXW": 1500, "GROUP_WORDS": 6, "GROUP_CHARS": 34},
           "portrait": {"W": 1080, "H": 1920, "FONT": 76, "PADX": 28, "PADY": 16, "MARGIN": 380, "MAXW": 940, "GROUP_WORDS": 5, "GROUP_CHARS": 28}}


def use_layout(width: int, height: int) -> str:
    """Select the layout for a sequence size (portrait if taller than wide) and set the module's layout constants to it. Returns its name."""
    global W, H, FONT, PADX, PADY, MARGIN, MAXW, GROUP_WORDS, GROUP_CHARS
    name = "portrait" if height > width else "landscape"
    L = LAYOUTS[name]
    W, H, FONT, PADX, PADY, MARGIN, MAXW, GROUP_WORDS, GROUP_CHARS = (L[k] for k in ("W", "H", "FONT", "PADX", "PADY", "MARGIN", "MAXW", "GROUP_WORDS", "GROUP_CHARS"))
    return name
CHAR_W = 0.58                          # average bold Inter glyph width, in em
STYLES = {
    "pill": "background: rgba(3, 52, 89, 0.82); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);",
    "plain": "text-shadow: 0 3px 14px rgba(3, 52, 89, 0.95), 0 0 3px rgba(0, 0, 0, 0.7);",
}
DASHES = re.compile(r"[—–]")


class CaptionError(Exception):
    pass


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bleep"))
import bleep as _bleep  # noqa: E402  (the word list: listed words are starred in the caption, because they are bleeped in the sound)
_PATS = _bleep.load_patterns()


@dataclass
class Word:
    text: str
    start: float
    end: float


def sanitize(text: str) -> tuple[str, int]:
    """Dashes become commas. A transcribed quote with an em dash reads as machine-processed."""
    n = len(DASHES.findall(text))
    out = re.sub(r"\s*[—–]\s*", ", ", text)
    out = re.sub(r",(\s*,)+", ",", out)                 # a dash next to a comma is one comma
    out = re.sub(r"^[,\s]+", "", re.sub(r"\s+", " ", out).strip())   # a dash on its own leaves nothing
    return out, n


def clean_words(ws, censor: bool = True) -> tuple[list[Word], int]:
    out, dashes = [], 0
    for w in ws:
        t, n = sanitize(w.text)
        dashes += n
        if t:
            out.append(Word(_bleep.censor(t, _PATS) if censor else t, float(w.start), float(w.end)))
    return out, dashes


def tidy_case(new: str, old: str) -> str:
    """The wording comes from the note; the casing follows the line it replaces: a capital first letter if the old line had one,
    a standalone 'i' written 'I', the old line's closing punctuation kept if the new one has none."""
    t = re.sub(r"\s+", " ", new.strip().strip('"\u201c\u201d'))
    t = re.sub(r"\bi\b", "I", t)
    if old[:1].isupper():
        t = t[:1].upper() + t[1:]
    if old.rstrip()[-1:] in ".?!," and t[-1:] not in ".?!,":
        t += old.rstrip()[-1]
    return t


def apply_fixes(groups: list[dict], fixes: list[dict], start: float) -> list[dict]:
    """Replace the words of the caption line showing at each fix's time (window-relative `start` is subtracted). The new words are spread
    over the old line's spoken span in proportion to their length, so the word-by-word highlight still runs from the first word to the last."""
    for fx in fixes:
        t = float(fx["at"]) - start
        hit = [g for g in groups if g["show_start"] - 0.05 <= t <= g["show_end"] + 0.05]
        if not hit:
            raise CaptionError(f"no caption line is showing at {fx['at']:.2f}s, so \"{fx['text']}\" has no line to replace")
        g = min(hit, key=lambda g: abs(t - (g["words"][0].start + g["words"][-1].end) / 2))
        text = tidy_case(fx["text"], g["text"])
        toks = text.split(" ")
        a, b = g["words"][0].start, g["words"][-1].end
        weights = np.array([max(len(x), 2) for x in toks], dtype=float)
        edges = np.r_[0.0, np.cumsum(weights) / weights.sum()] * (b - a) + a
        g["words"] = [Word(tok, float(edges[i]), float(edges[i + 1])) for i, tok in enumerate(toks)]
        g["text"] = " ".join(tok for tok in toks)
        g["fixed_from"] = fx.get("was") or ""
    return groups


FRAGMENT_SEC = 0.35        # a one-word line on screen for less than this cannot be read


def _fold_fragments(groups: list[list[Word]], max_words: int, max_chars: int, gap: float) -> list[list[Word]]:
    """A caption line that is one word spoken in under FRAGMENT_SEC (a trailing "it." after a sentence break) is joined to the line before it, or failing that the line after, when they are close
    in time and the joined line still fits. A line nobody can read is worse than a slightly longer one."""
    out = [list(g) for g in groups]
    i = 0
    while i < len(out):
        g = out[i]
        if len(g) == 1 and g[0].end - g[0].start < FRAGMENT_SEC and len(out) > 1:
            prev_ok = i > 0 and g[0].start - out[i - 1][-1].end <= gap and len(out[i - 1]) < max_words + 2 and len(" ".join(x.text for x in out[i - 1] + g)) <= max_chars + 6
            next_ok = i + 1 < len(out) and out[i + 1][0].start - g[0].end <= gap and len(out[i + 1]) < max_words + 2 and len(" ".join(x.text for x in g + out[i + 1])) <= max_chars + 6
            if prev_ok:
                out[i - 1] += g
                del out[i]
                continue
            if next_ok:
                out[i + 1] = g + out[i + 1]
                del out[i]
                continue
        i += 1
    return out


def group_words(ws: list[Word], max_words: int = 6, max_chars: int = 34, max_dur: float = 2.8, gap: float = 0.4) -> list[dict]:
    """Break the speech into caption lines at sentence ends, pauses, commas after a phrase, and size limits."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in ws:
        if cur:
            joined = " ".join(x.text for x in cur + [w])
            last = cur[-1].text
            if (w.start - cur[-1].end > gap or last.endswith((".", "?", "!")) or (last.endswith(",") and len(cur) >= 3)
                    or len(cur) >= max_words or len(joined) > max_chars or w.end - cur[0].start > max_dur):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    groups = _fold_fragments(groups, max_words, max_chars, gap)

    out = [{"words": g, "text": " ".join(x.text for x in g)} for g in groups]
    for k, g in enumerate(out):
        g["show_start"] = max(0.0, g["words"][0].start - 0.05)
    for k, g in enumerate(out):
        last_end = g["words"][-1].end
        nxt = out[k + 1]["show_start"] if k + 1 < len(out) else float("inf")
        g["show_end"] = max(last_end + 0.03, min(last_end + 0.30, nxt - 0.02))
        if k + 1 < len(out):
            out[k + 1]["show_start"] = max(out[k + 1]["show_start"], g["show_end"])
            out[k + 1]["show_start"] = min(out[k + 1]["show_start"], out[k + 1]["words"][0].start)
    return out


def est_box(text: str, font: int = FONT) -> tuple[float, float]:
    inner = MAXW - 2 * PADX
    text_w = len(text) * font * CHAR_W + max(0, len(text.split()) - 1) * font * 0.28
    lines = max(1, math.ceil(text_w / inner))
    return min(MAXW, text_w / lines + 2 * PADX) if lines == 1 else MAXW, lines * font * 1.22 + 2 * PADY


def _rect(pos: str, text: str) -> tuple[float, float, float, float]:
    w, h = est_box(text)
    return ((W - w) / 2, (H - MARGIN - h) if pos == "bottom" else MARGIN, w, h)


def _hit(a, b) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def blocked_from_overlay(pl: dict, pad: float = 20) -> dict:
    """Times and screen rectangles (1920x1080 layout space) an overlay callout occupies."""
    g, start = pl["geometry"], pl["place_overlay_on_timeline_at_sec"]
    a = g["arrow"]
    tx, ty = a["tip"]
    rects = [(g["box"]["x"], g["box"]["y"], g["box"]["w"], g["box"]["h"]),
             (g["label"]["x"], g["label"]["y"], g["label"]["w"], g["label"]["h"]),
             (min(a["x"], tx), min(a["y"], ty), abs(tx - a["x"]), abs(ty - a["y"]))]
    rects = [(x - pad, y - pad, w + 2 * pad, h + 2 * pad) for x, y, w, h in rects]
    return {"t0": start + g["t_in"] - 0.05, "t1": start + g["t_out"] + g["fade_out"], "rects": rects}


def blocked_from_title(pl: dict, pad: float = 20) -> list[dict]:
    """The same for a title layer (labs/overlay/make_title.py): while the title card is on, the band its text sits in; while a name tag is on, the tag's own box (lower left). The card's navy field
    is translucent and covers the frame by design, so captions may sit over it, but never over its words."""
    import make_title as mt
    g = pl.get("layout") or mt.layout(W, H, (pl.get("plan") or {}).get("label_y"))               # the layout the title was actually drawn with
    start = pl["place_overlay_on_timeline_at_sec"]
    plan = pl.get("plan") or {}
    out: list[dict] = []
    t = plan.get("title")
    if t:
        bottoms = []
        if t.get("small"):
            bottoms.append(g["small_y"] + g["small_px"] * 3.2)             # a long topic wraps to up to three lines
        if t.get("big"):
            bottoms.append(g["big_y"] + g["big_px"] * 1.2)
        if t.get("joke"):
            bottoms.append(g["joke_y"] + g["joke_px"] * 1.4)
        top = g["small_y"] if t.get("small") else g["big_y"]
        out.append({"t0": start + t["on"][0] - 0.05, "t1": start + t["off"], "rects": [(0 - pad, top - pad, W + 2 * pad, max(bottoms or [top + 100]) - top + 2 * pad)]})
    for lb in plan.get("labels", []):
        out.append({"t0": start + lb["on"] - 0.05, "t1": start + lb["off"], "rects": [(g["label_x"] - pad, g["label_y"] - pad, g["label_w"] + 2 * pad, g["label_px"] * 1.4 + 2 * pad)]})
    for lt in plan.get("lower_thirds", []):
        out.append({"t0": start + lt["on"] - 0.05, "t1": start + lt["off"], "rects": [(g["lt_x"] - pad, g["lt_y"] - pad, lt.get("w", g["lt_h"] * 3) + 2 * pad, lt.get("h", g["lt_h"]) + 2 * pad)]})
    return out


def blocked_for(pl: dict) -> list[dict]:
    return blocked_from_title(pl) if pl.get("kind") == "title_layer" else [blocked_from_overlay(pl)]


def pick_position(g: dict, blocked: list[dict], offset: float) -> tuple[str, bool]:
    """'bottom' unless an overlay is on screen there at the same time; then 'top'. Flags a collision if both are blocked."""
    t0, t1 = g["show_start"] + offset, g["show_end"] + offset
    live = [b for b in blocked if b["t0"] < t1 and t0 < b["t1"]]
    for pos in ("bottom", "top"):
        r = _rect(pos, g["text"])
        if not any(_hit(r, q) for b in live for q in b["rects"]):
            return pos, False
    return "bottom", True


def build_groups_html(groups: list[dict], dur: float) -> str:
    rows = []
    for i, g in enumerate(groups):
        spans = "".join(f'<span class="w"><span class="b">{html.escape(w.text)}</span>'
                        f'<span class="h" data-layout-allow-overlap>{html.escape(w.text)}</span></span>' for w in g["words"])
        rows.append(f'      <div id="g{i}" class="cap {g["pos"]} clip" data-start="0" data-duration="{dur}">{spans}</div>')
    return "\n".join(rows)


def write_project(proj: Path, groups: list[dict], dur: float, style: str) -> None:
    proj.mkdir(parents=True, exist_ok=True)
    cfg = {"groups": [{"pos": g["pos"], "show_start": round(g["show_start"], 3), "show_end": round(g["show_end"], 3),
                       "words": [{"start": round(w.start, 3), "end": round(w.end, 3)} for w in g["words"]]} for g in groups]}
    page = (HERE / "caption_template.html").read_text()
    for k, v in {"__CW__": str(W), "__CH__": str(H), "__DUR__": str(round(dur, 3)), "__MARGIN__": str(MARGIN), "__MAXW__": str(MAXW), "__FONT__": str(FONT),
                 "__PADX__": str(PADX), "__PADY__": str(PADY), "__PILL__": STYLES[style]}.items():
        page = page.replace(k, v)
    page = page.replace("__GROUPS__", build_groups_html(groups, dur)).replace("/*__CFG__*/null", json.dumps(cfg))
    (proj / "index.html").write_text(page)
    (proj / "gsap.min.js").write_bytes(mo.gsap_file().read_bytes())
    (proj / "meta.json").write_text(json.dumps({"id": "captions", "name": "captions"}))
    (proj / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
        "media": {"autoProxy": True}}, indent=2))


def transcribe_window(media: Path, start: float, dur: float) -> list[Word]:
    ws, _ = clean_words(words_mod.words_in(str(media), start, dur))
    # words_in returns times in the file's own clock; the composition clock starts at the window
    return [Word(w.text, w.start - start, w.end - start) for w in ws if w.end > start and w.start < start + dur]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True, help="the export the captions will go in")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--avoid", type=Path, action="append", default=[], help="an overlay folder (placement.json) to keep clear of")
    ap.add_argument("--style", choices=list(STYLES), default="pill")
    ap.add_argument("--fixes", type=Path, help="a JSON list of {at: timeline seconds, text: the wording the line should have}; kept across rebuilds")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()

    try:
        spec = mo.sequence_spec(a.xml, allow_portrait=True)
        layout_name = use_layout(spec["width"], spec["height"])
        cut = timeline.load_cut(a.xml)
        if not 0 <= a.start < a.end <= cut.zone_end + 0.01:
            raise CaptionError(f"the window {a.start}-{a.end}s is outside the cut (0-{cut.zone_end:.2f}s)")
        avoid = [json.loads((f / "placement.json").read_text()) for f in a.avoid]
    except (CaptionError, mo.OverlayError, timeline.TimelineError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1

    a.out.mkdir(parents=True, exist_ok=True)
    dur = round(a.end - a.start, 3)
    base = a.out / "cut_1080.mp4"
    if not base.exists():
        from render_preview import render_preview
        print("rendering a 1080p proxy of the cut (used for the transcript and the preview)...")
        render_preview(cut, base, height=H)

    ws = transcribe_window(base, a.start, dur)
    if not ws:
        print("REFUSING: no speech found in that window", file=sys.stderr)
        return 1
    groups = group_words(ws, max_words=GROUP_WORDS, max_chars=GROUP_CHARS)
    fixes = json.loads(a.fixes.read_text()) if a.fixes and a.fixes.exists() else []
    try:
        for g in groups:
            g["was"] = g["text"]
        groups = apply_fixes(groups, fixes, a.start)
    except CaptionError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    blocked = [b for pl in avoid for b in blocked_for(pl)]
    collisions = 0
    for g in groups:
        g["pos"], flagged = pick_position(g, blocked, a.start)
        collisions += flagged
    print(f"{len(ws)} words -> {len(groups)} caption lines; {sum(g['pos'] == 'top' for g in groups)} moved to the top to clear a callout"
          + (f"; {collisions} could not avoid one" if collisions else ""))

    proj, mov, preview = a.out / "hyperframes_project", a.out / "captions.mov", a.out / "captions_preview.mp4"
    write_project(proj, groups, dur, a.style)
    chk = mo.hf(proj, "check")
    if "Check passed" not in chk.stdout:
        print("HyperFrames check FAILED:\n" + "\n".join(chk.stdout.splitlines()[-14:]), file=sys.stderr)
        return 1
    print("HyperFrames check passed")
    rargs = ["render", "--format", "mov", "--fps", spec["fps_arg"], "-o", str(mov)]
    if spec["resolution"]:
        rargs += ["--resolution", spec["resolution"]]
    r = mo.hf(proj, *rargs)
    if r.returncode != 0 or not mov.exists():
        print("render FAILED:\n" + (r.stdout + r.stderr)[-1500:], file=sys.stderr)
        return 1

    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{a.start}", "-t", f"{dur}", "-i", str(base)]
    layers = [(mov, 0.0)] + [(Path(pl["overlay_path"]), pl["place_overlay_on_timeline_at_sec"] - a.start) for pl in avoid]
    for path, off in layers:
        cmd += ["-itsoffset", f"{off}", "-i", str(path)] if off else ["-i", str(path)]
    graph, prev = [], "0:v"
    for i, _ in enumerate(layers, start=1):
        graph.append(f"[{i}:v]scale={W}:{H},fps=30[l{i}]")
    for i in range(len(layers), 0, -1):                       # callouts first, captions on top
        graph.append(f"[{prev}][l{i}]overlay=eof_action=pass:format=auto[o{i}]")
        prev = f"o{i}"
    subprocess.run(cmd + ["-filter_complex", ";".join(graph), "-map", f"[{prev}]", "-map", "0:a", "-c:v", "libx264", "-crf", "17",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-t", f"{dur}", str(preview)], check=True)

    (a.out / "captions.json").write_text(json.dumps({
        "window": {"start": a.start, "end": a.end}, "style": a.style, "fixes": fixes,
        "groups": [{"text": g["text"], "pos": g["pos"], "show_start": g["show_start"], "show_end": g["show_end"],
                    "words": [{"text": w.text, "start": w.start, "end": w.end} for w in g["words"]]} for g in groups]}, indent=2))
    (a.out / "placement.json").write_text(json.dumps({
        "kind": "captions", "layout": {"name": layout_name, "w": W, "h": H}, "place_overlay_on_timeline_at_sec": a.start, "duration_sec": dur, "overlay": mov.name,
        "overlay_path": str(mov.resolve()), "render": {"width": spec["width"], "height": spec["height"], "fps": spec["fps"], "fps_arg": spec["fps_arg"]},
        "hyperframes": mo.HF_VERSION, "avoid": [str(f) for f in a.avoid]}, indent=2))

    v = subprocess.run([sys.executable, str(HERE / "verify_captions.py"), str(a.out)], capture_output=True, text=True)
    print(v.stdout.rstrip())
    if v.returncode != 0:
        print(v.stderr, file=sys.stderr)
        return 1
    print(f"\ncaptions.mov goes at {a.start:.2f}s on the timeline; preview: {preview}")
    if a.open:
        subprocess.run(["open", str(preview)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
