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
import copy
import re
import html
import json
import math
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
LT_MIN_SEC = 1.4             # a lower third on screen less than this cannot be read
LT_WIPE_IN, LT_WIPE_OUT = 0.38, 0.3
MIN_LABEL_SEC = 0.9
PORTRAIT = (1080, 1920)


class TitleError(Exception):
    pass


def sequence_spec(xml: Path) -> dict:
    """The target sequence's size and rate. Portrait 1080x1920 is rendered natively; the others are the overlay family's own."""
    return mo.sequence_spec(xml, allow_portrait=True)


def validate_spec(spec: dict) -> None:
    if not spec.get("title") and not spec.get("labels") and not spec.get("lower_thirds"):
        raise TitleError("the spec has neither a title, labels nor lower thirds")
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
    for i, lt in enumerate(spec.get("lower_thirds", []), 1):
        if not str(lt.get("name", "")).strip() or "anchor" not in lt:
            raise TitleError(f"lower third {i} needs 'name' and 'anchor'")


def plan(spec: dict, timeline_d: dict, zone_end: float) -> dict:
    """Every element's time on THIS timeline, from its source anchor. Refuses a moment that is not in the cut and any element that would run past it."""
    out: dict = {"title": None, "labels": [], "lower_thirds": [], "label_y": spec.get("label_y"), "lt_y": spec.get("lt_y")}                 # optional: where the labels sit, as a fraction of the height (the wallpaper reel's own 0.69 in portrait if absent)
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
    for i, lt in enumerate(spec.get("lower_thirds", []), 1):
        clip, t0 = mo.locate(lt["anchor"], timeline_d)
        off = min(round(t0 + float(lt.get("until", 3.0)), 4), round(clip["end"] - LABEL_CLEAR, 4))
        if lt.get("past_clip"):                                                  # the person asked for it to stay up longer (2026-10-13): that outranks clearing at its clip's end, which silently ate the request on a short clip
            off = max(off, min(round(t0 + float(lt.get("until", 3.0)), 4), round(zone_end - LABEL_CLEAR, 4)))
        if off - t0 < LT_MIN_SEC:
            raise TitleError(f"lower third {i} ('{lt['name']}') would be on screen {off - t0:.2f}s, under {LT_MIN_SEC}s: too short to read")
        if off > zone_end + 1e-6:
            raise TitleError(f"lower third {i} would run past the end of the cut")
        out["lower_thirds"].append({"anchor_time": round(t0, 4), "on": round(t0, 4), "off": off, "name": str(lt["name"]).strip(), "sub": str(lt.get("sub", "")).strip(), "key": lt.get("key")})
    for lt_in, lt_out in zip(spec.get("lower_thirds", []), out["lower_thirds"]):          # a tag kept up past its clip stops where the next graphic comes on
        if lt_in.get("past_clip"):
            later = [o["on"] for o in out["lower_thirds"] if o["on"] > lt_out["on"] + 1e-6] + [lb["on"] for lb in out["labels"] if lb["on"] > lt_out["on"] + 1e-6]
            if later:
                lt_out["off"] = min(lt_out["off"], round(min(later) - 0.1, 4))
    spans = sorted([(out["title"]["on"][0], out["title"]["off"], "title")] if out["title"] else [])
    spans += [(lb["on"], lb["off"], f"label '{lb['text']}'") for lb in out["labels"]]
    spans += [(lt["on"], lt["off"], f"lower third '{lt['name']}'") for lt in out["lower_thirds"]]
    spans.sort()
    for (a0, a1, an), (b0, b1, bn) in zip(spans, spans[1:]):
        if b0 < a1 - 1e-6:
            raise TitleError(f"{an} and {bn} would be on screen together ({a0:.2f}-{a1:.2f}s and {b0:.2f}-{b1:.2f}s)")
    return out


SUB_LINE_H = 1.3          # a wrapped second line's height, as a multiple of its font size


def lt_text_max(g: dict) -> int:
    """The widest the words of a lower third may be: the frame less the same margin at both sides, less the accent bar and the padding."""
    return g["lt_max_w"] - g["lt_bar"] - g["lt_pad_l"] - g["lt_pad_r"]


def lt_box(g: dict, name: str, sub: str) -> tuple[int, int]:
    """The width and height a lower third occupies (estimated from its text; the panel is as wide as its longer line plus the accent bar and padding). A second line longer than the frame has room for
    wraps onto more lines and the panel grows taller (2026-10-15: the call to action's reason, 50 characters, ran off the right edge of the frame: "...MIGHT HAV")."""
    sub_w = len(sub) * g["lt_sub_px"] * 0.8 if sub else 0               # uppercase, letter-spaced: 0.8 of its size a character, a little over the measured 0.74
    lines = max(1, math.ceil(sub_w / lt_text_max(g))) if sub else 1
    text_w = max(len(name) * g["lt_name_px"] * 0.64, min(sub_w, lt_text_max(g)))
    return round(g["lt_bar"] + g["lt_pad_l"] + text_w + g["lt_pad_r"]), g["lt_h"] + (lines - 1) * round(g["lt_sub_px"] * SUB_LINE_H)


def layout(w: int, h: int, label_y: float | None = None, lt_y: float | None = None) -> dict:
    """Sizes as fractions of the canvas, so the same spec reads right at 1080x1920, 1920x1080 and 3840x2160. Proportions are those measured off the wallpaper reel's labels and the title drawn for Reel 3."""
    portrait = h > w
    u = w if portrait else h * 9 / 16                 # the unit: the frame's short side at 9:16
    return {"lt_x": round(u * 0.065), "lt_y": round(h * (lt_y if lt_y is not None else (0.55 if portrait else 0.7))), "lt_name_px": round(u * 0.066), "lt_sub_px": round(u * 0.031), "lt_bar": round(u * 0.012), "lt_max_w": w - 2 * round(u * 0.065),
            "lt_pad_l": round(u * 0.026), "lt_pad_r": round(u * 0.034), "lt_h": round(u * 0.166),
            "label_px": round(u * 0.072), "label_x": round(u * 0.065), "label_y": round(h * (label_y if label_y is not None else (0.69 if portrait else 0.74))), "label_w": round(w * (0.72 if portrait else 0.5)),
            "small_px": round(u * 0.065), "small_y": round(h * 0.395), "big_px": round(u * 0.23), "big_y": round(h * 0.447), "joke_px": round(u * 0.054), "joke_y": round(h * 0.62)}


def body_html(p: dict, g: dict) -> tuple[str, list[dict]]:
    """The static elements and the on/off events that drive them."""
    parts, ev = [], []

    dur = "__DUR__"
    if p["title"]:
        t = p["title"]
        if t.get("field", True):
            parts.append(f'      <div id="field" class="hf-field clip" data-start="0" data-duration="{dur}"></div>')
            ev.append({"sel": "#field", "on": t["on"][0], "off": t["off"]})
        for k, (key, cls, px, y) in enumerate((("small", "hf-small", g["small_px"], g["small_y"]), ("big", "hf-big", g["big_px"], g["big_y"]), ("joke", "hf-joke", g["joke_px"], g["joke_y"]))):
            if not t[key]:
                continue
            parts.append(f'      <div id="t_{key}" class="hf-line {cls} clip" data-start="0" data-duration="{dur}" style="top:{y}px;font-size:{px}px">{html.escape(t[key])}</div>')
            ev.append({"sel": f"#t_{key}", "on": t["on"][k], "off": t["off"]})
    for i, lt in enumerate(p.get("lower_thirds", [])):
        box_h = lt.get("h", g["lt_h"])                                       # taller than the usual panel when the second line wraps
        sub = (f'<div class="lt-sub" style="font-size:{g["lt_sub_px"]}px;white-space:normal;max-width:{lt_text_max(g)}px;line-height:{SUB_LINE_H}">{html.escape(lt["sub"])}</div>') if lt["sub"] else ""
        parts.append(f'      <div id="lt{i}" class="hf-lt clip" data-start="0" data-duration="{dur}" style="left:{g["lt_x"]}px;top:{g["lt_y"]}px;height:{box_h}px">'
                     f'<div class="lt-panel" style="height:{box_h}px"><div class="lt-bar" style="width:{g["lt_bar"]}px"></div>'
                     f'<div class="lt-text" style="padding-left:{g["lt_pad_l"]}px;padding-right:{g["lt_pad_r"]}px"><div class="lt-name" style="font-size:{g["lt_name_px"]}px">{html.escape(lt["name"])}</div>'
                     f'<div class="lt-rule"></div>{sub}</div></div></div>')
        ev.append({"sel": f"#lt{i}", "on": lt["on"], "off": lt["off"], "kind": "lt", "wipe_in": LT_WIPE_IN, "wipe_out": LT_WIPE_OUT})
    for i, lb in enumerate(p["labels"]):
        spans = "".join(f'<span id="l{i}w{k}" class="hf-word">{html.escape(w)}</span>' for k, w in enumerate(lb["words"]))
        parts.append(f'      <div id="l{i}" class="hf-label clip" data-start="0" data-duration="{dur}" style="left:{g["label_x"]}px;top:{g["label_y"]}px;width:{g["label_w"]}px;font-size:{g["label_px"]}px">{spans}</div>')
        for k, tw in enumerate(lb["word_times"]):
            ev.append({"sel": f"#l{i}w{k}", "on": tw, "off": lb["off"]})
    return "\n".join(parts), ev


def write_project(proj: Path, p: dict, w: int, h: int, total: float) -> dict:
    g = layout(w, h, p.get("label_y"), p.get("lt_y"))
    for lt in p.get("lower_thirds", []):
        lt["w"], lt["h"] = lt_box(g, lt["name"], lt["sub"])                                     # recorded so the captions can keep clear of exactly this box
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


def anchors_of(spec: dict) -> list[dict]:
    """The source frame every element of the spec is tied to. A spec with no title card (a person's note took it out) has no title anchor: the title is optional, as validate_spec allows."""
    return (([{"what": "title", **spec["title"]["anchor"]}] if spec.get("title") else []) + [{"what": f"label: {lb['text']}", **lb["anchor"]} for lb in spec.get("labels", [])]
            + [{"what": f"lower third: {lt['name']}", **lt["anchor"]} for lt in spec.get("lower_thirds", [])])


LAYER_PAD = 0.25            # seconds of empty frames before and after a graphic's own span in its clip: the verifier needs moments where nothing is due, and the wipes have room


def split_plan(p: dict) -> list[tuple[str, str, dict]]:
    """The plan cut into separate graphics, each to be its own clip (Ryan, 2026-10-21: "make the overlay and the title two separate graphics"): [(key, file name, plan)].

    The navy field with its diagonals is the OVERLAY, the topic lines are the TITLE, each name tag or the call to action is its own graphic, and step labels (the wallpaper reel's) are one. All keep the
    same layout, so laid on top of each other they look exactly as the single layer did, and each can be moved, scaled and trimmed on its own. A graphic whose element the plan does not have is not made."""
    base = {k: v for k, v in p.items() if k not in ("title", "labels", "lower_thirds")}
    out: list[tuple[str, str, dict]] = []
    t = p.get("title")
    if t:
        out.append(("overlay", "Graphic - Overlay.mov", {**base, "title": {**t, "small": "", "big": "", "joke": "", "lines": False}, "labels": [], "lower_thirds": []}))
        if any(t.get(k) for k in ("small", "big", "joke")):
            out.append(("title", "Graphic - Title.mov", {**base, "title": {**t, "field": False}, "labels": [], "lower_thirds": []}))
    if p.get("labels"):
        out.append(("labels", "Graphic - Labels.mov", {**base, "title": None, "labels": p["labels"], "lower_thirds": []}))
    used: set[str] = set()
    for i, lt in enumerate(p.get("lower_thirds", []), 1):
        what = "Call to action" if lt.get("key") == "cta" else "Tag " + re.sub(r"[^\w ]+", "", lt["name"]).strip()[:24]
        name = f"Graphic - {what}.mov"
        n = 2
        while name in used:
            name = f"Graphic - {what} {n}.mov"
            n += 1
        used.add(name)
        out.append((f"tag-{i}", name, {**base, "title": None, "labels": [], "lower_thirds": [lt]}))
    return out


def layer_window(sp: dict, zone: float, fps: float) -> tuple[float, float]:
    """The layer's own start and length, frame-aligned: its element's span with LAYER_PAD either side, inside the cut."""
    spans = []
    if sp.get("title"):
        spans.append((sp["title"]["on"][0], sp["title"]["off"]))
    spans += [(x["on"], x["off"]) for x in sp.get("labels", []) + sp.get("lower_thirds", [])]
    lo, hi = min(a for a, _b in spans), max(b for _a, b in spans)
    start_f = max(0, round((lo - LAYER_PAD) * fps))
    end_f = min(round(zone * fps), round((hi + LAYER_PAD) * fps))
    return start_f / fps, (end_f - start_f) / fps


def shift_plan(sp: dict, start: float) -> dict:
    """The plan with every time counted from the layer's own start (the way place_overlay and the captions read a layer that does not begin at 0)."""
    s = copy.deepcopy(sp)
    if s.get("title"):
        s["title"]["on"] = [round(x - start, 4) for x in s["title"]["on"]]
        s["title"]["off"] = round(s["title"]["off"] - start, 4)
        s["title"]["anchor_time"] = round(s["title"]["anchor_time"] - start, 4)
    for lb in s.get("labels", []):
        lb["on"], lb["off"] = round(lb["on"] - start, 4), round(lb["off"] - start, 4)
        lb["word_times"] = [round(x - start, 4) for x in lb["word_times"]]
        lb["anchor_time"] = round(lb["anchor_time"] - start, 4)
    for lt in s.get("lower_thirds", []):
        lt["on"], lt["off"] = round(lt["on"] - start, 4), round(lt["off"] - start, 4)
        lt["anchor_time"] = round(lt["anchor_time"] - start, 4)
    return s


def _build_layer(xml: Path, spec: dict, p: dict, seq: dict, out: Path, total: float, start: float, mov_name: str, what: str, render=None) -> tuple[int, dict | None]:
    """One layer: its HyperFrames project, the render, its placement.json and its checks. `p` is the (possibly shifted) plan it draws; `start` where its clip begins on the timeline."""
    out.mkdir(parents=True, exist_ok=True)
    proj, mov = out / "hyperframes_project", out / mov_name
    info = write_project(proj, p, seq["width"], seq["height"], total)
    n_labels = len(p["labels"])
    print(f"{what}: {seq['width']}x{seq['height']}, {total:.2f}s" + (f" from {start:.2f}s" if start else "") + ", " + (f"title at {p['title']['on'][0]:.2f}s, " if p["title"] else "") + f"{n_labels} label(s), {len(p['lower_thirds'])} lower third(s)")
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
    placement = {"kind": "title_layer", "place_overlay_on_timeline_at_sec": start, "duration_sec": total, "overlay": mov.name, "overlay_path": str(mov.resolve()),
                 "render": {"width": seq["width"], "height": seq["height"], "fps": seq["fps"], "fps_arg": seq["fps_arg"]},
                 "anchors": anchors_of(spec),
                 "plan": p, "layout": info["layout"], "events": info["events"], "hyperframes": mo.HF_VERSION, "gsap": mo.GSAP_VERSION}
    (out / "placement.json").write_text(json.dumps(placement, indent=2))
    if render is None:
        v = subprocess.run([sys.executable, str(HERE / "verify_title.py"), str(out)], capture_output=True, text=True)
        print(v.stdout.rstrip())
        if v.returncode != 0:
            print(v.stderr, file=sys.stderr)
            return 1, placement
    return 0, placement


def build_layers(xml: Path, spec: dict, out: Path, render=None, spec_of=sequence_spec) -> tuple[int, dict | None]:
    """The spec as SEPARATE graphics: one folder per graphic beside `out` ("<out>_overlay", "<out>_title", "<out>_tag-1", ...), each a short clip of only its own span, and `out` itself holds the
    whole plan (placement.json, kind title_layers, with the list of layers in stacking order) for the tools that read what the finish made."""
    validate_spec(spec)
    seq = spec_of(xml)
    cut = timeline.load_cut(xml)
    p = plan(spec, cc.timeline_dict(cut), cut.zone_end)
    zone = round(cut.zone_end, 4)
    out.mkdir(parents=True, exist_ok=True)
    layers = []
    for key, fname, sp in split_plan(p):
        start, total = layer_window(sp, zone, seq["fps"])
        folder = out.parent / f"{out.name}_{key}"
        code, placement = _build_layer(xml, spec, shift_plan(sp, start), seq, folder, total, start, fname, f"{key} layer", render)
        if code != 0:
            return code, None
        layers.append({"key": key, "name": fname, "folder": str(folder), "start_sec": start, "duration_sec": total})
    whole = {"kind": "title_layers", "layers": layers, "plan": p, "anchors": anchors_of(spec), "render": {"width": seq["width"], "height": seq["height"], "fps": seq["fps"], "fps_arg": seq["fps_arg"]},
             "hyperframes": mo.HF_VERSION, "gsap": mo.GSAP_VERSION}
    (out / "placement.json").write_text(json.dumps(whole, indent=2))
    print(f"{len(layers)} separate graphic(s): " + ", ".join(l["name"].replace("Graphic - ", "").replace(".mov", "") for l in layers))
    return 0, whole


def build_title(xml: Path, spec: dict, out: Path, render=None, spec_of=sequence_spec) -> tuple[int, dict | None]:
    """The spec as ONE layer the length of the cut (the original form; `build_layers` makes the separate graphics)."""
    validate_spec(spec)
    seq = spec_of(xml)
    cut = timeline.load_cut(xml)
    p = plan(spec, cc.timeline_dict(cut), cut.zone_end)
    total = round(cut.zone_end, 4)
    code, placement = _build_layer(xml, spec, p, seq, out, total, 0.0, "title.mov", "title layer", render)
    if code == 0:
        print(f"\nplace title.mov on V2 at 0 with place_overlay.py \"{xml.name}\" \"{out}\" --out <new.xml>")
    return code, placement


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--split", action="store_true", help="separate graphics (overlay, title, each name tag) as their own clips, in folders beside --out, instead of one layer")
    a = ap.parse_args()
    try:
        code, _p = (build_layers if a.split else build_title)(a.xml, json.loads(a.spec.read_text()), a.out)
    except (TitleError, mo.OverlayError, timeline.TimelineError, OSError, json.JSONDecodeError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
