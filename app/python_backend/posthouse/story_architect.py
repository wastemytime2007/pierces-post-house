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

**Correction, 2026-09-03, same day: fixed-interval still frames cannot
tell you anything about editing.** Ryan's exact, correct objection: "You
cant understand video editing trends by looking at 4 random frames."
Editing pacing, rhythm, and transition style are properties of CUTS over
TIME — no number of evenly-spaced stills can show that, only more of the
same category of nothing. The fix wasn't more frames; it was sampling at
the right MOMENTS: `_detect_cuts()` runs ffmpeg's real scene-change
filter on the downloaded video to find actual cut timestamps, then
`_watch_video()` extracts the real frame immediately before and after a
spread of those real cuts (not just the first few — the whole video's
length) so the model sees actual transition character, not guesses from
isolated pictures. It's also handed the real, MEASURED cuts-per-second
and average-shot-length numbers computed from the real detected cuts —
a hard number, not an impression — and told explicitly not to claim a
pacing reading when a video has zero detected cuts (a genuinely static
single-shot video), falling back to a few stills for content-only
description in that case, with that limitation stated plainly rather
than smoothed over.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from posthouse.precut_bridge import import_precut
from posthouse.audience_relevance import TaggedFragment, load_tagged_fragments

# app_support_dir() lives in the app's own project.py, not PreCut donor
# code — safe to import directly (unlike precut_pipeline, which must go
# through import_precut). Used for the cross-project research cache: a
# audience/goal profile is shared across projects, so the cache should be
# too, not siloed per-project.
from project import app_support_dir

_anthropic_client = import_precut("precut_pipeline.anthropic_client")
_config = import_precut("precut_pipeline.config")
_cutlist = import_precut("precut_pipeline.cutlist")
_story_planner = import_precut("precut_pipeline.story_planner")
_story_assembler = import_precut("precut_pipeline.story_assembler")

build_anthropic_client = _anthropic_client.build_anthropic_client
ANTHROPIC_MODEL = _config.ANTHROPIC_MODEL
StoryAngle = _cutlist.StoryAngle
CreativeBrief = _cutlist.CreativeBrief
TopicRange = _cutlist.TopicRange
StoryPlannerError = _story_planner.StoryPlannerError
_extract_json = _story_planner._extract_json

# Confirmed 2026-09-03 by a real side-by-side call (see module docstring)
# — the direct-search tool variant, not the code-execution one.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}

# Cap on how many real videos we'll actually download+watch per research
# pass — bounds wall-clock time and cost against inherently flaky scraping
# (blocked, deleted, format-unavailable are all real, expected outcomes).
MAX_VIDEOS_TO_WATCH = 2

# Watching real videos (sampled frames as vision tokens) and reading a
# strategy video's transcript are the most expensive parts of a research
# pass — and they are also the ONLY parts that actually look at video.
# They stay ON by default.
#
# History worth keeping, 2026-09-04: after Ryan hit a $3 run these were
# briefly defaulted OFF. That was the wrong fix and he said so —
# "Why would we only research text when we need to find video trends? I
# feel like you're turning things off that we had for a reason." Correct:
# he asked for real video analysis specifically ("not just look at the
# first video that pops up in a search result"), and text-only research
# for a video-trends problem is not research.
#
# The real bug was never that this work happens; it was that it was being
# PAID FOR REPEATEDLY — generation re-bought research the planning
# conversation had already stored, and the cache was keyed on an intent
# string that changed every conversation turn so it never hit. Those are
# fixed (see run_generate_story_angle's `research` argument). This work is
# meant to be paid for once per audience/intent per 72h and reused across
# projects, which is exactly what the cache does when its key is stable.
#
# POSTHOUSE_WATCH_VIDEOS=0 remains as a deliberate escape hatch for a
# cheap run; it is not the default.
def _deep_research_enabled() -> bool:
    return os.environ.get("POSTHOUSE_WATCH_VIDEOS", "1").strip() not in ("0", "false", "no", "off")

# Matches a real INDIVIDUAL video permalink, not a discover/hashtag/
# category page (confirmed by testing which patterns those two shapes
# actually take, 2026-09-03).
_VIDEO_PERMALINK_RE = re.compile(
    r"(?:tiktok\.com/@[\w.-]+/video/\d+"
    r"|instagram\.com/reel/[\w-]+"
    r"|instagram\.com/p/[\w-]+"
    r"|youtube\.com/shorts/[\w-]+)"
)

# A full-length YouTube video (not a Short) — for real strategy/educational
# content, where the transcript is the signal, not editing pacing.
_YOUTUBE_VIDEO_RE = re.compile(
    r"(?:youtube\.com/watch\?v=[\w-]+|youtu\.be/[\w-]+)"
)

MAX_STRATEGY_VIDEOS_TO_WATCH = 2

TREND_TEXT_SEARCH_PROMPT = """Search the web for what's currently trending in short-form video \
content for the real niche implied by this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

Scope searches to the real niche (home renovation / real estate / contractor / small business \
content), not generic virality. Report real, sourced findings only.

After searching, return ONLY this JSON in a fenced ```json block — no prose synthesis, no \
preamble, nothing before or after the fence. Keep each "finding" as plain prose WITHOUT \
quotation marks inside it (paraphrase any quoted material instead of quoting it directly) so \
the JSON string doesn't break:
{{"findings": [{{"finding": "...", "source": "https://..."}}]}}

Omit anything you can't actually source — do not pad the list with a guess."""

TREND_NAMES_SEARCH_PROMPT = """Search the web for SPECIFIC NAMED trends currently circulating on \
TikTok/Instagram Reels in the niche implied by this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

I need actual NAMES — a specific trending sound/song title, a named challenge/format (e.g. "the \
'wait I can do that better' sound", "the [name] transition"), or a named creator's signature \
format — not a generic statement like "short-form video is popular" or "before/after content \
does well."

**If a trend is a specific sound/song, also find a real, direct link where an editor could \
actually listen to or download it** (Spotify, Apple Music, YouTube, SoundCloud, or the TikTok/\
Instagram sound-page itself) — not just an article that mentions it. Leave `listen_url` empty if \
you can't find a real one; never fabricate a link.

After searching, return ONLY this JSON in a fenced ```json block — no preamble:
{{"trends": [{{"name": "the specific trend/sound/challenge name", "description": "...", "source": "https://...", "listen_url": "https://... or empty string if not a sound/not found"}}]}}

If you genuinely cannot find a specific named trend for this niche, return an EMPTY list — \
{{"trends": []}} — do NOT invent a placeholder entry explaining that none was found; an empty \
list already says that."""

MARKETING_STRATEGY_SEARCH_PROMPT = """Search the web for CURRENT (2026), real, sourced advice on \
social media strategy, algorithm-aware content strategy, and audience targeting — specifically \
useful for reaching the audience and achieving the goal described here:

<audience_goal>
{audience_goal}
</audience_goal>

This is distinct from "what's trending" — I want real marketing/strategy craft: how to actually \
identify and reach a specific target audience on social platforms, what makes content \
genuinely resonate with that audience rather than a general one, and current \
platform-algorithm-aware posting/targeting practice. Not generic "post consistently" advice —
look for something with real specificity.

After searching, return ONLY this JSON in a fenced ```json block — no preamble. Keep each \
"finding" to ONE tight sentence, plain prose, WITHOUT quotation marks inside it (paraphrase any \
quoted material instead of quoting it directly) so the JSON stays short and doesn't break:
{{"findings": [{{"finding": "one sentence, concrete and specific", "source": "https://..."}}]}}

Omit anything you can't actually source — do not pad the list with a guess. Return at most 6 \
findings."""

MARKETING_VIDEO_SEARCH_PROMPT = """Search the web for 2-4 real, INDIVIDUAL YouTube video URLs \
(full videos, not Shorts) that are genuine how-to/educational content about social media \
marketing strategy or audience targeting relevant to this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

I need actual individual video URLs (youtube.com/watch?v=... or youtu.be/...) for real \
educational/strategy content — a marketing expert or creator teaching audience targeting, \
content strategy, or platform algorithm behavior. Not a Short, not a channel page, not \
fabricated. Just search; you don't need to summarize results in your final text."""

STRATEGY_TRANSCRIPT_PROMPT = """This is the real transcript of a video found via a search for \
social-media/audience-targeting strategy content, relevant to this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

<transcript>
{transcript}
</transcript>

Extract 2-4 of the most CONCRETE, ACTIONABLE pieces of advice this video actually gives about \
reaching a target audience or making content that audience wants — grounded in what's actually \
said in the transcript above, not generic marketing knowledge. Each point should be traceable \
to something in the transcript. If the transcript doesn't actually contain concrete, actionable \
advice (e.g. it's off-topic, or pure filler), say so honestly rather than padding.

Return ONLY this JSON, in a fenced ```json block:
{{
  "relevant": true or false — does this video actually contain real audience-targeting/content-strategy advice relevant to the niche?,
  "points": ["concrete point 1, grounded in the transcript", "concrete point 2"]
}}"""

VIDEO_PERMALINK_SEARCH_PROMPT = """Search the web for 3-5 real, INDIVIDUAL (not category/hashtag/\
discover-page) TikTok, Instagram Reels, or YouTube Shorts video URLs that are currently popular \
in the niche implied by this audience goal:

<audience_goal>
{audience_goal}
</audience_goal>
{trend_names_clause}

I need actual video permalink URLs (e.g. tiktok.com/@user/video/1234567890, \
instagram.com/reel/abc123, youtube.com/shorts/xyz) that could actually be opened and watched — \
not discover/hashtag/category pages, and never fabricated. Just search; you don't need to \
summarize results in your final text."""

VIDEO_WATCH_PROMPT = """These frame pairs are sampled from a real, currently-circulating video \
found via a search meant to target this audience/content goal's niche — but web search over \
TikTok/Instagram is unreliable and sometimes returns something unrelated. Judge that honestly \
first.

**A single still frame can't tell you anything about editing — pacing, rhythm, and transition \
style are properties of CUTS over time, not of any one image.** So instead of evenly-spaced \
stills, these are the frame immediately BEFORE and immediately AFTER each of several real, \
ffmpeg-detected cut points in this specific video — you're seeing what actually changes at real \
edit points, not guessing from isolated pictures. The video has {total_cuts} detected cuts over \
{duration:.1f}s ({cuts_per_sec:.2f} cuts/sec, {avg_shot_len:.2f}s average shot length) — a real, \
measured pacing number, not an impression. {sample_note}

{audio_note} (This came straight from the video's own platform metadata — not from you looking \
at silent frames, which cannot identify a song. Never guess a track/artist name yourself; only \
report what's given here.)

<audience_goal>
{audience_goal}
</audience_goal>

Return ONLY this JSON, in a fenced ```json block:
{{
  "relevant": true or false — is this video actually in the niche this audience goal implies?,
  "observed": "grounded in the real cut rate above and what actually changes across these before/after pairs: is pacing fast or slow for this niche, are cuts hard or do they use a visible transition effect, does subject/framing change dramatically at cuts or stay continuous, any consistent text-overlay pattern. Concrete and specific to these images and the real numbers, not generic knowledge. If not relevant, briefly say what it actually is instead."
}}"""

ARCHITECT_SYSTEM_PROMPT = """You are a documentary/branded-content story architect — an \
experienced editor, not a relevance filter. An Assistant Editor has already exhaustively read \
this project's raw interview transcripts and independently scored each moment's fit against the \
general audience/content goal (strong / possible / off_topic). That per-fragment score is \
CONTEXT for you, never a gate: it was computed one fragment at a time, in isolation, with no \
narrative frame — real editorial judgment doesn't work that way.

**The thing you actually have to do, per Ryan's own words (2026-09-04):** "the individual \
choices on their own may seem like strange choices, [but] you need to step back and see the \
bigger overall picture. This is what real, successful editing/storytelling does." His concrete \
example: a real cut used a moment of the subject saving a toad rather than killing it — on its \
own, scored in isolation against a recruiting audience goal, that fragment looks irrelevant, \
maybe even "off_topic." It was actually essential, because the footage's real opportunity was \
countering a real on-the-job perception ("he's an asshole") by revealing a genuine, caring side \
— and a small, odd, specific moment like that does more to prove real character than any \
on-topic-sounding generic statement could. A generic relevance score can't see that; only a \
chosen narrative thesis can.

So your job has two real phases, in order — do not skip to phase 2:

**Phase 1 — find ONE concrete, narrow topic this footage can actually carry start to finish. \
Corrected 2026-09-04 after a real failure, per Ryan directly.** The first version of this \
instruction produced a "thesis" broad enough that the tight cut wandered across five unrelated \
rooms/subjects in one project (a personal reflection, wallpaper steaming, an ADA cabinet theory, \
track lighting/trusses, and a bathroom renovation hypothetical) — Ryan's own words reviewing it: \
"the stuff on the left doesn't feel like a polished, intentional build of anything, it just feels \
like a bunch of random ideas." A broad theme ("this footage shows Bob reading a house's history") \
is not a topic — it's an excuse to include everything loosely related to it. His own concrete \
example of what he actually wants: "the wallpaper would be a really good storyline to do like a \
how-to video... a clean cut... that is basically the start of Bob introducing the room and then \
how to get the wallpaper off and having to make sure you get rid of the glue because it'll mess \
with the paint." That is ONE topic — a specific technique, procedure, room, or single scene — not \
a tour. Scan the fragments for the topic with the most real, connected coverage (an actual \
beginning, a middle, an end — not just scattered mentions), and commit to THAT ONE topic. Reject \
any candidate topic that would require jumping between unrelated rooms/subjects to fill it out. \
Only after you've picked the topic, name in `narrative_thesis` why THIS topic (not a broad theme) \
is worth telling — a real angle on it, not a permission slip to wander.

While you read, also watch for the two real kinds of gold (each fragment's `category` tag names \
which one the Assistant Editor thought it was, but judge for yourself too) — but ONLY within or \
directly bookending your chosen topic, not as license to pull in an unrelated room's character \
moment just because it's charming:
- **Human/heart/comedy/fun** — a genuine character moment connected to the chosen topic/scene \
(a joke made while doing the actual work, a real reaction in that same moment) — this is what \
makes the topic feel like real people doing it, not a disconnected humanizing detour.
- **Real substantive content** — the actual step-by-step/technique/craft knowledge for THIS one \
topic. This is the spine.
Per SoldFast's own brand doctrine, a humanizing beat still comes FIRST as the hook — but it should \
be the humanizing moment INSIDE or immediately around the chosen topic/scene, not an unrelated \
character beat borrowed from somewhere else in the footage just because it's good on its own.

**Phase 2 — build a TIGHT cut from ONLY that one topic's fragments, not a sprawling tour.** Your \
`sequence` is the smallest set of fragments, ALL from the topic you picked in Phase 1, that tells \
that one thread start to finish (intro → the real steps in order → the one real caution/tip/\
payoff) — never a different room, a different procedure, or a different subject, no matter how \
good it is on its own. Favor a structure that matches a real example from the trend research \
below when one's genuinely relevant (e.g., a real how-to format's pacing) over including one more \
"nice to have" beat. A fragment that's small or individually odd can still belong here if it's \
essential to THIS topic (see the toad-style example above) — but it still has to be about the \
same topic, not just thematically supportive of a broader idea.

**Phase 3 — everything else goes in `pool_indices`, including real material about OTHER topics.** \
This is the raw selects Ryan pulls from on the other side of the timeline while tightening the \
main cut. Unlike the tight cut, the pool is NOT restricted to one topic — put every other \
genuinely on-topic-for-the-audience-goal fragment here, including the other rooms/subjects that \
didn't make the cut (the ADA cabinet, the track lighting, the bathroom hypothetical, etc. — all \
real, all usable, just not part of THIS piece's one throughline). Be inclusive here: if a \
fragment is genuinely usable for this audience goal, include it in the pool even if it repeats or \
tangents from the main cut. Leave out only genuinely off-topic material (nothing to do with the \
subject at all) — that goes in neither list.

Hard rules:
- You may ONLY select from the fragments given to you, by their [index]. Never invent a time \
range, a quote, or a moment that isn't in the list — every selection must trace to a real, \
already-extracted fragment.
- Each fragment index may appear AT MOST ONCE in `sequence` — never reuse the same index for two \
different roles (e.g. once as "hook" and again as "payoff"). If a moment genuinely serves two \
purposes, pick ONE role for it rather than placing the same real clip on the timeline twice.
- `sequence` must be about ONE topic, no exceptions. If you're tempted to include a fragment \
because it's a good moment "from the same footage" rather than because it's genuinely part of the \
one topic's own start-to-finish arc, it belongs in `pool_indices`, not `sequence`.
- Most real interviews have more usable material than fits in one story — be honest about what \
you left out and why, in `omitted_reasoning`.
- Live trend research (given below) informs framing/tone only — never overrides what the \
footage and thesis actually support.
- Every arc must concretely answer, in `editorial_qna`: what's the bigger overall story, what \
makes it worth watching, how it relates to the viewer, and what the CTA is. None of these may be \
blank or generic filler — a real answer traces to the actual fragments and thesis you chose.

Return ONLY the structured output specified in the prompt, as a fenced ```json code block."""

ARCHITECT_PROMPT_TEMPLATE = """This project's stated audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

ALL real, already-extracted transcript fragments — the fit label is the Assistant Editor's \
ISOLATED, per-fragment score (context, not a gate; see system prompt):

<fragments>
{fragments}
</fragments>

Live trend research already gathered for this project. This is NOT background colour to cite in \
the write-up — it is a constraint on the cut itself. Where a finding says something concrete \
about FORMAT (how a piece like this is structured, what the hook pattern is, how long it runs, \
how fast it cuts), you must actually build to it: it governs which fragments you select, how many, \
what order they go in, and how long the result runs. Citing a format in `why_it_works` while \
building something shaped nothing like it is a failure, not a stylistic choice.

<trend_research>
{trend_research}
</trend_research>
{planning_context}

First, pick the ONE concrete, narrow topic (per Phase 1) this footage can carry start to finish, \
and in `narrative_thesis` name why THIS topic is worth telling — not a broad theme, not the \
generic audience goal restated. Then produce TWO separate lists: `sequence` — the tight, \
single-topic cut (by index, role hook/build/payoff, in order), every fragment genuinely about \
that one topic — and `pool_indices` — every other genuinely on-topic-for-the-audience-goal \
fragment, INCLUDING real material about other topics/rooms that didn't make this piece. Do not \
restrict yourself to "strong"-fit fragments for either list — a small, individually-odd moment \
that genuinely serves the chosen topic belongs in the sequence even if its isolated fit score was \
lower, and the pool should be generous with on-topic material regardless of topic.

**Every arc must be able to answer these specific editorial questions (Ryan, 2026-09-04) — fill \
in `editorial_qna` with a real, specific, concrete answer for each. None may be generic filler \
or left blank; "no CTA" is not an acceptable answer for `cta` — every piece asks for SOMETHING, \
even if it's soft ("follow for more," "DM to apply," a specific link):**

Return this exact JSON shape, in a fenced ```json block:

{{
  "narrative_thesis": "the ONE concrete, narrow topic this piece is about, and why it's worth telling — not a broad theme",
  "title": "short concept title",
  "hook": "1-2 sentence opening hook/headline",
  "why_it_works": "why this arc serves the thesis and the stated audience goal, citing which fragments (including any 'strange choice' ones and why they earn their place) and, if relevant, which real trend finding informed the framing",
  "editorial_qna": {{
    "bigger_story": "What is the bigger overall story being told in this video? (the arc as a whole, not just the thesis restated)",
    "why_watch": "What makes this specifically worth watching? Be concrete, not 'it's engaging.'",
    "viewer_relevance": "How does this actually relate to the viewer watching it — what's in it for them, or what do they recognize in it?",
    "cta": "What is the specific call to action, and why this one (not a generic 'follow us')?"
  }},
  "tone": "editorial tone guidance, e.g. 'quiet, unhurried, heart-led'",
  "target_duration_sec": <number of seconds this cut should run. If a target length was given above, this MUST be within it — and the `sequence` you return must actually add up to roughly that, not overrun it. This is checked after you answer; a sequence whose real duration blows the target is rejected and regenerated.>,
  "target_audience": "who this is for, restated from the audience goal",
  "call_to_action": "same specific CTA as editorial_qna.cta",
  "sequence": [
    {{"index": 0, "role": "hook"}},
    {{"index": 3, "role": "build"}},
    {{"index": 7, "role": "payoff"}}
  ],
  "pool_indices": [1, 2, 4, 5, 6, 8, 9],
  "omitted_reasoning": "1-2 sentences on what's genuinely off-topic and left out of both the sequence and the pool, and why"
}}"""


def _format_planning_context(stated_intent: str, max_duration_sec: float) -> str:
    """The block that carries the editor's own stated goal for THIS piece
    (and the length it has to fit) into the sequencing prompt as a real
    constraint. Empty string when nothing was stated — the undirected
    path behaves exactly as before."""
    intent = (stated_intent or "").strip()
    if not intent and not (max_duration_sec and max_duration_sec > 0):
        return ""

    parts = ["\nWHAT THE EDITOR ACTUALLY ASKED FOR — this governs the cut, not just its framing:\n"]
    if intent:
        parts.append(
            f"<stated_intent>\n{intent}\n</stated_intent>\n\n"
            "Build THIS. If the footage genuinely can't support it, say so plainly in "
            "`narrative_thesis` rather than quietly building a different piece that happens "
            "to be easier to assemble from the material."
        )
    if max_duration_sec and max_duration_sec > 0:
        parts.append(
            f"\n\nTARGET LENGTH: the tight cut (`sequence`) must run about "
            f"{max_duration_sec:.0f} seconds or less. This is measured against your actual "
            f"selected fragments after you answer, and a cut that overruns it is rejected. "
            f"Select fewer, shorter, better fragments — do not select everything good and "
            f"hope the length works out. Material that's genuinely on-topic but doesn't fit "
            f"in the time belongs in `pool_indices`, which has no length limit."
        )
    return "".join(parts) + "\n"


def _collect_candidate_fragments(
    tagged_by_source: Dict[str, List[TaggedFragment]],
) -> List[TaggedFragment]:
    """Flatten a project's per-source tagged fragments into ONE candidate
    pool — ALL of them, every fit level included.

    Corrected 2026-09-04 (Ryan): this used to filter down to "strong"
    (widening to "possible" only if there weren't enough) BEFORE the
    architect ever saw the material. That's backwards — a real editor
    doesn't pre-filter by an isolated per-fragment relevance score and
    then look for a story in what's left; they find the actual narrative
    thesis first, and THEN judge which real fragments serve it, including
    small/odd ones a naive relevance check would call "off_topic" (Ryan's
    own example: a real cut used a moment of the subject saving a toad —
    worthless by isolated topical relevance, essential once you know the
    thesis was "reveal the gruff guy's genuine heart"). The fit label is
    now passed through as context in the prompt, never used to exclude
    material before the architect reasons about it."""
    all_tagged: List[TaggedFragment] = []
    for tagged_list in tagged_by_source.values():
        all_tagged.extend(tagged_list)
    return all_tagged


def _format_candidates_for_llm(candidates: List[TaggedFragment]) -> str:
    lines = []
    for i, tf in enumerate(candidates):
        f = tf.fragment
        category = f" category={tf.category}" if tf.category else ""
        reasoning = f" — AE's reasoning: {tf.reasoning}" if tf.reasoning else ""
        lines.append(
            f'[{i}] fit={tf.fit}{category} file="{f.source_file}" '
            f'{f.source_start_sec:.1f}s-{f.source_end_sec:.1f}s '
            f'"{f.topic_label}": {f.summary}{reasoning}'
        )
    return "\n".join(lines)


def _looks_like_video_permalink(url: str) -> bool:
    return bool(url and _VIDEO_PERMALINK_RE.search(url))


def _get_audio_credit(url: str) -> Optional[dict]:
    """Real audio/sound credit straight from the video's own metadata —
    not AI-identified, not guessed. TikTok/Reels expose this directly
    (track/artist, or "original sound - <creator>" for a creator's own
    audio) via yt-dlp's info extraction, confirmed 2026-09-03 against
    real videos. Ryan asked for real trending audio/music signal — this
    is the honest source for it, not asking a vision model to name a
    song from silent frames, which it cannot actually do."""
    try:
        out = subprocess.run(
            ["yt-dlp", "--no-warnings", "-j", url],
            check=True, capture_output=True, timeout=30, text=True,
        )
        info = json.loads(out.stdout)
    except Exception:
        return None
    track = info.get("track")
    artist = info.get("artist") or (info.get("artists") or [None])[0]
    if not track and not artist:
        return None
    return {"track": track, "artist": artist}


AUDIO_LISTEN_LINK_PROMPT = """Search the web for a real, direct link where someone could \
actually listen to or download this specific track:

Track: {track}
Artist: {artist}

I need a real link — Spotify, Apple Music, YouTube, SoundCloud, or a TikTok/Instagram sound \
page — not a page that merely mentions the song. Return ONLY this JSON, in a fenced ```json \
block: {{"listen_url": "https://... or empty string if you can't find a real one"}}. Never \
fabricate a link."""


def _find_listen_link(track: str, artist: str, client, model: str) -> str:
    """Real, searched link to actually hear the track credited on a
    watched video — Ryan, 2026-09-04: a trending-audio finding is only
    useful if an editor can actually go listen to it, not just read its
    name. Returns "" on any failure rather than a guessed URL."""
    if not track and not artist:
        return ""
    try:
        resp = client.messages.create(
            model=model, max_tokens=300, tools=[WEB_SEARCH_TOOL],
            system="Return ONLY the requested JSON, in a fenced ```json block. No preamble.",
            messages=[{"role": "user", "content": AUDIO_LISTEN_LINK_PROMPT.format(
                track=track or "(unknown)", artist=artist or "(unknown)")}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if not text:
            return ""
        return str(_extract_json(text).get("listen_url", "")).strip()
    except Exception:
        return ""


def _looks_like_youtube_video(url: str) -> bool:
    return bool(url and _YOUTUBE_VIDEO_RE.search(url))


_VTT_TIMESTAMP_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")
_VTT_TAG_RE = re.compile(r"<[^>]+>")


def _parse_vtt(vtt_text: str) -> str:
    """Real transcript text from a .vtt caption file — strips cue numbers,
    timestamps, and inline formatting tags, and dedupes the rolling-caption
    repetition auto-generated captions produce (each line often repeats
    the previous one plus a word or two)."""
    lines = []
    for raw in vtt_text.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.isdigit() or _VTT_TIMESTAMP_RE.search(line):
            continue
        if line.startswith("Kind:") or line.startswith("Language:"):
            continue
        line = _VTT_TAG_RE.sub("", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return " ".join(lines)


def _get_transcript(url: str) -> Optional[str]:
    """Real captions for a real YouTube video via yt-dlp — no download of
    the video itself needed, since for strategy/educational content the
    SPOKEN content is the signal, not visual editing pacing (that's what
    `_watch_video` is for, for short-form trend clips)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_base = tmp_path / "cap"
        try:
            subprocess.run(
                ["yt-dlp", "--no-warnings", "--write-auto-sub", "--skip-download",
                 "--sub-lang", "en", "--sub-format", "vtt",
                 "-o", str(out_base) + ".%(ext)s", url],
                check=True, capture_output=True, timeout=45,
            )
        except Exception:
            return None
        vtt_files = list(tmp_path.glob("cap*.vtt"))
        if not vtt_files:
            return None
        try:
            text = _parse_vtt(vtt_files[0].read_text(errors="ignore"))
        except Exception:
            return None
        return text or None


def _watch_strategy_video(url: str, client, model: str, audience_goal: str) -> Optional[dict]:
    """Real, transcript-grounded extraction from a real strategy/educational
    video — never summarized from the video's title/description alone.
    Returns None if no real transcript is available or the model's
    response doesn't parse, rather than fabricating advice."""
    transcript = _get_transcript(url)
    if not transcript:
        return None
    # Cap transcript length fed to the model — long videos can run well
    # past a reasonable prompt budget; the real advice in a strategy
    # video is almost always front-loaded or clearly signposted anyway.
    transcript = transcript[:12000]
    try:
        resp = client.messages.create(
            model=model, max_tokens=700,
            system="Return ONLY the requested JSON, in a fenced ```json block. No preamble.",
            messages=[{"role": "user", "content": STRATEGY_TRANSCRIPT_PROMPT.format(
                audience_goal=audience_goal, transcript=transcript)}],
        )
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
        "points": [str(p)[:300] for p in parsed.get("points", [])][:6],
    }


MAX_CUT_PAIRS = 4  # frame-before/frame-after pairs sent to the vision call
SCENE_THRESHOLD = 0.3  # ffmpeg scene-detection sensitivity, same default the tool ships with


def _probe_duration(video_path: Path) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            check=True, capture_output=True, timeout=15, text=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _detect_cuts(video_path: Path) -> List[float]:
    """Real ffmpeg scene-change detection — the actual cut points in this
    specific video, not a guess. Editing pacing/rhythm is a property of
    WHEN cuts happen, which no fixed-interval frame sample can capture."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-i", str(video_path),
             "-filter:v", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
             "-f", "null", "-"],
            capture_output=True, timeout=30, text=True,
        )
    except Exception:
        return []
    timestamps = []
    for match in re.finditer(r"pts_time:([\d.]+)", out.stderr):
        try:
            timestamps.append(float(match.group(1)))
        except ValueError:
            continue
    return timestamps


def _extract_frame_at(video_path: Path, timestamp: float, out_path: Path) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video_path),
             "-vf", "scale=400:-1", "-frames:v", "1", str(out_path)],
            check=True, capture_output=True, timeout=15,
        )
        return out_path.exists()
    except Exception:
        return False


def _watch_video(url: str, client, model: str, audience_goal: str) -> Optional[dict]:
    """Actually download a real video and have Claude look at frames sampled
    at REAL, ffmpeg-detected cut points — not fixed time intervals. A
    single still can't say anything about editing pacing/rhythm; those are
    properties of cuts over time. Sampling before/after real cut points
    lets the model see actual transition character and a real, measured
    cuts-per-second rate, not an impression from isolated pictures.
    Returns None on ANY failure (blocked, deleted, unsupported format are
    all real and expected against TikTok/Instagram) rather than
    fabricating a description of a video that was never actually opened."""
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
        video_file = video_files[0]

        audio_credit = _get_audio_credit(url)
        duration = _probe_duration(video_file)
        cuts = _detect_cuts(video_file)
        if not duration or duration <= 0:
            return None

        total_cuts = len(cuts)
        cuts_per_sec = total_cuts / duration
        avg_shot_len = duration / (total_cuts + 1)

        # Sample cut points spread across the whole video, not just the
        # first few — a video that starts slow and speeds up (or vice
        # versa) needs coverage across its length to describe honestly.
        if cuts:
            step = max(1, len(cuts) // MAX_CUT_PAIRS)
            sampled_cuts = cuts[::step][:MAX_CUT_PAIRS]
        else:
            sampled_cuts = []

        frames: List[Path] = []
        for i, cut_t in enumerate(sampled_cuts):
            before_path = tmp_path / f"cut{i}_before.jpg"
            after_path = tmp_path / f"cut{i}_after.jpg"
            if _extract_frame_at(video_file, cut_t - 0.15, before_path):
                frames.append(before_path)
            if _extract_frame_at(video_file, cut_t + 0.15, after_path):
                frames.append(after_path)

        if not frames:
            # No detected cuts (a genuinely static/single-shot video) or
            # extraction failed for all of them — fall back to a few
            # evenly-spaced stills so we can still say SOMETHING real
            # about visual content, but sample_note below makes clear no
            # real pacing claim is being made from these.
            for i, t in enumerate([duration * f for f in (0.2, 0.5, 0.8)]):
                p = tmp_path / f"fallback{i}.jpg"
                if _extract_frame_at(video_file, t, p):
                    frames.append(p)
            if not frames:
                return None
            sample_note = ("No cuts were detected (or frame extraction at "
                            "cut points failed) — these are evenly-spaced stills "
                            "instead. Do not claim a specific pacing/cuts-per-second "
                            "reading from these; describe only what you can see.")
        else:
            sample_note = (f"These are {len(frames)} real before/after frames from "
                            f"{len(sampled_cuts)} of the {total_cuts} detected cuts, "
                            "spread across the video's full length.")

        if audio_credit and (audio_credit.get("track") or audio_credit.get("artist")):
            audio_note = (f"Real audio credit from this video's own metadata (not "
                           f"AI-guessed): track \"{audio_credit.get('track') or '?'}\" "
                           f"by {audio_credit.get('artist') or 'unknown'}.")
        else:
            audio_note = "No audio credit metadata available for this video."

        content = [{"type": "text", "text": VIDEO_WATCH_PROMPT.format(
            url=url, audience_goal=audience_goal, total_cuts=total_cuts,
            duration=duration, cuts_per_sec=cuts_per_sec, avg_shot_len=avg_shot_len,
            sample_note=sample_note, audio_note=audio_note)}]
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
            resp = client.messages.create(model=model, max_tokens=600,
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

        relevant = bool(parsed.get("relevant", False))
        listen_url = ""
        if relevant and audio_credit and (audio_credit.get("track") or audio_credit.get("artist")):
            # Only spend the extra search on videos that were actually
            # relevant — no point sourcing a listen link for a video the
            # watch step already threw out.
            listen_url = _find_listen_link(
                audio_credit.get("track", ""), audio_credit.get("artist", ""), client, model,
            )

        return {
            "url": url,
            "relevant": relevant,
            "observed": str(parsed.get("observed", ""))[:1000],
            "frames_analyzed": len(frames),
            # Real, measured — not the model's impression — so the UI/audit
            # trail can show a hard number, not just prose (Ryan: "you
            # can't understand video editing trends by looking at 4
            # random frames" — this is the actual fix, not a bigger N).
            "detected_cuts": total_cuts,
            "duration_sec": round(duration, 1),
            "cuts_per_sec": round(cuts_per_sec, 2),
            # Real, from the video's own platform metadata — never AI-guessed
            # from silent frames (Ryan asked specifically for real audio/
            # sound trend signal, 2026-09-03).
            "audio_track": (audio_credit or {}).get("track"),
            "audio_artist": (audio_credit or {}).get("artist"),
            # Real, searched listen/download link (Ryan, 2026-09-04) — "" if
            # none found, never fabricated.
            "audio_listen_url": listen_url,
        }


RESEARCH_CACHE_MAX_AGE_SEC = 72 * 3600  # Ryan, 2026-09-04: don't re-research

# How far over an agreed target length a tight cut may land before it's
# rejected. Fragments are whole transcript phrases and can't be trimmed
# mid-sentence at this stage, so demanding an exact fit would reject
# genuinely good cuts over a few seconds of unavoidable overshoot. 1.25
# leaves room for one slightly-long closing phrase while still catching
# the failure this exists for (a "Reel" that came out 12:44 against a
# ~60s intent is 12x over, not 25% over).
DURATION_OVERRUN_TOLERANCE = 1.25


def _research_cache_key_text(audience_goal: str, stated_intent: str = "") -> str:
    """The exact text the cache is keyed on. `stated_intent` MUST be part
    of it (2026-09-04): once a stated intent redirects the searches (see
    `research_trends`), targeted research for "how to remove wallpaper,
    quick and engaging" is a genuinely different result set from the
    generic sweep for the same audience_goal. Keying on audience_goal
    alone would serve one as the other from cache — silently returning
    generic trends for a targeted run, or worse, pinning every later
    generic run to whatever intent happened to be researched first."""
    goal = audience_goal.strip()
    intent = (stated_intent or "").strip()
    return f"{goal}\n<<INTENT>>\n{intent}" if intent else goal


def _research_cache_path(audience_goal: str, stated_intent: str = "") -> Path:
    """Shared across ALL projects, not per-project — the same audience/goal
    profile (e.g. "Contractor Recruiting") gets reused across shoots, and
    the whole point of caching is not paying for the same research twice.
    Keyed by the exact audience_goal text (a hash of it, for a safe
    filename) rather than a profile id, since story_architect only ever
    sees the resolved description text, never the profile id it came
    from. A stated intent, when present, is part of that key."""
    cache_dir = app_support_dir() / "research_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key_text = _research_cache_key_text(audience_goal, stated_intent)
    key = hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{key}.json"


def _load_cached_research(audience_goal: str, stated_intent: str = "") -> Optional[dict]:
    path = _research_cache_path(audience_goal, stated_intent)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    age_sec = time.time() - payload.get("cached_at", 0)
    if age_sec > RESEARCH_CACHE_MAX_AGE_SEC:
        return None
    stored_key = payload.get("cache_key_text")
    if stored_key is None:
        # Pre-2026-09-04 cache entry, written before stated_intent existed.
        # Still valid for a generic (no-intent) run — accepting it avoids
        # throwing away a paid-for research pass on upgrade — but never
        # for a targeted one, which needs genuinely different findings.
        if (stated_intent or "").strip():
            return None
        if payload.get("audience_goal") != audience_goal.strip():
            return None
    elif stored_key != _research_cache_key_text(audience_goal, stated_intent):
        return None  # hash collision or stale key reuse — never trust a mismatch
    return payload.get("research")


def _save_research_cache(audience_goal: str, research: dict, stated_intent: str = "") -> None:
    path = _research_cache_path(audience_goal, stated_intent)
    try:
        path.write_text(json.dumps({
            "audience_goal": audience_goal.strip(),
            "stated_intent": (stated_intent or "").strip(),
            "cache_key_text": _research_cache_key_text(audience_goal, stated_intent),
            "cached_at": time.time(),
            "research": research,
        }, indent=2))
    except Exception:
        pass  # caching is a cost optimization, never allowed to break a real run


def _augment_goal_with_intent(audience_goal: str, stated_intent: str) -> str:
    """Fold a stated intent INTO the goal block every research prompt
    already interpolates, so one addition redirects every search
    (trends, named trends, example videos, marketing strategy, strategy
    videos) instead of five near-identical prompt edits.

    2026-09-04, Ryan: "if I told it straight up what I'm looking for out
    of this is a how to wallpaper a bathroom type content that makes us
    look like the experts... then it could go look at high trending
    how-to renovation videos." Without this the searches only ever see
    the broad project-level audience profile, so they come back with a
    generic niche sweep that can't inform a specific piece."""
    goal = audience_goal.strip()
    intent = (stated_intent or "").strip()
    if not intent:
        return goal
    return (
        f"{goal}\n\n"
        f"THIS SPECIFIC PIECE — the editor has said exactly what they want to make "
        f"from the footage they have:\n\"{intent}\"\n\n"
        f"Target every search at THAT specifically: what is working right now for this "
        f"exact kind of piece — its format, structure, pacing, hook pattern, and typical "
        f"length — not a broad sweep of the niche. Findings that don't help someone build "
        f"this particular piece are not useful here."
    )


def research_trends(
    audience_goal: str,
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
    force_refresh: bool = False,
    stated_intent: str = "",
) -> dict:
    """Live trend research. Cached per exact audience_goal text (plus
    stated_intent, when given) for up to 72 hours (Ryan, 2026-09-04: "if
    the researcher has gone through or found research for a specific
    audience/goal in the last 72 hours it shouldn't do that work a
    second time, so we can save time and money") — shared across every
    project using that same audience/goal, not just re-runs of one
    project. Superseded from the original "live, run it fresh every
    time, never cached" rule (2026-09-03) by this later, more specific
    instruction. Pass `force_refresh=True` to bypass the cache
    deliberately. A cache hit is marked `research["cached"] = True` /
    `research["cached_at"]` so callers and the UI can tell.

    `stated_intent` is the editor's own description of the piece they're
    trying to make ("a quick how-to on wallpaper removal that makes us
    look like the experts"). When present it redirects every search at
    that specific piece rather than the broad niche — see
    `_augment_goal_with_intent`. It is part of the cache key, so a
    targeted run never gets served the generic sweep or vice versa.

    Two kinds of finding, clearly labeled and never conflated:
    `text_findings` (read from articles about trends, sourced by URL) and
    `video_findings` (a real video actually downloaded and watched via
    real sampled frames — see module docstring). `unverified` always says
    plainly when a search or a video came up empty rather than padding
    either list."""
    if not force_refresh:
        cached = _load_cached_research(audience_goal, stated_intent)
        if cached is not None:
            cached["cached"] = True
            return cached

    # Keep the ORIGINAL goal for cache keying — the cache is keyed on
    # (raw goal, stated_intent), and `_load_cached_research` above used
    # exactly that. Writing the augmented text back would save under a
    # key no read ever looks up, silently disabling the cache.
    raw_audience_goal = audience_goal
    # Every prompt below interpolates this as {audience_goal}; when an
    # intent was stated it carries the redirect (see helper above).
    audience_goal = _augment_goal_with_intent(audience_goal, stated_intent)

    client = build_anthropic_client(api_key=api_key)
    result: dict = {
        "named_trends": [], "text_findings": [], "video_findings": [],
        "marketing_findings": [], "strategy_video_findings": [], "unverified": [],
    }

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

    # Find SPECIFIC named trends (a sound, a challenge, a signature format)
    # first, so the video search can target an actual example of one of
    # them — rather than a generic niche keyword search that just returns
    # whatever ranks first for "construction" or "renovation" (Ryan:
    # "not just look at the first video that pops up in a search result").
    named_trends: List[dict] = []
    try:
        resp_names = client.messages.create(
            model=model, max_tokens=1200, tools=[WEB_SEARCH_TOOL],
            system="Return ONLY the requested JSON, in a fenced ```json block. No preamble.",
            messages=[{"role": "user", "content": TREND_NAMES_SEARCH_PROMPT.format(
                audience_goal=audience_goal)}],
        )
        text_names = "".join(b.text for b in resp_names.content if getattr(b, "type", None) == "text").strip()
        if text_names:
            raw_trends = _extract_json(text_names).get("trends", [])
            # Defensive filter: despite the prompt saying "empty list if
            # none found," a model can still wrap that refusal as a fake
            # entry (seen for real, 2026-09-03) — a real trend name is
            # short; a disclaimer sentence isn't.
            named_trends = [
                t for t in raw_trends
                if t.get("name") and len(t["name"]) <= 80
                and "no specific" not in t["name"].lower()
                and "not found" not in t["name"].lower()
            ]
        result["named_trends"] = named_trends
    except Exception as e:
        result["unverified"].append(f"Named-trend search failed: {e}")

    # 2026-09-04: named trends' listen_url came back empty in 10/10 real
    # runs on a real project (checked directly against every saved
    # story_research/*.json for "How to remove wallpaper") — Ryan: "the
    # brief still isnt sharing where to download or find the trending
    # audio sounds." Root cause: the trend-names call above asks the
    # model to self-report listen_url inline, unverified, in the same
    # pass that just names the trend — exactly the kind of unchecked
    # assertion "cite or don't assert" exists to catch. Fix: reuse the
    # SAME real, searched lookup (_find_listen_link) already built and
    # working for video_findings' audio credits, as a second real pass
    # over any trend whose self-reported link came back empty. Still
    # returns "" (never a fabricated link) on failure.
    for t in named_trends:
        if t.get("listen_url"):
            continue
        name = t.get("name", "")
        if not name:
            continue
        # "A New Season Had Begun (sound by @olivialodenius)" — split out
        # a credited artist/handle when the model included one; otherwise
        # search on the trend name alone.
        artist = ""
        track = name
        m = re.search(r"\(sound by (@?[\w.]+)\)", name, re.I)
        if m:
            artist = m.group(1)
            track = name[:m.start()].strip()
        try:
            t["listen_url"] = _find_listen_link(track, artist, client, model)
        except Exception:
            pass

    if not named_trends:
        result["unverified"].append(
            "No specific NAMED trend (a sound, a challenge, a signature format) found for "
            "this niche this run — video search below falls back to a general niche search "
            "rather than targeting a named trend."
        )
        trend_names_clause = ""
    else:
        names_list = ", ".join(f'"{t.get("name","")}"' for t in named_trends if t.get("name"))
        trend_names_clause = (
            f"\nSpecifically, find a video actually doing one of these named trends found above: "
            f"{names_list}. Prefer that over a generic niche-keyword result."
        )

    candidate_urls: List[str] = []
    try:
        resp2 = client.messages.create(
            model=model, max_tokens=600, tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": VIDEO_PERMALINK_SEARCH_PROMPT.format(
                audience_goal=audience_goal, trend_names_clause=trend_names_clause)}],
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
    if not _deep_research_enabled():
        result["unverified"].append(
            "Video watching was explicitly disabled for this run "
            "(POSTHOUSE_WATCH_VIDEOS=0), so trend findings are from web search only "
            "and nothing here was verified against a real video."
        )
        candidate_urls = []
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

    # --- Marketing/audience-targeting strategy research (2026-09-04) ---
    # Ryan: "it also needs to do actual research into the most current
    # social media, marketing, targeting information out there... blogs,
    # articles, videos on how to take real command of your social media
    # and advertising." Distinct from trend-spotting above (what's
    # circulating right now) — this is craft/strategy (how to reach and
    # actually serve a specific audience), and it's real, sourced
    # article research PLUS real transcript-grounded extraction from
    # real long-form YouTube strategy videos (never frame-sampled —
    # the spoken content is the signal here, not editing pacing).
    try:
        resp_mkt = client.messages.create(
            model=model, max_tokens=3000, tools=[WEB_SEARCH_TOOL],
            system="Return ONLY the requested JSON, in a fenced ```json block. No preamble.",
            messages=[{"role": "user", "content": MARKETING_STRATEGY_SEARCH_PROMPT.format(
                audience_goal=audience_goal)}],
        )
        text_mkt = "".join(b.text for b in resp_mkt.content if getattr(b, "type", None) == "text").strip()
        result["marketing_findings"] = _extract_json(text_mkt).get("findings", []) if text_mkt else []
        if not text_mkt:
            result["unverified"].append("Marketing-strategy search returned no text output.")
    except Exception as e:
        result["marketing_findings"] = []
        result["unverified"].append(f"Marketing-strategy search failed: {e}")

    strategy_video_urls: List[str] = []
    try:
        resp_mkt_vid = client.messages.create(
            model=model, max_tokens=600, tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": MARKETING_VIDEO_SEARCH_PROMPT.format(
                audience_goal=audience_goal)}],
        )
        for block in resp_mkt_vid.content:
            if getattr(block, "type", None) == "web_search_tool_result":
                items = block.content if isinstance(block.content, list) else []
                for item in items:
                    url = getattr(item, "url", None)
                    if url and _looks_like_youtube_video(url):
                        strategy_video_urls.append(url)
    except Exception as e:
        result["unverified"].append(f"Strategy-video search failed: {e}")

    result["strategy_video_findings"] = []
    if not strategy_video_urls:
        result["unverified"].append(
            "No individual strategy/educational YouTube video URLs found via search this run."
        )
    seen_mkt = set()
    if not _deep_research_enabled():
        result["unverified"].append(
            "Strategy-video transcript reading was explicitly disabled for this run "
            "(POSTHOUSE_WATCH_VIDEOS=0), so marketing findings are from web search "
            "only."
        )
        strategy_video_urls = []
    for url in strategy_video_urls:
        if len(result["strategy_video_findings"]) >= MAX_STRATEGY_VIDEOS_TO_WATCH:
            break
        if url in seen_mkt:
            continue
        seen_mkt.add(url)
        extracted = _watch_strategy_video(url, client, model, audience_goal)
        if extracted:
            result["strategy_video_findings"].append(extracted)
        else:
            result["unverified"].append(
                f"Found candidate strategy video {url} but could not get/use its transcript "
                "(no captions available, or the model found no real actionable content in it)."
            )

    result["cached"] = False
    result["stated_intent"] = (stated_intent or "").strip()
    _save_research_cache(raw_audience_goal, result, stated_intent)
    return result


def _format_citations_plaintext(research: dict) -> str:
    """Plain-text (no markdown syntax) citation list — real source/listen
    links only, for an editor reading the on-timeline Creative Brief
    marker inside Premiere itself. Skips the noisier audit-trail-only
    entries (unverified/excluded) that belong in the full .md brief and
    the research JSON, not a marker comment an editor has to read cold."""
    lines = ["=== SOURCES / EXAMPLES THIS IS BASED ON ==="]
    for t in research.get("named_trends", []):
        listen = f" | listen: {t['listen_url']}" if t.get("listen_url") else ""
        lines.append(f"- Trend \"{t.get('name','')}\": {t.get('source','')}{listen}")
    for v in research.get("video_findings", []):
        if not v.get("relevant"):
            continue
        audio = ""
        if v.get("audio_track") or v.get("audio_artist"):
            link = f" | listen: {v['audio_listen_url']}" if v.get("audio_listen_url") else ""
            audio = f" | audio: {v.get('audio_track') or '?'} - {v.get('audio_artist') or 'unknown'}{link}"
        lines.append(f"- Watched: {v['url']}{audio}")
    for f in research.get("marketing_findings", []):
        lines.append(f"- Strategy source: {f.get('source','')}")
    for f in research.get("strategy_video_findings", []):
        if f.get("relevant"):
            lines.append(f"- Strategy video: {f['url']}")
    if len(lines) == 1:
        lines.append("(no sourced examples found this run)")
    return "\n".join(lines)


def _format_research_for_llm(research: dict) -> str:
    lines = []
    for t in research.get("named_trends", []):
        lines.append(f"[named trend] {t.get('name','')} — {t.get('description','')} (source: {t.get('source','')})")
    for f in research.get("text_findings", []):
        lines.append(f"[text-sourced, from an article] {f.get('finding','')} (source: {f.get('source','')})")
    for f in research.get("video_findings", []):
        if not f.get("relevant", False):
            continue  # honestly flagged as off-niche by the watch step — not real signal
        lines.append(f"[ACTUALLY WATCHED — real video at {f.get('url','')}] {f.get('observed','')}")
    for f in research.get("marketing_findings", []):
        lines.append(f"[audience-targeting/strategy advice, from an article] {f.get('finding','')} (source: {f.get('source','')})")
    for f in research.get("strategy_video_findings", []):
        if not f.get("relevant", False):
            continue
        for point in f.get("points", []):
            lines.append(f"[from a real strategy video's transcript, {f.get('url','')}] {point}")
    for u in research.get("unverified", []):
        lines.append(f"[UNVERIFIED] {u}")
    return "\n".join(lines) if lines else "(no trend research available this run)"


def build_source_offset_lookup(project) -> Dict[str, float]:
    """original_file_path -> combined-timeline offset (seconds).

    Real bug found and fixed 2026-09-04: `assemble_cut_from_angle`
    resolves which source file a range belongs to purely from where its
    `source_start_sec` falls in the COMBINED multi-transcript timeline
    (`resolve_real_source`, exporter.py/story_assembler.py) — it does
    NOT trust `TopicRange.source_file`. But every fragment this module
    works with (`transcript_coverage`/`audience_relevance`) is extracted
    from ONE transcript at a time, so its start/end are in that FILE'S
    OWN local time, not the combined timeline. Confirmed concretely on
    real data: a fragment at local 565.3s in file `..._0004_D` was, by
    the combined timeline, inside file `..._0003_D`'s span instead
    (565.3 < that file's own combined-offset start of 578.0) — meaning
    the wrong clip would have been silently placed on export. This
    lookup lets `generate_story_angle` add the correct offset before
    emitting a range, so its coordinates actually mean what
    `assemble_cut_from_angle` assumes they mean.
    """
    import exporter as _exporter  # top-level app module, not PreCut donor code
    from precut_pipeline.transcriber import Transcript as _Transcript

    transcript_paths = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not transcript_paths:
        return {}
    offset_by_proxy = _exporter._build_source_offset_map(transcript_paths)
    proxy_to_original = _exporter._build_proxy_to_original_map(project, transcript_paths)
    return {
        proxy_to_original[proxy]: offset
        for proxy, offset in offset_by_proxy.items()
        if proxy in proxy_to_original
    }


def generate_story_angle(
    audience_goal: str,
    tagged_by_source: Dict[str, List[TaggedFragment]],
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
    research: Optional[dict] = None,
    source_offset_lookup: Optional[Dict[str, float]] = None,
    avoid_theses: Optional[List[str]] = None,
    stated_intent: str = "",
    max_duration_sec: float = 0.0,
) -> tuple:
    """Build one real StoryAngle from a project's exhaustively-extracted,
    audience-scored fragments plus live trend research.

    `source_offset_lookup` (original_file_path -> combined-timeline
    offset seconds, see `build_source_offset_lookup`) is added to each
    selected fragment's start/end before building its `TopicRange` — see
    that function's docstring for the real bug this fixes. Pass None only
    when you know the export path (`assemble_cut_from_angle`) will never
    see this angle's ranges.

    `avoid_theses`, when given, is folded into the prompt so this call
    produces a genuinely distinct angle instead of repeating one already
    generated in the same batch (see `generate_story_angles`).

    `research` should normally come from a prior `research_trends()` call
    (kept separate so it's independently inspectable/persistable — see
    `save_story_research`); if omitted, this function runs it internally.

    Returns (angle, research). PreCut's `CreativeBrief` dataclass has no
    field for the sourced findings (it's PreCut's own schema, not ours to
    extend), so `why_it_works` folds citations in as prose, but the full
    research would otherwise be silently dropped the moment this function
    returns — `research` exists so the caller persists that audit trail.

    `stated_intent` is the editor's own words for the piece they want
    ("a quick how-to on wallpaper removal that positions us as the
    experts"), captured in the planning conversation before any of this
    runs. `max_duration_sec`, when > 0, is a REAL cap: the assembled
    tight cut is measured against it after generation and a run that
    overruns raises rather than shipping. Both exist because research
    and stated goals used to be prompt decoration only — a cut citing
    Instagram Reel formats came out 12:44 long (Ryan, 2026-09-04: "The
    trends are meant to be applied to the edit on the timeline that is
    pitched. The steps exist to inform the next step not to just check
    off and move on.").

    Raises StoryPlannerError if there isn't enough real material to build
    from, if the API call / JSON parsing fails, or if the resulting cut
    overruns `max_duration_sec` — this never falls back to inventing a
    story from nothing, and never silently ships a cut that ignores the
    length it was asked for.
    """
    if not audience_goal or not audience_goal.strip():
        raise ValueError(
            "audience_goal must be non-empty — this module has no basis "
            "to judge or sequence a story without it."
        )

    candidates = _collect_candidate_fragments(tagged_by_source)
    if not candidates:
        raise StoryPlannerError(
            "No extracted fragments available at all — nothing real to "
            "build a story arc from. Run transcript flagging first."
        )

    if research is None:
        research = research_trends(
            audience_goal.strip(), model=model, api_key=api_key,
            stated_intent=stated_intent,
        )

    avoid_clause = ""
    if avoid_theses:
        titles = "\n".join(f"- {t}" for t in avoid_theses)
        avoid_clause = (
            "\n\nThis is one of several angles being generated from the same material. "
            f"Do NOT repeat these already-proposed theses — find a genuinely different real "
            f"angle in the footage instead:\n{titles}"
        )

    planning_context = _format_planning_context(stated_intent, max_duration_sec)

    client = build_anthropic_client(api_key=api_key)
    user_prompt = ARCHITECT_PROMPT_TEMPLATE.format(
        audience_goal=audience_goal.strip(),
        fragments=_format_candidates_for_llm(candidates),
        trend_research=_format_research_for_llm(research),
        planning_context=planning_context,
    ) + avoid_clause
    try:
        response = client.messages.create(
            model=model,
            # Raised from the original 4096, 2026-09-04: the output schema
            # has grown substantially (tight-cut sequence + full pool_indices
            # list + narrative_thesis + 4-part editorial_qna) since that
            # value was first set, with no corresponding increase — pure
            # safety margin against truncation on larger fragment pools.
            max_tokens=8192,
            temperature=0.4,
            system=ARCHITECT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        raise StoryPlannerError(f"Anthropic API error: {e}") from e

    if response.stop_reason == "max_tokens":
        # A truncated response can still parse as technically-valid JSON if
        # the cutoff happens to land somewhere _extract_json's lenient
        # repair can patch — producing a real but silently degenerate
        # result (e.g. a 1-range sequence, empty pool) instead of a clear
        # error. Fail loud instead; this is what the raised max_tokens
        # above is meant to prevent, but never trust that alone.
        raise StoryPlannerError(
            "Claude's response was truncated (stop_reason=max_tokens) before "
            "finishing — the sequence/pool this would have produced can't be "
            "trusted. Retry, or reduce candidate fragment count."
        )

    text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    text = "".join(text_parts).strip()
    if not text:
        raise StoryPlannerError("Empty text response from Claude (tool-use blocks only).")
    data = _extract_json(text)

    ranges: List[TopicRange] = []
    seen_indices = set()
    for entry in data.get("sequence", []):
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)):
            continue
        if idx in seen_indices:
            # Real bug, confirmed 2026-09-04 on a real export Ryan caught
            # via Premiere's Duplicate Frame markers: the model picked the
            # SAME fragment index twice (once as "hook", once as "payoff"),
            # producing the identical clip placed on the timeline twice —
            # and, downstream, the same audio-sync match placed twice too.
            # Never trust the prompt's "don't repeat" instruction alone for
            # a hard constraint; enforce it here.
            continue
        seen_indices.add(idx)
        tf = candidates[idx]
        f = tf.fragment
        role = str(entry.get("role", ""))
        # Add the combined-timeline offset for this fragment's real source
        # file, if we have one — see build_source_offset_lookup's
        # docstring for the real bug this fixes. Fragments are extracted
        # per-file in local time; assemble_cut_from_angle resolves the
        # source file purely from where start/end fall in the COMBINED
        # timeline, so local time alone would silently pick the wrong file.
        offset = (source_offset_lookup or {}).get(f.source_file, 0.0)
        ranges.append(TopicRange(
            source_file=f.source_file,
            source_start_sec=f.source_start_sec + offset,
            source_end_sec=f.source_end_sec + offset,
            topic_label=role or f.topic_label,
            summary=f.summary,
        ))

    if not ranges:
        raise StoryPlannerError(
            "Claude's response selected no valid fragment indices — "
            f"raw sequence field: {data.get('sequence')!r}"
        )
    if len(ranges) < 2:
        # A real hook/build/payoff arc can't legitimately be a single
        # clip — this is more likely a sign of a degraded/truncated
        # response than an intentional creative choice. Fail loud rather
        # than persist an idea that isn't a real arc.
        raise StoryPlannerError(
            f"Claude's response produced only {len(ranges)} range(s) for the "
            "tight cut — too few to be a real arc. Likely a degraded response; retry."
        )

    # 2026-09-04: the length the caller asked for is a REAL constraint, not
    # a suggestion the model reports back and nobody checks. Ryan caught a
    # cut that cited Instagram Reel formats in its own brief and ran 12:44
    # — because `target_duration_sec` was documented in the prompt as "not
    # enforced" and nothing downstream measured anything. Measured from the
    # real selected ranges, not from the model's self-reported number,
    # which is exactly the claim under suspicion.
    if max_duration_sec and max_duration_sec > 0:
        actual_sec = sum(r.source_end_sec - r.source_start_sec for r in ranges)
        if actual_sec > max_duration_sec * DURATION_OVERRUN_TOLERANCE:
            raise StoryPlannerError(
                f"Tight cut runs {actual_sec:.0f}s but the agreed target is "
                f"{max_duration_sec:.0f}s — it selected too much material for the "
                f"format it was asked to build. Retry with fewer/shorter fragments."
            )

    # 2026-09-04: the "pool" — everything else genuinely relevant to the
    # same topic, deliberately left OUT of the tight sequence (see module
    # docstring / Ryan's real editing workflow). Same dedup + offset
    # handling as the main sequence; never overlaps it (seen_indices is
    # shared).
    pool_ranges: List[TopicRange] = []
    for raw_idx in data.get("pool_indices", []):
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)) or idx in seen_indices:
            continue
        seen_indices.add(idx)
        tf = candidates[idx]
        f = tf.fragment
        offset = (source_offset_lookup or {}).get(f.source_file, 0.0)
        pool_ranges.append(TopicRange(
            source_file=f.source_file,
            source_start_sec=f.source_start_sec + offset,
            source_end_sec=f.source_end_sec + offset,
            topic_label=f.topic_label,
            summary=f.summary,
        ))

    thesis = str(data.get("narrative_thesis", "")).strip()
    qna = data.get("editorial_qna", {}) or {}
    bigger_story = str(qna.get("bigger_story", "")).strip()
    why_watch = str(qna.get("why_watch", "")).strip()
    viewer_relevance = str(qna.get("viewer_relevance", "")).strip()
    qna_cta = str(qna.get("cta", "")).strip()

    # Ryan (2026-09-04): every story suggestion has to be able to answer
    # these specific questions concretely — never blank, never generic
    # filler. Enforced here, not just requested in the prompt: a missing
    # answer is a real defect in the output, not a cosmetic gap.
    missing_qna = [k for k, v in {
        "bigger_story": bigger_story, "why_watch": why_watch,
        "viewer_relevance": viewer_relevance, "cta": qna_cta,
    }.items() if not v]
    if missing_qna:
        raise StoryPlannerError(
            f"Claude's response left editorial_qna field(s) blank: {missing_qna} — "
            "every story suggestion must answer these concretely."
        )

    call_to_action = str(data.get("call_to_action", "")).strip() or qna_cta

    # PreCut's CreativeBrief has no fields for narrative_thesis or the
    # editorial Q&A (see the matching `research[...]` copies below for the
    # full audit trail) — folded into why_it_works, clearly labeled, so
    # every answer is visible on the card itself without an extra click.
    # These are arguably the most important editorial judgments in the
    # whole output; they shouldn't be buried in an expandable panel.
    why_it_works = (
        f"Thesis: {thesis}\n\n"
        f"Bigger story: {bigger_story}\n\n"
        f"Why watch: {why_watch}\n\n"
        f"How it relates to the viewer: {viewer_relevance}\n\n"
        f"{str(data.get('why_it_works', '')).strip()}"
        f"\n\n{_format_citations_plaintext(research)}"
    )

    brief = CreativeBrief(
        title=str(data.get("title", ""))[:200],
        hook=str(data.get("hook", ""))[:500],
        # Ryan, 2026-09-04: "The brief isn't in the Premiere project which
        # is what I'd asked for. I dont want to have to search deep into
        # the finder... to find an arbitrary .md file." An editor who only
        # ever opens the Premiere project (not Post House itself) needs
        # the full brief — including sourced links — reachable from
        # there. The frame-0 marker this becomes (see exporter's
        # _build_creative_brief_marker) is the one place guaranteed to be
        # inside the actual .prproj/XML, so the FULL brief goes here, not
        # a truncated summary — raised well past the earlier 3000-char cap.
        why_it_works=why_it_works[:12000],
        tone=str(data.get("tone", ""))[:200],
        target_duration_sec=float(data.get("target_duration_sec", 0.0) or 0.0),
        target_audience=str(data.get("target_audience", ""))[:300],
        call_to_action=call_to_action[:300],
    )

    angle = StoryAngle(
        angle_id=f"angle_{uuid.uuid4().hex[:10]}",
        brief=brief,
        source_ranges=ranges,
        pool_ranges=pool_ranges,
    )
    research["omitted_reasoning"] = data.get("omitted_reasoning", "")
    # PreCut's CreativeBrief schema has no field for this (same situation as
    # trend findings — see module docstring) — persisted in the research
    # audit trail so it's never silently dropped. This is arguably the
    # single most important piece of editorial judgment in the whole
    # output: the specific thesis the arc is actually built to serve.
    research["narrative_thesis"] = data.get("narrative_thesis", "")
    research["editorial_qna"] = {
        "bigger_story": bigger_story, "why_watch": why_watch,
        "viewer_relevance": viewer_relevance, "cta": qna_cta,
    }
    return angle, research


POOL_GAP_SEC = 45.0  # real blank timeline space between the tight cut and the selects pool


def assemble_two_zone_cutlist(angle: "StoryAngle", transcript, db=None, **assemble_kwargs):
    """Real editing workflow, per Ryan directly (2026-09-04): "I build my
    storyline on the left side of the timeline and then I pull in all of
    the extra sound bites, b-roll and other things that I might use on the
    right side... with a little bit of space between those two chunks."

    Builds the tight cut (`angle.source_ranges`) and the selects pool
    (`angle.pool_ranges`) as two SEPARATE calls to PreCut's own, unmodified
    `assemble_cut_from_angle` — reusing its real file resolution, native
    dims, and B-roll marker generation for both halves rather than
    reimplementing any of it — then merges them onto one CutList: the
    pool's phrases/markers are timeline-shifted to start after the tight
    cut plus a real gap, with phrase ids remapped to a disjoint range so
    attached B-roll/flag markers still resolve correctly after the merge.

    `assemble_kwargs` should be exactly what the caller would otherwise
    pass straight to `assemble_cut_from_angle` (preset_key,
    source_offset_map, source_to_original, aspect_key, platform_key) —
    both halves are built with the same real project state.

    Returns a plain `assemble_cut_from_angle`-equivalent CutList when
    `angle.pool_ranges` is empty (nothing to merge) — safe to call
    unconditionally on any angle, including PreCut's own generate_angles
    output, which never sets pool_ranges."""
    assemble_cut_from_angle = _story_assembler.assemble_cut_from_angle

    left = assemble_cut_from_angle(angle=angle, transcript=transcript, db=db, **assemble_kwargs)
    if not angle.pool_ranges:
        return left

    pool_angle = StoryAngle(
        angle_id=angle.angle_id + "_pool",
        brief=CreativeBrief(title="", hook="", why_it_works="", tone="", target_duration_sec=0.0),
        source_ranges=angle.pool_ranges,
        selected_platform_key=angle.selected_platform_key,
        selected_aspect_key=angle.selected_aspect_key,
    )
    right = assemble_cut_from_angle(angle=pool_angle, transcript=transcript, db=db, **assemble_kwargs)
    if not right.aroll_track:
        return left

    shift = left.total_duration + POOL_GAP_SEC
    # Disjoint from both halves' own internal id counters (each starts
    # fresh at 1_000_000 inside assemble_cut_from_angle) so attached
    # markers can be remapped without colliding with the left side's ids.
    #
    # Real bug, found and fixed 2026-09-04 (Ryan: "theres no v1 folder
    # where the ideas are supposed to live"): this was 5_000_000, which
    # collides with a DIFFERENT, pre-existing PreCut convention in
    # multi_exporter.py's export_multi_timeline — any phrase with
    # phrase_id >= 2_000_000 is treated as the "All Synced A-Roll"
    # reference sequence (minted that way on purpose by
    # _build_all_aroll_sequences) and placed directly in Seq/, not
    # nested in Seq/v1/ where real story-angle sequences belong. Any
    # angle with a non-empty pool got its pool phrases remapped past
    # that threshold, misclassifying the WHOLE sequence and bumping it
    # out of Seq/v1/.
    #
    # First fix attempt (2026-09-04) dropped this to 1_500_000, but
    # that was still wrong: `right.aroll_track` ids ALREADY start at
    # 1_000_000 (assemble_cut_from_angle's own internal counter, same
    # base the left half uses), so the final id is 1_000_000 +
    # id_offset, not id_offset alone. 1_000_000 + 1_500_000 =
    # 2_500_000 — still over the 2_000_000 line. Verified via a real
    # export + XML parent-map check (sequence still landed directly
    # under Seq/, not Seq/v1/) before this second fix. The offset must
    # satisfy id_offset < 1_000_000 (right's final id then lands below
    # 2_000_000) while staying bigger than the left half's own range
    # count (a handful of ids from 1_000_000 up) so the two halves stay
    # disjoint. 500_000 gives large headroom on both sides.
    id_offset = 500_000
    remap = {}
    for p in right.aroll_track:
        remap[p.phrase_id] = p.phrase_id + id_offset
        p.phrase_id += id_offset
        p.timeline_start += shift
        p.timeline_end += shift

    for m in right.broll_markers:
        m.timeline_time += shift
        if m.phrase_id in remap:
            m.phrase_id = remap[m.phrase_id]
        if m.attach_to_phrase_id in remap:
            m.attach_to_phrase_id = remap[m.attach_to_phrase_id]

    for m in right.flag_markers:
        m.timeline_start += shift
        m.timeline_end += shift
        if m.attach_to_phrase_id in remap:
            m.attach_to_phrase_id = remap[m.attach_to_phrase_id]

    left.aroll_track.extend(right.aroll_track)
    left.broll_markers.extend(right.broll_markers)
    left.flag_markers.extend(right.flag_markers)
    left.total_duration = shift + right.total_duration
    return left


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


def load_project_material(project, emit) -> tuple:
    """Load a project's audience goal and every real transcript fragment
    available — flagged (audience-scored) or freshly extracted.

    Returns `(audience_goal, tagged_by_source)`, or `(None, None)` after
    emitting a `producer_error` explaining exactly what's missing.

    Extracted from `run_generate_story_angle` (2026-09-04) so the
    planning conversation (`posthouse/story_conversation.py`) can see the
    same real material the generator will use, without a second copy of
    this logic drifting out of sync with it."""
    from posthouse.manifest import load_manifest

    project_dir = project.dir()
    manifest_path = project_dir / "manifest.json"
    if not manifest_path.exists():
        emit({"type": "producer_error",
              "message": "No manifest.json for this project yet — run Organize first."})
        return None, None

    try:
        manifest = load_manifest(manifest_path)
    except Exception as e:
        emit({"type": "producer_error", "message": f"Failed to load manifest: {e}"})
        return None, None

    audience_goal = (manifest.get("project") or {}).get("audience_goal")
    if not audience_goal:
        emit({"type": "producer_error",
              "message": "No audience/content goal set for this project "
                         "(Project Manager intake) — nothing to build a story arc against."})
        return None, None

    # Confirmed real, 2026-09-04: on the RDOSS external drive, every real
    # file gets a macOS AppleDouble sidecar (`._<name>.json`) that
    # `Path.glob("*.json")` — unlike shell glob — DOES match, since it
    # doesn't apply Unix's "no leading dot" convention. json.loads() on
    # one of these binary sidecars raises UnicodeDecodeError. Filter them
    # out explicitly rather than relying on glob semantics.
    flags_dir = project_dir / "flags"
    flags_files = sorted(
        p for p in (flags_dir.glob("*.json") if flags_dir.exists() else [])
        if not p.name.startswith(".")
    )
    transcript_files = sorted(
        p for p in project.transcripts_dir().glob("*.json") if not p.name.startswith(".")
    )
    if not flags_files and not transcript_files:
        emit({"type": "producer_error",
              "message": "No transcripts yet — run the pipeline first so there's "
                         "real material to build from."})
        return None, None

    # Ryan, 2026-09-04: "I don't want the ideas created to only be generated
    # from flagged fragments. I'm not that confident in the flagging yet."
    # So flagging is no longer a hard prerequisite: any transcript WITHOUT
    # a matching flags file gets its own fresh, real exhaustive extraction
    # right here (transcript_coverage — the same real mechanism, just not
    # gated behind the separate flagging pipeline stage having already
    # run). Those fragments are marked fit="possible"/category="" — real
    # and eligible, but explicitly not claiming a relevance judgment that
    # was never actually made for them.
    tagged_by_source: Dict[str, List[TaggedFragment]] = {}
    flagged_stems = set()
    for fp in flags_files:
        try:
            tagged_by_source[fp.stem] = load_tagged_fragments(fp)
            flagged_stems.add(fp.stem)
        except Exception:
            continue

    from posthouse.transcript_coverage import extract_exhaustive_fragments
    from precut_pipeline.transcriber import Transcript

    unflagged = [tp for tp in transcript_files if tp.stem not in flagged_stems]
    for i, tp in enumerate(unflagged):
        emit({"type": "log", "level": "info",
              "message": f"No flagging yet for {tp.stem} — extracting fresh, real "
                         f"material directly from its transcript ({i + 1}/{len(unflagged)})..."})
        try:
            transcript = Transcript.load(tp)
            fragments, coverage = extract_exhaustive_fragments(transcript)
        except Exception as e:
            emit({"type": "log", "level": "warn",
                  "message": f"Raw extraction failed for {tp.stem}: {e}"})
            continue
        tagged_by_source[tp.stem] = [
            TaggedFragment(fragment=f, fit="possible", reasoning=(
                "Not yet scored by transcript flagging — raw exhaustive "
                "extraction only, included on its own merits."
            ), category="")
            for f in fragments
        ]

    if not tagged_by_source:
        emit({"type": "producer_error",
              "message": "No fragments available at all (flagged or freshly extracted) — "
                         "nothing real to build a story arc from."})
        return None, None

    return audience_goal, tagged_by_source


def run_generate_story_angle(
    project, job_id: str, emit,
    stated_intent: str = "",
    max_duration_sec: float = 0.0,
    research: Optional[dict] = None,
) -> None:
    """Backend-job wrapper: load a project's real audience goal and every
    real transcript fragment available — flagged (audience-scored) or
    not — generate 3 real, distinct story angles with live trend research
    (shared across all 3; see `research_trends`'s 72h cache), and persist
    each as its own idea (PreCut's format) + brief + research audit
    trail. Emits progress the same shape as `producer.run_generate_angles`
    so the existing job-tracking UI works unchanged.

    Ryan, 2026-09-04: "I don't want the ideas created to only be
    generated from flagged fragments. I'm not that confident in the
    flagging yet" — flagging is no longer a hard prerequisite. Any
    transcript without a matching flags file gets fresh, real exhaustive
    extraction right here instead of being excluded. "It should also
    provide 3 ideas each time the generate ideas button is pressed" —
    generates 3 distinct angles per call, each told not to repeat the
    theses already proposed earlier in the same batch.

    `stated_intent` / `max_duration_sec`, when given, come from the
    planning conversation (`posthouse/story_conversation.py`) and are
    passed straight through: the intent redirects the trend research and
    governs fragment selection, and the duration is a real enforced cap
    (see `generate_story_angle`).

    A no-op (not an error) when there's no manifest/audience_goal yet, or
    no transcripts at all yet (nothing real to build from either way)."""
    project_dir = project.dir()

    def emit_with_job(ev):
        ev.setdefault("job_id", job_id)
        emit(ev)

    audience_goal, tagged_by_source = load_project_material(project, emit_with_job)
    if not audience_goal or not tagged_by_source:
        return

    source_offset_lookup = build_source_offset_lookup(project)

    emit({"type": "producer_started", "job_id": job_id, "mode": "story_architect"})

    N_ANGLES = 3  # Ryan, 2026-09-04: "It should also provide 3 ideas each time"
    try:
        # Real cost bug, 2026-09-04 (Ryan: "it is BURNING through my api
        # credits... It's used $3 already and hasnt spit out one idea").
        # A planning conversation ALREADY pays for a full research pass
        # and stores it on the session. This function then threw that
        # away and bought it again — and because the cache is keyed on
        # the stated intent, and the conversation's resolved intent is a
        # different (evolved) string from the one research was cached
        # under, it missed cache every time and re-ran the expensive
        # part: downloading and watching real videos with vision frames
        # and fetching YouTube transcripts. Accept the caller's
        # already-paid-for research instead.
        if research is None:
            emit({"type": "log", "level": "info", "message": "Researching live trends (real web search + real video watching)..."})
            research = research_trends(audience_goal, stated_intent=stated_intent)
        else:
            emit({"type": "log", "level": "info",
                  "message": "Reusing the research from your planning conversation — "
                             "no new searches, videos, or transcript reads."})
        if research.get("cached"):
            emit({"type": "log", "level": "info",
                  "message": "Reused research from the last 72 hours for this exact audience/"
                             "goal — no new search/video calls made this run."})
        emit({"type": "log", "level": "info",
              "message": f"Trend research: {len(research['text_findings'])} text findings, "
                         f"{sum(1 for v in research['video_findings'] if v.get('relevant'))} "
                         f"real video(s) watched and relevant."})

        avoid_theses: List[str] = []
        succeeded = 0
        for i in range(N_ANGLES):
            emit({"type": "log", "level": "info",
                  "message": f"Building story arc {i + 1}/{N_ANGLES}..."})
            # One retry per slot, not the whole batch: a degraded/truncated
            # response on angle 2 shouldn't discard angle 1, which already
            # succeeded and is sitting on disk (Ryan hit a real version of
            # this — a bad response needs to fail that ONE slot loudly, not
            # take the whole click down with it).
            angle = angle_research = None
            last_error = None
            for attempt in range(2):
                try:
                    angle, angle_research = generate_story_angle(
                        audience_goal, tagged_by_source, research=research,
                        source_offset_lookup=source_offset_lookup, avoid_theses=avoid_theses,
                        stated_intent=stated_intent, max_duration_sec=max_duration_sec,
                    )
                    break
                except Exception as e:
                    last_error = e
                    emit({"type": "log", "level": "warn",
                          "message": f"Arc {i + 1}/{N_ANGLES} attempt {attempt + 1} failed: {e}"})
            if angle is None:
                emit({"type": "log", "level": "warn",
                      "message": f"Arc {i + 1}/{N_ANGLES} failed twice, skipping it: {last_error}"})
                continue

            avoid_theses.append(angle_research.get("narrative_thesis", angle.brief.title))

            idea_path = save_story_angle_as_idea(project.plans_dir(), angle)
            research_path = save_story_research(project_dir, angle, angle_research)
            brief_path = save_story_brief(project_dir, angle, angle_research, source_offset_lookup)
            succeeded += 1

            emit({
                "type": "producer_angle",
                "job_id": job_id,
                "idea_id": idea_path.stem,
                "angle": _angle_to_dict(angle),
                "research_path": str(research_path),
                "brief_path": str(brief_path),
            })

        if succeeded == 0:
            emit({"type": "producer_error", "job_id": job_id,
                  "message": "All 3 attempts to build a story arc failed — see the log above for why."})
            return
    except Exception as e:
        emit({"type": "producer_error", "job_id": job_id, "message": str(e)})
        return
    emit({"type": "producer_done", "job_id": job_id, "mode": "story_architect", "angle_count": succeeded})


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


def save_story_brief(project_dir: Path, angle: "StoryAngle", research: dict,
                      source_offset_lookup: Optional[Dict[str, float]] = None) -> Path:
    """A real, human-readable brief for the EDITOR — Ryan, 2026-09-04: "we
    need to also kick out a Brief of some sort that sits alongside or on
    the timeline so we as the editors can see what the pitch is
    specifically and the intention of what we're supposed to be editing.
    This should also include the links to the examples that it is basing
    its findings off of."

    A real file on disk (`<project_dir>/briefs/<angle_id>.md`), not just
    the frame-0 sequence marker PreCut already emits from `why_it_works` —
    a Premiere marker comment isn't the place for a dozen citation links.
    The marker still carries the core pitch (it reads `why_it_works`,
    which already has the thesis/Q&A folded in); this file is the full
    version, with every real source and example link this angle is
    actually based on, so an editor can open it standalone."""
    out_dir = Path(project_dir) / "briefs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{angle.angle_id}.md"

    b = angle.brief
    lines = [
        f"# {b.title or 'Untitled angle'}",
        "",
        f"**Hook:** {b.hook}",
        "",
        f"**Tone:** {b.tone}",
        "",
        f"**Target audience:** {b.target_audience}",
        "",
        f"**Call to action:** {b.call_to_action}",
        "",
        "## Why it works (thesis, editorial Q&A, reasoning)",
        "",
        b.why_it_works,
        "",
        "## Sequence",
        "",
    ]
    for i, r in enumerate(angle.source_ranges, 1):
        # r.source_start_sec/end are combined-timeline coordinates (needed
        # for correct export — see build_source_offset_lookup) — subtract
        # the same offset back out here so an editor reads the actual
        # in-video timecode for the named file, not a confusing large
        # number with no relation to that file's own length.
        offset = (source_offset_lookup or {}).get(r.source_file, 0.0)
        lines.append(
            f"{i}. **[{r.topic_label}]** `{Path(r.source_file).name}` "
            f"{r.source_start_sec - offset:.1f}s–{r.source_end_sec - offset:.1f}s — {r.summary}"
        )
    lines += ["", "---", "", "## Research this arc is based on", ""]

    if research.get("named_trends"):
        lines.append("### Specific named trends found")
        for t in research["named_trends"]:
            listen = f" — [listen/download]({t['listen_url']})" if t.get("listen_url") else ""
            lines.append(f"- **{t.get('name','')}** — {t.get('description','')} "
                         f"([source]({t.get('source','')})){listen}")
        lines.append("")

    relevant_videos = [v for v in research.get("video_findings", []) if v.get("relevant")]
    if relevant_videos:
        lines.append("### Real videos actually watched — what we're suggesting to build similarly to")
        for v in relevant_videos:
            audio = ""
            if v.get("audio_track") or v.get("audio_artist"):
                link = f" — [listen/download]({v['audio_listen_url']})" if v.get("audio_listen_url") else ""
                audio = f"\n  Audio: {v.get('audio_track') or '?'} — {v.get('audio_artist') or 'unknown'}{link}"
            pacing = ""
            if v.get("detected_cuts") is not None:
                pacing = f"\n  Pacing: {v['detected_cuts']} cuts over {v.get('duration_sec')}s ({v.get('cuts_per_sec')} cuts/sec)"
            lines.append(f"- [{v['url']}]({v['url']})\n  {v.get('observed','')}{pacing}{audio}")
        lines.append("")

    if research.get("marketing_findings"):
        lines.append("### Audience-targeting / social strategy findings")
        for f in research["marketing_findings"]:
            lines.append(f"- {f.get('finding','')} ([source]({f.get('source','')}))")
        lines.append("")

    relevant_strategy = [f for f in research.get("strategy_video_findings", []) if f.get("relevant")]
    if relevant_strategy:
        lines.append("### From real strategy videos (real transcripts)")
        for f in relevant_strategy:
            lines.append(f"- [{f['url']}]({f['url']})")
            for p in f.get("points", []):
                lines.append(f"  - {p}")
        lines.append("")

    if research.get("omitted_reasoning"):
        lines += ["### Real material left out of this arc, and why", "", research["omitted_reasoning"], ""]

    lines += [
        "---",
        "",
        f"*Full sourced audit trail (including what was checked and excluded as irrelevant, "
        f"and anything unverified): `{Path(project_dir) / 'story_research' / (angle.angle_id + '.json')}`*",
    ]

    out_path.write_text("\n".join(lines))
    return out_path
