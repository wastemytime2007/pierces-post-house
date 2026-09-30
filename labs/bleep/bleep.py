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
TONE_HZ = 1000.0
BELOW_PEAK_DB = 6.0
MUTED_SUFFIX = "-bleep"                       # a clipitem id ending this is a piece that was silenced by this tool


class BleepError(Exception):
    pass


# ---------------------------------------------------------------- the word list
def load_patterns(path: Path | None = None, extra: list[str] | None = None) -> list[re.Pattern]:
    words = [ln.strip().lower() for ln in (path or LIST_FILE).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
    words += [w.strip().lower() for w in (extra or []) if w.strip()]
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


def transcribe_timed(wav: Path, model: str = "small") -> list[tuple[str, float, float]]:
    """(word, start, end) for every spoken word. English is set, not guessed; `small` hears profanity better than `base`."""
    try:
        import whisper
    except ImportError as e:
        raise BleepError(f"Whisper is not installed, so the words cannot be found ({e})")
    r = whisper.load_model(model).transcribe(str(wav), language="en", word_timestamps=True, condition_on_previous_text=False, fp16=False)
    return [(w["word"].strip(), float(w["start"]), float(w["end"])) for seg in r["segments"] for w in seg.get("words", [])]


def find_hits(words: list[tuple[str, float, float]], pats: list[re.Pattern]) -> list[dict]:
    return [{"word": w, "start": round(s, 3), "end": round(e, 3)} for w, s, e in words if is_profane(w, pats)]


SUSPECT_MIN_SEC, SUSPECT_MAX_SEC, BURST_DB, BURST_MIN_SEC, FRAME_SEC = 0.40, 1.0, 6.0, 0.10, 0.05


def find_suspects(words: list[tuple[str, float, float]], speech: np.ndarray, sr: int = SR) -> list[dict]:
    """Whisper drops or softens profanity, so a curse word can be spoken and never written. The tell: a short word stretched over
    0.4-1.0 s with a loud burst inside it (6 dB over the speech around it, for 0.1 s or more). Returns the burst's span. These are
    SUSPECTS, not hits: nothing is bleeped from them unless a note points at that stretch."""
    n = int(FRAME_SEC * sr)
    frames = 20 * np.log10(np.maximum(np.sqrt((speech[:len(speech) // n * n].reshape(-1, n) ** 2).mean(axis=1)), 1e-6))
    out = []
    for w, s, e in words:
        if not SUSPECT_MIN_SEC <= e - s <= SUSPECT_MAX_SEC or len(norm(w)) > 6:
            continue
        i0, i1 = int(s / FRAME_SEC), int(np.ceil(e / FRAME_SEC))
        near = np.r_[frames[max(0, i0 - 30):max(0, i0)], frames[i1:i1 + 30]]
        near = near[near > -60]
        if len(near) < 8:
            continue
        base = float(np.median(near))
        hot = [i for i in range(i0, min(i1, len(frames))) if frames[i] >= base + BURST_DB]
        if len(hot) * FRAME_SEC >= BURST_MIN_SEC:
            out.append({"word": w, "word_start": round(s, 3), "word_end": round(e, 3), "start": round(hot[0] * FRAME_SEC, 3), "end": round((hot[-1] + 1) * FRAME_SEC, 3),
                        "burst_db_over_speech": round(float(frames[hot].max() - base), 1)})
    return out


def spans_of(hits: list[dict], duration: float) -> list[tuple[float, float]]:
    raw = sorted((max(0.0, h["start"] - PAD_BEFORE), min(duration, h["end"] + PAD_AFTER)) for h in hits)
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
    return sorted((a / cut_fps, b / cut_fps) for a, b in out)


# ---------------------------------------------------------------- the bleeps
def db(x: np.ndarray) -> float:
    return 20 * np.log10(max(float(np.sqrt((x ** 2).mean())) if len(x) else 0.0, 1e-9))


def lowpass(x: np.ndarray, hz: float, sr: int = SR) -> np.ndarray:
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1 / sr) > hz] = 0
    return np.fft.irfft(spec, len(x))


BODY_HZ, OUTSIDE_BAR_DB = 800.0, -12.0


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
        left = find_hits(words_of(wav), pats)
        rows.append(("NO-LISTED-WORD-LEFT", not left, "a second transcription of the result finds no listed word" if not left else
                     "still heard after bleeping: " + ", ".join(f"{h['word']} at {h['start']:.2f}s" for h in left)))
    return rows


# ---------------------------------------------------------------- the whole job
def bleep(xml_in: Path, out: Path, words_of=transcribe_timed, pats: list[re.Pattern] | None = None, check_transcript: bool = True,
          suspect_windows: list[tuple[float, float]] | None = None) -> dict:
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
    words = words_of(wav)
    hits = find_hits(words, pats)
    suspects = find_suspects(words, speech)
    for sus in suspects:
        sus["bleeped"] = False
    for wa, wb in suspect_windows or []:                          # a note pointed at this stretch: its single strongest suspect is bleeped, the rest are only reported
        inside = [x for x in suspects if wa <= x["start"] and x["end"] <= wb]
        if inside:
            top = max(inside, key=lambda x: x["burst_db_over_speech"])
            top["bleeped"] = True
            hits.append({"word": f"(not transcribed; heard as '{top['word']}')", "start": top["start"], "end": top["end"]})
    spans = spans_of(hits, cut.zone_end)
    res = {"hits": hits, "suspects": suspects, "spans": spans, "words_heard": len(words), "xml": None, "clips": [], "rows": []}
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


def windows_from_notes(ops: list[dict], notes: list[dict], around: float = 1.0) -> list[tuple[float, float]]:
    """The stretches a `bleep_word` note points at: the clip or element it was left on, else `around` seconds either side of its moment."""
    out = []
    for o in ops:
        if o.get("op") != "bleep_word":
            continue
        n = notes[o["note"] - 1]
        tg = n.get("target")
        if tg and tg.get("lane") in ("Clips", "Captions", "Cuts") and tg.get("end", 0) > tg.get("start", 0):
            out.append((float(tg["start"]), float(tg["end"])))
        else:
            out.append((max(0.0, n["timeline_sec"] - around), n["timeline_sec"] + around))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xml", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--list", type=Path, help="a word list to use instead of profanity.txt")
    ap.add_argument("--words", help="extra words to bleep for this run, comma separated")
    ap.add_argument("--suspects-in", action="append", default=[], metavar="START,END",
                    help="bleep a suspected untranscribed curse word found inside this stretch of the timeline (seconds); repeatable. Without it suspects are only reported")
    a = ap.parse_args()
    try:
        pats = load_patterns(a.list, (a.words or "").split(","))
        windows = [tuple(float(x) for x in w.split(",")) for w in a.suspects_in]
        r = bleep(a.xml, a.out, pats=pats, suspect_windows=windows)
    except (BleepError, timeline.TimelineError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    print(f"{r['words_heard']} words heard; {len(r['hits'])} listed: " + (", ".join(f"{h['word']} at {h['start']:.2f}s" for h in r["hits"]) or "none"))
    for sus in r["suspects"]:
        print(f"  suspect: '{sus['word']}' at {sus['word_start']:.2f}-{sus['word_end']:.2f}s is unusually long with a burst {sus['burst_db_over_speech']} dB over the speech at "
              f"{sus['start']:.2f}-{sus['end']:.2f}s: a possible word Whisper did not write. " + ("BLEEPED (a note pointed here)." if sus["bleeped"] else "Not bleeped; check by ear."))
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
