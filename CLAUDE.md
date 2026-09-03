# CLAUDE.md — Pierce's Post House (coordination repo)

This repo is the **home of the Pierce's Post House application and its
coordination hub**. **The end product is one application — a fork of
PreCut's own Tauri/React shell, at `app/` in this repo — that absorbs
PreCut's code and functionality and grows the post-house roles (Project
Manager → Assistant Editor → Creative Editor → …) into it as new screens.**
Ryan does not want two apps to run side by side, even temporarily; there is
only ever one app to open. **PreCut's own GitHub repo (`~/precut-checkout`)
is the protected donor** — read from, copied from, never committed to,
never modified — while it remains Ryan's production tool. "Harvested, never
rebuilt" means nothing PreCut already solved gets reimplemented from
scratch; it means copying and extending the working code, not calling an
external app from a separate one. This repo holds the plans, decisions,
team charter, current status, the safety net (`safety_net/`), the absorbed
app (`app/`), and the Python role logic (`posthouse/`), so that any session
or agent can pick up exactly where the last one left off.

## What to read, and when

**Always, before starting:** this file, `docs/REQUIREMENTS.md` (Ryan's founding
words — why this project exists and what it must do, quoted verbatim so
they can't be lost to compaction again), and `docs/STATUS.md` § Current
stage and § Next.

Everything else is **loaded on demand**, when its trigger fires. Do not
read these speculatively; they total ~3,800 lines and reading them all
to answer one question is the failure mode this section exists to
prevent.

| Load this | When |
| --- | --- |
| `ROADMAP.md` § Decision Log | Before a design choice that might contradict a settled one, or to find out *why* something is the way it is. Search it, don't read it front to back. |
| `ROADMAP.md` § phases | When planning what comes after the current work. |
| `docs/TEAM.md` | When acting as a named role, or dispatching subagents. |
| `docs/ARCHITECTURE.md` | Before touching PreCut integration or the three doors. |
| `precut-capabilities` skill | **Before writing any new code for any role.** Confirms whether PreCut already does it. Skipping this produced a real, costly duplicate build (2026-09-02) — see the skill's own header. |
| `docs/contracts/*.md` | When producing or consuming that artifact (manifest, culls). |
| `docs/design/PHASE4_CULL_DESIGN.md` | Phase 4 cull work only. Currently parked — see STATUS. |
| `docs/STATUS.md` § Done | To check whether something was already tried, and what the evidence was. |

If a task turns out to need a document you skipped, load it then. That
is cheaper than every session paying for every document up front.

## Related repos

| Repo | What | Status |
| --- | --- | --- |
| `wastemytime2007/precut` | The shipped PreCut app (Tauri + React + Python). The foundation. | **Protected** — see rules |
| `wastemytime2007/precut-premiere-extension` | Abandoned CEP/UXP panel | Reference only, never revive |
| `wastemytime2007/pierces-post-house` (this repo; being renamed from `test`) | Coordination hub | Active |

## Non-negotiable rules

1. **PreCut is protected, and nothing it already does gets rebuilt.** No
   commits to `precut`, ever, while it remains Ryan's production tool.
   The safety net (`safety_net/`) runs against a PreCut checkout via
   `PRECUT_ROOT` so it can exist without touching the protected repo.
   New capability is built as a *client* of PreCut, through the three
   doors in `docs/ARCHITECTURE.md` — never as edits to it, and never as
   a from-scratch reimplementation of something it already does. Load
   the `precut-capabilities` skill before writing new code for any role
   to check which of these two mistakes you'd be making.
2. **The Decision Log is law.** Settled decisions live in `ROADMAP.md`
   § Decision Log and in PreCut's own `DECISIONS.md`. No agent may act
   against a logged decision. If you believe one is wrong, write an
   escalation note in `docs/STATUS.md` § Escalations and stop that line
   of work — do not code around it.
3. **Docs have single writers.** Each document has one owning role (see
   `docs/TEAM.md`). Everyone else proposes changes via review notes;
   only the owner edits. Append-only governs the **Decision Log section
   only**; the rest of ROADMAP.md is freely revisable by its owner (the
   Lead).
4. **Every working session ends with the repo current.** Update
   `docs/STATUS.md` (stage, done, next, escalations), append any new
   decisions to the Decision Log, commit, push. A session that doesn't
   push its state didn't happen. STATUS § Done records only **completed
   and verified** work, each entry citing its evidence (commit, file, or
   artifact); work merely started or planned goes under § In progress.
   Writing an unearned Done entry corrupts the team's only memory.
5. **Every slice ends in something Ryan can judge.** Slice by outcome,
   never by component: a chain of "small" steps that each build a layer
   of machinery still produces nothing usable until the last one lands,
   which is how Phase 4 burned three days. Crude and visible beats
   sophisticated and invisible. If the first thing Ryan could look at is
   more than one step away, re-cut the plan.
6. **Supervisor is Ryan.** Product, creative, and taste calls are his.
   Agents surface options with a recommendation; they do not decide for
   him on those axes.
7. **Prove on one unit before scaling.** Every new capability starts on
   the single smallest real unit that exercises it — one clip, one
   transcript, one interview — and does not touch a second unit until
   Ryan has reviewed and confirmed the result on the first. This applies
   to every future capability, not just the one that taught it (Phase
   4's motion cull went straight to a full project). Broadening happens
   as its own separate, approved step, never folded into the step that
   proved the first unit.
8. **One role in flight at a time — superseded in part, 2026-09-03.**
   Originally: the next role (Project Manager → Assistant Editor →
   Creative Editor → Colorist → Audio Designer) does not start — not
   planning, not code — until Ryan has reviewed, tested on real
   material, and explicitly signed off on the current one. Ryan's own
   words changing this: "Lets just handle all of the skills across the
   board, Once we get each of them working, then we can worry about how
   to display them and under which role." The **role-sequencing gate is
   lifted**: skills from any role in `ROADMAP.md`'s § Role → skill map
   can be built in parallel, without waiting on a prior role's sign-off,
   and role/UI assignment is deliberately deferred until the underlying
   skills work. What does **not** change: rule 7 below (prove on one
   real unit before broadening any single skill) and the sign-off bar
   itself — passing tests is still not sign-off; Ryan reviewing real
   output on real material and saying so still is, per-skill. Phase 4's
   motion cull is PARKED for a real, already-logged reason (three
   detector approaches failed on real footage, see `docs/STATUS.md`) —
   this supersession doesn't itself unpark it; that needs its own
   explicit decision from Ryan.

## Where things run

- Planning, code, and tests: Claude Code sessions (cloud or local).
- Anything touching real footage: **Ryan's Mac only** — the media never
  leaves his machine, and PreCut's pipeline runs there.
