"""Camera / source-type tag inference from file paths.

Drop 4.38: lightweight heuristic that maps a file's path + filename to
searchable keywords like "drone", "aerial", "mavic", "gopro", "cinema",
etc. Applied at ingest time AND at export time (so existing projects
pick up the tags without needing to re-tag).

Pure pattern matching — no imports from the rest of the pipeline, no ML,
no network. Safe to call from anywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# Substring → tags mapping. Matched case-insensitively against the full
# lowercased path (including the filename). Order matters for precedence:
# specific patterns should come before generic ones when both might
# match (e.g. "osmo action" before "osmo").
_CAMERA_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    # Aerial / drones — highest-priority tags for editor search
    ("mavic",        ("drone", "aerial", "mavic")),
    ("avata",        ("drone", "aerial", "avata", "fpv")),
    ("phantom",      ("drone", "aerial", "phantom")),
    ("inspire",      ("drone", "aerial", "inspire")),
    ("air 2",        ("drone", "aerial")),
    ("air 3",        ("drone", "aerial")),
    ("mini 2",       ("drone", "aerial")),
    ("mini 3",       ("drone", "aerial")),
    ("mini 4",       ("drone", "aerial")),
    # Osmo / gimbal cameras (handheld, NOT drone)
    ("osmo pocket",  ("osmo", "gimbal", "pocket")),
    ("osmo action",  ("osmo", "action_cam")),
    ("osmo",         ("osmo",)),  # generic Osmo fallback
    # Action cameras
    ("gopro",        ("gopro", "action_cam")),
    ("action cam",   ("action_cam",)),
    ("action_cam",   ("action_cam",)),
    # Cinema / hybrid cameras
    ("ronin 4d",     ("ronin", "cinema")),
    ("ronin",        ("ronin",)),
    ("fx3",          ("sony", "cinema")),
    ("fx6",          ("sony", "cinema")),
    ("fx30",         ("sony", "cinema")),
    ("a7s",          ("sony",)),
    ("a7iv",         ("sony",)),
    ("a7r",          ("sony",)),
    ("r5c",          ("canon", "cinema")),
    ("c70",          ("canon", "cinema")),
    ("c300",         ("canon", "cinema")),
    # Shot type (not camera but useful)
    ("timelapse",    ("timelapse",)),
    ("time lapse",   ("timelapse",)),
    ("time-lapse",   ("timelapse",)),
]

# DJI filename suffix heuristic. _D.MP4 used to be interpreted as "DJI
# drone," but this is unreliable: Osmo Action and Osmo Pocket both write
# _D.MP4 too, and users who organize by shot type (e.g. "Osmo Timelapse/")
# rather than by camera get false-positive drone tags on indoor footage.
# Drop 4.44: removed. Filename-only drone detection now requires one of
# the explicit drone-model substrings in _CAMERA_PATTERNS (mavic, avata,
# phantom, inspire, air 2-3, mini 2-4), which are unambiguous.


def infer_camera_tags(path: Optional[Path]) -> list[str]:
    """Return a list of searchable tags based on the file's path/name.

    Matches camera/source names in both the filename and ancestor folders
    (users often organize B-roll by camera into subfolders like "Mavic 2/"
    or "Avata/"). The goal is to catch the common organizational patterns
    — false positives are cheap (an extra word in the Description column),
    false negatives just mean the editor has to search by a different
    keyword.
    """
    if path is None:
        return []
    full = str(path).lower()
    tags: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        if t and t not in seen:
            seen.add(t)
            tags.append(t)

    for pattern, tag_list in _CAMERA_PATTERNS:
        if pattern in full:
            for t in tag_list:
                _add(t)

    return tags
