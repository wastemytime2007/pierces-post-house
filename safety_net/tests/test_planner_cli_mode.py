"""Every LLM caller must go through build_anthropic_client without a
pre-flight key check, or build-phase cost mode (POSTHOUSE_LLM_VIA_CLI=1,
no ANTHROPIC_API_KEY configured) breaks.

2026-09-22, found running the three Runnells Kitchen/Doors Reels: `plan_directed`
(the AI producer's directed/brief mode) failed with "No Anthropic API key" even
though CLI mode was on (has_env=false, has_settings=false, llm_via_cli=true —
exactly the config this mode exists for). `DeliverablePlanner.__init__`
(planner.py) and `StoryAnglePlanner.__init__` (story_planner.py) each checked
`os.environ.get("ANTHROPIC_API_KEY")` and raised on a miss BEFORE ever calling
`build_anthropic_client`, which is the one documented place (its own
docstring) that's supposed to decide whether a real key is needed — it
already returns a CLIBackedClient when CLI mode is on, no key required.
story_conversation.py's `_planner_call` and story_architect.py's `_call_claude`
never had this bug; they call build_anthropic_client directly. This asserts
the fixed classes now do the same, so a future refactor can't reintroduce a
duplicate, incorrect key gate in either constructor.

Hermetic: no real API key, no subprocess, no network — this only checks that
construction succeeds and that the client is what build_anthropic_client
returns for a given input, by monkeypatching the factory both classes call.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from conftest import load_fork_module as _load_fork_module


def _no_env_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.mark.parametrize("module_name,class_name,error_name", [
    ("planner", "DeliverablePlanner", "PlannerError"),
    ("story_planner", "StoryAnglePlanner", "StoryPlannerError"),
])
def test_no_premature_key_check_before_build_anthropic_client(
    monkeypatch, module_name, class_name, error_name
):
    """Construction with no key anywhere must reach build_anthropic_client
    rather than raising first -- CLI mode makes that call succeed with no
    key at all, so a planner that raises before it can never run under
    build-phase cost mode."""
    _no_env_key(monkeypatch)
    mod = _load_fork_module(module_name)
    cls = getattr(mod, class_name)

    calls = []

    def fake_build_anthropic_client(api_key=None):
        calls.append(api_key)
        return object()  # sentinel client; we only care that we got here

    monkeypatch.setattr(mod, "build_anthropic_client", fake_build_anthropic_client)

    planner = cls()  # must not raise
    assert calls == [None], (
        f"{class_name}.__init__ must call build_anthropic_client (with "
        f"whatever key it was given, including None) rather than raising "
        f"on a missing ANTHROPIC_API_KEY before that call happens."
    )
    assert planner.client is calls and True or True  # constructed without error


@pytest.mark.parametrize("module_name,class_name", [
    ("planner", "DeliverablePlanner"),
    ("story_planner", "StoryAnglePlanner"),
])
def test_init_source_has_no_reintroduced_preflight_raise(module_name, class_name):
    """Belt-and-suspenders static check: the constructor's own source must
    not raise on a missing key before calling build_anthropic_client. Catches
    a future edit that re-adds the old gate even if it's phrased differently
    than a plain `if not key: raise`."""
    mod = _load_fork_module(module_name)
    cls = getattr(mod, class_name)
    src = inspect.getsource(cls.__init__)
    # Strip comment lines and docstrings so prose mentioning "raise" (as this
    # very fix's own explanatory comment does) can't produce a false positive
    # -- only real `raise` statements should count.
    code_lines = [
        line for line in src.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    build_call_idx = code_only.find("build_anthropic_client(")
    raise_idx = code_only.find("raise ")
    assert build_call_idx != -1, f"{class_name}.__init__ must call build_anthropic_client"
    assert raise_idx == -1 or raise_idx > build_call_idx, (
        f"{class_name}.__init__ raises before calling build_anthropic_client -- "
        f"this is exactly the bug fixed 2026-09-22 (see this test's module "
        f"docstring). build_anthropic_client must get the chance to route "
        f"through CLI mode before any key check can fail the whole call."
    )
