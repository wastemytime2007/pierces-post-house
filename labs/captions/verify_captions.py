#!/usr/bin/env python3
"""Check a built captions folder from the rendered files and an independent transcript.

    python3 labs/captions/verify_captions.py <folder>

  MOV-ALPHA            ProRes with real alpha at the sequence's size and frame rate, right length
  TRANSPARENT-BETWEEN  nothing is drawn in a gap between caption lines
  EVERY-LINE-VISIBLE   every caption line actually appears when it should
  PLACEMENT            each line sits in the band it was planned for, inside the frame margins
  WORD-BY-WORD         the highlight moves left to right through a line, word by word
  NO-COLLISION         where a callout overlay is on screen, no pixel of it is covered by a caption
  VERBATIM-TEXT        the lines are exactly the transcribed words, with no dashes
  NO-LOOPS             the transcript has no Whisper repetition loops (verified-quotes loop detector)
  INDEPENDENT-TRANSCRIPT   a different Whisper model, run on the same audio, agrees with the captions
  QUOTES-VERIFIED      the verified-quotes checker's counts for the caption lines (informational)
Exit 0 = every gating check passed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "overlay"))
sys.path.insert(0, str(HERE.parent / "review_loop"))
import verify_overlay as vo  # noqa: E402
import words as words_mod  # noqa: E402

SKILL = Path.home() / ".claude" / "skills" / "verified-quotes" / "scripts"
W = 1920
BLUE = (0, 173, 225)


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def hms(t: float) -> str:
    return srt_time(t).split(",")[0]


def second_opinion(media: Path, start: float, dur: float):
    """The same window transcribed by a different Whisper model (base), as phrases and words in the cut's clock."""
    from precut_pipeline.transcriber import Transcriber
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / "w.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
        tr = Transcriber(model_name="base").transcribe(wav)
    ws = [(w.text, w.start, w.end) for p in tr.phrases for w in p.words]
    return tr.phrases, ws


def agreement(cap_words: list[dict], other: list[tuple]) -> tuple[float, float, float, int]:
    a = [words_mod.tokens(w["text"])[0] if words_mod.tokens(w["text"]) else "" for w in cap_words]
    b = [words_mod.tokens(t)[0] if words_mod.tokens(t) else "" for t, _s, _e in other]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    diffs = [abs(cap_words[i]["start"] - other[j][1]) for blk in sm.get_matching_blocks() for i, j in zip(range(blk.a, blk.a + blk.size), range(blk.b, blk.b + blk.size))]
    med = float(np.median(diffs)) if diffs else float("nan")
    p90 = float(np.percentile(diffs, 90)) if diffs else float("nan")
    return sm.ratio(), med, p90, len(diffs)


def main() -> int:
    d = Path(sys.argv[1])
    cj = json.loads((d / "captions.json").read_text())
    pl = json.loads((d / "placement.json").read_text())
    groups, win0, dur = cj["groups"], cj["window"]["start"], pl["duration_sec"]
    rend = pl["render"]
    rw, rh, rfps = rend["width"], rend["height"], rend["fps"]
    s = rw / pl.get("layout", {}).get("w", W)                      # the layout canvas (portrait is 1080 wide) the render was scaled from
    mov = d / "captions.mov"
    rows: list[tuple[str, bool | None, str]] = []

    m = vo.probe(mov)
    num, den = (int(x) for x in m["r_frame_rate"].split("/"))
    rows.append(("MOV-ALPHA", m["codec_name"] == "prores" and "a" in m["pix_fmt"].replace("yuv", "") and (m["width"], m["height"]) == (rw, rh)
                 and abs(m["duration"] - dur) < 0.1 and abs(num / den - rfps) < 0.01,
                 f"{m['codec_name']} {m['pix_fmt']} {m['width']}x{m['height']} {num / den:.3f}fps {m['duration']:.2f}s (wanted {rw}x{rh} {rfps:.3f}fps {dur:.2f}s)"))

    def fr(t: float) -> np.ndarray:
        return vo.frame(mov, max(0.0, min(t, dur - 0.05)), "rgba", rw, rh)

    gaps = [(groups[k]["show_end"], groups[k + 1]["show_start"]) for k in range(len(groups) - 1) if groups[k + 1]["show_start"] - groups[k]["show_end"] > 0.4]
    if gaps:
        g0, g1 = max(gaps, key=lambda g: g[1] - g[0])
        amax = int(fr((g0 + g1) / 2)[..., 3].max())
        rows.append(("TRANSPARENT-BETWEEN", amax <= 2, f"max alpha {amax} at {(g0 + g1) / 2:.2f}s, in a {g1 - g0:.2f}s gap"))
    else:
        rows.append(("TRANSPARENT-BETWEEN", None, "no gap longer than 0.4s between lines"))

    seen, misplaced, bad_margin = 0, [], []
    bboxes = {}
    for i, g in enumerate(groups):
        t = (g["words"][0]["start"] + g["words"][-1]["end"]) / 2 if len(g["words"]) > 1 else g["words"][0]["start"] + 0.05
        al = fr(t)[..., 3] > 128
        ys, xs = np.where(al)
        if len(ys) < 400 * s * s:
            continue
        seen += 1
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        bboxes[i] = (x0, y0, x1, y1)
        if (g["pos"] == "bottom" and y1 < 0.78 * rh) or (g["pos"] == "top" and y0 > 0.22 * rh):
            misplaced.append(i)
        m40 = 40 * s
        if x0 < m40 or x1 > rw - m40 or y0 < m40 or y1 > rh - m40:
            bad_margin.append(i)
    rows.append(("EVERY-LINE-VISIBLE", seen == len(groups), f"{seen} of {len(groups)} caption lines appear at their own time"))
    rows.append(("PLACEMENT", not misplaced and not bad_margin,
                 f"{len(groups) - len(misplaced) - len(bad_margin)} of {len(groups)} lines in their planned band and inside the margins"
                 + (f"; misplaced {misplaced}, off-margin {bad_margin}" if misplaced or bad_margin else "")))

    single = [g for g in groups if len(g["words"]) >= 4 and (lambda b: b and (b[3] - b[1]) < 0.16 * rh)(bboxes.get(groups.index(g)))]
    if single:
        g = max(single, key=lambda x: len(x["words"]))
        cx = []
        for w in g["words"]:
            f = fr((w["start"] + w["end"]) / 2)
            r, gr, b, a = (f[..., k].astype(int) for k in range(4))
            mask = (a > 128) & (b > 170) & (gr > 120) & (r < 100)
            cx.append(float(np.where(mask)[1].mean()) if mask.sum() > 30 * s * s else float("nan"))
        ok = all(not np.isnan(c) for c in cx) and all(b > a - 2 * s for a, b in zip(cx, cx[1:]))
        rows.append(("WORD-BY-WORD", ok, f"highlight x by word in \"{g['text']}\": " + ", ".join("-" if np.isnan(c) else f"{c / s:.0f}" for c in cx)))
    else:
        rows.append(("WORD-BY-WORD", None, "no single-line group of 4+ words to test"))

    checked = both = hit = 0
    for folder in pl.get("avoid", []):
        op = json.loads((Path(folder) / "placement.json").read_text())
        og, ostart = op["geometry"], op["place_overlay_on_timeline_at_sec"]
        omov = Path(op["overlay_path"])
        orend = op["render"]
        for k in range(12):
            t_abs = ostart + og["t_in"] + 0.5 + k * 0.4
            if t_abs > ostart + og["t_out"]:
                break
            t = t_abs - win0
            live = any(g["show_start"] + 0.1 <= t <= g["show_end"] - 0.1 for g in groups)
            if not live:
                continue
            checked += 1
            ca = fr(t)[..., 3] > 128
            oa = vo.frame(omov, t_abs - ostart, "rgba", orend["width"], orend["height"])[..., 3] > 128
            if oa.shape != ca.shape:
                oa = np.kron(oa, np.ones((ca.shape[0] // oa.shape[0], ca.shape[1] // oa.shape[1]), dtype=bool))
            both += 1
            hit += int((ca & oa).sum())
    rows.append(("NO-COLLISION", None if not both else hit <= 20,
                 "no callout on screen while a caption was showing" if not both else f"{both} moments with a caption and a callout both showing; {hit} pixels overlap"))

    joined_ok = all(g["text"] == " ".join(w["text"] for w in g["words"]) for g in groups)
    dashes = sum(len(re.findall(r"[—–]", g["text"])) for g in groups)
    rows.append(("VERBATIM-TEXT", joined_ok and dashes == 0, f"each line is exactly its transcribed words; {dashes} em/en dashes"))

    base = d / "cut_1080.mp4"
    with tempfile.TemporaryDirectory() as td:
        srt_src = Path(td) / "captions_source.srt"
        cues = "".join(f"{i}\n{srt_time(g['show_start'] + win0)} --> {srt_time(g['show_end'] + win0)}\n{g['text']}\n\n" for i, g in enumerate(groups, start=1))
        srt_src.write_text(cues)
        loops = subprocess.run([sys.executable, str(SKILL / "loop_detector.py"), str(srt_src)], capture_output=True, text=True).stdout
        m_loops = re.search(r"loops:\s*(\d+)", loops)
        n_cues = re.search(r"cues:\s*(\d+)", loops)
        rows.append(("NO-LOOPS", bool(n_cues and int(n_cues.group(1)) > 0 and m_loops and int(m_loops.group(1)) == 0),
                     f"loop detector: {n_cues.group(1) if n_cues else '?'} cues read, {m_loops.group(1) if m_loops else '?'} loops"))

        phrases, other = second_opinion(base, win0, dur)
        cap_words = [w for g in groups for w in g["words"]]
        ratio, med, p90, matched = agreement(cap_words, other)
        rows.append(("INDEPENDENT-TRANSCRIPT", ratio >= 0.85 and med <= 0.15,
                     f"base-model transcript agrees on {ratio:.0%} of words; matched words start a median {med * 1000:.0f}ms apart (90th percentile {p90 * 1000:.0f}ms), {matched} matched"))

        (Path(td) / "independent.srt").write_text("".join(
            f"{i}\n{srt_time(p.start + win0)} --> {srt_time(p.end + win0)}\n{p.text}\n\n" for i, p in enumerate(phrases, start=1)))
        log = Path(td) / "caption_lines.md"
        log.write_text("Source: `independent.srt`\n\n" + "".join(f'- [{hms(g["words"][0]["start"] + win0)}] `independent.srt`: "{g["text"]}"\n' for g in groups))
        subprocess.run([sys.executable, str(SKILL / "verify_quotes.py"), "--transcripts", td, str(log), "--quiet"], capture_output=True, text=True)
        ver = (Path(td) / "caption_lines.verification.md")
        text = ver.read_text() if ver.exists() else ""
        n_ok = len(re.findall(r"\bVERIFIED\b", text))
        n_all = len(groups)
        misses = [ln.strip() for ln in text.splitlines() if re.search(r"NOT_FOUND|TIMECODE_MISMATCH", ln)]
        rows.append(("QUOTES-VERIFIED", None, f"{n_ok} of {n_all} caption lines found verbatim in the independent transcript at their timecode; "
                                              f"the other {n_all - n_ok} differ in wording between the two models" + ("\n      " + "\n      ".join(misses[:6]) if misses else "")))

    gating = {"MOV-ALPHA", "TRANSPARENT-BETWEEN", "EVERY-LINE-VISIBLE", "PLACEMENT", "WORD-BY-WORD", "NO-COLLISION", "VERBATIM-TEXT", "NO-LOOPS", "INDEPENDENT-TRANSCRIPT"}
    wd = max(len(n) for n, _, _ in rows)
    bad = 0
    for n, ok, detail in rows:
        mark = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        bad += (ok is False) and n in gating
        print(f"  [{mark}] {n.ljust(wd)}  {detail}")
    print("\nAll gating checks passed." if not bad else f"\n{bad} check(s) FAILED.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
