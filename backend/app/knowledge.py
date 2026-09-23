"""Knowledge base: purchase terms (delivery, payment, returns, contacts, FAQ...) with lexical retrieval.

Sources are loaded once at import from ``config.KNOWLEDGE_DIR``:

* ``*.md``   - split by ``##`` headings (a long section is further split by bullets/paragraphs);
* ``*.txt``  - scraped site pages; navigation/footer noise is dropped, text is split by page-specific
              heading patterns (city blocks in contacts, numbered steps in howto, paragraphs otherwise);
* ``faq.json`` - every ``{"q", "a"}`` pair is one chunk with ``title=q``.

Retrieval (``search_terms``) is deliberately simple and dependency-free: lowercase + ``ё->е`` +
punctuation stripping, a light Russian suffix stemmer, a synonym map (``оплата``/``платить``/``счет``/
``kaspi`` ...), a small Kazakh->Russian keyword map, and an idf-weighted overlap score with title and
topic bonuses. No LLM is needed, so it is fully testable offline.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

TOPICS = ["delivery", "payment", "returns", "minimum_order", "contacts", "company", "howto", "faq", "certificates"]

#: knowledge file name -> topic
FILE_TOPICS: dict[str, str] = {
    "delivery.md": "delivery",
    "payment.md": "payment",
    "returns.txt": "returns",
    "contacts.txt": "contacts",
    "company.md": "company",
    "howto.txt": "howto",
    "faq.json": "faq",
}

#: topic -> public page on ekt.kz that the chunk came from (returned as ``url``)
TOPIC_URLS: dict[str, str] = {
    "delivery": "https://ekt.kz/checkout-delivery/",
    "payment": "https://ekt.kz/payments/",
    "returns": "https://ekt.kz/return/",
    "faq": "https://ekt.kz/about/faq/",
    "howto": "https://ekt.kz/about/howto/",
    "contacts": "https://ekt.kz/about/contacts/",
    "company": "https://ekt.kz/about/",
    "minimum_order": "https://ekt.kz/checkout-delivery/",
    "certificates": "https://ekt.kz/about/faq/",
}

#: derived topics are assigned to any chunk whose text contains one of the substrings (normalized)
KEYWORD_TOPICS: dict[str, tuple[str, ...]] = {
    "minimum_order": ("кратн", "минимальн", "оптом", "оптов", "парти"),
    "certificates": ("сертификат", "декларац", "ст-kz", "ст kz", "гост"),
}

#: topic hints inside the query itself (used only as a tie-breaker bonus)
QUERY_TOPIC_HINTS: dict[str, tuple[str, ...]] = {
    "delivery": ("доставк", "привез", "курьер", "самовывоз", "забрать", "отправ"),
    "payment": ("оплат", "плат", "счет", "безнал", "карт", "каспи", "kaspi", "рассрочк", "кредит"),
    "returns": ("возврат", "вернут", "обмен", "обменят", "гарант"),
    "minimum_order": ("минимальн", "кратн", "парти", "опт"),
    "contacts": ("контакт", "телефон", "адрес", "график", "почт", "email", "филиал"),
    "certificates": ("сертификат", "декларац", "гост", "ст-kz"),
    "howto": ("оформ", "заказ", "корзин"),
}

# --- text normalization -----------------------------------------------------------------------

STOP_WORDS = frozenset(
    """а и в во на по для с со у к ко о об от до из за над под при про без через же бы ли не ни но
    или что это этот эта эти то так там тут как какой какая какие каков когда где куда откуда
    есть был была были будет быть мне меня мы вы вас вам ваш ваша ваши наш наша наши он она они его ее их
    я ты все всё еще уже ещё ну да нет можно ли надо нужно хочу хотим сколько чем тем кто кого ком
    подскажите скажите пожалуйста здравствуйте добрый день если бы б же ж вот только очень""".split()
)

# Russian light stemming: longest suffix first; a stem is kept only if >= 4 chars remain
_SUFFIXES = sorted(
    """ическая ического ическую ическим ической ических ический ически
    ованием ованию ования овании ование ованный ованная ованное ованные
    ениями ениях ением ению ения ении ение
    остями остях остью ости ость
    ениям ениях
    ами ями ого его ому ему ыми ими ах ях ой ей ий ый ая яя ое ее ую юю ым им ом ем ов ев ам ям
    ешь ишь ете ите ем им ет ит ут ют ат ят ешь
    ется ится аться яться иться еться ать ять ить еть уть ти ть ла ло ли л
    ы и а я о е у ю ь й""".split(),
    key=len,
    reverse=True,
)

#: Kazakh -> Russian keyword map (applied to whole tokens before stemming)
KK_RU: dict[str, str] = {
    "жеткізу": "доставка",
    "жеткізуі": "доставка",
    "жеткізудің": "доставка",
    "жеткізіледі": "доставка",
    "төлем": "оплата",
    "төлеу": "оплата",
    "төлемі": "оплата",
    "қайтару": "возврат",
    "қайтаруға": "возврат",
    "айырбастау": "обмен",
    "бағасы": "цена",
    "баға": "цена",
    "қоймада": "наличие",
    "қойма": "наличие",
    "кепілдік": "гарантия",
    "кепілдігі": "гарантия",
    "сертификат": "сертификат",
    "тегін": "бесплатно",
    "жұмыс": "работы",
    "кестесі": "график",
    "кесте": "график",
    "байланыс": "контакты",
    "мекенжай": "адрес",
    "телефон": "телефон",
    "заңды": "юридическое",
    "тұлға": "лицо",
    "жеке": "физическое",
    "тапсырыс": "заказ",
    "минималды": "минимальный",
    "ең": "",
    "аз": "минимальный",
    "көтерме": "оптом",
    "бөліп": "рассрочка",
    "төлеуге": "рассрочка",
    "несие": "кредит",
    "алматыда": "алматы",
    "алматыға": "алматы",
    "астанаға": "астана",
    "астанада": "астана",
    "шымкентке": "шымкент",
    "қалаға": "город",
    "қала": "город",
    "сома": "сумма",
    "сомадан": "сумма",
    "қанша": "",
    "тұрады": "стоит",
    "қалай": "",
    "бола": "",
    "ма": "",
    "ме": "",
    "ба": "",
    "бе": "",
    "па": "",
    "пе": "",
}

#: synonym groups (already-stemmed forms); every member expands to the whole group at query time
_SYNONYM_GROUPS: list[list[str]] = [
    ["доставк", "привез", "привоз", "курьер", "отправк", "отправ", "достав", "доставля"],
    ["оплат", "плат", "платеж", "счет", "безнал", "безналичн", "перечислен", "карт", "каспи", "kaspi", "халык", "halyk", "оплач"],
    ["возврат", "обмен", "вернут", "возвращ", "обменят", "верн"],
    ["минимальн", "кратн", "парти", "опт", "оптом", "оптов", "упаковк", "минимум"],
    ["рассрочк", "кредит", "рассрочку", "red"],
    ["самовывоз", "забрат", "забират", "заберу", "самост"],
    ["юр", "юридическ", "тоо", "ип", "компани", "организац", "b2b", "юрлиц", "предприят", "фирм"],
    ["физ", "физическ", "частн", "розниц", "розничн"],
    ["стоит", "стоимост", "цен", "тариф", "стоим", "дорог", "платн", "бесплатн"],
    ["бесплатн", "свыше", "сумм"],
    ["график", "режим", "час", "работ", "работаете", "открыт"],
    ["контакт", "телефон", "почт", "email", "адрес", "связат", "связ"],
    ["сертификат", "декларац", "гост", "соответств", "документ", "паспорт"],
    ["гарант", "гарантийн"],
    ["заказ", "оформ", "оформлен", "купит", "покупк", "приобрест"],
    ["наличи", "склад", "остат"],
    ["астан", "нур-султан", "нур", "султан"],
    ["город", "регион", "казахстан", "област"],
    ["скидк", "дисконт", "акци"],
    ["срок", "быстр", "долг", "когда"],
    ["аналог", "замен", "замена", "альтернатив"],
    ["спецификац", "перечен", "список", "смет"],
]
_SYNONYMS: dict[str, set[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _w in _group:
        _SYNONYMS.setdefault(_w, set()).update(_group)


def normalize(text: str) -> str:
    """Lowercase, ``ё -> е``, strip punctuation (keeps hyphens inside words like ``ст-kz``)."""
    t = text.lower().replace("ё", "е").replace(" ", " ")
    t = re.sub(r"[^\w\s\-]", " ", t)
    t = re.sub(r"(?<!\w)-|-(?!\w)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def stem(word: str) -> str:
    """Light Russian stemmer: cut the longest known suffix while >= 4 chars remain.

    A single-letter inflection may leave a 3-char stem (``лицу``/``лица`` -> ``лиц``, ``цена`` -> ``цен``),
    so short nouns still match across cases.
    """
    if len(word) <= 3 or not re.search(r"[а-я]", word):
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= (3 if len(suf) == 1 else 4):
            return word[: -len(suf)]
    return word


def tokenize(text: str, *, translate_kk: bool = True) -> list[str]:
    """Normalized, Kazakh-translated, stop-word-filtered, stemmed tokens."""
    out: list[str] = []
    for tok in normalize(text).split():
        if translate_kk and tok in KK_RU:
            tok = KK_RU[tok]
            if not tok:
                continue
        if tok in STOP_WORDS or len(tok) < 2:
            continue
        out.append(stem(tok))
    return out


# --- chunks -----------------------------------------------------------------------------------


@dataclass
class Chunk:
    """One retrievable piece of the knowledge base."""

    source: str
    title: str
    text: str
    topics: set[str]
    url: str
    tokens: set[str] = field(default_factory=set)
    title_tokens: set[str] = field(default_factory=set)
    n_tokens: int = 0

    def as_dict(self) -> dict:
        return {"source": self.source, "title": self.title, "text": self.text, "url": self.url}


_NOISE_LINES = {
    "главная", "›", "о компании", "контакты", "соцсети", "оставить заявку", "выберите город:", "выберите город",
    "экономьте свое время", "и деньги!", "при оформлении первого заказа", "на сайте для юр.лиц.",
    "дарим 5% дополнительной скидки!", "доставка", "бесплатно", "цены ниже,", "чем в торговом зале",
    "следующий заказ", "заходите в гости", "звоните нам", "пишите нам", "как сделать заказ",
    "условия возврата и обмена",
}
_CITY_NAMES = {"алматы", "астана", "шымкент", "актау", "атырау", "тараз", "усть-каменогорск", "караганда", "талдыкорган"}
_MAX_CHUNK_CHARS = 1700


def _clean_txt_lines(raw: str) -> list[str]:
    """Drop breadcrumb / footer / menu noise from a scraped page."""
    lines: list[str] = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or s.lower() in _NOISE_LINES or s.lower() in _CITY_NAMES:
            continue
        if re.fullmatch(r"пункт \d+ изложен.*", s.lower()):
            continue
        lines.append(s)
    return lines


def _split_long(text: str, limit: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split a long block on bullet/paragraph boundaries (then plain lines) into pieces <= ``limit`` chars."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    for p in re.split(r"\n(?=[-*•]\s|\d+\.\s|\n)", text):
        parts.extend(p.splitlines() if len(p) > limit else [p])
    out: list[str] = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if cur and len(cur) + len(p) + 1 > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def _chunks_md(name: str, raw: str, topic: str) -> list[Chunk]:
    doc_title = ""
    sections: list[tuple[str, list[str]]] = []
    for ln in raw.splitlines():
        if ln.startswith("# ") and not doc_title:
            doc_title = re.sub(r"\s*\(источник:.*?\)\s*", "", ln[2:]).strip()
            continue
        if ln.startswith("## "):
            sections.append((ln[3:].strip(), []))
            continue
        if not sections:
            sections.append((doc_title, []))
        sections[-1][1].append(ln)
    out: list[Chunk] = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        pieces = _split_long(body)
        for i, piece in enumerate(pieces):
            full_title = title if title == doc_title or not doc_title else f"{doc_title} — {title}"
            if len(pieces) > 1:
                full_title = f"{full_title} ({i + 1})"
            out.append(_mk(name, full_title, piece, topic))
    return out


def _chunks_txt(name: str, raw: str, topic: str) -> list[Chunk]:
    """Scraped page: title = first raw line; blocks start at city headings or numbered steps."""
    page_title = next((ln.strip() for ln in raw.splitlines() if ln.strip()), name)
    blocks: list[tuple[str, list[str]]] = [(page_title, [])]
    step_pending = False
    for ln in _clean_txt_lines(raw):
        if re.fullmatch(r"\d", ln):  # howto: a bare step number, its title is on the next line
            step_pending = True
            continue
        if step_pending or ln.startswith("Подразделение в г."):
            blocks.append((ln, []))
            step_pending = False
            continue
        blocks[-1][1].append(ln)
    out: list[Chunk] = []
    for title, body_lines in blocks:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        for i, piece in enumerate(_split_long(body)):
            t = page_title if title == page_title else f"{page_title} — {title}"
            if i:
                t = f"{t} ({i + 1})"
            out.append(_mk(name, t, piece, topic))
    return out


def _chunks_faq(name: str, raw: str, topic: str) -> list[Chunk]:
    out: list[Chunk] = []
    for item in json.loads(raw):
        q = (item.get("q") or "").strip()
        a = (item.get("a") or "").strip()
        if q and a:
            out.append(_mk(name, q, a, topic))
    return out


def _mk(source: str, title: str, text: str, topic: str) -> Chunk:
    topics = {topic}
    norm_all = normalize(f"{title} {text}")
    for extra, keys in KEYWORD_TOPICS.items():
        if any(k in norm_all for k in keys):
            topics.add(extra)
    toks = tokenize(f"{title} {text}", translate_kk=False)
    return Chunk(
        source=source,
        title=title,
        text=text,
        topics=topics,
        url=TOPIC_URLS.get(topic, config.EKT_SITE),
        tokens=set(toks),
        title_tokens=set(tokenize(title, translate_kk=False)),
        n_tokens=len(toks),
    )


def load_chunks(directory: Path | None = None) -> list[Chunk]:
    """Read every knowledge file in ``directory`` and split it into chunks."""
    directory = directory or config.KNOWLEDGE_DIR
    chunks: list[Chunk] = []
    if not directory.exists():
        return chunks
    for path in sorted(directory.iterdir()):
        topic = FILE_TOPICS.get(path.name, path.stem)
        raw = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            chunks.extend(_chunks_md(path.name, raw, topic))
        elif path.suffix == ".txt":
            chunks.extend(_chunks_txt(path.name, raw, topic))
        elif path.suffix == ".json":
            chunks.extend(_chunks_faq(path.name, raw, topic))
    return chunks


CHUNKS: list[Chunk] = load_chunks()

_DF: dict[str, int] = {}
for _c in CHUNKS:
    for _t in _c.tokens:
        _DF[_t] = _DF.get(_t, 0) + 1
_N_DOCS = max(1, len(CHUNKS))


def _idf(token: str) -> float:
    return math.log(1.0 + _N_DOCS / (1.0 + _DF.get(token, 0)))


def _query_topics(q_norm: str) -> set[str]:
    return {t for t, keys in QUERY_TOPIC_HINTS.items() if any(k in q_norm for k in keys)}


# --- public API ------------------------------------------------------------------------------


def search_terms(query: str, topic: str | None = None, limit: int = 5) -> list[dict]:
    """Return the best-matching knowledge chunks for a purchase-terms question.

    Args:
        query: user question in Russian or Kazakh (free text).
        topic: optional topic from ``TOPICS``; chunks of that topic get a strong bonus and
            unrelated chunks are dropped unless nothing else matches.
        limit: max number of chunks.

    Returns:
        List of ``{"source", "title", "text", "url"}`` dicts, best first. Empty when nothing matches.
    """
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    q_norm = normalize(query)
    hinted = _query_topics(q_norm)
    if topic:
        hinted.add(topic)
    # each query stem -> the set of chunk stems that count as a match
    groups: list[tuple[str, set[str]]] = []
    seen: set[str] = set()
    for t in q_tokens:
        if t in seen:
            continue
        seen.add(t)
        groups.append((t, _SYNONYMS.get(t, {t}) | {t}))

    scored: list[tuple[float, Chunk]] = []
    for ch in CHUNKS:
        score = 0.0
        matched = 0
        for orig, variants in groups:
            hit = [v for v in variants if v in ch.tokens]
            if not hit:
                # prefix match handles stems the suffix list missed (e.g. "оплатить" vs "оплата")
                hit = [v for v in variants if len(v) >= 5 and any(t.startswith(v) or v.startswith(t) for t in ch.tokens if len(t) >= 5)]
                if not hit:
                    continue
                weight = 0.6
            else:
                weight = 1.0 if orig in hit else 0.8
            matched += 1
            # rarity of the *query* concept, not of whichever synonym happened to be in the chunk
            w = _idf(orig) * weight
            if any(v in ch.title_tokens for v in hit):
                w *= 1.4
            score += w
        if not matched:
            continue
        coverage = matched / len(groups)
        score *= 0.5 + coverage  # prefer chunks covering more of the question
        if hinted & ch.topics:
            score *= 1.25 if topic in ch.topics else 1.1
        elif topic:
            score *= 0.4
        score /= 1.0 + 0.12 * math.log(max(1.0, ch.n_tokens / 120.0))
        scored.append((score, ch))
    scored.sort(key=lambda x: -x[0])
    return [ch.as_dict() for _s, ch in scored[:limit]]


def get_topic(topic: str) -> str:
    """Concatenated text of every chunk tagged with ``topic`` (for a "tell me everything about X" answer)."""
    parts = [f"## {c.title}\n{c.text}" for c in CHUNKS if topic in c.topics]
    return "\n\n".join(parts)


def topic_url(topic: str) -> str:
    """Public ekt.kz page for a topic."""
    return TOPIC_URLS.get(topic, config.EKT_SITE)
