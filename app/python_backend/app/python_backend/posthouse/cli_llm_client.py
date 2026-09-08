"""Route the app's LLM calls through the local Claude Code CLI instead of
the Anthropic API.

Ryan, 2026-09-04, during the build phase: "Can we connect all of those to
run through you temporarily as well?" — after seeded research removed the
expensive research pass, the remaining spend was planning turns, idea
generation, transcript extraction and flagging, all billed to his API
key while the app is still being built and tested.

`claude -p` runs a non-interactive query against the Claude Code session
already installed and authenticated on this machine, so those calls bill
to his Claude Code plan rather than API credits. Verified working
2026-09-04: returns clean fenced JSON in ~3s, which is exactly the shape
every prompt in this codebase already asks for and `_extract_json`
already parses.

This module exposes a duck-typed stand-in for the Anthropic SDK client —
just enough of `client.messages.create(...)` and its response object that
existing call sites work unchanged. `build_anthropic_client()` returns
one of these when POSTHOUSE_LLM_VIA_CLI is on, so all seven call sites
(story_conversation, story_architect, audience_relevance,
transcript_coverage, and PreCut's own story_planner/planner) switch over
with no edits of their own.

Real limits, stated rather than hidden:
  * TEXT ONLY. Image/vision blocks (the video-watching path) can't be
    passed through `-p` this way and raise instead of silently dropping
    the frames — a "video finding" with no video actually looked at
    would be a fabricated observation.
  * Server-side tools (the `web_search` tool block) aren't forwarded.
    The CLI has its own search, but it is not the same contract, so a
    call that depends on the tool result raises rather than quietly
    returning an unsourced answer.
  * `max_tokens` / `temperature` are not honoured — the CLI decides.
    `stop_reason` is reported as "end_turn"; truncation checks that rely
    on it can't fire, which is noted at the one call site that checks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, List, Optional


class CLIClientError(RuntimeError):
    """Raised when the CLI path can't honour a request. Never swallowed
    into a plausible-looking empty response."""


# Generous: a story-arc prompt carries the whole fragment set and the CLI
# adds session startup on top of model latency.
CLI_TIMEOUT_SEC = 600


def cli_mode_enabled() -> bool:
    raw = os.environ.get("POSTHOUSE_LLM_VIA_CLI", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: List[_TextBlock] = field(default_factory=list)
    stop_reason: str = "end_turn"


def _flatten_content(content: Any) -> str:
    """Turn a messages-API content field into plain text, refusing
    anything that would lose real information."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if btype == "text":
                parts.append(block["text"] if isinstance(block, dict) else block.text)
            elif btype == "image":
                raise CLIClientError(
                    "This request sends images (the video-watching path). The CLI "
                    "route is text-only, and dropping the frames would produce an "
                    "'observation' of a video nobody looked at. Use seeded research "
                    "for this step, or turn POSTHOUSE_LLM_VIA_CLI off for a live run."
                )
            else:
                raise CLIClientError(f"Unsupported content block type for CLI route: {btype!r}")
        return "\n\n".join(parts)
    raise CLIClientError(f"Unsupported content payload: {type(content).__name__}")


class _Messages:
    def __init__(self, model: Optional[str] = None):
        self._model = model

    def create(self, *, messages, system=None, model=None, tools=None,
               max_tokens=None, temperature=None, **_ignored) -> _Response:
        if tools:
            raise CLIClientError(
                "This request uses server-side tools (e.g. web_search). The CLI "
                "route doesn't forward them, and answering without the tool would "
                "produce unsourced findings. Use seeded research (POSTHOUSE_RESEARCH_SEED) "
                "for research, or turn POSTHOUSE_LLM_VIA_CLI off for a live run."
            )

        prompt_parts = [_flatten_content(m.get("content")) for m in messages
                        if m.get("role") == "user"]
        prompt = "\n\n".join(p for p in prompt_parts if p)
        if not prompt.strip():
            raise CLIClientError("No user content to send.")

        exe = shutil.which("claude")
        if not exe:
            raise CLIClientError(
                "POSTHOUSE_LLM_VIA_CLI is on but the `claude` CLI isn't on PATH."
            )

        cmd = [exe, "-p"]
        if system:
            cmd += ["--system-prompt", system]
        # Deliberately NOT passing the caller's API model id: the CLI
        # takes its own model names, and a mismatched id would fail in a
        # confusing way. Let the CLI use its configured default.
        # Strip API auth from the CHILD's environment. Confirmed real,
        # 2026-09-04: with ANTHROPIC_API_KEY set, the CLI exits 1 with
        # "claude.ai connectors are disabled because ANTHROPIC_API_KEY or
        # another auth source is set and takes precedence over your
        # claude.ai login". Worse than the error is what it implies — an
        # API key visible to the child is a key that could be billed,
        # which is the exact thing this route exists to avoid. The parent
        # process keeps its key (the app still needs it whenever this
        # route is off); only the child loses sight of it.
        child_env = {k: v for k, v in os.environ.items()
                     if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                  "ANTHROPIC_WORKSPACE_ID")}
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=CLI_TIMEOUT_SEC, env=child_env,
            )
        except subprocess.TimeoutExpired:
            raise CLIClientError(f"claude CLI timed out after {CLI_TIMEOUT_SEC}s.")

        if proc.returncode != 0:
            raise CLIClientError(
                f"claude CLI exited {proc.returncode}: {(proc.stderr or '').strip()[:400]}"
            )
        out = (proc.stdout or "").strip()
        if not out:
            raise CLIClientError("claude CLI returned no output.")
        return _Response(content=[_TextBlock(text=out)])


class CLIBackedClient:
    """Duck-typed stand-in for anthropic.Anthropic."""

    def __init__(self, model: Optional[str] = None):
        self.messages = _Messages(model)
