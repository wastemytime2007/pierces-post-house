"""Frame each shot on whoever is talking.

A two-person interview shot on one wide camera is cut into a tall picture (1080x1920): only a narrow vertical slice of the wide frame shows, and PreCut centres it, so with the two people at
the left and right of the frame the slice shows the gap between them. This finds who is talking and where each person stands, then slides the slice onto the speaker (Premiere's Basic Motion
position), cutting the clip where the speaker changes. Only the horizontal position moves: the scale stays what it was, so nothing about the vertical frame changes.

  WHO talks, and when   each person wears their own recorder, which hears its wearer 8 to 12 dB louder: `speakers.match_lavs` finds where the camera's audio falls in each person's file and
                        `speakers.speaker_runs` turns the two level tracks into runs (the same method that cut Reel 3).
  WHERE each person is  macOS's face detector (find_faces.swift) on a few frames: two faces, left and right. WHICH is whom: while the dominant speaker's own recorder says they are talking, the
                        face that moves more is theirs (a speaker moves their head and hands; a listener mostly does not). Weak evidence is reported as weak and nothing is framed on a guess.

Everything slow is cached beside the cut (`framing_cache.json`), keyed by the camera file, so a second pass costs seconds.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "reframe"))

import speakers as sp  # noqa: E402

FACES_SWIFT = HERE / "find_faces.swift"
MIN_SEPARATION = 0.12          # two faces closer than this (fraction of the width) are one person seen twice, or a poster
MIN_SAMPLE_FRAMES = 3          # frames with exactly two faces needed to trust where the two people stand
IDENTITY_BAND = 0.12           # motion is measured within this fraction of the width on either side of a face
IDENTITY_MARGIN = 1.15         # the speaker's side must move this much more than the other side, or the evidence is called weak
MOTION_FPS, MOTION_W, MOTION_H = 5, 192, 108
SNIPPETS_PER_PERSON, SNIPPET_SEC, SNIPPET_MIN_RUN, SNIPPET_SPACING = 6, 3.0, 2.0, 15.0     # who-is-who is judged on a few short snippets of each person talking, spread out
IDENTITY_SPAN_SEC = 300.0      # snippets are taken from this far either side of the cut: a cut where one person talks the whole time cannot answer "who is who" alone
MIN_SIDE_FRAMES = 15           # a person's motion counts only with this many frame pairs behind it (three seconds at 5 a second)
NOTHING_TO_DO_WIDTH = 0.95     # a window already this wide (fraction of the source) has no room to move


class FramingError(Exception):
    """Could not frame on the speaker; the message says why in words."""


# ------------------------------------------------------------------ geometry

def window_half_width(scale_pct: float, source_w: float, out_w: int) -> float:
    """Half the width of the slice of the source that shows, as a fraction of the source width."""
    return (out_w / (scale_pct / 100.0)) / source_w / 2.0


def horiz_for_person(x_frac: float, scale_pct: float, source_w: float, out_w: int) -> float:
    """Basic Motion centre.horiz that puts the person at fraction `x_frac` of the source in the middle of the frame. The slice is kept inside the source (a person near an edge gets a slice
    flush with that edge). The unit is the one Ryan's own Premiere export settled (safety_net/fixtures/premiere_motion): horiz = (the slice's offset) x scale / source width."""
    import reframe_xml as rx
    hw = window_half_width(scale_pct, source_w, out_w)
    x = min(max(x_frac, hw), 1.0 - hw)
    return rx.horiz_for_subject(x * source_w, scale_pct, source_w)


def x_for_horiz(horiz: float, scale_pct: float, source_w: float) -> float:
    """The fraction of the source width that sits in the middle of the frame for a given Basic Motion horiz (the inverse of horiz_for_person, without the clamp)."""
    import reframe_xml as rx
    return rx.subject_for_horiz(horiz, scale_pct, source_w) / source_w


# ------------------------------------------------------------------ who is talking

def speaker_runs_for(lavs: dict, t0: float, in_sec: float, dur: float) -> list[tuple[float, float, str]]:
    """[(start, end, person)] in seconds from the start of a clip that begins at camera time `in_sec` and lasts `dur`; `lavs` is match_lavs' answer for a stretch that began at camera time `t0`."""
    if len(lavs) < 2:
        return []
    levels = {who: sp.level_track(f, o + (in_sec - t0), dur) for who, (f, o, _sc) in lavs.items()}
    return sp.speaker_runs(levels)


def pieces_of(runs: list[tuple[float, float, str]], dur: float, fps: float) -> list[tuple[int, int, str]]:
    """[(first_frame, frame_count, person)] covering the clip, switching to the next speaker a little before they talk (speakers.pieces_for_cut) at the clip's own frame rate."""
    return sp.pieces_for_cut(runs, dur, fps=fps)


# ------------------------------------------------------------------ where each person is

def sample_frames(cam: str, times: list[float], out_dir: Path, width: int = 1600) -> list[Path]:
    paths = []
    for i, t in enumerate(times):
        p = out_dir / f"face_{i:02d}.jpg"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(t, 0):.3f}", "-i", cam, "-frames:v", "1", "-vf", f"scale={width}:-1", "-q:v", "4", str(p)], capture_output=True)
        if r.returncode == 0 and p.is_file():
            paths.append(p)
    return paths


def find_faces(images: list[Path]) -> list[list[dict]]:
    """Face boxes for each image (fractions, origin top left), from the macOS detector. Raises FramingError when it cannot run."""
    if not images:
        return []
    try:
        r = subprocess.run(["swift", str(FACES_SWIFT), *map(str, images)], capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FramingError(f"the face detector could not run ({exc})") from exc
    rows = {}
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rows[d.get("image")] = d.get("faces") or []
    if not rows:
        raise FramingError("the face detector gave no answer: " + (r.stderr.strip().splitlines() or ["(nothing printed)"])[-1][:200])
    return [rows.get(str(p), []) for p in images]


def two_people(face_rows: list[list[dict]]) -> dict | None:
    """Where the two people stand, from frames that show exactly two faces far enough apart: {"left": x, "right": x, "frames": n} (fractions of the width, medians), or None."""
    lefts, rights = [], []
    for faces in face_rows:
        if len(faces) != 2:
            continue
        a, b = sorted(faces, key=lambda f: f["x"])
        if b["x"] - a["x"] >= MIN_SEPARATION:
            lefts.append(a["x"])
            rights.append(b["x"])
    if len(lefts) < MIN_SAMPLE_FRAMES:
        return None
    return {"left": float(np.median(lefts)), "right": float(np.median(rights)), "frames": len(lefts)}


def motion_by_side(cam: str, segments: list[tuple[float, float]], left: float, right: float) -> tuple[float, float, int]:
    """(motion on the left face, motion on the right face, frames compared) over the given camera-time segments, from low-resolution frames five a second."""
    L = R = 0.0
    n = 0
    xs = (np.arange(MOTION_W) + 0.5) / MOTION_W
    lm, rm = np.abs(xs - left) <= IDENTITY_BAND, np.abs(xs - right) <= IDENTITY_BAND
    for s, e in segments:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{s:.2f}", "-t", f"{min(e - s, SNIPPET_SEC):.2f}", "-i", cam, "-vf", f"fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H},format=gray",
                            "-f", "rawvideo", "-"], capture_output=True)
        f = np.frombuffer(r.stdout, dtype=np.uint8)
        f = f[: (len(f) // (MOTION_W * MOTION_H)) * MOTION_W * MOTION_H].reshape(-1, MOTION_H, MOTION_W).astype(np.float32)
        if len(f) < 3:
            continue
        d = np.abs(np.diff(f, axis=0)).mean(axis=1)                         # (frames - 1, columns)
        L += float(d[:, lm].sum())
        R += float(d[:, rm].sum())
        n += len(d)
    return L, R, n


def snippets(runs: list[tuple[float, float, str]]) -> dict[str, list[tuple[float, float]]]:
    """For each person up to SNIPPETS_PER_PERSON short stretches (SNIPPET_SEC, from the middle of a run of them talking), longest runs first, at least SNIPPET_SPACING apart."""
    by: dict[str, list[tuple[float, float]]] = {}
    for s, e, who in runs:
        if who and e - s >= SNIPPET_MIN_RUN:
            by.setdefault(who, []).append((s, e))
    out: dict[str, list[tuple[float, float]]] = {}
    for who, rs in by.items():
        chosen: list[tuple[float, float]] = []
        for s, e in sorted(rs, key=lambda r: -(r[1] - r[0])):
            mid = (s + e) / 2
            if all(abs(mid - (a + b) / 2) >= SNIPPET_SPACING for a, b in chosen):
                half = min(SNIPPET_SEC, e - s) / 2
                chosen.append((mid - half, mid + half))
            if len(chosen) >= SNIPPETS_PER_PERSON:
                break
        out[who] = chosen
    return out


def who_is_left(cam: str, runs: list[tuple[float, float, str]], left: float, right: float, motion_fn=None) -> dict | None:
    """Which person stands on the left. `runs` are [(camera_start, camera_end, person)] over a wide stretch. While a person's own recorder says they are talking, the side of the picture they
    stand on moves more than the other side (a speaker moves head and hands; a listener mostly does not), so the left-to-right motion ratio is higher while the LEFT person talks than while the
    right person does. Both people's snippets are compared against each other, which cancels how fidgety each side is. Returns {"left", "right", "evidence"} or None when either person has too
    little speech to compare or the two ratios are too close to call."""
    motion_fn = motion_fn or motion_by_side
    segs = snippets(runs)
    names = sorted(segs, key=lambda w: -sum(b - a for a, b in segs[w]))
    if len(names) < 2:
        return None
    a, b = names[0], names[1]
    ratio, frames = {}, {}
    for w in (a, b):
        L, R, n = motion_fn(cam, segs[w], left, right)
        if n < MIN_SIDE_FRAMES or L + R <= 0:
            return None
        ratio[w], frames[w] = (L + 1e-6) / (R + 1e-6), n
    score = ratio[a] / ratio[b]                                              # above 1: the left side moves relatively more while `a` talks, so `a` is on the left
    if max(score, 1 / score) < IDENTITY_MARGIN:
        return None
    lft, rgt = (a, b) if score > 1 else (b, a)
    return {"left": lft, "right": rgt, "score": float(score),
            "evidence": f"{a} talking: the left side moves {ratio[a]:.2f}x the right ({frames[a]} frames); {b} talking: {ratio[b]:.2f}x ({frames[b]} frames); {a} is on the {'left' if score > 1 else 'right'} by {max(score, 1 / score):.2f}x"}


# ------------------------------------------------------------------ the whole analysis for one camera file

def _cache_key(cam: str) -> str:
    p = Path(cam)
    st = p.stat()
    return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"


def _read_cache(cache: Path | None) -> dict:
    try:
        return json.loads(cache.read_text()) if cache and cache.is_file() else {}
    except (OSError, ValueError):
        return {}


def _write_cache(cache: Path | None, data: dict) -> None:
    if cache:
        try:
            cache.write_text(json.dumps(data, indent=1))
        except OSError:
            pass


def analyse(cam: str, clips: list[dict], mic_dir: str | Path, cache: Path | None = None, progress=lambda s: None, faces_fn=None, sample_fn=None, lavs_fn=None) -> dict:
    """Who talks when in each clip, and where each person stands, for one camera file.

    `clips`: [{"idx", "src_in", "src_out", "fps"}] (camera seconds). Returns {"people": {"Bob": x, "Mitch": x}, "evidence": text, "clips": {idx: {"pieces": [(first_frame, frames, person)], "runs": [...]}}}.
    Raises FramingError, in words, when it cannot: fewer than two recorders matched, fewer than two faces, or no way to tell who is whom."""
    faces_fn = faces_fn or find_faces
    sample_fn = sample_fn or sample_frames
    t0 = max(0.0, min(c["src_in"] for c in clips) - 2.0)
    t1 = max(c["src_out"] for c in clips) + 2.0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        progress("Finding each person's recorder in the camera's timeline")
        if lavs_fn:
            lavs = lavs_fn(t0, t1)
        else:
            wav = td / "cam.wav"
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0:.3f}", "-i", cam, "-vn", "-ac", "1", "-ar", "8000", str(wav)], capture_output=True)
            if r.returncode != 0:
                raise FramingError("the camera's audio could not be read")
            lavs = sp.match_lavs(wav, 0.0, t1 - t0, Path(mic_dir))
        if len(lavs) < 2:
            found = ", ".join(sorted(lavs)) or "none"
            raise FramingError(f"both people's recorders could not be found against this camera (found: {found}); who is talking cannot be told")
        out_clips = {}
        all_runs: list[tuple[float, float, str]] = []
        for c in clips:
            dur = c["src_out"] - c["src_in"]
            runs = speaker_runs_for(lavs, t0, c["src_in"], dur)
            out_clips[c["idx"]] = {"runs": runs, "pieces": pieces_of(runs, dur, c["fps"])}
            all_runs += [(c["src_in"] + s, c["src_in"] + e, w) for s, e, w in runs]

        key = _cache_key(cam)
        cached = _read_cache(cache).get(key)
        names = sorted(lavs)
        if cached and sorted(cached.get("people", {})) == names:
            people, evidence = cached["people"], cached["evidence"] + " (from an earlier look at this camera)"
        else:
            progress("Finding where the two people stand in the picture")
            mids = sorted({round((c["src_in"] + c["src_out"]) / 2, 2) for c in clips})
            extra = np.linspace(t0 + 2, t1 - 2, 4).round(2).tolist()
            frames = sample_fn(cam, sorted(set(mids + extra))[:8], td)
            pos = two_people(faces_fn(frames))
            if pos is None:
                raise FramingError("two separate faces were not found in enough frames of this camera, so where each person stands is unknown")
            progress("Telling who is who from who moves while their own recorder hears them talk")
            lo, hi = max(0.0, t0 - IDENTITY_SPAN_SEC), t1 + IDENTITY_SPAN_SEC
            wide = [(lo + s, lo + e, w) for s, e, w in speaker_runs_for(lavs, t0, lo, hi - lo)]       # the recorders keep their offset to the camera across the whole file
            who = who_is_left(cam, wide or all_runs, pos["left"], pos["right"])
            if who is None:
                raise FramingError("which of the two faces is which person could not be told from the footage (the speaker's side did not move clearly more than the other); nothing was framed on a guess")
            people = {who["left"]: pos["left"], who["right"]: pos["right"]}
            evidence = f"{who['left']} on the left ({pos['left']:.2f} of the width), {who['right']} on the right ({pos['right']:.2f}), from {pos['frames']} frames; {who['evidence']}"
            data = _read_cache(cache)
            data[key] = {"people": people, "evidence": evidence}
            _write_cache(cache, data)
    return {"people": people, "evidence": evidence, "clips": out_clips, "t0": t0}
