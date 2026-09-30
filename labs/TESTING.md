# Testing guide: the creator-workflow skills, all at once

Everything below was built standalone in `labs/` and is **not in `app/`**. Nothing is marked done until you say so. A clickable
version of this guide, with links to every output, is `~/Documents/Post House Reviews/TESTING - creator workflow.html`.

Sample outputs live in `~/Documents/Post House Reviews/`. **Five of them were made from notes I wrote as stand-ins to test a chain,
not notes of yours** (they are flagged in the project index and marked `_stand_in` in their notes files). The footage, callout, captions,
music and sound effects are all yours and real.

## Already signed off by you (for reference, not part of this round)
Review page with drawing and notes; notes to a revised cut (your three real notes); the callout layer in Premiere; captions (first
window) in Premiere; generated sound effect and music; layers on the page; reconform after a revision; the QA pass; the beatmap.

## To test now (nothing below has been judged by you)

| # | Piece | Open this | Look for |
| --- | --- | --- | --- |
| 1 | **The overview** | `Runnells Tiling - project index.html` | does it tell you what exists and what each round found? Are the stand-in rounds clearly marked? |
| 2 | **Review page: click any box to leave a note on it** (NEW) | `Runnells Tiling v3 - review page (click any box to leave a note)/review.html` | click a box on the timeline map (a sound effect, a clip, the callout, the image card, a caption line) and write a note on that whole element, no playhead needed; Shift+click only jumps there; Download JSON: does each note name its element? Layers on/off and drawing still work |
| 3 | **Keep a graphic on screen longer** (stand-in note) | the callout at 12-18.7 s on that page; its preview `... longer callout (stand-in)/callout/overlay_preview.mp4`; QA `... QA pass (longer callout)/qa_report.html` | the callout now stays ~1.5 s longer; is 1.5 s the right default step when a note gives no amount? |
| 4 | **Change a callout's words** (stand-in note) | the same callout; `... callout words (stand-in)/callout/overlay_preview.mp4`; `... QA pass (callout words)/qa_report.html` | it reads "Cardboard spacer", second line gone |
| 5 | **Image card** (border + highlight a screenshot) | that page at 29.5-33.3 s (the card); source image `... image card (real frame)/screenshot.png` | whole image shown, blue border, orange box around the spacer, caption; does it look like what you want? |
| 6 | **Replace a sound effect from a note** | `... sfx and music v2 (0-22s)/audio_preview.mp4` at 13.97 s (new effect) against `... sfx and music (0-22s)/audio_preview.mp4` (old) | does the new effect sound like "something being highlighted on a piece of paper"? |
| 7 | **Music like a reference track** | `... reference music (Takin' a Walk)/chosen.mp3` (generated) next to the reference in your Artlist library (Barrell, Takin' a Walk); `... reference music on the cut/audio_preview.mp4`; `ranking.json` in that folder | the honest result: tempo and rhythm match, tone and dynamics did not; does the ranking of your own Artlist tracks look like a better route? |
| 8 | **Style from a reference video** | `... style profile (wallpaper reel vs Tiling cut)/style_report.html` (your finished reel as reference) and `... (creator reel vs Tiling cut)` | are the measured differences and the five suggested notes useful? The preview sits at -30.6 LUFS against your reel's -12.5 |
| 9 | **Emulate a reference video** (NEW) | `... emulate wallpaper reel (all aspects)/emulation_report.html`, `emulated_preview.mp4`, `reference_look.cube`; the picker page `Style brief.html` | your vertical reel as the reference, the horizontal Tiling cut as ours, all aspects on: vertical crop, tightened pauses, colour look, music (made for real); text, graphics, sound effects (measured only). Is the preview anything like the reel? The crop cuts a face off in places |
| 10 | **Reference music from the reference video** (NEW) | `... reference music from the wallpaper reel (measure only)/reference.json` | the music reference now comes from the reference video's own audio unless you give a track. Only 1.4 s of your reel has no speech, so the whole mix (voice in it) was measured and flagged: acceptable, or install a voice/music separation model? |
| 11 | **Your bleep at 29.44-29.70 s** (you set it in the editor; open only `0 - CURRENT (open this one)`) | `0 - CURRENT (open this one)/review/review.html`, `bleep_check.mp4`, `QA report/qa_report.html` | play 29.0 to 30.2 s: does it cover the word and leave "what the" before and "just" after clear? Passed every check including a listen check. The Bleeps lane stays editable |
| 12 | **Second real cuts and the learning** (NEW) | `Second real cuts - bleep test results.txt`, `What the bleep tool has learned.txt` | the bleep pipeline ran on four more of your cuts: about 660 words, nothing bleeped, no false alarms (it does not show it would catch a hidden word there); the tool now learns from your bleep edits: is that what you meant? |
| 13 | **Premiere import of the newest XML** | `... reconformed v4 E (image card)/Runnells_Tiling_v3_layers_v4.xml` | V1 cut, V2 callout, V3 image card, V4 captions, A3/A4 music, A5/A6 effect. Imports? Positions and timing right? (It carries the stand-in callout changes.) |
| 14 | **Project index, skill, docs** | `labs/README.md`, `labs/skill/post-house-review-loop/SKILL.md` | is the map of tools clear? |

## Try it on your own notes (the whole loop)
1. Open review page #2, leave 3-4 notes (include one with a drawing, one about the text bubble staying longer, one about a pause, one
   asking for a different sound effect), **Download JSON**.
2. `cd` to the repo worktree and run (paths are the newest ones; swap in yours):
```
export PRECUT_ROOT=~/precut-checkout
R="$HOME/Documents/Post House Reviews"; E="$R/Runnells Tiling v3 - reconformed v4 E (image card)"
python3 labs/review_loop/revise.py "$E/Runnells_Tiling_v3_layers_v4.xml" ~/Downloads/review_notes.json --out "$R/Runnells Tiling v3 - my round"
```
   `revise.py` prints each note as APPLIED or NOT APPLIED on the timeline. Then, depending on what it says:
```
# graphics notes (longer / new words):
python3 labs/overlay/change_callout.py --ops "$R/Runnells Tiling v3 - my round/ops.json" --notes ~/Downloads/review_notes.json --xml "$E/Runnells_Tiling_v3_layers_v4.xml" --out "$R/Runnells Tiling v3 - my callout"
# a sound-effect note:
python3 labs/audio/replace_sfx.py --audio "$E/audio" --ops "$R/Runnells Tiling v3 - my round/ops.json" --notes ~/Downloads/review_notes.json --out "$R/Runnells Tiling v3 - my sfx" --preview-video "$E/captions/captions_preview.mp4"
# put everything back on the (revised) cut, with whatever changed:
python3 labs/reconform/reconform.py --revised "$R/Runnells Tiling v3 - my round/<the revised .xml, or $E's XML if only graphics changed>" \
    --overlay "$R/Runnells Tiling v3 - my callout" --overlay "$R/Runnells Tiling v3 - image card (real frame)/card" \
    --captions "$E/captions" --audio "$E/audio" --out "$R/Runnells Tiling v3 - my result"
# check every note against the result:
python3 labs/qa/qa_pass.py --notes ~/Downloads/review_notes.json --revise-dir "$R/Runnells Tiling v3 - my round" --before-xml "$E/Runnells_Tiling_v3_layers_v4.xml" --after-xml "$R/Runnells Tiling v3 - my result/Runnells_Tiling_v3_layers_v4.xml" --out "$R/Runnells Tiling v3 - my QA"
python3 labs/project/project.py --root "$R" --name "Runnells Tiling"
```
   If a step refuses, the message says why; none of them changes the folders it reads from.

## Decisions waiting on you (I made a call on each, stated here)
1. **Default extra time when a note says "longer" with no amount: 1.5 s.** Change with `--default-extra`. Yours to set.
2. **Captions style:** white on a navy pill, light-blue word highlight, in Inter (ITC Avant Garde is not installed). Already accepted for the first window; say if you want the brand font installed.
3. **Music and sound effects: generate, or use your Artlist library?** You already have the library locally (44 music folders / 50 files, 35 sound effects).
   Generated music matched a reference's tempo but not its tone; ranking your own tracks against a reference is free and licensed.
   ElevenLabs' commercial-use terms for generated *sound effects* are unconfirmed: nothing generated goes into published work until you check them.
4. **When should reconform run?** Today you run it after `revise.py` when a layers warning appears. It could run automatically.
5. **Separating voice from music (Demucs).** Voice-over reels like yours keep the music under every word, so their music cannot be isolated without it. It is a new dependency and a model download; I did not install it (least of all into PreCut's environment). Say if you want it in a separate one.
5b. **Vertical reframe.** The crop is a centre crop and cuts faces off; Premiere's Auto Reframe follows the subject. Build a subject-following reframe, or use Auto Reframe on the result?
6. **Integration into `app/`:** not done, by your rule (all skills finished, tested, then together). Say when.
7. `docs/reference/WALLPAPER_REEL_ANATOMY.md` says 67.1 s; the file measures 66.03 s. I left the doc alone (it is the Lead's).

## What the checks cannot tell you
Whether anything *sounds* or *looks* right (I can measure, not hear), whether the newest XML imports cleanly in Premiere (only the earlier
callout, captions, music and effect XMLs are confirmed), and whether a style suits the piece. Those are the tests only you can run.
