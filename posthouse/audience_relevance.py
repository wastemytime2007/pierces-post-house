"""posthouse.audience_relevance — score exhaustively-extracted transcript
fragments against a project's stated audience/content goal.

**Scope, and how this fits with the two pieces built alongside it
(2026-09-03).** `transcript_coverage.py` finds every storyline-worthy
fragment in a transcript, exhaustively — neutral output, no judgment
about what any of it is FOR. Project Manager intake now captures ONE
audience/content-goal per project (a profile picked from the app-level
library in Settings, e.g. "Contractor Recruiting" or "Brand /
Authority" — see `docs/contracts/PROJECT_MANIFEST.md`'s
`project.audience_goal`). This module is the piece that connects them:
given that single stated goal and the fragment list, judge how well each
fragment actually serves it. This is what turns "here's what's in the
transcript" into the color-coded flagging Ryan originally asked for
("flag things by pulling up usable pieces of information that were
interesting or help inform the story ... color code it based on the
storyline being told").

**Why one call, not the same windowed approach as extraction.** The
skimming problem `transcript_coverage.py` fixes is about READING a long
transcript exhaustively — that's genuinely bottlenecked by output budget
on a single call. Scoring is different: there are only ever as many
fragments as extraction found (single digits to low tens per interview,
measured 2026-09-03), so one call comfortably reads all of them plus the
goal text and returns a judgment for each. No windowing needed here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from posthouse.precut_bridge import import_precut

_anthropic_client = import_precut("precut_pipeline.anthropic_client")
_config = import_precut("precut_pipeline.config")
_cutlist = import_precut("precut_pipeline.cutlist")
_story_planner = import_precut("precut_pipeline.story_planner")

build_anthropic_client = _anthropic_client.build_anthropic_client
ANTHROPIC_MODEL = _config.ANTHROPIC_MODEL
TopicRange = _cutlist.TopicRange
StoryPlannerError = _story_planner.StoryPlannerError
_extract_json = _story_planner._extract_json

VALID_FITS = ("strong", "possible", "off_topic")

# Maps to real, distinct RGB marker colors — reusing the mechanism
# exporter.py already writes into FCP7 XML markers (confirmed working
# in the exported XML: verified the actual <color> elements carry
# distinct RGB per fit). The first, muted palette (green/amber/gray)
# all showed as the same color in Premiere's own marker UI, 2026-09-03
# — real Premiere behavior, not a bug in our XML: Premiere's marker
# color swatches snap to its own limited named palette, and those
# muted/desaturated tones apparently landed on the same nearest match.
# Ryan's fix, bold and unambiguous: red=off-topic, green=possible,
# gold/yellow=strong.
FIT_COLORS = {
    "strong": (255, 204, 0),      # gold/yellow
    "possible": (0, 200, 0),      # green
    "off_topic": (220, 20, 20),   # red
}

RELEVANCE_SYSTEM_PROMPT = """You are helping an editor triage transcript material against a specific content goal. You judge fit honestly — most real interviews contain material that doesn't serve the stated goal, and pretending otherwise wastes the editor's time.

**Two distinct, equally real paths to "strong" — corrected 2026-09-04, per Ryan directly.** \
Topical on-message content (real educational/story/construction/company substance) is one path. \
The other is just as real: a genuine human, heart, comedy, or character moment that humanizes \
the person on camera — even when it has nothing to do with the stated topic on its face. Ryan's \
own example: a real finished cut used a moment of the subject saving a toad rather than killing \
it. Scored on topical relevance alone against a recruiting goal, that fragment looks worthless — \
it was actually essential, because it revealed a genuine caring side that countered a real \
on-the-job perception the subject dealt with. A fragment doesn't have to relate to the stated \
topic to be "strong" — it has to be either substantively on-topic OR genuinely human/character-\
revealing. Reserve "off_topic" for material that is neither: not substantive, and not a real \
character/heart/humor moment either (small talk, dead air, a technical aside nobody will use).

Never inflate relevance to seem more useful — most interviews still have real off_topic material \
by this standard, and calling everything "strong" helps no one. But don't undersell a genuine \
character moment just because it isn't topical; that's the exact mistake this correction fixes.

Return ONLY valid JSON. No preamble, no markdown fences."""

RELEVANCE_PROMPT = """A project's stated audience/content goal:

<audience_goal>
{audience_goal}
</audience_goal>

Here are transcript fragments already identified in this project's interview, each a distinct topic or moment:

<fragments>
{fragments}
</fragments>

For EVERY fragment listed above, judge its fit using exactly one of these three labels:
- "strong": EITHER directly, substantively serves the stated goal (real educational/story/construction/company content), OR is a genuine human/heart/comedy/character moment that humanizes the person on camera — these are two separate, equally valid reasons to mark something "strong," not just one.
- "possible": has some relevance or could support the goal indirectly, but isn't a direct hit on either path above.
- "off_topic": neither substantively on-topic nor a real character/heart moment — genuinely disposable material (small talk, dead air, an aside nobody would use).

Be honest, not generous on either path — most interviews have real off_topic material by this standard too. But actively look for genuine character/heart/comedy moments, not just topical ones; a moment that looks small or unrelated on its face can still be exactly right if it's real and humanizing.

For each fragment, also note in `category` which path earned the fit (or best describes it if off_topic): "substantive" (real educational/story/construction/company content) or "character" (human/heart/comedy/relatability moment).

Return JSON in this exact shape, one entry per fragment, in the same order given:

{{
  "scored": [
    {{"index": 0, "fit": "strong", "category": "character", "reasoning": "1 sentence: why this fit"}},
    {{"index": 1, "fit": "off_topic", "category": "substantive", "reasoning": "1 sentence: why this fit"}}
  ]
}}"""


@dataclass
class TaggedFragment:
    """One exhaustively-extracted fragment plus its judged fit against a
    project's stated audience/content goal.

    `category` (added 2026-09-04, Ryan): which of the two real, equally
    valid paths to "strong" this fragment took — "substantive" (real
    educational/story/construction/company content) or "character"
    (human/heart/comedy/relatability). Best-effort even for
    possible/off_topic fragments; empty string if the model didn't
    supply one (never blocks anything downstream)."""
    fragment: object  # TopicRange
    fit: str          # one of VALID_FITS
    reasoning: str
    category: str = ""  # "substantive" | "character" | ""

    @property
    def color_rgb(self):
        return FIT_COLORS.get(self.fit, FIT_COLORS["off_topic"])


def _format_fragments_for_llm(fragments: List) -> str:
    lines = []
    for i, f in enumerate(fragments):
        lines.append(
            f'[{i}] {f.source_start_sec:.1f}s-{f.source_end_sec:.1f}s '
            f'"{f.topic_label}": {f.summary}'
        )
    return "\n".join(lines)


def score_fragments_for_audience(
    audience_goal: str,
    fragments: List,
    model: str = ANTHROPIC_MODEL,
    api_key: Optional[str] = None,
) -> List[TaggedFragment]:
    """Score every fragment's fit against a single stated audience/goal.

    Returns one TaggedFragment per input fragment, same order, always —
    a fragment the model doesn't address in its response is defensively
    tagged "off_topic" with a note, never silently dropped (see module
    docstring: nothing gets silently dropped).
    """
    if not fragments:
        return []
    if not audience_goal or not audience_goal.strip():
        raise ValueError("audience_goal must be non-empty — this module has "
                          "nothing to score fragments against otherwise.")

    client = build_anthropic_client(api_key=api_key)
    user_prompt = RELEVANCE_PROMPT.format(
        audience_goal=audience_goal.strip(),
        fragments=_format_fragments_for_llm(fragments),
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0.2,
            system=RELEVANCE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        raise StoryPlannerError(f"Anthropic API error: {e}") from e

    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    text = "".join(parts).strip()
    if not text:
        raise StoryPlannerError("Empty response from Claude.")
    data = _extract_json(text)

    by_index = {}
    for entry in data.get("scored", []):
        try:
            idx = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        fit = str(entry.get("fit", "")).strip()
        if fit not in VALID_FITS:
            fit = "off_topic"
        by_index[idx] = TaggedFragment(
            fragment=fragments[idx] if 0 <= idx < len(fragments) else None,
            fit=fit,
            reasoning=str(entry.get("reasoning", ""))[:300],
            category=str(entry.get("category", ""))[:20],
        )

    results: List[TaggedFragment] = []
    for i, f in enumerate(fragments):
        if i in by_index and by_index[i].fragment is not None:
            results.append(by_index[i])
        else:
            results.append(TaggedFragment(
                fragment=f, fit="off_topic",
                reasoning="Not addressed in the model's response — defaulted, not dropped.",
            ))
    return results


def save_tagged_fragments(path, source_file: str, audience_goal: str,
                           tagged_fragments: List[TaggedFragment]) -> None:
    """Persist tagged fragments for one A-roll file as a pipeline artifact
    (``<project_dir>/flags/<safe_name>.json``, see ``pipeline.py``'s
    ``_run_transcript_flagging``). ``audience_goal`` is stored alongside
    the results purely for humans/debugging inspecting the file, and so a
    future re-run can tell the goal changed — this function doesn't
    itself compare it to anything.
    """
    data = {
        "source_file": source_file,
        "audience_goal": audience_goal,
        "fragments": [
            {
                "start": tf.fragment.source_start_sec,
                "end": tf.fragment.source_end_sec,
                "label": tf.fragment.topic_label,
                "summary": tf.fragment.summary,
                "fit": tf.fit,
                "reasoning": tf.reasoning,
                "category": tf.category,
            }
            for tf in tagged_fragments
        ],
    }
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def load_tagged_fragments(path) -> List[TaggedFragment]:
    """Load a previously-saved flags artifact back into TaggedFragments
    (reconstructing the underlying TopicRange for each one)."""
    from pathlib import Path
    data = json.loads(Path(path).read_text())
    source_file = data.get("source_file", "")
    results: List[TaggedFragment] = []
    for entry in data.get("fragments", []):
        fragment = TopicRange(
            source_file=source_file,
            source_start_sec=entry["start"],
            source_end_sec=entry["end"],
            topic_label=entry.get("label", ""),
            summary=entry.get("summary", ""),
        )
        fit = entry.get("fit", "off_topic")
        if fit not in VALID_FITS:
            fit = "off_topic"
        results.append(TaggedFragment(
            fragment=fragment, fit=fit, reasoning=entry.get("reasoning", ""),
            category=entry.get("category", ""),
        ))
    return results
