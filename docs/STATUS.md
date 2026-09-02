# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage — see § Done for the latest slice

**PHASES 0 THROUGH 3 COMPLETE. Phase 4 (Assistant Editor cull) in
design.** Benchmark v1 has a real answer key (clip 0006, 26 selects)
and a recorded select-everything baseline to beat: P 0.577 / R 1.000 /
F1 0.732 / IoU 0.392. The Architect is designing the cull's signal
stack, segmentation, and culls.json contract against Ryan's criteria
(motion intent per select; clear focus, rack allowed) before any build.

Earlier stage summary: **PHASES 0, 1, AND 2 ALL COMPLETE, nothing deferred.** Phase 0's last
Tier-2 gap (real-footage audio sync) closed with a real measurement:
4ms offset error, score 11.55 vs threshold 10.0 on manufactured real
speech. Phase 1's heavy-dep harvest wrappers (transcribe, index, sync)
shipped against the real venv. Phase 2's Project Manager runs headless,
verified end to end on a realistic fake shoot including a late-footage
re-run. Suite: 186 passed / 1 skipped (~50s on the Mac; Tier-2 tests
carry a `tier2` marker so cloud runs can deselect them).

Product pivoted (2026-09-01): the end product is a new role-driven app;
PreCut is the component donor. See ROADMAP §6 for the phase plan.

**What's next, and what it needs from Ryan:** the Assistant Editor is
the Phase 4 flagship and the roadmap gates it on Phase 3, the benchmark
— which is blocked on Ryan nominating one finished past project (raw
footage plus his delivered edit). Without it there is no answer key to
measure the cull against, and the cull is exactly the skill that must
not be tuned by vibes.

## In progress

*(nothing in flight)*

## Done

- 2026-08-31 — Reviewed `precut` and `precut-premiere-extension` end to
  end; PreCut confirmed as foundation-then-donor. *(ROADMAP.md, 6137dfe.)*
- 2026-08-31 — ROADMAP v1. *(6137dfe.)* 2026-09-01 — Governance layer.
  *(fc3cabc.)* Repo renamed to `pierces-post-house`. *(c874dee.)*
- 2026-09-01 — Adversarial architecture review: 14 findings, 3 blocking,
  all incorporated. *(037694a.)*
- 2026-09-01 — **Phase 0 Tier 1 safety net shipped**: hermetic exporter
  gate, canonicalized golden master, FCP7 quirk tests 1–5, import gate.
  16 passed / 2 skipped, verified independently by the Lead; sabotage
  check caught a planted regression. Tier 2 items (full import gate, DB
  migrations, real-footage sync) deferred to Ryan's Mac. *(5829746.)*
- 2026-09-01 — **Product pivot logged** (Ryan): new app with role-driven
  UX; PreCut = donor, harvested not rebuilt, untouched until superseded;
  build order PM → AE; Project Manifest contract (incl. `dual_use`
  flags) is the PM's hard deliverable. Roadmap restructured to Phases
  0–9. *(7331bf3.)* Brand Brief spec + co-location rule added. *(next
  two commits.)*
- 2026-09-01 — **Reviewer pass on the safety net**: 8 findings (2 would
  have failed on Ryan's Mac: markers import-gate assertion inverted
  under real ML deps; hash-seed-dependent path normalization order on
  macOS), all fixed and re-verified, plus a BLESS=1 refusal in the
  runner and loud-skip on bless. *(This commit.)*
- 2026-09-01 — **Phase 1 slice shipped**: `posthouse/` package — door-3
  bridge pinned to PreCut e035fbaf, cold-footage builder (segments JSON
  → V1 sequence XML through the proven exporter chain, API + CLI,
  non-zero exit on failure), light-dep harvest wrappers (auto_include,
  camera_inference, theme_categories, proxy_manager), 15 new tests +
  cold-footage golden. Suite now 31 passed / 2 skipped, verified by the
  Lead. Heavy-dep wrappers deferred (`posthouse/harvest/DEFERRED.md`).
  *(547a6cd.)*
- 2026-09-01 — Project Manifest contract v1 drafted, pending Ryan's
  ratification. *(8100c85.)*
- 2026-09-01 — **Teleported to Ryan's Mac; Phase 0 Tier 2 shipped for
  real**: full 35-module import gate and additive-only DB-migration
  test implemented (previously documented-only stubs) and run against
  the real `~/precut-venv-fresh` + a freshly pinned checkout
  (`e035fbaf`, exact match to `posthouse/PRECUT_PIN`). 60 passed /
  1 skipped. Sabotage re-verified on the Mac (historical off-by-one bug
  reintroduced in a scratch copy, caught three ways, reverted).
  `run_safety_net.sh` now auto-detects the real venv and refuses to run
  with `PRECUT_ROOT` unset rather than defaulting silently. Real-footage
  audio sync remains the one open Tier-2 item. *(ca50076.)*
- 2026-09-01 — **Project Manifest contract ratified by Ryan.** All 6
  open questions answered; 3 diverge from the draft's recommendation
  (delivery targets not proposed by the PM at all — Creative Editor's
  job after it has actually seen the organized footage; shoot dates
  read with no confirmation step; on-camera naming gets real added
  scope — per-voice attribution with propagating rename, scoped out to
  Phase 4). *(a25e2cc.)*
- 2026-09-01 — **Footage-portability tension resolved.** Ryan clarified
  "self-contained project folder" means brand/other small assets are
  copied to live alongside the footage (a sibling directory under the
  project root) — the footage itself is never copied or relocated,
  fully preserving PreCut's "source footage is never moved" design.
  Contract §2.3 updated; no blocker remains.
- 2026-09-01 — **Cross-clip speaker naming: verified real, then
  dropped.** Confirmed via web search Premiere can't do it (see
  ROADMAP Decision Log). Ryan then de-scoped the feature itself the
  same day: generic "Speaker 1" / "Speaker 2" labels are sufficient
  everywhere — no cross-clip voice matching or propagating rename
  needed. Removes real complexity from the eventual Phase 4 design.
  Manifest's `people` field simplified back to a plain intake roster.
  *(This commit.)*

## Next (in order)

1. **Phase 4 slices 1 to 3 built** (suite 302/1). Slice 3 emits real
   `culls.json` and the first benchmark score, and the score is an
   honest negative: **P 0.628 / R 0.553 / IoU 0.334, below the crude
   two-signal probe (0.701 / 0.775 / 0.459)**. Consolidation succeeded
   (463 runs to 65, median 2.90s; boundaries beat chance 38.5% vs 28.7%
   for the first time). The Lead diagnosed the loss: **the focus gate
   rejected 73% of Ryan's marked-usable footage** and, isolated, costs
   24 points of recall while buying no precision (gate off: 0.644 /
   0.791 / 0.407). Even so the pipeline still trails the crude probe on
   IoU.
   **Next: slice 4, the fitting harness** — and it is now the slice
   where this design must prove it earns its complexity. Staged fits of
   at most four parameters, contiguous-block CV, block bootstrap, and
   the non-overfittable fixture ordering guards. If honest fitting
   cannot beat two thresholds, the finding is to simplify the detector,
   not to add parameters. Measured on the real clip: 1.34x realtime
   (below the 4-5x projection, well inside the bar). Real-footage
   proxy check: sharpness shape survives compression (r 0.98) but its
   absolute level and the motion residual do not (r 0.54), so originals
   stay required. Slices 2 to 6 per `PHASE4_CULL_DESIGN.md` §5.
2. **Ryan, highest-value optional ask (~20 min):** mark about 5 minutes
   of the 33-minute clip `DJI_20260430071514_0005_D.MP4` as a held-out
   validation strip that is never fitted on. It turns "fitted on one
   clip, generalization unknown" into one honest held-out measurement.
3. **Ryan, 13 design questions with recommendations** in
   `PHASE4_CULL_DESIGN.md` §6 and `CULLS.md` §8 (e.g. pan decelerating
   into a hold: one select or two; slow push = movement or static;
   minimum select length; rack focus ending out of focus). The Lead is
   proceeding on the recommendations; override any at any time.
2. Unblocked while waiting on the benchmark (neither sets a threshold,
   so neither risks tuning by feel): (a) the cull's **signal-extraction
   layer** — per-frame motion magnitude (ffmpeg `vidstabdetect` or
   optical flow), Laplacian blur, exposure histograms, audio peaks,
   emitted as a signals file and checked against the safety-net
   fixtures, which were built with exactly this failure taxonomy
   (stable/shaky/blurred/under/over-exposed); (b) the Phase 3 scoring
   harness scaffold — answer-key format plus precision/recall scorer,
   runnable the moment a real project is nominated. Thresholds that turn
   signals into segments are NOT set until the benchmark exists.
3. **Ryan (when ready):** nominate the benchmark project (blocks
   Phase 3, not Phase 2). Also pending: ratify the "internal tool
   first, product maybe later" and "review happens in Premiere"
   assumptions from the gameplan discussion.

- 2026-09-01 — **Phase 3 scoring harness shipped and reviewed.**
  `posthouse/benchmark.py`: Premiere-export parser, time-overlap
  P/R/IoU with independently-dilated tolerance, per-ruleset and
  truth-scope handling, largest-misses report, CLI. Review found 8
  verified defects (3 load-bearing: gap-merging dilation, wrong-clip
  basename credit, untrimmed nests), all fixed with regression tests;
  truth scope added for the partial answer key. Suite 220 passed /
  1 skipped; arithmetic and real-key parse hand-verified by the Lead.
  Ryan's answer key (clip 0006, 26 selects, 39% usable) staged and
  parsing exactly. *(This commit.)*
- 2026-09-01 — **Benchmark v1 staged (Runnells Day 1) and the PM's
  first real-footage run.** Manifest at `benchmark/runnells-day-1/`
  (paths only; media stays on `RDOSS_2025`). The real run exposed two
  bugs fixtures could not: proxies + `._*` sidecars counted as footage
  (6 videos for a 2-clip shoot, phantom July date) and a stale census
  surviving re-runs. Both fixed, regression-tested, and re-validated on
  the drive: 2 videos, one shoot date, refresh on re-run with frozen
  ids. PM tests 23 passed. *(This commit.)*
- 2026-09-01 — **Phase 1 complete; Phase 0's sync gap closed.** Heavy-dep
  harvest wrappers `posthouse/harvest/{transcribe,index,sync}.py` with
  8 Tier-2 tests; suite 186 passed / 1 skipped, verified by the Lead.
  Sync recovered a known 1.5s offset to within 4ms at score 11.55 vs
  SCORE_USE 10.0 on real TTS speech; the index was proven by feeding it
  to PreCut's own `load_broll_library`; transcription reuses PreCut's
  phrase chunking and on-disk shape. Vision tagging opt-in, off by
  default, no network in tests. Full findings in the Decision Log.
  *(This commit.)*
- 2026-09-01 — **Phase 2 slice 3 shipped; PHASE 2 COMPLETE.**
  `posthouse/projectmanager.py` — census, unsupported aggregation,
  harvested camera inference with the real pin, shoot dates from file
  timestamps, brand-asset staging into `Brand Assets`, Brand Brief
  generation, append-only handoff record, and a hard handoff-validation
  gate before anything is written. 21 tests; suite 178 passed /
  1 skipped. Verified by the Lead on a realistic fake shoot including a
  late-footage re-run (revision 1→2, prior source ids frozen, exactly
  one new file on disk, footage never copied). Also amended contract
  §4.2 to drop the now-incorrect "delivery_targets is empty" warning,
  caught by watching the real run. *(This commit.)*
- 2026-09-01 — **Phase 2 slice 2: Brand Brief generator shipped.**
  `posthouse/brandbrief.py` — font extraction from name tables, macOS
  install status, deterministic palette, README + brand-card PNG inside
  `assets_dir` with the co-location invariant enforced in code, CLI.
  33 tests; suite 157 passed / 1 skipped, verified by the Lead against a
  realistic SoldFast-branded fixture (it correctly recovered navy
  #033459, blue #0391D8, light blue #00ADE1, orange #F4690B from a logo).
  Three defects found by *looking at* the rendered card rather than by
  tests — em dashes in generated copy, bare counts instead of named
  files with reasons, and a vivid orange labelled "neutral" — all fixed
  with regression tests. Out of scope this slice: PDF summarization and
  the frame-0 marker. *(This commit.)*
- 2026-09-01 — **Phase 2 slice 1: manifest builder/validator shipped.**
  `posthouse/manifest.py` — build/load/save/validate, source-ID minting
  per contract §5 (frozen, never renumbered), two-moment validation
  (intake warns / handoff rejects, exhaustive), atomic writes,
  contract_version refusal, CLI. 64 new tests; suite 124 passed /
  1 skipped, verified independently by the Lead. High-effort code
  review found 2 real defects (undetected nested-source kind conflicts;
  person-ID docstring/implementation mismatch) — both fixed with
  regression tests before commit. Four contract-gap judgment calls
  logged in ROADMAP's Decision Log. *(This commit.)*

## Attempts ledger

*(task · tier · attempt # · what was tried · why it failed — written by
the Lead before any re-dispatch; empty so far)*

## Escalations / blockers

*(none open)*

## Standing notes

- Repo: `wastemytime2007/pierces-post-house`, branch
  `claude/ai-video-editing-team-k2a66r`. Working copies: cloud session
  at `/home/user/test`; teleported Mac session at
  `/Users/pierce/pierces-post-house`.
- Real-footage work runs only on Ryan's Mac.
- PreCut pin for harvests: commit `e035fbaf1fe63bfb0647917af142304b4470d00d`
  (`v1.0.0-beta.3`), recorded in `posthouse/PRECUT_PIN`. A read-only
  checkout for `PRECUT_ROOT` lives at `~/precut-checkout` on Ryan's Mac
  (outside this repo, never modified — protected-repo rule).
- Real venv for Tier-2 runs: `~/precut-venv-fresh/bin/python`
  (auto-detected by `safety_net/run_safety_net.sh`).
