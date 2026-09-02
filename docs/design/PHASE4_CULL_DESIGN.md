# Phase 4 — the Assistant Editor's technical cull: detection design

Owner: Lead Architect (`docs/TEAM.md`). Status: **design, pre-build.**
Produces `docs/contracts/CULLS.md`'s artifact. No code exists yet; §5 is
the build order.

Everything numeric in this document is either measured on Ryan's Mac
against the benchmark clip (`DJI_20260430075045_0006_D.MP4`, 3840×2160
HEVC Main 10 yuv420p10le, 30000/1001 fps, 235.30s, 7052 frames,
67.3 Mbit/s), read out of his 26-select answer key, or explicitly
labelled as an estimate with its basis. Nothing here is a guess dressed
as a number.

## 0. What is actually being built, and the two things it must get right

ROADMAP §4 gives the cull two criteria, in Ryan's words, stated while he
marked the answer key:

1. **One motion intent per select, start to finish.** Static hold, or one
   pan, or one tilt. Boundaries are *motion-type change points*, not
   merely shake onsets. This retires "one settled segment per camera
   hold" and it is the novel part of the work.
2. **A clear focus point**, where a smooth intentional rack focus is a
   valid select and focus hunting is not. So sharpness is judged by its
   *shape over time*, never by a single threshold.

His key already demonstrates criterion 1 without being told to. Selects
#3 and #4 are 14.98–18.85 and 19.19–21.29, separated by 0.34s. Measured:

| interval | mean dx (px/frame @480) | mean dy | what it is |
| --- | --- | --- | --- |
| 14.98–18.85 | **−5.84** | +0.07 | a pan, held on one axis for 3.87s |
| 18.85–19.19 | mixed | mixed | the operator changing axis — in *neither* select |
| 19.19–21.29 | −0.09 | **−2.78** | a tilt down (confirmed by eye on frames at 19.3s and 21.2s) |

That is the spec, on his own data: two selects, one dropped transition,
a boundary placed where the axis changed and nowhere else.

## 1. Signal layer

### 1.1 The analysis decode

**Decision: one ffmpeg pass per file, VideoToolbox-accelerated HEVC
decode of the ORIGINAL, scaled to a 960×540 8-bit gray plane, emitted as
rawvideo on a pipe into numpy. No intermediate file is ever written.**

Measured on Ryan's Mac (ffmpeg 8.1, homebrew, Apple silicon), all against
the real 235.30s benchmark clip or a 30s prefix of it:

| decode | wall | ×realtime |
| --- | --- | --- |
| software, full res, `-f null` (30s prefix) | 23.3s | 1.29× |
| `-hwaccel videotoolbox`, full res, `-f null` (30s prefix) | 6.4s | 4.7× |
| `-hwaccel videotoolbox` + `scale=480:270,format=gray` → pipe (full clip) | 40.6s | 5.8× |
| `-hwaccel videotoolbox` + `scale=960:540,format=gray` → pipe (full clip) | 38.3s | 6.1× |
| audio only, `-ac 1 -ar 48000 -f f32le` → pipe (full clip) | 0.30s | 780× |

Software decode of 10-bit 4K HEVC is the bottleneck and VideoToolbox
removes it: 3.6× faster on the same frames. The scaler is the remaining
cost and it parallelizes, which is why 960×540 costs no more wall time
than 480×270.

**Why this is not a proxy shortcut.** ROADMAP §4 forbids measuring on
PreCut's CRF-28 proxies because "adaptive compression turns the blur
score into a bitrate meter, 8-bit re-encodes manufacture or hide exposure
clipping, and AAC does not preserve audio sample peaks." None of those
apply here: this is a *decode of the original bitstream*, downscaled by a
deterministic Lanczos filter in memory. There is no re-encode, no
rate-control, no generation loss. What downscaling does cost is
high-frequency detail — which matters only for sharpness, handled in
§1.4 — and it costs nothing at all for global motion, exposure, or
audio. `params.analysis.source_grade` records `"analysis_decode"` and the
contract rejects anything else (CULLS §7 REJECT 8).

**The gate that proves it**, required by ROADMAP §4: a fixture test
asserting proxy-vs-source metric agreement. Build it early (slice 1) and
expect it to *fail* for sharpness and audio peaks — that failure is the
evidence for the rule, and it is what stops a future session from
"optimizing" the cull onto proxies.

**Rejected alternatives for the decode:**

- *PyAV / OpenCV VideoCapture.* Neither is installed in
  `~/precut-venv-fresh` (verified: `import av` and `import cv2` both
  `ModuleNotFoundError`). Adding either to the venv PreCut's production
  pipeline runs on risks the torch/numpy ABI for no capability gain — a
  subprocess pipe gives the same frames.
- *Sampling instead of decoding every frame.* Ryan's shortest select is
  1.23s (37 frames) and the transition between #3 and #4 is 0.34s
  (10 frames). A sampling pass cannot see a 10-frame axis change. This is
  the same reason `motion_analyzer.py` is a non-goal (§1.6).

### 1.2 Global motion — the choice

**Decision: block-wise phase correlation on the FFT, numpy only.**

Per frame pair: a 3×3 grid of 256×256 Hann-windowed blocks over the
analysis plane, each block phase-correlated against the same block in the
previous frame, then a least-squares fit of a 4-DOF similarity model
(`tx`, `ty`, `log scale`, `roll`) across the nine block shifts, keeping
the fit **residual** as its own signal. Blocks whose correlation peak is
below a fitted confidence floor are dropped from the fit (a block of
featureless wall votes on nothing).

Measured: a reduced version of exactly this (one 256×256 centre block for
translation plus two half-blocks for divergence, plus all of §1.4–1.5)
ran the **entire 235.30s clip in 33.6s wall including the decode** —
7.0× realtime, pure numpy, no new dependencies. The 3×3 version is ~4×
the FFT work of the probe; §1.7 carries the projection.

Why this and not the alternatives named in the roadmap:

- **ffmpeg `vidstabdetect` — rejected, and it is not merely a preference:
  it does not exist on this machine.** `ffmpeg -filters | grep
  vidstabdetect` returns nothing; the installed build (ffmpeg 8.1,
  homebrew) is configured without `--enable-libvidstab`. Getting it would
  mean rebuilding the ffmpeg that PreCut's production pipeline shells out
  to, on Ryan's working machine, to gain a filter that (a) needs a
  *second* full decode pass because its transform log is its only output,
  (b) models translation and rotation but not zoom, so a push in is
  invisible to it, and (c) is tuned to feed a stabilizer, not to classify
  intent. Two of those three are fatal for criterion 1 on their own.
- **Dense optical flow (Farneback) — rejected as the primary.** OpenCV is
  not in the venv (verified above). Dense flow is roughly an order of
  magnitude more work per frame than nine FFT correlations, and it
  answers a question we are not asking: we want the *camera's* global
  motion, not a per-pixel field that a person walking through frame
  pollutes. Phase correlation is naturally robust to that — a moving
  subject shifts one or two blocks and gets outlier-rejected by the fit.
  Keep flow in the back pocket as an escalation for windows where the
  similarity fit is ambiguous (§6 Q7); do not build it now.
- **Phase correlation — chosen.** Global by construction, illumination
  invariant (it uses only the cross-power *phase*), cheap, dependency
  free, and the block grid buys scale and rotation, which is what
  separates a push from a pan.

Sub-pixel refinement is required, not optional: at 480 width the probe's
integer peak quantized "static" and "slow drift" into the same bucket
(measured static-select `dx` standard deviations of 0.30–0.48 px/frame
are at the quantization floor). Fit a parabola to the three correlation
samples around the peak on each axis — three multiplies, no cost.

### 1.3 Motion classification — the novel part

Per frame, from the smoothed similarity fit, six features:

| feature | from | separates |
| --- | --- | --- |
| `v` = (vx, vy) | `tx`, `ty`, smoothed over a fitted window | static vs moving; pan vs tilt by which axis dominates |
| `axis_ratio` = \|vx\| / (\|vx\|+\|vy\|) | above | pan (→1) vs tilt (→0) vs diagonal (≈0.5) |
| `div` = d(log scale)/dt | the fit's scale term | push in / pull out — invisible to translation-only methods |
| `roll_rate` | the fit's rotation term | a horizon roll (usually a defect on this camera) |
| `resid` | the similarity fit's least-squares residual | shake and parallax: a rigid camera move fits the model, a jolt does not |
| `hf_energy` | band power of `v` above ~3 Hz over a short window | shake vs a deliberate move at the same speed |

Then a **deterministic decision over those six**, not a learned
classifier — ground rule 3, and there is no training data anyway (26
selects is not a training set). Classes: `static`, `pan_left`,
`pan_right`, `tilt_up`, `tilt_down`, `push_in`, `pull_out`, `roll`,
`drift`, `shake`, `undecidable`. Each class gets a cost function of the
six features with fitted scalars — cheap, inspectable, and it feeds the
segmenter in §2 as a per-frame cost vector rather than a hard label,
which is what lets the segmenter smooth properly.

Two calibrations must be nailed down by test, not by assumption:

- **Sign conventions.** `dx < 0` and `dy < 0` in the probe's formulation
  correspond to the pan and the tilt-down that were confirmed by eye at
  15.1/18.7s and 19.3/21.2s. Slice 2's test pins the mapping to those two
  hand-verified intervals plus synthetic clips (§5), so a sign flip can
  never ship silently as "every pan is labelled the wrong way."
- **Units.** Every motion parameter is expressed in **px/frame
  normalized to 3840 width**, so changing the analysis plane size does
  not silently change every fitted threshold.

Grounding that the feature set separates Ryan's selects from the rest,
measured over all 7052 frames (39.6% of frames are inside a select):

| | inside selects | outside |
| --- | --- | --- |
| smoothed \|v\| (px/frame @480) | median 1.15, p90 3.76 | median 2.44, p90 7.47 |
| smoothed jerk | median 0.20, p90 0.67 | median 0.40, p90 1.47 |
| correlation peak (fit confidence) | median 0.524 | median 0.400 |
| \|divergence\| smoothed | median 0.40, p90 2.27 | median 0.93, p90 3.13 |
| clipped-low fraction | median 0.076, p90 0.244 | median 0.084, **p90 0.646** |

Every one points the right way, and the p90s show the discrimination is
in the tail, not the median — which is exactly the shape you want for a
gate.

### 1.4 Sharpness, and why an absolute threshold is impossible here

Laplacian variance on the analysis plane (4-neighbour kernel, whole
frame). Overall it separates well: **median 1547 inside selects versus
589 outside**, a 2.6× gap. But per select it ranges from **100 to 4890**,
and both extremes are shots Ryan kept:

- 105.17–108.17, median lapvar **100**, mean luma 70.5 — a frame grab at
  106.5s shows Mitch in a black hoodie in a dim interior, in focus,
  talking. Low variance because the scene is dark and low-contrast.
- 175.58–178.51, median lapvar **3972**, and 218.55–226.46, median
  **4890** — frame grab at 176.5s: wet bicycles and brick in flat
  daylight, locked off, extremely detailed.

A single global threshold that keeps the bicycles and the hoodie keeps
everything. So:

1. **Normalize per clip**, against a robust reference (a high quantile of
   the clip's own lapvar distribution). The fitted parameter is a
   quantile, not a variance.
2. **Condition on motion.** Motion blur depresses lapvar during a fast
   pan — the 14.98 select pans at 5.84 px/frame @480 (≈47 @4K) and still
   measures 1944, but a slower shot in the same room measures more.
   Regress log-lapvar on smoothed \|v\| within the clip and gate on the
   residual, so a pan is not scored as soft for being a pan.
   `focus.motion_adjusted` in the contract records whether this applied.
3. **Judge the shape, per criterion 2.** On smoothed log-lapvar:
   *steady* = low variance about its own mean; *rack* = a monotonic ramp
   between two stable plateaux, sign-consistent for at least a fitted
   number of frames, with stable levels at both ends; *hunting* =
   sign changes above a fitted rate per second. Three shapes, two fitted
   scalars each. `rack_in`/`rack_out` are accepted and labelled;
   `hunt` is a rejection reason.

A known limitation to write down now rather than discover later: whole-
frame lapvar cannot tell "the subject is in focus" from "the background
is in focus and the subject is not." A saliency-weighted version is the
obvious next step and it is deliberately **not** in scope (§5, and
ground rule 3 — there is nothing to measure it against yet).

### 1.5 Exposure

From the same gray plane, per frame: mean, standard deviation,
clipped-low fraction (< 16/255, the legal-range floor for this `tv`-range
bt709 source), clipped-high fraction (> 239/255), and a 64-bin histogram
stored decimated (§4). Gates are fitted, and the fixtures
(`underexposed.mp4`, `overexposed.mp4`) give them a non-benchmark
anchor. Measured relevance: clipped-low p90 is 0.646 outside selects
against 0.244 inside — crushed frames really do sit outside Ryan's key.

Note the gray plane is luma only, so a colour cast or a white-balance
error is invisible. That is correct scope: it is the Colorist's job
(Phase 8), not the cull's.

### 1.6 Audio (narrative ruleset)

One extra ffmpeg pass per file, `-vn -ac 1 -ar 48000 -f f32le` on the
**original** track: measured at **0.30s for the whole clip**, i.e. free.
Per 20ms window: sample peak dBFS, RMS dBFS, and a consecutive-sample
clipping count (which is why the original AAC is decoded rather than a
proxy's re-encode — ROADMAP §4). Then:

- **Dead audio**: RMS below a fitted floor for longer than a fitted
  duration.
- **Clipped audio**: clipped-sample runs above a fitted rate.
- **Speech presence**: **reuse `posthouse.harvest.transcribe`**, not a
  new VAD. It is already harvested, it already carries PreCut's phrase
  chunking verbatim (break on sentence punctuation, break on pauses
  ≥ 0.6s, cap 25 words, merge runts), and ground rule 1 says do not
  rebuild what the donor solved. `speech_frac` is the fraction of the
  candidate range covered by transcript word spans.
- Where a lav is synced to the A-roll, the *lav* is the track that gets
  measured; `audio.source` in the contract names which. Below-threshold
  sync pairs are an open Phase 4 item (`posthouse/harvest/sync.py`'s
  docstring says so explicitly) and the cull's rule is simply: an
  unreliable pair means the camera track is measured and the segment is
  flagged, never that the segment is silently scored against the wrong
  audio.

### 1.7 Why `motion_analyzer.py` is a non-goal (read, confirmed)

`~/precut-checkout/python_backend/precut_pipeline/motion_analyzer.py`,
253 lines: `ANALYSIS_FRAMES = 6`, `ANALYSIS_WIDTH = 160`, comparing
consecutive sampled pairs by mean absolute pixel difference and
brightness-*centroid* drift, emitting one to three whole-clip tags.

Three independent disqualifications, not one:

1. **Six frames per clip.** On this 235s clip that is one sample every
   39 seconds. The 0.34s transition between selects #3 and #4 is
   invisible; so is every one of the 26 boundaries.
2. **Whole-clip tags.** Its output has no time axis at all. The cull's
   entire job is locating boundaries in time.
3. **Brightness-centroid drift is not motion estimation.** A person
   walking across a static frame moves the brightness centroid; so does a
   cloud. It cannot distinguish a pan from shake, which is criterion 1's
   core requirement.

There is nothing to extend. The roadmap's "explicit non-goal" is
confirmed by reading the file, and this section exists so nobody
re-litigates it.

### 1.8 An opportunity, deliberately not taken in v1

The Osmo files carry a `djmd` data stream ("DJI meta", stream index 2,
11.5 kbit/s) alongside a `dbgi` debug stream. That is very likely IMU /
gimbal telemetry, which would give camera motion directly rather than
inferring it from pixels. It is undocumented, format-unknown, and
camera-specific — a cull that depends on it works on Osmo footage and
nothing else. **Not in v1.** Noted here because it is a cheap
cross-check to try once the pixel path is measured, and because a future
session should not rediscover it from scratch.

### 1.9 Measured addendum after slice 1 (Lead, 2026-09-01)

Slice 1 measured two things this section predicted differently. Both
are recorded here because builders read this document, not the log.

- **Runtime.** The full extractor runs at **1.34x realtime** on the real
  clip (175s for 235s of 4K HEVC with VideoToolbox), not the 4-5x
  projected in §7. Cause: a per-frame Python loop doing nine full 256px
  FFT correlations, not vectorized across frames. The 37-minute day is
  ~28 minutes, still far inside "overnight batch acceptable." Do not
  optimize until a later slice shows the time matters.
- **Proxy-vs-source.** On the synthetic fixtures the sharpness
  disagreement did not appear (testsrc2 has no fine detail for CRF-28
  to destroy). On the REAL clip against PreCut's own proxy the picture
  is partly inverted from §1.4's expectation: sharpness **absolute
  level** is destroyed (proxy median 30% lower) but its **per-frame
  shape survives** (r = 0.983), whereas the **motion residual, the
  stability signal, correlates only r = 0.544** (tx 0.92, ty 0.74). The
  no-proxies rule therefore stands for a corrected reason: the residual
  does not survive compression, and neither does absolute sharpness;
  only sharpness *shape* would. Any future "proxy shortcut" argument
  must clear the residual, not the sharpness, bar.

## 2. Segmentation

### 2.1 From per-frame cost vectors to labelled runs

**Decision: Viterbi decoding over the motion-class states, with a single
fitted transition penalty λ.** Emission cost per frame per class from
§1.3; transition cost λ for any class change, 0 for staying. The
minimum-cost path is the label sequence, and boundaries are where the
label changes.

Why this and not the roadmap's other two candidates:

- **`ruptures` (PELT / BinSeg) — rejected.** Not installed (verified:
  `ModuleNotFoundError`), so it is a new dependency in the venv PreCut
  runs on. More importantly it solves a different problem: it finds
  change points in a *continuous* signal, and we need a *labelled* motion
  intent per select — the label sequence gives the boundaries for free,
  while PELT would give boundaries that still need labelling. Viterbi
  with a single penalty λ over a categorical cost is mathematically the
  same family (penalized change-point labelling); it is ~30 lines of
  numpy and it produces both outputs at once.
- **A full HMM with learned transition probabilities — rejected.** 26
  selects cannot fit a 11×11 transition matrix. The single-λ form is the
  HMM with all its free parameters tied, which is the correct amount of
  model for the amount of data.
- **A hysteresis state machine — the slice-1 stand-in.** Simpler to
  review, easier to explain when it misfires, and it gives the fitting
  harness something to score on day one. Ship it first, upgrade to
  Viterbi in slice 3, and keep it as the A in an A/B on the benchmark. If
  it wins, keep it — that is the whole point of measuring.

### 2.2 From labelled runs to segments

For each maximal run of a single motion class:

1. **Settle-time trim.** Drop `settle_frames` from each end of the run
   (fitted, per class — a pan needs longer to reach constant velocity
   than a hold needs to stop wobbling). These frames become `settle`
   rejections; a run that is all settle becomes a `transition` rejection
   — which is exactly what the 0.34s between Ryan's #3 and #4 is.
2. **Minimum duration.** Runs shorter than `min_duration_sec` after the
   trim become `too_short` rejections. Ryan's shortest select is 1.23s;
   the fit range is therefore [1.0, 1.5] with a hard floor at 1.0.
3. **Class gate.** `shake` and `undecidable` never open a select.
   `static`, the four pan/tilt directions, `push_in`, `pull_out`, `roll`,
   and `drift` do.
4. **Quality gates**, which can *shorten* or *split* a run, never extend
   it: focus (§1.4), exposure (§1.5), and — narrative only — audio
   (§1.6). A focus loss mid-run closes the segment at the loss and
   reopens after recovery if what remains clears `min_duration_sec`; the
   gap is a `focus_lost` rejection. A `hunt` shape kills the whole run.
5. **Handles.** `handle_sec: 1.0` by default, matching both
   `coldfootage.DEFAULT_HANDLE_SEC` and the scorer's
   `DEFAULT_HANDLE_TOLERANCE_SEC` so that handles are neutral in
   scoring. The ratified rulings hold: validation applies to the
   pre-handle range, handles clamp to source bounds and never reject.
   `handle_in_sec`/`handle_out_sec` record what was actually available.
   **Handles are allowed to overlap a neighbouring select** — Ryan's own
   key has selects 0.33s and 0.77s apart, so anything else would
   contradict his marking (§6 Q4).

### 2.3 How the two rulesets differ

The rulesets differ in *which signals gate a boundary*, not in the signal
layer — one signal pass serves both, which is why dual-use double-culling
costs almost nothing extra.

| | `visual` | `narrative` |
| --- | --- | --- |
| runs on | `broll` sources; `aroll` with `dual_use: true` | `aroll` sources (dual-use included) |
| what opens/closes a segment | motion-class change (§2.1) | speech continuity from the transcript; audio faults |
| motion | **the primary gate** — one intent, start to finish | a *defect* gate only: `shake` above a fitted residual closes a segment; a boring locked-off hold is perfect, and a slow drift is not a defect |
| focus | full criterion-2 treatment, rack allowed | soft-frame gate only, at a looser fitted threshold |
| exposure | fitted gates both ends | looser; a dim interview is not a reject |
| audio | ignored entirely | clipping, dead track, `speech_frac` minimum |
| typical `min_duration_sec` | ~1.2s | longer — a usable spoken beat is a phrase, not a frame count |

This encodes ROADMAP §4 directly: "A-roll (talking humans) culls on audio
and framing only — a locked-off interview shot with 'boring' visuals is
not a defect." A dual-use file runs both passes over the same signals
and emits two independent, freely overlapping segment sets (CULLS §7
REJECT 7 permits cross-ruleset overlap and forbids within-ruleset
overlap).

## 3. Fitting against the benchmark

### 3.1 The parameters

Roughly 18 scalars across both rulesets (the worked example in CULLS §5
lists a representative set). Every one is fitted; none is hand-set
(ROADMAP §5), and `params.fit_provenance: "manual"` is a contract WARN
precisely so a hand-set threshold cannot ship quietly.

### 3.2 The honest problem: 26 selects on one clip

The answer key is 26 selects, 92.2s of 235.3s (39%), on **one** 235-second
clip: one camera, one operator, one property, one lighting condition, one
morning. Eighteen free parameters against that is overfitting by
construction if fitted naively. What follows is the containment, and it
is a containment, not a solution.

1. **Fit in stages, ≤4 free parameters at a time**, with the rest held at
   values fixed *by construction* rather than by search — e.g.
   `handle_sec` is 1.0 because the scorer's tolerance is 1.0, and the
   px/frame normalization is 3840 because that is the source width.
   Coordinate descent over stages: motion parameters first (they set the
   boundaries), then focus, then exposure, then the narrative audio set.
2. **Block cross-validation over time, not over selects.** Adjacent
   frames are correlated, so leave-one-select-out leaks. Split the clip
   into three contiguous ~78s blocks, fit on two, score on the third,
   rotate. Report the mean and the spread. A parameter set whose score
   swings wildly across blocks has not been fitted, it has been
   memorized.
3. **Block bootstrap for a confidence interval.** Resample the three
   blocks with replacement to get an honest interval on P/R/IoU rather
   than a single triumphant number. Expect the interval to be wide. Say
   so in the Decision Log entry.
4. **Non-benchmark anchors that cannot be overfitted.** The safety-net
   fixtures (`stable.mp4`, `shaky.mp4`, `blurred.mp4`, `underexposed.mp4`,
   `overexposed.mp4`) are asserted as *orderings*, not scores: shaky must
   have higher motion residual than stable, blurred lower lapvar than
   stable, and so on. These are sign checks. A parameter set that inverts
   one of them is broken no matter what it scores on the benchmark.
5. **The single highest-value thing Ryan can do** is mark ~5 minutes of
   the unmarked 33-minute clip `0005` as a **held-out validation strip**
   that is never fitted on. That converts "we cannot claim
   generalization" into "we have one held-out measurement." It is
   optional and non-blocking (Decision Log, 2026-09-01), and it is worth
   asking for (§6 Q9).

**What 26 selects on one clip CAN establish:** that the detector is not
broken; that it beats the baseline; that one parameter set beats another
coarsely; that the failure modes are the ones we think they are.
**What it CANNOT establish:** that the numbers transfer to a different
camera, operator, lighting, or subject. The Decision Log entry recording
the first score must say both sentences.

### 3.3 The baseline and the target

**Measured baseline — "select everything"** (one range covering the whole
clip, scored with the shipped harness at the default 1.0s handle
tolerance): **P = 0.577, R = 1.000, IoU = 0.392.** Precision is 0.577
rather than 0.392 because truth dilation by the handle tolerance is
neutral by design.

**Measured floor from a crude two-signal probe**, run purely to establish
what "not trying very hard" already achieves — motion-consistency
residual plus a per-clip lapvar quantile, a 0.7s smoothing window, 1.2s
minimum duration, no classification, no shape analysis, no settle logic,
no fitting beyond reading six grid points:

| rule | n | covered | P | R | IoU |
| --- | --- | --- | --- | --- | --- |
| resid < 1.0, lapvar > q30 | 24 | 107.5s | 0.710 | 0.656 | 0.434 |
| resid < 1.5, lapvar > q30 | 23 | 135.1s | 0.701 | 0.775 | **0.459** |
| resid < 2.0, lapvar > q30 | 19 | 147.2s | 0.665 | 0.789 | 0.436 |
| resid < 1.5, lapvar > q50 | 16 | 101.0s | 0.721 | 0.615 | 0.415 |

Two signals and no cleverness already move IoU from 0.392 to 0.459 and
precision from 0.577 to 0.70, and land 23 segments against Ryan's 26.
**These are grid points read off one clip, not a fitted result** — they
are quoted as the floor the real detector must clear, not as a score.

**Proposed first target, per ROADMAP §5's "recall matters more":**

> **R ≥ 0.85, P ≥ 0.70, IoU ≥ 0.55**, with block-CV spread reported.

Recall first because "an assistant editor who hides good footage is worse
than one who lets a little shake through." A run that hits R = 0.90 at
P = 0.65 is a better cull than one at R = 0.70 / P = 0.85, and the report
should rank candidates that way rather than by IoU. Precision below ~0.6
stops being useful in a different way — the Cold Footage timeline becomes
a chore to scrub — so 0.70 is the floor, not the goal.

## 4. The signals sidecar

**Format: NumPy `.npz` (compressed) for the numbers, plus a small JSON
companion for the header and the run-length-encoded state sequence.**

- `<name>.signals.npz` — one float32 array per signal, `analysed_frames`
  long: `tx`, `ty`, `log_scale`, `roll`, `resid`, `peak`, `hf_energy`,
  `lapvar`, `lapvar_norm`, `luma_mean`, `luma_std`, `clip_low`,
  `clip_high`, plus an int8 `state` array and a decimated 64-bin
  histogram (**`int32`**, every 15th frame; amended 2026-09-01 after
  review: the plane is 518,400 pixels, so a single bin can exceed
  int16's 32,767 and silently wrap negative on black slates and
  lens-cap frames, exactly the frames the exposure gate exists to
  catch). Audio arrays (`audio_peak_dbfs`, `audio_rms_dbfs`,
  `audio_clip_run`; the code's names are authoritative and this
  document follows them) at their own 20ms rate, in the same file with
  their own length.
- `<name>.signals.json` — provenance (`cull_id`, `params_id`, ffmpeg
  version, plane size, fps, frame count, sha256 of the npz), the column
  dictionary with units, and the state sequence **run-length encoded**
  (`[{state, frame_in, frame_out, sec_in, sec_out}]`). This is the part a
  human actually reads: 100–200 runs for a 4-minute clip, one line each,
  showing precisely where every boundary fell and what it was called.

**Measured size:** the probe's npz, 7052 frames × 11 float32 columns,
compressed to **180 KB** for 235s of footage. The full ~18-column version
scales to roughly **300 KB per 4 minutes ≈ 4.5 MB per hour**. The whole
Runnells day (37 minutes) lands under 3 MB. Sidecars live under
`analysis/signals/` and are gitignored like all media-derived artifacts;
only scores are ever committed.

Why not JSON for the numbers: 7052 × 18 floats is ~2 MB of unreadable
text. Why not Parquet or HDF5: new dependencies for no gain — numpy is
already a hard dependency of everything in the venv. Why not CSV: 18
columns × 7052 rows loads slower and stores worse than the npz, and
nobody reads 7052 rows anyway. The RLE state list in the JSON is the
human interface; the npz is the machine's.

The benchmark report's "largest misses" section (already shipped in
`posthouse/benchmark.py`) gains one thing from this: for each miss, the
state runs the sidecar recorded over that interval, so a miss reads
`"18.3s missed: labelled shake, resid 3.9"` instead of `"18.3s missed."`

## 5. Build plan

Six slices. Each is small enough for one high-effort review, each has
something to test against, and each ends with something Ryan can run on
real footage in under five minutes (ground rule 8).

**Slice 1 — `posthouse/cull/signals.py`: the extractor.**
One ffmpeg pipe per file, the full §1 signal set, sidecar written per §4.
No classification, no segments. CLI: a path in, a sidecar out, timing
printed.
*Tested against:* the five safety-net fixtures as **ordering** assertions
(shaky > stable on residual, blurred < stable on lapvar, under/over on
the clipping fractions); the frame-count invariant (`analysed_frames`
within 2 of `duration × fps`); determinism (same file twice → identical
npz); and **the proxy-vs-source agreement gate ROADMAP §4 requires**,
which is expected to fail for sharpness and audio peaks and to pass for
global motion — that asymmetry is the evidence for the no-proxies rule.
*Dependencies:* ffmpeg (already required), numpy. Nothing from PreCut.

**Slice 2 — `posthouse/cull/classify.py`: per-frame motion class.**
Features per §1.3, deterministic class costs, hysteresis smoothing.
*Tested against:* synthetic clips generated deterministically with ffmpeg
from a single still (`crop` with a moving x/y expression for pan and
tilt, `zoompan` for push, an added jitter expression for shake) — these
have *known* ground truth, unlike any real footage, and they are the only
place a classifier can be checked exactly; plus the two hand-verified
Runnells intervals as the sign-convention pin (§1.3); plus the fixtures.
*Dependencies:* slice 1.

**Slice 3 — `posthouse/cull/segment.py`: runs → segments, one ruleset.**
The visual ruleset only. Hysteresis first, Viterbi behind a flag, both
scored. Emits a valid `culls.json` per the contract, plus rejections and
the coverage invariant.
*Tested against:* the shape validator (`coldfootage.validate_segments_shape`
and `benchmark.load_culls` must both accept the output **unmodified** —
that is the contract's central claim and it gets a test, not a
paragraph); a round-trip through `build_coldfootage_xml` producing a
sequence; the coverage-tiling invariant; then **the benchmark**, first
score recorded.
*Dependencies:* slices 1–2, `posthouse.coldfootage`, `posthouse.manifest`.

**Slice 4 — `posthouse/cull/fit.py`: the fitting harness.**
Staged coordinate descent, block CV, block bootstrap, fixture-ordering
guard, writes a `params` object and a report. This is where every
threshold in the system comes from.
*Tested against:* a fixed seed reproduces a fixed parameter set; the
fixture-ordering guard rejects an inverted parameter set; the reported
CV spread is computed on held-out blocks (asserted by construction on a
synthetic case with a known answer).
*Dependencies:* slice 3, `posthouse.benchmark`.

**Slice 5 — the narrative ruleset.**
Audio pass, transcript-driven speech presence via
`posthouse.harvest.transcribe`, the narrative gate table (§2.3). Tier 2
(Ryan's Mac) because transcription is a heavy-dependency harvest.
*Tested against:* `AROLL_01.MOV` + `lav.wav` fixtures for the audio
metrics; the benchmark's narrative ruleset block.
*Dependencies:* slice 3, `harvest/transcribe.py`, `harvest/sync.py`.

**Slice 6 — dual-use, manifest wiring, and the CLI the AE actually runs.**
Read the manifest, resolve `source_id`+`rel_path` → `source_path`, decide
`rulesets_run` per source from `kind` + `dual_use`, run both passes over
one signal extraction, write the master and the per-ruleset views, append
the `handoffs` entry, build both Cold Footage sequences.
*Tested against:* the PM's realistic fake-shoot fixture end to end; then
the real Runnells manifest on Ryan's Mac.
*Dependencies:* all of the above.

**Deterministic vs generative, stated once.** Every slice above is
deterministic: ffmpeg, numpy, and fitted scalars. No model is asked
anything. The two places a model could eventually earn a role are
(a) saliency — "is the *subject* the thing in focus" (§1.4's known
limitation) — and (b) editorial culling, which ROADMAP §1 explicitly
assigns to Phase 6+ and which this phase "does not pretend to do."
Neither happens now, for the same reason: ground rule 3, and there is
nothing to measure either against. Revisit only when the deterministic
stack has a recorded benchmark score and a specific, named failure mode
that determinism cannot reach.

## 6. Risks and open questions for Ryan

Each has a recommendation. Several of these change numbers in `params`
rather than anything structural, so none of them blocks slice 1.

1. **A pan that decelerates into a hold — one select or two?**
   *Recommendation: two, with the deceleration dropped as `settle`.*
   This is what his own key does at 18.85–19.19 (a 0.34s axis change
   between two selects), and it is what criterion 1 says. The real
   question for him is narrower: how long may a transition be before it
   stops being a dropped `settle` and becomes an accepted `drift`
   segment in its own right?

2. **Does a slow push count as movement, or as a static hold?**
   *Recommendation: treat a consistent slow drift below a fitted
   `static_eps` as `static`.* Measured: his static-looking selects sit at
   0.30–0.48 px/frame @480 standard deviation, his pans at 2.1–5.8
   px/frame mean. There is a real gap to put a threshold in, and the
   detector reports `push_in`/`pull_out` separately when divergence
   dominates, so his answer changes a label, not the architecture.

3. **How short can a select be?** *Recommendation: fit
   `min_duration_sec` in [1.0, 1.5], hard floor 1.0s.* His shortest is
   1.23s, his median 3.4s, and his longest 7.91s. A floor below 1.0s
   makes a timeline nobody wants to scrub.

4. **Handles when selects are 0.33s apart.** His key has adjacent selects
   0.33s and 0.77s apart, and the default handle is ±1.0s, so handles
   *will* overlap. *Recommendation: allow it.* Handles clamp to source
   bounds only (the ratified ruling) and the pre-handle range is what is
   validated and scored. The alternative — shrinking handles to avoid a
   neighbour — would silently give him less trim room exactly where the
   footage is densest.

5. **Rack focus ending out of focus — still a select?**
   *Recommendation: yes, tagged `focus_shape: "rack_out"`.* He said
   "something starts in focus and goes out, or vice versa," so both
   directions are in. Worth confirming, because `rack_out` is the one
   that will look like a defect in a report.

6. **Dark interiors and normalized sharpness.** An absolute sharpness
   threshold cannot keep both the dim-interior select at lapvar 100 and
   the daylight exterior at 4890. *Recommendation: normalize per clip and
   condition on motion (§1.4), and handle "too dark to use" with the
   exposure gate instead, fitted on the fixtures.* The risk to flag: a
   clip that is soft from end to end normalizes to "fine." The mitigation
   is the exposure/contrast gate plus a WARN when a whole clip's
   unnormalized sharpness sits below the fixture-derived floor.

7. **The visual-ruleset scope on dual-use A-roll.** A person walking
   through a locked-off frame is subject motion, not camera motion.
   Phase correlation with block outlier rejection is designed to ignore
   it, but the failure mode when a subject fills the frame is real.
   *Recommendation: ship it, measure it, and keep dense optical flow as a
   named escalation for low-confidence windows only (§1.2) rather than
   building it speculatively.*

8. **Below-threshold sync pairs.** Already logged as open for Phase 4
   (`posthouse/harvest/sync.py`). The cull's dependency is narrow: an
   unreliable pair means the narrative ruleset measures the camera track
   and flags the segment. *Recommendation: decide the general policy
   separately; this default is safe either way.*

9. **The 33-minute clip.** *Recommendation: ask Ryan to mark ~5 minutes
   of `DJI_20260430071514_0005_D.MP4` as a held-out validation strip.*
   It is the difference between "fitted on one clip, generalization
   unknown" and "one held-out measurement exists" (§3.2). Non-blocking,
   but it is the highest-value 20 minutes he can spend on this phase.

10. **Runtime, and whether it needs to be faster.** Measured 7.0× realtime
    for a reduced signal stack over the whole benchmark clip; §7 projects
    ~4–5× for the full stack. The Runnells day (37 minutes of footage)
    lands at **8–10 minutes**, against ROADMAP §7's stated bar of
    "overnight batch acceptable." *Recommendation: do nothing about
    speed.* It is already 40× better than the bar, and the obvious
    optimizations (parallel files, smaller plane) trade accuracy or
    complexity for time nobody needs back.

## 7. Runtime estimate, and what it rests on

| | |
| --- | --- |
| **Measured** | Full pipe — VideoToolbox HEVC decode of the original, `scale=480:270,format=gray`, and per frame in numpy: one 256×256 phase correlation plus two 256×128 half-frame correlations, whole-frame Laplacian variance, luma mean/std, clipped-low/high fractions — **33.6s wall for the 235.30s / 7052-frame benchmark clip = 7.0× realtime.** |
| **Measured** | 960×540 gray decode alone costs no more wall time than 480×270 (38.3s vs 40.6s for the full clip) — the scaler parallelizes. |
| **Measured** | Audio extraction: 0.30s for the whole clip. Negligible. |
| **Projected** | The 3×3 block grid is ~4× the probe's FFT work (9 blocks of 256² vs 1 block of 256² and 2 of 256×128 ≈ 2 block-equivalents), and the sharpness/exposure work quadruples on a 960×540 plane. Estimate **4–5× realtime** for the full §1 stack: **50–60s for the benchmark clip.** |
| **Projected** | Runnells day 1 = 235.3s + ~1992s ≈ **37 minutes of footage → 8–10 minutes**, single-threaded, one pass. Files parallelize trivially if it ever matters. |
| **Basis for the projection** | The probe's own decode-vs-compute split: decode-only at 480 was 40.6s while decode+all-signals was 33.6s — i.e. the pipe consumer keeps up with the decoder and the pipeline is decoder-bound at ~6× realtime, so quadrupling the numpy work moves the bottleneck to compute at roughly 4–5×, not to 1.75×. This is the estimate most likely to be wrong; slice 1 measures it for real and this table gets replaced with the measurement. |

Against ROADMAP §7's bar ("overnight-batch acceptable on Ryan's machine
is the bar; measured in Phase 4"), ten minutes for a full shoot day is
not close to a constraint. The risk that remains is a *pathological*
file — a 4K 60fps clip, or a codec VideoToolbox will not accelerate — so
slice 1's CLI prints its realtime factor on every run and the contract
records `sources[].analysis_sec`, which is how a regression gets noticed
instead of tolerated.
