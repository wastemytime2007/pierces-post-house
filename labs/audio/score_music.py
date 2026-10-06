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


def generate_plan(score: dict, cache: Path) -> tuple[Path, dict]:
    plan = to_plan(score)
    h = hashlib.sha1(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:12]
    out = cache / f"score_{h}.mp3"
    if out.exists() and out.stat().st_size > 1000:
        return out, {"cached": True, "file": out.name}
    req = urllib.request.Request(f"{ma.API}/music?output_format=mp3_44100_128", data=json.dumps({"composition_plan": plan, "model_id": "music_v1"}).encode(),
                                 headers={"xi-api-key": ma.load_key(), "Content-Type": "application/json"})
    try:
        data = urllib.request.urlopen(req, timeout=600).read()
    except urllib.error.HTTPError as e:
        raise ma.AudioError(f"ElevenLabs music refused ({e.code}): {e.read()[:400].decode(errors='replace')}")
    cache.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, {"cached": False, "file": out.name}


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
    ap.add_argument("--cache", type=Path, default=Path.home() / "Library/Application Support/Post House/music_cache")
    a = ap.parse_args()
    score = json.loads(a.plan.read_text())
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
