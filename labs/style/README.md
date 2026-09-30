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
