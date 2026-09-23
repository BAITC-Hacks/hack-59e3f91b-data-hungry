"""Inspect and evaluate FTS5 + embedding ranking without saving the NITEC key.

  uv run --project backend python backend/scripts/hybrid_search.py search 'автомат 16А'
  uv run --project backend python backend/scripts/hybrid_search.py evaluate
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import catalog, hybrid_search  # noqa: E402

QRELS = Path(__file__).resolve().parent.parent / "tests/fixtures/semantic_queries.json"
PROFILES = {
    "semantic-heavy": {"cosine": 0.60, "semantic_hit": 0.15, "fts_hit": 0.10, "bm25": 0.15},
    "balanced": {"cosine": 0.40, "semantic_hit": 0.10, "fts_hit": 0.20, "bm25": 0.30},
    "lexical-heavy": {"cosine": 0.30, "semantic_hit": 0.05, "fts_hit": 0.25, "bm25": 0.40},
}


def ensure_key() -> None:
    if hybrid_search.available():
        return
    if not os.getenv("NITEC_API_KEY"):
        os.environ["NITEC_API_KEY"] = getpass.getpass("NITEC API key: ")
    if not os.environ["NITEC_API_KEY"]:
        raise ValueError("NITEC_API_KEY is required")


async def inspect(query: str, limit: int, rerank: bool) -> None:
    hits = await hybrid_search.ranked(query, max(limit, hybrid_search.RERANK_CANDIDATES) if rerank else limit)
    if rerank:
        hits = await hybrid_search.rerank_hits(query, hits)
    for hit in hits[:limit]:
        row = hit["product"]
        print(json.dumps({"id": hit["id"], "name": row["name"], "article": row["article"],
                          "score": round(hit["score"], 4), "cosine": round(hit["cosine_raw"], 4),
                          "fts_bm25": hit["bm25_raw"], "features": hit["features"],
                          "contributions": hit["contributions"], "rerank_score": hit.get("rerank_score")}, ensure_ascii=False))


async def evaluate(with_rerank: bool, models: list[str]) -> None:
    qrels = json.loads(QRELS.read_text(encoding="utf-8"))
    model, ids, matrix = await asyncio.to_thread(hybrid_search._collection)
    ranks: dict[str, list[int | None]] = {name: [] for name in (*PROFILES, "semantic", "fts")}
    rerank_models = tuple(models) if with_rerank else ()
    latency_ms: dict[str, list[float]] = {model: [] for model in rerank_models}
    ranks.update({model: [] for model in rerank_models})
    for index, item in enumerate(qrels, 1):
        query = item["query"]
        expected = set(item["ids"])
        vector = await hybrid_search._embed_query(model, query, matrix.shape[1])
        scores, semantic_top = await asyncio.to_thread(hybrid_search._semantic_scores, ids, matrix, vector, None)
        fts_rows = await asyncio.to_thread(catalog.fts_scored, query, hybrid_search.CANDIDATES)
        sequences = {
            "semantic": semantic_top,
            "fts": [row["id"] for row in fts_rows],
            **{name: [hit["id"] for hit in hybrid_search.fuse(scores, semantic_top, fts_rows, weights=weights)]
               for name, weights in PROFILES.items()},
        }
        if with_rerank:
            fused = hybrid_search.fuse(scores, semantic_top, fts_rows)
            leading = fused[:hybrid_search.RERANK_CANDIDATES]
            products = await asyncio.to_thread(catalog.products_by_ids, [hit["id"] for hit in leading])
            leading = [{**hit, "product": products[hit["id"]]} for hit in leading if hit["id"] in products]
            for rerank_model in rerank_models:
                started = time.perf_counter()
                reranked = await hybrid_search.rerank_hits(query, leading, model=rerank_model)
                latency_ms[rerank_model].append((time.perf_counter() - started) * 1000)
                sequences[rerank_model] = [hit["id"] for hit in reranked]
        for name, sequence in sequences.items():
            ranks[name].append(next((i for i, pid in enumerate(sequence[:10], 1) if pid in expected), None))
        print(f"{index}/{len(qrels)}", file=sys.stderr, flush=True)
    result = {}
    for name, values in ranks.items():
        n = len(values)
        result[name] = {"hit@1": sum(rank == 1 for rank in values) / n,
                        "hit@5": sum(rank is not None and rank <= 5 for rank in values) / n,
                        "mrr@10": sum(1 / rank for rank in values if rank is not None) / n,
                        "ranks": values}
    print(json.dumps({"model": model, "products": len(set(ids)), "queries": len(qrels),
                      "candidates_per_index": hybrid_search.CANDIDATES,
                      "rerank_candidates": hybrid_search.RERANK_CANDIDATES if with_rerank else None,
                      "rerank_mean_latency_ms": {name: sum(times) / len(times) for name, times in latency_ms.items()},
                      "profiles": PROFILES, "results": result}, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    search_ap = sub.add_parser("search", help="print score components for ranked results")
    search_ap.add_argument("query")
    search_ap.add_argument("--limit", type=int, default=10)
    search_ap.add_argument("--rerank", action="store_true", help="apply one rerank call after hybrid retrieval")
    eval_ap = sub.add_parser("evaluate", help="compare fixed weights on known-item queries")
    eval_ap.add_argument("--rerank", action="store_true", help="also compare NITEC's BGE and Qwen rerankers")
    eval_ap.add_argument("--rerank-models", nargs="+", default=["BAAI/bge-reranker-v2-m3", "Qwen/Qwen3-Reranker-8B"],
                         help="rerank model IDs to test (use the locally served model on the VM)")
    args = ap.parse_args()
    ensure_key()
    if args.command == "search":
        asyncio.run(inspect(args.query, max(1, min(args.limit, 100)), args.rerank))
    else:
        asyncio.run(evaluate(args.rerank, args.rerank_models))


if __name__ == "__main__":
    main()
