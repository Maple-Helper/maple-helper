"""Hebrew for texts the knowledge base brings in English: a quest's journal line, a pet skill's description, a skill
change's note, a skill's description and effects (the Skills tab). tools/translate_kb.py makes them every night into the KB's own he.json, so a new quest or note reads in
Hebrew from the next night on, with no app release; the files the app ships (assets/<kind>/he.json) are the owner's
own and win whenever they were made from the same English.

A translation is used only while it was made from the very English the KB has now ("en"): a text NiaMeowDB rewrites
shows in English until the next night translates it again.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "he.json"                    # in the KB: {kind: {key: {"en", "he"}}, "news": {...}}
KINDS = ("quest_tasks", "pet_skills", "skill_changes", "skill_desc", "item_desc", "item_notes", "cash_desc")
# a kind whose shipped file isn't assets/<kind>/he.json (an item's description and its Meow Notes: assets/items; a Cash
# Shop item's OSMS description, for one the KB has no Hebrew of: assets/cashshop, keyed "cash/<game id>")
ASSET_DIRS = {"skill_desc": "skills", "item_desc": "items", "item_notes": "items", "cash_desc": "cashshop"}
ASSETS = Path(__file__).resolve().parent.parent / "assets"

_cache: dict[str, tuple[float, dict]] = {}


def _load(path: Path) -> dict:
    """A JSON file, read again only when it changed (the nightly swaps the KB under a running app)."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    hit = _cache.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _cache[str(path)] = (stamp, data)
    return data


def kb_table(root: Path | None, kind: str) -> dict:
    table = _load(Path(root) / FILE).get(kind) if root else None
    return table if isinstance(table, dict) else {}


def he(root: Path | None, kind: str, key: str, en: str) -> str | None:
    """The Hebrew of `en` (kind, key), the app's own first, then the KB's; None when none was made from it. The app's
    file is the owner's curated Hebrew: made from the same English, it beats the night's machine translation (the
    KB's came first, so a hand fix to a poor nightly line never showed)."""
    en = (en or "").strip()
    if not en:
        return None
    for table in (_load(ASSETS / ASSET_DIRS.get(kind, kind) / "he.json"), kb_table(root, kind)):
        found = made_from(table.get(key), en)
        if found:
            return found
    return None


def made_from(row, en: str) -> str | None:
    """The Hebrew of a row made from `en`: the row's own, or an older version it keeps under "alt" (NiaMeowDB
    rewrites a text now and then, as on 2026-10-11; the app ships Hebrew for both sides, so a KB from before the
    rewrite and one from after it both read in Hebrew)."""
    en = (en or "").strip()
    if not isinstance(row, dict) or not en:
        return None
    for version in [row, *(row.get("alt") or [])]:
        if isinstance(version, dict) and str(version.get("en") or "").strip() == en \
                and str(version.get("he") or "").strip():
            return str(version["he"]).strip()
    return None
