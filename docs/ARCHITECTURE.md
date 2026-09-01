# ARCHITECTURE.md — The Post House system

Owner: Lead Architect. Describes the system we are building across repos.
PreCut's internal architecture is documented in its own repo
(`precut/ARCHITECTURE.md`); this file covers the layer *around* it.

## The three layers

```
┌────────────────────────────────────────────────────────────────┐
│ LAYER 3 — Orchestration (Claude Code)                          │
│   The engineering team (docs/TEAM.md) that builds and runs     │
│   the post-house roles. Coordination state lives in this repo. │
├────────────────────────────────────────────────────────────────┤
│ LAYER 2 — The agent layer ("the staff")                        │
│   Post-house role skills: cull, organize, story, music, audio, │
│   color-QC. Each is a CLIENT of PreCut: reads its artifacts,   │
│   writes new artifacts, emits sequences through its exporter.  │
│   No skill modifies the PreCut app.                            │
├────────────────────────────────────────────────────────────────┤
│ LAYER 1 — PreCut (shipped, protected)                          │
│   Tauri/React app + Python backend on Ryan's Mac.              │
│   Ingest, proxies, Whisper, Claude vision tagging, CLIP index, │
│   lav sync, story angles, FCP7 XML export → Premiere Pro.      │
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

## Integration points with PreCut (the only two doors)

1. **The JSON-lines backend protocol** — stdin/stdout commands
   (`create_project`, `add_source`, `run_pipeline`, `story_generate`,
   `export_timelines`) against `python_backend/backend.py`, plus
   `precut_pipeline/cli.py`. The agent layer drives PreCut exclusively
   through this door for actions.
2. **The on-disk project artifacts** — read (and additively extend)
   under `~/Library/Application Support/PreCut/projects/<name>/`:
   `project.json`, `transcripts/`, `broll_index/precut.db` (+ LanceDB
   vectors), `plans/`, exported XML. The agent layer's own artifacts
   (below) live alongside them, in new files — never rewriting PreCut's.

Anything that needs a third door (a schema change, a new backend
command) is a PreCut change: it waits for the Phase 0 safety net and goes
through the protected-repo process.

## Artifact contracts (the handoffs between roles)

The post-house roles communicate through typed artifacts, exactly as the
human house communicates through deliverables. Contracts are versioned
(`"contract_version"` field) and additive-only, mirroring PreCut's own
additive-only DB migration rule.

| Artifact | Producer → Consumer | Form | Status |
| --- | --- | --- | --- |
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

- **Fixture project**: tiny synthetic/trimmed clips covering the failure
  taxonomy (stable/shaky/blurred/over-under-exposed, lav pair, speech).
  Runs anywhere, including CI and cloud sessions.
- **Golden-master gate**: pipeline → XML on the fixture, byte-diffed
  against a blessed snapshot. Guards PreCut's six hard-won FCP7 quirks.
  Required green before and after any `precut` commit.
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
