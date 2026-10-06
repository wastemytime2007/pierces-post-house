# overlay: a review note with a drawing becomes a transparent callout layer

Spike. Standalone, file in and file out, not wired into `app/`. Built for Ryan's note 3 on the
Tiling cut ("add some kind of arrow and text on screen highlighting that this spacer is the piece
of cardboard he pulled off the box", with a box drawn round the spacer).

**In:** the page's exported notes, one note number, the review folder (`timeline.json`).
**Out:** `overlay.mov` (transparent ProRes 4444), `overlay_preview.mp4` (that layer composited over
the clip, with the clip's own audio), `excerpt.mp4` (the plain clip), `placement.json`, and the
HyperFrames project that made it.

```
python3 labs/overlay/make_overlay.py <review_notes.json> --note 3 \
    --review-dir "<folder>" --out "<folder>" \
    [--title "The spacer"] [--subtitle "A piece of cardboard pulled off the box"] \
    [--xml "<the export it will go in>"] [--open]
python3 labs/overlay/verify_overlay.py "<folder>"
python3 labs/overlay/place_overlay.py "<export.xml>" "<overlay folder>" --out "<new.xml>"
python3 -m pytest labs/overlay/tests -q
```
Pass `--xml` so the overlay is rendered at that sequence's own size and frame rate (3840x2160 at
59.94 for the Tiling cut). Without it the overlay is 1920x1080 at 30 and `place_overlay.py` will
refuse it for a 4K sequence, because dropped in at native size it would fill a quarter of the frame
and its frame rate would have to be conformed.

## Placing it in the XML (`place_overlay.py`)
Adds the overlay as one clip on a new video track above the cut and changes nothing else. It is
placed by the note's source frame, found in the target XML, so it lands correctly on any later
version of the cut. It refuses a size or frame-rate mismatch, a missing alpha channel, a frame no
longer in the cut, or a clip that would run past the cut, and it will not present the result unless
these pass: everything else in the sequence is structurally unchanged, one new clip with frame counts
that agree (timeline = file = definition), the callout enters on the frame the note was drawn on
(recomputed from the output), the file is reachable, and `verify_export.py` passes.
The clip has no `masterclipid` (there is no master clip to point to) and carries `alphatype=straight`.

## Changing a callout from notes (`change_callout.py`)
Two kinds of note change a callout that is already on the cut, and one render carries both:
**`extend_graphic`** (stay on screen longer, below) and **`edit_callout`** (different words, or drop the
smaller second line). For `edit_callout` the interpreter may only use words the note itself gives (quoted or
plainly stated; it is refused wording the note did not contain) and may only remove the second line if the
note mentions it. If two notes change the same field, the first is applied and the second is reported.
QA checks the new words against the note and confirms the rendered layer really changed, not just its record.
Proved on the real cut: "change the text bubble to say 'Cardboard spacer' and get rid of the small line" became
title "The spacer" -> "Cardboard spacer" with the second line removed, and the frame reads that way.

### Keeping a callout on screen longer
A note like "the text bubble only shows for a moment, have it sit on screen longer" goes through
`revise.py`: the interpreter chooses `extend_graphic`, and may give `seconds` only if the note states an
amount ("two more seconds"); otherwise it is null. `revise.py` changes nothing on the timeline for it.
```
python3 labs/overlay/change_callout.py --ops "<revise's ops.json>" --notes "<review_notes.json>" \
    --xml "<the XML the notes were left on>" --out "<new callout folder>" [--default-extra 1.5]
```
The note is matched to the callout on screen at the note's moment (by time, since the note is left while
the bubble is up, not on the frame it was drawn on). That callout is rendered again with a longer hold:
same words, same drawn region, same anchor frame, at the target sequence's size and rate, and every
`verify_overlay.py` check runs on it. **The amount** is the note's when it states one; otherwise a default
step of 1.5s, and the ledger says "the note gave no amount" so the choice is visible and easy to change
with `--default-extra`. If the shot ends before the extra time fits, the callout gets what fits; a gain
under 0.3s is reported as not applied. One change per callout per run. Then put it on the cut with
`labs/reconform` (`--overlay <new folder>`), which also rebuilds the captions so they keep clear of the
longer callout. `make_overlay.py --hold SEC` does the same for a fresh callout.

## A framed, highlighted image (`make_image_card.py`)
From the creator's "make this cropped screenshot look better, give it a border and highlight what matters".
```
python3 labs/overlay/make_image_card.py <image> --out "<folder>" --xml "<export.xml>" \
    --anchor-source "<file name>" --anchor-sec <seconds> [--highlight x0,y0,x1,y1] [--caption "..."] \
    [--position center|left|right] [--hold 3.5] [--lead 0.5]
```
The image is shown whole (fitted, never cropped, never enlarged past 1.5x) inside a brand-blue border with a
soft shadow; `--highlight` draws a hollow orange box (as fractions of the image) that animates in; `--caption`
adds a navy strip below. Rendered by HyperFrames to a transparent ProRes 4444 `.mov` at the target
sequence's size and rate, and **anchored to a source frame like a callout**, so `labs/reconform` re-places it
after a revision (pass it as another `--overlay`) and the captions keep clear of it. If the shot ends before
the hold fits, the hold shrinks (`hold_sec` in `placement.json` says how much; under 1.5s is refused).
`verify_image_card.py` measures the render against the source image: real alpha at the right size and rate,
transparent before and after, the image file untouched, margins, the opaque card covering exactly the
planned rectangle, the blue border on all four sides, the picture matching the source (mean difference), all
corners and edge midpoints of the source present (not cropped), the orange ring on four sides and hollow, the
caption strip with text. The tests include cards that are deliberately wrong (no border, shifted picture,
filled highlight, missing caption, altered source), each of which must be rejected.
Not done: choosing the image or where to highlight (both are inputs), animation styles beyond this one.

## Why a separate layer
The footage is never re-rendered. `overlay.mov` is transparent (ProRes 4444 with alpha, which is
what Premiere ingests), so it goes on a track above the untouched clip. `placement.json` says
where: `place_overlay_on_timeline_at_sec`. The preview only exists so the layer can be judged;
it is made by compositing, so it shows exactly what the layer does.

## How it decides
- The note is placed on THIS timeline by its source position, so it works whichever earlier
  version the note was left on.
- The callout is built from the drawing: the box is the drawing's bounding box plus padding, the
  label goes below (or above, if the box is low in the frame), and the arrow runs from the label
  edge to the box edge. The callout enters on the frame the note was left on, holds 3.3s, and fades.
- It stays inside the shot: the lead-in and hold shrink near a shot's edges, and it refuses if
  under 1.5s of hold is left.
- Wording is an input. The defaults are Claude's, drawn from the note. Change them with
  `--title` / `--subtitle`; the label shrinks the type for longer text and refuses text that cannot fit.

## What is verified (`verify_overlay.py`, on the rendered files)
The layer is ProRes with real alpha at 1920x1080 and the right length; it is fully transparent
before the callout enters and after it leaves; the box (hollow), label and arrow are each present;
the box centre is within 6px of the drawing's; the preview's audio is bit-identical to the plain
clip's; away from the callout the preview equals the original footage; over the label it differs.

## Real limits
- The callout is fixed in the frame. That is correct for this shot (steady camera, the spacer does
  not move; checked frame by frame across the hold) and would drift on a moving shot. No tracking.
- Ryan confirmed the 1080p `.mov` shows with its transparency in Premiere. That the XML written by
  `place_overlay.py` imports there, with the overlay on V2 at the right time and size, is unconfirmed.
- Font: HyperFrames swaps in a fixed web font for deterministic renders, so the label is Inter, not
  the brand's ITC Avant Garde Gothic (licensed, not installed). Drop the font file into the project
  and reference it with `@font-face` to use it.
- `revise.py` does not call `place_overlay.py` yet; they are run one after the other.
- Colours: navy label with a blue edge, one orange box and arrow (orange used sparingly, per the
  brand book). Style and timing are Ryan's calls.

## What it installed, outside the repo
HyperFrames 0.8.93 runs through `npx` (pinned in `make_overlay.py`); GSAP 3.15.0 is fetched from npm
once and cached in the temp dir. HyperFrames keeps its config in `~/.hyperframes/` (telemetry is
disabled there) and downloaded a ~197MB rendering Chrome plus a font cache into
`~/.cache/hyperframes/`. It added no skills or plugins to `~/.claude`.

## A title card and step labels, in portrait or landscape (`make_title.py`, 2026-10-06)
From Ryan's wallpaper reel (`docs/reference/WALLPAPER_REEL_ANATOMY.md`): a brand-navy title card with a small white line, a big orange word and an orange parenthetical joke, and heavy white step labels built a word at a time, lower left.
```
PRECUT_ROOT=~/precut-checkout python3 labs/overlay/make_title.py --xml "<export.xml>" --spec spec.json --out "<folder>"
python3 labs/overlay/verify_title.py "<folder>"
python3 labs/overlay/place_overlay.py "<export.xml>" "<folder>" --out "<new.xml>"
```
ONE transparent ProRes 4444 layer the length of the cut, at the sequence's own size and rate, **including portrait 1080x1920** (the callout and card templates are still landscape-only; `title_template.html` takes its canvas from the sequence). Every element is anchored to a SOURCE frame (`anchor: {source, source_sec}`), so on a revised cut you rebuild it on the new XML and each element lands on its own moment; `labs/reconform` does not rebuild it yet. Hard on and hard off, as the reference does. Spec format and the refusals (a moment not in the cut, a label under 0.9 s, elements on screen together, a title that runs past the cut) are in the module docstring and `tests/test_title.py`.
`verify_title.py` decodes the rendered file and checks it against the plan: format and alpha and exact frame count, fully transparent wherever nothing is due, the navy field over most of the frame, the three title lines arriving in order (white, then orange, then orange), each label empty before it starts and filling word by word, and clearing. Negative control run: a plan with the big word 1.0 s early and a label 0.6 s late fails three checks.
Real limits: the font is Inter (HyperFrames swaps in a fixed web font; ITC Avant Garde is not installed), no logo, English only, and the placement in Premiere is unconfirmed.
