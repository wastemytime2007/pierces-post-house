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

from render_preview import render_preview  # noqa: E402
from timeline import TimelineError, load_cut  # noqa: E402


def build(xml: Path, out: Path, height: int = 540) -> Path:
    cut = load_cut(xml)
    out.mkdir(parents=True, exist_ok=True)
    info = render_preview(cut, out / "preview.mp4", height=height)

    data = {
        "schema": "review_timeline.v0-draft",
        "sequence": cut.sequence_name,
        "duration": round(info["duration"], 3),
        "audio_source": info["audio_source"],
        "clips": [
            {"idx": c.idx, "start": round(c.tl_start, 3), "end": round(c.tl_end, 3),
             "source": c.name, "source_path": c.src_path,
             "src_in": round(c.src_in, 3), "src_out": round(c.src_out, 3)}
            for c in cut.video
        ],
        "audio": [
            {"start": round(a.tl_start, 3), "end": round(a.tl_end, 3), "source": a.name,
             "source_path": a.src_path, "src_in": round(a.src_in, 3), "src_out": round(a.src_out, 3)}
            for a in cut.audio
        ],
    }
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
    args = ap.parse_args()
    try:
        page = build(args.xml, args.out, args.height)
    except TimelineError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(page)
    if args.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
