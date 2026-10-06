"""Hebrew for texts the knowledge base brings in English: a quest's journal line, a pet skill's description, a skill
change's note. tools/translate_kb.py makes them every night into the KB's own he.json, so a new quest or note reads in
Hebrew from the next night on, with no app release; the files the app ships (assets/<kind>/he.json) are the owner's
own and win whenever they were made from the same English.

A translation is used only while it was made from the very English the KB has now ("en"): a text NiaMeowDB rewrites
shows in English until the next night translates it again.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = "he.json"                    # in the KB: {kind: {key: {"en", "he"}}, "news": {...}}
KINDS = ("quest_tasks", "pet_skills", "skill_changes")
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
    for table in (_load(ASSETS / kind / "he.json"), kb_table(root, kind)):
        row = table.get(key)
        if isinstance(row, dict) and str(row.get("en") or "").strip() == en and str(row.get("he") or "").strip():
            return str(row["he"]).strip()
    return None
