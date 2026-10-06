"""SUPERSEDED 2026-10-06: this side renderer routes around the pipeline (review page, exporter, overlay, audio tools). Its useful parts were moved into them: speaker timeline -> labs/review_loop/speakers.py, vertical framing -> labs/reframe, the frame-exact encode -> labs/review_loop/render_preview.py. Use labs/recruit/build_vertical_reel.py. Kept only until the title/label graphics and the beat-conformed music are moved into labs/overlay and labs/audio.

Render a resolved pitch as a finished vertical reel (1080x1920), shaped like Ryan's wallpaper reel.

Why this exists: the first Reel 3 was an XML at 16:9 with the camera's own audio, a sound effect and captions, and Ryan
rejected it: "Its not framed like a reel. It doesnt have any kind of clear story. The sfx was ridiculous. The audio
sounds like camera audio. the music can even be heard." This renders the parts the wallpaper reel has
(docs/reference/WALLPAPER_REEL_ANATOMY.md): full-bleed vertical picture cropped on whoever is talking, hard cuts with the
crop alternating wide and tight so the rhythm is visible, a title card with a progressive build and a small joke, bold
step labels built a word at a time in the lower left that clear before the next beat, a music bed that is always there
and measurably audible under the voice, loudness at the reference's -12.5 LUFS, and NO sound effects (the reference has none).

    python3 labs/recruit/render_reel.py --pitch p3 --style labs/recruit/pitches/2026-10-05_reel3_style.json \
        --music "<music_stem.wav>" --out "<folder>"

The voice is the speaker's own recorder (weekend interview), cut at the same camera-time ranges the XML uses and moved
into recorder time by the measured offset. The speaker in the picture is found from motion in each half of the frame
(the person talking moves most: mouth, head, hands). That is a clue, not proof, and it is written to the report.
Nothing here is an XML: the vertical XML for Premiere comes after Ryan has judged this render.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "review_loop"))
import sync_audio as sa  # noqa: E402
OUT_W, OUT_H = 1080, 1920
FPS_NUM, FPS_DEN = 30000, 1001
BRAND_NAVY, BRAND_ORANGE, WHITE = (3, 52, 89), (244, 105, 11), (255, 255, 255)
REFERENCE_LUFS = -12.5                                                                     # the wallpaper reel, measured
VOICE_LUFS = -15.0
MUSIC_UNDER_LU = 4.0                                                                      # the bed sits this far under the voice before ducking (the voice then ducks it a few LU more)
FADE = 0.008                                                                               # seconds, each side of a cut: no click
FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
SIDE_OF = {"Bob": "left", "Mitch": "right"}                                                # who stands where in the two-shot: checked two ways (motion in each half of the frame, and whose own recorder is loudest)
SWITCH_LEAD = 0.12                                                                         # the picture cuts to the next speaker this long before they start talking
SPEAKER_MARGIN_DB, MIN_RUN_SEC, QUIET_DB = 3.0, 0.4, -45.0
SIDE_X = {"left": 0.318, "right": 0.665}                                                   # where each person stands in the two-shot, as a fraction of the width
TIGHT = 0.72                                                                               # the punched-in crop's height as a fraction of the source


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-600:]}")
    return r


def probe_size(path: str) -> tuple[int, int]:
    w, h = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", path]).stdout.strip().split(",")[:2]
    return int(w), int(h)


# ---- who is in frame -------------------------------------------------------------------------------------------------

def side_from_motion(left: float, right: float, margin: float = 1.25) -> str:
    """'left' or 'right' when one half of the frame moves clearly more than the other, else 'unclear'."""
    if left > right * margin:
        return "left"
    if right > left * margin:
        return "right"
    return "unclear"


def speaker_side(path: str, t0: float, dur: float) -> tuple[str, float, float]:
    w, h, fps = 320, 180, 15
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", path, "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    f = np.frombuffer(raw[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(f, axis=0))
    left, right = float(d[:, :, : w // 2].mean()), float(d[:, :, w // 2:].mean())
    return side_from_motion(left, right), left, right


from speakers import level_track, match_lavs, pieces_for_cut, speaker_runs  # noqa: E402,F401  (moved to labs/review_loop/speakers.py)


# ---- the crop --------------------------------------------------------------------------------------------------------

def crop_box(src_w: int, src_h: int, subject_x: float, tight: bool) -> tuple[int, int, int, int]:
    """(w, h, x, y) of a 9:16 crop of the source: the full height ('wide') or TIGHT of it, centred on the subject and kept inside the frame."""
    ch = src_h if not tight else int(round(src_h * TIGHT / 2) * 2)
    cw = int(round(ch * 9 / 16 / 2) * 2)
    x = int(round(min(max(subject_x * src_w - cw / 2, 0), src_w - cw)))
    y = 0 if not tight else int(round(src_h * 0.02))                                          # tight keeps the head in the top third
    return cw, ch, x, min(y, src_h - ch)


# ---- text layers -----------------------------------------------------------------------------------------------------

def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def shadowed(base: Image.Image, xy: tuple[int, int], text: str, size: int, fill: tuple, anchor: str = "la") -> None:
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((xy[0] + 5, xy[1] + 6), text, font=font(size), fill=(0, 0, 0, 200), anchor=anchor)
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(5)))
    ImageDraw.Draw(base).text(xy, text, font=font(size), fill=fill + (255,), anchor=anchor)


def label_states(text: str, t_on: float, t_off: float, step: float = 0.2) -> list[tuple[Image.Image, float, float]]:
    """A step label built a word at a time in the lower left, white heavy caps with a drop shadow, off a little before the beat ends.
    One state per word count; each is on screen from its own time to the next one's (they never overlap, so nothing doubles up)."""
    words = text.upper().split()
    off = t_off - 0.15
    out = []
    for k in range(1, len(words) + 1):
        a = t_on + (k - 1) * step
        b = off if k == len(words) else t_on + k * step
        if b <= a:
            continue
        img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
        lines, line = [], ""
        for w in words[:k]:
            if font(78).getlength((line + " " + w).strip()) > OUT_W - 160 and line:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        lines.append(line)
        y = 1330
        for ln in lines:
            shadowed(img, (70, y), ln, 78, WHITE)
            y += 92
        out.append((img, a, b))
    return out


def title_states(small: str, big: str, joke: str, t_on: float, t_off: float, builds: tuple[float, float] = (0.45, 1.05)) -> list[tuple[Image.Image, float, float]]:
    """The title card: a brand-navy field over the picture (the speaker still faintly there), faint diagonals, the small white line, the big orange word, then the orange parenthetical joke. Cumulative: each state replaces the last."""
    def card(parts: int) -> Image.Image:
        img = Image.new("RGBA", (OUT_W, OUT_H), BRAND_NAVY + (214,))
        d = ImageDraw.Draw(img)
        for x in range(-OUT_H, OUT_W, 150):
            d.line([(x, OUT_H), (x + OUT_H, 0)], fill=(255, 255, 255, 14), width=3)
        if parts >= 1:
            shadowed(img, (OUT_W // 2, 760), small, 70, WHITE, "ma")
        if parts >= 2:
            shadowed(img, (OUT_W // 2, 860), big, 250, BRAND_ORANGE, "ma")
        if parts >= 3:
            shadowed(img, (OUT_W // 2, 1190), joke, 58, BRAND_ORANGE, "ma")
        return img
    marks = [t_on, t_on + builds[0], t_on + builds[1], t_off]
    return [(card(k), marks[k - 1], marks[k]) for k in (1, 2, 3)]


# ---- audio -----------------------------------------------------------------------------------------------------------

def lufs(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?\d+\.\d+) LUFS", r.stderr)
    if not m:
        raise RuntimeError("no loudness reading for " + str(path))
    return float(m[-1])


def music_marks(wav: Path, step: float = 0.25, within_db: float = 6.0) -> dict:
    """Where the music arrives and where it ends, measured on the file: the body level is the median of its louder half; the music has ARRIVED at the first step within `within_db` of that, and has ENDED after the last one.
    A generated track has a soft entrance and a decaying last chord, so its beats are found on the file instead of assumed from the section lengths that were asked for."""
    x = sa.read_window(wav, 0, 600)
    n = int(step * sa.SR)
    db = np.array([20 * np.log10(max(1e-6, float(np.sqrt((x[i * n:(i + 1) * n] ** 2).mean())))) for i in range(len(x) // n)])
    body = float(np.median(np.sort(db)[len(db) // 2:]))
    loud = np.where(db >= body - within_db)[0]
    return {"body_db": round(body, 1), "arrives": round(float(loud[0]) * step, 2), "ends": round(float(loud[-1] + 1) * step, 2), "length": round(len(x) / sa.SR, 2)}


def music_skip(marks: dict, end_at: float) -> float:
    """Seconds of the file to skip so that the music ENDS (its last loud step) at reel time `end_at`; never negative, since the reel cannot start before the music does."""
    return max(0.0, round(marks["ends"] - end_at, 2))


def music_gain_db(voice_lufs: float, music_lufs: float, under: float = MUSIC_UNDER_LU) -> float:
    """The gain that puts the bed `under` LU below the voice."""
    return round(voice_lufs - under - music_lufs, 2)


# ---- the render ------------------------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pitch", required=True)
    ap.add_argument("--style", type=Path, required=True)
    ap.add_argument("--music", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--proof", type=Path, default=Path.home() / "Documents/Post House Reviews/Recruitment footage (proof)")
    a = ap.parse_args()
    proof, out = a.proof, a.out
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)

    style = json.loads(a.style.read_text())
    cuts = [p for p in json.loads((proof / "reels/resolved.json").read_text())["pitches"] if p["key"] == a.pitch][0]["cuts"]
    media = json.loads((proof / "reels/media.json").read_text())
    synced = {d["id"]: d for d in json.loads((proof / "weekend/synced.json").read_text())}
    rows = {r["id"]: r for r in json.loads((proof / "weekend/selects.json").read_text())}
    mic_dir = Path(media["_mics"])

    FR = FPS_DEN / FPS_NUM                                                                  # seconds per frame
    segs, voices, plan, t = [], [], [], 0.0
    lav_cache: dict[tuple, dict] = {}
    for i, c in enumerate(cuts):
        dur = c["out_sec"] - c["in_sec"]
        src = media[c["camera"]]["original"]
        sw, sh = probe_size(src)
        tight = style["tight_roles"].count(c["role"]) > 0
        cam_wav = proof / "weekend/camera_audio" / (c["camera"] + ".wav")
        # who is on which recorder for this stretch of this camera (cached per camera)
        key = (c["camera"],)
        if key not in lav_cache:
            lo = min(x["in_sec"] for x in cuts if x["camera"] == c["camera"]) - 5.0
            hi = max(x["out_sec"] for x in cuts if x["camera"] == c["camera"]) + 5.0
            lav_cache[key] = {"t0": lo, "t1": hi, "lavs": match_lavs(cam_wav, lo, hi, mic_dir) if cam_wav.exists() else {}}
        lc = lav_cache[key]
        two = len(lc["lavs"]) == 2
        if two:
            levels = {who: level_track(f, o + (c["in_sec"] - lc["t0"]), dur) for who, (f, o, _sc) in lc["lavs"].items()}
            runs = speaker_runs(levels)
        else:
            runs = []
        side_motion, lm, rm = speaker_side(media[c["camera"]]["analysis"], c["in_sec"], dur)
        pcs = pieces_for_cut(runs, dur) if runs else []
        if not pcs:                                                                         # no pair of recorders for this camera: one piece, the speaker from motion, the voice from the clip's own recorder
            who = {v: k for k, v in SIDE_OF.items()}.get(side_motion, "Mitch")
            pcs = [(0, int(round(dur / FR)), who)]
        piece_plan = []
        for k, (f0, nf, who) in enumerate(pcs):
            ta, td = c["in_sec"] + f0 * FR, nf * FR
            cw, ch, cx, cy = crop_box(sw, sh, SIDE_X[SIDE_OF[who]], tight)
            seg = work / f"seg_{i}_{k}.mp4"
            # Frame-exact: frame k of the piece is source frame k at the cut. The first version used `fps` alone, which DUPLICATED each piece's first frame (every frame after it showed the picture one frame, 33 ms,
            # late against its voice) and rounded some pieces up a frame. Starting the piece's clock at 0, pinning the fps filter's start, passing frames through, and asking for exactly nf frames fixes all three;
            # the check at the end of this render measures the result.
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ta:.4f}", "-i", src, "-an",
                 "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{OUT_H}:flags=lanczos,setpts=PTS-STARTPTS,fps={FPS_NUM}/{FPS_DEN}:start_time=0,format=yuv420p",
                 "-fps_mode", "passthrough", "-frames:v", str(nf), "-c:v", "libx264", "-bf", "0", "-crf", "15", "-preset", "medium", str(seg)])
            segs.append(seg)
            # the voice: THAT person's own recorder, at the offset measured against the camera
            if two:
                lav, off, _sc = lc["lavs"][who]
                at = off + (ta - lc["t0"])
            else:
                d = synced[c["id"]]["camera_start"] - rows[c["id"]]["speech_start"]
                lav, at = mic_dir / (rows[c["id"]]["clip"] + ".WAV"), ta - d
            v = work / f"voice_{i}_{k}.wav"
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.4f}", "-t", f"{td:.4f}", "-i", lav, "-ac", "1", "-ar", "48000",
                 "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(td - FADE, 0):.4f}:d={FADE}", str(v)])
            voices.append(v)
            piece_plan.append({"person": who, "start": round(t + f0 * FR, 3), "dur": round(td, 3), "voice_file": lav.name, "crop_px": [cw, ch, cx, cy]})
        agree = side_motion == "unclear" or all(SIDE_OF[pp["person"]] == side_motion for pp in piece_plan) if len(piece_plan) == 1 else None
        plan.append({"cut": i + 1, "role": c["role"], "id": c["id"], "camera_in": c["in_sec"], "camera_out": c["out_sec"], "start": round(t, 3), "dur": round(dur, 3),
                     "crop": "tight" if tight else "wide", "pieces": piece_plan, "view_switches": len(piece_plan) - 1,
                     "motion_side_whole_cut": side_motion, "motion_left": round(lm, 2), "motion_right": round(rm, 2), "motion_agrees": agree,
                     "speaker_runs": [(round(a, 2), round(b, 2), w) for a, b, w in runs]})
        t += dur
    total = t

    def concat(files: list[Path], dest: Path, extra: list[str]) -> None:
        lst = work / (dest.stem + ".txt")
        lst.write_text("".join(f"file '{f}'\n" for f in files))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst] + extra + [str(dest)])
    concat(segs, work / "picture.mp4", ["-c", "copy"])
    concat(voices, work / "voice_raw.wav", ["-c", "copy"])

    # graphics layers: title over the setup, labels over the story beats, nothing over the last beat
    layers: list[tuple[Path, float, float]] = []
    n = 0

    def add(states):
        nonlocal n
        for img, ta, tb in states:
            p = work / f"layer_{n}.png"
            img.save(p)
            layers.append((p, ta, tb))
            n += 1
    ti = style["title"]
    first = [p for p in plan if p["role"] == ti["over_role"]][0]
    t_on = first["start"] + ti["delay"]
    builds, events, cm = (0.45, 1.05), [], None
    if style.get("music_conform"):
        # every title word, label and transition is an EVENT the music puts a beat on; the title's words are timed on beats of the music's own tempo
        sys.path.insert(0, str(HERE.parent / "audio"))
        import conform_music as cm
        grid = cm.fit_grid(a.music)
        starts = sorted({round(q["start"], 3) for pp in plan for q in pp["pieces"] if q["start"] > t_on + 1e-6} | {round(pp["start"], 3) for pp in plan if pp["start"] > t_on + 1e-6})
        transitions = [t_on] + starts
        beat = grid["ibi"] / cm.plan(transitions, grid["ibi"])["scale"]
        builds = (beat, 2 * beat)
        events = sorted(set([t_on, t_on + builds[0], t_on + builds[1]] + transitions))
    add(title_states(ti["small"], ti["big"], ti["joke"], t_on, t_on + ti["hold"], builds))
    for p in plan:
        if p["role"] in style["labels"]:
            add(label_states(style["labels"][p["role"]], p["start"] + style.get("label_delay", 0.25), p["start"] + p["dur"]))
    (work / "layers.json").write_text(json.dumps([{"png": str(png), "t_on": round(ta, 4), "t_off": round(tb, 4)} for png, ta, tb in layers], indent=1))     # reel_xml.py rebuilds the graphics as one transparent layer from these
    inputs, chain, last = ["-i", str(work / "picture.mp4")], [], "0:v"
    for k, (png, ta, tb) in enumerate(layers, 1):
        inputs += ["-framerate", f"{FPS_NUM}/{FPS_DEN}", "-loop", "1", "-i", str(png)]
        chain.append(f"[{last}][{k}:v]overlay=enable='between(t,{ta:.3f},{tb:.3f})':eof_action=pass[v{k}]")
        last = f"v{k}"
    run(["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-pix_fmt", "yuv420p", str(work / "graded.mp4")])

    # the audio: voice levelled, the bed set a measured distance under it and ducked a little by the voice, the whole at the reference's loudness
    run(["ffmpeg", "-v", "error", "-y", "-i", work / "voice_raw.wav", "-af", f"highpass=f=90,acompressor=threshold=0.06:ratio=3:attack=5:release=80:makeup=3,loudnorm=I={VOICE_LUFS}:TP=-1.5:LRA=7,aresample=48000", work / "voice.wav"])
    v_l = lufs(work / "voice.wav")
    body = work / "music_body.wav"
    music_in, marks, skip, conf = a.music, None, 0.0, None
    if style.get("music_conform"):
        music_in = work / "music_conformed.wav"                                                           # already on the reel's own clock: silence until the first event, a beat on every event, stopped on the last
        conf = cm.conform(a.music, events, total, music_in)
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{events[0]:.3f}", "-t", f"{events[-1] - events[0]:.3f}", "-i", music_in, body])
        m_l = lufs(body)
        g = music_gain_db(v_l, m_l)
        bed = (f"[1:a]aresample=48000,apad,atrim=0:{total:.3f},volume={g}dB[m];"
               "[0:a]asplit=2[vk][vm];[m][vk]sidechaincompress=threshold=0.1:ratio=2:attack=30:release=400[md]")
    else:
        marks = music_marks(a.music)
        last = plan[-1]
        skip = music_skip(marks, last["start"]) if style.get("music_end_at_last_cut", True) else 0.0       # the groove resolves as the last cut begins, so its last line is bare
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{marks['arrives']}", "-t", f"{marks['ends'] - marks['arrives']:.2f}", "-i", a.music, body])
        m_l = lufs(body)                                                                                  # the level the bed is set by is its body, not its quiet entrance and ending
        g = music_gain_db(v_l, m_l)
        bed = (f"[1:a]aresample=48000,atrim=start={skip},asetpts=PTS-STARTPTS,apad,atrim=0:{total:.3f},volume={g}dB,afade=t=in:d=0.3,afade=t=out:st={total - 0.8:.3f}:d=0.8[m];"
               "[0:a]asplit=2[vk][vm];[m][vk]sidechaincompress=threshold=0.1:ratio=2:attack=30:release=400[md]")
    run(["ffmpeg", "-v", "error", "-y", "-i", work / "voice.wav", "-i", music_in, "-filter_complex", bed + ";[vm]anullsink;[md]anull[o]", "-map", "[o]", work / "bed_only.wav"])
    run(["ffmpeg", "-v", "error", "-y", "-i", work / "voice.wav", "-i", music_in, "-filter_complex", bed + f";[vm][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I={REFERENCE_LUFS}:TP=-1.0:LRA=6,aresample=48000[o]", "-map", "[o]", work / "mix.wav"])
    if conf:
        conf["hits_in_the_bed"] = cm.measure_hits(work / "bed_only.wav", events)                              # measured again on the bed as mixed, not on the conformed file alone
    reel = out / "reel.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", work / "graded.mp4", "-i", work / "mix.wav", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", reel])

    # measured from the finished file
    w, h = probe_size(str(reel))
    dur_out = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", reel]).stdout)
    planned_frames = sum(int(round(q["dur"] / FR)) for p in plan for q in p["pieces"])
    pic_frames = int(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", reel]).stdout.strip())
    aud_sec = float(run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "csv=p=0", reel]).stdout.strip())
    seg_frames = sum(int(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", s]).stdout.strip()) for s in segs)
    av = {"planned_frames": planned_frames, "piece_frames": seg_frames, "reel_picture_frames": pic_frames, "reel_audio_seconds": round(aud_sec, 3), "picture_minus_audio_ms": round((pic_frames * FR - aud_sec) * 1000)}
    if seg_frames != planned_frames or abs(pic_frames - planned_frames) > 1 or abs(pic_frames * FR - aud_sec) > FR * 1.5:
        raise RuntimeError(f"picture and audio disagree: {av} (a piece has the wrong number of frames, or the overlay pass dropped frames); not writing a report for a reel that drifts")
    report = {"reel": reel.name, "size": [w, h], "seconds": round(dur_out, 2), "loudness_lufs": lufs(reel), "reference_lufs": REFERENCE_LUFS,
              "voice_lufs": v_l, "bed_lufs_after_ducking": lufs(work / "bed_only.wav"), "bed_under_voice_lu": round(v_l - lufs(work / "bed_only.wav"), 1),
              "music_file": str(a.music), "music_marks": marks, "music_skip_sec": skip, "music_conformed": conf, "events": events, "av": av, "sound_effects": 0, "cuts": plan, "title": ti, "labels": style["labels"], "layers": len(layers)}
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k not in ("cuts", "labels", "title")}, indent=1))
    for p in plan:
        print(f"  cut {p['cut']} {p['role']:24} {p['dur']:5.1f}s {p['crop']:5} " + " -> ".join(f"{q['person']} {q['dur']:.2f}s" for q in p["pieces"]) + f"   (motion over the whole cut: {p['motion_side_whole_cut']}, agrees: {p['motion_agrees']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
