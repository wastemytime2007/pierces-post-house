"""Speech lines from a finished video, for suggest.py when no captions.json exists.

    python3 labs/broll/lines.py --video "<finished video>" --out lines.json [--model small]

Transcribes the video's own audio with Whisper (English set, word timestamps) and writes one line per Whisper segment: `[{"text", "start", "end"}]`. The text is
what Whisper heard, not checked against anything; a mis-heard word gives a mis-matched suggestion, which the contact sheet shows next to the words so it is visible."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def transcribe_lines(video: Path, model: str = "small") -> list[dict]:
    try:
        import whisper
    except ImportError as e:
        raise RuntimeError(f"Whisper is not installed ({e})")
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
        if r.returncode or not wav.exists():
            raise RuntimeError(f"could not read the audio of {video.name}: {r.stderr.strip()[:200]}")
        res = whisper.load_model(model).transcribe(str(wav), language="en", condition_on_previous_text=False, fp16=False)
    return [{"text": s["text"].strip(), "start": round(float(s["start"]), 2), "end": round(float(s["end"]), 2)} for s in res["segments"] if s["text"].strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small")
    a = ap.parse_args(argv)
    try:
        lines = transcribe_lines(Path(a.video).expanduser(), a.model)
    except RuntimeError as e:
        print(f"lines: {e}", file=sys.stderr)
        return 1
    Path(a.out).expanduser().write_text(json.dumps({"video": a.video, "lines": lines}, indent=1))
    print(f"{len(lines)} lines -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
