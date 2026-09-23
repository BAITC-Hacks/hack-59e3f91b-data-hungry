"""Persistent content-addressed cache for parsed customer attachments."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from . import config


def _path() -> Path:
    return config.ATTACHMENT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS parsed_files (
            sha256 TEXT NOT NULL,
            kind TEXT NOT NULL,
            raw BLOB NOT NULL,
            stored_bytes BLOB NOT NULL,
            stored_suffix TEXT NOT NULL,
            text TEXT NOT NULL,
            lines_json TEXT NOT NULL,
            boxes_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            mime TEXT NOT NULL,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (sha256, kind)
        )
    """)
    return db


def get(sha256: str, kind: str) -> dict[str, Any] | None:
    with closing(_connect()) as db:
        row = db.execute("SELECT * FROM parsed_files WHERE sha256=? AND kind=?", (sha256, kind)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["lines"] = json.loads(result.pop("lines_json"))
        result["boxes"] = json.loads(result.pop("boxes_json"))
        return result


def put(sha256: str, kind: str, raw: bytes, stored_bytes: bytes, stored_suffix: str,
        text: str, lines: list[dict], boxes: list[dict], summary: str,
        mime: str, width: int, height: int) -> None:
    with closing(_connect()) as db, db:
        db.execute("""
            INSERT OR IGNORE INTO parsed_files
                (sha256,kind,raw,stored_bytes,stored_suffix,text,lines_json,boxes_json,summary,mime,width,height)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (sha256, kind, raw, stored_bytes, stored_suffix, text,
              json.dumps(lines, ensure_ascii=False), json.dumps(boxes, ensure_ascii=False),
              summary, mime, width, height))
