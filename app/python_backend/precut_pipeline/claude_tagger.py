"""Stage 1b: Descriptive tagging via Claude Sonnet 4.6 vision.

Drop 3.8: replaces LLaVA 7B (which hallucinated freely — would predict
"marble countertop" on a bedroom because it pattern-completed the real-estate
aesthetic) with Claude Sonnet vision. Same interface as OllamaTagger so
ingest can swap between them transparently.

Cost: ~$0.007 per frame at current Sonnet 4.6 pricing. A 1-minute clip
samples ~15 frames, so ~$0.10 per minute of B-roll footage. One-time cost
at ingest.

Accuracy: dramatically better. Claude refuses to invent things it can't see.
Empirically returns 4-8 grounded nouns per frame with near-zero hallucination.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # type: ignore

from .config import ANTHROPIC_MODEL


# Prompt tuned specifically to PREVENT the hallucination patterns we saw
# with LLaVA. Explicitly lists banned categories and demands grounding.
TAGGING_PROMPT = """You are generating searchable keywords for a B-roll video clip in a video editor's library.

Look at this frame and list ONLY what you can DIRECTLY SEE. Output 4-8 concrete nouns, comma-separated, lowercase.

STRICT RULES:
- Name only OBJECTS, MATERIALS, and PLACES visible in the frame.
- If you are not certain an object is present, DO NOT list it.
- Do NOT guess what rooms might be nearby or what the house as a whole looks like.
- Do NOT include any mood, aesthetic, or style words (e.g. "cozy", "modern", "minimalist", "contemporary", "neutral", "calm", "inviting", "clean").
- Do NOT include lighting descriptors (e.g. "well-lit", "bright", "natural light", "warm").
- Do NOT include subjective judgments (e.g. "simple", "elegant", "beautiful", "organized").
- Do NOT output "still image", "video", "photograph", or meta-words about the medium.
- Do NOT include camera angle or framing words — those are added separately.

GOOD output:   couch, coffee table, window, rug, pillow, lamp
BAD output:    couch, modern furniture, cozy, minimalist, well-lit, neutral colors

Output only the comma-separated list. No preamble, no prefix like "Keywords:"."""


# A short-circuit block list applied AFTER the model responds, as a belt-and-
# suspenders defense against hallucinated or useless tags. Any tag that matches
# (case-insensitive, whole-token) gets dropped.
#
# This list is intentionally aggressive. False drops are cheaper than noise —
# a missed "modern" on a clip is invisible; a hallucinated "kitchen" on a
# bedroom corrupts search results.
BANNED_TAGS = frozenset({
    # Style / aesthetic (hallucinated constantly)
    "modern", "contemporary", "contemporary style", "minimalist", "minimalistic",
    "minimal", "minimalist design", "minimalistic design", "minimal decor",
    "minimalist decor", "minimal decoration", "minimalistic decor",
    "minimalist art", "minimalist style",
    "cozy", "cozy atmosphere", "calm", "calm atmosphere", "calm mood",
    "serene atmosphere", "inviting", "welcoming", "spacious", "empty",
    "empty room", "empty space",
    "sleek", "monochromatic", "elegant", "simple", "simple design",
    "simple decor", "simple composition", "simplicity",
    "clean", "clean lines", "tidy", "neat", "organized",
    "classic", "traditional", "rustic", "industrial",

    # Color vibes (not visual facts)
    "neutral colors", "neutral color palette", "neutral tones", "neutral mood",
    "warm tones", "cool tones", "white walls", "white wall",

    # Lighting vibes
    "well-lit", "well lit", "brightly lit", "bright", "bright light",
    "bright lighting", "natural light", "artificial light", "light fixture",
    "dim", "moody lighting",

    # Meta / medium words
    "still image", "image", "photo", "photograph", "picture", "video",
    "frame", "video frame", "screenshot", "shot", "footage",
    "no people", "no person", "unoccupied",

    # Camera / framing (added by motion module instead)
    "close-up", "closeup", "wide shot", "wide-angle", "medium shot",
    "establishing shot", "establishing", "static", "static shot",
    "straight camera angle", "eye-level", "pan", "tilt", "zoom",

    # Generic catchall
    "decor", "decoration", "home", "home decor", "interior", "interior design",
    "indoor", "indoors", "indoor setting", "indoor scene",
    "room", "space", "area", "setting", "scene",
    "furniture", "modern furniture", "modern decor",
    "abstract", "gallery wall",
})


class ClaudeVisionTagger:
    """Tags frames using Claude Sonnet 4.6 vision.

    Same interface as OllamaTagger:
      - .model (str): for logging
      - .is_available() → bool
      - .tag_image(path) → list[str]

    Unlike OllamaTagger which returns [] on failure, this one also returns
    [] on failure — callers treat "tagger produced nothing" the same way
    either way.
    """

    TAGGER_ID = "claude-sonnet-4-6"

    def __init__(self, model: str = ANTHROPIC_MODEL, api_key: Optional[str] = None):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: Optional[Anthropic] = None

    def is_available(self) -> bool:
        """True if the anthropic SDK is importable AND an API key is present.

        We don't make a test API call here — that would cost money every time
        we check availability, and credentialed keys are almost always valid.
        """
        if Anthropic is None:
            return False
        if not self._api_key:
            return False
        return True

    def tag_image(self, image_path: Path) -> list[str]:
        """Generate tags for a single frame. Returns [] on any failure."""
        if not self.is_available():
            return []
        try:
            if self._client is None:
                self._client = Anthropic(api_key=self._api_key)

            # Read image and base64-encode it
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            img_b64 = base64.standard_b64encode(img_bytes).decode("ascii")

            # Pick a reasonable media type. We only generate .jpg frames so
            # this is usually exact; fall back to jpeg as a safe default.
            ext = image_path.suffix.lower()
            media_type = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp",
                ".gif": "image/gif",
            }.get(ext, "image/jpeg")

            response = self._client.messages.create(
                model=self.model,
                max_tokens=200,
                temperature=0.0,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": TAGGING_PROMPT},
                    ],
                }],
            )

            # Extract text from response
            text_parts = []
            for block in response.content:
                if getattr(block, "type", None) == "text":
                    text_parts.append(block.text)
            raw_text = " ".join(text_parts).strip()

            return _parse_and_filter_tags(raw_text)

        except Exception:
            # Any failure — network, quota, parse error — degrades gracefully
            # to empty tags. The pipeline continues with just CLIP embeddings.
            return []


def _parse_and_filter_tags(text: str) -> list[str]:
    """Clean a comma-separated string into a vetted tag list.

    Steps:
      1. Strip common prefixes ("Keywords:", "Tags:", markdown fences)
      2. Split on commas, newlines, semicolons
      3. Lowercase, dedupe
      4. Remove banned tags
      5. Drop very long entries (probably a sentence that slipped through)
      6. Cap at 12 tags per frame
    """
    if not text:
        return []

    text = text.strip()
    for prefix in ("Keywords:", "keywords:", "Tags:", "tags:", "Output:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    # Strip any markdown formatting characters
    text = text.replace("`", "").replace("*", "").replace('"', "")

    # Unify separators to comma
    text = text.replace(";", ",").replace("\n", ",")

    tags: list[str] = []
    seen: set[str] = set()
    for raw in text.split(","):
        tag = raw.strip()
        # Strip leading list bullets
        while tag and tag[0] in "0123456789.-)* ":
            tag = tag[1:]
        tag = tag.strip().lower()
        if not tag:
            continue
        if len(tag) > 40:
            # long → probably a sentence, drop
            continue
        if tag in BANNED_TAGS:
            continue
        # Drop partial matches on phrases like "well-lit room" where the
        # 'room' substring makes it a banned category: the full tag here
        # is fine (we only exact-match on BANNED). Leave it alone.
        if tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)

    return tags[:12]
