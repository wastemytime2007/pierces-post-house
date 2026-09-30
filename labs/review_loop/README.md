# review_loop: watch a cut, leave timecoded notes

Spike. Standalone, file in and file out, not wired into `app/`. Prompted by an
outside workflow that reviews a rendered cut on a web page and sends timestamped
notes back to the editor (see `docs/STATUS.md` In progress, 2026-09-29).

**In:** an export XML from the app. **Out:** a folder with `preview.mp4`,
`review.html`, `timeline.json`. Open `review.html`.

```
PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/build_review.py \
    <export.xml> --out "<folder>" [--height 540] [--open]
python3 labs/review_loop/verify_preview.py "<folder>"
python3 -m pytest labs/review_loop/tests -q
# page behaviour in real Chrome (manual; needs `npm i playwright-core` somewhere)
NODE_PATH=<dir>/node_modules node labs/review_loop/tests/drive_page.js "<folder>"
```
`PRECUT_ROOT` only silences a warning printed when the `posthouse` package is imported.

## What the page does
- Plays the cut with the synced audio placed where the XML puts it.
- Shows, live, which clip you are in, its source file, and the source timecode.
- `n` (or the button) drops a note at the current moment; `Enter` saves. Notes
  persist in the browser and show as orange pins on the timeline.
- `d` (or "Draw on frame") pauses on the current frame and turns on drawing:
  pen, circle, arrow, box, six colors, undo, clear. Draw, optionally type a
  comment, Save. A note can be text only, drawing only, or both. The drawing
  reappears whenever you are paused on that frame, and each note shows a
  thumbnail of the frame with the drawing on it.
- "Copy all feedback" / "Download JSON" give every note with timeline time, clip
  number, source file and source seconds. Drawings go out two ways: a plain
  sentence per shape ("circle in the upper left, x 10%-25%, y 20%-38% of the
  frame") and vector geometry with a bounding box. Schema is
  `review_notes.v0-draft`. The real revision-operation schema is Phase 5
  (`docs/ARCHITECTURE.md`), and this does not pre-empt it.

Drawing coordinates are fractions of the video frame (0 to 1, top-left origin),
so they hold at any size. They map onto the source frame directly only when the
export does not crop or reposition the clip; the preview scales the source to
fit and applies none of the sequence's per-clip scale/position filters.
A flattened "frame with drawing" PNG cannot be exported from a page opened
from disk (Chrome blocks reading video pixels there, confirmed by
`tests/drive_page.js`), which is why the geometry is what gets handed back.

## Revise: notes in, revised cut out (`revise.py`)
```
PRECUT_ROOT=~/precut-checkout python3 labs/review_loop/revise.py \
    <export.xml> <review_notes.json> --out "<folder>" [--ops ops.json] [--open]
```
`review_notes.json` is the page's "Download JSON". The notes go to the local `claude`
CLI (build-phase cost mode) which turns each one into an operation from a small
vocabulary; `--ops` skips that step so a plan can be read, edited and re-run.
Every note is accounted for: applied, or reported as not applied with the reason.

The interpreter only chooses. It never gets to invent a time:
- `tighten_pause`: a real silence detector runs on the audio at that spot. If there is
  no pause there, nothing changes and it says so.
- `remove_range`, `trim_start`, `trim_end`: only when the note itself states the times
  or seconds. A note like "trim the end" is refused.
- `drop_clip`: only when the note says to remove the clip.
- Anything else (swap to other footage, crop or reframe, graphics, music, colour, a
  drawing with no words) is `unsupported`. Drawings are not acted on yet.

Only the cut zone changes: removals ripple-close inside it and split clips where they
land mid-clip; the selects pool is left exactly where it was. Output is
`<name>_v2.xml`, `ops.json`, `changes.json` and a full V2 review folder whose page
opens with a "Changes from V1" list (click one to jump to where it landed).

It refuses to present V2 as ready unless all of these pass: the revised XML reloads with
every range inside its real file, the length equals V1 minus what was removed, no seams,
no footage that was not in V1, every lav piece keeps its V1 offset to camera, the pool is
identical, `safety_net/verify_export.py` passes, and `verify_preview.py` passes on the V2
render.

Not verified: that Premiere imports `_v2.xml`. It is checked by re-reading it here and by
`verify_export.py`, never opened in Premiere. Open it there before relying on it.
The pause detector calls a silence a pause at 3x the quiet level of that window (never
above 0.2x its loud level), which is looser than a fixed -55 dB. On the real tiling cut it
measured a 1.38s pause where ffmpeg's stricter detector saw 0.53s, so listen at the edit
to check no quiet word was clipped, and tighten the threshold if one was.

## What it deliberately does not do
- Only the **cut zone** (before the 20s gap that separates it from the selects
  pool). The pool is not previewed.
- Proxy quality, not a grade or a final render.
- No transcript text on the page yet. It would show what is being SAID next to
  the footage, which is what would have caught the Sink/Disposal wrong-footage
  bug on sight (`4513bc3`).

## Frame-rate conventions (why `timeline.py` looks the way it does)
Video in/out are read at the source file's rate. Synced-audio in/out are written
by the exporter in sequence frames (`multi_exporter._append_synced_clipitem`);
reading them at the WAV's declared rate puts every lav clip in the wrong place
and still passes a bounds check. `verify_preview.py` LAV-SYNC exists for exactly
this: on the real tiling export the correct reading lags +0.007s against the
camera audio and the file-rate reading lags -5.029s.

## Integration contract (when and if this earns a screen)
Reads what the app already writes (export XML) and writes plain files. Nothing
here touches `project.json`, the DB, or `precut_pipeline`.
