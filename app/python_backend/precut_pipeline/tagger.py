"""Descriptive tagging via local VLM (LLaVA through Ollama).

This generates the human-readable keywords you see when browsing clips.
CLIP embeddings handle the actual matching; tags are for display + fulltext fallback.
"""
import base64
from pathlib import Path
from typing import Optional

import requests

from .config import OLLAMA_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, TAGGING_PROMPT


class OllamaTagger:
    """Talks to a local Ollama instance for VLM tagging."""

    def __init__(self, url: str = OLLAMA_URL, model: str = OLLAMA_MODEL):
        self.url = url.rstrip("/")
        self.model = model

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is pulled."""
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=3)
            if r.status_code != 200:
                return False
            models = [m["name"] for m in r.json().get("models", [])]
            # Match either exact name or "model:tag"
            return any(m == self.model or m.startswith(self.model.split(":")[0])
                       for m in models)
        except requests.RequestException:
            return False

    def tag_image(self, image_path: Path) -> list[str]:
        """Generate descriptive tags for a frame. Returns empty list on failure."""
        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "model": self.model,
                "prompt": TAGGING_PROMPT,
                "images": [image_b64],
                "stream": False,
                "options": {
                    "temperature": 0.2,  # low temp = consistent tags
                    "num_predict": 150,
                }
            }

            r = requests.post(
                f"{self.url}/api/generate",
                json=payload,
                timeout=OLLAMA_TIMEOUT
            )
            r.raise_for_status()
            response_text = r.json().get("response", "")
            return self._parse_tags(response_text)

        except (requests.RequestException, ValueError):
            return []

    @staticmethod
    def _parse_tags(text: str) -> list[str]:
        """Clean up the model's output into a list of tags."""
        # Model sometimes prefixes with "Keywords:" or wraps in markdown
        text = text.strip()
        for prefix in ["Keywords:", "keywords:", "Tags:", "tags:"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()

        # Remove markdown formatting
        text = text.replace("*", "").replace("`", "").replace("\n", ", ")

        # Split and clean
        raw_tags = [t.strip() for t in text.split(",")]
        tags = []
        for tag in raw_tags:
            # Drop empty, overly long (probably a sentence), or list-numbered
            if not tag or len(tag) > 60:
                continue
            # Strip leading numbers like "1. " or "- "
            while tag and tag[0] in "0123456789.-) ":
                tag = tag[1:]
            tag = tag.strip().lower()
            if tag and tag not in tags:
                tags.append(tag)

        return tags[:15]  # cap at 15 per frame
