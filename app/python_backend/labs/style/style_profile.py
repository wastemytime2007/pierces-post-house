#!/usr/bin/env python3
"""How a reference video is built, measured, and how a cut differs from it.

    python3 labs/style/style_profile.py --reference "<reference.mp4>" --ours "<preview.mp4>" --out "<folder>" [--open]
    python3 labs/style/style_profile.py --reference "<reference.mp4>" --out "<folder>"        # just measure it

The creator's "analyse a competitor's video and replicate the style of the overlays and B-roll". What can be
measured is measured: cut rhythm (shots, cuts per minute, first cut, pace trend), loudness, brightness, contrast,
colour and saturation, motion energy, and lower-third text-like activity. Both videos are measured the same way
and the differences come out as a table, side-by-side frames, a picture of each one's shot rhythm, and SUGGESTED
notes in plain words. Nothing is edited from a comparison: what to copy, and whether to copy it at all, is the
editor's call. Not measured (and said so in the report): what the graphics look like, the content of the shots,
and whether the style suits the piece.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "audio"))
import reference_music as rm  # noqa: E402  (loudness)

SCENE = 0.20                       # reproduces the ~23 cuts documented for the wallpaper reel (0.12 gives 46, 0.30 gives 13)
SAMPLE_W, SAMPLE_H, SAMPLE_FPS = 96, 54, 4


class StyleError(Exception):
    pass


def probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        j = json.loads(p.stdout)
        v = next(s for s in j["streams"] if s["codec_type"] == "video")
        num, den = (int(x) for x in v["r_frame_rate"].split("/"))
        return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(num / den, 2), "duration": float(j["format"]["duration"]),
                "has_audio": any(s["codec_type"] == "audio" for s in j["streams"])}
    except (ValueError, StopIteration, KeyError, ZeroDivisionError):
        raise StyleError(f"could not read a video from {path}")


def cut_times(path: Path, threshold: float = SCENE) -> list[float]:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vf", f"select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return sorted({round(float(t), 3) for t in re.findall(r"pts_time:([\d.]+)", err)})


def sample_frames(path: Path) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_W}:{SAMPLE_H}", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    a = np.frombuffer(p.stdout, dtype=np.uint8)
    n = a.size // (SAMPLE_W * SAMPLE_H * 3)
    if n < 4:
        raise StyleError(f"{path.name}: too few frames could be sampled to measure")
    return a[:n * SAMPLE_W * SAMPLE_H * 3].reshape(n, SAMPLE_H, SAMPLE_W, 3).astype(np.float64)


def rhythm(times: list[float], duration: float) -> dict:
    edges = [0.0] + [t for t in times if 0 < t < duration] + [duration]
    shots = np.diff(edges)
    half = duration / 2
    first = sum(1 for t in times if t < half) / max(half / 60, 1e-9)
    second = sum(1 for t in times if t >= half) / max((duration - half) / 60, 1e-9)
    trend = "accelerating" if second > first * 1.25 else "decelerating" if second < first * 0.8 else "steady"
    return {"cuts": len(edges) - 2, "cuts_per_min": round((len(edges) - 2) / duration * 60, 1), "shots": len(shots), "avg_shot_sec": round(float(shots.mean()), 2),
            "median_shot_sec": round(float(np.median(shots)), 2), "longest_shot_sec": round(float(shots.max()), 2), "shortest_shot_sec": round(float(shots.min()), 2),
            "first_cut_sec": round(times[0], 2) if times else None, "pace_trend": trend, "shot_lengths": [round(float(s), 2) for s in shots]}


def pauses(path: Path) -> dict:
    """Silences of 0.3s or more in the audio, found against the recording's own quiet level (as ops.detect_pause does).
    Reliable only when there is a quiet floor to find: a continuous music bed hides the pauses."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-f", "f32le", "-ac", "1", "-ar", "8000", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)
    n = int(0.02 * 8000)
    if x.size < n * 50:
        return {"pauses_per_min": None, "longest_pause_sec": None, "pauses_reliable": False, "quiet_floor_db": None, "speech_fraction": None}
    r = np.sqrt((x[:x.size // n * n].reshape(-1, n) ** 2).mean(axis=1))
    floor, loud = float(np.percentile(r, 10)), float(np.percentile(r, 90))
    thr = max(min(floor * 3.0, loud * 0.2), 10 ** (-60 / 20))
    quiet = r < thr
    runs, i = [], 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if (j - i) * 0.02 >= 0.3:
                runs.append((j - i) * 0.02)
            i = j
        else:
            i += 1
    minutes = x.size / 8000 / 60
    floor_db = 20 * np.log10(max(floor, 1e-6))
    return {"pauses_per_min": round(len(runs) / minutes, 1), "longest_pause_sec": round(max(runs), 2) if runs else 0.0, "quiet_floor_db": round(float(floor_db), 1),
            "pauses_reliable": bool(floor_db < -40), "speech_fraction": round(float((~quiet).mean()), 2)}


def picture(fr: np.ndarray) -> dict:
    luma = fr @ np.array([0.299, 0.587, 0.114])
    mx, mn = fr.max(axis=3), fr.min(axis=3)
    sat = (mx - mn) / np.maximum(mx, 1.0)
    motion = np.median(np.abs(np.diff(luma, axis=0)).mean(axis=(1, 2)))                       # the median ignores the spikes at cuts
    q = (fr // 64).astype(int).clip(0, 3)
    codes = (q[..., 0] * 16 + q[..., 1] * 4 + q[..., 2]).ravel()
    top = np.argsort(np.bincount(codes, minlength=64))[::-1][:4]
    palette = ["#%02x%02x%02x" % tuple(int(v) for v in ((c // 16) * 64 + 32, ((c // 4) % 4) * 64 + 32, (c % 4) * 64 + 32)) for c in top]
    gy = np.abs(np.diff(luma, axis=1))[:, :, :-1]
    gx = np.abs(np.diff(luma, axis=2))[:, :-1, :]
    edge = gx + gy
    h = edge.shape[1]
    band = edge[:, int(h * 0.62):int(h * 0.92), :].mean(axis=(1, 2))
    whole = np.maximum(edge.mean(axis=(1, 2)), 1e-6)
    ratio = band / whole
    return {"brightness": round(float(luma.mean() / 255), 3), "contrast": round(float(luma.std(axis=(1, 2)).mean() / 255), 3), "saturation": round(float(sat.mean()), 3),
            "motion_energy": round(float(motion / 255), 4), "palette": palette, "lower_third_activity": round(float(np.median(ratio)), 2),
            "lower_third_heavy_frames": round(float((ratio > 1.6).mean()), 2)}


def profile(path: Path, true_cuts: list[float] | None = None, threshold: float = SCENE) -> dict:
    """`true_cuts` (timeline seconds of the real cuts, when they are known, e.g. our own cut) lets the profile say how many of
    them the visible-cut detector actually found."""
    info = probe(path)
    out = {"file": str(path), **info, "aspect": round(info["width"] / info["height"], 3), "orientation": "vertical" if info["height"] > info["width"] else "horizontal"}
    found = cut_times(path, threshold)
    out["rhythm"] = rhythm(found, info["duration"])
    if true_cuts:
        hit = sum(any(abs(f - t) <= 0.25 for f in found) for t in true_cuts)
        out["rhythm"]["actual_cuts"] = len(true_cuts)
        out["rhythm"]["actual_cuts_per_min"] = round(len(true_cuts) / info["duration"] * 60, 1)
        out["rhythm"]["detection_recall"] = round(hit / len(true_cuts), 2)
    out["picture"] = picture(sample_frames(path))
    out["audio"] = {**(rm.loudness(path) if info["has_audio"] else {"lufs": None, "lra": None}),
                    **(pauses(path) if info["has_audio"] else {"pauses_per_min": None, "longest_pause_sec": None, "pauses_reliable": False})}
    return out


# metric, path in the profile, tolerance kind, tolerance, plain-words suggestion when ours differs
METRICS = [
    ("visible cuts per minute", ("rhythm", "cuts_per_min"), "ratio", 0.30, "The reference shows about {ref:.0f} visible cuts a minute; this cut {our:.0f}. {move} cuts would bring the pace closer."),
    ("average visible shot (s)", ("rhythm", "avg_shot_sec"), "ratio", 0.35, "The reference holds a shot for about {ref:.1f}s on average; this cut {our:.1f}s. {move_len} shots would match it."),
    ("first cut (s)", ("rhythm", "first_cut_sec"), "abs", 2.0, "The reference makes its first cut at {ref:.1f}s; this cut at {our:.1f}s."),
    ("loudness (LUFS)", ("audio", "lufs"), "abs", 2.0, "The reference sits at {ref:.1f} LUFS; this cut at {our:.1f}. {move_lufs}"),
    ("loudness range (LU)", ("audio", "lra"), "abs", 3.0, "The reference varies by about {ref:.1f} LU in loudness; this cut by {our:.1f}."),
    ("pauses per minute", ("audio", "pauses_per_min"), "abs", 2.0, "The reference has about {ref:.1f} pauses (0.3s or more) a minute; this cut {our:.1f}. {pmove}"),
    ("longest pause (s)", ("audio", "longest_pause_sec"), "abs", 0.6, "The reference's longest pause is {ref:.1f}s; this cut's is {our:.1f}s."),
    ("brightness", ("picture", "brightness"), "ratio", 0.20, "The reference is {cmp} overall (brightness {ref:.2f} vs {our:.2f})."),
    ("contrast", ("picture", "contrast"), "ratio", 0.25, "The reference has {cmp} contrast ({ref:.2f} vs {our:.2f})."),
    ("saturation", ("picture", "saturation"), "ratio", 0.25, "The reference is {cmp} in colour ({ref:.2f} vs {our:.2f})."),
    ("motion energy", ("picture", "motion_energy"), "ratio", 0.50, "The reference has {cmp} movement inside its shots ({ref:.3f} vs {our:.3f})."),
    ("lower-third text-like activity", ("picture", "lower_third_activity"), "ratio", 0.25, "The reference shows {cmp} text-like detail in the lower third ({ref:.2f} vs {our:.2f}), which suggests {capt}."),
]


def _get(p: dict, path: tuple):
    v = p
    for k in path:
        v = v.get(k) if isinstance(v, dict) else None
    return v


def _words(name: str, ref: float, our: float) -> dict:
    """The plain words a suggestion needs, chosen by which way the gap goes."""
    ref_higher = ref > our
    hi, lo = {"brightness": ("brighter", "darker"), "contrast": ("higher", "lower"), "saturation": ("more saturated", "less saturated")}.get(name, ("more", "less"))
    return {"move": "More" if ref_higher else "Fewer",                              # cuts per minute: the reference has more, so this cut needs more
            "move_len": "Longer" if ref_higher else "Shorter",                      # average shot: the reference holds shots longer, so this cut needs longer ones
            "move_lufs": "It could be brought up." if ref_higher else "It could be brought down.",
            "pmove": "Leaving more breathing room between phrases would match it." if ref_higher else "Tightening more of the pauses would match it.",
            "cmp": hi if ref_higher else lo,
            "capt": "captions or on-screen text where this cut has less" if ref_higher else "this cut has more text-like detail down there than the reference"}


def compare(ref: dict, ours: dict) -> dict:
    rows, notes = [], []
    rows.append({"metric": "orientation", "reference": ref["orientation"], "ours": ours["orientation"], "verdict": "SAME" if ref["orientation"] == ours["orientation"] else "DIFFERENT",
                 "note": None if ref["orientation"] == ours["orientation"] else "The two are framed differently (vertical vs horizontal), so every measure below compares unlike formats; read the pace and loudness rows, treat the picture rows with care."})
    recall = ours["rhythm"].get("detection_recall")
    low_recall = recall is not None and recall < 0.6
    for name, path, kind, tol, text in METRICS:
        r, o = _get(ref, path), _get(ours, path)
        if path[0] == "rhythm" and path[1] in ("cuts_per_min", "avg_shot_sec") and low_recall:
            rows.append({"metric": name, "reference": r, "ours": o, "verdict": "UNRELIABLE",
                         "note": f"the visible-cut detector found only {recall:.0%} of this cut's real cuts (jump cuts inside one shoot look alike), so a pace comparison from the picture is not trustworthy; use the pause rows"})
            continue
        if path[0] == "audio" and path[1] in ("pauses_per_min", "longest_pause_sec") and not (ref["audio"].get("pauses_reliable") and ours["audio"].get("pauses_reliable")):
            rows.append({"metric": name, "reference": r, "ours": o, "verdict": "UNRELIABLE",
                         "note": "a continuous music bed or noise floor hides the pauses in one of the two, so pauses cannot be compared"})
            continue
        if r is None or o is None:
            rows.append({"metric": name, "reference": r, "ours": o, "verdict": "NOT MEASURED", "note": "one of the two has no audio" if path[0] == "audio" else None})
            continue
        diff = abs(o - r) / max(abs(r), 1e-9) if kind == "ratio" else abs(o - r)
        close = diff <= tol
        rows.append({"metric": name, "reference": r, "ours": o, "verdict": "CLOSE" if close else ("OURS HIGHER" if o > r else "OURS LOWER"),
                     "gap": round(diff, 3), "tolerance": tol, "kind": kind})
        if not close:
            notes.append({"metric": name, "suggestion": text.format(ref=r, our=o, **_words(name, r, o))})
    if "actual_cuts_per_min" in ours["rhythm"]:
        rows.append({"metric": "actual cuts per minute (ours, from the timeline)", "reference": None, "ours": ours["rhythm"]["actual_cuts_per_min"], "verdict": "NOT MEASURED",
                     "note": f"the real cut rate of ours; the picture-based detector saw {ours['rhythm']['cuts_per_min']} a minute ({ours['rhythm']['detection_recall']:.0%} of the real cuts)"})
    rows.append({"metric": "pace trend", "reference": ref["rhythm"]["pace_trend"], "ours": ours["rhythm"]["pace_trend"],
                 "verdict": "UNRELIABLE" if low_recall else ("SAME" if ref["rhythm"]["pace_trend"] == ours["rhythm"]["pace_trend"] else "DIFFERENT")})
    return {"rows": rows, "suggested_notes": notes,
            "not_measured": ["what the graphics or overlays look like", "the content of the shots (B-roll or not)", "whether the style suits this piece",
                              "jump cuts the picture cannot see (only their effect on pauses is measured)"]}


def frames_b64(path: Path, dur: float, n: int = 8) -> list[tuple[float, str]]:
    out = []
    for k in range(n):
        t = dur * (k + 0.5) / n
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=-2:200", "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True)
        if p.stdout:
            out.append((t, "data:image/jpeg;base64," + base64.b64encode(p.stdout).decode()))
    return out


def rhythm_svg(p: dict, color: str) -> str:
    total = sum(p["rhythm"]["shot_lengths"]) or 1.0
    x, rects = 0.0, []
    for i, s in enumerate(p["rhythm"]["shot_lengths"]):
        w = s / total * 1000
        rects.append(f'<rect x="{x:.1f}" y="0" width="{max(w - 1.5, 0.8):.1f}" height="26" fill="{color}" opacity="{0.95 if i % 2 else 0.6}"/>')
        x += w
    return f'<svg viewBox="0 0 1000 26" preserveAspectRatio="none" style="width:100%;height:26px;display:block">{"".join(rects)}</svg>'


def render(ref: dict, ours: dict | None, cmp: dict | None, title: str) -> str:
    def fmt(v):
        return "n/a" if v is None else (f"{v:.3g}" if isinstance(v, float) else str(v))
    frames = "".join(
        f'<div class="vid"><h3>{label}: {html.escape(Path(p["file"]).name)}</h3>'
        f'<div class="rh">{rhythm_svg(p, color)}<div class="cap">shot rhythm: each bar is a shot, width = its length ({p["rhythm"]["shots"]} shots, {p["rhythm"]["cuts_per_min"]} cuts/min)</div></div>'
        f'<div class="fr">{"".join(f"<figure><img src={chr(34)}{u}{chr(34)}><figcaption>{t:.0f}s</figcaption></figure>" for t, u in frames_b64(Path(p["file"]), p["duration"]))}</div></div>'
        for label, p, color in ([("Reference", ref, "#0391d8")] + ([("Ours", ours, "#f4690b")] if ours else [])))
    if cmp:
        body = "".join(f'<tr class="{r["verdict"].split()[0].lower()}"><td>{html.escape(r["metric"])}</td><td>{fmt(r["reference"])}</td><td>{fmt(r["ours"])}</td><td>{r["verdict"]}{("<div class=cap>" + html.escape(r["note"]) + "</div>") if r.get("note") and r["verdict"] in ("UNRELIABLE", "NOT MEASURED") else ""}</td></tr>' for r in cmp["rows"])
        table = f'<h2>Measured differences</h2><table><tr><th>metric</th><th>reference</th><th>ours</th><th>verdict</th></tr>{body}</table>'
        sugg = ("<h2>Suggested notes (yours to accept or ignore; nothing was changed)</h2><ul>" + "".join(f"<li>{html.escape(n['suggestion'])}</li>" for n in cmp["suggested_notes"]) + "</ul>") if cmp["suggested_notes"] else "<h2>Suggested notes</h2><p>Every measured metric is within tolerance.</p>"
        warn = next((r["note"] for r in cmp["rows"] if r["metric"] == "orientation" and r["note"]), None)
        nm = "<h2>Not measured</h2><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in cmp["not_measured"]) + "</ul>"
        extra = (f'<p class="warn">{html.escape(warn)}</p>' if warn else "") + table + sugg + nm
    else:
        extra = f'<h2>Measured</h2><pre>{html.escape(json.dumps({k: v for k, v in ref.items() if k != "rhythm"} | {"rhythm": {k: v for k, v in ref["rhythm"].items() if k != "shot_lengths"}}, indent=2))}</pre>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Style profile</title><style>
:root{{color-scheme:dark;--bg:#0d1420;--panel:#141d2c;--ink:#e8edf5;--mute:#8b98ad;--line:#25324a}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif}}main{{max-width:1050px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:16px;margin:26px 0 8px}}h3{{font-size:14px;margin:0 0 6px}}.sub{{color:var(--mute);margin:0 0 14px}}
.vid{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}}.cap{{color:var(--mute);font-size:12px;margin:2px 0 8px}}
.fr{{display:flex;gap:6px;overflow-x:auto}}figure{{margin:0;flex:none}}img{{height:150px;border-radius:4px;display:block}}figcaption{{color:var(--mute);font-size:11px}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}}th{{color:var(--mute);font-weight:600;font-size:13px}}
tr.close td:last-child,tr.same td:last-child{{color:#38d66b;font-weight:700}}tr.ours td:last-child,tr.different td:last-child{{color:#f4690b;font-weight:700}}tr.not td:last-child,tr.unreliable td:last-child{{color:var(--mute)}}
.warn{{background:#3a2a12;border:1px solid #f4690b;border-radius:8px;padding:8px 12px}}pre{{background:var(--panel);padding:12px;border-radius:8px;overflow:auto}}
</style></head><body><main><h1>{html.escape(title)}</h1><p class="sub">Measured from the files. A style comparison suggests; it never edits.</p>{frames}{extra}</main></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--ours", type=Path)
    ap.add_argument("--ours-timeline", type=Path, help="the timeline.json beside our review page: gives our real cut times, to report how many the picture detector found")
    ap.add_argument("--scene", type=float, default=SCENE, help="scene-change threshold for visible cuts")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    try:
        ref = profile(a.reference, threshold=a.scene)
        true = [c["start"] for c in json.loads(a.ours_timeline.read_text())["clips"][1:]] if a.ours_timeline else None
        ours = profile(a.ours, true, a.scene) if a.ours else None
    except (StyleError, OSError, KeyError, ValueError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    cmp = compare(ref, ours) if ours else None
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "style.json").write_text(json.dumps({"reference": ref, "ours": ours, "comparison": cmp}, indent=2))
    page = a.out / "style_report.html"
    page.write_text(render(ref, ours, cmp, "Style profile" + (f": {a.ours.stem} against {a.reference.stem}" if ours else f": {a.reference.stem}")))
    r = ref["rhythm"]
    print(f"reference: {ref['width']}x{ref['height']} {ref['duration']:.1f}s, {r['shots']} shots, {r['cuts_per_min']} cuts/min, avg shot {r['avg_shot_sec']}s, {r['pace_trend']}, "
          f"loudness {ref['audio']['lufs']} LUFS, lower-third activity {ref['picture']['lower_third_activity']}")
    if cmp:
        print(f"ours:      {ours['width']}x{ours['height']} {ours['duration']:.1f}s, {ours['rhythm']['shots']} shots, {ours['rhythm']['cuts_per_min']} cuts/min, avg shot {ours['rhythm']['avg_shot_sec']}s")
        for row in cmp["rows"]:
            print(f"  {row['verdict']:13s} {row['metric']}: ref {row['reference']} / ours {row['ours']}")
        print(f"\n{len(cmp['suggested_notes'])} suggested note(s); report: {page}")
    if a.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
