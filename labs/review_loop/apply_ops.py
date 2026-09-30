"""Apply operations to an export XML.

Only the cut zone changes: removals ripple-close inside it, and the selects
pool to the right is left exactly where it was (so the gap only grows).

Frame conventions are not guessed. Each clipitem's own numbers give the ratio
of source frames to sequence frames, k = (out-in)/(end-start): 1 for the lav
(sequence frames) and file_fps/seq_fps for video, so one rule covers both.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from ops import KEEP_SEC_DEFAULT, detect_pause, locate_start, measure_tail
from timeline import Cut, TimelineError, _seq_for_cut

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


@dataclass
class Change:
    note: int
    op: str
    applied: bool
    summary: str
    why: str = ""
    removed: tuple[float, float] | None = None   # V1 timeline seconds
    v2_time: float | None = None                 # where it lands on V2
    check: dict | None = None                    # what to re-verify on the finished render


def _fmt(s: float) -> str:
    m = int(s // 60)
    return f"{m:02d}:{s - m * 60:04.1f}"


def _merge(ivs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for a, b in sorted(ivs):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _shift_fn(removed: list[tuple[int, int]], inserted: list[tuple[int, int]] | None = None):
    """Old timeline frame -> new: closes removed spans, opens room for time added at a clip's end."""
    inserted = inserted or []

    def shift(t: int) -> int:
        return (t - sum(max(0, min(r1, t) - r0) for r0, r1 in removed if t > r0)
                + sum(length for pos, length in inserted if pos <= t))
    return shift


def _pieces(start: int, end: int, removed: list[tuple[int, int]]) -> list[tuple[int, int]]:
    cur, out = start, []
    for r0, r1 in removed:
        if r1 <= cur or r0 >= end:
            continue
        if r0 > cur:
            out.append((cur, min(r0, end)))
        cur = max(cur, r1)
        if cur >= end:
            break
    if cur < end:
        out.append((cur, end))
    return [(a, b) for a, b in out if b - a >= 1]


def plan(cut: Cut, ops: list[dict], notes: list[dict]):
    """Decide what to remove and what to add.

    Returns (changes, spans, insertions): spans are (start_sec, end_sec, change_index) to remove;
    insertions are (at_sec, length_sec, change_index, clip_idx) of time added to a clip's end.
    """
    changes: list[Change] = []
    spans: list[tuple[float, float, int]] = []
    insertions: list[tuple[float, float, int, int]] = []

    def clip_of(t: float) -> int:
        return next((c.idx for c in cut.video if c.tl_start <= t < c.tl_end), cut.video[-1].idx)

    def add(o: dict, s: float, e: float, what: str) -> None:
        spans.append((s, e, len(changes)))
        changes.append(Change(o["note"], o["op"], True,
                              f"removed {_fmt(s)}-{_fmt(e)} ({e - s:.2f}s) from clip {clip_of(s)}: {what}",
                              o.get("why", ""), (s, e)))

    for o in ops:
        n, kind = o["note"], o["op"]
        if kind == "unsupported":
            changes.append(Change(n, kind, False, o["reason"], o.get("why", "")))
        elif kind == "replace_sfx":
            changes.append(Change(n, kind, False, f'sound effect change to "{o["sound"]}" is made by the audio step (labs/audio/replace_sfx.py), not on the timeline', o.get("why", "")))
        elif kind == "tighten_pause":
            p = detect_pause(cut, o["at"])
            keep = o.get("keep_sec", KEEP_SEC_DEFAULT)
            if p is None:
                changes.append(Change(n, kind, False, f"no pause found near {_fmt(o['at'])} in the audio, so nothing was changed", o.get("why", "")))
            elif (p.end - p.start) - keep < 0.15:
                changes.append(Change(n, kind, False, f"the pause at {_fmt(p.start)} is only {p.end - p.start:.2f}s, already tight", o.get("why", "")))
            else:
                add(o, p.start + keep / 2, p.end - keep / 2,
                    f"measured a {p.end - p.start:.2f}s silence, kept {keep:.2f}s of it")
        elif kind == "remove_range":
            add(o, o["start"], o["end"], "range stated in the note")
        elif kind in ("trim_start", "trim_end"):
            c = cut.video[o["clip"] - 1]
            s, e = ((c.tl_start, c.tl_start + o["seconds"]) if kind == "trim_start"
                    else (c.tl_end - o["seconds"], c.tl_end))
            add(o, s, e, f"{kind.replace('_', ' ')} by {o['seconds']:g}s as the note asked")
        elif kind == "drop_clip":
            c = cut.video[o["clip"] - 1]
            add(o, c.tl_start, c.tl_end, f"clip {c.idx} dropped as the note asked")
        elif kind == "extend_end":
            c = cut.video[o["clip"] - 1]
            m = measure_tail(cut, c.idx, o.get("max_sec", 1.0))
            if "reason" in m:
                changes.append(Change(n, kind, False, m["reason"], o.get("why", "")))
            else:
                insertions.append((c.tl_end, m["ext"], len(changes), c.idx))
                changes.append(Change(n, kind, True,
                                      f"extended clip {c.idx} by {m['ext']:.2f}s: the sound was still at {m['at_cut_db']:.0f} dB at the cut "
                                      f"and decays to room level {m['ext']:.2f}s later",
                                      o.get("why", ""), check={"kind": "quiet_at", "path": m["path"], "t": m["t_end"],
                                                               "thresh_db": m["thresh_db"], "ext": m["ext"]}))
        elif kind == "start_at_words":
            c = cut.video[o["clip"] - 1]
            m = locate_start(cut, c.idx, o["words"])
            if "reason" in m:
                changes.append(Change(n, kind, False, m["reason"], o.get("why", "")))
            else:
                s, e = c.tl_start, c.tl_start + m["trim"]
                spans.append((s, e, len(changes)))
                dropped = f" dropped \"{m['dropped']}\"," if m["dropped"] else ""
                changes.append(Change(n, kind, True,
                                      f"clip {c.idx} now starts at \"{m['at_word']}\" ({o['words']}):{dropped} {m['trim']:.2f}s trimmed from its start, "
                                      f"cut placed at the quietest point before the word",
                                      o.get("why", ""), (s, e), check={"kind": "seam_text", "phrase": o["words"]}))
    return changes, spans, insertions


def _set(el: ET.Element, tag: str, val) -> None:
    child = el.find(tag)
    if child is None:
        raise TimelineError(f"clipitem {el.get('id')} has no <{tag}>")
    child.text = str(int(val))


def _edit_clip(c: ET.Element, removed, shift, used_ids: set[str], extend_at: dict[int, int]) -> list[ET.Element]:
    start, end = int(c.findtext("start")), int(c.findtext("end"))
    in0, out0 = int(c.findtext("in")), int(c.findtext("out"))
    k = (out0 - in0) / (end - start) if end > start else 1.0
    dur_text = c.findtext("duration")
    dur_is_len = dur_text is not None and int(dur_text) == end - start
    orig = copy.deepcopy(c)
    out: list[ET.Element] = []
    for n, (a, b) in enumerate(_pieces(start, end, removed)):
        grow = extend_at.get(end, 0) if b == end else 0
        if n == 0:
            el = c
        else:
            el = copy.deepcopy(orig)
            f = el.find("file")
            if f is not None:                       # a file body may be defined once only
                for ch in list(f):
                    f.remove(ch)
            base, i = orig.get("id"), n
            while f"{base}-r{i}" in used_ids:
                i += 1
            el.set("id", f"{base}-r{i}")
            used_ids.add(el.get("id"))
        in_new = in0 + round((a - start) * k)
        _set(el, "start", shift(a))
        _set(el, "end", shift(a) + (b - a) + grow)
        _set(el, "in", in_new)
        _set(el, "out", in_new + round((b - a + grow) * k))
        if dur_is_len:
            _set(el, "duration", b - a + grow)
        out.append(el)
    return out


def _trim_pool(groups: dict, file_id: str, old_out: float, new_out: float, fps: float) -> float:
    """Pool = complement of the cut. When a clip grows into footage the pool also holds, take that
    footage off the front of the pool clip (video and its audio together). Returns seconds trimmed."""
    trimmed = 0.0
    for (s, e), els in groups.items():
        v = next((el for kind, el in els if kind == "video" and el.find("file") is not None and el.find("file").get("id") == file_id), None)
        if v is None:
            continue
        k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
        a, b = int(v.findtext("in")) / (k * fps), int(v.findtext("out")) / (k * fps)
        if not (a < new_out and b > old_out):
            continue
        cut_f = round((max(a, new_out) - a) * fps)
        if cut_f <= 0:
            continue
        if cut_f >= e - s:
            raise TimelineError("extending this clip would consume a whole selects-pool clip; resolve by hand")
        for _kind, el in els:
            ks = (int(el.findtext("out")) - int(el.findtext("in"))) / (e - s)
            dur = el.findtext("duration")
            _set(el, "in", int(el.findtext("in")) + round(cut_f * ks))
            _set(el, "end", int(el.findtext("end")) - cut_f)
            if dur is not None and int(dur) == e - s:
                _set(el, "duration", e - s - cut_f)
        trimmed = max(trimmed, cut_f / fps)
    return trimmed


def apply_ops(xml_path: Path, out_xml: Path, cut: Cut, ops: list[dict], notes: list[dict]) -> tuple[list[Change], float]:
    fps = cut.fps
    changes, spans, insertions = plan(cut, ops, notes)
    zone_f = round(cut.zone_end * fps)
    removed = _merge([(max(0, round(s * fps)), min(zone_f, round(e * fps))) for s, e, _ in spans if round(e * fps) > round(s * fps)])
    if zone_f - sum(b - a for a, b in removed) < round(fps):
        raise TimelineError("these operations would remove the whole cut (under 1s would be left)")
    inserted = [(round(at * fps), max(1, round(ln * fps))) for at, ln, _i, _c in insertions]
    for pos_f, _ln in inserted:
        if any(r0 < pos_f and r1 >= pos_f - 1 for r0, r1 in removed):
            raise TimelineError("cannot extend the end of a clip whose end is also being trimmed by another note; resolve the two notes first")
    if len({p for p, _ in inserted}) != len(inserted):
        raise TimelineError("two notes both extend the same clip end")
    shift = _shift_fn(removed, inserted)
    for s, _e, idx in spans:
        changes[idx].v2_time = shift(round(s * fps)) / fps
    for (at, _ln, idx, _clip), (pos_f, _l) in zip(insertions, inserted):
        changes[idx].v2_time = shift(pos_f) / fps
    extend_at = dict(inserted)

    tree = ET.parse(xml_path)
    root = tree.getroot()
    seq = _seq_for_cut(root)
    defs_full = {f.get("id"): copy.deepcopy(f) for f in root.iter("file") if f.get("id") and len(list(f)) > 0}
    used_ids = {el.get("id") for el in root.iter("clipitem") if el.get("id")}
    vids = sorted(seq.findall("media/video/track/clipitem"), key=lambda c: int(c.findtext("start")))
    clip_file_id = {i + 1: c.find("file").get("id") for i, c in enumerate(vids[:len(cut.video)])}
    pool_groups: dict[tuple[int, int], list] = {}
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            for c in track.findall("clipitem"):
                if int(c.findtext("start")) >= zone_f:
                    pool_groups.setdefault((int(c.findtext("start")), int(c.findtext("end"))), []).append((kind, c))

    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            zone = [c for c in track.findall("clipitem") if int(c.findtext("start")) < zone_f]
            if not zone or not (removed or inserted):
                continue
            pos = list(track).index(zone[0])
            for c in zone:
                track.remove(c)
            repl: list[ET.Element] = []
            for c in zone:
                repl += _edit_clip(c, removed, shift, used_ids, extend_at)
            for i, c in enumerate(repl):
                track.insert(pos + i, c)

    for (at, ln, idx, clip_idx) in insertions:
        old_out = cut.video[clip_idx - 1].src_out
        trimmed = _trim_pool(pool_groups, clip_file_id[clip_idx], old_out, old_out + ln, fps)
        if trimmed:
            changes[idx].summary += (f"; also took {trimmed:.2f}s off the front of the selects-pool clip that held the same "
                                     "footage, so the pool still never repeats the cut")

    for m in seq.findall("marker"):
        t = int(m.findtext("in") or 0)
        if 0 < t < zone_f:
            m.find("in").text = str(shift(t))

    present = {f.get("id") for f in root.iter("file") if len(list(f)) > 0}
    for fid, body in defs_full.items():
        if fid not in present:
            for f in root.iter("file"):
                if f.get("id") == fid:
                    for ch in list(body):
                        f.append(copy.deepcopy(ch))
                    break

    ET.indent(root, space="\t")
    out_xml.write_text(HEADER + ET.tostring(root, encoding="unicode") + "\n")
    return changes, (sum(ln for _p, ln in inserted) - sum(b - a for a, b in removed)) / fps
