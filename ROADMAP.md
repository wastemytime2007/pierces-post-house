# Pierce's Post House — Roadmap

An AI post-production house, delivered as a **new application** whose
experience mirrors how a real post house runs: the user (Ryan — the
supervisor, and the client) briefs the **Project Manager**, watches the
PM hand off to the **Assistant Editor**, and so on down the chain, with
supervisor checkpoints between stations.

**PreCut is the component donor, not the permanent foundation.** It has
already crossed hurdles we will not re-cross — the FCP7 XML quirks, sync
thresholds, proxy conventions, tagging vocabulary, additive DB
migrations, the Tauri/Python app plumbing, macOS distribution. Those
solutions get **harvested** into the new app, not rebuilt. PreCut itself
stays untouched and working as Ryan's production tool until the new app
supersedes it role by role.

This document is the working plan. It gets edited as decisions are made —
settled calls move to the Decision Log at the bottom, in the same commit
that settles them.

Related repos:

- `wastemytime2007/pierces-post-house` (this repo) — the new app, its
  safety net, and all coordination docs.
- `wastemytime2007/precut` — the shipped donor app (1.0.0-beta.3).
  **Protected**: never modified, harvested from via the safety net.
- `wastemytime2007/precut-premiere-extension` — abandoned CEP/UXP panel.
  Reference only.

---

## 1. The operating model

A real post house is a pipeline of specialists, each of whom receives
organized work from the previous station and hands better-organized work
to the next:

```
Production (footage arrives)
   └─> Project Manager      — intake with the user: folder kinds, client
        │                     & brand assets, dual-use flags; organize,
        │                     validate, emit the Project Manifest
        └─> Assistant Editor — sync audio, cull to usable segments,
             │                 build cold-footage timelines, group by subject,
             │                 flag/color-code transcript by storyline
             └─> Creative Editor — pick the story, assemble the cut,
                  │                choose music, place SFX  (95% cut)
                  └─> Supervisor review loop (Ryan) ──> picture lock
                       └─> Colorist       — exposure fixes + look
                            └─> Audio Designer — levels, mix, added design
                                 └─> Supervisor approvals ──> final export
```

**The app's UX is this diagram.** Ryan's interaction is front-loaded at
the PM station (the intake conversation) and at review checkpoints;
between those, the app shows the handoffs happening. Each role is built
and proven headless first; the shell that makes the handoffs visible is
its own phase (Phase 5), earned by working roles.

### The two honest asymmetries in this model

1. **Everything up to picture lock fits the XML delivery model** (write
   artifacts + one XML, editor opens Premiere). **Color and final audio
   mix do not** — they happen inside the NLE after picture lock, and
   FCP7 XML cannot carry a Lumetri grade (audio clip gain can ride in
   XML; color cannot). The Colorist and Audio Designer therefore need
   different delivery mechanics and are deliberately scheduled last.
2. **Culling is two different jobs wearing one name.** Technical culling
   (shake, blur, exposure, clipped audio, accidental recordings) is
   deterministic and measurable. Editorial culling (false starts,
   repeated takes, "the energy is off") is taste. Phase 4 builds the
   first and does not pretend to do the second.

---

## 2. Ground rules

These are constraints, not suggestions. Changing one is a Decision Log
entry.

1. **Harvest, don't rebuild — and don't touch the donor.** No capability
   PreCut already solved gets rewritten from scratch. Capabilities move
   into the new app by wrapping `precut_pipeline` (door 3 in
   ARCHITECTURE) behind the safety net; a harvested capability must pass
   the same gate before it replaces anything. PreCut itself stays
   unmodified and working for real jobs throughout the transition —
   the new app replaces it role by role, never big-bang.
2. **No change to the `precut` repo without the safety net green** before
   and after. (Its own history — a silent regression shipped for a
   month — is why.)
3. **Deterministic before generative.** If ffmpeg + numpy can compute it,
   a model does not get asked to guess it.
4. **Every skill ships with its measurement.** A skill without a score
   against the benchmark project is not done, no matter how good its
   demo looks.
5. **Suggestions must be earned into placements.** PreCut demoted
   auto-placed B-roll to markers because matching wasn't trustworthy.
   Any move from "suggest" to "place on the timeline" is gated on a
   measured precision number, decided per skill in advance.
6. **The AI does the comprehension. The editor does the editing.**
   Every station prepares decisions for Ryan; no station hides them.
7. **Roles before shell.** The app's role-pipeline UX is the product,
   but every role is built, measured, and trusted headless before it
   gets its place in the shell (Phase 5). Interface work never precedes
   a working role.
8. **Every working session ends with something Ryan can test on real
   footage in under five minutes.**

---

## 3. Role → skill map, with honest feasibility grades

| Role | What it does here | Already in PreCut? | Feasibility | Phase |
| --- | --- | --- | --- | --- |
| Project Manager | Intake conversation (folder kinds, client/brand info, dual-use flags), organize drive, stage brand assets, validate media, emit Project Manifest | Partially (drop-zone folder kinds, camera inference, bin layout, Default Includes ≈ brand assets, unsupported-file warnings) | **A** — mostly harvest + a defined contract | 2 |
| Assistant Editor: sync | "All Footage Synced" sequence from lav sync | Yes (audio-offset-finder MFCC cross-correlation, score-thresholded) | **A** — repackage; must define handling of below-threshold pairs | 4 |
| Assistant Editor: technical cull | Usable in/out segments → "Cold Footage" timeline | No | **B** — deterministic and measurable, but the motion pipeline is new code (see §4) | **4 (flagship)** |
| Assistant Editor: subject grouping | Per-subject cold-footage sequences | Partially (CLIP tags, theme categories) | **B+** — clustering on existing index | 4 |
| Assistant Editor: transcript flagging | Color-coded storyline ranges visible on a timeline | Partially (story angles find ranges) | **B+** — new rendering of existing output | 4 |
| Creative Editor: story + assembly | Selects → assembled cut from a brief | Yes, v1 (angles → ranges → sequence + markers) | **B** — improve, then measure | 6 |
| Creative Editor: music | Tone-matched music from the Artlist library | No | **B−** — see Artlist reality below | 6 |
| Creative Editor: SFX | SFX placement suggestions | No (SFX bin exists, empty logic) | **B−** | 6 |
| Creative Editor: B-roll placement | Real clips on V2 instead of markers | Demoted by design | **Gated** on benchmark precision | 6 |
| Audio Designer | Loudness analysis → clip gain in XML + report | No | **B** — measurement solid, application partial | 7 |
| Colorist | Exposure/contrast QC report; a real grade | No | **C** — QC report yes; grading needs different tech | 8 |
| Supervisor loop | Structured notes → revised cut | No | **B** — protocol design, not ML | 6+ |

**The Artlist reality:** Artlist has no public search/download API. The
music skill works against a **local library** of tracks Ryan has
downloaded under his subscription, indexed locally (BPM, energy,
instrumentation, mood). It searches that index; it does not browse
Artlist — so it only knows tracks Ryan has pulled.

**The color reality:** a real grade cannot ride in FCP7 XML. Phase 8 v1
is a *colorist's assistant*: per-shot technical QC delivered as a report
plus timeline markers. Automated grading — if ever — means Premiere
automation or a Resolve round-trip, decided then, not assumed now.

---

## 4. The Assistant Editor's cull, specified

Phase 4 is the flagship because it is the highest-value role buildable
with deterministic tools, and it is the job Ryan described
frame-by-frame. For every source clip (e.g. `0001.mov`):

1. Scan the full duration. A clip yields **zero or many** usable segments
   — one clip is not one segment.
2. A segment **opens** when the camera settles: shake ends and the
   shooter is intentionally holding the shot.
3. A segment **closes** just before the next disturbance: shake resumes,
   the shooter recomposes to a new shot, focus is lost, or the recording
   ends.
4. Rejected outright: accidental record-taps, unusably short fragments,
   sustained blur, gross exposure faults, (for synced A-roll) clipped or
   dead audio.
5. Every accepted segment is placed, in source order, on a **Cold
   Footage** sequence.

Detection stack (all local, all deterministic — and all **new code**):
per-frame global-motion estimation for shake and pan/tilt-vs-recompose
classification (ffmpeg `vidstabdetect` transform logs or dense optical
flow), Laplacian variance for blur, histogram stats for exposure, audio
peak/RMS scan for clipped or dead audio, minimum-duration and
settle-time thresholds. Constraints learned in review:

- **Explicit non-goal: reusing `motion_analyzer.py`.** It samples 6
  frames per clip at 160px for one whole-clip tag via brightness-
  centroid drift — it cannot locate a settle point in time and cannot
  tell a pan from shake. The cull needs dense temporal analysis; there
  is nothing to extend.
- **Metrics run against originals (or a dedicated analysis-grade
  decode), never PreCut's CRF-28 proxies.** Lossy proxies destroy
  exactly the signals being measured: adaptive compression turns the
  blur score into a bitrate meter, 8-bit re-encodes manufacture or hide
  exposure clipping, and AAC does not preserve audio sample peaks.
  Phase 4 includes a fixture gate asserting proxy-vs-source metric
  agreement before any proxy shortcut is trusted.

Craft details that make the output professional rather than merely
correct:

- **Handles.** In/out points are biased conservative, but each segment
  carries trim handles (default ±1s where the source allows) so the
  lead editor can slip and trim.
- **A-roll is culled differently from B-roll.** B-roll culls on
  stability and image quality. A-roll (talking humans) culls on audio
  and framing only — a locked-off interview shot with "boring" visuals
  is not a defect, and content judgments (false starts, repeated takes)
  are Phase 6+ territory via the transcript.
- **Dual-use footage is culled twice.** Sources flagged `dual_use` in
  the Project Manifest (A-roll where the subject keeps talking while
  the shooter grabs scene coverage) run under BOTH rulesets — once for
  narrative usability, once for visual usability — and their segments
  appear in both cold-footage contexts. One clip, many segments,
  possibly two lives.

Output artifacts: `culls.json` (per-clip segment list with per-rule
scores and reasons — inspectable, diffable), plus the Cold Footage
sequence in the exported XML. Every rejection carries its reason;
nothing is silently dropped.

---

## 5. Measurement

### The benchmark project

One finished past project, nominated by Ryan: raw footage in, plus his
actual delivered edit as the **answer key**. Nothing generative gets
tuned without it.

- **Cull scoring:** precision (junk that got through) and recall (usable
  material missed) against the ranges in Ryan's edit plus a one-time
  "usable but unused" marking pass. Recall matters more: an assistant
  editor who hides good footage is worse than one who lets a little
  shake through.
- **Grouping scoring:** do per-subject sequences match how Ryan would
  bin the material (~30-minute spot-check labeling session).
- **Matching/B-roll scoring:** of the B-roll the skill would place, how
  much matches what Ryan actually used. This number decides whether
  markers ever become clips (Rule 5).
- **Story scoring:** qualitative — Ryan rates generated angles against
  the story he actually told.

### Definition of done, per skill

A skill is done when: (1) it runs headless on the fixture and the
benchmark; (2) its score is recorded in this repo; (3) Ryan has used its
output on at least one real project and it saved him time; (4) its
failure modes are written down. Then, and only then, does the next role
get built.

---

## 6. Phases

Each phase has an exit criterion. A phase without its exit criterion met
does not hand off to the next — same rule as the house itself.

### Phase 0 — Safety net ✅ Tier 1 shipped 2026-09-01
`safety_net/` in this repo, running against a PreCut checkout via
`PRECUT_ROOT`. Hermetic exporter gate: committed fixture media,
hand-built synthetic index and CutLists, canonicalized golden master
(never byte-diff), FCP7 quirk assertions 1–5, stdlib import gate.
Verified 16 passed / 2 skipped; sabotage check caught a planted
regression. **Doubly important after the pivot: this is the transplant
insurance for every capability harvested out of PreCut.**
Remaining (Tier 2, Ryan's Mac): full 35-module import gate, DB-migration
test, audio-sync coverage on real footage.
Standing rule: re-blessing the golden requires a Decision Log entry.

### Phase 1 — Harvest layer *(the donor organs, wrapped)*
Wrap PreCut's proven capabilities as standalone, importable skills the
new app composes: proxy generation, transcription, tagging + CLIP
index, audio sync, the exporter chain, Default Includes (→ brand-asset
staging), camera/source-type inference, unsupported-file warnings. Each
wrapper is runnable alone from the command line, pinned to a tagged
PreCut commit, and covered by the safety net. This phase also builds
the **cold-footage sequence builder** on the exporter chain (the
existing CutList model can't express arbitrary segments — known gap).
**Exit:** harvested exporter passes the golden master; proxy,
transcribe, and export wrappers run standalone on the fixture; a
deliberately failing wrapper surfaces as a non-zero exit, not a hang.

### Phase 2 — Project Manager *(first role of the new app)*
Two deliverables:
1. **The Project Manifest contract** — the file every later role reads.
   Client and project identity; brand assets (logos, graphics, LUTs,
   fonts — staged into the project, with the caveat that fonts can't
   ride XML into Premiere and get a separate install step); source
   folders with user-declared kinds (A-roll / B-roll / source audio /
   assets — same declaration pattern PreCut's drop zones proved);
   per-source flags including `dual_use`; delivery targets.
2. **The PM itself**, headless: an intake conversation that fills the
   manifest (user declares, PM labels and confirms), then organization
   — files placed per conventions, media validated (harvested
   unsupported-file warnings), brand assets staged, manifest emitted,
   handoff recorded. Default Includes harvests as-is for
   "files every project gets" (SFX, logos, recurring assets).
3. **The Brand Brief** — the bridge for assets Premiere can't import
   (fonts, PDFs, docs; note: plain .txt is also on the unsupported
   list). The PM *reads* the assets — font family names parsed from
   TTF/OTF name tables (deterministic), palette extracted from the
   logo, brand-guidelines PDF summarized — merges in intake answers,
   and delivers the brief three ways: `BRAND_README.txt` on disk next
   to the assets (including font-install pointers); a rendered
   brand-card PNG imported into a `Files/Brand` bin (readable in the
   source monitor — the "text file in Premiere," as an image); and
   searchable metadata via the harvested Description/Comment fields
   plus the existing frame-0 creative-brief sequence marker.
   **Co-location rule:** the brand-card PNG physically lives inside the
   staged brand-assets folder on disk (beside the README, fonts, PDFs),
   and only there — so right-click → Reveal in Finder on the card in
   Premiere opens the folder holding the non-importable assets. Bins
   are virtual, the folder is the doorway; never duplicate the card
   elsewhere. Bin naming (current "Logos" → "Brand Assets" or similar)
   is a Phase 2 back-end detail — noted, not now.
**Exit:** a real footage dump plus a ten-minute intake produces an
organized project + manifest that Phase 4 can consume blind; Ryan
approves the layout on a real project.

### Phase 3 — Benchmark
Ryan nominates the project; footage and answer key staged on his Mac;
scoring harness written against the artifacts; baseline scores for
PreCut's current matcher recorded — we know today's number before
improving anything.
**Exit:** baselines committed (scores only, never media).

### Phase 4 — Assistant Editor *(the 75% role, flagship)*
The cull as specified in §4, including dual-use double-culling. Then:
"All Footage Synced" sequence (with a settled answer for below-threshold
pairs — included-and-flagged, dropped, or surfaced; decide and log);
per-subject cold-footage sequences (clustering over the harvested index;
taxonomy widened beyond real-estate as needed); storyline color-coding
of transcript ranges on a review timeline. Consumes the Project
Manifest; produces `culls.json`, `groups.json`, and sequences.
**Exit:** cull precision/recall on the benchmark recorded; a raw dump +
manifest opens in Premiere as synced sequence + cold footage per subject
+ color-coded story timeline, zero manual prep; Ryan uses it on one real
project and it saves him time.

### Phase 5 — App shell v1 *(the house becomes visible)*
Only now, with PM and AE proven headless: the application around them.
Intake screen (the PM conversation), the visible PM → AE handoff,
progress per station, results ending in one XML. Harvest PreCut's app
plumbing where it fits (Tauri + Python IPC bridge, first-launch setup,
distribution packaging — all solved problems in the donor).
**Exit:** Ryan runs a real project start to finish in the app, no CLI.

### Phase 6 — Creative Editor
Artlist local-library indexer and tone-matched music selection; SFX
suggestion pass; story assembly quality pass (harvested story planner as
the base); B-roll clip placement **iff** the Phase 3/4 benchmark
precision clears the pre-set bar. Supervisor notes protocol: structured
revision requests ("tighten section 2 by ~30%", "swap music: too
somber") that map to re-assembly operations.
**Exit:** a brief goes in; a first cut with music and SFX candidates
comes out; one full supervisor revision round-trips.

### Phase 7 — Audio Designer
EBU R128 loudness analysis across the assembled cut; dialogue/music/SFX
level recommendations written as clip gain (and keyframes where needed)
into the XML; anomaly report (clipping, dead channels, hum).
**Exit:** an assembled cut re-exports with levels Ryan doesn't have to
touch for a review screening.

### Phase 8 — Colorist (assistant scope)
Per-shot exposure/WB/contrast QC report with timeline markers;
sequence-level consistency check. Automated grading out of scope until a
delivery mechanism is chosen (Premiere automation vs Resolve
round-trip) — separate decision.
**Exit:** QC report on the benchmark flags the shots Ryan agrees need
work, with acceptably few false alarms.

### Phase 9 — The house runs as one
The full chain with supervisor checkpoints between stations; cost and
runtime accounting per project; PreCut formally superseded when Ryan
retires it — not before.
**Exit:** one real project goes from footage dump to reviewed first cut
with Ryan touching only the intake and the checkpoints.

---

## 7. Risks and open questions

- **Two-app transition.** During Phases 1–5 PreCut remains the
  production tool while the new app grows beside it. Divergence risk is
  contained by pinning every harvest to a tagged PreCut commit and by
  PreCut being effectively frozen (protected repo).
- **Fonts.** Brand fonts cannot ride FCP7 XML into Premiere. The PM
  stages font files in the project and flags them for a one-time
  install on the edit machine; graphics with baked-in type are
  unaffected.
- **Artlist metadata capture.** How much mood/genre metadata is
  capturable at download time vs computed locally? Investigate at
  Phase 6 start, not before.
- **Runtime and cost.** §4 requires analysis against originals, not
  cheap proxies — dense per-frame motion is orders of magnitude more
  work than PreCut's 6-frames-per-clip tagging pass. Overnight-batch
  acceptable on Ryan's machine is the bar; measured in Phase 4.
- **Taxonomy width.** Theme categories are tuned for real-estate/reno
  work; Phase 4 clustering must not hard-depend on the fixed 14.
- **Whisper timing bias.** Reuse PreCut's phrase-boundary padding
  rather than re-deriving it.
- **Frontend source risk (PreCut) — largely retired.** Repo rebuilt and
  verified as 1.0.0-beta.3 (PROVENANCE.md). Residual: undocumented
  post-May-2026 UI changes were never in the repo. Matters less
  post-pivot: the new app's shell is new work regardless.
- **Answer-key survivorship.** Ground truth is what Ryan *kept*; cull
  recall needs the one-time usable-but-unused marking pass or it reads
  falsely low.

## 8. Decision Log

- **2026-08-31 — Architecture (superseded 2026-09-01, see pivot):** the
  AI team is built as *clients* of PreCut, not as changes to the app.
  The client principle survives the pivot; the "PreCut stays the app"
  assumption did not.
- **2026-08-31 — Order:** safety net → benchmark → cull before creative
  roles. (Renumbered by the pivot; the ordering principle stands.)
- **2026-08-31 — Music source:** Artlist subscription via a locally
  indexed library of downloaded tracks (no public API exists).
- **2026-08-31 — Color/audio scheduling:** post-picture-lock roles with
  different delivery mechanics; deliberately last. Colorist v1 is
  QC-report scope only.
- **2026-09-01 — Engineering team structure:** roles are hats
  instantiated per task; the repo is the team's only memory.
  Single-writer doc ownership, append-only Decision Log, subagents
  never push, Lead-owned two-strikes escalation ledger. `docs/TEAM.md`.
- **2026-09-01 — Model policy:** Fable 5 orchestration/hardest reasoning;
  Opus 5 architecture/review; Sonnet 5 default implementation; Haiku 4.5
  mechanical. Escalate after two failed attempts.
- **2026-09-01 — Governance docs** are the coordination layer; every
  session ends by updating STATUS and pushing.
- **2026-09-01 — Phase 0 green-lit by Ryan;** safety-net home is this
  repo (`safety_net/`), via `PRECUT_ROOT`.
- **2026-09-01 — Third door declared:** `precut_pipeline` importable as
  a library, pinned to a tagged PreCut commit, exporter chain treated as
  public API under the safety net.
- **2026-09-01 — Duration quirk resolved (doc-vs-code):** shipped code
  (Drop 4.30, nb_frames preferred) wins over PreCut DECISIONS.md item 5;
  that doc gets amended when push access exists. General rule:
  doc-vs-code contradictions in the protected repo resolve by amending
  the doc unless a Premiere test says otherwise.
- **2026-09-01 — Golden master is a canonicalizing comparison, never a
  byte-diff;** two-tier gate (hermetic anywhere / full pipeline on the
  Mac). Re-blessing requires a Decision Log entry.
- **2026-09-01 — Adversarial review policy:** first review returned 14
  findings, 3 blocking; all incorporated. Standing rule: plans of this
  size get an adversarial review before build.
- **2026-09-01 — Phase 0 Tier 1 shipped:** 16 passed / 2 skipped against
  precut main; sabotage check caught a planted exporter regression.
  Discoveries logged in `safety_net/README.md` (markers.py not
  stdlib-only; placeholder PNGs leak PRECUT_ROOT into XML;
  `_build_library_bin` is dead code containing the historical quirk-4
  bug).
- **2026-09-01 — THE PIVOT (Ryan):** the end product is a **new app**
  whose UX walks a project through the post-house roles with visible
  handoffs; Ryan interacts at intake and checkpoints. PreCut is the
  **component donor and reference**, not the permanent foundation: it
  stays untouched and in production use until the new app supersedes it
  role by role (Phase 9). Harvest rule: nothing PreCut solved gets
  rebuilt; capabilities move by wrapping `precut_pipeline` behind the
  safety net.
- **2026-09-01 — Brand Brief (Ryan + refinement):** non-importable brand
  assets are bridged by a PM-generated brief delivered as (a) a
  README.txt beside the assets on disk, (b) a rendered brand-card PNG
  in a `Files/Brand` bin (plain .txt cannot import into Premiere), and
  (c) searchable Description/Comment metadata + the frame-0
  creative-brief marker harvested from PreCut. The PM extracts what it
  can deterministically (font names from font files, palette from the
  logo) rather than asking the user to retype it.
- **2026-09-01 — Build order by role (Ryan):** Project Manager first,
  Assistant Editor second (the "75% of the difficulty" role). The PM's
  hard deliverable is the **Project Manifest** contract, including
  per-source `dual_use` flags — A-roll that also yields B-roll is
  culled twice, under both rulesets.
- **2026-09-01 — Phase 1 slice shipped; segments contract v1 ratified:**
  `posthouse/` package (door-3 bridge with commit pin, cold-footage
  builder API + CLI, light-dep harvest wrappers; heavy wrappers deferred
  to a Mac session per `posthouse/harvest/DEFERRED.md`). Contract
  rulings: (1) validation applies to the pre-handle range; handles
  always clamp to source bounds, never reject; (2) segment order in the
  file is final — the producer sorts, the builder lays; (3) sequence
  dimensions probe the first segment's source, falling back to
  1920x1080@30 — no per-segment aspect in v1.
- **2026-09-01 — Backlog for the first sanctioned PreCut change** (on
  Ryan's Mac, behind the safety net, when convenient — none are urgent):
  make `FCPXMLWriter._build_markers`'s import of `markers` conditional
  on the cutlist actually carrying markers (today every export needs
  the ML deps present or stubbed); gate the single-sequence
  `_build_audio` on ffprobe `has_audio` like the library builder
  already does (cold footage mixes silent sources onto V1 freely).
- **2026-09-01 — Phase 0 Tier 2 shipped, on Ryan's Mac, for real.**
  Teleported session ran the safety net against the real
  `~/precut-venv-fresh` (torch/lancedb/whisper/CLIP present) and a fresh
  pinned checkout (`e035fbaf`, matching `posthouse/PRECUT_PIN` exactly —
  no drift warning). The three Tier-2 stubs that were only ever
  documented as deferred are now real, passing tests: the full
  35-module import gate (`test_import_gate.py`, parametrized over every
  `precut_pipeline` and `python_backend` module, ML-deps-gated so it
  self-skips off-Mac), the additive-only DB-migration test
  (`test_db_migrations.py` — old-schema fixture → `Database.__init__` →
  asserts no row loss, idempotent re-open, new columns writable), and
  `run_safety_net.sh` now auto-detects the real venv when present and
  refuses to run with an unset `PRECUT_ROOT` instead of defaulting
  silently. Full suite: **60 passed, 1 skipped** (the stdlib-only-claim
  test correctly self-skips now that the real deps exist — the
  reviewer's earlier fix for exactly this case verified working, live).
  Sabotage re-run on the Mac: reintroduced the historical `<out> =
  duration - 1` bug in a scratch checkout copy — caught three ways
  (quirk-4 assertion, both golden masters); reverted, scratch deleted.
  **Remaining Tier-2 gap: real-footage audio sync** (needs genuine
  correlated dual-source audio, not synthetic tones — still open).
  Minor discovery for the backlog: the installed `lancedb` deprecates
  `table_names()` in favor of `list_tables()` (warning only, not
  failing yet).
- **2026-09-01 — Project Manifest contract v1 drafted**
  (`docs/contracts/PROJECT_MANIFEST.md`, Architect). Load-bearing
  choices: source IDs are minted once and frozen (`<kind>-<slug>-<NN>` —
  renames never orphan artifacts; downstream artifacts address files as
  `{source_id, rel_path}` so absolute paths live in exactly one place);
  two-moment validation (intake warns, handoff rejects); pipeline state
  deliberately NOT in the manifest (proxy/transcript status stays with
  the stage that owns it); co-location of the Brand Brief card is a
  validated invariant.
- **2026-09-01 — Project Manifest contract RATIFIED by Ryan.** Three
  rulings diverge from the draft's recommendations, one adds real
  scope:
  - **Delivery targets are not proposed at intake at all.** Rejected
    the draft's "both" recommendation. The PM never writes
    `delivery_targets`; it stays absent until the Creative Editor
    (Phase 6) has actually organized and familiarized itself with the
    footage and can make an informed suggestion, which then goes to
    Ryan for discussion before anything is `confirmed`.
  - **Shoot dates are read from file timestamps with no confirmation
    step** — stronger than the draft's "read, then confirm."
  - **Brand snapshots per project, per the recommendation** — and
    Ryan elevated it to a governing principle: a project folder should
    be fully self-contained so it can be handed to a human editor with
    nothing missing. **This surfaced a real, unresolved tension**: taken
    literally for raw footage, it conflicts with PreCut's deliberate
    "source footage is never moved" design. Flagged in the contract
    (§2.3), not silently resolved — needs an explicit decision before
    Phase 2's file-organization step is built.
  - **On-camera people, with added scope:** the PM asks once at intake
    (as recommended) but Ryan wants best-effort per-voice attribution
    across multiple speakers, with name corrections propagating to
    every clip carrying that voice. This is genuinely bigger than an
    intake field — it implies voice attribution spanning clips, which
    needs audio/transcript analysis the manifest doesn't have. Scoped
    out of the manifest and tracked as a Phase 4 (Assistant Editor)
    capability to design, not invented ahead of that phase; per Ryan,
    "ideal, not critical if it gets too complicated."
  - `dual_use` per folder and late-footage-as-revision both ratified
    exactly as recommended.
  PM implementation (Phase 2) can start once the flagged footage-
  portability tension is resolved.
- **Inherited from PreCut DECISIONS.md:** FCP7 XML is the delivery path;
  no CEP/UXP panel code; markers replace B-roll clips until matching
  precision is proven; API key (not OAuth) for Claude; deterministic
  motion tags. (Item 5 of that doc superseded per above.)
