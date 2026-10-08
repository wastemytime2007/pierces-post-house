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

import sys

from ops import KEEP_SEC_DEFAULT, detect_pause, locate_end, locate_start, measure_head, measure_join, measure_tail
from timeline import Cut, TimelineError, _seq_for_cut

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reframe"))
REFRAME_STEP = 0.12          # one "lower" or "raise" moves the shot by this fraction of the height of the window it shows

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
    insertions are (at_sec, length_sec, change_index, clip_idx, side) of time added to a clip's end (side "end") or in front of its start (side "front").
    """
    changes: list[Change] = []
    spans: list[tuple[float, float, int]] = []
    insertions: list[tuple[float, float, int, int, str]] = []

    def clip_of(t: float) -> int:
        return next((c.idx for c in cut.video if c.tl_start <= t < c.tl_end), cut.video[-1].idx)

    joined: set[int] = set()                                      # seams (the lower clip's number) already put back by a fix in this pass

    def try_join(o: dict, n: int, kind: str, c, side: str) -> bool:
        """The reviewer's own end/start fix could not extend because the neighbour's footage is in the way: put the footage between the two clips back instead, if it is a short stretch of the
        same recording. One join repairs both edges of the seam, so the second note about the same seam is satisfied by the first and adds nothing."""
        j = measure_join(cut, c.idx, side)
        if "reason" in j:
            return False
        seam = c.idx if side == "next" else c.idx - 1
        way = "after" if side == "next" else "before"
        if seam in joined:
            changes.append(Change(n, kind, True, f"clip {c.idx} is already joined to the clip {way} it by another fix, so there is no cut left here", o.get("why", "")))
            return True
        joined.add(seam)
        insertions.append((c.tl_end if side == "next" else c.tl_start, j["ext"], len(changes), c.idx, "end" if side == "next" else "front"))
        changes.append(Change(n, kind, True,
                              f"joined clip {c.idx} to clip {j['other']}: the {j['ext']:.2f}s of the recording between them is back in, so what is being said runs on without a cut",
                              o.get("why", ""), check={"kind": "joined", "ext": j["ext"], "side": "end" if side == "next" else "front"}))
        return True

    def add(o: dict, s: float, e: float, what: str) -> None:
        spans.append((s, e, len(changes)))
        changes.append(Change(o["note"], o["op"], True,
                              f"removed {_fmt(s)}-{_fmt(e)} ({e - s:.2f}s) from clip {clip_of(s)}: {what}",
                              o.get("why", ""), (s, e)))

    for o in ops:
        n, kind = o["note"], o["op"]
        if kind == "unsupported":
            changes.append(Change(n, kind, False, o["reason"], o.get("why", "")))
        elif kind == "extend_graphic":
            changes.append(Change(n, kind, False, "keeping the graphic on screen longer is made by the graphics step (labs/overlay/change_callout.py), not on the timeline", o.get("why", "")))
        elif kind == "bleep_word":
            changes.append(Change(n, kind, False, "bleeping a word is made by the bleep step (labs/bleep/bleep.py, run by reconform), not on the timeline", o.get("why", "")))
        elif kind == "remove_graphic":
            changes.append(Change(n, kind, False, "taking the graphic out is made by the graphics step (labs/overlay/remove_graphic.py and reconform --drop), not on the timeline", o.get("why", "")))
        elif kind == "edit_caption":
            changes.append(Change(n, kind, False, f'changing the caption to "{o["text"]}" is made by the captions step (labs/captions/fix_caption.py), not on the timeline', o.get("why", "")))
        elif kind == "end_graphic":
            changes.append(Change(n, kind, False, "making the graphic fade out at this moment is made by the graphics step (labs/overlay/change_callout.py), not on the timeline", o.get("why", "")))
        elif kind == "edit_callout":
            changes.append(Change(n, kind, False, "changing the callout's words is made by the graphics step (labs/overlay/change_callout.py), not on the timeline", o.get("why", "")))
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
        elif kind == "reframe_vertical":
            from render_preview import source_dims
            import reframe_xml as rx
            c = cut.video[o["clip"] - 1]
            if not c.motion:
                changes.append(Change(n, kind, False, f"clip {c.idx} has no scale and position set, so there is nothing to move; set its framing first (labs/reframe)", o.get("why", "")))
            else:
                scale, _h, vert = c.motion
                sw, sh = source_dims(c.src_path)
                y0 = rx.subject_y_for_vert(vert, scale, sh)
                window = cut.height / (scale / 100.0)
                y1 = y0 + (-1 if o["direction"] == "lower" else 1) * REFRAME_STEP * window         # lower on screen = the window moves UP the source
                limited = ""
                lo, hi = window / 2, sh - window / 2
                if y1 < lo or y1 > hi:
                    y1, limited = min(max(y1, lo), hi), "; stopped at the edge of the picture"
                if abs(y1 - y0) < 1.0:
                    changes.append(Change(n, kind, False, f"clip {c.idx} is already at the {'top' if o['direction'] == 'lower' else 'bottom'} edge of its picture, so it cannot move {o['direction']}", o.get("why", "")))
                else:
                    shown = abs(y1 - y0) * scale / 100.0
                    v1 = rx.vert_for_subject(y1, scale, sh)
                    changes.append(Change(n, kind, True,
                                          f"clip {c.idx} moved {'down' if o['direction'] == 'lower' else 'up'} in the frame by {shown:.0f} px (one step, {REFRAME_STEP:.0%} of the shot's height{limited}); Position y should now read "
                                          f"{rx.expected_position_y(y1, scale, cut.height, sh):.0f} (it was {rx.expected_position_y(y0, scale, cut.height, sh):.0f}). This rests on the vertical rule that has not been confirmed in Premiere",
                                          o.get("why", ""), check={"kind": "motion", "clip": c.idx, "vert": v1, "vert_before": vert, "scale": scale, "direction": o["direction"], "src_path": c.src_path, "src_in": c.src_in}))
        elif kind == "follow_speaker":
            import follow_speaker as fs
            from framing import FramingError
            try:
                info = fs.plan_follow(cut, o, Path(o["cache"]) if o.get("cache") else None)
            except FramingError as e:
                changes.append(Change(n, kind, False, f"not framed on the speaker: {e}", o.get("why", "")))
            else:
                who = sorted({w for f in info["files"].values() for w in f["people"]})
                changes.append(Change(n, kind, True,
                                      f"the picture now follows whoever is talking ({' and '.join(who)}); {next(iter(info['files'].values()))['evidence']}",
                                      o.get("why", ""), check={"kind": "follow", **info}))
        elif kind == "extend_end":
            c = cut.video[o["clip"] - 1]
            m = measure_tail(cut, c.idx, o.get("max_sec", 1.0))
            if "reason" in m and o.get("trusted") and ("next clip's footage" in m["reason"] or "keeps going" in m["reason"]) and try_join(o, n, kind, c, "next"):
                continue
            if "reason" in m:
                changes.append(Change(n, kind, False, m["reason"], o.get("why", "")))
            else:
                insertions.append((c.tl_end, m["ext"], len(changes), c.idx, "end"))
                changes.append(Change(n, kind, True,
                                      f"extended clip {c.idx} by {m['ext']:.2f}s: the sound was still at {m['at_cut_db']:.0f} dB at the cut "
                                      f"and decays to room level {m['ext']:.2f}s later",
                                      o.get("why", ""), check={"kind": "quiet_at", "path": m["path"], "t": m["t_end"],
                                                               "thresh_db": m["thresh_db"], "ext": m["ext"]}))
        elif kind == "extend_start":
            c = cut.video[o["clip"] - 1]
            m = measure_head(cut, c.idx, o.get("max_sec", 1.0))
            if "reason" in m and o.get("trusted") and ("previous clip's footage" in m["reason"] or "keeps going" in m["reason"]) and try_join(o, n, kind, c, "prev"):
                continue
            if "reason" in m:
                changes.append(Change(n, kind, False, m["reason"], o.get("why", "")))
            else:
                insertions.append((c.tl_start, m["ext"], len(changes), c.idx, "front"))
                changes.append(Change(n, kind, True,
                                      f"started clip {c.idx} {m['ext']:.2f}s earlier: the sound was already at {m['at_cut_db']:.0f} dB at the old start "
                                      f"and begins {m['ext']:.2f}s before it, from room level",
                                      o.get("why", ""), check={"kind": "quiet_from", "path": m["path"], "t": m["t_start"],
                                                               "thresh_db": m["thresh_db"], "ext": m["ext"]}))
        elif kind == "end_at_words":
            c = cut.video[o["clip"] - 1]
            m = locate_end(cut, c.idx, o["words"], o.get("reach", 8.0), o.get("max_trim", 6.0))
            if "reason" in m:
                changes.append(Change(n, kind, False, m["reason"], o.get("why", "")))
            else:
                s, e = c.tl_end - m["trim"], c.tl_end
                spans.append((s, e, len(changes)))
                dropped = f' dropped "{m["dropped"]}",' if m["dropped"] else ""
                changes.append(Change(n, kind, True, f'clip {c.idx} now ends after "{m["at_word"]}" ({o["words"]}):{dropped} {m["trim"]:.2f}s trimmed from its end, cut placed at the quietest point after the word',
                                      o.get("why", ""), (s, e)))
        elif kind == "move_clip":
            c, m = cut.video[o["clip"] - 1], cut.video[o["before"] - 1]
            if c.tl_start < m.tl_start:
                changes.append(Change(n, kind, False, f"clip {c.idx} already plays before clip {m.idx}", o.get("why", "")))
            else:
                changes.append(Change(n, kind, True, f"clip {c.idx} moved to play before clip {m.idx}, with its picture, voice and framing", o.get("why", ""),
                                      check={"kind": "move", "src_path": c.src_path, "src_in": c.src_in, "src_out": c.src_out, "before_path": m.src_path, "before_in": m.src_in, "before_out": m.src_out}))
        elif kind == "start_at_words":
            c = cut.video[o["clip"] - 1]
            m = locate_start(cut, c.idx, o["words"], o.get("reach", 6.0), o.get("max_trim", 3.0))
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


def _edit_clip(c: ET.Element, removed, shift, used_ids: set[str], extend_at: dict[int, int], extend_front_at: dict[int, int] | None = None) -> list[ET.Element]:
    start, end = int(c.findtext("start")), int(c.findtext("end"))
    in0, out0 = int(c.findtext("in")), int(c.findtext("out"))
    k = (out0 - in0) / (end - start) if end > start else 1.0
    dur_text = c.findtext("duration")
    dur_is_len = dur_text is not None and int(dur_text) == end - start
    orig = copy.deepcopy(c)
    out: list[ET.Element] = []
    for n, (a, b) in enumerate(_pieces(start, end, removed)):
        grow = extend_at.get(end, 0) if b == end else 0
        growf = (extend_front_at or {}).get(start, 0) if (n == 0 and a == start) else 0          # time added in front of the clip's first piece: it starts earlier in the source and earlier on the timeline
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
        in_new = in0 + round((a - start) * k) - round(growf * k)
        _set(el, "start", shift(a) - growf)                                  # shift() moved this start by the inserted time; the clip's own front growth takes it back
        _set(el, "end", shift(a) + (b - a) + grow)
        _set(el, "in", in_new)
        _set(el, "out", in_new + round((b - a + grow + growf) * k))
        if dur_is_len:
            _set(el, "duration", b - a + grow + growf)
        out.append(el)
    return out


def _trim_pool(groups: dict, file_id: str, old_out: float, new_out: float, fps: float, spf: float | None = None) -> float:
    """Pool = complement of the cut. When a clip grows into footage the pool also holds, take that
    footage off the front of the pool clip (video and its audio together). Returns seconds trimmed.

    `spf` is the seconds one source frame of this file lasts in the time base the clips' source times were READ in (cut.video[].src_in/out). Without it the pool is converted at the
    sequence's frame rate, which for a 29.97 file in a 30 fps sequence puts the pool 0.1% away from the clips it is compared with (0.37 s at 12 minutes in: footage the pool never held
    looked shared, and the pool was trimmed for it)."""
    trimmed = 0.0
    for (s, e), els in groups.items():
        v = next((el for kind, el in els if kind == "video" and el.find("file") is not None and el.find("file").get("id") == file_id), None)
        if v is None:
            continue
        k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
        a, b = (int(v.findtext("in")) * spf, int(v.findtext("out")) * spf) if spf else (int(v.findtext("in")) / (k * fps), int(v.findtext("out")) / (k * fps))
        if not (a < new_out and b > old_out):
            continue
        cut_f = round((max(a, new_out) - a) / spf / k) if spf else round((max(a, new_out) - a) * fps)
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


def _pool_overlaps(groups: dict, file_id: str, lo: float, hi: float, fps: float, spf: float | None = None) -> bool:
    """True when a selects-pool clip of this file holds any footage between source seconds lo and hi."""
    for (s, e), els in groups.items():
        v = next((el for kind, el in els if kind == "video" and el.find("file") is not None and el.find("file").get("id") == file_id), None)
        if v is None or e <= s:
            continue
        k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
        a, b = (int(v.findtext("in")) * spf, int(v.findtext("out")) * spf) if spf else (int(v.findtext("in")) / (k * fps), int(v.findtext("out")) / (k * fps))
        if a < hi and b > lo:
            return True
    return False


def _pool_tail_ok(groups: dict, file_id: str, lo: float, hi: float, fps: float, spf: float | None = None) -> bool:
    """True when every selects-pool clip of this file that holds footage between lo and hi only reaches into that stretch with its END (it starts before lo), so taking its tail off gives the cut
    the footage without the pool losing a whole clip."""
    for (s, e), els in groups.items():
        v = next((el for kind, el in els if kind == "video" and el.find("file") is not None and el.find("file").get("id") == file_id), None)
        if v is None or e <= s:
            continue
        k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
        a, b = (int(v.findtext("in")) * spf, int(v.findtext("out")) * spf) if spf else (int(v.findtext("in")) / (k * fps), int(v.findtext("out")) / (k * fps))
        if a < hi and b > lo and not (a < lo - 0.5):
            return False
    return True


def _trim_pool_tail(groups: dict, file_id: str, lo: float, hi: float, fps: float, spf: float | None = None) -> float:
    """Take the tail off each selects-pool clip of this file that runs into [lo, hi), so it ends at lo (video and its audio together). Returns the most seconds taken."""
    trimmed = 0.0
    for (s, e), els in groups.items():
        v = next((el for kind, el in els if kind == "video" and el.find("file") is not None and el.find("file").get("id") == file_id), None)
        if v is None or e <= s:
            continue
        k = (int(v.findtext("out")) - int(v.findtext("in"))) / (e - s)
        a, b = (int(v.findtext("in")) * spf, int(v.findtext("out")) * spf) if spf else (int(v.findtext("in")) / (k * fps), int(v.findtext("out")) / (k * fps))
        if not (a < hi and b > lo):
            continue
        cut_f = round((b - lo) / spf / k) if spf else round((b - lo) * fps)
        if cut_f <= 0:
            continue
        if cut_f >= e - s:
            raise TimelineError("starting this clip earlier would consume a whole selects-pool clip; resolve by hand")
        for _kind, el in els:
            ks = (int(el.findtext("out")) - int(el.findtext("in"))) / (e - s)
            dur = el.findtext("duration")
            _set(el, "out", int(el.findtext("out")) - round(cut_f * ks))
            _set(el, "end", int(el.findtext("end")) - cut_f)
            if dur is not None and int(dur) == e - s:
                _set(el, "duration", e - s - cut_f)
        trimmed = max(trimmed, cut_f / fps)
    return trimmed


def _set_aside_conflicts(xml_path: Path, cut: Cut, changes: list[Change], insertions: list, removed: list[tuple[int, int]], zone_f: int, fps: float) -> list:
    """The fixes that cannot be made together with the others are set aside one by one, each with its reason, and the rest are made. Before this, one conflicting fix (an extension into a trimmed edge, two
    extensions of one edge, an extension into footage the selects pool also holds) refused the whole revision, and the editor stopped on a single fix it could have left. Returns the insertions that stand."""
    tree = ET.parse(xml_path)
    seq = _seq_for_cut(tree.getroot())
    vids = sorted(seq.find("media/video/track").findall("clipitem"), key=lambda c: int(c.findtext("start")))
    clip_file_id = {i + 1: c.find("file").get("id") for i, c in enumerate(vids[:len(cut.video)])}
    spf_of_file: dict[str, float] = {}
    for i, el in enumerate(vids[:len(cut.video)]):
        in_f = int(el.findtext("in") or 0)
        if in_f > 0 and cut.video[i].src_in > 0:
            spf_of_file.setdefault(el.find("file").get("id"), cut.video[i].src_in / in_f)
    pool_groups: dict[tuple[int, int], list] = {}
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            for c in track.findall("clipitem"):
                if int(c.findtext("start")) >= zone_f:
                    pool_groups.setdefault((int(c.findtext("start")), int(c.findtext("end"))), []).append((kind, c))
    keep, seen = [], set()
    # every clip's source range as the fixes kept so far leave it; a clip the pass removes outright no longer holds footage
    gone = {c.idx for c in cut.video if any(r0 <= round(c.tl_start * fps) and round(c.tl_end * fps) <= r1 for r0, r1 in removed)}
    ranges = {c.idx: [c.src_in, c.src_out] for c in cut.video if c.idx not in gone}
    slack = 1.5 / fps
    for ins in insertions:
        at, ln, idx, clip_idx, side = ins
        pos_f = round(at * fps)
        why = None
        c0 = cut.video[clip_idx - 1]
        new = ([ranges[clip_idx][0], ranges[clip_idx][1] + ln] if side == "end" else [ranges[clip_idx][0] - ln, ranges[clip_idx][1]]) if clip_idx in ranges else None
        clash = None
        if new:
            for other in cut.video:
                if other.idx == clip_idx or other.idx not in ranges or other.src_path != c0.src_path:
                    continue
                a, b = ranges[other.idx]
                if new[0] < b - slack and a + slack < new[1] and not (c0.src_in < b - slack and a + slack < c0.src_out):
                    clash = other.idx
                    break
        if side == "end" and any(r0 < pos_f and r1 >= pos_f - 1 for r0, r1 in removed):
            why = f"clip {clip_idx}'s end is also being trimmed by another change in this pass, so it was not extended"
        elif side == "front" and any(r0 <= pos_f < r1 for r0, r1 in removed):
            why = f"clip {clip_idx}'s start is also being trimmed by another change in this pass, so it was not started earlier"
        elif (pos_f, side) in seen:
            why = f"another change in this pass already {'extends the end' if side == 'end' else 'starts earlier'} the same clip (clip {clip_idx}), so this one adds nothing"
        elif clash is not None:
            why = (f"{'extending clip %d' % clip_idx if side == 'end' else 'starting clip %d earlier' % clip_idx} would take footage clip {clash} also shows "
                   f"(another change in this pass already put that stretch back), so it was not made")
        elif side == "front":
            c0 = cut.video[clip_idx - 1]
            fid, spf = clip_file_id[clip_idx], spf_of_file.get(clip_file_id[clip_idx])
            if _pool_overlaps(pool_groups, fid, c0.src_in - ln, c0.src_in, fps, spf) and not _pool_tail_ok(pool_groups, fid, c0.src_in - ln, c0.src_in, fps, spf):
                why = f"starting clip {clip_idx} earlier would take a whole clip out of the selects pool, so it was not made (it needs a decision by hand)"
        if why:
            changes[idx].applied, changes[idx].summary, changes[idx].check = False, why, None
        else:
            seen.add((pos_f, side))
            keep.append(ins)
            if new:
                ranges[clip_idx] = new
    return keep


def _move_clip(seq: ET.Element, k: dict, zone_f: int, fps: float, path_of: dict, spf_of: dict, used_ids: set[str]) -> str | None:
    """Move one clip (as the cut now is: it may have been trimmed or split into pieces) to play just before another, carrying every track with it: the stretch of the timeline the clip occupies and
    the stretch from the other clip to it swap places, so nothing is lost, nothing overlaps and the length is unchanged. Returns a reason when it cannot, else None."""
    from follow_speaker import _split_at
    v1 = sorted([c for c in seq.find("media/video/track").findall("clipitem") if c.findtext("in") is not None and int(c.findtext("start")) < zone_f], key=lambda c: int(c.findtext("start")))

    def run_of(path: str, a: float, b: float) -> tuple[int, int] | None:
        hits = []
        for c in v1:
            fid = c.find("file").get("id") if c.find("file") is not None else None
            if path_of.get(fid) != path:
                continue
            spf = spf_of.get(fid) or 1.0 / fps
            mid = (int(c.findtext("in")) + int(c.findtext("out"))) / 2 * spf
            if a - 0.6 <= mid <= b + 0.6:
                hits.append(c)
        if not hits:
            return None
        return int(hits[0].findtext("start")), int(hits[-1].findtext("end"))
    seg, tgt = run_of(k["src_path"], k["src_in"], k["src_out"]), run_of(k["before_path"], k["before_in"], k["before_out"])
    if not seg or not tgt:
        return "the clip to move, or the one it should come before, is no longer in the cut after the other changes, so nothing was moved"
    a, b, t0 = seg[0], seg[1], tgt[0]
    if a <= t0:
        return "that clip already plays before the other one after the other changes, so nothing was moved"
    tracks = seq.findall("media/video/track") + seq.findall("media/audio/track")
    for tr in tracks:
        for el in [c for c in tr.findall("clipitem") if c.findtext("in") is not None and int(c.findtext("start")) < zone_f]:
            _split_at(el, tr, [t0, a, b], used_ids)
    span, gap = b - a, a - t0
    for tr in tracks:
        items = [c for c in tr.findall("clipitem") if c.findtext("in") is not None and int(c.findtext("start")) < zone_f]
        for el in items:
            s, e = int(el.findtext("start")), int(el.findtext("end"))
            d = -gap if a <= s < b else span if t0 <= s < a else 0
            if d:
                el.find("start").text, el.find("end").text = str(s + d), str(e + d)
        kids = list(tr)
        clips = sorted([c for c in kids if c.tag == "clipitem"], key=lambda c: int(c.findtext("start")))
        rest = [c for c in kids if c.tag != "clipitem"]
        for c in kids:
            tr.remove(c)
        for c in clips + rest:
            tr.append(c)
    return None


def apply_ops(xml_path: Path, out_xml: Path, cut: Cut, ops: list[dict], notes: list[dict]) -> tuple[list[Change], float]:
    fps = cut.fps
    changes, spans, insertions = plan(cut, ops, notes)
    zone_f = round(cut.zone_end * fps)
    removed = _merge([(max(0, round(s * fps)), min(zone_f, round(e * fps))) for s, e, _ in spans if round(e * fps) > round(s * fps)])
    if zone_f - sum(b - a for a, b in removed) < round(fps):
        raise TimelineError("these operations would remove the whole cut (under 1s would be left)")
    insertions = _set_aside_conflicts(xml_path, cut, changes, insertions, removed, zone_f, fps)
    inserted_s = [(round(at * fps), max(1, round(ln * fps)), side) for at, ln, _i, _c, side in insertions]
    inserted = [(p, ln) for p, ln, _sd in inserted_s]                      # the shift counts every insertion at or before a frame, front or end
    shift = _shift_fn(removed, inserted)
    for s, _e, idx in spans:
        changes[idx].v2_time = shift(round(s * fps)) / fps
    for (at, _ln, idx, _clip, side), (pos_f, ln_f, _sd) in zip(insertions, inserted_s):
        changes[idx].v2_time = (shift(pos_f) - (ln_f if side == "front" else 0)) / fps
    extend_at = {p: ln for p, ln, sd in inserted_s if sd == "end"}
    extend_front_at = {p: ln for p, ln, sd in inserted_s if sd == "front"}

    tree = ET.parse(xml_path)
    root = tree.getroot()
    seq = _seq_for_cut(root)
    for ch in changes:                                                 # a reframe is written BEFORE the cut edits, so every piece a clip is split into carries it
        if ch.applied and ch.check and ch.check.get("kind") == "motion":
            ci = sorted(seq.find("media/video/track").findall("clipitem"), key=lambda c: int(c.findtext("start")))[ch.check["clip"] - 1]      # the cut is the FIRST video track, as load_cut reads it; an overlay on V2 must not shift the count
            done = False
            for f in ci.findall("filter"):
                if f.findtext("effect/name") == "Basic Motion":
                    for prm in f.findall("effect/parameter"):
                        if prm.findtext("parameterid") == "center":
                            prm.find("value/vert").text = "0" if abs(ch.check["vert"]) < 1e-9 else f"{ch.check['vert']:.6f}"
                            done = True
            if not done:
                raise TimelineError(f"clip {ch.check['clip']}: no Basic Motion centre to change in the XML")
    defs_full = {f.get("id"): copy.deepcopy(f) for f in root.iter("file") if f.get("id") and len(list(f)) > 0}
    used_ids = {el.get("id") for el in root.iter("clipitem") if el.get("id")}
    vids = sorted(seq.find("media/video/track").findall("clipitem"), key=lambda c: int(c.findtext("start")))
    clip_file_id = {i + 1: c.find("file").get("id") for i, c in enumerate(vids[:len(cut.video)])}
    spf_of_file: dict[str, float] = {}                                      # seconds per source frame, in the time base the cut's clips were read in (see _trim_pool)
    for i, el in enumerate(vids[:len(cut.video)]):
        in_f = int(el.findtext("in") or 0)
        if in_f > 0 and cut.video[i].src_in > 0:
            spf_of_file.setdefault(el.find("file").get("id"), cut.video[i].src_in / in_f)
    pool_groups: dict[tuple[int, int], list] = {}
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            for c in track.findall("clipitem"):
                if int(c.findtext("start")) >= zone_f:
                    pool_groups.setdefault((int(c.findtext("start")), int(c.findtext("end"))), []).append((kind, c))

    follows = [ch for ch in changes if ch.applied and ch.check and ch.check.get("kind") == "follow"]
    for kind in ("video", "audio"):
        for track in seq.findall(f"media/{kind}/track"):
            # a still with no source in/out (PreCut's "SAFE ZONE OVERLAY" guide on V2) is not footage in the cut: it has nothing to ripple, so it is left exactly as it is
            zone = [c for c in track.findall("clipitem") if int(c.findtext("start")) < zone_f and c.findtext("in") is not None and c.findtext("out") is not None]
            if not zone or not (removed or inserted):
                continue
            pos = list(track).index(zone[0])
            for c in zone:
                track.remove(c)
            repl: list[ET.Element] = []
            for c in zone:
                repl += _edit_clip(c, removed, shift, used_ids, extend_at, extend_front_at)
            for i, c in enumerate(repl):
                track.insert(pos + i, c)

    if follows:                                                          # AFTER the cut edits, so it frames the clips as they now are; only video is split, the audio under it stays whole
        import follow_speaker as fs
        from render_preview import source_dims
        path_of_file = {clip_file_id[c.idx]: c.src_path for c in cut.video if c.idx in clip_file_id}
        new_zone_f = zone_f - sum(b - a for a, b in removed) + sum(ln for _p, ln in inserted)
        for note_ in fs.apply_follow(seq, [ch.check for ch in follows], new_zone_f, fps, cut.width, path_of_file, spf_of_file, source_dims, used_ids, root):
            for ch in follows:
                ch.summary += f"; {note_}"

    moves = [ch for ch in changes if ch.applied and ch.check and ch.check.get("kind") == "move"]
    if moves:                                                            # AFTER the cut edits and the framing: a clip moves as it now is, every track with it
        path_of = {clip_file_id[c.idx]: c.src_path for c in cut.video if c.idx in clip_file_id}
        new_zone = zone_f - sum(b - a for a, b in removed) + sum(ln for _p, ln in inserted)
        for ch in moves:
            why = _move_clip(seq, ch.check, new_zone, fps, path_of, spf_of_file, used_ids)
            if why:
                ch.applied, ch.summary, ch.check = False, why, None

    for (at, ln, idx, clip_idx, side) in insertions:
        if side == "front":                                                 # footage in front of the clip must not also be in the selects pool (it would play twice)
            c0 = cut.video[clip_idx - 1]
            fid, spf = clip_file_id[clip_idx], spf_of_file.get(clip_file_id[clip_idx])
            if _pool_overlaps(pool_groups, fid, c0.src_in - ln, c0.src_in, fps, spf):
                took = _trim_pool_tail(pool_groups, fid, c0.src_in - ln, c0.src_in, fps, spf)
                if took:
                    changes[idx].summary += (f"; also took {took:.2f}s off the end of the selects-pool clip that held the same footage, so the pool still never repeats the cut")
            continue
        old_out = cut.video[clip_idx - 1].src_out
        try:
            trimmed = _trim_pool(pool_groups, clip_file_id[clip_idx], old_out, old_out + ln, fps, spf_of_file.get(clip_file_id[clip_idx]))
        except TimelineError as e:
            raise TimelineError(f"{e} (note {changes[idx].note})") from e                  # the note is named, so the app can set that one fix aside and make the rest
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
