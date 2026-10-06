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
from fractions import Fraction
from pathlib import Path

from timeline import Cut, TimelineError

FFMPEG = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise TimelineError(f"ffmpeg failed: {' '.join(cmd[:12])} ... :: {p.stderr.strip()[:400]}")


def fps_arg(fps: float) -> str:
    """The sequence's frame rate as the exact fraction ffmpeg needs: 29.97002997 -> 30000/1001, never a rounded 30."""
    f = Fraction(fps).limit_denominator(1001)
    return f"{f.numerator}/{f.denominator}"


def clip_frames(clip, fps: float) -> int:
    return int(round((clip.tl_end - clip.tl_start) * fps))


def _render_segment(clip, out: Path, height: int, camera_audio: bool, fps: float) -> None:
    """Frame-exact: frame k of the segment is the source frame k after the clip's in point, at the SEQUENCE's rate, and the segment is exactly as many frames as the timeline says.
    The first version used `fps=30` with the clip's duration as an input limit, which duplicated each segment's first frame (every later frame one frame late), rounded some segments up a frame,
    ran the whole preview at 30 instead of 29.97, and lost frames when the segments were joined (874 frames for an 879-frame cut)."""
    nf = clip_frames(clip, fps)
    cmd = FFMPEG + ["-ss", f"{clip.src_in:.4f}", "-i", clip.src_path,
                    "-vf", f"scale=-2:{height},setpts=PTS-STARTPTS,fps={fps_arg(fps)}:start_time=0", "-fps_mode", "passthrough", "-frames:v", str(nf),
                    "-c:v", "libx264", "-bf", "0", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p", "-g", "15"]
    if camera_audio:
        cmd += ["-t", f"{nf / fps:.5f}", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-an"]
    _run(cmd + [str(out)])


def count_frames(path: Path) -> int:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(p.stdout.strip())


def render_preview(cut: Cut, out_mp4: Path, height: int = 540, workers: int = 3) -> dict:
    work = out_mp4.parent / "_work"
    work.mkdir(parents=True, exist_ok=True)
    camera_audio = not cut.audio

    segs = [work / f"seg_{c.idx:03d}.mp4" for c in cut.video]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda pair: _render_segment(pair[0], pair[1], height, camera_audio, cut.fps),
                      zip(cut.video, segs)))

    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segs))
    joined = work / "joined.mp4"
    _run(FFMPEG + ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    want, got = sum(clip_frames(c, cut.fps) for c in cut.video), count_frames(joined)
    if want != got:                                                       # a preview with the wrong number of frames puts every note after the first bad join on the wrong frame
        raise TimelineError(f"the preview has {got} frames but the timeline has {want}; refusing to hand over a preview whose frames are off")

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
