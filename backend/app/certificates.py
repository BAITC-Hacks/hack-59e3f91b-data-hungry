"""DEMO certificate registry: which conformity documents exist for a product (by brand x product family).

The real ekt.kz API returns no certificates at all, so ``data/certificates.json`` is a clearly marked
demo registry (``demo: true`` on every entry, synthetic numbers). It lets the assistant demonstrate the
"give me the certificate for this article" flow; the README discloses this.

Matching is heuristic: the brand comes from the catalog row (``product['brand']``), the detail's
``TORGOVAYA_MARKA`` property, the product name or the category slug; the family (cable, breaker,
lighting, panel, cable_mgmt, socket, terminal) comes from name/category keywords. Unknown brand or
family -> no certificates (the assistant then offers documents on request via a manager).
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache
from typing import Any

from . import config
from .brands import detect_brand

REGISTRY_PATH = config.DATA_DIR / "certificates.json"

#: extra name aliases the generic brand detector does not know (lowercase, whole-word)
_EXTRA_NAME_ALIASES: dict[str, str] = {
    "schnel": "Schneider Electric",
    "schel": "Schneider Electric",
    "se": "Schneider Electric",
    "dek": "DEKraft",
    "generica": "IEK",
    "itk": "IEK",
    "ект": "EKT",
    "ledvance": "Osram",
}

#: category-slug words (from the product url, matched as whole ``_``-separated words) -> brand
_CAT_BRAND_HINTS: list[tuple[str, str]] = [
    ("legrand", "Legrand"),
    ("schneider", "Schneider Electric"),
    ("easy9", "Schneider Electric"),
    ("unica", "Schneider Electric"),
    ("resi9", "Schneider Electric"),
    ("acti9", "Schneider Electric"),
    ("zelio", "Schneider Electric"),
    ("tesys", "Schneider Electric"),
    ("se", "Schneider Electric"),
    ("systeme", "Systeme Electric"),
    ("atlas", "Systeme Electric"),
    ("artgallery", "Systeme Electric"),
    ("dekraft", "DEKraft"),
    ("megalight", "MEGALIGHT"),
    ("generica", "IEK"),
    ("iek", "IEK"),
    ("ekf", "EKF"),
    ("keaz", "KEAZ"),
    ("chint", "Chint"),
    ("abb", "ABB"),
    ("wago", "WAGO"),
    ("dkc", "DKC"),
    ("tdm", "TDM"),
    ("navigator", "Navigator"),
    ("jazzway", "Jazzway"),
    ("osram", "Osram"),
    ("ledvance", "Osram"),
    ("philips", "Philips"),
    ("unit", "UNIT"),
    ("ekt", "EKT"),
]

#: family detection in priority order: (family, category-slug fragments, name keywords)
_FAMILY_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("socket", ("udlinitel", "setevye_filtry"), ("удлинител", "сетевой фильтр", "колодка")),  # before "кабель"
    (
        "cable_mgmt",
        ("kabelenesushch", "kabel_kanal", "kabel-kanal", "lotk", "lotok", "trub", "gofr", "metallorukav"),
        ("кабель-канал", "кабельканал", "кабель канал", "лоток", "лотк", "труба", "трубы", "труб ", "гофр", "металлорукав", "короб "),
    ),
    (
        "cable",
        ("kabel_provod", "kabel_silov", "provod", "vitaya_para", "kabel_s"),
        ("кабель", "провод", "ввг", "авбшв", "вббшв", "аввг", "пввг", "пугв", "пугнп", "пвс", "шввп", "nym", "сип-", "сип ",
         "витая пара", "utp", "ftp", "кг ", "кгтп", "ввгнг", "асввг", "пв1", "пв3", "пв-"),
    ),
    (
        "breaker",
        ("avtomat", "uzo", "avdt", "differ", "kontaktor", "puskatel", "rubilnik", "nizkovoltnaya_apparatura",
         "predokhranitel", "rele", "vyklyuchateli_nagruzki", "silovye_avtomat"),
        ("автомат", "авт.", "авт ", "авдт", "узо", "диф.", "дифф", "диф ", "дифавт", "контактор", "пускател", "рубильник", "ав ", "ва-",
         "ва47", "ва 47", "выключатель нагрузки", "выключ.нагрузки", "выкл. нагрузки", "выкл.нагрузки", "предохранител", "реле",
         "авр", "разъединител", "модульн"),
    ),
    (
        "lighting",
        ("svetiln", "lamp", "svetodiod", "prozhektor", "led_", "osveshch"),
        ("светильник", "лампа", "лампы", "led", "прожектор", "светодиод", "люстра", "спот", "бра ", "лента светодиодная", "дку", "дпо", "дсп", "дво", "дба"),
    ),
    (
        "panel",
        ("shkafy_shchity", "shchit", "shkaf", "boks", "korpus"),
        ("щит", "щрн", "щру", "щрв", "щмп", "шкаф", "бокс", "корпус", "щиток", "кмпн", "ящик"),
    ),
    (
        "socket",
        ("rozetk", "vyklyuchatel", "dimmer", "ramk", "mekhanizm", "elektroustanov"),
        ("розетк", "роз.", "выключател", "рамка", "рамки", "диммер", "клавиш", "механизм", "переключател", "терморегулятор"),
    ),
    (
        "terminal",
        ("klemm", "zazhim", "soedinitel", "nakonechnik"),
        ("клемм", "зажим", "соединител", "наконечник", "гильза", "wago"),
    ),
]

_FAMILY_LABELS: dict[str, str] = {
    "cable": "кабельно-проводниковая продукция",
    "breaker": "низковольтная аппаратура",
    "lighting": "светотехника",
    "panel": "щитовое оборудование",
    "cable_mgmt": "кабеленесущие системы",
    "socket": "электроустановочные изделия",
    "terminal": "клеммы и соединители",
}


@lru_cache(maxsize=1)
def _registry() -> dict[str, Any]:
    """Loaded registry: ``{"certs": {id: cert}, "by_key": {(brand, family): [cert...]}}``."""
    if not REGISTRY_PATH.exists():
        return {"certs": {}, "by_key": {}, "note": ""}
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    certs: dict[str, dict[str, Any]] = {}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in data.get("certificates", []):
        c = dict(c)
        c["demo"] = True
        certs[c["id"]] = c
        by_key.setdefault((c["brand"], c["family"]), []).append(c)
    return {"certs": certs, "by_key": by_key, "note": data.get("note", "")}


def _url(cert_id: str) -> str:
    return f"{config.PUBLIC_BASE_URL}/api/certificates/{cert_id}"


def _with_url(cert: dict[str, Any]) -> dict[str, Any]:
    out = dict(cert)
    out["url"] = _url(cert["id"])
    return out


def _name_of(product: dict[str, Any] | None, detail: dict[str, Any] | None) -> str:
    raw = (product or {}).get("name") or (detail or {}).get("name") or ""
    return html.unescape(str(raw))


def _cats_of(product: dict[str, Any] | None, detail: dict[str, Any] | None) -> str:
    """Category slugs joined (from cat1..cat3 columns, else parsed from the product url)."""
    p = product or {}
    cats = [p.get("cat1"), p.get("cat2"), p.get("cat3")]
    if not any(cats):
        url = p.get("url") or (detail or {}).get("url") or ""
        parts = [x for x in url.split("/catalog/")[-1].split("/") if x]
        cats = parts[:-1]
    return " ".join(str(c) for c in cats if c).lower()


def resolve_brand(product: dict[str, Any] | None, detail: dict[str, Any] | None = None) -> str | None:
    """Canonical brand for a product (catalog column, TORGOVAYA_MARKA, name aliases, category slug)."""
    p = product or {}
    if p.get("brand"):
        return str(p["brand"])
    props = (detail or {}).get("properties") or {}
    tm = str(props.get("TORGOVAYA_MARKA") or "").strip()
    if tm:
        return detect_brand(tm) or tm
    name = _name_of(product, detail)
    brand = detect_brand(name)
    if brand:
        return brand
    low = name.lower()
    for alias, canon in _EXTRA_NAME_ALIASES.items():
        if re.search(r"(?<![a-zа-яё0-9])" + re.escape(alias) + r"(?![a-zа-яё0-9])", low):
            return canon
    cat_words = set(re.split(r"[_\-\s/]+", _cats_of(product, detail)))
    for word, canon in _CAT_BRAND_HINTS:
        if word in cat_words:
            return canon
    # the company's own cable is listed as "... ГОСТ" without a brand word
    if "гост" in low and detect_family(product, detail) == "cable":
        return "EKT"
    return None


def detect_family(product: dict[str, Any] | None, detail: dict[str, Any] | None = None) -> str | None:
    """Product family key (``cable``, ``breaker``, ...) from category slugs and name keywords."""
    cats = _cats_of(product, detail)
    name = " " + _name_of(product, detail).lower() + " "
    props = (detail or {}).get("properties") or {}
    kind = str(props.get("OBYEM") or "").lower()  # the API stores the product kind under this odd code
    for fam, cat_keys, name_keys in _FAMILY_RULES:
        if any(k in cats for k in cat_keys):
            return fam
    for fam, _cat_keys, name_keys in _FAMILY_RULES:
        if any(k in name for k in name_keys) or (kind and any(k in kind for k in name_keys)):
            return fam
    return None


def certificates_for(product: dict[str, Any], detail: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Demo certificates for a product.

    Args:
        product: catalog row / card (``name``, ``brand``, ``cat1..3`` or ``url`` are used).
        detail: optional live detail (``properties.TORGOVAYA_MARKA``, ``OBYEM`` refine matching).

    Returns:
        ``[{id,title,type,number,issued,valid_until,url,scope,brand,family,demo:True}, ...]``; empty when the
        brand or family is unknown.
    """
    brand = resolve_brand(product, detail)
    family = detect_family(product, detail)
    if not brand or not family:
        return []
    return [_with_url(c) for c in _registry()["by_key"].get((brand, family), [])]


def get_certificate(cert_id: str) -> dict[str, Any] | None:
    """One registry entry (with ``url``) or ``None``."""
    cert = _registry()["certs"].get(cert_id)
    return _with_url(cert) if cert else None


def registry_note() -> str:
    """The disclosure text shipped with the registry (demo disclaimer)."""
    return str(_registry()["note"])


def render_certificate_html(cert_id: str, product: dict[str, Any] | None = None) -> str:
    """Printable HTML "certificate card" with a DEMO watermark (served by ``GET /api/certificates/{id}``)."""
    cert = get_certificate(cert_id)
    e = html.escape
    if not cert:
        return "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Не найдено</title><body style='font-family:sans-serif;padding:40px'><h1>Сертификат не найден</h1><p>Идентификатор " + e(cert_id) + " отсутствует в демо-реестре.</p></body></html>"
    prod_rows = ""
    if product:
        prod_rows = (
            f"<tr><th>Товар</th><td>{e(str(product.get('name') or ''))}</td></tr>"
            f"<tr><th>Артикул</th><td>{e(str(product.get('article') or ''))}</td></tr>"
        )
        if product.get("url"):
            prod_rows += f"<tr><th>Карточка</th><td><a href='{e(str(product['url']))}'>{e(str(product['url']))}</a></td></tr>"
    family_label = _FAMILY_LABELS.get(cert["family"], cert["family"])
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(cert['title'])} — {e(cert['brand'])}</title>
<style>
  body {{ font-family: Georgia, 'Times New Roman', serif; background:#f3f4f6; margin:0; padding:24px; color:#111827; }}
  .card {{ position:relative; max-width:820px; margin:0 auto; background:#fff; border:6px double #1d4ed8; padding:40px 48px; overflow:hidden; }}
  .wm {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; pointer-events:none; }}
  .wm span {{ font: bold 96px/1 Arial, sans-serif; color: rgba(220,38,38,.13); transform: rotate(-28deg); letter-spacing:.2em; white-space:nowrap; }}
  h1 {{ text-align:center; font-size:26px; margin:0 0 4px; letter-spacing:.04em; }}
  .sub {{ text-align:center; color:#374151; margin-bottom:28px; }}
  .num {{ text-align:center; font-size:20px; font-weight:bold; margin-bottom:24px; }}
  table {{ width:100%; border-collapse:collapse; font-size:15px; }}
  th {{ text-align:left; width:210px; padding:8px 10px; color:#374151; font-weight:normal; vertical-align:top; }}
  td {{ padding:8px 10px; border-bottom:1px solid #e5e7eb; }}
  .demo {{ margin-top:28px; padding:12px 14px; background:#fef2f2; border:1px solid #fca5a5; color:#991b1b; font: 13px/1.45 Arial, sans-serif; }}
  .foot {{ margin-top:24px; font: 12px Arial, sans-serif; color:#6b7280; text-align:center; }}
  @media print {{ body {{ background:#fff; padding:0; }} .card {{ border-width:4px; }} }}
</style></head>
<body><div class="card">
  <div class="wm"><span>ДЕМО · DEMO</span></div>
  <h1>{e(cert['title'])}</h1>
  <div class="sub">{e(cert['type'])} · {e(family_label)}</div>
  <div class="num">№ {e(cert['number'])}</div>
  <table>
    <tr><th>Изготовитель / торговая марка</th><td>{e(cert['brand'])}</td></tr>
    <tr><th>Область распространения</th><td>{e(cert['scope'])}</td></tr>
    <tr><th>Орган по сертификации</th><td>{e(cert.get('issuer') or '—')}</td></tr>
    <tr><th>Дата выдачи</th><td>{e(cert['issued'])}</td></tr>
    <tr><th>Действителен до</th><td>{e(cert['valid_until'])}</td></tr>
    {prod_rows}
  </table>
  <div class="demo"><b>Демонстрационный документ.</b> {e(registry_note())}</div>
  <div class="foot">ekt.kz · AI-ассистент · идентификатор {e(cert['id'])}</div>
</div></body></html>"""
