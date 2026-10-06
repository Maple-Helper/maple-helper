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
- image, image_w, image_h, image_src: NiaMeowDB's cover picture of the item, saved in the KB as img/news/<id>.<ext>
  (only a meowdb.com picture; an item whose cover is elsewhere has none).
- nexon: for Nexon's own announcements, the pictures of the article on nexon.com that show what the item's text names
  (the AP Reset Scroll, the six pets...) and its banner, in img/news/<id>/<media id>.<ext> (nexon_pictures()):
  {"id", "key", "banner": {file, w, h, src}?, "pictures": [{name, file, w, h, src}]}.

A picture is downloaded once: the next nights keep it while its address is the same; the pictures of items no longer
listed are deleted (prune_pictures). Every failure (a site down, a picture too big or not a picture) only means no
picture: the news stay.

    python tools/scrape_news.py            # refresh data/kb/news.json alone (the nightly scrape calls update())
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS_URL = "https://meowdb.com/msclassic/news"
MEOWDB = "https://meowdb.com"
# a Nexon article's content as JSON (www.nexon.com's article page is a script that loads it from here)
NEXON_ARTICLE = re.compile(r"https://www\.nexon\.com/maplestory/news/[a-z-]+/(\d+)(?:[/?#]|$)")
NEXON_API = "https://g.nexonstatic.com/maplestory/cms/v1/news/{id}"
NEXON_MEDIA = re.compile(r"https://g\.nexonstatic\.com/media/([a-z0-9]+)/([a-z0-9-]+)\.(png|jpe?g|webp)", re.I)
PICTURES = "img/news"
MAX_IMAGE_BYTES = 1_000_000          # a news picture is ~20-200 KB; anything over a megabyte is refused
MAX_PICTURES = 20                    # an article's named pictures (the Cash Shop's has 17)
FORMATS = {"webp": "WEBP", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}    # extension -> what Pillow must read
PAUSE = 0.5                          # between two picture requests (the scraper's own pace)
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


def clean_hebrew(kb: Path) -> int:
    """The Hebrew news.json already has, put in the app's terms (he_text). A KB published before the pipeline did that
    still said "תקרת לבל 100", which kb_release validate refuses (the night's run and a release carrying the KB
    forward both failed): the next night rewrites it even when the news page can't be fetched and nothing is
    translated. Returns how many items changed (counted as changes, so the cleaned KB gets published)."""
    path = kb / "news.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    except (OSError, json.JSONDecodeError, AttributeError):
        return 0
    changed = 0
    for i in items:
        before = json.dumps(i, ensure_ascii=False)
        for f in ("summary_he", "title_he", "commentary_he"):
            if isinstance(i.get(f), str) and i[f].strip():
                i[f] = he_text(i[f])
        if isinstance(i.get("highlights_he"), list):
            i["highlights_he"] = [he_text(str(x)) for x in i["highlights_he"]]
        changed += json.dumps(i, ensure_ascii=False) != before
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"news: {changed} items' Hebrew put in the app's terms", flush=True)
    return changed


# ---------------------------------------------------------------- pictures

def _image_size(data: bytes, ext: str) -> tuple[int, int] | None:
    """(width, height) when data is a picture of the format its name says (Pillow reads it), else None: an error page
    saved as .webp would show as nothing in the app."""
    import io
    try:
        from PIL import Image
    except ImportError:
        print("::warning::news: no Pillow, no news pictures", flush=True)
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != FORMATS.get(ext):
                return None
            img.load()
            return img.width, img.height
    except Exception:          # noqa: BLE001 - any undecodable picture is no picture
        return None


def _download(fetch_bytes, src: str, kb: Path, file: str) -> dict | bool | None:
    """One picture into kb/file: {"file", "w", "h", "src"}; None when the site didn't answer (asked again another
    night), False when what came is refused for good: over MAX_IMAGE_BYTES, or no picture (nothing written)."""
    try:
        data = fetch_bytes(src)
    except Exception:          # noqa: BLE001 - a failed picture never stops the news
        data = None
    time.sleep(PAUSE)
    if not isinstance(data, bytes) or not data:
        print(f"news: no answer for {src}", flush=True)
        return None
    ext = file.rsplit(".", 1)[-1]
    size = _image_size(data, ext) if len(data) <= MAX_IMAGE_BYTES else None
    if not size:
        print(f"news: {src} refused ({len(data):,} bytes, not a {ext} picture or too big)", flush=True)
        return False
    path = kb / file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"file": file, "w": size[0], "h": size[1], "src": src}


def _kept(old: dict | None, src: str, kb: Path) -> dict | None:
    """The picture there is when it came from this very address (the nights don't download it again)."""
    if isinstance(old, dict) and old.get("src") == src and isinstance(old.get("file"), str) \
            and old["file"].startswith(PICTURES + "/") and (kb / old["file"]).is_file():
        return {k: old.get(k) for k in ("file", "w", "h", "src")}
    return None


def _picture(old: dict | None, src: str, kb: Path, file: str, fetch_bytes) -> dict | bool | None:
    return _kept(old, src, kb) or (_download(fetch_bytes, src, kb, file) if fetch_bytes else None)


def cover_src(e: dict) -> str | None:
    """NiaMeowDB's cover of a news entry, when it is on meowdb.com (some old items point at other sites: none)."""
    url = str(e.get("image_url") or "").strip()
    if url.startswith("/") and not url.startswith("//"):
        url = MEOWDB + url
    if not re.fullmatch(r"https://meowdb\.com/[\w/.-]+\.(?:webp|png|jpe?g)", url, re.I):
        return None
    return url


def add_cover(i: dict, e: dict, old: dict | None, kb: Path, fetch_bytes) -> None:
    """The item's cover picture fields (image, image_w, image_h, image_src) when there is one."""
    src = cover_src(e)
    if not src or not re.fullmatch(r"[a-z0-9-]+", i["id"]):
        return
    ext = src.rsplit(".", 1)[-1].lower()
    was = {"file": old.get("image"), "w": old.get("image_w"), "h": old.get("image_h"),
           "src": old.get("image_src")} if isinstance(old, dict) else None
    got = _picture(was, src, kb, f"{PICTURES}/{i['id']}.{ext}", fetch_bytes)
    if got:
        i.update(image=got["file"], image_w=got["w"], image_h=got["h"], image_src=got["src"])


class _Images(HTMLParser):
    """Every <img src alt> of an article's HTML, in order."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            a = dict(attrs)
            if a.get("src"):
                self.found.append((a["src"].strip(), " ".join(str(a.get("alt") or "").split())))


def _fold(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _fold(name).lower().replace("'", "")).strip("-")


def _alt_name(alt: str, article: str) -> str:
    """What a picture shows, from its alt: Nexon writes "<what> <the article's name> <the game's name>" ("AP Reset
    Scroll Founder's Access Cash Shop Global MapleStory Classic World"). "" when the alt isn't written that way."""
    alt = _fold(html.unescape(alt)).strip()
    at = alt.lower().find(_fold(article).lower()) if article else -1
    if at > 0:
        return alt[:at].strip()
    m = re.search(r"\s+(?:Global\s+)?MapleStory(?:\s+Classic\s+World)?\s*$", alt, re.I)
    return alt[:m.start()].strip() if m and at < 0 else ""


def item_text(i: dict) -> str:
    """The English an item shows: what its pictures must be named in."""
    return "\n".join([i.get("title") or "", i.get("summary") or "", *(i.get("highlights") or []),
                      i.get("commentary") or ""])


def nexon_pictures(body: str, article: str, text: str) -> tuple[str | None, list[tuple[str, str]]]:
    """(the banner's address or None, [(name, address)]) of a Nexon article's HTML: the pictures of the things the
    item's text names, in the order it names them.

    A picture counts when its alt and its file name say the same thing ("AP Reset Scroll ..." and
    ".../ap-reset-scroll-founders-access-...png"): Nexon's article has alts that don't match their picture (a "Water of
    Life" alt on the Expanded Auto Move picture, "Signature Hair Coupon" on a palette): those are skipped. A name
    counts as written in the text, with its case, maybe plural; inside a longer name it doesn't ("Megaphone" in
    "Super Megaphones")."""
    parser = _Images()
    try:
        parser.feed(body)
    except Exception:          # noqa: BLE001 - a broken page is no pictures
        return None, []
    banner, named, seen = None, {}, set()
    for src, alt in parser.found:
        m = NEXON_MEDIA.fullmatch(src)
        if not m or src in seen:
            continue
        seen.add(src)
        slug = m.group(2).lower()
        if banner is None and "banner" in slug.split("-"):
            banner = src
            continue
        name = _alt_name(alt, article)
        if not name or len(name) > 60:
            continue
        s = _slugify(name)
        if s and (slug == s or slug.startswith(s + "-")) and name not in named:
            named[name] = src
    hay, found = _fold(text), []
    for name in sorted(named, key=len, reverse=True):            # the longest first, masked once found
        pat = re.compile(rf"(?<![\w']){re.escape(name)}(?:e?s)?(?![\w'])")
        hits = list(pat.finditer(hay))
        if hits:
            found.append((hits[0].start(), name))
            hay = pat.sub(lambda x: " " * len(x.group(0)), hay)
    found.sort()
    return banner, [(name, named[name]) for _, name in found[:MAX_PICTURES]]


def add_nexon(i: dict, old: dict | None, kb: Path, fetch, fetch_bytes) -> None:
    """i["nexon"]: the pictures of Nexon's own article (nexon_pictures), for an item Nexon published. One request
    for the article when the item's text or address changed since the night that matched its pictures; the pictures
    already there are kept. A failure keeps what there was."""
    m = NEXON_ARTICLE.match(i.get("source_url") or "")
    if not (m and i.get("publisher") == "Nexon" and re.fullmatch(r"[a-z0-9-]+", i["id"])):
        return
    prev = old.get("nexon") if isinstance(old, dict) and isinstance(old.get("nexon"), dict) else None
    pics = [p for p in (prev or {}).get("pictures") or [] if isinstance(p, dict)]
    have = [_kept(p, p.get("src"), kb) for p in pics] + \
        ([_kept(prev.get("banner"), (prev.get("banner") or {}).get("src"), kb)] if prev and prev.get("banner") else [])
    key = _hash(i.get("source_url") or "", item_text(i))
    if prev and prev.get("key") == key and all(have):
        i["nexon"] = prev                     # the same article, every picture there: nothing fetched
        return
    if not fetch_bytes:
        if prev and all(have):
            i["nexon"] = prev
        return
    try:
        page = fetch(NEXON_API.format(id=m.group(1)))
        data = json.loads(page) if page else None
    except Exception:          # noqa: BLE001
        data = None
    time.sleep(PAUSE)
    if not (isinstance(data, dict) and isinstance(data.get("body"), str)):
        print(f"news: Nexon's article {m.group(1)} unreadable; keeping its pictures", flush=True)
        if prev and all(have):
            i["nexon"] = prev
        return
    banner, named = nexon_pictures(data["body"], str(data.get("name") or ""), item_text(i))
    by_src = {p.get("src"): p for p in pics}
    out: dict = {"id": int(m.group(1)), "key": key}
    complete = True

    def get(src: str, was: dict | None) -> dict | bool | None:
        nonlocal complete
        mm = NEXON_MEDIA.fullmatch(src)
        ext = mm.group(3).lower()
        got = _picture(was, src, kb, f"{PICTURES}/{i['id']}/{mm.group(1).lower()}.{ext}", fetch_bytes)
        complete = complete and got is not None      # (a picture refused for good is not asked again)
        return got

    if banner:
        b = get(banner, (prev or {}).get("banner"))
        if b:
            out["banner"] = b
    out["pictures"] = [dict(name=name, **got) for name, src in named if (got := get(src, by_src.get(src)))]
    if not complete:
        out["key"] = ""             # a picture failed: the next night asks again
    if out.get("banner") or out["pictures"] or complete:
        i["nexon"] = out


def picture_files(items: list[dict]) -> set[str]:
    """Every picture file the items use (KB-relative)."""
    out = set()
    for i in items:
        out.add(i.get("image"))
        nx = i.get("nexon") if isinstance(i.get("nexon"), dict) else {}
        out.add((nx.get("banner") or {}).get("file"))
        out.update(p.get("file") for p in nx.get("pictures") or [] if isinstance(p, dict))
    return {f for f in out if isinstance(f, str)}


def prune_pictures(kb: Path, items: list[dict]) -> int:
    """Delete the pictures no item uses any more (an item gone from the list, a picture replaced). Returns how many."""
    root = kb / PICTURES
    if not root.is_dir():
        return 0
    used = picture_files(items)
    gone = 0
    for f in sorted(root.rglob("*"), reverse=True):        # files before their folders
        rel = f.relative_to(kb).as_posix()
        if f.is_file() and rel not in used:
            f.unlink()
            gone += 1
        elif f.is_dir() and not any(f.iterdir()):
            f.rmdir()
    if gone:
        print(f"news: {gone} old pictures deleted", flush=True)
    return gone


def update(kb: Path, fetch, fetch_bytes=None) -> int:
    """Refresh kb/news.json with fetch(url) -> str | None (the scraper's polite fetch). Returns how many items are
    new or changed (0 when nothing changed). A failed fetch or an unreadable page keeps the news.json there is:
    a site hiccup must not wipe the news, or the next good night announce all of it again.

    fetch_bytes(url) -> bytes | None downloads the pictures (add_cover, add_nexon); without it the items keep the
    pictures they have and get no new ones."""
    cleaned = clean_hebrew(kb)
    path = kb / "news.json"
    try:
        old = {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8")).get("items", [])}
    except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError):
        old = {}
    page = fetch(NEWS_URL)
    if not page:
        print("news: the news page could not be fetched; keeping the news there is", flush=True)
        return cleaned
    try:
        items = build(page, translations(kb=kb))
    except NewsError as e:
        print(f"news: {e}; keeping the news there is", flush=True)
        return cleaned
    found = {str(e.get("slug") or ""): e for e in entries(page)}
    for i in items:
        add_cover(i, found.get(i["id"], {}), old.get(i["id"]), kb, fetch_bytes)
        add_nexon(i, old.get(i["id"]), kb, fetch, fetch_bytes)
    changed = sum(1 for i in items if old.get(i["id"]) != i)
    removed = len(old.keys() - {i["id"] for i in items})
    pruned = prune_pictures(kb, items)            # (a picture deleted is a change too: the KB gets lighter)
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
    return changed + removed + cleaned + pruned


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "tools"))
    import scrape_meowdb
    update(scrape_meowdb.KB, scrape_meowdb.fetch, lambda url: scrape_meowdb.fetch(url, binary=True))
