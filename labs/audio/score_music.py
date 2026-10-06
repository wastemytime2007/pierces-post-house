"""Music scored to a cut's story beats, not a bed that starts at 0 and stops somewhere.

Ryan, 2026-10-05, on a reel with a generic acoustic bed: "The music still doesnt feel intentional at all." A reel's music
should do something at the beats the picture has: stay out of the way of the hook, come in WITH the title card, carry the
story, thin out so the last line lands bare, and end on a resolved chord. ElevenLabs Music takes a composition plan (named
sections with a length and a description each), so the structure is asked for, and then MEASURED on the file that comes back.

    python3 labs/audio/score_music.py --plan score.json --out music.wav [--cache <dir>]

score.json: {"global": "<style for the whole piece>", "avoid": "<what not to have>",
             "sections": [{"name": "...", "seconds": 3.0, "style": "<this section>"}, ...]}
Writes the wav, `<out>.json` (the plan, section start times, and measured loudness per section) and prints one line per section.
Section lengths are 3 s to 120 s (the API's limit); the total is what the plan adds up to.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_audio as ma  # noqa: E402

MIN_SECTION, MAX_SECTION = 3.0, 120.0


def to_plan(score: dict) -> dict:
    """The ElevenLabs composition plan for a score. Refuses sections outside the API's 3 to 120 s."""
    for s in score["sections"]:
        if not MIN_SECTION <= s["seconds"] <= MAX_SECTION:
            raise ma.AudioError(f"section '{s['name']}' is {s['seconds']} s; the music API takes {MIN_SECTION} to {MAX_SECTION} s")
    glob = [g.strip() for g in score["global"].split(";") if g.strip()]
    avoid = [g.strip() for g in score.get("avoid", "").split(";") if g.strip()]
    return {
        "positive_global_styles": glob,
        "negative_global_styles": avoid,
        "sections": [{"section_name": s["name"], "positive_local_styles": [x.strip() for x in s["style"].split(";") if x.strip()],
                      "negative_local_styles": [], "duration_ms": int(round(s["seconds"] * 1000)), "lines": []} for s in score["sections"]],
    }


def generate_plan(score: dict, cache: Path, salt: str = "") -> tuple[Path, dict]:
    """One take of the plan. A different `salt` is a different take of the same plan (the plan sent is unchanged; only the cache name differs)."""
    plan = to_plan(score)
    h = hashlib.sha1((json.dumps(plan, sort_keys=True) + salt).encode()).hexdigest()[:12]
    out = cache / f"score_{h}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out, {"cached": True, "file": out.name, "salt": salt}
    req = urllib.request.Request(f"{ma.API}/music?output_format=mp3_44100_128", data=json.dumps({"composition_plan": plan, "model_id": "music_v1"}).encode(),
                                 headers={"xi-api-key": ma.load_key(), "Content-Type": "application/json"})
    data = None
    for attempt in range(5):                                                      # a busy service (429, 503) is retried with a wait; anything else is a real refusal
        try:
            data = urllib.request.urlopen(req, timeout=600).read()
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 4:
                print(f"ElevenLabs is busy ({e.code}); waiting {20 * (attempt + 1)} s and trying again", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
                continue
            raise ma.AudioError(f"ElevenLabs music refused ({e.code}): {e.read()[:400].decode(errors='replace')}")
    cache.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, {"cached": False, "file": out.name, "salt": salt}


def with_reference(score: dict, ref: dict) -> dict:
    """The score with the reference's measured feel put into its global style, in words from reference_music.describe (a fixed mapping from measurements; no song, artist or lyric is named) and its tempo (folded to
    a mid-range one, as reference_music does). The sections and their lengths are the score's own."""
    import reference_music as rm
    w = rm.describe({**ref, "bpm": rm.prompt_tempo(ref["bpm"])})
    words = [f"about {round(rm.prompt_tempo(ref['bpm']))} BPM", w["tempo"], rm.describe(ref)["tone"], rm.describe(ref)["rhythm"], rm.describe(ref)["dynamics"], *rm.describe(ref)["extra"], "instrumental", "leaves room for a speaking voice"]
    return {**score, "global": "; ".join([*words, score.get("global", "")]).strip("; ")}


def analyse_groove(path: Path) -> dict:
    """reference_music.analyze of the part of a take that gets USED: from the groove's drop to the start of its last 6 s (its sparse intro and its ringing last chord are not what plays under the speech). A take with no
    findable drop is analysed whole. Measuring the whole file made takes look brighter and steadier than the finished stem turned out to be."""
    import tempfile
    import reference_music as rm
    import conform_music as cm
    try:
        grid = cm.fit_grid(path)
        t0 = grid["b0"] + cm.find_drop(grid["strength"]) * grid["ibi"]
    except cm.ConformError:
        return rm.analyze(path)
    length = max(grid["seconds"] - 6.0 - t0, 8.0)
    with tempfile.TemporaryDirectory() as td:
        seg = Path(td) / "groove.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{length:.3f}", "-i", str(path), "-ar", "22050", str(seg)], check=True)
        return rm.analyze(seg)


def pick_take(score: dict, ref: dict, cache: Path, tries: int = 3, generate=None, analyse=None) -> tuple[Path, dict]:
    """Generate up to `tries` takes of the plan, measure each with reference_music.analyze, and keep the closest to the reference (stopping at the first that passes reference_music.closeness)."""
    import reference_music as rm
    generate = generate or generate_plan
    analyse = analyse or analyse_groove
    takes = []
    for k in range(tries):
        mp3, info = generate(score, cache, salt="" if k == 0 else f"take{k + 1}")
        got = analyse(mp3)
        c = rm.closeness(ref, got)
        c_final = rm.closeness(ref, got, dynamics=False)                  # make_audio's verifier re-measures the FINISHED stem without dynamics (the stem is shaped), so a take must pass that way too
        # a take is "like the reference" only when ALL four measured aspects hold: tempo, brightness, rhythmic density AND steadiness (the reference is a bed within about 4 dB; a take that swings 10 dB is not)
        c = {**c, "passed": bool(all(c["checks"].values()) and c_final["passed"]), "checks_without_dynamics": c_final["checks"]}
        takes.append({"file": str(mp3), "take": k + 1, "features": got, "closeness": c, "info": info})
        if c["passed"]:
            break
    best = max(takes, key=lambda t: (t["closeness"]["passed"], t["closeness"]["score"]))
    return Path(best["file"]), {"takes": takes, "chosen_take": best["take"], "passed": best["closeness"]["passed"]}


def section_levels(wav: Path, score: dict) -> list[dict]:
    """Loudness of each section of the file that came back, from where the plan says each one starts."""
    out, t = [], 0.0
    for s in score["sections"]:
        v = ma.volume(wav, t, s["seconds"])
        out.append({"name": s["name"], "start": round(t, 2), "seconds": s["seconds"], "mean_db": v["mean"], "peak_db": v["peak"]})
        t += s["seconds"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--reference-features", type=Path, help="reference_music.analyze output (json) of the music to feel like: its measured tempo, tone, rhythm and dynamics go into the style and the closest of --tries takes is kept")
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--cache", type=Path, default=Path.home() / "Library/Application Support/Post House/music_cache")
    a = ap.parse_args()
    score = json.loads(a.plan.read_text())
    if a.reference_features:
        ref = json.loads(a.reference_features.read_text())
        score = with_reference(score, ref)
        print("style:", score["global"])
        mp3, picked = pick_take(score, ref, a.cache, a.tries)
        info = {"cached": all(t["info"]["cached"] for t in picked["takes"]), "file": mp3.name, "reference": ref, **{k: v for k, v in picked.items() if k != "takes"},
                "takes": [{"take": t["take"], "closeness": t["closeness"], "features": t["features"]} for t in picked["takes"]]}
        for t in picked["takes"]:
            c = t["closeness"]
            print(f"  take {t['take']}: {t['features']['bpm']} bpm (ref {ref['bpm']}), tone {t['features']['tone_centre_hz']} Hz (ref {ref['tone_centre_hz']}), {t['features']['onsets_per_sec']} onsets/s (ref {ref['onsets_per_sec']}), spread {t['features']['dynamic_spread_db']} dB (ref {ref['dynamic_spread_db']}): {'PASS' if c['passed'] else 'not close enough'} {c['checks']}")
    else:
        mp3, info = generate_plan(score, a.cache)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-ar", "48000", "-ac", "2", str(a.out)], check=True)
    secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(a.out)], capture_output=True, text=True).stdout)
    levels = section_levels(a.out, score)
    planned = sum(s["seconds"] for s in score["sections"])
    meta = {"plan": to_plan(score), "generated": info, "planned_seconds": planned, "file_seconds": round(secs, 2), "sections": levels}
    a.out.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    print(f"{'cached' if info['cached'] else 'generated'} {mp3.name}: planned {planned:.1f} s, file {secs:.1f} s")
    for s in levels:
        print(f"  {s['name']:10} from {s['start']:5.1f} s for {s['seconds']:5.1f} s   mean {s['mean_db']:6.1f} dB  peak {s['peak_db']:6.1f} dB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
