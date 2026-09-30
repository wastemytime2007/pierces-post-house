# qa: check every note against the finished new version

Spike. Standalone, file in and file out, not wired into `app/`. From the creator's workflow: after a
revision, "do a thorough QA pass, comparing my notes to make sure everything is done and you don't miss
anything", with frame grabs.

```
PRECUT_ROOT=~/precut-checkout python3 labs/qa/qa_pass.py \
    --notes review_notes.json --revise-dir <revise's folder> \
    --before-xml <old xml> --after-xml <new xml> \
    [--before-page <old review folder> --after-page <new review folder>] \
    [--overlay <callout folder> ...] [--audio-before F --audio-after F] --out <folder> [--open]
python3 -m pytest labs/qa/tests -q
```
Writes `qa_report.html` (a before and after frame per note, the evidence, whole-cut checks, and any
change nobody asked for) and `qa.json`. Exit 1 if anything FAILED or was changed without a note.

## The rule it follows
`revise.py`'s ledger says what was *attempted*. QA does not trust it. Each note gets a status measured
on the new version's files:

| Status | Meaning |
| --- | --- |
| VERIFIED | measured on the new version, and it holds |
| APPLIED-UNMEASURED | the ledger says applied, nothing here can independently measure that kind of change: look at the frames |
| NOT DONE | not applied, with the reason and what it would need (never silent) |
| FAILED | measured, and the new version does not do what the note asked |

A note's status is its worst row. A note with no operation at all is NOT DONE.

## What is measured, per operation
- `tighten_pause`, `remove_range`, `drop_clip`: the source footage the note removed is gone from the new
  cut (by source frame, not by trusting a time); for a pause, the silence near the note is measured before
  and after.
- `trim_start`, `trim_end`: the clip's source range moved by the amount asked.
- `extend_end`: the clip now runs longer in its source; the level at its new end is reported.
- `start_at_words`: a clip actually starts at the seam time (words alone cannot tell a seam from
  mid-sentence), and the new version's audio reads the asked words from it.
- `replace_sfx` (with `--audio-before/--audio-after`): the effect is a measurably different sound, generated
  from that note's words.
- A drawn or graphic note: done if a callout layer is anchored on its frame (with the layered page, the
  layer is also confirmed visible by pixels); if that frame was cut out, it is reported NOT DONE.
- Whole cut: `verify_export`, layers whole, length change. And any source footage that entered or left the
  cut with no note asking for it is listed.

## Proven, and how it was caught out
On the real Tiling round (Ryan's three notes on V2, V3 after): all three VERIFIED. A control feeding it
the *unchanged* V2 as the "new" version failed note 1, failed note 2 and reported note 3 NOT DONE: nothing
verified. That control first exposed a real weakness: note 2 still verified on V2, because the words check
alone accepted mid-sentence speech ("step on the tile up"). The structural seam check was added, and the
control now fails.

## Real limits
- Frames are grabbed at each note's moment; they show the picture, so judging *whether the result is
  good* stays with a person. VERIFIED means the change happened, not that it is right.
- Older revise records lack the removed spans (`changes.json` from before this tool); the range is then
  UNMEASURED and unrequested-change detection is skipped, with a message to re-run `revise.py`.
- The music, captions and effects are not re-listened to here: their own tools verify them; QA checks
  layers are whole and callouts are anchored.
- `start_at_words` re-transcribes a few seconds of the new preview, so it needs the new page's `preview.mp4`.
