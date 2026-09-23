"""Check hybrid ranking arithmetic and real FTS candidate retrieval offline."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import catalog, config, hybrid_search


def test_fusion_rewards_agreement_and_explains_score() -> None:
    scores = {1: 0.90, 2: 0.80, 3: 0.70}
    fts = [{"id": 2, "rank": -9.0}, {"id": 3, "rank": -2.0}]
    hits = hybrid_search.fuse(scores, [1, 2], fts)
    assert [hit["id"] for hit in hits] == [1, 2, 3]
    shared = hits[1]
    assert shared["features"]["semantic_hit"] == 1.0
    assert shared["features"]["fts_hit"] == 1.0
    assert shared["features"]["bm25"] == 1.0  # lower raw BM25 is better
    assert shared["score"] == pytest.approx(sum(shared["contributions"].values()))
    without_fts = hybrid_search.fuse(scores, [1, 2], [])
    assert shared["score"] > next(hit["score"] for hit in without_fts if hit["id"] == 2)
    assert hits[-1]["features"]["semantic_hit"] == 0.0


def test_local_vm_endpoints_need_no_nitec_key(monkeypatch, tmp_path) -> None:
    collection = tmp_path / "semantic.sqlite"
    collection.touch()
    monkeypatch.setattr(hybrid_search, "collection_path", lambda: collection)
    monkeypatch.delenv("NITEC_API_KEY", raising=False)
    monkeypatch.setenv("EKT_EMBEDDING_BASE_URL", "http://127.0.0.1:8891/v1")
    monkeypatch.setenv("EKT_RERANK_BASE_URL", "http://localhost:8892/v1")
    assert hybrid_search.available()
    assert hybrid_search._service_headers("embedding") == {}
    assert hybrid_search._service_headers("rerank") == {}


@pytest.mark.skipif(not config.DB_PATH.exists(), reason="catalog index missing")
def test_fts_scored_returns_bm25_and_honors_filter() -> None:
    rows = catalog.fts_scored("автомат 16А", limit=20, brand="Legrand")
    assert rows and all(row["brand"] == "Legrand" for row in rows)
    assert all(isinstance(row["rank"], float) for row in rows)
    assert [row["rank"] for row in rows] == sorted(row["rank"] for row in rows)


@pytest.mark.asyncio
@pytest.mark.skipif(not config.DB_PATH.exists(), reason="catalog index missing")
async def test_search_falls_back_to_lexical_without_key(monkeypatch) -> None:
    monkeypatch.setattr(hybrid_search, "available", lambda: False)
    rows = await hybrid_search.search("200300285", limit=3)
    assert rows and rows[0]["id"] == 515291  # exact article keeps first place


@pytest.mark.asyncio
async def test_one_rerank_call_reorders_merged_candidates_and_preserves_tail(monkeypatch) -> None:
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"results": [{"index": 1, "relevance_score": 0.9},
                                          {"index": 0, "relevance_score": 0.2}]},
            )

    monkeypatch.setenv("NITEC_API_KEY", "test-only")
    monkeypatch.setattr(hybrid_search, "_rerank_documents", lambda _ids: {1: "first", 2: "second"})
    monkeypatch.setattr(hybrid_search.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    hits = [{"id": n, "product": {"name": str(n), "article": str(n)}} for n in (1, 2, 3)]
    result = await hybrid_search.rerank_hits("test query", hits, top_k=2)
    assert [hit["id"] for hit in result] == [2, 1, 3]
    assert len(calls) == 1 and calls[0][1]["documents"] == ["first", "second"]
    assert calls[0][1]["model"] == "Qwen/Qwen3-Reranker-8B"


@pytest.mark.asyncio
async def test_reranker_failure_keeps_hybrid_order(monkeypatch) -> None:
    rows = [{"id": 1, "name": "First"}, {"id": 2, "name": "Second"}]
    monkeypatch.setattr(hybrid_search, "catalog", SimpleNamespace(
        get_by_article=lambda _query: [],
        search=lambda *_args, **_kwargs: [],
    ))
    monkeypatch.setattr(hybrid_search, "available", lambda: True)
    monkeypatch.setattr(hybrid_search, "rerank_enabled", lambda: True)

    async def ranked(*_args, **_kwargs):
        return [{"id": row["id"], "product": row} for row in rows]

    async def fails(*_args, **_kwargs):
        raise ValueError("bad reranker response")

    monkeypatch.setattr(hybrid_search, "ranked", ranked)
    monkeypatch.setattr(hybrid_search, "rerank_hits", fails)
    assert [row["id"] for row in await hybrid_search.search("query")] == [1, 2]
