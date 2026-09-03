"""Stage 2: A-roll transcription via local Whisper.

Produces word-level timestamps, then chunks into semantic phrases that become
the units the planner operates on.
"""
import json
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

import torch

from .config import WHISPER_MODEL, WHISPER_LANGUAGE


@dataclass
class Word:
    """A single word with its timing."""
    text: str
    start: float
    end: float


@dataclass
class Phrase:
    """A semantic chunk of the transcript — typically one sentence or clause.

    This is the atomic unit the deliverable planner reasons about. A 60-second
    ad is built from a selection of these phrases, in a specific order.
    """
    id: int
    start: float
    end: float
    text: str
    words: list[Word]

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def word_count(self) -> int:
        return len(self.words)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "duration": self.duration,
            "words": [asdict(w) for w in self.words],
        }


@dataclass
class Transcript:
    """The full result of transcribing an A-roll file."""
    source_path: str
    language: str
    duration: float
    phrases: list[Phrase]

    @property
    def full_text(self) -> str:
        return " ".join(p.text for p in self.phrases)

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "language": self.language,
            "duration": self.duration,
            "phrase_count": len(self.phrases),
            "phrases": [p.to_dict() for p in self.phrases],
        }

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        data = json.loads(Path(path).read_text())
        phrases = []
        for p in data["phrases"]:
            words = [Word(**w) for w in p["words"]]
            phrases.append(Phrase(
                id=p["id"], start=p["start"], end=p["end"],
                text=p["text"], words=words
            ))
        return cls(
            source_path=data["source_path"],
            language=data["language"],
            duration=data["duration"],
            phrases=phrases,
        )

    def format_for_llm(self) -> str:
        """Format for inclusion in planner prompt.

        Example output:
            [id=0 | 0.0-4.2s] "So I've been thinking about our manufacturing..."
            [id=1 | 4.2-7.8s] "And specifically how we source materials locally..."
        """
        lines = []
        for p in self.phrases:
            lines.append(
                f'[id={p.id} | {p.start:.1f}-{p.end:.1f}s] "{p.text.strip()}"'
            )
        return "\n".join(lines)


class Transcriber:
    """Wraps Whisper. Lazy-loads model on first use."""

    def __init__(self, model_name: str = WHISPER_MODEL, device: Optional[str] = None):
        self.model_name = model_name
        if device is None:
            if torch.cuda.is_available():
                device = "cuda"
            else:
                # Whisper doesn't currently support MPS well — fall back to CPU on Mac
                device = "cpu"
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model

    def transcribe(self, audio_path: Path, language: Optional[str] = WHISPER_LANGUAGE) -> Transcript:
        """Transcribe an audio/video file with word-level timestamps."""
        model = self._load()

        options = {
            "word_timestamps": True,
            "verbose": False,
        }
        if language:
            options["language"] = language

        result = model.transcribe(str(audio_path), **options)

        # Pull out word-level segments and re-chunk into sensible phrases
        all_words: list[Word] = []
        for segment in result.get("segments", []):
            for w in segment.get("words", []):
                # Whisper sometimes emits empty/malformed word entries
                text = w.get("word", "").strip()
                if not text:
                    continue
                all_words.append(Word(
                    text=text,
                    start=float(w["start"]),
                    end=float(w["end"]),
                ))

        phrases = chunk_into_phrases(all_words)

        return Transcript(
            source_path=str(audio_path),
            language=result.get("language", language or "unknown"),
            duration=float(result.get("duration", 0)) or (phrases[-1].end if phrases else 0),
            phrases=phrases,
        )


def chunk_into_phrases(
    words: list[Word],
    max_words: int = 25,
    min_words: int = 4,
    pause_threshold_sec: float = 0.6,
) -> list[Phrase]:
    """Group words into coherent phrases for the planner to reason about.

    Rules:
    - Break on sentence-ending punctuation (., !, ?)
    - Break on pauses longer than pause_threshold_sec
    - Force break if phrase would exceed max_words
    - Try to avoid phrases shorter than min_words (merge with neighbor if possible)
    """
    if not words:
        return []

    phrases: list[list[Word]] = []
    current: list[Word] = []

    for i, word in enumerate(words):
        current.append(word)

        # Decide whether to end this phrase after the current word
        should_break = False

        # Sentence-ending punctuation at the end of word text
        if word.text and word.text[-1] in ".!?":
            should_break = True

        # Long pause before the next word
        if i + 1 < len(words):
            gap = words[i + 1].start - word.end
            if gap >= pause_threshold_sec:
                should_break = True

        # Length cap
        if len(current) >= max_words:
            should_break = True

        # Last word always ends the final phrase
        if i == len(words) - 1:
            should_break = True

        if should_break:
            phrases.append(current)
            current = []

    # Merge tiny phrases with their neighbors where sensible
    merged: list[list[Word]] = []
    for phrase in phrases:
        if merged and len(phrase) < min_words and len(merged[-1]) + len(phrase) <= max_words:
            merged[-1].extend(phrase)
        else:
            merged.append(phrase)

    # Build Phrase objects
    result = []
    for idx, word_group in enumerate(merged):
        text = " ".join(w.text for w in word_group).strip()
        result.append(Phrase(
            id=idx,
            start=word_group[0].start,
            end=word_group[-1].end,
            text=text,
            words=word_group,
        ))
    return result
