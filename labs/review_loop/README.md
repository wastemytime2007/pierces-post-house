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
```
`PRECUT_ROOT` only silences a warning printed when the `posthouse` package is imported.

## What the page does
- Plays the cut with the synced audio placed where the XML puts it.
- Shows, live, which clip you are in, its source file, and the source timecode.
- `n` (or the button) drops a note at the current moment; `Enter` saves. Notes
  persist in the browser and show as orange pins on the timeline.
- "Copy all feedback" / "Download JSON" give every note with timeline time, clip
  number, source file and source seconds. Schema is `review_notes.v0-draft`. The
  real revision-operation schema is Phase 5 (`docs/ARCHITECTURE.md`), and this
  does not pre-empt it.

## What it deliberately does not do
- Only the **cut zone** (before the 20s gap that separates it from the selects
  pool). The pool is not previewed.
- Proxy quality, not a grade or a final render.
- Does not act on notes. Turning a note into a revised cut is the next slice.
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
