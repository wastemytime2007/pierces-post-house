#!/usr/bin/env python3
"""Every kind of revision the person can ask for, made on a real finished cut and measured in the result, with no AI calls.

    PRECUT_ROOT=~/precut-checkout python3 safety_net/battery_revisions.py "<a finished cut>.xml" [--out <folder>] [--only A,B]

Why (Ryan, 2026-10-12): "We need to give the AI editor the ability to do ALL SKILLS that were originally used to make the cut ... Anything it did in the first place it should be able to edit if
requested as a revision ... i've yet to see it implement even one revision ive given it without having to come to you." Each case below is a revision from the cut given, made through
creator_tools.apply_notes with the operations the notes reader produces (recorded here, so nothing is sent to a model), and then READ BACK from the new version: the title's words in the graphics plan,
the music level in the finish record, the layers on the cut. A case that says "applied" but is not in the result fails.

Cases:  A graphics words and time   B music level and sound effects   C music taken out   D graphics taken out   E footage, captions and the layers kept   F zoom, a different effect, a trim and a filler cut
"""
from __future__ import annotations

import argparse
import json
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


def _plan(folder: Path) -> dict | None:
    """The graphics plan of the new version, or None when the revision made none (a missing plan is a failure, never "nothing on screen")."""
    p = folder / "layers" / "graphics" / "placement.json"
    return json.loads(p.read_text())["plan"] if p.is_file() else None


def _record(folder: Path) -> dict:
    p = folder / "layers" / "finish.json"
    return json.loads(p.read_text()) if p.is_file() else {}


def _graphics(src: Path) -> list[dict]:
    """Every graphic the last finish put on the cut, with the time it comes on: [{"kind", "name", "on", "off"}]."""
    rec = ct._finish_record(src) or {}
    plan = json.loads((Path(rec["folder"]) / "graphics" / "placement.json").read_text())["plan"]
    out = []
    if plan.get("title"):
        out.append({"kind": "title", "name": plan["title"]["small"], "on": plan["title"]["on"][0], "off": plan["title"]["off"]})
    for lt in plan.get("lower_thirds", []):
        out.append({"kind": "tag", "name": lt["name"], "on": lt["on"], "off": lt["off"]})
    return out


def _run(src: Path, out: Path, name: str, notes: list[dict], ops: list[dict]) -> tuple[dict, Path]:
    d = out / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    nf, of = d / "notes.json", d / "ops.json"
    nf.write_text(json.dumps({"schema": "review_notes.v0-draft", "sequence": "s", "notes": notes}))
    of.write_text(json.dumps(ops))
    r = ct.apply_notes(str(src), str(nf), str(d / "rev"), 540, None, str(of))
    print("   message:", (r.get("message") or "")[:400], flush=True)
    return r, Path(r["xml"]).parent if r.get("xml") else d / "rev"


def case_a(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    g = _graphics(src)
    title = next(x for x in g if x["kind"] == "title")
    tags = [x for x in g if x["kind"] == "tag"]
    cta = tags[-1]
    mid = tags[1] if len(tags) > 2 else tags[0]
    notes = [{"timeline_sec": title["on"] + 0.5, "clip": 1, "text": 'change the title card to say "Septic timing"', "shapes": []},
             {"timeline_sec": tags[0]["on"] + 0.5, "clip": 1, "text": 'change the name tag to say "Bob, founder"', "shapes": []},
             {"timeline_sec": cta["on"] + 0.5, "clip": 1, "text": 'change the call to action to say "DM us septic"', "shapes": []},
             {"timeline_sec": mid["on"] + 0.5, "clip": 1, "text": "the name tag is up too briefly, keep it up two seconds longer", "shapes": []}]
    ops = [{"note": 1, "op": "edit_callout", "title": "Septic timing", "subtitle": None, "remove_subtitle": False},
           {"note": 2, "op": "edit_callout", "title": "Bob, founder", "subtitle": None, "remove_subtitle": False},
           {"note": 3, "op": "edit_callout", "title": "DM us septic", "subtitle": None, "remove_subtitle": False},
           {"note": 4, "op": "extend_graphic", "seconds": 2.0}]
    r, d = _run(src, out, "A_graphics_words", notes, ops)
    plan = _plan(d)
    if plan is None:
        return [("A RUNS", False, "the revision made no graphics plan: " + (r.get("message") or "")[:200])]
    names = [lt["name"] for lt in plan.get("lower_thirds", [])]
    new_mid = next((lt for lt in plan.get("lower_thirds", []) if lt["on"] == mid["on"] or abs(lt["on"] - mid["on"]) < 0.05), None)
    was = mid["off"] - mid["on"]
    return [("A1 title words", (plan.get("title") or {}).get("small") == "Septic timing", f'title reads {(plan.get("title") or {}).get("small")!r}'),
            ("A2 name tag words", "Bob, founder" in names, f"tags: {names}"),
            ("A3 call to action words", "DM us septic" in names, f"tags: {names}"),
            ("A4 name tag kept up longer", new_mid is not None and (new_mid["off"] - new_mid["on"]) > was + 0.5,
             f"was {was:.2f}s, now {(new_mid['off'] - new_mid['on']) if new_mid else 'missing'}s"),
            ("A5 every note reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


def case_b(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    notes = [{"timeline_sec": 10.0, "clip": 2, "text": "the music is too loud, bring it down", "shapes": []},
             {"timeline_sec": 20.0, "clip": 4, "text": "take out the sound effects", "shapes": []}]
    ops = [{"note": 1, "op": "music_volume", "direction": "down"}, {"note": 2, "op": "remove_sfx"}]
    r, d = _run(src, out, "B_music_sfx", notes, ops)
    rec, new = _record(d), timeline.load_cut(Path(r["xml"]))
    have = fc.layers_present(Path(r["xml"]))
    layers = [l for l in __import__("layers").find_layers(Path(r["xml"]))]
    sfx = [l for l in layers if "sfx" in l.name.lower()]
    before = (ct._finish_record(src) or {}).get("options", {}).get("music_db")
    now = (rec.get("options") or {}).get("music_db")
    return [("B1 music is quieter", now is not None and now < (fc.MUSIC_DB if before is None else before) - 1.0, f"music level {before} -> {now} dB"),
            ("B2 the music is still on", bool(have.get("music")), "music layer present"),
            ("B3 sound effects are gone", not sfx, f"effect layers left: {[l.name for l in sfx]}"),
            ("B4 every note reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


def case_c(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    notes = [{"timeline_sec": 10.0, "clip": 2, "text": "no music at all, remove the music", "shapes": []}]
    r, d = _run(src, out, "C_no_music", notes, [{"note": 1, "op": "remove_music"}])
    have = fc.layers_present(Path(r["xml"]))
    names = [l.name for l in __import__("layers").find_layers(Path(r["xml"]))]
    return [("C1 music is gone", not have.get("music") and not any("music" in n.lower() for n in names), f"layers: {sorted(set(names))}"),
            ("C2 captions and graphics are still on", any("captions" in n for n in names) and any("title" in n for n in names), f"layers: {sorted(set(names))}"),
            ("C3 reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


def case_d(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    g = _graphics(src)
    title = next(x for x in g if x["kind"] == "title")
    tags = [x for x in g if x["kind"] == "tag"]
    notes = [{"timeline_sec": title["on"] + 0.5, "clip": 1, "text": "remove the title card", "shapes": []},
             {"timeline_sec": tags[-1]["on"] + 0.5, "clip": 1, "text": "take out the call to action", "shapes": []}]
    ops = [{"note": 1, "op": "remove_graphic"}, {"note": 2, "op": "remove_graphic"}]
    r, d = _run(src, out, "D_remove_graphics", notes, ops)
    plan = _plan(d)
    if plan is None:
        return [("D RUNS", False, "the revision made no graphics plan: " + (r.get("message") or "")[:200])]
    names = [lt["name"] for lt in plan.get("lower_thirds", [])]
    return [("D1 title card is gone", not plan.get("title"), f"title: {plan.get('title')}"),
            ("D2 the call to action is gone", tags[-1]["name"] not in names, f"tags: {names}"),
            ("D3 the name tags stay", all(t["name"] in names for t in tags[:-1]), f"tags: {names}"),
            ("D4 reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


def case_e(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    cut = timeline.load_cut(src)
    notes = [{"timeline_sec": 12.0, "clip": 3, "text": 'fix the caption to say "TESTING A NEW CAPTION"', "shapes": []},
             {"timeline_sec": cut.video[3].tl_start + 1.0, "clip": 4, "text": "drop this clip", "shapes": []}]
    ops = [{"note": 1, "op": "edit_caption", "text": "TESTING A NEW CAPTION", "why": "battery"}, {"note": 2, "op": "drop_clip", "clip": 4, "why": "battery"}]
    r, d = _run(src, out, "E_footage_captions", notes, ops)
    new = timeline.load_cut(Path(r["xml"]))
    cap = d / "layers" / "captions" / "captions.json"
    cj = json.loads(cap.read_text()) if cap.is_file() else {"groups": []}
    names = [l.name for l in __import__("layers").find_layers(Path(r["xml"]))]
    return [("E1 the clip is gone", len(new.video) == len(cut.video) - 1 and new.zone_end < cut.zone_end - 1.0, f"{len(cut.video)} -> {len(new.video)} clips, {cut.zone_end:.1f} -> {new.zone_end:.1f} s"),
            ("E2 the caption has the new words", any("TESTING A NEW CAPTION" in json.dumps(g) for g in cj["groups"]), "captions.json"),
            ("E3 every layer came back on the shorter cut", all(any(k in n for n in names) for k in ("captions", "title", "music")), f"layers: {sorted(set(names))}"),
            ("E4 reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


def case_f(src: Path, out: Path) -> list[tuple[str, bool, str]]:
    cut = timeline.load_cut(src)
    z = 3                                                                  # the clip zoomed
    prev = (ct._finish_record(src) or {}).get("options", {})
    was_sfx = prev.get("sfx_name") or ct._sfx_named_in(ct._finish_record(src) or {})              # the effect the cut had (an older finish names it only in its summary)
    notes = [{"timeline_sec": cut.video[z - 1].tl_start + 1.0, "clip": z, "text": "zoom in on this shot a little", "shapes": []},
             {"timeline_sec": 2.0, "clip": 1, "text": "make the sound effect different", "shapes": []},
             {"timeline_sec": cut.video[1].tl_end - 0.5, "clip": 2, "text": "cut the last second off this clip", "shapes": []},
             {"timeline_sec": 6.9, "clip": 1, "text": "remove 6.74 to 7.10 seconds", "shapes": []}]
    ops = [{"note": 1, "op": "zoom_clip", "clip": z, "direction": "in", "amount": "little"}, {"note": 2, "op": "replace_sfx", "sound": "different"},
           {"note": 3, "op": "trim_end", "clip": 2, "seconds": 1.0}, {"note": 4, "op": "remove_range", "start": 6.74, "end": 7.10, "trusted": True}]
    r, d = _run(src, out, "F_zoom_sfx_trim", notes, ops)
    new = timeline.load_cut(Path(r["xml"]))
    rec = (_record(d).get("options") or {})
    was = cut.video[z - 1].motion[0] if cut.video[z - 1].motion else 0
    zoomed = [c for c in new.video if c.src_path == cut.video[z - 1].src_path and abs(c.src_in - cut.video[z - 1].src_in) < 0.5 and c.motion]
    return [("F1 the shot is closer", bool(zoomed) and zoomed[0].motion[0] > was * 1.1, f"scale {was:.1f}% -> {zoomed[0].motion[0] if zoomed else 'missing'}"),
            ("F2 a different sound effect", bool(rec.get("sfx_name")) and Path(rec["sfx_name"]).stem != Path(was_sfx or "none").stem, f"{was_sfx!r} -> {rec.get('sfx_name')!r}"),
            ("F3 the cut is shorter by the trim and the filler", abs((cut.zone_end - new.zone_end) - 1.36) < 0.15, f"{cut.zone_end:.2f} -> {new.zone_end:.2f} s (want about 1.36 s shorter)"),
            ("F4 the layers are all still on", all(fc.layers_present(Path(r["xml"])).get(k) for k in ("on_screen", "music")), str(fc.layers_present(Path(r["xml"])))),
            ("F5 reported made", all(i["applied"] for i in r["items"]), "; ".join(f"{i['note']}:{i['applied']}" for i in r["items"]))]


CASES = {"A": case_a, "B": case_b, "C": case_c, "D": case_d, "E": case_e, "F": case_f}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    out = args.out or Path.home() / "Library" / "Application Support" / "Post House" / "replays" / f"battery {time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, bool, str]] = []
    for key, fn in CASES.items():
        if args.only and key not in args.only.split(","):
            continue
        print(f"case {key} ...", flush=True)
        try:
            rows += fn(args.xml.expanduser().resolve(), out)
        except Exception as e:                                          # a case that cannot run is a failure, said in words
            rows.append((f"{key} RUNS", False, f"{type(e).__name__}: {str(e)[:300]}"))
    for n, ok, d in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {n}  {d}")
    failed = [n for n, ok, _d in rows if not ok]
    print(f"\n{len(failed)} failed of {len(rows)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
