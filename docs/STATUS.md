# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage

**Phase 0 complete (Tier 1 + Tier 2, verified on Ryan's Mac), except
real-footage audio sync — still open.** Phase 1 cloud-buildable slice
shipped. Product pivoted (2026-09-01): the end product is a new
role-driven app; PreCut is the component donor. **Project Manifest
contract ratified by Ryan** — one flagged tension (raw-footage
portability vs. PreCut's never-move-source-footage design) needs an
explicit decision before Phase 2's file-organization step is built.
See ROADMAP §6 for the phase plan.

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
  Phase 4). One real tension surfaced and flagged, not resolved: Ryan's
  "one self-contained project folder, nothing missing on handoff"
  principle conflicts with PreCut's deliberate never-move-source-footage
  design for raw footage specifically. Contract updated in place; full
  rulings in ROADMAP's Decision Log. *(This commit.)*

## Next (in order)

0. **Ryan:** decide the flagged raw-footage-portability tension
   (ROADMAP Decision Log, this date) — copy footage into the project
   folder, relink in place (PreCut's current model), proxy-only
   handoff, or something else. Blocks Phase 2's file-organization step
   specifically; the manifest schema itself is otherwise ready.
1. Phase 2: PM role headless build against the ratified contract
   (intake → organize → Brand Brief → manifest), once 0 is settled.
2. Phase 1 remainder (Mac session, available now): heavy-dep harvest
   wrappers (transcribe, tag/index, sync) per
   `posthouse/harvest/DEFERRED.md`; the one remaining Tier-2 gap
   (real-footage audio sync — needs genuine correlated dual-source
   audio to exercise the MFCC threshold, not synthetic tones).
3. **Ryan (when ready):** nominate the benchmark project (blocks
   Phase 3, not Phase 2). Also pending: ratify the "internal tool
   first, product maybe later" and "review happens in Premiere"
   assumptions from the gameplan discussion.

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
