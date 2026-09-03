"""Audio indexing for sync preparation.

Instead of copying audio files (they're already small), we record metadata
for each one to a JSON sidecar. This gives the sync UI in Drop 3 enough
information to match clean audio to A-roll takes.

We use ffprobe (ships with ffmpeg) rather than a new library dependency.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from proxy_manager import find_ffmpeg  # for fallback ffprobe discovery


# Common install locations for ffprobe (parallel to find_ffmpeg)
COMMON_FFPROBE_PATHS = [
    "/opt/homebrew/bin/ffprobe",
    "/usr/local/bin/ffprobe",
    "/opt/local/bin/ffprobe",
    "/usr/bin/ffprobe",
]


def find_ffprobe() -> Optional[str]:
    """Return absolute path to ffprobe, or None.

    ffprobe installs alongside ffmpeg. We first check common paths, then
    try to derive ffprobe from the ffmpeg path we already resolved.
    """
    import os
    import shutil

    found = shutil.which("ffprobe")
    if found:
        return found

    for candidate in COMMON_FFPROBE_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    # Last resort: ffmpeg's directory
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).parent / "ffprobe"
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)

    return None


@dataclass
class AudioInfo:
    """Probe result for one audio file."""
    source_path: str              # absolute path (what we'll reference later)
    display_name: str             # basename for UI
    duration_sec: float           # length
    sample_rate: int              # e.g. 48000
    channels: int                 # 1=mono, 2=stereo
    codec: str                    # 'pcm_s24le', 'aac', 'flac', etc.
    size_bytes: int               # file size
    indexed_at: float             # when we probed it


def probe_audio(path: Path, ffprobe_bin: str) -> Optional[AudioInfo]:
    """Run ffprobe and parse results. Returns None on failure."""
    import time

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-select_streams", "a:0",   # first audio stream
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, FileNotFoundError):
        return None

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    if not streams:
        return None
    s = streams[0]

    # Duration can come from either the stream or the container
    duration = None
    for src in (s.get("duration"), fmt.get("duration")):
        if src:
            try:
                duration = float(src)
                break
            except ValueError:
                continue

    if duration is None:
        return None

    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    return AudioInfo(
        source_path=str(path.resolve()),
        display_name=path.name,
        duration_sec=round(duration, 3),
        sample_rate=int(s.get("sample_rate", 0) or 0),
        channels=int(s.get("channels", 0) or 0),
        codec=s.get("codec_name", "unknown"),
        size_bytes=size,
        indexed_at=time.time(),
    )


def save_audio_index(info: AudioInfo, index_dir: Path) -> Path:
    """Write one audio file's metadata as a JSON sidecar.

    Filename scheme: stem of source file + .json, with collision handling
    (we may have the same basename in different folders).
    """
    index_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(info.source_path).stem
    candidate = index_dir / f"{stem}.json"
    # Collision handling — if an existing sidecar has a different source_path,
    # append a hash of the full path.
    if candidate.exists():
        try:
            existing = json.loads(candidate.read_text())
            if existing.get("source_path") != info.source_path:
                import hashlib
                h = hashlib.sha1(info.source_path.encode()).hexdigest()[:8]
                candidate = index_dir / f"{stem}_{h}.json"
        except (json.JSONDecodeError, OSError):
            pass
    candidate.write_text(json.dumps(asdict(info), indent=2))
    return candidate


def load_audio_index(index_dir: Path) -> list[AudioInfo]:
    """Read all sidecars in an index dir."""
    if not index_dir.exists():
        return []
    out = []
    for fp in sorted(index_dir.glob("*.json")):
        try:
            d = json.loads(fp.read_text())
            out.append(AudioInfo(**d))
        except (json.JSONDecodeError, TypeError, OSError):
            continue
    return out
