#!/usr/bin/env python3
"""The clickable testing guide, written next to the review folders, with every link checked to exist.

    python3 labs/project/testing_page.py --root "~/Documents/Post House Reviews" [--open]

Writes `<root>/TESTING - creator workflow.html`. The content mirrors `labs/TESTING.md`; here each output is a link. The script
refuses to write a page that points at a file that is not there, so the guide cannot go stale silently.
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

P = "Runnells Tiling v3 - "
E = P + "reconformed v4 E (image card)"
ART = Path("~/Downloads/Artlist Library/Music/Barrell - Takin' a Walk/Barrell - Takin' a Walk.mp3").expanduser()

TESTS = [
    ("1", "The overview", "Does it tell you what exists and what each round found? Are the stand-in rounds clearly marked?",
     [("Project index", "Runnells Tiling - project index.html")]),
    ("2", "Review page: click any box to leave a note on it", "NEW. Click a box on the timeline map (a sound effect, a clip, the callout, the image card, a caption line) and write a note on that whole element, no playhead needed. Shift+click only jumps there. Download JSON: does each note name its element? Also still there: Layers on/off, drawing.",
     [("Review page (new)", "Runnells Tiling v3 - review page (click any box to leave a note)/review.html"), ("Review page (before)", f"{E}/review/review.html")]),
    ("3", "Keep a graphic on screen longer (stand-in note)", "The callout (12.2-18.7 s on the page) now stays about 1.5 s longer. Is 1.5 s the right default step when a note gives no amount?",
     [("Callout preview", P + "longer callout (stand-in)/callout/overlay_preview.mp4"), ("QA report", P + "QA pass (longer callout)/qa_report.html")]),
    ("4", "Change a callout's words (stand-in note)", 'It should read "Cardboard spacer" with the small line gone.',
     [("Callout preview", P + "callout words (stand-in)/callout/overlay_preview.mp4"), ("QA report", P + "QA pass (callout words)/qa_report.html")]),
    ("5", "Image card: border and highlight a screenshot", "On the review page at 29.5-33.3 s: the whole image, blue border, orange box around the spacer, caption. Is this what you want?",
     [("Source image", P + "image card (real frame)/screenshot.png"), ("Review page", f"{E}/review/review.html")]),
    ("6", "Replace a sound effect from a note", 'At 13.97 s: does the new effect sound like "something being highlighted on a piece of paper"? Compare with the original.',
     [("New effect (v2)", P + "sfx and music v2 (0-22s)/audio_preview.mp4"), ("Original", P + "sfx and music (0-22s)/audio_preview.mp4")]),
    ("7", "Music like a reference track", "Honest result: tempo and rhythm matched, tone and dynamics did not. Listen to the generated take next to the reference in your Artlist library (Barrell, Takin' a Walk); look at the ranking of your own tracks.",
     [("Generated take", P + "reference music (Takin' a Walk)/chosen.mp3"), ("On the cut", P + "reference music on the cut/audio_preview.mp4"), ("Library ranking", P + "reference music (Takin' a Walk)/ranking.json")]),
    ("8", "Style from a reference video", "Your finished wallpaper reel as the reference. Are the measured differences and the suggested notes useful? The preview sits at -30.6 LUFS against the reel's -12.5.",
     [("Wallpaper reel vs Tiling cut", P + "style profile (wallpaper reel vs Tiling cut)/style_report.html"), ("Creator reel vs Tiling cut", P + "style profile (creator reel vs Tiling cut)/style_report.html")]),
    ("9", "Emulate a reference video", "NEW. Your vertical wallpaper reel as the reference, the horizontal Tiling cut as ours, every aspect on. Open the report: what was made for real (vertical crop, tightened pauses, colour look, music) and what was only measured (text, graphics, sound effects). Is the preview anything like the reel? The crop cuts a face off in places. Try the brief page to state what to take.",
     [("Report", "Runnells Tiling v3 - emulate wallpaper reel (all aspects)/emulation_report.html"), ("Emulated preview", "Runnells Tiling v3 - emulate wallpaper reel (all aspects)/emulated_preview.mp4"), ("Colour look (.cube)", "Runnells Tiling v3 - emulate wallpaper reel (all aspects)/reference_look.cube"), ("Style brief page", "Style brief.html")]),
    ("10", "Reference music from the reference video", "NEW. The music reference now comes from the reference video's own audio unless you give a track. On this reel only 1.4 s has no speech, so the whole mix (voice in it) was measured and flagged. Does that fallback seem acceptable, or should a voice/music separation model be installed?",
     [("What was measured", "Runnells Tiling v3 - reference music from the wallpaper reel (measure only)/reference.json")]),
    ("11", "Round 11: the bleep at 28.7-29.4 s", "NEW. Shorter, as you asked: 28.7 to 29.4 s (0.7 s, was 0.88 s). It ends 0.1 s after the 29.3 you typed, because the sound shows the 'k' of the word releasing at 29.32-29.36 s and 'just' starting at about 29.42 s; stopping at 29.3 leaves the 'ck' audible. Listen at 28.4-30.0 s: is 'fuck' fully covered, are 'don't know' before it and 'just' after it clear? If you want it to end at 29.3 exactly, say so and I will. The bell, callout fade, caption wording and card removal are all still in; QA: 6 verified, your 3 'no' notes recorded as rejections.",
     [("Review page", "Runnells Tiling v3 - your round 11 result/review/review.html"), ("QA report", "Runnells Tiling v3 - your round 11 QA/qa_report.html"), ("Premiere XML", "Runnells Tiling v3 - your round 11 result/Runnells_Tiling_v3_layers_v4.xml")]),
    ("12", "Premiere import of the newest XML", "V1 cut, V2 callout, V3 image card, V4 captions, A3/A4 music, A5/A6 sound effect. Does it import, and are positions and timing right? (It carries the stand-in callout changes.)",
     [("XML", f"{E}/Runnells_Tiling_v3_layers_v4.xml")]),
]

DECISIONS = [
    "Default extra time when a note says \"longer\" with no amount: 1.5 s (I chose it; change with --default-extra).",
    "Captions style: white on a navy pill with a light-blue word highlight, in Inter because ITC Avant Garde is not installed. Say if you want the brand font installed.",
    "Music and sound effects: generate, or use your Artlist library? You already have it locally (44 music folders / 50 files, 35 sound effects). Ranking your own tracks against a reference is free and licensed; generated music matched tempo but not tone. ElevenLabs' commercial terms for generated sound effects are unconfirmed, so nothing generated goes into published work until you check them.",
    "When should reconform run? Today you run it after revise.py when a layers warning appears. It could run automatically.",
    "Separating voice from music (Demucs). Voice-over reels like yours keep the music under every word, so their music cannot be isolated without it. It is a new dependency (and a model download), so I did not install it, least of all into PreCut's environment. Say if you want it in a separate one.",
    "Vertical reframe: the crop is a centre crop and cuts faces off. Premiere's Auto Reframe follows the subject. Build a subject-following reframe, or use Auto Reframe on the result?",
    "Finding a curse word Whisper hides: silencing the loud stretch and listening again worked on your cut (one word, n = 1). It adds about 80 s of transcription per cut. Keep it on by default? And should the padding after a bleeped word be shorter (it clipped the start of 'just')? A larger Whisper model is untried and is a 1.5 GB or larger download.",
    "The bleep rule (every cut, automatic): the word list is labs/bleep/profanity.txt, edit it freely. I left damn, hell and crap off. Whisper drops some curse words from its transcript, so the automatic rule only catches words it wrote; on your cut it missed the one in clip 8 and I bleeped that only because your note pointed there. Want a stricter detector that also flags loud bursts in long words for you to confirm? It found 11 across the cut, mostly emphasis, so it is noisy.",
    "Integration into app/: not done, by your rule (all skills finished and tested, then together). Say when.",
    "docs/reference/WALLPAPER_REEL_ANATOMY.md says 67.1 s; the file measures 66.03 s. I left the doc alone (it belongs to the Lead).",
]


def build(root: Path) -> tuple[str, list[str]]:
    missing = []

    def link(label: str, rel: str) -> str:
        if not (root / rel).exists():
            missing.append(rel)
        return f'<a href="{quote(rel)}">{html.escape(label)}</a>'
    rows = "".join(f'<tr><td class="n">{n}</td><td><b>{html.escape(t)}</b><div class="look">{html.escape(look)}</div></td><td class="lk">{" ".join(link(l, r) for l, r in links)}</td>'
                   f'<td class="ck"><input type="checkbox" aria-label="tested {n}"></td></tr>' for n, t, look, links in TESTS)
    decisions = "".join(f"<li>{html.escape(d)}</li>" for d in DECISIONS)
    if not ART.exists():
        missing.append(str(ART))
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Testing guide</title><style>
:root{{color-scheme:dark;--bg:#0d1420;--panel:#141d2c;--ink:#e8edf5;--mute:#8b98ad;--line:#25324a;--acc:#0391d8;--or:#f4690b}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Helvetica,Arial,sans-serif}}main{{max-width:1050px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:16px;margin:26px 0 8px}}.sub{{color:var(--mute);margin:0 0 14px}}
.note{{background:#3a2a12;border:1px solid var(--or);border-radius:8px;padding:8px 12px;margin:10px 0}}
table{{border-collapse:collapse;width:100%}}td{{padding:10px;border-bottom:1px solid var(--line);vertical-align:top}}.n{{color:var(--mute);width:24px}}
.look{{color:var(--mute);font-size:13px;margin-top:3px}}.lk a{{display:block;color:var(--acc);margin-bottom:3px}}.ck{{width:40px}}
li{{margin:6px 0}}code,pre{{background:var(--panel);border-radius:6px}}pre{{padding:10px;overflow:auto;font-size:12px}}
</style></head><body><main><h1>Testing guide: the creator-workflow skills, all at once</h1>
<p class="sub">Everything here was built standalone in <code>labs/</code> and is <b>not in the app</b>. Nothing is marked done until you say so.</p>
<p class="note">Five of the sample rounds were made from notes <b>I wrote as stand-ins to test a chain, not notes of yours</b> (flagged in the project index). The footage, callout, captions, music and sound effects are yours and real.</p>
<h2>To test now</h2><table>{rows}</table>
<h2>Try it on your own notes</h2><p>Leave 3-4 notes on page 2 (one with a drawing, one about the text bubble staying longer, one about a pause, one asking for a different sound effect), Download JSON, then follow the commands in <code>labs/TESTING.md</code> (revise, then change_callout / replace_sfx as it says, then reconform, then qa_pass, then the project index). Every step refuses with a reason instead of guessing, and none changes the folders it reads.</p>
<h2>Decisions waiting on you (I made a call on each)</h2><ol>{decisions}</ol>
<h2>What the checks cannot tell you</h2><p>Whether anything sounds or looks right (I can measure, not hear), whether the newest XML imports cleanly in Premiere (only the earlier callout, captions, music and effect XMLs are confirmed), and whether a style suits the piece. Those are the tests only you can run.</p>
<p class="sub">Project index: {link("open", "Runnells Tiling - project index.html")}</p></main></body></html>"""
    return page, missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args()
    root = a.root.expanduser()
    page, missing = build(root)
    if missing:
        print("REFUSING: the guide points at files that are not there:\n  " + "\n  ".join(dict.fromkeys(missing)), file=sys.stderr)
        return 1
    out = root / "TESTING - creator workflow.html"
    out.write_text(page)
    print(f"{len(TESTS)} tests, {len(DECISIONS)} decisions, every link checked\n{out}")
    if a.open:
        subprocess.run(["open", str(out)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
