"""Download the ekt.kz product list (GET /api/products) into data/dump/list_NNNN.json pages.

Usage (from ``backend/``):
    uv run python scripts/dump_catalog.py                    # full dump -> data/dump/ (all pages)
    uv run python scripts/dump_catalog.py --pages 2          # quick start: only the first two pages
    uv run python scripts/dump_catalog.py --per-page 500 --pages 1 --out /tmp/dump
Then build the search index:  uv run python scripts/build_index.py   (reads data/dump by default).

Behaviour:
    * resumable - pages already on disk are kept and skipped;
    * ``--workers`` pages are fetched concurrently, in batches, so the run stops early at the first short page
      (fewer than ``--per-page`` items) or when the API wraps around to page 1 (it does past the last page,
      verified 2026-09-23: 15 035 items = 3 full pages of 5000 + 35);
    * responses may carry trailing bytes after the JSON, so they are parsed with ``raw_decode``;
    * credentials and base URL come from ``app.config`` (``EKT_API_BASE``, ``EKT_API_USER``, ``EKT_API_PASS``).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

MAX_PAGES = 400  # hard cap: protects against an endless wrap-around loop when nothing else stops the run
ATTEMPTS = 5
PAGE_TIMEOUT = 300.0  # a 5000-item page takes ~80 s on the test API
_decoder = json.JSONDecoder()


def page_file(out_dir: Path, page: int) -> Path:
    """Path of the JSON file for ``page`` (``list_0001.json`` ...), the layout build_index.py expects."""
    return out_dir / f"list_{page:04d}.json"


def fetch_page(client: httpx.Client, page: int, per_page: int) -> dict[str, Any] | None:
    """Fetch one list page with retries.

    Args:
        client: authenticated httpx client with ``base_url`` = ``config.EKT_API_BASE``.
        page: 1-based page number.
        per_page: page size requested from the API.

    Returns:
        The decoded page (``{"page", "per_page", "count", "items"}``) or ``None`` after ``ATTEMPTS`` failures.
    """
    for attempt in range(1, ATTEMPTS + 1):
        started = time.time()
        try:
            r = client.get("/products", params={"page": page, "per_page": per_page})
            r.raise_for_status()
            obj, end = _decoder.raw_decode(r.text.lstrip("﻿ \n\r\t"))
            if not isinstance(obj, dict) or "items" not in obj:
                raise ValueError("unexpected payload")
            print(f"page {page}: count={obj.get('count')} {time.time() - started:.1f}s trailing={len(r.text) - end}", flush=True)
            return obj
        except Exception as exc:  # network / HTTP / JSON - all retried the same way
            print(f"  page {page} attempt {attempt}/{ATTEMPTS} error: {exc}", flush=True)
            time.sleep(3 * attempt)
    return None


def load_or_fetch(client: httpx.Client, out_dir: Path, page: int, per_page: int) -> tuple[int, dict[str, Any] | None, bool]:
    """Return ``(page, payload, from_disk)``; a page already on disk is read instead of fetched."""
    path = page_file(out_dir, page)
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            return page, json.load(fh), True
    return page, fetch_page(client, page, per_page), False


def first_id(payload: dict[str, Any]) -> int | None:
    items = payload.get("items") or []
    return int(items[0]["id"]) if items else None


def dump(out_dir: Path, per_page: int, pages: int, workers: int) -> int:
    """Download list pages into ``out_dir``.

    Args:
        out_dir: target directory (created if missing).
        per_page: page size (the API accepts up to 5000).
        pages: maximum number of pages to keep, ``0`` = until the catalog ends.
        workers: concurrent requests.

    Returns:
        Process exit code: 0 on success, 1 if at least one page could not be downloaded.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = pages or MAX_PAGES
    saved = failed = 0
    items_total = 0
    anchor: int | None = None  # first product id of page 1 - used to detect the wrap-around
    stop_reason = f"page limit {limit}"
    with httpx.Client(
        base_url=config.EKT_API_BASE,
        auth=(config.EKT_API_USER, config.EKT_API_PASS),
        timeout=httpx.Timeout(PAGE_TIMEOUT, connect=15.0),
        headers={"User-Agent": "ekt-ai-assistant/dump_catalog"},
    ) as client, ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        page = 1
        while page <= limit:
            batch = list(range(page, min(page + max(1, workers), limit + 1)))
            results = list(pool.map(lambda p: load_or_fetch(client, out_dir, p, per_page), batch))
            done = False
            for p, payload, from_disk in results:  # in page order: the first stop condition wins
                if payload is None:
                    failed += 1
                    stop_reason = f"page {p} failed"
                    done = True
                    break
                if p == 1:
                    anchor = first_id(payload)
                elif anchor is not None and first_id(payload) == anchor:
                    stop_reason = f"page {p} wraps around to page 1"
                    done = True
                    break
                if not from_disk:
                    with page_file(out_dir, p).open("w", encoding="utf-8") as fh:
                        json.dump(payload, fh, ensure_ascii=False)
                saved += 1
                n = len(payload.get("items") or [])
                items_total += n
                if n < per_page:
                    stop_reason = f"short page {p} ({n} < {per_page})"
                    done = True
                    break
            if done:
                break
            page = batch[-1] + 1
    print(f"DONE {saved} pages, {items_total} items -> {out_dir} (stopped: {stop_reason})", flush=True)
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump the ekt.kz product list into list_NNNN.json pages.")
    ap.add_argument("--out", default=str(config.DATA_DIR / "dump"), help="output directory (default: backend/data/dump)")
    ap.add_argument("--per-page", type=int, default=5000, help="items per page, max 5000 (default: 5000)")
    ap.add_argument("--pages", type=int, default=0, help="stop after N pages; 0 = the whole catalog (default)")
    ap.add_argument("--workers", type=int, default=4, help="concurrent requests (default: 4)")
    args = ap.parse_args()
    sys.exit(dump(Path(args.out), max(1, min(args.per_page, 5000)), max(0, args.pages), args.workers))


if __name__ == "__main__":
    main()
