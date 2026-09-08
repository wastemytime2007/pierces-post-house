# Anatomy of the finished wallpaper Reel

Owner: Lead. **This is the target.** Ryan cut this by hand from the same
footage the app works on, then handed it over specifically to calibrate
what "good" means: *"Pay attention to the pacing, music, sfx, text on
screen, transitions, colors, tone, everything you can to help guide us
closer to the end goal in the future."* (2026-09-08)

Source: `~/Desktop/SoldFast Exports/Removing Wallpaper Tutorial.mp4`
(not in-repo — 166 MB). Every number below is measured from that file
with ffmpeg/scene detection and real frames viewed, not estimated.

Related: `safety_net/fixtures/reference_edits/removing_wallpaper_tutorial.xml`
is the TIMELINE for this piece (11 cut clips + 8 unused), already used as
the contract's ground truth. This document is the FINISHED piece — the
graphics, sound and structure the XML can't express.

---

## Hard numbers

| Property | Value |
| --- | --- |
| Duration | **67.1s** |
| Frame | 1080x1920 vertical, 60fps |
| Cuts | **~23**, average **one every 2.9s** |
| Loudness | **-12.5 LUFS** mean, range only **4.4 LU** |
| Silence | **none** — audio is continuous end to end |
| Black frames / dips | **none** |
| Motion profile | "moderate visual complexity, low motion" — static mid-detail shots |

**67 seconds is the single most useful number here.** It sits inside the
30-90s window Ryan later stated as the rule, and well past the 45s the
app had been aiming at. Do not treat 45s as the default target for a
how-to: this piece needed 67s to carry four method beats plus a joke.

## Pacing has three gears, and they are deliberate

| Section | Cuts | Rate |
| --- | --- | --- |
| 0:00-0:26 (hook + first method beats) | 12 | ~2.2s per cut |
| 0:26-0:49 (the glue explanation) | 5 | ~4.6s per cut |
| 0:49-1:07 (payoff + return) | 6+ | ~2.5s per cut |

Fast to get in, **slower while the actual expertise is being explained**,
fast again to get out. This is the opposite of a uniform "punchy" edit,
and it matches the length allocation his timeline shows: short clips to
establish, long clips for the substance.

## Structure: it's a LOOP, not a line

The kitchen scene is **split and used as bookends** around the work
footage:

```
0:00-0:02  KITCHEN, black hoodie. "It smelled like rotted corpses."  <- cold open
0:02-0:03  He pulls the hoodie off over his head                     <- physical gag
0:03-0:05  TITLE CARD over the gag
0:05-0:08  ORANGE tee, hallway walk -> "STEP 1: STEAM WAND"
0:08-0:26  Steam method: moisture, reactivates glue, why some paper resists
0:26-0:40  Diagnosis: "that's just the glue... it was a wheat glue"
0:40-0:53  Scrape it, then TSP / Spic and Span
0:53-0:59  THE PAYOFF: "if you don't get this glue off, when you go to
           paint it's going to look all kinds of funky"
0:58       WHIP PAN back to the kitchen
1:00-1:07  KITCHEN, black hoodie again. Holds up the DIF jug: "I found
           some in the shop and I don't know if something climbed in
           that bottle"                                              <- loop back to the smell
```

The smell line that opens the piece and the bottle that closes it are
**the same moment in the same scene**, cut apart and placed at either
end. The hook is a fragment of the outro. No generator that only walks
the timeline forward will find this shape.

## Graphics

**Title card (0:03-0:05)**, over the hoodie gag, on a navy/blue brand
field with faint diagonal geometry:

- `HOW TO` — white, bold caps, small
- `REMOVE WALLPAPER` — **orange**, large, bold caps, slight arc
- `(AND SWEATSHIRTS)` — orange, small caps, **the joke**
- `with Bob` — white, sentence case
- SoldFast logo, bottom centre (white "Sold" + light-blue "Fast" + house mark)

Text **builds progressively** over roughly two seconds rather than
appearing at once — title line, then the parenthetical, then the name.

**Step labels**, e.g. 0:05-0:08: white heavy bold caps, drop-shadowed,
lower-left third, also built progressively (`STEP` -> `STEP 1:` ->
`STEP 1: STEAM WAND`). They sit over live action, never over a card, and
they clear before the next beat.

Nothing is centred over the subject's face or hands. During the actual
demonstration (e.g. 0:24-0:25, scraping) there is **no text at all** —
the technique is left to read on its own.

## Colour, wardrobe, tone

- Brand palette used as graphics only: navy/blue field, **orange for
  emphasis**, white for primary text. Footage itself is ungraded-looking
  and warm — real job-site light, skylight blowout left in.
- **Wardrobe carries the structure**: black SoldFast hoodie for the
  kitchen bookends, orange SoldFast tee for the work. The change of
  shirt is what tells you which timeframe you're in, and the hoodie
  removal is what bridges them.
- **No CTA card, no "follow us", no end slate.** The brand is carried by
  the shirt and the title logo. It ends on the joke.

## Sound

- Music runs **continuously** under the whole piece — zero detected
  silence. Mean -12.5 LUFS with only 4.4 LU of range means it's
  compressed and levelled hard, speech riding consistently over a bed.
- No detectable SFX stings at the cuts (no black-frame or loudness
  spikes at transition points).
- Transitions are **hard cuts plus one real whip pan** at 0:58 — a
  camera move used as the transition, not a plugin effect. No
  dissolves, no fades.

## The thing that matters most for the app

**Comedy is structural here, not decoration.** The hook is a physical
gag (pulling the sweatshirt off), the title contains a joke
(`AND SWEATSHIRTS`), and the closing beat is a joke about something
having crawled into a bottle. The instructional content is real and
sits in the middle, protected by humour at both ends.

For weeks the audience profile told the planner "Heart-driven, **not
comedic**" (corrected 2026-09-07, see `docs/STATUS.md`). That rule was
the exact opposite of what Ryan's own finished piece does. When judging
whether a proposed cut is on-brand, this file — not a remembered rule —
is the reference.

## What to change in the app because of this

Concrete, and each traceable to a number above:

1. **Don't default a how-to to 45s.** 67s carried four method beats plus
   a joke; 45s would have cost one of them. `DEFAULT_REEL_TARGET_SEC` is
   60s with a 75s ceiling, which is closer, but a how-to specifically
   should be allowed toward 90s.
2. **Vary clip length by role, not uniformly.** ~2.2s in the hook,
   ~4.6s while explaining, tight again to close.
3. **Look for a loop.** The strongest hook here is a fragment of the
   final scene. The generator can already reorder; it should be told the
   opener may come from the end.
4. **A character/comedy beat can be the hook of an instructional
   piece** — and can be a different scene entirely from the method.
5. **Leave the demonstration clean.** No text over the moment the
   technique is visible.
