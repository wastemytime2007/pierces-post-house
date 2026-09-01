# The Post House — Roadmap

An AI post-production team built on top of PreCut. Ryan is the supervisor;
every other role in the house is filled, one at a time, by a skill or agent
that is built, measured, and proven before the next one starts.

This document is the working plan. It gets edited as decisions are made —
settled calls move to the Decision Log at the bottom, in the same commit
that settles them.

Related repos:

- `wastemytime2007/precut` — the shipped app (1.0.0-beta.3). Ingest,
  transcription, B-roll tagging/search, lav sync, story angles, FCP7 XML
  export to Premiere. **The foundation. Protected.**
- `wastemytime2007/precut-premiere-extension` — abandoned CEP/UXP panel.
  Reference only; per PreCut's DECISIONS.md, panel code does not come back.

---

## 1. The operating model

A real post house is a pipeline of specialists, each of whom receives
organized work from the previous station and hands better-organized work to
the next:

```
Production (footage arrives)
   └─> Project Manager      — ingest, organize by client/project/date/type
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

We are building this house with AI staff. Ryan supervises every station's
output and makes the calls the house can't.

### The two honest asymmetries in this model

Stated up front so we design around them instead of discovering them:

1. **Everything up to picture lock fits PreCut's delivery model** (write
   artifacts + one XML, editor opens Premiere). **Color and final audio
   mix do not.** They happen *inside* the NLE *after* picture lock, and
   FCP7 XML cannot carry a Lumetri grade. Audio clip gain/keyframes *are*
   representable in the XML, color is not. So the Colorist and Audio
   Designer roles need different delivery mechanics than everything else,
   and they are deliberately scheduled last (Phases 6–7).
2. **Culling is two different jobs wearing one name.** Technical culling
   (shake, blur, exposure, clipped audio, accidental recordings) is
   deterministic and measurable. Editorial culling (false starts, repeated
   takes, "the energy is off") is taste. Phase 3 builds the first and does
   not pretend to do the second.

---

## 2. Ground rules

These are constraints, not suggestions. Changing one is a Decision Log
entry.

1. **Skills are clients of PreCut, not patches to it.** The team operates
   through PreCut's JSON-lines backend protocol and its on-disk artifacts
   (`project.json`, `transcripts/`, `precut.db`, LanceDB vectors, `plans/`,
   exported XML). New capabilities live outside the app until proven, then
   migrate in behind the golden-master test.
2. **No change to the PreCut app without the safety net.** Phase 0's
   golden-master XML test and fixture project must pass before and after
   any app change. PreCut currently has zero tests; its own history (a
   silent regression shipped for a month) is the proof this rule earns its
   keep.
3. **Deterministic before generative.** If ffmpeg + numpy can compute it,
   a model does not get asked to guess it. (PreCut's motion tags already
   follow this rule; it holds for the whole house.)
4. **Every skill ships with its measurement.** A skill without a score
   against the benchmark project is not done, no matter how good its demo
   looks.
5. **Suggestions must be earned into placements.** PreCut deliberately
   demoted auto-placed B-roll to markers because matching wasn't
   trustworthy. Any move from "suggest" to "place on the timeline" is
   gated on a measured precision number, decided per skill in advance.
6. **The AI does the comprehension. The editor does the editing.**
   (Quoted from PreCut's own story planner.) Every station prepares
   decisions for Ryan; no station hides them from him.
7. **No UI until the pipeline earns it.** Skills produce inspectable
   artifacts (sequences in XML, CSVs, reports). Interface work comes only
   after the underlying skill is measured and trusted.
8. **Every working session ends with something Ryan can test on real
   footage in under five minutes.**

---

## 3. Role → skill map, with honest feasibility grades

| Role | What it does here | Already in PreCut? | Feasibility | Phase |
| --- | --- | --- | --- | --- |
| Project Manager | Ingest, organize by client/project/date/type, bins | Largely yes (ingest, camera inference, bin layout) | **A** — extend conventions | 4 |
| Assistant Editor: sync | "All Footage Synced" sequence from lav sync | Yes (audalign + cross-validation) | **A** — repackage as sequence | 4 |
| Assistant Editor: technical cull | Usable in/out segments → "Cold Footage" timeline | No | **A** — deterministic, measurable | **3 (flagship)** |
| Assistant Editor: subject grouping | Per-subject cold-footage sequences | Partially (CLIP tags, theme categories) | **B+** — clustering on existing index | 4 |
| Assistant Editor: transcript flagging | Color-coded storyline ranges visible on a timeline | Partially (story angles find ranges) | **B+** — new rendering of existing output | 4 |
| Creative Editor: story + assembly | Selects → assembled cut from a brief | Yes, v1 (angles → ranges → sequence + markers) | **B** — improve, then measure | 5 |
| Creative Editor: music | Tone-matched music from the Artlist library | No | **B−** — see Artlist reality below | 5 |
| Creative Editor: SFX | SFX placement suggestions | No (SFX bin exists, empty logic) | **B−** | 5 |
| Creative Editor: B-roll placement | Real clips on V2 instead of markers | Demoted by design | **Gated** on benchmark precision | 5 |
| Audio Designer | Loudness analysis → clip gain in XML + report | No | **B** — measurement solid, application partial | 6 |
| Colorist | Exposure/contrast QC report; a real grade | No | **C** — QC report yes; grading needs different tech | 7 |
| Supervisor loop | Structured notes → revised cut | No | **B** — protocol design, not ML | 5+ |

**The Artlist reality:** Artlist has no public search/download API. The
music skill therefore works against a **local library**: tracks Ryan has
downloaded under his subscription, indexed by the house (BPM, energy,
instrumentation, mood — computed locally, plus Artlist's own metadata where
we can capture it at download time). The skill searches that index; it does
not browse Artlist. This means the library only knows tracks Ryan has
pulled — a real limitation, stated now rather than discovered in Phase 5.

**The color reality:** a real grade cannot ride in FCP7 XML. Phase 7 v1 is
a *colorist's assistant*: per-shot technical QC (under/over-exposure,
white-balance drift, mixed color temps across a sequence) delivered as a
report plus timeline markers. Actual automated grading — if we ever want
it — means either Premiere automation or a Resolve round-trip, and gets
decided then, not assumed now.

---

## 4. The Assistant Editor's cull, specified

Phase 3 is the flagship because it is the highest-value role that is
buildable with deterministic tools, and it is the job Ryan described
frame-by-frame. Spec, from that description:

For every source clip (e.g. `0001.mov`):

1. Scan the full duration. A clip yields **zero or many** usable segments
   — one clip is not one segment.
2. A segment **opens** when the camera settles: shake ends and the shooter
   is intentionally holding the shot.
3. A segment **closes** just before the next disturbance: shake resumes,
   the shooter recomposes to a new shot, focus is lost, or the recording
   ends.
4. Rejected outright: accidental record-taps, unusably short fragments,
   sustained blur, gross exposure faults, (for synced A-roll) clipped or
   dead audio.
5. Every accepted segment is placed, in source order, on a **Cold
   Footage** sequence exported through the existing XML path.

Detection stack (all local, all deterministic): global-motion estimation
for shake and for pan/tilt-vs-recompose classification (building on
`motion_analyzer.py`), Laplacian variance for blur, histogram stats for
exposure, audio peak/RMS scan for clipped or dead audio, minimum-duration
and settle-time thresholds.

Two craft details that make the output professional rather than merely
correct:

- **Handles.** In/out points are biased conservative, but each segment
  carries trim handles (default ±1s where the source allows) so the lead
  editor can slip and trim. An assistant editor who cuts exactly to the
  frame steals the editor's room to work.
- **A-roll is culled differently from B-roll.** B-roll culls on stability
  and image quality. A-roll (talking humans) culls on audio and framing
  only — a locked-off interview shot with "boring" visuals is not a
  defect, and content judgments (false starts, repeated takes) are Phase
  5+ territory via the transcript, not Phase 3 image heuristics.

Output artifacts: `culls.json` (per-clip segment list with per-rule scores
and reasons — inspectable, diffable), plus the Cold Footage sequence in
the exported XML. Every rejection carries its reason; nothing is silently
dropped.

---

## 5. Measurement

### The benchmark project

One finished past project, nominated by Ryan: raw footage in, plus his
actual delivered edit as the **answer key**. This is the single most
valuable asset in the house. Nothing generative gets tuned without it.

- **Cull scoring:** overlap between the skill's accepted segments and the
  source ranges that actually appear in (or were plausibly considered
  for) Ryan's edit — precision (how much junk got through) and recall
  (how much usable material was missed). Recall matters more: an
  assistant editor who hides good footage is worse than one who lets a
  little shake through.
- **Grouping scoring:** do the per-subject sequences match how Ryan would
  bin the material (spot-check labeling session, ~30 minutes of his time).
- **Matching/B-roll scoring:** of the B-roll the skill would place, how
  much matches what Ryan actually cut in at those story beats. This number
  decides whether markers ever become clips (Rule 5).
- **Story scoring:** qualitative — Ryan rates generated angles against the
  story he actually told.

### Definition of done, per skill

A skill is done when: (1) it runs headless via the driver on the fixture
and the benchmark; (2) its score is recorded in this repo; (3) Ryan has
used its output on at least one real project and it saved him time; (4) its
failure modes are written down. Then, and only then, does the next role
get built. (This is the "one skill at a time, tested" principle — kept
from the original plan, with integration continuous instead of deferred.)

---

## 6. Phases

Each phase has an exit criterion. A phase without its exit criterion met
does not hand off to the next — same rule as the house itself.

### Phase 0 — Safety net *(protects everything)*
Fixture project (tiny synthesized/trimmed clips covering: stable shot,
shaky shot, blurred shot, over/under-exposed shot, a lav pair, an A-roll
with speech). Golden-master test: pipeline runs headless on the fixture,
exported XML is compared against a blessed snapshot; the six
expensive-to-learn FCP7 quirks in PreCut's DECISIONS.md are each covered.
Import/compile check for all backend modules.
**Exit:** tests pass on current `precut` main; a deliberate one-line
sabotage of the exporter is caught.

### Phase 1 — Headless driver *(the chassis)*
A skill that drives PreCut's backend end-to-end from the command line:
create project, add sources, run pipeline, generate stories, export XML.
Touches zero app code.
**Exit:** fixture project goes footage-to-XML with one command.

### Phase 2 — Benchmark
Ryan nominates the project; footage and answer key staged; scoring
harness for cull/grouping/matching written against the artifacts.
**Exit:** baseline scores for PreCut's *current* matcher are recorded —
we know today's number before improving anything.

### Phase 3 — Assistant Editor: the cull *(flagship)*
As specified in §4. Built as a client (reads proxies, writes `culls.json`,
emits Cold Footage sequence through the existing exporter).
**Exit:** precision/recall on the benchmark recorded; Ryan runs it on one
real project and the Cold Footage timeline is genuinely usable.

### Phase 4 — Assistant Editor: organization
Per-subject cold-footage sequences (clustering over the existing
CLIP/tag index; taxonomy widened beyond real-estate as needed). "All
Footage Synced" sequence. Storyline color-coding of transcript ranges
rendered onto a review timeline. Project Manager folder/bin conventions
(client/project/date/type) formalized.
**Exit:** a raw dump opens in Premiere as: synced sequence + cold footage
per subject + color-coded story timeline, with zero manual prep.

### Phase 5 — Creative Editor
Artlist local-library indexer and tone-matched music selection; SFX
suggestion pass; story assembly quality pass; B-roll clip placement **if
and only if** Phase 2/3 benchmark precision clears the bar set in
advance. Supervisor notes protocol: structured revision requests
("tighten section 2 by ~30%", "swap music: too somber") that map to
re-assembly operations — free-form vibes notes demonstrably don't work as
agent input.
**Exit:** a brief goes in; a first cut with music and SFX candidates comes
out; one full supervisor revision round-trips successfully.

### Phase 6 — Audio Designer
EBU R128 loudness analysis across the assembled cut; dialogue/music/SFX
level recommendations written as clip gain (and keyframes where needed)
into the XML; anomaly report (clipping, dead channels, hum).
**Exit:** an assembled cut re-exports with levels Ryan doesn't have to
touch for a review screening.

### Phase 7 — Colorist (assistant scope)
Per-shot exposure/WB/contrast QC report with timeline markers; sequence-
level consistency check (mixed temps, jumps between adjacent shots).
Automated *grading* is explicitly out of scope until a delivery mechanism
is chosen (Premiere automation vs Resolve round-trip) — separate decision.
**Exit:** QC report on the benchmark flags the shots Ryan agrees need
work, with acceptably few false alarms.

### Phase 8 — The house runs as one
Only now: the roles chain into a single "new project arrives" flow, with
supervisor checkpoints between stations (mirroring the real house — work
does not skip the supervisor). Cost/runtime accounting per project.
Decide what, if anything, migrates into the PreCut app proper vs stays as
the agent layer.
**Exit:** one real project goes from footage dump to reviewed first cut
with Ryan touching only supervisor checkpoints.

---

## 7. Risks and open questions

- **Artlist metadata capture.** How much mood/genre metadata can we keep
  at download time vs compute locally? Affects music-match quality.
  (Open — investigate at Phase 5 start, not before.)
- **Runtime and cost.** Full-footage motion/blur analysis is CPU-real.
  The cull must run overnight-batch acceptable on Ryan's machine; budget
  measured in Phase 3 on the benchmark, not estimated.
- **Taxonomy width.** Theme categories are tuned for real-estate/reno
  interview work. Confirmed as still the dominant vertical — but Phase 4
  clustering should not hard-depend on the fixed 14 categories.
- **Whisper timing bias.** Phrase-boundary padding already exists for a
  reason; storyline color-coding inherits the same early-end bias. Reuse
  the existing padding rather than re-deriving it.
- **Frontend source risk (PreCut).** Post-May-2026 UI source may be lost
  (PROVENANCE.md). Any UI work in the app starts with verifying the
  current source builds what's shipping. Agent-layer work is unaffected.
- **Answer-key survivorship.** The benchmark's "usable" ground truth is
  what Ryan *kept*, which under-represents footage that was usable but
  unchosen. Cull recall scoring needs a one-time human pass marking
  usable-but-unused ranges, or recall will read falsely low.

## 8. Decision Log

- **2026-08-31 — Architecture:** the AI team is built as *clients* of
  PreCut (backend protocol + on-disk artifacts), not as changes to the
  app. App changes require the Phase 0 safety net and migrate only proven
  skills.
- **2026-08-31 — Order:** safety net → driver → benchmark → cull → the
  rest. The cull (Assistant Editor, technical) is the first new role.
- **2026-08-31 — Music source:** Artlist subscription via a locally
  indexed library of downloaded tracks (no public API exists).
- **2026-08-31 — Color/audio scheduling:** Colorist and Audio Designer are
  post-picture-lock roles with different delivery mechanics; deliberately
  last. Colorist v1 is QC-report scope only.
- **2026-09-01 — Engineering team structure:** roles are hats
  instantiated per task, not standing agents; the repo is the team's
  only memory. Single-writer doc ownership, append-only Decision Log,
  subagents never push, two-strikes escalation ladder
  (Sonnet → Opus → Fable → Ryan). Full charter: `docs/TEAM.md`.
- **2026-09-01 — Model policy:** Fable 5 for orchestration and hardest
  reasoning only; Opus 5 for architecture and review; Sonnet 5 default
  for implementation; Haiku 4.5 for mechanical tasks. Escalate after two
  failed attempts, never silently retry a third time.
- **2026-09-01 — Governance docs:** `CLAUDE.md` (rules), `docs/TEAM.md`
  (charter), `docs/ARCHITECTURE.md` (system), `docs/STATUS.md` (state)
  are the coordination layer; every session ends by updating STATUS and
  pushing.
- **Inherited from PreCut DECISIONS.md:** FCP7 XML is the delivery path;
  no CEP/UXP panel code; markers replace B-roll clips until matching
  precision is proven; API key (not OAuth) for Claude; deterministic
  motion tags.
