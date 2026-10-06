"""Resolve reel cuts to word-level in and out points in the camera clips (standalone; nothing in app/).

    python3 labs/recruit/rough_cut.py --cuts cuts.json --units "May15=<folder>" --units "Jun13=<folder>" --synced <synced.json> --media media.json --out resolved.json

`cuts.json`: {"pitches": [{"key", "title", "cuts": [{"unit", "id", "role", "from": "<opening words>", "to": "<closing words>" (optional: same as from)}]}]}.
Each cut is a range of a moment's transcript, named by the words it starts and ends on. For every cut:
  1. the moment's place in a camera clip is found (a moment from the May 15 interview is in its own camera clip; a weekend moment uses sync_audio's match). The WORDS are timed on the cleanest audio there is: for a weekend
     moment the speaker's own recorder (a camera microphone hears a different sentence on hard phrases: for "not the right fit to operate" the camera's Whisper wrote "not going to like you" and its large model "the right thing to tell them", while the
     recorder's small and large models agree with the sequence transcript), mapped to camera time by the sync offset; for a May 15 moment, the camera's own audio, which has no recorder sync;
  2. the opening and closing words are found among those words (fuzzy: the camera hears things slightly differently from the microphone the moment was found on) and refused if they do not match well (MIN_RATIO);
  3. the in point is moved to the quietest spot just before the first word and the out point to the quietest spot just after the last word, so a word is not clipped and a neighbouring one is not let in;
  4. both are rounded OUT to whole frames at 29.97 fps (the in down, the out up).
Nothing is guessed: a phrase that is not in the moment's transcript, or not found in the camera audio, stops the run with the cut named. `media.json` maps a camera clip's name to {"analysis": <file to listen to>, "original": <file the XML points at>}."""
from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

FPS = 30000 / 1001
MIN_RATIO = 0.62
LEAD_SEARCH = (0.30, 0.02)        # the in point is the quietest moment this long before the first word
TAIL_SEARCH = (0.04, 0.45)        # the out point is the quietest moment this long after the last word
FRAME_MS = 10
MAX_CUT_SEC = 70.0
SR = 16000


class CutError(Exception):
    pass


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", s.lower())).strip()


def phrase_in_text(phrase: str, text: str) -> bool:
    return norm(phrase) in norm(text)


def frame_floor(t: float) -> float:
    return math.floor(t * FPS + 1e-6) / FPS


def frame_ceil(t: float) -> float:
    return math.ceil(t * FPS - 1e-6) / FPS


def variants(phrase: str) -> list[str]:
    """The ways a speech recogniser may write the same words: a number with a percent sign is also written out ('100%' -> '100 percent', 'a hundred percent'), and a dollar figure
    ('$88') as '88 dollars'. The phrase itself always comes first."""
    out = [phrase]
    if re.search(r"\d+%", phrase):
        out.append(re.sub(r"(\d+)%", r"\1 percent", phrase))
        if "100%" in phrase:
            out += [phrase.replace("100%", "a hundred percent"), phrase.replace("100%", "one hundred percent")]
    if re.search(r"\$\d+", phrase):
        out.append(re.sub(r"\$(\d+)", r"\1 dollars", phrase))
    return out


FIRST_OK = 0.75              # the first place the phrase matches this well is where it is, even if a cleaner match comes later


def find_phrase(phrase: str, words: list[tuple[str, float, float]], start_index: int = 0) -> tuple[int, int, float] | None:
    """(first word index, last word index, ratio) for `phrase` (or a spoken-form variant of it) among consecutive words at or after `start_index`, or None.

    Every starting word is scored with its best window length. The phrase is where it FIRST matches well (FIRST_OK or better): within that first region (the next len(phrase) starting words) the best alignment wins,
    so a window with one stray extra word in front does not beat the exact one a word later. A closing phrase such as 'check it' is therefore the first 'check it' after the opening, even when a later one is a cleaner
    match (the recogniser wrote 'checked it' at the first and 'check it' at the second). If no region reaches FIRST_OK, the best window anywhere is returned when it clears MIN_RATIO."""
    per_start = []
    n = len(norm(phrase).split())
    for i in range(start_index, len(words)):
        best_i = None
        for v in variants(phrase):
            target = norm(v)
            m = len(target.split())
            for w in range(max(1, m - 2), m + 3):
                if i + w > len(words):
                    break
                r = difflib.SequenceMatcher(None, target, norm(" ".join(x[0] for x in words[i:i + w]))).ratio()
                if best_i is None or r > best_i[2]:
                    best_i = (i, i + w - 1, r)
        if best_i:
            per_start.append(best_i)
    for k, cand in enumerate(per_start):
        if cand[2] >= FIRST_OK:
            region = per_start[k:k + n + 1]
            return max(region, key=lambda c: (c[2], -c[0]))
    best = max(per_start, key=lambda c: c[2]) if per_start else None
    return best if best and best[2] >= MIN_RATIO else None


QUIET_RUN_SEC = 0.09           # a safe silence: longer than any stop consonant inside a word (those last about 60 to 80 ms)
SHORT_QUIET_SEC = 0.05         # a shorter silence is accepted only BETWEEN two known words that are close together
ABUT_SEC = 0.35
QUIET_ABOVE_FLOOR_DB = 6.0      # quiet = within 6 dB of THIS recording's own noise floor ...
QUIET_BELOW_SPEECH_DB = 6.0     # ... but always at least 6 dB below the speech itself
OUT_PAD, IN_PAD = 0.05, 0.10
OUT_FALLBACK, IN_FALLBACK = 0.30, 0.12
TAIL_MAX, LEAD_MAX = 0.80, 0.60


def energy(x: np.ndarray, sr: int = SR, frame_ms: int = FRAME_MS) -> tuple[np.ndarray, float]:
    n = int(sr * frame_ms / 1000)
    k = len(x) // n
    if k == 0:
        return np.zeros(0), frame_ms / 1000
    return np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(axis=1)), frame_ms / 1000


def quiet_threshold(rms: np.ndarray, a: int, b: int) -> float:
    """The level (linear rms) below which a frame counts as quiet. A recorder worn on the chest sits near -47 dB between words and its speech near -22 dB; an outdoor camera microphone never gets below about -27 dB
    and an indoor one has only about 12 to 17 dB between speech and its dips. So 'quiet' is relative to the recording itself: within 6 dB of its own floor (the 10th percentile of its frames), but never closer than 6 dB to
    the speech (the median of the frames inside the words, a to b)."""
    live = rms[rms > 1e-6]
    if len(live) == 0:
        return 1e-4
    level = float(np.median(rms[a:b])) if b > a else float(rms.max())
    floor_db = 20 * np.log10(max(float(np.percentile(live, 10)), 1e-6))
    level_db = 20 * np.log10(max(level, 1e-6))
    return max(10 ** (min(floor_db + QUIET_ABOVE_FLOOR_DB, level_db - QUIET_BELOW_SPEECH_DB) / 20), 1e-4)


def quiet_intervals(rms: np.ndarray, thr: float, t_lo: float, t_hi: float, t0: float, frame_sec: float) -> list[tuple[float, float]]:
    """The maximal runs of quiet frames (rms below thr) between t_lo and t_hi, as (start, end) in the clock of t0, in time order."""
    a = max(0, int(round((t_lo - t0) / frame_sec)))
    b = min(len(rms), int(round((t_hi - t0) / frame_sec)))
    out, k = [], a
    while k < b:
        if rms[k] < thr:
            e = k
            while e < b and rms[e] < thr:
                e += 1
            out.append((t0 + k * frame_sec, t0 + e * frame_sec))
            k = e
        else:
            k += 1
    return out


VALLEY_BACK, VALLEY_FWD = 0.08, 0.50
SMOOTH_FRAMES = 3


def valley(rms: np.ndarray, t_lo: float, t_hi: float, t0: float, frame_sec: float, prefer: str) -> float | None:
    """The time of the deepest valley of the (3-frame smoothed) energy between t_lo and t_hi, or None for an empty range. This is where continuous speech can be cut when there is no real pause: between two words, the energy
    dips even if it never reaches silence. `prefer` 'late' or 'early' breaks ties toward that end of the range."""
    a = max(0, int(round((t_lo - t0) / frame_sec)))
    b = min(len(rms), int(round((t_hi - t0) / frame_sec)))
    if b - a < 2:
        return None
    sm = np.convolve(rms, np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES, mode="same")[a:b]
    m = float(sm.min())
    ties = [i for i, v in enumerate(sm) if v <= m * 1.12]
    k = ties[-1] if prefer == "late" else ties[0]
    return t0 + (a + k + 0.5) * frame_sec


def snap_detail(first_start: float, last_end: float, rms: np.ndarray, t0: float, frame_sec: float, prev_end: float | None = None, next_start: float | None = None) -> dict:
    """In and out for words that run from `first_start` to `last_end` (the clock of `t0`, the start of `rms`), and the quiet intervals found: {"in", "out", "lead_quiet", "tail_quiet"}.

    The OUT point is where speech actually stops, found in tiers. (1) The first quiet interval of at least 90 ms after the last word (quiet is relative to the recording, see quiet_threshold): longer than any stop consonant
    inside a word, so it cannot be a dip in the word's own end; Whisper's word ends run early, so a plain 'quietest frame' is not safe. (2) If that finds nothing and the next word starts within 0.35 s, the longest quiet
    interval of at least 50 ms BETWEEN the two words: a short silence is believable only between two known words. (3) Otherwise 0.30 s after the last word. It never goes past the next word's start (minus 20 ms). `tail_quiet`
    is the interval used (None for tier 3); out sits 50 ms into it, or in its middle when it is shorter than 100 ms. The IN point mirrors this before the first word (`lead_quiet`), never earlier than the previous word's end
    (plus 20 ms), and at least 0.10 s ahead of the first word when there was no silence to use (a soft onset such as an 'f' is easy to clip). resolve_overlaps puts a shared boundary in the middle of the silence."""
    if len(rms) == 0:
        return {"in": first_start - IN_FALLBACK, "out": last_end + OUT_FALLBACK, "lead_quiet": None, "tail_quiet": None}
    idx = lambda t: int(round((t - t0) / frame_sec))                                    # noqa: E731
    a, b = max(0, idx(first_start)), min(len(rms), max(idx(first_start) + 1, idx(last_end)))
    thr = quiet_threshold(rms, a, b)
    hi = last_end + TAIL_MAX
    if next_start is not None:
        hi = min(hi, next_start - 0.02)
    tail = None
    ivs = quiet_intervals(rms, thr, last_end, hi, t0, frame_sec)
    safe = [iv for iv in ivs if iv[1] - iv[0] >= QUIET_RUN_SEC]
    if safe:
        tail = safe[0]
    elif next_start is not None and next_start - last_end <= ABUT_SEC:
        short = [iv for iv in ivs if iv[1] - iv[0] >= SHORT_QUIET_SEC]
        tail = max(short, key=lambda iv: iv[1] - iv[0]) if short else None
    if tail is not None:
        out = tail[0] + OUT_PAD if tail[1] - tail[0] >= 2 * OUT_PAD else (tail[0] + tail[1]) / 2
    else:                                                                                 # continuous speech: cut in the deepest valley after the last word
        v = valley(rms, last_end - VALLEY_BACK, min(last_end + VALLEY_FWD, hi), t0, frame_sec, "early")
        out = v if v is not None else min(last_end + OUT_FALLBACK, hi)
    out = max(min(out, hi), last_end + 0.02)
    lo = first_start - LEAD_MAX
    if prev_end is not None:
        lo = max(lo, prev_end + 0.02)
    lead = None
    ivs = quiet_intervals(rms, thr, lo, first_start, t0, frame_sec)
    safe = [iv for iv in ivs if iv[1] - iv[0] >= QUIET_RUN_SEC]
    if safe:
        lead = safe[-1]
    elif prev_end is not None and first_start - prev_end <= ABUT_SEC:
        short = [iv for iv in ivs if iv[1] - iv[0] >= SHORT_QUIET_SEC]
        lead = max(short, key=lambda iv: iv[1] - iv[0]) if short else None
    if lead is not None:
        t_in = lead[1] - OUT_PAD if lead[1] - lead[0] >= 2 * OUT_PAD else (lead[0] + lead[1]) / 2
    else:                                                                                 # continuous speech: cut in the deepest valley just before the first word
        v = valley(rms, max(lo, first_start - VALLEY_FWD), first_start + 0.03, t0, frame_sec, "late")
        t_in = v if v is not None else first_start - IN_FALLBACK
    t_in = max(min(t_in, first_start - 0.03 if lead is not None else first_start - 0.02), lo)
    return {"in": min(t_in, first_start - 0.02), "out": out, "lead_quiet": lead, "tail_quiet": tail}


def snap(first_start: float, last_end: float, rms: np.ndarray, t0: float, frame_sec: float, prev_end: float | None = None, next_start: float | None = None) -> tuple[float, float]:
    d = snap_detail(first_start, last_end, rms, t0, frame_sec, prev_end, next_start)
    return d["in"], d["out"]


def read_audio(media: Path, t0: float, dur: float) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0)}", "-t", f"{dur}", "-i", str(media), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if r.returncode:
        raise CutError(f"could not read audio from {media.name}: {r.stderr.decode()[:160]}")
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def doubted(seg: dict) -> bool:
    """Whisper's own rule for a segment that is probably not speech: it says so (no_speech_prob over 0.6) and is not confident in the words (avg_logprob under -1.0). Recognisers invent text over trailing silence."""
    return float(seg.get("no_speech_prob", 0.0)) > 0.6 and float(seg.get("avg_logprob", 0.0)) < -1.0


def transcribe_words(model, audio: np.ndarray, t0: float) -> list[tuple[str, float, float]]:
    res = model.transcribe(audio, language="en", word_timestamps=True, condition_on_previous_text=False, fp16=False)
    return [(w["word"].strip(), t0 + float(w["start"]), t0 + float(w["end"])) for seg in res["segments"] if not doubted(seg) for w in seg.get("words", []) if w["word"].strip()]


def lav_to_camera_delta(row: dict, synced: dict) -> float:
    """Seconds to ADD to a time on the speaker's recorder to get the same moment on the camera clip: the camera clip's time for the moment's first word minus the recorder's."""
    return float(synced["camera_start"]) - float(row["speech_start"])


def resolve_cut(cut: dict, row: dict, synced: dict | None, media: dict, model) -> dict:
    """One cut's camera clip, word-level in/out (frame aligned) and the evidence. Raises CutError, naming the cut, when it cannot be placed.

    A weekend cut is analysed on the speaker's own recorder (words AND where it goes quiet: a chest-worn microphone has a clean floor), then everything is mapped to camera time by the sync offset. A May 15 cut is analysed on
    the camera's own audio, which is all there is."""
    cid = f"{cut['unit']}/{cut['id']}"
    a, b = cut["from"], cut.get("to", cut["from"])
    for p in (a, b):
        if not phrase_in_text(p, row["text"]):
            raise CutError(f"{cid}: the words {p!r} are not in that moment's transcript")
    dur = row["speech_end"] - row["speech_start"]
    if cut["unit"] == "May15" or not synced:
        stem, t_speech = row["clip"], row["speech_start"]
        mics, mic_t0, shift = None, None, 0.0
    else:
        if not synced.get("synced"):
            raise CutError(f"{cid}: the moment is not synced to a camera, so it has no picture to cut")
        stem, t_speech = synced["camera"], synced["camera_start"]
        mics, mic_t0, shift = media.get("_mics"), max(0.0, row["speech_start"] - 3.0), lav_to_camera_delta(row, synced)
    if stem not in media:
        raise CutError(f"{cid}: no media known for camera clip {stem}")
    win = dur + 6.0
    if mics:
        mic = Path(mics) / f"{row['clip']}.WAV"
        if not mic.exists():
            raise CutError(f"{cid}: the recorder file {mic.name} is not there")
        audio, clock0, heard_on = read_audio(mic, mic_t0, win), mic_t0, "the speaker's own recorder"
    else:
        clock0 = max(0.0, t_speech - 3.0)
        audio, heard_on = read_audio(Path(media[stem]["analysis"]), clock0, win), "the camera's own audio"
    words = transcribe_words(model, audio, clock0)
    m1 = find_phrase(a, words)
    if not m1:
        raise CutError(f"{cid}: the opening words {a!r} were not found in {heard_on}")
    m2 = find_phrase(b, words, m1[0]) if b != a else (m1[0], m1[1], m1[2])
    if not m2:
        raise CutError(f"{cid}: the closing words {b!r} were not found after the opening in {heard_on}")
    first, last = words[m1[0]], words[m2[1]]
    rms, fs = energy(audio)
    prev_end = words[m1[0] - 1][2] if m1[0] > 0 else None
    next_start = words[m2[1] + 1][1] if m2[1] + 1 < len(words) else None
    d = snap_detail(first[1], last[2], rms, clock0, fs, prev_end, next_start)
    to_cam = lambda t: None if t is None else t + shift                                  # noqa: E731
    i_s, o_s = frame_floor(max(0.0, d["in"] + shift)), frame_ceil(d["out"] + shift)
    if o_s - i_s > MAX_CUT_SEC or o_s <= i_s:
        raise CutError(f"{cid}: the cut came out {o_s - i_s:.1f} s long, which is not plausible")
    return {"pitch_unit": cut["unit"], "id": cut["id"], "role": cut.get("role", ""), "from": a, "to": b, "camera": stem, "source_original": media[stem]["original"],
            "in_sec": round(i_s, 4), "out_sec": round(o_s, 4), "first_word": first[0], "last_word": last[0], "first_word_at": round(first[1] + shift, 3), "last_word_end": round(last[2] + shift, 3),
            "lead_quiet": None if d["lead_quiet"] is None else [round(to_cam(d["lead_quiet"][0]), 3), round(to_cam(d["lead_quiet"][1]), 3)],
            "tail_quiet": None if d["tail_quiet"] is None else [round(to_cam(d["tail_quiet"][0]), 3), round(to_cam(d["tail_quiet"][1]), 3)],
            "ratios": [round(m1[2], 2), round(m2[2], 2)], "heard": " ".join(w[0] for w in words[m1[0]:m2[1] + 1]), "words_from": heard_on}


def resolve_overlaps(cuts: list[dict]) -> list[str]:
    """Make cuts from the same camera clip touch instead of overlap, in place; returns the notes (and raises CutError when two cuts truly repeat the same WORDS).

    Each cut's in and out are already outside its own words, so where two cuts of one source overlap the overlap is the gap between the earlier one's last word and the later one's first. The shared boundary goes in the
    SILENCE of that gap (the middle of where the earlier cut's quiet run and the later one's meet), or at the middle of the gap when no silence was found, on a whole frame: both cuts keep their words and neither starts on a
    consonant that was already beginning. If the earlier cut's words run into the later cut's, the two cuts repeat the same speech and that is refused (a line may play once; a hook is a fragment moved to the front,
    as in the wallpaper reel, not a repeat)."""
    notes = []
    by_src: dict[str, list[dict]] = {}
    for c in cuts:
        by_src.setdefault(c["source_original"], []).append(c)
    for src, group in by_src.items():
        group.sort(key=lambda c: c["in_sec"])
        for a_, b_ in zip(group, group[1:]):
            if b_["in_sec"] >= a_["out_sec"] - 1e-6:
                continue
            if a_["last_word_end"] > b_["first_word_at"] - 0.01:
                raise CutError(f"{a_['id']} '{a_['role']}' and {b_['id']} '{b_['role']}' repeat the same words ({a_['last_word_end']:.2f} s runs into {b_['first_word_at']:.2f} s)")
            ta, lb = a_.get("tail_quiet"), b_.get("lead_quiet")
            both = ta is not None and lb is not None and max(ta[0], lb[0]) < min(ta[1], lb[1])
            mid = (max(ta[0], lb[0]) + min(ta[1], lb[1])) / 2 if both else (a_["last_word_end"] + b_["first_word_at"]) / 2
            lo, hi = a_["last_word_end"] + 0.005, b_["first_word_at"] - 0.005
            f = min(max(round(mid * FPS) / FPS, lo), hi)
            a_["out_sec"] = round(min(a_["out_sec"], f), 4)
            b_["in_sec"] = round(max(b_["in_sec"], f), 4)
            notes.append(f"{a_['id']} '{a_['role']}' and {b_['id']} '{b_['role']}' overlapped; they now meet at {f:.3f} s" + (" (in the silence between them)" if both else " (the middle of the gap between their words)"))
    return notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--units", action="append", required=True)
    ap.add_argument("--synced", required=True, help="synced.json from sync_audio.py (weekend moments)")
    ap.add_argument("--media", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="small")
    a = ap.parse_args(argv)
    import whisper
    units = {}
    for sp in a.units:
        name, _, path = sp.partition("=")
        units[name] = {r["id"]: r for r in json.loads((Path(path).expanduser() / "selects.json").read_text())}
    synced = {d["id"]: d for d in json.loads(Path(a.synced).expanduser().read_text())}
    media = json.loads(Path(a.media).expanduser().read_text())
    spec = json.loads(Path(a.cuts).expanduser().read_text())
    model = whisper.load_model(a.model)
    out, errors = [], []
    for p in spec["pitches"]:
        resolved = []
        for cut in p["cuts"]:
            row = units.get(cut["unit"], {}).get(cut["id"])
            if row is None:
                errors.append(f"{p['key']}: no moment {cut['unit']}/{cut['id']}")
                continue
            try:
                r = resolve_cut(cut, row, synced.get(cut["id"]) if cut["unit"] != "May15" else None, media, model)
                resolved.append(r)
                print(f"  {p['key']} {cut['id']:4} {r['camera'][-14:]} {r['in_sec']:8.2f} to {r['out_sec']:8.2f} ({r['out_sec'] - r['in_sec']:4.1f} s) ratios {r['ratios']}  '{r['heard'][:60]}'", flush=True)
            except CutError as e:
                errors.append(f"{p['key']}: {e}")
        try:
            for n in resolve_overlaps(resolved):
                print(f"  {p['key']} NOTE {n}")
        except CutError as e:
            errors.append(f"{p['key']}: {e}")
        out.append({"key": p["key"], "title": p["title"], "cuts": resolved})
    Path(a.out).expanduser().write_text(json.dumps({"pitches": out, "errors": errors}, indent=1))
    if errors:
        print("ERRORS:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
