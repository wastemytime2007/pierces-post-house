# STATUS.md — where we are

Owner: Lead. Updated at the end of every working session. This is the
first thing any resuming session reads after CLAUDE.md. § Done records
only completed, verified work with its evidence; § In progress is for
everything in flight.

## Current stage

**Phase 0 — Safety net. Green-lit by Ryan 2026-09-01. Build in
progress.**

## In progress

- Phase 0 safety-net implementation (fixtures, golden master, quirk
  tests, import gate) — QA agent (Sonnet) building in `safety_net/`,
  briefed with the review corrections (nb_frames quirk resolution,
  canonicalization list, sync excluded from Tier 1).

## Done

- 2026-08-31 — Reviewed `precut` and `precut-premiere-extension` repos
  end to end. PreCut confirmed as the foundation. *(Evidence: findings
  reflected throughout ROADMAP.md, commit 6137dfe.)*
- 2026-08-31 — `ROADMAP.md` v1 pushed. *(Commit 6137dfe.)*
- 2026-09-01 — Governance layer v1 pushed: CLAUDE.md, TEAM.md,
  ARCHITECTURE.md, STATUS.md. *(Commit fc3cabc.)*
- 2026-09-01 — Repo renamed `test` → `pierces-post-house` by Ryan; docs
  updated. *(Commit c874dee.)*
- 2026-09-01 — **Adversarial architecture review completed** (Opus
  architect agent): 14 findings, 3 blocking. All findings incorporated
  into ROADMAP/ARCHITECTURE/TEAM/CLAUDE/STATUS. Key corrections:
  golden master respecified as canonicalizing two-tier gate; safety-net
  home decided (this repo); third door (library import) declared for
  Phase 3; cull grade A→B with new-code motion pipeline and
  originals-not-proxies rule; nb_frames doc-vs-code contradiction
  resolved for the code; escalation ladder rewritten as Lead-owned
  ledger; audalign reference corrected to audio-offset-finder.
  *(Evidence: this commit and its diff.)*

## Next (in order)

1. QA agent completes Phase 0 build → Lead reviews, runs the suite and
   the sabotage check, commits.
2. Code review pass on the safety net diff before it becomes the gate.
3. Phase 1: headless driver (per ARCHITECTURE door 1 protocol rules).
4. **Ryan (when ready):** nominate the benchmark project (raw footage +
   delivered edit still on disk). Blocks Phase 2, not Phases 0–1.

## Attempts ledger

*(task · tier · attempt # · what was tried · why it failed — written by
the Lead before any re-dispatch; empty so far)*

## Escalations / blockers

*(none open)*

## Standing notes

- Coordination repo: `wastemytime2007/pierces-post-house` (renamed from
  `test` 2026-09-01; old URLs redirect). Working branch
  `claude/ai-video-editing-team-k2a66r`.
- Real-footage work runs only on Ryan's Mac (see ARCHITECTURE.md
  § Where things execute).
