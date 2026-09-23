"""CartStore: propose / confirm clamping with a fake ekt_api.fetch_detail (no network)."""
from __future__ import annotations

import time

import pytest

from app import ekt_api
from app.cart import CartStore, PendingActionError, kratnost_of

# product_id -> live detail stub
DETAILS: dict[int, dict] = {
    1: {"id": 1, "name": "Автомат 16А", "article": "A16", "price": 1500, "url": "https://ekt.kz/p/1",
        "stores": [{"name": "Алматы", "quantity": 20}, {"name": "Астана", "quantity": 3}, {"name": "Брак MEGALIGHT", "quantity": 99}],
        "properties": {"KRATNOST_MIN": "1"}},
    2: {"id": 2, "name": "Кабель 5 м", "article": "K5", "price": 900, "url": "https://ekt.kz/p/2",
        "stores": [{"name": "Алматы", "quantity": 23}], "properties": {"KRATNOST_MIN": "5"}},
    3: {"id": 3, "name": "Лампа", "article": "L1", "price": 300, "stores": [{"name": "Алматы", "quantity": 0}], "properties": {}},
    4: {"id": 4, "name": "Щит без цены", "article": "S0", "price": None, "stores": [{"name": "Алматы", "quantity": 2}], "properties": {}},
}


@pytest.fixture(autouse=True)
def fake_detail(monkeypatch):
    async def _fetch(pid: int, *, ttl=None):
        return DETAILS.get(int(pid))

    monkeypatch.setattr(ekt_api, "fetch_detail", _fetch)


@pytest.fixture
def store() -> CartStore:
    return CartStore()


def test_kratnost_of():
    assert kratnost_of({"properties": {"KRATNOST_MIN": "5"}}) == 5
    assert kratnost_of({"properties": {"KRATNOST_MIN": "2,0"}}) == 2
    assert kratnost_of({"properties": {}}) == 1
    assert kratnost_of(None) == 1
    assert kratnost_of({"properties": {"KRATNOST_MIN": "0"}}) == 1


async def test_propose_builds_pending_with_live_stock(store):
    action = await store.propose("s1", [{"product_id": 1, "qty": 50}, {"product_id": 2, "qty": 7}, {"product_id": 3, "qty": 1}])
    assert action["action_id"].startswith("act_") and action["type"] == "add_to_cart"
    by_id = {i["product_id"]: i for i in action["items"]}
    assert by_id[1]["max_qty"] == 23 and "23" in by_id[1]["note"]  # service store excluded, over-stock warned
    assert by_id[2]["kratnost"] == 5 and "кратность" in by_id[2]["note"]
    assert by_id[3]["max_qty"] == 0 and by_id[3]["note"] == "нет в наличии"
    assert store.pending("s1")["action_id"] == action["action_id"]
    assert store.get_json("s1")["count"] == 0  # nothing added yet


async def test_propose_merges_duplicates_and_replaces_previous(store):
    first = await store.propose("s1", [{"product_id": 1, "qty": 1}])
    second = await store.propose("s1", [{"product_id": 2, "qty": 2}, {"product_id": 2, "qty": 3}])
    assert store.pending("s1")["action_id"] == second["action_id"] != first["action_id"]
    assert second["items"][0]["qty"] == 5


async def test_confirm_clamps_to_stock_and_kratnost(store):
    action = await store.propose("s1", [{"product_id": 1, "qty": 50}, {"product_id": 2, "qty": 12}, {"product_id": 3, "qty": 1}])
    result = await store.confirm("s1", action["action_id"])
    applied = {a["product_id"]: a["qty"] for a in result["applied"]}
    assert applied == {1: 23, 2: 10}  # 50 -> stock 23; 12 -> 10 (multiple of 5)
    assert [s["product_id"] for s in result["skipped"]] == [3]
    assert any("на складе 23" in n for n in result["notes"]) and any("кратность" in n for n in result["notes"])
    cart = result["cart"]
    assert cart["count"] == 2 and cart["total"] == 23 * 1500 + 10 * 900
    assert cart["url"].endswith("/cart/s1")
    assert store.pending("s1") is None


async def test_confirm_does_not_increase_request_to_minimum_pack(store):
    action = await store.propose("s1", [{"product_id": 2, "qty": 2}])
    assert "товар не будет добавлен" in action["items"][0]["note"]
    result = await store.confirm("s1", action["action_id"])
    assert result["applied"] == []
    assert "минимальная партия 5 шт" in result["skipped"][0]["reason"]
    assert "запрошено 2 шт" in result["skipped"][0]["reason"]
    assert result["cart"]["count"] == 0


async def test_confirm_pack_rounding_respects_existing_cart_and_stock(store):
    first = await store.propose("s1", [{"product_id": 2, "qty": 15}])
    await store.confirm("s1", first["action_id"])
    second = await store.propose("s1", [{"product_id": 2, "qty": 10}])
    assert "будет добавлено 5 шт" in second["items"][0]["note"]
    result = await store.confirm("s1", second["action_id"])
    assert result["applied"][0]["qty"] == 5
    assert result["cart"]["items"][0]["qty"] == 20
    third = await store.propose("s1", [{"product_id": 2, "qty": 5}])
    result = await store.confirm("s1", third["action_id"])
    assert result["applied"] == []
    assert "минимальной партии 5 шт" in third["items"][0]["note"]
    assert result["cart"]["items"][0]["qty"] == 20


async def test_confirm_rechecks_reduced_live_stock_before_adding(store, monkeypatch):
    stock = 23

    async def changing_detail(pid: int, *, ttl=None):
        assert pid == 2
        detail = dict(DETAILS[2])
        detail["stores"] = [{"name": "Алматы", "quantity": stock}]
        return detail

    monkeypatch.setattr(ekt_api, "fetch_detail", changing_detail)
    action = await store.propose("s1", [{"product_id": 2, "qty": 10}])
    stock = 4
    result = await store.confirm("s1", action["action_id"])
    assert result["applied"] == []
    assert "остаток 4 шт меньше минимальной партии 5 шт" in result["skipped"][0]["reason"]
    assert result["cart"]["count"] == 0


async def test_direct_add_is_idempotent_and_never_adds_more_than_requested(store):
    too_small = await store.add_requested("s1", [{"product_id": 2, "qty": 2}], request_id="click-small")
    assert too_small["applied"] == []
    assert store.get_json("s1")["count"] == 0
    first = await store.add_requested("s1", [{"product_id": 2, "qty": 7}], request_id="click-7")
    assert first["applied"][0]["qty"] == 5
    repeated = await store.add_requested("s1", [{"product_id": 2, "qty": 7}], request_id="click-7")
    assert repeated == first
    assert store.get_json("s1")["items"][0]["qty"] == 5


async def test_confirm_never_exceeds_stock_across_confirmations(store):
    a1 = await store.propose("s1", [{"product_id": 1, "qty": 20}])
    await store.confirm("s1", a1["action_id"])
    a2 = await store.propose("s1", [{"product_id": 1, "qty": 20}])
    result = await store.confirm("s1", a2["action_id"])
    assert result["applied"][0]["qty"] == 3
    assert store.get_json("s1")["items"][0]["qty"] == 23
    a3 = await store.propose("s1", [{"product_id": 1, "qty": 1}])
    result = await store.confirm("s1", a3["action_id"])
    assert not result["applied"] and result["skipped"][0]["product_id"] == 1


async def test_confirm_price_none_is_noted(store):
    action = await store.propose("s1", [{"product_id": 4, "qty": 1}])
    result = await store.confirm("s1", action["action_id"])
    assert result["applied"][0]["qty"] == 1
    assert any("цена по запросу" in n for n in result["notes"])
    assert result["cart"]["total"] == 0


async def test_confirm_requires_matching_pending(store):
    with pytest.raises(PendingActionError):
        await store.confirm("nobody", "act_x")
    action = await store.propose("s1", [{"product_id": 1, "qty": 1}])
    with pytest.raises(PendingActionError):
        await store.confirm("s1", "act_wrong")
    assert store.pending("s1") is not None  # a wrong id does not consume the proposal
    await store.confirm("s1", action["action_id"])
    with pytest.raises(PendingActionError):
        await store.confirm("s1", action["action_id"])  # already applied


async def test_pending_expires(store, monkeypatch):
    action = await store.propose("s1", [{"product_id": 1, "qty": 1}])
    monkeypatch.setattr(time, "time", lambda: action["_expires_ts"] + 1)
    assert store.pending("s1") is None
    with pytest.raises(PendingActionError):
        await store.confirm("s1", action["action_id"])


async def test_reject_remove_clear(store):
    action = await store.propose("s1", [{"product_id": 1, "qty": 1}])
    store.reject("s1", "act_other")
    assert store.pending("s1") is not None
    store.reject("s1", action["action_id"])
    assert store.pending("s1") is None

    action = await store.propose("s1", [{"product_id": 1, "qty": 2}, {"product_id": 2, "qty": 5}])
    await store.confirm("s1", action["action_id"])
    assert store.remove("s1", 1)["count"] == 1
    assert store.clear("s1")["count"] == 0
    assert store.get_json("other")["items"] == []


async def test_propose_rejects_garbage(store):
    with pytest.raises(ValueError):
        await store.propose("s1", [{"qty": 2}, {"product_id": "x"}])
