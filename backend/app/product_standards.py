"""Keep product-scoped standard references visible in compact LLM tool results.

These excerpts are source statements, not verified certificates or a compliance
verdict. Keep surrounding sentences so requirements, negations and scope survive.
"""
from __future__ import annotations

import html
import re
from typing import Any

_MARKER = re.compile(
    r"(?<!\w)(?:ГОСТ|GOST)(?![а-яa-z])|"
    r"(?<!\w)ТР\s*(?:ТС|ЕАЭС|РК)(?![а-яa-z])|"
    r"(?<!\w)техническ[а-я]*\s+регламент[а-я]*",
    re.IGNORECASE,
)
_TAGS = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
MAX_EXCERPT_CHARS = 1800
MAX_EXCERPTS = 8


def _clean(value: Any) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", str(value or ""))).split())


def extract_standard_context(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract attributable mentions before description/property truncation.

    The original wording and the source field are retained. Do not infer
    compliance from a marker, a certificate link, a category or another product.
    """
    sources = [("name", _clean(card.get("name")))]
    sources.extend((f"properties.{key}", _clean(f"{key}: {value}"))
                   for key, value in (card.get("properties") or {}).items())
    sources.append(("description", _clean(card.get("description"))))
    out: list[dict[str, Any]] = []
    for source, text in sources:
        if not _MARKER.search(text):
            continue
        sentences = _SENTENCE_BREAK.split(text)
        spans: list[tuple[int, int]] = []
        for i, sentence in enumerate(sentences):
            if not _MARKER.search(sentence):
                continue
            start, end = max(0, i - 1), min(len(sentences), i + 2)
            if spans and start <= spans[-1][1]:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
        for start, end in spans:
            excerpt = " ".join(sentences[start:end])
            truncated = len(excerpt) > MAX_EXCERPT_CHARS
            if truncated:
                hit = _MARKER.search(excerpt)
                offset = max(0, min(hit.start() - 400, len(excerpt) - MAX_EXCERPT_CHARS))
                excerpt = excerpt[offset:offset + MAX_EXCERPT_CHARS]
            if len(out) == MAX_EXCERPTS:
                out[-1]["truncated"] = True
                return out
            out.append({"source": source, "text": excerpt, "truncated": truncated})
    return out
