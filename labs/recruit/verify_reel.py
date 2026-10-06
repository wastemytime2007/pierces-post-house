"""Verify a reel XML before it goes anywhere near Premiere (standalone; nothing in app/).

    PRECUT_ROOT=~/precut-checkout python3 labs/recruit/verify_reel.py --xml "<reel>.xml" --pitch p3 --resolved resolved.json --units "May15=<folder>" --units "Jun13=<folder>" --synced synced.json --mics "<recorder folder>" [--no-asr]

Structure (from the XML itself): the sequence runs at 29.97 fps; V1 and A1 hold the same cuts with identical start, end, in and out (picture and audio cannot drift apart); the cuts follow each other
with no gap; each cut's frames are what rough_cut asked for (within one frame); every source file is reachable; audio clips are enabled and carry a sourcetrack.
Content (from the media files, at the XML's own frame ranges): the audio Premiere will play is not silent; the opening and closing PHRASES are the intended ones with at most two stray words either side (speech recognition run
on exactly that frame range; for a weekend cut on the speaker's own recorder at the mapped time, because the camera microphone mishears hard phrases).
SYNC, measured: for a weekend cut the camera audio at the XML's frame range is cross-correlated (GCC-PHAT) against the speaker's recorder around the mapped time. The lag it finds must be under one frame (33 ms) and the peak
strong. This is a measurement of whether the audio in the XML is in the right place, not an assumption that it is.
Then `safety_net/verify_export.py` is run on the XML (rule 10). Exit 0 only if every check passes."""
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import rough_cut as rc          # noqa: E402
import sync_audio as sa         # noqa: E402

FPS = 30000 / 1001
MAX_LAG_SEC = 1.0 / FPS
MIN_SYNC_SCORE = 25.0
MIN_RMS_DB = -50.0
MAX_STRAY_WORDS = 2
REPO = HERE.parents[1]


def txt(e, tag):
    x = e.find(tag)
    return None if x is None else x.text


def read_xml(path: Path) -> dict:
    root = ET.parse(path).getroot()
    seq = next(root.iter("sequence"))
    rate = seq.find("rate")
    files = {}
    for f in root.iter("file"):
        pu = f.find("pathurl")
        if pu is not None:
            files[f.get("id")] = unquote(pu.text.replace("file://localhost", ""))

    def items(kind):
        tr = seq.find(f"media/{kind}/track")
        out = []
        for c in (tr.findall("clipitem") if tr is not None else []):
            fl = c.find("file")
            out.append({"name": txt(c, "name"), "start": int(txt(c, "start")), "end": int(txt(c, "end")), "in": int(txt(c, "in")), "out": int(txt(c, "out")), "enabled": txt(c, "enabled"),
                        "file": files.get(fl.get("id")) if fl is not None else None, "sourcetrack": c.find("sourcetrack") is not None})
        return out
    return {"name": txt(seq, "name"), "timebase": int(txt(rate, "timebase")), "ntsc": txt(rate, "ntsc"), "duration": int(txt(seq, "duration") or 0), "v": items("video"), "a": items("audio")}


def extract(path: str, t0: float, dur: float, sr: int) -> np.ndarray:
    return rc.read_audio(Path(path), t0, dur) if sr == rc.SR else sa.read_window(Path(path), t0, dur, sr)


def best_window(phrase_tokens: list[str], toks: list[str], lo: int, hi: int) -> tuple[int, int, float]:
    """(first index, last index, ratio) of the best match for `phrase_tokens` among consecutive tokens within toks[lo:hi]."""
    target = " ".join(phrase_tokens)
    best = (0, 0, 0.0)
    n = len(phrase_tokens)
    for i in range(max(0, lo), max(0, hi)):
        for w in range(max(1, n - 1), n + 2):
            if i + w > len(toks):
                break
            r = difflib.SequenceMatcher(None, target, " ".join(toks[i:i + w])).ratio()
            if r > best[2]:
                best = (i, i + w - 1, r)
    return best


def edges_at_valleys(sig: np.ndarray, pre: float, dur: float, sr: int = sa.SR) -> tuple[float, float, bool]:
    """(in-edge excess dB, out-edge excess dB, at_valleys) for the cut that lies in `sig` from `pre` seconds in for `dur` seconds. 'Excess' is how far the energy at the cut point (the 25 ms either side) is above the deepest
    valley within 0.15 s of it: 0 means the cut sits at the bottom of a valley (a real pause, or the dip between two words), a large number means it sits mid-sound (a clipped word or a click). At most 6 dB counts as at a valley.
    Continuous speech has only shallow valleys and a clean pause has deep ones, so this is judged against the cut's own surroundings, not against a fixed silence level. ADVISORY: a flagged cut is one to listen to."""
    f = int(0.025 * sr)
    w = int(0.15 * sr)
    db = lambda v: 20 * np.log10(max(float(v), 1e-6))                                                 # noqa: E731

    def excess(i: int) -> float:
        here = db(np.sqrt((sig[max(0, i - f):i + f] ** 2).mean())) if len(sig) > 2 * f else -90.0
        lo, hi = max(0, i - w), min(len(sig), i + w)
        deepest = min((db(np.sqrt((sig[k:k + f] ** 2).mean())) for k in range(lo, max(lo + 1, hi - f), max(1, f // 2))), default=here)
        return round(here - deepest, 1)
    a, b = excess(int(round(pre * sr))), excess(int(round((pre + dur) * sr)))
    return a, b, bool(a <= 6.0 and b <= 6.0)


def best_window(phrase_tokens: list[str], toks: list[str], lo: int, hi: int) -> tuple[int, int, float]:
    """(first index, last index, ratio) of the best match for `phrase_tokens` among consecutive tokens within toks[lo:hi]."""
    target = " ".join(phrase_tokens)
    best = (0, 0, 0.0)
    n = len(phrase_tokens)
    for i in range(max(0, lo), max(0, hi)):
        for w in range(max(1, n - 1), n + 2):
            if i + w > len(toks):
                break
            r = difflib.SequenceMatcher(None, target, " ".join(toks[i:i + w])).ratio()
            if r > best[2]:
                best = (i, i + w - 1, r)
    return best


def edges_quiet(sig: np.ndarray, pre: float, dur: float, sr: int = sa.SR) -> tuple[float, float, float, bool]:
    """(head dB, tail dB, limit dB, quiet?) for the cut that lies in `sig` from `pre` seconds in for `dur` seconds. The first and last 50 ms must be within 9 dB of THIS recording's own noise floor (the 10th percentile of
    its 30 ms frames over the whole window) and at least 6 dB under the speech: a word cut off mid-sound, or speech running on past the cut, puts sound at an edge. Judged on the speaker's own recorder when there is one:
    a chest-worn microphone has a clean floor (about -47 dB against speech at -22), a camera microphone outdoors does not."""
    n50 = int(0.05 * sr)
    i0, i1 = int(round(pre * sr)), int(round((pre + dur) * sr))
    cut = sig[i0:i1]
    frames = np.sqrt((sig[:len(sig) // 240 * 240].reshape(-1, 240) ** 2).mean(axis=1))
    cut_frames = np.sqrt((cut[:len(cut) // 240 * 240].reshape(-1, 240) ** 2).mean(axis=1))
    db = lambda v: 20 * np.log10(max(float(v), 1e-6))                                              # noqa: E731
    floor_db = db(np.percentile(frames[frames > 1e-6], 10)) if (frames > 1e-6).any() else -90.0
    level_db = db(np.median(cut_frames)) if len(cut_frames) else -90.0
    limit = min(floor_db + 9.0, level_db - 6.0)
    head = db(np.sqrt((cut[:n50] ** 2).mean())) if len(cut) >= n50 else -90.0
    tail = db(np.sqrt((cut[-n50:] ** 2).mean())) if len(cut) >= n50 else -90.0
    return round(head, 1), round(tail, 1), round(limit, 1), bool(head <= limit and tail <= limit)


def edge_check(heard: str, from_phrase: str, to_phrase: str) -> tuple[bool, int, int, float, float]:
    """(ok, stray words before, stray words after, opening ratio, closing ratio). The opening phrase must be found within the first few recognised words, the closing within the last few, each at 0.6 or better
    (one misheard word does not fail a phrase), with at most two recognised words outside them."""
    toks = rc.norm(heard).split()
    a, b = rc.norm(from_phrase).split(), rc.norm(to_phrase).split()
    if not toks or not a or not b:
        return False, 0, 0, 0.0, 0.0
    i0, _i1, r_start = best_window(a, toks, 0, min(len(toks), len(a) + 4))
    _j0, j1, r_end = best_window(b, toks, max(0, len(toks) - len(b) - 4), len(toks))
    before, after = i0, len(toks) - 1 - j1
    return (r_start >= 0.6 and r_end >= 0.6 and before <= MAX_STRAY_WORDS and after <= MAX_STRAY_WORDS), before, after, round(r_start, 2), round(r_end, 2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", required=True)
    ap.add_argument("--pitch", required=True)
    ap.add_argument("--resolved", required=True)
    ap.add_argument("--units", action="append", required=True)
    ap.add_argument("--synced", required=True)
    ap.add_argument("--mics", required=True)
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--report", help="write the report as json here")
    a = ap.parse_args(argv)
    x = read_xml(Path(a.xml))
    pitch = next(p for p in json.loads(Path(a.resolved).read_text())["pitches"] if p["key"] == a.pitch)
    cuts = pitch["cuts"]
    units = {}
    for sp in a.units:
        name, _, path = sp.partition("=")
        units[name] = {r["id"]: r for r in json.loads((Path(path).expanduser() / "selects.json").read_text())}
    synced = {d["id"]: d for d in json.loads(Path(a.synced).read_text())}
    rows, fails = [], 0

    def check(name, ok, detail):
        nonlocal fails
        rows.append((name, ok, detail))
        fails += 0 if ok else 1

    check("RATE", x["timebase"] == 30 and x["ntsc"] == "TRUE", f"{x['timebase']} fps timebase, ntsc {x['ntsc']} (29.97)")
    check("CUT-COUNT", len(x["v"]) == len(cuts) == len(x["a"]), f"{len(x['v'])} picture clips, {len(x['a'])} audio clips, {len(cuts)} cuts asked for")
    pair_bad = [i for i, (v, au) in enumerate(zip(x["v"], x["a"]), 1) if (v["start"], v["end"], v["in"], v["out"]) != (au["start"], au["end"], au["in"], au["out"])]
    check("PICTURE-AUDIO-PAIRED", not pair_bad, "every cut's audio has exactly its picture's start, end, in and out" if not pair_bad else f"cut(s) {pair_bad} differ between V1 and A1")
    gaps = [i for i in range(1, len(x["v"])) if x["v"][i]["start"] != x["v"][i - 1]["end"]] + ([0] if x["v"] and x["v"][0]["start"] != 0 else [])
    check("NO-GAPS", not gaps, "cuts run back to back from frame 0" if not gaps else f"gap or overlap at cut(s) {gaps}")
    dev = [max(abs(v["in"] - round(c["in_sec"] * FPS)), abs(v["out"] - round(c["out_sec"] * FPS))) for v, c in zip(x["v"], cuts)]
    check("FRAMES-AS-ASKED", all(d <= 1 for d in dev), f"largest difference from the resolved in/out: {max(dev) if dev else 0} frame(s)")
    check("SEQUENCE-LENGTH", x["v"] and x["duration"] in (x["v"][-1]["end"], x["v"][-1]["end"] + 0), f"{x['duration']} frames = {x['duration'] / FPS:.2f} s" if x["v"] else "empty")
    missing = sorted({v["file"] for v in x["v"] if not v["file"] or not Path(v["file"]).exists()})
    check("FILES-REACHABLE", not missing, "every source file is where the XML says" if not missing else f"missing: {missing}")
    check("AUDIO-ENABLED+SOURCETRACK", all(au["enabled"] == "TRUE" and au["sourcetrack"] for au in x["a"]), "audio clips enabled, each with a sourcetrack")

    content = []
    for i, (v, c) in enumerate(zip(x["v"], cuts), 1):
        t0, dur = v["in"] / FPS, (v["out"] - v["in"]) / FPS
        cam = sa.read_window(Path(v["file"]), t0, dur, sa.SR)
        rms_db = 20 * np.log10(max(1e-6, float(np.sqrt((cam ** 2).mean()))))
        item = {"cut": i, "id": c["id"], "role": c["role"], "seconds": round(dur, 2), "rms_db": round(rms_db, 1)}
        row = units[c["pitch_unit"]][c["id"]]
        s = synced.get(c["id"]) if c["pitch_unit"] != "May15" else None
        if s and s.get("synced"):
            delta = s["camera_start"] - row["speech_start"]
            lav = Path(a.mics) / f"{row['clip']}.WAV"
            lt0 = t0 - delta
            win = sa.read_window(lav, max(0.0, lt0 - 1.0), dur + 2.0, sa.SR)
            pre = min(1.0, lt0)                                                              # the window began `pre` seconds before the mapped start
            n = sa.next_fast(len(win) + len(cam))
            off, score = sa.gcc_phat(sa._fft.rfft(win.astype(np.float32), n), n, cam)
            item.update(lag_ms=round((off - pre) * 1000, 1), sync_score=round(score, 1), recorder=row["clip"])
            asr_audio = (lav, lt0)
            h_ex, t_ex, q_ok = edges_at_valleys(win, pre, dur)                                 # judged on the speaker's own recorder
            edge_on = "recorder"
        else:
            asr_audio = (Path(v["file"]), t0)
            wide = sa.read_window(Path(v["file"]), max(0.0, t0 - 1.5), dur + 3.0, sa.SR)
            h_ex, t_ex, q_ok = edges_at_valleys(wide, min(1.5, t0), dur)                       # no recorder for this cut: the camera's own audio
            edge_on = "camera"
        item.update(edge_in_excess_db=h_ex, edge_out_excess_db=t_ex, edges_quiet=q_ok, edges_judged_on=edge_on)
        if not a.no_asr:
            import whisper
            if "_model" not in globals():
                globals()["_model"] = whisper.load_model("small")
            audio = rc.read_audio(asr_audio[0], asr_audio[1], dur)
            heard = " ".join(w[0] for w in rc.transcribe_words(globals()["_model"], audio, 0.0))
            ok, before, after, r_start, r_end = edge_check(heard, c["from"], c.get("to", c["from"]))
            item.update(heard=heard, edges_ok=ok, stray_before=before, stray_after=after, ratios=[r_start, r_end], heard_on="recorder" if asr_audio[0] != Path(v["file"]) else "camera")
        content.append(item)
    check("AUDIO-NOT-SILENT", all(it["rms_db"] >= MIN_RMS_DB for it in content), "quietest cut: %.1f dBFS RMS" % min(it["rms_db"] for it in content) if content else "no clips")
    mid_sound = [it["cut"] for it in content if not it["edges_quiet"]]
    rows.append(("CUT-POINTS-AT-VALLEYS (advisory)", None, "every cut starts and ends at the bottom of a pause or a dip between words" if not mid_sound else f"cut(s) {mid_sound} start or end mid-sound: listen to them (a clipped consonant or a click)"))
    syn = [it for it in content if "lag_ms" in it]
    if syn:
        check("SYNC-MEASURED", all(abs(it["lag_ms"]) < MAX_LAG_SEC * 1000 and it["sync_score"] >= MIN_SYNC_SCORE for it in syn),
              f"{len(syn)} weekend cut(s): camera audio vs the speaker's recorder, worst lag {max(abs(it['lag_ms']) for it in syn):.1f} ms (limit {MAX_LAG_SEC * 1000:.0f} ms), weakest score {min(it['sync_score'] for it in syn):.0f}")
    if not a.no_asr:
        bad = [it["cut"] for it in content if not it["edges_ok"]]
        check("CUT-EDGES-ON-THE-RIGHT-WORDS", not bad, "first and last words are the intended ones, at most 2 stray words either side" if not bad else f"cut(s) {bad} do not start or end on the intended words")

    ve = subprocess.run([sys.executable, str(REPO / "safety_net" / "verify_export.py"), a.xml], capture_output=True, text=True)
    ve_lines = [l.strip() for l in ve.stdout.splitlines() if l.strip().startswith("[")]
    check("SAFETY-NET verify_export", ve.returncode == 0, "; ".join(l for l in ve_lines if "[FAIL]" in l) or f"{len(ve_lines)} checks, none failed")

    print(f"\n{Path(a.xml).name}: {x['duration'] / FPS:.1f} s, {len(cuts)} cuts")
    for name, ok, detail in rows:
        print(f"  [{'INFO' if ok is None else 'PASS' if ok else 'FAIL'}] {name:30} {detail}")
    for it in content:
        extra = f" lag {it['lag_ms']:+.1f} ms score {it['sync_score']}" if "lag_ms" in it else " (no recorder sync for this cut)"
        edge = ("" if a.no_asr else f" phrases {'ok' if it['edges_ok'] else 'BAD'} (+{it['stray_before']}/{it['stray_after']}, {it['ratios']}) on {it['heard_on']}") + f" cut points {it['edge_in_excess_db']:+.0f}/{it['edge_out_excess_db']:+.0f} dB above the nearest valley ({it['edges_judged_on']}){'' if it['edges_quiet'] else ' MID-SOUND'}"
        print(f"     cut {it['cut']} {it['id']:4} {it['seconds']:5.1f} s {it['rms_db']:6.1f} dB{extra}{edge}")
    if a.report:
        Path(a.report).write_text(json.dumps({"xml": a.xml, "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in rows], "cuts": content}, indent=1))
    print("ALL CHECKS PASSED" if not fails else f"{fails} CHECK(S) FAILED: do not ship this XML")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
