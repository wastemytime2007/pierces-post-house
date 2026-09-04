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

TREND_NAMES_SEARCH_PROMPT = """Search the web for SPECIFIC NAMED trends currently circulating on \
TikTok/Instagram Reels in the niche implied by this audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

I need actual NAMES — a specific trending sound/song title, a named challenge/format (e.g. "the \
'wait I can do that better' sound", "the [name] transition"), or a named creator's signature \
format — not a generic statement like "short-form video is popular" or "before/after content \
does well."

After searching, return ONLY this JSON in a fenced ```json block — no preamble:
{{"trends": [{{"name": "the specific trend/sound/challenge name", "description": "...", "source": "https://..."}}]}}

If you genuinely cannot find a specific named trend for this niche, return an EMPTY list — \
{{"trends": []}} — do NOT invent a placeholder entry explaining that none was found; an empty \
list already says that."""

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
        return {
            "url": url,
            "relevant": bool(parsed.get("relevant", False)),
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
    result: dict = {"named_trends": [], "text_findings": [], "video_findings": [], "unverified": []}

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
