# Final review — did the finished edit match what the app found?

Owner: Lead. Built 2026-09-15. Ryan: *"would it make sense to add a
section to upload the final edited videos for each project so that the
app can analyze the final product and see how its ideas were
implemented, what it may have missed, and learn how to do a better job
in the future?"*

**Scope of this slice, agreed with Ryan before building it:** a project
convention plus a diff report a human reads. It does **not** feed back
into planning automatically — that is a separate, bigger, not-yet-
approved step (rule 7: prove on one real unit before scaling any
capability; rule 5: every slice ends in something Ryan can judge, not a
layer of machinery).

## Status: built and hermetically tested, not yet run on real data

`posthouse/final_review.py` has 8 passing tests
(`safety_net/tests/test_final_review.py`) against synthetic idea JSON
and a hand-written FCP7 XML fixture. **No real project has a finished
edit sitting in `finals/` yet** — the two finished videos this project
has (the wallpaper Reel, the eviction video) both predate the app's own
suggestions and were hand-cut references, not something edited from an
exported idea. The first real run needs Ryan to finish editing an
already-exported idea (the Mitch Interview "A Little Bit Further" plan,
re-exported after the 2026-09-11 fixes, is the obvious first candidate)
and drop the result into `finals/`. Until that happens this stays
**§ In progress**, not Done, per rule 4 — a hermetic test proves the
logic; it does not prove the tool is useful on Ryan's own material.

## The convention

Alongside a project's existing `plans/idea_<hash>.json`, drop the
finished Premiere export into:

```
<project>/finals/idea_<hash>_final.xml
```

Export the actual edited sequence (File > Export > Final Cut Pro XML) —
not a selects-only sequence, the real timeline. A rendered
`finals/idea_<hash>_final.mp4` alongside it is optional but
recommended: it's the only reliable source of the finished runtime (see
below), and separately worth a qualitative pacing/tone pass the way
`WALLPAPER_REEL_ANATOMY.md` was built from Ryan's own wallpaper Reel —
this tool does not attempt that; it only diffs ranges.

## Running it

```bash
python3 -m posthouse.final_review <project>/plans/idea_<hash>.json \
                                   <project>/finals/idea_<hash>_final.xml \
                                   --video <project>/finals/idea_<hash>_final.mp4
```

Prints a markdown report. `--json-out <path>` also writes the structured
result, for whatever reads it next once there's a reason to.

## What it answers

Four questions, all things Ryan asked directly:

1. **Kept** — which of the idea's proposed `source_ranges` (the tight
   cut) survived into the final, and how much of each.
2. **Dropped** — which proposed ranges never made it in at all.
3. **Pulled from the pool** — final footage that came from `pool_ranges`
   (the leftovers zone), which is the direct proof that side of the
   two-zone export is doing real work and not just clutter.
4. **Added from elsewhere** — final footage the idea never surfaced in
   *either* zone. This is the most actionable bucket: if the same kind
   of footage keeps showing up here across several reviews, that's a
   real, evidenced gap in what gets found, not a guess.

## The matching problem this exists to get right

An idea's ranges carry the **proxy** path — that's what the planner
read (`.../proxies/A005_..._Proxy.mp4`). A real Premiere export
references the **original** camera file, resolved at export time by
`exporter._build_proxy_to_original_map` (`.../A005_..._Proxy.mov` — same
stem, different directory, different extension). This is the exact
proxy/original distinction behind the wrong-camera export bug fixed
2026-09-11 (`ROADMAP.md` Decision Log). Matching by exact path, or even
exact basename — which is what `posthouse.benchmark._group_by_source`
correctly does, for a *different* purpose (benchmark precision/recall
against a human answer key) — would silently find zero matches here: a
clean parse with a wrong number, the exact failure mode the
`footage-analysis` skill warns about. `final_review.py` matches by
filename **stem** instead (case-insensitive, extension stripped),
proven in `test_stem_matches_across_proxy_to_original_extension_change`.

## Why "final runtime" needs a video, and isn't computed from the XML

Summing every clipitem's own duration double-counts overlapping tracks,
ignores speed changes, and says nothing about titles or music-only
stretches — another clean-parse-wrong-number trap. The report says
"not available" rather than guess when no video is supplied, and reads
the real duration via `ffprobe` when one is.

## What it reuses, on purpose

`posthouse.benchmark.parse_answer_key_xml` for the XML itself, because
it already solves the two real frame-rate gotchas (conform-to-sequence
vs. a retimed source) that produce a clean-but-wrong parse if
reimplemented from intuition — see the `footage-analysis` skill and
that function's own docstring. Its interval-math helpers
(`_merge_intervals`, `_overlap_sec`) are reused too. The stem-based
grouping and the kept/dropped/pool/added classification are new — they
answer "which of these specific proposed ranges survived," not
benchmark-style aggregate precision/recall.
