"""MapleStory Classic news, from the knowledge base's news.json (tools/scrape_news.py: NiaMeowDB's news section,
scraped nightly with the rest of the KB).

The chat's news card shows Global news the player hasn't dismissed, from the last NEW_DAYS days (a first start
doesn't greet the player with half a year of news); the patch notes window's News tab lists everything. Titles stay
as published; the summary is in Hebrew when a translation was made from its current English (summary_he), else the
English one, and the UI says so.

News never changes what the app treats as released: availability.py reads the release guide only. The AI gets the
recent official items as context when a question asks about news, launch, maintenance or what opens next.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

NEWS_PAGE = "https://meowdb.com/msclassic/news"
SETTING = "news_read"   # the ids the player dismissed or read (store.Settings), newest kept
KEEP = 300
SINCE = "2026-10-02"    # news start here (the owner's call, 2026-10-04); tools/scrape_news.py cuts the same
NEW_DAYS = 14          # how long an item can still come up as unread news in the chat
SHOWN_REGION = "gms"   # the app is for Global Classic: China / Taiwan news stay in the News tab only
REGIONS = ("gms", "cms", "tms")


def items(kb) -> list[dict]:
    """news.json's items, newest first (memoized on the file's time, like recent.changelog)."""
    root = getattr(kb, "root", None)
    path = root / "news.json" if root else None
    try:
        stamp = path.stat().st_mtime if path else None
    except OSError:
        stamp = None
    memo = getattr(kb, "_news", None)
    if memo and memo[0] == stamp:
        return memo[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if stamp is not None else {}
        found = data.get("items") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        found = None
    out = [i for i in found or [] if isinstance(i, dict) and i.get("id") and i.get("title") and i.get("date")
           and str(i["date"]) >= SINCE]        # (a KB from before the cut still carries older news)
    out.sort(key=lambda i: (str(i["date"]), str(i["id"])), reverse=True)
    try:
        kb._news = (stamp, out)
    except AttributeError:
        pass
    return out


def _day(i: dict) -> date | None:
    try:
        return date.fromisoformat(str(i.get("date") or "")[:10])
    except ValueError:
        return None


def unread(kb, dismissed, today: date | None = None) -> list[dict]:
    """Global news from the last NEW_DAYS days the player hasn't dismissed, newest first."""
    today = today or date.today()
    since = today - timedelta(days=NEW_DAYS)
    gone = set(dismissed or ())
    return [i for i in items(kb) if i.get("region", SHOWN_REGION) == SHOWN_REGION and i["id"] not in gone
            and (d := _day(i)) and since <= d <= today]


def mark_read(settings, ids) -> None:
    """Remember these items as read (dismissed in the chat, or seen in the News tab)."""
    have = [x for x in (settings[SETTING] or []) if isinstance(x, str)]
    add = [x for x in ids if x not in have]
    if add:
        settings[SETTING] = (have + add)[-KEEP:]


def summary(i: dict, lang: str) -> tuple[str, bool]:
    """(the summary to show, whether it's in the UI's language). Hebrew falls back to the English summary."""
    if lang == "he":
        he = str(i.get("summary_he") or "").strip()
        if he:
            return he, True
        return str(i.get("summary") or ""), False
    return str(i.get("summary") or ""), True


def body(i: dict, lang: str) -> tuple[list[str], str, bool]:
    """The article's body: (highlights, NiaMeowDB's note, whether in the UI's language). Hebrew when translated from
    the current English (highlights_he / commentary_he), else the English."""
    hl, note = [str(x) for x in i.get("highlights") or []], str(i.get("commentary") or "")
    if lang != "he":
        return hl, note, True
    he_hl, he_note = i.get("highlights_he"), str(i.get("commentary_he") or "")
    if (not hl or (isinstance(he_hl, list) and len(he_hl) == len(hl))) and (not note or he_note):
        return [str(x) for x in he_hl or []], he_note, True
    return hl, note, False


def title(i: dict, lang: str) -> str:
    """The title in the UI's language: Hebrew when translated from its current English (title_he), else as published."""
    if lang == "he":
        he = str(i.get("title_he") or "").strip()
        if he:
            return he
    return str(i.get("title") or "")


def short_date(i: dict) -> str:
    """2026-10-03 -> 3.10 (this year) or 3.10.2025 (the same in both directions)."""
    d = _day(i)
    if not d:
        return str(i.get("date") or "")
    return f"{d.day}.{d.month}" if d.year == date.today().year else f"{d.day}.{d.month}.{d.year}"


# ---------------------------------------------------------------- for the AI

_ASKS_NEWS = re.compile(
    r"\b(news|announce\w*|maintenance|patch(?: notes)?|launch\w*|release\w*|open(?:s|ing)?|founder'?s|update|"
    r"server|downtime|event|coming|roadmap)\b|חדשות|הודע|תחזוק|השק|נפתח|ייפתח|יפתח|פתיחה|עדכון|שרת|אירוע|מתי",
    re.I)


def asks_news(question: str) -> bool:
    return bool(_ASKS_NEWS.search(question or ""))


def ai_lines(kb, limit: int = 6, today: date | None = None) -> list[str]:
    """The newest Global news as context lines: "News 2026-10-03 (official, Nexon): <title>. <summary>". Official
    items first; never the source for what is released (the game scope line is)."""
    today = today or date.today()
    pool = [i for i in items(kb) if i.get("region", SHOWN_REGION) == SHOWN_REGION and (d := _day(i)) and d <= today]
    official = [i for i in pool if i.get("official")][:limit]
    pool = official + [i for i in pool if not i.get("official")][:limit - len(official)]
    pool.sort(key=lambda i: (str(i["date"]), str(i["id"])), reverse=True)
    out = []
    for i in pool:
        who = ("official, " if i.get("official") else "community, ") + (i.get("publisher") or "NiaMeowDB")
        out.append(f"News {i['date']} ({who}): {i['title']}. {i.get('summary') or ''}".strip())
    if out:
        out.insert(0, "MapleStory Classic news from NiaMeowDB's news section (announcements, not the game's data: what is "
                      "in the game is the game scope in your instructions, not these; say when a date is only announced):")
    return out
