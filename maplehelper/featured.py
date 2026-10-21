"""A featured article: an official announcement copied into the app as it looks on Nexon's site, shown once to every
player who opens the chat while it runs (the owner's call, 2026-10-21: the Grand Launch release notes).

assets/featured/<id>/manifest.json (written by tools/featured_article.py) holds, per language, the article as
pictures (tiles from the top down, in CSS pixels times "scale"), its animations' and links' places on it, and the
dates it is shown between. ui/featuredview.py draws it; to_pdf saves the same pictures as a PDF.

A new article needs no app release: its folder comes live (live.py, kind "featured"), every file through
file_path(), and it shows once all its tiles are on disk.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path

from . import live
from .store import ASSETS

log = logging.getLogger(__name__)
ROOT = ASSETS / "featured"
SETTING = "featured_seen"          # ids of the featured articles already shown to this player


def articles() -> tuple[dict, ...]:
    """Every featured article that is complete on disk (a missing tile drops its language), newest "from" first:
    the shipped ones and the live ones (live.py)."""
    return _articles(live.generation())


@lru_cache(maxsize=1)
def _articles(_gen: int) -> tuple[dict, ...]:
    folders = {p.parent.name for p in ROOT.glob("*/manifest.json")} if ROOT.is_dir() else set()
    folders |= {rel.split("/")[1] for rel in live.files("featured/") if rel.endswith("/manifest.json")}
    out = []
    for folder in sorted(folders):
        path = live.asset(f"featured/{folder}/manifest.json")
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
            langs = {lang: v for lang, v in (m.get("langs") or {}).items()
                     if v.get("tiles") and all(live.asset(f"featured/{folder}/{t['file']}").is_file()
                                               for t in v["tiles"])}
        except (OSError, ValueError, AttributeError, KeyError, TypeError) as e:
            log.warning("featured article unreadable: %s: %s", path, e)
            continue
        if not (m.get("id") and langs):
            continue
        out.append({**m, "langs": langs, "dir": ROOT / folder, "folder": folder})
    return tuple(sorted(out, key=lambda m: m.get("from", ""), reverse=True))


def _day(s) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def running(m: dict, today: date | None = None) -> bool:
    today = today or date.today()
    start, end = _day(m.get("from")), _day(m.get("until"))
    return (start is None or start <= today) and (end is None or today <= end)


def due(seen, today: date | None = None) -> dict | None:
    """The article to show now: running, not shown to this player yet."""
    seen = set(seen or ())
    return next((m for m in articles() if m["id"] not in seen and running(m, today)), None)


def for_news(news_id: str) -> dict | None:
    """The featured copy of a news item (its full article in the News window), whatever the dates."""
    return next((m for m in articles() if news_id and m.get("news_id") == news_id), None)


def mark_seen(settings, article_id: str) -> None:
    seen = list(settings[SETTING] or [])
    if article_id not in seen:
        settings[SETTING] = seen + [article_id]


def language(m: dict, lang: str) -> str:
    """The app's language when the article has it, else English (or whatever it has)."""
    return lang if lang in m["langs"] else ("en" if "en" in m["langs"] else next(iter(m["langs"])))


def text(m: dict, field: str, lang: str) -> str:
    v = m.get(field) or {}
    return v.get(lang) or v.get("en") or "" if isinstance(v, dict) else str(v)


def file_path(m: dict, name: str) -> Path:
    """One of the article's files (a tile, an animation): the live copy when there is one, else the shipped one."""
    return live.asset(f"featured/{m.get('folder') or Path(m['dir']).name}/{name}")


def tile_path(m: dict, tile: dict) -> Path:
    return file_path(m, tile["file"])


def to_pdf(m: dict, lang: str, dest: Path) -> Path:
    """The article as the app shows it, as a PDF: one page per tile, at its own resolution (the pictures only; the
    animations show their first frame, as on the page under them)."""
    from PIL import Image

    page = m["langs"][lang]
    dpi = 72 * float(page.get("scale") or 1)           # the page as wide in points as the article in CSS pixels
    images = []
    try:
        for t in page["tiles"]:
            with Image.open(tile_path(m, t)) as im:
                images.append(im.convert("RGB"))
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        images[0].save(tmp, "PDF", save_all=True, append_images=images[1:], resolution=dpi,
                       title=text(m, "title", lang), author=text(m, "publisher", lang) or "Nexon")
        tmp.replace(dest)
    finally:
        for im in images:
            im.close()
    return dest
