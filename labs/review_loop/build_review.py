#!/usr/bin/env python3
"""Export XML in, review folder out (preview.mp4 + review.html + timeline.json).

    PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/build_review.py \
        <export.xml> --out <dir> [--height 540] [--open]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bleep"))

from layers import beatmap, composite, find_layers, verify as verify_layers  # noqa: E402
from render_preview import render_preview  # noqa: E402
from timeline import TimelineError, load_cut  # noqa: E402


def build(xml: Path, out: Path, height: int = 540, changes: dict | None = None, suspects: list[dict] | None = None, editable_bleeps: bool = False, scan_json: Path | None = None) -> Path:
    cut = load_cut(xml)
    out.mkdir(parents=True, exist_ok=True)
    info = render_preview(cut, out / "preview.mp4", height=height)

    data = {
        "schema": "review_timeline.v0-draft",
        "built": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sequence": cut.sequence_name,
        "duration": round(info["duration"], 3),
        "audio_source": info["audio_source"],
        "clips": [
            {"idx": c.idx, "start": round(c.tl_start, 3), "end": round(c.tl_end, 3),
             "source": c.name, "source_path": c.src_path,
             "src_in": round(c.src_in, 3), "src_out": round(c.src_out, 3),
             "motion": list(c.motion) if c.motion else None}
            for c in cut.video
        ],
        "frame": {"width": cut.width, "height": cut.height, "framed_by_motion": info.get("framed_by_motion", False)},
        "audio": [
            {"start": round(a.tl_start, 3), "end": round(a.tl_end, 3), "source": a.name,
             "source_path": a.src_path, "src_in": round(a.src_in, 3), "src_out": round(a.src_out, 3)}
            for a in cut.audio
        ],
    }
    layers = find_layers(xml)
    if layers:
        composite(out / "preview.mp4", layers, out / "preview_full.mp4")
        rows = verify_layers(out / "preview.mp4", out / "preview_full.mp4", layers, cut.zone_end)
        for name, ok, detail in rows:
            print(f"  [{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}] {name}  {detail}")
        if any(ok is False for _n, ok, _d in rows):
            raise TimelineError("the preview with layers does not match the layers in the XML")
        data["preview_full"] = "preview_full.mp4"
        data["layers"] = [{"kind": l.kind, "name": l.name, "start": round(l.start, 2), "end": round(l.end, 2)} for l in layers]
    bl_layers = [l for l in layers if l.kind == "audio" and Path(l.path).name.startswith("bleep_")]
    if bl_layers or editable_bleeps:                # bleeps are editable on the page: a second preview with the speech whole and no bleep baked in, so they can be heard live
        import shutil
        import wave
        import numpy as np
        import bleep as bp
        if bl_layers:
            live_xml = out / "_live.xml"
            bp.strip_previous(xml, live_xml)
            live_cut = load_cut(live_xml)
            render_preview(live_cut, out / "preview_live_clean.mp4", height=height)
            live_layers = find_layers(live_xml)
            if live_layers:
                composite(out / "preview_live_clean.mp4", live_layers, out / "preview_live.mp4")
            else:
                shutil.copy2(out / "preview_live_clean.mp4", out / "preview_live.mp4")
            live_xml.unlink(missing_ok=True)
        else:                                       # nothing bleeped yet: the ordinary previews already have the speech whole, so they are the live ones; bleeps can still be ADDED
            live_cut = cut
            shutil.copy2(out / "preview.mp4", out / "preview_live_clean.mp4")
            shutil.copy2(out / ("preview_full.mp4" if layers else "preview.mp4"), out / "preview_live.mp4")
        sp = bp.cut_audio(live_cut)                 # the speech as it is without bleeps: its loudness every 10 ms, for the close-up
        if bl_layers:
            with wave.open(bl_layers[0].path) as w:
                peak = float(np.abs(np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")).max()) / 32767
        else:
            peak = float(np.percentile(np.abs(sp), 99.9)) * 10 ** (-bp.BELOW_PEAK_DB / 20)          # the level an automatic bleep would have had
        n = int(0.01 * bp.SR)
        rms = np.sqrt((sp[:len(sp) // n * n].reshape(-1, n) ** 2).mean(axis=1))
        env = np.clip((20 * np.log10(np.maximum(rms, 1e-6)) + 60) / 55, 0, 1)
        data.update({"envelope": [int(round(x * 100)) for x in env], "envelope_hz": 100})
        data.update({"preview_live": "preview_live.mp4", "preview_live_clean": "preview_live_clean.mp4", "bleep_level": round(peak, 4),
                     "live_bleeps": [{"start": round(l.start, 3), "end": round(l.end, 3)} for l in sorted(bl_layers, key=lambda l: l.start)], "xml": str(xml)})
    if scan_json and Path(scan_json).exists():       # what the automatic scan did, so "0 bleeps" is not mistaken for "the scan failed"
        sj = json.loads(Path(scan_json).read_text())
        data["scan"] = {"words_heard": sj.get("words_heard", len(sj.get("words", []))), "bleeped": len(sj.get("spans", [])), "listed_found": len(sj.get("hits", [])),
                        "flagged": [{"start": x["word_start"], "end": x["word_end"], "word": x["word"], "tier": x.get("tier", "possible")} for x in sj.get("suspects", [])]}
    if changes:
        data["changes"] = changes
    data["beatmap"] = beatmap(cut, layers, changes["items"] if changes else None, suspects)
    (out / "timeline.json").write_text(json.dumps(data, indent=2))
    html = (HERE / "review_template.html").read_text().replace(
        "/*__DATA__*/null", json.dumps(data).replace("</", "<\\/"))
    page = out / "review.html"
    page.write_text(html)
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--scan", type=Path, help="the automatic scan's bleep.json, so the page can say what the scan found (and did not)")
    ap.add_argument("--bleeps", action="store_true", help="make the bleeps editable on the page even if the cut has none yet (so ones the tool missed can be added)")
    args = ap.parse_args()
    try:
        page = build(args.xml, args.out, args.height, editable_bleeps=args.bleeps, scan_json=args.scan)
    except TimelineError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(page)
    if args.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
