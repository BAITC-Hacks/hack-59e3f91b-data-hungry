"""Build and evaluate a searchable EKT product embedding collection via NITEC.

Examples (from the repository root):
  python backend/scripts/semantic_catalog.py benchmark --sample-size 2000
  python backend/scripts/semantic_catalog.py build --model Qwen/Qwen3-Embedding-8B --strategy compact
  python backend/scripts/semantic_catalog.py search 'светильник для подъезда с датчиком' \
      --db backend/data/semantic_catalog.sqlite

The API key is read from NITEC_API_KEY or a hidden terminal prompt. It is never
written to the collection. Price and stock are deliberately excluded because
they change; the application fetches them live from the EKT detail API.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.catalog import humanize_properties  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DB = ROOT / "data/ekt_catalog.sqlite3"
CATALOG_DB = ROOT / "backend/data/catalog.sqlite"
DEFAULT_DB = ROOT / "backend/data/semantic_catalog.sqlite"
BENCH_DIR = ROOT / "backend/data/semantic_benchmark"
QRELS = ROOT / "backend/tests/fixtures/semantic_queries.json"
API_URL = os.getenv("NITEC_BASE_URL", "https://llm.nitec.kz/v1/").rstrip("/") + "/embeddings"
MODELS = (
    "BAAI/bge-m3",
    "intfloat/multilingual-e5-large-instruct",
    "Qwen/Qwen3-Embedding-8B",
)
STRATEGIES = ("compact", "full", "split")
TASK = "Retrieve relevant electrical products from an EKT catalog for a customer's shopping request"
_WS = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_SKIP_PROPS = {
    "Штрихкод", "Артикул производителя", "Кратность (мин. партия)", "Кратность (макс.)",
    "Новинка", "Спецпредложение", "Страна-производитель", "Гарантия", "Вес",
}


def clean(value: Any) -> str:
    return _WS.sub(" ", html.unescape(str(value or ""))).strip()


def load_products(path: Path = AUDIT_DB) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, list_json, detail_json FROM products ORDER BY id")
        products = []
        for pid, raw_list, raw_detail in rows:
            item = json.loads(raw_list)
            detail = json.loads(raw_detail) if raw_detail else {}
            products.append({
                "id": pid,
                "name": clean(item.get("name") or detail.get("name")),
                "article": clean(item.get("article") or detail.get("article")),
                "url": clean(item.get("url") or detail.get("url")),
                "description": clean(detail.get("description")),
                "properties": detail.get("properties") or {},
            })
        return products
    finally:
        con.close()


def category(url: str) -> str:
    if "/catalog/" not in url:
        return ""
    segments = [s.replace("_", " ") for s in url.split("/catalog/", 1)[1].split("/") if s]
    return " / ".join(segments[:-1][:3])


def property_lines(product: dict[str, Any]) -> list[str]:
    props = humanize_properties(product["properties"])
    lines = []
    for label, value in props.items():
        if label in _SKIP_PROPS or len(value) > 100 or value.lower() in {"нет", "0"}:
            continue
        lines.append(f"{label}: {clean(value)}")
    return lines


def _truncated_lines(lines: list[str], budget: int) -> str:
    kept = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > budget:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


def split_description(text: str, width: int = 620, overlap: int = 100) -> list[str]:
    """Sentence-aware windows; a long sentence falls back to character windows."""
    if not text:
        return []
    sentences = _SENTENCE.split(text)
    windows: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > width:
            if current:
                windows.append(current)
                current = ""
            step = width - overlap
            windows.extend(sentence[i:i + width] for i in range(0, len(sentence), step))
            continue
        if current and len(current) + len(sentence) + 1 > width:
            windows.append(current)
            current = current[-overlap:] + " " + sentence
        else:
            current = (current + " " + sentence).strip()
    if current:
        windows.append(current)
    return [w.strip() for w in windows if w.strip()]


def chunks(product: dict[str, Any], strategy: str) -> list[str]:
    header = f"Товар: {product['name']}\nАртикул: {product['article']}"
    cat = category(product["url"])
    if cat:
        header += f"\nКатегория: {cat}"
    props = property_lines(product)
    if strategy == "compact":
        return [(header + "\n" + _truncated_lines(props, 420)).strip()]
    if strategy == "full":
        # E5 accepts at most 512 tokens. Keep the enriched single-vector card
        # short enough for every model, so the benchmark compares the same text.
        description = product["description"][:480]
        body = "\nОписание: " + description if description else ""
        specs = _truncated_lines(props, 320)
        return [(header + body + ("\n" + specs if specs else "")).strip()[:900]]
    if strategy == "split":
        identity = (header + "\n" + _truncated_lines(props, 420)).strip()
        pieces = [identity]
        for part in split_description(product["description"][:2000]):
            pieces.append(f"Товар: {product['name']}\nОписание: {part}")
        return pieces
    raise ValueError(strategy)


def query_text(model: str, query: str) -> str:
    if model in ("Qwen/Qwen3-Embedding-8B", "intfloat/multilingual-e5-large-instruct"):
        return f"Instruct: {TASK}\nQuery: {query}"
    return query


class EmbeddingClient:
    def __init__(self, key: str):
        self.client = httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0),
                                   headers={"Authorization": "Bearer " + key})

    def embed(self, model: str, texts: list[str]) -> np.ndarray:
        for attempt in range(6):
            try:
                response = self.client.post(API_URL, json={"model": model, "input": texts})
                response.raise_for_status()
                data = sorted(response.json()["data"], key=lambda row: row["index"])
                vectors = np.asarray([row["embedding"] for row in data], dtype=np.float32)
                if len(vectors) != len(texts) or vectors.ndim != 2 or vectors.shape[1] < 1000:
                    raise ValueError(f"Unexpected embedding shape: {vectors.shape}")
                norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                if np.any(norms == 0):
                    raise ValueError("Zero embedding")
                return vectors / norms
            except (httpx.HTTPError, KeyError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
                    if len(texts) > 1:
                        mid = len(texts) // 2
                        return np.concatenate((self.embed(model, texts[:mid]),
                                               self.embed(model, texts[mid:])), axis=0)
                    raise ValueError(
                        f"Embedding rejected for a single {len(texts[0])}-character text: "
                        f"{exc.response.text[:300]}"
                    ) from exc
                if attempt == 5 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500
                                    and exc.response.status_code != 429):
                    raise
                wait = min(2 ** attempt, 20)
                print(f"API retry {attempt + 1}/6 after {type(exc).__name__}; wait {wait}s", flush=True)
                time.sleep(wait)
        raise RuntimeError("unreachable")


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    product_id INTEGER NOT NULL,
    chunk_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY(product_id, chunk_no)
);
CREATE INDEX IF NOT EXISTS chunks_product ON chunks(product_id);
"""


def meta_get(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def build(path: Path, products: list[dict[str, Any]], model: str, strategy: str,
          client: EmbeddingClient, batch_size: int = 24) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for key, value in (("model", model), ("strategy", strategy)):
        old = meta_get(con, key)
        if old is not None and old != value:
            raise ValueError(f"{path}: {key} is {old!r}, expected {value!r}")
        con.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", (key, value))
    selected_ids = [p["id"] for p in products]
    fingerprint = hashlib.sha256(json.dumps(selected_ids).encode()).hexdigest()
    old = meta_get(con, "product_ids_sha256")
    if old is not None and old != fingerprint:
        raise ValueError(f"{path}: selected product set changed; use another collection path")
    con.execute("INSERT OR IGNORE INTO meta VALUES (?,?)", ("product_ids_sha256", fingerprint))
    con.commit()
    existing = {(pid, idx): digest for pid, idx, digest in
                con.execute("SELECT product_id, chunk_no, text_hash FROM chunks")}
    pending: list[tuple[int, int, str, str]] = []
    current_keys: set[tuple[int, int]] = set()
    for product in products:
        for idx, value in enumerate(chunks(product, strategy)):
            current_keys.add((product["id"], idx))
            digest = hashlib.sha256(value.encode()).hexdigest()
            if existing.get((product["id"], idx)) != digest:
                pending.append((product["id"], idx, value, digest))
    obsolete = set(existing) - current_keys
    if obsolete:
        con.executemany("DELETE FROM chunks WHERE product_id=? AND chunk_no=?", obsolete)
        con.commit()
    print(f"{model} / {strategy}: {len(products)} products, {len(pending)} chunks to embed", flush=True)
    started = time.monotonic()
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        vectors = client.embed(model, [item[2] for item in batch])
        dim = int(vectors.shape[1])
        old_dim = meta_get(con, "dimensions")
        if old_dim is not None and int(old_dim) != dim:
            raise ValueError(f"Embedding dimension changed: {old_dim} -> {dim}")
        con.execute("INSERT OR IGNORE INTO meta VALUES ('dimensions',?)", (str(dim),))
        con.executemany(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?)",
            ((pid, idx, value, digest, vec.tobytes())
             for (pid, idx, value, digest), vec in zip(batch, vectors)),
        )
        con.commit()
        if offset == 0 or (offset // batch_size) % 25 == 0 or offset + len(batch) == len(pending):
            print(f"  {offset + len(batch)}/{len(pending)}; {time.monotonic()-started:.1f}s", flush=True)
    count = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    print(f"collection {path}: {count} chunks, {meta_get(con, 'dimensions')} dimensions", flush=True)
    con.close()


def load_vectors(path: Path) -> tuple[str, np.ndarray, np.ndarray, list[str]]:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    model = meta_get(con, "model")
    dim = int(meta_get(con, "dimensions") or 0)
    if not model or dim < 1000:
        raise ValueError(f"Invalid collection {path}")
    rows = con.execute("SELECT product_id, text, vector FROM chunks ORDER BY product_id, chunk_no").fetchall()
    con.close()
    ids = np.asarray([r[0] for r in rows], dtype=np.int64)
    matrix = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32).reshape(-1, dim).copy()
    return model, ids, matrix, [r[1] for r in rows]


def ranked_ids(ids: np.ndarray, matrix: np.ndarray, query_vec: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
    scores = matrix @ query_vec
    unique, inverse = np.unique(ids, return_inverse=True)
    best = np.full(len(unique), -np.inf, dtype=np.float32)
    np.maximum.at(best, inverse, scores)
    top = np.argsort(-best)[:k]
    return [(int(unique[i]), float(best[i])) for i in top]


def load_qrels(path: Path = QRELS) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate(path: Path, qrels: list[dict[str, Any]], client: EmbeddingClient) -> dict[str, Any]:
    model, ids, matrix, _ = load_vectors(path)
    query_vectors = client.embed(model, [query_text(model, q["query"]) for q in qrels])
    details = []
    for q, vec in zip(qrels, query_vectors):
        expected = set(q["ids"])
        results = ranked_ids(ids, matrix, vec, 10)
        rank = next((i for i, (pid, _) in enumerate(results, 1) if pid in expected), None)
        details.append({"query": q["query"], "expected": sorted(expected), "rank": rank,
                        "top3": [pid for pid, _ in results[:3]]})
    metrics = {
        "hit@1": sum(d["rank"] == 1 for d in details) / len(details),
        "hit@5": sum(d["rank"] is not None and d["rank"] <= 5 for d in details) / len(details),
        "mrr@10": sum(1 / d["rank"] for d in details if d["rank"] is not None) / len(details),
    }
    con = sqlite3.connect(path)
    try:
        strategy = meta_get(con, "strategy")
    finally:
        con.close()
    return {"model": model, "strategy": strategy,
            "dimensions": matrix.shape[1], "products": len(np.unique(ids)),
            "chunks": len(ids), "metrics": metrics, "queries": details}


def api_key() -> str:
    key = os.getenv("NITEC_API_KEY")
    if not key:
        key = getpass.getpass("NITEC API key: ")
    if not key:
        raise ValueError("NITEC_API_KEY is required")
    return key


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    build_ap = sub.add_parser("build")
    build_ap.add_argument("--model", choices=MODELS, required=True)
    build_ap.add_argument("--strategy", choices=STRATEGIES, required=True)
    build_ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    build_ap.add_argument("--batch-size", type=int, default=24)
    build_ap.add_argument("--evaluate", action="store_true", help="score the built collection on the known-item queries")
    bench_ap = sub.add_parser("benchmark")
    bench_ap.add_argument("--sample-size", type=int, default=2000)
    bench_ap.add_argument("--batch-size", type=int, default=24)
    bench_ap.add_argument("--out-dir", type=Path, default=BENCH_DIR)
    bench_ap.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    bench_ap.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    search_ap = sub.add_parser("search")
    search_ap.add_argument("query")
    search_ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    search_ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    key = api_key()
    client = EmbeddingClient(key)
    if args.command == "build":
        build(args.db, load_products(), args.model, args.strategy, client, args.batch_size)
        if args.evaluate:
            result = evaluate(args.db, load_qrels(), client)
            args.db.with_suffix(".evaluation.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({k: v for k, v in result.items() if k != "queries"}, ensure_ascii=False), flush=True)
    elif args.command == "benchmark":
        products = load_products()
        qrels = load_qrels()
        targets = {pid for q in qrels for pid in q["ids"]}
        selected = [p for p in products if p["id"] in targets]
        remaining = [p for p in products if p["id"] not in targets]
        selected += random.Random(42).sample(remaining, max(0, args.sample_size - len(selected)))
        selected.sort(key=lambda p: p["id"])
        args.out_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for model in args.models:
            for strategy in args.strategies:
                name = model.rsplit("/", 1)[-1].lower() + "_" + strategy + ".sqlite"
                path = args.out_dir / name
                build(path, selected, model, strategy, client, args.batch_size)
                result = evaluate(path, qrels, client)
                print(json.dumps({k: v for k, v in result.items() if k != "queries"}, ensure_ascii=False), flush=True)
                results.append(result)
                (args.out_dir / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    elif args.command == "search":
        model, ids, matrix, _ = load_vectors(args.db)
        vector = client.embed(model, [query_text(model, args.query)])[0]
        hits = ranked_ids(ids, matrix, vector, args.limit)
        con = sqlite3.connect(f"file:{CATALOG_DB.resolve()}?mode=ro", uri=True)
        for pid, score in hits:
            row = con.execute("SELECT name, article FROM products WHERE id=?", (pid,)).fetchone()
            print(f"{score:.4f}\t{pid}\t{row[0] if row else '?'}\t{row[1] if row else ''}")
        con.close()


if __name__ == "__main__":
    main()
