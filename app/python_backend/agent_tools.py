#!/usr/bin/env python3
"""The toolbox the AI editor's Claude agent works with (creator_tools.agent_edit starts the agent and tells it to use this).

    python3 agent_tools.py ops-help
    python3 agent_tools.py inspect <cut.xml>                      the cut clip by clip: where each plays, where it comes from, the words heard in it with their times
    python3 agent_tools.py search <cut.xml> "some words"          lines of the recordings that hold those words (also lines the cut does not use)
    python3 agent_tools.py apply <cut.xml> <notes.json> <ops.json>   make the operations as a new version, through every check the app's own revision has

An `apply` is the same step the app's Submit button runs (creator_tools.apply_notes): the measured edits, the checks on the result, the captions, music and graphics put back on a
finished cut. A refusal or a failed check comes back in words and nothing is made. The agent decides WHAT to do; this decides whether it holds.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import creator_tools as ct  # noqa: E402

ct.use_labs()
import ops as ops_mod  # noqa: E402
import timeline  # noqa: E402
import words as words_mod  # noqa: E402


def cmd_ops_help() -> int:
    doc = ops_mod.__doc__ or ""
    print(doc.split("All times are on the timeline")[0].strip())
    print('\nAn operations file is a JSON list. Each item: {"note": <number of the note in the notes file, from 1>, "op": "<name>", <its fields>, "why": "<one line>"}.')
    print('Examples: {"note":1,"op":"end_at_words","clip":5,"words":"there\'s a 2x12 above"}  {"note":2,"op":"drop_clip","clip":3}  {"note":3,"op":"remove_range","start":12.4,"end":13.1}')
    print('          {"note":4,"op":"restore_footage","words":"the words of the line to put back","before":4}  {"note":5,"op":"trim_start","clip":2,"seconds":0.4}  {"note":6,"op":"extend_start","clip":2,"max_sec":3}')
    print("Times in remove_range are timeline seconds of the version you apply to. `clip` is the clip number `inspect` prints.")
    return 0


def _clip_words(cut, clip) -> tuple[str, float, float, list]:
    path, a, b = ops_mod._audio_for(cut, clip)
    ws = words_mod.words_in(path, max(0.0, a - 0.5), (b - a) + 1.0)
    return path, a, b, ws


def cmd_inspect(xml: str) -> int:
    cut = timeline.load_cut(Path(xml))
    print(f"{Path(xml).name}: {len(cut.video)} clips, {cut.zone_end:.1f} s long")
    for c in cut.video:
        try:
            path, a, b, ws = _clip_words(cut, c)
        except Exception as exc:
            print(f"clip {c.idx}  timeline {c.tl_start:.2f}-{c.tl_end:.2f}  (words could not be read: {type(exc).__name__})")
            continue
        inside = " ".join(f"{w.text.strip()}@{w.start:.2f}" for w in ws if w.end > a - 0.05 and w.start < b + 0.05)
        print(f"clip {c.idx}  timeline {c.tl_start:.2f}-{c.tl_end:.2f}  picture {Path(c.src_path).name} {c.src_in:.2f}-{c.src_out:.2f}  sound {Path(path).name} {a:.2f}-{b:.2f}")
        print(f"    words (word@seconds in that recording): {inside or '(none heard)'}")
    return 0


def cmd_search(xml: str, text: str) -> int:
    cut = timeline.load_cut(Path(xml))
    want = ops_mod._distinctive(text) or words_mod.tokens(text)
    seen = set()
    rows = []
    for c in cut.video:
        if c.src_path in seen:
            continue
        seen.add(c.src_path)
        ph = ops_mod._transcript_phrases(c.src_path)
        for cand in ops_mod._line_candidates(ph or [], want, keep=8):
            a, b = cand["span"]
            line = {"start": ph[a]["start"], "end": ph[b]["end"], "text": " ".join(str(ph[i].get("text", "")).strip() for i in range(a, b + 1))}
            used = next((k.idx for k in cut.video if k.src_path == c.src_path and k.src_in - 0.3 <= line["start"] and line["end"] <= k.src_out + 0.3), None)
            rows.append((cand["rank"], Path(c.src_path).name, line, used))
    for _s, name, cand, used in sorted(rows, key=lambda r: -r[0])[:12]:
        print(f'{name} {cand["start"]:.2f}-{cand["end"]:.2f}  {"in the cut as clip " + str(used) if used else "NOT in the cut"}: "{cand["text"]}"')
    if not rows:
        print("no line of the cut's recordings holds those words")
    return 0


def cmd_apply(xml: str, notes: str, plan: str) -> int:
    ops = json.loads(Path(plan).read_text())
    for o in ops:
        o["trusted"] = True                                      # the agent reasoned from the words and the audio, as the AI reviewer's own fixes do
    trusted = Path(plan).with_suffix(".trusted.json")
    trusted.write_text(json.dumps(ops, indent=1))
    try:
        r = ct.apply_notes(xml, notes, None, 540, lambda s: print(f"  ... {s}", flush=True), str(trusted), False)
    except ct.ToolError as exc:
        print("NOT MADE. The revision was refused or a check failed; nothing was written. In the tool's words:\n" + str(exc)[:3500])
        return 2
    for i in r.get("items", []):
        print(f'  note {i.get("note")}: {"MADE" if i.get("applied") else "NOT MADE"}  {i.get("op")}: {str(i.get("summary"))[:300]}')
    if not r.get("xml"):
        print("Nothing was changed, so there is no new version.", r.get("message", ""))
        return 3
    print(f'NEW VERSION: {r["xml"]}')
    state = os.environ.get("AGENT_STATE_FILE")
    if state:
        p = Path(state)
        rows = json.loads(p.read_text()) if p.is_file() else []
        rows.append({**r, "from": xml})
        p.write_text(json.dumps(rows, indent=1, default=str))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "ops-help":
        return cmd_ops_help()
    if len(argv) == 3 and argv[1] == "inspect":
        return cmd_inspect(argv[2])
    if len(argv) == 4 and argv[1] == "search":
        return cmd_search(argv[2], argv[3])
    if len(argv) == 5 and argv[1] == "apply":
        return cmd_apply(argv[2], argv[3], argv[4])
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
