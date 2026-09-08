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
- **Check the target length against the ACTUAL fragments you're building around, not a genre \
average.** The footage list shows each fragment's real duration. The generator CAN cut inside a \
fragment (down to word boundaries), so a long fragment is not a blocker on its own — a 167-second \
explanation can legitimately yield the tight 30 seconds that earn their place. What you should \
check is whether the material has enough distinct BEATS for the piece you're proposing: if the \
whole topic is one continuous take of a single action, a multi-beat arc will feel thin no matter \
how it's trimmed. Say so plainly, and propose a length honest to what's actually there.
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
{visual_block}
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
{visual_block}
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
    # What was actually seen on screen, if a visual check ran (2026-09-08).
    # None means nobody looked — never treat that as "nothing there".
    visual_check: Optional[dict] = None
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


_STOPWORDS = {
    "a", "an", "and", "the", "of", "to", "for", "in", "on", "that", "this",
    "with", "is", "it", "we", "us", "our", "you", "your", "make", "makes",
    "looks", "look", "like", "quick", "engaging", "fun", "watch", "type",
    "content", "video", "piece", "about", "how", "some", "into", "out",
}


def _keywords(text: str) -> set:
    import re as _re
    return {
        w for w in _re.findall(r"[a-z]{3,}", (text or "").lower())
        if w not in _STOPWORDS
    }


def _pick_anchor_fragment(tagged_by_source, stated_intent: str):
    """The fragment worth spending a visual probe on.

    2026-09-08, real bug this fixes. This used to pick the LONGEST
    `strong` fragment, full stop. On the wallpaper project that selected
    "Track lighting and 80s design trends" (381s, 13 minutes into a
    different part of the shoot) while the editor had asked for a
    wallpaper how-to. The planner was then handed frames of a man walking
    an empty house with his hands in his pockets and concluded — clearly,
    confidently, and WRONGLY — that the wallpaper footage contains no
    demonstration, talking the plan out of the piece Ryan actually wanted.
    A direct probe of the real wallpaper span (263-285s) shows the steamer
    plate and a green-handled putty knife in frame the whole time.

    A confident wrong observation is worse than no observation, so the
    anchor must be chosen by RELEVANCE to what the editor asked for, with
    duration only as a tie-break.
    """
    strong = [
        tf for tagged in tagged_by_source.values() for tf in tagged
        if getattr(tf, "fit", "") == "strong"
    ]
    if not strong:
        return None

    wanted = _keywords(stated_intent)
    if not wanted:
        # No stated intent — longest strong fragment is the best guess
        # available, and the scope warning in the visual block keeps the
        # planner from over-reading it.
        return max(strong, key=lambda tf: tf.fragment.source_end_sec - tf.fragment.source_start_sec)

    def relevance(tf):
        # The topic LABEL is what the fragment is about; the summary
        # mentions plenty of things in passing. Weighting them equally
        # tied "Wallpaper steaming process explained" with four unrelated
        # fragments whose summaries merely said the word "wallpaper", and
        # the duration tie-break then picked a 345s fragment about a
        # mysterious item found in every house. Label matches dominate.
        frag = tf.fragment
        label_hits = len(wanted & _keywords(frag.topic_label))
        summary_hits = len(wanted & _keywords(frag.summary))
        return label_hits * 3 + summary_hits

    scored = [(relevance(tf), tf) for tf in strong]
    best_score = max(sc for sc, _ in scored)
    if best_score == 0:
        # Nothing on-topic to look at. Probing an unrelated fragment is
        # exactly the failure above, so decline rather than mislead.
        return None
    tied = [tf for sc, tf in scored if sc == best_score]
    return max(tied, key=lambda tf: tf.fragment.source_end_sec - tf.fragment.source_start_sec)


def _probe_anchor_fragment(tagged_by_source, emit, job_id,
                           stated_intent: str = "",
                           project_dir=None) -> Optional[dict]:
    """Look at what is actually ON SCREEN in the fragment this piece would
    be built around, before the plan commits to it.

    The planning conversation used to be blind to the picture: it read
    transcripts only, so it had to ask Ryan by hand *"is someone
    demonstrating with the steamer, or explaining the process verbally?"*
    — the very thing that decides whether a piece can be show-don't-tell
    or is a talking head with B-roll gaps.

    One probe per session, on the fragment most relevant to the stated
    intent (see `_pick_anchor_fragment`), via the free CLI route. Costs
    minutes, not money. Returns None — never a guess — when there's
    nothing on-topic to look at or the probe fails; the conversation then
    proceeds on transcripts alone and is told so explicitly.
    """
    anchor = _pick_anchor_fragment(tagged_by_source, stated_intent)
    if anchor is None:
        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": "No strong fragment clearly matching what you asked for, so "
                         "skipping the visual check rather than looking at unrelated "
                         "footage and drawing the wrong conclusion."})
        return None

    frag = anchor.fragment
    if not Path(str(frag.source_file)).exists():
        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": f"Skipping the visual check — {Path(str(frag.source_file)).name} "
                         f"isn't reachable (drive not mounted?)."})
        return None

    emit({"type": "log", "level": "info", "job_id": job_id,
          "message": f"Looking at what's actually on screen in \"{frag.topic_label}\" "
                     f"({frag.source_end_sec - frag.source_start_sec:.0f}s) before "
                     f"planning around it. This reads real frames and takes a few "
                     f"minutes, but costs nothing."})
    try:
        from posthouse.video_probe import is_on_camera_demonstration
        # Probe a window INSIDE the fragment rather than its first
        # seconds: a long explanation often opens on preamble before the
        # work starts, and the opening frames alone would misrepresent it.
        span = frag.source_end_sec - frag.source_start_sec
        start = frag.source_start_sec + (span * 0.35 if span > 60 else 0.0)
        end = min(frag.source_end_sec, start + 40.0)
        result = is_on_camera_demonstration(
            str(frag.source_file), start, end, project_dir=project_dir)
    except Exception as e:
        emit({"type": "log", "level": "warn", "job_id": job_id,
              "message": f"Visual check failed, planning from transcripts only: {e}"})
        return None

    if not result.get("confident") or not result.get("frames_viewed"):
        emit({"type": "log", "level": "warn", "job_id": job_id,
              "message": "Visual check couldn't actually view frames — planning from "
                         "transcripts only rather than trusting an assumption."})
        return None

    result["topic_label"] = frag.topic_label
    result["probed_span_sec"] = [start, end]
    emit({"type": "log", "level": "info", "job_id": job_id,
          "message": ("Reusing the visual note from an earlier look "
                      if result.get("from_cache") else "")
                     + f"Saw it ({result['frames_viewed']} real frames): "
                     f"{str(result.get('answer',''))[:160]}"})
    return result


def _format_visual_block(probe: Optional[dict]) -> str:
    """The visual truth, handed to the planner as fact — scoped hard to the
    one span that was actually viewed, or an explicit statement that
    nobody looked.

    The scope warning is not boilerplate. On 2026-09-08 the planner was
    given frames from one fragment and generalised them into a confident
    claim about a different fragment entirely ("nobody is working... no
    steamer, no scraper"), which was false and changed the plan. Naming
    the span and forbidding extrapolation is part of the fix.
    """
    if not probe:
        return (
            "\nNO VISUAL CHECK WAS DONE this run — you are working from transcripts "
            "only. You genuinely do not know what is on screen anywhere in this "
            "footage. Do not assert that anything is or isn't demonstrated, shown, or "
            "visible; if that distinction matters to the plan, ask the editor.\n"
        )
    span = probe.get("probed_span_sec") or []
    span_txt = f"{span[0]:.0f}s-{span[1]:.0f}s" if len(span) == 2 else "an unrecorded span"
    return (
        f"\nWHAT IS ACTUALLY ON SCREEN — real frames were viewed, so this is "
        f"observation rather than inference. **It covers ONE span only:** "
        f"\"{probe.get('topic_label','')}\", {span_txt}.\n"
        f"<visual_check frames_viewed=\"{probe.get('frames_viewed')}\" "
        f"span=\"{span_txt}\" fragment=\"{probe.get('topic_label','')}\">\n"
        f"{probe.get('answer','')}\n\n"
        f"Visible in frame: {probe.get('what_is_visible','')}\n"
        f"</visual_check>\n\n"
        f"Rules for using this:\n"
        f"- It tells you about THAT span and nothing else. Do NOT extrapolate it to "
        f"other fragments, other clips, or the footage as a whole. If it says no work "
        f"is shown there, that does not mean no work is shown anywhere.\n"
        f"- If it says the work IS demonstrated on camera, you may plan a "
        f"show-don't-tell piece and cite this as why.\n"
        f"- If it says the work is NOT shown in that span, say only that — and if the "
        f"plan depends on a demonstration existing elsewhere, ask the editor rather "
        f"than concluding the footage can't support it.\n"
    )


def _build_footage_digest(tagged_by_source) -> str:
    """A compact, real read of what's in the footage: every fragment's own
    topic label and DURATION, grouped by source file. Deliberately NOT the
    full fragment text — the planner needs to know what's here to talk
    about it; the generator gets the complete set when it actually
    builds.

    Duration is not decoration here. 2026-09-07, real failure on the
    wallpaper project: the planner agreed to a 45-second Reel built
    around "Wallpaper steaming process explained" without ever being told
    that fragment is a continuous 167-SECOND chunk — this system selects
    whole extracted fragments, it does not sub-clip within one, so that
    target was structurally impossible from the moment it was agreed to.
    Three story-arc attempts then failed the (correct) duration check and
    NOTHING was generated. Showing duration up front lets the planner
    catch this during the conversation, when it's cheap to fix, instead
    of after paying for generation three times."""
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
            dur = f.source_end_sec - f.source_start_sec
            lines.append(f"  [{mins:d}:{secs:02d}, {dur:.0f}s long] {f.topic_label}")
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
    target_duration_sec} dict, or raises — never invents a turn.

    One self-correcting retry if the reply comes back as plain prose
    instead of the required JSON envelope. Confirmed real, 2026-09-07:
    against the FULL real prompt (full footage digest + full research),
    the CLI route occasionally answers in good, on-topic prose and just
    drops the JSON fence — the reasoning itself was sound (it correctly
    caught a 45s target being impossible against a 167s fragment), only
    the required structure was missing. A short, sharp reminder appended
    to the SAME prompt reliably restores it, and is far cheaper than
    treating a format slip as an unrecoverable planning failure."""
    client = build_anthropic_client(api_key=api_key)

    def _call(prompt: str):
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0.6,
            system=PLANNER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
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
        return text

    text = _call(user_prompt)
    try:
        data = _extract_json(text)
    except Exception:
        retry_prompt = (
            f"{user_prompt}\n\nYour previous reply was plain prose with no JSON — reproduced "
            f"here so you don't lose the reasoning in it, but it doesn't fit the required "
            f"envelope:\n\n{text[:1500]}\n\nReturn that same reasoning as ONLY the fenced JSON "
            f"block the format requires — no prose before or after the fence."
        )
        text = _call(retry_prompt)
        data = _extract_json(text)  # let this one raise for real if it still fails

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

        # Look at the picture before planning around it (2026-09-08).
        # Free via the CLI, costs a few minutes, and answers the question
        # this conversation previously had to put to Ryan by hand.
        visual_check = _probe_anchor_fragment(
            tagged_by_source, emit, job_id, stated_intent=stated_intent,
            project_dir=project.dir())

        emit({"type": "log", "level": "info", "job_id": job_id,
              "message": "Working out a game plan from the footage and the research..."})
        result = _planner_call(
            PLANNER_OPENING_TEMPLATE.format(
                audience_goal=audience_goal.strip(),
                intent_block=intent_block,
                footage_digest=footage_digest,
                visual_block=_format_visual_block(visual_check),
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
        visual_check=visual_check,
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
                visual_block=_format_visual_block(session.visual_check),
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
