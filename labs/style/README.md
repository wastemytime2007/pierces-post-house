# style: how a reference video is built, and how a cut differs from it

Spike. Standalone, file in and file out, not wired into `app/`. From the creator's "analyse a competitor's video and
replicate the style of the overlays and B-roll". What can honestly be measured is measured; what to copy is left to
the editor. **A style comparison suggests; it never edits anything.**

```
python3 labs/style/style_profile.py --reference "<reference.mp4>" --ours "<preview.mp4>" \
    --ours-timeline "<our review folder>/timeline.json" --out "<folder>" [--open] [--scene 0.2]
python3 labs/style/style_profile.py --reference "<reference.mp4>" --out "<folder>"          # just measure one
python3 -m pytest labs/style/tests -q
```
Writes `style_report.html` (each video's shot rhythm drawn to scale, eight frames of each, a table of measured
differences, SUGGESTED notes in plain words, and what was not measured) and `style.json`.

## What is measured, the same way for both
Visible cuts (count, per minute, average/median/longest/shortest shot, first cut, pace trend), loudness and
loudness range, pauses in the speech (per minute, longest), brightness, contrast, saturation, dominant palette,
motion energy (median frame difference, so cuts do not count as movement), and lower-third text-like activity
(edge detail in the lower third against the rest of the frame: a hint of captions or titles, not a reading of them).
Each differing metric beyond its tolerance produces one suggested note, worded by which way the gap goes.

## Validated against a real reference, and where it is weak
Checked against `docs/reference/WALLPAPER_REEL_ANATOMY.md` (Ryan's finished reel): loudness -12.5 LUFS and range
4.4 LU match exactly; 25 shots (21.8 cuts/min) against the doc's ~23 cuts (20.6/min). The default scene threshold
(0.20) was chosen because it reproduces that count on that reel (0.12 gives 46, 0.30 gives 13). The file measures
66.03 s in video, audio and container; the doc says 67.1 s (unexplained, possibly an earlier export).

**Jump cuts are mostly invisible to a picture detector.** On the 13-clip Tiling cut (a tutorial cut from one
continuous shoot) only 2 of its 12 real cuts had a visible scene-score signature; ten scored 0.001-0.013, which is
noise. So: the rows are labelled "visible cuts"; when `--ours-timeline` gives the real cut times the tool reports how
many of them the detector landed near (that proximity count can flatter it, since motion bursts near a cut count),
and the pace rows are marked **UNRELIABLE** when that falls below 60%, with no pace advice given from them. Audio
pauses are the signal that does see jump-cut editing (few, short pauses), but they are only trusted when there is a
quiet floor to find them in: a continuous music bed hides them, and those rows say UNRELIABLE instead of guessing.

## Not measured
What the graphics look like, what the shots show (B-roll or not), whether the style suits the piece, and jump cuts
the picture cannot see. Unlike formats (a vertical reel against a horizontal tutorial) are flagged at the top of
the report, because every picture row then compares unlike frames.


# Emulating a reference (`emulate.py`)

`style_profile.py` measures and suggests. `emulate.py` goes further: it takes a **style brief** (the reference video and the
aspects to emulate, every aspect on by default) and makes our cut follow the reference, into new files, then re-measures.

    PRECUT_ROOT=~/precut-checkout python3 labs/style/emulate.py --reference "<video>" --ours-xml "<layered xml>" \
        --ours-preview "<clean preview.mp4>" --out "<new folder>" [--emulate all | orientation,cuts,music,color,text,graphics,sfx] \
        [--skip music] [--orientation match|vertical|horizontal] [--music-file "<track>"] [--color-strength 0.7] [--no-generate]

Or open `style_brief.html`, untick what you do not want, and Download `style_brief.json` (or copy the command) and pass `--brief`.

| Aspect | What it does | Checked by |
| --- | --- | --- |
| vertical or horizontal | crops the preview to the reference's shape (centre, or `--reframe-focus`), enlarged to 1080 high | the output file's measured shape |
| type of cuts (pauses) | compares pauses from word timing (works under a music bed) and tightens pauses longer than the reference's longest ordinary gap, through `revise.py` | revise's own checks; applied count, seconds removed |
| music | the reference's music comes from the reference video's own audio (music-only stretches; whole mix, flagged, when there are fewer than 6 s) or from `--music-file`; generated, re-measured, mixed under our speech | `reference_music.py` closeness (tempo, tone, density, dynamics) |
| colour | a `.cube` LUT moving our Lab mean and spread toward the reference's, plus a graded preview | the gap to the reference must shrink by 15% on at least two of brightness, contrast, saturation, measured from the graded file |
| text, callouts/graphics, sound effects | **measured only**; nothing reliable can reproduce them (no wording is invented; no overlay detector; no separation of effects from voice and music) | said so in the report |

Statuses are EMULATED, PLANNED (dry run), TRIED, NOT CLOSER, GENERATED, NOT CLOSE ENOUGH, FAILED, MEASURED ONLY, ALREADY, NOT SELECTED.
A negative control (colour strength 0) must report NOT CLOSER, not EMULATED: it caught a real flaw in the first version of the check.

**Limits.** A centre crop cannot follow a subject and can cut a face off (seen on the real run); Premiere's Auto Reframe does. The
crop is taken from a 540p preview, so it is soft. With a voice-over reel whose music never stops (the wallpaper reel has 1.4 s
without speech), the music reference is the whole mix with the voice in it, which makes tone and dynamics unreliable: a real fix
needs a voice/music separation model (Demucs), not installed, a new dependency for Ryan to decide on. The cuts aspect never adds
shots: more cuts per minute needs footage.
