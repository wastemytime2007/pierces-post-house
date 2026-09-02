# Benchmark v2 candidate: Des Moines Estabs

Ryan provided this on 2026-09-02: a real, organized drone (establishing
shot) project spanning eight shoot days across Des Moines, April through
July 2026. Raw footage lives on `/Volumes/video`, referenced in place per
the same rule as Runnells (footage is never copied).

## What this is, and why it's a different kind of ground truth than Runnells

Runnells was marked in a dedicated pass, purpose-built as an answer key:
Ryan opened the raw clips and set in/out around every usable range. This
project is different: it is real, already-organized production
work-Premiere sequences named `<Location>_Culled` that a real job already
produced. That makes it a genuinely independent measurement, not a second
version of the same one, and it adds ground truth Runnells could not:
**whole-clip rejects.** Runnells was one continuous clip; every frame of
it had to be judged in-range or out. This project has 119 raw clips
across its seven usable location folders, and 59 of them were never
touched by any select at all-a clean, real negative example a walkthrough
clip cannot produce.

The honest caveat, stated plainly: these are `_Culled` sequences, i.e.
the assistant-editor style organized-selects product, not a polished
narrative cut. That should make them close in spirit to Runnells (usable
ranges, not "what a story needed"), but it has not been confirmed with
Ryan the way Runnells' marking pass was purpose-built and explained.
Treat it as a strong benchmark candidate, not yet ratified the way
Runnells was.

## A different shoot, on purpose

This is what makes it valuable beyond just "more data": it is drone
footage (DJI Mavic 2, gimbal-stabilized aerial), not handheld Osmo
walkthrough footage. Different camera, different operator behavior,
different failure modes (no handheld shake; sustained pans, orbits, and
reveals are often the *entire point* of a shot rather than a defect), and
select durations that are an order of magnitude longer (median 12.7s vs
Runnells' 3.4s). Design PHASE4_CULL_DESIGN.md is explicit that Runnells
alone can rank parameter sets but cannot establish generalization. This
is the first real chance to find out whether anything measured on
Runnells holds up on footage that looks nothing like it.

## A real defect this asset caught, immediately

The original export (`answer_key_original_unfiltered.xml`, kept here
unmodified as the source of record) contains one sequence,
`Downtown Night Shoot_Culled`, built with eight nested Premiere
sequences inside it (`Nested Sequence 01`-`08`, almost certainly mask or
grade adjustment nests-there are `Masks` folders alongside the raw
footage). `posthouse.benchmark.parse_answer_key_xml` refused to parse
the file, exactly as slice 3 designed it to: an outer clipitem's in/out
does not trim what is nested inside it, so silently walking in would have
over-counted that sequence's truth ranges. The guard did its job on the
first real file that could have tripped it.

**Resolution:** `answer_key.xml` in this folder is a filtered copy with
`Downtown Night Shoot_Culled` and its eight nests removed (by element,
not by rewriting ranges-no trim math was invented here, which is
deliberate; a mis-derived range would be the worst kind of benchmark
error, a silently wrong ground truth). That drops 16 raw clips and about
0.7 of the 74.5 total minutes from the ground truth. Building a real,
tested nested-sequence flattener-or asking Ryan to flatten that one
sequence by hand in Premiere-is future work, not done under time
pressure here. Until then, `Downtown Night Shoot_Culled`'s 16 clips are
excluded from BOTH the select ground truth and the full-clip-reject
ground truth (a clip's absence from the filtered selects does not mean
Ryan rejected it-it may simply live in the excluded sequence).

## Numbers (filtered answer key, `answer_key.xml`)

**Corrected 2026-09-02.** The numbers first published here were computed
with a parser that had a real frame-rate bug, described below. These are
the corrected figures.

- 7 of 8 location sequences usable: Downtown, Capital Building, Grays
  Lake, Empowerment Bridge, Historic Valley Junction, Ingersoll St., Oak
  Highland Park.
- 238 select ranges across 60 distinct source clips.
- Total marked-usable time: **41.5 minutes** (2,492s), about 27x
  Runnells' 92.2s.
- Select duration: min **0.8s**, median **6.5s**, max **71.3s**.
- Raw clips in the 7 usable sequences' folders: 119. Of those, **59 are
  true full-clip rejects**, never selected at all.

### The frame-rate bug that made the first numbers wrong

Most of this dataset was shot at a different frame rate than the
project's 23.976fps edit timeline (59.94fps Osmo, 120fps Avata 2, and
others). When Premiere conforms a source into a sequence at a different
rate, it writes each clipitem's own `<rate>` tag as the SEQUENCE's rate,
while the clipitem's `<in>`/`<out>` frame numbers stay counted in the
SOURCE FILE's native rate. `parse_answer_key_xml` divided by the
clipitem's declared rate, inflating those durations by
native_rate / sequence_rate - a factor of 2.5 for a 59.94fps source.
**39 of the 60 source clips were affected**, which is why the total read
73.1 minutes instead of the true 41.5.

The arithmetic proves itself: one clipitem on a 265.4s file declared
`out=15390` at a stated 24fps, which is 641 seconds - longer than the
file it points into, and therefore impossible. At the file's real
59.94fps it is 256.8s, inside the file. Fixed in `posthouse/benchmark.py`
by resolving each clipitem against the referenced `<file>`'s own declared
rate whenever the two disagree, plus a bounds check that now refuses any
range extending past its own file's duration rather than emitting it
silently. Runnells is unaffected (no rate mismatch) and parses
identically before and after, which is the no-regression check.

## Status

Staged, parses cleanly through the shipped harness (verified
2026-09-02), not yet scored against any detector. Scoring against it is
the natural next step once Phase 4 slice 5 (the stability-threshold
detector, demoted classifier) lands-this is a materially more informative
test of generalization than the small held-out strip Ryan is separately
marking on the Runnells 33-minute clip, precisely because it is a
different shoot.
