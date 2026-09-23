"""Brand detection from product names (the list API has no brand field; detail has TORGOVAYA_MARKA sometimes)."""
from __future__ import annotations

import re

# canonical brand -> aliases (lowercase, matched as whole words in the product name)
BRANDS: dict[str, list[str]] = {
    "Legrand": ["legrand", "легранд"],
    "Schneider Electric": ["schneider", "шнайдер", "шнейдер", "schneider electric"],
    "Systeme Electric": ["systeme electric", "systeme"],
    "IEK": ["iek", "иэк", "iek group"],
    "EKF": ["ekf"],
    "KEAZ": ["keaz", "кэаз"],
    "DEKraft": ["dekraft", "декрафт"],
    "Chint": ["chint", "чинт"],
    "ABB": ["abb", "абб"],
    "Siemens": ["siemens"],
    "Eaton": ["eaton"],
    "Hager": ["hager"],
    "MEGALIGHT": ["megalight", "мегалайт"],
    "EKT": ["ekt", "экт", "электрокомплект"],
    "UNIT": ["unit", "юнит"],
    "TDM": ["tdm", "tdm electric"],
    "WAGO": ["wago"],
    "DKC": ["dkc", "дкс"],
    "Osram": ["osram", "ledvance"],
    "Philips": ["philips"],
    "Navigator": ["navigator", "навигатор"],
    "Jazzway": ["jazzway"],
    "Gauss": ["gauss"],
    "Uniel": ["uniel"],
    "Feron": ["feron"],
    "Camelion": ["camelion"],
    "ЭРА": ["эра", "era"],
    "JUNG": ["jung"],
    "Lezard": ["lezard"],
    "Makel": ["makel"],
    "Viko": ["viko"],
    "Optimus": ["optimus"],
    "Hikvision": ["hikvision"],
    "Dahua": ["dahua"],
    "Fluke": ["fluke"],
    "Knipex": ["knipex"],
    "Haupa": ["haupa"],
    "Weidmuller": ["weidmuller", "weidmüller"],
    "Phoenix Contact": ["phoenix contact", "phoenix"],
    "Finder": ["finder"],
    "Omron": ["omron"],
    "Delta": ["delta"],
    "LS Electric": ["ls electric", "lsis"],
    "Mitsubishi": ["mitsubishi"],
    "Danfoss": ["danfoss"],
    "Hyundai": ["hyundai"],
    "Sassin": ["sassin"],
    "Andeli": ["andeli"],
    "Elvert": ["elvert"],
    "Simon": ["simon"],
    "Werkel": ["werkel"],
    "Kopos": ["kopos"],
    "Ostec": ["ostec"],
    "СКАТ": ["скат"],
    "ТЕХЭНЕРГО": ["техэнерго"],
}

_ALIAS_RE: list[tuple[str, re.Pattern[str]]] = []
for canon, aliases in BRANDS.items():
    for a in sorted(aliases, key=len, reverse=True):
        _ALIAS_RE.append((canon, re.compile(r"(?<![a-zа-яё0-9])" + re.escape(a) + r"(?![a-zа-яё0-9])", re.I)))
# longest aliases first so "schneider electric" wins over "schneider"
_ALIAS_RE.sort(key=lambda x: -len(x[1].pattern))


def detect_brand(name: str) -> str | None:
    n = name.lower()
    for canon, rx in _ALIAS_RE:
        if rx.search(n):
            return canon
    return None
