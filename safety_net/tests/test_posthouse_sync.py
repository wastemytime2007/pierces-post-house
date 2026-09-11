"""The two `posthouse/` copies must not drift.

There are two on purpose: `posthouse/` at the repo root is where the code
is edited, and `app/python_backend/posthouse/` is the copy the running
app actually imports and that Tauri bundles into a release (see
`app/src-tauri/tauri.conf.json` — `resources` globs
`../python_backend/**/*.py`, which is why this can't just be a symlink).

Both are tracked in git, so they can silently diverge — and on
2026-09-08 they did, by four days. An `rsync` had been run with relative
paths from the wrong working directory, so a fix under test was not the
code being executed: a live research call kept returning the OLD error
message while the source on disk clearly contained the new one. That
wasted a real debugging cycle and, worse, briefly made a broken build
look fixed.

Ryan's instruction that this file serves, 2026-09-08: *"lets just make
sure all future projects get this proper treatment."* Every improvement
to fragment granularity, cut length, or directed-mode strictness is
worthless if it doesn't reach the app the next project runs through. This
turns that failure from silent staleness into an immediate, obvious
test failure.

To fix a failure here, run: `./safety_net/sync_posthouse.sh`
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = REPO_ROOT / "posthouse"
APP_COPY = REPO_ROOT / "app" / "python_backend" / "posthouse"

FIX_HINT = "run ./safety_net/sync_posthouse.sh to bring the app copy up to date"


def _source_files(root: Path) -> dict:
    """Every tracked-shape source file, by path relative to its root.

    `__pycache__` is excluded — compiled bytecode differing is normal and
    harmless, and comparing it would make this test fail constantly for
    no reason.
    """
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        out[p.relative_to(root).as_posix()] = hashlib.sha256(
            p.read_bytes()
        ).hexdigest()
    return out


def test_both_posthouse_copies_exist():
    assert SOURCE.is_dir(), f"missing the edit-side copy: {SOURCE}"
    assert APP_COPY.is_dir(), (
        f"missing the copy the app actually imports: {APP_COPY}. The running "
        f"backend and the Tauri bundle both read from there — {FIX_HINT}."
    )


def test_app_copy_has_no_missing_or_extra_files():
    src, app = _source_files(SOURCE), _source_files(APP_COPY)

    missing = sorted(set(src) - set(app))
    assert not missing, (
        f"{len(missing)} file(s) exist in posthouse/ but NOT in the copy the app "
        f"imports, so the app cannot see them: {missing[:10]} — {FIX_HINT}"
    )

    extra = sorted(set(app) - set(src))
    assert not extra, (
        f"{len(extra)} stale file(s) in the app copy that no longer exist in "
        f"posthouse/: {extra[:10]} — {FIX_HINT}"
    )


def test_app_copy_contents_match():
    """The failure that actually bit us: same filenames, different bytes."""
    src, app = _source_files(SOURCE), _source_files(APP_COPY)
    differing = sorted(
        name for name in set(src) & set(app) if src[name] != app[name]
    )
    assert not differing, (
        f"{len(differing)} file(s) differ between posthouse/ and the copy the app "
        f"imports — the app is running DIFFERENT code than the source you're "
        f"editing: {differing} — {FIX_HINT}"
    )


# ---------------------------------------------------------------------------
# CLI routing must not be defeated by a caller's own API-key guard.
#
# 2026-09-11: every fragment extraction failed with "No Anthropic API key"
# after Ryan cleared his key, even though llm_via_cli was on and calls were
# free and working. `transcript_coverage._call_claude` raised on a missing
# key BEFORE reaching build_anthropic_client(), which is the one place that
# decides how a call is routed. Whether a call can be made is a question
# about the route, not about the key.

def test_no_call_site_preempts_the_client_factory_with_a_key_guard():
    """No posthouse module may refuse to run for a missing key without
    also checking cli_mode_enabled()."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "posthouse"
    offenders = []
    for py in root.glob("*.py"):
        if py.name == "cli_llm_client.py":
            continue
        src = py.read_text()
        for m in re.finditer(r"No Anthropic API key", src):
            # Look at the 400 chars before the message for a CLI check.
            window = src[max(0, m.start() - 400):m.start()]
            if "cli_mode_enabled" not in window:
                offenders.append(f"{py.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert not offenders, (
        "these raise on a missing API key without checking CLI mode, so they "
        f"break every call while llm_via_cli is on: {offenders}"
    )


def test_settings_payload_tells_the_ui_about_cli_routing():
    """The UI decides whether to say 'you can't generate ideas' from this
    payload; if llm_via_cli isn't in it, it will say so while CLI calls work."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "app" / "python_backend" / "settings.py").read_text()
    assert '"llm_via_cli"' in src, (
        "get_api_key_summary() must expose llm_via_cli — without it the UI "
        "gates generation on the key alone"
    )
