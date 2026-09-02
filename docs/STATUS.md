# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage — see § Done for the latest slice

**PHASES 0 THROUGH 3 COMPLETE. Phase 4 (Assistant Editor cull) deep in
iteration, not yet shipped.** Slices 1-5 all built and reviewed;
significant real-footage findings at every stage; the benchmark itself
has been hardened twice this session (a real parser bug, a real
granularity blind spot). Where the cull's accuracy actually stands
right now, in one line: **a per-clip-normalized direction-stability
feature reaches a real, non-chance AUC of 0.714 on the one clip with
trustworthy ground truth (Runnells)** — genuine signal, not yet a
shipped detector, and not yet proven to generalize to the drone
footage.

**The single most important finding of the session**: precision/
recall/IoU can score a detector well while it does none of the real
work. A 4-blob detector that just kept most of a clip scored
P=0.727/R=0.993/IoU=0.593 (beating select-everything) against Ryan's
real, hand-corrected answer key for that clip, while being exactly the
"arbitrary cuts, basically unusable" output Ryan had already told us it
was — because 63.5% of that clip is genuinely usable, so covering most
of it scores fine on pure overlap regardless of whether the cuts mean
anything. Fixed: `granularity_ratio` and `under_segmentation_events`/
`over_segmentation_events` are now part of every score, naming the
specific offending segment and which real cuts it swallowed. This
same blind spot had already hidden the opposite failure earlier in
the session (463-run over-fragmentation on Runnells). Every future
Phase 4 score must be read alongside these, not just P/R/IoU.

**Two real parser bugs found and fixed on real footage this session**,
both because a review or a direct hand-check caught a wrong number
before it was trusted: an FCP7 frame-rate resolution bug (self-
consistency check needed for retimed/conformed clips, not just the
earlier sequence-rate-mismatch case) and, before that, the Des Moines
answer-key frame-rate inflation bug. Neither would have been caught by
tests on synthetic fixtures alone — both needed real, messy Premiere
exports.

Product pivoted (2026-09-01): the end product is a new role-driven app;
PreCut is the component donor. See ROADMAP §6 for the phase plan.

**What's next:** slice 4 (fitting) is being retried with the corrected
direction-stability feature and the exact scoring the granularity check
now requires — a "good score" is no longer trustworthy without checking
segment count/size too. The still-open question from the transfer study
(does anything here generalize past Runnells) remains unresolved and
is the next real test once a clean drone-footage answer key exists —
Ryan's hand-corrected Historic Valley Junction cuts are exactly that,
now correctly parsed and ready to score against.

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
- 2026-09-01 — **Phase 2 slice 1: manifest builder/validator shipped.**
  `posthouse/manifest.py` — build/load/save/validate, source-ID minting
  per contract §5 (frozen, never renumbered), two-moment validation
  (intake warns / handoff rejects, exhaustive), atomic writes,
  contract_version refusal, CLI. 64 new tests; suite 124 passed /
  1 skipped, verified independently by the Lead. High-effort code
  review found 2 real defects (undetected nested-source kind conflicts;
  person-ID docstring/implementation mismatch) — both fixed with
  regression tests before commit. Four contract-gap judgment calls
  logged in ROADMAP's Decision Log.
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
  the frame-0 marker.
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
  caught by watching the real run.
- 2026-09-01 — **Phase 1 complete; Phase 0's sync gap closed.** Heavy-dep
  harvest wrappers `posthouse/harvest/{transcribe,index,sync}.py` with
  8 Tier-2 tests; suite 186 passed / 1 skipped, verified by the Lead.
  Sync recovered a known 1.5s offset to within 4ms at score 11.55 vs
  SCORE_USE 10.0 on real TTS speech; the index was proven by feeding it
  to PreCut's own `load_broll_library`; transcription reuses PreCut's
  phrase chunking and on-disk shape. Vision tagging opt-in, off by
  default, no network in tests.
- 2026-09-01 — **Benchmark v1 staged (Runnells Day 1) and the PM's
  first real-footage run.** Manifest at `benchmark/runnells-day-1/`
  (paths only; media stays on `RDOSS_2025`). The real run exposed two
  bugs fixtures could not: proxies + `._*` sidecars counted as footage
  (6 videos for a 2-clip shoot, phantom July date) and a stale census
  surviving re-runs. Both fixed, regression-tested, and re-validated on
  the drive: 2 videos, one shoot date, refresh on re-run with frozen
  ids. PM tests 23 passed.
- 2026-09-01 — **Phase 3 scoring harness shipped and reviewed.**
  `posthouse/benchmark.py`: Premiere-export parser, time-overlap
  P/R/IoU with independently-dilated tolerance, per-ruleset and
  truth-scope handling, largest-misses report, CLI. Review found 8
  verified defects (3 load-bearing: gap-merging dilation, wrong-clip
  basename credit, untrimmed nests), all fixed with regression tests;
  truth scope added for the partial answer key. Suite 220 passed /
  1 skipped; arithmetic and real-key parse hand-verified by the Lead.
  Ryan's answer key (clip 0006, 26 selects, 39% usable) staged and
  parsing exactly.
- 2026-09-02 — **Phase 4 slices 1-5 built, reviewed, and honestly
  measured.** Signal extractor (slice 1: memory bug and a sign-inversion
  bug caught by review before shipping, fixed and verified); per-frame
  motion classifier (slice 2: correct per-frame, 18x over-fragmented
  when consolidated — accepted as a labeller, not a boundary-setter);
  segmentation (slice 3: first real culls.json and first honest
  benchmark score, below the crude two-threshold probe); fitting (slice
  4: the pre-committed rule fired — two thresholds beat the full
  classify+consolidate+gate pipeline on a fair held-out comparison, so
  the detector was simplified rather than given more parameters);
  stability detector adopted as production (slice 5: classifier demoted
  to a labeller only, as Ryan approved). Full detail and every number in
  ROADMAP's 2026-09-02 entries — not duplicated here.
- 2026-09-02 — **Benchmark v2 candidate staged: Des Moines Estabs**
  (real 8-day drone project, 41.5 min usable after a parser fix, 59
  true full-clip rejects). Cross-shoot transfer tested both directions:
  fit-on-Des-Moines transfers to Runnells, the reverse does not, and
  per-clip normalization (quantile + robust_scale, both shipped) did
  not close that gap on its own — until Ryan confirmed the real cause:
  Runnells is an exhaustive technical mark, Des Moines is real
  production selects filtered by editorial taste, so the two answer
  keys were never measuring the same thing. The four architecture
  options considered at the time (fit per shoot / per camera / accept
  asymmetry) are withdrawn as premature for that reason.
- 2026-09-02 — **Ryan corrected the detector by hand and gave concrete
  motion criteria** (shape-over-time, not just magnitude; frame-rate-
  aware B-roll interpretation). Video-vision evaluated and scoped as a
  diagnostic aid only — our native-rate signal sampling already exceeds
  what sparse frame extraction could offer for jerk/oscillation
  judgments. Three rounds of feature testing against real ground truth,
  each reported honestly before asking for more of Ryan's time: raw
  axis-purity was backwards (falsified by real data — most of Ryan's
  intentional moves are compound, multi-axis motion); direction-
  stability with a per-clip-normalized floor reached a real AUC of
  0.714 on Runnells, genuine signal, not yet proven to generalize.
- 2026-09-02 — **Two parser bugs found and fixed reading real Premiere
  exports; a real benchmark blind spot found and fixed as a result.**
  Getting Ryan's hand-corrected cuts to parse needed a self-consistency
  rate-resolution fix (retimed clips) plus a rescale correction caught
  by hand-checking the numbers rather than trusting a clean parse.
  Scoring the original detector against those real cuts then exposed
  that precision/recall/IoU cannot see segment count or size: a 4-blob
  detector scored P=0.727/R=0.993/IoU=0.593 — beating select-everything
  — while doing none of the real culling work. Fixed: `granularity_
  ratio` and `under/over_segmentation_events` are now part of every
  score. Suite 363 passed / 1 skipped. *(926a3d6, e36e2c9.)*

## Next (in order)

1. **Re-run slice 4 fitting** with the direction-stability feature
   (0.714 AUC on Runnells) and read every result through the new
   granularity metrics, not just P/R/IoU — a "good score" is no longer
   trustworthy on its own after today's finding.
2. **Score against Ryan's real Historic Valley Junction cuts** (now
   correctly parsed, 28 ranges) as the first real drone-footage
   technical-cull test — this is a materially stronger generalization
   check than the Des Moines Estabs set, which is now known to carry
   editorial contamination (real production selects, not an exhaustive
   technical mark).
3. Optional, not blocking: Ryan marking ~5 minutes of the unmarked
   33-minute Runnells clip `DJI_20260430071514_0005_D.MP4` as a
   held-out validation strip never fitted on.
4. Not yet revisited: the 13 open design questions in
   `PHASE4_CULL_DESIGN.md` §6 / `CULLS.md` §8 (pan-into-a-hold split,
   slow push classification, minimum select length, rack focus ending
   out of focus, etc). The pipeline they were written against
   (classifier-driven consolidation) has since been superseded by the
   stability detector, so some may now be moot — worth a pass once the
   detector's real-footage generalization question above is settled,
   not before.
5. Low priority, still technically open from the original gameplan
   discussion: ratify the "internal tool first, product maybe later"
   and "review happens in Premiere" assumptions with Ryan.

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
