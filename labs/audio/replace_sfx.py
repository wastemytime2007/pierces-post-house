#!/usr/bin/env python3
"""Notes that ask for a different sound effect -> a new audio folder with that one effect replaced.

    python3 labs/audio/replace_sfx.py --audio "<folder from make_audio.py>" \
        --ops "<ops.json from revise.py>" --notes "<review_notes.json>" --out "<new folder>" \
        [--preview-video "<video to mix under>"]

revise.py turns a note like "make this sound effect sound like something being highlighted on a
piece of paper" into a `replace_sfx` operation whose description is the note's own words. This
applies it: the note is matched to the sound effect nearest its moment, that one effect is generated
again from the note's description, and the music, the speech and the effect's time are left exactly
as they were. Every note is reported as applied or not, with the reason. The result is re-verified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_audio as ma  # noqa: E402
import verify_audio as va  # noqa: E402

REACH_SEC = 1.0                     # a note counts as "at" an effect from this long before it starts to this long after it ends
PROMPT_TAIL = ", short and subtle, a sound effect for a graphic appearing on screen"


def sfx_for_note(clips: list[dict], t: float) -> dict | None:
    """The effect clip a note at timeline time t is about: the nearest one whose start-1s .. end+1s covers t."""
    near = [c for c in clips if c["kind"] == "sfx" and c["start_sec"] - REACH_SEC <= t <= c["start_sec"] + c["duration_sec"] + REACH_SEC]
    return min(near, key=lambda c: abs(t - c["start_sec"])) if near else None


def new_prompt(sound: str) -> str:
    return sound.strip().rstrip(".") + PROMPT_TAIL


def replace(audio_dir: Path, ops: list[dict], notes: list[dict], out: Path, preview_video: Path | None = None) -> tuple[list[dict], bool]:
    """Returns (ledger, changed). The ledger has one entry per replace_sfx op, applied or not."""
    meta = json.loads((audio_dir / "audio.json").read_text())
    clips = meta["clips"]
    ledger, done = [], set()
    todo: list[tuple[dict, str]] = []
    for o in ops:
        if o.get("op") != "replace_sfx":
            continue
        n = o["note"]
        t = notes[n - 1]["timeline_sec"]
        entry = {"note": n, "note_time": t, "note_text": notes[n - 1].get("text", ""), "sound": o["sound"], "applied": False, "reason": ""}
        c = sfx_for_note(clips, t)
        if c is None:
            entry["reason"] = f"no sound effect within {REACH_SEC:.0f}s of {t:.2f}s (the effect is at {[round(x['start_sec'], 2) for x in clips if x['kind'] == 'sfx']})"
        elif c["name"] in done:
            entry["reason"] = "another note already changed this effect; one change per effect per run"
        else:
            done.add(c["name"])
            todo.append((entry, c["name"]))
        ledger.append(entry)
    if not todo:
        return ledger, False

    entry, name = todo[0]
    old_clip = next(c for c in clips if c["name"] == name)
    out.mkdir(parents=True, exist_ok=True)
    for f in ("music_stem.wav", "speech_window.wav"):
        shutil.copy2(audio_dir / f, out / f)
    dur = meta["window"]["end"] - meta["window"]["start"]
    prompt = new_prompt(entry["sound"])
    mp3, info = ma.generate("sfx", prompt, round(old_clip["duration_sec"], 2), audio_dir / "generated")
    shutil.copytree(audio_dir / "generated", out / "generated", dirs_exist_ok=True)      # the new folder carries the cache, so a rebuild from it needs nothing else
    gain = ma.build_sfx_clip(mp3, out / "speech_window.wav", out / "sfx_clip.wav", meta["sfx_below_speech_peak_db"])
    pv = preview_video or (Path(meta["preview_video"]) if meta.get("preview_video") else None)
    if pv is None:
        raise ma.AudioError("no video to mix under: give --preview-video (this folder's audio.json does not record one)")
    ma.mix_preview(pv, out / "music_stem.wav", out / "sfx_clip.wav", out / "audio_preview.mp4", meta["window"]["start"], dur, meta["callout_sec"])

    new = json.loads(json.dumps(meta))
    new["generated"]["sfx"] = {**info, "prompt_from_note": entry["sound"]}
    new["sfx_gain_db"] = round(gain, 2)
    new["speech_wav"] = str((out / "speech_window.wav").resolve())
    new["preview_video"] = str(pv)
    for c in new["clips"]:
        c["path"] = str((out / c["name"]).resolve())
        if c["name"] == name:
            c["duration_sec"] = ma.probe_dur(out / "sfx_clip.wav")
    new["replaced"] = {"note": entry["note"], "was": meta["generated"]["sfx"]["prompt"], "now": prompt}
    (out / "audio.json").write_text(json.dumps(new, indent=2))
    (out / "placement.json").write_text(json.dumps({"kind": "audio", "clips": new["clips"]}, indent=2))
    entry["applied"], entry["reason"], entry["prompt"] = True, f'regenerated from the note\'s words: "{prompt}"', prompt
    (out / "replacement.json").write_text(json.dumps({"ledger": ledger}, indent=2))
    return ledger, True


def extra_checks(audio_dir: Path, out: Path) -> list[tuple[str, bool, str]]:
    """Beyond verify_audio: only the effect changed, and it really is a different sound in the same place."""
    old, new = json.loads((audio_dir / "audio.json").read_text()), json.loads((out / "audio.json").read_text())
    h = lambda p: hashlib.sha1(p.read_bytes()).hexdigest()
    rows = [("MUSIC-UNCHANGED", h(audio_dir / "music_stem.wav") == h(out / "music_stem.wav"), "the music stem is byte-identical to before"),
            ("SPEECH-UNCHANGED", h(audio_dir / "speech_window.wav") == h(out / "speech_window.wav"), "the speech window is byte-identical to before")]
    so, sn = next(c for c in old["clips"] if c["kind"] == "sfx"), next(c for c in new["clips"] if c["kind"] == "sfx")
    rows.append(("EFFECT-KEPT-ITS-TIME", so["start_sec"] == sn["start_sec"], f"the effect is still at {sn['start_sec']:.3f}s"))
    a, b = va.pcm(audio_dir / "sfx_clip.wav"), va.pcm(out / "sfx_clip.wav")
    n = min(len(a), len(b))
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    cc = np.fft.irfft(np.fft.rfft(a, 2 * n) * np.conj(np.fft.rfft(b, 2 * n)), 2 * n)
    corr = float(np.max(np.abs(cc)) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9))
    rows.append(("EFFECT-IS-A-DIFFERENT-SOUND", corr < 0.6, f"the new effect matches the old one at {corr:.2f} (1.00 would be the same sound; need under 0.60)"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio", type=Path, required=True)
    ap.add_argument("--ops", type=Path, required=True)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--preview-video", type=Path)
    a = ap.parse_args()
    try:
        ops = json.loads(a.ops.read_text())
        notes = json.loads(a.notes.read_text())["notes"]
        ma.load_key()
        ledger, changed = replace(a.audio, ops, notes, a.out, a.preview_video)
    except (ma.AudioError, OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    if not ledger:
        print("no sound-effect notes in these operations", file=sys.stderr)
        return 1
    for e in ledger:
        print(f"  note {e['note']} [{e['note_time']}s] {'APPLIED    ' if e['applied'] else 'NOT APPLIED'}  {e['reason']}")
    if not changed:
        print("\nNothing was changed, so no new folder was written.")
        return 0
    print("\nChecks:")
    bad = 0
    for name, ok, detail in extra_checks(a.audio, a.out):
        bad += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    v = subprocess.run([sys.executable, str(HERE / "verify_audio.py"), str(a.out)], capture_output=True, text=True)
    print(v.stdout.rstrip())
    if bad or v.returncode:
        print("\nThe replacement did not verify. Do not use this folder.", file=sys.stderr)
        return 1
    print(f"\npreview: {a.out / 'audio_preview.mp4'}\nplace it with: python3 labs/audio/place_audio.py <export.xml> \"{a.out}\" --out <new.xml>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
