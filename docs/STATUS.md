# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage — see § Done for the latest slice

**PHASE 2 COMPLETE — the Project Manager role runs headless.** All
three slices shipped (manifest builder/validator, Brand Brief
generator, organization step). Verified end to end on a realistic fake
shoot: a raw footage dump plus intake answers produces an organized
project folder holding footage (never moved), a `Brand Assets`
directory, and a validated `manifest.json`. Suite: 178 passed /
1 skipped.

Phase 0 is complete (Tier 1 + Tier 2, verified on this Mac) except
real-footage audio sync; Phase 1's cloud-buildable slice shipped.
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

1. **Ryan: nominate the benchmark project** — one finished past job
   whose raw footage AND delivered edit are both still on disk. This is
   now the critical path: Phase 3 (benchmark) gates Phase 4 (the
   Assistant Editor cull), and the cull is the one skill that must be
   measured against a real answer key rather than tuned by feel.
2. Phase 1 remainder (Mac session, available now): heavy-dep harvest
   wrappers (transcribe, tag/index, sync) per
   `posthouse/harvest/DEFERRED.md`; the one remaining Tier-2 gap
   (real-footage audio sync — needs genuine correlated dual-source
   audio to exercise the MFCC threshold, not synthetic tones).
3. **Ryan (when ready):** nominate the benchmark project (blocks
   Phase 3, not Phase 2). Also pending: ratify the "internal tool
   first, product maybe later" and "review happens in Premiere"
   assumptions from the gameplan discussion.

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
