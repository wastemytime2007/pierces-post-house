"""posthouse.harvest.transcribe — re-export of PreCut's Whisper transcription.

Provenance: ``precut_pipeline.transcriber`` at the pin recorded in
``posthouse/PRECUT_PIN`` (see ``posthouse.precut_bridge``). ROADMAP.md's
role→skill map lists transcription as Phase 1 harvest material feeding
the Assistant Editor's transcript-flagging skill (Phase 4) and the Creative
Editor's story planner (Phase 6).

**Heavy dependency, Ryan's Mac only.** ``precut_pipeline.transcriber``
imports ``torch`` unconditionally at module scope and lazily imports
``whisper`` inside :meth:`Transcriber._load`, so this module is not
importable in a cloud session — it self-skips (see
``safety_net/tests/test_transcribe.py``) the way the Tier-2 safety-net
tests already do.

**On-disk shape.** ``Transcript.to_dict()`` / ``Transcript.save()`` (both
re-exported here unchanged) already produce exactly what PreCut's own
pipeline writes per A-roll under a project's ``transcripts/`` directory —
see ``pipeline.py``'s transcribe-stage worker, which does nothing more
than ``transcript = transcriber.transcribe(proxy_path); transcript.save(
transcript_dir / f"{source_file.stem}.json")``. Nothing here reformats
that shape; :func:`transcript_to_json` and :func:`save_transcript` are
thin conveniences over the same two calls.

**Whisper timing-bias risk (ROADMAP.md §7).** The risk note asks that
phrase-boundary handling be reused, not re-derived. Reading
``transcriber.py`` end to end: phrase chunking (``chunk_into_phrases`` —
break on sentence punctuation, break on pauses >= 0.6s, cap at 25 words,
merge runts) lives entirely inside the module this wrapper imports
unchanged, so calling :func:`transcribe` below already gets it verbatim —
there is no separate padding step to reimplement here. (PreCut's actual
*padding* logic — correcting Whisper's early-end bias when phrases get
placed on a timeline — lives downstream in ``matcher.py``'s
``_apply_padding``, applied at assembly time, not at transcription time;
out of scope for this wrapper, in scope for whichever Phase 4/6 skill
calls the matcher.)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from posthouse.precut_bridge import import_precut

_mod = import_precut("precut_pipeline.transcriber")
_config = import_precut("precut_pipeline.config")

Word = _mod.Word
Phrase = _mod.Phrase
Transcript = _mod.Transcript
Transcriber = _mod.Transcriber
chunk_into_phrases = _mod.chunk_into_phrases

WHISPER_MODEL = _config.WHISPER_MODEL
WHISPER_LANGUAGE = _config.WHISPER_LANGUAGE


def transcribe(
    media_path: Union[str, Path],
    *,
    language: Optional[str] = WHISPER_LANGUAGE,
    model_name: str = WHISPER_MODEL,
    device: Optional[str] = None,
) -> "Transcript":
    """Transcribe an A-roll audio/video file with PreCut's own Whisper path.

    ``media_path`` is whatever file has the audio track worth transcribing
    (a proxy, as PreCut's own pipeline uses, or an original — Whisper reads
    either via ffmpeg). ``language`` defaults to PreCut's own
    ``WHISPER_LANGUAGE`` (``None`` = auto-detect); ``model_name`` defaults
    to PreCut's own ``WHISPER_MODEL`` (``"base"``) so callers get identical
    behavior unless they deliberately override it.

    Lazy-loads the Whisper model on first call (``Transcriber._load``) —
    this is where a first-ever run on a machine without
    ``~/.cache/whisper/<model>.pt`` would download weights; callers that
    care should check that cache themselves before calling, the way this
    module's own test does.
    """
    transcriber = Transcriber(model_name=model_name, device=device)
    return transcriber.transcribe(Path(media_path), language=language)


def transcript_to_json(transcript: "Transcript") -> str:
    """The exact JSON text ``Transcript.save`` would write, as a string."""
    import json

    return json.dumps(transcript.to_dict(), indent=2)


def save_transcript(transcript: "Transcript", path: Union[str, Path]) -> Path:
    """Write ``transcript`` to ``path`` in PreCut's on-disk shape.

    Thin wrapper over ``Transcript.save`` (re-exported above) so callers in
    ``posthouse`` don't need to know the method exists on the dataclass —
    matches the calling convention of this package's other wrappers.
    """
    path = Path(path)
    transcript.save(path)
    return path


__all__ = [
    "Word",
    "Phrase",
    "Transcript",
    "Transcriber",
    "chunk_into_phrases",
    "WHISPER_MODEL",
    "WHISPER_LANGUAGE",
    "transcribe",
    "transcript_to_json",
    "save_transcript",
]
