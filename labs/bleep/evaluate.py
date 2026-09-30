#!/usr/bin/env python3
"""Score the suspect detector against Ryan's labels.

    PRECUT_ROOT=~/precut-checkout python3 labs/bleep/evaluate.py --speech <16 kHz mono wav of the cut's speech> \
        --words small.json [--other base.json] [--truth labs/bleep/ground_truth.json] [--min-signals 2]

`--words` and `--other` are lists of [word, start, end, probability] (what `transcribe_detail` returns). A flagged stretch counts as a hit when it
overlaps a labelled curse word by any amount, as a false alarm when it overlaps a rejected suspect, and is not counted when it is unjudged.
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bleep as bl  # noqa: E402

NEAR = 0.3                                         # a labelled word counts as found when a flag lies within this many seconds of it


def score(flagged: list[dict], truth: dict) -> dict:
    words = truth.get("curse_words", [])
    found = [w for w in words if any(f["start"] - NEAR <= w["at"] <= f["end"] + NEAR for f in flagged)]
    false_alarms = [r for r in truth.get("rejected_suspects", []) if any(f["start"] < r["end"] and f["end"] > r["start"] for f in flagged)]
    unjudged = [u for u in truth.get("not_judged", []) if any(f["start"] < u["end"] and f["end"] > u["start"] for f in flagged)]
    judged = len(false_alarms) + sum(1 for f in flagged if any(f["start"] - NEAR <= w["at"] <= f["end"] + NEAR for w in words))
    return {"flagged": len(flagged), "curse_words": len(words), "found": len(found), "missed": [w["word"] for w in words if w not in found],
            "false_alarms": len(false_alarms), "unjudged": len(unjudged),
            "precision": None if not judged else round((len(found)) / judged, 2), "recall": None if not words else round(len(found) / len(words), 2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speech", type=Path, required=True)
    ap.add_argument("--words", type=Path, required=True)
    ap.add_argument("--other", type=Path)
    ap.add_argument("--truth", type=Path, default=HERE / "ground_truth.json")
    ap.add_argument("--min-signals", type=int, default=bl.MIN_SIGNALS)
    a = ap.parse_args()
    w = wave.open(str(a.speech))
    speech = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32767
    w.close()
    det = [tuple(t) for t in json.loads(a.words.read_text())]
    other = [tuple(t[:3]) for t in json.loads(a.other.read_text())] if a.other else None
    flagged = bl.find_suspects([t[:3] for t in det], speech, details=det, other=other, min_signals=a.min_signals)
    r = score(flagged, json.loads(a.truth.read_text()))
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
