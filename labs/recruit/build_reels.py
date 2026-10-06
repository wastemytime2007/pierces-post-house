"""Build one Premiere XML per reel from resolved cuts, through PreCut's own exporter (posthouse.coldfootage), standalone; nothing in app/.

    PRECUT_ROOT=~/precut-checkout python3 labs/recruit/build_reels.py --resolved resolved.json --out "<folder>" [--version 1]

For each pitch in resolved.json (rough_cut.py's output) a segments file is written (the camera ORIGINAL as the source, in and out at word level, no handles because every cut is a tight
line, and the cut's role as its label) and `posthouse.coldfootage.build_coldfootage_xml` writes `Reel <n> - <title>_v<version>.xml`: PreCut's Seq/Footage/Audio/Files structure, V1 and A1 linked by
the same frames, at the source's own size and frame rate (3840x2160, 29.97). Vertical is Premiere's Auto Reframe on that sequence (Ryan, 2026-09-30). Camera audio is the audio: it is the
file's own track, so it is in sync with its picture by construction; `verify_reel.py` checks that it also says the right words in the right place."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))


def safe(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9' ,.&-]+", "", title).strip()


def segments_for(pitch: dict) -> dict:
    return {"contract_version": 1, "sequence_name": f"Reel {pitch['n']} - {safe(pitch['title'])}",
            "segments": [{"source_path": c["source_original"], "in_sec": c["in_sec"], "out_sec": c["out_sec"], "label": f"{c['id']}: {c['role']}", "handle_sec": 0.0} for c in pitch["cuts"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resolved", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--version", type=int, default=1)
    a = ap.parse_args(argv)
    from posthouse.coldfootage import build_coldfootage_xml, ColdFootageError
    data = json.loads(Path(a.resolved).expanduser().read_text())
    if data.get("errors"):
        print("REFUSING: resolved.json carries errors: " + "; ".join(data["errors"]), file=sys.stderr)
        return 1
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for n, p in enumerate(data["pitches"], 1):
        p = {**p, "n": n}
        seg = segments_for(p)
        (out / f"reel_{n}_segments.json").write_text(json.dumps(seg, indent=1))
        xml = out / f"{seg['sequence_name']}_v{a.version}.xml"
        try:
            build_coldfootage_xml(seg, xml)
        except ColdFootageError as e:
            print(f"FAILED reel {n}: {e}", file=sys.stderr)
            return 1
        made.append(str(xml))
        print(f"reel {n}: {len(seg['segments'])} cuts -> {xml.name}")
    (out / "built.json").write_text(json.dumps(made, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
