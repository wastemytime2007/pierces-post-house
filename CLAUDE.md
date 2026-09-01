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

## Required reading order (every agent, every session)

1. This file — the rules.
2. `docs/STATUS.md` — where we are right now and what's next.
3. `ROADMAP.md` — the phase plan and Decision Log.
4. `docs/TEAM.md` — your role, its boundaries, and the anti-conflict rules.
5. `docs/ARCHITECTURE.md` — the system you're working on.

Do not start work before reading 1–2. Do not make design choices before
reading 3–5.

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
5. **No code before its phase.** We are in planning/architecture. Code
   starts with Phase 0 when the supervisor green-lights it.
6. **Supervisor is Ryan.** Product, creative, and taste calls are his.
   Agents surface options with a recommendation; they do not decide for
   him on those axes.

## Where things run

- Planning, code, and tests: Claude Code sessions (cloud or local).
- Anything touching real footage: **Ryan's Mac only** — the media never
  leaves his machine, and PreCut's pipeline runs there.
