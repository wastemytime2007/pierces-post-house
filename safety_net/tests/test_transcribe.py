"""Tier-2: posthouse.harvest.transcribe against REAL speech audio.

Whisper needs a real venv (torch + whisper) and is not byte-deterministic,
so this test never asserts an exact string — see module docstring in
posthouse/harvest/transcribe.py and ROADMAP.md §7's Whisper timing-bias
note. It generates real speech with macOS `say` (not a synthetic tone —
Whisper cannot transcribe a sine wave) and asserts on keywords and
phrase-structure invariants instead.

Voice note: the default `say` voice (system default, "Alex" on this
machine) transcribed "countertops" as "countered ups" / "counter -dops" —
Whisper base's actual acoustic behavior on that voice's synthesis, not a
bug in this wrapper. `-v Samantha` transcribes the same sentence cleanly.
Recorded here rather than silently switched to, because a keyword miss
on a differently-configured `say` is a real, reproducible finding worth
knowing about, not a flaky test.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.tier2


def _require_deps_or_skip():
    missing = [m for m in ("torch",) if importlib.util.find_spec(m) is None]
    if missing:
        pytest.skip(
            f"transcribe test requires {', '.join(missing)}; run on Ryan's "
            f"Mac (~/precut-venv-fresh)."
        )
    try:
        import whisper  # noqa: F401
    except ImportError:
        pytest.skip("transcribe test requires the `whisper` package.")
    if shutil.which("say") is None:
        pytest.skip("transcribe test requires macOS `say` (TTS) on PATH.")
    if shutil.which("ffmpeg") is None:
        pytest.skip("transcribe test requires ffmpeg on PATH.")


SENTENCE = "the kitchen has new granite countertops and stainless appliances"
KEYWORDS = ("kitchen", "granite", "countertops")


@pytest.fixture(scope="module")
def speech_wav(tmp_path_factory) -> Path:
    _require_deps_or_skip()
    work = tmp_path_factory.mktemp("transcribe_speech")
    aiff = work / "speech.aiff"
    wav = work / "speech.wav"
    subprocess.run(
        ["say", "-v", "Samantha", "-o", str(aiff), SENTENCE],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(aiff), "-ar", "16000", str(wav)],
        check=True, capture_output=True,
    )
    return wav


def test_transcribe_recovers_keywords(speech_wav: Path):
    _require_deps_or_skip()
    from posthouse.harvest.transcribe import transcribe

    transcript = transcribe(speech_wav)

    full_text_lower = transcript.full_text.lower()
    hits = [kw for kw in KEYWORDS if kw in full_text_lower]
    assert hits, (
        f"none of {KEYWORDS} recovered from transcript: {transcript.full_text!r}"
    )
    # Report exact hit rate rather than requiring all three — Whisper output
    # is not deterministic across machines/model versions (see module
    # docstring); a majority is a meaningful signal, not the whole claim.
    assert len(hits) >= 2, (
        f"only {hits} of {KEYWORDS} recovered: {transcript.full_text!r}"
    )


def test_transcribe_phrase_structure_invariants(speech_wav: Path):
    _require_deps_or_skip()
    from posthouse.harvest.transcribe import transcribe

    transcript = transcribe(speech_wav)

    assert transcript.phrases, "transcribe() produced zero phrases for real speech"

    prev_end = None
    for i, phrase in enumerate(transcript.phrases):
        assert phrase.id == i, f"phrase ids not sequential: {[p.id for p in transcript.phrases]}"
        assert phrase.text.strip(), f"phrase {phrase.id} has empty text"
        assert phrase.start <= phrase.end, f"phrase {phrase.id} start > end"
        if prev_end is not None:
            assert phrase.start >= prev_end, (
                f"phrase {phrase.id} starts ({phrase.start}) before the "
                f"previous phrase ended ({prev_end}) — overlapping phrases"
            )
        prev_end = phrase.end


def test_transcript_round_trips_to_precut_on_disk_shape(speech_wav: Path, tmp_path: Path):
    _require_deps_or_skip()
    from posthouse.harvest.transcribe import (
        Transcript,
        save_transcript,
        transcribe,
        transcript_to_json,
    )

    transcript = transcribe(speech_wav)
    json_text = transcript_to_json(transcript)
    assert '"phrases"' in json_text and '"source_path"' in json_text

    out_path = tmp_path / f"{speech_wav.stem}.json"
    returned = save_transcript(transcript, out_path)
    assert returned == out_path
    assert out_path.exists()

    reloaded = Transcript.load(out_path)
    assert [p.text for p in reloaded.phrases] == [p.text for p in transcript.phrases]
    assert reloaded.duration == transcript.duration
