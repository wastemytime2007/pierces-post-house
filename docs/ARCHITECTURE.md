# ARCHITECTURE.md — The Post House system

Owner: Lead Architect. Describes the system we are building across repos.
PreCut's internal architecture is documented in its own repo
(`precut/ARCHITECTURE.md`); this file covers the layer *around* it.

## The product and the three layers

The end product is **a new application** (this repo) whose UX walks a
project through the post-house roles with visible handoffs: Ryan briefs
the Project Manager at intake, the PM hands off to the Assistant Editor,
and so on to one Premiere-ready XML, with supervisor checkpoints
between stations. PreCut is the **component donor**: its solved
capabilities are harvested (wrapped, pinned, gated by the safety net),
never rebuilt and never modified in place.

```
┌────────────────────────────────────────────────────────────────┐
│ LAYER 3 — Orchestration (Claude Code)                          │
│   The engineering team (docs/TEAM.md) that builds the house.   │
│   Coordination state lives in this repo.                       │
├────────────────────────────────────────────────────────────────┤
│ LAYER 2 — Pierce's Post House (the new app: roles + shell)     │
│   Post-house roles: Project Manager (manifest + organization), │
│   Assistant Editor (sync, cull, grouping, flagging), Creative  │
│   Editor, Audio Designer, Colorist-QC — composed from          │
│   harvested PreCut skills + new code, headless first; the      │
│   role-pipeline shell arrives in Phase 5.                      │
├────────────────────────────────────────────────────────────────┤
│ LAYER 1 — PreCut (shipped, protected, DONOR)                   │
│   Tauri/React app + Python backend on Ryan's Mac.              │
│   Ingest, proxies, Whisper, Claude vision tagging, CLIP index, │
│   lav sync, story angles, FCP7 XML export → Premiere Pro.      │
│   Stays working and untouched until superseded role by role.   │
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

## Integration points with PreCut (three doors)

1. **The JSON-lines backend protocol** — stdin/stdout commands
   (`create_project`, `add_source`, `run_pipeline`, `story_generate`,
   `export_timelines`) against `python_backend/backend.py`, plus
   `precut_pipeline/cli.py`.
   **Protocol reality any driver must respect:** job commands are
   fire-and-forget (submitted to a thread pool, no ack); a worker
   failure emits `{"type":"error"}` with **no** completion event, and
   some errors carry no `job_id` at all — so a driver must mint and
   pass its own `job_id` on every job command, pair events itself,
   enforce a wall-clock timeout instead of waiting forever, and confirm
   output files exist on disk before `shutdown` (which abandons
   in-flight work). The `ready` handshake reports the backend version
   string (`0.4.43-…`), not the app version (`1.0.0-beta.3`) — both are
   correct, a known naming split in PreCut's PROVENANCE.md.
2. **The on-disk project artifacts** — read (and additively extend) a
   project's directory: `project.json`, `transcripts/`,
   `broll_index/precut.db` (+ LanceDB vectors), `plans/`,
   `audio_index/`, `exports/`. Projects are found via PreCut's
   `known_projects` registry — since Drop 4.16 a project can live at an
   arbitrary `root_dir`, so never glob the default
   `~/Library/Application Support/PreCut/projects/` and assume that's
   all of them. The agent layer's own artifacts live alongside, in new
   files — never rewriting PreCut's.
3. **`precut_pipeline` imported as a Python library**, pinned to a
   tagged PreCut commit. Needed because door 1 cannot express a
   cold-footage timeline: `export_timelines` only builds sequences from
   `plans/` ideas (running the matcher internally) or `library_only`
   mode, and the `CutList` model has no representation for arbitrary
   source in/out segments without an A-roll transcript spine. This door
   makes the exporter chain a de-facto public API — which is exactly
   why the Phase 0 safety net covers that surface.

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
