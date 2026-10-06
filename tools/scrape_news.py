"""MapleStory Classic news from NiaMeowDB (meowdb.com/msclassic/news) into the knowledge base: data/kb/news.json.

Used with NiaMeowDB's permission, like the rest of the KB. One request a night: the news page carries every
article as structured data (slug, date, region, publisher, headline, summary, highlights) in its Next.js payload,
the same records the article pages render, so the ~52 article pages are never fetched one by one.

news.json = {"source", "fetched_at", "items": [{id, date, title, summary, highlights, commentary, region, official,
publisher, source_url, url, tags, mentions, hash, summary_he?}]}, newest first.

- official: the publisher is a game's operator (Nexon for Global, Shengqu for China, Gamania for Taiwan), on its
  site or social channels; NiaMeowDB's own analysis and press articles are community news.
- mentions: regions and content the item names (Ossyria, Orbis, 3rd job…). Only a hint for the AI and the owner:
  availability.py reads the release guide, never the news (an announcement is not the game).
- summary_he: the Hebrew summary from assets/news/he.json (tools/translate_news.py), kept only while it was made from
  this very English text (hash): a summary NiaMeowDB rewrote shows in English until it is translated again.

    python tools/scrape_news.py            # refresh data/kb/news.json alone (the nightly scrape calls update())
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS_URL = "https://meowdb.com/msclassic/news"
# the app's news start here (the owner's call, 2026-10-04): every item from then on, none older (maplehelper/news.py)
SINCE = "2026-10-02"
TRANSLATIONS = ROOT / "assets" / "news"
OFFICIAL_PUBLISHERS = {"Nexon", "Shengqu", "Gamania"}       # the operators of Global, China and Taiwan Classic
# content whose opening news may announce: a hint list, matched as whole words (case-insensitive)
MENTIONS = ("Victoria Island", "Ossyria", "Orbis", "El Nath", "Ludibrium", "Aqua Road", "Leafre", "Mu Lung",
            "Nihal Desert", "Forgotten Hollow", "Sleepywood", "Masteria", "3rd job", "4th job", "level cap", "Zakum",
            "Papulatus", "Horntail", "Cash Shop", "Hired Merchant")
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)


class NewsError(Exception):
    pass


def payload(page: str) -> str:
    """The page's React Server Components payload, as one string (the chunks Next.js streams into the HTML)."""
    out = []
    for chunk in _PUSH.findall(page):
        try:
            out.append(json.loads('"' + chunk + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def entries(page: str) -> list[dict]:
    """The news list the page renders: {"entries": [{slug, date_published, headline, ...}], "locale": "en"}."""
    text = payload(page)
    at = text.find('{"entries":[')
    if at < 0:
        raise NewsError("no news list in the page (the site's layout changed?)")
    try:
        data, _ = json.JSONDecoder().raw_decode(text, at)
    except json.JSONDecodeError as e:
        raise NewsError(f"news list unreadable: {e}") from e
    found = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(found, list):
        raise NewsError("news list is not a list")
    return [e for e in found if isinstance(e, dict)]


def _hash(title: str, summary: str) -> str:
    return hashlib.sha1(f"{title}\n{summary}".encode("utf-8")).hexdigest()[:16]


def mentions(*texts: str) -> list[str]:
    blob = "\n".join(t for t in texts if t)
    return [m for m in MENTIONS if re.search(rf"(?<![A-Za-z]){re.escape(m)}(?![A-Za-z])", blob, re.I)]


def item(e: dict) -> dict | None:
    """One news.json item from a site entry; None when it lacks what every item needs."""
    slug, title, date = str(e.get("slug") or ""), str(e.get("headline") or "").strip(), str(e.get("date_published") or "")
    if not (slug and title and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date[:10])):
        return None
    summary = str(e.get("summary") or "").strip()
    highlights = [str(h).strip() for h in e.get("highlights") or [] if str(h).strip()]
    publisher = str(e.get("source_label") or "").strip()
    out = {
        "id": slug, "date": date[:10], "title": title, "summary": summary, "highlights": highlights,
        "commentary": str(e.get("commentary") or "").strip(),
        "region": str(e.get("region") or "").lower(),                      # gms (Global) | cms (China) | tms (Taiwan)
        "official": publisher in OFFICIAL_PUBLISHERS,
        "publisher": publisher, "source_url": str(e.get("source_url") or ""),
        "url": f"{NEWS_URL}/{slug}", "tags": [str(t) for t in e.get("tags") or []],
        "mentions": mentions(title, summary, *highlights),
        "hash": _hash(title, summary),
    }
    return out


def _body_hash(i: dict) -> str:
    """The highlights and the note, which the title + summary hash doesn't cover: a translation of the body is kept
    only while it was made from this text."""
    return _hash(" | ".join(i.get("highlights") or []), str(i.get("commentary") or ""))


def translations(lang: str = "he", kb: Path | None = None) -> dict[str, dict]:
    """Each item's Hebrew: the app's own file, then the KB's he.json "news" (tools/translate_kb.py, every night)."""
    try:
        data = json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data = data if isinstance(data, dict) else {}
    if kb is not None:
        try:
            made = json.loads((Path(kb) / "he.json").read_text(encoding="utf-8")).get("news") or {}
        except (OSError, json.JSONDecodeError, AttributeError):
            made = {}
        if isinstance(made, dict):
            # the night's, made from the newest English, over the app's older ones; made from the same English (title,
            # summary and body), the app's (the owner's curated Hebrew) stays
            data = {**data, **{k: v for k, v in made.items() if not (
                isinstance(data.get(k), dict) and isinstance(v, dict)
                and (data[k].get("source_hash"), data[k].get("body_hash")) == (v.get("source_hash"), v.get("body_hash")))}}
    return data


def build(page: str, he: dict[str, dict] | None = None) -> list[dict]:
    """news.json's items, newest first, each with its Hebrew summary when one was made from its current text."""
    he = translations() if he is None else he
    items = [i for i in (item(e) for e in entries(page)) if i]
    if not items:
        raise NewsError("the news list is empty")
    items = [i for i in items if i["date"] >= SINCE]
    for i in items:
        _with_he(i, he)
    return sorted(items, key=lambda i: (i["date"], i["id"]), reverse=True)


def he_text(text: str) -> str:
    """A Hebrew translation in the app's terms: "רמה", never the gamer's "לבל" (in any form: "בלבל 10", "הלבל"),
    "גריינד", a level named as one. The same rules the AI's answers go through (brain.drop_keys): the machine
    translation wrote "תקרת לבל 100" into the news past its prompt's rule, and the news are shown as they are."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from maplehelper.brain import drop_keys
    return drop_keys(text).strip()


def _with_he(i: dict, he: dict) -> None:
    """An item's Hebrew, from a translation made from its current English (its title and summary hash, its body's)."""
    for f in ("summary_he", "title_he", "highlights_he", "commentary_he"):
        i.pop(f, None)
    tr = he.get(i["id"])
    if isinstance(tr, dict) and tr.get("source_hash") == i["hash"] and (
            str(tr.get("summary") or "").strip() or str(tr.get("title") or "").strip()):
        # (an item with no English summary has no Hebrew one either, but its title is still translated)
        if str(tr.get("summary") or "").strip() and str(i.get("summary") or "").strip():
            i["summary_he"] = he_text(tr["summary"])
        if str(tr.get("title") or "").strip():          # the title too (the hash covers both)
            i["title_he"] = he_text(tr["title"])
        # the article's body: its highlights and NiaMeowDB's note, when translated from the current English
        if tr.get("body_hash") == _body_hash(i):
            if isinstance(tr.get("highlights"), list) and len(tr["highlights"]) == len(i.get("highlights") or []):
                i["highlights_he"] = [he_text(str(x)) for x in tr["highlights"]]
            if str(tr.get("commentary") or "").strip() and i.get("commentary"):
                i["commentary_he"] = he_text(tr["commentary"])


def apply_translations(kb: Path) -> None:
    """news.json's items with the Hebrew there is now (tools/translate_kb.py, after it adds some)."""
    path = kb / "news.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    he = translations(kb=kb)
    for i in data.get("items") or []:
        _with_he(i, he)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def update(kb: Path, fetch) -> int:
    """Refresh kb/news.json with fetch(url) -> str | None (the scraper's polite fetch). Returns how many items are
    new or changed (0 when nothing changed). A failed fetch or an unreadable page keeps the news.json there is:
    a site hiccup must not wipe the news, or the next good night announce all of it again."""
    path = kb / "news.json"
    try:
        old = {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8")).get("items", [])}
    except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError):
        old = {}
    page = fetch(NEWS_URL)
    if not page:
        print("news: the news page could not be fetched; keeping the news there is", flush=True)
        return 0
    try:
        items = build(page, translations(kb=kb))
    except NewsError as e:
        print(f"news: {e}; keeping the news there is", flush=True)
        return 0
    changed = sum(1 for i in items if old.get(i["id"]) != i)
    removed = len(old.keys() - {i["id"] for i in items})
    if changed or removed:
        path.write_text(json.dumps({"source": "NiaMeowDB (meowdb.com/msclassic/news)",
                                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    "items": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for i in items:
        if i["id"] not in old and i["official"] and i["mentions"]:
            # for the owner, in the nightly run's log: official news that may mean content opens (availability is
            # never changed from news; the release guide stays the source)
            print(f"::notice::official news names {', '.join(i['mentions'])}: {i['title']} ({i['url']})", flush=True)
    print(f"news: {len(items)} items, {changed} new or changed", flush=True)
    return changed + removed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "tools"))
    import scrape_meowdb
    update(scrape_meowdb.KB, scrape_meowdb.fetch)
