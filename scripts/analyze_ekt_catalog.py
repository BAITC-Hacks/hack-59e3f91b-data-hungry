#!/usr/bin/env python3
"""Analyze a locally crawled EKT catalog and write a reproducible Markdown report."""

from __future__ import annotations

import argparse
import collections
import datetime
import html
import json
import re
import sqlite3
import statistics
from pathlib import Path
from urllib.parse import urlparse


def clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split())


def percentage(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator * 100:.1f}%" if denominator else "—"


def safe_median(values: list[int]) -> str:
    return str(round(statistics.median(values))) if values else "—"


def safe_percentile(values: list[int], percentile: float) -> str:
    if not values:
        return "—"
    ordered = sorted(values)
    return str(ordered[round((len(ordered) - 1) * percentile)])


def catalog_category(url: object) -> tuple[str, str]:
    if not isinstance(url, str):
        return ("unknown", "unknown")
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts or parts[0] != "catalog":
        return ("unknown", "unknown")
    return (parts[1] if len(parts) > 1 else "unknown", parts[2] if len(parts) > 2 else "unknown")


def analyze(db_path: Path) -> str:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    page_rows = db.execute("SELECT page, item_count FROM pages ORDER BY page").fetchall()
    total = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    detail_total = 0
    detail_errors = 0
    descriptions = 0
    description_lengths: list[int] = []
    description_values = collections.Counter()
    name_lengths: list[int] = []
    names = collections.Counter()
    articles = collections.Counter()
    supplier_articles = collections.Counter()
    barcodes = collections.Counter()
    property_coverage = collections.Counter()
    detail_field_coverage = collections.Counter()
    list_field_coverage = collections.Counter()
    categories = collections.Counter()
    subcategories = collections.Counter()
    with_image = with_price = positive_stock = offers = detail_offers = 0
    zero_price = zero_stock = store_stock_mismatch = 0
    bad_list_rows = 0
    for raw_list, raw_detail, error in db.execute(
        "SELECT list_json, detail_json, detail_error FROM products ORDER BY id"
    ):
        if error:
            detail_errors += 1
        try:
            listing = json.loads(raw_list)
        except (TypeError, ValueError):
            bad_list_rows += 1
            continue
        for key, value in listing.items():
            if value is not None and value != "" and value != []:
                list_field_coverage[key] += 1
        name = clean_text(listing.get("name"))
        if name:
            names[name.casefold()] += 1
            name_lengths.append(len(name))
        article = clean_text(listing.get("article"))
        if article:
            articles[article.casefold()] += 1
        if listing.get("image"):
            with_image += 1
        if isinstance(listing.get("price"), (int, float)):
            with_price += 1
            if listing["price"] == 0:
                zero_price += 1
        if listing.get("offers"):
            offers += 1
        category, subcategory = catalog_category(listing.get("url"))
        categories[category] += 1
        subcategories[(category, subcategory)] += 1
        if not raw_detail:
            continue
        detail_total += 1
        detail = json.loads(raw_detail)
        if detail.get("offers"):
            detail_offers += 1
        for key, value in detail.items():
            if value is not None and value != "" and value != [] and value != {}:
                detail_field_coverage[key] += 1
        description = clean_text(detail.get("description"))
        if description:
            descriptions += 1
            description_lengths.append(len(description))
            description_values[description.casefold()] += 1
        properties = detail.get("properties")
        if isinstance(properties, dict):
            for key, value in properties.items():
                if value is not None and value != "" and value != []:
                    property_coverage[key] += 1
            supplier_article = clean_text(properties.get("ARTIKULPOSTAVSHCHIKA"))
            if supplier_article:
                supplier_articles[supplier_article.casefold()] += 1
            barcode = clean_text(properties.get("CML2_BAR_CODE"))
            if barcode:
                barcodes[barcode.casefold()] += 1
        stock = detail.get("quantity")
        if isinstance(stock, (int, float)):
            if stock > 0:
                positive_stock += 1
            else:
                zero_stock += 1
            stores = detail.get("stores")
            if isinstance(stores, list):
                store_sum = sum(
                    store.get("quantity", 0)
                    for store in stores
                    if isinstance(store, dict) and isinstance(store.get("quantity"), (int, float))
                )
                if store_sum != stock:
                    store_stock_mismatch += 1
    db.close()
    max_page_size = max((count for _, count in page_rows), default=0)
    complete = bool(page_rows) and page_rows[0][0] == 1 and all(
        page == i for i, (page, _) in enumerate(page_rows, start=1)
    ) and page_rows[-1][1] < max_page_size
    lines = [
        "# Аудит каталога ekt.kz для поиска",
        "",
        f"Дата формирования: {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "Источник: Basic Auth API `/api/products` и `/api/products/detail?id=…`. Данные получены скриптом `scripts/ekt_catalog_audit.py`; учётные данные в репозитории не хранятся.",
        "",
        "## Полнота выгрузки",
        "",
        f"- Страниц списка: {len(page_rows)}; последняя страница: {page_rows[-1][0] if page_rows else '—'}, товаров на ней: {page_rows[-1][1] if page_rows else '—'}.",
        f"- Уникальных товаров: **{total:,}**; деталей получено: **{detail_total:,}** ({percentage(detail_total, total)}); ошибок деталей: {detail_errors:,}.",
        f"- Статус списка: **{'полный по признаку последней неполной страницы' if complete else 'не подтверждён как полный'}**.",
        f"- Повреждённых строк списка: {bad_list_rows}.",
        "- API не вернул общее число товаров или версию каталога; это результат последовательного прохода страниц, а не атомарный снимок.",
        "",
        "## Покрытие данных",
        "",
        "| Поле | Заполнено | Доля |",
        "|---|---:|---:|",
    ]
    for label, count, denominator in [
        ("Название", list_field_coverage["name"], total),
        ("Артикул", list_field_coverage["article"], total),
        ("Цена", with_price, total),
        ("Изображение", with_image, total),
        ("URL карточки", list_field_coverage["url"], total),
        ("Непустое описание", descriptions, detail_total),
        ("Свойства", detail_field_coverage["properties"], detail_total),
        ("Остаток", detail_field_coverage["quantity"], detail_total),
        ("Склады", detail_field_coverage["stores"], detail_total),
        ("Непустые offers в списке", offers, total),
        ("Непустые offers в деталях", detail_offers, detail_total),
    ]:
        lines.append(f"| {label} | {count:,} | {percentage(count, denominator)} |")
    lines.extend(
        [
            "",
            f"Медианная длина названия: {safe_median(name_lengths)} символов; описания: {safe_median(description_lengths)} символов.",
            f"Длина описания: p95={safe_percentile(description_lengths, 0.95)}, максимум={max(description_lengths) if description_lengths else '—'} символов; описаний длиннее 2 000 символов: {sum(n > 2000 for n in description_lengths):,}.",
            f"Уникальных непустых описаний: {len(description_values):,} из {descriptions:,}; повторяющихся названий: {sum(n-1 for n in names.values() if n > 1):,}; повторяющихся артикулов: {sum(n-1 for n in articles.values() if n > 1):,}.",
            f"Артикулов поставщика: {sum(supplier_articles.values()):,} значений / {len(supplier_articles):,} уникальных; штрихкодов: {sum(barcodes.values()):,} значений / {len(barcodes):,} уникальных.",
            f"Различных кодов полей в `properties`: {len(property_coverage):,}; описаний сверх первого экземпляра одинакового текста: {descriptions - len(description_values):,}.",
            f"Нулевая цена: {zero_price:,}; остаток > 0: {positive_stock:,}; остаток = 0: {zero_stock:,}; несовпадение `quantity` с суммой `stores`: {store_stock_mismatch:,} из {detail_total:,} деталей.",
            "",
            "## Свойства в деталях",
            "",
            "| Код поля | Товаров | Доля от загруженных деталей |",
            "|---|---:|---:|",
        ]
    )
    for key, count in property_coverage.most_common(45):
        lines.append(f"| `{key}` | {count:,} | {percentage(count, detail_total)} |")
    lines.extend(["", "## Крупнейшие разделы по URL", "", "| Раздел | Товаров |", "|---|---:|"])
    for category, count in categories.most_common(20):
        lines.append(f"| `{category}` | {count:,} |")
    lines.extend(
        [
            "",
            "## Особенности качества данных",
            "",
            "- Одинаковые описания встречаются у разных модификаций: сохранять в эмбеддинге название и отличающие параметры, а не одно описание.",
            "- Нулевая цена не означает бесплатный товар; для ответа покупателю проверять актуальную карточку.",
            "- `quantity` иногда отличается от суммы `stores`; не выводить общую доступность как сумму складов без проверки.",
            "- Перед индексированием декодировать HTML entities, убирать лишние пробелы и приводить варианты единиц вроде `16 А`/`16А` к общей форме. Исходные значения сохранять для показа пользователю.",
            "- Числовые характеристики в названии, описании и `properties` следует сверять. Например, у `id=515291` название содержит `160А`, а `NOMINALNYY_TOK` — `250 А`; это может отражать разные номиналы серии и конкретной модификации. Не подставлять одно значение в ответ без проверки контекста карточки.",
            "- Для метражного товара количество может означать метры; проверять `METRAZHNYY_TOVAR` и не добавлять единицу «шт.» автоматически.",
        ]
    )
    lines.extend(
        [
            "",
            "## Что индексировать",
            "",
            "| Источник | Точный / частичный поиск | Полнотекстовый поиск | Эмбеддинг | Фильтр / актуальная выдача |",
            "|---|---|---|---|---|",
            "| `id`, `article`, `CML2_BAR_CODE`, `ARTIKULPOSTAVSHCHIKA` | Да; префикс и подсказки при опечатках, без автоматической подмены кода | Нет | Нет | ID карточки |",
            "| `name` | Префикс, подстрока, опечатки | Да, высокий вес | Да | — |",
            "| `description` | — | Да | Да, если не пустое | — |",
            "| `TORGOVAYA_MARKA`, тип, назначение, материал | Да | Да | Выбранные человекочитаемые поля | Фасеты |",
            "| Электрические и размерные характеристики | Точные токены с нормализацией единиц | Да | Краткая запись ключевых характеристик | Числовые/категориальные фильтры |",
            "| `price`, `quantity`, `stores` | — | Нет | Нет | Да; обновлять отдельно |",
            "| `image` | — | Нет | Только отдельный визуальный индекс | Показ в карточке |",
            "",
            "1. **Точное совпадение:** `id`, `article`, штрихкод и артикул поставщика из `properties`. Нормализовать пробелы, регистр, дефисы и подчёркивания; точное совпадение артикула должно быть выше любого векторного результата.",
            "2. **Полнотекстовый и частичный поиск:** `name`, `description`, названия брендов и категорий, осмысленные текстовые характеристики. Индексировать отдельные токены и префиксы; для опечаток применять trigram/fuzzy. Числовые спецификации вроде `16А`, `30мА`, `400В`, `IP20` сохранять как фильтры и поисковые токены.",
            "3. **Эмбеддинги:** один документ на товар из `name` + непустого `description` + выбранных человекочитаемых свойств и предметной категории. Маркетинговые разделы URL (`novinki`, `spets_predlozhenie`, `arkhiv`) не описывают тип товара, поэтому не включать их как категорию в текст эмбеддинга. Эмбеддинги помогут запросам по задаче/синонимам («защита от перегрузки», «автомат на 16 ампер»), но не заменят точный поиск по артикулу.",
            "   Описания семейства могут быть одинаковыми у разных модификаций, поэтому `name` и различающие характеристики обязательно включать в текст для эмбеддинга.",
            "4. **Фильтры и свежие поля:** `price`, общий `quantity`, остатки по `stores`, флаги акций, новизны и структурные параметры хранить отдельно. Цену и остаток не включать в эмбеддинг: они меняются, и вектор будет устаревать.",
            "5. **Изображения:** `image` — URL, а не содержимое картинки. Визуальные эмбеддинги возможны только после отдельной загрузки изображений и проверки лицензии/качества; для первой версии достаточно текстового поиска.",
            "6. **Служебные поля:** не включать в текст эмбеддинга `CML2_TRAITS`, `CML2_TAXES`, `BRAND_PRIORITY`, `IMYAKARTINKI`, `RECOMMEND` и флаги акций. Их смысл отличается от описания товара и может ухудшать семантический поиск.",
            "",
            "## Рекомендуемый маршрут запроса",
            "",
            "`нормализация запроса → точные артикулы/штрихкоды → BM25/FTS + fuzzy + векторный поиск → объединение кандидатов → фильтры цены/наличия → карточки с актуальными ценой и остатками`.",
            "",
            "При неполной выгрузке деталей выводы о покрытиях `description`, `properties` и `stores` относятся только к загруженной части.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/ekt_catalog.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("reports/ekt_catalog_audit.md"))
    args = parser.parse_args()
    report = analyze(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
