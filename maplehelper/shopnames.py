"""Matching the Cash Shop's items (OSMS) to the KB by name, and their KB Hebrew: no Qt, so the nightly's
tools/translate_kb.py (which runs without the app's UI libraries) can use the same rules as the Shop page."""
from __future__ import annotations

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


def same_text(a: str, b: str) -> bool:
    """The same words, whatever the spacing, line breaks and punctuation."""
    import re
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
