"""LLM shopping assistant: confirmation gate, tool loop and response assembly.

Public entry points:
    chat(session, message, attachments, page_url, lang) -> ChatResponse dict
    render_confirmation(result, lang) -> reply text after cart_store.confirm
    record_exchange(session, user_text, assistant_text) -> keep the LLM history in sync with button clicks
    llm_configured() -> whether an Anthropic credential is present
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from . import analogs, attachments as attachments_mod, catalog, certificates, config, ekt_api, knowledge
from .cart import PendingActionError, cart_store
from .sessions import Session

log = logging.getLogger("ekt.agent")

MAX_ITERATIONS = 6
HISTORY_LIMIT = 24
MAX_PRODUCTS = 8
LLM_TIMEOUT = 55.0

MANAGER_CONTACTS = {"phone": "+7 (727) 346-88-88", "whatsapp": "+7 (778) 046-88-88", "email": "almaty@ekt.kz"}

# ---------------------------------------------------------------------------------------------------------------
# System prompt (static -> cached with cache_control; per-turn facts go into the user message context block)
# ---------------------------------------------------------------------------------------------------------------
SYSTEM_PROMPT = """Ты — AI-ассистент интернет-магазина ekt.kz (ТОО «Электрокомплект», Казахстан): электротехническая продукция — автоматы, кабель, светильники, розетки, щиты, инструмент и т.д. Помогаешь клиентам найти товар, проверить наличие и цену, подобрать аналог, узнать условия покупки и собрать корзину.

ЯЗЫК: отвечай на языке пользователя. По умолчанию русский; если lang=kk или пользователь пишет по-казахски — отвечай по-казахски.

ИСТОЧНИК ПРАВДЫ — ТОЛЬКО ИНСТРУМЕНТЫ:
- Любые цены, остатки, характеристики, сертификаты бери ТОЛЬКО из результатов инструментов этого диалога. Никогда не придумывай и не вспоминай цифры «из памяти». Если инструмент ничего не вернул — так и скажи и предложи эскалацию менеджеру.
- Цены указывай в тенге (₸), остатки — в штуках (шт). Если price = null — «цена по запросу».
- Артикул или код товара в запросе (например «027228», «200300285_») — сначала get_product(article=...), при пустом результате — search_products.
- Если товар найден, но quantity = 0 / stock_status = out_of_stock — ОБЯЗАТЕЛЬНО вызови find_analogs и объясни, чем каждый аналог подходит (те же параметры: ток, полюса, бренд/класс, назначение). Сообщи, что сам товар можно заказать под заказ через менеджера.
- Условия покупки (доставка, оплата, минимальный заказ/сумма, возврат, гарантия, контакты, режим работы) — только через get_purchase_terms; отвечай конкретно: суммы, города, сроки, пороги.
- Сертификаты: если в карточке товара (поле certificates) есть документы — дай их названия и ссылки. Если пусто — скажи, что сертификаты и паспорта предоставляются по запросу менеджером (не выдумывай ссылки).

КОРЗИНА — ТОЛЬКО С ЯВНОГО ПОДТВЕРЖДЕНИЯ:
- Ты НЕ можешь класть товар в корзину. Ты можешь только ПРЕДЛОЖИТЬ через propose_add_to_cart, после чего корзина НЕ изменена, пока пользователь явно не подтвердит («да, добавь» или кнопка «Подтвердить»).
- После propose_add_to_cart обязательно напиши: «Подтвердите: добавить <товар> — <N> шт?» (перечисли все позиции с ценой и суммой). Никогда не пиши «добавил», «добавлено в корзину», пока это не подтверждено системой.
- Учитывай кратность упаковки (kratnost) и остаток: если пользователь просит больше, чем есть, предложи доступное количество; если количество не кратно упаковке — предупреди, что будет округлено.
- Если товар не в наличии — не предлагай его в корзину, предложи аналоги.
- Никогда не запрашивай и не обсуждай данные банковских карт и платёжные реквизиты клиента. Оплата происходит на сайте/через менеджера.

ВЛОЖЕНИЯ:
- Фото: прочитай артикул/модель/бренд/маркировку с изображения и найди товар через get_product/search_products. Если распознал несколько вариантов — проверь каждый.
- Спецификация (Excel/Word/PDF): для каждой позиции найди товар (по артикулу, затем по названию). Покажи компактную таблицу: позиция — найденный товар — цена — остаток. Затем ОДНИМ вызовом propose_add_to_cart предложи добавить все найденные позиции в наличии (количества из спецификации), и попроси подтверждение. Ненайденные позиции перечисли отдельно.

ЭСКАЛАЦИЯ: вызывай escalate_to_manager при крупных проектах и объёмных заявках, индивидуальных ценах/скидках, жалобах, когда товар не найден или вопрос выходит за рамки инструментов. Дай контакты из результата инструмента.

КОНТЕКСТ СТРАНИЦЫ: если в контексте указан page_url страницы товара на ekt.kz — пользователь смотрит именно этот товар; используй это для «есть ли в наличии?», «добавь 2 шт» и т.п. (найди товар по URL/названию через search_products или по id, если он известен из истории).

СТИЛЬ: коротко и по делу, без длинных вступлений. Markdown: **жирный** для ключевых цифр, списки для перечислений, ссылки на карточки товара. Карточки товаров виджет показывает сам — не дублируй все характеристики текстом, назови 3–5 ключевых. В конце, если уместно, предложи следующий шаг (добавить в корзину, посмотреть аналоги, уточнить количество)."""

# ---------------------------------------------------------------------------------------------------------------
# Tools (strict schemas: every property listed in `required`, optional ones are nullable)
# ---------------------------------------------------------------------------------------------------------------
_nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_nullable_int = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_products",
        "description": (
            "Полнотекстовый поиск по каталогу ekt.kz (>200 тыс. SKU): название, артикул, бренд, параметры "
            "(например 'автомат 16А 1P Legrand', 'кабель ВВГнг 3х2.5', '027228'). Возвращает карточки товаров с живым "
            "остатком и ценой. Используй для любого поиска товара; для точного артикула сначала get_product."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Поисковый запрос (название, артикул, параметры)."},
                "limit": {"type": "integer", "description": "Сколько результатов вернуть, 1..10 (обычно 5)."},
                "brand": {**_nullable_str, "description": "Фильтр по бренду (например 'Legrand') или null."},
                "category": {**_nullable_str, "description": "Фильтр по категории (slug/название) или null."},
            },
            "required": ["query", "limit", "brand", "category"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_product",
        "description": (
            "Карточка конкретного товара: живой остаток по складам, цена, характеристики, кратность упаковки, описание, "
            "сертификаты. Укажи product_id (если известен) ИЛИ article (артикул, например '027228' или '200300285_')."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {**_nullable_int, "description": "ID товара на ekt.kz или null."},
                "article": {**_nullable_str, "description": "Артикул товара или null."},
            },
            "required": ["product_id", "article"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_analogs",
        "description": (
            "Подобрать аналоги (замены) для товара — по категории, ключевым параметрам (ток, полюса, мощность...) и "
            "бренду. Возвращает карточки в наличии с полем reason (почему подходит). Обязательно вызывай, когда товар "
            "отсутствует на складе или пользователь просит замену."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "ID исходного товара."},
                "limit": {"type": "integer", "description": "Сколько аналогов вернуть, 1..5 (обычно 3)."},
            },
            "required": ["product_id", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_purchase_terms",
        "description": (
            "База знаний по условиям покупки в ekt.kz: доставка (города, сроки, стоимость), оплата (юр./физ. лица, "
            "Kaspi, безнал), минимальный заказ и партия, возврат и гарантия, контакты и график, как оформить заказ, "
            "сертификаты. Возвращает релевантные фрагменты с источниками."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Вопрос пользователя своими словами."},
                "topic": {
                    "anyOf": [{"type": "string", "enum": list(knowledge.TOPICS)}, {"type": "null"}],
                    "description": "Тема для сужения поиска или null.",
                },
            },
            "required": ["question", "topic"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_add_to_cart",
        "description": (
            "Создать ПРЕДЛОЖЕНИЕ добавить товары в корзину. Корзина НЕ меняется: пользователь должен явно подтвердить "
            "(кнопка «Подтвердить» или «да, добавь»). Одним вызовом можно предложить несколько позиций. Вызывай только "
            "для товаров в наличии и только когда пользователь попросил добавить/купить/положить в корзину."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Позиции для добавления.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "integer", "description": "ID товара."},
                            "qty": {"type": "integer", "description": "Количество, шт (>=1)."},
                        },
                        "required": ["product_id", "qty"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_cart",
        "description": "Текущее содержимое корзины пользователя (позиции, количества, сумма, ссылка на страницу корзины).",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "escalate_to_manager",
        "description": (
            "Передать вопрос менеджеру ekt.kz: возвращает контакты (телефон, WhatsApp, e-mail) и помечает диалог как "
            "требующий менеджера. Используй для крупных проектов, индивидуальных цен, жалоб, ненайденных товаров."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "Краткая причина эскалации (для менеджера)."}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]

# ---------------------------------------------------------------------------------------------------------------
# Confirmation / rejection detection (no LLM involved)
# ---------------------------------------------------------------------------------------------------------------
CONFIRM_WORDS = (
    "да", "ага", "угу", "добавь", "добавляй", "добавить", "подтверждаю", "подтверди", "подтвердить", "ок", "окей",
    "давай", "согласен", "согласна", "верно", "точно", "иә", "ия", "қос", "қосыңыз", "растаймын", "растау",
    "yes", "yep", "yeah", "confirm", "ok", "okay", "sure",
)
REJECT_WORDS = (
    "нет", "не", "отмена", "отмени", "отменить", "стоп", "жоқ", "болдырмау", "no", "nope", "cancel", "stop",
)
CONFIRM_RE = re.compile(r"(?<!\w)(?:" + "|".join(CONFIRM_WORDS) + r")(?!\w)", re.IGNORECASE)
REJECT_RE = re.compile(r"(?<!\w)(?:" + "|".join(REJECT_WORDS) + r")(?!\w)", re.IGNORECASE)
MAX_CONFIRM_WORDS = 8


def classify_confirmation(message: str, pending: dict[str, Any]) -> str | None:
    """Return 'confirm' / 'reject' when a short message explicitly answers the pending proposal, else None.

    Rules: at most 8 words; a rejection word wins over a confirmation word ("не добавляй" -> reject); numbers are
    allowed only when they equal a proposed quantity ("да, 2 шт" confirms, "добавь 5" goes to the LLM to re-propose).
    """
    text = message.strip()
    words = re.findall(r"\w+", text)
    if not words or len(words) > MAX_CONFIRM_WORDS:
        return None
    if REJECT_RE.search(text):
        return "reject"
    if not CONFIRM_RE.search(text):
        return None
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    proposed = {int(it["qty"]) for it in pending.get("items", [])}
    if numbers and not all(n in proposed for n in numbers):
        return None
    return "confirm"


DIRECT_ADD_RE = re.compile(r"\bid\s*[:#]?\s*(\d+)\b.*?(\d+)\s*(?:шт|дана|pcs)", re.IGNORECASE | re.DOTALL)


def parse_direct_add(message: str) -> list[dict[str, int]]:
    """Parse the widget's button message «Добавь <name> (id 515291) — 2 шт» into propose() items (no LLM needed)."""
    return [{"product_id": int(pid), "qty": int(qty)} for pid, qty in DIRECT_ADD_RE.findall(message)]


# ---------------------------------------------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------------------------------------------
def fmt_money(value: float | int | None) -> str:
    if value is None:
        return "по запросу"
    return f"{int(round(value)):,}".replace(",", " ") + " ₸"


def render_confirmation(result: dict[str, Any], lang: str = "ru") -> str:
    """Human reply after cart_store.confirm(): applied items, clamping notes, skipped items and the cart link."""
    kk = lang == "kk"
    lines: list[str] = []
    if result["applied"]:
        lines.append("**Себетке қосылды:**" if kk else "**Добавлено в корзину:**")
        for it in result["applied"]:
            unit = "дана" if kk else "шт"
            lines.append(f"- {it['name']} — {it['qty']} {unit} × {fmt_money(it['price'])}")
    else:
        lines.append("Себетке ештеңе қосылмады." if kk else "В корзину ничего не добавлено.")
    if result["notes"]:
        lines.append("")
        lines.append("**Ескерту:**" if kk else "**Примечания:**")
        lines.extend(f"- {n}" for n in result["notes"])
    if result["skipped"]:
        lines.append("")
        lines.append("**Қосылмады:**" if kk else "**Не добавлено:**")
        lines.extend(f"- {s['name']} — {s['reason']}" for s in result["skipped"])
    cart = result["cart"]
    lines.append("")
    if kk:
        lines.append(f"Себет: {cart['count']} позиция, барлығы **{fmt_money(cart['total'])}** — [Себетті ашу]({cart['url']})")
    else:
        lines.append(f"Корзина: {cart['count']} поз., итого **{fmt_money(cart['total'])}** — [Открыть корзину]({cart['url']})")
    return "\n".join(lines)


def render_rejection(lang: str = "ru") -> str:
    return "Жарайды, себетке ештеңе қоспаймын." if lang == "kk" else "Хорошо, ничего не добавляю. Корзина без изменений."


def record_exchange(session: Session, user_text: str, assistant_text: str) -> None:
    """Append a plain user/assistant pair to the LLM history (used for button-driven confirm/reject)."""
    session.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
    session.messages.append({"role": "assistant", "content": [{"type": "text", "text": assistant_text}]})
    _trim_history(session)


def llm_configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(timeout=LLM_TIMEOUT, max_retries=1)
    return _client


# ---------------------------------------------------------------------------------------------------------------
# Per-turn state and tool execution
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class TurnState:
    session: Session
    products: list[dict[str, Any]] = field(default_factory=list)
    escalation: dict[str, Any] | None = None

    def add_products(self, cards: list[dict[str, Any]]) -> None:
        seen = {c["id"] for c in self.products}
        for c in cards:
            if c.get("id") not in seen:
                seen.add(c["id"])
                self.products.append(c)


def _card_for_llm(card: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Compact card for tool results: keeps what the model needs, drops the long tail."""
    out: dict[str, Any] = {
        k: card.get(k)
        for k in ("id", "name", "article", "brand", "price", "quantity", "in_stock", "stock_status", "url", "kratnost")
    }
    stores = [f"{s.get('name')}: {s.get('quantity')} шт" for s in card.get("stores") or [] if int(s.get("quantity") or 0) > 0]
    if stores:
        out["stores"] = stores
    if card.get("reason"):
        out["reason"] = card["reason"]
    props = card.get("properties") or {}
    if props:
        out["properties"] = dict(list(props.items())[: 30 if full else 12])
    certs = card.get("certificates") or []
    if certs:
        out["certificates"] = [{k: c.get(k) for k in ("title", "number", "valid_until", "url") if c.get(k)} for c in certs]
    if full and card.get("description"):
        out["description"] = str(card["description"])[:600]
    return out


def with_certificates(card: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        card["certificates"] = certificates.certificates_for(card, detail)
    except Exception:  # registry problems must never break a reply
        log.exception("certificates_for failed for %s", card.get("id"))
        card.setdefault("certificates", [])
    return card


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


async def _tool_search_products(inp: dict[str, Any], state: TurnState) -> str:
    query = str(inp.get("query") or "").strip()
    if not query:
        return "Ошибка: пустой запрос."
    limit = max(1, min(int(inp.get("limit") or 5), 10))
    found = catalog.get_by_article(query)
    seen = {p["id"] for p in found}
    for p in catalog.search(query, limit=limit, brand=inp.get("brand") or None, category=inp.get("category") or None):
        if p["id"] not in seen:
            seen.add(p["id"])
            found.append(p)
    found = found[:limit]
    if not found:
        return _dump({"results": [], "hint": "Ничего не найдено. Попробуй другой запрос (без бренда, по ключевым словам) или эскалацию."})
    cards = [with_certificates(c) for c in await catalog.product_cards(found)]
    state.add_products(cards)
    return _dump({"results": [_card_for_llm(c) for c in cards]})


async def _tool_get_product(inp: dict[str, Any], state: TurnState) -> str:
    pid = inp.get("product_id")
    article = str(inp.get("article") or "").strip()
    products: list[dict[str, Any]] = []
    if pid:
        product = catalog.get_product(int(pid))
        if product is None:  # not in the local index (dump is partial) — build from the live detail
            detail = await ekt_api.fetch_detail(int(pid))
            if detail:
                product = {k: detail.get(k) for k in ("id", "name", "article", "price", "image", "url")}
                product["brand"] = None
        if product:
            products.append(product)
    if not products and article:
        products = catalog.get_by_article(article) or catalog.search(article, limit=3)
    if not products:
        return _dump({"found": False, "hint": "Товар не найден по заданному id/артикулу. Попробуй search_products по названию."})
    products = products[:3]
    details = await asyncio.gather(*(ekt_api.fetch_detail(int(p["id"])) for p in products))
    cards = []
    for product, detail in zip(products, details):
        card = await catalog.product_card(product, detail, with_detail=False)
        cards.append(with_certificates(card, detail))
    state.add_products(cards)
    return _dump({"found": True, "products": [_card_for_llm(c, full=True) for c in cards]})


async def _tool_find_analogs(inp: dict[str, Any], state: TurnState) -> str:
    pid = int(inp.get("product_id") or 0)
    limit = max(1, min(int(inp.get("limit") or 3), 5))
    cards = await analogs.find_analogs(pid, limit=limit, only_in_stock=True)
    note = None
    if not cards:
        cards = await analogs.find_analogs(pid, limit=limit, only_in_stock=False)
        note = "Аналогов в наличии нет — показаны ближайшие позиции без остатка (под заказ)."
    cards = [with_certificates(c) for c in cards]
    state.add_products(cards)
    payload: dict[str, Any] = {"analogs": [_card_for_llm(c) for c in cards]}
    if note:
        payload["note"] = note
    if not cards:
        payload["hint"] = "Аналоги не найдены — предложи эскалацию менеджеру."
    return _dump(payload)


async def _tool_get_purchase_terms(inp: dict[str, Any], state: TurnState) -> str:
    question = str(inp.get("question") or "")
    topic = inp.get("topic") or None
    if topic not in knowledge.TOPICS:
        topic = None
    hits = knowledge.search_terms(question, topic=topic, limit=5)
    if not hits and topic:
        text = knowledge.get_topic(topic)
        hits = [{"source": topic, "title": topic, "text": text[:4000], "url": None}] if text else []
    if not hits:
        return _dump({"results": [], "contacts": MANAGER_CONTACTS, "hint": "В базе знаний нет ответа — дай контакты менеджера."})
    return _dump({"results": hits})


async def _tool_propose_add_to_cart(inp: dict[str, Any], state: TurnState) -> str:
    items = inp.get("items") or []
    try:
        action = await cart_store.propose(state.session.id, items)
    except ValueError as exc:
        return f"Ошибка: {exc}"
    public = {k: v for k, v in action.items() if not k.startswith("_")}
    return _dump(
        {
            "status": "proposal_created",
            "instruction": (
                "Предложение создано; корзина НЕ изменена. Попроси пользователя явно подтвердить "
                "(«Подтвердите: добавить ... — N шт?»). Не пиши, что товар добавлен. Обрати внимание на note у позиций."
            ),
            "pending_action": public,
        }
    )


async def _tool_get_cart(inp: dict[str, Any], state: TurnState) -> str:
    return _dump(cart_store.get_json(state.session.id))


async def _tool_escalate(inp: dict[str, Any], state: TurnState) -> str:
    reason = str(inp.get("reason") or "запрос клиента")
    state.escalation = {"reason": reason, "contacts": dict(MANAGER_CONTACTS)}
    try:
        extra = knowledge.get_topic("contacts")[:1500]
    except Exception:
        extra = ""
    return _dump({"escalated": True, "contacts": MANAGER_CONTACTS, "office_info": extra})


TOOL_HANDLERS = {
    "search_products": _tool_search_products,
    "get_product": _tool_get_product,
    "find_analogs": _tool_find_analogs,
    "get_purchase_terms": _tool_get_purchase_terms,
    "propose_add_to_cart": _tool_propose_add_to_cart,
    "get_cart": _tool_get_cart,
    "escalate_to_manager": _tool_escalate,
}


async def _run_tool(block: Any, state: TurnState) -> dict[str, Any]:
    """Execute one tool_use block; errors are reported back to the model instead of raising."""
    handler = TOOL_HANDLERS.get(block.name)
    tool_input = block.input if isinstance(block.input, dict) else {}
    try:
        if handler is None:
            raise KeyError(f"unknown tool {block.name}")
        content = await handler(tool_input, state)
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}
    except Exception as exc:
        log.exception("tool %s failed", block.name)
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Ошибка инструмента: {exc}", "is_error": True}


# ---------------------------------------------------------------------------------------------------------------
# History management
# ---------------------------------------------------------------------------------------------------------------
def _has_block(msg: dict[str, Any], kind: str) -> bool:
    content = msg.get("content")
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == kind for b in content)


def _trim_history(session: Session) -> None:
    """Keep the last HISTORY_LIMIT messages, never starting with an orphaned tool_result or an assistant turn."""
    msgs = session.messages
    if len(msgs) > HISTORY_LIMIT:
        msgs = msgs[-HISTORY_LIMIT:]
    while msgs and (msgs[0]["role"] != "user" or _has_block(msgs[0], "tool_result")):
        msgs = msgs[1:]
    session.messages = msgs


def _serialize_content(blocks: list[Any]) -> list[dict[str, Any]]:
    return [b.model_dump(mode="json", exclude_none=True) for b in blocks]


def _context_block(session: Session, lang: str, page_url: str | None, pending: dict[str, Any] | None) -> str:
    cart = cart_store.get_json(session.id)
    if cart["count"]:
        items = "; ".join(f"{i['name']} × {i['qty']} (id {i['product_id']})" for i in cart["items"])
        cart_line = f"{cart['count']} поз. на {fmt_money(cart['total'])}: {items}"
    else:
        cart_line = "пусто"
    if pending:
        items = "; ".join(f"{i['name']} — {i['qty']} шт (макс {i['max_qty']}, id {i['product_id']})" for i in pending["items"])
        pending_line = f"{pending['action_id']}: {items} — пользователь ЕЩЁ НЕ подтвердил"
    else:
        pending_line = "нет"
    last = "; ".join(f"id {p['id']} — {p['name']}" for p in session.last_products[:5]) or "—"
    return (
        "[Служебный контекст — не показывать пользователю]\n"
        f"Язык ответа: {lang}\n"
        f"Страница пользователя: {page_url or '—'}\n"
        f"Корзина: {cart_line}\n"
        f"Ожидает подтверждения: {pending_line}\n"
        f"Последние показанные товары: {last}"
    )


def _user_content(message: str, attachments: list[Any], context: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for att in attachments:
        if getattr(att, "kind", None) == "image":
            content.append(attachments_mod.image_block(att))
            content.append({"type": "text", "text": f"Вложение (фото): {att.filename}"})
        else:
            content.append({"type": "text", "text": attachments_mod.text_for_llm(att)})
    text = message.strip() or ("Посмотри вложение." if attachments else "(пустое сообщение)")
    content.append({"type": "text", "text": f"{text}\n\n{context}"})
    return content


def _response(session: Session, reply: str, state: TurnState | None, *, cart_updated: bool, t0: float) -> dict[str, Any]:
    products = (state.products if state else [])[:MAX_PRODUCTS]
    if products:
        session.last_products = products
    return {
        "session_id": session.id,
        "reply": reply,
        "products": products,
        "pending_action": _public_pending(cart_store.pending(session.id)),
        "cart": cart_store.get_json(session.id),
        "cart_updated": cart_updated,
        "escalation": state.escalation if state else None,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def _public_pending(action: dict[str, Any] | None) -> dict[str, Any] | None:
    return {k: v for k, v in action.items() if not k.startswith("_")} if action else None


NO_KEY_REPLY = {
    "ru": (
        "LLM-ассистент пока не настроен: на сервере нет ANTHROPIC_API_KEY. Поиск по каталогу, корзина и подтверждения "
        "работают, а свободный диалог появится после добавления ключа в `.env`."
    ),
    "kk": "LLM-ассистент әлі бапталмаған: серверде ANTHROPIC_API_KEY жоқ. Кілт `.env` файлына қосылғаннан кейін диалог іске қосылады.",
}


# ---------------------------------------------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------------------------------------------
async def chat(session: Session, message: str, attachments: list[Any], page_url: str | None, lang: str) -> dict[str, Any]:
    """Handle one user turn and return a ChatResponse-shaped dict.

    Flow: explicit confirm/reject of a pending proposal is handled without the LLM; everything else goes through the
    tool loop (capped at MAX_ITERATIONS). Products returned by tools this turn are collected into `products`.
    """
    t0 = time.perf_counter()
    lang = "kk" if lang == "kk" else "ru"
    session.lang = lang
    if page_url:
        session.page_url = page_url
    sid = session.id
    pending = cart_store.pending(sid)

    # (a) confirmation gate — deterministic, no LLM
    if pending:
        verdict = classify_confirmation(message, pending)
        if verdict == "confirm":
            try:
                result = await cart_store.confirm(sid, pending["action_id"])
            except PendingActionError as exc:
                reply = str(exc)
                record_exchange(session, message, reply)
                return _response(session, reply, None, cart_updated=False, t0=t0)
            reply = render_confirmation(result, lang)
            record_exchange(session, message, reply)
            return _response(session, reply, None, cart_updated=bool(result["applied"]), t0=t0)
        if verdict == "reject":
            cart_store.reject(sid, pending["action_id"])
            pending = None
            if len(re.findall(r"\w+", message)) <= 3:
                reply = render_rejection(lang)
                record_exchange(session, message, reply)
                return _response(session, reply, None, cart_updated=False, t0=t0)

    state = TurnState(session=session)

    # (b) no credential: keep the button-driven add flow working, explain the rest
    if not llm_configured():
        direct = parse_direct_add(message)
        if direct:
            try:
                action = await cart_store.propose(sid, direct)
                items = ", ".join(f"{i['name']} — {i['qty']} шт (в наличии {i['max_qty']})" for i in action["items"])
                reply = f"Подтвердите: добавить {items}?"
            except ValueError as exc:
                reply = str(exc)
        else:
            reply = NO_KEY_REPLY[lang]
        record_exchange(session, message, reply)
        return _response(session, reply, state, cart_updated=False, t0=t0)

    # (c) LLM tool loop
    context = _context_block(session, lang, page_url or session.page_url, pending)
    checkpoint = len(session.messages)
    session.messages.append({"role": "user", "content": _user_content(message, attachments, context)})
    reply = ""
    try:
        reply = await _run_llm(session, state)
    except anthropic.AuthenticationError:
        del session.messages[checkpoint:]
        reply = "Ключ Anthropic API не принят сервером (AuthenticationError). Проверьте ANTHROPIC_API_KEY."
    except anthropic.RateLimitError:
        del session.messages[checkpoint:]
        reply = "Сервис перегружен, попробуйте повторить через несколько секунд."
    except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
        del session.messages[checkpoint:]
        log.warning("LLM API error: %s", exc)
        reply = "Не удалось связаться с AI-сервисом. Попробуйте ещё раз или свяжитесь с менеджером: " + MANAGER_CONTACTS["phone"]
    except Exception:
        del session.messages[checkpoint:]
        log.exception("chat turn failed")
        reply = "Произошла внутренняя ошибка. Попробуйте переформулировать запрос или позвоните менеджеру: " + MANAGER_CONTACTS["phone"]
    _trim_history(session)
    return _response(session, reply, state, cart_updated=False, t0=t0)


async def _run_llm(session: Session, state: TurnState) -> str:
    """Manual agentic loop: call the model, execute all tool_use blocks, feed results back; return the reply text."""
    client = _get_client()
    interim = ""  # text emitted alongside tool calls ("Сейчас проверю...") — used only if no final text arrives
    for _ in range(MAX_ITERATIONS):
        response = await client.messages.create(
            model=config.LLM_MODEL,
            max_tokens=config.LLM_MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=session.messages,
            output_config={"effort": config.LLM_EFFORT},
        )
        log.info(
            "llm stop=%s in=%s cached=%s out=%s",
            response.stop_reason,
            response.usage.input_tokens,
            getattr(response.usage, "cache_read_input_tokens", None),
            response.usage.output_tokens,
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "refusal":
            return "Я не могу помочь с этим запросом. Обратитесь к менеджеру: " + MANAGER_CONTACTS["phone"]

        if response.stop_reason == "max_tokens" or not tool_uses:
            # Final answer (or a truncated one): drop unexecutable tool_use blocks so the history stays valid.
            content = _serialize_content([b for b in response.content if b.type != "tool_use"])
            if not any(b.get("type") == "text" for b in content):
                text = text or "Не удалось сформировать ответ. Уточните, пожалуйста, запрос."
                content = [{"type": "text", "text": text}]
            session.messages.append({"role": "assistant", "content": content})
            if response.stop_reason == "pause_turn":
                continue
            return text or interim or "Не удалось сформировать ответ."

        session.messages.append({"role": "assistant", "content": _serialize_content(list(response.content))})
        results = await asyncio.gather(*(_run_tool(b, state) for b in tool_uses))
        session.messages.append({"role": "user", "content": list(results)})
        interim = text or interim
    tail = "Я сделал много шагов, но не успел завершить ответ. Уточните запрос, пожалуйста."
    return f"{interim}\n\n{tail}" if interim else tail
