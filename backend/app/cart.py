"""Prototype cart with live-stock validation.

The LLM can only *propose* (`propose`); the cart changes only when `confirm` is called with the matching
action_id, coming from an explicit chat confirmation. A direct product-card click uses `add_requested`,
which runs the same validation and final stock check without another confirmation.
At confirmation time stock is re-fetched live, quantities are clamped to stock and rounded down to the pack
multiplicity (KRATNOST_MIN). Everything is in memory (hackathon prototype).
"""
from __future__ import annotations

import asyncio
import copy
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config, ekt_api

PENDING_TTL = 10 * 60  # seconds a proposal stays valid


class PendingActionError(Exception):
    """No pending action, wrong action_id or the proposal has expired."""


def kratnost_of(detail: dict[str, Any] | None) -> int:
    """Pack multiplicity from properties.KRATNOST_MIN (int >= 1, default 1)."""
    props = (detail or {}).get("properties") or {}
    raw = props.get("KRATNOST_MIN")
    try:
        k = int(float(str(raw).replace(",", ".")))
    except (TypeError, ValueError):
        return 1
    return k if k >= 1 else 1


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _line_sum(qty: int, price: float | int | None) -> float | int:
    return qty * (price or 0)


class CartStore:
    """Per-session carts and pending proposals."""

    def __init__(self) -> None:
        self._carts: dict[str, dict[int, dict[str, Any]]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._completed_adds: dict[tuple[str, str], tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}

    # ---- read ---------------------------------------------------------------------------------------------------
    def get_json(self, session_id: str) -> dict[str, Any]:
        """Cart in the API_CONTRACT shape (items, count, total, url)."""
        items = []
        for it in self._carts.get(session_id, {}).values():
            row = dict(it)
            row["sum"] = _line_sum(row["qty"], row.get("price"))
            items.append(row)
        return {
            "items": items,
            "count": len(items),
            "total": sum(r["sum"] for r in items),
            "url": f"{config.PUBLIC_BASE_URL}/cart/{session_id}",
        }

    def pending(self, session_id: str) -> dict[str, Any] | None:
        """Current proposal for the session, or None (expired proposals are dropped)."""
        action = self._pending.get(session_id)
        if action is None:
            return None
        if action["_expires_ts"] < time.time():
            del self._pending[session_id]
            return None
        return copy.deepcopy(action)

    async def add_requested(self, session_id: str, items: list[dict[str, Any]], *,
                            request_id: str | None = None) -> dict[str, Any]:
        """Apply an explicit customer add request in one step, checking live stock.

        Clicking the widget button is the customer's explicit add request.
        Reuse proposal validation and the final live-stock check without a
        second confirmation step; chat-agent proposals remain separate.
        """
        request_items = copy.deepcopy(items)
        if request_id:
            key = (session_id, request_id)
            previous = self._completed_adds.get(key)
            if previous and time.time() - previous[0] < PENDING_TTL:
                if previous[1] != request_items:
                    raise ValueError("request_id уже использован для другого товара или количества")
                result = copy.deepcopy(previous[2])
                result["cart"] = self.get_json(session_id)
                return result
        action = await self.propose(session_id, items)
        result = await self.confirm(session_id, action["action_id"])
        if request_id:
            self._completed_adds[(session_id, request_id)] = (time.time(), request_items, copy.deepcopy(result))
            if len(self._completed_adds) > 1000:
                cutoff = time.time() - PENDING_TTL
                self._completed_adds = {key: value for key, value in self._completed_adds.items() if value[0] >= cutoff}
        return result

    # ---- propose / confirm / reject ---------------------------------------------------------------------------
    async def propose(self, session_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Create a pending add-to-cart action (replaces any previous one). Nothing is added yet.

        Args:
            session_id: chat session.
            items: [{product_id, qty, name?}] — duplicates are merged; `name` is only a fallback label.

        Returns:
            The pending action dict: action_id, type, items[{product_id,name,article,url,qty,max_qty,price,kratnost,note}],
            expires_at (ISO-8601).
        """
        merged: dict[int, dict[str, Any]] = {}
        bad_qty = False
        for raw in items:
            try:
                pid = int(raw["product_id"])
                qty = int(raw.get("qty") if raw.get("qty") is not None else 1)
            except (KeyError, TypeError, ValueError):
                continue
            if pid <= 0 or qty < 1:  # a malformed tool call must not become a proposal the user confirms by reflex
                bad_qty = True
                continue
            entry = merged.setdefault(pid, {"qty": 0, "name": raw.get("name")})
            entry["qty"] += qty
        if not merged:
            raise ValueError("Некорректное количество: укажи целое число >= 1" if bad_qty else "Нет корректных позиций для добавления")

        details = await asyncio.gather(*(ekt_api.fetch_detail(pid) for pid in merged))
        out_items = []
        for (pid, entry), detail in zip(merged.items(), details):
            qty = entry["qty"]
            name = (detail or {}).get("name") or entry.get("name") or f"Товар #{pid}"
            price = (detail or {}).get("price")
            max_qty = ekt_api.total_quantity(detail) if detail else 0
            k = kratnost_of(detail)
            existing = self._carts.get(session_id, {}).get(pid, {}).get("qty", 0)
            room = max_qty - existing
            note: str | None = None
            if detail is None:
                note = "не удалось проверить остаток — добавление возможно только после проверки"
            elif max_qty <= 0:
                note = "нет в наличии"
            elif qty < k:
                note = f"минимальная партия {k} шт — запрошено {qty} шт, товар не будет добавлен"
            elif room < k:
                note = f"доступно для добавления {max(room, 0)} шт — меньше минимальной партии {k} шт"
            elif qty > room:
                addable = (room // k) * k
                note = f"на складе только {max_qty} шт; доступно для добавления {room} шт — будет добавлено {addable} шт"
            elif qty % k:
                note = f"кратность упаковки {k} шт — будет добавлено {(qty // k) * k} шт"
            out_items.append(
                {
                    "product_id": pid,
                    "name": name,
                    "article": (detail or {}).get("article"),
                    "url": (detail or {}).get("url"),
                    "image": (detail or {}).get("image"),
                    "qty": qty,
                    "max_qty": max_qty,
                    "price": price,
                    "kratnost": k,
                    "note": note,
                }
            )
        now = time.time()
        action = {
            "action_id": "act_" + uuid.uuid4().hex,
            "type": "add_to_cart",
            "items": out_items,
            "created_at": _iso(now),
            "expires_at": _iso(now + PENDING_TTL),
            "_expires_ts": now + PENDING_TTL,
        }
        self._pending[session_id] = action
        return copy.deepcopy(action)

    async def confirm(self, session_id: str, action_id: str) -> dict[str, Any]:
        """Apply the pending action: re-check live stock, clamp and round to pack multiplicity, add to cart.

        Returns:
            {'cart': cart_json, 'applied': [{product_id,name,qty,requested_qty,price}],
             'skipped': [{product_id,name,reason}], 'notes': [str]}

        Raises:
            PendingActionError: nothing pending, expired, or action_id mismatch.
        """
        action = self.pending(session_id)
        if action is None:
            raise PendingActionError("Нет предложения, ожидающего подтверждения (или оно истекло)")
        if action["action_id"] != action_id:
            raise PendingActionError("Это предложение устарело — подтвердите актуальное")
        del self._pending[session_id]

        cart = self._carts.setdefault(session_id, {})
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        notes: list[str] = []
        details = await asyncio.gather(*(ekt_api.fetch_detail(it["product_id"], ttl=0) for it in action["items"]))
        for item, detail in zip(action["items"], details):
            pid, name, requested = item["product_id"], item["name"], int(item["qty"])
            if detail is None:
                skipped.append({"product_id": pid, "name": name, "reason": "не удалось проверить остаток на складе"})
                continue
            stock = ekt_api.total_quantity(detail)
            k = kratnost_of(detail)
            existing = cart[pid]["qty"] if pid in cart else 0
            room = stock - existing
            if stock <= 0:
                skipped.append({"product_id": pid, "name": name, "reason": "нет в наличии"})
                continue
            if requested < k:
                skipped.append({"product_id": pid, "name": name,
                                "reason": f"минимальная партия {k} шт; запрошено {requested} шт"})
                continue
            if room < k:
                reason = (
                    f"в корзине уже {existing} шт — это всё, что есть на складе"
                    if existing
                    else f"остаток {stock} шт меньше минимальной партии {k} шт"
                )
                skipped.append({"product_id": pid, "name": name, "reason": reason})
                continue
            qty = (min(requested, room) // k) * k
            price = detail.get("price")
            cart[pid] = {
                "product_id": pid,
                "name": detail.get("name") or name,
                "article": detail.get("article") or item.get("article"),
                "qty": existing + qty,
                "price": price,
                "url": detail.get("url") or item.get("url"),
                "image": detail.get("image") or item.get("image"),
                "kratnost": k,
            }
            applied.append({"product_id": pid, "name": cart[pid]["name"], "qty": qty,
                            "requested_qty": requested, "price": price, "kratnost": k})
            if qty < requested and requested > room:
                notes.append(f"{name}: добавлено {qty} шт вместо {requested} — на складе {stock} шт")
            elif qty != requested:
                notes.append(f"{name}: добавлено {qty} шт вместо {requested} — кратность упаковки {k} шт")
            if price is None:
                notes.append(f"{name}: цена по запросу (уточнит менеджер)")
        return {"cart": self.get_json(session_id), "applied": applied, "skipped": skipped, "notes": notes}

    def reject(self, session_id: str, action_id: str | None = None) -> None:
        """Drop the pending action (only if `action_id` matches when given)."""
        action = self._pending.get(session_id)
        if action and (action_id is None or action["action_id"] == action_id):
            del self._pending[session_id]

    # ---- direct edits (widget / cart page) ---------------------------------------------------------------------
    def remove(self, session_id: str, product_id: int) -> dict[str, Any]:
        self._carts.get(session_id, {}).pop(int(product_id), None)
        return self.get_json(session_id)

    def clear(self, session_id: str) -> dict[str, Any]:
        self._carts.pop(session_id, None)
        return self.get_json(session_id)


cart_store = CartStore()
