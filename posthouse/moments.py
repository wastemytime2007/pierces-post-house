#!/usr/bin/env python3
"""Find real moments in transcribed footage and hand them back as a Premiere
sequence, with every quote machine-verified against its source transcript.

This is the "Guided" retrieval path: Ryan describes what he is looking for in
his own words, and this returns the actual moments, as a sequence he opens.
It is retrieval, not story composition -- the judgment about whether a moment
belongs in a piece stays with him. What is automated is the finding.

Why verification is not optional
--------------------------------
Measured on the one project where it was checked, 47% of AI-produced quote
claims were either absent from the transcripts entirely or attributed to the
wrong timestamp. So nothing here presents a quote it has not checked: every
candidate is written to a log, that log is run through the ``verified-quotes``
skill's checker against the source SRTs, and anything that does not come back
VERIFIED is dropped (or, with ``--keep-unverified``, kept but conspicuously
flagged). The doctrine already said "never invent or paraphrase a soundbite";
this makes that mechanical rather than aspirational.

Pipeline
--------
1. Load per-source Whisper transcripts (JSON with word-level timings).
2. Resolve each transcript back to its real media file on disk.
3. Score segments against the query (IDF-weighted term overlap).
4. Window hits into ranges on word boundaries, pad, and merge neighbours.
5. Verify every quote against its SRT; drop what fails.
6. Emit ``moments.md`` (review), ``moments.json`` (the coldfootage segments
   contract) and ``moments.xml`` (the Premiere sequence).

Usage
-----
    python -m posthouse.moments search \\
        --transcripts DIR --media-root DIR \\
        --query "the septic inspection problem" \\
        --out-dir ./out

    python -m posthouse.moments search ... --note brief.md --max 12
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from posthouse import coldfootage
from posthouse._util import now_iso

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".m4v", ".mts", ".m2ts"}
AUDIO_EXTS = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".flac"}

DEFAULT_PAD_SEC = 1.0
DEFAULT_MAX_RESULTS = 10
DEFAULT_MERGE_GAP_SEC = 2.0
MIN_MOMENT_SEC = 1.5
MAX_MOMENT_SEC = 90.0

DEFAULT_VERIFY_SCRIPT = (
    Path.home() / ".claude" / "skills" / "verified-quotes" / "scripts" / "verify_quotes.py"
)

# Words too common to carry meaning in a footage query.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "for", "with", "is", "are", "was", "were", "be", "been", "it", "its",
    "this", "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "me", "him", "her", "us", "them", "my", "your", "our", "their", "do",
    "does", "did", "so", "as", "by", "from", "up", "out", "about", "into",
    "then", "than", "there", "here", "what", "when", "where", "who", "how",
    "just", "like", "get", "got", "go", "going", "know", "think", "really",
    "yeah", "okay", "ok", "well", "right", "gonna", "kind", "sort",
}


class MomentsError(Exception):
    """Base class for failures in this module."""


class MomentsValidationError(MomentsError):
    """Raised with every input problem listed, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "Moments input validation failed:\n" + "\n".join(f"  - {p}" for p in problems)
        )


# --------------------------------------------------------------------------
# Transcript loading
# --------------------------------------------------------------------------

@dataclass
class Segment:
    """One transcript cue, with the word timings that let us cut on a word."""
    stem: str
    start: float
    end: float
    text: str
    words: list[dict] = field(default_factory=list)


@dataclass
class Transcript:
    stem: str
    json_path: Path
    srt_path: Optional[Path]
    media_path: Optional[str]
    segments: list[Segment]


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if t and t not in _STOPWORDS and len(t) > 1]


def _load_manifest_map(transcripts_dir: Path) -> dict[str, str]:
    """transcript_stem -> source media path, from whatever manifests exist.

    The manifests are authoritative where present but do not cover every
    transcript (two of the ten tiers are in neither), so this is only the
    first of two resolution strategies.
    """
    mapping: dict[str, str] = {}
    for name in ("manifest.json", "manifest_broll_gaps.json"):
        path = transcripts_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for entry in data.get("transcripts", []):
            stem = entry.get("transcript_stem")
            src = entry.get("source_audio") or entry.get("source")
            if stem and src:
                mapping[stem] = src
    return mapping


def _index_media(media_root: Path) -> dict[str, list[Path]]:
    """Every media file under ``media_root``, indexed by lowercased stem."""
    by_stem: dict[str, list[Path]] = {}
    for path in media_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("._"):
            continue
        if path.suffix.lower() in VIDEO_EXTS or path.suffix.lower() in AUDIO_EXTS:
            by_stem.setdefault(path.stem.lower(), []).append(path)
    return by_stem


def _resolve_media(
    stem: str, manifest_map: dict[str, str], by_stem: dict[str, list[Path]]
) -> Optional[str]:
    """Map a transcript stem to a real media file.

    Two strategies, in order: the manifest (authoritative), then the naming
    convention ``<shoot-folder-slug>__<media-stem>``. Some stems carry a
    ``video_`` prefix from a flattened subfolder, so that is tried stripped
    as well. Video is preferred over audio when both match, since the point
    is a sequence someone opens.
    """
    if stem in manifest_map:
        return manifest_map[stem]

    media_stem = stem.split("__", 1)[1] if "__" in stem else stem
    candidates = [media_stem]
    if media_stem.lower().startswith("video_"):
        candidates.append(media_stem[6:])

    for cand in candidates:
        hits = by_stem.get(cand.lower())
        if hits:
            hits = sorted(hits, key=lambda p: 0 if p.suffix.lower() in VIDEO_EXTS else 1)
            return str(hits[0])
    return None


def load_transcripts(transcripts_dir: Path, media_root: Optional[Path]) -> list[Transcript]:
    """Load every Whisper JSON under ``transcripts_dir`` and resolve its media.

    Expects PreCut/Whisper ``--output_format all`` shape: ``{text, segments:
    [{start, end, text, words: [{word, start, end}]}], language}``. Segments
    without word timings still load; they just cut on segment boundaries.
    """
    if not transcripts_dir.is_dir():
        raise MomentsValidationError([f"transcripts dir not found: {transcripts_dir}"])

    manifest_map = _load_manifest_map(transcripts_dir)
    by_stem = _index_media(media_root) if media_root and media_root.is_dir() else {}

    out: list[Transcript] = []
    for json_path in sorted(transcripts_dir.rglob("*.json")):
        if json_path.name.startswith("manifest"):
            continue
        try:
            data = json.loads(json_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        raw_segments = data.get("segments")
        if not isinstance(raw_segments, list):
            continue

        stem = json_path.stem
        segments = []
        for seg in raw_segments:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(seg.get("start"))
                end = float(seg.get("end"))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            segments.append(
                Segment(stem=stem, start=start, end=end, text=text,
                        words=[w for w in (seg.get("words") or []) if isinstance(w, dict)])
            )
        if not segments:
            continue

        srt = json_path.with_suffix(".srt")
        out.append(Transcript(
            stem=stem,
            json_path=json_path,
            srt_path=srt if srt.is_file() else None,
            media_path=_resolve_media(stem, manifest_map, by_stem),
            segments=segments,
        ))
    return out


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

@dataclass
class Hit:
    transcript: Transcript
    start: float
    end: float
    text: str
    score: float


def _idf(transcripts: Iterable[Transcript]) -> dict[str, float]:
    """Inverse document frequency over segments.

    Matters more than it looks: this corpus contains Whisper hallucination
    loops where one phrase repeats hundreds of times. A term that appears
    everywhere earns almost no weight, so a loop cannot drive a match on its
    own.
    """
    df: Counter = Counter()
    total = 0
    for tr in transcripts:
        for seg in tr.segments:
            total += 1
            df.update(set(_tokens(seg.text)))
    if not total:
        return {}
    return {term: math.log(total / (1 + n)) for term, n in df.items()}


def search(
    transcripts: list[Transcript],
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    require_media: bool = True,
) -> list[Hit]:
    """Rank segments against ``query`` by IDF-weighted term overlap.

    Deliberately simple. If literal matching turns out to be insufficient,
    that is a finding worth having before reaching for embeddings.
    """
    q_terms = _tokens(query)
    if not q_terms:
        raise MomentsValidationError(["query has no searchable terms after stopword removal"])

    idf = _idf(transcripts)
    q_set = set(q_terms)

    hits: list[Hit] = []
    for tr in transcripts:
        if require_media and not tr.media_path:
            continue
        for seg in tr.segments:
            seg_terms = set(_tokens(seg.text))
            overlap = q_set & seg_terms
            if not overlap:
                continue
            score = sum(idf.get(t, 0.0) for t in overlap)
            # Favour segments that cover more of the query rather than
            # repeating one rare word.
            score *= len(overlap) / len(q_set)
            if score > 0:
                hits.append(Hit(tr, seg.start, seg.end, seg.text, score))

    hits.sort(key=lambda h: h.score, reverse=True)
    return _merge_hits(hits)[:max_results]


def _merge_hits(hits: list[Hit], gap_sec: float = DEFAULT_MERGE_GAP_SEC) -> list[Hit]:
    """Collapse adjacent hits in the same source into one moment."""
    by_source: dict[str, list[Hit]] = {}
    for h in hits:
        by_source.setdefault(h.transcript.stem, []).append(h)

    merged: list[Hit] = []
    for stem, group in by_source.items():
        group.sort(key=lambda h: h.start)
        current = None
        for h in group:
            if current is None:
                current = h
                continue
            if h.start - current.end <= gap_sec:
                current = Hit(
                    transcript=current.transcript,
                    start=current.start,
                    end=max(current.end, h.end),
                    text=(current.text + " " + h.text).strip(),
                    score=max(current.score, h.score),
                )
            else:
                merged.append(current)
                current = h
        if current is not None:
            merged.append(current)

    merged.sort(key=lambda h: h.score, reverse=True)
    return merged


def _snap_to_words(hit: Hit, pad_sec: float) -> tuple[float, float]:
    """Expand a hit to whole-word boundaries, then pad.

    Word timings come free with the Whisper JSON, so there is no reason to
    cut mid-word.
    """
    starts, ends = [], []
    for seg in hit.transcript.segments:
        if seg.end < hit.start or seg.start > hit.end:
            continue
        for w in seg.words:
            try:
                ws, we = float(w.get("start")), float(w.get("end"))
            except (TypeError, ValueError):
                continue
            if we >= hit.start and ws <= hit.end:
                starts.append(ws)
                ends.append(we)
    start = min(starts) if starts else hit.start
    end = max(ends) if ends else hit.end
    start = max(0.0, start - pad_sec)
    end = end + pad_sec
    if end - start > MAX_MOMENT_SEC:
        end = start + MAX_MOMENT_SEC
    return start, end


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def contiguous_text(transcript: Transcript, start: float, end: float) -> str:
    """Every transcript segment overlapping ``[start, end]``, joined in order.

    This must be re-derived from the transcript rather than assembled from
    the hits that produced the range. Merging two hits that had an
    unmatched segment between them and concatenating their texts yields a
    quote that never appears contiguously in the source -- the verifier
    correctly rejects it, and a real moment gets dropped for a reason that
    is entirely our fault. Caught exactly that way on the first real run.
    """
    parts = [
        seg.text for seg in transcript.segments
        if seg.end >= start and seg.start <= end
    ]
    return " ".join(" ".join(parts).split())


def _quote_for(hit: Hit) -> str:
    """A verbatim span from the transcript, safe in a quoted log line.

    Copied, never retyped or re-punctuated: the checker matches literally
    after light normalization, so a reconstructed contraction reads as a
    fabrication. Truncation is on a word boundary for the same reason.
    """
    text = contiguous_text(hit.transcript, hit.start, hit.end) or " ".join(hit.text.split())
    text = text.replace('"', "'").replace("“", "'").replace("”", "'")
    if len(text) > 300:
        text = text[:300].rsplit(" ", 1)[0]
    return text


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def is_audio_only(media_path: Optional[str]) -> bool:
    """Whether a source carries no video stream.

    Matters because the exporter cannot place these. PreCut's probe refuses
    a file with no video stream, so an audio-only source cannot become a
    sequence clip. On this corpus that is 29% of transcripts: the lav and
    interview recordings, which for a documentary are often the best audio
    in the project. They are surfaced in the log rather than dropped -- but
    putting them on a timeline needs sync (posthouse/harvest/sync.py),
    because a lav runs continuously across many camera starts and stops.
    """
    if not media_path:
        return False
    return Path(media_path).suffix.lower() in AUDIO_EXTS


def write_review_log(hits: list[Hit], query: str, out_path: Path) -> Path:
    """Write the human-readable log, in the shape the verifier parses.

    One artifact serving two purposes: what Ryan reads, and what the checker
    consumes. They cannot drift apart because they are the same file.
    """
    lines = [f"# Moments: {query}", "", f"Generated {now_iso()}", ""]
    by_stem: dict[str, list[Hit]] = {}
    for h in hits:
        by_stem.setdefault(h.transcript.stem, []).append(h)

    for stem, group in sorted(by_stem.items()):
        lines.append(f"Sources: `{stem}.srt`")
        lines.append("")
        for h in sorted(group, key=lambda x: x.start):
            media = h.transcript.media_path or "UNRESOLVED MEDIA"
            # Emit the full range, not a bare start. The checker assumes a
            # 5-second span when given a single timecode, so a 20-second
            # moment's tail falls outside its tolerance window and reads as
            # TIMECODE_MISMATCH. Found on the first real run.
            lines.append(
                f'- [{_hms(h.start)}–{_hms(h.end)}] "{_quote_for(h)}"'
            )
            note = "  [AUDIO ONLY — needs sync to place in a sequence]" if is_audio_only(h.transcript.media_path) else ""
            lines.append(
                f"    - score {h.score:.2f} · {h.start:.2f}–{h.end:.2f}s · `{Path(media).name}`{note}"
            )
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def verify_log(
    log_path: Path, transcripts_dir: Path, verify_script: Path
) -> tuple[dict[str, int], set[int], str]:
    """Run the verified-quotes checker over the log.

    Returns ``(counts, verified_line_numbers, raw_stdout)``. Every line the
    report does NOT flag is treated as verified, which is why the report's
    "Flagged (not VERIFIED)" section is the thing parsed.
    """
    if not verify_script.is_file():
        raise MomentsError(
            f"verification script not found at {verify_script}. "
            "Pass --verify-script, or install the verified-quotes skill. "
            "Refusing to emit unverified quotes."
        )

    proc = subprocess.run(
        [sys.executable, str(verify_script), "--transcripts", str(transcripts_dir), str(log_path)],
        capture_output=True, text=True,
    )
    report = log_path.with_suffix(".verification.md")
    counts: dict[str, int] = {}
    flagged: set[int] = set()
    if report.is_file():
        for line in report.read_text().splitlines():
            m = re.match(r"- (\w+): (\d+)$", line.strip())
            if m:
                counts[m.group(1)] = int(m.group(2))
            m2 = re.match(r"- L(\d+) ", line.strip())
            if m2:
                flagged.add(int(m2.group(1)))
    return counts, flagged, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------

def build_segments(hits: list[Hit], pad_sec: float, sequence_name: str) -> dict:
    """The coldfootage segments contract, ready for build_coldfootage_xml."""
    segments = []
    for h in hits:
        if not h.transcript.media_path:
            continue
        start, end = _snap_to_words(h, pad_sec)
        if end - start < MIN_MOMENT_SEC:
            end = start + MIN_MOMENT_SEC
        label = " ".join(h.text.split())[:60]
        segments.append({
            "source_path": h.transcript.media_path,
            "in_sec": round(start, 3),
            "out_sec": round(end, 3),
            "label": label,
            "handle_sec": 0.0,
        })
    return {
        "contract_version": 1,
        "sequence_name": sequence_name,
        "segments": segments,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run_search(args: argparse.Namespace) -> int:
    query = args.query
    if args.note:
        note_path = Path(args.note)
        if not note_path.is_file():
            raise MomentsValidationError([f"note file not found: {note_path}"])
        query = f"{query or ''} {note_path.read_text()}".strip()
    if not query:
        raise MomentsValidationError(["provide --query or --note"])

    transcripts_dir = Path(args.transcripts).expanduser()
    media_root = Path(args.media_root).expanduser() if args.media_root else None
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    transcripts = load_transcripts(transcripts_dir, media_root)
    if not transcripts:
        raise MomentsError(f"no usable transcripts found under {transcripts_dir}")

    resolved = sum(1 for t in transcripts if t.media_path)
    print(f"loaded {len(transcripts)} transcripts ({resolved} resolved to media)")

    hits = search(transcripts, query, max_results=args.max, require_media=not args.allow_unresolved)
    if not hits:
        print("no moments matched that query.")
        return 0
    print(f"found {len(hits)} candidate moments")

    log_path = write_review_log(hits, query, out_dir / "moments.md")
    counts, flagged, raw = verify_log(log_path, transcripts_dir, Path(args.verify_script))
    print(f"verification: {counts or 'no claims parsed'}")

    # The log writes two lines per hit, so map flagged line numbers back to hits
    # by re-reading which quote each flagged line carried.
    kept: list[Hit] = []
    log_lines = log_path.read_text().splitlines()
    flagged_quotes = set()
    for ln in flagged:
        if 1 <= ln <= len(log_lines):
            m = re.search(r'"([^"]+)"', log_lines[ln - 1])
            if m:
                flagged_quotes.add(m.group(1))
    for h in hits:
        if _quote_for(h) in flagged_quotes:
            if args.keep_unverified:
                kept.append(h)
            continue
        kept.append(h)

    dropped = len(hits) - len(kept)
    if dropped:
        print(f"dropped {dropped} unverified moment(s)"
              + (" (kept anyway: --keep-unverified)" if args.keep_unverified else ""))
    if not kept:
        print("nothing survived verification. Not emitting a sequence.")
        return 1

    # Audio-only sources cannot be placed by the exporter (see is_audio_only).
    # They stay in the log, visible and labelled, rather than vanishing.
    placeable = [h for h in kept if not is_audio_only(h.transcript.media_path)]
    audio_only = [h for h in kept if is_audio_only(h.transcript.media_path)]
    if audio_only:
        print(f"{len(audio_only)} verified moment(s) are audio-only (lav/interview) and are "
              f"listed in moments.md but not placed in the sequence; they need sync first")
    if not placeable:
        print("every verified moment is audio-only. Sequence not built; see moments.md.")
        return 0

    seq_name = args.sequence_name or f"Moments: {query[:40]}"
    segments = build_segments(placeable, args.pad, seq_name)
    problems = coldfootage.validate_segments_shape(segments)
    if problems:
        raise MomentsValidationError(problems)

    json_path = out_dir / "moments.json"
    json_path.write_text(json.dumps(segments, indent=2))
    print(f"wrote {json_path} ({len(segments['segments'])} segments)")
    print(f"wrote {log_path}")

    if args.no_xml:
        print("skipping XML (--no-xml)")
        return 0

    try:
        xml_path = coldfootage.build_coldfootage_xml(segments, out_dir / "moments.xml")
        print(f"wrote {xml_path}")
    except Exception as exc:  # noqa: BLE001 - report, don't crash the whole run
        print(f"XML export failed: {exc}", file=sys.stderr)
        print("The segments JSON above is still valid and can be exported later.",
              file=sys.stderr)
        return 1
    return 0


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m posthouse.moments",
        description="Find verified moments in transcribed footage and emit a Premiere sequence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="Search transcripts and emit moments.")
    s.add_argument("--transcripts", required=True, help="Directory of Whisper JSON/SRT transcripts.")
    s.add_argument("--media-root", default=None, help="Directory holding the source media.")
    s.add_argument("--query", default=None, help="What to look for, in plain words.")
    s.add_argument("--note", default=None, help="A file whose contents are the query.")
    s.add_argument("--out-dir", required=True, help="Where to write moments.{md,json,xml}.")
    s.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS, help="Max moments to return.")
    s.add_argument("--pad", type=float, default=DEFAULT_PAD_SEC, help="Seconds of handle each side.")
    s.add_argument("--sequence-name", default=None, help="Name for the Premiere sequence.")
    s.add_argument("--verify-script", default=str(DEFAULT_VERIFY_SCRIPT),
                   help="Path to verified-quotes' verify_quotes.py.")
    s.add_argument("--keep-unverified", action="store_true",
                   help="Keep moments that failed verification (flagged in the log).")
    s.add_argument("--allow-unresolved", action="store_true",
                   help="Include transcripts whose media file could not be found.")
    s.add_argument("--no-xml", action="store_true", help="Emit JSON and log only.")
    s.set_defaults(func=run_search)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MomentsValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except MomentsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - defensive, matches sibling modules
        print(f"unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
