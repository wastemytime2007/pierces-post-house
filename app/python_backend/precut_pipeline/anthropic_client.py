"""Shared Anthropic client construction — the one place workspace-scoped
keys get handled, so it's fixed everywhere at once instead of per-caller.

Most users' API keys work with just `api_key=`. Keys created under an
Anthropic organization/team Console setup with multiple workspaces
additionally require an `anthropic-workspace-id` header on every request
(discovered 2026-09-03) — a property of the ACCOUNT the key came from,
not something to assume for every user. `ANTHROPIC_WORKSPACE_ID` is
optional and blank by default; see `settings.py`'s module docstring for
why it must never be hardcoded.
"""
from __future__ import annotations

import os
from typing import Optional

import anthropic


def build_anthropic_client(api_key: Optional[str] = None):
    """Construct an Anthropic client, adding the workspace-id header only
    when one is actually configured (env var or app settings, injected
    into env by `settings.apply_settings_to_env()` at backend startup).

    2026-09-04 (build phase, Ryan: "Can we connect all of those to run
    through you temporarily as well?"): when POSTHOUSE_LLM_VIA_CLI is on,
    this returns a duck-typed client that shells out to the local
    `claude` CLI instead, so calls bill to the Claude Code plan rather
    than API credits. Every LLM call site in the app goes through this
    one factory, so the switch happens here and nowhere else. See
    posthouse/cli_llm_client.py for what that route can and can't do
    (text only; no server-side tools).
    """
    try:
        from posthouse.cli_llm_client import cli_mode_enabled, CLIBackedClient
    except Exception:
        cli_mode_enabled = None  # posthouse not importable (donor-only context)
    if cli_mode_enabled is not None and cli_mode_enabled():
        return CLIBackedClient()

    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return anthropic.Anthropic(api_key=api_key, default_headers=default_headers)
