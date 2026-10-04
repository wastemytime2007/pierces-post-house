"""Library first: a sound effect is generated with ElevenLabs only when no sound for it already exists.

Ryan, 2026-10-03: "generate music and sfx from eleven labs. Only generate sfx for things that a sound doesn't already exist for in our library."

The library is every audio file under the roots below: his Artlist sound-effects folder (`~/Downloads/Artlist Library/Sound Effects`, override with POSTHOUSE_SFX_LIBRARY, several folders
separated by `:`) and the store of effects generated earlier (`~/Library/Application Support/Post House/generated_sfx`, override with POSTHOUSE_GENERATED_SFX), so a sound is paid for once.

`find(description)` asks the local `claude` CLI (the free route already used for the vision check; text only, no tools) which ONE file name in the library would plausibly serve as that sound
effect, or none. It is told to pick a file only when the SAME KIND of sound is there (a doorknob for a doorknob, not a door slam for a doorknob) and to answer none otherwise. The answer must be a
file name from the list, or the call refuses; if the check itself cannot run the caller refuses too, because "could not check" is not "nothing there" and must not turn into a paid generation.
Answers are cached by (description, library listing), so asking again is free and gives the same answer.

Music is not handled here: it is always generated (or reuses an exact earlier file); the library of tracks is only ranked against a reference when asked (`reference_music.py --rank-library`)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

AUDIO_EXT = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".flac"}
MAX_LIBRARY_SFX_SEC = 4.0           # a longer library file is cut to this with a short fade, so an ambience bed does not run under the whole window
TIMEOUT_SEC = 180
PROMPT_VERSION = "v2"             # part of the cache key: a changed question must not be answered from the old cache


class LibraryError(RuntimeError):
    pass


def default_roots() -> list[Path]:
    lib = os.environ.get("POSTHOUSE_SFX_LIBRARY")
    roots = [Path(p).expanduser() for p in lib.split(":") if p] if lib else [Path.home() / "Downloads" / "Artlist Library" / "Sound Effects"]
    return roots + [generated_store()]


def generated_store() -> Path:
    return Path(os.environ.get("POSTHOUSE_GENERATED_SFX") or Path.home() / "Library" / "Application Support" / "Post House" / "generated_sfx").expanduser()


def describe(path: Path) -> str:
    """A file's name as words: extension, copy markers like ' (1)', underscores and doubled spaces removed."""
    s = re.sub(r"\s*\(\d+\)\s*$", "", path.stem)
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip()


def index(roots: list[Path] | None = None) -> list[dict]:
    """[{file, name, description}] for every audio file, de-duplicated by description (a '(1)' copy is the same sound listed once). Missing roots are skipped, not an error."""
    seen, out = set(), []
    for r in roots if roots is not None else default_roots():
        if not r.is_dir():
            continue
        for p in sorted(r.rglob("*"), key=lambda q: (bool(re.search(r"\(\d+\)$", q.stem)), str(q))):          # the original before its '(1)' copies
            if p.is_file() and p.suffix.lower() in AUDIO_EXT and not p.name.startswith("."):
                d = describe(p)
                if d.lower() in seen:
                    continue
                seen.add(d.lower())
                out.append({"file": str(p), "name": p.name, "description": d})
    return out


def build_prompt(description: str, items: list[dict]) -> str:
    listing = "\n".join(f"- {it['name']}" for it in items)
    return (
        "You are checking a sound-effects library before any new sound is generated.\n\n"
        f'THE SOUND NEEDED: "{description}"\n\n'
        f"THE LIBRARY ({len(items)} files):\n{listing}\n\n"
        "Is there ONE file in the library that would plausibly serve as that sound effect when it plays over a video? Pick a file only when the SAME KIND of sound is there "
        "(a doorknob opening for a doorknob; a camera shutter for a camera shutter). A similar object making a different sound, or a vaguely related mood, is NOT a match. "
        "If several fit, pick the best one. If nothing fits, answer null. If the best you can say for a file is that it is 'the closest', 'similar' or 'no actual X, but', that is NOT a fit: answer null "
        "(a sound is generated for a null, and generating is what is wanted when the library does not really have it).\n\n"
        'Answer with ONLY JSON: {"match": "<the exact file name from the list, or null>", "why": "<at most 12 words>"}'
    )


def parse(reply: str, items: list[dict]) -> dict | None:
    """The matched library item, or None for "nothing fits". Refuses (LibraryError) anything that is not clearly one or the other."""
    a, b = reply.find("{"), reply.rfind("}")
    if a < 0 or b <= a:
        raise LibraryError(f"the library check returned no answer I can read: {reply.strip()[:120]!r}")
    try:
        d = json.loads(reply[a:b + 1])
    except ValueError:
        raise LibraryError(f"the library check returned unreadable JSON: {reply.strip()[:120]!r}")
    m = d.get("match")
    if m in (None, "", "null"):
        return {"item": None, "why": str(d.get("why", ""))[:100]}
    by_name = {it["name"]: it for it in items}
    if m not in by_name:
        raise LibraryError(f"the library check named {m!r}, which is not a file in the library")
    return {"item": by_name[m], "why": str(d.get("why", ""))[:100]}


def ask_cli(prompt: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise LibraryError("the `claude` CLI is not on PATH, so the library cannot be checked")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_WORKSPACE_ID")}      # the Claude Code plan, never the API key
    try:
        r = subprocess.run([exe, "-p"], input=prompt, capture_output=True, text=True, timeout=TIMEOUT_SEC, env=env)
    except subprocess.TimeoutExpired:
        raise LibraryError(f"the library check timed out after {TIMEOUT_SEC} s")
    if r.returncode != 0 or not (r.stdout or "").strip():
        raise LibraryError(f"claude CLI exited {r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}")
    return r.stdout


def find(description: str, roots: list[Path] | None = None, ask: Callable[[str], str] = ask_cli, cache_path: Path | None = None) -> dict:
    """{"item": {file, name, description} | None, "why": str, "checked": N, "cached": bool}. `ask` is injectable for tests."""
    items = index(roots)
    if not items:
        return {"item": None, "why": "the library is empty or not found", "checked": 0, "cached": False}
    key = hashlib.sha1(("|".join([PROMPT_VERSION, description.strip().lower()] + [it["name"] for it in items])).encode()).hexdigest()
    cache = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except ValueError:
            cache = {}
    if key in cache:
        name = cache[key]["match"]
        item = next((it for it in items if it["name"] == name), None) if name else None
        if not name or item:
            return {"item": item, "why": cache[key].get("why", ""), "checked": len(items), "cached": True}
    got = parse(ask(build_prompt(description, items)), items)
    if cache_path:
        cache[key] = {"match": got["item"]["name"] if got["item"] else None, "why": got["why"], "description": description}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1))
    return {**got, "checked": len(items), "cached": False}


def shorten(src: Path, out: Path, max_sec: float = MAX_LIBRARY_SFX_SEC) -> Path:
    """`src` if it is short enough, else a copy of its first `max_sec` seconds with a 0.2 s fade-out."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)], capture_output=True, text=True)
    try:
        dur = float(r.stdout.strip())
    except ValueError:
        raise LibraryError(f"could not read the length of {src.name}")
    if dur <= max_sec:
        return src
    out.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", f"{max_sec}", "-af", f"afade=t=out:st={max_sec - 0.2}:d=0.2", str(out)], capture_output=True, text=True)
    if p.returncode or not out.exists():
        raise LibraryError(f"could not shorten {src.name}: {p.stderr.strip()[:150]}")
    return out


def save_generated(mp3: Path, description: str) -> Path:
    """Keep a freshly generated effect in the store, named by what it is, so the next request for that sound finds it in the library instead of paying again."""
    store = generated_store()
    store.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9]+", " ", description).strip()[:80].strip() or "generated sound"
    dst = store / f"Generated - {name}.mp3"
    shutil.copy2(mp3, dst)
    return dst


def main(argv=None) -> int:
    """Dry run, no spend: `python3 labs/audio/sfx_library.py "a doorknob opening" "a fast whoosh"` says, for each sound, whether the library has it or it would be generated."""
    import argparse
    import sys
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("sounds", nargs="+", help="what each sound effect is, in words")
    a = ap.parse_args(argv)
    roots = default_roots()
    print(f"library: {sum(1 for r in roots if r.is_dir())} folder(s), {len(index(roots))} distinct sounds")
    cache = generated_store().parent / "sfx_library_check.json"
    bad = 0
    for s in a.sounds:
        try:
            h = find(s, roots, ask_cli, cache)
        except LibraryError as e:
            print(f"  ?  {s!r}: could not check ({e})")
            bad += 1
            continue
        if h["item"]:
            print(f"  LIBRARY   {s!r} -> {h['item']['name']}   ({h['why']})")
        else:
            print(f"  GENERATE  {s!r}   ({h['why']})")
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
