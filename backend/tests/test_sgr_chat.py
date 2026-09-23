"""SGR adapter invariants; no live OpenAI calls."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from openai import pydantic_function_tool

from app import agent, sgr_chat
from app.sessions import Session


def test_chat_completions_settings_and_strict_tools():
    kwargs = sgr_chat.ShopLLMConfig(model="gpt-4.1-mini", max_tokens=500).to_openai_client_kwargs()
    assert "reasoning_effort" not in kwargs
    assert kwargs["parallel_tool_calls"] is False
    assert "temperature" not in kwargs and "max_tokens" not in kwargs
    assert sgr_chat.ShopLLMConfig(model="gpt-5.6-terra").to_openai_client_kwargs()["reasoning_effort"] == "none"
    for cls in [sgr_chat.ShopReasoningTool, *sgr_chat.TOOLKIT]:
        spec = pydantic_function_tool(cls, name=cls.tool_name)
        assert spec["function"]["strict"] is True
        assert spec["function"]["parameters"]["additionalProperties"] is False


def test_unverified_certificate_is_not_evidence_for_agent():
    card = {"id": 1, "name": "Тест", "stock_status": "unknown", "certificates": [
        {"title": "Сертификат", "number": "UNVERIFIED-123", "url": "https://example.test/document"},
    ]}
    visible = agent._card_for_llm(card)
    assert "certificates" not in visible


def test_conflicting_product_current_is_marked_for_agent():
    visible = agent._card_for_llm({"id": 2, "name": "Автомат 160А", "properties": {"Номинальный ток": "250 А"}})
    assert "160 А" in visible["data_conflict"] and "250 А" in visible["data_conflict"]


def test_customer_reply_warns_about_conflicting_catalog_facts():
    session = Session(id="sgr-conflict-test")
    state = agent.TurnState(session)
    state.products = [{"id": 2, "name": "Автомат 160А", "article": "TEST-2", "stock_status": "in_stock",
                       "properties": {"Номинальный ток": "250 А"}}]
    response = agent._response(session, "Нашёл товар.", state, cart_updated=False, t0=time.perf_counter())
    assert "**Важно:**" in response["reply"]
    assert "160 А" in response["reply"] and "250 А" in response["reply"]


def test_snapshot_stock_is_labeled_for_agent_and_customer():
    note = "остаток по данным на 23.09 10:00 (сайт ekt.kz не ответил вовремя)"
    card = {"id": 3, "name": "Кабель", "stock_status": "in_stock", "stock_note": note}
    assert agent._card_for_llm(card)["stock_note"] == note
    session = Session(id="sgr-stale-stock-test")
    state = agent.TurnState(session)
    state.products = [card]
    response = agent._response(session, "Есть в наличии.", state, cart_updated=False, t0=time.perf_counter())
    assert note in response["reply"]
    assert "не подтверждены сейчас" in response["reply"]


@pytest.mark.asyncio
async def test_widget_cart_button_bypasses_configured_llm(monkeypatch):
    session = Session(id="sgr-button-no-llm-0001")
    monkeypatch.setattr(agent, "llm_configured", lambda: True)
    monkeypatch.setattr(agent, "llm_provider", lambda: "sgr")

    async def llm_must_not_run(*args, **kwargs):
        raise AssertionError("cart button called the LLM")

    async def fake_propose(session_id, items):
        assert session_id == session.id
        assert items == [{"product_id": 515291, "qty": 2}]
        return {"items": [{"name": "Тестовый автомат", "qty": 2, "max_qty": 5}]}

    monkeypatch.setattr(sgr_chat, "run_turn", llm_must_not_run)
    monkeypatch.setattr(agent.cart_store, "propose", fake_propose)
    result = await agent.chat(session, "Добавь в корзину: Тестовый автомат (id 515291), 2 шт", [], None, "ru")
    assert result["reply"] == "Подтвердите: добавить Тестовый автомат — 2 шт (в наличии 5)?"
    assert result["cart_updated"] is False


@pytest.mark.asyncio
async def test_cart_proposal_requires_customer_request(monkeypatch):
    calls = []

    async def fake_handler(inp, state):
        calls.append(inp)
        return "proposal"

    monkeypatch.setitem(agent.TOOL_HANDLERS, "propose_add_to_cart", fake_handler)
    tool = sgr_chat.ProposeCart(items=[sgr_chat.CartLine(product_id=7, qty=2)])
    blocked = await tool(None, None, state=SimpleNamespace(), allow_cart_proposal=False)
    assert "не просил" in blocked and not calls
    assert await tool(None, None, state=SimpleNamespace(), allow_cart_proposal=True) == "proposal"
    assert calls == [{"items": [{"product_id": 7, "qty": 2}]}]


@pytest.mark.asyncio
async def test_sgr_chat_preserves_confirmation_gate(monkeypatch):
    session = Session(id="sgr-test")
    monkeypatch.setattr(agent.config, "LLM_PROVIDER", "sgr")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    async def fake_run_turn(sess, message, attachments, context, state):
        assert sess is session
        assert message == "Условия доставки?"
        return "Проверил условия."

    monkeypatch.setattr(sgr_chat, "run_turn", fake_run_turn)
    result = await agent.chat(session, "Условия доставки?", [], None, "ru")
    assert result["reply"] == "Проверил условия."
    assert session.sgr_messages == [
        {"role": "user", "content": "Условия доставки?"},
        {"role": "assistant", "content": "Проверил условия."},
    ]
    assert not agent.CART_REQUEST_RE.search("Что в этой спецификации?")
    assert agent.CART_REQUEST_RE.search("Добавь в корзину 2 шт")
    assert agent.customer_requested_cart_action("Добавь в корзину 2 шт")
    assert not agent.customer_requested_cart_action("Посмотри файл, но пока не добавляй в корзину")
    assert not agent.customer_requested_cart_action("Что сейчас в корзине?")
    assert agent.parse_direct_add("Не добавляй товар (id 515291) — 2 шт") == []
