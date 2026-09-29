"""Render a watchable proxy of a Cut: video from each clip's source range,
audio from the enabled audio clips placed exactly where the XML puts them.

If the XML has no enabled audio at all, camera audio is used instead and the
result says so. A silent preview would hide the "exported silent" failure
that verify_export.py's AUDIO-ENABLED check exists for.
"""
from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from timeline import Cut, TimelineError

FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise TimelineError(f"ffmpeg failed: {' '.join(cmd[:12])} ... :: {p.stderr.strip()[:400]}")


def _render_segment(clip, out: Path, height: int, camera_audio: bool) -> None:
    dur = clip.tl_end - clip.tl_start
    cmd = FFMPEG + ["-ss", f"{clip.src_in:.4f}", "-t", f"{dur:.4f}", "-i", clip.src_path,
                    "-vf", f"scale=-2:{height},fps=30", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "27", "-pix_fmt", "yuv420p", "-g", "15"]
    if camera_audio:
        cmd += ["-c:a", "aac", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    _run(cmd + [str(out)])


def render_preview(cut: Cut, out_mp4: Path, height: int = 540, workers: int = 3) -> dict:
    work = out_mp4.parent / "_work"
    work.mkdir(parents=True, exist_ok=True)
    camera_audio = not cut.audio

    segs = [work / f"seg_{c.idx:03d}.mp4" for c in cut.video]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda pair: _render_segment(pair[0], pair[1], height, camera_audio),
                      zip(cut.video, segs)))

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segs))
    joined = work / "joined.mp4"
    _run(FFMPEG + ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])

    if camera_audio:
        joined.replace(out_mp4)
        audio_note = "camera audio (no enabled synced audio in this XML)"
    else:
        inputs, graph = [], []
        for i, a in enumerate(cut.audio):
            dur = a.tl_end - a.tl_start
            inputs += ["-ss", f"{a.src_in:.4f}", "-t", f"{dur:.4f}", "-i", a.src_path]
            graph.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                         f"adelay={int(round(a.tl_start * 1000))}:all=1[a{i}]")
        mix = "".join(f"[a{i}]" for i in range(len(cut.audio)))
        graph.append(f"{mix}amix=inputs={len(cut.audio)}:normalize=0:duration=longest[out]")
        mixed = work / "mixed.m4a"
        _run(FFMPEG + inputs + ["-filter_complex", ";".join(graph), "-map", "[out]",
                                "-t", f"{cut.zone_end:.4f}", "-c:a", "aac", "-b:a", "160k", str(mixed)])
        _run(FFMPEG + ["-i", str(joined), "-i", str(mixed), "-map", "0:v", "-map", "1:a",
                       "-c", "copy", "-shortest", "-movflags", "+faststart", str(out_mp4)])
        names = sorted({a.name for a in cut.audio})
        audio_note = f"synced audio: {', '.join(names)}"

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(out_mp4)], capture_output=True, text=True)
    shutil.rmtree(work, ignore_errors=True)
    return {"duration": float(probe.stdout.strip()), "audio_source": audio_note}
