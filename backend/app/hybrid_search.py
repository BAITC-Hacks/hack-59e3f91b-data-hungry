"""Hybrid EKT product retrieval: local FTS5 BM25 + NITEC query embeddings.

The vector collection is built offline by scripts/semantic_catalog.py. Query
vectors are requested only when both the collection and NITEC_API_KEY exist;
otherwise the existing lexical search stays available. No API key is stored in
the collection or in search results.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import numpy as np

from . import catalog, config

log = logging.getLogger("ekt.hybrid_search")
DEFAULT_DB = config.DATA_DIR / "semantic_catalog.sqlite"
CANDIDATES = 100
RERANK_CANDIDATES = 30
DEFAULT_RERANK_MODEL = "Qwen/Qwen3-Reranker-8B"
TASK = "Retrieve relevant electrical products from an EKT catalog for a customer's shopping request"
# Weights are explicit and sum to 1. Both continuous features are normalized
# per query; the hit features are binary membership in the top-CANDIDATES lists.
WEIGHTS = {"cosine": 0.60, "semantic_hit": 0.15, "fts_hit": 0.10, "bm25": 0.15}


def collection_path() -> Path:
    return Path(os.getenv("EKT_SEMANTIC_DB_PATH", str(DEFAULT_DB)))


def _service_base(kind: str) -> str:
    override = os.getenv("EKT_EMBEDDING_BASE_URL" if kind == "embedding" else "EKT_RERANK_BASE_URL")
    return (override or os.getenv("NITEC_API_BASE_URL") or os.getenv("NITEC_BASE_URL")
            or "https://llm.nitec.kz/v1").rstrip("/")


def _service_headers(kind: str) -> dict[str, str]:
    key = os.getenv("EKT_EMBEDDING_API_KEY" if kind == "embedding" else "EKT_RERANK_API_KEY") or os.getenv("NITEC_API_KEY")
    if key:
        return {"Authorization": "Bearer " + key}
    if urlparse(_service_base(kind)).hostname in {"127.0.0.1", "localhost", "::1"}:
        return {}
    raise RuntimeError(f"API key required for non-local {kind} endpoint")


def available() -> bool:
    if not collection_path().is_file():
        return False
    try:
        _service_headers("embedding")
        return True
    except RuntimeError:
        return False


def rerank_enabled() -> bool:
    return os.getenv("EKT_RERANK_ENABLED", "1").lower() not in ("0", "false", "no")


def rerank_model() -> str:
    return os.getenv("NITEC_RERANK_MODEL") or DEFAULT_RERANK_MODEL


@lru_cache(maxsize=2)
def _load_collection(path: str, mtime_ns: int, size: int) -> tuple[str, np.ndarray, np.ndarray]:
    """Read the 80 MiB collection once per process and validate its metadata."""
    del mtime_ns, size  # cache invalidation is encoded in the arguments
    con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        model = meta["model"]
        dim = int(meta["dimensions"])
        if dim < 1000:
            raise ValueError(f"Embedding dimensions below 1000: {dim}")
        rows = con.execute("SELECT product_id, vector FROM chunks ORDER BY product_id, chunk_no").fetchall()
        if not rows or any(len(blob) != dim * 4 for _, blob in rows):
            raise ValueError("Invalid embedding collection vectors")
        ids = np.fromiter((row[0] for row in rows), dtype=np.int64, count=len(rows))
        matrix = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(-1, dim).copy()
        return model, ids, matrix
    finally:
        con.close()


def _collection() -> tuple[str, np.ndarray, np.ndarray]:
    path = collection_path()
    stat = path.stat()
    return _load_collection(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _query_text(model: str, query: str) -> str:
    if model in ("Qwen/Qwen3-Embedding-8B", "intfloat/multilingual-e5-large-instruct"):
        return f"Instruct: {TASK}\nQuery: {query}"
    return query


async def _embed_query(model: str, query: str, dim: int) -> np.ndarray:
    url = _service_base("embedding") + "/embeddings"
    async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0)) as client:
        for attempt in range(2):
            response = await client.post(url, headers=_service_headers("embedding"),
                                         json={"model": model, "input": [_query_text(model, query)]})
            if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            response.raise_for_status()
            value = np.asarray(response.json()["data"][0]["embedding"], dtype=np.float32)
            if value.ndim != 1 or len(value) != dim or not np.isfinite(value).all():
                raise ValueError("Query embedding has invalid dimension or values")
            norm = np.linalg.norm(value)
            if not np.isfinite(norm) or norm == 0:
                raise ValueError("Query embedding has zero/invalid norm")
            return value / norm
    raise RuntimeError("Embedding request did not complete")


def _semantic_scores(ids: np.ndarray, matrix: np.ndarray, vector: np.ndarray,
                     eligible: set[int] | None, limit: int = CANDIDATES) -> tuple[dict[int, float], list[int]]:
    """Return best cosine per product and top eligible product IDs."""
    scores = matrix @ vector
    unique, inverse = np.unique(ids, return_inverse=True)
    best = np.full(len(unique), -np.inf, dtype=np.float32)
    np.maximum.at(best, inverse, scores)
    all_scores = {int(pid): float(score) for pid, score in zip(unique, best) if np.isfinite(score)}
    ordered = np.argsort(-best, kind="stable")
    top = [int(unique[i]) for i in ordered if eligible is None or int(unique[i]) in eligible][:limit]
    return all_scores, top


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 1.0 if value >= high else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def fuse(semantic_scores: dict[int, float], semantic_top: list[int],
         fts_rows: list[dict[str, Any]], *, weights: dict[str, float] = WEIGHTS) -> list[dict[str, Any]]:
    """Rank the union and expose each feature and weighted contribution.

    Cosine is min-max scaled over semantic top-K. BM25 is inverted and min-max
    scaled over FTS top-K because SQLite FTS5 returns better hits as lower
    (usually negative) numbers. Missing FTS gets zero FTS features.
    """
    if abs(sum(weights.values()) - 1.0) > 1e-6 or any(v < 0 for v in weights.values()):
        raise ValueError("Hybrid search weights must be nonnegative and sum to 1")
    sem_hit = set(semantic_top)
    fts_rank = {int(row["id"]): float(row["rank"]) for row in fts_rows}
    candidates = sem_hit | set(fts_rank)
    if not candidates:
        return []
    sem_values = [semantic_scores[pid] for pid in semantic_top if pid in semantic_scores]
    sem_low, sem_high = (min(sem_values), max(sem_values)) if sem_values else (0.0, 0.0)
    bm_values = [-rank for rank in fts_rank.values()]
    bm_low, bm_high = (min(bm_values), max(bm_values)) if bm_values else (0.0, 0.0)
    out = []
    for pid in candidates:
        cosine = semantic_scores.get(pid, 0.0)
        features = {
            "cosine": _scale(cosine, sem_low, sem_high) if sem_values else 0.0,
            "semantic_hit": float(pid in sem_hit),
            "fts_hit": float(pid in fts_rank),
            "bm25": _scale(-fts_rank[pid], bm_low, bm_high) if pid in fts_rank else 0.0,
        }
        contributions = {name: weights[name] * value for name, value in features.items()}
        out.append({"id": pid, "score": sum(contributions.values()), "cosine_raw": cosine,
                    "bm25_raw": fts_rank.get(pid), "features": features, "contributions": contributions})
    out.sort(key=lambda hit: (-hit["score"], -hit["cosine_raw"], hit["id"]))
    return out


async def ranked(query: str, limit: int = 10, *, category: str | None = None,
                 brand: str | None = None, exclude_id: int | None = None,
                 weights: dict[str, float] = WEIGHTS) -> list[dict[str, Any]]:
    """Search with both indices; each hit includes a product and a score breakdown."""
    if not available():
        raise RuntimeError("Embedding endpoint and semantic collection are required for hybrid ranking")
    if not brand:
        detected, rest = catalog._query_brand(catalog.query_tokens(query)[0])
        if detected and rest:
            brand = detected
    model, ids, matrix = await asyncio.to_thread(_collection)
    vector = await _embed_query(model, query, matrix.shape[1])
    eligible, fts_rows = await asyncio.gather(
        asyncio.to_thread(catalog.filtered_product_ids, category=category, brand=brand, exclude_id=exclude_id),
        asyncio.to_thread(catalog.fts_scored, query, CANDIDATES, category=category, brand=brand, exclude_id=exclude_id),
    )
    scores, top = await asyncio.to_thread(_semantic_scores, ids, matrix, vector, eligible)
    fused = fuse(scores, top, fts_rows, weights=weights)
    products = await asyncio.to_thread(catalog.products_by_ids, [hit["id"] for hit in fused[:limit]])
    return [{**hit, "product": products[hit["id"]]} for hit in fused[:limit] if hit["id"] in products]


def _rerank_documents(product_ids: list[int]) -> dict[int, str]:
    """Use the same stable, enriched catalog text that was embedded offline."""
    if not product_ids:
        return {}
    marks = ",".join("?" for _ in product_ids)
    con = sqlite3.connect(f"file:{collection_path().resolve()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT product_id, text FROM chunks WHERE product_id IN ({marks}) ORDER BY product_id, chunk_no",
            product_ids,
        ).fetchall()
    finally:
        con.close()
    documents: dict[int, str] = {}
    for pid, text in rows:
        if pid not in documents:
            documents[pid] = text[:1200]
    return documents


async def rerank_hits(query: str, hits: list[dict[str, Any]], *, model: str | None = None,
                      top_k: int = RERANK_CANDIDATES) -> list[dict[str, Any]]:
    """Call NITEC once to rerank the merged top-K; retain the hybrid tail."""
    count = min(len(hits), max(1, top_k))
    if count < 2:
        return hits
    leading = hits[:count]
    texts = await asyncio.to_thread(_rerank_documents, [hit["id"] for hit in leading])
    documents = [texts.get(hit["id"]) or f"Товар: {hit['product']['name']}\nАртикул: {hit['product'].get('article') or ''}"
                 for hit in leading]
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        response = await client.post(
            _service_base("rerank") + "/rerank",
            headers=_service_headers("rerank"),
            json={"model": model or rerank_model(), "query": query, "documents": documents, "top_n": count},
        )
        response.raise_for_status()
        results = response.json()["results"]
    scores: dict[int, float] = {}
    for result in results:
        index = int(result["index"])
        score = float(result["relevance_score"])
        if index < 0 or index >= count or index in scores or not math.isfinite(score):
            raise ValueError("Reranker returned invalid scores or indices")
        scores[index] = score
    if len(scores) != count:
        raise ValueError("Reranker returned an incomplete candidate list")
    rescored = [{**hit, "rerank_score": scores[i]} for i, hit in enumerate(leading)]
    rescored.sort(key=lambda hit: -hit["rerank_score"])  # stable hybrid order for score ties
    return [*rescored, *hits[count:]]


async def search(query: str, limit: int = 10, *, category: str | None = None,
                 brand: str | None = None, exclude_id: int | None = None) -> list[dict[str, Any]]:
    """Products in hybrid order, with exact article matches first and lexical fallback."""
    exact = await asyncio.to_thread(catalog.get_by_article, query)
    if category or brand or exclude_id is not None:
        eligible = await asyncio.to_thread(catalog.filtered_product_ids, category=category, brand=brand,
                                           exclude_id=exclude_id)
        exact = [row for row in exact if row["id"] in eligible]
    if not available():
        rest = await asyncio.to_thread(catalog.search, query, limit=limit, category=category,
                                       brand=brand, exclude_id=exclude_id)
    else:
        try:
            hits = await ranked(query, max(limit, CANDIDATES), category=category, brand=brand, exclude_id=exclude_id)
            if rerank_enabled():
                try:
                    hits = await rerank_hits(query, hits)
                except (httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
                    log.warning("reranker unavailable (%s); keeping hybrid order", type(exc).__name__)
            rest = [hit["product"] for hit in hits]
        except (httpx.HTTPError, OSError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
            log.warning("hybrid retrieval unavailable (%s); falling back to FTS", type(exc).__name__)
            rest = await asyncio.to_thread(catalog.search, query, limit=limit, category=category,
                                           brand=brand, exclude_id=exclude_id)
    seen: set[int] = set()
    result = []
    for product in [*exact, *rest]:
        if product["id"] not in seen:
            seen.add(product["id"])
            result.append(product)
            if len(result) >= limit:
                break
    return result
