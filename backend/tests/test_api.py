"""HTTP API smoke tests. catalog/knowledge/analogs/certificates/attachments are replaced per-test via monkeypatch
(no network, no DB); modules that do not exist yet get an empty placeholder so app.main can be imported."""
from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

for _name in ("catalog", "knowledge", "analogs", "certificates", "attachments"):
    try:
        importlib.import_module(f"app.{_name}")
    except ImportError:  # module not written yet -> placeholder so that `from . import <name>` works
        sys.modules[f"app.{_name}"] = types.ModuleType(f"app.{_name}")

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, config, ekt_api, main  # noqa: E402

PRODUCT = {"id": 515291, "name": "027228 АВ DRX250 MT 3ф 160А 18ka Legrand", "article": "200300285_", "price": 64920,
           "image": None, "url": "https://ekt.kz/catalog/x/027228/", "cat1": "nizkovoltnaya_apparatura", "cat2": None, "cat3": None,
           "brand": "Legrand"}
DETAIL = {**PRODUCT, "stores": [{"name": "Алматы", "quantity": 5}, {"name": "Нур-Султан", "quantity": 8}],
          "properties": {"KRATNOST_MIN": "1", "NOMINALNYY_TOK": "160 А"}, "description": "Автомат"}


def _card(p, detail=None):
    return {**p, "quantity": 13, "in_stock": True, "stock_status": "in_stock", "stores": [{"name": "Алматы", "quantity": 5}],
            "properties": {"Номинальный ток": "160 А"}, "kratnost": 1, "description": "", "reason": None, "certificates": []}


@dataclass
class Attachment:
    id: str
    filename: str
    kind: str
    summary: str = ""
    session_id: str = ""


def _stubs() -> dict[str, SimpleNamespace]:
    async def product_card(product, detail=None, *, with_detail=True):
        return _card(product, detail)

    async def product_cards(products):
        return [_card(p) for p in products]

    def ensure_db():
        raise RuntimeError("no db")

    catalog = SimpleNamespace(
        ensure_db=ensure_db,
        count_products=lambda: 1,
        get_product=lambda pid: PRODUCT if int(pid) == PRODUCT["id"] else None,
        get_by_article=lambda a: [PRODUCT] if a in ("027228", "200300285_") else [],
        search=lambda q, limit=10, **kw: [PRODUCT] if "legrand" in q.lower() else [],
        humanize_properties=lambda props: {"Номинальный ток": props.get("NOMINALNYY_TOK", "")},
        product_card=product_card,
        product_cards=product_cards,
    )
    knowledge = SimpleNamespace(
        TOPICS=["delivery", "payment", "returns", "minimum_order", "contacts", "company", "howto", "faq", "certificates"],
        search_terms=lambda q, topic=None, limit=5: [{"source": "delivery.md", "title": "Доставка", "text": "По Алматы бесплатно от 50 000 ₸", "url": None}],
        get_topic=lambda t: "Контакты: +7 (727) 346-88-88",
    )

    async def find_analogs(product_id, limit=3, *, only_in_stock=True):
        return [{**_card(PRODUCT), "id": 1, "reason": "тот же ток и число полюсов", "score": 0.9}]

    analogs = SimpleNamespace(find_analogs=find_analogs)
    certificates = SimpleNamespace(
        certificates_for=lambda product, detail=None: [{"title": "Сертификат ЕАЭС (демо)", "url": "http://localhost/cert/1", "demo": True}]
    )
    registry: dict[str, Attachment] = {}

    async def save_and_parse(filename, content, mime, session_id=""):
        att = Attachment(id=f"att_{len(registry) + 1}", filename=filename, kind="excel" if filename.endswith("xlsx") else "image", summary=f"{len(content)} bytes", session_id=session_id)
        registry[att.id] = att
        return att

    def get_attachment(att_id, session_id=None):
        att = registry.get(att_id)
        return att if att and (session_id is None or att.session_id == session_id) else None

    attachments = SimpleNamespace(
        save_and_parse=save_and_parse,
        get_attachment=get_attachment,
        image_block=lambda att: {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}},
        text_for_llm=lambda att, max_chars=6000: f"file {att.filename}",
    )
    return {"catalog": catalog, "knowledge": knowledge, "analogs": analogs, "certificates": certificates, "attachments": attachments}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Stub the sibling modules inside agent/main, remove any LLM credential, fake the live detail API."""
    stubs = _stubs()
    for name, stub in stubs.items():
        monkeypatch.setattr(agent, "attachments_mod" if name == "attachments" else name, stub)
    monkeypatch.setattr(main, "catalog", stubs["catalog"])
    monkeypatch.setattr(main, "attachments", stubs["attachments"])
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")

    async def _fetch(pid: int, *, ttl=None):
        return DETAIL if int(pid) == PRODUCT["id"] else None

    monkeypatch.setattr(ekt_api, "fetch_detail", _fetch)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["products"] == 1 and body["model"] and body["llm_configured"] is False


def test_root_redirects_to_demo(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/widget/demo/index.html"


def test_search_and_product(client):
    r = client.get("/api/products/search", params={"q": "027228"})
    assert r.status_code == 200
    products = r.json()["products"]
    assert products[0]["id"] == PRODUCT["id"] and products[0]["in_stock"] is True
    assert products[0]["certificates"][0]["title"].startswith("Сертификат")
    assert client.get("/api/products/search", params={"q": ""}).json() == {"products": []}

    r = client.get(f"/api/products/{PRODUCT['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["article"] == "200300285_" and body["detail"]["stores"][0]["name"] == "Алматы"
    assert body["detail"]["properties"] == {"Номинальный ток": "160 А"}
    assert client.get("/api/products/999999").status_code == 404


def test_upload_validation(client):
    r = client.post("/api/upload", files={"file": ("virus.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    r = client.post("/api/upload", files={"file": ("spec.xlsx", b"PK\x03\x04data", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert r.status_code == 200
    body = r.json()
    assert body["attachment_id"].startswith("att_") and body["kind"] == "excel" and body["filename"] == "spec.xlsx"
    r = client.post("/api/upload", files={"file": ("empty.png", b"", "image/png")})
    assert r.status_code == 400


def test_attachment_is_bound_to_upload_session(client):
    uploaded = client.post("/api/upload", data={"session_id": "owner-session-0001"},
                           files={"file": ("request.txt", b"Need cable", "text/plain")})
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["session_id"] == "owner-session-0001"
    response = client.post("/api/chat", json={"session_id": "other-session-0001", "message": "прочитай файл",
                                              "attachment_ids": [body["attachment_id"]]})
    assert response.status_code == 404


def test_confirm_without_pending_is_409(client):
    r = client.post("/api/chat/confirm", json={"session_id": "sess1-sess1-sess1", "action_id": "act_nothing", "confirm": True})
    assert r.status_code == 409


def test_chat_without_llm_key_is_helpful(client):
    r = client.post("/api/chat", json={"session_id": None, "message": "Есть ли 027228?"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] and "ANTHROPIC_API_KEY" in body["reply"]
    assert body["cart"]["count"] == 0 and body["pending_action"] is None and body["latency_ms"] >= 0


def test_button_add_confirm_and_cart_page(client):
    sid = "abc123-abc123-abc123"
    # widget button message -> proposal (works even without the LLM)
    r = client.post("/api/chat", json={"session_id": sid, "message": f"Добавь {PRODUCT['name']} (id {PRODUCT['id']}) — 2 шт"})
    body = r.json()
    assert body["pending_action"] is not None and body["cart_updated"] is False
    action = body["pending_action"]
    assert action["items"][0]["qty"] == 2 and action["items"][0]["max_qty"] == 13
    assert "Подтвердите" in body["reply"]

    # explicit textual confirmation -> cart updated, link returned
    r = client.post("/api/chat", json={"session_id": sid, "message": "да, добавь"})
    body = r.json()
    assert body["cart_updated"] is True and body["pending_action"] is None
    assert body["cart"]["count"] == 1 and body["cart"]["items"][0]["qty"] == 2 and body["cart"]["total"] == 2 * 64920
    assert body["cart"]["url"] in body["reply"]

    # confirm endpoint with the stale id -> 409
    r = client.post("/api/chat/confirm", json={"session_id": sid, "action_id": action["action_id"], "confirm": True})
    assert r.status_code == 409

    # button flow: propose again, then reject via the endpoint
    r = client.post("/api/chat", json={"session_id": sid, "message": f"Добавь товар (id {PRODUCT['id']}) — 50 шт"})
    action = r.json()["pending_action"]
    r = client.post("/api/chat/confirm", json={"session_id": sid, "action_id": action["action_id"], "confirm": False})
    assert r.status_code == 200 and r.json()["cart_updated"] is False and r.json()["cart"]["items"][0]["qty"] == 2

    # confirm via endpoint: 50 requested, 13 in stock, 2 already in cart -> +11
    r = client.post("/api/chat", json={"session_id": sid, "message": f"Добавь товар (id {PRODUCT['id']}) — 50 шт"})
    action = r.json()["pending_action"]
    r = client.post("/api/chat/confirm", json={"session_id": sid, "action_id": action["action_id"], "confirm": True})
    body = r.json()
    assert body["cart_updated"] is True and body["cart"]["items"][0]["qty"] == 13

    # JSON cart, HTML cart page, delete item
    assert client.get(f"/api/cart/{sid}").json()["count"] == 1
    r = client.get(f"/cart/{sid}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert PRODUCT["name"] in r.text and "ekt.kz/personal/cart" in r.text and "843 960" in r.text
    assert client.delete(f"/api/cart/{sid}/items/{PRODUCT['id']}").json()["count"] == 0
    assert "Корзина пуста" in client.get(f"/cart/{sid}").text
    assert client.get("/cart/bad id!").status_code == 400


def test_textual_rejection(client):
    sid = "rej1-rej1-rej1-rej1"
    client.post("/api/chat", json={"session_id": sid, "message": f"Добавь (id {PRODUCT['id']}) — 1 шт"})
    r = client.post("/api/chat", json={"session_id": sid, "message": "нет"})
    body = r.json()
    assert body["pending_action"] is None and body["cart"]["count"] == 0 and "ничего не добавляю" in body["reply"].lower()
