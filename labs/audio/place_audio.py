#!/usr/bin/env python3
"""Add a built audio folder (music stem and effect clip) to an export XML as new audio tracks.

    python3 labs/audio/place_audio.py <export.xml> <audio folder> --out <new.xml>

Each stereo clip goes on two new tracks, left and right, the way the existing tracks carry a file's
channels. The speech tracks, the picture tracks and the markers are not touched, and the result is
refused unless every check passes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "overlay"))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parents[1] / "safety_net"))

import place_overlay as po  # noqa: E402  (E, seq_spec, _ser, HEADER)
import timeline  # noqa: E402
import verify_export  # noqa: E402
from posthouse.benchmark import _decode_pathurl  # noqa: E402


class PlaceError(Exception):
    pass


def probe_wav(path: Path) -> dict:
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels:format=duration",
                                   "-of", "json", str(path)], capture_output=True, text=True).stdout)
    s = j["streams"][0]
    return {"sr": int(s["sample_rate"]), "ch": int(s["channels"]), "dur": float(j["format"]["duration"])}


def audio_rate(seq: ET.Element) -> int:
    v = seq.findtext("media/audio/format/samplecharacteristics/samplerate")
    if not v:
        raise PlaceError("the sequence declares no audio sample rate, so the clips cannot be matched to it")
    return int(v)


def place(xml_in: Path, xml_out: Path, folder: Path) -> dict:
    meta = json.loads((folder / "placement.json").read_text())
    root = ET.parse(xml_in).getroot()
    seq = timeline._seq_for_cut(root)
    sp = po.seq_spec(seq)
    cut = timeline.load_cut(xml_in)
    sr = audio_rate(seq)
    ids = {el.get("id") for el in root.iter() if el.get("id")}
    audio = seq.find("media/audio")
    clips, k = [], 1
    for c in meta["clips"]:
        path = Path(c["path"])
        if not path.exists():
            raise PlaceError(f"the audio file is missing: {path}")
        pw = probe_wav(path)
        if (pw["sr"], pw["ch"]) != (sr, 2):
            raise PlaceError(f"{path.name} is {pw['sr']} Hz, {pw['ch']} channel(s); the sequence is {sr} Hz and this places stereo files")
        n = round(pw["dur"] * sp["fps"])
        start = round(c["start_sec"] * sp["fps"])
        if start < 0 or start + n > round(cut.zone_end * sp["fps"]) + 1:
            raise PlaceError(f"{c['name']} would run outside the cut")
        while f"audio-file-{k}" in ids:
            k += 1
        fid = f"audio-file-{k}"
        ids.add(fid)
        for ch in (1, 2):
            track = po.E(audio, "track")
            ci = po.E(track, "clipitem", id=f"audio-clipitem-{k}-{ch}")
            po.E(ci, "name", path.name)
            po.E(ci, "enabled", "TRUE")
            po.E(ci, "duration", n)
            r = po.E(ci, "rate"); po.E(r, "timebase", sp["timebase"]); po.E(r, "ntsc", "TRUE" if sp["ntsc"] else "FALSE")
            po.E(ci, "start", start); po.E(ci, "end", start + n); po.E(ci, "in", 0); po.E(ci, "out", n)
            if ch == 1:
                f = po.E(ci, "file", id=fid)
                po.E(f, "name", path.name)
                po.E(f, "pathurl", "file://localhost" + quote(str(path.resolve()), safe="/"))
                fr = po.E(f, "rate"); po.E(fr, "timebase", sp["timebase"]); po.E(fr, "ntsc", "TRUE" if sp["ntsc"] else "FALSE")
                po.E(f, "duration", n)
                media_audio = po.E(po.E(f, "media"), "audio")
                sc = po.E(media_audio, "samplecharacteristics")
                po.E(sc, "depth", 16); po.E(sc, "samplerate", sr)
                po.E(media_audio, "channelcount", 2)
            else:
                po.E(ci, "file", id=fid)
            st = po.E(ci, "sourcetrack"); po.E(st, "mediatype", "audio"); po.E(st, "trackindex", ch)
            po.E(track, "enabled", "TRUE"); po.E(track, "locked", "FALSE")
        clips.append({"name": c["name"], "kind": c["kind"], "start": start, "frames": n, "path": path, "start_sec": c["start_sec"]})
        k += 1

    ET.indent(root, space="\t")
    xml_out.write_text(po.HEADER + ET.tostring(root, encoding="unicode") + "\n")
    return {"fps": sp["fps"], "clips": clips}


def verify_placed(xml_in: Path, xml_out: Path, info: dict) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    a, b = ET.parse(xml_in).getroot(), ET.parse(xml_out).getroot()
    sa, sb = timeline._seq_for_cut(a), timeline._seq_for_cut(b)
    ta, tb_ = sa.findall("media/audio/track"), sb.findall("media/audio/track")
    same = (po._ser(ta) == po._ser(tb_[:len(ta)]) and po._ser(sa.findall("media/video/track")) == po._ser(sb.findall("media/video/track"))
            and po._ser(sa.findall("marker")) == po._ser(sb.findall("marker")))
    rows.append(("REST-UNCHANGED", same, "every existing audio track, picture track and marker is structurally identical to the input (whitespace ignored)" if same else "the existing timeline changed"))
    new = tb_[len(ta):]
    want = 2 * len(info["clips"])
    rows.append(("NEW-TRACKS", len(new) == want and all(len(t.findall("clipitem")) == 1 for t in new), f"{len(new)} new audio track(s), one clip each ({len(info['clips'])} stereo clip(s), left and right)"))
    fps = info["fps"]
    cut2 = timeline.load_cut(xml_out)
    for i, c in enumerate(info["clips"]):
        pair = new[2 * i:2 * i + 2]
        cis = [t.find("clipitem") for t in pair]
        vals = [tuple(int(ci.findtext(x)) for x in ("start", "end", "in", "out")) for ci in cis]
        fdur = int(cis[0].find("file").findtext("duration"))
        ok = vals[0] == vals[1] and (vals[0][1] - vals[0][0]) == (vals[0][3] - vals[0][2]) == fdur == c["frames"] and vals[0][2] == 0 and vals[0][0] == c["start"]
        rows.append((f"FRAME-EXACT ({c['kind']})", ok, f"starts at frame {vals[0][0]} ({vals[0][0] / fps:.3f}s; wanted {c['start_sec']:.3f}s), {vals[0][1] - vals[0][0]} frames on the timeline = {vals[0][3] - vals[0][2]} in the file = {fdur} in its definition, both channels alike"))
        rows.append((f"SAMPLES ({c['kind']})", cis[0].find("file/media/audio/samplecharacteristics/samplerate").text == str(audio_rate(sb)), "the file's sample rate matches the sequence's"))
        path = _decode_pathurl(cis[0].find("file").findtext("pathurl"))[0]
        rows.append((f"FILE-REACHABLE ({c['kind']})", Path(path) == c["path"].resolve() and Path(path).exists(), path))
        rows.append((f"INSIDE-THE-CUT ({c['kind']})", vals[0][1] <= round(cut2.zone_end * fps) + 1, f"ends at {vals[0][1] / fps:.2f}s; the cut ends at {cut2.zone_end:.2f}s"))
    import export_gate
    rows.append(export_gate.row(xml_out))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    try:
        info = place(a.xml, a.out, a.folder)
    except (PlaceError, timeline.TimelineError, KeyError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    rows = verify_placed(a.xml, a.out, info)
    bad = 0
    for name, ok, detail in rows:
        bad += ok is False
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if bad:
        print(f"\n{bad} check(s) FAILED. {a.out.name} is NOT verified, do not use it.")
        return 1
    print(f"\n{a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
