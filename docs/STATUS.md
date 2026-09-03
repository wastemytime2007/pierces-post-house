# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage — see § Done for the latest slice

**READ `docs/REQUIREMENTS.md` FIRST.** It carries Ryan's founding words —
why this project exists, the five roles in his own description, the four
PreCut shortcomings he named directly, and the process failure that led to
this re-scope. That file, not this section, is the source of truth for intent.

**This is one application, forked from PreCut — not a separate app, and not
a PreCut rewrite.** PreCut's own repo (`~/precut-checkout`) is the protected
donor: never modified, never committed to. Its ingest, transcription,
tagging, audio sync, and Premiere export are proven code, copied into `app/`
and extended in place, never reimplemented from scratch. This app grows real
code only where PreCut falls short — there is only ever one app to open, per
Ryan's explicit correction (`ROADMAP.md` Decision Log, 2026-09-03). See
`precut-capabilities` skill for exactly what PreCut does and doesn't do,
verified from its source on 2026-09-03 after this project spent three days
operating on a wrong model of it.

**The real, confirmed gap** (Ryan's own words, 2026-09-02): PreCut's story
planner "tends to skim through transcripts and that leaves a lot on the
table." Measured: one Claude call, ~9 ranges / ~13 minutes of material per
run, reading a whole project's transcripts concatenated into one prompt.
Ryan's own hand-pass over comparable material produced 250 selects. That gap
— exhaustive, verified reading vs. one skimmed pass — is the thing worth
building. Nothing else about PreCut is being replaced.

**PHASES 0-3 (safety net, harvest layer, Project Manager code, benchmark
harness) are built but Phase 0/1/3 output was never shown to Ryan; the
Project Manager specifically has never been verified by him** — that's
task 1.1 below, the cheapest possible next step.

**Phase 4's motion cull is PARKED**, not shipped, not being worked on. Five
single-signal detectors were tried; none beat "keep everything" on real
drone footage; grid-fitting was ruled out as the cause. Full history in the
Decision Log. Revisit only with a genuinely new idea, per Ryan's own call.

**`posthouse/moments.py` was built, verified once by Ryan on a real query
("They are about the cabinets" — the first thing in this project confirmed
working by the person who has to trust it), then deleted 2026-09-03** once a
full read of PreCut showed it duplicated the existing story planner with
weaker selection and no audio sync. The verification method it proved
(machine-check every quote before showing it) survives in the
`verified-quotes` skill; the module itself does not.

**Agent Studio and the Content-Engine Obsidian vault were surveyed and
retired as separate systems.** Both were almost entirely doctrine, never
run. Value ported to four global skills (`soldfast-content-funnels`,
`longform-story-craft`, `footage-assembly-method`, `hook-writing`) that the
Creative Editor role loads. `studio.py` and the vault are dead, left alone,
not revived.

**New non-negotiable rules, `CLAUDE.md` §7-8**: prove every new capability
on one clip/transcript/interview before scaling, and one role in flight at a
time with Ryan's tested sign-off as the only valid "done." Both exist
because this session violated them once each.

## In progress

- **Task 1.1/2.1 converged into one tab: "Project," code-complete, not yet
  Ryan-verified.** History, each step driven by Ryan actually using the
  previous one: (1) PMTab and IngestTab held two independent records of
  the same A-roll/B-roll/Source-Audio declarations, so footage got
  declared twice — merged so PMTab is the only place that happens,
  calling PreCut's own `add_source`/`remove_source` directly *(454bd05,
  broken up into c8f994e after a staging mistake — see below)*. (2) Ryan
  then reported three more real problems in one message: Ingest didn't
  make sense as a separate tab any more; Organize should auto-start
  processing instead of requiring a second click; run_pipeline never
  tagged dual-use footage as B-roll; and after syncing, the Assistant
  Editor tab was just showing the same thing Ingest did. All four
  addressed:
  - **Real backend bug, not UI**: PreCut's `add_source` is path-keyed
    with exactly one kind per path, so a dual-use A-roll folder never
    appeared in `sources_by_kind("broll")` for `pipeline.py`'s B-roll
    collection — no wiring of the checkbox could have fixed this without
    a model change. Fixed: `SourceFolder.dual_use` field,
    `Project.set_dual_use()`, and `_collect_videos` unions in dual_use
    A-roll sources when collecting "broll". Verified with a scripted
    before/after check (empty broll collection before the flag, includes
    the file — same SourceFolder object — immediately after).
  - IngestTab.jsx and AETab.jsx deleted; their content (run-pipeline
    button/modal, progress, transcripts, SyncMatrix + AudioSyncPreview)
    absorbed into PMTab.jsx. ProjectView now has two tabs: Project and
    Ideas. Ryan's own read: none of it was ever distinct Ingest or
    Assistant Editor work.
  - Organize now auto-fires `run_pipeline` on manifest-write success,
    with the same defaults the manual modal used.
  *(c8f994e — this is the second of two commits: the first, b9b8de9,
  shipped with only file deletions because `git add` aborted on an
  already-removed pathspec before staging the real changes, same mistake
  as this session's earlier `ea6d093`; caught immediately and fixed with
  an honest follow-up commit, not a silent amend.)*
  Verified: the scripted dual-use check above; `vite build` clean
  (61→59 modules); `npm run tauri dev` compiles and runs clean alongside
  the real PreCut.app with no Python tracebacks in the startup log.
  **Ryan ran it on Runnells Day 1**: Organize did auto-start processing,
  and the sync review renders correctly under the merged Project tab
  (screenshots show the matrix and the "2 reliable of 8 pairs" state).
  **Still not confirmed**: the dual-use B-roll tagging fix, since this
  run declared no B-roll/dual-use sources at all — needs a real dual-use
  shoot to actually exercise it.
- 2026-09-03 — **Real sync-quality finding on that same run, investigated
  and fixed with new capability, not yet Ryan-verified.** Ryan: "the sync
  isnt as accurate as it used to be. I think you may have broken it with
  the one source one clip idea." Investigated before agreeing or
  defending: `precut_pipeline/audio_sync.py` is byte-identical to the
  protected PreCut checkout (diffed directly, confirmed never edited);
  today's changes touched only `dual_use`/B-roll collection, irrelevant
  to this aroll+audio-only project. The real cause, found in the
  persisted `project.json`: PreCut's own unmodified `sync_project()`
  scored clip `0006` at 2.8-3.15 against all four audio files (noise
  level) because a ~5-minute stretch of dead/irrelevant audio (Bob out of
  the room, then on the phone elsewhere) diluted the single whole-file
  correlation PreCut runs per pair — a real, pre-existing PreCut
  limitation, not a regression. Ryan then named the general case
  precisely: "bridge the gap in the syncing for when people walk in and
  out of the room... finding a way to sync the useable portions... is
  the ideal scenario." Built `posthouse/sync_coverage.py`
  (`analyze_pair_coverage`) — genuinely new capability, confirmed PreCut
  has nothing like it. Gates candidate windows via one cheap ffmpeg
  `silencedetect` pass (energy, not correlation — a phone call still has
  energy and still gets tried, correctly scoring low on its own merits),
  then runs PreCut's own unmodified `sync_pair()` on each window against
  the full A-roll proxy, requiring at least two windows to independently
  agree before trusting either. **Verified on the exact real pair before
  writing the algorithm**: manual windowed scan of clip 0006 vs. both
  take-2 lav files found four windows (420-540s into one lav file)
  agreeing on offset -308.6s (scores up to 18.6, three above STRONG) and
  six windows on the other lav agreeing on -306.8s (scores up to 31.0) —
  both invisible to the whole-file pass. The finished module reproduces
  this exactly. Regression test (`test_sync_coverage.py`, tier2) built a
  synthetic dead-zone case the same way `test_sync.py`'s existing fixture
  is built (real TTS speech, not synthetic tones) and caught a real bug
  in the process: `analyze_pair_coverage` never exposed `min_window_sec`,
  so a small `window_sec` silently produced zero candidate windows with
  no error — fixed before shipping. Suite: 224 passed / 1 skipped
  (non-tier2, unaffected) + 1 passed / 1 skipped (new tier2 tests, one
  honestly skips rather than force a pass). Wired into the app as an
  "Analyze coverage" action on a selected pair in the merged Project
  tab's sync review — read-only, reports a proposed offset and supporting
  time ranges, never rewrites the matrix's own score/offset. *(f52886f.)*
  **Two real bugs found on first use, both fixed, neither yet verified**:
  Ryan: "Im not seeing a Analyze Coverage button. And is there a way to
  re run the sync process? Clicking run pipeline is still giving the
  same output from the original run." (1) `sync_project()` caches by a
  hash of the source paths, which never changes on a plain re-run, so
  "Run pipeline" was silently always returning the identical cached
  result — no UI ever exposed a way around this. Added `force_audio_sync`
  to `PipelineJob` (clears `project.audio_sync` before calling
  `sync_project()`) and a dedicated "Re-sync audio (ignore cached
  results)" button. (2) The coverage button only appeared after clicking
  a matrix cell, and the empty-preview hint said "click a **reliable**
  pair" — exactly the pairs that don't need it; Ryan had no way to find
  it. Added `WeakPairsPanel`: every pair below `SCORE_USE` is now listed
  explicitly and unconditionally below the matrix with its own "Find
  usable stretches" button, no implicit clickability knowledge required.
  *(d1cb8be.)*
  **Third round, both a real bug and real UX confusion, both fixed:**
  Ryan: "It doesn't seem to be doing anything. But it's also really
  confusing on what all the buttons are for... click on the actual audio
  files, under that... individual clips with the find useable stretches
  button, under that... an Analyze Audio button." The "doesn't seem to be
  doing anything" part was a genuine bug, not confusion: `WeakPairsPanel`
  called `setSelectedPair()` (to also load the pair into the preview
  player) in the same click that started the analysis; the preview
  player's own effect reset coverage/loading/error state whenever
  `selectedPair` changed, firing on that same click and wiping the
  "Analyzing…" flag right after it was set. The confusion part was real
  too — three overlapping entry points (matrix click, panel row, a
  second button that appeared once a pair was selected) for one action.
  Fixed by separation, not more labels: `WeakPairsPanel` is now fully
  self-contained (own subscribe listener, own per-pair-key state, own
  inline result under the triggering row) and doesn't touch
  `selectedPair`/the preview player at all — the two actions can no
  longer interfere with each other by construction. Removed the
  redundant second button entirely. Added three numbered section headers
  (Sync results / Preview a pair / Fix a weak pair). *(15037b4.)*
  **Fourth round: a real, un-tested performance bug, found by checking
  process state rather than assuming.** Ryan: "I clicked analyze on 3
  things and have waited 5 minutes they still just say analyzing." `ps
  aux` showed the backend genuinely still computing (20+ min accumulated
  CPU), not hung. Measured the real cost directly: correlating an 18s
  clip against a 33-minute A-roll proxy took 7.3s — cost is driven by
  the A-roll's length, not the window's. A long lav file's non-silent
  stretches can produce 50-60+ candidate windows against a long A-roll:
  6-8+ minutes for ONE pair, with three started at once making it worse.
  The original build was only ever verified against the one SHORT clip
  from the original investigation (0006, 235s) — never tested against
  this project's actual long files, which are most of its weak pairs.
  Fixed: `DEFAULT_MAX_WINDOWS=10` (subsampled evenly across the whole
  file, not truncated from the start — truncating would silently only
  ever look at the first few minutes, exactly wrong for "comes back
  later"), a `progress_callback` and `cancel_flag` threaded through to a
  new `pair_coverage_progress` event and `cancel_pair_coverage` command
  (reusing the same `_jobs`/`ActiveJob` registry `cancel_job` already
  uses), and a `windows_available` field so a result can say "sampled 10
  of 42" instead of implying an exhaustive search. Re-measured after the
  fix: the short-clip case ~12s (was already fast), the worst real case
  in this project (both ~30min files against each other) ~63s (was on
  track for several minutes) — offsets unchanged on both. *(d2ad077.)*
  **Fifth round: multi-click starvation, root-caused and fixed with a
  real (not reasoned-about) test.** Ryan: "The first one analyzed but
  the other 5 just say starting... and arent loading." Each click still
  spawned its own raw thread doing CPU-bound correlation (plus reading a
  long file off the same external drive) — six competing starved
  everything but the one that ran first; the other five never got far
  enough to emit even one progress event. Fixed with a single background
  worker pulling from a queue, one request at a time. Verified by
  actually firing 3 requests at the real handler and capturing every
  emitted event (not just reasoning about the fix): confirmed request 2
  doesn't start until request 1's "analyzed" event fires, request 3 not
  until request 2's, every window of every request actually progressing,
  in that exact serialized order. New "queued (N ahead)" state shown
  before a request runs. Also caught and fixed an ordering race in that
  same test: the very first request could emit "started" before "queued"
  if the worker grabbed it before the caller finished emitting.
  *(711a268.)*
  **Sixth round: a real scope gap, not a bug.** Ryan: "It found some
  matches but when i went to export it still didnt include the found
  matches." Coverage was deliberately scoped read-only from the start —
  that held, but it also meant a finding could never reach export, since
  nothing ever gave Ryan a way to act on one. Confirmed exactly what
  export checks first: `exporter.py` only uses pairs where
  `SyncPair.is_reliable` is true (`score >= SCORE_USE or
  promoted_via_consistency`). New `apply_pair_coverage` command writes
  the accepted offset into the matching pair and sets
  `promoted_via_consistency=True` — PreCut's own existing field for
  exactly this case, not a new mechanism — leaving the original score
  untouched so the matrix keeps showing honestly where the number came
  from. New "Apply this sync (use it on export)" button. Verified
  end-to-end against a COPY of the real project.json (never the live
  one): applied a real result, confirmed both in-memory and
  reloaded-from-disk state show the update correctly. *(2744b87.)*
  **Not yet verified in the app or against a real export.**
  **Seventh round: Ryan rejected the whole manual-workflow approach, and
  was right to.** "Its still not exporting with all of the wavs. And
  this is feeling extremely overcomplicated... There shouldnt be extra
  steps needed. We should just be able to sync things." Re-architected
  rather than patched: `pipeline.py`'s `_run_audio_sync` now runs
  `analyze_pair_coverage` automatically on every pair PreCut's own pass
  couldn't confidently sync, as part of the same stage, before Ryan ever
  sees a result — a rescued pair's offset and `promoted_via_consistency`
  are set right there, nothing to apply afterward. Removed entirely:
  `WeakPairsPanel`, `CoverageResult`, the three manual IPC commands and
  their queue worker, the "3. Fix a weak pair" section — 519 lines
  removed for 54 added. Verified against a full reset copy of the real
  project (never the live file): ran the exact logic now in
  `pipeline.py` unattended against all 6 real weak pairs — 3 rescued,
  3 correctly found no match (they're the wrong mic/clip
  cross-pairings). **All four real (mic, clip) correspondences in this
  project are now reliable — every WAV placed somewhere real, ~170s,
  one pass, no clicking.** *(b613002.)* **Not yet verified in the app
  or against a real export** — that's the piece that actually matters
  now: does export include all four WAVs.

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
- 2026-09-02 — **Slice 4 re-fit with direction-stability, shipped as a
  sixth first-class arm; it wins, narrowly.** The diagnostic sweep that
  found AUC 0.714 was lost to context compaction (never committed as
  code) — reconstructed from scratch against the same cached signals and
  reproduced to 3 decimal places before building on it, not trusted from
  memory. Shipped as `stability_combine="dirstab_only"` in `segment.py`
  (per-clip-normalized circular-statistics signal on motion direction)
  and wired into `fit.py` as a seventh CV/bootstrap/fixture-guarded arm.
  9 new tests; suite 223 passed / 1 skipped (non-tier2) + 2 real-clip
  tier2 tests. Re-fit on Runnells (same sidecar/answer-key/precision-
  floor as the prior resid_only run): `dirstab_only` wins the arm
  ranking, held-out **P 0.629 / R 0.915 / IoU 0.417** — inside noise of
  `resid_only`'s 0.627/0.911/0.417, and still short of the crude
  two-threshold probe overall (beats it on recall, trails on precision
  and IoU by ~0.01). Genuine, confirmed signal; not a breakthrough. Not
  yet scored against real drone footage.
- 2026-09-02 — **Scored against Ryan's real Historic Valley Junction
  cuts: the transfer question is answered, and the answer is no.**
  Both re-fitted arms (dirstab_only, resid_only) score within rounding
  of select-everything on P/R/IoU (0.727/0.99/0.593 vs select-
  everything's 0.726/1.00/0.596). The granularity metrics catch what
  P/R/IoU hides: dirstab_only genuinely predicts more, finer segments
  (9 vs resid_only's 4 vs select-everything's 1) — a real difference —
  but even those 9 are still giant blobs swallowing 7-8 real cuts each.
  Neither arm does the actual culling work Ryan asked for on this
  footage. Likely contributing factor, not yet confirmed: both arms'
  own motion gate is pinned to the edge of its Runnells search grid
  (`stability_resid_max=9.0` of `[0.8,9.0]`, `stability_dirstab_
  max=0.9` of `[0.1,0.9]`) — a gate that already wants to be nearly
  disabled on the shoot it was fitted on is a weak bet to transfer.
- 2026-09-02 — **Widened both grid-edge-pinned grids: neither threshold
  wants to be disabled, ruling out that theory — but the wider grid's
  tie-broken pick is measurably worse on real footage.** Extended
  `stability_resid_max`'s grid to 35.0 (past this clip's own observed
  max of 21.34) and `stability_dirstab_max`'s to its true 1.0 ceiling;
  re-fit on Runnells. Both land on genuine interior optima (18.0, 0.95),
  no longer flagged by the grid-edge alarm, with held-out metrics on
  Runnells essentially unchanged from the narrower grid. Re-scored
  against Historic Valley Junction: `resid_only`'s new value produces
  IDENTICAL output to the old one (both already far above the drone
  footage's own motion scale). `dirstab_only`'s new value (0.95) is
  WORSE — 6 predicted segments (granularity_ratio 0.231) vs the old
  0.9's 9 (0.346) — a real regression from a change that is a dead tie
  on the fitting clip. Concrete proof that fitting on one clip cannot
  see this kind of difference. Not shipped: the original
  `runnells_fit_dirstab/params.json` (0.9) stays the record; the
  widened-grid result is kept alongside at `runnells_fit_dirstab_
  widened/` as a documented alternative, not a silent regression. Suite
  223 passed / 1 skipped, re-verified after the grid change.

- 2026-09-02 — **Agent-proficiency work (outside this repo's code).** Ryan
  supplied four talks/clips as resources; consumed in full. Acted on them:
  global `~/.claude/CLAUDE.md` gained four always-on working rules; the
  existing `agent-guardrails` skill gained the two lessons this session
  taught (slice by outcome not component; documentation is not progress).
  Two new skills built and verified: `verified-quotes` (machine-checks
  transcript quotes against source SRTs — port proven faithful by A/B against
  the original script on identical input) and `footage-analysis` (cut-rhythm
  measurement plus the FCP7/xmeml parsing gotchas and the granularity blind
  spot from this week). In THIS repo: required reading cut from 2,258 lines
  to 179 (progressive disclosure table), and rule 5 replaced — it still said
  "no code yet, we are in planning."

- 2026-09-02 — **`posthouse/moments.py`: transcript query to verified moments to
  Premiere sequence.** The first slice that joins the two halves. Loads 270
  Runnells Whisper transcripts (word-level timings), resolves 268 to real media,
  ranks segments by IDF-weighted term overlap, machine-verifies every quote via
  the `verified-quotes` skill, and emits the coldfootage segments contract plus
  an FCP7 XML. 20 new tests; suite 243 passed / 1 skipped. Measured: retrieval
  recall@1 40.5%, @5 66.7%, @10 73.8% against 42 gold-chunk `why` descriptions;
  XML round-trips through `parse_answer_key_xml` to the exact input ranges;
  0 of 40 returned moments overlapped a Whisper hallucination loop. Two bugs
  found by the verifier on the first real run and fixed with regressions
  (Frankenstein quotes from merged non-adjacent segments; bare timecodes
  reading as TIMECODE_MISMATCH). Known limit: 29% of this corpus is audio-only
  lav/interview material the exporter cannot place; those moments are surfaced
  and labelled, not dropped, and need `harvest/sync.py` to reach a timeline.
  **Confirmed by Ryan** — opened `~/Desktop/Moments_Demo_Runnells/moments.xml`,
  checked the four kitchen-cabinet moments against the footage himself: "They
  are about the cabinets." First slice in this project to be verified working
  end to end by the person who has to trust it, not just by tests.
- 2026-09-02 — **Agent Studio doctrine ported to skills; `studio.py` left dead.**
  Four new global skills (`soldfast-content-funnels`, `longform-story-craft`,
  `footage-assembly-method`, `hook-writing`) consolidated from 15+ scattered
  files, plus `loop_detector.py` added to `verified-quotes` (import fixed, and
  it found one Runnells transcript that is 91% hallucination loops). Agent
  Studio itself untouched and read-only throughout.
- 2026-09-03 — **Task 1.0: PreCut forked into this repo at `app/`, confirmed
  running by Ryan.** Architecture corrected first (see ROADMAP Decision Log)
  — one app, not two: Ryan does not want a separate new app calling PreCut,
  he wants PreCut's own shell absorbed and extended. Copied
  `~/precut-checkout` (source only, no git history) into `app/`; changed
  `productName`/`identifier`/window title to "Post House" /
  `com.pierce.posthouse` so it can't collide with the real installed
  PreCut.app; found and fixed a real data-safety issue along the way — the
  Application Support directory was hardcoded to "PreCut" in three files
  (`project.py`, `setup_helper.py`, `settings.py`), which would have meant
  sharing live settings.json and the project registry with Ryan's
  production app on first run. `npm install` clean; `npm run tauri dev`
  compiled 367 crates with zero errors on this checkout's first-ever build;
  window title confirmed "Post House" via macOS's accessibility API; the
  fork's Python backend and Rust shell run as processes fully separate from
  the real PreCut.app, verified running side by side with no conflict.
  **Ryan's sign-off**: "Ok the app works like regular precut." *(eb6d42c.)*
- 2026-09-03 — **Task 1.1: Project Manager tab built, run end to end on a
  real folder, and taken through three rounds of Ryan's direct feedback.**
  `PMTab.jsx` reuses `DropZone.jsx`'s existing drag-and-drop pattern for
  A-roll/B-roll/source-audio/assets, adds a dual-use checklist for A-roll
  that also serves as B-roll, and a new `organize_project` backend command
  calls the existing, already-tested `projectmanager.organize_project()`
  directly. Three real bugs/mismatches found and fixed from Ryan's actual
  runs: (1) single-folder-plus-dropdown UI didn't match how a real project
  folder holds separately-kinded subfolders — rebuilt around drag-and-drop
  per kind; (2) `brandbrief.py`'s asset scan crashed on a macOS AppleDouble
  sidecar (`._SF-Main-RE-light.png` on external volume RDOSS_2025) because
  it lacked the dotfile filter `projectmanager.py` already used for footage
  census — fixed, regression-tested (confirmed to fail without the fix via
  `git stash`); (3) dragging the same folder into both A-roll and B-roll
  correctly tripped the manifest's kind-conflict rule but with an
  unexplained assertion error — added client-side duplicate-blocking with a
  message pointing at the dual-use checkbox, the contract-correct way to
  express "same footage, both uses." Ryan then asked for a naming change
  (`DEFAULT_ASSETS_SUBDIR` "Brand Assets" → "Company Branding", matched in
  `projectmanager.py`, `posthouse/README.md`, and both contract docs) —
  done, tested (224 passed / 1 skipped, non-tier2), and confirmed live in
  the running app. Suite and commit: *(20682fb.)*
  **Signed off by Ryan, 2026-09-03**: "I'm good with it." Task 1.1 is
  complete.
- 2026-09-03 — **Confirmed (not assumed): PreCut does NOT interpret B-roll
  footage to a different/preselected frame rate than A-roll for dual-use
  sources.** Ryan's belief going in: "the dual use should remember that
  B-roll needs to be interpreted to the preselected framerate and the a
  roll will not (i believe precut does this already but confirm)." Read
  `precut_pipeline/multi_exporter.py` and `story_assembler.py` in full.
  Findings, cited: every clip (A-roll or B-roll) is probed for its own
  native fps via `_safe_probe()`/`detect_frame_rate()`
  (`multi_exporter.py:379-508`), and both `_build_aroll_master_for_path`
  (`:1358`) and `_build_broll_master_for_entry` (`:1433`) declare that
  clip's own native rate as its FCPXML master rate — same code path, no
  kind-based branch. The **sequence** frame rate is set from A-roll's
  native fps (`story_assembler.py:261-263`, "A-roll native dims
  fallback"), not from B-roll, and there is no retime/conform/speed-change
  logic anywhere in either file for either roll kind — `detect_frame_rate`
  (`exporter.py:48-73`) only snaps a measured fps to the nearest standard
  rate, identically for any clip. Separately: in the current pipeline
  B-roll isn't even placed on the timeline as real clips —
  `story_assembler.py`'s `CutList.broll_track` is hardcoded to `[]`
  ("Markers-only; V2 stays omitted per Drop 3.7+"), so even a working
  version of the behavior Ryan described wouldn't currently reach an
  actual B-roll clip. **Conclusion: this is a real gap, not something
  PreCut already does.** Ryan then gave the concrete spec for it in the
  same message as Task 1.1's sign-off (see ROADMAP Decision Log,
  2026-09-03): B-roll conforms to the export frame rate, A-roll never
  does, and a dual-use source needs to exist as **two separate items in
  Premiere's Project panel** (one native, one conformed) — not a shared
  master clip. Captured for Phase 4 (Assistant Editor); not scheduled,
  not started.

## Next (in order)

Per `CLAUDE.md` §8 (one role in flight at a time), only the next task is
listed. Later tasks exist in `docs/REQUIREMENTS.md`'s companion plan but are
not written here as committed work until this one is signed off — writing
them in now would be exactly the unearned-Done-adjacent overclaim §4 warns
against, just shifted to "Next."

Task 1.0 is signed off by Ryan (see § Done, 2026-09-03). Task 1.1 was
signed off once, then substantially rebuilt across two more rounds of
Ryan's direct use and feedback (see § In progress) — those rebuilds
absorbed what was tracked here as a separate "Task 2.1" (Assistant
Editor sync review) entirely into the same tab, per Ryan's own
conclusion that it was never distinct AE work. There is no separate
Task 2.1 any more.

1. **Get Ryan's sign-off on the merged Project tab**, on a real project
   (Runnells Day 1, or a real dual-use shoot) — confirm Organize starts
   processing automatically, a dual-use clip actually gets tagged as
   B-roll, and the sync review (moved from the old AE tab) still works.
   Only after that does the next real Assistant Editor task get defined
   and written in here — likely the first AE work that's genuinely
   distinct from Project Manager responsibility: acting on the reviewed
   sync (e.g. writing it into an export-ready sequence), not just
   displaying it.

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
