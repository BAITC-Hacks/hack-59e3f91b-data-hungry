#!/usr/bin/env python3
"""Resume-safe EKT catalog crawl. Stores API responses in a local SQLite DB.

Usage:
  python3 scripts/ekt_catalog_audit.py --list-only
  python3 scripts/ekt_catalog_audit.py --details-only

Credentials come from EKT_API_USER / EKT_API_PASSWORD or an interactive prompt.
The database is intentionally ignored by git.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import getpass
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = "https://ekt.kz/api/products"
DEFAULT_DB = Path("data/ekt_catalog.sqlite3")


class EktOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urllib.parse.urlparse(new_url)
        if parsed.scheme != "https" or parsed.hostname != "ekt.kz":
            raise ValueError("Refusing to forward API credentials to another host")
        return super().redirect_request(request, response, code, message, headers, new_url)


OPENER = urllib.request.build_opener(EktOnlyRedirect)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            list_json TEXT NOT NULL,
            detail_json TEXT,
            detail_error TEXT
        );
        CREATE TABLE IF NOT EXISTS pages (
            page INTEGER PRIMARY KEY,
            item_count INTEGER NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return db


def authorization_header() -> str:
    username = os.environ.get("EKT_API_USER") or input("EKT API user: ").strip()
    if not username:
        raise ValueError("EKT API user is required")
    password = os.environ.get("EKT_API_PASSWORD") or getpass.getpass("EKT API password: ")
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def fetch_json(url: str, auth: str, retries: int = 4) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": auth, "Accept": "application/json", "User-Agent": "DataHungryCatalogAudit/1.0"},
    )
    for attempt in range(retries):
        try:
            with OPENER.open(request, timeout=50) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ValueError(f"Expected JSON object from {url}")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise exc
        time.sleep(min(2 ** attempt, 8))
    raise AssertionError("Unreachable")


def crawl_listing(db: sqlite3.Connection, auth: str, per_page: int, max_pages: int | None) -> None:
    last_page = db.execute("SELECT page, item_count FROM pages ORDER BY page DESC LIMIT 1").fetchone()
    if last_page and last_page[1] < per_page:
        print(f"listing already complete at page={last_page[0]}", flush=True)
        return
    page = (last_page[0] if last_page else 0) + 1
    while max_pages is None or page <= max_pages:
        query = urllib.parse.urlencode({"page": page, "per_page": per_page})
        payload = fetch_json(f"{BASE_URL}?{query}", auth)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError(f"Page {page}: items is not a list")
        if payload.get("page") != page or payload.get("count") != len(items):
            raise ValueError(f"Page {page}: response page/count does not match items")
        ids = [item.get("id") for item in items if isinstance(item, dict)]
        if len(ids) != len(items) or len(set(ids)) != len(ids):
            raise ValueError(f"Page {page}: invalid or repeated IDs")
        before = db.total_changes
        with db:
            db.executemany(
                "INSERT INTO products(id, list_json) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET list_json=excluded.list_json",
                [(item["id"], json.dumps(item, ensure_ascii=False)) for item in items],
            )
            db.execute("INSERT OR REPLACE INTO pages(page, item_count) VALUES (?, ?)", (page, len(items)))
        total = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        print(f"listing page={page} items={len(items)} distinct_total={total} db_changes={db.total_changes-before}", flush=True)
        if not items or len(items) < per_page:
            break
        page += 1


def detail_url(item: dict) -> str:
    url = item.get("url_api_detail")
    if isinstance(url, str) and url.startswith("https://ekt.kz/api/products/detail?"):
        return url
    return f"{BASE_URL}/detail?" + urllib.parse.urlencode({"id": item["id"]})


def crawl_details(db: sqlite3.Connection, auth: str, workers: int, max_details: int | None) -> None:
    rows = db.execute(
        "SELECT id, list_json FROM products WHERE detail_json IS NULL ORDER BY id"
        + (f" LIMIT {int(max_details)}" if max_details is not None else "")
    ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"details pending={len(rows)} total={total} workers={workers}", flush=True)
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            futures = {
                pool.submit(fetch_json, detail_url(json.loads(raw)), auth): product_id
                for product_id, raw in batch
            }
            results = []
            for future in concurrent.futures.as_completed(futures):
                product_id = futures[future]
                try:
                    detail = future.result()
                    if detail.get("id") != product_id:
                        raise ValueError(f"ID mismatch for {product_id}")
                    results.append((json.dumps(detail, ensure_ascii=False), None, product_id))
                except Exception as exc:
                    failures += 1
                    results.append((None, f"{type(exc).__name__}: {exc}", product_id))
            with db:
                db.executemany(
                    "UPDATE products SET detail_json=?, detail_error=? WHERE id=?", results
                )
            completed = db.execute("SELECT COUNT(*) FROM products WHERE detail_json IS NOT NULL").fetchone()[0]
            print(f"details completed={completed} of={total} failures_this_run={failures}", flush=True)
    if failures:
        raise RuntimeError(f"Failed to fetch {failures} details; rerun to retry missing records")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--per-page", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--details-only", action="store_true")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-details", type=int)
    args = parser.parse_args()
    if args.list_only and args.details_only:
        parser.error("--list-only and --details-only are mutually exclusive")
    if not 1 <= args.per_page <= 1000:
        parser.error("--per-page must be between 1 and 1000")
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    db = connect(args.db)
    last_page = db.execute("SELECT item_count FROM pages ORDER BY page DESC LIMIT 1").fetchone()
    need_list = not args.details_only and not (last_page and last_page[0] < args.per_page)
    need_details = not args.list_only and bool(
        db.execute("SELECT 1 FROM products WHERE detail_json IS NULL LIMIT 1").fetchone()
    )
    if not need_list and not need_details:
        print("Catalog snapshot already complete; nothing to fetch.")
        db.close()
        return
    auth = authorization_header()
    if not args.details_only:
        crawl_listing(db, auth, args.per_page, args.max_pages)
    if not args.list_only:
        crawl_details(db, auth, args.workers, args.max_details)
    db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; completed pages and detail batches are saved.", file=sys.stderr)
        raise SystemExit(130)
