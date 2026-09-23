"""Read-only access to the local catalog index (data/catalog.sqlite) + product card assembly.

The list API of ekt.kz has no search, so scripts/build_index.py dumps it into SQLite with:
  products      - one row per SKU (id, name, article, price, image, url, cat1..3, cat, brand, name_norm, article_norm, search_text)
  products_fts  - FTS5 word index over (name_norm, article, brand, cat), external content = products
  products_tri  - FTS5 trigram index over search_text (substring matching: partial articles, '2.5', 'нг-ls' ...)

Search pipeline (see search()):
  1. normalize the query exactly like the index text (normalize_text)
  2. article-like tokens -> exact article, article prefix, substring-in-name (trigram)
  3. FTS5 MATCH with all tokens ANDed (word stems with prefix '*', synonyms, Russian words also matched against
     transliterated category slugs), ranked by "tokens present in the name" then bm25
  4. fallbacks: OR of tokens, trigram substring per token
Every step honours brand/category filters and exclude_id; results are deduped and returned as plain dicts.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any

from . import config, ekt_api
from .brands import detect_brand

# --------------------------------------------------------------------------- normalization

# unit spellings that follow a number: Latin/Cyrillic variants -> one canonical form (used on both index and query side)
_UNIT_FOLD: dict[str, str] = {
    "a": "а", "а": "а",  # ampere -> Cyrillic а  (16A == 16А)
    "v": "в", "в": "в",  # volt   -> Cyrillic в
    "w": "w", "вт": "w",  # watt  -> Latin w
    "k": "k", "к": "k",  # kelvin (4000K)
    "ka": "ka", "ка": "ka",  # breaking capacity 18kA
    "kv": "кв", "кв": "кв",
    "ma": "ма", "ма": "ма",
    "lm": "lm", "лм": "lm",
    "mm": "мм", "мм": "мм",
    "mm2": "мм2", "мм2": "мм2",
    "p": "p", "р": "p",  # poles 3P / 1P+N
}
_UNIT_RE = re.compile(r"(\d)([a-zа-я]{1,3})(?![a-zа-я0-9])")
_DIM_RE = re.compile(r"(?<=\d)\s?[хx×*]\s?(?=\d)")
_DEC_RE = re.compile(r"(?<=\d),(?=\d)")
_JUNK_RE = re.compile(r"[*!\"«»'`]+")
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Normalize free text for indexing/searching: lowercase, ё->е, ²->2, '3х2,5'->'3x2.5', unit folding (16A->16а)."""
    s = (text or "").lower().replace("ё", "е").replace("²", "2").replace("³", "3")
    s = _DEC_RE.sub(".", s)
    s = _DIM_RE.sub("x", s)
    s = _UNIT_RE.sub(lambda m: m.group(1) + _UNIT_FOLD.get(m.group(2), m.group(2)), s)
    s = _JUNK_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


_ARTICLE_RE = re.compile(r"^[a-zа-я0-9_./+\-]+$")


def is_article_like(token: str) -> bool:
    """A token that looks like an article / model code: >=4 chars, contains a digit, no exotic chars."""
    return len(token) >= 4 and any(c.isdigit() for c in token) and bool(_ARTICLE_RE.match(token))


# --------------------------------------------------------------------------- connection handling

_local = threading.local()


def _connect() -> sqlite3.Connection:
    """One read-only connection per thread (sqlite connections are not thread-safe)."""
    con = getattr(_local, "con", None)
    if con is None:
        ensure_db()
        con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con = con
    return con


def ensure_db() -> None:
    """Raise RuntimeError with build instructions if the catalog index is missing."""
    if not config.DB_PATH.exists():
        raise RuntimeError(
            f"Catalog index not found at {config.DB_PATH}. Build it with:\n"
            "  cd backend && uv run python scripts/build_index.py --dump <dir with list_*.json pages>"
        )


def count_products() -> int:
    """Number of indexed SKUs."""
    return int(_connect().execute("SELECT count(*) FROM products").fetchone()[0])


_ROW_COLS = "id, name, article, price, image, url, cat1, cat2, cat3, brand"


def _rows(sql: str, params: tuple | list) -> list[dict[str, Any]]:
    return [dict(r) for r in _connect().execute(sql, params).fetchall()]


def get_product(pid: int) -> dict[str, Any] | None:
    """Catalog row by id: {id,name,article,price,image,url,cat1,cat2,cat3,brand} or None."""
    rows = _rows(f"SELECT {_ROW_COLS} FROM products WHERE id = ?", (int(pid),))
    return rows[0] if rows else None


def get_by_article(article: str) -> list[dict[str, Any]]:
    """Exact article match (case-insensitive, trailing '_' optional), then article prefix."""
    a = normalize_text(article).replace(" ", "")
    if not a:
        return []
    rows = _rows(f"SELECT {_ROW_COLS} FROM products WHERE article_norm IN (?, ?, ?) LIMIT 20", (a, a + "_", a.rstrip("_")))
    if rows:
        return rows
    return _rows(f"SELECT {_ROW_COLS} FROM products WHERE article_norm >= ? AND article_norm < ? LIMIT 20", (a, a + "\uffff"))


# --------------------------------------------------------------------------- search

_FTS_WEIGHTS = "8.0, 4.0, 2.0, 1.0"  # name_norm, article, brand, cat


def _filters(category: str | None, brand: str | None, exclude_id: int | None, alias: str = "p") -> tuple[str, list[Any]]:
    """SQL fragment (starts with ' AND ...') + params for the optional filters."""
    sql, params = "", []
    if brand:
        canon = detect_brand(brand) or brand
        sql += f" AND ({alias}.brand = ? OR {alias}.name_norm LIKE ?)"
        params += [canon, f"%{normalize_text(brand)}%"]
    if category:
        c = category.strip().lower()
        sql += f" AND ({alias}.cat1 = ? OR {alias}.cat2 = ? OR {alias}.cat3 = ? OR {alias}.cat LIKE ?)"
        params += [c, c, c, f"%{c}%"]
    if exclude_id is not None:
        sql += f" AND {alias}.id != ?"
        params.append(int(exclude_id))
    return sql, params


def _fts_term(token: str, prefix: bool) -> str:
    """One FTS5 query term: quoted phrase ('3x2.5' -> "3x2.5" matches tokens 3x2,5 consecutively), optional prefix '*'."""
    quoted = '"' + token.replace('"', '""') + '"'
    return quoted + "*" if prefix else quoted


def _stem(token: str) -> str:
    """Crude Russian stemming by truncation: 'выключатели' -> 'выключател', 'кабель' -> 'кабел', 'щит' -> 'щит'."""
    if len(token) >= 7:
        return token[:-2]
    if len(token) >= 5:
        return token[:-1]
    return token


# Cyrillic -> Latin as Bitrix builds URL slugs ('щиты металлические' -> 'shchity_metallicheskie')
_SLUG_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh",
    "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(word: str) -> str:
    """Transliterate a lower-case Russian word the way category slugs are spelled."""
    return "".join(_SLUG_MAP.get(c, c) for c in word)


# colloquial query words -> how the catalog actually names the thing (OR-ed with the word itself, prefix-matched)
SYNONYMS: dict[str, list[str]] = {
    "дифавтомат": ["диф", "авдт", "дифференциальн"],
    "дифавтоматы": ["диф", "авдт", "дифференциальн"],
    "дифференциальный": ["диф", "авдт"],
    "автомат": ["автоматическ", "ав", "ва"],
    "автоматы": ["автоматическ", "ав", "ва"],
    "автоматический": ["авт", "ав", "ва"],
    "выключатель": ["выкл", "ва"],
    "выключатели": ["выкл", "ва"],
    "лампочка": ["лампа"],
    "лампочки": ["лампа"],
    "щиток": ["щит", "щрн", "щрв"],
    "провод": ["кабель", "пв", "пугв"],
}


def _fts_terms(tokens: list[str]) -> list[str]:
    """FTS5 terms per token: words -> stem prefix (+ synonyms, + category slugs), codes/numbers -> exact phrase."""
    terms = []
    for t in tokens:
        if t.isalpha() and len(t) >= 3:
            stem = _stem(t)
            alts = [_fts_term(stem, True)] + [_fts_term(a, True) for a in SYNONYMS.get(t, ())]
            slug = slugify(stem)
            if slug != stem:
                alts.append("cat:" + _fts_term(slug, True))
            terms.append("(" + " OR ".join(alts) + ")")
        else:
            terms.append(_fts_term(t, False))
    return terms


def _name_hits(name_norm: str, tokens: list[str]) -> int:
    """Weighted count of query tokens found in the normalized name (spec tokens like '16а'/'3p' weigh 3, words 1)."""
    hits = 0
    for t in tokens:
        if t.isalpha():
            hits += any(a in name_norm for a in [_stem(t), *SYNONYMS.get(t, ())])
        else:
            hits += 3 * (t.replace(".", " ").replace("+", " ") in name_norm)
    return hits


def _fts_search(match: str, limit: int, filt: tuple[str, list[Any]]) -> list[dict[str, Any]]:
    fsql, fparams = filt
    sql = (
        f"SELECT {', '.join('p.' + c.strip() for c in _ROW_COLS.split(','))}, p.name_norm, bm25(products_fts, {_FTS_WEIGHTS}) AS rank "
        f"FROM products_fts JOIN products p ON p.id = products_fts.rowid "
        f"WHERE products_fts MATCH ?{fsql} ORDER BY rank LIMIT ?"
    )
    try:
        return _rows(sql, [match, *fparams, limit])
    except sqlite3.OperationalError:  # malformed MATCH expression (should not happen with quoted terms)
        return []


def _tri_search(needles: list[str], limit: int, filt: tuple[str, list[Any]]) -> list[dict[str, Any]]:
    """Substring match via the trigram index: every needle (>=3 chars) must occur in search_text."""
    needles = [n for n in needles if len(n) >= 3]
    if not needles:
        return []
    match = " AND ".join('"' + n.replace('"', '""') + '"' for n in needles)
    fsql, fparams = filt
    sql = (
        f"SELECT {', '.join('p.' + c.strip() for c in _ROW_COLS.split(','))} FROM products_tri "
        f"JOIN products p ON p.id = products_tri.rowid WHERE products_tri MATCH ?{fsql} LIMIT ?"
    )
    try:
        return _rows(sql, [match, *fparams, limit])
    except sqlite3.OperationalError:
        return []


def search(
    query: str,
    limit: int = 10,
    *,
    category: str | None = None,
    brand: str | None = None,
    exclude_id: int | None = None,
) -> list[dict[str, Any]]:
    """Rank catalog rows for a free-text query (article, name fragments, Cyrillic/Latin, dimensions like '3х2,5').

    Order of precedence: exact article -> article prefix -> article-like token as substring of the name ->
    FTS (all tokens; most tokens in the name first, then bm25) -> OR fallback -> trigram substring fallback.
    Returns at most `limit` plain dicts, deduped, without stock information.
    """
    qn = normalize_text(query)
    tokens = qn.split()
    if not tokens:
        return []
    filt = _filters(category, brand, exclude_id)
    fsql, fparams = filt
    out: dict[int, dict[str, Any]] = {}

    def add(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            if r["id"] not in out and len(out) < limit:
                r.pop("name_norm", None)
                r.pop("rank", None)
                out[r["id"]] = r

    # 1) article-like tokens: exact -> prefix -> substring in name/article
    art_tokens = [t for t in tokens if is_article_like(t)]
    for t in art_tokens:
        add(_rows(f"SELECT {_ROW_COLS} FROM products p WHERE p.article_norm IN (?, ?, ?){fsql} LIMIT ?",
                  [t, t + "_", t.rstrip("_"), *fparams, limit]))
    for t in art_tokens:
        if len(out) >= limit:
            break
        add(_rows(f"SELECT {_ROW_COLS} FROM products p WHERE p.article_norm >= ? AND p.article_norm < ?{fsql} LIMIT ?",
                  [t, t + "\uffff", *fparams, limit]))
    if len(art_tokens) == len(tokens):  # pure code query: substring match beats word matching
        for t in art_tokens:
            if len(out) >= limit:
                break
            add(_tri_search([t], limit, filt))

    # 2) FTS: all tokens AND-ed (word stems with prefix + synonyms, also matched against category slugs);
    #    ranked by how many tokens occur in the name itself, then bm25
    if len(out) < limit:
        terms = _fts_terms(tokens)
        rows = _fts_search(" AND ".join(terms), limit * 3, filt)
        if len(rows) < 3 and len(tokens) > 1:
            # relax: keep every spec token ('16а', '3p') mandatory, any of the words is enough; then plain OR
            spec = [term for term, t in zip(terms, tokens) if not t.isalpha()]
            words = [term for term, t in zip(terms, tokens) if t.isalpha()]
            if spec and words:
                rows += _fts_search(" AND ".join([*spec, "(" + " OR ".join(words) + ")"]), limit * 3, filt)
            if len(rows) < 3:
                rows += _fts_search(" OR ".join(terms), limit * 3, filt)
        rows.sort(key=lambda r: (-_name_hits(r["name_norm"], tokens), r["rank"]))
        add(rows)

    # 3) trigram substrings (partial codes, 'нг-ls', typos inside a known fragment)
    if len(out) < max(3, limit // 2):
        add(_tri_search(tokens, limit, filt))
    return list(out.values())


def same_category_products(product: dict[str, Any], limit: int = 80, exclude_id: int | None = None) -> list[dict[str, Any]]:
    """Products from the same leaf category (cat3), topped up with the same cat2; closest price first."""
    price = float(product.get("price") or 0)
    out: dict[int, dict[str, Any]] = {}
    for cols in (("cat1", "cat2", "cat3"), ("cat1", "cat2")):
        vals = [product.get(c) for c in cols]
        if not all(vals) or len(out) >= limit:
            continue
        where = " AND ".join(f"{c} = ?" for c in cols)
        params: list[Any] = [*vals]
        if exclude_id is not None:
            where += " AND id != ?"
            params.append(int(exclude_id))
        for r in _rows(f"SELECT {_ROW_COLS} FROM products WHERE {where} ORDER BY abs(coalesce(price,0) - ?) LIMIT ?", [*params, price, limit]):
            if r["id"] not in out and len(out) < limit:
                out[r["id"]] = r
    return list(out.values())


# --------------------------------------------------------------------------- detail helpers

# CML2 property codes seen in the detail API -> Russian labels
PROPERTY_LABELS: dict[str, str] = {
    "NOMINALNYY_TOK": "Номинальный ток",
    "KOLICHESTVO_POLYUSOV": "Количество полюсов",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "TIP_USTANOVKI": "Тип установки",
    "TORGOVAYA_MARKA": "Торговая марка",
    "SECHENIE_MM2": "Сечение, мм²",
    "KOLICHESTVO_ZHIL": "Количество жил",
    "MATERIAL_ZHILY": "Материал жилы",
    "NAPRYAZHENIE": "Напряжение",
    "GOST": "ГОСТ",
    "KRATNOST_MIN": "Кратность (мин. партия)",
    "KRATNOST_MAKS": "Кратность (макс.)",
    "ARTIKULPOSTAVSHCHIKA": "Артикул производителя",
    "CML2_BAR_CODE": "Штрихкод",
    "OBYEM": "Тип изделия",
    "SPOSOB_MONTAZHA": "Способ монтажа",
    "NAZNACHENIE": "Назначение",
    "STEPEN_ZASHCHITY": "Степень защиты (IP)",
    "MOSHCHNOST": "Мощность",
    "TSVETOVAYA_TEMPERATURA": "Цветовая температура",
    "SVETOVOY_POTOK": "Световой поток",
    "GIBKOST": "Гибкость",
    "GLUBINA": "Глубина",
    "TOLSHCHINA": "Толщина",
    "VYSOTA_UPAKOVKI_MM": "Высота упаковки, мм",
    "METRAZHNYY_TOVAR": "Метражный товар",
    "VYKHODNOE_NAPRYAZHENIE_": "Выходное напряжение",
    "MATERIAL_IZOLYATSII_I_OBOLOCHKI": "Материал изоляции и оболочки",
    "NALICHIE_METALLICHESKOY_BRONI": "Наличие металлической брони",
    "VVOD_KABELYA": "Ввод кабеля",
    "KATEGORIYA_SHCHITOVOGO_OBORUDOVANIYA": "Категория щитового оборудования",
    "KOLICHESTVO_VVODNYKH_MODULEY": "Количество вводных модулей",
    "KOLICHESTVO_MONTAZHNYKH_MODULEY": "Количество монтажных модулей",
    "KOLICHESTVO_RYADOV": "Количество рядов",
    "TSVET": "Цвет",
    "MATERIAL": "Материал",
    "SERIYA": "Серия",
    "STRANA_PROIZVODITEL": "Страна-производитель",
    "GARANTIYA": "Гарантия",
    "VES": "Вес",
    "DLINA": "Длина",
    "SHIRINA": "Ширина",
    "VYSOTA": "Высота",
    "TIP_LAMPY": "Тип лампы",
    "TSOKOL": "Цоколь",
    "CHASTOTA": "Частота",
    "KLASS_ZASHCHITY": "Класс защиты",
    "KHARAKTERISTIKA_SRABATYVANIYA": "Характеристика срабатывания",
    "TOK_UTECHKI": "Ток утечки",
    "NOVINKA": "Новинка",
    "SPETSPREDLOZHENIE": "Спецпредложение",
}
# internal/marketing keys that are never shown to the user
SERVICE_KEYS: frozenset[str] = frozenset({
    "BRAND_PRIORITY", "CML2_ARTICLE", "CML2_TRAITS", "CML2_TAXES", "IMYAKARTINKI", "RECOMMEND",
    "POKAZYVAT_TSENY", "KOLICHESTVOVREZERVE", "KRATNOST_MAKS_1", "WB_EXPORT", "insta", "wbb",
})
# reverse transliteration for unknown codes (longest digraphs first)
_TRANSLIT = [
    ("SHCH", "щ"), ("YA", "я"), ("YU", "ю"), ("YO", "ё"), ("ZH", "ж"), ("KH", "х"), ("TS", "ц"), ("CH", "ч"), ("SH", "ш"),
    ("A", "а"), ("B", "б"), ("V", "в"), ("G", "г"), ("D", "д"), ("E", "е"), ("Z", "з"), ("I", "и"), ("Y", "й"), ("K", "к"),
    ("L", "л"), ("M", "м"), ("N", "н"), ("O", "о"), ("P", "п"), ("R", "р"), ("S", "с"), ("T", "т"), ("U", "у"), ("F", "ф"),
]


def _label_from_code(code: str) -> str:
    """Fallback label: 'VYKHODNOE_NAPRYAZHENIE_' -> 'Выходное напряжение' (best-effort reverse transliteration)."""
    words = []
    for w in code.strip("_").split("_"):
        s, i = "", 0
        while i < len(w):
            if w[i] == "Y" and not w.startswith(("YA", "YU", "YO"), i):
                # 'Y' is 'й' before a vowel, after a vowel or after another Y (NYY -> ный); otherwise 'ы' (VYKH -> вых)
                nxt, prev = w[i + 1 : i + 2], w[i - 1 : i] if i else ""
                s += "й" if (nxt in "AEIOU" and nxt) or (prev in "AEIOUY" and prev) else "ы"
                i += 1
                continue
            for lat, cyr in _TRANSLIT:
                if w.startswith(lat, i):
                    s += cyr
                    i += len(lat)
                    break
            else:
                s += w[i].lower()
                i += 1
        words.append(s)
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def humanize_properties(props: dict[str, Any]) -> dict[str, str]:
    """CML2 code -> Russian label; service keys dropped, unknown codes labelled best-effort, empty values skipped."""
    out: dict[str, str] = {}
    for code, val in (props or {}).items():
        if code in SERVICE_KEYS:
            continue
        if isinstance(val, list):
            val = ", ".join(str(v).strip() for v in val if str(v).strip())
        val = str(val or "").strip()
        if not val or (code in ("NOVINKA", "SPETSPREDLOZHENIE") and val.lower() == "нет"):
            continue
        out[PROPERTY_LABELS.get(code) or _label_from_code(code)] = val
    return out


def kratnost(detail: dict[str, Any] | None) -> int:
    """Pack multiplicity (KRATNOST_MIN); orders must be multiples of it. Defaults to 1."""
    raw = str(((detail or {}).get("properties") or {}).get("KRATNOST_MIN") or "").replace(",", ".").strip()
    try:
        return max(1, int(float(raw)))
    except ValueError:
        return 1


def stock_status(detail: dict[str, Any] | None) -> str:
    """'in_stock' | 'out_of_stock' | 'unknown' (detail API unavailable)."""
    if not detail:
        return "unknown"
    return "in_stock" if ekt_api.total_quantity(detail) > 0 else "out_of_stock"


def brand_of(product: dict[str, Any], detail: dict[str, Any] | None = None) -> str | None:
    """Brand from the catalog row, else detail TORGOVAYA_MARKA, else detected from the name."""
    props = (detail or {}).get("properties") or {}
    return product.get("brand") or str(props.get("TORGOVAYA_MARKA") or "").strip() or detect_brand(product.get("name") or "")


def _clean_description(text: str | None) -> str:
    return _WS_RE.sub(" ", (text or "").replace("\r", " ").replace("\t", " ")).strip()


async def product_card(product: dict[str, Any], detail: dict[str, Any] | None = None, *, with_detail: bool = True) -> dict[str, Any]:
    """Card shape from API_CONTRACT (stock, stores, humanized properties). `product` may be a bare {'id': N}.

    Args:
        product: catalog row (or at least {'id'}); missing fields are taken from `detail`.
        detail: live detail dict; fetched via ekt_api.fetch_detail when None and with_detail=True.
        with_detail: set False to build a stock-less card without any network call.
    """
    pid = int(product["id"])
    if detail is None and with_detail:
        detail = await ekt_api.fetch_detail(pid)
    d = detail or {}
    props = d.get("properties") or {}
    status = stock_status(detail)
    quantity = ekt_api.total_quantity(d) if detail else None
    price = d.get("price") if d.get("price") is not None else product.get("price")
    return {
        "id": pid,
        "name": product.get("name") or d.get("name") or "",
        "article": product.get("article") or d.get("article") or "",
        "price": price,
        "quantity": quantity,
        "in_stock": status == "in_stock",
        "stock_status": status,
        "url": product.get("url") or d.get("url") or "",
        "image": product.get("image") or d.get("image"),
        "brand": brand_of(product, detail),
        "stores": [{"name": s.get("name"), "quantity": int(s.get("quantity") or 0)} for s in ekt_api.stores_in_stock(d)] if detail else [],
        "properties": humanize_properties(props),
        "kratnost": kratnost(detail),
        "description": _clean_description(d.get("description")),
        "reason": None,
        "certificates": [],
    }


async def product_cards(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cards for many rows with details fetched concurrently (order preserved)."""
    details = await ekt_api.fetch_details([int(p["id"]) for p in products])
    return [await product_card(p, details.get(int(p["id"])), with_detail=False) for p in products]
