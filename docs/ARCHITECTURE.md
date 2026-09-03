# ARCHITECTURE.md — The Post House system

Owner: Lead Architect. Describes the system we are building across repos.
PreCut's internal architecture is documented in its own repo
(`precut/ARCHITECTURE.md`); this file covers the layer *around* it.

## The product: one app, forked from PreCut

**Corrected 2026-09-03** — this section previously described a separate new
app calling PreCut as an external dependency, headless until "Phase 5."
Ryan corrected that directly: *"I don't want to have to run two apps
separately... effectively replace precut by absorbing all of its code and
functionality,"* and each role must be verifiable *"in something that I can
interact with like an app vs. terminal commands."* Full record:
`ROADMAP.md` Decision Log, 2026-09-03.

The end product is **one application**, living at `app/` in this repo: a
fork of PreCut's own Tauri/React shell (same window, same Rust↔Python
bridge, same install flow), copied here and extended with the post-house
roles as new screens — Project Manager (manifest + organization), Assistant
Editor (sync, cull, grouping, flagging), Creative Editor, Colorist,
Audio Designer — with supervisor checkpoints between stations. There is
never a separate PreCut app running alongside it.

**PreCut's own GitHub repo (`~/precut-checkout`) is the protected donor**:
read from and copied from, never committed to, never modified, for as long
as it remains Ryan's separate production tool. "Harvested, never rebuilt"
means the working code is copied into `app/` and extended, not
reimplemented from scratch and not called as a permanently-external
dependency.

```
┌────────────────────────────────────────────────────────────────┐
│ LAYER 2 — Orchestration (Claude Code)                          │
│   The engineering team (docs/TEAM.md) that builds the house.   │
│   Coordination state lives in this repo.                       │
├────────────────────────────────────────────────────────────────┤
│ LAYER 1 — Pierce's Post House (`app/` — ONE application)       │
│   Forked from PreCut's Tauri/React shell + Python backend.     │
│   Ships PreCut's own capabilities as-is (ingest, proxies,      │
│   Whisper, Claude vision tagging, CLIP index, lav sync, story  │
│   angles, FCP7 XML export) PLUS the post-house roles as new    │
│   screens, added one at a time, each proven on real material   │
│   before the next starts (CLAUDE.md §7-8).                     │
│   PreCut's own repo (`~/precut-checkout`) is read from and     │
│   copied from during this build; never committed to.           │
└────────────────────────────────────────────────────────────────┘
```

## Where things execute

| Work | Where | Why |
| --- | --- | --- |
| Planning, code, tests on fixtures | Claude Code sessions (cloud OK) | No real media needed |
| Anything touching real footage | **Ryan's Mac only** | Media never leaves his machine; PreCut and ffmpeg live there |
| Product AI calls (tagging, story) | Claude API from PreCut backend | Existing, API-key based (per PreCut DECISIONS.md) |

This split is structural: cloud sessions can never see Ryan's drives, so
every media-touching skill must be runnable headless on his machine, and
every cloud-buildable part (logic, parsers, XML writers, scorers) must be
testable on fixtures without real footage.

## Reaching PreCut's capabilities (corrected 2026-09-03)

Previously described as three "doors" between two separately-running
processes. That framing assumed a separate app talking to a live PreCut
instance; the fork model (above) makes most of it internal instead. Kept
here because the mechanics are still real and still matter:

1. **The JSON-lines protocol is now internal, not cross-app.** The forked
   app's own Rust shell spawns its own copy of `python_backend/backend.py`
   (`create_project`, `add_source`, `run_pipeline`, `story_generate`,
   `export_timelines`, plus new post-house commands added alongside them)
   — the same protocol PreCut used, just no longer talking to a separate
   process. The protocol's real behavior still applies wherever it's
   driven programmatically: job commands are fire-and-forget (submitted to
   a thread pool, no ack); a worker failure emits `{"type":"error"}` with
   **no** completion event, and some errors carry no `job_id` at all — so
   a driver must mint and pass its own `job_id`, pair events itself,
   enforce a wall-clock timeout, and confirm output files exist before
   `shutdown` (which abandons in-flight work).
2. **On-disk project artifacts, for compatibility with existing PreCut
   projects.** Ryan's already-created projects (`project.json`,
   `transcripts/`, `broll_index/precut.db` + LanceDB, `plans/`,
   `audio_index/`, `exports/`) should keep working when opened in the
   forked app — read (and additively extend) them in place, never assume
   they live at the default
   `~/Library/Application Support/PreCut/projects/` path (a project can
   live at an arbitrary `root_dir` since Drop 4.16). New post-house
   artifacts (manifest.json, culls.json, etc.) live alongside, in new
   files — never rewriting PreCut's own.
3. **`precut_pipeline` as the fork's own vendored code**, not an external
   pinned dependency. Copied into `app/python_backend/` as part of the
   fork (Task 1.0), then extended in place — `posthouse/`'s existing
   modules (`projectmanager.py`, `manifest.py`, `coldfootage.py`, etc.)
   get reached from the same Python process going forward, no
   cross-checkout bridge required once absorbed. `coldfootage.py` exists
   because PreCut's own `CutList`/`export_timelines` has no representation
   for arbitrary source in/out segments without an A-roll transcript spine
   — a real, still-true gap, now closed by code living in the same app
   rather than a separate one.

Anything beyond these three (a schema change, a new backend command) is
a PreCut change: it waits for the Phase 0 safety net and goes through
the protected-repo process.

## Artifact contracts (the handoffs between roles)

The post-house roles communicate through typed artifacts, exactly as the
human house communicates through deliverables. Contracts are versioned
(`"contract_version"` field) and additive-only, mirroring PreCut's own
additive-only DB migration rule.

| Artifact | Producer → Consumer | Form | Status |
| --- | --- | --- | --- |
| **Project Manifest** | PM role → every later role | `manifest.json` at project root — identity/provenance (incl. PreCut pin), client + brand (with Brand Brief artifacts), sources with frozen IDs (`<kind>-<slug>-<NN>`) and `dual_use` flags, delivery targets from PreCut's preset vocabulary, append-only handoff log | **Drafted** — `docs/contracts/PROJECT_MANIFEST.md`, awaiting Ryan's ratification (6 open questions) |
| Footage index | PreCut pipeline → everyone | SQLite + LanceDB (exists) | Shipped |
| Transcript | PreCut (Whisper) → story/cull | JSON per A-roll (exists) | Shipped |
| Story plan | PreCut planner → assembler | JSON in `plans/` (exists) | Shipped |
| **`culls.json`** | Cull skill → cold-footage export, benchmark scorer | Per-clip usable segments, each with in/out, handles, per-rule scores, rejection reasons | To spec in Phase 3 |
| **`groups.json`** | Organizer → sequence builder | Subject clusters over indexed clips | Phase 4 |
| **Benchmark scores** | Scoring harness → Decision Log | JSON + committed report | Phase 2 |
| **Music/SFX candidates** | Music skill → creative editor | Ranked local-library tracks with tone rationale | Phase 5 |
| **Timeline** | Exporter → Premiere | FCP7 XML via PreCut's existing writer | Shipped |
| **Revision notes** | Supervisor → assembler | Structured operations (tighten/swap/reorder), schema TBD Phase 5 | Phase 5 |

Detailed field-level schemas are written in the phase that first
produces the artifact, reviewed by the Architect, and recorded here.

## Testing architecture

Two tiers, because the exporter is testable anywhere but the AI pipeline
is not (multi-GB models, MPS-vs-CPU embedding drift, live API calls):

- **Tier 1 — hermetic exporter gate (CI/cloud/anywhere).** Fixture media
  (tiny ffmpeg-synthesized clips, committed for hermeticity) + a
  hand-built synthetic index and `CutList` objects drive the exporter
  chain directly — no Whisper, no CLIP, no API. The XML writer is
  nondeterministic by construction (random UUIDs per clip, absolute
  paths in `file://` URLs including the export output dir, set-order-
  driven ID allocation, ffprobe-derived values), so the golden master is
  a **canonicalizing comparison** — UUIDs, roots, and encoded URLs
  neutralized, `PYTHONHASHSEED` pinned, ffmpeg/ffprobe version recorded
  in a fixture manifest — never a byte-diff. The five FCP7 **XML**
  quirks from PreCut's DECISIONS.md are each covered by an explicit
  quirk → fixture asset → assertion row (e.g. a mixed-case-extension
  original for the case-probing quirk). Quirk 6 (additive-only DB
  migrations) is not observable in XML and gets its own DB test in
  Tier 2. Required green before and after any `precut` commit.
- **Tier 2 — full-pipeline gate (Ryan's Mac only). Shipped 2026-09-01,
  verified live.** The real venv (`~/precut-venv-fresh`), auto-detected
  by `run_safety_net.sh`. Two parts are implemented and green: the full
  35-module import gate (`test_import_gate.py`, parametrized across
  every `precut_pipeline` + `python_backend` module, ML-deps-gated so
  it self-skips off-Mac) and the additive-only DB-migration test
  (`test_db_migrations.py` — old-schema fixture DB → `Database.__init__`
  → no row loss, idempotent re-open, new columns writable). Both are
  covered by the same sabotage discipline as Tier 1. **Still open:**
  real-footage audio-sync coverage — synthetic audio can't clear the
  MFCC score threshold, so Tier 1 keeps sync off rather than silently
  blessing an un-synced XML, and this needs genuine correlated
  dual-source audio to exercise for real. Anything model-output-shaped
  here is asserted on invariants, never tree-diffed — model output is
  not golden-masterable.
- **Benchmark project**: one real past project + Ryan's delivered edit
  as answer key (plus a one-time "usable but unused" marking pass).
  Lives only on Ryan's Mac; scoring harness runs there headless and
  commits only the *scores*, never the media.

## Settled constraints inherited from PreCut

FCP7 XML is the delivery path · no CEP/UXP panel code · markers replace
B-roll clips until matching precision is proven · API key (not OAuth)
for product AI calls · deterministic beats generative wherever ffmpeg +
numpy suffice · DB migrations additive-only.

## Open architecture questions (tracked, not blocking)

1. `culls.json` field-level schema — Phase 3, Architect + QA.
2. Revision-notes operation vocabulary — Phase 5.
3. Artlist local-library index shape (what metadata is capturable at
   download time) — investigate at Phase 5 start.
4. Color delivery mechanism (Premiere automation vs Resolve round-trip)
   — deferred past Phase 7 v1 by decision.
