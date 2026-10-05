"""From located moments to something Ryan can open: a verified-quotes log, a preview clip per moment, and a page (standalone; nothing in app/).

    python3 labs/recruit/build_selects.py --located located.json --headlines headlines.json --srt "<transcripts>" --sources sources.json --out "<folder>" [--used "<finished video .srt>"]

1. Each moment's HEADLINE (one sentence, copied from its located text) is checked to be a verbatim part of that text and is timed to the segment it sits in.
2. A log in the verified-quotes format is written and `~/.claude/skills/verified-quotes/scripts/verify_quotes.py` is run on it against the SAME transcripts: the quote must exist in the named transcript within 8 s of its timecode.
3. A preview clip (video, or audio for audio-only moments) is cut for each moment from its source, `sources.json` mapping a transcript name to the media file to cut from.
4. `selects.html`: cards grouped by audience, each with the clip, the verified headline, why it helps, flags, how it was heard, and whether the finished video (`--used`) already used it.

Nothing is placed or cut into a project. The page says so, and says where the transcript may have misheard (Whisper small, English set, not checked against the audio by a person)."""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import find_moments as fm

VERIFY = Path.home() / ".claude" / "skills" / "verified-quotes" / "scripts" / "verify_quotes.py"


def tc(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}"


def headline_time(headline: str, segs: list[dict]) -> float | None:
    """Start of the segment (or first of up to 3 consecutive segments) that contains the headline's opening words, or None if the headline is not in the transcript."""
    h = fm.norm(headline)
    first = " ".join(h.split()[:3])
    for i in range(len(segs)):
        for w in range(1, 4):
            if h in fm.norm(" ".join(s["text"] for s in segs[i:i + w])):
                for j in range(i, i + w):                      # the segment the headline BEGINS in, not the start of the window: the verifier allows 8 s either way
                    if first in fm.norm(segs[j]["text"]) or j == i + w - 1:
                        return segs[j]["start"]
    return None


def claim_lines(clip: str, t: float, headline: str) -> list[str]:
    """The two log lines for one claim, in the shape the verifier's parser actually reads: a line holding a backticked .srt name is a SOURCE declaration for the lines after it
    (not a claim, whatever the skill's example shows), so the file is named on its own Sources line and the claim line carries only the timecode and the quote."""
    return [f"Sources: `{clip}.srt`", f'- [{tc(t)}] "{headline}"']


def choose_media(r: dict, sources: dict, synced: dict | None) -> tuple[Path | None, float, float, bool, str]:
    """(file, start, end, audio_only, how) to cut for one moment. A moment synced to a camera is cut from that camera clip at the matched time (same length as the transcript clip, padding kept);
    otherwise it is cut from the file its transcript came from (audio only when that is a sound file); nothing is cut when no source file is known."""
    sy = (synced or {}).get(r["id"])
    if sy and sy.get("synced") and sources.get(sy["camera"]):
        pad = r["speech_start"] - r["start"]
        cs = max(0.0, sy["camera_start"] - pad)
        return Path(sources[sy["camera"]]), cs, cs + (r["end"] - r["start"]), False, f"picture from camera clip {sy['camera']}, synced by audio (score {sy['score']})"
    src = sources.get(r["clip"])
    if not src:
        return None, r["start"], r["end"], False, "no source file known"
    audio = Path(src).suffix.lower() in (".wav", ".mp3", ".m4a")
    return Path(src), r["start"], r["end"], audio, ("audio only: no camera match found" if audio and synced is not None else "")


def where_text(r: dict, synced: dict | None) -> str:
    """Where to find the moment: the camera clip and time when it is synced, else the clip its words came from."""
    dur = r["speech_end"] - r["speech_start"]
    sy = (synced or {}).get(r["id"])
    if sy and sy.get("synced"):
        return f"{sy['camera']} {tc(sy['camera_start'])} to {tc(sy['camera_start'] + dur)} (heard on {r['clip']})"
    return f"{r['clip']} {tc(r['speech_start'])} to {tc(r['speech_end'])}"


def mic_note(r: dict) -> str:
    """The speaker clue as a sentence, hedged the way the evidence allows."""
    who = r.get("mic")
    if who in (None, ""):
        return ""
    if who == "unclear":
        return "Speaker unclear: both people's microphones heard this about equally."
    gap = r.get("mic_gap")
    return f"Likely {who}: their own microphone was the clearer one" + (f" (confidence gap {gap})" if gap is not None else " (only their microphone had it)") + ". A clue, not a fact: listen."


def cut(src: Path, start: float, end: float, dst: Path, audio_only: bool) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if audio_only:
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-to", f"{end}", "-i", str(src), "-vn", "-ac", "1", "-b:a", "64k", str(dst)]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-to", f"{end}", "-i", str(src), "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
               "-c:a", "aac", "-b:a", "80k", str(dst)]
    return subprocess.run(cmd, capture_output=True).returncode == 0 and dst.exists()


def used_in(final_srt: Path | None, headline: str, text: str) -> str | None:
    """The finished video's own words if it contains the headline or a long run of the moment's words, else None."""
    if not final_srt or not final_srt.exists():
        return None
    final = fm.norm(" ".join(s["text"] for s in fm.parse_srt(final_srt)))
    h = fm.norm(headline)
    if h and h in final:
        return "the finished video says this line"
    words = fm.norm(text).split()
    for i in range(0, max(1, len(words) - 7)):
        if " ".join(words[i:i + 8]) in final:
            return "the finished video uses a passage from this moment"
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--located", required=True)
    ap.add_argument("--headlines", required=True)
    ap.add_argument("--srt", required=True)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--used", help="the finished video's .srt, to mark what it already used")
    ap.add_argument("--title", default="Recruitment selects", help="the page's heading")
    ap.add_argument("--intro", default="", help="one or two sentences saying which footage this page is about")
    ap.add_argument("--reuse", action="store_true", help="keep clips already cut in <out>/clips instead of cutting them again (to change a note or a headline without waiting for 4K video)")
    ap.add_argument("--synced", help="synced.json from sync_audio.py: moments matched to a camera clip get their picture from it")
    ap.add_argument("--notes", help="json {id: {heard: '...', speakers: '...'}} written by hand from reading the sequence transcript")
    a = ap.parse_args(argv)
    out, srt_dir = Path(a.out).expanduser(), Path(a.srt).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    clips = fm.load_clips(srt_dir)
    located = [m for m in json.loads(Path(a.located).read_text()) if m.get("found")]
    heads = json.loads(Path(a.headlines).read_text())
    sources = json.loads(Path(a.sources).read_text())
    notes = json.loads(Path(a.notes).read_text()) if a.notes else {}
    synced = {d["id"]: d for d in json.loads(Path(a.synced).read_text())} if a.synced else None

    log, rows = ["# Recruitment selects: quotes to verify", ""], []
    for m in located:
        h = heads.get(m["id"], "")
        t = headline_time(h, clips[m["clip"]]) if h else None
        ok = bool(h) and fm.norm(h) in fm.norm(m["text"]) and t is not None
        rows.append({**m, "headline": h, "headline_in_text": ok, "headline_time": t})
        if ok:
            # the verifier reads a line holding a backticked .srt name as a SOURCE declaration, not a claim (its docstring example would not parse), so each claim gets its own Sources line
            log += claim_lines(m["clip"], t, h)
    (out / "selects_log.md").write_text("\n".join(log) + "\n")
    v = subprocess.run([sys.executable, str(VERIFY), "--transcripts", str(srt_dir), str(out / "selects_log.md"), "--quiet"], capture_output=True, text=True)
    ver = (out / "selects_log.verification.md").read_text() if (out / "selects_log.verification.md").exists() else ""
    status = {}
    checked = int((re.search(r"Total claims checked:\s*(\d+)", ver) or [0, 0])[1])
    flagged = [l for l in ver.split("## Flagged", 1)[-1].splitlines() if l.startswith("- L")] if "## Flagged" in ver else []
    n_expected = sum(1 for r in rows if r["headline_in_text"])
    for r in rows:
        if not r["headline_in_text"]:
            status[r["id"]] = "NOT_CHECKED"
            continue
        line = next((l for l in flagged if r["headline"][:40] in l), "")
        if line:
            status[r["id"]] = next((s for s in ("TIMECODE_MISMATCH", "NOT_FOUND", "NO_SOURCE_MATCHED", "UNPARSEABLE_TIMECODE", "SKIPPED_SHORT") if s in line), "UNKNOWN")
        else:
            status[r["id"]] = "VERIFIED" if checked == n_expected and n_expected else "UNKNOWN"      # not flagged, and the verifier read every claim I wrote
    counts = {k: sum(1 for v_ in status.values() if v_ == k) for k in set(status.values())}
    print("verification:", counts, "| verifier exit", v.returncode)

    used = Path(a.used).expanduser() if a.used else None
    for r in rows:
        src, c0, c1, audio, how = choose_media(r, sources, synced)
        r["clip_file"], r["media_note"], r["where"] = None, how, where_text(r, synced)
        if src and src.exists():
            dst = out / "clips" / f"{r['id']}{'.m4a' if audio else '.mp4'}"
            if (a.reuse and dst.exists() and dst.stat().st_size > 0) or cut(src, c0, c1, dst, audio):
                r["clip_file"] = f"clips/{dst.name}"
                r["audio_only"] = audio
        r["used"] = used_in(used, r["headline"], r["text"])
        r["status"] = status.get(r["id"], "NOT_CHECKED")
        r.update(notes.get(r["id"], {}))
        if not r.get("speakers") and mic_note(r):
            r["speakers"] = mic_note(r)
    (out / "selects.json").write_text(json.dumps(rows, indent=1))

    esc = html.escape
    css = ("body{font:15px -apple-system,Helvetica,sans-serif;margin:0;background:#f6f8fa;color:#033459}header{background:#033459;color:#fff;padding:22px 28px}h1{margin:0 0 6px;font-size:24px}"
           ".sub{max-width:900px;opacity:.9}main{padding:20px 28px;max-width:1100px}h2{margin:28px 0 8px;border-bottom:2px solid #0391d8;padding-bottom:4px}"
           ".card{background:#fff;border:1px solid #d7dde3;border-radius:8px;padding:14px 16px;margin:12px 0;display:grid;grid-template-columns:340px 1fr;gap:16px}"
           "@media(max-width:800px){.card{grid-template-columns:1fr}}video,audio{width:100%;border-radius:6px;background:#000}.q{font-size:17px;font-weight:600;margin:0 0 6px}"
           ".tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:9px;color:#fff;margin-right:6px}.t-sub{background:#0391d8}.t-fr{background:#033459}.t-both{background:#00ade1}"
           ".ok{color:#0a7d2c;font-weight:600}.warn{background:#fff3e8;border-left:4px solid #f4690b;padding:4px 8px;margin:6px 0;font-size:13px}.used{background:#eef;border-left:4px solid #888;padding:4px 8px;margin:6px 0;font-size:13px}"
           ".meta{font-size:12px;color:#555}details{margin-top:6px}summary{cursor:pointer;color:#0391d8}.no{opacity:.8}")
    groups = [("subcontractors", "Hiring subcontractors", "t-sub"), ("franchisees", "Recruiting franchisees and partners", "t-fr"), ("both", "Works for both", "t-both")]
    n_ver = sum(1 for r in rows if r["status"] == "VERIFIED")
    parts = [f"<!doctype html><meta charset=utf-8><title>{esc(a.title)}</title><style>{css}</style><header><h1>{esc(a.title)}</h1>"
             f"<div class=sub>{esc(a.intro)} Moments that could help recruit franchisees and hire subcontractors. Nothing is cut into a project. Each clip plays the moment with a second or so either side. "
             f"Headlines are copied from the transcript and checked against it ({n_ver} of {len(rows)} verified). The transcript is Whisper's, so a word can be wrong: where two readings disagree the card says so, "
             f"and the clip is the final word.</div></header><main>"]
    for key, title, cls in groups:
        sel = [r for r in rows if r["audience"] == key]
        if not sel:
            continue
        parts.append(f"<h2>{esc(title)} ({len(sel)})</h2>")
        for r in sel:
            media = ""
            if r.get("clip_file"):
                media = (f"<audio controls preload=none src=\"{esc(r['clip_file'])}\"></audio><div class=warn>{esc(r.get('audio_only_note') or r.get('media_note') or 'Audio only: no picture for this moment.')}</div>" if r.get("audio_only")
                         else f"<video controls preload=metadata src=\"{esc(r['clip_file'])}\"></video>")
            else:
                media = "<div class=meta>no clip could be cut</div>"
            fl = "".join(f"<div class=warn>{esc(f)}</div>" for f in r.get("flags", []))
            us = f"<div class=used>Already used: {esc(r['used'])}.</div>" if r.get("used") else ""
            heard = f"<div class=warn>Heard differently: {esc(r['heard'])}</div>" if r.get("heard") else ""
            sp = f"<div class=meta>Speaker: {esc(r['speakers'])}</div>" if r.get("speakers") else ""
            ver = "<span class=ok>verified</span>" if r["status"] == "VERIFIED" else f"<span class=meta>{esc(r['status'].lower().replace('_', ' '))}</span>"
            parts.append(f"<div class=card><div>{media}<div class=meta>{esc(r['where'])}</div></div><div>"
                         f"<span class='tag {cls}'>{esc(r['id'])}</span><b>{esc(r['theme'])}</b> &middot; {ver}"
                         f"<p class=q>&ldquo;{esc(r['headline'])}&rdquo;</p><div>{esc(r['why'])}</div>{us}{fl}{heard}{sp}"
                         f"<details><summary>Everything said in this moment (transcript)</summary><p>{esc(r['text'])}</p></details></div></div>")
    parts.append("</main>")
    (out / "selects.html").write_text("".join(parts))
    print(f"{sum(1 for r in rows if r.get('clip_file'))} of {len(rows)} clips cut -> {out / 'selects.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
