"""Finish a cut: captions, music (and an effect if asked), bleep, each by the tool that already does it, chained the way `labs/recruit/build_vertical_reel.py` chains them.

    PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/finish_cut.py <cut.xml> --out <folder> [--no-captions] [--no-music] [--no-bleep] [--sfx-at SEC] [--music-reference FILE]

Order (Ryan's rulings, STATUS and labs/recruit/README.md): captions first (the bleep silences a word, and the captions must still hear it), then music as a bed under the voice (generated, modelled on the
reference track, a beat on frame 0 and on every cut, no effect unless asked for), then the bleep LAST (every cut is scanned, every listed word bleeped). Each tool's own checks gate its step; a step that
fails stops the run and nothing is kept. The picture is never touched: the finished XML must have the same clips on V1 as the one it started from.

Not idempotent by design: a cut that already carries on-screen layers (`overlay-file-*`) is not captioned again, one that already carries generated audio (`audio-file-*`) gets no second music bed; the
bleep undoes its own earlier pass and can always run again. Writes `<folder>/<name>_v<N+1>.xml` (the next version), the tools' own folders beside it, and `finish.json`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
for sub in ("review_loop", "audio", "overlay", "captions", "bleep", "qa", "reconform"):
    sys.path.insert(0, str(LABS / sub))
sys.path.insert(0, str(LABS.parent / "safety_net"))

import timeline  # noqa: E402

MUSIC_DB = -6.0
MUSIC_STYLE = ("upbeat, driving, steady and full from the first beat to the last; bass-heavy groove, clean drums, no build-up and no fade-in; instrumental")
MUSIC_AVOID = "vocals; sound effects; a slow or quiet start; a long fade out"
REFERENCE_GLOB = "Downloads/Artlist Library/Music/Aves - Bumpin*"


class FinishError(Exception):
    pass


def _tail(text: str, n: int = 12) -> str:
    lines = [l for l in text.splitlines() if l.strip() and "Warning" not in l and "frames/s" not in l]
    pick = [l for l in lines if any(k in l for k in ("REFUSING", "FAIL", "Error", "error"))] or lines
    return "\n".join(pick[-n:])


def reference_track() -> Path | None:
    """The music reference: POSTHOUSE_MUSIC_REFERENCE, else Ryan's Artlist example (Aves - Bumpin'), else none (the music is then made from the style words alone)."""
    env = os.environ.get("POSTHOUSE_MUSIC_REFERENCE")
    if env:
        return Path(env).expanduser() if Path(env).expanduser().is_file() else None
    for d in sorted(Path.home().glob(REFERENCE_GLOB)):
        files = sorted(p for p in d.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".aac", ".flac") and not p.name.startswith("."))
        if files:
            return files[0]
    return None


def layers_present(xml: Path) -> dict[str, bool]:
    """Which finishing layers a cut already carries, by the file ids the placing tools give them (timeline.LAYER_*_PREFIX); `bleep` is true when speech has been muted for one."""
    root = ET.parse(xml).getroot()
    fids = [(c.find("file").get("id") or "") for c in root.iter("clipitem") if c.find("file") is not None]
    ids = [c.get("id") or "" for c in root.iter("clipitem")]
    return {"on_screen": any(f.startswith(timeline.LAYER_VIDEO_PREFIX) for f in fids), "music": any(f.startswith(timeline.LAYER_AUDIO_PREFIX) for f in fids),
            "bleep": any(i.endswith("-bleep") for i in ids)}


TITLE_HOLD = 2.4           # the title card stays on this long
TITLE_BUILDS = [0.0, 0.2, 0.4]
TAG_SEC = 3.2              # a lower third stays on this long
TAG_AFTER_TITLE = 0.3      # and never starts before the title is off
ROLES = {"Bob": "Builder / Founder", "Mitch": "Operator / CEO"}    # the roles Ryan gives them in his brand notes (his standing context); change them here if they are wrong
MIN_TURN_SEC = 1.0         # a person's first turn must run this long to earn a name tag
SFX_AT_START = 0.05
TAG_Y = 0.55               # a lower third's top sits this far down the frame (fraction of the height): above the band the captions use, so the captions never have to jump to the top across the speaker's face


def graphics_plan(cut, mic_dir: str | None, cache: Path | None, analyse=None) -> tuple[dict, list[float], list[str]]:
    """What on-screen graphics this cut gets, from the cut itself: a title card with the topic (the sequence's own name) in the first seconds, and a name tag the first time each person talks for a
    second or more (who talks when comes from each person's own recorder). Nothing is invented: the words are the sequence's name and the speakers' names. Returns (the make_title spec, the timeline
    seconds each graphic comes on, notes in words about anything left out)."""
    notes: list[str] = []
    first = cut.video[0]
    spec: dict = {"title": {"anchor": {"source": Path(first.src_path).name, "source_sec": round(first.src_in, 3)}, "small": cut.sequence_name.strip(), "big": "", "builds": TITLE_BUILDS, "hold": TITLE_HOLD}}
    times = [round(first.tl_start + SFX_AT_START, 3)]
    thirds: list[dict] = []
    if not mic_dir:
        notes.append("no lower thirds: the folder of each person's own recordings is not known, so who is talking cannot be told")
    else:
        import framing
        analyse = analyse or framing.analyse
        seen: dict[str, float] = {}
        try:
            for path in dict.fromkeys(c.src_path for c in cut.video):
                mine = [c for c in cut.video if c.src_path == path]
                res = analyse(path, [{"idx": c.idx, "src_in": c.src_in, "src_out": c.src_out, "fps": cut.fps} for c in mine], mic_dir, cache)
                for c in mine:
                    for s, e, who in res["clips"][c.idx]["runs"]:
                        if who in seen or e - s < MIN_TURN_SEC:
                            continue
                        at = max(c.tl_start + s + 0.15, TITLE_HOLD + TAG_AFTER_TITLE)
                        if at + MIN_TURN_SEC > c.tl_start + e or at + TAG_SEC > c.tl_end:
                            continue
                        seen[who] = at
                        thirds.append({"anchor": {"source": Path(path).name, "source_sec": round(c.src_in + (at - c.tl_start), 3)}, "name": who, "sub": ROLES.get(who, ""), "until": TAG_SEC})
                        times.append(round(at, 3))
        except Exception as e:                                            # a failed look at who talks costs the name tags only, never the title or the rest
            thirds, times = [], times[:1]
            notes.append(f"no lower thirds: who is talking could not be told ({str(e).splitlines()[0][:140]})")
        for who in ("Bob", "Mitch"):
            if thirds and who not in seen:
                notes.append(f"no lower third for {who}: no turn of a second or more was heard in the cut")
    if thirds:
        spec["lower_thirds"] = thirds
        spec["lt_y"] = TAG_Y
    return spec, times, notes


SFX_WORDS = re.compile(r"woosh|whoosh|swoosh|swish|swipe", re.I)
SFX_MAX_SEC = 1.6          # the effect is cut to this with a short fade: a whoosh's tail would run under the speech
SFX_HIT_AT = 0.2           # the effect's peak lands this far into the graphic's wipe


def effect_profile(path: Path) -> dict:
    """Seconds, where it peaks, and how long it stays within 12 dB of its peak (all from the decoded audio)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, dtype="<f4")
    n = 400
    env = [20 * np.log10(max(1e-6, float(np.sqrt((x[i * n:(i + 1) * n] ** 2).mean())))) for i in range(len(x) // n)]
    if not env:
        return {"sec": 0.0, "peak_at": 0.0, "body_sec": 0.0}
    pk = int(np.argmax(env))
    return {"sec": len(x) / 8000, "peak_at": pk * n / 8000, "body_sec": sum(1 for e in env if e > env[pk] - 12) * n / 8000}


def choose_graphic_sfx(roots: list[Path] | None = None, profile=effect_profile) -> dict | None:
    """The library effect for a graphic coming on: a real whoosh, found by measurement, not by asking for "a soft pop" (a faint file the finder once picked decays 8 dB in a tenth of a second and
    is silent after 0.3 s). Candidates are whooshes in the sound-effects library (never the generated store); the best builds to a peak 0.2 to 1.2 s in and holds its body for at least half a second."""
    import sfx_library as sl
    roots = roots or [r for r in sl.default_roots() if r != sl.generated_store()]
    best = None
    for it in sl.index(roots):
        if not SFX_WORDS.search(it["name"]) or it["name"].startswith("Generated"):
            continue
        pr = profile(Path(it["file"]))
        if not (0.2 <= pr["peak_at"] <= 1.2 and pr["body_sec"] >= 0.5 and pr["sec"] >= 0.8):
            continue
        score = pr["body_sec"] - abs(pr["peak_at"] - 0.7) - 0.1 * max(0.0, pr["sec"] - 3.0)
        if best is None or score > best[0]:
            best = (score, it, pr)
    return None if best is None else {"file": best[1]["file"], "name": best[1]["name"], **best[2]}


def prepare_effect(choice: dict, out: Path) -> Path:
    """The chosen effect cut to SFX_MAX_SEC with a fade at its end, as the file make_audio is handed."""
    dest = out / "sfx_graphic.wav"
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", choice["file"], "-t", f"{SFX_MAX_SEC}", "-af", f"afade=t=out:st={SFX_MAX_SEC - 0.3}:d=0.3", "-ar", "48000", "-ac", "2", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise FinishError("the sound effect could not be prepared: " + r.stderr[-200:])
    return dest


def default_bleep(xml: Path, out: Path, requests: list[dict] | None) -> dict:
    import bleep as bp
    return bp.bleep(xml, out, requests=requests or None, detail_of=bp.transcribe_detail, reveal_with=bp.transcribe_timed)


def version_name(src: Path) -> str:
    m = re.match(r"^(.*)_v(\d+)$", src.stem)
    return f"{m.group(1) if m else src.stem}_v{(int(m.group(2)) if m else 1) + 1}.xml"


def run(xml: str | Path, out: str | Path, captions: bool = True, music: bool = True, bleep: bool = True, sfx_at: float | None = None,
        music_reference: str | Path | None = None, progress=lambda s: None, runner=None, rebuild: bool = False, music_file: str | Path | None = None,
        caption_fixes: list[dict] | None = None, bleep_requests: list[dict] | None = None, bleep_fn=None, final_name: str | None = None,
        graphics: bool = False, sfx: bool = False, graphics_fn=None, choose_fn=None) -> dict:
    """Returns {"xml", "folder", "steps": [{"name", "done", "summary"}], "checks": [(name, ok, detail)], "music_raw"}. Raises FinishError, in words, when a step fails.

    `rebuild`: the cut already carries layers and its picture has just been revised under them. The earlier bleep is undone and every layer taken off (what is left is the cut itself, checked to be the
    same cut), then the steps run again on the revised picture. `music_file` is the track the first finish made (before it was fitted to the cuts), so a rebuild never changes the music;
    `caption_fixes` are the caption lines a person corrected, kept through the rebuild; `bleep_requests` are what bleep notes asked for (bleep.requests_from_notes)."""
    src, out = Path(xml).expanduser().resolve(), Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if rebuild:
        import bleep as bp
        import reconform
        unb = out / "_unbleeped.xml"
        eff = unb if bp.strip_previous(src, unb) != (0, 0) else src
        clean = out / "_clean.xml"
        reconform.strip_layers(eff, clean)
        base = clean
    else:
        base = src
    cut0 = timeline.load_cut(base)
    zone = cut0.zone_end
    have = layers_present(base)
    steps: list[dict] = []
    cur = base
    music_raw: str | None = None

    def tool(name: str, script: str, *args) -> None:
        cmd = [sys.executable, str(LABS / script), *map(str, args)]
        p = (runner or subprocess.run)(cmd, capture_output=True, text=True, env=os.environ.copy())
        if p.returncode != 0:
            raise FinishError(f"{name} failed, so nothing was kept. The tool said:\n{_tail(p.stdout + chr(10) + p.stderr)}")

    # 1a. graphics: a title card and name tags (made and placed before the captions, which then keep clear of them)
    sfx_times: list[float] = []
    graphics_folder: Path | None = None
    if graphics and have["on_screen"]:
        steps.append({"name": "graphics", "done": False, "summary": "the cut already has on-screen layers, so no title card or name tags were added"})
    elif graphics:
        progress("Graphics: a title card and lower thirds")
        import follow_speaker as fs
        spec, sfx_times, gnotes = (graphics_fn or graphics_plan)(cut0, fs.mic_dir_of(cut0), Path.home() / "Library" / "Application Support" / "Post House" / "framing_cache.json")
        (out / "graphics_spec.json").write_text(json.dumps(spec, indent=1))
        tool("The title card and lower thirds", "overlay/make_title.py", "--xml", cur, "--spec", out / "graphics_spec.json", "--out", out / "graphics")
        nxt = out / "_step_graphics.xml"
        tool("Placing the title card and lower thirds", "overlay/place_overlay.py", cur, out / "graphics", "--out", nxt)
        cur, graphics_folder = nxt, out / "graphics"
        tags = [l["name"] for l in spec.get("lower_thirds", [])]
        steps.append({"name": "graphics", "done": True, "summary": "a title card with the topic" + (f" and lower thirds for {' and '.join(tags)}" if tags else "") + ("; " + "; ".join(gnotes) if gnotes else "")})

    # 1. captions: what is said, on screen
    if captions and have["on_screen"]:
        steps.append({"name": "captions", "done": False, "summary": "the cut already has on-screen layers, so it was not captioned again"})
    elif captions:
        progress("Captions: listening to the cut and making what is said into on-screen text")
        cargs = ["--xml", cur, "--out", out / "captions", "--start", "0", "--end", f"{zone - 0.001:.3f}"]
        if graphics_folder:
            cargs += ["--avoid", graphics_folder]                          # the captions move clear of the title card and name tags
        if caption_fixes:
            (out / "captions").mkdir(parents=True, exist_ok=True)
            (out / "captions" / "fixes.json").write_text(json.dumps(caption_fixes, indent=2))
            cargs += ["--fixes", out / "captions" / "fixes.json"]
        tool("The captions", "captions/make_captions.py", *cargs)
        nxt = out / "_step_captions.xml"
        tool("Placing the captions", "overlay/place_overlay.py", cur, out / "captions", "--out", nxt)
        cur = nxt
        steps.append({"name": "captions", "done": True, "summary": "captions of what is said, placed above the picture"})

    # 2. music (and an effect when asked): generated, modelled on the reference, a beat on frame 0 and on every cut, a bed under the voice
    if music and have["music"]:
        steps.append({"name": "music", "done": False, "summary": "the cut already has generated audio, so no second music bed was added"})
    elif music:
        progress("Music: describing the feel, generating it, fitting it to every cut (a few minutes, uses the music service)")
        import build_review
        try:
            build_review.build(cur, out / "review_for_audio", height=960)
        except Exception as e:
            raise FinishError(f"Rendering the cut to fit the music to failed, so nothing was kept: {type(e).__name__}: {e}") from e
        ref = Path(music_reference).expanduser() if music_reference else reference_track()
        if music_file:
            ref = None                                               # a rebuild keeps the track it has: nothing is measured or generated again
        events = [0.0] + sorted({round(v.tl_start, 4) for v in timeline.load_cut(cur).video if v.tl_start > 0})
        (out / "events.json").write_text(json.dumps({"events": [{"t": e} for e in events]}, indent=1))
        (out / "audio_in").mkdir(exist_ok=True)
        plan = {"global": MUSIC_STYLE, "avoid": MUSIC_AVOID, "sections": [{"name": "groove", "seconds": float(max(3.0, min(120.0, math.ceil(zone) + 4))), "style": MUSIC_STYLE}]}      # a little more than the cut: fitting it to the cuts takes the tail off, and a track a hair short refuses
        (out / "score.json").write_text(json.dumps(plan, indent=1))
        score_args = ["--plan", out / "score.json", "--out", out / "audio_in/music.wav"]
        feats = None
        if ref and not music_file:
            import reference_music
            feats = reference_music.analyze(ref)
            (out / "audio_in/reference_features.json").write_text(json.dumps(feats, indent=1, default=float))
            score_args += ["--reference-features", out / "audio_in/reference_features.json"]
        if music_file:
            track = Path(music_file).expanduser()
        else:
            tool("The music", "audio/score_music.py", *score_args)
            track = out / "audio_in/music.wav"
        tone_note = ""
        if feats:
            import tone_match
            matched = out / "audio_in/music_matched.wav"
            try:
                tm = tone_match.match_tone(track, feats, matched)
                (out / "audio_in/tone_match.json").write_text(json.dumps(tm, indent=1, default=float))
                track = matched
            except Exception as e:                       # the take is too far from the reference's tone for an EQ to fix (match_tone refuses past 6 dB): use it as it is, and say so
                tone_note = f"; its tone could not be matched to the reference ({str(e).split(':')[0][:110]}), so it is used as generated"
        music_raw = str(track)
        conformed = out / "audio_in/music_conformed.wav"
        tool("Fitting the music to the cuts", "audio/conform_music.py", "--music", track, "--events", out / "events.json", "--total", f"{zone:.4f}", "--out", conformed, "--tail", "run")
        margs = ["--xml", cur, "--base", out / "review_for_audio/preview.mp4", "--out", out / "audio", "--start", "0", "--end", f"{zone - 0.001:.3f}",
                 "--mix-style", "bed", "--music-db", f"{MUSIC_DB:g}", "--music-file", conformed]
        times = ([sfx_at] if sfx_at is not None else []) + (sfx_times if sfx else [])
        sfx_note = ""
        if times:
            choice = (choose_fn or choose_graphic_sfx)()
            if choice:
                lead = min(max(choice["peak_at"] - SFX_HIT_AT, 0.0), 0.8)                  # start early so the effect's peak lands as the graphic wipes in
                times = sorted({round(max(0.0, x - lead), 3) for x in times})
                times = [x for i, x in enumerate(times) if i == 0 or x - times[i - 1] >= SFX_MAX_SEC]      # never two on top of each other
                margs += ["--sfx-file", prepare_effect(choice, out / "audio_in"), "--sfx-below-mean-db", "3"]
                sfx_note = f" ({choice['name'].rsplit('.', 1)[0]})"
            else:
                sfx_note = " (no real whoosh was found in the library, so make_audio's own library lookup chose it)"
        margs += ["--sfx-at", f"{times[0]:g}"] if times else ["--no-sfx"]
        if ref and not tone_note:
            margs += ["--music-reference", ref]
        try:
            tool("Mixing the music under the voice", "audio/make_audio.py", *margs)
        except FinishError as e:
            if "REFERENCE-MATCH" not in str(e) or "--music-reference" not in margs:
                raise
            # the take does not match the reference on the measures make_audio holds it to: mix it anyway, and say so with the numbers instead of hiding it or failing the whole finish
            miss = next((l.strip() for l in str(e).splitlines() if "REFERENCE-MATCH" in l), "")
            margs = [a for i, a in enumerate(margs) if a != "--music-reference" and (i == 0 or margs[i - 1] != "--music-reference")]
            tool("Mixing the music under the voice", "audio/make_audio.py", *margs)
            tone_note += "; it does NOT match the reference's feel (" + re.sub(r"^\[FAIL\]\s*REFERENCE-MATCH\s*", "", miss)[:160] + "), so judge it by ear"
        for name in ("placement.json", "audio.json"):                       # the same library effect on each remaining graphic: more entries of the one effect file, which place_audio puts on one pair of tracks
            f = out / "audio" / name
            if len(times) > 1 and f.is_file():
                meta = json.loads(f.read_text())
                tmpl = next((c for c in meta["clips"] if c["kind"] == "sfx"), None)
                if tmpl:
                    meta["clips"] += [{**tmpl, "start_sec": float(x)} for x in times[1:]]
                    f.write_text(json.dumps(meta, indent=1))
        nxt = out / "_step_music.xml"
        tool("Placing the music", "audio/place_audio.py", cur, out / "audio", "--out", nxt)
        cur = nxt
        steps.append({"name": "music", "done": True,
                      "summary": ("the same music as before, refitted to the revised cuts" if music_file else "a music bed modelled on " + ref.parent.name if ref else "a music bed from the style words (no reference track was found)")
                      + (f", with a sound effect on each graphic ({len(times)}){sfx_note}" if times else ", no sound effect") + tone_note})

    if sfx and not music and graphics:
        steps.append({"name": "sfx", "done": False, "summary": "the sound effects are mixed and placed with the music step, and the music is off, so none were added"})

    # 3. the bleep, last: every listed word is silenced and a bleep laid on it
    if bleep:
        progress("Bleep: listening for listed words (takes a few minutes)")
        try:
            res = (bleep_fn or default_bleep)(cur, out / "bleep", bleep_requests)
        except Exception as e:
            raise FinishError(f"The bleep failed, so nothing was kept: {type(e).__name__}: {e}") from e
        bad = [n for n, ok, _d in res.get("rows", []) if ok is False]
        if bad:
            raise FinishError(f"The bleep failed its own checks ({', '.join(bad)}), so nothing was kept; see {out / 'bleep'}")
        if res.get("xml"):
            cur = Path(res["xml"])
            hits = ", ".join(f"{h['word']} at {h['start']:.1f}s" for h in res.get("hits", []))
            steps.append({"name": "bleep", "done": True, "summary": f"{len(res.get('spans', []))} stretch(es) silenced and bleeped" + (f": {hits}" if hits else "")})
        else:
            steps.append({"name": "bleep", "done": True, "summary": "listened, and no listed word was heard, so nothing was bleeped"})

    final = out / (final_name or version_name(src))
    if cur == base and not rebuild:
        raise FinishError("nothing was asked for, or everything asked for was already on the cut, so there is nothing to write")
    shutil.copy(cur, final)
    for tmp in list(out.glob("_step_*.xml")) + [out / "_unbleeped.xml", out / "_clean.xml"]:
        tmp.unlink(missing_ok=True)
    checks = check(base if not rebuild else src, final, zone)
    result = {"xml": str(final), "folder": str(out), "steps": steps, "checks": checks, "music_raw": music_raw or (str(music_file) if music_file else None)}
    (out / "finish.json").write_text(json.dumps({**{k: v for k, v in result.items() if k != "checks"}, "checks": checks,
                                                 "options": {"captions": captions, "music": music, "bleep": bleep, "graphics": graphics, "sfx": sfx}}, indent=1, default=str))
    return result


def check(before: Path, after: Path, zone: float) -> list[tuple[str, bool | None, str]]:
    """The finished XML read back: it loads, the picture and length are exactly what they were, and the whole-file export checks pass."""
    rows: list[tuple[str, bool | None, str]] = []
    c1, c2 = timeline.load_cut(before), timeline.load_cut(after)
    same = len(c1.video) == len(c2.video) and all(abs(a.tl_start - b.tl_start) < 1e-3 and abs(a.tl_end - b.tl_end) < 1e-3 and a.src_path == b.src_path
                                                 and abs(a.src_in - b.src_in) < 1e-3 and abs(a.src_out - b.src_out) < 1e-3 and a.motion == b.motion for a, b in zip(c1.video, c2.video))
    rows.append(("PICTURE-UNCHANGED", same, f"{len(c2.video)} clips on V1, the same ranges, positions and framing as before" if same else "the picture changed, which finishing must never do"))
    rows.append(("LENGTH-UNCHANGED", abs(c1.zone_end - c2.zone_end) < 0.01, f"{c2.zone_end:.2f}s (was {c1.zone_end:.2f}s)"))
    import verify_export
    rep = verify_export.Report()
    verify_export.check_xml(after, rep)
    rep0 = verify_export.Report()                                          # held to what the cut already passed: a defect it came in with is reported, not blamed on the finish
    verify_export.check_xml(before, rep0)
    inherited = {n for n, ok, _d in rep0.rows if ok is False}
    for n, ok, d in rep.rows:
        if ok is False and n in inherited:
            rows.append(("verify_export " + n, None, d + "  [ALREADY FAILING on the cut this was made from, so not caused by finishing; the export itself needs fixing]"))
        else:
            rows.append(("verify_export " + n, ok, d))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xml", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-bleep", action="store_true")
    ap.add_argument("--sfx-at", type=float, help="put one sound effect (library first, generated only if missing) at this time in the cut; none by default")
    ap.add_argument("--graphics", action="store_true", help="a title card with the topic and a name tag the first time each person talks")
    ap.add_argument("--sfx", action="store_true", help="a library sound effect as each of those graphics comes on (needs the music step: the effects are mixed and placed with it)")
    ap.add_argument("--music-reference", type=Path)
    a = ap.parse_args()
    try:
        r = run(a.xml, a.out, not a.no_captions, not a.no_music, not a.no_bleep, a.sfx_at, a.music_reference, progress=lambda s: print(f"== {s}", flush=True), graphics=a.graphics, sfx=a.sfx)
    except (FinishError, timeline.TimelineError) as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 1
    for s in r["steps"]:
        print(f"  [{'DONE' if s['done'] else 'SKIP'}] {s['name']}: {s['summary']}")
    bad = 0
    for n, ok, d in r["checks"]:
        print(f"  [{'PASS' if ok else 'FAIL' if ok is False else 'SKIP'}] {n}  {d}")
        bad += ok is False
    print(f"\n{r['xml']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
