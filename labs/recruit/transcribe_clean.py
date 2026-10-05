"""Clean per-clip transcripts: English set, hallucinations on silence filtered, SRT and JSON per file (standalone; nothing in app/).

    python3 labs/recruit/transcribe_clean.py --src "<a media file or a folder of them>" --out "<folder>" [--model small]

Why this exists: the earlier WhisperX transcripts of the Runnells weekend interview were 66 to 97 percent repeated-phrase loops (`language` left on auto-detect, per `docs/reference/RUNNELLS_CONTENT_INVENTORY.md`),
so most of what was said was missing. Here the language is set to English, each clip is transcribed with `condition_on_previous_text=False`, and a segment is dropped when Whisper itself says it was probably
silence (no_speech_prob over 0.6 AND avg_logprob under -1.0) or when the same text would appear a third time in a row. Per file: `<stem>.srt`, and `<stem>.json` holding every kept segment with its
no_speech_prob and avg_logprob (the confidence that pick_mic.py reads) and how many were dropped. A file that already has an .srt is skipped."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

MEDIA = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".mxf", ".aif", ".aiff"}


def ts(t: float) -> str:
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}".replace(".", ",")


def keep(seg: dict, prev_texts: list[str]) -> bool:
    """False for a segment Whisper itself doubts was speech, or that would be the same text three times running."""
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
        return False
    t = seg["text"].strip().lower()
    return not (len(prev_texts) >= 2 and prev_texts[-1] == t and prev_texts[-2] == t)


def filter_segments(segments: list[dict]) -> tuple[list[dict], int]:
    kept, prev, dropped = [], [], 0
    for s in segments:
        if not s["text"].strip():
            continue
        if keep(s, prev):
            kept.append(s)
            prev.append(s["text"].strip().lower())
        else:
            dropped += 1
    return kept, dropped


def write_outputs(out: Path, stem: str, source: str, kept: list[dict], dropped: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.json").write_text(json.dumps({"source": source, "dropped_as_silence_or_loop": dropped, "segments": [
        {"start": s["start"], "end": s["end"], "text": s["text"].strip(), "no_speech": s.get("no_speech_prob"), "avg_logprob": s.get("avg_logprob")} for s in kept]}, indent=1))
    (out / f"{stem}.srt").write_text("".join(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text'].strip()}\n\n" for i, s in enumerate(kept, 1)))


def transcribe(src: Path, model, out: Path) -> tuple[int, int, int]:
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
        if r.returncode or not wav.exists():
            raise RuntimeError(f"could not read the audio of {src.name}: {r.stderr.strip()[:200]}")
        res = model.transcribe(str(wav), language="en", condition_on_previous_text=False, fp16=False)
    kept, dropped = filter_segments(res["segments"])
    write_outputs(out, src.stem, str(src), kept, dropped)
    return len(kept), dropped, sum(len(s["text"].split()) for s in kept)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small")
    a = ap.parse_args(argv)
    src, out = Path(a.src).expanduser(), Path(a.out).expanduser()
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.suffix.lower() in MEDIA and not p.name.startswith("."))
    if not files:
        print(f"no media files in {src}", file=sys.stderr)
        return 1
    try:
        import whisper
    except ImportError as e:
        print(f"Whisper is not installed ({e})", file=sys.stderr)
        return 1
    model = whisper.load_model(a.model)
    for f in files:
        if (out / f"{f.stem}.srt").exists():
            print(f"skip {f.stem} (already transcribed)")
            continue
        try:
            n, d, w = transcribe(f, model, out)
        except RuntimeError as e:
            print(f"FAILED {f.name}: {e}", file=sys.stderr)
            continue
        print(f"done {f.stem}: {n} segments kept, {d} dropped, {w} words", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
