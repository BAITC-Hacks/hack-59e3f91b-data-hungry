"""Load dumped product details (JSONL from /api/products/detail) into catalog.sqlite as a persistent detail cache.

Usage:  uv run python scripts/import_details.py --file data/dump/details.jsonl
The table `product_details` is also written by the running backend (every live fetch is persisted), so the
assistant keeps answering from the last known stock/properties when ekt.kz is slow or unreachable.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS product_details (
  id INTEGER PRIMARY KEY,
  fetched_at REAL NOT NULL,
  quantity INTEGER,
  data TEXT NOT NULL
);
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(config.DATA_DIR / "dump" / "details.jsonl"))
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"no such file: {path}")
    mtime = path.stat().st_mtime
    con = sqlite3.connect(args.db)
    con.executescript(DDL)
    rows, skipped = [], 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(d, dict) or "id" not in d or d.get("error"):
                skipped += 1
                continue
            rows.append((int(d["id"]), mtime, d.get("quantity"), json.dumps(d, ensure_ascii=False)))
    con.executemany(
        "INSERT INTO product_details(id, fetched_at, quantity, data) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at, quantity=excluded.quantity, data=excluded.data "
        "WHERE excluded.fetched_at >= product_details.fetched_at",
        rows,
    )
    con.commit()
    n = con.execute("SELECT count(*) FROM product_details").fetchone()[0]
    print(f"imported {len(rows)} details (skipped {skipped}); table now has {n} rows; snapshot time {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}")


if __name__ == "__main__":
    main()
