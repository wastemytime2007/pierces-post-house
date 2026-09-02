# Slice 5 follow-up: the gate-combination investigation, 2026-09-02

Dispatched from the Lead's investigation (ROADMAP Decision Log,
2026-09-02): the shipped `slice5_stability` fit required BOTH motion
residual < R AND sharpness > Q (an AND gate). `stability_resid_max`
fitted to 2.0, the exact max of its 5-point grid; widening the grid 3x
through the real `fit()` entry point pushed it to 6.0 — the new max —
again. Isolating each signal in-sample showed why: motion residual alone
scores IoU 0.455, sharpness alone 0.420, both ABOVE the shipped AND
gate's 0.442–0.446. Two strong predictors, combined adversarially.

This directory is the re-fit against the same cached, already-classified
Runnells sidecar, with four changes to `posthouse/cull/fit.py` and
`posthouse/cull/segment.py`:

1. **Automatic edge-value alarm** (`fit.py`'s `check_grid_edges`) — every
   grid search's result is checked against its own grid's min/max, and a
   hit is written as a structured entry in `fit_report.json`'s top-level
   `"warnings"` list, not left for a human to notice.
2. **The stability grids widened properly**, informed by the real
   `resid`/`lapvar_norm` distributions measured on the cached Runnells
   sidecar (smoothed resid: p50 1.14, p75 2.09, p90 3.34, p95 4.88,
   p99 10.90, max 21.34 px/frame) — `stability_resid_max`'s grid became
   `[0.8, 1.2, 1.8, 2.7, 4.0, 6.0, 9.0]` (see
   `fit.py`'s `STABILITY_RESID_MAX_GRID` comment for the full reasoning,
   including the cross-camera check against two Des Moines Estabs clips
   that motivated the score-based combine mode below).
3. **`SegmentParams.stability_combine`**, five modes, all fit and ablated
   as first-class arms: `"and"` (the original gate), `"or"`,
   `"resid_only"`, `"lapvar_only"`, and `"score"` — a single fitted
   threshold over a weighted, per-clip percentile-rank combination of the
   two signals (`_stability_score` in `segment.py`), scale-free by
   construction so it cannot pin to a wall the way an absolute threshold
   can.
4. **resid-only and lapvar-only are permanent ablation arms** in
   `fit.py`, not just numbers in a diagnostic script.

## Command

```
python -m posthouse.cull.fit \
  --sidecar <cached sidecar>.signals.npz \
  --answer-key benchmark/runnells-day-1/answer_key.xml \
  --out benchmark/runnells-day-1/scores/slice5_followup \
  --precision-floor 0.60 --n-blocks 3 --passes 2 --seed 0 --n-bootstrap 5000
```

## Result: the ablation table (mean held-out, 3-block CV)

| combine | P | R | F1 | IoU | edge warning |
| --- | --- | --- | --- | --- | --- |
| **resid_only (winner)** | 0.627 | **0.911** | 0.737 | 0.417 | `stability_resid_max` = 9.0, grid MAX |
| and (original AND gate) | 0.622 | 0.879 | 0.723 | 0.414 | `stability_lapvar_quantile` = 0.05, grid MIN |
| score | 0.620 | 0.876 | 0.722 | 0.404 | `stability_score_threshold` = 0.10, grid MIN (widened once, still pinned) |
| or | 0.622 | 0.864 | 0.719 | 0.407 | `stability_resid_max` = 9.0, grid MAX |
| lapvar_only | 0.619 | 0.862 | 0.713 | 0.398 | `stability_lapvar_quantile` = 0.05, grid MIN |

**Every arm's motion-stage parameter pinned to a grid edge, not just the
original AND gate's.** This is itself a finding, stated plainly rather
than chased with a fourth grid-widening: under this harness's own
"recall-first subject to a 0.60 precision floor" ranking rule, ANY
single-signal or combined-signal gate that stays above the precision
floor wants to loosen further, because loosening only ever trades a
little precision for more recall until the floor is nearly hit. The
combination-structure fix (point 3 above) is real and measured — `score`
is the only mode whose ceiling is intrinsic (percentile ranks are
[0, 1] by construction, so it cannot "run off" the way an absolute
threshold can) — but it still lands at *its own* floor because the
ranking rule itself rewards maximum recall at the floor, independent of
combine structure. **Flagged as an open question for the ranking rule /
precision floor, not silently resolved here** — see the session report.

`resid_only` wins the harness's own ranking rule (highest recall among
arms clearing the 0.60 floor) and is what shipped in `params.json`. It
is *not* obviously better than `score` — the two are within ~1 point of
each other on every metric — and `resid_only`'s own edge-pin (wanting an
even looser residual cap) is the same symptom the whole investigation
started from, just on a single-signal arm instead of the AND gate.

## Held-out vs. the fair-comparison crude probe

| detector | P | R | F1 | IoU |
| --- | --- | --- | --- | --- |
| crude probe (fair CV, slice 4 Decision Log) | 0.635 | 0.881 | 0.737 | **0.428** |
| **resid_only (this fit, held-out)** | 0.627 | **0.911** | 0.737 | 0.417 |

Ties on F1, wins on recall, loses narrowly on precision and IoU. **Does
not clearly beat the crude probe** — `beats_crude_probe_overall: false`
in `fit_report.json` (requires P, R, AND IoU all ≥ the probe's). This is
recorded plainly: the gate-combination fix closed most, not all, of the
gap the original AND-gate investigation found, and the honest bootstrap
interval is wide (precision 95% CI [0.561, 0.719], IoU [0.348, 0.491] —
3 blocks is not a lot of data).

## Shipped params (`params.json`, `stability_resid_only_full`)

`consolidation=stability`, `stability_combine=resid_only`,
`stability_resid_max=9.0`, `stability_lapvar_quantile=0.3` (unused under
this combine mode, carried for provenance), `exposure_gate=True`,
`clip_low_frac_max=0.4` (also pinned to its own grid's max — a
pre-existing exposure-gate grid, out of this investigation's scope but
flagged for whoever owns it next), `clip_high_frac_max=0.06`.

## Des Moines Estabs generalization score

See `benchmark/des-moines-estabs/scores/README.md`. Short version: **the
Runnells-fitted detector does not generalize** — on a reliable 5-clip
Des Moines subset it scores *below* select-everything on precision and
IoU, both for `resid_only` (the shipped arm) and for `score` (the
architecturally motivated alternative). A frame-rate parsing defect in
`posthouse.benchmark.parse_answer_key_xml` (found during this work,
documented in that README) corrupts ground truth for any Des Moines
source clip whose native frame rate differs from the project's 23.976fps
edit timeline — which is most of the dataset — so this generalization
score is necessarily narrower (5 clips, one camera/frame-rate) than
originally planned, and the fix belongs to the QA/Test Engineer role,
not this session.
