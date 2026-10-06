# captions: the cut's speech as a transparent caption layer

Spike. Standalone, file in and file out, not wired into `app/`. One of the skills from the creator's
"Forget Capcut" workflow (captions generated from the speech and moved around the frame so they stay
clear of other graphics).

**In:** an export XML, a time window, optionally the overlay folders to keep clear of.
**Out:** `captions.mov` (transparent ProRes 4444 at the sequence's own size and frame rate),
`captions_preview.mp4` (that layer, plus any overlays, over the untouched cut), `captions.json`
(every line and word with its timing and position), `placement.json`, and the HyperFrames project.

```
PRECUT_ROOT=~/precut-checkout python3 labs/captions/make_captions.py \
    --xml "<export.xml>" --out "<folder>" --start 0 --end 22 \
    [--avoid "<overlay folder>"] [--style pill|plain] [--open]
python3 labs/captions/verify_captions.py "<folder>"
python3 labs/overlay/place_overlay.py "<export.xml>" "<captions folder>" --out "<new.xml>"
python3 -m pytest labs/captions/tests -q
```
`place_overlay.py` puts it on a new video track at the time it was made for (no note anchor).

## How it works
- **The words** come from transcribing the cut's own rendered audio with PreCut's `Transcriber` (its
  model, language and seeded decode), so timing is what a viewer hears across every seam, including
  the edits made from notes. Not from a separate transcript of the raw footage.
- **Verbatim.** The captions are the speech as transcribed: no rewording. Dashes become commas, because
  an em dash in a transcribed quote reads as machine-processed.
- **Lines** break at sentence ends, pauses over 0.4s, a comma after a phrase of three words, or 6 words,
  34 characters or 2.8s. One line is on screen at a time; the word being spoken is highlighted in light
  blue as it is said.
- **Position.** Bottom by default. If an overlay callout would be on screen at the same time in that spot,
  the line goes to the top. If both are blocked it stays at the bottom and says so.
- **Style.** White type on a navy pill (brand navy, light blue highlight), or `--style plain` for a soft
  shadow with no pill. The font is Inter, not the brand's ITC Avant Garde: HyperFrames substitutes a fixed
  web font for deterministic renders and the brand font is licensed and not installed.

## What is verified (`verify_captions.py`)
On the rendered files: real alpha at the right size, rate and length; transparent in the gaps between
lines; every line appears at its own time; each line is in its planned band and inside the frame
margins; the highlight moves left to right through a line word by word; and **no pixel of a callout
overlay is covered by a caption** while both are on screen (alpha of the two rendered layers compared frame
by frame).

On the words, per the `verified-quotes` skill: the repetition-loop detector finds no loops; a **different
Whisper model (base)** transcribes the same audio and must agree with the captions on at least 85% of
words with a median start-time difference of 150ms or less; and the skill's `verify_quotes.py` checks each
caption line against that independent transcript, with counts reported. That last check is informational,
because two models legitimately word some phrases differently.

## Real limits
- The two transcripts share one audio track, so a word both models mishear the same way passes. The
  check catches drift and invention, not a shared error. Read the captions.
- Captions are placed at the bottom or top only. It does not look at the picture, so it can cover a face
  or a subject; it only avoids the overlays it is told about.
- Word timing is Whisper's, good to about a tenth of a second.
- English only (PreCut's `WHISPER_LANGUAGE`).
- One window at a time. Style, size, colours and timing are Ryan's calls.

## Portrait (1080x1920), 2026-10-06
For a vertical sequence the captions use the portrait layout (`make_captions.LAYOUTS`, chosen by `use_layout` from the sequence's size): a 1080x1920 canvas, 76 px type, lines of at most 28 characters or 5 words (two rows at most), and a 380 px bottom margin so a caption stays clear of a platform's own buttons and text. `placement.json` records the layout and `verify_captions.py` reads it. Landscape is unchanged. The first real portrait run (Reel 3, 34 lines): every check passed. Words on the bleep list are starred in the caption (`a**`), so the clear word never shows.
