# TEAM.md — Engineering team charter

How the AI engineering team that *builds* the Post House is organized.
(Not to be confused with the post-production roles the product itself
fills — those are in `ROADMAP.md`. This file is about who builds them.)

## Reality check, stated up front

Agents in this system do not persist between sessions and hold no memory
of their own. A "role" is a hat: a role definition + model assignment +
the docs in this repo, instantiated as a subagent (or a session) when a
phase needs it, and dissolved when its task ends. **The repo is the
team's entire institutional memory.** That is why the doc rules below are
strict: they are not bureaucracy, they are the team's brain.

Consequence: we do not staff all roles at all times. Each phase activates
only the roles it needs (see Activation below). An idle role costs
nothing; an active one costs real tokens.

## Roles

| Role | Owns (single writer) | Model tier | Responsibilities |
| --- | --- | --- | --- |
| **Supervisor** | Final say on product & creative | Ryan (human) | Approves phases, judges output quality, nominates benchmark, settles escalations that reach him |
| **Lead / Orchestrator** | `CLAUDE.md`, `ROADMAP.md` (incl. Decision Log), `docs/STATUS.md`, all commits/pushes | Fable 5 | Plans, delegates, reviews and merges all work, owns escalations, keeps repo current |
| **Product Manager** | `docs/REQUIREMENTS.md` (when created) | Opus 5 | Turns Ryan's workflow descriptions into testable requirements and acceptance criteria per phase |
| **Lead Architect** | `docs/ARCHITECTURE.md`, data contracts | Opus 5 | System design, interface changes, adversarial review of plans before build |
| **Senior Engineer (pipeline/backend)** | implementation branches | Sonnet 5 | Python pipeline work, PreCut-client skills, ffmpeg/media code |
| **QA / Test Engineer** | `tests/`, fixtures, benchmark harness | Sonnet 5 | Phase 0 safety net, golden-master XML test, scoring harness, regression gates |
| **Code Reviewer** | review reports | Opus 5 (via `/code-review` at high effort) | Reviews every diff before the Lead merges; findings are bug reports, not suggestions |
| **Security Reviewer** | security review reports | Sonnet 5 (via `/security-review`) | API-key handling, subprocess/path safety, anything touching Ryan's filesystem; runs at each phase close |
| **Frontend Engineer** | UI branches | Sonnet 5 | Dormant until a phase earns UI (Roadmap rule 7) |
| **Docs / Housekeeping** | formatting, indexes, summaries | Haiku 4.5 | Mechanical doc upkeep, log summarization, changelog assembly. Substantive docs are written by the role that did the work, never delegated down |

## Model policy

Grounded in current pricing (per Mtok in/out): Fable 5 $10/$50 ·
Opus 5 $5/$25 · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5.

- **Fable 5** — orchestration and the hardest reasoning only: cross-repo
  architectural calls, debugging that has beaten Opus, taste-critical
  prompt design for the product's own AI stages. Most expensive; used
  deliberately, not by default.
- **Opus 5** — architecture, adversarial review, algorithmically tricky
  design (audio sync, FCP7 XML edge cases, cull segmentation logic).
- **Sonnet 5** — the default for implementation, tests, and most tasks.
  When in doubt, start here.
- **Haiku 4.5** — mechanical work with unambiguous specs: formatting,
  summarizing, boilerplate, file shuffling.

### Escalation ladder (two-strikes rule — a LEAD obligation)

Subagents have no memory and cannot re-dispatch themselves, so attempt
counting and escalation are the Lead's job, backed by a durable ledger:

1. Before every dispatch of a previously-attempted task, the Lead writes
   a row to `docs/STATUS.md` § Attempts (task · tier · attempt # · what
   was tried · why it failed) and pastes the prior rows into the new
   agent's brief.
2. After two failed attempts at a tier, the Lead escalates the task one
   tier up — never a third silent retry at the same tier.
3. Sonnet → Opus → Fable (Lead) → **Ryan**. Product/creative ambiguity
   skips straight to Ryan — no model tier resolves a taste question.
4. Agents escalate *disagreement* by reporting, not by acting: an agent
   that believes a logged decision is wrong says so in its report with
   reasoning; it never codes around it. The Lead carries it up.

## Anti-conflict rules

1. **Single writer per doc/path.** The ownership column above is
   exclusive. Everyone else proposes via review notes; the owner edits.
2. **Decision Log is append-only and Lead-written.** Reversing a
   decision requires a new entry that cites the old one and the reason.
   Nothing is ever silently deleted.
3. **Read before work.** Every agent's brief includes the required
   reading order from `CLAUDE.md`. An agent that hasn't read STATUS and
   the Decision Log is not allowed to make choices, only to execute.
4. **One write-capable agent at a time.** Parallel subagents share one
   working tree, so partitioning by intention is not a mechanism. The
   enforceable rule: at most ONE write-capable subagent runs at a time;
   unlimited read-only agents (reviews, exploration) may run in
   parallel with it. If genuinely parallel writes are ever needed, each
   writer gets its own git worktree and the Lead merges.
5. **Subagents never commit or push.** They produce diffs, reports, and
   files; the Lead reviews, commits, and pushes. One integration point,
   one coherent history.
6. **Review is not override.** Reviewers report findings; the owning
   engineer (or the Lead) applies fixes. A reviewer never rewrites
   another role's work directly.
7. **Contradictions halt, not fork.** If two artifacts disagree (doc vs
   doc, doc vs code), work on that area stops until the Lead reconciles
   them and logs the resolution. Building on a known contradiction is
   the one unforgivable sin here.

## Activation by phase

| Phase | Active roles |
| --- | --- |
| Planning (now) | Lead, Architect, PM |
| 0 — Safety net | Lead, QA, Senior Eng, Reviewer |
| 1 — Headless driver | Lead, Senior Eng, QA, Reviewer |
| 2 — Benchmark | Lead, QA, PM (metrics definition), Ryan (answer key) |
| 3 — Cull | Lead, Architect (segmentation design), Senior Eng, QA, Reviewer, Security |
| 4+ | Per roadmap; Frontend remains dormant until UI is earned |

## How work physically happens

The Lead runs in a Claude Code session. Roles are instantiated as
subagents (Explore/Plan/general agents with an explicit model choice) or,
for large parallel work Ryan approves, as orchestrated workflows.
Subagent briefs always include: the role, the required reading, the task,
the definition of done, and the rule that output is a report/diff — not a
push.
