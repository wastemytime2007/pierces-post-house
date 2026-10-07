"""Make the picture follow whoever is talking.

The recorder on each person says who is talking (speakers.py, framing.py). Where a clip's picture shows both people side by side and is punched in so only part of the width shows (Reel 3: an 8K
source in a 1080x1920 frame shows about a quarter of it), this splits the clip at each change of speaker and gives each piece a Basic Motion centre on the person talking. Sound is never touched:
only the video clip is split, the audio under it stays one piece, so lav sync cannot move.

  plan_follow(cut, op, cache)     analysis (slow the first time, cached per camera file): {"files": {path: {"people", "runs", "evidence"}}, "clips": [...]}
  apply_follow(seq, info, ...)    the XML step, run AFTER the cut edits so it works on the clips as they now are
  expected_horiz(...)             what a piece should read, shared by the checks
"""
from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET

import numpy as np
from pathlib import Path

import framing
import speakers as sp
from framing import FramingError
from timeline import TimelineError

FRAME_MIN_SEC = 1.0        # a change of speaker shorter than this ("mm-hm", a one-word answer) does not move the picture: it would read as the frame twitching


FACE_NEAR = 0.15           # a face found at a speaker's turn counts as that person only within this fraction of the width of where they usually stand
FACE_MAX_AGE = 30.0        # seconds: a piece with no face sample of its speaker this close in time falls back to where that person usually stands
FACE_MIN_RUN = 0.8         # runs shorter than this get no sample of their own


def sample_faces(path: str, runs: list, people: dict, sample_fn=None, faces_fn=None, limit: int = 24) -> list[list]:
    """[[camera_time, person, x]]: where the talking person's face actually is during each run of them talking: the median of three looks (a fifth, half and four fifths of the way through the
    run), each the face nearest where they usually stand. People lean and shift between turns, so one position per person leaves some pieces off-centre, and one look per run catches a lean."""
    import tempfile
    sample_fn, faces_fn = sample_fn or framing.sample_frames, faces_fn or framing.find_faces
    runs = [r for r in runs if r[1] - r[0] >= FACE_MIN_RUN]
    if len(runs) > limit:
        runs = runs[:: -(-len(runs) // limit)]
    if not runs:
        return []
    out = []
    with tempfile.TemporaryDirectory() as td:
        looks = [round(a + (b - a) * q, 2) for a, b, _w in runs for q in (0.2, 0.5, 0.8)]
        rows = faces_fn(sample_fn(path, looks, Path(td)))
        for i, (a, b, who) in enumerate(runs):
            xs = []
            for faces in rows[3 * i:3 * i + 3]:
                near = [f["x"] for f in faces if abs(f["x"] - people[who]) <= FACE_NEAR]
                if near:
                    xs.append(min(near, key=lambda x: abs(x - people[who])))
            if xs:
                out.append([round((a + b) / 2, 2), who, round(float(np.median(xs)), 4)])
    return out


def person_x(f: dict, who: str, t: float) -> float:
    """Where to centre on `who` at camera time t: the nearest face sample of them (within FACE_MAX_AGE), else where they usually stand."""
    near = [(abs(ft - t), x) for ft, w, x in f.get("faces", []) if w == who and abs(ft - t) <= FACE_MAX_AGE]
    return min(near)[1] if near else f["people"][who]


def mic_dir_of(cut) -> str | None:
    """The folder of each person's own recordings, read from the cut itself: the folder its lav (.wav) clips come from. Bob's recorder sits in the same folder as Mitch's even when only one of
    them is on the timeline."""
    from collections import Counter
    dirs = Counter(str(Path(a.src_path).parent) for a in cut.audio if a.src_path.lower().endswith(".wav"))
    return dirs.most_common(1)[0][0] if dirs else None


def plan_follow(cut, o: dict, cache: Path | None, progress=lambda s: None, analyse=None, sample_fn=None, faces_fn=None) -> dict:
    """Who is where and who talks when, for the clips the op names. Raises FramingError (in words) when it cannot."""
    analyse = analyse or framing.analyse
    mic_dir = o.get("mic_dir") or os.environ.get("POSTHOUSE_MIC_DIR") or mic_dir_of(cut)
    if cache is None:                                   # who stands where does not change between cuts of one camera file, so it is looked up once
        cache = Path(os.environ.get("POSTHOUSE_FRAMING_CACHE") or Path.home() / "Library" / "Application Support" / "Post House" / "framing_cache.json")
    if not mic_dir:
        raise FramingError("no folder of each person's own recordings is known for this project, so who is talking cannot be told")
    want = o.get("clips")
    clips = [c for c in cut.video if want in (None, "all") or c.idx in want]
    if not clips:
        raise FramingError("no clip to frame")
    files: dict[str, dict] = {}
    for path in sorted({c.src_path for c in clips}):
        mine = [c for c in clips if c.src_path == path]
        if not any(c.motion for c in mine):
            continue
        rows = [{"idx": c.idx, "src_in": c.src_in, "src_out": c.src_out, "fps": cut.fps} for c in mine]
        res = analyse(path, rows, mic_dir, cache, progress)
        runs = sorted((c.src_in + s, c.src_in + e, w) for c in mine for s, e, w in res["clips"][c.idx]["runs"])
        progress("Finding where each person's face is when they talk")
        files[path] = {"people": res["people"], "runs": [[round(a, 3), round(b, 3), w] for a, b, w in runs], "evidence": res["evidence"],
                       "faces": sample_faces(path, runs, res["people"], sample_fn, faces_fn)}
    if not files:
        raise FramingError("none of these clips has a scale and position set, so there is no punched-in window to move; set its framing first (labs/reframe)")
    return {"files": files, "clips": [c.idx for c in clips],
            "ranges": [[c.src_path, round(c.src_in, 3), round(c.src_out, 3)] for c in clips if c.src_path in files]}


def merge_short(pieces: list[tuple[int, int, str]], fps: float, min_sec: float = FRAME_MIN_SEC) -> list[tuple[int, int, str]]:
    """[(first_frame, frames, person)] with every piece shorter than min_sec taken into its longer neighbour, the shortest first (so a blink of the other speaker between two long pieces
    disappears, and two blinks in a row do not hand the picture to the second one); neighbours of one person then join."""
    out = [list(p) for p in pieces]
    floor = round(min_sec * fps)
    while len(out) > 1:
        i = min((i for i, p in enumerate(out) if p[1] < floor), key=lambda i: out[i][1], default=None)
        if i is None:
            break
        nb = [j for j in (i - 1, i + 1) if 0 <= j < len(out)]
        j = max(nb, key=lambda j: out[j][1])
        lo, hi = min(i, j), max(i, j)
        out[lo:hi + 1] = [[out[lo][0], out[lo][1] + out[hi][1], out[j][2]]]
        k = 0
        while k < len(out) - 1:
            if out[k][2] == out[k + 1][2]:
                out[k:k + 2] = [[out[k][0], out[k][1] + out[k + 1][1], out[k][2]]]
            else:
                k += 1
    return [tuple(p) for p in out]


def local_runs(runs: list, a: float, b: float) -> list[tuple[float, float, str]]:
    """The speaker runs inside camera time [a, b], as seconds from a."""
    out = []
    for s, e, w in runs:
        s2, e2 = max(s, a), min(e, b)
        if e2 - s2 > 0.05:
            out.append((s2 - a, e2 - a, w))
    return out


def expected_horiz(x_frac: float, scale: float, source_w: float, out_w: int) -> float:
    return framing.horiz_for_person(x_frac, scale, source_w, out_w)


def speaker_at(runs: list, t: float) -> str | None:
    """Who the runs say is talking at camera time t (None between runs)."""
    for s, e, w in runs:
        if s <= t < e:
            return w
    return None


def _set_horiz(el: ET.Element, horiz: float) -> bool:
    for f in el.findall("filter"):
        if f.findtext("effect/name") == "Basic Motion":
            for prm in f.findall("effect/parameter"):
                if prm.findtext("parameterid") == "center":
                    prm.find("value/horiz").text = "0" if abs(horiz) < 1e-9 else f"{horiz:.6f}"
                    return True
    return False


def _motion(el: ET.Element):
    for f in el.findall("filter"):
        if f.findtext("effect/name") == "Basic Motion":
            vals = {p.findtext("parameterid"): p for p in f.findall("effect/parameter")}
            try:
                return float(vals["scale"].findtext("value"))
            except (KeyError, TypeError, ValueError):
                return None
    return None


def apply_follow(seq: ET.Element, infos: list[dict], zone_f: int, seq_fps: float, out_w: int, path_of_file: dict[str, str], spf_of_file: dict[str, float],
                 source_dims, used_ids: set[str]) -> list[str]:
    """Split the cut's video clips (first video track, inside the cut zone) at each speaker change and centre each piece on its speaker. Returns one note per clip it left alone, in words."""
    track = seq.find("media/video/track")
    skipped: list[str] = []
    people_of, runs_of, files_of = {}, {}, {}
    for info in infos:
        for path, f in info["files"].items():
            people_of[path] = f["people"]
            runs_of.setdefault(path, []).extend(f["runs"])
            files_of[path] = f
    ranges = [r for info in infos for r in info["ranges"]]
    items = [c for c in track.findall("clipitem") if int(c.findtext("start")) < zone_f and c.findtext("in") is not None]
    items.sort(key=lambda c: int(c.findtext("start")))
    for el in items:
        fid = (el.find("file").get("id") if el.find("file") is not None else None)
        path = path_of_file.get(fid)
        if path not in people_of:
            continue
        scale = _motion(el)
        if scale is None:
            skipped.append(f"a clip of {Path(path).name} has no scale and position, so it was left alone")
            continue
        start, end, in0, out0 = int(el.findtext("start")), int(el.findtext("end")), int(el.findtext("in")), int(el.findtext("out"))
        if end - start < 2:
            continue
        spf = spf_of_file.get(fid) or 1.0 / seq_fps
        k = (out0 - in0) / (end - start)
        a, b = in0 * spf, out0 * spf
        if not any(rp == path and a < re_ and b > rs for rp, rs, re_ in ranges):          # a clip the op did not name keeps its framing
            continue
        runs = local_runs(runs_of[path], a, b)
        sw, _sh = source_dims(path)
        if framing.window_half_width(scale, sw, out_w) * 2 >= framing.NOTHING_TO_DO_WIDTH:
            skipped.append(f"a clip of {Path(path).name} already shows nearly the whole width, so there is no room to move")
            continue
        pieces = merge_short(sp.pieces_for_cut(runs, (end - start) / seq_fps, fps=seq_fps), seq_fps) if runs else []
        if not pieces:
            skipped.append(f"a clip of {Path(path).name} has no one talking in it that the recorders could place, so it was left alone")
            continue
        dur_text = el.findtext("duration")
        dur_is_len = dur_text is not None and int(dur_text) == end - start
        orig = copy.deepcopy(el)
        made = []
        for n, (f0, nf, who) in enumerate(pieces):
            if n == 0:
                p = el
            else:
                p = copy.deepcopy(orig)
                fl = p.find("file")
                if fl is not None:
                    for ch in list(fl):
                        fl.remove(ch)
                base, i = orig.get("id"), n
                while f"{base}-f{i}" in used_ids:
                    i += 1
                p.set("id", f"{base}-f{i}")
                used_ids.add(p.get("id"))
            last = n == len(pieces) - 1
            i_in = in0 + round(f0 * k)
            i_out = out0 if last else in0 + round((f0 + nf) * k)
            for tag, v in (("start", start + f0), ("end", start + f0 + nf), ("in", i_in), ("out", i_out)):
                p.find(tag).text = str(int(v))
            if dur_is_len:
                p.find("duration").text = str(int(nf))
            mid_t = (i_in + (i_out - i_in) / 2) * spf
            _set_horiz(p, expected_horiz(person_x(files_of[path], who, mid_t), scale, sw, out_w))
            made.append(p)
        pos = list(track).index(el)
        for j, p in enumerate(made[1:], 1):
            track.insert(pos + j, p)
    return skipped


def check_written(new_cut, info: dict, source_dims) -> tuple[bool, str]:
    """Read the finished cut back: every piece of a framed clip that sits where one person talks must carry that person's centre. Judged on camera time, from the recorders' runs, not on the plan."""
    judged = right = 0.0
    wrong: list[str] = []
    pieces = 0
    for path, a, b in info["ranges"]:
        f = info["files"][path]
        sw, _sh = source_dims(path)
        for c in new_cut.video:
            if c.src_path != path or not (c.src_in < b and c.src_out > a) or not c.motion:
                continue
            pieces += 1
            who = speaker_at(f["runs"], (c.src_in + c.src_out) / 2)
            if who is None:
                continue
            want = expected_horiz(person_x(f, who, (c.src_in + c.src_out) / 2), c.motion[0], sw, new_cut.width)
            d = c.src_out - c.src_in
            judged += d
            if abs(c.motion[1] - want) < 5e-4:
                right += d
            else:
                wrong.append(f"{c.src_in:.1f}s ({who}): {c.motion[1]:+.3f}, wanted {want:+.3f}")
    if judged <= 0:
        return False, "no piece of the framed clips sits where the recorders place a speaker"
    ok = right / judged >= 0.95
    return ok, f"{pieces} pieces; {right / judged:.0%} of the {judged:.1f}s where someone is talking is centred on that person" + ("" if ok else "; off: " + "; ".join(wrong[:4]))
