# Per-clip residual normalization: the cross-shoot transfer measurement

2026-09-02. Ryan's ruling on the generalization failure: normalize the
motion-residual gate **per clip** so its threshold adapts to each shoot,
not per-shoot fitting, not per-camera scoping. Per the Lead's own framing
before building, "normalize per clip" has two meanings that fail
differently, so both ship as competing, separately selectable strategies
(`SegmentParams.stability_resid_norm`) and this document is the
measurement that decides between them — fit and cross-validated through
the same machinery `posthouse/cull/fit.py` already built (3-block CV,
block bootstrap, fixture-ordering guards, the grid-edge alarm,
recall-first-under-precision-floor ranking), **never re-fitted on the
shoot being scored.**

## The two implementations

Both live in `posthouse/cull/segment.py`, gated by the new
`SegmentParams.stability_resid_norm` field (`"absolute"` | `"quantile"` |
`"robust_scale"`), isolated for this measurement under
`stability_combine="resid_only"` — the cleanest arm for "does per-clip
residual normalization alone achieve transfer," since it removes the
lapvar/combine-structure question entirely.

- **`"absolute"`** — the control arm, unchanged: `resid_smooth <
  stability_resid_max`, a raw px/frame cap. This is the mode that failed
  to transfer in the original measurement.
- **`"quantile"`** — mirrors how `stability_lapvar_quantile` already
  works: threshold at a fitted per-clip percentile of the smoothed
  residual (`stability_resid_quantile`), keeping the bottom
  `stability_resid_quantile` fraction of THIS clip's own residual
  distribution (`resid_smooth <= percentile(resid_smooth, 100 *
  stability_resid_quantile)`). Scale-free, but assumes a roughly constant
  *fraction* of every clip is usable — a uniformly excellent clip still
  loses its top `1 - stability_resid_quantile` fraction even though none
  of it is actually bad.
- **`"robust_scale"`** — median/MAD z-scoring: `(resid_smooth -
  median(resid_smooth)) / (1.4826 * MAD(resid_smooth))`, thresholded
  against `stability_resid_z_max` in normalized units (1.4826 is the
  standard normal-consistent MAD scale factor). Scale-free AND not
  fraction-fixed: a uniformly good clip can stay almost entirely
  selected, a uniformly bad one almost entirely rejected.

### Degenerate-case guards (`_robust_z`, `_resid_ok` in `segment.py`)

- **Zero-MAD clip (perfectly constant smoothed residual)**: `_robust_z`
  returns exactly `0.0` for every frame rather than dividing by zero —
  every frame trivially ties its own median, so nothing is abnormal
  relative to a distribution with no spread, and a perfectly stable clip
  is judged entirely stable rather than rejected by a silent
  `nan <= threshold` (always `False` in numpy). Tested:
  `test_resid_ok_robust_scale_zero_mad_does_not_divide_by_zero`.
- **Clip shorter than the smoothing window**: `_classify._smooth`
  already edge-pads correctly for a window wider than the array, and
  `np.median`/`np.percentile` are well-defined on any non-empty array, so
  this was mostly already safe; still exercised directly end-to-end
  (5-frame clip, all three norm strategies, asserts `segment_source` runs
  to completion and the tiling invariant holds) in
  `test_segment_source_clip_shorter_than_smoothing_window_does_not_crash`.
- **Empty residual array**: `_robust_z(np.zeros(0))` returns a length-0
  array rather than raising — `test_resid_ok_robust_scale_empty_array_does_not_crash`.
- **Scale invariance** (the actual property the ruling is asking for):
  `test_resid_ok_quantile_keeps_the_fitted_fraction_per_clip` and
  `test_resid_ok_robust_scale_z_score_and_scale_invariance` assert
  directly that rescaling a clip's residual by a constant factor (a
  different camera's absolute noise floor) does not change which frames
  pass under either normalized strategy.

`posthouse/cull/fit.py` gained the matching harness plumbing:
`STAGE_GRID_STABILITY_RESID_ONLY_QUANTILE` / `_ROBUST_SCALE` (both plain,
evenly spaced brackets around the reasoned unfit defaults — 0.70 and 3.0
— since both parameters are scale-free by construction and need no
data-informed absolute bounds), `stability_resid_norm` threaded through
`fit_one()`/`run_arm()`, two new first-class, fully block-CV'd arms
(`stability_resid_only_quantile_full`, `stability_resid_only_robust_scale_full`,
alongside the pre-existing `stability_resid_only_full` as the `"absolute"`
control), and two new ablation verdicts
(`stability_resid_norm_quantile_vs_absolute`,
`stability_resid_norm_robust_scale_vs_absolute`) plus a 3-way ranking in
every `fit_report.json`.

## The Des Moines baseline, re-measured with the fixed parser

The failing number on record (P 0.317 / R 0.714 / IoU 0.255 for the
Runnells-fitted detector) was measured on a 5-clip subset chosen under
the OLD, buggy answer-key parser. `posthouse/benchmark.py` already
carries the fix (resolving each clipitem's rate against its referenced
`<file>`'s own rate on disagreement, per the 2026-09-02 Decision Log) —
unmodified by this session, verified by reading `_resolve_conversion_rate`
and `parse_answer_key_xml` directly. This measurement re-runs against
that fixed parser on a **12-clip set**, the full cached-sidecar coverage
available this session (extraction is the expensive step, ~3x realtime;
no new clips were extracted — the cached set already spans what matters):

| clip | duration | truth | native rate/camera |
| --- | --- | --- | --- |
| `A014C0005_700101_C0B714.MOV` | 124.2s | 50.6s (5 selects) | 60fps |
| `DJI_0040.MP4` | 73.2s | 34.0s (2) | 59.94fps Mavic 2 |
| `DJI_0041.MP4` | 207.1s | 103.4s (3) | 23.976fps Mavic 2 |
| `DJI_0134.MP4` | 301.0s | 67.4s (8) | 23.976fps Mavic |
| `DJI_0138.MP4` | 81.4s | 37.7s (3) | 23.976fps Mavic 2 |
| `DJI_0144.MP4` | 301.0s | 59.1s (3) | 23.976fps Mavic 2 |
| `DJI_0145.MP4` | 1.0s | 0.0s (0) | 23.976fps — **true full-clip reject** |
| `DJI_0211.MP4` | 64.6s | 0.0s (0) | 29.97fps — **true full-clip reject** |
| `DJI_20260420163534_0002_D.MP4` | 231.9s | 24.1s (1) | 119.88fps |
| `DJI_20260513155107_0002_D.MP4` | 265.4s | 34.8s (8) | 59.94fps |
| `DJI_20260616135119_0004_D.MP4` | 120.6s | 63.2s (9) | 59.94fps |
| `DJI_20260619103722_0003_D.MP4` | 159.0s | 68.9s (3) | 59.94fps |

Coverage: 5 native frame rates (23.976/29.97/59.94/60/119.88fps — the
exact mismatch class the parser bug corrupted), long-and-mostly-usable
clips (`DJI_0134.MP4`/`DJI_0144.MP4`, 301s each) alongside short ones,
and 2 genuine full-clip rejects. Total: 32.2 minutes of footage, 9.05
minutes (543.2s) marked usable across 10 clips with truth.

**Re-measured select-everything baseline (fixed parser, 12-clip set):
P 0.336 / R 1.000 / F1 0.503 / IoU 0.291.** Close to, not identical to,
the previously-reported 5-clip-subset number (P 0.338 / R 1.000 / IoU
0.300) — expected, since the earlier number was measured on clips
independently pre-verified correct, i.e. it was already a clean subset;
the new number adds real diversity (5 frame rates instead of 1, 2 true
full-clip rejects) without moving materially.

**Caveat carried forward, not fixed by this session (`posthouse/benchmark.py`
is out of this session's file-ownership scope — Senior Engineer writes to
`posthouse/cull/`, `safety_net/`, `benchmark/`, not the scoring harness
itself):** `bm.score()`'s existing, deliberate convention excludes any
predicted source with **zero truth ranges** from precision/recall/IoU
entirely (`Score.unscored_predicted_sources`), because it cannot
distinguish "genuinely rejected in full" from "the answer key doesn't
cover this source yet." Verified directly: `DJI_0145.MP4` and
`DJI_0211.MP4` — this set's two true full-clip rejects — both appear in
`unscored_predicted_sources`, not scored. **Every number in this
document is therefore blind to whether either detector correctly
rejects a whole clip**, despite the des-moines-estabs README's framing of
full-clip rejects as valuable ground truth Runnells cannot produce.
Flagged for the QA/Test Engineer role, not silently worked around here.

## Fit on Runnells → score on Des Moines (the original failing direction)

Runnells' single 235s clip, 3-block time-CV (existing `fit.py` machinery,
`stability_combine="resid_only"`), scored — **unchanged, never re-fitted**
— against the 12-clip Des Moines set above.

| strategy | Runnells held-out (in-sample reference) | → Des Moines score | vs. Des Moines baseline (P0.336/IoU0.291) |
| --- | --- | --- | --- |
| `absolute` (control) | P 0.625 / R 0.932 / IoU 0.430 | **P 0.330 / R 0.858 / F1 0.477 / IoU 0.273** | below on both P and IoU |
| `quantile` | P 0.637 / R 0.880 / IoU 0.429 | **P 0.329 / R 0.759 / F1 0.459 / IoU 0.261** | below on both |
| `robust_scale` | P 0.625 / R 0.932 / IoU 0.430 | **P 0.328 / R 0.832 / F1 0.471 / IoU 0.268** | below on both |

**None of the three strategies beat select-everything on the shoot they
were NOT fitted on.** Recall drops sharply from ~0.88-0.93 on Runnells to
~0.76-0.86 on Des Moines, and precision — already modest on Runnells at
~0.62-0.64 — collapses to ~0.33, essentially tied with (`quantile`,
`robust_scale`) or fractionally below (`absolute`) the 0.336 baseline.
**Per-clip normalization, in either form, does not fix the original
generalization failure in this direction.**

Every arm's residual-side parameter pinned to the max of its own search
grid on Runnells (`stability_resid_max=9.0`, `stability_resid_quantile=0.9`,
`stability_resid_z_max=6.0` — all flagged by the automatic grid-edge alarm),
the same "wants to loosen further" signature the 2026-09-02 investigation
found for the AND gate: under this harness's recall-first-subject-to-a-floor
ranking rule, any single-signal gate above the 0.60 precision floor wants
to loosen more, independent of whether it is absolute or normalized.

## Fit on Des Moines → score on Runnells

The mirror direction, via a new leave-clip-group-out multi-clip harness
(`MultiClipEvaluator` in the measurement script) that **reuses `fit.py`'s
own `coordinate_descent`/`block_bootstrap`/`check_grid_edges`/`rank_key`
functions directly** rather than reimplementing the procedure — the
12 Des Moines clips split into 3 groups of 4, leave-one-group-out CV,
same grids, same ranking rule, same bootstrap. Scored — unchanged — against
Runnells' full 235s clip.

| strategy | Des Moines held-out (leave-clip-group-out CV) | → Runnells score | vs. Runnells baseline (P0.577/IoU0.392) |
| --- | --- | --- | --- |
| `absolute` (control) | P 0.396 / R 0.839 / IoU 0.304 | **P 0.628 / R 0.912 / F1 0.744 / IoU 0.427** | **beats both** |
| `quantile` | P 0.407 / R 0.770 / IoU 0.301 | **P 0.642 / R 0.897 / F1 0.748 / IoU 0.433** | **beats both** |
| `robust_scale` | P 0.401 / R 0.831 / IoU 0.305 | **P 0.631 / R 0.947 / F1 0.757 / IoU 0.433** | **beats both** |

**All three strategies — including the "absolute" control — transfer
Des Moines → Runnells, beating select-everything on both precision and
IoU.** `quantile` and `robust_scale` again pinned to their own grid
maxima (0.9 and 6.0) even fit on this broader, 12-clip, 5-frame-rate set;
`absolute`, by contrast, landed at an interior value
(`stability_resid_max=4.0`) with no edge warning — the same parameter
that pinned to a wall on Runnells alone found room to settle once fit on
a more diverse set.

## The verdict, stated plainly

**Per-clip residual normalization (`quantile` or `robust_scale`) does NOT
achieve cross-shoot transfer on its own.** In the direction that matters —
Runnells → Des Moines, the direction the original failure was measured
in — neither normalized strategy beats select-everything, no better than
the absolute control. That falsifies the simple version of Ryan's
hypothesis ("an absolute px/frame threshold is the problem, so making it
relative fixes transfer"): making the threshold scale-free removed the
raw-magnitude mismatch but not the failure itself.

**What the data says instead, and this is the finding worth surfacing
rather than the one that was assumed going in:** the direction that
transfers is **Des Moines → Runnells, and it transfers for ALL THREE
strategies, including the unchanged absolute control.** The variable that
correlates with transfer in this measurement is not normalization
strategy — it is the **diversity of the fitting set**. Twelve clips
across five native frame rates and several camera bodies produces a
motion-residual gate that generalizes even down to a single narrow
clip; one clip, one camera, one operator does not generalize up to a
twelve-clip, multi-camera set, no matter how the residual is normalized
before thresholding.

This is consistent with, and sharpens, the design doc's own §3.2 caveat
("26 selects on one clip... cannot establish that the numbers transfer to
a different camera, operator, lighting, or subject") — the finding here
is that the fix is not a smarter per-clip statistic on the *scoring* side,
it is fitting-set diversity on the *fitting* side. **Per ROADMAP's own
framing of this fork, the honest options are per-shoot fitting (already
what "fit on the bigger, more diverse set" amounts to in practice) or
per-camera scoping — this is Ryan's decision, not one to make silently.**
Reported here, not resolved.

## Open questions flagged, not decided silently

1. **`quantile` and `robust_scale` pinned to their grid's max in EVERY
   fit run performed** (both directions), never landing at an interior
   optimum. Per the 2026-09-02 investigation's own standing finding, a
   parameter pinned to a wall after widening is not evidence the wall is
   in the wrong place — it can also mean the parameter structurally wants
   to be disabled under this ranking rule. Whether a much wider grid
   (`stability_resid_quantile` > 0.9, `stability_resid_z_max` > 6.0)
   would find an interior optimum, or would simply pin again, is not
   settled by this measurement.
2. **The `bm.score()` unscored-source convention** (see above) means this
   entire measurement is blind to full-clip-reject correctness — a real
   gap between what Des Moines Estabs is supposed to test and what it
   currently can. Out of this session's file-ownership scope to fix.
3. **12 clips is still a small, cached-availability-driven sample**, not
   a deliberately chosen representative one — extraction is the expensive
   step (~3x realtime) and no new clips were pulled this session. The
   direction-2 (Des Moines → Runnells) multi-clip fit used a fresh,
   non-canonical leave-clip-group-out harness (this measurement script,
   not a permanent `fit.py` entry point) — reusing `fit.py`'s functions
   directly, but the grouping-into-3-folds-by-round-robin-index is an ad
   hoc choice for this measurement, not a designed CV scheme the way
   Runnells' contiguous time blocks are.

## Reproducing

```
PRECUT_ROOT=~/precut-checkout PYTHONPATH=/Users/pierce/pierces-post-house \
  ~/precut-venv-fresh/bin/python <path-to-transfer_measure.py>
```

Full per-arm params, held-out metrics, bootstrap intervals, and edge
warnings for both directions are in `transfer_results.json` in this
directory. `runnells_fit/params.json` and `runnells_fit/fit_report.json`
are the complete, unmodified output of `posthouse.cull.fit.fit()` for
direction 1 (every legacy/combine arm it always fits, not just the three
resid_only norm arms this document focuses on).
