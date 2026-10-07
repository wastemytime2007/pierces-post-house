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
- `extend_end`: for "this word is cut off". Measured from how the sound decays after the cut
  (loud at the cut, quiet a few hundredths later), capped at 1.5s. Word timing cannot see a
  clipped tail, the audio level can. If the audio is already quiet at the cut, or keeps going
  for more than the cap, nothing changes and it says so. Growing a clip into footage the
  selects pool also holds takes that footage off the front of the pool clip, because the pool
  must never repeat the cut.
- `start_at_words`: for "the clean cut should be X to Y". Finds Y in the clip's opening seconds
  with word timing (PreCut's own `Transcriber`, so its model and seeded decode carry over) and
  starts the clip there, cutting at the quietest point just before the word. The words must be
  in the note. If they are not heard there, it reports what the audio does say.
- Anything else (swap to other footage, crop or reframe, graphics, music, colour, a
  drawing with no words) is `unsupported`. Drawings are not acted on yet.

Only the cut zone changes: removals ripple-close inside it and split clips where they
land mid-clip; the selects pool is left exactly where it was. Output is
`<name>_v2.xml`, `ops.json`, `changes.json` and a full V2 review folder whose page
opens with a "Changes from V1" list (click one to jump to where it landed).

Versions follow the input: give it a V2 XML and you get a V3 (`..._v3.xml`), and the page says
"Changes from V2".

It refuses to present the result as ready unless all of these pass: the revised XML reloads
with every range inside its real file, the length equals the input plus what was added minus
what was removed, no seams, no footage that was not in the input (except a measured
extension), every lav piece keeps its offset to camera, the pool only lost footage the cut
gained, `safety_net/verify_export.py` passes, and `verify_preview.py` passes on the new
render. Then the measured edits are re-checked on the finished render: an extended cut must
land at or below room level, and a `start_at_words` seam must read as the note asked when the
render itself is transcribed.

Word timing from Whisper is good to about a tenth of a second, so cuts snap to a quiet point
and the seam check transcribes the finished render. The SEAM-TEXT check uses the same
Whisper family as the edit, so it confirms the outcome but is not fully independent of it.

Not verified: that Premiere imports the revised XML. It is checked by re-reading it here and by
`verify_export.py`, never opened in Premiere. Open it there before relying on it.
The pause detector calls a silence a pause at 3x the quiet level of that window (never
above 0.2x its loud level), which is looser than a fixed -55 dB. On the real tiling cut it
measured a 1.38s pause where ffmpeg's stricter detector saw 0.53s, so listen at the edit
to check no quiet word was clipped, and tighten the threshold if one was.

## Layers on the page (`layers.py`)
A cut that has had a callout, captions, music or an effect placed on it (by `labs/overlay`,
`labs/captions`, `labs/audio`) shows them on the page: the layers are read back from the XML (the
tracks whose file ids start `overlay-file-` and `audio-file-`) and composited into
`preview_full.mp4`, so the page plays what Premiere will. **Layers: on/off** switches between it
and `preview.mp4`, the clean cut, which stays the verified one. Notes keep the same timecodes
either way. `build_review.py` refuses to write the page unless the full preview passes: same
length, identical to the clean one outside the layers, each picture layer visibly present, each
audio layer in the mix (mix minus clean-plus-layers far below the layer), and the clean sound
unchanged after the layers end. `timeline.py` skips the placed music and effects when reading the
cut's speech, so they are never mistaken for it.

**Beatmap.** Under the timeline bar the page draws one lane per kind of edit decision on the same
time axis: **Cuts** (a tick at every seam), **Edits** (the revision's applied edits, on a revised
page), **Callout**, **Captions** (one block per caption line when the layer's `captions.json` sits
beside its file, offset correctly for a split layer piece), **Music** and **SFX**. A playhead runs
through every lane, the block or tick under it lights up, and the line under the video names what is
on now (including the caption text). Clicking a block seeks to it. Lane names come from the layer
file names (`layers.lane_name`); anything unrecognised lands in a Graphics or Audio lane.

**Name layered XMLs with the cut's version last** (`..._layers_v3.xml`): `revise.py` reads the
version from a trailing `_vN`, so a name ending `_audio_v2` makes a V3 cut read as V2.

**What a revision does to layers, and its known gap.** `revise.py` ripples every track alike.
That keeps captions with the speech, and layers after the edit shift with the picture. But a
removal that runs through a callout, an effect or the music bed cuts through it, and each layer
that lost time is listed as a `layers` warning in the Changes panel and the console. Proved on the
real cut: tightening the 1.4s pause at 13.7-15.1s (inside the callout) applied and verified, and
warned that the callout, captions, music and effect were each cut through. **`labs/reconform`
puts them back**: it strips the layers off the revised XML and re-places the callout by its frame
and rebuilds the captions, music and effect on the revised cut (see its README). Run it after
`revise.py` whenever a warning appears.

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

## Preview frame accuracy (fixed 2026-10-06)
`render_preview.py` used to encode each segment with `fps=30` and the clip's duration as an input limit. Measured on a real 8-clip cut (879 frames at 29.97): the preview ran at 30 fps, started 0.033 s late, rendered 874 frames, and each segment's first frame was shown twice, so every frame after it was one frame (33 ms) behind the source. Now each segment starts its own clock at 0, runs at the sequence's exact rate (`fps_arg`: 29.97 is 30000/1001), is exactly the timeline's number of frames, and `render_preview` refuses to hand back a preview whose total frame count differs from the timeline's. `test_the_preview_is_frame_exact_...` uses a source whose every frame is a flat grey set by its frame number (a frame early or late is a clear step) and fails on the old renderer (checked: it ran at `30/1`). Review pages built before this date can have timecodes a few frames off late in a long cut; rebuild them to correct that.

## reframe_vertical (2026-10-06)
A note such as "lower this shot so his head isn't cropped off at the top" is a change to the clip's Basic Motion, not to its timing. The operation takes a clip and a direction (`lower` or `raise`), and the direction must be the note's own: `ops.validate` refuses a move the note does not ask for and a note that does not talk about framing. The amount is one fixed step, 12% of the height of the window the shot shows (`apply_ops.REFRAME_STEP`), clamped so the window stays inside the picture; at the edge it says so and changes nothing. A clip with no Basic Motion is reported, not guessed at.

- Applied before the cut edits, to the first video track only (the cut, as `load_cut` reads it), so an overlay clip on V2 does not shift which shot is moved.
- Nothing ripples (no timing change), so none of the audio layers move.
- `revise.verify_motion` reads the revised XML back; `labs/qa/qa_pass.py` measures old XML against new (direction, scale unchanged).
- **Rests on the vertical unit that has not been confirmed in Premiere** (`labs/reframe/README.md`). Every summary line says what Position y should read, so one look in Effect Controls confirms or refutes it.
- Tried on Ryan's real notes against the `final_v2` XML (dry run): only clips 4 and 8 changed. Not run through the interpreter call or the full QA pass.

## In the app (2026-10-06)
The app's Review tab (`app/src/screens/tabs/ReviewTab.jsx`, backend `app/python_backend/creator_tools.py`) runs `build_review.py`, then `revise.py` and `labs/qa/qa_pass.py` on a notes file, from a window. Two fixes came out of the first real run: `revise.verify`'s LAV-SYNC-PRESERVED now measures each lav piece at its own start (a lav split mid-clip around a bleep used to read as drift), and `verify_preview.py` no longer needs a source longer than 97 s.

## Embedded in the app (2026-10-06)
The Review tab frames this page. Two additions make that work: the page posts `{type: 'review:count', n}` to its host when its notes change and answers `{type: 'review:get-notes', id}` with `{type: 'review:notes', id, payload}` (the object "Download JSON" saves). Standalone use is unchanged. `tests/drive_embedded.js` drives the framed page in Chrome (manual, like `drive_page.js`).

## AI review (2026-10-06)
`ai_review.py <export.xml> --out <folder>` writes `ai_review.json`: `checks` (CUT-EDGES, SOURCE-AUDIO, STORY, HOOK, ENDING) and `notes` in the shape the review page saves. The page takes them with `postMessage({type: 'review:add-notes', notes})` (a new run replaces notes flagged `ai`, hand-written notes are untouched). The method, thresholds and how they were chosen are in the module docstring and `docs/STATUS.md`. `--no-story` runs only the mechanical checks (no model call). Requires the `claude` CLI on PATH and its normal environment for the story step; without it the story check reports why and the other findings still stand. Limits: speech-only (the picture is not looked at); Whisper word times are only good to about 0.1 s; a cut that lands in a pause cannot be told from a clean one.

## Submit changes, and what the editor can and cannot do (2026-10-06)
`Submit changes` (page footer, Review tab toolbar) = `revise.py` + `qa_pass` on the page's notes. Operations that exist: `tighten_pause`, `remove_range`, `trim_start`, `trim_end`, `extend_end` (to where the sound decays, up to 1.5 s), `start_at_words`, `drop_clip`, `reframe_vertical`, plus hand-offs to the overlay, caption, bleep and audio steps. Missing, and what the AI review's own notes ask for most: `extend_start` (start a clip earlier), extending an end through speech that continues (to the next pause), joining two clips, and anything that needs new content (a rewritten line, a different hook). Those come back as Not done with the reason. `verify_preview` LAV-SYNC reports a lag that is exactly 0.1% of the clip's place in the camera file as an open question (29.97 vs 30 reading), not as a failure.

## The AI editor's own loop (2026-10-06)
`creator_tools.auto_edit` (app side) drives `ai_review.py` and `revise.py`: review a version, submit the notes that carry a fix, review the result, repeat (max 4 rounds), stop on: nothing fixable left, a round that leaves more to fix (the earlier version is named), the length floor (60%), the editor applying nothing, an error, or Stop. Operations it can make from a note's `suggested_op`: `extend_start`, `extend_end` (up to 3 s, to the next pause), `drop_clip`, `start_at_words`. `extend_start` mirrors `extend_end`: `ops.measure_head` finds where the sound begins before the cut; `apply_ops` starts the clip at the same timeline frame, reading earlier source, and ripples everything after it; `revise.verify` allows the footage in front (`extra_in`) and re-checks the new start is in quiet (`START-IN-QUIET`); QA measures the earlier start on the two XMLs. Refused with a reason: a start another note also trims, footage in front that the selects pool also holds, a start that runs into the previous clip's footage, sound that keeps going past the look-ahead.

