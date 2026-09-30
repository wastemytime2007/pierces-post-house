# reconform: put the layers back on a revised cut

Spike. Standalone, file in and file out, not wired into `app/`. It closes the gap the review page
made visible: `revise.py` ripples every track alike, which keeps captions with the speech but cuts
through a callout, an effect or the music bed (each shown as a `layers` warning on the page).

```
PRECUT_ROOT=~/precut-checkout python3 labs/reconform/reconform.py \
    --revised "<revise's output xml>" --overlay "<callout folder>" [--overlay ...] \
    --captions "<captions folder>" --audio "<audio folder>" --out "<folder>"
python3 -m pytest labs/reconform/tests -q
```

## What it does
1. **Strips the layers** off the revised XML: every clip whose file id is `overlay-file-*` or
   `audio-file-*`, however many pieces the revision split it into. What is left is exactly the
   revised cut (checked: the cut's clips and speech tracks are identical before and after).
2. **Callout**: re-placed on the new cut by the source frame it was drawn on, with the same `.mov`.
   If that frame was cut out of the revision, the callout is reported dropped, and the effect that
   went with it is not rebuilt (it would have nothing to be timed to).
3. **Captions**: rebuilt from the revised cut's own audio (`make_captions.py`, same style), for the
   window the layer still covers after the revision. They avoid the re-placed callout.
4. **Music and effect**: `make_audio.py` again on the revised speech, with the same prompts, levels,
   music length and cache, so the same generated audio is mixed and ducked under the new timing. Nothing
   is generated again and nothing is spent. The effect goes at the re-placed callout's entrance.
5. Each step is verified by its own tool (`place_overlay`, `verify_captions`, `verify_audio`,
   `place_audio`), then the whole is checked: every layer whole (not split), the cut unchanged, the
   effect with its callout. Then the review page is built from the new XML, which verifies the layers
   are visible and in the mix.

## Real limits
- A window shorter than one second after the revision is not rebuilt (reported).
- The music is the same generated file, cut to the new window and re-ducked: its phrasing is not
  re-fitted to the new length, only faded out at the end.
- Captions are re-transcribed, so a word's wording can differ from the earlier run; they are verified
  the same way as any captions (independent second transcript), and the lines are Ryan's to read.
- One captions layer and one audio folder per run; several callouts are supported.
- It rebuilds layers; it does not decide what a note wants a layer to be. Edits to a layer itself
  (a different callout wording, another effect) are separate notes and separate tools.
