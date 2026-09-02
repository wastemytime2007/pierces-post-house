# Des Moines Estabs generalization score, 2026-09-02

Per the task brief: run the Runnells-fitted detector (unchanged, no
re-fitting on Des Moines) against Des Moines Estabs and report the score
exactly as measured, good or bad. **It is bad — but a real, load-bearing
bug in the answer-key parser was found along the way and materially
changes what "the score" even means here.** Both are reported plainly.

## The manifest

`benchmark/des-moines-estabs/manifest.json`, built via
`posthouse.projectmanager.organize_project` — the 7 usable location
folders (per the README's "7 of 8 location sequences usable") each
declared as a single `broll` source (no `dual_use`; this is pure aerial
B-roll, no interview/A-roll component), `project_type: "other"` (it is
neither an interview, a property tour of one property, a renovation, an
event, nor a product shoot — "other" avoids implying a rule set this
project doesn't fit; `property_tour` was considered and rejected because
that enum's connotation is one continuous walkthrough of one property,
not a multi-location city B-roll library). Media stays on `/Volumes/video`,
referenced in place, never copied, per the same rule as Runnells.

## A blocking discovery: the answer key's timing is wrong for most clips

`posthouse.benchmark.parse_answer_key_xml` resolves each `<clipitem>`'s
own `<rate>` tag to convert its `<in>`/`<out>` frame numbers to seconds.
Every `_Culled` sequence in this project is edited at **23.976fps**
(`<sequence><rate><timebase>24</timebase><ntsc>TRUE</ntsc></rate>`,
verified directly in the XML for all 7 sequences) — but the SOURCE clips
are shot at a mix of 23.976 / 25 / 29.97 / 59.94 / 120fps depending on
camera and shoot day (verified with `ffprobe -show_entries
stream=r_frame_rate` directly against the files on `/Volumes/video`).

For a clip whose native rate happens to equal 23.976fps, the clipitem's
`<rate>` tag also reads 24/NTSC and everything is consistent. For every
other clip, the exported `<in>`/`<out>` frame numbers are counted in the
clip's OWN native rate (standard FCP7 convention — a clipitem's in/out
mark source trim points, not timeline position), but the parser converts
them using the wrong (sequence) rate, inflating every duration by
roughly `native_fps / 23.976`. Verified directly: `DJI_20260616135119_
0004_D.MP4` (Empowerment Bridge, Osmo, native 59.94fps) has a real
duration of 120.6s (`ffprobe`), but its answer-key truth ranges run out
to 296.5s — a 2.46x inflation, matching `59.94 / 23.976 = 2.5` almost
exactly. The same pattern holds for every 59.94fps and 120fps clip
checked (Historic Valley Junction Osmo, Gray's Lake Avata 2, Downtown,
Ingersoll Street's `DJI_0040.MP4`, the Ronin 4D clip) —**most of the
dataset**, since only Mavic 2 footage shot at native 23.976fps parses
correctly.

**This is out of this session's lane to fix** (`posthouse/benchmark.py`
is the QA/Test Engineer's file per `docs/TEAM.md`'s ownership table, and
this task's brief is the cull's gate architecture, not the scoring
harness). It is reported here as a blocking finding for the Lead to
route, with the exact evidence needed to fix it: prefer the SOURCE
FILE's own native rate (from the `<file>`/master-clip `<rate>`, not the
per-instance `<clipitem><rate>`, which mirrors the sequence rate on this
project) when it disagrees with the clipitem's own rate tag.

## Two scores, for exactly this reason

**`resid_only_score.json` / `score_arm_score.json`** — the original
8-clip representative sample (below), scored as-is. **Not trustworthy**:
6 of the 8 clips have corrupted (inflated) truth timing per the bug
above, so precision/recall against them is measuring the parser bug more
than the detector. Kept for the record, not used as the reported result.

**`resid_only_reliable.json` / `score_arm_reliable.json`** — a 5-clip
subset restricted to clips independently verified (`ffprobe`) to be
native 23.976fps, matching the project's edit rate, so their parsed
truth is trustworthy. This is the reported result.

## The reliable subset and why

| clip | location | camera/fps | truth |
| --- | --- | --- | --- |
| `DJI_0138.MP4` | Capital Building | Mavic 2, 23.976fps | 3 selects, 37.7s |
| `DJI_0144.MP4` | Capital Building | Mavic 2, 23.976fps | 3 selects, 59.1s |
| `DJI_0145.MP4` | Capital Building | Mavic 2, 23.976fps | **true full-clip reject** — 1.0s junk recording, never selected |
| `DJI_0041.MP4` | Ingersoll St. | Mavic 2, 23.976fps | 3 selects, 103.4s |
| `DJI_0134.MP4` | Historic Valley Junction | Mavic, 23.976fps | 8 selects, 67.4s |

Chosen because they are the ONLY clips across the 7 usable locations
independently confirmed to parse correctly (every other location's
folder — Downtown, Gray's Lake, Empowerment Bridge, Oak Highland Park —
and every Osmo/Avata 2/Ronin 4D subfolder checked, plus one Mavic 2 clip
in each of Ingersoll St. and Historic Valley Junction, run at 25/29.97/
59.94/120fps native, all corrupted per the bug above). This is a real
limitation, not a preference: **a properly representative, multi-camera
Des Moines sample cannot be scored today** without either fixing the
parser or hand-deriving corrected truth ranges for the other clips,
neither of which is this session's job. The subset still spans 3 of the
7 locations, includes one genuinely long clip (301s) and one short one
(207s), a mix of long (67-103s aggregate) and negligible (1s) truth, and
one true full-clip reject — as representative as the bug allows.

Signals were extracted fresh for all 5 (not cached) via
`posthouse.cull.signals.extract_signals`, classified via
`posthouse.cull.classify.classify_sidecar`, then segmented via
`posthouse.cull.segment.segment_source` with the shipped Runnells-fitted
params (`consolidation=stability`), unchanged.

## The score — reported exactly as measured

| arm | P | R | F1 | IoU |
| --- | --- | --- | --- | --- |
| **select-everything baseline** | **0.338** | 1.000 | 0.505 | **0.300** |
| `resid_only` (shipped) | 0.317 | 0.714 | 0.439 | 0.255 |
| `score` (secondary comparison) | 0.318 | 0.695 | 0.436 | 0.253 |
| *(for reference)* Runnells held-out, `resid_only` | 0.627 | 0.911 | 0.737 | 0.417 |
| *(for reference)* crude probe, fair CV, on Runnells | 0.635 | 0.881 | 0.737 | 0.428 |

**Both fitted arms score BELOW select-everything on precision and IoU on
Des Moines.** Recall drops from ~0.91 (Runnells held-out) to ~0.70-0.71.
Precision roughly halves, from ~0.62-0.63 to ~0.32. This is the
generalization failure ROADMAP's benchmark v2 exists to surface, stated
plainly rather than rationalized: **a detector fitted on handheld Osmo
footage (Runnells) does not transfer to gimbal-stabilized Mavic 2 aerial
footage (Des Moines), even restricted to the one camera/frame-rate the
ground truth can currently be trusted for.**

Per-clip detail (`resid_only`, the shipped arm): `DJI_0138.MP4` predicted
ZERO segments (recall 0 on that clip — the exposure gate or the
resid_max=9.0 cap, tuned to Runnells' handheld noise floor, apparently
rejects this specific clip's whole content; not diagnosed further here,
out of scope). `DJI_0145.MP4`, the true full-clip reject, correctly
predicted zero segments — the one clean win. `DJI_0144.MP4`,
`DJI_0041.MP4`, and `DJI_0134.MP4` all show real overlap with truth but
with heavy over-prediction (predicted duration 1.5-4x truth duration on
each), which is exactly what an absolute residual cap fitted to
Runnells' handheld noise floor (median smoothed resid 1.14 px/frame)
does on footage whose own smoothed resid rarely exceeds 0.5-1.0 px/frame
(measured directly on two Des Moines clips during the grid-widening
work, see `posthouse/cull/fit.py`'s `STABILITY_RESID_MAX_GRID` comment)
— nearly every frame clears a cap of 9.0 trivially, so the detector
barely discriminates at all on this camera.

## What this does and does not say

Does not say: the `score` combine mode is meaningfully worse than
`resid_only` (they are within a point of each other on Des Moines, same
as on Runnells). Does not say the gate-combination fix (this session's
main deliverable) was wrong — Runnells held-out numbers genuinely
improved. **Does say**: an absolute, unnormalized motion-residual
threshold cannot be expected to transfer across cameras with different
native shake/stabilization characteristics, which is a structural
argument for a PER-CLIP-RELATIVE threshold (exactly what the `score`
combine mode's percentile-rank normalization was designed to provide) —
but the measurement here shows that percentile-rank normalization ALONE,
without also being fitted on footage that looks like the target camera,
does not close the gap either. Both `resid_only`'s absolute cap and
`score`'s per-clip rank are being asked to make a call that this fit
never saw evidence for. Flagged as an open question, not resolved here:
whether the real fix is per-camera-class fitting, a differently
normalized signal, or accepting that this cull needs its threshold
refit whenever the camera changes materially.
