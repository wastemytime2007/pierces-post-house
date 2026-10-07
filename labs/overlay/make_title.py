#!/usr/bin/env python3
"""A title card and step labels as ONE transparent layer, in the style of Ryan's wallpaper reel (docs/reference/WALLPAPER_REEL_ANATOMY.md), rendered by HyperFrames and placed by place_overlay.py.

    PRECUT_ROOT=~/precut-checkout python3 labs/overlay/make_title.py --xml "<the export it will go in>" --spec spec.json --out "<folder>"

spec.json (every word is an input; nothing is invented):
    {"title":  {"anchor": {"source": "<file name>", "source_sec": 1181.4}, "small": "THE SUB SAID", "big": "“DONE”", "joke": "(THE BACKYARD DISAGREED)",
                "builds": [0.0, 0.56, 1.12], "hold": 2.7},
     "labels": [{"anchor": {"source": "<file name>", "source_sec": 1190.02}, "text": "The closing", "word_step": 0.2, "until": "clip_end"}, ...]}
The title is a brand-navy field over the picture, with the small white line, the big orange word and the orange parenthetical joke each coming ON at its `builds` offset (seconds after the anchor frame) and all going off
together after `hold`. A label is heavy white caps with a drop shadow, lower left, built a word at a time every `word_step` seconds from its anchor frame, and clears just before its clip ends (`until: "clip_end"`) or after
`until` seconds. Hard on and hard off: the wallpaper reel builds in and clears out, it does not fade. Nothing is placed over the last beat unless the spec asks for it.

The layer is ONE ProRes 4444 .mov with alpha the length of the cut, at the sequence's own size and frame rate (1080x1920 portrait, 1920x1080 or 3840x2160), so `place_overlay.py` drops it on V2 at 0. Every element is anchored to a
SOURCE frame, so after a revision the layer is rebuilt on the new cut (`--xml <new export>`) and each element lands on its own moment. `labs/reconform` does not rebuild it yet.
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE))

import change_callout as cc  # noqa: E402  (timeline_dict)
import make_overlay as mo  # noqa: E402  (hf, gsap_file, locate, HF_VERSION, GSAP_VERSION, OverlayError)
import timeline  # noqa: E402

LABEL_CLEAR = 0.15           # a label goes off this long before its clip ends
MIN_LABEL_SEC = 0.9
PORTRAIT = (1080, 1920)


class TitleError(Exception):
    pass


def sequence_spec(xml: Path) -> dict:
    """The target sequence's size and rate. Portrait 1080x1920 is rendered natively; the others are the overlay family's own."""
    return mo.sequence_spec(xml, allow_portrait=True)


def validate_spec(spec: dict) -> None:
    if not spec.get("title") and not spec.get("labels"):
        raise TitleError("the spec has neither a title nor labels")
    t = spec.get("title")
    if t:
        for k in ("anchor", "small", "big", "builds", "hold"):
            if k not in t:
                raise TitleError(f"title is missing '{k}'")
        if not (isinstance(t["builds"], list) and len(t["builds"]) == 3 and t["builds"] == sorted(t["builds"]) and t["builds"][0] >= 0):
            raise TitleError("title.builds is three offsets in seconds, in order, from 0")
        if t["hold"] <= t["builds"][-1]:
            raise TitleError("title.hold must be longer than the last build, or the last line would never be seen")
    for i, lb in enumerate(spec.get("labels", []), 1):
        if not lb.get("text", "").strip() or "anchor" not in lb:
            raise TitleError(f"label {i} needs 'text' and 'anchor'")


def plan(spec: dict, timeline_d: dict, zone_end: float) -> dict:
    """Every element's time on THIS timeline, from its source anchor. Refuses a moment that is not in the cut and any element that would run past it."""
    out: dict = {"title": None, "labels": [], "label_y": spec.get("label_y")}                 # optional: where the labels sit, as a fraction of the height (the wallpaper reel's own 0.69 in portrait if absent)
    t = spec.get("title")
    if t:
        clip, t0 = mo.locate(t["anchor"], timeline_d)
        times = [round(t0 + b, 4) for b in t["builds"]]
        off = round(t0 + t["hold"], 4)
        if off > zone_end + 1e-6:
            raise TitleError(f"the title would run to {off:.2f}s, past the end of the cut ({zone_end:.2f}s)")
        out["title"] = {"anchor_time": round(t0, 4), "on": times, "off": off, "small": t["small"], "big": t["big"], "joke": t.get("joke", "")}
    for i, lb in enumerate(spec.get("labels", []), 1):
        clip, t0 = mo.locate(lb["anchor"], timeline_d)
        until = lb.get("until", "clip_end")
        off = round(clip["end"] - LABEL_CLEAR, 4) if until == "clip_end" else round(t0 + float(until), 4)
        if off - t0 < MIN_LABEL_SEC:
            raise TitleError(f"label {i} ('{lb['text']}') would be on screen {off - t0:.2f}s, under {MIN_LABEL_SEC}s: too short to read")
        if off > zone_end + 1e-6:
            raise TitleError(f"label {i} would run past the end of the cut")
        words = lb["text"].upper().split()
        step = float(lb.get("word_step", 0.2))
        wt = [round(t0 + k * step, 4) for k in range(len(words))]
        if wt[-1] >= off:
            raise TitleError(f"label {i} ('{lb['text']}'): the last word would come on at {wt[-1]:.2f}s, after the label clears at {off:.2f}s")
        out["labels"].append({"anchor_time": round(t0, 4), "words": words, "word_times": wt, "on": round(t0, 4), "off": off, "text": lb["text"]})
    spans = sorted([(out["title"]["on"][0], out["title"]["off"], "title")] if out["title"] else [])
    spans += [(lb["on"], lb["off"], f"label '{lb['text']}'") for lb in out["labels"]]
    spans.sort()
    for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
        if b0 < a1 - 1e-6:
            raise TitleError(f"{an} and {bn} would be on screen together ({a0:.2f}-{a1:.2f}s and {b0:.2f}-{b1:.2f}s)")
    return out


def layout(w: int, h: int, label_y: float | None = None) -> dict:
    """Sizes as fractions of the canvas, so the same spec reads right at 1080x1920, 1920x1080 and 3840x2160. Proportions are those measured off the wallpaper reel's labels and the title drawn for Reel 3."""
    portrait = h > w
    u = w if portrait else h * 9 / 16                 # the unit: the frame's short side at 9:16
    return {"label_px": round(u * 0.072), "label_x": round(u * 0.065), "label_y": round(h * (label_y if label_y is not None else (0.69 if portrait else 0.74))), "label_w": round(w * (0.72 if portrait else 0.5)),
            "small_px": round(u * 0.065), "small_y": round(h * 0.395), "big_px": round(u * 0.23), "big_y": round(h * 0.447), "joke_px": round(u * 0.054), "joke_y": round(h * 0.62)}


def body_html(p: dict, g: dict) -> tuple[str, list[dict]]:
    """The static elements and the on/off events that drive them."""
    parts, ev = [], []

    dur = "__DUR__"
    if p["title"]:
        t = p["title"]
        parts.append(f'      <div id="field" class="hf-field clip" data-start="0" data-duration="{dur}"></div>')
        ev.append({"sel": "#field", "on": t["on"][0], "off": t["off"]})
        for k, (key, cls, px, y) in enumerate((("small", "hf-small", g["small_px"], g["small_y"]), ("big", "hf-big", g["big_px"], g["big_y"]), ("joke", "hf-joke", g["joke_px"], g["joke_y"]))):
            if not t[key]:
                continue
            parts.append(f'      <div id="t_{key}" class="hf-line {cls} clip" data-start="0" data-duration="{dur}" style="top:{y}px;font-size:{px}px">{html.escape(t[key])}</div>')
            ev.append({"sel": f"#t_{key}", "on": t["on"][k], "off": t["off"]})
    for i, lb in enumerate(p["labels"]):
        spans = "".join(f'<span id="l{i}w{k}" class="hf-word">{html.escape(w)}</span>' for k, w in enumerate(lb["words"]))
        parts.append(f'      <div id="l{i}" class="hf-label clip" data-start="0" data-duration="{dur}" style="left:{g["label_x"]}px;top:{g["label_y"]}px;width:{g["label_w"]}px;font-size:{g["label_px"]}px">{spans}</div>')
        for k, tw in enumerate(lb["word_times"]):
            ev.append({"sel": f"#l{i}w{k}", "on": tw, "off": lb["off"]})
    return "\n".join(parts), ev


def write_project(proj: Path, p: dict, w: int, h: int, total: float) -> dict:
    g = layout(w, h, p.get("label_y"))
    body, ev = body_html(p, g)
    proj.mkdir(parents=True, exist_ok=True)
    page = ((HERE / "title_template.html").read_text().replace("__W__", str(w)).replace("__H__", str(h)).replace("__DUR__", str(total)).replace("__BODY__", body.replace("__DUR__", str(total)))
            .replace("/*__CFG__*/null", json.dumps(ev).replace("</", "<\\/")))
    (proj / "index.html").write_text(page)
    (proj / "gsap.min.js").write_bytes(mo.gsap_file().read_bytes())
    (proj / "meta.json").write_text(json.dumps({"id": "title", "name": "title"}))
    (proj / "hyperframes.json").write_text(json.dumps({"$schema": "https://hyperframes.heygen.com/schema/hyperframes.json", "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
                                                      "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"}, "media": {"autoProxy": True}}, indent=2))
    return {"layout": g, "events": ev}


def build_title(xml: Path, spec: dict, out: Path, render=None, spec_of=sequence_spec) -> tuple[int, dict | None]:
    validate_spec(spec)
    seq = spec_of(xml)
    cut = timeline.load_cut(xml)
    p = plan(spec, cc.timeline_dict(cut), cut.zone_end)
    total = round(cut.zone_end, 4)
    out.mkdir(parents=True, exist_ok=True)
    proj, mov = out / "hyperframes_project", out / "title.mov"
    info = write_project(proj, p, seq["width"], seq["height"], total)
    n_labels = len(p["labels"])
    print(f"title layer: {seq['width']}x{seq['height']}, {total:.2f}s, " + (f"title at {p['title']['on'][0]:.2f}s, " if p["title"] else "") + f"{n_labels} label(s)")
    if render is None:
        chk = mo.hf(proj, "check")
        if "Check passed" not in chk.stdout:
            print("HyperFrames check FAILED:\n" + "\n".join(chk.stdout.splitlines()[-12:]), file=sys.stderr)
            return 1, None
        print("HyperFrames check passed")
        rargs = ["render", "--format", "mov", "--fps", seq["fps_arg"], "-o", str(mov)]
        if seq["resolution"]:
            rargs += ["--resolution", seq["resolution"]]
        r = mo.hf(proj, *rargs)
        if r.returncode != 0 or not mov.exists():
            print("render FAILED:\n" + (r.stdout + r.stderr)[-1500:], file=sys.stderr)
            return 1, None
    else:
        render(proj, mov, seq, info)
    placement = {"kind": "title_layer", "place_overlay_on_timeline_at_sec": 0.0, "duration_sec": total, "overlay": mov.name, "overlay_path": str(mov.resolve()),
                 "render": {"width": seq["width"], "height": seq["height"], "fps": seq["fps"], "fps_arg": seq["fps_arg"]},
                 "anchors": [{"what": "title", **spec["title"]["anchor"]}] * bool(spec.get("title")) + [{"what": f"label: {lb['text']}", **lb["anchor"]} for lb in spec.get("labels", [])],
                 "plan": p, "layout": info["layout"], "events": info["events"], "hyperframes": mo.HF_VERSION, "gsap": mo.GSAP_VERSION}
    (out / "placement.json").write_text(json.dumps(placement, indent=2))
    if render is None:
        v = subprocess.run([sys.executable, str(HERE / "verify_title.py"), str(out)], capture_output=True, text=True)
        print(v.stdout.rstrip())
        if v.returncode != 0:
            print(v.stderr, file=sys.stderr)
            return 1, placement
    print(f"\nplace {mov.name} on V2 at 0 with place_overlay.py \"{xml.name}\" \"{out}\" --out <new.xml>")
    return 0, placement


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    try:
        code, _p = build_title(a.xml, json.loads(a.spec.read_text()), a.out)
    except (TitleError, mo.OverlayError, timeline.TimelineError, OSError, json.JSONDecodeError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
