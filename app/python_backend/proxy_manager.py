"""Proxy generation, adapted from the user's ProxyGen.py.

Differences from the original:
  - Streams progress as events to a callable `emit()` instead of printing
  - Accepts a cancellation flag so the UI "Stop" button actually works
  - Mirrors folder structure but outputs into a labeled subdir:
        <output_dir>/<kind>/<original_relative_path>.mp4
    where kind is 'aroll' | 'broll' | 'audio' — critical for later stages
    to know which footage is which.
  - Uses subprocess.Popen so we can poll cancellation during long encodes
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# ---------- Supported formats ----------

VIDEO_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv",
    ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".3gp",
    ".flv", ".webm", ".ts", ".vob",
}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".flac", ".m4a", ".aif", ".aiff", ".ogg"}
UNSUPPORTED_EXTENSIONS = {".braw", ".r3d", ".ari", ".arri", ".crm"}

# Proxy encode settings
PROXY_HEIGHT = 540
PROXY_CRF = 28
AUDIO_BITRATE = "128k"
MAX_PARALLEL_JOBS = 6


# ---------- Binary discovery ----------

# GUI-launched macOS apps don't inherit shell PATH, so `shutil.which("ffmpeg")`
# returns None even when Homebrew FFmpeg is installed. We check common paths
# explicitly before giving up.
COMMON_FFMPEG_PATHS = [
    "/opt/homebrew/bin/ffmpeg",   # Apple Silicon Homebrew
    "/usr/local/bin/ffmpeg",      # Intel Homebrew
    "/opt/local/bin/ffmpeg",      # MacPorts
    "/usr/bin/ffmpeg",            # Rare but possible
]


def find_ffmpeg() -> Optional[str]:
    """Return absolute path to ffmpeg, or None if not found."""
    # First try PATH — might work if the Rust side set up PATH correctly
    found = shutil.which("ffmpeg")
    if found:
        return found
    # Fallback: check common install locations directly
    for candidate in COMMON_FFMPEG_PATHS:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


# ---------- Data classes ----------

@dataclass
class ProxyJob:
    sources: list[Path]
    kind: str                        # "aroll" | "broll" | "audio"
    output_dir: Optional[Path] = None  # where the <kind> subdir goes
    cancel_flag: threading.Event = field(default_factory=threading.Event)


# ---------- Public entry point ----------

def generate_proxies_streaming(
    job: ProxyJob,
    job_id: str,
    emit: Callable[[dict], None],
) -> None:
    """Run proxy generation for a job, streaming events through `emit`.

    Layout of outputs:
        <output_dir>/<kind>/<preserved relative path>.mp4  (video)
        <output_dir>/<kind>/<preserved relative path>       (audio — copied as-is)
    """
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path is None:
        emit({
            "type": "error",
            "job_id": job_id,
            "message": (
                "FFmpeg is not installed (or not found at a standard path).\n"
                "Install with: brew install ffmpeg\n"
                "Checked: " + ", ".join(COMMON_FFMPEG_PATHS)
            ),
        })
        return

    # Validate kind
    if job.kind not in ("aroll", "broll", "audio"):
        emit({"type": "error", "job_id": job_id,
              "message": f"Unknown kind: {job.kind}"})
        return

    # Work out output root if not provided
    if job.output_dir is None:
        # Default: sibling folder named "PreCut_Output" next to the source
        first_src = job.sources[0].resolve()
        base = first_src.parent if first_src.is_file() else first_src.parent
        job.output_dir = base / "PreCut_Output"

    output_root = job.output_dir / job.kind
    output_root.mkdir(parents=True, exist_ok=True)

    # --- Scan ---
    video_files: list[tuple[Path, Path]] = []  # (source_file, source_root)
    audio_files: list[tuple[Path, Path]] = []
    unsupported: list[Path] = []

    for source in job.sources:
        source = source.resolve()
        if not source.exists():
            emit({"type": "log", "level": "warn",
                  "message": f"Skipping missing path: {source}"})
            continue

        root = source if source.is_dir() else source.parent
        iterator = _walk(source) if source.is_dir() else [source]

        for path in iterator:
            ext = path.suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                video_files.append((path, root))
            elif ext in AUDIO_EXTENSIONS:
                audio_files.append((path, root))
            elif ext in UNSUPPORTED_EXTENSIONS:
                unsupported.append(path)

    # For audio jobs we only care about audio files; for video jobs ignore audio
    if job.kind == "audio":
        files_to_process = [("audio", src, root) for src, root in audio_files]
    else:
        files_to_process = [("video", src, root) for src, root in video_files]

    total = len(files_to_process)
    if unsupported:
        emit({
            "type": "log",
            "level": "warn",
            "message": (
                f"Skipping {len(unsupported)} camera-RAW files "
                f"(BRAW/R3D/ARRIRAW need vendor tools)"
            ),
        })

    emit({
        "type": "job_started",
        "job_id": job_id,
        "kind": job.kind,
        "total_files": total,
        "output_dir": str(output_root),
    })

    if total == 0:
        emit({
            "type": "job_complete",
            "job_id": job_id,
            "success": 0, "skipped": 0, "failed": 0,
            "message": "No processable files found.",
        })
        return

    # --- Process ---
    cpu_count = multiprocessing.cpu_count()
    workers = max(1, min(cpu_count - 2, MAX_PARALLEL_JOBS))

    success = skipped = failed = 0
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _process_file,
                kind=file_kind,
                source=src,
                source_root=root,
                output_root=output_root,
                cancel_flag=job.cancel_flag,
                ffmpeg_path=ffmpeg_path,
            ): src
            for file_kind, src, root in files_to_process
        }

        for future in as_completed(futures):
            src = futures[future]
            if job.cancel_flag.is_set():
                # Best-effort: let currently-running encodes finish,
                # but stop scheduling new events.
                continue

            try:
                result = future.result()
            except Exception as e:
                result = {
                    "status": "failed",
                    "file": src.name,
                    "elapsed_sec": 0.0,
                    "error": f"{type(e).__name__}: {e}",
                    "output_path": None,
                }

            completed += 1
            if result["status"] == "success":
                success += 1
            elif result["status"] == "skipped":
                skipped += 1
            else:
                failed += 1

            emit({
                "type": "file_done",
                "job_id": job_id,
                **result,
                "completed": completed,
                "total": total,
            })

    if job.cancel_flag.is_set():
        emit({
            "type": "job_complete",
            "job_id": job_id,
            "success": success, "failed": failed, "skipped": skipped,
            "cancelled": True,
        })
    else:
        emit({
            "type": "job_complete",
            "job_id": job_id,
            "success": success, "failed": failed, "skipped": skipped,
        })


# ---------- Internals ----------

def _walk(root: Path):
    """Yield all non-hidden files under root."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            yield Path(dirpath) / fn


def _process_file(
    kind: str,
    source: Path,
    source_root: Path,
    output_root: Path,
    cancel_flag: threading.Event,
    ffmpeg_path: str,
) -> dict:
    """Process one file. Returns dict matching file_done event shape."""
    start = time.time()
    rel = source.relative_to(source_root)

    if kind == "audio":
        # Audio files get COPIED not transcoded — we want exact sample accuracy
        # for sync later. Filename/extension preserved.
        dest = output_root / rel
        return _copy_audio(source, dest, start)

    # Video → H.264 proxy
    proxy_path = output_root / rel.with_suffix(".mp4")

    if proxy_path.exists() and proxy_path.stat().st_size > 0:
        return {
            "status": "skipped",
            "file": source.name,
            "elapsed_sec": 0.0,
            "output_path": str(proxy_path),
            "source_path": str(source),
            "error": None,
        }

    proxy_path.parent.mkdir(parents=True, exist_ok=True)
    return _encode_proxy(source, proxy_path, cancel_flag, start, ffmpeg_path)


def _copy_audio(source: Path, dest: Path, start: float) -> dict:
    if dest.exists() and dest.stat().st_size == source.stat().st_size:
        return {
            "status": "skipped",
            "file": source.name,
            "elapsed_sec": 0.0,
            "output_path": str(dest),
            "source_path": str(source),
            "error": None,
        }
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, dest)
        return {
            "status": "success",
            "file": source.name,
            "elapsed_sec": time.time() - start,
            "output_path": str(dest),
            "source_path": str(source),
            "error": None,
        }
    except Exception as e:
        return {
            "status": "failed",
            "file": source.name,
            "elapsed_sec": time.time() - start,
            "output_path": None,
            "source_path": str(source),
            "error": str(e),
        }


def _encode_proxy(
    source: Path,
    proxy_path: Path,
    cancel_flag: threading.Event,
    start: float,
    ffmpeg_path: str,
) -> dict:
    cmd = [
        ffmpeg_path,
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", str(PROXY_CRF),
        "-vf", f"scale=-2:{PROXY_HEIGHT}",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        str(proxy_path),
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Poll for cancellation every 500ms
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_flag.is_set():
                    proc.kill()
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
                    if proxy_path.exists():
                        proxy_path.unlink()
                    return {
                        "status": "failed",
                        "file": source.name,
                        "elapsed_sec": time.time() - start,
                        "output_path": None,
                        "source_path": str(source),
                        "error": "cancelled",
                    }

        elapsed = time.time() - start
        if proc.returncode == 0 and proxy_path.exists() and proxy_path.stat().st_size > 0:
            return {
                "status": "success",
                "file": source.name,
                "elapsed_sec": elapsed,
                "output_path": str(proxy_path),
                "source_path": str(source),
                "error": None,
            }

        if proxy_path.exists():
            proxy_path.unlink()
        err_line = ((stderr or "").strip().split("\n") or ["unknown error"])[-1]
        return {
            "status": "failed",
            "file": source.name,
            "elapsed_sec": elapsed,
            "output_path": None,
            "source_path": str(source),
            "error": err_line[:500],
        }

    except Exception as e:
        if proxy_path.exists():
            proxy_path.unlink()
        return {
            "status": "failed",
            "file": source.name,
            "elapsed_sec": time.time() - start,
            "output_path": None,
            "source_path": str(source),
            "error": str(e),
        }
