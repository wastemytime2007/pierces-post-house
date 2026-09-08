"""Look at what is actually ON SCREEN, for free, via the Claude Code CLI.

Ryan, 2026-09-08: *"Can you research and find a video tool that you can
use CLI with for testing?"* Found and verified one — this module is it.

**Why this matters more than "another research source".** Every editorial
decision this app makes has been based on the TRANSCRIPT alone. That is a
poor proxy for noisy job-site footage (proven on 2026-09-07: Ryan's own
reference edit reads as garbled nonsense in transcript form), and it is
completely blind to what the camera is pointed at. The clearest example
is a question the planning conversation asked Ryan by hand and could not
answer itself:

    "is someone demonstrating with the steamer, or explaining the
     process verbally?"

That distinction decides whether a piece can be show-don't-tell or is a
talking head with B-roll gaps. Asked through this module, the answer came
back in one call, from 21 real frames: a demonstration, steamer plate and
putty knife both visible, wallpaper coming off in sheets.

**How it works, and why it's free.** The `claude-video-vision` MCP server
is registered at user level, so a `claude -p` subprocess CAN reach its
video tools when they are named in `--allowedTools` (confirmed
2026-09-08; an earlier probe concluded otherwise only because it guessed
the tool names wrong). That runs on the Claude Code plan, not the
Anthropic API — unlike `story_architect._watch_video`, which posts real
sampled frames as vision content blocks and is billed.

**The cost is time, not money.** One ~50s segment of 4K footage took
6m40s wall clock. Budget for that: this is a deliberate, targeted probe
of a specific question about a specific span, never a bulk pass over a
project. Ask it the one thing that changes the edit.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from posthouse.story_architect import _extract_json

# The video MCP tools, plus the local helpers the server leans on. These
# must be named explicitly: a `claude -p` subprocess exposes NO MCP tools
# unless they're in --allowedTools.
_MCP = "mcp__plugin_claude-video-vision_claude-video-vision__"
VIDEO_TOOLS = [
    f"{_MCP}video_info",
    f"{_MCP}video_analyze",
    f"{_MCP}video_watch",
    f"{_MCP}video_detail",
    "Bash",
    "Read",
]

# 4K footage is slow to sample. Measured: ~6m40s for a 50s segment.
VIDEO_PROBE_TIMEOUT_SEC = 1800


class VideoProbeError(RuntimeError):
    """Raised when a probe can't be completed. Never swallowed into a
    plausible-looking answer — an invented observation of footage nobody
    looked at is the one failure mode this module exists to avoid."""


PROBE_PROMPT = """Watch this video segment and answer from what you can SEE. \
Do not infer from audio or speech — this question is specifically about what is \
visible on screen.

File: {path}
Segment: {start:.0f}s to {end:.0f}s

{question}

Return ONLY this JSON in a fenced ```json block, no prose before or after:
{{"answer": "<direct answer to the question>",
  "what_is_visible": "<what is actually on screen in this segment>",
  "frames_viewed": <how many real frames you actually looked at>,
  "confident": true/false}}

If you could not actually view frames, set confident to false and say so in \
`answer` rather than describing what you assume is there."""


def probe_segment(
    video_path: str | Path,
    start_sec: float,
    end_sec: float,
    question: str,
    timeout_sec: int = VIDEO_PROBE_TIMEOUT_SEC,
) -> dict:
    """Ask one visual question about one span of one file.

    Returns the parsed answer dict. Raises `VideoProbeError` rather than
    guessing if the CLI is unavailable, the file is missing, the probe
    times out, or the reply can't be parsed.

    `frames_viewed == 0` or `confident is False` in the result means the
    model did not actually see the footage — treat that as "unknown", not
    as an observation.
    """
    path = Path(video_path)
    if not path.exists():
        raise VideoProbeError(f"No such video file: {path}")
    if end_sec <= start_sec:
        raise VideoProbeError(f"Empty segment: {start_sec}-{end_sec}")

    exe = shutil.which("claude")
    if not exe:
        raise VideoProbeError("The `claude` CLI isn't on PATH.")

    prompt = PROBE_PROMPT.format(
        path=str(path), start=start_sec, end=end_sec, question=question.strip()
    )

    # Same reasoning as cli_llm_client: an API key visible to the child
    # makes the CLI refuse the Claude Code login, which is the whole
    # point of routing here.
    child_env = {
        k: v for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                     "ANTHROPIC_WORKSPACE_ID")
    }

    try:
        proc = subprocess.run(
            [exe, "-p", "--allowedTools", ",".join(VIDEO_TOOLS)],
            input=prompt, capture_output=True, text=True,
            timeout=timeout_sec, env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise VideoProbeError(
            f"Video probe timed out after {timeout_sec}s on "
            f"{path.name} {start_sec:.0f}-{end_sec:.0f}s. 4K footage is slow "
            f"to sample; raise timeout_sec or probe a shorter span."
        )

    if proc.returncode != 0:
        raise VideoProbeError(
            f"claude CLI exited {proc.returncode}: {(proc.stderr or '').strip()[:400]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise VideoProbeError("Video probe returned no output.")

    try:
        data = _extract_json(out)
    except Exception as e:
        raise VideoProbeError(f"Could not parse the probe's reply: {e}\n{out[:400]}")

    data.setdefault("answer", "")
    data.setdefault("what_is_visible", "")
    data.setdefault("frames_viewed", 0)
    data.setdefault("confident", False)
    data["video_file"] = str(path)
    data["segment_sec"] = [start_sec, end_sec]
    # Say plainly when nothing was actually seen, so a caller can never
    # present an assumption as an observation.
    if not data.get("frames_viewed"):
        data["confident"] = False
    return data


def _cache_path(project_dir, video_path, start_sec: float, end_sec: float) -> Path:
    import hashlib
    key = f"{Path(str(video_path)).name}|{start_sec:.1f}|{end_sec:.1f}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    d = Path(project_dir) / "visual_notes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def probe_segment_cached(
    project_dir,
    video_path: str | Path,
    start_sec: float,
    end_sec: float,
    question: str,
    **kwargs,
) -> dict:
    """`probe_segment` with a per-project cache on disk.

    A probe costs minutes of wall clock, which is fine once and
    unacceptable every time an idea is generated or a plan is revisited.
    Ryan, 2026-09-08, on what ideas should be built from: *"based off of
    the content that it knows is there both auditorial and visual"* — that
    only works if looking is cheap after the first look. Frames don't
    change, so the observation is safe to keep.

    Cache misses fall through to a real probe. A failed probe is NOT
    cached: a transient failure (drive unmounted, timeout) must not be
    remembered as "there's nothing there".
    """
    path = _cache_path(project_dir, video_path, start_sec, end_sec)
    if path.exists():
        try:
            cached = json.loads(path.read_text())
            cached["from_cache"] = True
            return cached
        except Exception:
            pass  # unreadable cache is just a miss

    result = probe_segment(video_path, start_sec, end_sec, question, **kwargs)
    if result.get("confident") and result.get("frames_viewed"):
        try:
            path.write_text(json.dumps(result, indent=2))
        except Exception:
            pass  # caching is an optimisation, never a hard failure
    result["from_cache"] = False
    return result


def describe_frames(
    frame_paths: list, prompt: str, timeout_sec: int = VIDEO_PROBE_TIMEOUT_SEC
) -> str:
    """Have the CLI actually LOOK at frame images already on disk.

    This is the free replacement for posting base64 image blocks to the
    API. `story_architect._watch_video` does the genuinely valuable work
    for free already — yt-dlp download, ffmpeg cut detection, real
    measured cuts-per-second, frame extraction at real cut points — and
    only the vision call was billed. So only that is swapped: the same
    frames, the same prompt, read off disk by a subprocess instead of
    uploaded.

    Returns the reply text (callers parse their own JSON out of it).
    Raises `VideoProbeError` rather than returning something plausible.
    """
    paths = [Path(p) for p in frame_paths]
    missing = [p for p in paths if not p.exists()]
    if not paths or missing:
        raise VideoProbeError(
            f"{len(missing)} of {len(paths)} frame file(s) missing; refusing to "
            "describe frames that aren't there."
        )

    exe = shutil.which("claude")
    if not exe:
        raise VideoProbeError("The `claude` CLI isn't on PATH.")

    listing = "\n".join(f"  {p}" for p in paths)
    full = (
        f"{prompt}\n\n"
        f"Read each of these {len(paths)} real frame images, in this order, and "
        f"base your answer ONLY on what they actually show:\n{listing}\n\n"
        f"You must actually read every one of them before answering."
    )

    child_env = {
        k: v for k, v in os.environ.items()
        if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                     "ANTHROPIC_WORKSPACE_ID")
    }
    try:
        proc = subprocess.run(
            [exe, "-p", "--allowedTools", "Read"],
            input=full, capture_output=True, text=True,
            timeout=timeout_sec, env=child_env,
        )
    except subprocess.TimeoutExpired:
        raise VideoProbeError(f"Frame description timed out after {timeout_sec}s.")
    if proc.returncode != 0:
        raise VideoProbeError(
            f"claude CLI exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise VideoProbeError("Frame description returned no output.")
    return out


def is_on_camera_demonstration(
    video_path: str | Path, start_sec: float, end_sec: float,
    project_dir=None,
) -> dict:
    """The specific question the planning conversation had to ask Ryan by
    hand: is this span a real on-camera demonstration, or someone talking
    about the work off to the side?

    It decides whether a piece can be built show-don't-tell, so it is
    worth a real probe rather than an assumption drawn from a transcript.
    """
    question = (
        "Is a person demonstrating the work ON CAMERA here (hands/tools "
        "visible, doing the thing), or are they only talking about it while "
        "the work isn't shown? Name any tool that is actually visible."
    )
    if project_dir is not None:
        return probe_segment_cached(
            project_dir, video_path, start_sec, end_sec, question)
    return probe_segment(video_path, start_sec, end_sec, question)
