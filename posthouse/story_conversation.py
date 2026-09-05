"""The planning conversation that happens BEFORE anything gets built.

Ryan, 2026-09-04, on why this exists: *"it feels like the app is doing
the tasks to check them off but not learning anything to apply to its
planning... The research is meant to inform the planning of the reels
and videos. The trends are meant to be applied to the edit on the
timeline that is pitched. The steps exist to inform the next step not to
just check off and move on."*

And on the shape it should take: *"the application needs to kind of have
a conversation with the user about what it found and what it thinks is a
good game plan with the footage that they have at their disposal, but
not actually put anything together until the user tells them what their
end goal is... if the user can pitch their end goal before the ideas are
generated then that could save us a lot of money in time."*

So the flow this module implements is:

  1. `start_planning_session` — read the real footage (fragments already
     extracted by `story_architect.load_project_material`), do the trend
     research, and come back with a plain-language read of what's
     actually here plus a proposed game plan. **Nothing is generated
     yet.** If the editor stated an intent up front, that intent
     redirects the research itself (see `research_trends`'s
     `stated_intent`) rather than being applied after the fact.
  2. `continue_planning_session` — a real back-and-forth. The editor
     pushes back, adds a constraint, or answers a question; the plan
     changes.
  3. `generate_from_planning_session` — only when the editor explicitly
     asks for it (a button, never a phrase parsed out of their reply).
     The resolved intent and target length from the conversation are
     passed into generation as REAL constraints.

Turns are discrete jobs over persisted session state, not one long-lived
paused job — the backend has no pause-and-resume primitive and doesn't
need one for this (same pattern `refine_idea` already uses).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from precut_pipeline.anthropic_client import build_anthropic_client

from posthouse.story_architect import (
    ANTHROPIC_MODEL,
    _extract_json,
    _format_research_for_llm,
    load_project_material,
    research_trends,
    run_generate_story_angle,
)


class PlanningError(Exception):
    """Raised when a planning turn can't be completed for a real reason."""


# How many fragment topic labels to show the planner. The conversation
# needs to know what's IN the footage, not every fragment's full text —
# the generator gets the complete set later. Keeps a chatty, multi-turn
# flow from re-sending a huge fragment dump on every single reply.
MAX_DIGEST_FRAGMENTS = 120


PLANNER_SYSTEM_PROMPT = """You are a working video editor's planning partner, talking with them \
about footage they have already shot, before either of you builds anything.

You have three real inputs: what is actually in their footage (extracted transcript fragments), \
live trend/format research for their audience, and whatever they've told you they want to make.

How to behave:

- **Talk like an editor, not a report.** Short paragraphs, plain language. No headers, no bullet \
lists unless you're genuinely listing options, no restating their own goal back at them as if \
it were an insight.
- **Lead with what's actually in the footage.** Name the real, specific things you found — the \
actual topics, the actual moments — not categories. "There's a full walkthrough of steaming \
wallpaper and getting the glue off, and separately a bit where he reads a low cabinet as an ADA \
accommodation" beats "there is renovation content and character content."
- **Then say what you'd build and why**, tied to what the research actually says about the \
format. If the research says pieces like this run 30-60 seconds and open on the problem, say \
that, and say what that means for what you'd cut.
- **Be honest about what the footage can't do.** If they've asked for something the material \
genuinely doesn't support, say so directly and say what it CAN support instead. Never quietly \
substitute an easier piece.
- **Ask at most one real question per turn**, and only when the answer would actually change the \
plan. If you have what you need, say so and stop asking.
- Never claim a trend, sound, or format you weren't actually given in the research. If the \
research came up empty on something, say it came up empty.
- Do not write the cut. No fragment lists, no timecodes, no shot-by-shot sequence. You are \
agreeing on the plan; the generator builds it afterward.

Return ONLY this JSON, in a fenced ```json block:

{
  "message": "what you're saying to the editor this turn — plain prose, no markdown headers",
  "resolved_intent": "one or two sentences capturing what you both now understand the piece to be. If they haven't said yet, your best current proposal. This is what the generator will actually be told to build.",
  "target_duration_sec": <number of seconds the tight cut should run, based on the format you're proposing and anything they've said about length. Use a real number, and keep it honest to the format — a Reel is not 12 minutes.>
}"""


PLANNER_OPENING_TEMPLATE = """The editor's project-level audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>
{intent_block}
What is actually in their footage — real fragments already extracted from the transcripts:

<footage>
{footage_digest}
</footage>

Live trend/format research for this audience:

<research>
{research}
</research>

Open the conversation: tell them what you actually found in this footage, what the research says \
about how pieces like this are working right now, and what you'd build with it. Then either ask \
the ONE question you genuinely need answered, or say you have what you need."""


PLANNER_REPLY_TEMPLATE = """Continuing the same planning conversation.

<audience_goal>
{audience_goal}
</audience_goal>

<footage>
{footage_digest}
</footage>

<research>
{research}
</research>

The conversation so far:

{transcript}

The editor just said:

<editor>
{user_message}
</editor>

Respond. If they redirected you, actually change the plan — don't restate the old one with new \
words. If they answered your question, move forward with it. Keep `resolved_intent` and \
`target_duration_sec` current with wherever the plan now stands."""


@dataclass
class ConversationTurn:
    role: str          # "assistant" | "editor"
    text: str
    at: float = field(default_factory=time.time)


@dataclass
class PlanningSession:
    """One planning conversation about one project's footage.

    Persisted whole on every turn — these are small, and a rewrite is
    simpler and safer than an append protocol when a turn can fail
    partway through.
    """
    session_id: str
    audience_goal: str
    stated_intent: str = ""          # what the editor pitched up front, if anything
    resolved_intent: str = ""        # where the conversation has landed (planner-maintained)
    target_duration_sec: float = 0.0
    footage_digest: str = ""
    research: dict = field(default_factory=dict)
    turns: List[ConversationTurn] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    generated_idea_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["turns"] = [asdict(t) for t in self.turns]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PlanningSession":
        turns = [ConversationTurn(**t) for t in d.get("turns", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known and k != "turns"}, turns=turns)


def sessions_dir(project) -> Path:
    d = project.dir() / "planning_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_path(project, session_id: str) -> Path:
    return sessions_dir(project) / f"{session_id}.json"


def save_session(project, session: PlanningSession) -> Path:
    path = session_path(project, session.session_id)
    path.write_text(json.dumps(session.to_dict(), indent=2))
    return path


def load_session(project, session_id: str) -> PlanningSession:
    path = session_path(project, session_id)
    if not path.exists():
        raise PlanningError(f"No planning session {session_id} for this project.")
    return PlanningSession.from_dict(json.loads(path.read_text()))


def latest_session(project) -> Optional[PlanningSession]:
    """Most recently created session, or None. Lets the UI reopen the
    conversation the editor was already having instead of starting a
    fresh one every time the tab mounts."""
    paths = [p for p in sessions_dir(project).glob("*.json") if not p.name.startswith(".")]
    if not paths:
        return None
    newest = max(paths, key=lambda p: p.stat().st_mtime)
    try:
        return PlanningSession.from_dict(json.loads(newest.read_text()))
    except Exception:
        return None


def _build_footage_digest(tagged_by_source) -> str:
    """A compact, real read of what's in the footage: every fragment's own
    topic label, grouped by source file. Deliberately NOT the full
    fragment text — the planner needs to know what's here to talk about
    it; the generator gets the complete set when it actually builds."""
    lines: List[str] = []
    total = 0
    for stem, tagged in sorted(tagged_by_source.items()):
        lines.append(f"\n{stem} ({len(tagged)} fragments):")
        for tf in tagged:
            if total >= MAX_DIGEST_FRAGMENTS:
                lines.append("  ... (more fragments not listed here)")
                return "\n".join(lines)
            f = tf.fragment
            mins = int(f.source_start_sec // 60)
            secs = int(f.source_start_sec % 60)
            lines.append(f"  [{mins:d}:{secs:02d}] {f.topic_label}")
            total += 1
    return "\n".join(lines)


def _format_transcript(turns: List[ConversationTurn]) -> str:
    out = []
    for t in turns:
        who = "YOU" if t.role == "assistant" else "EDITOR"
        out.append(f"{who}: {t.text}")
    return "\n\n".join(out)


def _planner_call(user_prompt: str, model: str, api_key: Optional[str]) -> dict:
    """One planning turn. Returns the parsed {message, resolved_intent,
    target_duration_sec} dict, or raises — never invents a turn."""
    client = build_anthropic_client(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        temperature=0.6,
        system=PLANNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise PlanningError(
            "The planner's reply was cut off mid-response (hit the token limit) — "
            "not showing a truncated plan as if it were complete."
        )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise PlanningError("Empty response from the planner.")
    data = _extract_json(text)
    message = str(data.get("message", "")).strip()
    if not message:
        raise PlanningError("The planner returned no message text.")
    try:
        target = float(data.get("target_duration_sec") or 0.0)
    except (TypeError, ValueError):
        target = 0.0
    return {
        "message": message,
        "resolved_intent": str(data.get("resolved_intent", "")).strip(),
        "target_duration_sec": target,
    }


def start_planning_session(
    project, job_id: str, emit, stated_intent: str = "",
    model: str = ANTHROPIC_MODEL, api_key: Optional[str] = None,
) -> None:
    """Research + read the footage, then open the conversation with a real
    proposed game plan. Generates NO ideas — that's the whole point: the
    editor gets to redirect before anything expensive runs."""
    emit({"type": "story_plan_started", "job_id": job_id})

    def emit_with_job(ev):
        ev.setdefault("job_id", job_id)
        if ev.get("type") == "producer_error":
            ev["type"] = "story_plan_error"
        emit(ev)

    audience_goal, tagged_by_source = load_project_material(project, emit_with_job)
    if not audience_goal or not tagged_by_source:
        return  # load_project_material already emitted a real reason

    stated_intent = (stated_intent or "").strip()
    try:
        if stated_intent:
            emit({"type": "log", "level": "info", "job_id": job_id,
                  "message": "Researching trends and formats for what you asked for "
                             "specifically (not a generic sweep of the niche)..."})
        else:
            emit({"type": "log", "level": "info", "job_id": job_id,
                  "message": "Researching live trends for this project's audience goal..."})
        research = research_trends(audience_goal, model=model, api_key=api_key,
                                   stated_intent=stated_intent)
        if research.get("cached"):
            emit({"type": "log", "level": "info", "job_id": job_id,
                  "message": "Reused research from the last 72 hours — no new search "
                             "or video calls made this run."})

        footage_digest = _build_footage_digest(tagged_by_source)
        intent_block = ""
        if stated_intent:
            intent_block = (
                f"\nWhat the editor has already told you they want to make:\n\n"
                f"<stated_intent>\n{stated_intent}\n</stated_intent>\n"
            )

        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": "Working out a game plan from the footage and the research..."})
        result = _planner_call(
            PLANNER_OPENING_TEMPLATE.format(
                audience_goal=audience_goal.strip(),
                intent_block=intent_block,
                footage_digest=footage_digest,
                research=_format_research_for_llm(research),
            ),
            model, api_key,
        )
    except Exception as e:
        emit({"type": "story_plan_error", "job_id": job_id, "message": str(e)})
        return

    session = PlanningSession(
        session_id=f"plan_{uuid.uuid4().hex[:10]}",
        audience_goal=audience_goal,
        stated_intent=stated_intent,
        resolved_intent=result["resolved_intent"],
        target_duration_sec=result["target_duration_sec"],
        footage_digest=footage_digest,
        research=research,
    )
    if stated_intent:
        session.turns.append(ConversationTurn(role="editor", text=stated_intent))
    session.turns.append(ConversationTurn(role="assistant", text=result["message"]))
    save_session(project, session)

    emit({"type": "story_plan_turn", "job_id": job_id, "session": session.to_dict()})


def continue_planning_session(
    project, job_id: str, emit, session_id: str, user_message: str,
    model: str = ANTHROPIC_MODEL, api_key: Optional[str] = None,
) -> None:
    """One more turn of the back-and-forth. Reuses the session's already-
    paid-for research and footage digest — a reply costs one small call,
    not another research pass."""
    emit({"type": "story_plan_started", "job_id": job_id})
    user_message = (user_message or "").strip()
    if not user_message:
        emit({"type": "story_plan_error", "job_id": job_id, "message": "Empty message."})
        return

    try:
        session = load_session(project, session_id)
        session.turns.append(ConversationTurn(role="editor", text=user_message))
        result = _planner_call(
            PLANNER_REPLY_TEMPLATE.format(
                audience_goal=session.audience_goal.strip(),
                footage_digest=session.footage_digest,
                research=_format_research_for_llm(session.research),
                transcript=_format_transcript(session.turns[:-1]),
                user_message=user_message,
            ),
            model, api_key,
        )
    except Exception as e:
        emit({"type": "story_plan_error", "job_id": job_id, "message": str(e)})
        return

    session.turns.append(ConversationTurn(role="assistant", text=result["message"]))
    if result["resolved_intent"]:
        session.resolved_intent = result["resolved_intent"]
    if result["target_duration_sec"]:
        session.target_duration_sec = result["target_duration_sec"]
    save_session(project, session)

    emit({"type": "story_plan_turn", "job_id": job_id, "session": session.to_dict()})


def generate_from_planning_session(project, job_id: str, emit, session_id: str) -> None:
    """The expensive step, run only when the editor explicitly asks for it.

    Hands the conversation's resolved intent and agreed length to the
    existing generator as real constraints. Emits the same `producer_*`
    events `run_generate_story_angle` always has, so the existing idea-
    tracking UI works unchanged."""
    try:
        session = load_session(project, session_id)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    # The resolved intent (what the conversation landed on) is what the
    # GENERATOR should build to. But it must never be used as a research
    # cache key: it's a fresh sentence every turn, so it would miss cache
    # every single time and re-run the expensive research. Research is
    # passed in explicitly below instead.
    intent = (session.resolved_intent or session.stated_intent or "").strip()
    if intent:
        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": f"Building to the plan you agreed: {intent}"})
    if session.target_duration_sec:
        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": f"Target length for the tight cut: "
                         f"~{session.target_duration_sec:.0f}s (enforced — a cut that "
                         f"overruns it is rejected and retried)."})

    run_generate_story_angle(
        project, job_id, emit,
        stated_intent=intent,
        max_duration_sec=session.target_duration_sec,
        # Already paid for when this conversation opened — reuse it
        # rather than buying a second, near-identical research pass
        # (real cost bug, see run_generate_story_angle's comment).
        research=session.research or None,
    )
