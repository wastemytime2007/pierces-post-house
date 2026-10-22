"""Still frames from the person's own footage, for the screens to show (project posters, idea storyboards, sequence tiles).

A frame is cut with ffmpeg, scaled, cached on disk by (path, modified time, second, width), and handed to the page as a data: URI so no file access rules are involved.
Nothing here can fail a screen: a missing drive, a missing file or a slow disk gives None and the page draws its own colored tile instead.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mxf", ".mkv", ".avi"}


def _ffmpeg() -> str | None:
    try:
        from proxy_manager import find_ffmpeg           # the app's own locator (a GUI-launched app has a short PATH)
        return find_ffmpeg()
    except ImportError:
        import shutil
        return shutil.which("ffmpeg")


def _cache_dir() -> Path:
    from project import app_support_dir
    d = app_support_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def frame_data_uri(path: str | Path, t: float = 1.0, width: int = 480) -> str | None:
    """One frame of a video at second `t` as a JPEG data URI, or None when it cannot be had."""
    p = Path(path)
    proxy = p.parent / "proxies" / f"{p.stem}.mp4"           # the small local proxy, same timeline as the original: quick, and works with the drive unplugged
    try:
        if proxy.is_file() and not proxy.name.startswith("."):
            p = proxy
        if not p.is_file() or p.suffix.lower() not in VIDEO_SUFFIXES or p.name.startswith("."):
            return None
        key = hashlib.sha1(f"{p}|{p.stat().st_mtime_ns}|{round(float(t), 1)}|{width}".encode()).hexdigest()[:20]
        out = _cache_dir() / f"{key}.jpg"
        if not out.is_file():
            ff = _ffmpeg()
            if ff is None:
                return None
            tmp = out.with_suffix(".part.jpg")
            r = subprocess.run([ff, "-v", "error", "-y", "-ss", f"{max(0.0, float(t)):.2f}", "-i", str(p), "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "5", str(tmp)],
                               capture_output=True, timeout=20)
            if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                tmp.unlink(missing_ok=True)
                return None
            tmp.replace(out)
        return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode("ascii")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def project_poster(project_dir: str | Path, width: int = 640) -> str | None:
    """A frame that stands for a project: from its interview footage first, then its B-roll. Prefers the small proxy (local and quick) over the original (often on a drive)."""
    try:
        d = json.loads((Path(project_dir) / "project.json").read_text())
    except (OSError, ValueError):
        return None
    ordered = sorted(d.get("sources", []), key=lambda s: {"aroll": 0, "broll": 1}.get(s.get("kind"), 2))
    for src in ordered:
        if src.get("kind") == "audio":
            continue
        for fpath, info in sorted((src.get("files") or {}).items()):
            for cand in (info.get("proxy_path"), fpath):
                if cand and Path(cand).is_file():
                    uri = frame_data_uri(cand, 8.0, width) or frame_data_uri(cand, 0.5, width)
                    if uri:
                        return uri
        root = Path(src.get("root_path", ""))
        if root.is_file():
            uri = frame_data_uri(root, 8.0, width) or frame_data_uri(root, 0.5, width)
            if uri:
                return uri
    return None
