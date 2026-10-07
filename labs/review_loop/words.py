"""Word timing for short windows of a source, from PreCut's own Transcriber.

Reuses `precut_pipeline.transcriber.Transcriber` (its model, language and seeded decode
carry hard-won settings: no temperature pin, no conditioning on previous text) rather than
re-implementing Whisper. It is pointed at a short extracted window, not a whole file.

Whisper's word times are good to roughly a tenth of a second, not a frame. Cut points
therefore snap to the quietest moment near the word boundary, which is also where an editor
would cut.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "app" / "python_backend"))
if (REPO_ROOT / "precut_pipeline").is_dir():                                   # the app's bundled copy: python_backend itself plays the repo root
    sys.path.insert(0, str(REPO_ROOT))

_T = None


def _transcriber():
    global _T
    if _T is None:
        from precut_pipeline.transcriber import Transcriber
        _T = Transcriber()
    return _T


@dataclass
class W:
    text: str
    start: float   # absolute seconds in the source file
    end: float


def words_in(path: str, start: float, dur: float) -> list[W]:
    start = max(0.0, start)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "window.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        tr = _transcriber().transcribe(wav)
    return [W(w.text, start + w.start, start + w.end) for p in tr.phrases for w in p.words]


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("’", "'"))


def _sim(a: str, b: str) -> float:
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return 1.0                       # Whisper often splits "stepping" into "step" + "on"
    return SequenceMatcher(None, a, b).ratio()


def find_phrase(words: list[W], phrase: str, min_score: float = 0.8) -> tuple[int, float] | None:
    """Index of the word where `phrase` begins.

    The first word must match closely; the rest of the first few words only have to turn up
    in the next few heard words, since Whisper drops, splits and merges small words.
    """
    want = tokens(phrase)[:5]
    heard = [(tokens(w.text) or [""])[0] for w in words]
    if not want:
        return None
    best: tuple[int, float] | None = None
    for i in range(len(heard)):
        first = _sim(want[0], heard[i])
        if first < 0.75:
            continue
        rest, window = want[1:], heard[i + 1:i + 1 + len(want) + 2]
        found = (sum(1 for r in rest if any(_sim(r, h) >= 0.8 for h in window)) / len(rest)) if rest else 1.0
        score = 0.5 * first + 0.5 * found
        if best is None or score > best[1]:
            best = (i, score)
    return best if best and best[1] >= min_score else None


def heard(words: list[W]) -> str:
    return " ".join(w.text for w in words)


def valley(path: str, lo: float, hi: float) -> float:
    """Time of the quietest 10ms in [lo, hi] of `path`: where a cut does the least damage."""
    lo = max(0.0, lo)
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{lo:.4f}", "-t", f"{max(hi - lo, 0.02):.4f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True)
    pcm = np.frombuffer(p.stdout, dtype=np.float32)
    n = len(pcm) // 80
    if n < 2:
        return (lo + hi) / 2
    rms = np.sqrt((pcm[: n * 80].reshape(n, 80) ** 2).mean(axis=1))
    return lo + (int(np.argmin(rms)) + 0.5) * 0.01
