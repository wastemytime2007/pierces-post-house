"""Stage 2.5: Deliverable Planner.

The AI producer. Given a transcript, either:
  (A) analyze_and_recommend() — pitch 3-5 deliverable concepts
  (B) plan_deliverable(brief, preset) — execute a specific brief

Uses Claude API (Anthropic) for editorial judgment. This task genuinely benefits
from frontier model quality — local LLMs are noticeably worse at nuanced selection.
"""
import os
import json
import re
from typing import Optional

import anthropic

from .config import ANTHROPIC_MODEL, ANTHROPIC_MAX_TOKENS
from .transcriber import Transcript
from .deliverable import (
    Deliverable, SegmentPlan, DeliverableConcept, AnalysisReport,
)
from .presets import DeliverablePreset, get_preset, PRESETS_BY_KEY


class PlannerError(Exception):
    """Raised when the planner fails (API error, bad LLM output, etc.)."""


class DeliverablePlanner:
    """Calls Claude to produce editorial plans from transcripts."""

    def __init__(self, api_key: Optional[str] = None, model: str = ANTHROPIC_MODEL):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise PlannerError(
                "No Anthropic API key. Set ANTHROPIC_API_KEY env var or pass api_key."
            )
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    # ------------------------------------------------------------
    # Analyze & Recommend mode
    # ------------------------------------------------------------

    def analyze_and_recommend(
        self,
        transcript: Transcript,
        available_preset_keys: Optional[list[str]] = None,
        max_concepts: int = 5,
    ) -> AnalysisReport:
        """Read the full transcript and pitch deliverable concepts.

        The LLM doesn't yet pick exact segments — it pitches ideas. The user
        then accepts concepts, and plan_deliverable() turns each into a
        fully-specified Deliverable.
        """
        if available_preset_keys is None:
            available_preset_keys = list(PRESETS_BY_KEY.keys())

        preset_menu = "\n".join(
            f"  - {k}: {PRESETS_BY_KEY[k].display_name} "
            f"({PRESETS_BY_KEY[k].target_duration_sec:.0f}s target)"
            for k in available_preset_keys
        )

        system = SYSTEM_ANALYZE
        user = ANALYZE_PROMPT.format(
            transcript=transcript.format_for_llm(),
            total_duration=transcript.duration,
            phrase_count=len(transcript.phrases),
            preset_menu=preset_menu,
            max_concepts=max_concepts,
        )

        response_text = self._call_claude(system, user)
        data = _extract_json(response_text)

        concepts = []
        for c in data.get("concepts", []):
            concepts.append(DeliverableConcept(
                concept=c["concept"],
                pitch=c["pitch"],
                suggested_preset=c["suggested_preset"],
                estimated_duration=float(c["estimated_duration"]),
                key_phrase_ids=[int(x) for x in c.get("key_phrase_ids", [])],
                tone=c.get("tone", ""),
                why_it_works=c.get("why_it_works", ""),
            ))

        return AnalysisReport(
            transcript_source=transcript.source_path,
            total_duration=transcript.duration,
            summary=data.get("summary", ""),
            concepts=concepts,
        )

    # ------------------------------------------------------------
    # Directed mode
    # ------------------------------------------------------------

    def plan_deliverable(
        self,
        transcript: Transcript,
        preset_key: str,
        brief: str = "",
        topic_focus: str = "",
    ) -> Deliverable:
        """Generate a complete plan for one deliverable.

        Args:
            transcript: the A-roll transcript
            preset_key: which DeliverablePreset to target
            brief: freeform creative brief ("punchy ad for sustainability")
            topic_focus: what the deliverable should be ABOUT
                (can be empty for talking-head full edits)
        """
        preset = get_preset(preset_key)

        # Talking-head full edit uses a DIFFERENT prompt path — it doesn't trim,
        # it only annotates which phrases need cutaways.
        if preset.key == "talking_head_full":
            return self._plan_talking_head(transcript, preset, brief, topic_focus)

        # Build a duration worksheet for the LLM: show phrase durations so it
        # can sum them without hallucinating math. This materially improves
        # duration adherence.
        duration_hint = self._build_duration_hint(preset)

        system = SYSTEM_PLAN
        user = PLAN_PROMPT.format(
            transcript=transcript.format_for_llm(),
            total_duration=transcript.duration,
            preset_name=preset.display_name,
            target_duration=preset.target_duration_sec,
            tolerance=preset.duration_tolerance,
            min_duration=max(1, preset.target_duration_sec - preset.duration_tolerance),
            max_duration=preset.target_duration_sec + preset.duration_tolerance,
            style_notes=preset.style_notes,
            brief=brief or "(no specific brief — use your editorial judgment)",
            topic_focus=topic_focus or "(no topic constraint — pick what's strongest)",
            duration_hint=duration_hint,
        )

        response_text = self._call_claude(system, user)
        data = _extract_json(response_text)

        deliverable = _build_deliverable_from_response(data, transcript, preset, is_trim=True)

        # Guardrail: if the LLM badly overshot or undershot duration, issue a
        # correction call with explicit feedback. This catches the most common
        # failure mode where the model ignored the duration budget.
        if self._duration_out_of_bounds(deliverable, preset):
            deliverable = self._retry_with_duration_correction(
                transcript, preset, brief, topic_focus, deliverable, duration_hint,
            )

        return deliverable

    def _plan_talking_head(
        self,
        transcript: Transcript,
        preset: DeliverablePreset,
        brief: str,
        topic_focus: str,
    ) -> Deliverable:
        """Talking-head edits use the full A-roll — we only annotate cutaways."""
        system = SYSTEM_TALKING_HEAD
        user = TALKING_HEAD_PROMPT.format(
            transcript=transcript.format_for_llm(),
            total_duration=transcript.duration,
            style_notes=preset.style_notes,
            brief=brief or "(no specific brief — full interview edit)",
        )

        response_text = self._call_claude(system, user)
        data = _extract_json(response_text)

        return _build_deliverable_from_response(data, transcript, preset, is_trim=False)

    # ------------------------------------------------------------
    # Duration enforcement helpers
    # ------------------------------------------------------------

    @staticmethod
    def _duration_out_of_bounds(deliverable: Deliverable, preset: DeliverablePreset) -> bool:
        # Tolerance already factors in preset-level slack. Add 20% extra grace
        # before triggering a correction — it's expensive to re-call Claude.
        min_ok = max(1, preset.target_duration_sec - preset.duration_tolerance * 1.2)
        max_ok = preset.target_duration_sec + preset.duration_tolerance * 1.2
        return deliverable.actual_duration < min_ok or deliverable.actual_duration > max_ok

    def _build_duration_hint(self, preset: DeliverablePreset) -> str:
        """Build a short explainer the prompt can reference inline."""
        return (
            f"You MUST select phrases whose SUMMED durations land between "
            f"{max(1, preset.target_duration_sec - preset.duration_tolerance):.0f}s and "
            f"{preset.target_duration_sec + preset.duration_tolerance:.0f}s. "
            f"For each phrase, duration = end - start (both in the transcript). "
            f"Add them up as you select. If you overshoot, drop the weakest phrase. "
            f"If you undershoot, add a supporting phrase. The total budget is "
            f"{preset.target_duration_sec:.0f}s — treat this like a word count, not a suggestion."
        )

    def _retry_with_duration_correction(
        self,
        transcript: Transcript,
        preset: DeliverablePreset,
        brief: str,
        topic_focus: str,
        bad_plan: Deliverable,
        duration_hint: str,
    ) -> Deliverable:
        """Re-call Claude with explicit feedback about the duration violation."""
        overshoot = bad_plan.actual_duration - preset.target_duration_sec
        direction = "TOO LONG" if overshoot > 0 else "TOO SHORT"
        correction = (
            f"YOUR PREVIOUS PLAN WAS {direction}.\n"
            f"  Target: {preset.target_duration_sec:.0f}s\n"
            f"  Your plan: {bad_plan.actual_duration:.1f}s\n"
            f"  Delta: {overshoot:+.1f}s\n\n"
            f"This is a hard failure. {direction.lower().capitalize()} plans cannot "
            f"be delivered as a {preset.display_name}. Revise your phrase selection.\n\n"
            f"Previously selected phrase IDs (for reference — feel free to change): "
            f"{[pid for seg in bad_plan.segments for pid in seg.phrase_ids]}\n\n"
        )

        system = SYSTEM_PLAN
        user = correction + PLAN_PROMPT.format(
            transcript=transcript.format_for_llm(),
            total_duration=transcript.duration,
            preset_name=preset.display_name,
            target_duration=preset.target_duration_sec,
            tolerance=preset.duration_tolerance,
            min_duration=max(1, preset.target_duration_sec - preset.duration_tolerance),
            max_duration=preset.target_duration_sec + preset.duration_tolerance,
            style_notes=preset.style_notes,
            brief=brief or "(no specific brief — use your editorial judgment)",
            topic_focus=topic_focus or "(no topic constraint — pick what's strongest)",
            duration_hint=duration_hint,
        )

        response_text = self._call_claude(system, user)
        data = _extract_json(response_text)
        return _build_deliverable_from_response(data, transcript, preset, is_trim=True)

    # ------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------

    def _call_claude(self, system: str, user: str) -> str:
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            raise PlannerError(f"Claude API error: {e}") from e

        # Extract text from content blocks
        text_parts = []
        for block in message.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts)


# ------------------------------------------------------------
# Response parsing helpers
# ------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract the JSON object from Claude's response.

    Claude sometimes wraps JSON in ```json fences or adds preamble/postamble.
    This handles both, walks brace depth to find the first complete object,
    and attempts a best-effort REPAIR pass if the extracted JSON is malformed
    (large responses sometimes miss a comma between fields).
    """
    # Try fenced block first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Try repair on the fenced candidate before falling through
            repaired = _try_repair_json(candidate)
            if repaired is not None:
                return repaired

    # Find the first '{' then walk forward, tracking brace depth AND string
    # state (so braces inside string literals don't count). Stop when depth
    # returns to 0 — that's the end of the first complete object.
    start = text.find("{")
    if start == -1:
        raise PlannerError(
            f"Could not find JSON in LLM response. First 500 chars:\n{text[:500]}"
        )

    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        end = text.rfind("}")
        if end <= start:
            raise PlannerError(
                f"Unbalanced JSON in LLM response. First 500 chars:\n{text[:500]}"
            )

    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as first_err:
        # Attempt repair before giving up. Large responses from Claude
        # occasionally drop a comma between fields — fixable deterministically.
        repaired = _try_repair_json(candidate)
        if repaired is not None:
            return repaired
        raise PlannerError(
            f"LLM returned malformed JSON: {first_err}\n"
            f"First 500 chars of candidate:\n{candidate[:500]}"
        ) from first_err


def _try_repair_json(candidate: str) -> Optional[dict]:
    """Best-effort repair for common Claude JSON mistakes in long responses.

    Handles:
      1. Trailing commas before ] or }
      2. Missing commas between fields in an object
      3. Missing commas between objects in an array
      4. Unescaped newlines inside string literals
    Returns the parsed dict on success, None if every repair attempt fails.
    """
    attempts: list[str] = []

    # Repair 1: strip trailing commas before } or ] (JSON doesn't allow them)
    step1 = re.sub(r",(\s*[}\]])", r"\1", candidate)
    attempts.append(step1)

    # Repair 2: insert a comma between a closing "}" or "]" or string/number/
    # true/false/null and the NEXT "key": or opening { — i.e. a missing field
    # separator inside an object. We walk char-by-char respecting strings.
    step2 = _insert_missing_commas(step1)
    attempts.append(step2)

    # Repair 3: escape literal newlines inside strings (Claude occasionally
    # emits raw \n inside a quoted value, which json.loads rejects).
    step3 = _escape_newlines_in_strings(step2)
    attempts.append(step3)

    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    return None


def _insert_missing_commas(text: str) -> str:
    """Walk the JSON text and insert commas where two tokens abut without a
    separator. Respects string literals and escape sequences.

    Triggers:
      - After closing " of a VALUE string, if the next non-ws char is a "
        that starts a key (i.e. has a `:` after it)
      - After `}` or `]`, if the next non-ws char is a " that starts a key

    We specifically detect "is this string a key?" by looking at what comes
    immediately after its closing quote — a `:` means key, anything else
    means value.
    """
    out: list[str] = []
    n = len(text)
    i = 0
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        out.append(ch)

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                # We just closed a string. Determine if it was a key or value.
                # A key has `:` as its next non-ws char. A value doesn't.
                nxt = i + 1
                while nxt < n and text[nxt] in " \t\r\n":
                    nxt += 1
                this_was_value = (nxt >= n or text[nxt] != ":")
                if this_was_value:
                    # If the next non-ws char is another " that opens a key,
                    # we need a comma between.
                    if nxt < n and text[nxt] == '"' and _looks_like_object_key(text, nxt):
                        has_comma = any(text[k] == "," for k in range(i + 1, nxt))
                        if not has_comma:
                            out.append(",")
        else:
            if ch == '"':
                in_string = True
            elif ch in "}]":
                # Closing an object or array — check for missing comma before
                # a following key-opening quote.
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] == '"' and _looks_like_object_key(text, j):
                    has_comma = any(text[k] == "," for k in range(i + 1, j))
                    if not has_comma:
                        out.append(",")
        i += 1
    return "".join(out)


def _looks_like_object_key(text: str, start: int) -> bool:
    """Starting at a `"` position, scan forward past the string literal and
    whitespace. If the next non-ws char is `:`, this is a key.
    """
    if start >= len(text) or text[start] != '"':
        return False
    i = start + 1
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            # End of string — look for `:`
            j = i + 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            return j < len(text) and text[j] == ":"
        i += 1
    return False


def _escape_newlines_in_strings(text: str) -> str:
    """Replace raw \\n and \\r inside string literals with escaped forms."""
    out: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == "\\":
                out.append(ch)
                escape = True
            elif ch == '"':
                out.append(ch)
                in_string = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            out.append(ch)
            if ch == '"':
                in_string = True
    return "".join(out)


def _build_deliverable_from_response(
    data: dict,
    transcript: Transcript,
    preset: DeliverablePreset,
    is_trim: bool,
) -> Deliverable:
    """Convert LLM response dict into a Deliverable, validating phrase IDs."""
    phrase_by_id = {p.id: p for p in transcript.phrases}

    segments = []
    for seg_data in data.get("segments", []):
        phrase_ids = [int(x) for x in seg_data.get("phrase_ids", [])]
        # Validate all IDs exist
        unknown = [pid for pid in phrase_ids if pid not in phrase_by_id]
        if unknown:
            raise PlannerError(f"LLM referenced unknown phrase IDs: {unknown}")
        if not phrase_ids:
            continue

        phrases = [phrase_by_id[pid] for pid in phrase_ids]
        # Sort by source start time so timing is correct even if LLM listed out of order
        phrases_sorted = sorted(phrases, key=lambda p: p.start)

        segments.append(SegmentPlan(
            phrase_ids=phrase_ids,
            order=int(seg_data.get("order", len(segments))),
            source_start=phrases_sorted[0].start,
            source_end=phrases_sorted[-1].end,
            text=" ".join(p.text for p in phrases_sorted),
            role=seg_data.get("role", "beat"),
            broll_themes=list(seg_data.get("broll_themes", [])),
            broll_pacing=seg_data.get("broll_pacing", "medium"),
            cutaway_density=float(seg_data.get("cutaway_density", 0.5)),
            editorial_notes=seg_data.get("editorial_notes", ""),
        ))

    # Sort by 'order' so list reflects final-cut order
    segments.sort(key=lambda s: s.order)

    actual_duration = sum(s.duration for s in segments)

    return Deliverable(
        concept=data.get("concept", ""),
        pitch=data.get("pitch", ""),
        preset_key=preset.key,
        target_duration=preset.target_duration_sec if is_trim else transcript.duration,
        actual_duration=actual_duration,
        segments=segments,
        suggested_title=data.get("suggested_title", ""),
        tone=data.get("tone", ""),
        opening_hook=data.get("opening_hook", ""),
        why_it_works=data.get("why_it_works", ""),
    )


# ------------------------------------------------------------
# Prompts — the heart of the planner
# ------------------------------------------------------------

SYSTEM_ANALYZE = """You are a senior video editor and producer analyzing raw A-roll \
footage to identify publishable deliverables. You read transcripts like a \
documentary editor: looking for moments that land, narrative through-lines, \
surprising or emotionally resonant beats, and natural story arcs hidden inside \
long-form material.

You respond ONLY with valid JSON. No preamble, no explanation outside the JSON."""


ANALYZE_PROMPT = """Here is a transcript from raw A-roll footage. Each phrase has \
an ID and timecode.

TRANSCRIPT ({total_duration:.1f}s total, {phrase_count} phrases):
{transcript}

AVAILABLE DELIVERABLE PRESETS:
{preset_menu}

Your job: Propose up to {max_concepts} distinct, publishable deliverables that \
could be cut from this footage. Each concept should be a real, specific idea — \
not a generic format description. Think like an editor scrolling through this \
material for the first time, noticing what's actually there.

Good concepts:
- Find a latent story the speaker didn't set out to tell
- Identify the single most quotable line and build around it
- Pull out a surprising admission or reversal
- Spot a theme that recurs across distant parts of the transcript

Bad concepts (avoid these):
- "A highlight reel of the interview" (too generic)
- "15s clip of the introduction" (not editorial)
- Multiple concepts that are minor variations of each other

Respond with JSON in this exact shape:

{{
  "summary": "One paragraph describing what the A-roll contains and its overall shape.",
  "concepts": [
    {{
      "concept": "Short pitch (under 15 words)",
      "pitch": "2-3 sentence explanation of the editorial angle, what makes it work, and who it's for.",
      "suggested_preset": "one of the preset keys from the menu above",
      "estimated_duration": 30.0,
      "key_phrase_ids": [3, 12, 18],
      "tone": "punchy | reflective | energetic | intimate | authoritative | playful",
      "why_it_works": "One sentence on the editorial logic — why these moments form a coherent piece."
    }}
  ]
}}"""


SYSTEM_PLAN = """You are a senior video editor with a HARD duration budget. \
Your cuts land within the target duration or they cannot be delivered. \
You think like a broadcast editor under a runtime clock, not a documentarian. \
You reorder segments when doing so strengthens the cut, and you annotate each \
selected segment with B-roll themes, pacing, and cutaway density.

CRITICAL RULE: Before finalizing, you sum the durations of every phrase you \
selected and verify the total falls within the allowed range. If you exceed it, \
you drop the weakest phrase and recompute. This is not optional.

You respond ONLY with valid JSON."""


PLAN_PROMPT = """Plan a deliverable with these parameters:

FORMAT: {preset_name}
TARGET DURATION: {target_duration:.0f}s
ACCEPTABLE RANGE: {min_duration:.0f}s to {max_duration:.0f}s (HARD LIMIT)
STYLE NOTES: {style_notes}

CREATIVE BRIEF: {brief}
TOPIC FOCUS: {topic_focus}

A-ROLL TRANSCRIPT ({total_duration:.1f}s total):
{transcript}

==== DURATION INSTRUCTIONS ====
{duration_hint}

==== WORKFLOW ====
1. Identify candidate phrases by ID that serve the brief.
2. CALCULATE THE RUNNING TOTAL of their durations using end - start from the timecodes.
3. If total exceeds {max_duration:.0f}s, drop phrases until it fits.
4. If total is under {min_duration:.0f}s, add one supporting phrase.
5. Only when the total fits the range, group phrases into segments and order them.
6. A segment is a contiguous-in-final-cut group. Segments may reorder non-chronologically \
for editorial impact — the first segment is the HOOK.
7. For each segment, suggest 3-6 concrete B-roll themes ("aerial shot of factory at dawn", \
not "industrial"). Set cutaway_density (0=on speaker, 1=heavy B-roll) and broll_pacing \
("sparse" / "medium" / "heavy").

==== SHORT-FORM DISCIPLINE (for reels/ads under 60s) ====
Short-form cuts fail when editors try to include too much context. A 30s reel has room \
for ONE central idea, delivered in 4-8 phrases, not five acts. Prefer fewer phrases said \
brilliantly over many phrases said redundantly. When in doubt, cut.

Respond with JSON in this exact shape:

{{
  "concept": "One-line pitch for this deliverable.",
  "pitch": "2-3 sentences on the editorial angle.",
  "suggested_title": "A good working title.",
  "tone": "punchy | reflective | energetic | etc.",
  "opening_hook": "Why the first segment grabs attention.",
  "why_it_works": "One paragraph on the editorial logic of this cut.",
  "computed_total_duration": 28.5,
  "segments": [
    {{
      "order": 0,
      "phrase_ids": [12],
      "role": "hook | development | proof | close | beat",
      "broll_themes": ["close-up of hands assembling", "wide shot of workshop", "..."],
      "broll_pacing": "sparse | medium | heavy",
      "cutaway_density": 0.6,
      "editorial_notes": "Hold on speaker face for the first beat, cut to B-roll on the word 'manufacturing'."
    }}
  ]
}}

IMPORTANT:
- Only use phrase_ids that exist in the transcript above
- Total selected duration MUST be within {min_duration:.0f}s-{max_duration:.0f}s. Verify before submitting.
- Include the computed_total_duration field showing your arithmetic
- Segments should be listed in their FINAL CUT order (by 'order' field)
- Output ONLY the JSON, no other text"""


SYSTEM_TALKING_HEAD = """You are a senior video editor planning a full talking-head \
edit. You do NOT trim the A-roll. Your entire job is to identify, for each phrase \
in the transcript, how much B-roll coverage it should get and what visuals would \
work — so the human editor has a clear cutaway plan for the whole piece.

You respond ONLY with valid JSON."""


TALKING_HEAD_PROMPT = """Create a B-roll coverage plan for this full talking-head edit.

A-ROLL TRANSCRIPT ({total_duration:.1f}s total):
{transcript}

STYLE NOTES: {style_notes}

BRIEF: {brief}

Group consecutive phrases into coverage segments. For each segment decide:
- cutaway_density (0 = stay on speaker, 1 = heavy B-roll)
- specific B-roll themes if density > 0
- editorial rationale

Keep the FULL transcript covered — every phrase ID must appear in exactly one segment. \
Segments should be contiguous chronologically (no reordering for talking-head edits).

Respond with JSON:

{{
  "concept": "Full talking-head edit with cutaway coverage.",
  "pitch": "2-3 sentences summarizing the overall coverage philosophy.",
  "suggested_title": "A working title.",
  "tone": "reflective | authoritative | etc.",
  "opening_hook": "Describe the opening beat.",
  "why_it_works": "The coverage logic across the piece.",
  "segments": [
    {{
      "order": 0,
      "phrase_ids": [0, 1, 2],
      "role": "beat",
      "broll_themes": ["..."],
      "broll_pacing": "sparse | medium | heavy",
      "cutaway_density": 0.3,
      "editorial_notes": "Stay on speaker — the facial expression carries this moment."
    }}
  ]
}}

IMPORTANT: Every phrase ID from the transcript must be included in exactly one segment. \
Segments are chronological (don't reorder). Output ONLY the JSON."""
