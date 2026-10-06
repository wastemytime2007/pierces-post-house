"""One reel as a vertical Premiere XML, through the built pipeline: standalone; nothing in app/.

    PRECUT_ROOT=~/precut-checkout python3 labs/recruit/build_vertical_reel.py --pitch p3 --style labs/recruit/pitches/2026-10-05_reel3_style.json --out "<folder>"

The steps are the pipeline's own, not a parallel one:
 1. the resolved cuts (`rough_cut.py`) are split where the speaker changes (`labs/review_loop/speakers.py`: whichever person's own recorder is louder; the picture cuts to them just before they talk),
 2. PreCut's exporter builds the sequence through `posthouse.coldfootage` (door 3) from the camera ORIGINALS, handed an audio-sync state for both people's recorders, so PreCut itself writes each microphone on its own
    audio track at the measured offset and mutes the camera audio,
 3. `labs/reframe/reframe_xml.py` makes it vertical with Premiere's own Basic Motion (scale and position) on each original clip, keeping only the speaker's microphone live in each piece,
 4. `safety_net/verify_export.py` (rule 10) and the reframe checks gate the result.
Graphics and music are placed on this XML afterwards by `labs/overlay` and `labs/audio`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE.parent / "review_loop"))
sys.path.insert(0, str(HERE.parent / "reframe"))
FR = 1001 / 30000
SOURCE_W, SOURCE_H = 3840, 2160
OUT_W, OUT_H = 1080, 1920
SIDE_X = {"Bob": 0.318, "Mitch": 0.665}              # where each person stands in the two-shot, as a fraction of the width (the left person is Bob, the right is Mitch; checked by motion and by whose recorder is loudest)
TIGHT = 0.72                                          # a tight shot shows this fraction of the source height
HEAD_TOP_Y = {"Mitch": 215, "Bob": 390}              # the top of each person's head in the 3840x2160 two-shot (source pixels), from the frames Ryan's notes pointed at (Mitch's is at about 220 to 232; 215 allows for him leaning up)
HEADROOM = 0.06                                       # a tight shot leaves this fraction of its height above the head: with the window centred on the frame (y = 1080) it started at y = 302 and cut Mitch's head off (Ryan's notes 2 and 3)


def subject_y_for(person: str, scale_pct: float) -> float:
    """The source row to put mid-frame: the frame's own centre for a full-height shot, and for a punched-in one the row that leaves HEADROOM above the person's head (kept inside the clip)."""
    window = OUT_H / (scale_pct / 100.0)
    if window >= SOURCE_H - 1:
        return SOURCE_H / 2
    y = HEAD_TOP_Y[person] - HEADROOM * window + window / 2
    return round(min(max(y, window / 2), SOURCE_H - window / 2))


def lav_state(matches: dict[str, dict], media: dict):
    """A PreCut AudioSyncState from the recorder matches: one SyncPair per (camera original, recorder) and one TrackGroup per person."""
    from posthouse.precut_bridge import import_precut
    asy = import_precut("precut_pipeline.audio_sync")
    pairs, files = [], {}
    for camera, m in matches.items():
        for person, (lav, off, score) in m["lavs"].items():
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(lav)], capture_output=True, text=True).stdout)
            pairs.append(asy.SyncPair(aroll_file=media[camera]["original"], aroll_proxy=media[camera]["analysis"], audio_file=str(lav), offset_sec=m["t0"] - off,
                                      score=float(score), audio_duration_sec=dur, computed_at=time.time()))
            files.setdefault(person, []).append(str(lav))
    groups = [asy.TrackGroup(group_id=p, display_name=p, audio_files=sorted(set(f))) for p, f in sorted(files.items())]
    return asy.AudioSyncState(pairs=pairs, groups=groups, source_hash="recruit", computed_at=time.time())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pitch", required=True)
    ap.add_argument("--style", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--proof", type=Path, default=Path.home() / "Documents/Post House Reviews/Recruitment footage (proof)")
    ap.add_argument("--resolved", type=Path, help="the resolved cuts to use (rough_cut.py's output); default <proof>/reels/resolved.json")
    ap.add_argument("--captions", action="store_true", help="captions of what is said (labs/captions, portrait), placed on V2: the on-screen text")
    ap.add_argument("--no-bleep", action="store_true", help="skip the bleep pass (the standing rule is that every cut is bleeped; only for a cut with no speech worth scanning)")
    ap.add_argument("--graphics", type=Path, help="a make_title.py spec: the title card and step labels, placed on V2 by labs/overlay")
    ap.add_argument("--music-track", type=Path, help="a generated track (labs/audio/score_music.py): conformed so a beat lands on every title word and transition, mixed as a steady bed with no sound effect, placed by labs/audio")
    ap.add_argument("--music-reference", type=Path, help="the track whose measured feel the music must match (make_audio re-measures the finished stem against it)")
    a = ap.parse_args()
    import speakers
    import reframe_xml
    from posthouse.coldfootage import build_coldfootage_xml

    style = json.loads(a.style.read_text())
    proof = a.proof
    cuts = [p for p in json.loads((a.resolved or proof / "reels/resolved.json").read_text())["pitches"] if p["key"] == a.pitch][0]["cuts"]
    media = json.loads((proof / "reels/media.json").read_text())
    mic_dir = Path(media["_mics"])
    a.out.mkdir(parents=True, exist_ok=True)

    # 1. who is on which recorder, per camera (the span of the cuts on that camera, with 5 s either side)
    matches: dict[str, dict] = {}
    for c in cuts:
        cam = c["camera"]
        if cam in matches:
            continue
        mine = [x for x in cuts if x["camera"] == cam]
        t0, t1 = min(x["in_sec"] for x in mine) - 5.0, max(x["out_sec"] for x in mine) + 5.0
        wav = proof / "weekend/camera_audio" / (cam + ".wav")
        lavs = speakers.match_lavs(wav, t0, t1, mic_dir) if wav.exists() else {}
        matches[cam] = {"t0": t0, "lavs": lavs}
        print(f"{cam}: recorders matched {{" + ", ".join(f'{p}: {Path(v[0]).name} (score {v[2]:.0f})' for p, v in lavs.items()) + "}")
    if any(len(m["lavs"]) < 2 for m in matches.values()):
        print("REFUSING: this reel has a camera with fewer than two matched recorders, so there is no speaker timeline to reframe from (the May 15 interview has no separate recorders in the proof folder)", file=sys.stderr)
        return 1

    # 2. the pieces: each cut split at the speaker changes, on whole frames
    pieces, segs, cut_frames = [], [], []
    for c in cuts:
        dur = c["out_sec"] - c["in_sec"]
        pcs, runs = speakers.cut_pieces(matches[c["camera"]]["lavs"], matches[c["camera"]]["t0"], c["in_sec"], dur)
        cut_frames.append(sum(nf for _f0, nf, _w in pcs))
        for f0, nf, who in pcs:
            ta = c["in_sec"] + f0 * FR
            segs.append({"source_path": c["source_original"], "in_sec": ta, "out_sec": ta + nf * FR, "label": f"{c['role']} ({who})", "handle_sec": 0.0})
            pieces.append({"role": c["role"], "person": who, "frames": nf, "tight": c["role"] in style["tight_roles"]})
    print(f"{len(cuts)} cuts -> {len(pieces)} pieces: " + ", ".join(f"{p['person']} {p['frames']}f" for p in pieces))

    # 3. PreCut's exporter, with both recorders synced (door 3 + the audio-sync state)
    base = a.out / "01_exporter.xml"
    build_coldfootage_xml({"contract_version": 1, "sequence_name": f"Reel {a.pitch} (vertical)", "segments": segs}, base, audio_sync_state=lav_state(matches, media))
    root = ET.parse(base).getroot()
    seq = [s for s in root.iter("sequence") if s.find("media/video/track") is not None and s.find("media/audio") is not None][0]
    v1 = seq.find("media/video/track").findall("clipitem")
    if len(v1) != len(pieces):
        print(f"REFUSING: the exporter wrote {len(v1)} picture clips for {len(pieces)} pieces", file=sys.stderr)
        return 1
    got = [int(c.findtext("end")) - int(c.findtext("start")) for c in v1]
    if got != [p["frames"] for p in pieces]:
        print(f"REFUSING: the exporter's clip lengths {got} differ from the planned frames {[p['frames'] for p in pieces]}", file=sys.stderr)
        return 1

    # 4. the reframe plan from what the exporter wrote
    plan_pieces = []
    for p, ci in zip(pieces, v1):
        scale = round(OUT_H / (SOURCE_H * (TIGHT if p["tight"] else 1.0)) * 100, 2)
        plan_pieces.append({"start": int(ci.findtext("start")), "end": int(ci.findtext("end")), "person": p["person"], "subject_x": round(SIDE_X[p["person"]] * SOURCE_W), "scale": scale, "subject_y": subject_y_for(p["person"], scale)})
    mics = {Path(str(lav)).name: person for m in matches.values() for person, (lav, _o, _s) in m["lavs"].items()}
    plan = {"source_width": SOURCE_W, "source_height": SOURCE_H, "pieces": plan_pieces, "mics": mics}
    (a.out / "reframe_plan.json").write_text(json.dumps(plan, indent=1))
    final = a.out / f"Reel {a.pitch} vertical.xml"
    r = subprocess.run([sys.executable, str(REPO / "labs/reframe/reframe_xml.py"), str(base), str(a.out / "reframe_plan.json"), "--out", str(final)], capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode:
        print(r.stderr[-800:], file=sys.stderr)
        return 1
    print(f"-> {final}")
    if not (a.graphics or a.captions or a.music_track or not a.no_bleep):
        return 0

    def tool(*cmd) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, *map(str, cmd)], capture_output=True, text=True)
        keep = [l for l in r.stdout.splitlines() if not l.startswith(("!", "POSTHOUSE", "  Could not read", "  Is PRECUT", "[overlay]"))]
        print("\n".join(keep[-24:]))
        if r.returncode:
            print(r.stderr[-1200:], file=sys.stderr)
        return r
    cur = final
    parts: list[str] = []                                                      # the steps that actually ran, for the final file's name
    # 5. the title card and step labels: labs/overlay (HyperFrames), anchored to source frames, placed on V2
    if a.graphics:
        print("\n== graphics (labs/overlay/make_title.py, place_overlay.py)")
        if tool(REPO / "labs/overlay/make_title.py", "--xml", cur, "--spec", a.graphics, "--out", a.out / "graphics").returncode:
            return 1
        nxt = a.out / f"Reel {a.pitch} vertical + graphics.xml"
        if tool(REPO / "labs/overlay/place_overlay.py", cur, a.out / "graphics", "--out", nxt).returncode:
            return 1
        cur = nxt
        parts.append("graphics")
    # 5b. captions of what is said: labs/captions (portrait), placed on V2 (or the next free picture track)
    if a.captions:
        import timeline as _tl
        zone = _tl.load_cut(cur).zone_end
        print("\n== captions (labs/captions/make_captions.py, place_overlay.py)")
        if tool(REPO / "labs/captions/make_captions.py", "--xml", cur, "--out", a.out / "captions", "--start", "0", "--end", f"{zone - 0.001:.3f}").returncode:
            return 1
        nxt = a.out / f"Reel {a.pitch} vertical + captions.xml"
        if tool(REPO / "labs/overlay/place_overlay.py", cur, a.out / "captions", "--out", nxt).returncode:
            return 1
        cur = nxt
        parts.append("captions")
        # every cut must start and end on the words it was meant to (read from what the captions heard in the finished cut)
        sys.path.insert(0, str(HERE))
        import check_edges
        t_acc, spans = 0.0, []
        for n in cut_frames:
            spans.append((t_acc, t_acc + n * FR))
            t_acc += n * FR
        rows = check_edges.edge_rows(cuts, spans, check_edges.caption_words(a.out / "captions"))
        for name, ok, why in rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:40} {why}")
        if not all(ok for _n, ok, _w in rows):
            print("REFUSING: a cut starts or ends on the wrong word (see above). Nudge it with in_nudge / out_nudge in the cuts file and run again.", file=sys.stderr)
            return 1
    # 6. the music: events -> tone match -> conform (from the first frame to the last) -> make_audio as a bed with no effect -> place_audio
    if a.music_track:
        print("\n== music (labs/audio/conform_music.py, make_audio.py --mix-style bed --no-sfx, place_audio.py)")
        if tool(REPO / "labs/review_loop/build_review.py", cur, "--out", a.out / "review_for_audio", "--height", "960").returncode:
            return 1
        sys.path.insert(0, str(REPO / "labs/review_loop"))
        import timeline
        cut = timeline.load_cut(cur)
        events = [0.0] + sorted({round(v.tl_start, 4) for v in cut.video if v.tl_start > 0})       # the music starts with the reel (a beat on frame 0) and has a beat on every transition
        (a.out / "events.json").write_text(json.dumps({"events": [{"t": e} for e in events]}, indent=1))
        (a.out / "audio_in").mkdir(exist_ok=True)
        track = a.music_track
        if a.music_reference:                                                                   # the least EQ that brings the track's tone to the reference's, re-measured
            sys.path.insert(0, str(REPO / "labs/audio"))
            import reference_music as _rm
            import tone_match as _tm
            ref_feats = _rm.analyze(a.music_reference)
            matched = a.out / "audio_in/music_matched.wav"
            tm = _tm.match_tone(a.music_track, ref_feats, matched)
            print(f"tone match: {tm['tone_before']} Hz -> {tm['tone_after']} Hz with a {tm['cut_db']:.0f} dB low-shelf cut (reference {tm['reference_tone']} Hz)")
            (a.out / "audio_in/tone_match.json").write_text(json.dumps(tm, indent=1))
            track = matched
        conformed = a.out / "audio_in/music_conformed.wav"
        if tool(REPO / "labs/audio/conform_music.py", "--music", track, "--events", a.out / "events.json", "--total", f"{cut.zone_end:.4f}", "--out", conformed, "--tail", "run").returncode:
            return 1
        args = ["--xml", cur, "--base", a.out / "review_for_audio/preview.mp4", "--out", a.out / "audio", "--start", "0", "--end", f"{cut.zone_end - 0.001:.3f}", "--no-sfx", "--mix-style", "bed", "--music-db", "-6", "--music-file", conformed]
        if a.music_reference:
            args += ["--music-reference", a.music_reference]
        if tool(REPO / "labs/audio/make_audio.py", *args).returncode:
            return 1
        nxt = a.out / f"Reel {a.pitch} vertical + graphics + music.xml"
        if tool(REPO / "labs/audio/place_audio.py", cur, a.out / "audio", "--out", nxt).returncode:
            return 1
        cur = nxt
        parts.append("music")
    # 7. the bleep, last (the standing rule: every cut is scanned and every listed word bleeped): labs/bleep silences the word and lays the bleep
    if not a.no_bleep:
        print("\n== bleep (labs/bleep/bleep.py)")
        r = tool(REPO / "labs/bleep/bleep.py", "--xml", cur, "--out", a.out / "bleep")
        if r.returncode:
            return 1
        out_xml = a.out / "bleep" / cur.name
        if out_xml.exists():
            cur = out_xml
            parts.append("bleep")
        else:
            print("nothing was bleeped (no listed word was heard)")
    named = a.out / f"Reel {a.pitch} vertical + {' + '.join(parts)}.xml" if parts else cur
    if named != cur:
        import shutil
        shutil.copy(cur, named)
        cur = named
    print(f"\nfinal -> {cur}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
