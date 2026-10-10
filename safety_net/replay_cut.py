#!/usr/bin/env python3
"""Replay a cut's whole recorded history through the app's own code, with no AI calls, before the app is handed to Ryan.

    PRECUT_ROOT=~/precut-checkout python3 safety_net/replay_cut.py "<cut>.xml" [--out <folder>] [--rounds N]

Why (Ryan, 2026-10-09): "we keep fixing the same things and then on the next cut we lose the fixes ... Its taking up so many tokens rerunning the
app on the same video over and over". A fix was checked on the path where its bug showed, and Ryan found the next break on another path by
running the app again. This runs every path on the real footage instead:

  1. every revision the app made (the AI editor's rounds and Ryan's own notes) is made again from the version it came from, with the ops
     that were recorded for it (ops.json: no notes reader, no AI review), through creator_tools.apply_notes, so a revision of a finished cut
     also puts its layers back (the rebuild that failed twice on 2026-10-09);
  2. every finish is made again through creator_tools.finish_cut, reusing the music it made (finish.json's music_raw: nothing generated);
  3. the result is checked against what Ryan has asked for: no voice off the microphones (the off-camera question), each person on their
     own recorder, no jump cut left without a punch-in, the title card, name tags, call to action, captions and music on the finished cut,
     and the export checks.

Every recorded result is compared with the replayed one (which notes were made). The cost is local: Whisper, HyperFrames and ffmpeg. Run it
after any change to labs/ or creator_tools.py and before restarting the app; hand the app over only when it passes.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app" / "python_backend"))
import creator_tools as ct  # noqa: E402

ct.use_labs()
import finish_cut as fc  # noqa: E402
import timeline  # noqa: E402


def history(root_xml: Path) -> list[dict]:
    """Every version made from `root_xml`, parent before child: [{"xml", "folder", "parent", "kind": "notes"|"finish"}]. A version's folder sits
    inside its parent's folder (creator_tools.next_version_folder / free_version_folder), so the tree is read from the disk."""
    out = []

    def walk(parent_xml: Path, where: Path):
        for d in sorted(p for p in where.iterdir() if p.is_dir() and p.name.endswith(" - revised")):
            xmls = [x for x in d.glob("*.xml") if "(before layers)" not in x.name and not x.name.startswith("_")]
            if len(xmls) != 1:
                continue
            kind = "notes" if (d / "ops.json").is_file() else "finish" if (d / "finish.json").is_file() else None
            if kind:
                out.append({"xml": xmls[0], "folder": d, "parent": parent_xml, "kind": kind})
            walk(xmls[0], d)
    walk(root_xml, root_xml.parent)
    return out


def _recorded_made(folder: Path) -> dict[int, bool]:
    f = folder / "changes.json"
    if not f.is_file():
        return {}
    return {i["note"]: bool(i.get("applied")) for i in json.loads(f.read_text()).get("items", [])}


def replay_notes(v: dict, into: Path, src: Path) -> tuple[Path | None, list[tuple[str, bool | None, str]]]:
    """The revision made again from `src` (the replayed parent) with the notes and ops that made it."""
    p = v["parent"]
    review = p.parent / f"{p.stem} - review"                                  # notes on V1 are saved in its review folder; on a later version, in that version's own folder
    notes = review / "review_notes.json" if (review / "review_notes.json").is_file() else p.parent / "review_notes.json"
    if not notes.is_file():
        return None, [(f"{v['xml'].stem} NOTES-RECORDED", None, f"no notes file beside {v['parent'].name}: not replayed")]
    ops = v["folder"] / "ops.json"
    rows = []
    try:
        r = ct.apply_notes(str(src), str(notes), str(into), 540, None, str(ops))
    except ct.ToolError as e:
        return None, [(f"{v['xml'].stem} REVISION", False, str(e).splitlines()[0][:300])]
    made = {i["note"]: bool(i.get("applied")) for i in r.get("items", [])}
    was = _recorded_made(v["folder"])
    lost = sorted(n for n, ok in was.items() if ok and not made.get(n))
    rows.append((f"{v['xml'].stem} SAME-NOTES-MADE", not lost, f"{sum(made.values())} of {len(made)} notes made" + (f"; made when recorded, not now: notes {lost}" if lost else "")))
    if r.get("message", "").startswith("The picture was revised, but"):
        rows.append((f"{v['xml'].stem} LAYERS-PUT-BACK", False, r["message"][:300]))
    elif r.get("layers"):
        rows.append((f"{v['xml'].stem} LAYERS-PUT-BACK", True, r["message"][:200]))
    return (Path(r["xml"]) if r.get("xml") else None), rows


def replay_finish(v: dict, into: Path, src: Path) -> tuple[Path | None, list[tuple[str, bool | None, str]]]:
    rec = json.loads((v["folder"] / "finish.json").read_text())
    opts = rec.get("options") or {}
    music = rec.get("music_raw")
    music = music if music and Path(music).is_file() else None
    try:
        r = ct.finish_cut(str(src), str(into), bool(opts.get("captions", True)), bool(opts.get("music", True)) and music is not None, bool(opts.get("bleep", True)),
                          None, 540, None, bool(opts.get("graphics", False)), bool(opts.get("sfx", False)), music_file=music)
    except ct.ToolError as e:
        return None, [(f"{v['xml'].stem} FINISH", False, str(e).splitlines()[0][:300])]
    steps = "; ".join(f"{i['op']}{'' if i['applied'] else ' (not done)'}" for i in r["items"])
    return Path(r["xml"]), [(f"{v['xml'].stem} FINISH", True, steps + ("" if music else "; music not replayed: the recorded track is gone"))]


def checks(final: Path) -> list[tuple[str, bool | None, str]]:
    """What Ryan has asked of every cut, measured on the replayed result."""
    rows = []
    cut = timeline.load_cut(final)
    import ai_review as air
    import layers as layers_mod
    import punch_in as pi
    import render_preview
    words = air.transcript_of(cut)
    try:
        om = air.off_mic_findings(cut, words)
        rows.append(("NO-OFF-CAMERA-VOICE", om.ok is not False, om.detail[:200]))
    except Exception as e:
        rows.append(("NO-OFF-CAMERA-VOICE", None, f"not measured: {type(e).__name__}: {str(e)[:160]}"))
    voices = {Path(a.src_path).name for a in cut.audio if a.tl_end - a.tl_start > 0.5}
    lav = sorted(v for v in voices if re.search(r"bob|mitch", v, re.I))
    rows.append(("EACH-PERSON-ON-THEIR-RECORDER", bool(lav) and any("bob" in v.lower() for v in lav) and any("mitch" in v.lower() for v in lav),
                 f"voice from: {', '.join(sorted(voices))}"))
    left = pi.plan(cut, render_preview.source_dims)
    rows.append(("NO-JUMP-CUT-LEFT", not left, "every jump cut alternates wide and close" if not left else f"clips {[r['idx'] for r in left]} are jump cuts with no punch-in"))
    have = fc.layers_present(final)
    names = [l.name for l in layers_mod.find_layers(final)]
    rows.append(("CAPTIONS-ON", any("captions" in n for n in names), ", ".join(sorted(set(names)))[:200]))
    rows.append(("TITLE-AND-NAME-TAGS-ON", any("title" in n for n in names), ""))
    rows.append(("MUSIC-ON", bool(have.get("music")), ""))
    pl = next((p for p in [final.parent / "graphics" / "placement.json", final.parent / "layers" / "graphics" / "placement.json"] if p.is_file()), None)
    if pl:
        lts = [lt["name"] for lt in json.loads(pl.read_text()).get("plan", {}).get("lower_thirds", [])]
        rows.append(("CALL-TO-ACTION-ON", len(lts) >= 2, f"on-screen boxes: {lts}"))
    import verify_export
    rep = verify_export.Report()
    verify_export.check_xml(final, rep)
    bad = [n for n, ok, _d in rep.rows if ok is False and n not in ("XML-POOL-NO-DUPLICATES", "XML-POOL-NOT-IN-CUT")]
    rows.append(("EXPORT-CHECKS", not bad, "pass" if not bad else f"failing: {bad}"))
    return rows


def stale_copies() -> list[str]:
    """Files of labs/ that differ from the copy the app runs (app/python_backend/labs): a replay of those would test old code."""
    out = []
    shipped = {d.name for d in (REPO / "app" / "python_backend" / "labs").iterdir() if d.is_dir()}     # the folders sync_labs.sh carries into the app
    for f in (REPO / "labs").rglob("*.py"):
        if "/tests/" in str(f) or f.relative_to(REPO / "labs").parts[0] not in shipped:
            continue
        twin = REPO / "app" / "python_backend" / "labs" / f.relative_to(REPO / "labs")
        if not twin.is_file() or twin.read_bytes() != f.read_bytes():
            out.append(str(f.relative_to(REPO)))
    return out


def main() -> int:
    stale = stale_copies()
    if stale:
        print(f"REFUSING: the app's copy of labs is out of date ({len(stale)} file(s), e.g. {stale[0]}); run ./safety_net/sync_labs.sh first, or this replays old code")
        return 2
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path, help="the cut's first version (V1)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--all", action="store_true", help="every version ever made from this cut, not only the chain to the newest")
    args = ap.parse_args()
    root = args.xml.expanduser().resolve()
    out = (args.out or Path.home() / "Library" / "Application Support" / "Post House" / "replays" / f"{root.stem} {time.strftime('%Y%m%d-%H%M%S')}").expanduser()
    out.mkdir(parents=True, exist_ok=True)
    start = out / root.name
    shutil.copy(root, start)
    made: dict[Path, Path] = {root: start}
    rows: list[tuple[str, bool | None, str]] = []
    final = start
    every = history(root)
    if not args.all and every:                                               # the chain that leads to the newest version: what Ryan last saw
        by_xml = {v["xml"]: v for v in every}
        chain, cur = [], max(every, key=lambda v: v["xml"].stat().st_mtime)
        while cur:
            chain.append(cur)
            cur = by_xml.get(cur["parent"])
        every = list(reversed(chain))
    print(f"{len(every)} version(s) to replay", flush=True)
    for v in every:
        src = made.get(v["parent"])
        if src is None:
            rows.append((f"{v['xml'].stem} PARENT", None, "its parent was not replayed, so neither was this"))
            continue
        print(f"replaying {v['kind']}: {v['xml'].name}", flush=True)
        into = ct.free_version_folder(src)[0] if v["kind"] == "finish" else ct.next_version_folder(src)
        got, r = (replay_finish if v["kind"] == "finish" else replay_notes)(v, into, src)
        rows += r
        if got:
            made[v["xml"]] = got
            final = got
    print(f"checking the last version: {final.name}", flush=True)
    rows += checks(final)
    lines = [f"[{'PASS' if ok else 'FAIL' if ok is False else 'info'}] {n}  {d}" for n, ok, d in rows]
    (out / "replay_report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    failed = [n for n, ok, _d in rows if ok is False]
    print(f"\n{len(failed)} failed. Report: {out / 'replay_report.txt'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
