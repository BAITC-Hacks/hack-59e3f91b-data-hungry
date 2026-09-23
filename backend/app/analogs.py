"""Analog (substitute) search for a product, e.g. when it is out of stock.

Candidates = site RECOMMEND ids + FTS on the product's "type words" (+ its key spec tokens) + same category.
Live details are fetched concurrently in rounds of BATCH candidates (the detail API takes ~2-3 s per call, so the
first round usually settles it), out-of-stock candidates are dropped (only_in_stock), and each candidate is scored:

  score = 0.45 * spec_match + 0.25 * type_overlap + 0.12 * category + 0.03 * same_brand + 0.05 * price_proximity + 0.03 * recommended
  spec_match   - weighted share of the base product's key specs (current, poles, leakage, section, cores, power,
                 lamp socket, colour temperature, IP, voltage, breaking capacity, lumen, modules) that the candidate
                 matches; unknown on the candidate side gives quarter credit; a mismatch on a critical spec (current,
                 poles, leakage, section, cores, power, socket, modules) multiplies the final score by 0.35
  type_overlap - idf-weighted share of the base name's type words (non-numeric tokens minus brand/stop words) found
                 in the candidate, so a rare discriminating word ('телефонная') weighs more than 'розетка'
Specs are parsed from the normalized name first ('160а', '3p', '1p+n', '3x2.5', '30w', 'e27', '4000k', 'ip65', '30ма')
and from CML2 properties second. Each returned card carries 'reason' (Russian sentence) and 'score' in 0..1.
Site RECOMMEND ids are accessories more often than substitutes, so they are only a candidate source: they still
need type/category evidence to be returned.
"""
from __future__ import annotations

import functools
import math
import re
from typing import Any

from . import catalog, ekt_api
from .brands import BRANDS

MAX_CANDIDATES = 40
BATCH = 24  # details fetched per round (= ekt_api's concurrency); we stop early once `limit` strong analogs are in hand
MIN_SCORE = 0.2
STRONG_SCORE = 0.45
PROMO_CATS = {"spets_predlozhenie", "novinki", "arkhiv", "korzina_elektrika", "magazin_podarkov_1"}

# spec key -> (weight, critical)
SPECS: dict[str, tuple[float, bool]] = {
    "current": (3.0, True),
    "poles": (3.0, True),
    "leakage": (2.0, True),
    "section": (3.0, True),
    "cores": (3.0, True),
    "power": (3.0, True),
    "modules": (2.0, True),
    "socket": (3.0, True),
    "colour_temp": (2.0, False),
    "ip": (1.5, False),
    "voltage": (1.0, False),
    "breaking": (1.0, False),
    "lumen": (1.0, False),
}
# regexes over catalog.normalize_text(name); group 1 is the value
_NAME_SPEC_RE: dict[str, re.Pattern[str]] = {
    "current": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?[aа](?![a-zа-я0-9])"),
    "leakage": re.compile(r"(?<![\d.])(\d+)\s?ма(?![a-zа-я0-9])"),
    "voltage": re.compile(r"(?<![\d.])(\d{2,4})\s?[vв](?![a-zа-я0-9])"),
    "breaking": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?ka(?![a-zа-я0-9])"),
    "power": re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?w(?![a-zа-я0-9])"),
    "colour_temp": re.compile(r"(?<![\d.])(\d{4})\s?k(?![a-zа-я0-9])"),
    "ip": re.compile(r"ip\s?(\d\d)(?![\d])"),
    "lumen": re.compile(r"(?<![\d.])(\d+)\s?lm(?![a-zа-я0-9])"),
    "modules": re.compile(r"(?:(\d+)\s?мод|щр[нв][\W_п]{0,4}(\d+)(?![\d]))"),
}
_CURVE_CURRENT_RE = re.compile(r"(?<![a-zа-я0-9])[bcdсвд](\d{1,3})(?![\d.a-zа-я])")  # 'C16', 'С25': tripping curve + current
_SOCKET_RE = re.compile(r"(?<![a-zа-я0-9])(e14|e27|e40|g13|gx53|gu10|gu5\.3|g4|g9|g53|r7s|2g11)(?![a-zа-я0-9])")  # lamp base
_STRING_SPECS = ("poles", "socket")
_POLES_N_RE = re.compile(r"(?<![\d.])(\d)\s?[pпф]\s?\+\s?[nн]")
_POLES_RE = re.compile(r"(?<![\d.])(\d)\s?[pпф](?![a-zа-я0-9+])")
_CABLE_RE = re.compile(r"(?<![\d.a-zа-я])(\d{1,2})x(\d+(?:\.\d+)?)(?![\d.x])")
_PROP_SPEC: dict[str, str] = {
    "NOMINALNYY_TOK": "current",
    "KOLICHESTVO_POLYUSOV": "poles",
    "TOK_UTECHKI": "leakage",
    "NOMINALNOE_NAPRYAZHENIE": "voltage",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "breaking",
    "SECHENIE_MM2": "section",
    "KOLICHESTVO_ZHIL": "cores",
    "MOSHCHNOST": "power",
    "TSVETOVAYA_TEMPERATURA": "colour_temp",
    "STEPEN_ZASHCHITY": "ip",
    "SVETOVOY_POTOK": "lumen",
    "KOLICHESTVO_MONTAZHNYKH_MODULEY": "modules",
    "TSOKOL": "socket",
}
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

_BRAND_WORDS = {w for aliases in BRANDS.values() for a in aliases for w in a.split()} | {w for b in BRANDS for w in b.lower().split()}
_STOP_WORDS = {"new", "pro", "гост", "шт", "для", "с", "и", "под", "на", "в", "от", "до", "со", "без", "тм", "ту", "ekt", "ект",
               "кв", "мм", "мм2", "м", "уп", "fr", "cl", "mt", "dim", "quot", "standart", "standard", "eco"}
# spellings of the same product type
_TYPE_CANON = {
    "авдт": "диф", "дифф": "диф", "дифавтомат": "диф", "дифференциальный": "диф", "диф": "диф",
    "авт": "автомат", "ав": "автомат", "ва": "автомат", "ba": "автомат", "автоматический": "автомат", "автомат": "автомат",
    "выкл": "выключатель", "выключатель": "выключатель",
    "светильник": "светильник", "led": "led", "лампа": "лампа",
}


def _num(text: Any) -> float | None:
    m = _NUM_RE.search(str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def extract_specs(name: str, props: dict[str, Any] | None = None) -> dict[str, Any]:
    """Key technical parameters parsed from the (normalized) name, then from CML2 properties.

    Returns e.g. {'current': 16.0, 'poles': '1+n', 'leakage': 30.0} or {'power': 30.0, 'lumen': 2400.0, 'colour_temp': 4000.0, 'ip': 20.0}.
    """
    n = catalog.normalize_text(name)
    specs: dict[str, Any] = {}
    for key, rx in _NAME_SPEC_RE.items():
        m = rx.search(n)
        if m:
            specs[key] = float(next(g for g in m.groups() if g))
    if "current" not in specs and (m := _CURVE_CURRENT_RE.search(n)):
        specs["current"] = float(m.group(1))
    if m := _POLES_N_RE.search(n):
        specs["poles"] = f"{m.group(1)}+n"
    elif m := _POLES_RE.search(n):
        specs["poles"] = m.group(1)
    if m := _CABLE_RE.search(n):
        specs["cores"], specs["section"] = float(m.group(1)), float(m.group(2))
    if m := _SOCKET_RE.search(n):
        specs["socket"] = m.group(1)
    for code, key in _PROP_SPEC.items():
        if key in specs or not (props or {}).get(code):
            continue
        raw = str(props[code]).strip().lower()  # type: ignore[index]
        if key == "poles":
            specs[key] = f"{raw[0]}+n" if "+" in raw or "n" in raw else raw
        elif key == "socket":
            if m := _SOCKET_RE.search(catalog.normalize_text(raw)):
                specs[key] = m.group(1)
        elif (v := _num(raw)) is not None:
            specs[key] = v * (1000 if key == "voltage" and "кв" in raw else 1)
    return specs


def type_words(name: str, brand: str | None = None) -> list[str]:
    """Non-numeric words of the name that describe the product type ('диф', 'автомат', 'led', 'stark')."""
    skip = _BRAND_WORDS | _STOP_WORDS | {(brand or "").lower()}
    out: list[str] = []
    for tok in re.split(r"[\s./,()\[\]{}\-+:;]+", catalog.normalize_text(name)):
        if len(tok) >= 2 and tok.isalpha() and tok not in skip:
            w = _TYPE_CANON.get(tok, tok)
            if w not in out:
                out.append(w)
    return out


def _spec_equal(key: str, a: Any, b: Any) -> bool:
    """Spec match with a tolerance: 12 % for power/lumen, 10 % for voltage (220 == 230 == 240 V), exact otherwise."""
    if key in _STRING_SPECS:
        return str(a) == str(b)
    tol = 0.12 if key in ("power", "lumen") else 0.1 if key == "voltage" else 0.001
    return abs(float(a) - float(b)) <= tol * max(abs(float(a)), 1.0)


@functools.lru_cache(maxsize=8192)
def _idf(word: str) -> float:
    """Rarity weight of a type word across catalog names (common words like 'розетка' weigh less than 'телефонная')."""
    try:
        total = max(1, catalog.count_products())
        df = catalog.doc_frequency(word)
    except Exception:  # index unavailable (unit tests without a DB) -> all words equal
        return 1.0
    return math.log((total + 1) / (df + 1)) + 0.1


def _fmt_num(v: Any) -> str:
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}".replace(".", ",")


_TYPE_LABEL = {"диф": "дифавтомат", "автомат": "автоматический выключатель", "led": "LED", "выключатель": "выключатель"}


def _type_label(words: list[str]) -> str:
    """Human label for canonical type words: ['диф', 'автомат'] -> 'дифавтомат', ['led', 'stark'] -> 'LED stark'."""
    labels = [_TYPE_LABEL.get(w, w) for w in words]
    if "дифавтомат" in labels:  # a differential breaker is already an automatic breaker
        labels = [x for x in labels if x not in ("автоматический выключатель", "выключатель")]
    return " ".join(dict.fromkeys(labels))


def _spec_text(key: str, v: Any) -> str:
    if key == "poles":
        p = str(v).upper().replace("+N", "P+N")
        return p if "+" in p else f"{p} полюс{'' if p == '1' else 'а' if p in ('2', '3', '4') else 'ов'}"
    if key == "socket":
        return f"цоколь {str(v).upper()}"
    units = {"current": "А", "leakage": "мА", "voltage": "В", "breaking": "кА", "power": "Вт", "colour_temp": "K",
             "lumen": "лм", "cores": "жил", "section": "мм²", "modules": "модулей"}
    if key == "ip":
        return f"IP{int(float(v))}"
    if key == "section":
        return f"сечение {_fmt_num(v)} мм²"
    return f"{_fmt_num(v)} {units[key]}"


def score_candidate(base: dict[str, Any], cand: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Score 0..1 plus the facts used (matched/mismatched specs, category/brand/price flags) for the reason text.

    `base`/`cand`: {'specs', 'types', 'brand', 'price', 'cat1', 'cat2', 'cat3', 'recommended'}.
    """
    matched, mismatched, got_w, total_w = [], [], 0.0, 0.0
    for key, (w, critical) in SPECS.items():
        if key not in base["specs"]:
            continue
        total_w += w
        if key not in cand["specs"]:
            got_w += w * 0.25
        elif _spec_equal(key, base["specs"][key], cand["specs"][key]):
            matched.append(key)
            got_w += w
        else:
            mismatched.append((key, critical))
    common = [t for t in base["types"] if t in cand["types"]]
    type_score = sum(_idf(t) for t in common) / max(1e-9, sum(_idf(t) for t in base["types"])) if base["types"] else 0.0
    spec_score = got_w / total_w if total_w else type_score
    if base.get("cat3") and base["cat3"] == cand.get("cat3"):
        cat_score = 1.0
    elif base.get("cat2") and base["cat2"] == cand.get("cat2"):
        cat_score = 0.3 if base.get("cat1") in PROMO_CATS else 0.6
    else:
        cat_score = 0.0
    same_brand = bool(base.get("brand") and base["brand"] == cand.get("brand"))
    bp, cp = base.get("price") or 0, cand.get("price") or 0
    price_delta = (cp - bp) / bp if bp and cp else None
    price_score = max(0.0, 1 - abs(price_delta)) if price_delta is not None else 0.5
    score = 0.45 * spec_score + 0.25 * type_score + 0.12 * cat_score + 0.03 * same_brand + 0.05 * price_score + 0.03 * cand.get("recommended", 0)
    if any(critical for _, critical in mismatched):
        score *= 0.35
    facts = {"matched": matched, "mismatched": [k for k, _ in mismatched], "common_types": common, "cat_score": cat_score,
             "same_brand": same_brand, "price_delta": price_delta}
    return round(min(1.0, score), 3), facts


def build_reason(base: dict[str, Any], cand: dict[str, Any], facts: dict[str, Any], quantity: int | None) -> str:
    """Russian one-liner: 'Тот же тип: диф автомат, 16 А, 1P+N; бренд IEK вместо Legrand; в наличии 12 шт; дешевле на 30%'."""
    parts: list[str] = []
    common = facts["common_types"]
    missing = [t for t in base["types"] if t not in common and len(t) >= 3][:2]
    type_txt = _type_label(common) or _type_label(cand["types"][:3])
    # always show the candidate's real value; a tolerance match ('9 Вт' for a 10 W base) is marked with '≈'
    same = [
        _spec_text(k, cand["specs"][k]) + ("" if cand["specs"][k] == base["specs"][k] else f" (≈{_spec_text(k, base['specs'][k])})")
        for k in facts["matched"]
    ]
    if common and not missing:
        head = f"Тот же тип: {type_txt}"
    elif type_txt:
        head = f"Похожий товар ({type_txt})"
    else:
        head = "Похожий товар"
    parts.append(head + (", " + ", ".join(same) if same else ""))
    if common and missing:
        parts.append("не совпадает: " + ", ".join(missing))
    if facts["mismatched"]:
        parts.append("отличается: " + ", ".join(f"{_spec_text(k, cand['specs'][k])} вместо {_spec_text(k, base['specs'][k])}" for k in facts["mismatched"]))
    if cand.get("recommended") and common:
        parts.append("рекомендован на сайте к этому товару")
    if facts["cat_score"] >= 0.6:
        parts.append("та же категория")
    if cand.get("brand") and base.get("brand"):
        parts.append(f"тот же бренд {cand['brand']}" if facts["same_brand"] else f"бренд {cand['brand']} вместо {base['brand']}")
    elif cand.get("brand"):
        parts.append(f"бренд {cand['brand']}")
    if quantity is None:
        parts.append("наличие не подтверждено")
    elif quantity > 0:
        parts.append(f"в наличии {quantity} шт")
    else:
        parts.append("нет в наличии")
    d = facts["price_delta"]
    if d is not None:
        parts.append("цена такая же" if abs(d) < 0.03 else f"дешевле на {round(-d * 100)}%" if d < 0 else f"дороже на {round(d * 100)}%")
    return "; ".join(parts)


def _profile(row: dict[str, Any], detail: dict[str, Any] | None, recommended: bool = False) -> dict[str, Any]:
    props = (detail or {}).get("properties") or {}
    name = row.get("name") or (detail or {}).get("name") or ""
    brand = catalog.brand_of(row, detail)
    price = (detail or {}).get("price") if (detail or {}).get("price") is not None else row.get("price")
    return {"specs": extract_specs(name, props), "types": type_words(name, brand), "brand": brand, "price": price,
            "cat1": row.get("cat1"), "cat2": row.get("cat2"), "cat3": row.get("cat3"), "recommended": recommended}


def _row_for(cid: int, detail: dict[str, Any] | None) -> dict[str, Any]:
    """Catalog row, or a minimal row synthesized from the detail for ids missing in the index."""
    row = catalog.get_product(cid)
    if row:
        return row
    d = detail or {}
    return {"id": cid, "name": d.get("name") or "", "article": d.get("article") or "", "price": d.get("price"),
            "image": d.get("image"), "url": d.get("url") or "", "cat1": None, "cat2": None, "cat3": None, "brand": None}


async def find_analogs(product_id: int, limit: int = 3, *, only_in_stock: bool = True) -> list[dict[str, Any]]:
    """Top `limit` analog cards (with 'reason' and 'score') for a product; [] when nothing plausible is found.

    Args:
        product_id: catalog id of the base product.
        limit: how many analogs to return.
        only_in_stock: drop candidates whose live sellable stock is zero or unknown.
    """
    detail = await ekt_api.fetch_detail(product_id)
    row = catalog.get_product(product_id)
    if row is None and detail is None:
        return []
    row = row or _row_for(product_id, detail)
    base = _profile(row, detail)
    props = (detail or {}).get("properties") or {}

    # candidate ids in priority order: type words + key specs search, category-scoped spec search (names made of
    # codes only, e.g. 'ВА47-29 (1ф) 16А'), site recommendations, type-only search, same category
    cands: dict[int, bool] = {}  # id -> recommended by the site
    words = base["types"][:4]
    all_specs = [t.strip("()") for t in catalog.normalize_text(row["name"]).split()
                 if any(c.isdigit() for c in t) and not t.strip("()").isdigit() and not catalog.is_article_like(t.strip("()"))]
    spec_tokens = all_specs[:2]
    queries = [" ".join(words + spec_tokens), " ".join(words)] if words else [" ".join(spec_tokens)]
    for r in catalog.search(queries[0], limit=30, exclude_id=product_id) if queries[0].strip() else []:
        cands.setdefault(r["id"], False)
    scope = row.get("cat3") or row.get("cat2")
    if all_specs and scope:
        for r in catalog.search(" ".join(all_specs[:3]), limit=30, category=scope, exclude_id=product_id):
            cands.setdefault(r["id"], False)
    for rid in props.get("RECOMMEND") or []:
        if str(rid).isdigit() and int(rid) != product_id:
            cands[int(rid)] = True
    for q in queries[1:]:
        for r in catalog.search(q, limit=30, exclude_id=product_id) if q.strip() else []:
            cands.setdefault(r["id"], False)
    for r in catalog.same_category_products(row, limit=40, exclude_id=product_id):
        cands.setdefault(r["id"], False)
    cand_ids = list(cands)[:MAX_CANDIDATES]

    scored: list[tuple[float, int, dict[str, Any], dict[str, Any], int | None]] = []
    for start in range(0, len(cand_ids), BATCH):
        batch = cand_ids[start:start + BATCH]
        details = await ekt_api.fetch_details(batch)
        for cid in batch:
            d = details.get(cid)
            qty = ekt_api.total_quantity(d) if d else None
            if only_in_stock and not qty:
                continue
            crow = _row_for(cid, d)
            prof = _profile(crow, d, recommended=cands[cid])
            score, facts = score_candidate(base, prof)
            if score < MIN_SCORE or not (facts["common_types"] or facts["cat_score"]):
                continue  # RECOMMEND alone is no evidence: those ids are usually accessories, not substitutes
            scored.append((score, cid, crow, {"prof": prof, "facts": facts, "detail": d}, qty))
        if sum(1 for sc in scored if sc[0] >= STRONG_SCORE) >= limit:
            break
    scored.sort(key=lambda x: (-x[0], x[1]))

    cards = []
    for score, cid, crow, ctx, qty in scored[:limit]:
        card = await catalog.product_card(crow, ctx["detail"], with_detail=False)
        card["reason"] = build_reason(base, ctx["prof"], ctx["facts"], qty)
        card["score"] = score
        cards.append(card)
    return cards
