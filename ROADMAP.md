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

**Ryan's selection criteria (2026-09-01, stated while marking the
Runnells answer key). These are the spec; the detection stack serves
them:**

- **Movement intent, consistent across the whole select.** A good shot
  has *intentional* movement, and "static, held" counts as an intent. A
  select is one continuous motion intent from its first frame to its
  last: a locked-off static hold, or a pan right, or a tilt down. The
  moment the motion type changes (the operator stops panning and holds;
  a hold turns into a tilt) the select ends and, if the new motion is
  clean, a new one begins. So the cull's segment boundaries are
  **motion-type change points**, not only shake onsets. This means
  per-frame motion *classification* (static / pan / tilt / push /
  handheld-drift / shake) plus change-point detection, and it retires
  any idea of "one settled segment per camera hold."
- **A clear focus point.** The subject must be in focus, with one
  deliberate exception: a **rack focus** (something starts in focus and
  goes out, or vice versa) is a legitimate select when the transition
  is intentional and smooth. Focus hunting is not. So the sharpness
  signal (Laplacian variance over time) is judged by *shape*: steady
  high = in focus; a smooth monotonic ramp between two stable levels =
  rack focus, keep; erratic oscillation = hunting, cut.

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

One real project, nominated by Ryan, with the raw footage still on disk
(benchmark v1: Runnells Day 1, see the Decision Log). Nothing generative
gets tuned without it.

**The answer key is marked, not inferred from an edit.** Ryan opens the
raw clips in Premiere, sets in/out around every range an assistant
editor would call usable, inserts each onto one selects sequence, and
exports it as FCP7 XML (`benchmark/README.md` has the steps). For the
cull this beats a delivered edit outright: it measures "what is usable"
directly instead of "what happened to get used," so there is no
survivorship gap to correct for. A finished edit is still the right
answer key for matching and story scoring later (benchmark v2, next real
job).

- **Cull scoring** (`posthouse/benchmark.py`): time-based precision
  (junk that got through) and recall (usable material missed) against
  the marked ranges, with each truth range dilated independently by the
  trim-handle tolerance so handles are neutral but the gaps *between*
  usable ranges stay outside truth (a cull that never cuts on a short
  disturbance must score as wrong, not perfect). Per-ruleset breakdown
  for dual-use footage. Recall matters more: an assistant editor who
  hides good footage is worse than one who lets a little shake through.
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
- **Answer-key survivorship: retired for the cull by design.** Benchmark
  v1's answer key is marked directly from raw footage (every usable
  range, not just the used ones), so this gap does not exist for cull
  scoring. It returns only when a delivered edit is the answer key
  (matching and story scoring, benchmark v2), where a one-time
  usable-but-unused marking pass is still needed.

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
    nothing missing. **This surfaced a tension, now resolved (same
    date):** Ryan clarified the scope means copying brand and other
    small assets to live alongside the footage — a sibling directory
    under the project root — never copying or relocating the footage
    itself. PreCut's "source footage is never moved" design (README.md)
    stays fully intact; `sources[].path` keeps referencing footage in
    place. Contract §2.3 updated; no longer a blocker.
  - **On-camera people:** the PM asks once at intake (as recommended);
    `project.people` is a simple roster. The follow-on ask (per-voice
    attribution across clips with propagating rename) was raised, then
    **de-scoped by Ryan the same day**: generic "Speaker 1" / "Speaker
    2" labels are sufficient everywhere — see the next entry.
  - `dual_use` per folder and late-footage-as-revision both ratified
    exactly as recommended.
  Contract is fully settled — no open blockers. Phase 2 (PM
  implementation) can start.
- **2026-09-02 — A full working session on real footage: Ryan's direct
  correction of the detector, three rounds of honest feature testing,
  a two-part parser bug in the tool that reads his corrections, and a
  real blind spot in the benchmark itself.** Summarized in order.
  1. **Ryan corrected the detector's output by hand in Premiere and
     gave concrete, specific criticism**, not vague dissatisfaction:
     the cuts were arbitrary with no discernible reason to open or
     close where they did; motion needs judging by its *shape over
     time* (a pan developing over a few seconds is intentional, the
     same displacement in half a second is a jerk; a pan that
     oscillates on the perpendicular axis while panning is shake
     wearing a pan's clothes, even if net motion looks clean); and
     high-frame-rate B-roll should be evaluated as if conformed to
     delivery rate, since the same motion plays out slower once
     retimed. He also asked directly whether `claude-video-vision`
     would help, given the pipeline works frame-by-frame.
  2. **Video-vision evaluated and scoped as diagnostic-only, not
     production.** Our phase-correlation signals already sample at the
     source's native rate (60/frame for this footage); a vision tool
     sampling a few times a second would have LESS temporal resolution
     for exactly the jerk/oscillation judgments requested, not more.
     Right role for it: spot-checking a specific disputed moment by
     eye while diagnosing, never a per-frame production dependency
     (ground rule 3, deterministic before generative, stands).
  3. **Feasibility discussion, honestly graded rather than assumed.**
     Distinguished "useful triage tool" (bounded time-series feature
     classification, well-understood problem class, real evidence
     already in hand of predictive individual signals) from "fully
     autonomous, no review needed" (a much higher bar the project was
     never actually aiming for — "the AI does the comprehension, the
     editor does the editing" was the founding principle). Corrected
     the "train on every possible movement" framing: this is fitting
     4-8 scalar thresholds via cross-validation, not a data-hungry
     model: a handful of well-chosen minimal pairs would matter far
     more than exhaustive coverage, and only after cheaper validation
     against data already in hand.
  4. **Three rounds of honest feature testing against real ground
     truth, reported plainly at each step, before asking for any more
     of Ryan's time.** Round 1 (raw jerk + naive single-axis "purity"):
     jerk near chance (Runnells AUC 0.51); purity consistently
     BACKWARDS (0.43 mean) — diagnosed directly against real data: 8 of
     Ryan's first 10 Runnells selects are COMPOUND motion (both axes
     genuinely active), so "orthogonal axis should be quiet" was false
     as a general rule for how he actually shoots. Round 2 (circular
     direction-stability instead of axis purity): still weak/mixed
     (Runnells 0.535), and the Lead caught a real methodological flaw
     in the test itself — one fixed motion-detection floor across
     clips whose scales differ 10x, the identical mistake already paid
     for once in the per-clip-normalization work. Round 3 (same
     feature, per-clip floor using each clip's own quiet-moment
     percentile): **floors measured at 0.014 to 1.967 px/frame (140x
     spread, confirming the confound was real), and direction-stability
     at a 1-second window reached AUC 0.714 on Runnells** — a real,
     non-chance signal on the one clip with trustworthy ground truth,
     though still inconsistent on the Des Moines set (0.20-0.71),
     entangled with the still-unresolved editorial-contamination
     question there. Recorded as genuine progress, not proof, and the
     right next input identified as the still-outstanding clean drone
     answer key, not a fourth blind variation.
  5. **Ryan then gave the real drone answer key directly**, correcting
     the detector's own output by hand in Premiere rather than marking
     from scratch — see the entry immediately below for the parser work
     that was needed to actually read it, the granularity finding it
     surfaced, and the fix that came out of it.
- **2026-09-02 — Reading Ryan's real corrected cuts took two parser
  fixes, and the SCORE of his correction against the original detector
  output exposed a genuine blind spot in the benchmark itself.**
  1. **The Lead lost the thread once, caught it when Ryan asked about
     it directly, and fixed it properly rather than guessing.** Ryan's
     corrected XML failed to parse; a clarifying question was asked and
     the conversation moved on to the broader methodology discussion
     without circling back. When Ryan asked "is the footage I cut not a
     clean answer key," that was the prompt to actually resolve it.
  2. **Root cause, worked out by hand before dispatching a fix**: one
     clipitem declared its own duration as almost exactly double the
     referenced file's declared duration (31,815 vs 15,911 frames,
     both at 60fps) — the signature of a clip genuinely retimed at the
     source (matching exactly what Ryan had asked for: high-frame-rate
     B-roll conformed to delivery rate). Fix: a self-consistency check
     tried in order — does the file's own rate/duration bound this
     clipitem's in/out; if not, does the clipitem's own rate/duration.
     Whichever is internally consistent wins, handling both this case
     and the earlier, different sequence-conform bug without regressing
     either.
  3. **First version of that fix was still wrong, and the Lead caught
     it by checking the real numbers rather than trusting a clean
     parse.** It correctly identified WHICH clips were retimed but left
     their seconds in the retimed (2x-slowed) timeline instead of
     rescaling back to real position in the source file — 15 of 28
     ranges landed past the file's real 265.45s duration, up to 520s.
     Sent back with the exact correcting formula
     (`real_seconds = frame / clip_own_duration_frames *
     file_real_duration_seconds`, rate-agnostic, doesn't assume a
     specific conform ratio) and a hand-verified expected value for the
     specific clipitem. Second version matched the hand calculation
     almost exactly (expected ~257.6-260.4s, actual 257.53-260.28s);
     all 28 ranges now fit inside the real file, and both previously-
     recorded real answer keys (Runnells, Des Moines) are unchanged.
  4. **Scoring the detector's original 4-blob output against Ryan's now-
     correctly-parsed 28 real cuts produced a score that looked almost
     respectable — P=0.727, R=0.993, IoU=0.593, beating select-
     everything's P=0.635 — while being exactly the "arbitrary cuts,
     basically unusable" output Ryan had already told us it was.** The
     detector's 4 giant segments (avg 66s, one spanning 154s) happened
     to cover 99% of Ryan's real 168.7s of usable footage simply because
     63.5% of this clip is usable, so almost any coarse "keep most of
     it" strategy scores well on overlap. **Precision, recall, and IoU
     were never able to see that the detector had done none of the real
     culling work — they only measure time overlap, never segment count
     or size.** This is the same blind spot from the opposite direction
     as slice 2's 463-run over-fragmentation, now confirmed to hide
     both failure modes. Fixed (see the immediately following commit):
     `granularity_ratio` (4/26≈0.154 here) plus `under_segmentation_
     events`/`over_segmentation_events`, additive to `Score`, naming the
     specific offending segment and which real cuts it swallowed (the
     154s blob alone: 15 distinct truth segments across 49.2s of real
     gaps) — a specific, actionable finding no bare P/R/IoU number could
     surface. Suite 363 passed / 1 skipped, verified independently
     against the real case before commit.
- **2026-09-02 — Ryan confirmed the asterisk: the two answer keys measure
  different things, which reframes the asymmetric-transfer finding.**
  Runnells was marked as a purpose-built teaching example: every
  technically-usable range, exhaustively, regardless of whether Ryan
  would actually cut it in. Des Moines is real production selects: Ryan
  choosing the *best* shots for an actual job, not marking everything
  that clears the technical bar. That is the line ROADMAP §1 already
  draws — **technical cull (deterministic: shake, blur, exposure) vs
  editorial cull (taste: which of several good takes to use)** — and
  Phase 4 is scoped to the first one only. Des Moines' selects carry
  editorial taste on top of technical usability (two clean drone passes
  of one location, only one makes a real edit), which is a fully
  sufficient explanation for its lower usable fraction (33.6% vs
  Runnells' 57.7%) and for why fitting on Runnells could not clear its
  baseline — the detector was being scored against a pickier standard
  than the one it was trained to meet, not a genuinely different
  technical distribution.
  **Consequence: the four options logged in the prior entry (fit per
  shoot, scope per camera, accept the asymmetry, or investigate the
  keys) were premature.** Fitting per-shoot or scoping per-camera would
  tune the detector to match Ryan's editorial taste on Des Moines,
  which is the wrong target for this phase entirely — Phase 4 does not
  claim to do editorial culling (ROADMAP §1, from day one). The
  per-clip normalization work itself stands: it is still correct that
  thresholds must be scale-free across cameras, and nothing above
  argues otherwise.
  **Proposed next step, awaiting Ryan:** a small Runnells-style
  exhaustive marking pass on ONE Des Moines clip (not the full 73
  minutes) — every technically-usable range, ignoring whether it would
  make a real cut, same protocol as Runnells. That produces a clean
  technical-cull benchmark on a different camera, which is the actual
  comparison this phase needs and the one the current data cannot give.
- **2026-09-02 — TRANSFER IS ASYMMETRIC, and per-clip normalization
  does not fix the failing direction. The reason is structural, not a
  tuning problem.** Measured both directions, all three strategies, no
  re-fitting on the scored shoot:
  | direction | strategy | P | R | F1 | IoU | vs baseline |
  | --- | --- | --- | --- | --- | --- | --- |
  | fit Runnells → score Des Moines | absolute | 0.330 | 0.858 | 0.477 | 0.273 | **below** (base P 0.336 / IoU 0.291) |
  | " | quantile | 0.329 | 0.759 | 0.459 | 0.261 | **below** |
  | " | robust_scale | 0.328 | 0.832 | 0.471 | 0.268 | **below** |
  | fit Des Moines → score Runnells | absolute | 0.628 | 0.912 | 0.744 | 0.427 | **transfers** (base P 0.577 / IoU 0.392) |
  | " | quantile | 0.642 | 0.897 | 0.748 | 0.433 | **transfers** |
  | " | robust_scale | 0.631 | 0.947 | 0.757 | 0.433 | **transfers** |
  **Fitting on the harder shoot transfers to the easier one; the reverse
  does not, and normalization does not rescue it.** All three strategies
  land within 0.01 of each other in the failing direction, which is the
  tell that the residual signal's *scale* was never the binding
  constraint there.
  **The Lead's diagnosis, measured rather than assumed:** the two shoots
  have very different usable fractions. Runnells is **57.7% usable**
  (1.5 of 3.9 min); the 12-clip Des Moines set is **33.6% usable** (9.1
  of 31.1 min). Select-everything precision IS the usable fraction, so
  it is also the ceiling any detector's precision is judged against. A
  detector fitted where 58% is usable learns to keep generously, and
  keeping generously on footage that is only 34% usable cannot clear
  that shoot's baseline. Worse for the `quantile` strategy specifically:
  per-clip usable fraction across the Des Moines set runs from **0% to
  52%** (two clips are true full-clip rejects), so the assumption that
  each clip has a roughly constant usable fraction — which the Lead
  flagged as its weakness *before* building it — is directly falsified
  on Ryan's real footage. That prediction being confirmed is worth as
  much as the numbers.
  **What this means, stated plainly:** per-clip normalization was
  necessary and is now in place (it makes thresholds scale-free, which
  they genuinely were not), but it is **not sufficient** for
  cross-shoot transfer. Ryan's ruling is implemented and the honest
  result is that it does not close the gap on its own. The open options
  are unchanged and remain his call: fit per shoot (cheap now that the
  harness exists — one fit per job), scope the cull per camera or
  footage type, or accept asymmetric transfer and always fit on the
  harder/denser shoot. There is also a fourth possibility worth testing
  before deciding: the usable-fraction gap may be an artifact of *how
  the two answer keys were made* (Runnells was a purpose-built marking
  pass, Des Moines is a real job's organized selects — the very caveat
  flagged when Des Moines was staged), in which case the two are not
  measuring quite the same thing and the comparison deserves that
  asterisk.
- **2026-09-02 — Ryan's ruling on the generalization failure: normalize
  per clip so thresholds adapt to each shoot.** Not per-shoot fitting,
  not per-camera scoping. The Lead flagged, before building, that
  "normalize per clip" has two meanings that fail differently, so both
  are implemented as competing arms and measurement chooses:
  - **`quantile`** — threshold at a fitted per-clip quantile of the
    smoothed residual, mirroring how `stability_lapvar_quantile`
    already works. Scale-free, but assumes a roughly constant *fraction*
    of every clip is usable, so a uniformly excellent clip still loses
    the top (1-q) fraction. That weakness is real and gets reported
    rather than hidden. Drone footage, being long and mostly usable, is
    exactly where it should hurt.
  - **`robust_scale`** — normalize by a per-clip robust statistic
    (median/MAD) and threshold in those units. Scale-free AND not
    fraction-fixed: a uniformly good clip stays mostly selected, a
    uniformly bad one mostly rejected. Expected to be the better fit for
    Ryan's actual footage mix, but not assumed.
  `absolute` is kept as the control arm. **The deliverable is not the
  implementation, it is the transfer table: fit on Runnells score on Des
  Moines, and fit on Des Moines score on Runnells, both directions,
  because a strategy that transfers one way only is not transfer.** The
  brief states explicitly that if no strategy beats select-everything on
  the shoot it was NOT fitted on, that gets reported as a finding, since
  it would mean per-clip normalization is insufficient and the real
  options become per-shoot fitting or per-camera scoping — Ryan's call,
  not one to make silently in a tuning pass.
- **2026-09-02 — THE GENERALIZATION TEST FAILED, AND A REAL PARSER BUG
  WAS FOUND MEASURING IT. Both reported as measured.**
  Three separate pieces of work landed together; the second and third
  matter more than the first.
  **(1) The gate-combination fix.** `fit.py` gained an automatic
  **grid-edge alarm** (a fitted value landing on its search grid's min
  or max is now a structured warning in the fit report, so this class of
  problem can never again require a human to notice it by reading JSON),
  properly widened grids informed by measured signal percentiles on the
  real clip (p50 1.14, p90 3.34, p99 10.90 px/frame; the original grid
  reached only ~p65), and `stability_combine` as a real parameter with
  five modes (`and`/`or`/`resid_only`/`lapvar_only`/`score`) all fit and
  cross-validated as first-class arms. Building the `score` mode's test
  caught a genuine bug: the percentile-rank helper broke ties by array
  position rather than averaging them, silently injecting a fake
  time-correlated signal across any constant stretch, which is exactly
  what a locked-off shot produces. **Result on Runnells: `resid_only`
  won at P 0.627 / R 0.911 / F1 0.737 / IoU 0.417** — ties the fair-CV
  crude probe on F1, beats it on recall, does not clear it on precision
  or IoU. And **every combine mode still pinned its motion parameter to
  a grid edge**, which points past gate structure at the ranking rule
  itself (recall-first under a 0.60 precision floor rewards maximum
  looseness). Logged as an open question, not silently retuned.
  **(2) A confirmed, load-bearing bug in the answer-key parser.**
  `parse_answer_key_xml` converted a clipitem's `<in>`/`<out>` frames to
  seconds using the clipitem's own declared `<rate>`. When Premiere
  conforms a source into a differently-rated sequence it writes that tag
  as the SEQUENCE's rate while the frame numbers stay in the SOURCE's
  native rate, inflating durations by `native/sequence`. **39 of the 60
  Des Moines sources were affected.** The Lead verified it independently
  and by arithmetic that proves itself: a clipitem on a 265.4s file
  declared `out=15390` at a stated 24fps, i.e. 641 seconds, longer than
  the file it points into and therefore impossible; at the file's real
  59.94fps it is 256.8s, inside the file. Fixed by resolving against the
  referenced `<file>`'s own rate on disagreement, plus a **bounds check
  that now refuses any range extending past its own file's duration**
  rather than emitting it silently. Runnells parses identically before
  and after (no mismatch present) — the no-regression check.
  **Consequence: the Des Moines benchmark is 41.5 minutes of marked-
  usable footage, not the 73.1 the Lead published on staging.** That
  README is corrected, with the bug and the arithmetic recorded in it.
  **(3) The generalization result itself, which is the point.** The
  Runnells-fitted detector, run unchanged (no re-fitting, per the rule)
  against Des Moines drone footage, scored **BELOW the select-everything
  baseline** on precision and IoU: `resid_only` P 0.317 / R 0.714 /
  IoU 0.255 and `score` P 0.318 / R 0.695 / IoU 0.253, against a
  baseline of P 0.338 / R 1.000 / IoU 0.300. **A detector fitted on 3.9
  minutes of one handheld clip does not transfer to gimbal-stabilized
  aerial footage from different cameras.** Direct supporting evidence
  from the signals themselves: smoothed motion-residual maxima on Des
  Moines clips run ~0.5-1.0 px/frame against Runnells' p99 of 10.90, an
  order of magnitude apart, so an absolute threshold cannot survive the
  crossing. This is the finding the whole benchmark exists to produce,
  and the design doc's standing instruction is to say so rather than
  quietly re-fit on the new footage and call it fixed.
  **Caveat on that number, stated rather than buried:** it was measured
  on a 5-clip subset chosen because their ground truth was independently
  verified correct under the OLD parser. With the parser now fixed, the
  honest full-dataset number is still owed and is the immediate next
  step; the Lead does not expect it to reverse the conclusion, since the
  detector already lost on clips whose truth was known-good, but the
  real number gets measured rather than assumed.
- **2026-09-02 — Slice 5 landed the stability detector as production
  code, but its own report didn't reproduce the diagnostic's numbers,
  and the Lead's investigation found something more interesting than a
  bug: the two-signal AND-gate is the wrong architecture, not just the
  wrong thresholds.** Production held-out score: P 0.669 / R 0.804 /
  IoU 0.436 (vs the diagnostic's P 0.635 / R 0.881 / IoU 0.428).
  Diagnosed step by step:
  1. **`stability_resid_max` fitted to 2.0, exactly the maximum of its
     5-point search grid** `[0.8, 1.0, 1.2, 1.5, 2.0]`. A parameter
     pinned to the wall of its own search space is not evidence of an
     optimum.
  2. Widened the grid 3x (`resid_max` to 6.0) using the real production
     `fit()` entry point, not a reimplementation. The fitted value
     **climbed to 6.0 — the new maximum — again.** Widening the fence
     doesn't stop the parameter from wanting to leave the yard.
  3. **That is the tell that the parameter wants to be disabled, not
     that the range was too narrow**, so the Lead isolated each signal
     directly (in-sample, on the real clip):
     | config | P | R | IoU |
     | --- | --- | --- | --- |
     | both signals disabled (= select-everything) | 0.577 | 1.000 | 0.392 |
     | **motion residual alone** | 0.678 | 0.852 | **0.455** |
     | **sharpness alone** | 0.641 | 0.813 | 0.420 |
     | both required together (AND, the shipped structure) | 0.700 | 0.740 | 0.446 |
     | AND + exposure gate (exactly what shipped) | 0.703 | 0.721 | 0.442 |
     **Both signals are individually real** (each clears select-
     everything by a wide margin). **Requiring both at once is worse on
     IoU than motion residual alone.** The AND combination doesn't add
     the two signals' value, it subtracts recall from each to buy
     precision neither asked for. That is an architecture defect in how
     the gates compose, not a threshold that needs a wider search.
  **New finding on top of slice 4's, not a replacement for it:** slice 4
  proved classification-and-consolidation loses to two thresholds.
  This shows the two thresholds themselves are being combined wrong.
  **Consequence for the next pass, scoped and dispatched, not
  hand-patched:** (a) widen both grids properly so no fitted value can
  land on an edge undetected — and alarm on it when it does, this
  should never again require a manual check to notice; (b) add
  resid-only and lapvar-only as first-class ablation arms alongside
  full/no-focus/no-exposure, since the ablation table's own job is to
  catch exactly this and it didn't have the arms to; (c) consider a
  combination rule beyond strict AND (e.g. a summed or weighted score
  against one threshold) as an explicit, measured candidate rather than
  an assumption; (d) re-fit, re-measure held-out, and score the result
  against the newly staged Des Moines Estabs benchmark — the real
  generalization test.
- **2026-09-02 — Benchmark v2 candidate staged: Des Moines Estabs, a
  real drone (establishing-shot) project across 8 shoot days.** Ryan
  provided a Premiere export of already-organized `_Culled` sequences
  plus the raw footage on `/Volumes/video`. Genuinely different from
  Runnells in every way that matters for generalization: gimbal-
  stabilized aerial rather than handheld, a different camera (DJI Mavic
  2), 8 dates instead of 1, select durations an order of magnitude
  longer (median 12.7s vs 3.4s), and 119 raw clips instead of 2 -
  giving **59 true full-clip rejects**, ground truth Runnells could
  never produce (a single continuous clip has no "never touched"
  case).
  **The nested-sequence guard slice 3 built for exactly this situation
  caught a real defect on the first real file that could trip it**: one
  sequence (`Downtown Night Shoot_Culled`) nests 8 sub-sequences,
  almost certainly mask/grade adjustment layers, and the parser refused
  rather than silently over-count. Resolved conservatively: that one
  sequence (16 clips, ~0.7 of 74.5 total minutes) is excluded from both
  the select and full-clip-reject ground truth in the staged answer
  key, by removing the element rather than inventing trim math for the
  nest - a wrong derived range would be a silently poisoned ground
  truth, the worst failure mode a benchmark can have. Building a real,
  tested flattener is future work if this recurs; the original
  unfiltered export is kept as the source of record.
  **Staged at `benchmark/des-moines-estabs/`**, parses cleanly through
  the shipped harness. Not yet scored - that is the natural next step
  once slice 5 lands, and it is a materially stronger generalization
  test than the small held-out strip on Runnells' 33-minute clip,
  precisely because it is a different shoot. One caveat carried
  forward, not yet resolved with Ryan: these are organized-selects
  sequences from a real job, not a purpose-built marking pass the way
  Runnells was, so their fitness as ground truth is a reasonable
  inference, not yet a ratified fact.
- **2026-09-02 — PHASE 4 SLICE 4: the pre-committed rule fires. Two
  thresholds beat the whole detector, so the detector gets simplified.**
  `posthouse/cull/fit.py` (15 tests) implements the design's fitting
  plan: staged coordinate descent, at most 4 free parameters per stage,
  3 contiguous time blocks, fit on two and score on the held-out third,
  10k-resample block bootstrap, non-overfittable fixture ordering
  guards, recall-first ranking under a 0.60 precision floor, and gate
  ablation as a first-class output.
  - **Ablation confirmed the Lead's pre-fit diagnosis: the focus gate
    does not earn its place.** Held out, removing it goes from
    P 0.601 / R 0.707 to **P 0.634 / R 0.838**. The exposure gate earns
    its place but only just, and on a floor-sensitive margin.
    Hysteresis beats Viterbi, narrowly.
  - **The slice's own comparison was unfair and the Lead corrected it.**
    It scored the fitted pipeline HELD-OUT against the crude probe's
    IN-SAMPLE numbers, which systematically favours the probe's opponent
    being measured the harder way. Re-fitting the probe under the
    identical scheme (same blocks, same folds, same rule, 25-point grid
    on its two parameters) gives the honest comparison:
    | detector | P | R | F1 | IoU |
    | --- | --- | --- | --- | --- |
    | crude probe, 2 thresholds | 0.635 | **0.881** | **0.737** | **0.428** |
    | full pipeline, slices 2-4 | 0.634 | 0.838 | 0.710 | 0.387 |
  - **Verdict: two thresholds on two signals tie the pipeline on
    precision and beat it on recall, F1 and IoU.** Per the rule recorded
    before fitting began, the finding is to **simplify the detector, not
    to add parameters.** Recorded plainly rather than rationalised.
  - **What this does and does not condemn.** Slice 1's signal extractor
    is the foundation of BOTH detectors and is not in question. What
    failed to earn its place is the machinery built on top of it for
    BOUNDARY PLACEMENT: the 11-class motion classification, the
    consolidation stage, the settle/class gates, and the focus gate.
    Note also that the winning probe sets its sharpness threshold at the
    10th percentile, i.e. it barely uses sharpness at all: **the working
    signal is motion stability alone.**
  - **The uncomfortable implication for criterion 1, stated rather than
    buried:** Ryan's stated rule is one motion intent per select, and we
    built classification to serve it. On his actual marks, a plain
    stability threshold predicts better than intent classification does.
    Either our implementation of the criterion is poor, or what he marks
    is driven more by stability than by intent type. Both are worth
    knowing; neither is settled by one clip.
  - **Recommendation carried to slice 5: demote, do not delete.** Adopt
    the simple stability-threshold detector for segment EXTENT, and keep
    the motion classifier only as a LABELLER, because "pan, 3.9s" on a
    Premiere clip name is genuinely useful to Ryan even though its
    boundaries lose to a threshold. That keeps every measured win and
    drops what measurement says is not paying for itself.
- **2026-09-01 — Phase 4 slice 3 built (`posthouse/cull/segment.py`,
  13 tests, suite 302 passed / 1 skipped): the first real `culls.json`,
  the first Cold Footage sequence, and the first honest benchmark score.
  The score is a NEGATIVE result and is recorded as one.**
  - **Consolidation worked.** Slice 2's 463 runs (median 0.20s) become
    **65 runs, median 2.90s** (hysteresis) or 104 (Viterbi), and
    boundary placement **beat chance for the first time**: 38.5% of
    Ryan's 52 cut points within 0.5s of a boundary against a 28.7%
    random baseline (Viterbi 40.4% vs 30.9%). Slice 2 was 69% vs 67%,
    i.e. nothing. Hysteresis wins on every metric, so it is the default.
  - **But the full pipeline scored P 0.628 / R 0.553 / IoU 0.334, BELOW
    the crude two-signal probe (P 0.701 / R 0.775 / IoU 0.459).** A
    1200-line detector losing to six grid points on two signals is a
    real finding, not a footnote.
  - **The Lead diagnosed the cause rather than assuming slice 4 would
    fix it.** The focus gate rejected **67.6s, which is 73% of Ryan's
    entire 92.2s of marked-usable footage**, and was the primary killer
    of seven of the eight selects that scored 0% coverage. Isolating it
    (one parameter, four points, to find a cause, NOT a grid search for
    a good number) gives **P 0.644 / R 0.791 / IoU 0.407 with the gate
    effectively off**: it costs 24 points of recall and buys *no*
    precision. It is pure loss at its unfitted default, exactly the
    outcome design §1.4 warned about when it proved an absolute
    sharpness threshold impossible on footage spanning lapvar 100 to
    4890.
  - **Even with that gate off, the pipeline still trails the crude probe
    on precision and IoU.** Fair caveat, stated so it is not used as an
    excuse: the probe's numbers were six grid points read off this same
    clip, so they are mildly self-fitted, while slice 3 is entirely
    unfitted. The comparison is not clean. It is still the bar.
  - **Consequence for slice 4:** fitting is no longer a polish step, it
    is where this design proves it earns its complexity. If honest
    fitting with block CV still cannot beat two thresholds, that is
    evidence to simplify the detector, not to add parameters. Recorded
    now, before fitting, so the conclusion cannot be rationalized later.
  - Bug found on real footage that fixtures could not surface: the
    focus-hunt gate's literal "sign changes per second" spec trips on
    99.6% of frames unsmoothed and 31% after a second of smoothing, so
    the first build produced **zero** accepted segments end to end.
    Fixed with a Schmitt-trigger crossing count scaled to the clip's own
    noise floor. Flagged scope cuts: `analysis_sec` is 0.0 because
    slice 1's sidecar does not record it (a slice 1 gap), and the full
    CULLS §7 validator beyond tiling, shared-shape and the ruleset enum
    is not implemented this slice.
- **2026-09-01 — Phase 4 slice 2 built (`posthouse/cull/classify.py`,
  23 tests, suite 289 passed / 1 skipped), and the Lead's independent
  check found the thing the slice's own tests could not.** The
  classifier is correct per frame: synthetic clips with known ground
  truth classify right, fixtures order correctly, the sign convention
  holds both ways (camera pans right, content moves left, dx < 0), and
  classification of an extracted sidecar costs 0.095s. On the two
  hand-verified windows it looks excellent: **the 14.98-18.85 pan is
  100% `pan_right` uninterrupted, the 19.19-21.29 tilt is 92%
  `tilt_down`.**
  **But those two windows were the Architect's cherry-picked clean
  examples, and testing against all 26 of Ryan's selects tells a
  different story.** Measured by the Lead: only **9 of 26** selects are
  dominated (>=80%) by a single motion class, and his 52 cut points land
  within 0.5s of a class boundary **69% of the time against 67% for
  randomly placed cuts** — i.e. the current run structure carries almost
  **no predictive signal about where he actually cut**. Cause is
  fragmentation, not misclassification: **463 runs over 235s, one every
  0.51s, median run 0.20s, 87% of runs shorter than his 1.0s floor** —
  roughly **18x over-segmented** against his 26 selects, with `drift`
  supplying 34% of runs from only 12.3% of frames.
  **Verdict: slice 2 is accepted as a per-frame classifier and is NOT
  yet evidence for criterion 1.** Turning this into intents is
  precisely slice 3's job (min-duration, hysteresis at the run level,
  and the Viterbi transition penalty that exists to buy long runs), and
  slice 3 now has a concrete target: get from 463 runs to the order of
  26, and beat the 67% chance baseline on boundary placement. Recorded
  because "it matched the two windows we checked" is exactly how a
  detector gets believed before it has earned it.
- **2026-09-01 — All 9 slice 1 findings fixed and independently
  verified by the Lead** (suite 266 passed / 1 skipped). Measured after
  the fixes, on the real clip: **peak RSS 265 MB** (was ~3.7 GB held for
  this clip, ~31 GB projected for the 33-minute one) and **3.23x
  realtime, up from 1.34x** — the FFT work (spectra cached across
  frames, `rfft2`, no complex128 upcast) more than doubled throughput,
  so the Runnells day now analyses in about 12 minutes. Sign convention
  verified directly (`np.roll` right 5 / down 3 returns dx=+5.000,
  dy=+3.000) and the histogram verified int32 with an exact 518,400
  count on a black frame. Design §1.3's sign pin corrected with
  re-measured values (pan −13.32 px/frame, tilt −6.66 at the shipped
  960-wide plane; magnitudes consistent with the old 480-wide table
  after scaling, signs unchanged) and the convention stated for slice 2:
  **positive dx means content moved right, positive dy means down.**
  Design §4 and CULLS §6 amended for the `<sha12>` sidecar name, with
  `sidecar_paths()` named as the single constructor of that path.
  Two honest notes from the fix work: ffmpeg's muxers refuse to keep a
  genuinely packetless audio track, so the zero-sample case is tested at
  the unit level against the documented contract rather than with a real
  file; and hardware decode needs the probed pixel format
  (`p010le` for 10-bit, `nv12` otherwise) since no single fixed format
  works across both.
- **2026-09-01 — Slice 1 full review: 8 verified findings beyond the
  deadlock, two of them critical for slice 2.** (1) **Memory:** the
  extractor materialized every decoded frame in RAM before analysis.
  Lead verified the arithmetic against the real probed clips: **31.0 GB
  for the 33-minute clip, 34.6 GB for the Runnells day.** The trap is
  that this Mac has 206 GB, so it would have *worked* here and looked
  fine, then died on a longer interview or any smaller machine. A bug
  that passes on the developer's hardware and fails on the job is worse
  than one that fails immediately. Decode and analysis become one
  streaming pass holding two frames. (2) **Sign inversion:** the phase correlation returned
  displacement with the sign flipped (`fa*conj(fb)`), invisible to
  tests that use sign-agnostic medians, but slice 2 would have inherited
  every pan direction, push vs pull, and roll direction backwards.
  Fixed with direction unit tests; the design's sign-convention pin
  (pan −5.84 px/frame on Ryan's selects 3/4) is re-measured after the fix
  and corrected in the design doc. Also: int16 histogram bins wrap on
  black frames (now int32, design §4 amended); `decode_mode` claimed
  hardware for ProRes/FFV1 that ffmpeg silently software-decoded (made a
  hard failure that routes to the fallback); an audio stream with zero
  samples wrote empty arrays flagged present (present decided after
  decode); 15 tests would crash rather than skip under the cloud numpy
  stub (module marked tier2); the proxy-motion test was vacuous
  (medians of static footage) and is replaced by one asserting the
  measured truth that proxies do NOT preserve per-frame motion; sidecar
  filenames collided on DJI card-rollover basenames (sha12 suffix).
  Perf items taken because they address the 1.34x runtime: FFT spectra
  cached across frames, rfft2, no complex128 upcast, streamed audio,
  vectorized windows. Prose in tests/README that claimed the sharpness
  effect "was measured on real 4K footage in the design doc" was false
  and now cites §1.9. Verdict on the pattern: the fixtures validated
  orderings and determinism perfectly and missed every one of these,
  because they are quiet, small, static, and synthetic; the review and
  the real clip caught them. Fixture green is necessary, never
  sufficient.
- **2026-09-01 — Slice 1 review found a deadlock; fixed and committed
  (`c2fc65c`).** The extractor read frames from ffmpeg's stdout while
  draining stderr only afterwards, so a chatty decode filled the 64 KB
  stderr pipe and both processes blocked forever. It never shows on
  fixtures (quiet decodes) and would have frozen the cull on real footage
  at 2 a.m. The reviewer reproduced the hang; the Lead reproduced the old
  pattern standalone (hangs at an 8s timeout) to prove the new regression
  test bites. Fix: stderr to a temp file (no buffer limit, tail quoted on
  failure), kill ffmpeg cleanly on early consumer exit instead of
  misreporting the EPIPE exit, `-nostdin`. Two regression tests drive a
  fake ffmpeg that floods stderr with 260 KB before emitting frames.
  Standing rule for every subprocess pipe in the house: never
  `stderr=PIPE` alongside an incrementally-read stdout; use a file or
  `communicate()`.
- **2026-09-01 — Phase 4 slice 1 built: `posthouse/cull/signals.py`, the
  signal extractor** (25 tests; suite 245 passed / 1 skipped). All five
  fixture ordering assertions held on the first implementation (shaky >
  stable on residual 7.67 vs 3.05; blurred < stable on Laplacian 0.87 vs
  121.6; under/over lead their clip fractions), frame counts within 2,
  byte-identical sidecars on repeat. Honest discrepancies reported, not
  hidden: **runtime 1.34x realtime on the real clip vs the design's 4-5x
  projection** (a per-frame Python loop with 9 full FFT correlations;
  the 37-minute day is ~28 minutes, still far inside the overnight bar;
  optimize only after downstream slices show it matters); the design's
  3x3 grid of 256px blocks does not tile a 960x540 plane, so blocks
  overlap (documented); hf_energy folds roll into the speed scalar
  because shaky.mp4's shake is rotational.
  **The proxy-vs-source gate did not reproduce on synthetic fixtures**
  (testsrc2 has no fine detail for CRF-28 to destroy; ratio 1.03), so the
  Lead ran it on the REAL clip against PreCut's own proxy of clip 0006.
  Result partly inverts the design's expectation: **sharpness absolute
  level is destroyed (proxy median 30% lower, ratio 1.43) but its
  per-frame shape survives (r = 0.983)**, while **the motion residual,
  the stability signal, correlates only r = 0.544** (tx 0.92, ty 0.74).
  Ruling: the no-proxies rule stands, for the corrected reason: the
  residual (shake) signal does not survive compression, and absolute
  sharpness does not either; only sharpness *shape* would. Recorded as a
  dated addendum in the design doc so builders see it.
- **2026-09-01 — Phase 4 design delivered and accepted by the Lead**
  (`docs/design/PHASE4_CULL_DESIGN.md`, `docs/contracts/CULLS.md`;
  Architect). The design is measured, not estimated. Chosen stack: one
  ffmpeg pass per file with VideoToolbox hardware decode of the
  **original** HEVC (software decode runs 1.29x realtime, hardware 4.7x)
  to a 960x540 gray plane; block-wise phase correlation (3x3 grid) fit to
  a 4-DOF similarity (translation, log-scale, roll) plus residual, so
  push/pull is visible; deterministic motion classification into 11
  states; Laplacian variance normalized per clip and judged by temporal
  shape (steady / monotonic ramp = rack / oscillation = hunt); exposure
  histograms; audio peak/RMS with speech presence via the harvested
  transcribe. Segmentation: Viterbi over motion classes with one fitted
  transition penalty (emits labelled runs), hysteresis state machine
  shipped first as the A/B control. Rejected with evidence:
  `vidstabdetect` (**not compiled into Ryan's ffmpeg**, and blind to
  zoom), Farneback (no OpenCV, ~10x cost, per-pixel answer to a
  camera-motion question), `ruptures` (not installed, wrong output).
  Grounding on Ryan's own key: selects #3/#4 measure a pan at -5.84
  px/frame then a tilt at -2.78, with the 0.34s axis change in neither;
  accepted selects span lapvar 100 (dim interior) to 4890 (daylight), so
  an absolute sharpness threshold is impossible. **Runtime measured: 7.0x
  realtime for a reduced stack over the whole 235s clip; projected 4-5x
  for the full stack, so the 37-minute day runs in 8-10 minutes** against
  a bar of overnight. Crude two-signal probe already reaches P 0.70 /
  R 0.78 / IoU 0.46 (quoted as the floor). **First target: R >= 0.85,
  P >= 0.70, IoU >= 0.55, recall ranked first, block-CV spread reported.**
  `culls.json` stays `contract_version: 1` by field-by-field proof
  (additive only; the shared validator ignores unknown keys); identity
  is `source_id`+`rel_path`, `source_path` is resolution, identity wins
  on disagreement; top-level `rejections[]` must tile `[0, duration]`
  with segments per (source, ruleset). Fitting is contained, not solved:
  staged <=4-parameter fits, contiguous-block CV, block bootstrap,
  non-overfittable fixture *ordering* guards; 26 selects on one clip can
  show the detector is not broken and rank parameter sets coarsely, and
  **cannot** establish generalization. Build plan: six reviewable slices;
  slice 1 (signal extractor) dispatched this date. Open questions for
  Ryan (13, none blocking slice 1) are recorded in both docs with
  recommendations; the Lead proceeds on the recommendations until Ryan
  overrides. Lead correction applied: sequence names in the contract
  used em dashes, which is project-facing text in Premiere; changed.
- **2026-09-01 — PHASE 3 EXIT MET: baseline recorded before anything is
  improved.** With no cull yet, the honest baseline is "select
  everything" (what zero culling hands an editor). On clip 0006 against
  Ryan's 26 selects: **precision 0.577, recall 1.000, F1 0.732, IoU
  0.392** (`benchmark/runnells-day-1/baselines/select_all/`). Why 0.577
  and not the raw 39%: the 1.0s handle tolerance forgives ~1s either side
  of each of 26 truth ranges (capped at gap midpoints), roughly 44s of
  neutral footage, so 0.577 is the tolerance-adjusted floor and IoU 0.392
  the strict one. The cull must beat both; recall is what it must not
  lose. Observation for the fitting plan: a 1.0s tolerance is generous
  against 3.4s-median selects, so the Phase 4 design should treat
  tolerance as a reported parameter, not a constant. Phases 0 through 3
  are complete.
- **2026-09-01 — PHASE 3 harness shipped: `posthouse/benchmark.py`**
  (parser for Premiere's real FCP7 export, time-based P/R/IoU scorer,
  per-ruleset breakdown, largest-misses report, CLI). Suite 220 passed /
  1 skipped. The Lead hand-verified the arithmetic against computed
  cases (P .6923 / R .80 / IoU .5333 on a known overlap; exact-tolerance
  overcover scores 1.0) and the parser against Ryan's real export (26
  ranges, 92.2s, matching an independent count exactly).
  **A high-effort code review found 8 verified defects before commit,
  three of them load-bearing for every later measurement:** (1) truth
  dilation merged ranges separated by short gaps, so a cull that never
  cut on a brief disturbance scored perfect; on Ryan's real key 7 of 25
  gaps are under 2s, so this would have hidden 28% of his boundaries.
  Fixed: each truth interval dilates independently, capped at the gap
  midpoint. (2) The basename fallback silently credited the wrong clip
  when two cards share a camera-native filename; now refused and
  reported as unmatched. (3) Nested sequences were walked but never
  trimmed (3x over-count in one case); now refused loudly with a flatten
  instruction. Plus: malformed culls (negative, NaN, bool, non-object)
  now rejected; header and segment errors reported together as promised;
  the culls validator, which had already drifted from coldfootage's, is
  now one shared function so Phase 4's contract bump cannot silently
  fork them; `pathurl` decoding no longer truncates on `#`/`?`; the CLI
  test no longer needs PreCut. **Added the same day from real data:
  truth scope** — predicted sources with no truth are excluded from the
  overall score and listed as unscored, because the answer key covers
  only clip 0006. 14 regression tests, each reproducing the reviewer's
  exact scenario.
- **2026-09-01 — Benchmark v1 answer key delivered by Ryan (partial,
  by design).** He marked clip `DJI_20260430075045_0006_D.MP4` (3.9 min)
  only; the 33-minute clip stays unmarked for now (optional later, not
  blocking). Result: **26 selects, 92.2s usable of 235.3s (39%)**,
  durations 1.2–7.9s (median ~3.4s), sequence named "Culled B-Roll".
  Staged at `benchmark/runnells-day-1/answer_key.xml`. Two consequences
  ruled immediately: (1) **truth scope** — the harness scores only
  sources that have truth and reports the rest as unscored, so the
  unmarked clip never records false positives against a judgment nobody
  has made; (2) the key contains adjacent selects **0.34s and 0.77s
  apart**, which turns the reviewer's dilation-merging finding from a
  theoretical hole into a certain one on this very data. **Ryan's
  selection criteria, stated while marking, are now the cull spec (§4):
  one consistent motion intent per select (static hold, or one pan/tilt
  start to finish; boundaries are motion-type change points), and a
  clear focus point, with an intentional rack focus allowed.**
- **2026-09-01 — Benchmark v1 nominated and staged: Runnells Day 1.**
  Ryan has no finished project with both raw footage and a delivered
  edit, so the benchmark uses raw footage plus an answer key he marks
  directly in Premiere (every usable in/out onto a selects sequence,
  exported as FCP7 XML). For the cull this is *better* than a finished
  edit: it removes the survivorship gap (§7) outright, since he marks
  what is usable rather than what he happened to use. Footage: two 4K
  HEVC 29.97 Osmo clips (33.2 min + 3.9 min) on `RDOSS_2025`, referenced
  in place, declared `aroll` + `dual_use` (Ryan: the walkthrough A-roll
  doubles as B-roll), plus four DJI lav files. The repo holds only paths,
  the manifest, and later the answer key and scores; media is
  gitignored under `benchmark/`. A deliberate-defects mini-shoot is the
  planned supplement for defect coverage; the next real job with a
  finished edit becomes benchmark v2 for matching/story scoring.
- **2026-09-01 — First real-footage PM run found two bugs the fixtures
  never could.** (1) The census counted **6 videos for a 2-clip shoot**
  and invented a **July 30 shoot date**: it walked into PreCut's
  `Osmo/proxies/` and counted the proxies plus their macOS `._*`
  AppleDouble sidecars (which also end in `.mp4`), and dated the shoot by
  proxy-generation time. Fix: `_iter_files` now skips PreCut's own
  skip-list (`proxies`, `PreCut_Output`, harvested from
  `multi_exporter._find_original_for_proxy` so PM and exporter agree on
  what is not footage) and every dotfile. (2) On re-run, existing sources
  kept a **stale census** from the prior revision while `shoot_dates`
  (also disk-derived) had already corrected itself. Ruling: a re-run
  refreshes the snapshot fields (media, unsupported, inference) of
  known sources; only identity (id, added_at, dual_use, notes) is
  frozen. Both fixed with regression tests reproducing the exact
  on-disk layout, and re-validated against the real drive (stale 6
  corrected to 2 on re-run, id unchanged). Lesson logged for every
  later role: the fixtures prove correctness; only real footage proves
  the assumptions.
- **2026-09-01 — PHASE 1 COMPLETE, and Phase 0's last Tier-2 gap is
  CLOSED.** The three heavy-dep harvest wrappers shipped
  (`posthouse/harvest/{transcribe,index,sync}.py`, 8 Tier-2 tests marked
  `tier2` so cloud runs can deselect them; suite 186 passed / 1 skipped,
  ~50s on the Mac). No model weights downloaded: Whisper `base` and CLIP
  ViT-B-32 were already cached from PreCut.
  - **Sync gap closed with a real measurement, not a hand-wave.** The
    long-standing Tier-2 hole was "synthetic sine tones can't clear
    PreCut's MFCC threshold." Resolution: manufacture *real speech* with
    macOS `say`, split it into a camera MOV and a lav WAV with a known
    1.5s offset, different gain/EQ, and added noise. Measured: offset
    recovered **-1.504s vs known -1.5s (4ms error)**, score **11.55 vs
    `SCORE_USE=10.0`**. Real speech clears the floor with margin. The test
    skips-with-the-number rather than weakening if a future run ever
    measures below threshold; the threshold itself was not touched.
  - **Index schema proven by consumption**, not column-matching: the
    wrapper's output is fed to PreCut's own `load_broll_library`, which
    returns real entries. 512-dim vectors, one per sampled frame;
    re-indexing an unchanged clip is a true no-op via PreCut's
    `clip_exists_unchanged`. Vision tagging (Claude / LLaVA) is opt-in
    and OFF by default: the index builds offline on CLIP alone, and tests
    make no network calls.
  - **Transcription** reuses PreCut's phrase chunking and persists the
    exact on-disk transcript shape. Keyword recovery 3/3 with the
    `Samantha` voice. Acoustic finding, not a bug: the default `Alex`
    voice garbles "countertops" under Whisper `base`, so the test pins
    the voice and asserts ≥2/3 keywords plus structural invariants,
    never an exact string.
  - Below-threshold sync policy stays a Phase 4 decision: `sync_pairs`
    returns every pair flagged with `passed_threshold`, never drops or
    silently includes one.
  - Backlog notes for anyone touching this venv: no `pandas` installed,
    so LanceDB reads must use `.to_arrow()`; `BrollLibraryEntry` exposes
    `.source_path`, not `.path`.
  **Phase 0 and Phase 1 are now fully complete with nothing deferred.**
- **2026-09-01 — PHASE 2 COMPLETE: the Project Manager role runs
  headless.** Final slice `posthouse/projectmanager.py` (21 tests; suite
  178 passed / 1 skipped): per-source media census by extension (not
  ffprobe — a card dump is thousands of files and this is an intake
  snapshot, not analysis), unsupported-file aggregation with harvested
  reasons, harvested camera inference recorded with the real PreCut pin
  (`agrees_with_declaration` computed but declared `kind` always wins),
  shoot dates read from `st_birthtime` with an mtime fallback and no
  confirmation step (as ratified), brand-asset staging into a
  `Brand Assets` sibling directory, Brand Brief generation against the
  staged copy, the append-only handoff record, and a hard gate that the
  emitted manifest passes handoff validation.
  **Verified end to end by the Lead on a realistic fake shoot**, not
  just by tests: a project folder now opens as footage folders + Brand
  Assets + manifest.json, exactly the "hand one folder to a human
  editor" shape Ryan asked for. A late-footage re-run bumped `revision`
  1→2, appended the new source with a fresh id, left all prior ids
  frozen, appended a second handoff entry, and added exactly one file to
  disk — footage never copied, which is the ruling's whole point.
  Judgment call accepted (logged, overridable by Ryan):
  `agrees_with_declaration` is false when an `aroll`/`source_audio`
  source infers `drone`/`aerial`/`timelapse` tags; `broll`/`assets`
  always agree, since B-roll can legitimately be anything.
  **Phase 2 exit criterion met.** Next role: Assistant Editor (Phase 4
  flagship), gated on the Phase 3 benchmark, which needs Ryan to
  nominate a past project.
- **2026-09-01 — Contract amended: an empty `delivery_targets` is not a
  warning.** Caught by watching a real PM run print it. §4.2 listed it,
  but Open Q 1's ratification had since made the field
  Creative-Editor-owned and deliberately absent at PM handoff, so the
  rule fired on the *correct* state and would have trained everyone to
  tune warnings out. Removed from the contract and the validator; the
  check moves to the Creative Editor's own validation in Phase 6, where
  an empty list genuinely means something is missing. (Lead edit to an
  Architect-owned doc, made rather than re-dispatching an agent for two
  lines; noted here for the record.)
- **2026-09-01 — Generated deliverables are published copy: no em dashes.**
  The Brand Brief's README and card land in Ryan's projects and get handed
  to human editors, which puts them under his published-copy style rule
  (em dashes are the most-recognized AI tell). Repo docs, code comments,
  and docstrings are exempt; anything the pipeline *generates* for a
  project is not. Enforced by a test that scans both the generated README
  and the card's rendered string literals, so it can't quietly come back.
  **This generalizes:** every later role that writes project-facing text
  (marker names, sequence names, captions, the Creative Editor's output)
  inherits the same rule.
- **2026-09-01 — Phase 2 slice 2 shipped: the Brand Brief generator**
  (`posthouse/brandbrief.py`, 33 tests). Font families parsed from TTF/OTF
  name tables via fontTools (never dropped: a corrupt font degrades to
  `extracted_by: "filename"`), macOS install-status by directory scan,
  deterministic palette extraction (fixed MEDIANCUT quantize, sorted by
  descending pixel count with ascending-hex tiebreak), README + 1920x1080
  card PNG written inside `assets_dir` with the co-location invariant
  enforced in code. Deliberately out of scope this slice: PDF
  summarization (`summarized` stays false, no LLM call) and the frame-0
  creative-brief marker (`marker_written` stays false, that's the
  exporter's job). No golden PNG is committed: text rendering drifts
  across PIL/font versions, so the card is asserted on structural
  properties plus byte-identical determinism across consecutive runs,
  while the brand *data* gets a normal golden.
  **Three defects found by looking at a real rendered card, which the
  tests could not catch, all fixed with regression tests:** em dashes in
  the generated copy (see the entry above); the card printing bare counts
  ("DOCUMENTS: 1") instead of naming files and their harvested
  not-importable reasons, which defeats the card's whole purpose; and a
  palette role heuristic that ranked purely by frequency, labelling
  SoldFast's vivid orange "neutral" because it was least common. Roles now
  gate `neutral` on HLS saturation and rank only the chromatic colours.
- **2026-09-01 — Phase 2 slice 1 shipped: the manifest builder/validator**
  (`posthouse/manifest.py`, 60+ tests). Four implementation judgment calls
  made against gaps the ratified contract didn't specify — all accepted by
  the Lead, none needed to go back to Ryan:
  - **`revision` bumps on every save to an existing path**, using
    file-existence as the signal; no content-diffing. Simple and
    deterministic; a no-op save still bumps. Revisit only if the number
    inflating in practice actually bothers anyone.
  - **`delivery_targets` absence is enforced by API shape** —
    `build_manifest` has no such parameter and never writes the key, so
    §2.5's "PM never proposes" ruling can't be violated by accident. The
    contract's §2.1 default column now cross-references §2.5 to stop the
    next implementer writing `[]` there (a near-miss this build caught).
  - **Aggregate `unsupported[].reason` phrasing** for the four categories
    with a harvested per-file reason follows the contract's one worked
    example by parallel structure, embedding the harvested string
    verbatim rather than paraphrasing.
  - **`project.people[].id` collisions** reuse the `-NN` scheme; the bare
    slug is implicitly 01, so a second "Sam" is `sam-02`.
  Code review (high effort) found two real defects, both fixed with
  regression tests before commit: nested-source kind conflicts were
  undetected (only exact-path duplicates were caught, so a `broll` folder
  nested inside an `aroll` folder passed handoff validation silently —
  contract §4.1 rule 5 requires rejecting it), and `_mint_person_id`
  contradicted its own docstring (`-01` vs the documented `-02`).
  Nesting is compared as path parts, not string prefixes, so
  `/proj/A-roll` is correctly not "inside" `/proj/A`.
- **2026-09-01 — Cross-clip speaker naming: verified as real work,
  then de-scoped entirely.** First verified (web search, not assumed)
  that Premiere's built-in Speech to Text cannot do it — "rename all
  instances" is scoped to one transcript, and cross-clip speaker
  tracking is a confirmed, currently-open Adobe feature request, so it
  isn't something to borrow from the NLE. Then, on reflection the same
  day, **Ryan de-scoped the feature itself**: generic "Speaker 1" /
  "Speaker 2" style labels are sufficient — no cross-clip voice
  identity, matching, or propagating rename is needed anywhere in the
  Post House. Net effect: Phase 4 (Assistant Editor) needs no custom
  diarization/voice-matching work at all; whatever speaker separation
  the AE's transcription step naturally produces is the finished
  feature.
- **Inherited from PreCut DECISIONS.md:** FCP7 XML is the delivery path;
  no CEP/UXP panel code; markers replace B-roll clips until matching
  precision is proven; API key (not OAuth) for Claude; deterministic
  motion tags. (Item 5 of that doc superseded per above.)
