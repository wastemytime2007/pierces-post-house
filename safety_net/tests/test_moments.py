"""Tests for posthouse.moments -- transcript retrieval to verified moments.

Hermetic: transcripts are hand-built JSON written to tmp_path, no ffmpeg, no
real footage, no PreCut. The two regression tests at the bottom cover bugs
found on the first real run against the Runnells corpus, both of which produced
plausible-looking output that the verification step correctly rejected.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from posthouse.moments import (
    Hit,
    MomentsValidationError,
    Segment,
    Transcript,
    _merge_hits,
    _resolve_media,
    _snap_to_words,
    _tokens,
    build_segments,
    contiguous_text,
    is_audio_only,
    load_transcripts,
    search,
    write_review_log,
)
from posthouse import coldfootage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _words(text: str, start: float, per: float = 0.5) -> list[dict]:
    out = []
    t = start
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "end": round(t + per, 3)})
        t += per
    return out


def _write_transcript(dirpath: Path, stem: str, segments: list[tuple[float, float, str]]) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    data = {
        "text": " ".join(s[2] for s in segments),
        "language": "en",
        "segments": [
            {"id": i, "start": s, "end": e, "text": txt, "words": _words(txt, s)}
            for i, (s, e, txt) in enumerate(segments)
        ],
    }
    path = dirpath / f"{stem}.json"
    path.write_text(json.dumps(data))
    return path


def _fake_transcript(stem: str = "shoot__CLIP_01", media: str | None = "/fake/CLIP_01.MP4") -> Transcript:
    segs = [
        Segment(stem, 0.0, 5.0, "The septic inspection failed.", _words("The septic inspection failed.", 0.0)),
        Segment(stem, 5.0, 9.0, "We had to replace the lateral.", _words("We had to replace the lateral.", 5.0)),
        Segment(stem, 30.0, 34.0, "Completely unrelated cabinet talk.", _words("Completely unrelated cabinet talk.", 30.0)),
    ]
    return Transcript(stem=stem, json_path=Path(f"/fake/{stem}.json"), srt_path=None,
                      media_path=media, segments=segs)


# ---------------------------------------------------------------------------
# Tokenizing / search
# ---------------------------------------------------------------------------

def test_tokens_drop_stopwords_and_punctuation():
    toks = _tokens("The septic inspection, and the WATER source!")
    assert "septic" in toks and "inspection" in toks and "water" in toks
    assert "the" not in toks and "and" not in toks


def test_search_ranks_relevant_segment_first():
    trs = [_fake_transcript()]
    hits = search(trs, "septic inspection", max_results=5)
    assert hits, "expected at least one hit"
    assert hits[0].start < 10.0, "the septic segment should outrank the cabinet segment"


def test_search_rejects_query_with_only_stopwords():
    with pytest.raises(MomentsValidationError):
        search([_fake_transcript()], "the and of it")


def test_search_skips_transcripts_without_media_when_required():
    trs = [_fake_transcript(media=None)]
    assert search(trs, "septic inspection", require_media=True) == []
    assert search(trs, "septic inspection", require_media=False)


def test_idf_prevents_a_repeated_phrase_from_dominating():
    """A hallucination loop must not be able to drive a match.

    Whisper repeats one phrase hundreds of times on bad audio (91% of one real
    file). A term appearing in every segment earns near-zero IDF, so the loop
    cannot outrank a genuine, rare match. Measured on the real corpus: zero of
    40 returned moments overlapped a loop.
    """
    loop_segs = [Segment("loop__A", float(i), float(i) + 1, "We have to go downstairs.",
                         _words("We have to go downstairs.", float(i)))
                 for i in range(200)]
    loop_tr = Transcript("loop__A", Path("/fake/a.json"), None, "/fake/a.MP4", loop_segs)
    real = _fake_transcript("real__B", "/fake/b.MP4")

    hits = search([loop_tr, real], "downstairs septic inspection", max_results=5)
    assert hits[0].transcript.stem == "real__B", (
        "the rare genuine match must outrank a 200x repeated loop phrase"
    )


# ---------------------------------------------------------------------------
# Merging / windowing
# ---------------------------------------------------------------------------

def test_merge_hits_combines_adjacent_and_keeps_distant_separate():
    tr = _fake_transcript()
    hits = [
        Hit(tr, 0.0, 5.0, "a", 1.0),
        Hit(tr, 5.5, 9.0, "b", 1.0),
        Hit(tr, 30.0, 34.0, "c", 1.0),
    ]
    merged = _merge_hits(hits, gap_sec=2.0)
    spans = sorted((round(h.start, 1), round(h.end, 1)) for h in merged)
    assert spans == [(0.0, 9.0), (30.0, 34.0)]


def test_snap_to_words_expands_to_word_boundaries_and_pads():
    tr = _fake_transcript()
    hit = Hit(tr, 1.2, 3.4, "partial", 1.0)
    start, end = _snap_to_words(hit, pad_sec=1.0)
    assert start >= 0.0
    assert end > start


def test_snap_to_words_never_goes_negative():
    tr = _fake_transcript()
    start, _ = _snap_to_words(Hit(tr, 0.0, 2.0, "x", 1.0), pad_sec=5.0)
    assert start == 0.0


# ---------------------------------------------------------------------------
# Media resolution
# ---------------------------------------------------------------------------

def test_resolve_media_prefers_manifest():
    got = _resolve_media("shoot__CLIP_01", {"shoot__CLIP_01": "/from/manifest.mp4"}, {})
    assert got == "/from/manifest.mp4"


def test_resolve_media_falls_back_to_stem_convention():
    by_stem = {"clip_01": [Path("/media/CLIP_01.MP4")]}
    assert _resolve_media("shoot__CLIP_01", {}, by_stem) == "/media/CLIP_01.MP4"


def test_resolve_media_strips_video_prefix():
    """Some stems carry a `video_` prefix from a flattened subfolder.

    52 of 270 real transcripts were unresolvable until this was handled.
    """
    by_stem = {"dji_0001": [Path("/media/DJI_0001.MP4")]}
    assert _resolve_media("shoot__video_DJI_0001", {}, by_stem) == "/media/DJI_0001.MP4"


def test_resolve_media_prefers_video_over_audio():
    by_stem = {"clip_01": [Path("/media/CLIP_01.WAV"), Path("/media/CLIP_01.MP4")]}
    assert _resolve_media("s__CLIP_01", {}, by_stem).endswith(".MP4")


def test_is_audio_only():
    assert is_audio_only("/x/a.WAV") and is_audio_only("/x/a.mp3")
    assert not is_audio_only("/x/a.MP4")
    assert not is_audio_only(None)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_load_transcripts_reads_word_timings_and_skips_manifests(tmp_path):
    tdir = tmp_path / "transcripts"
    _write_transcript(tdir, "shoot__CLIP_01", [(0.0, 3.0, "hello there world")])
    (tdir / "manifest.json").write_text(json.dumps({"transcripts": []}))

    trs = load_transcripts(tdir, None)
    assert len(trs) == 1
    assert trs[0].stem == "shoot__CLIP_01"
    assert trs[0].segments[0].words, "word timings must survive loading"


def test_load_transcripts_missing_dir_raises():
    with pytest.raises(MomentsValidationError):
        load_transcripts(Path("/nope/not/here"), None)


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def test_build_segments_matches_the_coldfootage_contract():
    tr = _fake_transcript()
    segs = build_segments([Hit(tr, 0.0, 5.0, "The septic inspection failed.", 2.0)],
                          pad_sec=1.0, sequence_name="Test")
    assert coldfootage.validate_segments_shape(segs) == [], "must satisfy the shared segments contract"
    assert segs["segments"][0]["source_path"] == "/fake/CLIP_01.MP4"


def test_build_segments_omits_sources_that_never_resolved():
    tr = _fake_transcript(media=None)
    segs = build_segments([Hit(tr, 0.0, 5.0, "x", 1.0)], pad_sec=1.0, sequence_name="T")
    assert segs["segments"] == []


# ---------------------------------------------------------------------------
# Regressions from the first real run
# ---------------------------------------------------------------------------

def test_quote_is_contiguous_source_text_not_assembled_fragments(tmp_path):
    """REGRESSION: merged hits must not produce a Frankenstein quote.

    Merging two hits that had an unmatched segment between them and
    concatenating their texts yields a string that never appears contiguously
    in the source. The verifier correctly returned NOT_FOUND, and a real
    moment was dropped for a reason that was entirely our fault. The quote is
    now re-derived from the transcript over the merged range.
    """
    stem = "shoot__CLIP_01"
    segs = [
        Segment(stem, 0.0, 2.0, "First matching part.", _words("First matching part.", 0.0)),
        Segment(stem, 2.0, 4.0, "Intervening unmatched line.", _words("Intervening unmatched line.", 2.0)),
        Segment(stem, 4.0, 6.0, "Second matching part.", _words("Second matching part.", 4.0)),
    ]
    tr = Transcript(stem, Path("/f.json"), None, "/f/CLIP_01.MP4", segs)

    text = contiguous_text(tr, 0.0, 6.0)
    assert "Intervening unmatched line." in text, (
        "the intervening segment must be included, or the quote is not real source text"
    )


def test_review_log_emits_a_full_range_not_a_bare_timecode(tmp_path):
    """REGRESSION: a bare timecode reads as TIMECODE_MISMATCH.

    The verifier assumes a 5-second span when given a single timecode, so a
    20-second moment's tail falls outside its tolerance window and a real
    quote gets flagged. The log now emits `[HH:MM:SS-HH:MM:SS]`.
    """
    tr = _fake_transcript()
    out = write_review_log([Hit(tr, 0.0, 9.0, "The septic inspection failed.", 1.0)],
                           "septic", tmp_path / "moments.md")
    body = out.read_text()
    assert "[00:00:00–00:00:09]" in body, f"expected a full range in the log, got:\n{body}"


def test_review_log_marks_audio_only_sources(tmp_path):
    """Audio-only moments stay visible rather than vanishing.

    The exporter cannot place a file with no video stream (29% of this
    corpus), so they are surfaced and labelled instead of silently dropped.
    """
    tr = _fake_transcript(media="/fake/LAV_01.WAV")
    out = write_review_log([Hit(tr, 0.0, 5.0, "The septic inspection failed.", 1.0)],
                           "septic", tmp_path / "moments.md")
    assert "AUDIO ONLY" in out.read_text()
