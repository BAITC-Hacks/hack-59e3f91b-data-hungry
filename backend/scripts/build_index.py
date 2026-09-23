"""Build data/catalog.sqlite from raw list dumps (JSON pages from GET /api/products).

Usage:  uv run python scripts/build_index.py --dump ../../probe/dump [--dump other/dir ...] [--limit N]
        uv run python scripts/build_index.py --audit-db ../data/ekt_catalog.sqlite3
Re-runnable: rebuilds the DB from scratch each time (~15k rows in a couple of seconds, 200k in well under a minute).
Several --dump directories are merged; duplicates (same id) keep the first occurrence.

Note (verified 2026-09-23): the list API exposes 15 035 items and wraps around to page 1 past the end, so a dump
with more pages than needed contains only duplicates - harmless here.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402
from app.brands import detect_brand  # noqa: E402
from app.catalog import normalize_text  # noqa: E402

SCHEMA = """
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS products_fts;
DROP TABLE IF EXISTS products_tri;
CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  article TEXT,
  price REAL,
  image TEXT,
  url TEXT,
  cat1 TEXT, cat2 TEXT, cat3 TEXT,
  cat TEXT,            -- 'cat1 cat2 cat3' path, used by the FTS index and category filters
  brand TEXT,
  name_norm TEXT,      -- normalize_text(name): what FTS indexes and what queries are normalized to
  article_norm TEXT,   -- lower-cased article for exact/prefix lookups
  search_text TEXT     -- name_norm + article + brand for the trigram (substring) index
);
CREATE INDEX idx_products_article ON products(article_norm);
CREATE INDEX idx_products_cat ON products(cat1, cat2, cat3);
CREATE INDEX idx_products_brand ON products(brand);
-- word-level index (ranked search); external content = products, so column names must exist there
CREATE VIRTUAL TABLE products_fts USING fts5(name_norm, article, brand, cat, content='products', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
-- trigram index (substring / partial article / '2.5' style fragments)
CREATE VIRTUAL TABLE products_tri USING fts5(search_text, content='products', content_rowid='id', tokenize='trigram');
"""


def categories_from_url(url: str | None) -> tuple[str | None, str | None, str | None]:
    """/catalog/<cat1>/<cat2>/<cat3>/<product-slug>/ -> (cat1, cat2, cat3).

    The last segment is the product slug; promo URLs like /catalog/spets_predlozhenie/<promo>/<slug>/ yield
    cat1='spets_predlozhenie', cat2=<promo>, cat3=None. Deeper trees keep only the first three levels.
    """
    if not url or "/catalog/" not in url:
        return (None, None, None)
    parts = [p for p in url.split("/catalog/", 1)[1].split("/") if p]
    cats = parts[:-1] if len(parts) > 1 else []
    return tuple((cats + [None, None, None])[:3])  # type: ignore[return-value]


def norm(s: str | None) -> str:
    return " ".join((s or "").split())


def load_items(dump_dirs: list[str], limit: int = 0) -> list[dict]:
    """Merge list_*.json pages from all dump dirs; first occurrence of an id wins."""
    files: list[str] = []
    for d in dump_dirs:
        files += sorted(glob.glob(os.path.join(d, "list_*.json")))
    if not files:
        sys.exit(f"no list_*.json files in {dump_dirs}")
    seen: set[int] = set()
    items: list[dict] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            page = json.load(fh)
        for it in page.get("items", []):
            pid = int(it["id"])
            if pid in seen:
                continue
            seen.add(pid)
            items.append(it)
            if limit and len(items) >= limit:
                return items
    print(f"read {len(files)} pages -> {len(items)} unique products")
    return items


def load_audit_db(path: str, limit: int = 0) -> list[dict]:
    """Read the local EKT ingestion audit DB without exporting raw pages to JSON."""
    source = Path(path)
    if not source.is_file():
        sys.exit(f"audit DB not found: {source}")
    con = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        query = "SELECT list_json FROM products ORDER BY id" + (" LIMIT ?" if limit else "")
        rows = con.execute(query, (limit,) if limit else ())
        items = [json.loads(row[0]) for row in rows]
    finally:
        con.close()
    print(f"read {len(items)} products from {source}")
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="append", default=None, help="directory with list_*.json (repeatable)")
    ap.add_argument("--audit-db", help="SQLite ingestion DB containing products.list_json")
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    dump_dirs = args.dump or [os.getenv("EKT_DUMP_DIR", str(config.DATA_DIR / "dump"))]

    items = load_audit_db(args.audit_db, args.limit) if args.audit_db else load_items(dump_dirs, args.limit)
    rows = []
    for it in items:
        name = norm(it.get("name"))
        article = norm(it.get("article"))
        c1, c2, c3 = categories_from_url(it.get("url"))
        brand = detect_brand(name)
        name_norm = normalize_text(name)
        search_text = " ".join(x for x in (name_norm, article.lower(), (brand or "").lower()) if x)
        rows.append((int(it["id"]), name, article, it.get("price"), it.get("image"), it.get("url"), c1, c2, c3,
                     " ".join(c for c in (c1, c2, c3) if c), brand, name_norm, article.lower(), search_text))

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO products_fts(rowid, name_norm, article, brand, cat) SELECT id, name_norm, article, brand, cat FROM products")
    con.execute("INSERT INTO products_tri(rowid, search_text) SELECT id, search_text FROM products")
    con.commit()
    n = con.execute("SELECT count(*) FROM products").fetchone()[0]
    b = con.execute("SELECT count(*) FROM products WHERE brand IS NOT NULL").fetchone()[0]
    con.close()
    os.replace(tmp, db)  # atomic swap: readers never see a half-built DB
    print(f"indexed {n} products ({b} with detected brand) -> {db}")


if __name__ == "__main__":
    main()
