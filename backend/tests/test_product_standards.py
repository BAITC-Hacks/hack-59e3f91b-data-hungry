"""Catalog standards evidence stays verbatim and survives compact tool cards.

These tests use catalog-shaped dictionaries only: no model or network calls.
"""
from __future__ import annotations

from typing import Any

import pytest

from app import agent
from app.product_standards import extract_standard_context


def _context(card: dict[str, Any]) -> list[dict[str, Any]]:
    context = extract_standard_context(card)
    assert isinstance(context, list)
    for entry in context:
        assert set(entry) == {"source", "text", "truncated"}
        assert isinstance(entry["source"], str) and entry["source"].strip()
        assert isinstance(entry["text"], str) and entry["text"].strip()
        assert isinstance(entry["truncated"], bool)
    return context


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("standard", ["ГОСТ Р МЭК 62275-2015", "ТР ТС 004/2011"])
def test_late_description_evidence_survives_card_compaction(full: bool, standard: str) -> None:
    statement = f"Изделие соответствует {standard}."
    description = "Описание назначения и способа монтажа изделия. " * 30 + statement
    assert description.index(standard) > 600
    card = {"id": 101, "name": "Тестовый товар", "description": description}

    context = _context(card)
    assert context and any(statement in item["text"] for item in context)
    visible = agent._card_for_llm(card, full=full)
    assert visible["standards_context"] == context


@pytest.mark.parametrize("full", [False, True])
def test_standard_property_beyond_both_property_limits_is_visible(full: bool) -> None:
    properties = {f"Параметр {index}": str(index) for index in range(35)}
    properties["Соответствие стандартам"] = "ГОСТ Р МЭК 62275-2015; ТР ТС 004/2011"
    card = {"id": 102, "name": "Тестовый товар", "properties": properties}

    context = _context(card)
    assert context
    assert any(properties["Соответствие стандартам"] in item["text"] for item in context)
    visible = agent._card_for_llm(card, full=full)
    assert "Соответствие стандартам" not in visible["properties"]
    assert visible["standards_context"] == context


@pytest.mark.parametrize("value", ["Нет", "Да"])
def test_boolean_gost_property_is_preserved_without_inventing_details(value: str) -> None:
    context = _context({"id": 103, "name": "Тестовый товар", "properties": {"Гост": value}})

    assert len(context) == 1
    entry = context[0]
    assert entry["text"] in {value, f"Гост: {value}"}
    assert "гост" in f"{entry['source']} {entry['text']}".casefold()
    assert not any(character.isdigit() for character in entry["text"])
    assert entry["truncated"] is False


@pytest.mark.parametrize(
    "description",
    [
        "Качество электроэнергии должно соответствовать ГОСТ Р 54149-2010.",
        "Изделие не соответствует ГОСТ Р МЭК 62275-2015; испытания не проводились.",
        "Соответствие ТР ТС 004/2011 не подтверждено. Декларация отсутствует.",
        "Требование относится только к питающей сети. "
        "Качество электроэнергии должно соответствовать ГОСТ Р 54149-2010. "
        "Это не подтверждает соответствие самого изделия.",
        "Изделие соответствует ГОСТ Р МЭК 62275-2015 только при монтаже внутри помещений. "
        "Для наружного монтажа соответствие не заявлено.",
    ],
)
def test_scope_negation_and_adjacent_qualifiers_are_kept_verbatim(description: str) -> None:
    context = _context({"id": 104, "name": "Тестовый товар", "description": description})

    assert context
    assert any(description in item["text"] for item in context)
    assert all(item["truncated"] is False for item in context)


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize(
    "card",
    [
        {"id": 105, "name": "Светильник для гостиницы", "description": "Для гостиниц и офисов."},
        {"id": 106, "name": "Кабель", "description": "Для стационарного монтажа.", "properties": {"Цвет": "Белый"}},
        {"id": 107, "name": "Тестовый товар", "description": None, "properties": None},
    ],
)
def test_no_standard_evidence_is_invented(card: dict[str, Any], full: bool) -> None:
    assert _context(card) == []
    assert not agent._card_for_llm(card, full=full).get("standards_context")


@pytest.mark.parametrize("full", [False, True])
def test_unverified_certificates_are_not_promoted_to_standards_evidence(full: bool) -> None:
    card = {
        "id": 108,
        "name": "Тестовый товар",
        "certificates": [
            {
                "title": "Сертификат соответствия ГОСТ Р МЭК 62275-2015",
                "number": "ТР ТС 004/2011",
                "url": "https://example.test/unverified-certificate.pdf",
            }
        ],
    }

    assert _context(card) == []
    visible = agent._card_for_llm(card, full=full)
    assert "certificates" not in visible
    assert not visible.get("standards_context")
