"""Auto-include rules — files the user wants included in every export.

A user-preference feature: editors typically have a stock SFX library,
brand logos, and other assets they re-use in every project. Instead of
manually adding these to each export, they configure the rules ONCE in
settings, and every subsequent export silently includes them in bins
of the user's choosing.

A rule looks like:

    {
        "id": "uuid-string",
        "type": "file" | "folder",
        "source_path": "/Users/.../airhorn.wav",
        "bin_path": "Audio/SFX",
        "file_glob": "*.wav"      // only meaningful for type=folder
    }

Rules persist in the same settings.json as everything else, under the
key "auto_include_rules".

This module:
- Defines the rule shape and validation
- Expands a list of rules into a flat list of (file_path, bin_path)
  tuples for the exporter to consume
- Sniffs file kind by extension so the exporter knows which master-
  clip helper to use

Bin path semantics:
- Slash-delimited path through the bin tree, e.g. "Audio/SFX" or
  "Files/Logos" or "Audio/Music/Royalty Free"
- Intermediate bins are auto-created if they don't exist (the
  exporter's responsibility, not this module's)
- Empty/whitespace-only segments are rejected as invalid
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


# File extensions we know how to handle, mapped to a "kind" the exporter
# uses to pick the right master-clip helper.
#
# Extension lists are deliberately curated to what Premiere actually
# imports via FCP7 XML — adding extensions here only makes sense if
# the master-clip builder for the target kind can produce a <file>
# element Premiere will accept.
#
# Drop 1.0.0-beta.2: added .svg, .bmp, .gif, .webp to images. Premiere
# treats SVG as a still image (sized to project frame on import) so it
# routes through the same overlay/image master path as PNGs.
_AUDIO_EXTS = {".wav", ".mp3", ".aac", ".aif", ".aiff", ".m4a", ".flac"}
_VIDEO_EXTS = {".mov", ".mp4", ".avi", ".mkv", ".mxf", ".m4v"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff",
               ".svg", ".bmp", ".gif", ".webp"}

# Common file types editors might try to add that Premiere CANNOT
# import via FCP7 XML — used to give specific, actionable error
# messages instead of silent drops or generic "unsupported type."
# .cube, .look, .3dl: LUTs (Premiere imports these as Lumetri presets,
# not as project items — they have no FCP7 XML path).
# .pdf, .txt, .rtf, .doc, .docx: documents.
# .ai, .psd: Premiere DOES import these as stills via prproj, but the
# FCP7 XML path is unreliable across CC versions, so we reject them
# explicitly with guidance rather than producing flaky imports.
_UNSUPPORTED_REASONS = {
    ".cube":  "LUT files can't be imported via Premiere XML. "
              "Save it as a Lumetri preset in Premiere instead.",
    ".look":  "LUT files can't be imported via Premiere XML. "
              "Save it as a Lumetri preset in Premiere instead.",
    ".3dl":   "LUT files can't be imported via Premiere XML. "
              "Save it as a Lumetri preset in Premiere instead.",
    ".pdf":   "PDFs aren't importable as Premiere project items.",
    ".txt":   "Text files aren't importable as Premiere project items.",
    ".rtf":   "Text files aren't importable as Premiere project items.",
    ".doc":   "Word docs aren't importable as Premiere project items.",
    ".docx":  "Word docs aren't importable as Premiere project items.",
    ".ai":    "Illustrator files import unreliably via FCP7 XML. "
              "Export as PNG or SVG instead.",
    ".psd":   "Photoshop files import unreliably via FCP7 XML. "
              "Export as PNG instead.",
}


@dataclass
class AutoIncludeRule:
    """One configured auto-include rule."""
    id: str
    type: str               # "file" or "folder"
    source_path: str        # absolute path to file or folder
    bin_path: str           # slash-delimited bin path, e.g. "Audio/SFX"
    file_glob: str = ""     # only used when type="folder"; empty means "*"

    @classmethod
    def from_dict(cls, d: dict) -> "AutoIncludeRule":
        return cls(
            id=str(d.get("id", "")),
            type=str(d.get("type", "file")),
            source_path=str(d.get("source_path", "")),
            bin_path=str(d.get("bin_path", "")),
            file_glob=str(d.get("file_glob", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "source_path": self.source_path,
            "bin_path": self.bin_path,
            "file_glob": self.file_glob,
        }


def kind_for_path(path: Path) -> str | None:
    """Return 'audio', 'video', 'image', or None for unknown."""
    ext = path.suffix.lower()
    if ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _IMAGE_EXTS:
        return "image"
    return None


def unsupported_reason(path: Path) -> str | None:
    """Return a human-readable explanation if `path`'s extension is
    one of the file types editors commonly try to add but Premiere
    can't import via FCP7 XML — None if the type is supported OR
    if it's an unknown extension we don't have a specific message for.

    Used by the UI to surface specific, actionable errors at config
    time ("Save as Lumetri preset instead") rather than letting the
    rule silently drop at export time.
    """
    ext = path.suffix.lower()
    return _UNSUPPORTED_REASONS.get(ext)


def validate_rule(rule: AutoIncludeRule) -> str | None:
    """Return None if the rule is valid, else an error message string.

    Validation is intentionally LIBERAL — we don't check whether files
    exist on disk here, since rules can be saved at config time when
    paths are valid and only consulted later (when paths might be moved
    or unmounted). Missing files are warn-and-skip at export time.
    """
    if rule.type not in ("file", "folder"):
        return f"type must be 'file' or 'folder', got {rule.type!r}"
    if not rule.source_path.strip():
        return "source_path is required"
    if not rule.bin_path.strip():
        return "bin_path is required"
    # Bin path: each segment must be non-empty
    segments = [s for s in rule.bin_path.split("/")]
    if not segments or any(not s.strip() for s in segments):
        return (f"bin_path {rule.bin_path!r} has empty segments — "
                f"use 'Audio/Music' not 'Audio//Music' or '/Audio/Music'")
    return None


def normalize_bin_path(bin_path: str) -> list[str]:
    """Split a bin_path into a list of trimmed segments.

    'Audio/SFX' -> ['Audio', 'SFX']
    'Files/Logos' -> ['Files', 'Logos']
    'Audio/Music/Royalty Free' -> ['Audio', 'Music', 'Royalty Free']
    """
    return [seg.strip() for seg in bin_path.split("/") if seg.strip()]


def expand_rule(rule: AutoIncludeRule) -> tuple[
    list[tuple[Path, list[str], str]],
    list[str],
]:
    """Resolve a rule into (file_tuples, warnings).

    file_tuples: list of (file_path, bin_path_segments, kind) for every
        file that should be included.

    warnings: list of human-readable warning strings explaining why
        any specific file was skipped — empty if everything resolved
        cleanly. Used by the exporter to log to the user-facing log
        panel (not just stderr).

    For type='file': file_tuples has 0 or 1 entry. If empty, warnings
        explains why (file missing, unsupported extension with reason).
    For type='folder': one entry per matching file, plus warnings for
        any files in the folder we couldn't import.
        Glob defaults to '*' if rule.file_glob is empty.

    Drop 1.0.0-beta.2: previously this function silently dropped any
    file with an unrecognized extension. Now it produces a specific
    warning explaining WHY (e.g., "LUT files can't be imported via
    Premiere XML — save as Lumetri preset instead"). This makes the
    feature discoverable when a user adds a .cube or .svg and wonders
    why nothing showed up.
    """
    err = validate_rule(rule)
    if err is not None:
        return [], [f"Rule {rule.id!r} invalid: {err}"]

    bin_segments = normalize_bin_path(rule.bin_path)
    src = Path(rule.source_path).expanduser()

    files: list[tuple[Path, list[str], str]] = []
    warnings: list[str] = []

    if rule.type == "file":
        if not src.exists():
            warnings.append(f"File not found: {src}")
            return files, warnings
        if not src.is_file():
            warnings.append(f"Not a file: {src}")
            return files, warnings

        kind = kind_for_path(src)
        if kind is None:
            reason = unsupported_reason(src)
            if reason:
                warnings.append(f"{src.name}: {reason}")
            else:
                warnings.append(
                    f"{src.name}: extension {src.suffix!r} isn't supported "
                    f"(audio, video, or image only)."
                )
            return files, warnings
        files.append((src.resolve(), bin_segments, kind))
        return files, warnings

    # type == "folder"
    if not src.exists():
        warnings.append(f"Folder not found: {src}")
        return files, warnings
    if not src.is_dir():
        warnings.append(f"Not a folder: {src}")
        return files, warnings

    glob = rule.file_glob.strip() or "*"
    skipped_extensions: dict[str, int] = {}  # collect, not per-file
    for child in sorted(src.glob(glob)):
        if not child.is_file():
            continue
        kind = kind_for_path(child)
        if kind is None:
            ext = child.suffix.lower() or "(no extension)"
            skipped_extensions[ext] = skipped_extensions.get(ext, 0) + 1
            continue
        files.append((child.resolve(), bin_segments, kind))

    # Summarize skipped files in folder by extension — one line per
    # extension to keep the log compact even for big folders.
    for ext, count in sorted(skipped_extensions.items()):
        reason = _UNSUPPORTED_REASONS.get(ext)
        if reason:
            warnings.append(
                f"{src.name}: {count} {ext} file(s) skipped — {reason}"
            )
        else:
            warnings.append(
                f"{src.name}: {count} {ext} file(s) skipped (unsupported "
                f"extension; expected audio, video, or image)."
            )

    return files, warnings


def expand_rules(rules: list[AutoIncludeRule]) -> tuple[
    list[tuple[Path, list[str], str]],
    list[str],
]:
    """Expand a list of rules to (file_tuples, warnings).

    Deduplicates by file path — if the same file matches multiple rules,
    only the first (in rule order) wins. This prevents the exporter from
    creating duplicate masters when a rule overlaps another.

    Drop 1.0.0-beta.2: now also returns aggregated warnings from each
    rule. Caller is expected to log them to the user-facing export log.
    """
    seen: set[str] = set()
    result: list[tuple[Path, list[str], str]] = []
    all_warnings: list[str] = []
    for rule in rules:
        files, warnings = expand_rule(rule)
        all_warnings.extend(warnings)
        for path, segs, kind in files:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append((path, segs, kind))
    return result, all_warnings
