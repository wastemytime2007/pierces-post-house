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

**Research is a separate, auditable step — not folded into the sequencing
call.** Two real gaps Ryan found in the first version (2026-09-03): (1)
nothing surfaced the sourced trend findings anywhere he could see them,
and (2) text search alone means "trend research" is really "reading
articles about trends," not seeing real trending content — he asked for
it to actually watch reels the way `claude-video-vision` would. That MCP
tool only exists inside an interactive agent session, not a bare
`anthropic` SDK call from this backend — so this module does the
equivalent directly: search finds real individual video permalinks
(`tiktok.com/@x/video/123`, not a `/discover/` category page), `yt-dlp`
downloads the actual video, `ffmpeg` samples real frames, and those
frames go to Claude as real image content in a follow-up call — genuinely
watched, not described secondhand. **Proven live, 2026-09-03**: found a
real, individually-permalinked, currently-trending home-renovation TikTok
(`@rooshome`), downloaded it, extracted 4 frames, and had Claude
correctly describe its real before/after text-overlay pattern and
lighting-driven pacing from the actual pixels — not from what a blog post
says renovation reels tend to do.
**Known real limitation, not smoothed over**: search engines mostly
surface category/hashtag pages for TikTok/Instagram, not individual
permalinks (confirmed by testing — one dedicated search for "3-5 real
individual video URLs" returned only ONE genuine permalink out of many
category-page results, and Claude itself flagged this rather than
inventing more). So video-verified findings will often be zero or one
per run, not three — `research_trends()` reports that honestly via
`unverified` rather than padding the count.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from posthouse.precut_bridge import import_precut
from posthouse.audience_relevance import TaggedFragment, load_tagged_fragments

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

# Cap on how many real videos we'll actually download+watch per research
# pass — bounds wall-clock time and cost against inherently flaky scraping
# (blocked, deleted, format-unavailable are all real, expected outcomes).
MAX_VIDEOS_TO_WATCH = 2

# Matches a real INDIVIDUAL video permalink, not a discover/hashtag/
# category page (confirmed by testing which patterns those two shapes
# actually take, 2026-09-03).
_VIDEO_PERMALINK_RE = re.compile(
    r"(?:tiktok\.com/@[\w.-]+/video/\d+"
    r"|instagram\.com/reel/[\w-]+"
    r"|instagram\.com/p/[\w-]+"
    r"|youtube\.com/shorts/[\w-]+)"
)

TREND_TEXT_SEARCH_PROMPT = """Search the web for what's currently trending in short-form video \
content for the real niche implied by this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

Scope searches to the real niche (home renovation / real estate / contractor / small business \
content), not generic virality. Report real, sourced findings only.

After searching, return ONLY this JSON in a fenced ```json block — no prose synthesis, no \
preamble, nothing before or after the fence:
{{"findings": [{{"finding": "...", "source": "https://..."}}]}}

Omit anything you can't actually source — do not pad the list with a guess."""

VIDEO_PERMALINK_SEARCH_PROMPT = """Search the web for 3-5 real, INDIVIDUAL (not category/hashtag/\
discover-page) TikTok, Instagram Reels, or YouTube Shorts video URLs that are currently popular \
in the niche implied by this audience goal:

<audience_goal>
{audience_goal}
</audience_goal>

I need actual video permalink URLs (e.g. tiktok.com/@user/video/1234567890, \
instagram.com/reel/abc123, youtube.com/shorts/xyz) that could actually be opened and watched — \
not discover/hashtag/category pages, and never fabricated. Just search; you don't need to \
summarize results in your final text."""

VIDEO_WATCH_PROMPT = """These frames are sampled from a real, currently-circulating video found \
via a search meant to target this audience/content goal's niche — but web search over TikTok/\
Instagram is unreliable and sometimes returns something unrelated. Judge that honestly first.

<audience_goal>
{audience_goal}
</audience_goal>

Return ONLY this JSON, in a fenced ```json block:
{{
  "relevant": true or false — is this video actually in the niche this audience goal implies?,
  "observed": "what you actually SEE across these frames — real visual format, text-overlay pattern, pacing, lighting/mood — concrete and specific to these images, not generic knowledge. If not relevant, briefly say what it actually is instead."
}}"""

ARCHITECT_SYSTEM_PROMPT = """You are a documentary/branded-content story architect. An Assistant \
Editor has already exhaustively read a project's raw interview transcripts and scored every \
storyline-worthy moment against this project's stated audience/content goal — you are not \
finding material, you are the first pass at SEQUENCING already-vetted real material into an \
actual narrative arc (hook, build, payoff), the way an experienced editor would before ever \
touching a timeline. Live trend research (some of it from actually-watched real videos, some \
from articles about trends — clearly labeled which is which) has already been gathered \
separately and is given to you below; use it to inform framing and tone only — never to justify \
picking a fragment that doesn't actually serve the stated audience goal.

Hard rules:
- You may ONLY select from the fragments given to you, by their [index]. Never invent a time \
range, a quote, or a moment that isn't in the list — every selection must trace to a real, \
already-extracted fragment.
- Most real interviews have more usable material than fits in one story — be honest about which \
fragments you're leaving out and why, in `omitted_reasoning`.

Return ONLY the structured output specified in the prompt, as a fenced ```json code block."""

ARCHITECT_PROMPT_TEMPLATE = """This project's stated audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

Real, already-extracted and audience-scored transcript fragments available to build from \
(fit={fit_note}):

<fragments>
{fragments}
</fragments>

Live trend research already gathered for this project (use to inform framing/tone only):

<trend_research>
{trend_research}
</trend_research>

Build ONE story arc from the fragments above: pick which ones to use (by index), what role \
each plays (hook / build / payoff — you may use more than one fragment per role), and the order \
they should play in. You do not have to use every fragment — leave out anything that doesn't \
serve a coherent single arc, and say what you left out and why.

Return this exact JSON shape, in a fenced ```json block:

{{
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


def _looks_like_video_permalink(url: str) -> bool:
    return bool(url and _VIDEO_PERMALINK_RE.search(url))


def _watch_video(url: str, client, model: str, audience_goal: str) -> Optional[dict]:
    """Actually download a real video and have Claude look at real sampled
    frames from it — genuinely watched, not described secondhand. Returns
    None on ANY failure (blocked, deleted, unsupported format, etc. are all
    real and expected against TikTok/Instagram) rather than fabricating a
    description of a video that was never actually opened."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        video_glob = tmp_path / "v.%(ext)s"
        try:
            subprocess.run(
                ["yt-dlp", "--no-warnings", "-f", "best[height<=480]/best",
                 "-o", str(video_glob), url],
                check=True, capture_output=True, timeout=60,
            )
        except Exception:
            return None

        video_files = [p for p in tmp_path.glob("v.*")]
        if not video_files:
            return None

        frame_pattern = tmp_path / "frame_%02d.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_files[0]),
                 "-vf", "fps=1/3,scale=400:-1", "-frames:v", "4", str(frame_pattern)],
                check=True, capture_output=True, timeout=30,
            )
        except Exception:
            return None

        frames = sorted(tmp_path.glob("frame_*.jpg"))
        if not frames:
            return None

        content = [{"type": "text", "text": VIDEO_WATCH_PROMPT.format(
            url=url, audience_goal=audience_goal)}]
        for fp in frames:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(fp.read_bytes()).decode(),
                },
            })
        try:
            resp = client.messages.create(model=model, max_tokens=500,
                                           messages=[{"role": "user", "content": content}])
        except Exception:
            return None
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            return None
        try:
            parsed = _extract_json(text)
        except StoryPlannerError:
            return None
        return {
            "url": url,
            "relevant": bool(parsed.get("relevant", False)),
            "observed": str(parsed.get("observed", ""))[:1000],
            "frames_analyzed": len(frames),
        }


def research_trends(
    audience_goal: str,
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
) -> dict:
    """Live trend research, per Ryan's explicit call — run fresh every
    time, never cached. Two kinds of finding, clearly labeled and never
    conflated: `text_findings` (read from articles about trends, sourced
    by URL) and `video_findings` (a real video actually downloaded and
    watched via real sampled frames — see module docstring). `unverified`
    always says plainly when a search or a video came up empty rather
    than padding either list."""
    client = build_anthropic_client(api_key=api_key)
    result: dict = {"text_findings": [], "video_findings": [], "unverified": []}

    try:
        resp = client.messages.create(
            model=model, max_tokens=2000, tools=[WEB_SEARCH_TOOL],
            system="Return ONLY the requested JSON, in a fenced ```json block. No preamble, "
                   "no summary text before or after it.",
            messages=[{"role": "user", "content": TREND_TEXT_SEARCH_PROMPT.format(
                audience_goal=audience_goal)}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if text:
            data = _extract_json(text)
            result["text_findings"] = data.get("findings", [])
        else:
            result["unverified"].append("Text trend search returned no text output (tool-use blocks only).")
    except Exception as e:
        result["unverified"].append(f"Text trend search failed: {e}")

    candidate_urls: List[str] = []
    try:
        resp2 = client.messages.create(
            model=model, max_tokens=600, tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": VIDEO_PERMALINK_SEARCH_PROMPT.format(
                audience_goal=audience_goal)}],
        )
        for block in resp2.content:
            if getattr(block, "type", None) == "web_search_tool_result":
                items = block.content if isinstance(block.content, list) else []
                for item in items:
                    url = getattr(item, "url", None)
                    if url and _looks_like_video_permalink(url):
                        candidate_urls.append(url)
    except Exception as e:
        result["unverified"].append(f"Video permalink search failed: {e}")

    if not candidate_urls:
        result["unverified"].append(
            "No individual, watchable trending-video permalinks found via search "
            "(search mostly surfaces TikTok/Instagram category pages, not permalinks — "
            "a confirmed real limitation, not a bug). Trend signal is text-sourced only "
            "this run, not video-verified."
        )

    seen = set()
    for url in candidate_urls:
        if len(result["video_findings"]) >= MAX_VIDEOS_TO_WATCH:
            break
        if url in seen:
            continue
        seen.add(url)
        watched = _watch_video(url, client, model, audience_goal)
        if watched:
            result["video_findings"].append(watched)
        else:
            result["unverified"].append(
                f"Found candidate video {url} but could not download/analyze it "
                "(blocked, deleted, or unsupported format — expected against "
                "TikTok/Instagram sometimes)."
            )

    return result


def _format_research_for_llm(research: dict) -> str:
    lines = []
    for f in research.get("text_findings", []):
        lines.append(f"[text-sourced, from an article] {f.get('finding','')} (source: {f.get('source','')})")
    for f in research.get("video_findings", []):
        if not f.get("relevant", False):
            continue  # honestly flagged as off-niche by the watch step — not real signal
        lines.append(f"[ACTUALLY WATCHED — real video at {f.get('url','')}] {f.get('observed','')}")
    for u in research.get("unverified", []):
        lines.append(f"[UNVERIFIED] {u}")
    return "\n".join(lines) if lines else "(no trend research available this run)"


def generate_story_angle(
    audience_goal: str,
    tagged_by_source: Dict[str, List[TaggedFragment]],
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
    research: Optional[dict] = None,
) -> tuple:
    """Build one real StoryAngle from a project's exhaustively-extracted,
    audience-scored fragments plus live trend research.

    `research` should normally come from a prior `research_trends()` call
    (kept separate so it's independently inspectable/persistable — see
    `save_story_research`); if omitted, this function runs it internally.

    Returns (angle, research). PreCut's `CreativeBrief` dataclass has no
    field for the sourced findings (it's PreCut's own schema, not ours to
    extend), so `why_it_works` folds citations in as prose, but the full
    research would otherwise be silently dropped the moment this function
    returns — `research` exists so the caller persists that audit trail.

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

    if research is None:
        research = research_trends(audience_goal.strip(), model=model, api_key=api_key)

    client = build_anthropic_client(api_key=api_key)
    user_prompt = ARCHITECT_PROMPT_TEMPLATE.format(
        audience_goal=audience_goal.strip(),
        fragments=_format_candidates_for_llm(candidates),
        fit_note=fit_note,
        trend_research=_format_research_for_llm(research),
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0.4,
            system=ARCHITECT_SYSTEM_PROMPT,
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
    research["omitted_reasoning"] = data.get("omitted_reasoning", "")
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


def run_generate_story_angle(project, job_id: str, emit) -> None:
    """Backend-job wrapper: load a project's real audience goal + already-
    tagged transcript-flagging fragments, generate one real story angle
    with live trend research, and persist both the idea (PreCut's format)
    and the research audit trail. Emits progress the same shape as
    `producer.run_generate_angles` so the existing job-tracking UI works
    unchanged.

    A no-op (not an error) when there's no manifest/audience_goal yet, or
    no tagged fragments yet — same rule `pipeline._run_transcript_flagging`
    already applies, since this stage strictly depends on that one having
    run first.
    """
    from posthouse.manifest import load_manifest

    project_dir = project.dir()
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No manifest.json for this project yet — run Organize first."})
        return

    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id, "message": f"Failed to load manifest: {e}"})
        return

    audience_goal = (manifest.get("project") or {}).get("audience_goal")
    if not audience_goal:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No audience/content goal set for this project "
                         "(Project Manager intake) — nothing to build a story arc against."})
        return

    flags_dir = project_dir / "flags"
    flags_files = sorted(flags_dir.glob("*.json")) if flags_dir.exists() else []
    if not flags_files:
        emit({"type": "producer_error", "job_id": job_id,
              "message": "No transcript-flagging results yet — run the pipeline "
                         "first so there's real, scored material to build from."})
        return

    tagged_by_source = {}
    for fp in flags_files:
        try:
            tagged_by_source[fp.stem] = load_tagged_fragments(fp)
        except Exception:
            continue

    emit({"type": "producer_started", "job_id": job_id, "mode": "story_architect"})

    try:
        emit({"type": "log", "level": "info", "message": "Researching live trends (real web search + real video watching)..."})
        research = research_trends(audience_goal)
        emit({"type": "log", "level": "info",
              "message": f"Trend research: {len(research['text_findings'])} text findings, "
                         f"{sum(1 for v in research['video_findings'] if v.get('relevant'))} "
                         f"real video(s) watched and relevant."})

        emit({"type": "log", "level": "info", "message": "Building story arc from flagged fragments..."})
        angle, research = generate_story_angle(audience_goal, tagged_by_source, research=research)
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return

    idea_path = save_story_angle_as_idea(project.plans_dir(), angle)
    research_path = save_story_research(project_dir, angle, research)

    emit({
        "type": "producer_angle",
        "job_id": job_id,
        "idea_id": idea_path.stem,
        "angle": _angle_to_dict(angle),
        "research_path": str(research_path),
    })
    emit({"type": "producer_done", "job_id": job_id, "mode": "story_architect", "angle_count": 1})


def _angle_to_dict(angle: "StoryAngle") -> dict:
    from dataclasses import asdict
    return asdict(angle)


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
