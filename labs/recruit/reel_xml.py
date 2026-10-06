"""The Premiere XML for a rendered vertical reel (render_reel.py's output folder), standalone; nothing in app/.

    PRECUT_ROOT=~/precut-checkout python3 labs/recruit/reel_xml.py --render "<Reel N vertical folder>" [--name "Reel 3 - That's on me (vertical)"]

What goes in the timeline, and why it is built this way:
- **V1/A1: one clip per picture piece**, each the exact 1080x1920 crop the render used (the speaker-following framing is baked into the clip) with that piece's levelled voice from the speaker's own
  recorder. The sequence is therefore 1080x1920 at 29.97 with no scale/position metadata at all. The alternative, the camera originals with a Basic Motion scale and centre, was NOT built: PreCut's exporter
  only ever writes scale with a zero centre, and no file in this repo shows what unit or direction Premiere reads the centre in, so the framing would rest on a guess. The price of the baked crops: the
  clips have no handles (a cut can be shortened, not extended) and cannot be re-framed in Premiere.
- **V2: the title card and step labels as ONE transparent layer** (ProRes 4444) made from render_reel's layers, placed by `labs/overlay/place_overlay.py`.
- **A2/A3: the conformed music** (stereo, already on the reel's clock, a beat on every title word, label and transition), placed by `labs/audio/place_audio.py`.
- No sound effects (the reference has none).
The result goes through `safety_net/verify_export.py` (rule 10) and this file's own checks, and is refused if any fail. Not confirmed: that Premiere imports it (nobody has opened it there).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
FPS_NUM, FPS_DEN = 30000, 1001
FR = FPS_DEN / FPS_NUM
OUT_W, OUT_H = 1080, 1920


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed: {(r.stderr or r.stdout)[-700:]}")
    return r


def frames_of(path: Path) -> int:
    return int(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path]).stdout.strip())


def piece_list(report: dict, work: Path) -> list[dict]:
    """Every picture piece in timeline order: its crop file, role and frame count; start frames run on from 0 with no gaps."""
    out, f0 = [], 0
    for c in report["cuts"]:
        for k, q in enumerate(c["pieces"]):
            seg = work / f"seg_{c['cut'] - 1}_{k}.mp4"
            nf = frames_of(seg)
            out.append({"seg": seg, "role": c["role"], "person": q["person"], "frames": nf, "start_frame": f0})
            f0 += nf
    return out


def graphics_command(layers: list[dict], total_frames: int, out: Path) -> list:
    """ffmpeg that lays every title/label state on a transparent 1080x1920 canvas at its own times and writes ProRes 4444 with alpha, exactly `total_frames` long."""
    secs = total_frames * FR
    cmd: list = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={OUT_W}x{OUT_H}:r={FPS_NUM}/{FPS_DEN}:d={secs:.4f},format=rgba"]
    chain, last = [], "0:v"
    for k, ly in enumerate(layers, 1):
        cmd += ["-framerate", f"{FPS_NUM}/{FPS_DEN}", "-loop", "1", "-i", ly["png"]]
        chain.append(f"[{last}][{k}:v]overlay=enable='between(t,{ly['t_on']:.4f},{ly['t_off']:.4f})':format=auto:eof_action=pass[g{k}]")
        last = f"g{k}"
    cmd += ["-filter_complex", ";".join(chain) + f";[{last}]format=yuva444p10le[o]", "-map", "[o]", "-frames:v", total_frames, "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", out]
    return cmd


def alpha_at(mov: Path, t: float) -> float:
    """Fraction of pixels with any alpha at time t (decoded from the file)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", str(mov), "-frames:v", "1", "-vf", "scale=270:480,format=rgba", "-f", "rawvideo", "-"], capture_output=True).stdout
    px = len(raw) // 4
    return float(sum(1 for i in range(3, len(raw), 4) if raw[i] > 8)) / px if px else 0.0


def check_final(xml: Path, pieces: list[dict], total_frames: int, music_name: str, graphics_name: str) -> list[tuple[str, bool, str]]:
    root = ET.parse(xml).getroot()
    seq = root.find(".//sequence")
    rows = []
    w, h = seq.findtext("media/video/format/samplecharacteristics/width"), seq.findtext("media/video/format/samplecharacteristics/height")
    tb, ntsc = seq.findtext("rate/timebase"), seq.findtext("rate/ntsc")
    rows.append(("VERTICAL-SEQUENCE", (w, h) == (str(OUT_W), str(OUT_H)) and tb == "30" and ntsc == "TRUE", f"{w}x{h}, timebase {tb}, ntsc {ntsc}"))
    vt = seq.findall("media/video/track")
    v1 = [ci for ci in vt[0].findall("clipitem")]
    got = [(int(ci.findtext("start")), int(ci.findtext("end"))) for ci in v1]
    want = [(p["start_frame"], p["start_frame"] + p["frames"]) for p in pieces]
    rows.append(("PICTURE-PIECES", got == want, f"{len(got)} picture clips, frames as rendered" if got == want else f"clips {got[:3]}... differ from the render {want[:3]}..."))
    a1 = seq.findall("media/audio/track")[0].findall("clipitem")
    rows.append(("VOICE-PAIRED", [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out"))) for c in a1] ==
                 [(int(c.findtext("start")), int(c.findtext("end")), int(c.findtext("in")), int(c.findtext("out"))) for c in v1], "every voice clip has exactly its picture's start, end, in and out"))
    rows.append(("NO-GAPS", all(got[i][1] == got[i + 1][0] for i in range(len(got) - 1)) and got[0][0] == 0 and got[-1][1] == total_frames, f"cuts run back to back from frame 0 to {total_frames} ({total_frames * FR:.2f} s)"))
    gt = vt[1].findall("clipitem")
    rows.append(("GRAPHICS-LAYER", len(vt) == 2 and len(gt) == 1 and gt[0].findtext("name") == graphics_name and int(gt[0].findtext("start")) == 0 and int(gt[0].findtext("end")) == total_frames, f"{graphics_name} on V2 from frame 0 for {total_frames} frames"))
    at = seq.findall("media/audio/track")
    mt = at[1:]
    rows.append(("MUSIC-TRACKS", len(mt) == 2 and all(len(t.findall("clipitem")) == 1 and t.find("clipitem").findtext("name") == music_name and int(t.find("clipitem").findtext("start")) == 0 for t in mt), f"{music_name} on two new audio tracks (left, right) from frame 0"))
    bad = []
    for fe in root.iter("file"):
        pu = fe.findtext("pathurl")
        if pu and not Path(unquote(pu.replace("file://localhost", ""))).exists():
            bad.append(pu)
    rows.append(("FILES-REACHABLE", not bad, "every file in the XML is where it says" if not bad else f"missing: {bad[:2]}"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--render", type=Path, required=True)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    render = a.render.expanduser()
    work = render / "work"
    rep = json.loads((render / "report.json").read_text())
    layers = json.loads((work / "layers.json").read_text())
    out = a.out or render / "xml"
    media = out / "media"
    media.mkdir(parents=True, exist_ok=True)
    name = a.name or f"{render.name} xml"

    pieces = piece_list(rep, work)
    total = sum(p["frames"] for p in pieces)
    print(f"{len(pieces)} picture pieces, {total} frames = {total * FR:.2f} s")

    # 1. one self-contained clip per piece: the crop (copied, not re-encoded) and that piece's slice of the levelled voice, as PCM so nothing shifts
    segs = []
    for n, p in enumerate(pieces, 1):
        t0, d = p["start_frame"] * FR, p["frames"] * FR
        clip = media / f"clip_{n:02d}.mov"
        run(["ffmpeg", "-v", "error", "-y", "-i", p["seg"], "-ss", f"{t0:.5f}", "-t", f"{d:.5f}", "-i", work / "voice.wav", "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-shortest", clip])
        segs.append({"source_path": str(clip), "in_sec": 0.0, "out_sec": p["frames"] * FR - 0.0004, "label": f"{p['role']} ({p['person']})", "handle_sec": 0.0})

    # 2. the sequence, through PreCut's exporter
    from posthouse.coldfootage import build_coldfootage_xml
    base = out / "01_cuts.xml"
    build_coldfootage_xml({"contract_version": 1, "sequence_name": name, "segments": segs}, base)

    # 3. the graphics as one transparent layer, placed on V2
    gdir = out / "graphics"
    gdir.mkdir(exist_ok=True)
    mov = gdir / "graphics.mov"
    run(graphics_command(layers, total, mov))
    (gdir / "placement.json").write_text(json.dumps({"kind": "graphics", "place_overlay_on_timeline_at_sec": 0.0, "duration_sec": round(total * FR, 4), "overlay": mov.name, "overlay_path": str(mov),
                                                      "render": {"width": OUT_W, "height": OUT_H, "fps": FPS_NUM / FPS_DEN, "fps_arg": f"{FPS_NUM}/{FPS_DEN}"}, "avoid": []}, indent=1))
    with_graphics = out / "02_with_graphics.xml"
    r = subprocess.run([sys.executable, str(REPO / "labs/overlay/place_overlay.py"), str(base), str(gdir), "--out", str(with_graphics)], capture_output=True, text=True, env={**__import__("os").environ})
    print(r.stdout.strip()[-1500:])
    if r.returncode:
        print(r.stderr[-800:], file=sys.stderr)
        print("REFUSING: the graphics layer was not placed")
        return 1

    # 4. the music, placed on two new audio tracks
    adir = out / "audio"
    adir.mkdir(exist_ok=True)
    music = work / "music_conformed.wav"
    dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", music]).stdout)
    (adir / "placement.json").write_text(json.dumps({"kind": "audio", "clips": [{"kind": "music", "name": music.name, "path": str(music), "start_sec": 0.0, "duration_sec": round(dur, 3)}]}, indent=1))
    final = out / f"{re.sub(r'[^A-Za-z0-9 ,.()&-]+', '', name).strip()}.xml"
    r = subprocess.run([sys.executable, str(REPO / "labs/audio/place_audio.py"), str(with_graphics), str(adir), "--out", str(final)], capture_output=True, text=True)
    print(r.stdout.strip()[-1800:])
    if r.returncode:
        print(r.stderr[-800:], file=sys.stderr)
        print("REFUSING: the music was not placed")
        return 1

    # 5. the safety net (rule 10) and this file's own checks
    v = subprocess.run([sys.executable, str(REPO / "safety_net/verify_export.py"), str(final), "--target-sec", f"{total * FR:.2f}"], capture_output=True, text=True)
    print("verify_export:", (v.stdout.strip() or v.stderr.strip())[-1500:])
    rows = check_final(final, pieces, total, music.name, mov.name)
    # the layer itself: something on screen at the title, nothing where the render has no graphics
    t_title = layers[0]["t_on"] + 0.3
    quiet = [t for t in (total * FR - 2.0, total * FR - 4.0) if all(not (ly["t_on"] <= t <= ly["t_off"]) for ly in layers)]
    rows.append(("GRAPHICS-ALPHA", alpha_at(mov, t_title) > 0.05 and all(alpha_at(mov, t) < 0.001 for t in quiet),
                 f"{alpha_at(mov, t_title) * 100:.0f}% of the frame has graphics at {t_title:.2f} s; none at {', '.join(f'{t:.1f} s' for t in quiet)}"))
    sil = []
    for n in range(1, len(pieces) + 1):
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(media / f"clip_{n:02d}.mov"), "-af", "volumedetect", "-vn", "-f", "null", "-"], capture_output=True, text=True).stderr
        m = re.search(r"mean_volume: (-?[\d.]+) dB", err)
        if not m or float(m.group(1)) < -45:
            sil.append(n)
    rows.append(("VOICE-NOT-SILENT", not sil, "every clip's voice is audible" if not sil else f"clips {sil} are silent"))
    ok = v.returncode == 0
    for nm, good, why in rows:
        print(f"  [{'PASS' if good else 'FAIL'}] {nm:18} {why}")
        ok = ok and good
    (out / "report.json").write_text(json.dumps({"xml": str(final), "pieces": [{"file": f"clip_{i:02d}.mov", "role": p["role"], "person": p["person"], "frames": p["frames"], "start_frame": p["start_frame"]} for i, p in enumerate(pieces, 1)],
                                                  "frames": total, "seconds": round(total * FR, 3), "checks": [{"name": n, "pass": g, "detail": w} for n, g, w in rows], "verify_export_ok": v.returncode == 0}, indent=1))
    print(("ALL CHECKS PASSED -> " if ok else "SOME CHECKS FAILED -> ") + str(final))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
