#!/usr/bin/env python3
"""Bleep the curse words in a cut: find them by word timing, silence them in the speech, lay a bleep over each.

    PRECUT_ROOT=~/precut-checkout python3 labs/bleep/bleep.py --xml "<cut .xml>" --out "<new folder>" [--words "a,b"] [--list profanity.txt]

This is a standing rule, not a request: every cut is scanned and every word on `profanity.txt` is bleeped without being asked.
Reconform runs it by default (`--no-bleep` turns it off). The list is a plain text file you can edit.

What it does, in order:
  1. renders the speech of the cut from the XML's enabled audio clips and transcribes it with word timing (Whisper, English);
  2. matches whole words against the list (ass never matches class; fuck* also matches fucking);
  3. pads each hit (0.08 s before, 0.12 s after: word timing is good to about a tenth of a second), merges overlaps;
  4. SILENCES the speech there by splitting each speech clip at the span and disabling the inner piece (the picture is untouched);
  5. makes a 1 kHz bleep per span, peak 6 dB under the speech peak, and places each as an audio layer (lane "Bleep");
  6. re-measures from files: the spans are silent, the audio just outside them is unchanged, each bleep is a 1 kHz tone at the
     right level and time, and a second transcription of the result finds no listed word left.

Limits: a word Whisper mishears or misses is not bleeped (the last check says so when it can tell); padding can clip a
neighbouring word by a few hundredths of a second; the bleep is a plain tone, not a chosen sound.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
for d in (LABS / "review_loop", LABS / "audio", LABS / "overlay", LABS.parent / "safety_net"):
    sys.path.insert(0, str(d))
import timeline  # noqa: E402

LIST_FILE = HERE / "profanity.txt"
SR = 16000
OUT_SR = 48000
PAD_BEFORE, PAD_AFTER = 0.08, 0.12


def _model() -> dict:
    """What labs/bleep/learn.py has learned from Ryan's edits (small, bounded, starts from the defaults). Missing or unreadable: nothing learned, defaults."""
    p = Path(os.environ.get("POSTHOUSE_BLEEP_LEARNING") or Path(__file__).resolve().parent / "learning") / "model.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def learned_pads(source: str) -> tuple[float, float]:
    m = _model().get("pads", {}).get(source)
    return (float(m["before"]), float(m["after"])) if m and m.get("n") else (PAD_BEFORE, PAD_AFTER)
TONE_HZ = 1000.0
BELOW_PEAK_DB = 6.0
MUTED_SUFFIX = "-bleep"                       # a clipitem id ending this is a piece that was silenced by this tool


class BleepError(Exception):
    pass


# ---------------------------------------------------------------- the word list
def load_patterns(path: Path | None = None, extra: list[str] | None = None) -> list[re.Pattern]:
    words = [ln.strip().lower() for ln in (path or LIST_FILE).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    words += [w.strip().lower() for w in (extra or []) if w.strip()]
    words += [w for w in (_model().get("words", {}).get("add", {}) if path is None else {}) if w.isalpha()]        # words Ryan added bleeps over at least twice (never written to profanity.txt)
    pats = []
    for w in words:
        stem = re.escape(w.rstrip("*"))
        pats.append(re.compile(rf"^{stem}{'[a-z]*' if w.endswith('*') else ''}$"))
    return pats


def norm(token: str) -> str:
    return re.sub(r"[^a-z*']", "", token.lower()).replace("'", "")


def is_profane(token: str, pats: list[re.Pattern]) -> bool:
    t = norm(token)
    if not t:
        return False
    if re.fullmatch(r"[a-z]\*{2,}[a-z]*", t):                  # the model already starred it (f***, s***)
        return True
    return any(p.match(t) for p in pats)


def censor(text: str, pats: list[re.Pattern]) -> str:
    """'Fucking,' -> 'F*****,': first letter kept, the rest starred, punctuation kept. Other words are untouched."""
    def one(m: re.Match) -> str:
        w = m.group(0)
        return w[0] + "*" * (len(w) - 1) if is_profane(w, pats) else w
    return re.sub(r"[A-Za-z*']+", one, text)


# ---------------------------------------------------------------- the speech of a cut
def cut_audio(cut: timeline.Cut, sr: int = SR) -> np.ndarray:
    """Mono mix of the cut's ENABLED audio clips placed where the XML puts them (what the preview plays)."""
    n = int(round(cut.zone_end * sr))
    mix = np.zeros(n)
    for a in cut.audio:
        dur = a.tl_end - a.tl_start
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a.src_in:.4f}", "-t", f"{dur:.4f}", "-i", a.src_path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                           capture_output=True)
        x = np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)
        i = int(round(a.tl_start * sr))
        x = x[:max(0, n - i)]
        mix[i:i + len(x)] += x
    return mix


def write_wav(path: Path, x: np.ndarray, sr: int, channels: int = 1) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm[:, None], 2, axis=1).reshape(-1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def transcribe_detail(wav: Path, model: str = "small") -> list[tuple[str, float, float, float]]:
    """(word, start, end, probability) for every spoken word. English is set, not guessed; `small` hears profanity better than `base`."""
    try:
        import whisper
    except ImportError as e:
        raise BleepError(f"Whisper is not installed, so the words cannot be found ({e})")
    r = whisper.load_model(model).transcribe(str(wav), language="en", word_timestamps=True, condition_on_previous_text=False, fp16=False)
    return [(w["word"].strip(), float(w["start"]), float(w["end"]), float(w.get("probability", 1.0))) for seg in r["segments"] for w in seg.get("words", [])]


def transcribe_timed(wav: Path, model: str = "small") -> list[tuple[str, float, float]]:
    return [(w, s, e) for w, s, e, _p in transcribe_detail(wav, model)]


def find_hits(words: list[tuple[str, float, float]], pats: list[re.Pattern]) -> list[dict]:
    return [{"word": w, "start": round(s, 3), "end": round(e, 3), "source": "transcript"} for w, s, e in words if is_profane(w, pats)]


SUSPECT_MIN_SEC, SUSPECT_MAX_SEC, BURST_DB, BURST_MIN_SEC, FRAME_SEC = 0.40, 1.0, 6.0, 0.10, 0.05
UNSURE_P, UNSURE_MIN_SEC, DISAGREE_MIN_SEC, MIN_SIGNALS, LIKELY_SIGNALS = 0.35, 0.20, 0.20, 2, 3


def find_suspects(words: list[tuple[str, float, float]], speech: np.ndarray, sr: int = SR, details: list | None = None,
                  other: list | None = None, min_signals: int = MIN_SIGNALS, require_doubt: bool = True) -> list[dict]:
    """Whisper drops or softens profanity, so a curse word can be spoken and never written. A word is a SUSPECT when at least `min_signals`
    independent signals agree that something odd was said there:
      stretched        a short word spread over 0.4-1.0 s (the decoder stretched it over something it did not write)
      burst            a loud burst inside it, 6 dB over the speech around it for 0.1 s or more
      unsure           Whisper's own probability for the word is under 0.35 (needs `details`)
      models disagree  a second, smaller model heard a different word at the same time (needs `other`)
    Two signals make it 'possible', three or more 'likely'. When the transcript's own uncertainty is available (`details` or `other`), at least one
    of `unsure` or `models disagree` is REQUIRED: a hidden word leaves a transcription problem, while a loud or long word that Whisper is sure of
    and both models agree on is just emphasis (9 of the 14 flags on Ryan's cut were exactly that). Nothing is bleeped from a suspect unless a note
    confirms it. The span is the burst if there is one, else the word."""
    n = int(FRAME_SEC * sr)
    lo_sec = float(_model().get("detection", {}).get("suspect_min_sec") or SUSPECT_MIN_SEC)          # learned from bleeps Ryan added where the tool found none
    frames = 20 * np.log10(np.maximum(np.sqrt((speech[:len(speech) // n * n].reshape(-1, n) ** 2).mean(axis=1)), 1e-6))
    prob = {(round(s, 2), round(e, 2)): p for _w, s, e, p in (details or [])}
    other_words = [(norm(w), s, e) for w, s, e in (other or [])]
    out = []
    for w, s, e in words:
        d = e - s
        sig, burst = [], None
        if lo_sec <= d <= SUSPECT_MAX_SEC and len(norm(w)) <= 6:
            sig.append("stretched")
            i0, i1 = int(s / FRAME_SEC), int(np.ceil(e / FRAME_SEC))
            near = np.r_[frames[max(0, i0 - 30):max(0, i0)], frames[i1:i1 + 30]]
            near = near[near > -60]
            if len(near) >= 8:
                base = float(np.median(near))
                hot = [i for i in range(i0, min(i1, len(frames))) if frames[i] >= base + BURST_DB]
                if len(hot) * FRAME_SEC >= BURST_MIN_SEC:
                    sig.append("burst")
                    burst = (hot[0] * FRAME_SEC, (hot[-1] + 1) * FRAME_SEC, float(frames[hot].max() - base))
        p = prob.get((round(s, 2), round(e, 2)))
        if p is not None and p < UNSURE_P and d >= UNSURE_MIN_SEC:
            sig.append("unsure")
        if other is not None and d >= DISAGREE_MIN_SEC and norm(w) not in [t for t, a, b in other_words if a < e + 0.15 and b > s - 0.15]:
            sig.append("models disagree")
        transcript_doubt = "unsure" in sig or "models disagree" in sig
        if len(sig) >= min_signals and (transcript_doubt or not require_doubt or (details is None and other is None)):
            out.append({"word": w, "word_start": round(s, 3), "word_end": round(e, 3),
                        "start": round(burst[0], 3) if burst else round(s, 3), "end": round(burst[1], 3) if burst else round(e, 3),
                        "burst_db_over_speech": round(burst[2], 1) if burst else 0.0, "signals": sig, "score": len(sig),
                        "tier": "likely" if len(sig) >= LIKELY_SIGNALS else "possible", "probability": None if p is None else round(p, 2)})
    return out


REVEAL_CONTEXT_SEC = 2.0
REVEAL_PARTS = (0.85, 0.6, -0.6)                               # mask the first 85% of the stretch, the first 60%, the last 60% (negative = from the end)


def reveal(speech: np.ndarray, regions: list[tuple[float, float]], words_of, pats: list[re.Pattern], work: Path, sr: int = SR) -> list[dict]:
    """Find curse words Whisper folds into other words. Whisper can stretch a neighbouring word over a curse word it will not write; silence
    part of the loud stretch and it stops hiding the rest. Each region is tried ON ITS OWN (silencing several at once can leave nothing to
    hear) with a few partial masks, on a short window around it, and any listed word that now appears is kept. The span returned is the word's own, as
    Whisper timed it on the partly silenced audio.
    On Ryan's cut: silencing 28.9-29.2 s turned "what just happened" into "what the fuck just happened" (fuck at 29.38-29.58 s); silencing 28.9-29.4
    as well hid the word completely, which is why the parts are tried separately."""
    out: list[dict] = []
    fade = int(0.01 * sr)
    for ri, (a, b) in enumerate(regions):
        t0 = max(0.0, a - REVEAL_CONTEXT_SEC)
        i0, i1 = int(t0 * sr), min(len(speech), int((b + REVEAL_CONTEXT_SEC) * sr))
        for part in REVEAL_PARTS:
            ma, mb = (a, a + part * (b - a)) if part > 0 else (b + part * (b - a), b)
            x = speech[i0:i1].copy()
            lo, hi = int(ma * sr) - i0, int(mb * sr) - i0
            x[lo:hi] = 0.0
            if lo >= fade:
                x[lo - fade:lo] *= np.linspace(1, 0, fade)
            if hi + fade <= len(x):
                x[hi:hi + fade] *= np.linspace(0, 1, fade)
            wav = work / f"masked_{ri}.wav"
            write_wav(wav, x, sr)
            for h in find_hits(words_of(wav), pats):
                hs, he = h["start"] + t0, h["end"] + t0
                if any(abs(hs - o["word_start"]) < 0.25 for o in out):
                    continue
                # the span is the WORD's own (Whisper's timing, good to about 0.1 s); joining it to the silenced stretch dragged the start 0.5 s early on the real cut
                out.append({"word": h["word"], "start": round(hs, 3), "end": round(he, 3), "word_start": round(hs, 3), "word_end": round(he, 3),
                            "revealed": True, "masked": [[round(ma, 3), round(mb, 3)]]})
    return out


def snap_voiced(speech: np.ndarray, t: float, sr: int = SR, reach: float = 0.4, half: float = 0.3) -> tuple[float, float]:
    """The spoken stretch at a clicked moment: the run of voiced 20 ms frames (12 dB over the local quiet level) nearest `t`, trimmed to `half`
    seconds either side of it. When nothing is voiced there it is simply t +- half. A person clicked on the word, so the answer is theirs."""
    n = int(0.02 * sr)
    fr = 20 * np.log10(np.maximum(np.sqrt((speech[:len(speech) // n * n].reshape(-1, n) ** 2).mean(axis=1)), 1e-6))
    i = min(max(int(t / 0.02), 0), len(fr) - 1)
    lo, hi = max(0, i - 100), min(len(fr), i + 100)
    thr = float(np.percentile(fr[lo:hi], 15)) + 12.0
    cand = [j for j in range(max(0, i - int(reach / 0.02)), min(len(fr), i + int(reach / 0.02) + 1)) if fr[j] >= thr]
    if not cand:
        return round(max(0.0, t - half), 3), round(t + half, 3)
    j = min(cand, key=lambda k: abs(k - i))
    a = b = j
    while a > 0 and fr[a - 1] >= thr - 3:
        a -= 1
    while b < len(fr) - 1 and fr[b + 1] >= thr - 3:
        b += 1
    return round(max(a * 0.02, t - half), 3), round(min((b + 1) * 0.02, t + half), 3)


def spans_of(hits: list[dict], duration: float) -> list[tuple[float, float]]:
    def pads(h):                                                   # padding learned from how Ryan trims each kind of hit (defaults until he has)
        return learned_pads(h.get("source", "transcript"))
    raw = sorted((max(0.0, h["start"] - pads(h)[0]), min(duration, h["end"] + pads(h)[1])) for h in hits)
    out: list[list[float]] = []
    for a, b in raw:
        if out and a <= out[-1][1] + 0.05:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out if b - a > 0.05]


# ---------------------------------------------------------------- silencing the speech in the XML
def _spans_frames(spans: list[tuple[float, float]], fps: float) -> list[tuple[int, int]]:
    return [(round(a * fps), round(b * fps)) for a, b in spans if round(b * fps) > round(a * fps)]


def mute_spans(xml_in: Path, xml_out: Path, spans: list[tuple[float, float]]) -> dict:
    """Split every enabled speech clip at the spans and disable the pieces inside them. No ripple: the cut keeps its length."""
    cut = timeline.load_cut(xml_in)
    fps = cut.fps
    zone_f = round(cut.zone_end * fps)
    frames = _spans_frames(spans, fps)
    tree = ET.parse(xml_in)
    root = tree.getroot()
    seq = timeline._seq_for_cut(root)
    used = {el.get("id") for el in root.iter("clipitem") if el.get("id")}
    muted_frames = 0
    pieces_made = 0
    for track in seq.findall("media/audio/track"):
        new_children: list[tuple[ET.Element, list[ET.Element]]] = []
        for c in track.findall("clipitem"):
            if (c.findtext("enabled") or "TRUE").strip().upper() != "TRUE" or (c.find("file").get("id") or "").startswith(timeline.LAYER_AUDIO_PREFIX):
                continue
            start, end = int(c.findtext("start")), int(c.findtext("end"))
            if start >= zone_f:
                continue
            hit = [(max(a, start), min(b, end)) for a, b in frames if a < end and b > start]
            if not hit:
                continue
            in0, out0 = int(c.findtext("in")), int(c.findtext("out"))
            k = (out0 - in0) / (end - start) if end > start else 1.0
            dur_text = c.findtext("duration")
            dur_is_len = dur_text is not None and int(dur_text) == end - start
            orig = copy.deepcopy(c)
            cuts, cur = [], start
            for a, b in hit:
                if a > cur:
                    cuts.append((cur, a, False))
                cuts.append((a, b, True))
                cur = b
            if cur < end:
                cuts.append((cur, end, False))
            els = []
            for n, (a, b, mute) in enumerate(cuts):
                if n == 0:
                    el = c
                else:
                    el = copy.deepcopy(orig)
                    f = el.find("file")
                    if f is not None:
                        for ch in list(f):
                            f.remove(ch)
                    base, i = orig.get("id"), n
                    cand = f"{base}-b{i}{MUTED_SUFFIX if mute else ''}"
                    while cand in used:
                        i += 1
                        cand = f"{base}-b{i}{MUTED_SUFFIX if mute else ''}"
                    el.set("id", cand)
                    used.add(cand)
                if n == 0 and mute:                                 # the muted piece is the first one: the original element keeps its id, so mark by renaming
                    el.set("id", orig.get("id") + MUTED_SUFFIX)
                in_new = in0 + round((a - start) * k)
                for tag, val in (("start", a), ("end", b), ("in", in_new), ("out", in_new + round((b - a) * k))):
                    el.find(tag).text = str(int(val))
                if dur_is_len and el.find("duration") is not None:
                    el.find("duration").text = str(b - a)
                if mute:
                    el.find("enabled").text = "FALSE"
                    muted_frames += b - a
                els.append(el)
            pieces_made += len(els) - 1
            new_children.append((c, els))
        for c, els in new_children:
            pos = list(track).index(c)
            track.remove(c)
            for i, el in enumerate(els):
                track.insert(pos + i, el)
    ET.indent(root, space="\t")
    xml_out.write_text(timeline_header(xml_in) + ET.tostring(root, encoding="unicode") + "\n")
    return {"muted_frames": muted_frames, "extra_pieces": pieces_made, "fps": fps}


def timeline_header(xml_in: Path) -> str:
    first = xml_in.read_text(errors="ignore").split("<xmeml", 1)[0]
    return first if first.strip() else '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


def unmute(xml_in: Path, xml_out: Path) -> int:
    """Undo a previous bleep: every piece this tool silenced is enabled again (the splits stay; the cut is identical)."""
    tree = ET.parse(xml_in)
    n = 0
    for c in tree.getroot().iter("clipitem"):
        if (c.get("id") or "").endswith(MUTED_SUFFIX) and c.find("enabled") is not None:
            c.find("enabled").text = "TRUE"
            c.set("id", c.get("id")[:-len(MUTED_SUFFIX)])
            n += 1
    if n:
        ET.indent(tree.getroot(), space="\t")
        xml_out.write_text(timeline_header(xml_in) + ET.tostring(tree.getroot(), encoding="unicode") + "\n")
    return n


def strip_previous(xml_in: Path, xml_out: Path) -> tuple[int, int]:
    """Undo a previous bleep completely: silenced pieces enabled again and the bleep layers removed. Returns (pieces, layers)."""
    tree = ET.parse(xml_in)
    root = tree.getroot()
    seq = timeline._seq_for_cut(root)
    pieces = 0
    for c in root.iter("clipitem"):
        if (c.get("id") or "").endswith(MUTED_SUFFIX) and c.find("enabled") is not None:
            c.find("enabled").text = "TRUE"
            c.set("id", c.get("id")[:-len(MUTED_SUFFIX)])        # no longer a silenced piece, so it no longer looks like one
            pieces += 1
    layers = 0
    for track in seq.findall("media/audio/track"):
        items = track.findall("clipitem")
        if items and all((c.findtext("name") or "").startswith("bleep_") and (c.find("file").get("id") or "").startswith(timeline.LAYER_AUDIO_PREFIX) for c in items):
            seq.find("media/audio").remove(track)
            layers += 1
    if pieces or layers:
        ET.indent(root, space="\t")
        xml_out.write_text(timeline_header(xml_in) + ET.tostring(root, encoding="unicode") + "\n")
    return pieces, layers


def muted_spans_in(xml: Path) -> list[tuple[float, float]]:
    cut_fps = timeline.load_cut(xml).fps
    root = ET.parse(xml).getroot()
    out = set()
    for c in root.iter("clipitem"):
        if (c.get("id") or "").endswith(MUTED_SUFFIX):
            out.add((int(c.findtext("start")), int(c.findtext("end"))))
    merged: list[list[float]] = []
    for a, b in sorted((a / cut_fps, b / cut_fps) for a, b in out):      # a span that crosses a seam between two speech clips is silenced as two touching pieces: one span
        if merged and a <= merged[-1][1] + 1.5 / cut_fps:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ---------------------------------------------------------------- the bleeps
def db(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt((x ** 2).mean())) if len(x) else 0.0, 1e-9))


def lowpass(x: np.ndarray, hz: float, sr: int = SR) -> np.ndarray:
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1 / sr) > hz] = 0
    return np.fft.irfft(spec, len(x))


BODY_HZ, OUTSIDE_BAR_DB = 800.0, -12.0


AUDIBLE_DB, AUDIBLE_SHARE = -55.0, 0.5


def audible_share(x: np.ndarray, a: float, b: float, sr: int = SR) -> float:
    """The fraction of the stretch a-b (seconds) that has sound in it: 20 ms frames above -55 dBFS."""
    n = int(0.02 * sr)
    seg = x[int(a * sr):int(b * sr)]
    k = len(seg) // n
    if k == 0:
        return 0.0
    rms = 20 * np.log10(np.maximum(np.sqrt((seg[:k * n].reshape(k, n) ** 2).mean(axis=1)), 1e-9))
    return float((rms > AUDIBLE_DB).mean())


def make_bleeps(spans: list[tuple[float, float]], speech_peak: float, out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    peak = speech_peak * 10 ** (-BELOW_PEAK_DB / 20)
    clips = []
    for i, (a, b) in enumerate(spans, start=1):
        n = int(round((b - a) * OUT_SR))
        t = np.arange(n) / OUT_SR
        x = np.sin(2 * np.pi * TONE_HZ * t) * peak
        fade = int(0.005 * OUT_SR)
        x[:fade] *= np.linspace(0, 1, fade)
        x[-fade:] *= np.linspace(1, 0, fade)
        name = f"bleep_{i}.wav"
        write_wav(out / name, x, OUT_SR, 2)
        clips.append({"kind": "bleep", "name": name, "path": str((out / name).resolve()), "start_sec": a, "duration_sec": round(b - a, 3)})
    (out / "placement.json").write_text(json.dumps({"kind": "audio", "clips": clips}, indent=2))
    return clips


# ---------------------------------------------------------------- verifying from the files
def verify(before: Path, after: Path, spans: list[tuple[float, float]], clips: list[dict], speech_peak: float,
           words_of=None, pats: list[re.Pattern] | None = None, work: Path | None = None) -> list[tuple[str, bool | None, str]]:
    rows: list[tuple[str, bool | None, str]] = []
    a0, a1 = cut_audio(timeline.load_cut(before)), cut_audio(timeline.load_cut(after))
    n = min(len(a0), len(a1))
    rows.append(("SAME-LENGTH", abs(len(a0) - len(a1)) <= SR // 50, f"speech {len(a0) / SR:.2f}s before, {len(a1) / SR:.2f}s after"))
    sl = lambda x, s, e: x[int(s * SR):int(e * SR)]          # noqa: E731
    loud = [db(sl(a0, s, e)) for s, e in spans]
    quiet = [db(sl(a1, s + 0.01, e - 0.01)) for s, e in spans]
    rows.append(("SPANS-SILENT", bool(all(q < -60 for q in quiet) and all(ld > q + 20 for ld, q in zip(loud, quiet))),
                 "; ".join(f"{s:.2f}-{e:.2f}s {ld:.0f} dBFS -> {q:.0f} dBFS" for (s, e), ld, q in zip(spans, loud, quiet))))
    worst = -200.0
    for s, e in spans:
        for lo, hi in ((max(0.0, s - 0.35), max(0.0, s - 0.02)), (min(n / SR, e + 0.02), min(n / SR, e + 0.35))):
            if hi - lo > 0.05:
                l0, h0 = int(lo * SR), int(hi * SR)
                # Compare the speech body (under 800 Hz). The check places each clip on a 16 kHz grid while a piece starts on a video frame, so in the
                # MEASUREMENT it can sit a whole sample (allowed, +-2) or a fraction of one off, which only shows in the highs. The XML is frame-exact.
                # Wrong content, silence or a level change still read near 0 dB or worse.
                ref_sig = lowpass(a0[l0 - 4:h0 + 4], BODY_HZ)[4:-4]
                ref = max(db(ref_sig), -90)
                best = min(db(lowpass(a1[l0 + g - 4:h0 + g + 4], BODY_HZ)[4:-4] - ref_sig) for g in range(-2, 3))
                worst = max(worst, best - ref)
    rows.append(("OUTSIDE-UNCHANGED", bool(worst < OUTSIDE_BAR_DB), f"the speech body just outside each span matches the original (difference at most {worst:.0f} dB relative to it, bar {OUTSIDE_BAR_DB:.0f}; unchanged reads -12 or lower, damage reads about 0 or more)"))
    cb, ca = timeline.load_cut(before), timeline.load_cut(after)
    lost = sum(x.tl_end - x.tl_start for x in cb.audio) - sum(x.tl_end - x.tl_start for x in ca.audio)
    want = sum(max(0.0, min(x.tl_end, e) - max(x.tl_start, s)) for x in cb.audio for s, e in spans)       # each span, over every speech clip it touches
    rows.append(("ONLY-THE-SPANS-LOST", bool(lost > 0 and abs(lost - want) < 0.05 * len(spans) + 0.05),
                 f"{lost:.2f}s of speech audio disabled; the spans cover {want:.2f}s of the clips they touch"))
    for c in clips:
        x = wave.open(c["path"])
        raw = np.frombuffer(x.readframes(x.getnframes()), dtype="<i2").reshape(-1, x.getnchannels())[:, 0].astype(np.float64) / 32767
        x.close()
        core = raw[int(0.02 * OUT_SR):-int(0.02 * OUT_SR)]
        f = np.fft.rfftfreq(len(core), 1 / OUT_SR)[np.argmax(np.abs(np.fft.rfft(core)))]
        pk_db = 20 * np.log10(max(np.abs(raw).max(), 1e-9))
        want_db = 20 * np.log10(speech_peak) - BELOW_PEAK_DB
        ok = bool(abs(f - TONE_HZ) < 25 and abs(pk_db - want_db) < 1.5 and abs(len(raw) / OUT_SR - c["duration_sec"]) < 0.01)
        rows.append((f"BLEEP-IS-A-TONE ({c['name']})", ok, f"{f:.0f} Hz, peak {pk_db:.1f} dBFS (wanted {want_db:.1f}), {len(raw) / OUT_SR:.2f}s at {c['start_sec']:.2f}s"))
    if words_of is not None and pats is not None and work is not None:
        wav = work / "after_speech.wav"
        write_wav(wav, a1, SR)
        heard = find_hits(words_of(wav), pats)
        # Whisper writes words over digital silence (its language model fills the gap: "what the fuck" into 0.7 s of nothing). A word counts as left
        # behind only if at least half of its time has sound in the bleeped audio; one written over silence is reported, not failed.
        left = [h for h in heard if audible_share(a1, h["start"], h["end"]) >= AUDIBLE_SHARE]
        phantom = [h for h in heard if h not in left]
        note = ("; written over silence, so not counted: " + ", ".join(f"{h['word']} at {h['start']:.2f}-{h['end']:.2f}s ({audible_share(a1, h['start'], h['end']):.0%} audible)" for h in phantom)) if phantom else ""
        rows.append(("NO-LISTED-WORD-LEFT", not left, ("a second transcription of the result finds no listed word over sound" + note) if not left else
                     "still heard after bleeping: " + ", ".join(f"{h['word']} at {h['start']:.2f}s ({audible_share(a1, h['start'], h['end']):.0%} audible)" for h in left) + note))
    return rows


# ---------------------------------------------------------------- the whole job
def bleep(xml_in: Path, out: Path, words_of=transcribe_timed, pats: list[re.Pattern] | None = None, check_transcript: bool = True,
          suspect_windows: list[tuple[float, float]] | None = None, requests: list[dict] | None = None, detail_of=None, other_of=None,
          min_signals: int = MIN_SIGNALS, reveal_with=None, detect: bool = True) -> dict:
    """Returns {hits, spans, xml, clips, rows}. If nothing is found, xml is None and nothing is written but bleep.json."""
    import place_audio as pa
    pats = pats or load_patterns()
    out.mkdir(parents=True, exist_ok=True)
    source = xml_in
    pieces, layers = strip_previous(xml_in, out / "_unmuted.xml")      # already bleeped: start from the unmuted speech, so the result is the same every time
    if pieces or layers:
        source = out / "_unmuted.xml"
    cut = timeline.load_cut(source)
    speech = cut_audio(cut)
    wav = out / "speech.wav"
    write_wav(wav, speech, SR)
    if detect:
        detail = detail_of(wav) if detail_of else None
        words = [(w, s, e) for w, s, e, _p in detail] if detail else words_of(wav)
        other = other_of(wav) if other_of else None
    else:                                                         # the spans are given (Ryan's edits): nothing is scanned or transcribed
        detail, words, other, reveal_with = None, [], None, None
    hits = find_hits(words, pats)
    skip = _model().get("words", {}).get("skip", {})                  # words Ryan removed the bleep from at least twice and never kept: reported, not bleeped
    skipped = [h for h in hits if norm(h["word"]) in skip]
    hits = [h for h in hits if norm(h["word"]) not in skip]
    suspects = find_suspects(words, speech, details=detail, other=other, min_signals=min_signals)
    revealed = []
    loud = [(x_["start"], x_["end"]) for x_ in find_suspects(words, speech, min_signals=2, require_doubt=False) if "burst" in x_.get("signals", [])] if detect else []
    if reveal_with is not None:                                   # loud stretches Whisper may be hiding a curse word behind: silence them and listen again
        revealed = reveal(speech, loud, reveal_with, pats, out)
        hits += [{"word": f"{h['word']} (was hidden; revealed by silencing {h['masked'][0][0]:.2f}-{h['masked'][0][1]:.2f}s)" if h["masked"] else f"{h['word']} (revealed)",
                  "start": h["start"], "end": h["end"], "source": "revealed"} for h in revealed if not any(abs(h["word_start"] - q["start"]) < 0.3 for q in hits)]
    for sus in suspects:
        sus["bleeped"] = False
    reqs = list(requests or []) + [{"kind": "window", "start": a_, "end": b_} for a_, b_ in (suspect_windows or [])]
    for r in reqs:
        if r["kind"] == "span":                                   # the page's Suspects box was confirmed by a note: exactly that span
            hits.append({"word": f"(confirmed by a note: {r.get('label', 'suspect')})", "start": r["start"], "end": r["end"]})
            for x in suspects:
                x["bleeped"] = x["bleeped"] or (x["start"] < r["end"] and x["end"] > r["start"])
        elif r["kind"] == "at":                                   # a note left by clicking the clip at the word: the spoken stretch at that spot
            a_, b_ = snap_voiced(speech, r["t"])
            hits.append({"word": f"(the spot clicked at {r['t']:.2f}s)", "start": a_, "end": b_})
            for x in suspects:
                x["bleeped"] = x["bleeped"] or (x["start"] < b_ and x["end"] > a_)
        else:                                                     # a note pointed at a stretch: its single strongest suspect is bleeped, the rest are only reported
            inside = [x for x in suspects if r["start"] <= x["start"] and x["end"] <= r["end"]]
            if inside:
                top = max(inside, key=lambda x: (x.get("score", 0), x.get("burst_db_over_speech", 0.0)))
                top["bleeped"] = True
                hits.append({"word": f"(not transcribed; heard as '{top['word']}')", "start": top["start"], "end": top["end"], "source": "suspect"})
    exact = [(float(r["start"]), float(r["end"])) for r in reqs if r["kind"] == "exact"]
    # a person who gives times is correcting the tool: where the tool's own hits overlap those times (within 0.15 s) the person's times win, unmerged
    overridden = [h for h in hits if any(h["start"] < b_ + 0.15 and h["end"] > a_ - 0.15 for a_, b_ in exact)]
    hits = [h for h in hits if h not in overridden]
    auto_hits = list(hits)
    for a_, b_ in exact:
        hits.append({"word": f"(the times you gave: {a_:.2f}-{b_:.2f}s)", "start": a_, "end": b_})
    spans = spans_of(auto_hits, cut.zone_end)
    for a_, b_ in exact:                                          # times a person gave are used as given: no padding
        spans.append((round(a_, 3), round(min(b_, cut.zone_end), 3)))
    merged: list[list[float]] = []
    for a_, b_ in sorted(spans):
        if merged and a_ <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b_)
        else:
            merged.append([a_, b_])
    spans = [(a_, b_) for a_, b_ in merged if b_ - a_ > 0.05]
    res = {"origin": "automatic" if detect else "edits", "skipped": skipped, "hits": hits, "overridden": overridden, "revealed": revealed, "suspects": suspects, "spans": spans,
           "words_heard": len(words), "loud": [[round(a_, 3), round(b_, 3)] for a_, b_ in loud],          # what a later edit is compared with, and what learn.py studies
           "words": [[w, round(s_, 3), round(e_, 3), (round(p_, 2) if p_ is not None else None)] for (w, s_, e_), p_ in zip(words, [t[3] for t in detail] if detail else [None] * len(words))],
           "xml": None, "clips": [], "rows": []}
    (out / "bleep.json").write_text(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=2))
    if not spans:
        return res
    speech_peak = float(np.percentile(np.abs(speech), 99.9))
    muted = out / f"_muted_{xml_in.name}"
    mute_spans(source, muted, spans)
    clips = make_bleeps(spans, speech_peak, out / "bleeps")
    final = out / xml_in.name
    info = pa.place(muted, final, out / "bleeps")
    rows = verify(source, final, spans, clips, speech_peak, words_of if check_transcript else None, pats, out)
    rows += [(n, ok, d) for n, ok, d in pa.verify_placed(muted, final, info)]
    muted.unlink(missing_ok=True)
    res.update(xml=str(final), clips=clips, rows=rows, speech_peak=speech_peak)
    (out / "bleep.json").write_text(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=2, default=str))
    return res


TIMES = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:-|\u2013|to)\s*(\d{1,3}(?:\.\d+)?)\s*(?:s\b|sec|seconds)?", re.I)


def requests_from_notes(ops: list[dict], notes: list[dict], around: float = 1.0) -> list[dict]:
    """What each `bleep_word` note asks for, most exact first: a confirmed Suspects box (that span); a click on a clip box at the word (the spoken
    stretch at the click); a clip or element (its strongest suspect); else about a second around the note's moment."""
    out = []
    for o in ops:
        if o.get("op") != "bleep_word":
            continue
        n = notes[o["note"] - 1]
        tg = n.get("target") or {}
        said = TIMES.search(n.get("text", ""))
        if said and float(said.group(1)) < float(said.group(2)) <= float(said.group(1)) + 3.0:          # "bleep 28.9-29.2": the note states the times, so they are used exactly
            out.append({"kind": "exact", "start": float(said.group(1)), "end": float(said.group(2))})
        elif tg.get("lane") == "Suspects":
            out.append({"kind": "span", "start": float(tg["start"]), "end": float(tg["end"]), "label": str(tg.get("label", ""))[:60]})
        elif tg.get("clicked"):
            out.append({"kind": "at", "t": float(n["timeline_sec"])})
        elif tg.get("lane") in ("Clips", "Captions", "Cuts") and tg.get("end", 0) > tg.get("start", 0):
            out.append({"kind": "window", "start": float(tg["start"]), "end": float(tg["end"])})
        else:
            out.append({"kind": "window", "start": max(0.0, n["timeline_sec"] - around), "end": n["timeline_sec"] + around})
    exact = [r for r in out if r["kind"] in ("span", "at", "exact")]
    def holds(w, r):                                              # a vague request that contains a more exact one is the same ask, made loosely: the exact one wins
        a, b = (r["start"], r["end"]) if r["kind"] in ("span", "exact") else (r["t"], r["t"])
        return w["start"] <= a and b <= w["end"]
    return [r for r in out if r["kind"] != "window" or not any(holds(r, e) for e in exact)]


def windows_from_notes(ops: list[dict], notes: list[dict], around: float = 1.0) -> list[tuple[float, float]]:
    """The stretch-style requests only (kept for callers that want windows)."""
    return [(r["start"], r["end"]) for r in requests_from_notes(ops, notes, around) if r["kind"] == "window"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--list", type=Path, help="a word list to use instead of profanity.txt")
    ap.add_argument("--words", help="extra words to bleep for this run, comma separated")
    ap.add_argument("--at", action="append", default=[], metavar="START,END", help="bleep exactly these timeline seconds, unpadded; repeatable")
    ap.add_argument("--no-reveal", action="store_true", help="skip the silence-and-listen-again pass that finds curse words Whisper hides inside other words")
    ap.add_argument("--min-signals", type=int, default=MIN_SIGNALS, help="how many independent signals must agree before a stretch is called a suspect (2 = possible, 3 = likely)")
    ap.add_argument("--suspects-in", action="append", default=[], metavar="START,END",
                    help="bleep a suspected untranscribed curse word found inside this stretch of the timeline (seconds); repeatable. Without it suspects are only reported")
    a = ap.parse_args()
    try:
        pats = load_patterns(a.list, (a.words or "").split(","))
        windows = [tuple(float(x) for x in w.split(",")) for w in a.suspects_in]
        r = bleep(a.xml, a.out, pats=pats, suspect_windows=windows, requests=[{"kind": "exact", "start": float(w.split(",")[0]), "end": float(w.split(",")[1])} for w in a.at], detail_of=transcribe_detail, other_of=lambda wav: transcribe_timed(wav, "base"), min_signals=a.min_signals,
                  reveal_with=None if a.no_reveal else transcribe_timed)
    except (BleepError, timeline.TimelineError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(f"{r['words_heard']} words heard; {len(r['hits'])} listed: " + (", ".join(f"{h['word']} at {h['start']:.2f}s" for h in r["hits"]) or "none"))
    for h in r.get("skipped", []):
        print(f"  skipped '{h['word']}' at {h['start']:.2f}s: you removed that bleep before (undo with: learn.py forget {norm(h['word'])})")
    for sus in r["suspects"]:
        print(f"  suspect ({sus['tier']}): '{sus['word']}' at {sus['word_start']:.2f}-{sus['word_end']:.2f}s, signals: {', '.join(sus['signals'])}. "
              + ("BLEEPED (a note pointed here)." if sus["bleeped"] else "Not bleeped; check by ear."))
    if not r["spans"]:
        print("nothing to bleep; no XML written")
        return 0
    bad = 0
    for name, ok, detail in r["rows"]:
        bad += ok is False
        print(f"  [{'SKIP' if ok is None else 'PASS' if ok else 'FAIL'}] {name}  {detail}")
    print(f"\n{'FAILED: do not use this XML' if bad else 'all checks passed'}: {r['xml']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
