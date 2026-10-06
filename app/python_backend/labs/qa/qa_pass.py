#!/usr/bin/env python3
"""QA pass: every note checked against the finished new version, from the files.

    PRECUT_ROOT=~/precut-checkout python3 labs/qa/qa_pass.py \
        --notes review_notes.json --revise-dir <revise's folder> \
        --before-xml <old xml> --after-xml <new xml> \
        [--before-page <old review folder> --after-page <new review folder>] \
        [--overlay <callout folder> ...] [--audio-before F --audio-after F] --out <folder>

The revise step's own ledger says what it attempted. This does not trust it: each note is re-measured
on the new version (the cut's source ranges, the silence, the words heard, the layer files) and given
one of

  VERIFIED             measured on the new version, and it holds
  APPLIED-UNMEASURED   the ledger says it was applied and nothing here can measure that kind of change;
                       look at the before and after frames
  NOT DONE             not applied, with the reason and what it would need
  FAILED               measured, and the new version does not do what the note asked

It also lists anything that changed in the cut that no note asked for. The report is a page with a
before and after frame for every note. Exit 1 if anything FAILED.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
for sub in ("review_loop", "overlay", "audio", "captions", "bleep"):
    sys.path.insert(0, str(LABS / sub))
sys.path.insert(0, str(LABS.parent / "safety_net"))

import layers as ly  # noqa: E402
import ops as opsmod  # noqa: E402
import timeline  # noqa: E402
import verify_export  # noqa: E402
import words as words_mod  # noqa: E402

VERIFIED, UNMEASURED, NOT_DONE, FAILED = "VERIFIED", "APPLIED-UNMEASURED", "NOT DONE", "FAILED"
RANK = {VERIFIED: 0, UNMEASURED: 1, NOT_DONE: 2, FAILED: 3}
TOL = 0.05          # seconds: about three frames at 60 fps


@dataclass
class Row:
    note: int
    op: str
    status: str
    detail: str


@dataclass
class NoteResult:
    note: int
    time: float
    text: str
    rows: list[Row] = field(default_factory=list)
    t_new: float | None = None
    shapes: bool = False

    @property
    def status(self) -> str:
        return max((r.status for r in self.rows), key=lambda s: RANK[s]) if self.rows else NOT_DONE


def clip_at(cut: timeline.Cut, t: float):
    return next((c for c in cut.video if c.tl_start <= t < c.tl_end), None)


def map_time(old: timeline.Cut, new: timeline.Cut, t: float) -> float | None:
    """Where the moment at time t on the old cut is on the new cut, found by its source frame."""
    c = clip_at(old, t)
    if c is None:
        return None
    src = c.src_in + (t - c.tl_start)
    for n in new.video:
        if n.src_path == c.src_path and n.src_in - 0.02 <= src <= n.src_out + 0.02:
            return n.tl_start + (src - n.src_in)
    return None


def src_intervals(cut: timeline.Cut, s: float, e: float) -> list[tuple[str, float, float]]:
    out = []
    for c in cut.video:
        lo, hi = max(s, c.tl_start), min(e, c.tl_end)
        if hi - lo > 1e-6:
            out.append((c.src_path, c.src_in + (lo - c.tl_start), c.src_in + (hi - c.tl_start)))
    return out


def still_used(cut: timeline.Cut, path: str, lo: float, hi: float) -> float:
    """Seconds of [lo, hi] in a source file that this cut still plays."""
    spans = sorted((max(lo, c.src_in), min(hi, c.src_out)) for c in cut.video if c.src_path == path and min(hi, c.src_out) > max(lo, c.src_in))
    total, cur = 0.0, lo
    for a, b in spans:
        a = max(a, cur)
        if b > a:
            total += b - a
            cur = b
    return total


def gone(old: timeline.Cut, new: timeline.Cut, s: float, e: float) -> tuple[bool, str]:
    ivs = src_intervals(old, s, e)
    total = sum(hi - lo for _p, lo, hi in ivs)
    left = sum(still_used(new, p, lo, hi) for p, lo, hi in ivs)
    ok = left <= 2 * TOL
    return ok, (f"the {total:.2f}s at {s:.2f}-{e:.2f}s on the old cut is gone from the new one" if ok
                else f"{left:.2f}s of the {total:.2f}s that should be gone at {s:.2f}-{e:.2f}s is still in the new cut")


def find_piece(new: timeline.Cut, path: str, *, src_in: float | None = None, src_out: float | None = None):
    for n in new.video:
        if n.src_path == path and (src_in is None or abs(n.src_in - src_in) <= TOL) and (src_out is None or abs(n.src_out - src_out) <= TOL):
            return n
    return None


def anchor_of(l: ly.Layer):
    """The frame a callout layer was drawn on, read from the placement.json beside its file (None if there is none)."""
    try:
        a = json.loads((Path(l.path).parent / "placement.json").read_text())["anchor"]
        return a["source"], round(a["source_sec"], 2)
    except (OSError, ValueError, KeyError):
        return None


def check_extend_graphic(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    row, pair = _callout_pair("extend_graphic", o, note, layers)
    if row:
        return row
    b, a = pair
    was, now = b.end - b.start, a.end - a.start
    gained = now - was
    want = o.get("seconds")
    need = (want - 0.25) if want is not None else 0.3
    asked = f"asked for +{want:.2f}s" if want is not None else "no amount stated, so the default step applies"
    ok = gained >= need
    return Row(n, "extend_graphic", VERIFIED if ok else FAILED, f"the callout is on screen {now:.2f}s, was {was:.2f}s ({gained:+.2f}s; {asked})")


def check_end_graphic(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    row, pair = _callout_pair("end_graphic", o, note, layers)
    if row:
        return row
    b, a = pair
    t = note["timeline_sec"]
    shorter = (b.end - b.start) - (a.end - a.start)
    ok = shorter > 0.3 and t - 0.1 <= a.end <= t + 1.0               # the fade starts at t, so the layer ends within its fade of t
    return Row(n, "end_graphic", VERIFIED if ok else FAILED,
               f"the callout now ends at {a.end:.2f}s (asked to fade out at {t:.2f}s; it ended at {b.end:.2f}s, {shorter:+.2f}s shorter)")


def check_remove_graphic(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    if not layers or layers[0] is None:
        return Row(n, "remove_graphic", UNMEASURED, "no before-version XML was given (pass the layered XML the note was left on as --before-xml)")
    before, after = layers
    t, tg = note["timeline_sec"], note.get("target")
    gl = [l for l in before if l.kind == "video" and ly.lane_name(l) in ("Callout", "Card")]
    if tg:
        was = [l for l in gl if ly.lane_name(l) == tg.get("lane") and abs(l.start - float(tg.get("start", -9))) <= 0.25]
    else:
        was = [l for l in gl if l.start <= t <= l.end]
    if not was:
        return Row(n, "remove_graphic", NOT_DONE, f"no callout or card was on screen at {t:.2f}s on the version the note was left on")
    b = was[0]
    still = [l for l in after if l.kind == "video" and ly.lane_name(l) == ly.lane_name(b) and abs(l.start - b.start) <= 0.25]
    others = sum(1 for l in gl if l is not b) == sum(1 for l in after if l.kind == "video" and ly.lane_name(l) in ("Callout", "Card"))
    ok = not still and others
    return Row(n, "remove_graphic", VERIFIED if ok else FAILED,
               f"the {ly.lane_name(b).lower()} at {b.start:.2f}-{b.end:.2f}s is {'gone' if not still else 'still there'}; "
               + ("the other graphics are all still there" if others else "a different graphic was lost or added too"))


def check_edit_caption(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    if not layers or layers[0] is None:
        return Row(n, "edit_caption", UNMEASURED, "no before-version XML was given")
    before, after = layers
    cap = [l for l in after if l.kind == "video" and ly.lane_name(l) == "Captions"]
    if not cap:
        return Row(n, "edit_caption", FAILED, "there is no captions layer on the new version")
    try:
        groups = json.loads((Path(cap[0].path).parent / "captions.json").read_text())["groups"]
    except (OSError, ValueError, KeyError):
        return Row(n, "edit_caption", UNMEASURED, "the captions layer has no captions.json beside it to read the lines from")
    import make_captions as mc
    t = note["timeline_sec"] - cap[0].start
    line = next((g for g in groups if g["show_start"] - 0.05 <= t <= g["show_end"] + 0.05), None)
    if line is None:
        return Row(n, "edit_caption", FAILED, f"no caption line is showing at {note['timeline_sec']:.2f}s on the new version")
    want = mc.tidy_case(o["text"], line["text"])
    ok = line["text"] == want
    return Row(n, "edit_caption", VERIFIED if ok else FAILED, f'the caption at {note["timeline_sec"]:.2f}s reads "{line["text"]}"' + ("" if ok else f', not "{want}"'))


def check_bleep_word(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    if not layers or layers[0] is None:
        return Row(n, "bleep_word", UNMEASURED, "no before-version XML was given")
    _before, after = layers
    t, tg = note["timeline_sec"], note.get("target")
    if tg and tg.get("lane") == "Suspects":
        lo, hi = float(tg["start"]), float(tg["end"])
    elif tg and tg.get("clicked"):
        lo, hi = t - 0.8, t + 0.8
    elif tg and tg.get("lane") in ("Clips", "Captions", "Cuts") and tg.get("end", 0) > tg.get("start", 0):
        lo, hi = float(tg["start"]), float(tg["end"])
    else:
        lo, hi = t - 1.0, t + 1.0
    got = [l for l in after if l.kind == "audio" and ly.lane_name(l) == "Bleep" and l.start < hi and l.end > lo]
    if not got:
        return Row(n, "bleep_word", NOT_DONE, f"no bleep sits in {lo:.2f}-{hi:.2f}s on the new version (nothing in that stretch was found to bleep; see bleep.json for the suspects)")
    folder = Path(got[0].path).parent.parent
    ok_file = (folder / "bleep.json").exists()
    return Row(n, "bleep_word", VERIFIED if ok_file else UNMEASURED, f"a bleep sits at {got[0].start:.2f}-{got[0].end:.2f}s"
               + ("; its own checks (silence, tone, level) are in that folder's bleep run" if ok_file else "; its bleep.json is missing, so the silence was not re-measured")
               + ". Whether the bleeped word is the curse word is for your ear")


def _callout_pair(op_name: str, o: dict, note: dict, layers: tuple | None):
    """(row, None) when the pair cannot be found, else (None, (before layer, after layer))."""
    n = o["note"]
    if not layers or layers[0] is None:
        return Row(n, op_name, UNMEASURED, "no before-version XML with the callout was given (pass the layered XML the note was left on as --before-xml)"), None
    before, after = layers
    t = note["timeline_sec"]
    tg = note.get("target")
    if tg:                                              # left on a timeline element: that element's start names the callout
        live = [l for l in before if l.kind == "video" and ly.lane_name(l) == "Callout" and tg.get("lane") == "Callout"
                and abs(l.start - float(tg.get("start", -9))) <= 0.25]
    else:
        live = [l for l in before if l.kind == "video" and ly.lane_name(l) == "Callout" and l.start <= t <= l.end]
    if not live:
        return Row(n, op_name, NOT_DONE, (f'the {tg["lane"]} element "{tg.get("label", "")}" is not a callout on the version the note was left on' if tg
                                          else f"no callout was on screen at {t:.2f}s on the version the note was left on")), None
    b = min(live, key=lambda l: abs(t - (l.start + l.end) / 2))
    key = anchor_of(b)
    cand = [l for l in after if l.kind == "video" and ly.lane_name(l) == "Callout" and (key is None or anchor_of(l) == key)]
    if not cand:
        return Row(n, op_name, FAILED, "that callout is not on the new version"), None
    return None, (b, cand[0])


def _placement(l: ly.Layer) -> dict:
    try:
        return json.loads((Path(l.path).parent / "placement.json").read_text())
    except (OSError, ValueError):
        return {}


def check_edit_callout(o: dict, note: dict, layers: tuple | None) -> Row:
    n = o["note"]
    row, pair = _callout_pair("edit_callout", o, note, layers)
    if row:
        return row
    b, a = pair
    pb, pa_ = _placement(b), _placement(a)
    bt, bs, at, as_ = pb.get("title"), pb.get("subtitle"), pa_.get("title"), pa_.get("subtitle")
    if at is None:
        return Row(n, "edit_callout", UNMEASURED, "the new callout has no placement.json to read its words from")
    want_t = o.get("title")
    want_s = "" if o.get("remove_subtitle") else o.get("subtitle")
    wrong = []
    if want_t is not None and at != want_t:
        wrong.append(f'title is "{at}", the note asked for "{want_t}"')
    if want_s is not None and as_ != want_s:
        wrong.append(f'second line is "{as_}", the note asked for "{want_s}"')
    if wrong:
        return Row(n, "edit_callout", FAILED, "; ".join(wrong))
    if (at, as_) == (bt, bs):
        return Row(n, "edit_callout", FAILED, f'the callout still reads "{at}" / "{as_}": nothing changed')
    g = pa_.get("geometry", {})
    tmid = g.get("t_in", 0.5) + 0.5 * (g.get("t_out", 2.0) - g.get("t_in", 0.5))
    try:
        w, h = ly._size(Path(a.path))
        if ly._size(Path(b.path)) != (w, h):
            w, h = 960, 540
        fa = ly._frame(Path(a.path), tmid, w, h)
        fb = ly._frame(Path(b.path), tmid, w, h)
        diff = int((np.abs(fa - fb).max(axis=2) > 40).sum())
    except Exception:
        diff = -1
    if diff < 0:
        return Row(n, "edit_callout", UNMEASURED, f'the words are now "{at}" / "{as_}" (was "{bt}" / "{bs}"); the rendered layers could not be compared')
    scale = (w * h) / (1920 * 1080)
    ok = diff >= 150 * scale
    return Row(n, "edit_callout", VERIFIED if ok else FAILED,
               f'the callout now reads "{at}" / "{as_}" (was "{bt}" / "{bs}"); {diff} pixels of the rendered layer differ at {tmid:.1f}s into it'
               + ("" if ok else ", too few for a changed label"))


def check_op(o: dict, item: dict | None, note: dict, old: timeline.Cut, new: timeline.Cut, pages: dict, t_new: float | None,
             audio: dict, layers: tuple | None = None) -> Row:
    n, kind = o["note"], o["op"]
    applied = bool(item and item.get("applied"))
    if kind == "unsupported":
        return Row(n, kind, NOT_DONE, f"not done: {o.get('reason', 'not supported')}")
    if kind == "replace_sfx":
        return check_replace_sfx(o, note, audio)
    if kind == "extend_graphic":
        return check_extend_graphic(o, note, layers)
    if kind == "end_graphic":
        return check_end_graphic(o, note, layers)
    if kind == "remove_graphic":
        return check_remove_graphic(o, note, layers)
    if kind == "edit_caption":
        return check_edit_caption(o, note, layers)
    if kind == "bleep_word":
        return check_bleep_word(o, note, layers)
    if kind == "edit_callout":
        return check_edit_callout(o, note, layers)
    if not applied:
        return Row(n, kind, NOT_DONE, f"not applied: {(item or {}).get('summary', 'no record from the revise step')}")

    if kind in ("tighten_pause", "remove_range"):
        rem = item.get("removed")
        parts, status = [], VERIFIED
        if rem:
            ok, d = gone(old, new, rem[0], rem[1])
            parts.append(d)
            status = VERIFIED if ok else FAILED
        else:
            status = UNMEASURED
            parts.append("the revise record has no removed span (re-run revise.py), so the range itself was not re-checked")
        if kind == "tighten_pause" and t_new is not None:
            was = opsmod.detect_pause(old, o["at"])
            now = opsmod.detect_pause(new, t_new)
            if was is None:
                parts.append("no pause could be measured on the old version")
                status = status if status == FAILED else UNMEASURED
            else:
                wl = was.end - was.start
                nl = (now.end - now.start) if now else 0.0
                good = now is None or (nl <= wl - 0.2 and nl <= 0.35)
                parts.append(f"the pause near {o['at']:.2f}s measured {wl:.2f}s; on the new version " + (f"{nl:.2f}s" if now else "no silence of 0.25s or more remains"))
                if not good:
                    status = FAILED
        return Row(n, kind, status, "; ".join(parts))
    if kind in ("trim_start", "trim_end", "drop_clip", "extend_end", "start_at_words"):
        idx = o["clip"]
        if not 1 <= idx <= len(old.video):
            return Row(n, kind, UNMEASURED, f"clip {idx} is not on the old cut")
        c = old.video[idx - 1]
        if kind == "drop_clip":
            ok, d = gone(old, new, c.tl_start, c.tl_end)
            return Row(n, kind, VERIFIED if ok else FAILED, d)
        if kind == "trim_start":
            p = find_piece(new, c.src_path, src_out=c.src_out)
            if p is None:
                return Row(n, kind, FAILED, "no piece of that clip ends where it did before")
            got = p.src_in - c.src_in
            return Row(n, kind, VERIFIED if got >= o["seconds"] - TOL else FAILED, f"clip {idx} now starts {got:.2f}s later in its source (asked for {o['seconds']:.2f}s)")
        if kind == "trim_end":
            p = find_piece(new, c.src_path, src_in=c.src_in)
            if p is None:
                return Row(n, kind, FAILED, "no piece of that clip starts where it did before")
            got = c.src_out - p.src_out
            return Row(n, kind, VERIFIED if got >= o["seconds"] - TOL else FAILED, f"clip {idx} now ends {got:.2f}s earlier in its source (asked for {o['seconds']:.2f}s)")
        if kind == "extend_end":
            p = find_piece(new, c.src_path, src_in=c.src_in)
            if p is None:
                return Row(n, kind, FAILED, "no piece of that clip starts where it did before")
            ext = p.src_out - c.src_out
            lvl = ""
            try:
                a = opsmod._audio_for(new, p)
                lvl = f"; level at the new end {opsmod.level_db(a[0], a[2] - 0.03):.0f} dB"
            except Exception:
                pass
            return Row(n, kind, VERIFIED if ext >= 0.05 else FAILED, f"clip {idx} now runs {ext:.2f}s longer{lvl}")
        if kind == "start_at_words":
            pv = pages.get("after_preview")
            seam = item.get("v2_time")
            if seam is None:
                return Row(n, kind, UNMEASURED, "the revise record has no seam time to check")
            if not any(abs(c.tl_start - seam) <= TOL for c in new.video):                  # a cut must exist there: words alone cannot tell a seam from mid-sentence
                return Row(n, kind, FAILED, f"no clip starts at the seam time {seam:.2f}s on the new version")
            if pv is None:
                return Row(n, kind, UNMEASURED, f"a clip starts at the seam ({seam:.2f}s); no new-version preview was given to listen to, so the words were not re-checked")
            after = [w for w in words_mod.words_in(str(pv), seam - 0.3, 4.0) if w.start >= seam - 0.1]
            hit = words_mod.find_phrase(after, o["words"])
            return Row(n, kind, VERIFIED if hit is not None and hit[0] <= 1 else FAILED,
                       f'a clip starts at the seam ({seam:.2f}s) and the new version reads from it: "{words_mod.heard(after[:9])}" (asked to start at "{o["words"]}")')
    if kind == "reframe_vertical":
        c = old.video[o["clip"] - 1] if 1 <= o["clip"] <= len(old.video) else None
        if c is None or not c.motion:
            return Row(n, kind, UNMEASURED, f"clip {o['clip']} has no position on the old cut to compare")
        p = find_piece(new, c.src_path, src_in=c.src_in)
        if p is None or not p.motion:
            return Row(n, kind, FAILED, "no clip with a position starts where that clip did on the new cut")
        moved = p.motion[2] - c.motion[2]                                              # positive = the picture is lower on screen (the rule that has not been confirmed in Premiere)
        right_way = moved > 0 if o["direction"] == "lower" else moved < 0
        ok = right_way and abs(moved) >= 0.01 and abs(p.motion[0] - c.motion[0]) < 0.01
        return Row(n, kind, VERIFIED if ok else FAILED,
                   f"clip {o['clip']}: vertical position {c.motion[2]:+.3f} -> {p.motion[2]:+.3f} ({'down' if moved > 0 else 'up' if moved < 0 else 'unchanged'} in the frame; asked to {o['direction']} it), scale {p.motion[0]:g}. "
                   "Measured from the XML under an ASSUMED vertical rule: the confirmation is Premiere's Position y")
    return Row(n, kind, UNMEASURED, f"no independent check exists for {kind}")


def check_replace_sfx(o: dict, note: dict, audio: dict) -> Row:
    n = o["note"]
    if not audio.get("before") or not audio.get("after"):
        return Row(n, "replace_sfx", UNMEASURED, "no audio folders given, so the new effect was not compared with the old one")
    try:
        import verify_audio as va
        meta = json.loads((audio["after"] / "audio.json").read_text())
        a, b = va.pcm(audio["before"] / "sfx_clip.wav"), va.pcm(audio["after"] / "sfx_clip.wav")
    except (OSError, ValueError, KeyError) as e:
        return Row(n, "replace_sfx", UNMEASURED, f"could not read the audio folders: {e}")
    m = min(len(a), len(b))
    a, b = a[:m] - a[:m].mean(), b[:m] - b[:m].mean()
    cc = np.fft.irfft(np.fft.rfft(a, 2 * m) * np.conj(np.fft.rfft(b, 2 * m)), 2 * m)
    corr = float(np.max(np.abs(cc)) / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-9))
    rep = meta.get("replaced") or {}
    # replace_sfx's own folder records which note it came from; a folder rebuilt by reconform does not, but it records the prompt the effect was
    # generated from, and that prompt is built from the note's words
    prompt = str((meta.get("generated", {}).get("sfx") or {}).get("prompt", ""))
    asked = o["sound"].lower() in (str(rep.get("now", "")) or prompt).lower()
    ok = bool(corr < 0.6 and asked and rep.get("note", n) == n)
    return Row(n, "replace_sfx", VERIFIED if ok else FAILED,
               f'the effect is now a different sound (waveform match {corr:.2f}, limit 0.60); generated from "{rep.get("now", "?")}"' if ok
               else f"the effect matches the old one at {corr:.2f} or was not generated from this note's words")


def check_callout(note: dict, overlays: list[Path], new: timeline.Cut, after_xml: Path, pages: dict) -> Row | None:
    """A note with a drawing is 'done' by a callout layer anchored on the frame it was drawn on."""
    for f in overlays:
        pl = json.loads((f / "placement.json").read_text())
        an = pl.get("anchor")
        if not an or an["source"] != note.get("source") or abs(an["source_sec"] - note.get("source_sec", -99)) > 0.15:
            continue
        found = [l for l in ly.find_layers(after_xml) if l.kind == "video" and Path(l.path).resolve() == Path(pl["overlay_path"]).resolve()]
        if not found:
            return Row(note["_n"], "callout", NOT_DONE, "this note's callout is not on the new cut (its frame was cut out, so it was dropped)")
        l = found[0]
        want = None
        for c in new.video:
            if c.name == an["source"] and c.src_in - 0.02 <= an["source_sec"] <= c.src_out + 0.02:
                want = c.tl_start + (an["source_sec"] - c.src_in) - an["lead_sec"]
        if want is None or abs(l.start - want) > 2 * TOL:
            return Row(note["_n"], "callout", FAILED, f"the callout is at {l.start:.2f}s but its frame is at {want if want is None else round(want, 2)}s")
        seen = ""
        if pages.get("after_preview") and pages.get("after_full"):
            t = l.start + min(2.0, (l.end - l.start) / 2)
            w, h = ly._size(pages["after_preview"])
            d = int((np.abs(ly._frame(pages["after_preview"], t, w, h) - ly._frame(pages["after_full"], t, w, h)).max(axis=2) > 40).sum())
            seen = f"; {d} pixels differ from the clean picture at {t:.1f}s"
            if d < 300:
                return Row(note["_n"], "callout", FAILED, f"the callout layer is on the timeline at {l.start:.2f}s but is not visible in the layered preview{seen}")
        return Row(note["_n"], "callout", VERIFIED, f"a callout layer is placed on this note's frame ({l.start:.2f}-{l.end:.2f}s){seen}")
    return None


def unrequested(old: timeline.Cut, new: timeline.Cut, items: list[dict]) -> tuple[list[str], str]:
    """Source footage that entered or left the cut without any note asking for it."""
    if items and not any("removed" in i for i in items):
        return [], "skipped: the revise record predates this check (no removed spans); re-run revise.py to enable it"
    allowed: dict[str, list[tuple[float, float]]] = {}
    for i in items:
        if not i.get("applied"):
            continue
        if i.get("removed"):
            for p, lo, hi in src_intervals(old, i["removed"][0], i["removed"][1]):
                allowed.setdefault(p, []).append((lo - TOL, hi + TOL))
    # footage added to a clip's end is allowed up to the total extension the revise record lists
    ext = sum(i.get("extended_sec") or 0 for i in items if i.get("applied"))
    findings = []
    paths = {c.src_path for c in old.video} | {c.src_path for c in new.video}
    for p in paths:
        for label, a, b in (("left", old, new), ("entered", new, old)):
            for c in a.video:
                if c.src_path != p:
                    continue
                miss = (c.src_out - c.src_in) - still_used(b, p, c.src_in, c.src_out)
                if miss <= 2 * TOL:
                    continue
                # which parts are missing: walk the interval
                pos = c.src_in
                spans = sorted((max(c.src_in, x.src_in), min(c.src_out, x.src_out)) for x in b.video if x.src_path == p and min(c.src_out, x.src_out) > max(c.src_in, x.src_in))
                gaps = []
                for s0, s1 in spans:
                    if s0 - pos > 2 * TOL:
                        gaps.append((pos, s0))
                    pos = max(pos, s1)
                if c.src_out - pos > 2 * TOL:
                    gaps.append((pos, c.src_out))
                for g0, g1 in gaps:
                    if label == "left" and any(lo <= g0 + 2 * TOL and g1 - 2 * TOL <= hi for lo, hi in allowed.get(p, [])):
                        continue
                    if label == "entered" and ext and g1 - g0 <= ext + 4 * TOL:
                        continue
                    findings.append(f"{Path(p).name}: {g1 - g0:.2f}s at source {g0:.2f}-{g1:.2f}s {label} the cut with no note asking for it")
    return findings, ""


def frame_jpeg(video: Path | None, t: float, out: Path, fallback_cut: timeline.Cut | None = None) -> bool:
    src, at = video, t
    if video is None and fallback_cut is not None:
        c = clip_at(fallback_cut, t)
        if c is None:
            return False
        src, at = Path(c.src_path), c.src_in + (t - c.tl_start)
    if src is None:
        return False
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(at, 0):.3f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=560:-2", "-q:v", "5", str(out)], capture_output=True)
    return p.returncode == 0 and out.exists()


def b64(p: Path | None) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p and p.exists() else ""


BADGE = {VERIFIED: "#38d66b", UNMEASURED: "#ffd400", NOT_DONE: "#f4690b", FAILED: "#e5333d"}


def fmt(t: float) -> str:
    m = int(t // 60)
    return f"{m:02d}:{t - m * 60:04.1f}"


def render(results: list[NoteResult], glob: list[tuple[str, bool | None, str]], unreq: list[str], unreq_note: str, frames: dict, title: str) -> str:
    counts = {s: sum(r.status == s for r in results) for s in RANK}
    head = (f"{len(results)} notes: {counts[VERIFIED]} verified, {counts[UNMEASURED]} applied but not measurable, "
            f"{counts[NOT_DONE]} not done, {counts[FAILED]} failed")
    rows = []
    for r in results:
        b, a = frames.get((r.note, "before")), frames.get((r.note, "after"))
        imgs = ""
        if b:
            imgs += f'<figure><img src="{b64(b)}"><figcaption>before, {fmt(r.time)}</figcaption></figure>'
        if a:
            imgs += f'<figure><img src="{b64(a)}"><figcaption>after, {fmt(r.t_new)}</figcaption></figure>'
        elif r.t_new is None:
            imgs += '<figure class="gone"><figcaption>the frame this note was left on is not in the new version</figcaption></figure>'
        ops = "".join(f'<li><span class="op">{html.escape(x.op)}</span> <b style="color:{BADGE[x.status]}">{x.status}</b> {html.escape(x.detail)}</li>' for x in r.rows) or "<li>no operation was produced for this note</li>"
        rows.append(f'<section class="note"><div class="hd"><span class="n">note {r.note}</span><span class="t">{fmt(r.time)}</span>'
                    f'<span class="badge" style="background:{BADGE[r.status]}">{r.status}</span></div>'
                    f'<p class="q">{html.escape(r.text or "(drawing only)")}</p><ul>{ops}</ul><div class="imgs">{imgs}</div></section>')
    g = "".join(f'<li><b style="color:{"#38d66b" if ok else "#e5333d" if ok is False else "#8b98ad"}">{"PASS" if ok else "FAIL" if ok is False else "INFO"}</b> {html.escape(n)}: {html.escape(d)}</li>' for n, ok, d in glob)
    u = ("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in unreq) + "</ul>") if unreq else f"<p>{html.escape(unreq_note) if unreq_note else 'Nothing changed in the cut that a note did not ask for.'}</p>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>QA pass</title><style>
:root{{color-scheme:dark;--bg:#0d1420;--panel:#141d2c;--ink:#e8edf5;--mute:#8b98ad;--line:#25324a}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif}}
main{{max-width:1000px;margin:0 auto;padding:20px 16px 60px}} h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:26px 0 8px}}
.sum{{color:var(--mute);margin:0 0 16px}} .read{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--mute);font-size:13px}}
.note{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}} .hd{{display:flex;gap:10px;align-items:center}}
.n{{font-weight:700}} .t{{color:var(--mute)}} .badge{{margin-left:auto;color:#0d1420;font-weight:700;font-size:12px;padding:2px 8px;border-radius:99px}}
.q{{margin:6px 0;color:var(--ink)}} ul{{margin:6px 0;padding-left:18px}} .op{{color:var(--mute)}}
.imgs{{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}} figure{{margin:0}} img{{width:100%;max-width:470px;border-radius:6px;display:block}}
figcaption{{color:var(--mute);font-size:12px;margin-top:2px}} .gone{{color:var(--mute);border:1px dashed var(--line);border-radius:6px;padding:10px}}
</style></head><body><main><h1>{html.escape(title)}</h1><p class="sum">{html.escape(head)}</p>
<p class="read">Every status here was measured on the new version's files, not taken from the revise step's own report. "Applied but not measurable" means the ledger says it was applied and nothing here can independently check that kind of change: look at the frames. A note that was not done says why.</p>
{"".join(rows)}<h2>Whole-cut checks</h2><ul>{g}</ul><h2>Changes nobody asked for</h2>{u}</main></body></html>"""


def qa(notes: list[dict], ops: list[dict], items: list[dict], old: timeline.Cut, new: timeline.Cut, after_xml: Path, out: Path, *,
       before_page: Path | None = None, after_page: Path | None = None, overlays: list[Path] | None = None,
       audio: dict | None = None, title: str = "QA pass", before_xml: Path | None = None) -> tuple[list[NoteResult], list, list[str], Path]:
    out.mkdir(parents=True, exist_ok=True)
    layers = (ly.find_layers(before_xml) if before_xml else None, ly.find_layers(after_xml))
    fdir = out / "frames"
    fdir.mkdir(exist_ok=True)
    audio = audio or {}
    overlays = overlays or []
    pages = {"after_preview": after_page / "preview.mp4" if after_page and (after_page / "preview.mp4").exists() else None,
             "after_full": after_page / "preview_full.mp4" if after_page and (after_page / "preview_full.mp4").exists() else None}
    by_item = {i["note"]: i for i in items}
    results: list[NoteResult] = []
    frames: dict = {}
    for k, note in enumerate(notes, start=1):
        note = dict(note, _n=k)
        r = NoteResult(k, note["timeline_sec"], note.get("text", ""), shapes=bool(note.get("shapes")))
        r.t_new = map_time(old, new, note["timeline_sec"])
        item = by_item.get(k)
        if item and item.get("v2_time") is not None and item.get("applied"):
            t_for_op = item["v2_time"]
        else:
            t_for_op = r.t_new
        for o in [x for x in ops if x["note"] == k]:
            r.rows.append(check_op(o, item, note, old, new, pages, t_for_op, audio, layers))
        if any(x.status == NOT_DONE for x in r.rows) or not r.rows:
            c = check_callout(note, overlays, new, after_xml, pages) if overlays else None
            if c:
                r.rows = [x for x in r.rows if x.op != "unsupported"] + [c]
        results.append(r)
        bv = (before_page / "preview_full.mp4") if before_page and (before_page / "preview_full.mp4").exists() else ((before_page / "preview.mp4") if before_page else None)
        av = pages["after_full"] or pages["after_preview"]
        bj, aj = fdir / f"note{k}_before.jpg", fdir / f"note{k}_after.jpg"
        if frame_jpeg(bv, r.time, bj, old if bv is None else None):
            frames[(k, "before")] = bj
        if r.t_new is not None and frame_jpeg(av, r.t_new, aj, new if av is None else None):
            frames[(k, "after")] = aj

    glob: list[tuple[str, bool | None, str]] = []
    rep = verify_export.Report()
    verify_export.check_xml(after_xml, rep)
    bad = [n for n, ok, _d in rep.rows if ok is False]
    glob.append(("verify_export", not bad, "every applicable export check passes on the new XML" if not bad else f"FAILED: {', '.join(bad)}"))
    lay = ly.find_layers(after_xml)
    names = [l.name for l in lay]
    glob.append(("LAYERS-WHOLE", len(names) == len(set(names)), f"{len(lay)} layer(s), none cut into pieces" if len(names) == len(set(names)) else "a layer is in pieces: run labs/reconform"))
    glob.append(("LENGTH", True, f"old cut {old.zone_end:.2f}s, new cut {new.zone_end:.2f}s ({new.zone_end - old.zone_end:+.2f}s)"))
    unreq, unreq_note = unrequested(old, new, items)
    (out / "qa_report.html").write_text(render(results, glob, unreq, unreq_note, frames, title))
    (out / "qa.json").write_text(json.dumps({
        "notes": [{"note": r.note, "time": r.time, "text": r.text, "status": r.status, "t_new": r.t_new,
                   "rows": [{"op": x.op, "status": x.status, "detail": x.detail} for x in r.rows]} for r in results],
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in glob], "unrequested": unreq, "unrequested_note": unreq_note}, indent=2))
    return results, glob, unreq, out / "qa_report.html"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", type=Path, required=True)
    ap.add_argument("--revise-dir", type=Path, required=True, help="the folder revise.py wrote (ops.json, changes.json)")
    ap.add_argument("--before-xml", type=Path, required=True)
    ap.add_argument("--after-xml", type=Path, required=True)
    ap.add_argument("--before-page", type=Path)
    ap.add_argument("--after-page", type=Path)
    ap.add_argument("--overlay", type=Path, action="append", default=[])
    ap.add_argument("--audio-before", type=Path)
    ap.add_argument("--audio-after", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    try:
        notes = json.loads(a.notes.read_text())["notes"]
        ops = json.loads((a.revise_dir / "ops.json").read_text())
        ch = a.revise_dir / "changes.json"
        items = json.loads(ch.read_text())["items"] if ch.exists() else []
        old, new = timeline.load_cut(a.before_xml), timeline.load_cut(a.after_xml)
        results, glob, unreq, report = qa(notes, ops, items, old, new, a.after_xml, a.out, before_page=a.before_page, after_page=a.after_page,
                                          overlays=a.overlay, audio={"before": a.audio_before, "after": a.audio_after},
                                          title=f"QA pass: {a.after_xml.stem}", before_xml=a.before_xml)
    except (timeline.TimelineError, OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    for r in results:
        print(f"note {r.note} [{fmt(r.time)}] {r.status}: {r.text[:70]}")
        for x in r.rows:
            print(f"    {x.status:19s} {x.op}: {x.detail}")
    print("\nWhole-cut checks:")
    for n, ok, d in glob:
        print(f"  [{'PASS' if ok else 'FAIL' if ok is False else 'INFO'}] {n}  {d}")
    print("\nChanges nobody asked for:", "none" if not unreq else "")
    for x in unreq:
        print(f"  {x}")
    counts = {s: sum(r.status == s for r in results) for s in RANK}
    print(f"\n{len(results)} notes: {counts[VERIFIED]} verified, {counts[UNMEASURED]} applied but not measurable, {counts[NOT_DONE]} not done, {counts[FAILED]} failed")
    print(f"report: {report}")
    if a.open:
        subprocess.run(["open", str(report)])
    return 1 if counts[FAILED] or any(ok is False for _n, ok, _d in glob) or unreq else 0


if __name__ == "__main__":
    sys.exit(main())
