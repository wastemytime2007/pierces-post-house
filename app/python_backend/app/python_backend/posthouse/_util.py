"""posthouse._util — tiny shared helpers with no posthouse dependencies.

Extracted out of ``posthouse.cull.signals`` (code review finding #9,
2026-09-01): ``now_iso()`` and ``atomic_write_bytes()`` were each
reimplemented per-module (``manifest.py``, ``brandbrief.py``,
``projectmanager.py``, ``cull/signals.py``). This module is the one place
new code should import them from; the older per-module copies are left
alone for now (noted for a later cleanup pass, not touched here to avoid
widening this diff into unrelated modules).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """UTC timestamp, second precision, ``Z`` suffix — the convention every
    posthouse module's provenance timestamp already uses."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory tempfile + ``os.replace``,
    so a reader never observes a partially-written file and a crash mid-write
    never corrupts the previous version."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
