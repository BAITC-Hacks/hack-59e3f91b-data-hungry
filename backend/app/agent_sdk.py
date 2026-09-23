"""Alternative LLM backend: Claude Agent SDK (Claude Code engine).

Why: it authenticates with a Claude subscription login or a long-lived ``CLAUDE_CODE_OAUTH_TOKEN`` (``claude setup-token``),
so the prototype can run without an Anthropic Console API key. The same tool handlers, system prompt and
confirmation gate from :mod:`app.agent` are reused; only the model loop is delegated to Claude Code.

Selected with ``LLM_PROVIDER=claude_code`` (see :mod:`app.config`). Multi-turn memory is kept by resuming the
Claude Code session stored on our chat session (``session.sdk_session_id``).
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from . import config

log = logging.getLogger("ekt.agent_sdk")

SERVER_NAME = "ekt"
BUILTIN_TOOLS_OFF = ["Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "WebSearch", "WebFetch", "Task", "TodoWrite", "NotebookEdit", "Agent", "Skill", "KillShell", "BashOutput"]
_WORKDIR = Path(tempfile.gettempdir()) / "ekt-assistant-sdk"


def configured() -> bool:
    """True when Claude Code can authenticate: a setup-token in the env or a local `claude login` profile."""
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY"):
        return True
    creds = Path.home() / ".claude" / ".credentials.json"
    return creds.exists()


def _make_server(state: Any, tool_specs: list[dict[str, Any]], handlers: dict[str, Any]):
    """Build an in-process MCP server whose tools close over this turn's state (products, escalation)."""
    sdk_tools = []
    for spec in tool_specs:
        name = spec["name"]
        handler = handlers[name]
        schema = {k: v for k, v in spec["input_schema"].items() if k != "additionalProperties"}

        def _bind(_name: str, _handler: Any, _schema: dict[str, Any]):
            @tool(_name, spec["description"], _schema)
            async def _impl(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    out = await _handler(args if isinstance(args, dict) else {}, state)
                    return {"content": [{"type": "text", "text": str(out)}]}
                except Exception as exc:  # report to the model instead of killing the turn
                    log.exception("tool %s failed", _name)
                    return {"content": [{"type": "text", "text": f"Ошибка инструмента: {exc}"}], "is_error": True}

            return _impl

        sdk_tools.append(_bind(name, handler, schema))
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=sdk_tools)


async def run_turn(session: Any, state: Any, user_text: str, *, system_prompt: str, tool_specs: list[dict[str, Any]], handlers: dict[str, Any]) -> str:
    """Run one assistant turn through Claude Code and return the final reply text."""
    _WORKDIR.mkdir(parents=True, exist_ok=True)
    server = _make_server(state, tool_specs, handlers)
    names = [f"mcp__{SERVER_NAME}__{spec['name']}" for spec in tool_specs]
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={SERVER_NAME: server},
        tools=[],  # no built-in Claude Code tools: the assistant only sees the shop tools
        allowed_tools=names,
        disallowed_tools=BUILTIN_TOOLS_OFF,
        permission_mode="bypassPermissions",
        max_turns=int(os.getenv("LLM_SDK_MAX_TURNS", "10")),
        model=os.getenv("LLM_SDK_MODEL", "opus"),
        effort=os.getenv("LLM_SDK_EFFORT", config.LLM_EFFORT),
        setting_sources=[],
        cwd=str(_WORKDIR),
        resume=getattr(session, "sdk_session_id", None) or None,
        env={"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
    )
    t0 = time.perf_counter()
    final_text, interim, tool_calls, cost = "", "", 0, None
    async for msg in query(prompt=user_text, options=options):
        if isinstance(msg, AssistantMessage):
            text = "".join(b.text for b in msg.content if isinstance(b, TextBlock)).strip()
            uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
            tool_calls += len(uses)
            if text:
                if uses:
                    interim = text
                else:
                    final_text = text
        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                session.sdk_session_id = msg.session_id
            cost = msg.total_cost_usd
            if msg.subtype != "success" and not final_text:
                log.warning("claude code result subtype=%s result=%s", msg.subtype, getattr(msg, "result", None))
                if getattr(msg, "result", None) and "login" in str(msg.result).lower():
                    return "Claude Code не авторизован на сервере: нужен CLAUDE_CODE_OAUTH_TOKEN (claude setup-token)."
    log.info("claude-code turn: %.1fs, %d tool calls, cost=%s", time.perf_counter() - t0, tool_calls, cost)
    return final_text or interim or "Не удалось сформировать ответ. Уточните, пожалуйста, запрос."
