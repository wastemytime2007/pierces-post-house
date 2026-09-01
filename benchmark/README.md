# The benchmark: making the answer key

This folder holds one real project (Runnells Day 1) that we use to
measure the Assistant Editor's cull instead of guessing whether it's
any good. Your job here is about an hour of normal editor work in
Premiere. No code, no new tools.

## What you're making

For each of the two clips in the Osmo folder (one is about 33 minutes,
the other about 4), mark every range that an assistant editor would
call usable, and put them all on one sequence in source order. That sequence, exported as an XML, becomes the
"correct answer" the software gets checked against.

## Steps

1. Open Premiere and import the two Osmo clips from
   `First Walkthrough After Taking Over/Osmo` (on `RDOSS_2025`).
2. Create a new sequence named exactly **`Runnells Day 1 Selects`**.
3. Go through each clip start to finish. Every time you find a range
   you would actually use (or hand to an editor as a candidate), set
   an in point and an out point around it, the same way you'd mark a
   select on any real job. A clip can have zero, one, or many usable
   ranges.
4. Insert each marked range onto the Selects sequence, in the order
   you found it. Don't worry about making it watchable or trimming it
   tight, just mark the range and insert it. Handles are fine, tight
   cuts are fine, whatever your normal instinct is. Don't nest
   sequences. If Premiere made one for you along the way, flatten it
   before exporting.
5. When you've gone through both clips completely, select the
   sequence and go to **File > Export > Final Cut Pro XML**.
6. Save the file as:
   `benchmark/runnells-day-1/answer_key.xml`

That's it. Nothing else in this folder needs to change.

## Why mark usable ranges directly, instead of using a finished edit

A finished edit only tells you what got used, not everything that
was usable. Good footage gets left out of a cut for a lot of reasons
that have nothing to do with quality: the story didn't need it, there
was a better take, time ran short. If we scored the software against
a finished edit, it would look wrong every time it correctly found
good footage that you simply didn't use.

Marking every usable range straight from the raw footage skips that
problem entirely. There's no gap between "what's usable" and "what
got used" to correct for, because we're not looking at a used cut at
all.
