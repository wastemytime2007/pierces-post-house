"""Which person's microphone a moment was clearest on (standalone; nothing in app/).

    python3 labs/recruit/pick_mic.py --moments moments.json --mics "Bob=<folder>" --mics "Mitch=<folder>" --out located.json

Each person wore their own recorder, so a moment can be looked for in each person's transcripts separately (`find_moments`). A lavalier also hears the people near it, so the same words usually turn up
in more than one person's file. For each match the mean Whisper confidence (avg_logprob) of the segments it covers is taken from the folder's .json beside each .srt; the microphone with the HIGHER confidence
is the likely speaker (the person closest to their own mic is the loudest and cleanest on it). That is a clue, not a fact: the result records every candidate and the gap so a person can overrule it,
and a gap under CLOSE (0.05) is reported as 'unclear' rather than a name."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import find_moments as fm

CLOSE = 0.05                  # checked against what the words themselves say, in the one interview where they settle it: W14 (Mitch, 'Bob physically saw'), W16 (Bob, 'as Mitchell was pointing out') and W21 (Bob, 'the old days, Ryan') all came out right: 3 of 3, small evidence


def confidence(folder: Path, clip: str, start: float, end: float) -> float | None:
    """Mean avg_logprob of the segments of `clip` overlapping [start, end] (None if the .json is missing or nothing overlaps)."""
    p = folder / f"{clip}.json"
    if not p.exists():
        return None
    segs = [s for s in json.loads(p.read_text())["segments"] if s["end"] > start and s["start"] < end and s.get("avg_logprob") is not None]
    return sum(s["avg_logprob"] for s in segs) / len(segs) if segs else None


def choose(cands: dict[str, dict]) -> tuple[str, float | None]:
    """(speaker or 'unclear', gap) from {person: {'conf': mean avg_logprob}}: the more confident mic wins only by at least CLOSE."""
    scored = sorted(((v["conf"], k) for k, v in cands.items() if v.get("conf") is not None), reverse=True)
    if not scored:
        return "unclear", None
    if len(scored) == 1:
        return scored[0][1], None
    gap = scored[0][0] - scored[1][0]
    return (scored[0][1] if gap >= CLOSE else "unclear"), round(gap, 3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--moments", required=True)
    ap.add_argument("--mics", action="append", required=True, help="Name=<folder of that person's .srt and .json>")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pad", type=float, default=1.5)
    a = ap.parse_args(argv)
    folders = {}
    for spec in a.mics:
        name, _, path = spec.partition("=")
        folders[name] = Path(path).expanduser()
    clips = {n: fm.load_clips(f) for n, f in folders.items()}
    out = []
    for m in json.loads(Path(a.moments).expanduser().read_text()):
        cands = {}
        for name, cl in clips.items():
            r = fm.locate(m, cl, a.pad)
            if r["found"]:
                r["conf"] = confidence(folders[name], r["clip"], r["speech_start"], r["speech_end"])
                cands[name] = r
        if not cands:
            out.append({**m, "found": False, "reason": "not found in any person's transcripts"})
            continue
        # the match with the best anchor agreement per person is already chosen by locate(); now the speaker clue
        who, gap = choose(cands)
        pick = cands[who] if who in cands else max(cands.values(), key=lambda r: (r["start_ratio"] + r["end_ratio"]))
        out.append({**pick, "mic": who, "mic_gap": gap, "mic_candidates": {k: {"clip": v["clip"], "start": v["speech_start"], "end": v["speech_end"], "conf": None if v["conf"] is None else round(v["conf"], 3),
                                                                           "ratios": [v["start_ratio"], v["end_ratio"]]} for k, v in cands.items()}})
    Path(a.out).expanduser().write_text(json.dumps(out, indent=1))
    for r in out:
        if r["found"]:
            print(f"OK  {r['id']:4} {r['mic']:8} {r['clip'][-12:]:12} {r['speech_start']:8.1f}-{r['speech_end']:8.1f}  gap {r['mic_gap']}  " + " ".join(f"{k}:{v['conf']}" for k, v in r["mic_candidates"].items()))
        else:
            print(f"NOT FOUND {r['id']:4} {r['reason']}")
    print(f"{sum(1 for r in out if r['found'])} of {len(out)} moments located")
    return 0


if __name__ == "__main__":
    sys.exit(main())
