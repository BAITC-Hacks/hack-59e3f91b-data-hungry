"""The full audit's detail payloads can seed a dated, non-live fallback cache."""
from __future__ import annotations

import sqlite3
import sys

from scripts import import_details


def test_import_from_audit_db(tmp_path, monkeypatch):
    source = tmp_path / "audit.sqlite3"
    target = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(source) as con:
        con.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, detail_json TEXT)")
        con.executemany("INSERT INTO products VALUES (?, ?)", [
            (7, '{"id":7,"quantity":3,"name":"Кабель"}'),
            (8, '{"id":8,"quantity":0,"name":"Автомат"}'),
            (9, '{"id":10,"quantity":1}'),
        ])
    monkeypatch.setattr(sys, "argv", ["import_details.py", "--audit-db", str(source), "--db", str(target)])
    import_details.main()
    with sqlite3.connect(target) as con:
        rows = con.execute("SELECT id, quantity FROM product_details ORDER BY id").fetchall()
    assert rows == [(7, 3), (8, 0)]
