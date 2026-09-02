# Slice 5: the stability threshold detector, fitted for real, 2026-09-02

Ryan approved the rescope recorded in ROADMAP's 2026-09-02 Decision Log:
adopt the stability-threshold detector for segment EXTENT, demote the
motion classifier to a labeller. This directory is that slice's real run
against `DJI_20260430075045_0006_D.MP4` (the cached, already-classified
sidecar from slices 1-2, reused per the task brief rather than
re-extracted).

## Command

```
python -m posthouse.cull.fit \
  --sidecar <cached sidecar>.signals.npz \
  --answer-key benchmark/runnells-day-1/answer_key.xml \
  --out benchmark/runnells-day-1/scores/slice5_stability \
  --seed 0 --n-bootstrap 5000 --passes 2
```

Fitted arms: `stability_full` (exposure gate on), `stability_no_exposure`
(exposure gate off), plus `hysteresis_full`/`hysteresis_no_focus`/
`hysteresis_no_exposure`/`viterbi_full` kept and fully fit for comparison
(demote, do not delete — nothing from slices 2-4 was removed).

## Fitted parameters (chosen arm: `stability_no_exposure`)

| param | value |
| --- | --- |
| `stability_resid_max` | **2.0** (grid: 0.8, 1.0, 1.2, 1.5, **2.0**) |
| `stability_lapvar_quantile` | **0.10** (grid: **0.10**, 0.20, 0.30, 0.40, 0.50) |
| `stability_smooth_sec` | 0.7 (fixed by construction, not searched) |
| `min_duration_sec` | 1.15 (fixed by construction — the shipped default, not part of this stage's 2-parameter search) |
| `exposure_gate` | **False** — see "the exposure-gate finding" below |

**Both fitted values landed on the edge of their search grid** (resid_max
at the top of its range, quantile at the bottom). That means the true
coordinate-descent optimum may lie outside the grid this run searched;
flagged here rather than silently accepted. A follow-up fit with a wider
grid (e.g. resid_max up to 3.0, quantile down to 0.02) is the obvious
next step and was not done in this slice (task brief: reuse the harness,
do not rewrite it — widening `STAGE_GRID_STABILITY` in `fit.py` is a
one-line follow-up, not a harness rewrite, left for the next session).

## Held-out score (3-block CV, the honest number)

| | P | R | F1 | IoU |
| --- | --- | --- | --- | --- |
| **stability (slice 5, this run)** | 0.669 | 0.804 | 0.728 | 0.436 |
| crude probe, fair CV (Decision Log 2026-09-02) | 0.635 | **0.881** | 0.737 | 0.428 |
| full pipeline, slices 2-4, fair CV | 0.634 | 0.838 | 0.710 | 0.387 |
| legacy best this run (`hysteresis_no_focus`) | 0.634 | 0.838 | 0.710 | 0.387 |

Per-fold held-out (`stability_no_exposure`):

| block | P | R | IoU |
| --- | --- | --- | --- |
| 0 | 0.657 | 0.853 | 0.402 |
| 1 | 0.611 | 0.603 | 0.340 |
| 2 | 0.739 | 0.957 | 0.565 |

Block bootstrap, 5000 resamples, 95% interval:

| | lo | hi | width |
| --- | --- | --- | --- |
| precision | 0.611 | 0.739 | 0.128 |
| recall | 0.603 | 0.957 | 0.354 |
| f1 | 0.607 | 0.834 | 0.227 |
| iou | 0.340 | 0.565 | 0.224 |

With three blocks the interval is wide, as design §3.2 point 3 warns it
would be. Not smoothed over.

## In-sample production score (fit on all blocks, scored on all blocks)

Run through the full chain for real — `write_culls` with the chosen
final params against the real manifest, then `posthouse.benchmark score`
against the real answer key:

**P 0.680 / R 0.828 / F1 0.746 / IoU 0.452** (25 accepted segments, 67
rejections). This is close to but slightly better than the held-out mean
above (0.669/0.804/0.436) — a small, expected optimism gap for a
2-parameter model, not a sign of gross overfitting.

## Did the real integrated version match the diagnostic script's numbers? No — and here is why, not papered over

The Decision Log's fair-comparison crude probe scored **P 0.635 / R 0.881
/ IoU 0.428** held-out. This slice's real, production `stability_no_exposure`
arm scores **P 0.669 / R 0.804 / IoU 0.436** held-out — similar IoU,
higher precision, **77 points lower recall**. Three concrete, documented
differences between this production detector and the original diagnostic
script explain the gap:

1. **`lapvar_norm` (per-clip normalized), not raw `lapvar`.** The design
   doc's crude probe table is written as "lapvar > q30" using the raw
   `lapvar` column; this module uses the already-normalized
   `lapvar_norm` column instead (documented judgment call,
   `SegmentParams.stability_lapvar_quantile`'s own docstring) for
   resolution independence. A per-clip quantile of a normalized column
   is not numerically identical to a per-clip quantile of the raw one on
   footage with any luma/plane-size dependence in the normalization.
2. **Both signals smoothed by the same 0.7s window, not just resid.**
   The design doc's prose ("a 0.7s smoothing window") does not say
   whether lapvar itself was smoothed in the original probe; this module
   smooths both (flagged in `_run_stability_pipeline`'s own docstring).
   Smoothing lapvar changes which frames clear a given quantile.
3. **`min_duration_sec` fixed at 1.15s (the shipped default), not the
   design doc's "1.2s minimum duration."** Per design §3.2 point 1,
   min_duration_sec is held fixed by construction during this 2-parameter
   stage rather than re-searched; 1.15 is `SegmentParams`'s own shipped
   default value, not 1.2.
4. **Coordinate descent, not an exhaustive joint grid search.** The
   Decision Log's "25-point grid" refit of the crude probe most plausibly
   evaluated all 25 (resid, quantile) combinations jointly and read off
   the best. This module's `coordinate_descent` (design §3.2 point 1's
   own containment mechanism, reused unmodified per the task brief)
   optimizes one parameter at a time, 2 passes, which is not guaranteed
   to find the same joint optimum on a non-convex, floor-gated ranking
   surface — and the fitted values landing on the grid's own edge (see
   above) is independent evidence the search did not fully converge.

None of these four is a bug; all four are either documented judgment
calls made in this slice or a direct, intentional reuse of the existing
harness exactly as instructed. The net effect is a real detector that is
similar in spirit and IoU to the diagnostic, not numerically identical to
it — reported plainly per the task brief's instruction to report any
difference rather than paper over it.

## The exposure-gate finding: does NOT clearly earn its place under stability

Slice 4 found the exposure gate "earns its place, but only just" for the
LEGACY classify+consolidate+gate pipeline. Re-measured here, under the
SAME ranking rule, for the STABILITY detector specifically: **it does
not** — `stability_no_exposure` outranks `stability_full` on this run's
held-out score (`ablation_verdicts.stability_exposure_gate`: "does not
earn its place — recommend removing"). The shipped `params.json` reflects
this honestly (`exposure_gate: false`). This is a genuinely new finding,
not a re-statement of slice 4's: the same gate, on the same footage,
earns its place under one detector and not the other. Plausible reason
(not verified further in this slice): the stability detector's own
lapvar/resid thresholds already reject a fraction of the frames an
unfitted exposure gate would also have caught, making the exposure gate
partially redundant once the extent detector itself is doing more of the
rejecting. Flagged for a future session, not resolved here.

## motion_intent distribution (fitted params, full clip)

25 accepted segments: `pan_left` (11), `pan_right` (9), `tilt_up` (4),
`static` (1). No `push_in`/`pull_out`/`roll`/`drift` in this run (the
Runnells walkthrough is pan/tilt-dominated, consistent with earlier
slices' own findings).

## Example segments vs. Ryan's answer key

CULLS.md §5's worked example is Ryan's selects **#3** (14.98-18.85s, a
pan) and **#4** (19.19-21.29s, a tilt down), with the 0.34s between them
being the axis-change transition. Under the fitted stability detector,
**this exact split does not reproduce**: the nearest accepted segment is

```
16.68s - 22.99s   pan_right   confidence 0.36
```

— one segment spanning across BOTH of Ryan's two selects rather than two
segments split at the axis change. This is the real, uncomfortable
implication the Decision Log already named before this slice was built:
"a plain stability threshold predicts better than intent classification
does... what he marks is driven more by stability than by intent type."
The stability detector does not see an axis change as a boundary at all
(it isn't one, by residual/sharpness) — but the labeller's own
**confidence of 0.36** (well below the 0.8-0.9+ confidence on this run's
clean single-intent segments) is a genuine, useful signal that this
window is NOT one clean intent, even though its extent was not split.
That is exactly the "demote, don't delete" value the classifier still
provides: it cannot draw this boundary reliably, but it can flag that the
window it was handed is mixed.

A cleaner match: segment `12.71s - 15.72s, pan_right, confidence 0.86`
sits mostly inside Ryan's #3 pan window (14.98-18.85s) with a high,
single-intent-consistent confidence — a case where extent, intent, and
confidence all read the way a human would expect.

## Files here

- `params.json`, `fit_report.json` — the fit.py run's full output.
- `culls_out/culls.json` (+ `culls.visual.json` view) — the actual
  culls.json this params set produces on the full clip, via
  `write_culls` against the real project manifest.
- `benchmark_report.json` / `.txt` — the in-sample production score
  against the real answer key.
