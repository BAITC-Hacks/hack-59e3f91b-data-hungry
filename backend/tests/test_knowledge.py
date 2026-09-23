"""Knowledge-base retrieval: the questions the jury is likely to ask must hit the right chunk first."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ on sys.path (no package install)

from app import knowledge  # noqa: E402


def test_chunks_loaded_from_all_files() -> None:
    sources = {c.source for c in knowledge.CHUNKS}
    assert {"delivery.md", "payment.md", "returns.txt", "contacts.txt", "company.md", "howto.txt", "faq.json"} <= sources
    assert len(knowledge.CHUNKS) > 80
    assert all(c.text and c.title for c in knowledge.CHUNKS)
    assert all(len(c.text) <= 2000 for c in knowledge.CHUNKS)


def test_faq_chunks_have_question_as_title() -> None:
    faq = [c for c in knowledge.CHUNKS if c.source == "faq.json"]
    assert any(c.title.startswith("Можно ли вернуть") for c in faq)
    assert all(c.url == "https://ekt.kz/about/faq/" for c in faq)


def test_txt_noise_dropped_and_split_by_city() -> None:
    contacts = [c for c in knowledge.CHUNKS if c.source == "contacts.txt"]
    assert len(contacts) == 9
    assert all("Экономьте" not in c.text and "›" not in c.text for c in contacts)
    astana = next(c for c in contacts if "Астана" in c.title)
    assert "astana@ekt.kz" in astana.text


def test_derived_topics_by_keywords() -> None:
    assert "кратност" in knowledge.get_topic("minimum_order").lower()
    assert "сертификат" in knowledge.get_topic("certificates").lower()
    assert "30 000" in knowledge.get_topic("delivery")


@pytest.mark.parametrize("word,expected", [("доставка", "доставк"), ("оплатить", "оплат"), ("лицу", "лиц"), ("минимальная", "минимальн"), ("стоит", "стоит")])
def test_stemmer(word: str, expected: str) -> None:
    assert knowledge.stem(word) == expected


def test_kazakh_keywords_are_translated() -> None:
    assert "доставк" in knowledge.tokenize("жеткізу бағасы қанша")
    assert "оплат" in knowledge.tokenize("төлем")


@pytest.mark.parametrize(
    "query,source,title_part,text_part",
    [
        ("сколько стоит доставка по Алматы", "delivery.md", "Доставка по Алматы", "30 000"),
        ("как оплатить юр лицу", None, "ридическ", "счёт"),
        ("минимальная партия", "delivery.md", "Минимальная партия", "кратность"),
        ("можно ли вернуть товар", None, "верн", "возврат"),
        ("доставка в Астану бесплатно от какой суммы", "delivery.md", "Доставка по Алматы", "400 000"),
        ("рассрочка", None, "рассрочк", "рассрочк"),
        ("график работы", None, "график работы", "09:00"),
    ],
)
def test_required_questions_hit_right_chunk_first(query: str, source: str | None, title_part: str, text_part: str) -> None:
    res = knowledge.search_terms(query, limit=3)
    assert res, query
    top = res[0]
    assert set(top) == {"source", "title", "text", "url"}
    if source:
        assert top["source"] == source, (query, top["title"])
    assert title_part.lower() in top["title"].lower(), (query, top["title"])
    assert text_part.lower() in top["text"].lower(), (query, top["text"][:100])
    assert top["url"].startswith("https://ekt.kz/")


def test_topic_filter_and_limit() -> None:
    res = knowledge.search_terms("оплата", topic="payment", limit=2)
    assert len(res) == 2
    assert all(r["url"] in ("https://ekt.kz/payments/", "https://ekt.kz/about/faq/") for r in res)
    assert knowledge.search_terms("") == []
    assert knowledge.search_terms("и в на") == []


def test_kazakh_query_finds_delivery_and_payment() -> None:
    assert "оставк" in knowledge.search_terms("жеткізу бағасы қанша")[0]["title"].lower()
    top = knowledge.search_terms("төлем қалай жасалады")[0]
    assert "оплат" in (top["title"] + top["text"]).lower()
