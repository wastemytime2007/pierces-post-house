"""A second, human-like look at B-roll candidates: does this frame actually show what the line is about?

CLIP ranks frames by how much they look like a line's words, but it cannot say "nothing fits", and it reads text printed in a picture (a burned-in caption that shares a word with the
line "matches"). For each line, the best few CLIP candidates are shown to the local `claude` CLI (the same free, no-API route as `posthouse/video_probe.describe_frames`, which this mirrors:
the CLI reads the frame files off disk with the Read tool only) and it answers, per frame:

    fits          the frame clearly shows what the words are about, so it would be a sensible cutaway while the line is spoken (a shared word or a vague likeness is not enough)
    text_overlay  the frame has captions, subtitles, a lower third or other text burned into the picture (such a clip would carry that text into the new video)
    why           a few words

A frame is kept only when fits is true and text_overlay is false. A reply that is missing, unparsable or short a frame counts as "no" for that frame, said so in `why`; it is never read as a yes.
Verdicts are cached by (prompt version, line, frames) in a json file, so a rerun costs nothing.

Limits: a model's yes/no is a judgement, not a measurement: it can be wrong in both directions, and it sees one still, not the clip. About 15-40 s per line (one CLI call, up to 5 images)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

PROMPT_VERSION = "v1"
TIMEOUT_SEC = 300


class JudgeError(RuntimeError):
    pass


def build_prompt(line: str, paths: list[Path]) -> str:
    listing = "\n".join(f"  {i}. {p}" for i, p in enumerate(paths, 1))
    return (
        "You are choosing B-roll (cutaway footage) for a short video. While the speaker says the line below, a cutaway would be shown.\n\n"
        f'LINE: "{line}"\n\n'
        f"Read each of these {len(paths)} frame images, in order, and judge each one separately:\n{listing}\n\n"
        "For each image answer:\n"
        '  fits: true ONLY if the frame clearly shows the object, place or action the words are about, so a viewer would find it a sensible cutaway for this line. '
        "False if the link is just a shared word, a similar colour or a vague likeness, or if the line is only conversation that names nothing visible.\n"
        "  text_overlay: true if the frame has captions, subtitles, a lower third or any other text burned into the picture.\n"
        '  why: at most 8 words.\n\n'
        f'Answer with ONLY a JSON list of exactly {len(paths)} objects, like [{{"n":1,"fits":false,"text_overlay":false,"why":"..."}}, ...]. No other text.'
    )


def parse(reply: str, count: int) -> list[dict]:
    """One verdict per frame, in order. Anything missing or malformed is a 'no' that says why."""
    blank = [{"fits": False, "text_overlay": False, "why": "no verdict returned"} for _ in range(count)]
    a, b = reply.find("["), reply.rfind("]")
    if a < 0 or b <= a:
        return blank
    try:
        items = json.loads(reply[a:b + 1])
    except ValueError:
        return blank
    out = list(blank)
    for k, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        n = it.get("n", k + 1)
        if isinstance(n, int) and 1 <= n <= count:
            out[n - 1] = {"fits": it.get("fits") is True, "text_overlay": it.get("text_overlay") is True, "why": str(it.get("why", ""))[:80]}
    return out


def ask_cli(prompt: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise JudgeError("the `claude` CLI is not on PATH")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")}      # the Claude Code plan, never the API key
    try:
        r = subprocess.run([exe, "-p", "--allowedTools", "Read"], input=prompt, capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env)
    except subprocess.TimeoutExpired:
        raise JudgeError(f"the vision check timed out after {TIMEOUT_SEC} s")
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise JudgeError(f"claude CLI exited {r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}")
    return r.stdout


def _key(line: str, paths: list[Path]) -> str:
    return hashlib.sha1(("|".join([PROMPT_VERSION, line] + [str(p) for p in paths])).encode()).hexdigest()


def judge_line(line: str, paths: list[Path], ask: Callable[[str], str] = ask_cli, cache: dict | None = None) -> list[dict]:
    """Verdicts for `paths` against `line`. `ask` is injectable (tests); `cache` is a dict the caller loads and saves."""
    if not paths:
        return []
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise JudgeError(f"{len(missing)} frame file(s) missing; refusing to judge frames that are not there")
    k = _key(line, paths)
    if cache is not None and k in cache:
        return cache[k]
    v = parse(ask(build_prompt(line, paths)), len(paths))
    if cache is not None:
        cache[k] = v
    return v


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.write_text(json.dumps(cache, indent=1))
