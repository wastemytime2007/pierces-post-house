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
    [--title "The spacer"] [--subtitle "A piece of cardboard pulled off the box"] [--open]
python3 labs/overlay/verify_overlay.py "<folder>"
python3 -m pytest labs/overlay/tests -q
```

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
- Not opened in Premiere. That the `.mov` imports with its alpha there is unconfirmed.
- Font: HyperFrames swaps in a fixed web font for deterministic renders, so the label is Inter, not
  the brand's ITC Avant Garde Gothic (licensed, not installed). Drop the font file into the project
  and reference it with `@font-face` to use it.
- The overlay is not inserted into the revised XML. It is a separate file you place.
- Colours: navy label with a blue edge, one orange box and arrow (orange used sparingly, per the
  brand book). Style and timing are Ryan's calls.

## What it installed, outside the repo
HyperFrames 0.8.93 runs through `npx` (pinned in `make_overlay.py`); GSAP 3.15.0 is fetched from npm
once and cached in the temp dir. HyperFrames keeps its config in `~/.hyperframes/` (telemetry is
disabled there) and downloaded a ~197MB rendering Chrome plus a font cache into
`~/.cache/hyperframes/`. It added no skills or plugins to `~/.claude`.
