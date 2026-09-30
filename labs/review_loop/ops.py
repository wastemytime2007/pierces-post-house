"""Review notes -> timeline operations.

The vocabulary is deliberately small and every op is something that can be done
to an XML timeline deterministically and checked afterwards. The interpreter (an
LLM) only chooses among them; it does not get to invent a time.

  tighten_pause {at}             find the real silence near `at` in the audio and
                                 shorten it. Measured, not guessed.
  extend_end {clip}              a word is cut off; the amount is read from the audio's decay
  start_at_words {clip, words}   start the clip at named words, found with word timing
  remove_range {start, end}      only when the note itself states the times
  trim_start / trim_end {clip, seconds}   only when the note states the amount
  drop_clip {clip}               only when the note says to remove the clip
  replace_sfx {sound}            the note wants a sound EFFECT at this moment to sound different; the
                                 description is the note's own words. Made by labs/audio/replace_sfx.py,
                                 not on the timeline, so revise.py reports it as not applied here.
  unsupported {reason}           everything else, reported and never silently dropped

All times are on the timeline the notes were left on (V1).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

import numpy as np

import words as words_mod
from timeline import Cut

SR = 8000
KEEP_SEC_DEFAULT = 0.15
MIN_SILENCE_SEC = 0.25
NOTE_WORDS = r"one|two|three|four|five|six|seven|eight|nine|ten|half"
HAS_NUMBER = re.compile(rf"\d|\b({NOTE_WORDS})\b", re.I)
SAYS_SOUND = re.compile(r"\b(sfx|sound effects?|sound|noise|whoosh|swoosh|pop|click|ding|chime|beep|clap|thud)\b", re.I)
SAYS_REMOVE = re.compile(r"\b(remove|delete|drop|get rid|lose|kill|take (this|it|that) out|cut (this|it|that)( out| clip| shot)?)\b", re.I)

SYSTEM = """You translate an editor's review notes on a rough cut into edit operations.
Reply with ONLY a JSON array. Each item: {"note": <1-based note number>, "op": <name>, ...params, "why": <one short sentence>}.

Operations (times are seconds on the timeline the notes were left on):
- tighten_pause {"at": t}            The note says a pause/gap/silence/dead air should be tighter or cut. Use the note's own time as "at". The pause is located later by measuring the audio, so never guess a duration.
- remove_range {"start": s, "end": e}  ONLY if the note itself states both times.
- trim_start {"clip": n, "seconds": x}, trim_end {"clip": n, "seconds": x}  ONLY if the note states how many seconds.
- extend_end {"clip": n, "max_sec": 1.0}  The note says a word or sentence at the END of clip n is cut off too soon or needs more time to finish. "clip" is the note's own clip unless the note says otherwise. The amount is measured from how the sound decays, so never give a duration.
- start_at_words {"clip": n, "words": "..."}  The note says clip n should START at specific words, dropping words before them (for example "the clean cut should be X to Y": the clip after the seam starts at Y). "words" must be copied from the note. The point is found by listening, so never give a time.
- drop_clip {"clip": n}              ONLY if the note clearly says to remove/delete that clip or shot.
- replace_sfx {"sound": "..."}       The note says a sound EFFECT (a whoosh, pop, click, swoosh, "sound effect") at this moment should sound different, and says what it should sound like. "sound" must be copied from the note's own words describing the wanted sound. The effect and its time are found from the audio project, so never give a time.
- unsupported {"reason": "..."}      Anything else: swapping to different footage, reframing or cropping, adding graphics or text, music, audio levels, colour, vague taste notes, or drawings that need interpretation. Say plainly what would be needed.

Rules: never invent a time or an amount that the note does not give. A note may produce more than one op. Every note must appear at least once. Prefer unsupported over guessing."""


def describe_shape(s: dict) -> str:
    b = s.get("bbox", {})
    return f"{s.get('type')} in the {s.get('region', '?')} (x {b.get('x0')}-{b.get('x1')}, y {b.get('y0')}-{b.get('y1')} of the frame)"


def build_prompt(cut: Cut, notes: list[dict]) -> str:
    lines = ["CLIPS (timeline seconds, source):"]
    for c in cut.video:
        lines.append(f"clip {c.idx}: {c.tl_start:.2f}-{c.tl_end:.2f}s  {c.name}  source {c.src_in:.2f}-{c.src_out:.2f}s")
    lines.append(f"\nCut length: {cut.zone_end:.2f}s\n\nNOTES:")
    for i, n in enumerate(notes, start=1):
        drawn = "; ".join(describe_shape(s) for s in n.get("shapes", []))
        lines.append(f'note {i} at {n["timeline_sec"]}s (clip {n.get("clip")}): "{n.get("text", "")}"'
                     + (f"  [drawn on frame: {drawn}]" if drawn else ""))
    return "\n".join(lines)


def _json_array(text: str) -> list:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    a, b = t.find("["), t.rfind("]")
    if a < 0 or b < a:
        raise ValueError("no JSON array in interpreter reply")
    return json.loads(t[a:b + 1])


def _unsupported(note: int, reason: str, why: str = "") -> dict:
    return {"note": note, "op": "unsupported", "reason": reason, "why": why}


def validate(ops: list, notes: list[dict], cut: Cut) -> list[dict]:
    """Coerce anything invalid into a reported `unsupported`, and make sure every note is accounted for."""
    n_clips, zone = len(cut.video), cut.zone_end
    out: list[dict] = []
    for raw in ops:
        try:
            note = int(raw["note"])
            if not 1 <= note <= len(notes):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        text = notes[note - 1].get("text", "")
        op, why = raw.get("op"), str(raw.get("why", ""))[:200]
        try:
            if op == "tighten_pause":
                at = float(raw.get("at", notes[note - 1]["timeline_sec"]))
                if not 0 <= at <= zone:
                    raise ValueError(f"'at' {at} outside the cut")
                out.append({"note": note, "op": op, "at": at, "why": why})
            elif op == "remove_range":
                s, e = float(raw["start"]), float(raw["end"])
                if not 0 <= s < e <= zone:
                    raise ValueError(f"range {s}-{e} outside the cut or empty")
                if not HAS_NUMBER.search(text):
                    raise ValueError("note states no explicit time, refusing to invent one")
                out.append({"note": note, "op": op, "start": s, "end": e, "why": why})
            elif op in ("trim_start", "trim_end"):
                clip, sec = int(raw["clip"]), float(raw["seconds"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                length = cut.video[clip - 1].tl_end - cut.video[clip - 1].tl_start
                if not 0 < sec < length:
                    raise ValueError(f"{sec}s does not fit a {length:.2f}s clip")
                if not HAS_NUMBER.search(text):
                    raise ValueError("note states no amount, refusing to invent one")
                out.append({"note": note, "op": op, "clip": clip, "seconds": sec, "why": why})
            elif op == "extend_end":
                clip = int(raw.get("clip", notes[note - 1].get("clip", 0)))
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                mx = float(raw.get("max_sec", 1.0))
                out.append({"note": note, "op": op, "clip": clip, "max_sec": min(max(mx, 0.2), 1.5), "why": why})
            elif op == "start_at_words":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                phrase = str(raw["words"]).strip()
                pt = words_mod.tokens(phrase)
                if not pt or len(pt) > 14:
                    raise ValueError("no usable words")
                if " ".join(pt) not in " ".join(words_mod.tokens(text)):
                    raise ValueError("those words are not in the note, refusing to invent a target")
                out.append({"note": note, "op": op, "clip": clip, "words": phrase, "why": why})
            elif op == "drop_clip":
                clip = int(raw["clip"])
                if not 1 <= clip <= n_clips:
                    raise ValueError(f"clip {clip} does not exist")
                if not SAYS_REMOVE.search(text):
                    raise ValueError("note does not say to remove the clip")
                out.append({"note": note, "op": op, "clip": clip, "why": why})
            elif op == "replace_sfx":
                sound = str(raw.get("sound", "")).strip()
                st, nt = words_mod.tokens(sound), set(words_mod.tokens(text))
                if not st or len(sound) > 200:
                    raise ValueError("no usable description of the wanted sound")
                if not SAYS_SOUND.search(text):
                    raise ValueError("the note does not talk about a sound effect")
                if sum(w in nt for w in st) < 0.7 * len(st):
                    raise ValueError("that description is not in the note, refusing to invent a sound")
                out.append({"note": note, "op": op, "sound": sound, "why": why})
            elif op == "unsupported":
                out.append(_unsupported(note, str(raw.get("reason", "not supported"))[:300], why))
            else:
                raise ValueError(f"unknown op {op!r}")
        except (KeyError, TypeError, ValueError) as e:
            out.append(_unsupported(note, f"interpreter proposed {op!r} but it was refused: {e}", why))
    covered = {o["note"] for o in out}
    for i in range(1, len(notes) + 1):
        if i not in covered:
            out.append(_unsupported(i, "no operation was produced for this note"))
    return sorted(out, key=lambda o: o["note"])


def interpret(cut: Cut, notes: list[dict], client=None) -> list[dict]:
    if client is None:
        from posthouse.cli_llm_client import CLIBackedClient
        client = CLIBackedClient()
    resp = client.messages.create(system=SYSTEM, max_tokens=2000, temperature=0,
                                  messages=[{"role": "user", "content": build_prompt(cut, notes)}])
    return validate(_json_array(resp.content[0].text), notes, cut)


@dataclass
class Pause:
    start: float   # timeline seconds
    end: float
    floor_db: float


def _pcm(path: str, start: float, dur: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.4f}", "-t", f"{dur:.4f}", "-i", path,
                        "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def detect_pause(cut: Cut, at: float, window: float = 3.0, max_distance: float = 1.0) -> Pause | None:
    """The silence run closest to `at` in the audio the cut actually plays, or None.

    The threshold adapts to the clip: 3x the quiet-frame level, so a noisy room and a
    dead-quiet one are both judged against their own floor.
    """
    clip = next((c for c in cut.video if c.tl_start <= at < c.tl_end), cut.video[-1])
    lo, hi = max(clip.tl_start, at - window), min(clip.tl_end, at + window)
    src = next((a for a in cut.audio if a.tl_start <= at < a.tl_end), None) or clip
    t0 = src.src_in + (lo - src.tl_start)
    pcm = _pcm(src.src_path, t0, hi - lo)
    frame = int(SR * 0.02)
    n = len(pcm) // frame
    if n < 10:
        return None
    rms = np.sqrt((pcm[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    floor = float(np.percentile(rms, 10))
    loud = float(np.percentile(rms, 90))
    # 3x the quiet level, but never above 0.2x the loud level: with no real contrast
    # in the window (steady noise, continuous speech) nothing counts as a pause.
    thresh = max(min(floor * 3.0, loud * 0.2), 10 ** (-60 / 20))
    quiet = rms < thresh
    runs, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= MIN_SILENCE_SEC:
                runs.append((lo + i * 0.02, lo + j * 0.02))
            i = j
        else:
            i += 1
    if not runs:
        return None
    def dist(r): return 0.0 if r[0] <= at <= r[1] else min(abs(r[0] - at), abs(r[1] - at))
    best = min(runs, key=dist)
    if dist(best) > max_distance:
        return None
    return Pause(best[0], best[1], 20 * np.log10(max(floor, 1e-9)))


def _audio_for(cut: Cut, clip):
    a = next((a for a in cut.audio if abs(a.tl_start - clip.tl_start) < 0.05), None)
    return (a.src_path, a.src_in, a.src_out) if a else (clip.src_path, clip.src_in, clip.src_out)


def _threshold(rms: np.ndarray) -> float:
    floor, loud = float(np.percentile(rms, 10)), float(np.percentile(rms, 90))
    return max(min(floor * 3.0, loud * 0.2), 10 ** (-60 / 20))


def _rms10(pcm: np.ndarray) -> np.ndarray:
    n = len(pcm) // 80
    return np.sqrt((pcm[: n * 80].reshape(n, 80) ** 2).mean(axis=1)) if n else np.zeros(0)


def level_db(path: str, t: float, dur: float = 0.03) -> float:
    r = _rms10(_pcm(path, max(0.0, t), dur))
    return 20 * float(np.log10(max(float(r.mean()) if len(r) else 1e-6, 1e-6)))


def measure_tail(cut: Cut, clip_idx: int, max_sec: float) -> dict:
    """How far past the cut the last sound keeps going, read from the audio's decay.

    Returns {"ext": seconds, "path", "t_end" (source), "thresh_db", "at_cut_db"} or {"reason": ...}.
    """
    clip = cut.video[clip_idx - 1]
    path, _src_in, src_out = _audio_for(cut, clip)
    lo = max(0.0, src_out - 1.0)
    pcm = _pcm(path, lo, 1.0 + max_sec + 0.3)
    rms = _rms10(pcm)
    if len(rms) < 30:
        return {"reason": "not enough audio after the cut to measure"}
    thresh = _threshold(rms)
    cut_i = int(round((src_out - lo) * 100))
    at_cut = float(rms[max(0, cut_i - 5):cut_i].mean())
    if at_cut < thresh:
        return {"reason": f"the audio is already quiet at the cut ({20 * np.log10(max(at_cut, 1e-6)):.0f} dB, room level), so nothing looks cut off"}
    j = cut_i
    while j + 3 <= len(rms) and not (rms[j:j + 3] < thresh).all():
        j += 1
    if j + 3 > len(rms) or (j - cut_i) * 0.01 > max_sec:
        return {"reason": f"the sound keeps going for more than {max_sec:g}s after the cut, so it is not a single word finishing; give an explicit amount instead"}
    ext = (j - cut_i) * 0.01 + 0.04
    if ext < 0.05:
        return {"reason": "the sound has already decayed at the cut"}
    nxt = cut.video[clip_idx] if clip_idx < len(cut.video) else None
    if nxt and nxt.src_path == clip.src_path and nxt.src_in < clip.src_out + ext + 0.02:
        return {"reason": "extending would run into the next clip's footage"}
    return {"ext": ext, "path": path, "t_end": src_out + ext, "thresh_db": 20 * float(np.log10(thresh)),
            "at_cut_db": 20 * float(np.log10(max(at_cut, 1e-6)))}


def locate_start(cut: Cut, clip_idx: int, phrase: str) -> dict:
    """Where clip `clip_idx` should start so it begins at `phrase`. Returns {"trim": s, ...} or {"reason": ...}."""
    clip = cut.video[clip_idx - 1]
    path, src_in, _ = _audio_for(cut, clip)
    ws = words_mod.words_in(path, src_in - 0.6, 6.6)
    ws = [w for w in ws if w.end > src_in - 0.05]
    hit = words_mod.find_phrase(ws, phrase)
    if hit is None:
        return {"reason": f"could not find \"{phrase}\" near the start of clip {clip_idx}; the audio there reads: \"{words_mod.heard(ws[:10])}\""}
    idx = hit[0]
    t = words_mod.valley(path, ws[idx].start - 0.12, ws[idx].start)
    trim = t - src_in
    dropped = words_mod.heard(ws[:idx])
    if trim < 0.05:
        return {"reason": f"clip {clip_idx} already starts at \"{ws[idx].text}\""}
    if trim > 3.0:
        return {"reason": f"starting at those words would remove {trim:.1f}s, which is too much to do without confirmation"}
    return {"trim": trim, "dropped": dropped, "at_word": ws[idx].text, "heard": words_mod.heard(ws[:idx + 6])}
