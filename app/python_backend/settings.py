"""App-level settings persistence.

Stored at:
    ~/Library/Application Support/Post House/settings.json

Currently holds the Anthropic API key (and, for the minority of users
whose key is workspace-scoped, an optional workspace ID) so users don't
have to wrangle launchctl env vars. Read once at backend startup and
injected into os.environ so the planner's env lookup finds it.

**Workspace ID is optional and per-user, never hardcoded.** Anthropic
keys created under an individual account work with just an API key. Keys
created under an organization/team Console setup with multiple workspaces
require an `anthropic-workspace-id` header on every request (discovered
2026-09-03, Ryan's own account) — that requirement is a property of the
ACCOUNT the key came from, not something every user of this app will hit.
Baking one specific workspace ID into the app would only work for keys
from that one workspace and silently break for everyone else, so this
stays a blank-by-default settings field.

Security notes:
- File is created with mode 0600 (owner read/write only)
- We never log the key value itself, only whether one is set
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from project import app_support_dir


def settings_path() -> Path:
    return app_support_dir() / "settings.json"


def load_settings() -> dict:
    """Load settings.json, returning empty dict if missing/corrupt."""
    path = settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict) -> None:
    """Atomic write with 0600 permissions."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    # 0600 — owner rw only, no group/world access
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)


def apply_settings_to_env() -> dict:
    """Load settings and inject any API keys into os.environ.

    Env vars (ANTHROPIC_API_KEY etc.) take precedence over the settings
    file, so a user with launchctl-set keys won't have them overridden by
    a stale settings file.

    Returns a summary dict for logging (does NOT include key values).
    """
    settings = load_settings()
    summary = {"api_key_source": None}

    env_key = os.environ.get("ANTHROPIC_API_KEY")
    settings_key = settings.get("anthropic_api_key")

    if env_key:
        summary["api_key_source"] = "env"
    elif settings_key:
        os.environ["ANTHROPIC_API_KEY"] = settings_key
        summary["api_key_source"] = "settings"
    else:
        summary["api_key_source"] = "missing"

    # Optional — see module docstring. Only set for the minority of users
    # whose key is workspace-scoped; env takes precedence over settings,
    # same rule as the API key above.
    env_ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    settings_ws = settings.get("anthropic_workspace_id")
    if env_ws:
        summary["workspace_id_source"] = "env"
    elif settings_ws:
        os.environ["ANTHROPIC_WORKSPACE_ID"] = settings_ws
        summary["workspace_id_source"] = "settings"
    else:
        summary["workspace_id_source"] = "missing"

    return summary


def set_api_key(api_key: str) -> None:
    """Persist a new API key and inject into the current process."""
    api_key = api_key.strip()
    settings = load_settings()
    if api_key:
        settings["anthropic_api_key"] = api_key
        os.environ["ANTHROPIC_API_KEY"] = api_key
    else:
        # Empty string → remove the key
        settings.pop("anthropic_api_key", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
    save_settings(settings)


def set_workspace_id(workspace_id: str) -> None:
    """Persist an optional workspace ID and inject into the current
    process. Blank for most users — see module docstring."""
    workspace_id = workspace_id.strip()
    settings = load_settings()
    if workspace_id:
        settings["anthropic_workspace_id"] = workspace_id
        os.environ["ANTHROPIC_WORKSPACE_ID"] = workspace_id
    else:
        settings.pop("anthropic_workspace_id", None)
        os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)
    save_settings(settings)


def get_api_key_summary() -> dict:
    """Return enough info for the UI to show state, without leaking the key.

    Determines 'active source' by what's actually persisted:
      - If settings.json has a key, source is 'settings' (even though env
        also has it because we mirror for the current process).
      - If only env has it, source is 'env'.
      - Otherwise 'none'.
    """
    settings = load_settings()
    env_key = os.environ.get("ANTHROPIC_API_KEY", "")
    settings_key = settings.get("anthropic_api_key", "")

    if settings_key:
        active = "settings"
        key = settings_key
    elif env_key:
        active = "env"
        key = env_key
    else:
        active = "none"
        key = ""

    # Optional, not secret — safe to show in full, unlike the key itself.
    workspace_id = (
        os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
        or settings.get("anthropic_workspace_id", "")
    )

    return {
        "active_source": active,
        "has_env": bool(env_key),
        "has_settings": bool(settings_key),
        "key_suffix": key[-4:] if len(key) >= 4 else "",
        "workspace_id": workspace_id,
        # Drop 4.44: onboarding flags so the UI knows whether to show
        # the welcome modal / tour / api-key help panel.
        "welcome_seen": bool(settings.get("welcome_seen")),
        "tour_seen": bool(settings.get("tour_seen")),
        "api_key_help_auto_shown": bool(settings.get("api_key_help_auto_shown")),
        # Drop 4.47: nudge users about the Default Includes feature on
        # their first export. Set to True after the nudge is shown once
        # (whether dismissed or acted on) so we never re-nag.
        "auto_include_nudge_seen": bool(settings.get("auto_include_nudge_seen")),
    }


# ---------------------------------------------------------------------------
# Onboarding flags (Drop 4.44)
# ---------------------------------------------------------------------------
#
# Three separate boolean flags so we can independently track which parts of
# onboarding the user has dismissed:
#
#   welcome_seen              — 4-screen welcome modal has been shown and
#                               either completed or skipped
#   tour_seen                 — lightweight tour tooltips on start-screen
#                               and project-view have been dismissed
#   api_key_help_auto_shown   — the "Don't know what Claude is?" panel has
#                               been auto-opened once. After that, the
#                               user must click the (?) button to see it
#                               again. Prevents re-nagging users who set
#                               up a key once and cleared it later.

def set_onboarding_flag(flag: str, value: bool = True) -> None:
    """Set one of the onboarding boolean flags. Values are stored in the
    same settings.json so they survive restarts."""
    _ALLOWED = {
        "welcome_seen",
        "tour_seen",
        "api_key_help_auto_shown",
        # Drop 4.47: dismissed/acted on the first-export Default Includes nudge.
        "auto_include_nudge_seen",
    }
    if flag not in _ALLOWED:
        raise ValueError(f"unknown onboarding flag: {flag}")
    settings = load_settings()
    settings[flag] = bool(value)
    save_settings(settings)


# ---------------------------------------------------------------------------
# Auto-include rules (Drop 4.46)
# ---------------------------------------------------------------------------
#
# Stored under "auto_include_rules" in settings.json as a list of rule dicts.
# Each rule has the shape defined in precut_pipeline.auto_include.AutoIncludeRule.
# The exporter consults these rules at export time and silently includes the
# resolved files in the user-specified bins.

def get_auto_include_rules() -> list[dict]:
    """Return the saved list of auto-include rules.

    Always returns a list (empty if no rules saved). Rule shape is dicts —
    the exporter converts to AutoIncludeRule dataclass via from_dict.
    """
    settings = load_settings()
    rules = settings.get("auto_include_rules", [])
    if not isinstance(rules, list):
        return []
    return [r for r in rules if isinstance(r, dict)]


def set_auto_include_rules(rules: list[dict]) -> None:
    """Persist a new list of auto-include rules. Replaces the entire list."""
    if not isinstance(rules, list):
        raise ValueError("auto_include_rules must be a list")
    # Strip out any non-dict entries defensively.
    sanitized = [r for r in rules if isinstance(r, dict)]
    settings = load_settings()
    settings["auto_include_rules"] = sanitized
    save_settings(settings)


# ---------------------------------------------------------------------------
# Audience / content-goal profiles (2026-09-03)
# ---------------------------------------------------------------------------
#
# App-level, not per-project: Ryan wants these authored ONCE on the main
# screen (before opening any project), then picked from a dropdown at
# Project Manager intake -- not retyped as free text per project. Stored
# here under "audience_profiles" as a list of {id, name, description}.
#
# Seeded once, on first access, with SoldFast's three real content funnels
# (ported from Agent Studio's content-engine team -- see
# ~/.claude/skills/soldfast-content-funnels/SKILL.md) plus a placeholder
# long-form/heart-driven profile Ryan can edit, since that one was never
# formally spec'd anywhere. "Seeded once" is tracked via a separate flag so
# deleting all profiles later doesn't bring the defaults back.

_DEFAULT_AUDIENCE_PROFILES = [
    {
        "id": "brand-authority",
        "name": "Brand / Authority",
        "description": (
            "Establish SoldFast as an industry expert and build trust. "
            "Broad audience -- future sellers, franchisees, contractors, "
            "and the general community. Heart-driven, not comedic; leads "
            "with the burden, not the brand; show don't say. Draws on "
            "finished projects, HGTV behind-the-scenes, sit-down "
            "interviews with Bob and Mitch, and genuine how-to value."
        ),
    },
    {
        "id": "franchisee-recruiting",
        "name": "Franchisee Recruiting",
        "description": (
            "Recruit real-estate operators in other markets who are "
            "hitting a ceiling on their own deal flow and want systems, "
            "brand, and a lead engine -- a playbook, not a logo. Leads "
            "with the operator's real ceiling, proves the model travels "
            "across markets, never uses hype or pressure."
        ),
    },
    {
        "id": "contractor-recruiting",
        "name": "Contractor Recruiting",
        "description": (
            "Recruit skilled tradespeople into SoldFast's in-house/partner "
            "network. Audience: contractors exposed to feast-or-famine job "
            "cycles. Leads with the real pain (gaps between jobs, chasing "
            "invoices) and proves steady pipeline, reliable payment, and "
            "real volume through real stories, not claims."
        ),
    },
    {
        "id": "long-form-heart-driven",
        "name": "Long-form / heart-driven",
        "description": (
            "Intentional, story-driven long-form work -- not the social-"
            "media treadmill. Placeholder: edit this to describe the real "
            "audience and goal for this kind of edit; it was never "
            "formally defined the way the three funnels above were."
        ),
    },
]


def get_audience_profiles() -> list[dict]:
    """Return the saved list of audience/content-goal profiles, seeding
    the defaults above on first-ever access (never re-seeded after that,
    even if the user deletes everything)."""
    settings = load_settings()
    if not settings.get("audience_profiles_seeded"):
        settings["audience_profiles"] = list(_DEFAULT_AUDIENCE_PROFILES)
        settings["audience_profiles_seeded"] = True
        save_settings(settings)
        return settings["audience_profiles"]
    profiles = settings.get("audience_profiles", [])
    if not isinstance(profiles, list):
        return []
    return [p for p in profiles if isinstance(p, dict)]


def set_audience_profiles(profiles: list[dict]) -> None:
    """Persist a new list of audience profiles. Replaces the entire list."""
    if not isinstance(profiles, list):
        raise ValueError("audience_profiles must be a list")
    sanitized = [p for p in profiles if isinstance(p, dict) and p.get("name")]
    settings = load_settings()
    settings["audience_profiles"] = sanitized
    settings["audience_profiles_seeded"] = True
    save_settings(settings)
