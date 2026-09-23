"""Thin async client for the ekt.kz test API (basic auth).

Endpoints (verified 2026-09-23):
  GET /api/products?page=N&per_page=M        -> {"page","per_page","count","items":[{id,name,article,price,image,url,url_api_detail,offers}]}
  GET /api/products/detail?id=ID             -> {id,name,article,description,price,quantity,stores:[{id,name,quantity}],image,url,offers,properties:{...}}
There is NO search endpoint. Responses may carry trailing bytes after the JSON — always use raw_decode.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from . import config

_decoder = json.JSONDecoder()
_client: httpx.AsyncClient | None = None
_detail_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_sem = asyncio.Semaphore(8)


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=config.EKT_API_BASE,
            auth=(config.EKT_API_USER, config.EKT_API_PASS),
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "ekt-ai-assistant/0.1"},
        )
    return _client


def parse_json(raw: str) -> Any:
    obj, _end = _decoder.raw_decode(raw.lstrip("﻿ \n\r\t"))
    return obj


async def fetch_detail(product_id: int, *, ttl: int | None = None) -> dict[str, Any] | None:
    """Live product detail (stock per store, properties, description). Cached for DETAIL_CACHE_TTL seconds."""
    ttl = config.DETAIL_CACHE_TTL if ttl is None else ttl
    now = time.time()
    hit = _detail_cache.get(product_id)
    if hit and now - hit[0] < ttl:
        return hit[1]
    async with _sem:
        try:
            r = await _get_client().get("/products/detail", params={"id": product_id})
            r.raise_for_status()
            data = parse_json(r.text)
        except Exception:
            return hit[1] if hit else None
    if not isinstance(data, dict) or "id" not in data:
        return None
    _detail_cache[product_id] = (now, data)
    return data


async def fetch_details(product_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Fetch many details concurrently (the endpoint answers in ~0.2 s; 8 in flight)."""
    ids = list(dict.fromkeys(int(i) for i in product_ids))
    results = await asyncio.gather(*(fetch_detail(i) for i in ids))
    return {i: d for i, d in zip(ids, results) if d}


async def fetch_list_page(page: int, per_page: int = 100) -> dict[str, Any]:
    r = await _get_client().get("/products", params={"page": page, "per_page": per_page})
    r.raise_for_status()
    return parse_json(r.text)


def total_quantity(detail: dict[str, Any]) -> int:
    """Sellable stock = sum over real stores, excluding service warehouses (defective, marketing, transit...)."""
    stores = detail.get("stores") or []
    if not stores:
        return int(detail.get("quantity") or 0)
    return sum(int(s.get("quantity") or 0) for s in stores if not is_service_store(s.get("name", "")))


SERVICE_STORE_MARKERS = ("брак", "маркетинг", "перемещение", "образцы", "витрина", "востановленный", "восстановленный")


def is_service_store(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in SERVICE_STORE_MARKERS)


def stores_in_stock(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in detail.get("stores") or [] if int(s.get("quantity") or 0) > 0 and not is_service_store(s.get("name", ""))]
