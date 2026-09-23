"""Catalog search / card / analog tests against the real index (skipped when data/catalog.sqlite is missing).

Run:  uv run pytest tests/test_catalog.py -q            (offline tests)
      uv run pytest tests/test_catalog.py -q -m network  (live ekt.kz API: analogs, cards)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on the path without a conftest
from app import analogs, catalog, config  # noqa: E402

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="catalog.sqlite not built (scripts/build_index.py)")
network = pytest.mark.network


def ids(rows: list[dict]) -> list[int]:
    return [r["id"] for r in rows]


# ----------------------------------------------------------------------------- normalization

def test_normalize_dimensions_and_units() -> None:
    assert catalog.normalize_text("Кабель ВВГнг 3х2,5") == "кабель ввгнг 3x2.5"
    assert catalog.normalize_text("ВВГ 3 x 2.5") == "ввг 3x2.5"
    assert catalog.normalize_text("Диф.авт. 1p+N 16A (30mA) 18kA") == "диф.авт. 1p+n 16а (30ма) 18ka"
    assert catalog.normalize_text("***LED 30Вт 4000К IP20!!!") == "led 30w 4000k ip20"
    assert catalog.normalize_text("Ёлка 0,2-4мм²") == "елка 0.2-4мм2"


def test_article_like_detection() -> None:
    assert all(catalog.is_article_like(t) for t in ("027228", "200300285_", "ярп4520", "drx250", "221-413"))
    assert not any(catalog.is_article_like(t) for t in ("кабель", "16а", "led", "3p"))


# ----------------------------------------------------------------------------- search

def test_article_search_exact_and_prefix() -> None:
    assert ids(catalog.search("200300285_"))[:1] == [515291]
    assert ids(catalog.search("200300285"))[:1] == [515291]  # trailing '_' optional
    assert ids(catalog.search("027228"))[:1] == [515291]  # manufacturer article inside the name
    assert ids(catalog.search("ярп4520"))[:1] == [45357]  # Cyrillic article
    assert ids(catalog.get_by_article("200300285"))[:1] == [515291]


def test_article_search_with_brand_word() -> None:
    assert ids(catalog.search("Legrand 027228"))[0] == 515291


def test_name_search_cyrillic() -> None:
    rows = catalog.search("Диф.авт. 1p+N 16А", limit=5)
    assert rows and rows[0]["id"] == 25397
    assert all("16а" in catalog.normalize_text(r["name"]) for r in rows)


def test_dimension_normalization_cable() -> None:
    a = catalog.search("кабель ввг 3х2,5", limit=5)
    b = catalog.search("ВВГ 3x2.5", limit=5)
    assert a and b
    assert all("3x2.5" in catalog.normalize_text(r["name"]) for r in a + b)


def test_synonyms_and_slug_matching() -> None:
    rows = catalog.search("дифавтомат 16а", limit=5)
    assert rows and all("16а" in catalog.normalize_text(r["name"]) for r in rows)
    rows = catalog.search("щиты металлические", limit=5)
    assert rows and any(r["cat2"] == "shchity_metallicheskie" for r in rows)


def test_filters_and_exclude() -> None:
    rows = catalog.search("автомат 16А", brand="Legrand", limit=5)
    assert rows and all(r["brand"] == "Legrand" for r in rows)
    rows = catalog.search("DRX250", exclude_id=515291, limit=10)
    assert rows and 515291 not in ids(rows)
    rows = catalog.search("выключатель", category="avtomaticheskie_vyklyuchateli", limit=5)
    assert rows and all("avtomaticheskie_vyklyuchateli" in " ".join(filter(None, (r["cat1"], r["cat2"], r["cat3"]))) for r in rows)


def test_search_latency() -> None:
    queries = ["027228", "кабель ввг 3х2,5", "автоматический выключатель 3p 160а", "розетка с заземлением 16а", "led 30w 4000k", "nonexistent-zzz-999"]
    for q in queries:
        catalog.search(q)  # warm-up (connection / page cache)
    t = time.perf_counter()
    for q in queries:
        catalog.search(q)
    per_query_ms = (time.perf_counter() - t) * 1000 / len(queries)
    assert per_query_ms < 100, f"{per_query_ms:.1f} ms per query"


def test_same_category_products() -> None:
    p = catalog.get_product(515291)
    rows = catalog.same_category_products(p, limit=10, exclude_id=515291)
    assert rows and 515291 not in ids(rows)
    assert all(r["cat2"] == p["cat2"] for r in rows)


# ----------------------------------------------------------------------------- detail helpers

def test_humanize_properties_and_kratnost() -> None:
    props = {"NOMINALNYY_TOK": "250 А", "KOLICHESTVO_POLYUSOV": "3", "CML2_TRAITS": ["x"], "RECOMMEND": ["1"], "NOVINKA": "Нет",
             "VYKHODNOE_NAPRYAZHENIE_": "65", "KRATNOST_MIN": "5", "UNKNOWN_KOD": ""}
    h = catalog.humanize_properties(props)
    assert h["Номинальный ток"] == "250 А" and h["Количество полюсов"] == "3"
    assert h["Выходное напряжение"] == "65"  # unknown code, reverse-transliterated
    assert "CML2_TRAITS" not in h and "Новинка" not in h and not any(k.startswith("UNKNOWN") for k in h)
    assert catalog.kratnost({"properties": props}) == 5
    assert catalog.kratnost(None) == 1


def test_stock_status_and_card_offline() -> None:
    import asyncio

    detail = {"id": 1, "price": 10, "stores": [{"name": "Алматы", "quantity": 4}, {"name": "Брак", "quantity": 9}], "properties": {"TORGOVAYA_MARKA": " IEK "}}
    assert catalog.stock_status(detail) == "in_stock"
    assert catalog.stock_status({"stores": [{"name": "Алматы", "quantity": 0}]}) == "out_of_stock"
    assert catalog.stock_status(None) == "unknown"
    card = asyncio.run(catalog.product_card({"id": 1, "name": "Тест", "brand": None}, detail, with_detail=False))
    assert card["quantity"] == 4 and card["in_stock"] and card["stores"] == [{"name": "Алматы", "quantity": 4}]
    assert card["brand"] == "IEK" and card["reason"] is None and card["certificates"] == []
    stale = asyncio.run(catalog.product_card({"id": 2, "name": "Тест", "price": 999}, None, with_detail=False))
    assert stale["price"] is None and stale["stock_status"] == "unknown"


def test_extract_specs_and_type_words() -> None:
    s = analogs.extract_specs("007886 Диф.авт. 1p+N 16А (30мА) 411002 Legrand (1)!!!")
    assert s == {"current": 16.0, "leakage": 30.0, "poles": "1+n"}
    s = analogs.extract_specs("***LED STARK 30W 2400Lm d98x180 4000K IP20 MEGALIGHT (20)")
    assert s["power"] == 30 and s["colour_temp"] == 4000 and s["ip"] == 20 and s["lumen"] == 2400 and "cores" not in s
    assert analogs.extract_specs("ВВГнг(А)-LS 3х2,5")["section"] == 2.5
    assert analogs.extract_specs("ВА47-29 1Р C16 4,5кА IEK")["current"] == 16
    assert analogs.type_words("007886 Диф.авт. 1p+N 16А (30мА) 411002 Legrand (1)!!!", "Legrand") == ["диф", "автомат"]


# ----------------------------------------------------------------------------- live API

@pytest.fixture
def fresh_api_loop_state() -> None:
    """pytest-asyncio gives every test its own loop; ekt_api's module-level semaphore/client are bound to the first one."""
    import asyncio

    from app import ekt_api

    ekt_api._sem = asyncio.Semaphore(8)
    ekt_api._client = None


@network
@pytest.mark.usefixtures("fresh_api_loop_state")
async def test_product_card_live() -> None:
    card = await catalog.product_card(catalog.get_product(515291))
    assert card["article"] == "200300285_" and card["stock_status"] in ("in_stock", "out_of_stock")
    assert "Количество полюсов" in card["properties"] and card["brand"] == "Legrand"


@network
@pytest.mark.usefixtures("fresh_api_loop_state")
@pytest.mark.parametrize("pid", [45357, 25397])
async def test_analogs_for_out_of_stock(pid: int) -> None:
    t = time.perf_counter()
    cards = await analogs.find_analogs(pid, limit=3)
    elapsed = time.perf_counter() - t
    assert cards, f"no analogs for {pid}"
    assert all(c["in_stock"] and c["quantity"] > 0 for c in cards)
    assert all(c["id"] != pid and c["reason"] and 0 < c["score"] <= 1 for c in cards)
    assert cards == sorted(cards, key=lambda c: -c["score"])
    assert elapsed < 60, f"analogs took {elapsed:.1f}s"  # the live detail API alone takes 2-8 s per call
