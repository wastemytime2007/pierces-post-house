# CLAUDE.md — Pierce's Post House (coordination repo)

This repo is the **home of the Pierce's Post House application and its
coordination hub**. The end product is a new app whose UX walks a
project through post-house roles (Project Manager → Assistant Editor →
Creative Editor → …) with visible handoffs; **PreCut is the component
donor** — its solved problems get harvested, never rebuilt, and the
PreCut repo itself is never modified while it remains Ryan's production
tool. This repo holds the plans, decisions, team charter, current
status, the safety net (`safety_net/`), and the app's code as it grows,
so that any session or agent can pick up exactly where the last one
left off.

## What to read, and when

**Always, before starting:** this file, plus `docs/STATUS.md` §
Current stage and § Next. That is the whole mandatory set — 179 lines
as of 2026-09-02, down from 2,258 when all five docs were mandatory.

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

1. **PreCut is protected.** No commits to `precut` until the Phase 0
   safety net (fixture + golden-master XML test) exists and passes. The
   safety net itself lives in THIS repo (`safety_net/`) and runs against
   a PreCut checkout via `PRECUT_ROOT` — that is how it can come into
   being without touching the protected repo. New capability is built as
   *clients* of PreCut, not as edits to it, through the three doors in
   `docs/ARCHITECTURE.md`.
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

## Where things run

- Planning, code, and tests: Claude Code sessions (cloud or local).
- Anything touching real footage: **Ryan's Mac only** — the media never
  leaves his machine, and PreCut's pipeline runs there.
