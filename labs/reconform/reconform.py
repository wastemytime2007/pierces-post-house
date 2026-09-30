#!/usr/bin/env python3
"""Put the layers back on a revised cut.

    PRECUT_ROOT=~/precut-checkout python3 labs/reconform/reconform.py \
        --revised "<revise's output xml>" --overlay "<callout folder>" \
        --captions "<captions folder>" --audio "<audio folder>" --out "<folder>"

revise.py ripples every track alike, which keeps captions with the speech but cuts through a callout,
an effect or the music bed. This strips the layers off the revised XML (leaving exactly the revised
cut) and puts them back the way each one is anchored:

  callout   re-placed by the source frame it was drawn on (the same .mov, a new position)
  captions  rebuilt from the revised cut's own audio, so they match every new seam
  music     the same generated music, mixed and ducked again under the revised speech (nothing regenerated)
  effect    the same generated effect, at the callout's new time

Each layer keeps the settings it was built with. Every step is verified by its own tool, and the
result is checked as a whole. Then the review page is built from the new XML.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
for sub in ("review_loop", "overlay", "audio", "captions"):
    sys.path.insert(0, str(LABS / sub))
sys.path.insert(0, str(LABS.parent / "safety_net"))

import layers as ly  # noqa: E402
import place_audio as pa  # noqa: E402
import place_overlay as po  # noqa: E402
import timeline  # noqa: E402


class ReconformError(Exception):
    pass


def strip_layers(xml_in: Path, xml_out: Path) -> int:
    """The XML with every layer our tools placed removed: what is left is the cut itself. Returns clips removed."""
    root = ET.parse(xml_in).getroot()
    seq = timeline._seq_for_cut(root)
    removed = 0
    for kind, prefix in (("video", timeline.LAYER_VIDEO_PREFIX), ("audio", timeline.LAYER_AUDIO_PREFIX)):
        media = seq.find(f"media/{kind}")
        for track in list(media.findall("track")):
            mine = [c for c in track.findall("clipitem") if c.find("file") is not None and (c.find("file").get("id") or "").startswith(prefix)]
            if not mine:
                continue
            for c in mine:
                track.remove(c)
                removed += 1
            if not track.findall("clipitem"):
                media.remove(track)
    ET.indent(root, space="\t")
    xml_out.write_text(po.HEADER + ET.tostring(root, encoding="unicode") + "\n")
    return removed


def kept_end(layers: list[ly.Layer], mov_or_wav: Path) -> float | None:
    """Where the (ripple-shifted) pieces of one layer file end on the revised timeline: the new length of its window."""
    ends = [l.end for l in layers if Path(l.path).resolve() == mov_or_wav.resolve()]
    return max(ends) if ends else None


def replace_callout(xml_in: Path, xml_out: Path, folder: Path, out_dir: Path) -> dict:
    """The callout on the new cut, found by the frame it was drawn on. Returns the new placement.json's folder."""
    info = po.place(xml_in, xml_out, folder)
    rows = po.verify_placed(xml_in, xml_out, info, folder)
    bad = [n for n, ok, _d in rows if ok is False]
    if bad:
        raise ReconformError(f"the re-placed callout failed its checks: {', '.join(bad)}")
    pl = json.loads((folder / "placement.json").read_text())
    new = dict(pl)
    was = pl["place_overlay_on_timeline_at_sec"]
    new["place_overlay_on_timeline_at_sec"] = info["start"] / info["fps"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "placement.json").write_text(json.dumps(new, indent=2))
    return {"was_sec": was, "now_sec": new["place_overlay_on_timeline_at_sec"], "folder": str(out_dir)}


def run_captions(clean_xml: Path, out: Path, start: float, end: float, style: str, avoid: list[Path]) -> None:
    cmd = [sys.executable, str(LABS / "captions" / "make_captions.py"), "--xml", str(clean_xml), "--out", str(out),
           "--start", f"{start:.3f}", "--end", f"{end:.3f}", "--style", style]
    for a in avoid:
        cmd += ["--avoid", str(a)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout.rstrip())
    if p.returncode:
        raise ReconformError("rebuilding the captions failed:\n" + (p.stderr or p.stdout)[-800:])


def run_audio(clean_xml: Path, out: Path, meta: dict, base: Path, preview: Path, start: float, end: float,
              callout: Path, old_audio: Path) -> None:
    g = meta["generated"]
    cmd = [sys.executable, str(LABS / "audio" / "make_audio.py"), "--xml", str(clean_xml), "--base", str(base), "--preview-video", str(preview),
           "--out", str(out), "--start", f"{start:.3f}", "--end", f"{end:.3f}", "--callout", str(callout),
           "--sfx-prompt", g["sfx"]["prompt"], "--music-prompt", g["music"]["prompt"], "--music-ms", str(g["music"]["ms"]),
           "--music-db", str(meta["music_db_rel_speech"]), "--duck-db", str(meta["duck_db"]),
           "--sfx-below-peak-db", str(meta["sfx_below_speech_peak_db"]), "--cache", str(old_audio / "generated")]
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout.rstrip())
    if p.returncode:
        raise ReconformError("rebuilding the music and effect failed:\n" + (p.stderr or p.stdout)[-800:])


def reconform(revised: Path, overlays: list[Path], captions: Path | None, audio: Path | None, out: Path,
              captions_step=run_captions, audio_step=run_audio) -> tuple[Path, list[dict]]:
    """Returns (the new XML, a ledger with one entry per layer)."""
    out.mkdir(parents=True, exist_ok=True)
    ledger: list[dict] = []
    after = ly.find_layers(revised)
    clean = out / "_clean.xml"
    n = strip_layers(revised, clean)
    cut_before, cut_clean = timeline.load_cut(revised), timeline.load_cut(clean)
    same = ([(round(c.tl_start, 4), round(c.tl_end, 4), c.src_path, round(c.src_in, 3), round(c.src_out, 3)) for c in cut_before.video]
            == [(round(c.tl_start, 4), round(c.tl_end, 4), c.src_path, round(c.src_in, 3), round(c.src_out, 3)) for c in cut_clean.video]
            and len(cut_before.audio) == len(cut_clean.audio))
    if not same:
        raise ReconformError("stripping the layers changed the cut itself; refusing")
    ledger.append({"layer": "strip", "ok": True, "detail": f"removed {n} layer clip(s); the revised cut is unchanged ({len(cut_clean.video)} clips, {cut_clean.zone_end:.2f}s)"})

    cur = clean
    placed_dirs: list[Path] = []
    for i, folder in enumerate(overlays, start=1):
        nxt = out / f"_step_overlay{i}.xml"
        try:
            r = replace_callout(cur, nxt, folder, out / f"overlay_{i}")
            cur = nxt
            placed_dirs.append(Path(r["folder"]))
            ledger.append({"layer": f"callout {i}", "ok": True, "detail": f"re-placed by its frame: {r['was_sec']:.2f}s -> {r['now_sec']:.2f}s", **r})
        except po.PlaceError as e:
            ledger.append({"layer": f"callout {i}", "ok": False, "detail": f"dropped: {e}"})

    if captions is not None:
        cj = json.loads((captions / "captions.json").read_text())
        cp = json.loads((captions / "placement.json").read_text())
        end = kept_end(after, Path(cp["overlay_path"]))
        end = min(end, cut_clean.zone_end) if end else None
        if not end or end - cj["window"]["start"] < 1.0:
            ledger.append({"layer": "captions", "ok": False, "detail": "the revision left less than a second of the captions' window; not rebuilt"})
        else:
            cdir = out / "captions"
            captions_step(clean, cdir, cj["window"]["start"], end, cj["style"], placed_dirs)
            nxt = out / "_step_captions.xml"
            info = po.place(cur, nxt, cdir)
            bad = [n_ for n_, ok, _d in po.verify_placed(cur, nxt, info, cdir) if ok is False]
            if bad:
                raise ReconformError(f"the rebuilt captions failed placement checks: {', '.join(bad)}")
            cur = nxt
            new = json.loads((cdir / "captions.json").read_text())
            ledger.append({"layer": "captions", "ok": True, "detail": f"rebuilt from the revised audio for {cj['window']['start']:.1f}-{end:.1f}s: {len(new['groups'])} lines", "folder": str(cdir)})

    if audio is not None:
        aj = json.loads((audio / "audio.json").read_text())
        stem = Path(next(c["path"] for c in aj["clips"] if c["kind"] == "music"))
        end = kept_end(after, stem)
        end = min(end, cut_clean.zone_end) if end else None
        if not placed_dirs:
            ledger.append({"layer": "music and effect", "ok": False, "detail": "no callout is left to time the effect to; not rebuilt"})
        elif not end or end - aj["window"]["start"] < 1.0:
            ledger.append({"layer": "music and effect", "ok": False, "detail": "the revision left less than a second of the audio window; not rebuilt"})
        else:
            adir = out / "audio"
            base = out / "captions" / "cut_1080.mp4"
            preview = out / "captions" / "captions_preview.mp4"
            audio_step(clean, adir, aj, base, preview, aj["window"]["start"], end, placed_dirs[0], audio)
            nxt = out / "_step_audio.xml"
            info = pa.place(cur, nxt, adir)
            bad = [n_ for n_, ok, _d in pa.verify_placed(cur, nxt, info) if ok is False]
            if bad:
                raise ReconformError(f"the rebuilt audio failed placement checks: {', '.join(bad)}")
            cur = nxt
            ledger.append({"layer": "music and effect", "ok": True, "detail": f"same generated audio, mixed under the revised speech for {aj['window']['start']:.1f}-{end:.1f}s; effect at the callout's new time", "folder": str(adir)})

    final = out / revised.name
    final.write_text(cur.read_text())
    for tmp in out.glob("_*.xml"):
        tmp.unlink()
    return final, ledger


def check_result(final: Path, revised: Path, expect: dict) -> list[tuple[str, bool, str]]:
    """The whole result, from the XML: every layer whole (not cut through), the cut untouched, the effect with its callout."""
    rows: list[tuple[str, bool, str]] = []
    got = ly.find_layers(final)
    names = [l.name for l in got]
    rows.append(("LAYERS-WHOLE", len(names) == len(set(names)), f"{len(got)} layer(s), none split into pieces: {', '.join(dict.fromkeys(names))}"))
    warn = ly.layer_warnings(ly.find_layers(revised), got)
    rows.append(("info: REVISION-CUT-THROUGH", True, f"the revision had cut through {len(warn)} layer(s); rebuilt they are whole again" if warn else "the revision had cut through none of them"))
    cf, cr = timeline.load_cut(final), timeline.load_cut(revised)
    rows.append(("CUT-UNCHANGED", [(round(c.tl_start, 3), round(c.tl_end, 3), c.src_in) for c in cf.video] == [(round(c.tl_start, 3), round(c.tl_end, 3), c.src_in) for c in cr.video],
                 f"the revised cut is exactly as revise.py made it ({len(cf.video)} clips, {cf.zone_end:.2f}s)"))
    ov = [l for l in got if l.kind == "video" and l.name == "overlay.mov"]
    sfx = [l for l in got if l.kind == "audio" and l.name == "sfx_clip.wav"]
    if ov and sfx and expect.get("t_in") is not None:
        fps = cf.fps
        d = abs((ov[0].start + expect["t_in"]) - sfx[0].start)
        rows.append(("EFFECT-WITH-ITS-CALLOUT", d <= 1.5 / fps, f"the effect starts {d * 1000:.0f} ms from where the callout enters"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revised", type=Path, required=True, help="the XML revise.py wrote (its layers may have been cut through)")
    ap.add_argument("--overlay", type=Path, action="append", default=[], help="a callout folder (make_overlay.py output); repeatable")
    ap.add_argument("--captions", type=Path)
    ap.add_argument("--audio", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--height", type=int, default=540)
    a = ap.parse_args()
    try:
        final, ledger = reconform(a.revised, a.overlay, a.captions, a.audio, a.out)
    except (ReconformError, po.PlaceError, pa.PlaceError, timeline.TimelineError, OSError, KeyError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print("\nLayers:")
    for e in ledger:
        print(f"  [{'DONE' if e['ok'] else 'NOT DONE'}] {e['layer']}: {e['detail']}")
    t_in = None
    if a.overlay:
        try:
            t_in = json.loads((a.overlay[0] / "placement.json").read_text())["geometry"]["t_in"]
        except (OSError, KeyError):
            pass
    rows = check_result(final, a.revised, {"t_in": t_in})
    print("\nChecks on the result:")
    bad = 0
    for name, ok, detail in rows:
        bad += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if bad:
        print(f"\n{bad} check(s) FAILED. {final.name} is NOT verified, do not use it.", file=sys.stderr)
        return 1
    from build_review import build
    print("\nBuilding the review page with the rebuilt layers:")
    page = build(final, a.out / "review", a.height)
    (a.out / "reconform.json").write_text(json.dumps({"ledger": ledger, "xml": str(final)}, indent=2))
    print(f"\n{final}\n{page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
