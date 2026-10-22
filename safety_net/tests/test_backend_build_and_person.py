"""The backend says who the person is and whether its own code has changed since it started.

The second one exists because of 2026-10-22: a backend started before a change answered "Unknown command: open_in_premiere_project" and a finished export was shown as a failure. An app that knows
it is behind can say "restart" instead of failing a button.
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "app" / "python_backend"


def _backend(monkeypatch):
    # backend.py starts a pipeline on import; only the two small functions are wanted, so they are read out of the file and run on their own
    src = (BACKEND / "backend.py").read_text()
    start = src.index("def source_build()")
    end = src.index("def emit_ready()")
    ns = {"__file__": str(BACKEND / "backend.py"), "Path": Path, "os": __import__("os")}
    exec(compile(src[start:end], "backend_fragment", "exec"), ns)
    return ns


def test_the_fingerprint_is_short_stable_and_changes_when_a_file_changes(monkeypatch, tmp_path):
    ns = _backend(monkeypatch)
    first = ns["source_build"]()
    assert first and len(first) == 12 and first == ns["source_build"]()      # the same code gives the same answer

    # a file that changes gives a different answer (checked on a copy, so the real backend is untouched)
    import shutil
    copy = tmp_path / "python_backend"
    shutil.copytree(BACKEND, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    ns2 = {"__file__": str(copy / "backend.py"), "Path": Path, "os": __import__("os")}
    src = (BACKEND / "backend.py").read_text()
    exec(compile(src[src.index("def source_build()"):src.index("def emit_ready()")], "f", "exec"), ns2)
    before = ns2["source_build"]()
    (copy / "creator_tools.py").write_text((copy / "creator_tools.py").read_text() + "\n# touched\n")
    assert ns2["source_build"]() != before


def test_the_name_is_a_first_name_or_nothing_never_a_question(monkeypatch):
    ns = _backend(monkeypatch)
    name = ns["person_name"]()
    assert isinstance(name, str)
    assert name == "" or (name[0].isalpha() and " " not in name)


def test_the_name_is_empty_rather_than_junk_when_the_account_has_none(monkeypatch):
    ns = _backend(monkeypatch)
    import pwd
    monkeypatch.setattr(pwd, "getpwuid", lambda _: type("P", (), {"pw_gecos": ",,,"})())
    import getpass
    monkeypatch.setattr(getpass, "getuser", lambda: "_svc42")
    assert ns["person_name"]() == ""
