"""Matching the Cash Shop's items (OSMS) to the KB by name, and their KB Hebrew: no Qt, so the nightly's
tools/translate_kb.py (which runs without the app's UI libraries) can use the same rules as the Shop page."""
from __future__ import annotations

import re

from . import itemdetails, translations


def fold(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def kb_names(kb) -> dict[str, list[str]]:
    """Folded item name -> every KB item of that name, Cash ones first (a name can be two items: a male and a female
    version, a Cash and a game one)."""
    seen: dict[str, list[str]] = {}
    for k, e in (getattr(kb, "entities", None) or {}).items():
        if k.startswith("item/"):
            seen.setdefault(fold(e.get("name")), []).append(k)
    cash = lambda k: str((kb.get(k) or {}).get("type") or "").startswith("Cash")      # noqa: E731
    return {n: sorted(ks, key=lambda k: not cash(k)) for n, ks in seen.items()}


_CHANCE = re.compile(r"^(\d+(?:\.\d+)?)% chance per crate\b")
CRATE_WHOLE = 1.0                   # a crate is whole when its chances add up to 100% give or take this (rounding)
_crates: dict[int, tuple[object, dict[str, list[tuple[str, float]]]]] = {}


def kb_crates(kb) -> dict[str, list[tuple[str, float]]]:
    """What each Mystery Fashion Crate can give, from the KB's item pages (NiaMeowDB: each item's Cash Shop part
    names its crate, then "2.5% chance per crate · about 1 in 40 · ..."): folded crate name -> [(item key, percent)],
    likeliest first. Only a crate whose chances add up to 100%: a crate the KB knows a few items of (a new one, while
    NiaMeowDB fills it in) is left out rather than shown as if that were all it gives. One read per KB (~0.4 s)."""
    hit = _crates.get(id(kb))
    if hit and hit[0] is kb:
        return hit[1]
    found: dict[str, list[tuple[str, float]]] = {}
    for k, e in (getattr(kb, "entities", None) or {}).items():
        if not k.startswith("item/") or not str(e.get("type") or "").startswith("Cash"):
            continue
        lines = [ln.strip() for ln in kb.page(k).splitlines()]
        for i, ln in enumerate(lines):
            m = _CHANCE.match(ln)
            crate = next((x for x in reversed(lines[max(0, i - 2):i]) if x.endswith("Crate")), None)
            if m and crate:
                found.setdefault(fold(crate), []).append((k, float(m.group(1))))
    whole = {c: sorted(got, key=lambda x: (-x[1], x[0])) for c, got in found.items()
             if abs(sum(p for _, p in got) - 100) <= CRATE_WHOLE}
    _crates.clear()
    _crates[id(kb)] = (kb, whole)
    return whole


def same_text(a: str, b: str) -> bool:
    """The same words, whatever the spacing, line breaks and punctuation."""
    norm = lambda t: re.sub(r"[^a-z0-9%]+", " ", str(t or "").lower()).strip()      # noqa: E731
    return norm(a) == norm(b)


def kb_hebrew(kb, root, keys: list[str], game_text: str = "") -> str | None:
    """The Hebrew of an item's KB description, from any KB item of its name that has one (the app's file or the
    KB's nightly translation, made from the KB's own English). With game_text (OSMS's words: the game's own), only a
    KB text that says the same: the KB's closed-test words can differ ("up to 24 items" where the game says 18)."""
    for k in keys:
        try:
            en = itemdetails.details(kb, k).description or ""
        except Exception:          # noqa: BLE001 - a page the parser didn't expect
            continue
        if not en or (game_text and not same_text(en, game_text)):
            continue
        he = translations.he(root, "item_desc", k, en)
        if he:
            return he
    return None
