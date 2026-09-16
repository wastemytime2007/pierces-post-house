"""Regression guards for the two Whisper decode options that decide whether
a transcript contains the shoot or contains nothing.

Found 2026-09-16 on the Runnells tiling day. PreCut shipped with
``WHISPER_LANGUAGE = None`` (auto-detect) and no anti-loop guard. Whisper
picks the language from the first 30 seconds of audio; Osmo A-roll opens on
tool noise and room tone about as often as it opens on speech. When that
window has no clear English in it the ENTIRE file decodes as whatever was
guessed.

What that looked like on real footage --- DJI_20260630093329_0003_D, 235
seconds of two people grouting tile and arguing about whether to wear gloves:

    language=None   ->  detected "ja", 8 segments, all 8 the identical string
                        "JR東日本E233系電車" (a Japanese train announcement).
                        Zero words of the actual conversation.
    language="en"   ->  33 segments of the real conversation.

The same corruption is sitting on disk in projects that were already
"successfully" ingested: "How to remove wallpaper" _0001_D is `ja`, and four
files in the "new" project are `nn` (Norwegian Nynorsk). This was previously
mis-diagnosed as "the Runnells transcripts are 28-46% hallucinated," i.e. as a
property of the footage. It was a setting.

These tests are hermetic --- they assert on the options handed to Whisper, not
on decode output, so they run anywhere without torch. The tier-2 test in
test_transcribe.py covers real decoding.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# The fork loader now lives in conftest.py -- this file had its own copy
# until the same ambiguity bit a second test module on 2026-09-16.
from conftest import load_fork_module as _load_fork_module


def test_whisper_language_is_pinned_not_autodetected():
    """Auto-detect is the bug. If this ever goes back to None, transcripts
    silently turn into another language's hallucinations and every stage
    downstream --- tagging, fragment extraction, the architect --- reads that
    as the truth of what was said on set."""
    config = _load_fork_module("config")

    assert config.WHISPER_LANGUAGE == "en", (
        "WHISPER_LANGUAGE must stay pinned. None means Whisper guesses from "
        "the first 30s, which on Osmo A-roll is frequently tool noise -- and "
        "one bad guess corrupts the whole file. See this module's docstring "
        "for the measured Japanese-train-announcement case."
    )


def test_transcribe_disables_previous_text_conditioning():
    """Whisper feeds each window's decoded text in as the next window's prompt.
    On sparse audio that turns one bad guess into a loop that eats the rest of
    the file. Asserted on the call, not the output, so this stays hermetic."""
    T = _load_fork_module("transcriber")

    captured = {}

    class FakeModel:
        def transcribe(self, path, **opts):
            captured.update(opts)
            return {"segments": [], "language": "en", "duration": 0.0}

    tr = T.Transcriber.__new__(T.Transcriber)
    tr.model_name = "base"
    tr.device = "cpu"
    tr._model = FakeModel()

    tr.transcribe(Path("/nonexistent/probe.mp4"))

    assert captured.get("condition_on_previous_text") is False, (
        "condition_on_previous_text must be False; leaving it on is what "
        "degraded 'Now with the gloves on...' into 'the right on the left "
        "and the left and left and whatever' on the tiling footage."
    )
    assert captured.get("word_timestamps") is True, (
        "word timestamps are load-bearing -- chunk_into_phrases and every "
        "range the architect emits are built from them."
    )
    assert captured.get("language") == "en", (
        "the pinned language must actually reach Whisper, not just sit in "
        "config -- transcribe()'s default argument is the wiring."
    )
    assert "temperature" not in captured, (
        "temperature must NOT be pinned. Pinning it to 0 disables Whisper's "
        "fallback ladder, and that ladder is the guard against repetition "
        "loops. Pinning it was tried on 2026-09-16 and reverted: it was "
        "justified on one shoot (tiling) and regressed another (wallpaper) "
        "into 'Go on the truck.' x11 and \"I'll give him a fall.\" x14. "
        "Measured on wallpaper _0004_D -- small+temperature=0: 44 duplicate "
        "segments; small+ladder+seed: 14. Determinism comes from seeding, "
        "not from removing the guard."
    )


def test_harvest_wrapper_inherits_the_same_pinned_language():
    """posthouse.harvest.transcribe is a second door onto the same Whisper.

    Asserted as WIRING, not as a value. Which `precut_pipeline` this module
    resolves depends on PRECUT_ROOT and sys.path order, so pinning the default
    to the literal "en" makes the test pass or fail on how the suite was
    invoked rather than on whether the code is right (it did exactly that on
    first write). What must hold in every environment is that harvest
    *inherits* the setting instead of carrying its own -- then fixing PreCut's
    config fixes both doors, which is what actually happened here.
    """
    inspect = pytest.importorskip("inspect")
    try:
        from posthouse.harvest import transcribe as H
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"posthouse.harvest.transcribe not importable here: {exc}")

    default = inspect.signature(H.transcribe).parameters["language"].default
    assert default is H.WHISPER_LANGUAGE, (
        "harvest.transcribe must default to PreCut's own WHISPER_LANGUAGE, "
        "not a hardcoded language of its own -- otherwise the two doors drift "
        "and a fix to config.py silently misses this one."
    )


def test_decoding_is_seeded_so_reruns_match():
    """Reproducibility is load-bearing -- comparing a generated idea against
    Ryan's finished edit assumes a re-run is comparable to the last one. It
    must not be bought by disabling the loop guard, so it comes from seeding
    the RNG the sampled retries draw on.

    Verified on real audio: two seeded runs of wallpaper _0004_D hash
    identically, an unseeded run does not.
    """
    import inspect

    T = _load_fork_module("transcriber")
    src = inspect.getsource(T.Transcriber.transcribe)
    assert "torch.manual_seed" in src, (
        "transcribe() must seed torch before decoding, or the fallback "
        "ladder's sampled retries make every run different."
    )
    assert isinstance(T._DECODE_SEED, int)
