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


def build_anthropic_client(api_key: Optional[str] = None) -> anthropic.Anthropic:
    """Construct an Anthropic client, adding the workspace-id header only
    when one is actually configured (env var or app settings, injected
    into env by `settings.apply_settings_to_env()` at backend startup)."""
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    default_headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
    return anthropic.Anthropic(api_key=api_key, default_headers=default_headers)
