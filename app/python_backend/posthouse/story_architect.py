"""posthouse.story_architect — Creative Editor: story + assembly, the
SELECTION/SEQUENCING half only.

**What this replaces, and what it doesn't.** PreCut's own
`story_planner.generate_angles()` is one Claude call, capped at ~3 angles
covering ~9 ranges / ~13 minutes, reading a whole project's transcripts
concatenated into one prompt with no output-budget room to consider more
— the project's own named founding gap (`docs/REQUIREMENTS.md`). This
module does not re-skim the transcript the way `generate_angles()` does.
It works from material Assistant Editor's transcript-flagging arc already
produced exhaustively and scored against the project's real audience
goal (`posthouse.transcript_coverage` + `posthouse.audience_relevance`,
persisted per source file as `project.dir()/flags/<stem>.json`) — so the
new work here is genuinely new: sequencing already-vetted material into a
narrative arc (hook -> build -> payoff), not finding it.

**Assembly itself is intentionally NOT rebuilt here.** PreCut already has
a complete, working, SHIPPED path from a `StoryAngle` to a real assembled
`CutList` to a real XML export: `story_assembler.assemble_cut_from_angle()`
plus `exporter.py`'s `idea_kind == "story_angle"` branch, both already
wired to the app's existing Ideas UI (`project.plans_dir()/*.json`,
`kind: "story_angle"`). This module's only job is to produce a real
`StoryAngle` and persist it in that exact same format
(`producer.py`'s `_angle_from_dict`/`_to_dict` shape) so it flows through
that unmodified, already-proven machinery — confirmed by reading
`exporter.py` and `producer.py` directly, 2026-09-03, per the
`precut-capabilities` rule: build the missing piece, never the machine
that already exists.

**Live trend research, per Ryan's explicit call (2026-09-03: "Live, run
it fresh each time").** Ports the doctrine from the
`trend-research` skill (`~/.claude/skills/trend-research`, itself ported
from Agent Studio's never-run `trend-scouting` doctrine) directly into
this Claude call via the Anthropic API's server-side web_search tool —
no separate agent step, no caching. **Tool version matters, confirmed by
a real side-by-side test, 2026-09-03**: `web_search_20260318` routes
through Claude's code-execution sandbox and, in a real test call, spun
through ~15 failed programmatic search attempts, burned ~137K input
tokens, hit a rate limit, and gave up with an honest disclosure instead
of real results. `web_search_20250305` did a clean, direct 2-query
search for ~17K input tokens and returned real sourced links. Use
`web_search_20250305` — this is not a stylistic choice, it's the only
one of the two that worked when actually tried.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from posthouse.precut_bridge import import_precut
from posthouse.audience_relevance import TaggedFragment

_anthropic_client = import_precut("precut_pipeline.anthropic_client")
_config = import_precut("precut_pipeline.config")
_cutlist = import_precut("precut_pipeline.cutlist")
_story_planner = import_precut("precut_pipeline.story_planner")

build_anthropic_client = _anthropic_client.build_anthropic_client
ANTHROPIC_MODEL = _config.ANTHROPIC_MODEL
StoryAngle = _cutlist.StoryAngle
CreativeBrief = _cutlist.CreativeBrief
TopicRange = _cutlist.TopicRange
StoryPlannerError = _story_planner.StoryPlannerError
_extract_json = _story_planner._extract_json

# Below this many "strong" fragments, widen the candidate pool to include
# "possible" ones too rather than trying to build an arc out of too little
# real material. Never invents fragments — only widens which real,
# already-extracted ones are eligible.
MIN_STRONG_FRAGMENTS = 3

# Confirmed 2026-09-03 by a real side-by-side call (see module docstring)
# — the direct-search tool variant, not the code-execution one.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

ARCHITECT_SYSTEM_PROMPT = """You are a documentary/branded-content story architect. An Assistant \
Editor has already exhaustively read a project's raw interview transcripts and scored every \
storyline-worthy moment against this project's stated audience/content goal — you are not \
finding material, you are the first pass at SEQUENCING already-vetted real material into an \
actual narrative arc (hook, build, payoff), the way an experienced editor would before ever \
touching a timeline.

You will also be given live web search to check what's currently trending in this content \
niche (formats, audio, adjacent creator activity). Use it to inform framing and tone \
recommendations only — never to justify picking a fragment that doesn't actually serve the \
stated audience goal, and never to invent or embellish material that isn't in the given list.

Hard rules:
- You may ONLY select from the fragments given to you, by their [index]. Never invent a time \
range, a quote, or a moment that isn't in the list — every selection must trace to a real, \
already-extracted fragment.
- Every trend claim in your reasoning needs a real source (URL) from your search. If a search \
doesn't turn up real, checkable results, say so plainly rather than presenting a guess as \
verified trend signal.
- Most real interviews have more usable material than fits in one story — be honest about which \
fragments you're leaving out and why, in `omitted_reasoning`.

Return ONLY the structured output specified in the prompt, as a fenced ```json code block at \
the very end of your response (after any search/reasoning text)."""

ARCHITECT_PROMPT_TEMPLATE = """This project's stated audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

Real, already-extracted and audience-scored transcript fragments available to build from \
(fit={fit_note}):

<fragments>
{fragments}
</fragments>

First, search the web for what's currently trending in this content niche (formats, audio, \
adjacent creator/competitor activity) — scope your searches to the real niche implied by the \
audience goal above, not generic virality. Report what you actually found, with sources; if a \
search comes up empty or unsourceable, say so rather than guessing.

Then build ONE story arc from the fragments above: pick which ones to use (by index), what role \
each plays (hook / build / payoff — you may use more than one fragment per role), and the order \
they should play in. You do not have to use every fragment — leave out anything that doesn't \
serve a coherent single arc, and say what you left out and why.

Return this exact JSON shape as the LAST thing in your response, in a fenced ```json block:

{{
  "trend_findings": [
    {{"finding": "...", "source": "https://..."}},
    {{"finding": "...", "source": "unverified — no sourceable result"}}
  ],
  "title": "short concept title",
  "hook": "1-2 sentence opening hook/headline",
  "why_it_works": "why this arc serves the stated audience goal, citing which fragments and (if relevant) which real trend finding informed the framing",
  "tone": "editorial tone guidance, e.g. 'quiet, unhurried, heart-led'",
  "target_duration_sec": <rough number, not enforced>,
  "target_audience": "who this is for, restated from the audience goal",
  "call_to_action": "",
  "sequence": [
    {{"index": 0, "role": "hook"}},
    {{"index": 3, "role": "build"}},
    {{"index": 7, "role": "payoff"}}
  ],
  "omitted_reasoning": "1-2 sentences on what real fragments were left out and why"
}}"""


def _collect_candidate_fragments(
    tagged_by_source: Dict[str, List[TaggedFragment]],
) -> List[TaggedFragment]:
    """Flatten a project's per-source tagged fragments into one candidate
    pool, `strong` fragments first. Widens to include `possible` fragments
    too when there aren't enough `strong` ones to build a real arc from —
    never fabricates material, only widens which REAL, already-extracted
    fragments are eligible for selection."""
    all_tagged: List[TaggedFragment] = []
    for tagged_list in tagged_by_source.values():
        all_tagged.extend(tagged_list)

    strong = [t for t in all_tagged if t.fit == "strong"]
    if len(strong) >= MIN_STRONG_FRAGMENTS:
        return strong
    possible = [t for t in all_tagged if t.fit == "possible"]
    return strong + possible


def _format_candidates_for_llm(candidates: List[TaggedFragment]) -> str:
    lines = []
    for i, tf in enumerate(candidates):
        f = tf.fragment
        lines.append(
            f'[{i}] fit={tf.fit} file="{f.source_file}" '
            f'{f.source_start_sec:.1f}s-{f.source_end_sec:.1f}s '
            f'"{f.topic_label}": {f.summary}'
        )
    return "\n".join(lines)


def generate_story_angle(
    audience_goal: str,
    tagged_by_source: Dict[str, List[TaggedFragment]],
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
) -> tuple:
    """Build one real StoryAngle from a project's exhaustively-extracted,
    audience-scored fragments plus live trend research.

    Returns (angle, research) — `research` is the raw
    `{trend_findings, omitted_reasoning}` the model returned. PreCut's
    `CreativeBrief` dataclass has no field for either (it's PreCut's own
    schema, not ours to extend), so `why_it_works` folds trend citations
    in as prose, but the full sourced findings and what real material got
    left out would otherwise be silently dropped the moment this function
    returns — `research` exists so the caller can persist that audit
    trail instead (see `save_story_research`). Never assume a citation
    that isn't captured here still exists somewhere.

    Raises StoryPlannerError if there isn't enough real material to build
    from, or if the API call / JSON parsing fails — this never falls back
    to inventing a story from nothing.
    """
    if not audience_goal or not audience_goal.strip():
        raise ValueError(
            "audience_goal must be non-empty — this module has no basis "
            "to judge or sequence a story without it."
        )

    candidates = _collect_candidate_fragments(tagged_by_source)
    if not candidates:
        raise StoryPlannerError(
            "No strong or possible fragments available — nothing real to "
            "build a story arc from. Run transcript flagging first."
        )

    strong_count = sum(1 for t in candidates if t.fit == "strong")
    fit_note = "strong" if strong_count == len(candidates) else "strong + possible (not enough strong fragments alone)"

    client = build_anthropic_client(api_key=api_key)
    user_prompt = ARCHITECT_PROMPT_TEMPLATE.format(
        audience_goal=audience_goal.strip(),
        fragments=_format_candidates_for_llm(candidates),
        fit_note=fit_note,
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0.4,
            system=ARCHITECT_SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        raise StoryPlannerError(f"Anthropic API error: {e}") from e

    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    text = "".join(text_parts).strip()
    if not text:
        raise StoryPlannerError("Empty text response from Claude (tool-use blocks only).")
    data = _extract_json(text)

    ranges: List[TopicRange] = []
    for entry in data.get("sequence", []):
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)):
            continue
        tf = candidates[idx]
        f = tf.fragment
        role = str(entry.get("role", ""))
        ranges.append(TopicRange(
            source_file=f.source_file,
            source_start_sec=f.source_start_sec,
            source_end_sec=f.source_end_sec,
            topic_label=role or f.topic_label,
            summary=f.summary,
        ))

    if not ranges:
        raise StoryPlannerError(
            "Claude's response selected no valid fragment indices — "
            f"raw sequence field: {data.get('sequence')!r}"
        )

    brief = CreativeBrief(
        title=str(data.get("title", ""))[:200],
        hook=str(data.get("hook", ""))[:500],
        why_it_works=str(data.get("why_it_works", ""))[:3000],
        tone=str(data.get("tone", ""))[:200],
        target_duration_sec=float(data.get("target_duration_sec", 0.0) or 0.0),
        target_audience=str(data.get("target_audience", ""))[:300],
        call_to_action=str(data.get("call_to_action", ""))[:300],
    )

    angle = StoryAngle(
        angle_id=f"angle_{uuid.uuid4().hex[:10]}",
        brief=brief,
        source_ranges=ranges,
    )
    research = {
        "trend_findings": data.get("trend_findings", []),
        "omitted_reasoning": data.get("omitted_reasoning", ""),
    }
    return angle, research


def save_story_angle_as_idea(project_plans_dir: Path, angle: "StoryAngle") -> Path:
    """Persist a StoryAngle in the EXACT idea-JSON shape PreCut's own
    `producer.py`/`exporter.py` already read (`kind: "story_angle"`) —
    so it shows up in the existing Ideas UI and flows through the
    existing, unmodified `assemble_cut_from_angle` export path with zero
    new wiring. Shape confirmed by reading `producer.py`'s
    `run_generate_angles` persistence code directly, 2026-09-03."""
    from dataclasses import asdict

    project_plans_dir = Path(project_plans_dir)
    project_plans_dir.mkdir(parents=True, exist_ok=True)

    idea_id = f"idea_{uuid.uuid4().hex[:10]}"
    idea_path = project_plans_dir / f"{idea_id}.json"
    angle_dict = asdict(angle)
    payload = {
        "idea_id": idea_id,
        "kind": "story_angle",
        "created_at": time.time(),
        "refinement_history": [],
        "selected_preset_key": angle.suggested_preset,
        "selected_platform_key": "",
        "selected_aspect_key": "",
        "data": angle_dict,
    }
    idea_path.write_text(json.dumps(payload, indent=2))
    return idea_path


def save_story_research(project_dir: Path, angle: "StoryAngle", research: dict) -> Path:
    """Persist the sourced trend findings and omitted-material reasoning
    that `generate_story_angle` returns alongside the angle — this is the
    audit trail behind `why_it_works`'s prose citations. Kept separate
    from the idea JSON (PreCut's own schema) rather than crammed into it."""
    out_dir = Path(project_dir) / "story_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{angle.angle_id}.json"
    out_path.write_text(json.dumps({
        "angle_id": angle.angle_id,
        "title": angle.brief.title,
        **research,
    }, indent=2))
    return out_path
