# The fair comparison, 2026-09-02

Slice 4's report compared the fitted pipeline's HELD-OUT score against the
crude probe's IN-SAMPLE score. That is not a fair test: held-out numbers
are systematically pessimistic against in-sample ones, so it stacked the
deck against the pipeline.

The Lead re-fitted the crude probe under the identical scheme the
pipeline got: the same three contiguous time blocks, fit on two, scored
on the held-out third, the same recall-first rule with the same 0.60
precision floor, over a 25-point grid on its two parameters.

| detector | P | R | F1 | IoU |
| --- | --- | --- | --- | --- |
| crude probe, 2 thresholds | 0.635 | **0.881** | **0.737** | **0.428** |
| full pipeline, slices 2 to 4 | 0.634 | 0.838 | 0.710 | 0.387 |

Per fold, crude probe: P 0.583 R 0.922 IoU 0.355 / P 0.581 R 0.751
IoU 0.364 / P 0.740 R 0.972 IoU 0.566.

Two thresholds on two signals tie the pipeline on precision and beat it
on recall, F1, and IoU. Both use signals from slice 1's extractor, so
slice 1 is the foundation of each and is not in question. What is in
question is everything built on top of it for boundary placement.
