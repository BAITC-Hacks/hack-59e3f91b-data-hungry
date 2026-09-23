"""Fallback LLM backend: any OpenAI-compatible chat-completions API with function calling.

Covers OpenAI itself, the hackathon gateway ``llm.nitec.kz`` (``openai/gpt-oss-120b``), OpenRouter, Groq, vLLM.
The tools, handlers, system prompt and the confirmation gate are the same as for the Anthropic backend
(:mod:`app.agent`); only the wire format differs. Selected with ``LLM_PROVIDER=openai`` or automatically when
``OPENAI_API_KEY`` / ``NITEC_API_KEY`` is set and no Anthropic credential is present.

Env: ``OPENAI_API_KEY`` (or ``NITEC_API_KEY``), ``OPENAI_BASE_URL`` (default: NITEC base when only the NITEC key is set,
else https://api.openai.com/v1), ``OPENAI_MODEL`` (default ``openai/gpt-oss-120b`` for NITEC, ``gpt-4.1`` otherwise).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("ekt.agent_openai")

MAX_ITERATIONS = 6
HISTORY_LIMIT = 24
_client: httpx.AsyncClient | None = None


def settings() -> dict[str, str] | None:
    """Resolve key/base/model; None when no key is configured."""
    key = os.getenv("OPENAI_API_KEY") or ""
    nitec_key = os.getenv("NITEC_API_KEY") or ""
    if not key and not nitec_key:
        return None
    if key:
        base = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        model = os.getenv("OPENAI_MODEL") or "gpt-4.1"
    else:
        key = nitec_key
        base = os.getenv("OPENAI_BASE_URL") or os.getenv("NITEC_API_BASE_URL") or "https://llm.nitec.kz/v1"
        model = os.getenv("OPENAI_MODEL") or "openai/gpt-oss-120b"
    return {"key": key, "base": base.rstrip("/"), "model": model}


def configured() -> bool:
    return settings() is not None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))
    return _client


def _to_openai_tools(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for spec in tool_specs:
        params = dict(spec["input_schema"])
        out.append({"type": "function", "function": {"name": spec["name"], "description": spec["description"], "parameters": params}})
    return out


def _trim(history: list[dict[str, Any]]) -> None:
    """Keep the last HISTORY_LIMIT messages, cutting only at a user-message boundary so tool pairs stay intact."""
    if len(history) <= HISTORY_LIMIT:
        return
    cut = len(history) - HISTORY_LIMIT
    while cut < len(history) and history[cut].get("role") != "user":
        cut += 1
    del history[:cut]


async def _complete(cfg: dict[str, str], messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    body = {"model": cfg["model"], "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.2}
    r = await _get_client().post(f"{cfg['base']}/chat/completions", json=body, headers={"Authorization": f"Bearer {cfg['key']}"})
    if r.status_code >= 400:
        raise RuntimeError(f"LLM API {r.status_code}: {r.text[:300]}")
    return r.json()


async def run_turn(session: Any, state: Any, user_text: str, *, system_prompt: str, tool_specs: list[dict[str, Any]], handlers: dict[str, Any]) -> str:
    """One assistant turn: chat-completions loop with function calling; returns the reply text."""
    cfg = settings()
    if cfg is None:
        raise RuntimeError("OpenAI-compatible provider is not configured")
    history: list[dict[str, Any]] = session.oa_messages
    history.append({"role": "user", "content": user_text})
    checkpoint = len(history) - 1
    tools = _to_openai_tools(tool_specs)
    interim = ""
    t0 = time.perf_counter()
    try:
        for _ in range(MAX_ITERATIONS):
            data = await _complete(cfg, [{"role": "system", "content": system_prompt}] + history, tools)
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            text = (msg.get("content") or "").strip()
            calls = msg.get("tool_calls") or []
            usage = data.get("usage") or {}
            log.info("llm(openai) model=%s finish=%s in=%s out=%s calls=%d", data.get("model"), choice.get("finish_reason"), usage.get("prompt_tokens"), usage.get("completion_tokens"), len(calls))
            if not calls:
                history.append({"role": "assistant", "content": text or interim or "Не удалось сформировать ответ."})
                _trim(history)
                log.info("openai turn done in %.1fs", time.perf_counter() - t0)
                return text or interim or "Не удалось сформировать ответ. Уточните, пожалуйста, запрос."
            history.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            interim = text or interim

            async def _run(call: dict[str, Any]) -> dict[str, Any]:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                handler = handlers.get(name)
                try:
                    if handler is None:
                        raise KeyError(f"unknown tool {name}")
                    content = await handler(args if isinstance(args, dict) else {}, state)
                except Exception as exc:
                    log.exception("tool %s failed", name)
                    content = f"Ошибка инструмента: {exc}"
                return {"role": "tool", "tool_call_id": call.get("id"), "content": str(content)}

            results = await asyncio.gather(*(_run(c) for c in calls))
            history.extend(results)
        tail = "Я сделал много шагов, но не успел завершить ответ. Уточните запрос, пожалуйста."
        history.append({"role": "assistant", "content": tail})
        return f"{interim}\n\n{tail}" if interim else tail
    except Exception:
        del history[checkpoint:]
        raise
