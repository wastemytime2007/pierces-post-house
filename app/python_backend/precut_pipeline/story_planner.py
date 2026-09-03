"""Drop 4.0: Story Angle Planner.

Replaces the heavy-lifting Deliverable planner from earlier drops. Instead
of having the AI try to assemble a full cut (pick phrases, decide order,
set pacing, insert B-roll), this planner does one job well:

  Read the transcript. Spot the 3 strongest story angles that could land
  with an audience. For each angle, identify which phrases belong to it.

The matcher then strings those phrases out on V1 in SOURCE ORDER with
their natural in/out points. No re-arranging. No pacing heuristics. The
editor does the editing. The AI does the comprehension.

Design notes:
  - "Curated-lax" phrase selection: include connective tissue, not just
    phrases that literally match the topic keywords. If the interviewee
    talks about box trucks, then economics, then ties it back to box
    trucks — include the economics section even though it doesn't say
    "box truck." This matches how real interview storytelling works.
  - 3 distinct angles per transcript. A "Request More" code path exists
    for generating another 3 that don't duplicate the first batch.
  - Returns the same phrase ID list back unmodified in source order —
    the matcher will re-sort to confirm, but we trust the planner.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Optional

import anthropic

from .anthropic_client import build_anthropic_client
from .config import ANTHROPIC_MODEL, ANTHROPIC_MAX_TOKENS
from .transcriber import Transcript
from .cutlist import StoryAngle, CreativeBrief, TopicRange
from .presets import PRESETS_BY_KEY


class StoryPlannerError(Exception):
    """Raised when the story planner fails (API error, bad LLM output, etc.)."""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# The planner's job is to find STORIES, not compile facts. The prompt
# orients Claude toward viral/engagement potential rather than completeness.
SYSTEM_PROMPT = """You are a senior video editor and content strategist. You've studied what makes short-form content go viral: strong hooks, clear stakes, satisfying payoffs, surprising framings, counterintuitive insights.

When given an interview transcript, your job is to spot STORY ANGLES — not summarize, not list topics. A story angle is a specific, shareable framing of the material that would make someone stop scrolling and watch.

Your output is CONTINUOUS TIME RANGES in the source interview — not individual phrases. Think of yourself as a researcher marking up the interview for an editor: "the box truck discussion lives from 4:20 to 7:15" rather than "phrases 12, 14, 17." Ranges should be generous with SOFT topic boundaries: if a topic drifts into a related subtopic (costs, logistics, a relevant aside) and loops back, include the whole arc as one range. The editor will trim later; don't fragment their material for them.

An angle can be built from 1-3 separate ranges if a natural "hook up front, payoff later" structure exists in the interview. Otherwise prefer a single range.

Return ONLY valid JSON. No preamble, no markdown fences."""


ANGLE_PROMPT = """Here is an interview transcript. Each phrase has an ID, a start time in seconds, and an end time in seconds.

<transcript>
{transcript}
</transcript>

Total duration: {total_duration:.1f} seconds
Phrase count: {phrase_count}
Source file: {source_file}

{existing_angles_note}

Your task: propose {n_angles} DISTINCT story angles from this transcript. Each angle should be a specific, shareable framing that has strong viral or engagement potential. Avoid generic summaries. Lean into what's counterintuitive, emotionally resonant, or specifically-useful.

For each angle, identify the CONTINUOUS TIME RANGES in the source interview where the angle's material lives. Rules:

1. A range is defined by source_start_sec and source_end_sec (seconds into the source file). Use values from the phrase start/end times in the transcript.
2. Prefer ONE range per angle when the discussion is contiguous. Use 2-3 ranges only when there's a natural hook-then-payoff structure separated by unrelated material.
3. Use SOFT topic boundaries. If she's talking about box trucks and drifts into staging costs that TIE BACK to box trucks, include the whole arc — that's all one range. Only break the range when she clearly pivots to a completely unrelated topic.
4. Each range must cover AT LEAST 4 seconds (tiny ranges are useless to editors). Typical range is 15-90 seconds.
5. Ranges must not overlap within a single angle.
6. Be generous — when in doubt about whether content belongs, include it. The editor trims.

Return JSON in this exact shape:

{{
  "angles": [
    {{
      "title": "Short, punchy title (3-6 words)",
      "hook": "1-2 sentence opening hook the editor could literally use as text overlay or as a script beat",
      "why_it_works": "Why this angle will engage viewers. 2-3 sentences. Be specific about what's shareable or surprising.",
      "tone": "Short tone description, e.g. 'punchy and authoritative', 'warm storytelling', 'counterintuitive explainer'",
      "target_audience": "Who's this for? e.g. 'small business owners considering staging', 'first-time home sellers'",
      "call_to_action": "Suggested CTA if relevant, e.g. 'Follow for more staging tips', or empty string if none",
      "estimated_duration_sec": 45.0,
      "source_ranges": [
        {{
          "source_start_sec": 120.4,
          "source_end_sec": 187.8,
          "topic_label": "hook",
          "summary": "Opens with the counterintuitive claim about box trucks."
        }},
        {{
          "source_start_sec": 412.1,
          "source_end_sec": 468.5,
          "topic_label": "payoff",
          "summary": "Ties the $4-5K staging cost back to the truck economics."
        }}
      ]
    }},
    ...
  ]
}}"""


EXISTING_ANGLES_TEMPLATE = """You have already proposed these angles previously. Do NOT duplicate them — come up with {n_angles} NEW angles that explore different framings of the transcript:

{existing_list}
"""


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class StoryAnglePlanner:
    """Calls Claude to identify story angles in a transcript."""

    def __init__(self, api_key: Optional[str] = None, model: str = ANTHROPIC_MODEL):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise StoryPlannerError(
                "No Anthropic API key. Set ANTHROPIC_API_KEY env var or pass api_key."
            )
        self.client = build_anthropic_client(api_key=key)
        self.model = model

    def generate_angles(
        self,
        transcript: Transcript,
        n_angles: int = 3,
        existing_angles: Optional[list[StoryAngle]] = None,
        available_preset_keys: Optional[list[str]] = None,
    ) -> list[StoryAngle]:
        """Generate N story angles from a transcript.

        Args:
            transcript: the A-roll transcript to analyze
            n_angles: how many distinct angles to produce (default 3)
            existing_angles: if provided, the planner is asked to produce
                NEW angles that don't duplicate these. Used for the UI's
                "Request More" button.
            available_preset_keys: limit the preset suggestions to this set.
                Default: all presets.
        """
        # Drop 4.3: for story angles the dropdown is aspect-only and runtime
        # lives in the Creative Brief. Default the Claude preset menu to just
        # the three aspect_ keys so the model picks an aspect ratio, not a
        # duration-coupled legacy preset the dropdown can't even display.
        # Drop 4.8: the planner no longer suggests a preset. The editor
        # picks format via the UI dropdowns. available_preset_keys is kept
        # in the signature for API compatibility but ignored.

        if existing_angles:
            # Compact existing angles into a bullet list the model can see
            existing_list = "\n".join(
                f"  - {a.brief.title}: {a.brief.hook}"
                for a in existing_angles
            )
            existing_note = EXISTING_ANGLES_TEMPLATE.format(
                n_angles=n_angles,
                existing_list=existing_list,
            )
        else:
            existing_note = ""

        user_prompt = ANGLE_PROMPT.format(
            transcript=transcript.format_for_llm(),
            total_duration=transcript.duration,
            phrase_count=len(transcript.phrases),
            source_file=transcript.source_path,
            existing_angles_note=existing_note,
            n_angles=n_angles,
        )

        response_text = self._call_claude(SYSTEM_PROMPT, user_prompt)
        data = _extract_json(response_text)

        angles: list[StoryAngle] = []
        phrases_by_id = {p.id: p for p in transcript.phrases}
        source_file = transcript.source_path
        transcript_duration = transcript.duration

        # Minimum usable range length — anything smaller is noise, likely a
        # hallucinated or truncated time value.
        MIN_RANGE_SEC = 4.0

        for entry in data.get("angles", []):
            raw_ranges = entry.get("source_ranges", [])
            # Fallback: if the model regressed to emitting phrase_ids (happens
            # occasionally on models that over-weight examples from earlier
            # prompts), we derive ranges from those phrase IDs by grouping
            # consecutive phrases and taking their time spans.
            if not raw_ranges and entry.get("phrase_ids"):
                raw_ranges = _derive_ranges_from_phrase_ids(
                    entry.get("phrase_ids", []), phrases_by_id, source_file,
                )

            # Parse + sanitize each range
            ranges: list[TopicRange] = []
            for rr in raw_ranges:
                try:
                    start = float(rr.get("source_start_sec", -1))
                    end = float(rr.get("source_end_sec", -1))
                except (TypeError, ValueError):
                    continue
                if start < 0 or end <= start:
                    continue
                # Clamp to transcript duration so we never ask for video past
                # the source file's end.
                start = max(0.0, start)
                end = min(transcript_duration, end)
                if end - start < MIN_RANGE_SEC:
                    continue

                # Resolve the source file: planner may echo the source_file
                # from the prompt, or leave it empty. Default to transcript's.
                rr_source = (rr.get("source_file") or source_file).strip() \
                    if isinstance(rr.get("source_file"), str) else source_file

                ranges.append(TopicRange(
                    source_file=rr_source,
                    source_start_sec=start,
                    source_end_sec=end,
                    topic_label=str(rr.get("topic_label", ""))[:40],
                    summary=str(rr.get("summary", ""))[:400],
                ))

            if not ranges:
                # Angle with no valid ranges is useless; skip it
                continue

            # Sort ranges by start time within a single source file, then
            # merge overlaps (the planner sometimes emits overlapping ranges
            # for the "hook + payoff" pattern).
            ranges = _merge_overlapping_ranges(ranges)

            # Derive phrase_ids and previews from the ranges, for UI compat
            # and for any downstream code that still expects phrase_ids.
            derived_phrase_ids: list[int] = []
            previews: list[str] = []
            for r in ranges:
                phrases_in_range = [
                    p for p in transcript.phrases
                    if p.end > r.source_start_sec and p.start < r.source_end_sec
                ]
                phrases_in_range.sort(key=lambda p: p.start)
                derived_phrase_ids.extend(p.id for p in phrases_in_range)
                # Preview line: prefer the planner-supplied summary; fall back
                # to joining a few phrase fragments.
                if r.summary:
                    label = f"[{r.topic_label}] " if r.topic_label else ""
                    previews.append(_truncate(
                        f"{label}{r.summary}  ({r.duration_sec:.0f}s)",
                        160,
                    ))
                else:
                    joined = " / ".join(p.text for p in phrases_in_range[:2])
                    previews.append(_truncate(
                        f"{r.source_start_sec:.1f}s-{r.source_end_sec:.1f}s: {joined}",
                        160,
                    ))

            brief = CreativeBrief(
                title=str(entry.get("title", "Untitled Angle"))[:80],
                hook=str(entry.get("hook", "")),
                why_it_works=str(entry.get("why_it_works", "")),
                tone=str(entry.get("tone", "")),
                target_duration_sec=float(entry.get("estimated_duration_sec", 0.0) or 0.0),
                source_phrase_ids=derived_phrase_ids,
                target_audience=str(entry.get("target_audience", "")),
                call_to_action=str(entry.get("call_to_action", "")),
            )

            # Drop 4.8: do NOT seed suggested_preset. The aspect + platform
            # dropdowns default to "None" (A-roll native / no overlay) and
            # the user picks explicitly. Seeding a preset here would pre-fill
            # the aspect dropdown with whatever Claude happened to pick,
            # which looked arbitrary to the editor.

            angles.append(StoryAngle(
                angle_id=_make_angle_id(),
                brief=brief,
                source_ranges=ranges,
                phrase_ids=derived_phrase_ids,
                suggested_preset="",  # intentionally empty
                phrase_previews=previews,
            ))

        return angles

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _call_claude(self, system: str, user: str) -> str:
        """Make the Claude API call and return the raw text response."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                temperature=0.7,  # some creativity; not deterministic
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            raise StoryPlannerError(f"Anthropic API error: {e}") from e

        parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        text = "".join(parts).strip()
        if not text:
            raise StoryPlannerError("Empty response from Claude.")
        return text


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _make_angle_id() -> str:
    """Short stable identifier for a story angle (first 8 chars of a uuid4)."""
    return f"angle-{uuid.uuid4().hex[:8]}"


def _truncate(s: str, limit: int) -> str:
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "\u2026"


def _merge_overlapping_ranges(ranges: list[TopicRange]) -> list[TopicRange]:
    """Sort ranges by start time and merge any that overlap or are adjacent
    within 1 second. Preserves topic_label/summary from the earliest range.

    Merging is conservative: only truly overlapping/adjacent ranges collapse.
    A hook at 60-90s plus a payoff at 300-340s stays as two separate ranges.
    """
    if not ranges:
        return []
    # Group by source_file; merge within each group
    by_file: dict[str, list[TopicRange]] = {}
    for r in ranges:
        by_file.setdefault(r.source_file, []).append(r)

    merged: list[TopicRange] = []
    for src, group in by_file.items():
        group.sort(key=lambda r: r.source_start_sec)
        current = group[0]
        for nxt in group[1:]:
            if nxt.source_start_sec <= current.source_end_sec + 1.0:
                # Overlap or near-adjacency → merge
                new_end = max(current.source_end_sec, nxt.source_end_sec)
                # Prefer the earlier range's label/summary; append the later
                # one's summary if it adds information.
                summary = current.summary
                if nxt.summary and nxt.summary not in summary:
                    summary = (summary + " / " + nxt.summary).strip(" /")
                current = TopicRange(
                    source_file=src,
                    source_start_sec=current.source_start_sec,
                    source_end_sec=new_end,
                    topic_label=current.topic_label or nxt.topic_label,
                    summary=summary,
                )
            else:
                merged.append(current)
                current = nxt
        merged.append(current)

    # Final sort across files: group by file then by start
    merged.sort(key=lambda r: (r.source_file, r.source_start_sec))
    return merged


def _derive_ranges_from_phrase_ids(
    phrase_ids: list,
    phrases_by_id: dict,
    source_file: str,
) -> list[dict]:
    """Fallback: if the planner returned phrase_ids instead of source_ranges
    (legacy behavior), group consecutive phrase IDs into contiguous ranges.

    Returns dicts in the same shape the normal parser consumes — this lets us
    feed them straight back through the main range parsing path without a
    special code branch.
    """
    ids: list[int] = []
    for pid in phrase_ids:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if pid_int in phrases_by_id and pid_int not in ids:
            ids.append(pid_int)
    if not ids:
        return []
    ids.sort()

    ranges: list[dict] = []
    run_start_pid = ids[0]
    run_prev_pid = ids[0]
    for pid in ids[1:]:
        if pid == run_prev_pid + 1:
            run_prev_pid = pid
            continue
        # Close the previous run
        start_ph = phrases_by_id[run_start_pid]
        end_ph = phrases_by_id[run_prev_pid]
        ranges.append({
            "source_start_sec": float(start_ph.start),
            "source_end_sec": float(end_ph.end),
            "topic_label": "",
            "summary": "",
            "source_file": source_file,
        })
        run_start_pid = pid
        run_prev_pid = pid
    # Close the final run
    start_ph = phrases_by_id[run_start_pid]
    end_ph = phrases_by_id[run_prev_pid]
    ranges.append({
        "source_start_sec": float(start_ph.start),
        "source_end_sec": float(end_ph.end),
        "topic_label": "",
        "summary": "",
        "source_file": source_file,
    })
    return ranges


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Parse JSON from Claude's response, tolerating common formatting noise.

    The model is instructed to return bare JSON, but occasionally wraps it
    in markdown fences or prefixes "Here is the JSON:" — strip those before
    parsing.
    """
    text = text.strip()

    # Try fenced code block first
    match = _JSON_FENCE_RE.search(text)
    if match:
        text = match.group(1)
    else:
        # Trim any text before the first { and after the last }
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            text = text[first_brace : last_brace + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # One retry with trailing-comma cleanup (common LLM mistake)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise StoryPlannerError(
                f"Claude returned malformed JSON: {e}. First 500 chars: {text[:500]!r}"
            ) from e
