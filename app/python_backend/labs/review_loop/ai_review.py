#!/usr/bin/env python3
"""AI review of a cut: the checks a careful editor makes, written as notes the review page can carry.

    PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/ai_review.py <export.xml> --out <review folder> [--no-story]

Writes <out>/ai_review.json: {"summary", "checks": [{name, ok, detail}], "notes": [...]}. The notes have the shape the review page saves (timeline_sec, clip, source, source_sec,
text, shapes) so they can be dropped onto the page and applied with the same button as notes written by hand.

What it looks at, and how each finding is grounded (nothing here is a guess dressed as a measurement):

  CUT-EDGES      every place the picture jumps to different footage, and the start and end of the cut. At each edge, on the audio you actually hear (the lav under it), the level in the 30 ms
                 inside the clip is compared with the clip's own loud speech: still loud means the cut lands in the middle of a sound. Whisper is also run on the source around the edge: a
                 word whose start or end falls on the other side of the edge is a cut-through word. Reported with the heard words and the time.
  SOURCE-AUDIO   which recorder the voice comes from, for how much of the cut; stretches with no voice under them; voice tracks that are silent.
  SYNC           the voice recorder against the camera's own audio, on a few clips, at the alignment the XML gives. A lag that is exactly 0.1% of the clip's place in the camera file is the
                 29.97-versus-30 question. For PreCut's exports it no longer appears (the loader reads the clip's own rate, as Premiere does: confirmed by Ryan 2026-10-07); if it does, the XML is read
                 at the wrong rate. Any other lag is reported as out of sync.
  STORY          the words of the finished cut, in the order they play, are read by the local Claude CLI (free, no API). It judges the hook, the point and the ending and lists problems with an
                 exact quote. A quote that is not in the transcript is thrown away (counted, never shown): the model's words are checked against the real ones.

Not covered: anything that needs looking at the picture (a cropped head, a wrong shot). The CLI route cannot pass frames, so there is no claim about the picture here.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import words as words_mod  # noqa: E402
from timeline import Cut, load_cut  # noqa: E402

SR = 16000
EDGE_SEC = 0.03            # the stretch just inside a clip that is measured at each edge
LOUD_DB = 12.0             # inside this many dB of the clip's own loud speech (p90 of 10 ms frames) = the sound has not stopped. Tight cuts the editor chose sit near -20 (measured on Reel 3); a chop sits near -7
FRIC_DB = 26.0             # a hiss (more than 25% of the energy above 3.5 kHz) this close to loud speech = a trailing s/f cut in half
WORD_NEAR_DB = 26.0        # a word Whisper times across the edge only counts when the audio there is not near-silence (its word times are only good to about 0.1 s)
PROBE = 0.01               # how far inside a clip the voice piece is looked up, so a seam never picks the neighbour's piece
HF_HZ, HF_SHARE = 3500.0, 0.25
SAME_SOURCE_GAP = 0.05     # clips that continue each other in the same file are not an edge
SILENT_DB = -55.0
MAX_STORY_NOTES = 6
START_WORDS = 8            # how many words of a story fix's "start at these words" are kept
EXTEND_LOOKAHEAD = 3.0     # how far the editor may look for the pause an edge fix runs on to (it measures; this only bounds the search)


@dataclass
class Finding:
    name: str
    ok: bool | None
    detail: str
    notes: list[dict] = field(default_factory=list)


def pcm(path: str, start: float, dur: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.4f}", "-t", f"{max(dur, 0.01):.4f}", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    return np.frombuffer(p.stdout, dtype=np.float32)


def _db(x: np.ndarray) -> float:
    return float(20 * np.log10(max(float(np.sqrt((x ** 2).mean())) if len(x) else 0.0, 1e-6)))


def _frames_db(x: np.ndarray, ms: int = 10) -> np.ndarray:
    n = int(SR * ms / 1000)
    k = len(x) // n
    if k == 0:
        return np.array([-120.0])
    return 20 * np.log10(np.maximum(np.sqrt((x[:k * n].reshape(k, n) ** 2).mean(axis=1)), 1e-6))


def _hf_share(x: np.ndarray) -> float:
    if len(x) < 64:
        return 0.0
    s = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(s[f > HF_HZ].sum() / max(float(s.sum()), 1e-12))


def piece_at(cut: Cut, t: float):
    """The voice audio clip under timeline time t (the enabled, non-layer audio the loader read). Strict: a time exactly on a seam belongs to the piece that starts there."""
    return next((a for a in cut.audio if a.tl_start <= t < a.tl_end), None)


def clip_at(cut: Cut, t: float):
    return next((v for v in cut.video if v.tl_start - 1e-3 <= t < v.tl_end + 1e-3), cut.video[-1] if cut.video else None)


def edges_of(cut: Cut) -> list[tuple[float, str, object]]:
    """(timeline time, 'start' | 'end', clip) for the start of the cut, the end of the cut, and every jump between different footage."""
    out = []
    for i, v in enumerate(cut.video):
        prev = cut.video[i - 1] if i else None
        nxt = cut.video[i + 1] if i + 1 < len(cut.video) else None
        if prev is None or prev.src_path != v.src_path or abs(v.src_in - prev.src_out) > SAME_SOURCE_GAP:
            out.append((v.tl_start, "start", v))
        if nxt is None or nxt.src_path != v.src_path or abs(nxt.src_in - v.src_out) > SAME_SOURCE_GAP:
            out.append((v.tl_end, "end", v))
    return out


def _heard_near(ws, t: float, kind: str, n: int = 3) -> str:
    if kind == "end":
        pick = [w for w in ws if w.start < t][-n:]
    else:
        pick = [w for w in ws if w.end > t][:n]
    return words_mod.heard(pick)


def edge_findings(cut: Cut, words_fn=None, pcm_fn=pcm) -> Finding:
    words_fn = words_fn or words_mod.words_in
    notes, checked = [], 0
    for tl, kind, clip in edges_of(cut):
        probe_t = tl + PROBE if kind == "start" else tl - PROBE
        a = piece_at(cut, probe_t)
        if a is None:
            continue                                                  # SOURCE-AUDIO reports a stretch with no voice
        checked += 1
        src_t = a.src_in + (tl - a.tl_start)
        ref = pcm_fn(a.src_path, a.src_in + (clip.tl_start - a.tl_start), max(clip.tl_end - clip.tl_start, 0.2))
        p90 = float(np.percentile(_frames_db(ref), 90))
        win = pcm_fn(a.src_path, src_t if kind == "start" else src_t - EDGE_SEC, EDGE_SEC)
        edge_db, hf = _db(win), _hf_share(win)
        reasons = []
        if edge_db > p90 - LOUD_DB:
            reasons.append(f"the voice is still {abs(edge_db - p90):.0f} dB from its loudest at the edge of the clip")
        elif hf > HF_SHARE and edge_db > p90 - FRIC_DB:
            reasons.append("the clip ends on a hiss (the last s or f of a word)" if kind == "end" else "the clip starts on a hiss")
        try:
            ws = words_fn(a.src_path, src_t - 1.0, 2.0)
        except Exception:                                             # a failed transcription must not hide the level finding
            ws = []
        straddle = [w for w in ws if (w.start < src_t - 0.03 and w.end > src_t + 0.02)] if kind == "end" else [w for w in ws if (w.start < src_t - 0.02 and w.end > src_t + 0.03)]
        if straddle and edge_db > p90 - WORD_NEAR_DB:
            reasons.append(f'the {"end" if kind == "end" else "start"} lands inside the word "{straddle[0].text.strip()}"')
        if not reasons:
            continue
        try:                                                          # the editor's own measurement decides: a note it would answer with "already quiet, nothing cut off" is not raised
            import ops as _ops
            m = _ops.measure_tail(cut, clip.idx, EXTEND_LOOKAHEAD) if kind == "end" else _ops.measure_head(cut, clip.idx, EXTEND_LOOKAHEAD)
            if "already quiet" in str(m.get("reason", "")):
                continue
        except Exception:
            pass
        heard = _heard_near(ws, src_t, kind)
        if kind == "end":
            text = f'Clip {clip.idx} may cut off the end of what is being said ("{heard}"): {"; ".join(reasons)}. Let the clip run a little longer.'
        else:
            text = f'Clip {clip.idx} may start in the middle of a sound ("{heard}"): {"; ".join(reasons)}. Start the clip a little earlier.'
        fix = {"op": "extend_end" if kind == "end" else "extend_start", "clip": clip.idx, "max_sec": EXTEND_LOOKAHEAD}
        notes.append(note_at(cut, tl - (0.05 if kind == "end" else -0.05), text, quote=heard, kind=f"edge-{kind}", suggested_op=fix))
    if notes:
        return Finding("CUT-EDGES", False, f"{len(notes)} of {checked} cut edges look cut into speech", notes)
    return Finding("CUT-EDGES", True, f"{checked} cut edges checked: each lands in silence or between words")


def note_key(kind: str, quote: str, source: str, source_sec: float) -> str:
    """A name for a finding that survives the cut changing: clip numbers and timeline times move between versions, the words heard at the spot do not. A finding with no words is named by where
    it is in its source file (to the second)."""
    q = " ".join(words_mod.tokens(quote))
    return f"{kind}|{q}" if q else f"{kind}|{source}@{round(source_sec)}"


def note_at(cut: Cut, t: float, text: str, quote: str = "", kind: str = "", suggested_op: dict | None = None) -> dict:
    t = min(max(t, 0.0), max(cut.zone_end - 0.05, 0.0))
    v = clip_at(cut, t)
    source, source_sec = (v.name if v else ""), round((v.src_in + (t - v.tl_start)) if v else t, 2)
    n = {"timeline_sec": round(t, 2), "clip": v.idx if v else 1, "source": source, "source_sec": source_sec,
         "where": quote, "text": "AI: " + text, "shapes": [], "ai": True, "kind": kind or "note", "key": note_key(kind or "note", quote, source, source_sec)}
    if suggested_op:
        n["suggested_op"] = suggested_op                              # the editor makes this fix as it stands (amounts are measured from the audio, not written here)
    return n


def framing_findings(cut: Cut, dims_fn=None, mic_dir_fn=None) -> Finding:
    """A punched-in two-person shot whose picture never changes sides: every shot of a camera file sits on one centre, so whoever talks, the frame stays where it is. Only suggested when both people's own
    recorders are in the cut's recordings folder (that is what tells who is talking); the editor measures the rest (follow_speaker)."""
    import framing
    import follow_speaker as fs
    dims_fn = dims_fn or __import__("render_preview").source_dims
    mic = (mic_dir_fn or fs.mic_dir_of)(cut)
    if not mic or not Path(mic).is_dir():
        return Finding("FRAMING", None, "not checked: the recordings folder is not known, so who is talking cannot be told")
    have = {fs.person_of(p.name) for p in Path(mic).glob("*.WAV")}
    if not {"Bob", "Mitch"} <= have:
        return Finding("FRAMING", None, "not checked: both people's own recorders are not in the recordings folder")
    notes, same = [], []
    for path in sorted({c.src_path for c in cut.video if c.motion}):
        mine = [c for c in cut.video if c.src_path == path and c.motion]
        sw, _sh = dims_fn(path)
        if any(framing.window_half_width(c.motion[0], sw, cut.width) * 2 >= framing.NOTHING_TO_DO_WIDTH for c in mine):
            continue                                                  # a shot already showing nearly the whole width has no room to move
        if len({round(c.motion[1], 3) for c in mine}) == 1:
            same.append(Path(path).name)
            t = mine[0].tl_start + 0.05
            notes.append(note_at(cut, t, "the picture does not follow who is talking: every shot from this camera sits on the same centre. Both people's recorders are available, so it can be centred on the one talking.",
                                 quote="follow the speaker", kind="framing", suggested_op={"op": "follow_speaker", "clip": mine[0].idx}))
    if notes:
        return Finding("FRAMING", False, f"{', '.join(same)}: the frame stays on one centre", notes)
    return Finding("FRAMING", True, "no punched-in shot is stuck on one centre")


OFF_MIC_DIFF_DB = 17.0       # the camera hears it this much louder than the best of the two people's own recorders (measured: an off-camera question +21 to +25 dB, a real answer on a recorder +5 to +14)
OFF_MIC_NEAR_PEAK_DB = 10.0  # and it is loud on the camera: within this of the clip's loudest stretches (so a quiet room is not "a voice")
OFF_MIC_MIN_SEC = 2.0
OFF_MIC_STEP = 0.25
_lav_cache: dict = {}


def _lavs_for(path: str, t0: float, t1: float, mic_dir: str):
    """Both people's recorders matched to this camera over t0..t1 (cached: the matching takes tens of seconds and a review looks at the same footage again each round)."""
    key = (path, round(t0), round(t1), mic_dir)
    if key not in _lav_cache:
        import tempfile
        import speakers as sp
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "cam.wav"
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t0):.3f}", "-t", f"{t1 - max(0.0, t0):.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "8000", str(wav)], capture_output=True)
            if r.returncode != 0:
                raise RuntimeError("the camera's audio could not be read")
            _lav_cache[key] = sp.match_lavs_timed(wav, max(0.0, t0), Path(mic_dir))
    return _lav_cache[key]


def off_mic_spans(cam_db: list[float], bob_db: list[float], mit_db: list[float], word_times: list[tuple[float, float]], step: float = OFF_MIC_STEP,
                  min_sec: float = OFF_MIC_MIN_SEC) -> list[tuple[float, float]]:
    """Stretches (seconds from the clip's start) where someone is speaking but neither person's own recorder is near them: the camera hears it clearly (loud against the clip's own peaks) and a lot
    louder than the louder recorder, and the cut has words there. Gaps under 0.6 s inside a stretch are bridged; a stretch must run `min_sec` and hold at least two words."""
    n = min(len(cam_db), len(bob_db), len(mit_db))
    if n < 4:
        return []
    peak = float(np.percentile(cam_db[:n], 90))
    flags = [cam_db[i] - max(bob_db[i], mit_db[i]) >= OFF_MIC_DIFF_DB and cam_db[i] >= peak - OFF_MIC_NEAR_PEAK_DB for i in range(n)]
    spans: list[list[float]] = []
    for i, f in enumerate(flags):
        if not f:
            continue
        a, b = i * step, (i + 1) * step
        if spans and a - spans[-1][1] <= 0.6:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [(a, b) for a, b in spans if b - a >= min_sec and sum(1 for s, e in word_times if s >= a - 0.05 and e <= b + 0.15) >= 2]


def off_mic_findings(cut: Cut, words: list, mic_dir_fn=None, lavs_fn=None, level_fn=None) -> Finding:
    """A voice in the cut that is on neither person's microphone, for example the interviewer asking from behind the camera: on the recording it is faint and garbled, and no one's recorder was near it.
    Found by comparing the camera's level with both recorders'. When it opens a clip, the fix is to start the clip where the person on a microphone begins; anywhere else it is reported for a person to decide."""
    import follow_speaker as fs
    import speakers as sp
    mic = (mic_dir_fn or fs.mic_dir_of)(cut)
    if not mic or not Path(mic).is_dir():
        return Finding("OFF-MIC", None, "not checked: the recordings folder is not known, so which voices are on a microphone cannot be told")
    lavs_fn, level_fn = lavs_fn or _lavs_for, level_fn or sp.level_track
    notes, found = [], 0
    for path in dict.fromkeys(c.src_path for c in cut.video):
        mine = [c for c in cut.video if c.src_path == path]
        lavs = lavs_fn(path, min(c.src_in for c in mine) - 2.0, max(c.src_out for c in mine) + 2.0, str(mic))
        if len(lavs) < 2:
            return Finding("OFF-MIC", None, "not checked: both people's recorders could not be matched to the camera")
        for c in mine:
            dur = c.src_out - c.src_in
            cam = level_fn(path, c.src_in, dur, OFF_MIC_STEP)
            bob = sp.level_track_timed(lavs["Bob"], c.src_in, dur, OFF_MIC_STEP)
            mit = sp.level_track_timed(lavs["Mitch"], c.src_in, dur, OFF_MIC_STEP)
            wt = [(w[0] - c.tl_start, w[1] - c.tl_start) for w in words if c.tl_start - 0.01 <= w[0] < c.tl_end]
            for a, b in off_mic_spans(cam, bob, mit, wt):
                found += 1
                inside = [w for w in words if c.tl_start + a - 0.05 <= w[0] and w[1] <= c.tl_start + b + 0.15]
                heard = " ".join(w[2] for w in inside)
                after = [w for w in words if w[0] >= c.tl_start + b - 0.05 and w[0] < c.tl_end][:4]
                fix = None
                if a <= 0.6 and len(after) >= 3:
                    fix = {"op": "start_at_words", "clip": c.idx, "words": " ".join(w[2] for w in after), "reach": round(b + 3.0, 1), "max_trim": round(b + 1.0, 1)}   # the stretch was measured: let the fix reach past it and remove it
                where = "at the start of clip %d" % c.idx if a <= 0.6 else "in clip %d" % c.idx
                notes.append(note_at(cut, c.tl_start + a + 0.05, f"A voice that is not on anyone's microphone ({where}, {b - a:.1f}s): someone off camera, so it is faint and unclear on the recording"
                                     + (". Start the clip where the person on the microphone begins." if fix else ". Cut it or replace it by hand."), quote=heard, kind="offmic", suggested_op=fix))
    if found:
        return Finding("OFF-MIC", False, f"{found} stretch(es) of off-camera voice that no recorder was near", notes)
    return Finding("OFF-MIC", True, "every voice in the cut is on a person's own microphone")


def source_audio_findings(cut: Cut, pcm_fn=pcm, covered: list[tuple[float, float]] | None = None) -> Finding:
    """`covered`: stretches where the voice is replaced on purpose (a bleep layer); not reported as holes."""
    dur = cut.zone_end
    if not cut.audio:
        return Finding("SOURCE-AUDIO", False, "no enabled audio in the cut", [note_at(cut, 0.0, "The cut has no audible audio.", kind="audio-none")])
    cam = {v.src_path for v in cut.video}
    share: dict[str, float] = {}
    spans = []
    for a in cut.audio:
        s, e = max(0.0, a.tl_start), min(dur, a.tl_end)
        if e > s:
            share[a.src_path] = share.get(a.src_path, 0.0) + (e - s)
            spans.append((s, e))
    spans = sorted(spans + [(max(0.0, s), min(dur, e)) for s, e in (covered or [])])
    gaps, t = [], 0.0
    for s, e in spans:
        if s - t > 0.15:
            gaps.append((t, s))
        t = max(t, e)
    if dur - t > 0.15:
        gaps.append((t, dur))
    notes = [note_at(cut, g0 + 0.01, f"No voice under {g0:.1f} to {g1:.1f} s: the audio has a hole here.", kind="audio-hole") for g0, g1 in gaps[:4]]
    silent = []
    for a in cut.audio:
        x = pcm_fn(a.src_path, a.src_in, min(a.tl_end - a.tl_start, 30.0))
        if _db(x) < SILENT_DB:
            silent.append(a)
            notes.append(note_at(cut, a.tl_start + 0.01, f"The voice track here ({a.name}) is silent. The wrong recorder may have been synced to this footage.", kind="audio-silent"))
    cam_share = sum(v for k, v in share.items() if k in cam)
    parts = ", ".join(f"{Path(k).name} {v / dur:.0%}" for k, v in sorted(share.items(), key=lambda kv: -kv[1]))
    detail = f"voice from: {parts} (camera audio {cam_share / dur:.0%}, separate recorders {(sum(share.values()) - cam_share) / dur:.0%})"
    if gaps:
        detail += f"; {len(gaps)} stretch(es) with no voice"
    if silent:
        detail += f"; {len(silent)} silent voice piece(s)"
    return Finding("SOURCE-AUDIO", not gaps and not silent, detail, notes)


SYNC_CLIPS = 4             # how many clips the voice recorder is compared with the camera's own audio on (spread across the cut)


def _sync_lag(clip, piece) -> float:
    """Seconds the voice recorder lags the camera's own audio on this clip, from the XML's alignment (NaN when there is nothing to compare)."""
    import verify_preview as vp
    span = min(6.0, clip.tl_end - clip.tl_start - 0.05)
    if span < 1.0:
        return float("nan")
    lav = vp._pcm(piece.src_path, piece.src_in + (clip.tl_start - piece.tl_start), span)
    cam = vp._pcm(clip.src_path, clip.src_in, span)
    if len(lav) < 4000 or len(cam) < 4000:
        return float("nan")
    return vp._lag(lav, cam)


def sync_findings(cut: Cut, lag_fn=None) -> Finding:
    """Is the voice recorder lined up with the picture? The lav is compared with the camera's own audio at the place the XML puts them, on a few clips. A lag that is exactly 0.1% of the
    clip's position in the camera file is the 29.97-versus-30 reading question (see verify_preview.ntsc_explained): reported as open, to be settled in Premiere, never as a pass."""
    import verify_preview as vp
    lag_fn = lag_fn or _sync_lag
    pairs = []
    for v in cut.video:
        a = piece_at(cut, v.tl_start + PROBE)
        if a is not None and a.src_path != v.src_path:
            pairs.append((v, a))
    if not pairs:
        return Finding("SYNC", None, "the voice comes from the camera's own audio, so there is no separate recorder to line up")
    step = max(1, len(pairs) // SYNC_CLIPS)
    picked = pairs[::step][:SYNC_CLIPS]
    good, open_q, bad, none = [], [], [], 0
    for v, a in picked:
        lag = lag_fn(v, a)
        if lag != lag:
            none += 1
        elif abs(lag) < 0.1:
            good.append((v, lag))
        elif vp.ntsc_explained(lag, v.src_in):
            open_q.append((v, lag))
        else:
            bad.append((v, lag))
    if bad:
        return Finding("SYNC", False, "the voice recorder is out of line with the camera: " + "; ".join(f"clip {v.idx} by {lag:+.2f} s" for v, lag in bad))
    if open_q:
        v, lag = open_q[0]
        return Finding("SYNC", None, f"open question: on {len(open_q)} of {len(picked)} clips the recorder is {abs(lag):.2f} s from the camera audio, which is exactly 0.1% of where the clip sits in the camera file "
                                     f"({v.src_in:.0f} s). They line up if the XML's video in-points are read at the sequence's 30 fps and are 0.1% apart if read at the file's 29.97 fps. "
                                     "Open the XML in Premiere and check whether the camera and recorder waveforms line up on one of these clips.")
    if not good:
        return Finding("SYNC", None, "the recorder could not be compared with the camera audio (no usable signal)")
    return Finding("SYNC", True, f"the voice recorder lines up with the camera audio on {len(good)} clip(s) checked (largest lag {max(abs(l) for _v, l in good) * 1000:.0f} ms)")


def transcript_of(cut: Cut, words_fn=None) -> list[tuple[float, float, str, object]]:
    """The words of the finished cut in timeline time: [(start, end, text, clip)], read from the voice audio under each stretch."""
    words_fn = words_fn or words_mod.words_in
    out = []
    for a in cut.audio:
        dur = a.tl_end - a.tl_start
        if dur <= 0.05:
            continue
        for w in words_fn(a.src_path, a.src_in, dur):
            t0, t1 = a.tl_start + (w.start - a.src_in), a.tl_start + (w.end - a.src_in)
            if t1 > a.tl_start and t0 < a.tl_end:
                out.append((max(t0, a.tl_start), min(t1, a.tl_end), w.text.strip(), clip_at(cut, max(t0, a.tl_start) + 0.01)))
    return sorted(out, key=lambda r: r[0])


STORY_SYSTEM = """You are a video editor reviewing a finished short social video from its transcript. The words are in the order they play; "||" marks a cut to different footage.
Judge only what the words show. Decide: does it open on a clear hook, is there one clear point, does it end cleanly, and does each part follow from the one before it?
Reply with one JSON object and nothing else:
{"story": "<one plain sentence: what this video says>",
 "hook": "good" | "weak" | "missing",
 "ending": "clean" | "abrupt" | "missing",
 "problems": [{"quote": "<words copied EXACTLY from the transcript where the problem is>", "note": "<what is wrong and what to change, plain language, one or two sentences>",
               "fix": {"op": "end_at_words", "clip": <clip number>, "words": "<the LAST words to keep, copied EXACTLY from that clip>"} or {"op": "move_clip", "clip": <clip number>, "before": <clip number it should play before>} or {"op": "drop_clip", "clip": <clip number>} or {"op": "start_at_words", "clip": <clip number>, "words": "<words copied EXACTLY from that clip>"} or null}]}
The editor can make four kinds of fix: "start_at_words" starts a clip at a later phrase (skip a false start, filler or an off-topic opening); "end_at_words" ends a clip after a phrase (drop a stray word or a sentence that trails off); "move_clip" plays a clip before another one (a line that sets up the point goes first); "drop_clip" removes a clip that is garbled, contradicts the rest, or has no point of its own.
Whenever one of these would solve a problem, GIVE it: every problem you raise should come with the fix that solves it if the footage in the cut allows it.
Use null when the problem needs new words or a decision. Never propose a fix that would leave the video without a hook or an ending.
Rules: list only real problems (at most 6), most important first. Every quote must be copied exactly from the transcript. A fragment that starts or ends mid-thought is a problem. A line that does not follow
from the one before it is a problem. No praise. No hype words. If the video works, return an empty problems list."""


def _json_object(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[a:b + 1])


def _clip_holding(want: list[str], words: list, named: int, n_clips: int) -> int | None:
    """The clip whose words contain `want` in order: the one the model named if they are there, else the one clip they are in."""
    if not want:
        return None

    def has(idx: int) -> bool:
        have = [(words_mod.tokens(w[2]) or [""])[0] for w in words if w[3] is not None and w[3].idx == idx]
        return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))
    if has(named):
        return named
    found = [i for i in range(1, n_clips + 1) if has(i)]
    return found[0] if len(found) == 1 else None


def _already_true(fix: dict | None, cut: Cut) -> bool:
    """The editor's own measurement says the fix is already so (the clip already starts or ends at those words): the reviewer's transcript and the editor's listening disagree, and the note would
    only come back as "already". Such a note is not raised."""
    if not fix or fix.get("op") not in ("start_at_words", "end_at_words"):
        return False
    try:
        import ops as _ops
        m = (_ops.locate_start if fix["op"] == "start_at_words" else _ops.locate_end)(cut, fix["clip"], fix["words"], fix.get("reach", 6.0), fix.get("max_trim", 3.0))
        return "already" in str(m.get("reason", ""))
    except Exception:
        return False


FIX_SYSTEM = """You are a video editor. For each problem below in a short video's transcript, give the ONE edit that solves it, if one of these does:
{"op": "start_at_words", "clip": n, "words": "<first words to keep, copied EXACTLY from that clip>"}  start the clip later
{"op": "end_at_words", "clip": n, "words": "<last words to keep, copied EXACTLY from that clip>"}  end the clip earlier
{"op": "move_clip", "clip": n, "before": m}  play clip n before clip m
{"op": "drop_clip", "clip": n}  remove the clip
Reply with ONLY a JSON object: {"fixes": [{"problem": <number>, "fix": <one of the above, or null only if no edit of the footage in the cut can solve it>}]}"""


def _ask_for_fixes(client, transcript: str, problems: list[dict]) -> dict[int, dict]:
    """One more call, for the problems the reviewer raised without a fix: just the edit for each. The answers go through the same checks as any fix."""
    listing = "\n".join(f"{i}. {p['note']} (quote: \"{p['quote']}\")" for i, p in enumerate(problems, start=1))
    resp = client.messages.create(system=FIX_SYSTEM, max_tokens=1200, temperature=0, messages=[{"role": "user", "content": f"Transcript:\n{transcript}\n\nProblems:\n{listing}"}])
    out = {}
    for f in (_json_object(resp.content[0].text).get("fixes") or []):
        try:
            out[int(f.get("problem"))] = f.get("fix")
        except (TypeError, ValueError):
            continue
    return out


def _checked_fix(raw, cut: Cut, words: list) -> dict | None:
    """A fix the model proposed for a story problem, kept only if it is one the editor makes and it points at something real: an existing clip, and for start_at_words, words that are in that clip."""
    if not isinstance(raw, dict) or raw.get("op") not in ("drop_clip", "start_at_words", "end_at_words", "move_clip"):
        return None
    try:
        clip = int(raw["clip"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= clip <= len(cut.video):
        return None
    if raw["op"] == "drop_clip":
        return {"op": "drop_clip", "clip": clip}
    if raw["op"] == "move_clip":
        try:
            before = int(raw["before"])
        except (KeyError, TypeError, ValueError):
            return None
        return {"op": "move_clip", "clip": clip, "before": before} if 1 <= before <= len(cut.video) and before != clip else None
    if raw["op"] == "end_at_words":
        want = words_mod.tokens(str(raw.get("words", "")))[-8:]                # the last words to keep are what the editor looks for
        clip = _clip_holding(want, words, clip, len(cut.video))
        if clip is None:
            return None
        dur = cut.video[clip - 1].tl_end - cut.video[clip - 1].tl_start
        return {"op": "end_at_words", "clip": clip, "words": " ".join(want), "reach": round(dur + 0.5, 1), "max_trim": round(max(dur - 0.5, 3.0), 1)}     # the words were found in this clip: the editor may look across all of it
    want = words_mod.tokens(str(raw.get("words", "")))[:START_WORDS]    # the editor only needs the first words to find the spot
    clip = _clip_holding(want, words, clip, len(cut.video))
    if clip is None:
        return None                                                  # the words are in no clip: the fix is dropped, the note stays
    dur = cut.video[clip - 1].tl_end - cut.video[clip - 1].tl_start
    return {"op": "start_at_words", "clip": clip, "words": " ".join(want), "reach": round(dur + 0.5, 1), "max_trim": round(max(dur - 0.5, 3.0), 1)}


def story_findings(cut: Cut, words: list, client=None) -> tuple[list[Finding], dict]:
    if not words:
        return [Finding("STORY", None, "no words were heard in the cut, so the story was not judged")], {"dropped": 0}
    if client is None:
        from posthouse.cli_llm_client import CLIBackedClient
        client = CLIBackedClient()
    lines, cur = [], None
    for t0, _t1, text, clip in words:
        idx = clip.idx if clip else 0
        if idx != cur:
            lines.append(f"\n|| clip {idx} [{t0:.1f}s] " if cur is not None else f"clip {idx} [{t0:.1f}s] ")
            cur = idx
        lines[-1] += text + " "
    transcript = "".join(lines).strip()
    prompt = f'The cut is "{cut.sequence_name}", {cut.zone_end:.1f} seconds, {len(cut.video)} clips.\n\nTranscript:\n{transcript}'
    resp = client.messages.create(system=STORY_SYSTEM, max_tokens=2000, temperature=0, messages=[{"role": "user", "content": prompt}])
    data = _json_object(resp.content[0].text)
    toks = [(words_mod.tokens(w[2]) or [""])[0] for w in words]
    notes, dropped, kept = [], 0, []
    for p in (data.get("problems") or [])[:MAX_STORY_NOTES * 2]:
        q = words_mod.tokens(str(p.get("quote", "")))
        at = next((i for i in range(len(toks) - len(q) + 1) if q and toks[i:i + len(q)] == q), None)
        if at is None or not str(p.get("note", "")).strip():
            dropped += 1                                              # the model's quote is not in the real transcript: not shown
            continue
        kept.append((p, at, _checked_fix(p.get("fix"), cut, words)))
        if len(kept) >= MAX_STORY_NOTES:
            break
    missing = [k for k, (_p, _a, fx) in enumerate(kept) if fx is None]
    if missing:                                                       # a problem raised without a usable fix: ask once more, for the edit alone
        try:
            more = _ask_for_fixes(client, transcript, [{"note": str(kept[k][0]["note"]), "quote": str(kept[k][0]["quote"])} for k in missing])
            for n, k in enumerate(missing, start=1):
                fx = _checked_fix(more.get(n), cut, words)
                if fx:
                    kept[k] = (kept[k][0], kept[k][1], fx)
        except Exception:
            pass
    for p, at, fix in kept:
        if _already_true(fix, cut):
            continue                                                  # the editor hears it as already done: not a problem to hand on
        notes.append(note_at(cut, words[at][0], str(p["note"]).strip(), quote=str(p["quote"]).strip(), kind="story", suggested_op=fix))
    hook, ending = str(data.get("hook", "")).lower(), str(data.get("ending", "")).lower()
    rows = [Finding("STORY", None, str(data.get("story", "")).strip() or "(no summary)"),
            Finding("HOOK", hook == "good", f"the opening is {hook or 'not judged'}"),
            Finding("ENDING", ending == "clean", f"the ending is {ending or 'not judged'}")]
    rows[0].notes = notes
    return rows, {"dropped": dropped, "story": rows[0].detail}


def review(xml: Path, client=None, words_fn=None, pcm_fn=pcm, story: bool = True, progress=lambda s: None) -> dict:
    cut = load_cut(xml)
    checks: list[Finding] = []
    progress("Checking every cut edge against the voice under it")
    checks.append(edge_findings(cut, words_fn, pcm_fn))
    tr_rows: list | None = None

    def transcript_rows():
        nonlocal tr_rows
        if tr_rows is None:
            tr_rows = transcript_of(cut, words_fn)
        return tr_rows
    progress("Checking for a voice that is on nobody's microphone")
    try:
        checks.append(off_mic_findings(cut, transcript_rows()))
    except Exception as exc:
        checks.append(Finding("OFF-MIC", None, f"not checked: {type(exc).__name__}: {str(exc)[:160]}"))
    progress("Checking whether the picture follows who is talking")
    try:
        checks.append(framing_findings(cut))
    except Exception as exc:
        checks.append(Finding("FRAMING", None, f"not checked: {type(exc).__name__}: {str(exc)[:160]}"))
    progress("Checking where the voice comes from")
    try:
        import layers as layers_mod
        bleeps = [(l.start, l.end) for l in layers_mod.find_layers(xml) if l.kind == "audio" and l.name.lower().startswith("bleep")]
    except Exception:
        bleeps = []
    checks.append(source_audio_findings(cut, pcm_fn, bleeps))
    progress("Checking the voice recorder against the camera audio")
    try:
        checks.append(sync_findings(cut))
    except Exception as exc:
        checks.append(Finding("SYNC", None, f"not checked: {type(exc).__name__}: {str(exc)[:160]}"))
    dropped = 0
    summary = ""
    if story:
        progress("Reading the finished cut as a story")
        try:
            rows, meta = story_findings(cut, transcript_rows(), client)
            checks += rows
            dropped, summary = meta.get("dropped", 0), meta.get("story", "")
        except Exception as exc:                                      # the edge and audio findings stand even if the model call cannot run
            checks.append(Finding("STORY", None, f"the story was not judged: {type(exc).__name__}: {str(exc)[:200]}"))
    notes = sorted((n for c in checks for n in c.notes), key=lambda n: n["timeline_sec"])
    return {"schema": "ai_review.v0-draft", "xml": str(xml), "sequence": cut.sequence_name, "duration": round(cut.zone_end, 2), "summary": summary,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks], "notes": notes, "unverified_quotes_dropped": dropped,
            "not_covered": "the picture itself (a cropped head, a wrong shot): frames cannot be passed through the free CLI route; only whether the frame follows the speaker is measured"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-story", action="store_true", help="only the mechanical checks (no model call)")
    a = ap.parse_args()
    res = review(a.xml, story=not a.no_story, progress=lambda s: print(s, file=sys.stderr))
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "ai_review.json").write_text(json.dumps(res, indent=2))
    for c in res["checks"]:
        print(f"  [{'SKIP' if c['ok'] is None else 'PASS' if c['ok'] else 'FAIL'}] {c['name']}  {c['detail']}")
    for n in res["notes"]:
        print(f"  note at {n['timeline_sec']}s (clip {n['clip']}): {n['text']}")
    print(f"{len(res['notes'])} notes -> {a.out / 'ai_review.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
