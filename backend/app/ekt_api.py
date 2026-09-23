"""Thin async client for the ekt.kz test API (basic auth).

Endpoints (verified 2026-09-23):
  GET /api/products?page=N&per_page=M        -> {"page","per_page","count","items":[{id,name,article,price,image,url,url_api_detail,offers}]}
  GET /api/products/detail?id=ID             -> {id,name,article,description,price,quantity,stores:[{id,name,quantity}],image,url,offers,properties:{...}}
There is NO search endpoint. Responses may carry trailing bytes after the JSON — always use raw_decode.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
from typing import Any

import httpx

from . import config

log = logging.getLogger("ekt.api")
_decoder = json.JSONDecoder()
DETAIL_HTTP_TIMEOUT = float(__import__("os").getenv("DETAIL_HTTP_TIMEOUT", "12"))  # ekt.kz answers in 0.2-8 s; fall back to the DB after this
_client: httpx.AsyncClient | None = None
_detail_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_sem = asyncio.Semaphore(24)  # the endpoint copes with ~24 in flight (measured 24 calls in 3.5 s)


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
    """Live product detail (stock per store, properties, description). Cached for DETAIL_CACHE_TTL seconds.

    Args:
        product_id: ekt.kz product id.
        ttl: cache lifetime override in seconds. ``0`` means "live only": the API must answer now, and on any
            error ``None`` is returned instead of a possibly stale cached record (used when confirming a cart
            action, where an outdated stock figure must never be trusted).
    """
    ttl = config.DETAIL_CACHE_TTL if ttl is None else ttl
    now = time.time()
    hit = _detail_cache.get(product_id)
    if hit and now - hit[0] < ttl:
        return hit[1]
    async with _sem:
        try:
            r = await _get_client().get("/products/detail", params={"id": product_id}, timeout=DETAIL_HTTP_TIMEOUT)
            r.raise_for_status()
            data = parse_json(r.text)
        except Exception as exc:
            if ttl == 0:
                return None
            if hit:
                return hit[1]
            stale = _db_get(product_id)
            if stale is not None:
                log.warning("detail %s: live fetch failed (%s); serving snapshot from %s", product_id, type(exc).__name__, stale.get("stale_since"))
            return stale
    if not isinstance(data, dict) or "id" not in data:
        return None
    _detail_cache[product_id] = (now, data)
    _db_put(product_id, data, now)
    return data


# ---- persistent detail snapshot (table product_details in catalog.sqlite; also filled by scripts/import_details.py) ----
_db_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection | None:
    global _db_conn
    if _db_conn is None:
        try:
            _db_conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _db_conn.execute("CREATE TABLE IF NOT EXISTS product_details (id INTEGER PRIMARY KEY, fetched_at REAL NOT NULL, quantity INTEGER, data TEXT NOT NULL)")
            _db_conn.commit()
        except sqlite3.Error as exc:
            log.warning("detail snapshot DB unavailable: %s", exc)
            return None
    return _db_conn


def _db_get(product_id: int) -> dict[str, Any] | None:
    con = _db()
    if con is None:
        return None
    with _db_lock:
        row = con.execute("SELECT fetched_at, data FROM product_details WHERE id=?", (int(product_id),)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[1])
    except json.JSONDecodeError:
        return None
    data["stale_since"] = time.strftime("%d.%m %H:%M", time.localtime(row[0]))
    data["stale_age_s"] = int(time.time() - row[0])
    return data


def _db_put(product_id: int, data: dict[str, Any], fetched_at: float) -> None:
    con = _db()
    if con is None:
        return
    try:
        with _db_lock:
            con.execute(
                "INSERT INTO product_details(id, fetched_at, quantity, data) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at, quantity=excluded.quantity, data=excluded.data",
                (int(product_id), fetched_at, data.get("quantity"), json.dumps(data, ensure_ascii=False)),
            )
            con.commit()
    except sqlite3.Error as exc:
        log.warning("detail snapshot write failed: %s", exc)


async def fetch_details(product_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Fetch many details concurrently (2-3 s per call today; up to 24 in flight)."""
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
