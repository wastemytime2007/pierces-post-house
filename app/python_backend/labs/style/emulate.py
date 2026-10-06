#!/usr/bin/env python3
"""Emulate a reference video: take the aspects you pick (default: all) and make our cut follow them, then measure how close it got.

    PRECUT_ROOT=~/precut-checkout python3 labs/style/emulate.py --reference "<video>" \
        --ours-xml "<layered .xml>" --ours-preview "<clean preview.mp4>" --out "<folder>" \
        [--emulate all | orientation,music,cuts,color,text,graphics,sfx] [--skip music,...] [--brief style_brief.json] \
        [--orientation match|vertical|horizontal] [--music-file "<a track to use instead of the reference video's music>"] [--no-generate]

What each aspect does. "Emulated" means a file was made that Ryan can open and the result was re-measured;
"measured only" means nothing could be reproduced automatically and the page says why. Nothing here edits
the source cut: every result is a new file in the output folder.

  orientation  re-frames the preview to the reference's shape (vertical or horizontal) by cropping. Crude: a centre crop
               (or --reframe-focus) cannot follow a subject. Checked: the output has the reference's shape.
  color        a 3D LUT (.cube, loads in Premiere) that moves our picture's Lab mean and spread toward the reference's, at
               --color-strength (default 0.7), plus a graded preview. Checked: the gap to the reference shrank, from the graded file.
  music        the reference's music is taken from the reference video's own audio (its music-only stretches; the whole mix, flagged,
               when there are none), or from --music-file, described from measurements, generated, re-measured, and mixed under our
               speech in the preview. Checked: the closeness measurements of reference_music.py. Costs a few cents per take.
  cuts         the pauses between our words are compared with the reference's (from word timing, which works under a music bed).
               Pauses longer than the reference's longest ordinary gap are tightened through the verified revise.py path. A new cut,
               in its own folder. More cuts per minute (new shots) cannot be made without footage and is reported, not faked.
  text         measured only: how much text-like detail sits in the lower third. No wording is ever invented.
  graphics     measured only: there is no reliable overlay detector; the reference's frames are shown for your eyes.
  sfx          measured only: sound effects cannot be separated from voice and music with the tools installed.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for d in (HERE, HERE.parent / "audio", HERE.parent / "review_loop"):
    sys.path.insert(0, str(d))
import make_audio as ma  # noqa: E402
import reference_music as rm  # noqa: E402
import style_profile as sp  # noqa: E402

ASPECTS = ["orientation", "cuts", "music", "color", "text", "graphics", "sfx"]
PLAIN = {"orientation": "vertical or horizontal", "cuts": "type of cuts (pauses)", "music": "music", "color": "colour", "text": "text on screen",
         "graphics": "callouts and graphics", "sfx": "sound effects"}
COLOR_STRENGTH = 0.7
STD_RATIO = (0.6, 1.6)                       # how far the spread of a Lab channel may be stretched or squeezed
JUDGE_HEIGHT = 1080
MATERIAL_SHRINK = 0.15                      # the gap to the reference must fall by this fraction (and 0.01) to count as closer
MIN_TIGHTEN_SEC = 0.5                       # a gap shorter than this is never tightened, whatever the reference does


class EmulateError(Exception):
    pass


def pick_aspects(emulate="all", skip=()) -> list[str]:
    """The aspects to run: everything unless told otherwise. An unknown name is an error, not silently ignored."""
    names = ASPECTS if emulate in (None, "all", ["all"]) else [x.strip() for x in (emulate.split(",") if isinstance(emulate, str) else emulate)]
    bad = [n for n in list(names) + list(skip) if n not in ASPECTS]
    if bad:
        raise EmulateError(f"unknown aspect(s) {bad}; the choices are {ASPECTS}")
    return [a for a in ASPECTS if a in names and a not in skip]


# ---------------------------------------------------------------- orientation
def crop_box(w: int, h: int, target: str, focus: float = 0.5) -> tuple[int, int, int, int]:
    """(crop_w, crop_h, x, y) of the largest 9:16 (vertical) or 16:9 (horizontal) window, centred at `focus` along the long side."""
    focus = min(max(focus, 0.0), 1.0)
    ar = 9 / 16 if target == "vertical" else 16 / 9
    if w / h > ar:                                           # too wide: cut the sides
        cw, ch = int(round(h * ar / 2)) * 2, h
        return cw, ch, int(round((w - cw) * focus)), 0
    cw, ch = w, int(round(w / ar / 2)) * 2                   # too tall: cut top and bottom
    return cw, ch, 0, int(round((h - ch) * focus))


def target_orientation(ref: dict, ours: dict, setting: str = "match") -> str:
    return ref["orientation"] if setting == "match" else setting


# ---------------------------------------------------------------- colour
def _lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _gam(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.maximum(c, 0) ** (1 / 2.4) - 0.055)


M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
WHITE = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb in 0..1, shape (...,3)."""
    xyz = (_lin(rgb) @ M.T) / WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_srgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) * 27 / 24389) * WHITE
    return np.clip(_gam(xyz @ np.linalg.inv(M).T), 0, 1)


def lab_stats(frames: np.ndarray) -> dict:
    """Mean and spread of L, a, b over the sampled frames (frames: N,H,W,3 in 0..255)."""
    lab = srgb_to_lab(frames.reshape(-1, 3) / 255.0)
    return {"mean": lab.mean(axis=0).tolist(), "std": lab.std(axis=0).tolist()}


def transfer_lut(ref: dict, ours: dict, strength: float = COLOR_STRENGTH, size: int = 33) -> np.ndarray:
    """(size**3, 3) RGB table, red fastest, moving our Lab mean/spread toward the reference's by `strength` (0 = unchanged, 1 = full)."""
    g = (np.arange(size) / (size - 1))
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")                          # last axis (r) varies fastest, as the .cube format wants
    rgb = np.stack([r, gg, b], axis=-1).reshape(-1, 3)
    lab = srgb_to_lab(rgb)
    mo, so, mr, sr = (np.array(x) for x in (ours["mean"], ours["std"], ref["mean"], ref["std"]))
    ratio = np.clip(sr / np.maximum(so, 1e-6), *STD_RATIO)
    full = (lab - mo) * ratio + mr
    return lab_to_srgb(lab + strength * (full - lab))


def write_cube(path: Path, lut: np.ndarray, size: int, title: str) -> None:
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in lut)
    path.write_text(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n\n{rows}\n')


def colour_gap(ref_pic: dict, pic: dict) -> dict:
    return {k: round(abs(pic[k] - ref_pic[k]) / max(abs(ref_pic[k]), 1e-9), 3) for k in ("brightness", "contrast", "saturation")}


# ---------------------------------------------------------------- cuts (pauses from word timing)
def gap_profile(words: list[tuple[float, float]], duration: float) -> dict:
    ws = sorted(words)
    gaps = [b[0] - a[1] for a, b in zip(ws, ws[1:]) if b[0] - a[1] > 0.02]
    if len(ws) < 10 or not gaps:
        return {"words": len(ws), "reliable": False}
    span = max(ws[-1][1] - ws[0][0], 1e-9)
    long_ = [g for g in gaps if g >= 0.3]
    return {"words": len(ws), "reliable": True, "words_per_min": round(len(ws) / span * 60, 1), "pauses_per_min": round(len(long_) / (duration / 60), 1),
            "median_gap_sec": round(float(np.median(gaps)), 2), "p95_gap_sec": round(float(np.percentile(gaps, 95)), 2), "longest_gap_sec": round(max(gaps), 2)}


def tighten_targets(ours_words: list[tuple[float, float]], limit: float) -> list[dict]:
    ws = sorted(ours_words)
    return [{"at": round((a[1] + b[0]) / 2, 2), "gap": round(b[0] - a[1], 2)} for a, b in zip(ws, ws[1:]) if b[0] - a[1] > limit]


# ---------------------------------------------------------------- running
def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def do_orientation(ref, ours, base: Path, out: Path, setting: str, focus: float) -> dict:
    tgt = target_orientation(ref, ours, setting)
    cur = ours["orientation"]
    if tgt == cur:
        return {"status": "ALREADY", "what": f"our cut is already {cur}, like the reference", "vf": None}
    cw, ch, x, y = crop_box(ours["width"], ours["height"], tgt, focus)
    return {"status": "PLANNED", "what": f"{cur} to {tgt} by a crop of {cw}x{ch} at x={x}, y={y} (focus {focus}); a crop cannot follow a subject, so check where it cuts the picture off",
            "vf": f"crop={cw}:{ch}:{x}:{y}", "target": tgt}


def do_color(ref, ours, out: Path, strength: float) -> dict:
    ref_fr, our_fr = sp.sample_frames(Path(ref["file"])), sp.sample_frames(Path(ours["file"]))
    rs, os_ = lab_stats(ref_fr), lab_stats(our_fr)
    lut = transfer_lut(rs, os_, strength)
    cube = out / "reference_look.cube"
    write_cube(cube, lut, 33, f"look of {Path(ref['file']).stem} at {strength}")
    return {"status": "PLANNED", "cube": str(cube), "strength": strength, "ref_lab": rs, "our_lab": os_,
            "what": f"a colour LUT that moves our Lab mean and spread toward the reference's at strength {strength}", "vf": f"lut3d=file='{cube}'"}


def music_source(brief_music: dict | None, reference: Path, out: Path, words_of=rm.transcribe_words) -> tuple[Path, dict]:
    """The music file the reference is measured from: a file the user gave, else the reference video's own audio."""
    given = (brief_music or {}).get("from")
    if given and given != "reference video":
        p = Path(given).expanduser()
        if not p.exists():
            raise EmulateError(f"the music track {p} is not there")
        return p, {"source": f"the music track you gave: {p.name}", "voice_included": False, "note": "used instead of the reference video's own music"}
    r = rm.music_from_video(reference, out / "music_reference", words_of=words_of)
    return Path(r["path"]), r


def do_music(ref_path: Path, src: dict, base: Path, out: Path, dry: bool, tries: int = 2, generate=ma.generate) -> dict:
    feats = rm.analyze(ref_path)
    prompt = rm.build_prompt(feats)
    res = {"source": src["source"], "voice_included": bool(src.get("voice_included")), "note": src.get("note", ""), "reference_features": feats, "prompt": prompt}
    if dry:
        return {**res, "status": "PLANNED", "what": "not generated (--no-generate); this is the description that would be sent", "stem": None}
    dur = sp.probe(base)["duration"]
    mp3, pick = rm.pick_best(feats, prompt, dur + 1.0, out / "generated", tries, generate=generate)
    speech = out / "speech.wav"
    ma.speech_wav(base, speech, 0.0, dur)
    stem = out / "music_stem.wav"
    ma.build_music_stem(mp3, speech, stem, dur, -5.0, 12.0)
    best = next(t for t in pick["takes"] if t["take"] == pick["chosen_take"])
    return {**res, "status": "PLANNED", "stem": str(stem), "mp3": str(mp3), "closeness": best["closeness"], "passed": bool(pick["passed"]), "takes": len(pick["takes"]),
            "what": f"{len(pick['takes'])} generated take(s) measured against the reference; the closest was mixed under our speech"}


def do_cuts(ref_words, ours_base: Path, ours_xml: Path, out: Path, words_of=rm.transcribe_words, revise=None) -> dict:
    ours_words = words_of(ours_base)
    dur = sp.probe(ours_base)["duration"]
    rp, op = gap_profile(ref_words["words"], ref_words["duration"]), gap_profile(ours_words, dur)
    if not rp.get("reliable") or not op.get("reliable"):
        return {"status": "MEASURED ONLY", "what": "too few spoken words were found in one of the two to compare pauses", "reference": rp, "ours": op}
    limit = max(rp["p95_gap_sec"], MIN_TIGHTEN_SEC)
    targets = tighten_targets(ours_words, limit)
    res = {"reference": rp, "ours": op, "limit_sec": limit, "proposed": targets,
           "not_emulated": "more cuts per minute means more shots; that needs footage and is left to you"}
    if not targets:
        return {**res, "status": "ALREADY", "what": f"none of our pauses is longer than the reference's longest ordinary gap ({limit:.2f}s)"}
    notes = [{"timeline_sec": t["at"], "text": f"style: tighten this {t['gap']}s pause toward the reference's rhythm", "source": "style", "_style_generated": True} for t in targets]
    ops = [{"note": i, "op": "tighten_pause", "at": t["at"], "why": "longer than the reference's ordinary gaps"} for i, t in enumerate(targets, start=1)]
    rdir = out / "revised_cut"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir.parent / "style_notes.json").write_text(json.dumps({"schema": "review_notes.v0-draft", "sequence": "style", "preview_duration_sec": dur, "notes": notes}, indent=2))
    (rdir.parent / "style_ops.json").write_text(json.dumps(ops, indent=2))
    p = (revise or (lambda *a: run([sys.executable, str(HERE.parent / "review_loop" / "revise.py"), str(a[0]), str(a[1]), "--ops", str(a[2]), "--out", str(a[3])])))(
        ours_xml, rdir.parent / "style_notes.json", rdir.parent / "style_ops.json", rdir)
    changes = json.loads((rdir / "changes.json").read_text()) if (rdir / "changes.json").exists() else None
    applied = [i for i in (changes or {}).get("items", []) if i.get("applied")]
    removed = round(sum(i["removed"][1] - i["removed"][0] for i in applied if i.get("removed")), 2)
    ok = p.returncode == 0 and applied and (rdir / "preview.mp4").exists()
    return {**res, "status": "PLANNED" if ok else "FAILED", "applied": len(applied), "removed_sec": removed, "revised_dir": str(rdir),
            "revised_preview": str(rdir / "preview.mp4") if ok else None, "log": (p.stdout or "")[-1500:] + (p.stderr or "")[-500:],
            "what": f"{len(applied)} of {len(targets)} pauses longer than {limit:.2f}s tightened, {removed}s removed; a new cut in its own folder" if ok
            else "the revise step did not produce a verified cut; nothing was changed"}


def render_final(base: Path, vf: list[str], stem: Path | None, out: Path, size: tuple[int, int] | None) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(base)]
    if stem:
        cmd += ["-i", str(stem)]
    chain = ",".join(vf + ([f"scale={size[0]}:{size[1]}"] if size else []))
    fc = []
    if chain:
        fc.append(f"[0:v]{chain}[v]")
    if stem:
        fc.append("[0:a][1:a]amix=inputs=2:duration=first:normalize=0[a]")
    if fc:
        cmd += ["-filter_complex", ";".join(fc)]
    cmd += ["-map", "[v]" if chain else "0:v", "-map", "[a]" if stem else "0:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out)]
    p = run(cmd)
    if p.returncode != 0 or not out.exists():
        raise EmulateError(f"the final render failed: {p.stderr[-300:]}")
    return out


def emulate(brief: dict, reference: Path, ours_xml: Path, ours_preview: Path, out: Path, dry: bool = False, words_of=rm.transcribe_words,
            generate=ma.generate, revise=None) -> dict:
    aspects = pick_aspects(brief.get("emulate", "all"), brief.get("skip", ()))
    out.mkdir(parents=True, exist_ok=True)
    ref = sp.profile(reference)
    ours = sp.profile(ours_preview)
    result = {"reference": str(reference), "ours_xml": str(ours_xml), "ours_preview": str(ours_preview), "selected": aspects,
              "brief": brief, "reference_profile": ref, "ours_profile": ours, "aspects": {}}
    A = result["aspects"]
    for a in ASPECTS:
        if a not in aspects:
            A[a] = {"status": "NOT SELECTED", "what": "left out of this run"}

    base = ours_preview
    ref_words = None
    if "cuts" in aspects:
        if not ref["has_audio"]:
            A["cuts"] = {"status": "MEASURED ONLY", "what": "the reference video has no audio to read pauses from"}
        else:
            wav = out / "reference_audio_for_words.wav"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(reference), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            ref_words = {"words": words_of(wav), "duration": ref["duration"]}
            A["cuts"] = do_cuts(ref_words, ours_preview, ours_xml, out / "cuts", words_of, revise)
            if A["cuts"].get("revised_preview"):
                base = Path(A["cuts"]["revised_preview"])
    vf: list[str] = []
    base_p = sp.profile(base) if base != ours_preview else ours
    if "color" in aspects:
        A["color"] = do_color(ref, {**base_p, "file": str(base)}, out, float(brief.get("color_strength", COLOR_STRENGTH)))
        vf.append(A["color"]["vf"])
    if "orientation" in aspects:
        A["orientation"] = do_orientation(ref, base_p, base, out, brief.get("orientation", "match"), float(brief.get("reframe_focus", 0.5)))
        if A["orientation"]["vf"]:
            vf.append(A["orientation"]["vf"])
            if base_p["height"] < JUDGE_HEIGHT:                      # a crop of a small preview is tiny; enlarge it so it can be judged (it is softer than a real export)
                vf.append(f"scale=-2:{JUDGE_HEIGHT}:flags=lanczos")
                A["orientation"]["what"] += f". Cropped from a {base_p['width']}x{base_p['height']} preview and enlarged to {JUDGE_HEIGHT} high, so it is softer than a real export would be; in Premiere, Auto Reframe follows the subject"
    stem = None
    if "music" in aspects:
        try:
            ref_path, src = music_source(brief.get("music"), reference, out, (lambda wav: ref_words["words"]) if ref_words else words_of)
            A["music"] = do_music(ref_path, src, base, out / "music", dry, int(brief.get("music_tries", 2)), generate)
            stem = Path(A["music"]["stem"]) if A["music"].get("stem") else None
        except (rm.ReferenceError, EmulateError, ma.AudioError) as e:
            A["music"] = {"status": "FAILED", "what": str(e)}
    if vf or stem:
        final = render_final(base, vf, stem, out / "emulated_preview.mp4", None)
        result["emulated_preview"] = str(final)
        fp = sp.profile(final)
        result["emulated_profile"] = fp
        if "orientation" in aspects and A["orientation"].get("vf"):
            ok = fp["orientation"] == ref["orientation"] if brief.get("orientation", "match") == "match" else fp["orientation"] == brief["orientation"]
            A["orientation"].update(status="EMULATED" if ok else "FAILED", measured=f"{fp['width']}x{fp['height']} ({fp['orientation']})")
        if "color" in aspects:
            graded = render_final(base, [A["color"]["vf"]], None, out / "color_only_preview.mp4", None)       # measured without the crop, which would change the picture's statistics
            before, after = colour_gap(ref["picture"], base_p["picture"]), colour_gap(ref["picture"], sp.profile(graded)["picture"])
            closer = sum(before[k] - after[k] >= max(MATERIAL_SHRINK * before[k], 0.01) for k in before)      # a shrink that re-encode noise could make does not count
            A["color"].update(status="EMULATED" if closer >= 2 else "TRIED, NOT CLOSER", gap_before=before, gap_after=after,
                              measured=f"gap to the reference (brightness, contrast, saturation): before {before}, after {after}")
        if stem and "music" in aspects and A["music"].get("stem"):
            A["music"]["status"] = "EMULATED" if A["music"]["passed"] else "GENERATED, NOT CLOSE ENOUGH"
    for a in ("cuts",):
        if A.get(a, {}).get("status") == "PLANNED":
            A[a]["status"] = "EMULATED" if A[a].get("applied") else "FAILED"
    for a in ("music",):
        if A.get(a, {}).get("status") == "PLANNED" and dry:
            A[a]["status"] = "PLANNED"
    # the aspects that are measured, never reproduced
    if "text" in aspects:
        r_, o_ = ref["picture"], ours["picture"]
        A["text"] = {"status": "MEASURED ONLY", "what": f"text-like detail in the lower third: reference {r_['lower_third_activity']} (heavy on {r_['lower_third_heavy_frames']:.0%} of frames), ours {o_['lower_third_activity']} ({o_['lower_third_heavy_frames']:.0%}). "
                     "No wording is invented, so this is reported; say what text you want with a review note."}
    if "graphics" in aspects:
        A["graphics"] = {"status": "MEASURED ONLY", "what": "no reliable detector for callouts or overlays exists here; the reference's frames are shown below for you to read. A callout is placed from a note that names where and what."}
    if "sfx" in aspects:
        A["sfx"] = {"status": "MEASURED ONLY", "what": "sound effects cannot be separated from voice and music with the tools installed, so their number and type are not measured. Generated effects keep their current style."}
    return result


def render_report(res: dict, out: Path) -> Path:
    ref, ours = res["reference_profile"], res["ours_profile"]
    rows = []
    for a in ASPECTS:
        x = res["aspects"][a]
        extra = ""
        if a == "cuts" and x.get("reference"):
            extra = f"<div class=cap>pauses: reference {json.dumps(x['reference'])}<br>ours {json.dumps(x['ours'])}</div>"
        if a == "music" and x.get("note"):
            extra = f"<div class=cap>reference music: {html.escape(x['source'])}. {html.escape(x['note'])}</div>"
        if x.get("measured"):
            extra += f"<div class=cap>measured: {html.escape(str(x['measured']))}</div>"
        rows.append(f'<tr class="{x["status"].split()[0].lower()}"><td>{html.escape(PLAIN[a])}</td><td class=st>{html.escape(x["status"])}</td><td>{html.escape(x["what"])}{extra}</td></tr>')
    frames = "".join(f'<figure><img src="{u}"><figcaption>{t:.0f}s</figcaption></figure>' for t, u in sp.frames_b64(Path(ref["file"]), ref["duration"], 10))
    vids = (f'<video controls preload="metadata" src="{Path(res["emulated_preview"]).name}" style="max-height:480px"></video>' if res.get("emulated_preview") else "<p>No preview was made (nothing selected changes the picture or sound).</p>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Emulation report</title><style>
:root{{color-scheme:dark;--bg:#0d1420;--panel:#141d2c;--ink:#e8edf5;--mute:#8b98ad;--line:#25324a}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif}}main{{max-width:1050px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:16px;margin:26px 0 8px}}.sub,.cap{{color:var(--mute);font-size:13px}}table{{border-collapse:collapse;width:100%}}
td,th{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}.st{{font-weight:700;white-space:nowrap}}
tr.emulated .st{{color:#38d66b}}tr.measured .st,tr.not .st,tr.already .st,tr.planned .st{{color:var(--mute)}}tr.failed .st,tr.tried .st,tr.generated .st{{color:#f4690b}}
.fr{{display:flex;gap:6px;overflow-x:auto}}figure{{margin:0;flex:none}}img{{height:170px;border-radius:4px;display:block}}figcaption{{color:var(--mute);font-size:11px}}
video{{max-width:100%;border-radius:8px;background:#000}}</style></head><body><main>
<h1>Emulating {html.escape(Path(res['reference']).name)}</h1>
<p class="sub">Reference: {ref['width']}x{ref['height']} {ref['orientation']}, {ref['duration']:.1f}s. Ours: {ours['width']}x{ours['height']} {ours['orientation']}, {ours['duration']:.1f}s. Selected: {html.escape(', '.join(res['selected']))}. The source cut was not changed.</p>
<h2>Result</h2>{vids}
<h2>Aspect by aspect</h2><table><tr><th>aspect</th><th>status</th><th>what was done, and how it was checked</th></tr>{''.join(rows)}</table>
<h2>The reference, sampled</h2><div class="fr">{frames}</div>
<p class="sub">EMULATED = a file was made and re-measured from that file. MEASURED ONLY = nothing could be reproduced automatically. Whether it looks or sounds like the reference is your call.</p>
</main></body></html>"""
    p = out / "emulation_report.html"
    p.write_text(page)
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", type=Path, help="a style_brief.json from the style brief page; flags below override it")
    ap.add_argument("--reference", type=Path)
    ap.add_argument("--ours-xml", type=Path, required=True)
    ap.add_argument("--ours-preview", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--emulate", help="all (default) or a comma list of: " + ", ".join(ASPECTS))
    ap.add_argument("--skip", default="", help="aspects to leave out")
    ap.add_argument("--orientation", choices=["match", "vertical", "horizontal"])
    ap.add_argument("--reframe-focus", type=float)
    ap.add_argument("--music-file", type=Path, help="use this track as the music reference instead of the reference video's music")
    ap.add_argument("--color-strength", type=float)
    ap.add_argument("--no-generate", action="store_true", help="do not call ElevenLabs; show what would be generated")
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    brief = json.loads(a.brief.read_text()) if a.brief else {}
    ref = a.reference or (Path(brief["reference"]).expanduser() if brief.get("reference") else None)
    if not ref:
        print("REFUSING: give --reference or a brief that names one", file=sys.stderr)
        return 1
    if a.emulate:
        brief["emulate"] = a.emulate
    if a.skip:
        brief["skip"] = [s for s in a.skip.split(",") if s]
    for k, v in (("orientation", a.orientation), ("reframe_focus", a.reframe_focus), ("color_strength", a.color_strength)):
        if v is not None:
            brief[k] = v
    if a.music_file:
        brief["music"] = {"from": str(a.music_file)}
    try:
        if not ref.exists():
            raise EmulateError(f"the reference video {ref} is not there")
        if "music" in pick_aspects(brief.get("emulate", "all"), brief.get("skip", ())) and not a.no_generate:
            ma.load_key()
        res = emulate(brief, ref, a.ours_xml, a.ours_preview, a.out, dry=a.no_generate)
    except (EmulateError, sp.StyleError, rm.ReferenceError, ma.AudioError, OSError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    (a.out / "emulation.json").write_text(json.dumps(res, indent=2, default=str))
    page = render_report(res, a.out)
    print(f"reference {res['reference_profile']['orientation']} {res['reference_profile']['width']}x{res['reference_profile']['height']}; ours {res['ours_profile']['orientation']}")
    for k in ASPECTS:
        x = res["aspects"][k]
        print(f"  {x['status']:26s} {PLAIN[k]}: {x['what'][:150]}")
    print(f"\nreport: {page}")
    if a.open:
        subprocess.run(["open", str(page)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
