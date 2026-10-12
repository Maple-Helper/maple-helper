"""MapleStory Classic news from Nexon itself (nexon.com/maplestory/news) into the knowledge base: data/kb/news.json.

The news are Nexon's own announcements, in Nexon's own words: nothing is rewritten or summed up (no AI, no
community site). nexon.com's news pages are a script that loads them from Nexon's public JSON: one request a night
for the list (NEWS_LIST, every article's id, name, summary, date and category), then one per Classic World article
for its HTML (NEXON_API), with a pause between two.

news.json = {"source", "fetched_at", "items": [{id, date, title, summary, highlights, commentary, region, official,
publisher, source_url, url, tags, mentions, hash, summary_he?}]}, newest first.

- which: Classic World articles (Nexon marks them isMSCW, or "Classic World" is in their name or summary) from SINCE.
- id: the id the item already had in news.json (an older KB's item for the same Nexon article keeps its id, so the
  players' "read" marks and the Hebrew made for it stay), else "nexon-<Nexon's id>".
- title, summary: Nexon's name and summary as published. highlights: the article's own sentences (highlights()):
  every paragraph, list line and heading of a short article; the headings of a long one (a Cash Shop, an interview).
  commentary: always "" (the field stays for older KBs, where it was a community site's note).
- official / publisher / region: True / "Nexon" / "gms" (Global). url and source_url: the article on nexon.com.
- mentions: regions and content the item names (Ossyria, Orbis, 3rd job…). Only a hint for the AI and the owner:
  availability.py reads the release guide, never the news (an announcement is not the game).
- summary_he: the Hebrew summary from assets/news/he.json (tools/translate_news.py), kept only while it was made from
  this very English text (hash): a summary Nexon edits shows in English until it is translated again.
- image, image_w, image_h, image_src: the article's thumbnail on Nexon's site (its imageThumbnail), saved in the KB as
  img/news/<id>.<ext>.
- nexon: the pictures of the article that show what the item's text names (the AP Reset Scroll, the six pets...) and
  its banner, in img/news/<id>/<media id>.<ext> (nexon_pictures()):
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
NEXON = "https://g.nexonstatic.com"
NEWS_LIST = NEXON + "/maplestory/cms/v1/news"          # every article, newest first, without its body
NEXON_API = NEXON + "/maplestory/cms/v1/news/{id}"     # one article with its HTML (body)
NEWS_PAGE = "https://www.nexon.com/maplestory/news"
ARTICLE_URL = NEWS_PAGE + "/{category}/{id}"           # what a player opens (the page loads NEXON_API)
NEXON_ARTICLE = re.compile(r"https://www\.nexon\.com/maplestory/news/[a-z-]+/(\d+)(?:[/?#]|$)")
# a picture on Nexon's media server: an article's ("/media/<id>/<name>.png") and the list's thumbnails alike
NEXON_MEDIA = re.compile(r"https://g\.nexonstatic\.com/media/([a-z0-9]+)/([a-z0-9_-]+)\.(png|jpe?g|webp)", re.I)
PICTURES = "img/news"
MAX_IMAGE_BYTES = 1_000_000          # a news picture is ~20-200 KB; anything over a megabyte is refused
MAX_PICTURES = 20                    # an article's named pictures (the Cash Shop's has 17)
FORMATS = {"webp": "WEBP", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}    # extension -> what Pillow must read
PAUSE = 0.5                          # between two requests to Nexon (the scraper's own pace)
# the app's news start here (the owner's call, 2026-10-04): every item from then on, none older (maplehelper/news.py)
SINCE = "2026-10-02"
TRANSLATIONS = ROOT / "assets" / "news"
PUBLISHER = "Nexon"
# content whose opening news may announce: a hint list, matched as whole words (case-insensitive)
MENTIONS = ("Victoria Island", "Ossyria", "Orbis", "El Nath", "Ludibrium", "Aqua Road", "Leafre", "Mu Lung",
            "Nihal Desert", "Forgotten Hollow", "Sleepywood", "Masteria", "3rd job", "4th job", "level cap", "Zakum",
            "Papulatus", "Horntail", "Cash Shop", "Hired Merchant")
MAX_HIGHLIGHTS = 12                  # a short article's lines, or a long one's headings
SHORT_HEADINGS = 6                   # a long article with no headings: its first paragraphs
MAX_HIGHLIGHT = 400                  # characters; a longer paragraph ends at its last sentence that fits
# the greetings and sign-offs around an announcement: not what it says
BOILERPLATE = re.compile(r"(?:h(?:i|ello|ey),? maplers|join our official|thank you|we appreciate|happy mapling|"
                         r"see you (?:in|on|there)|[-—–]\s*the maplestory team)", re.I)


class NewsError(Exception):
    pass


def entries(text: str) -> list[dict]:
    """Nexon's news list: [{id, name, summary, liveDate, category, isMSCW?, imageThumbnail, ...}]."""
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as e:
        raise NewsError(f"news list unreadable: {e}") from e
    if not isinstance(data, list):
        raise NewsError("news list is not a list (Nexon's API changed?)")
    found = [e for e in data if isinstance(e, dict) and isinstance(e.get("id"), int)
             and str(e.get("name") or "").strip()
             and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("liveDate") or "")[:10])]
    if not found:
        raise NewsError("the news list is empty")
    return found


def _truthy(v) -> bool:
    """Nexon's flags: true in the list, "True" in an article (and missing on some entries)."""
    return v is True or str(v).strip().lower() == "true"


def classic(e: dict) -> bool:
    """A Classic World article: marked so, or naming it (the Inkwell interview isn't marked)."""
    return _truthy(e.get("isMSCW")) or any("classic world" in str(e.get(f) or "").lower() for f in ("name", "summary"))


def selected(found: list[dict]) -> list[dict]:
    """The articles the app shows: Classic World's, from SINCE on."""
    return [e for e in found if classic(e) and str(e["liveDate"])[:10] >= SINCE]


def article_url(e: dict) -> str:
    category = str(e.get("category") or "").lower()
    return ARTICLE_URL.format(category=category if re.fullmatch(r"[a-z-]+", category) else "general", id=e["id"])


def _hash(title: str, summary: str) -> str:
    return hashlib.sha1(f"{title}\n{summary}".encode("utf-8")).hexdigest()[:16]


def mentions(*texts: str) -> list[str]:
    blob = "\n".join(t for t in texts if t)
    return [m for m in MENTIONS if re.search(rf"(?<![A-Za-z]){re.escape(m)}(?![A-Za-z])", blob, re.I)]


# ---------------------------------------------------------------- the article's own text

BLOCKS = ("p", "li", "h1", "h2", "h3", "h4")
HEADINGS = ("h1", "h2", "h3")


class _Blocks(HTMLParser):
    """An article's text blocks in order, [(tag, text)]: each paragraph, list line and heading, a <br> inside one
    read as "; " ("Thursday, October 8; PDT (UTC -7): 7:00 AM - 9:00 AM"). A list line holding a list is its own
    text, then each line under it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []
        self.open: list[str] = []
        self.text: list[str] = []
        self.skip = 0

    def flush(self):
        text, self.text = "".join(self.text), []
        if self.open:
            line = "; ".join(p for p in (" ".join(x.split()) for x in text.split("\n")) if p)
            if line:
                self.found.append((self.open[-1], line))

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "iframe"):
            self.skip += 1
        elif tag in BLOCKS:
            self.flush()
            self.open.append(tag)
        elif tag == "br":
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "iframe"):
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCKS and tag in self.open:
            self.flush()
            del self.open[len(self.open) - 1 - self.open[::-1].index(tag):]

    def handle_data(self, data):
        if self.open and not self.skip:
            self.text.append(data)


def blocks(body: str) -> list[tuple[str, str]]:
    """The article's text blocks (_Blocks), a line ending with ":" joined to the one it introduces ("Times:" and the
    times, "What will be unavailable:" and what)."""
    parser = _Blocks()
    try:
        parser.feed(body)
        parser.close()
    except Exception:          # noqa: BLE001 - a broken article is no text
        return []
    out: list[tuple[str, str]] = []
    lead = ""
    for tag, text in parser.found:
        text = html.unescape(text).strip()
        if lead:
            text, lead = f"{lead} {text}", ""
        if text.endswith(":") and tag not in HEADINGS:
            lead = text
            continue
        out.append((tag, text))
    if lead:
        out.append(("p", lead))
    return out


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def clip(text: str, limit: int = MAX_HIGHLIGHT) -> str:
    """At most limit characters, ending at the last sentence that fits (else at a word, with "…")."""
    if len(text) <= limit:
        return text
    cut = max((m.end() for m in re.finditer(r"[.!?][)\"'”’]?(?=\s)", text[:limit + 1])), default=0)
    if cut >= limit // 3:
        return text[:cut].strip()
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def highlights(body: str, summary: str = "", title: str = "") -> list[str]:
    """What the article says, in Nexon's words and order, chosen the same way every night: a short article's every
    paragraph, list line and heading (up to MAX_HIGHLIGHTS); a longer one's headings (its sections: the Cash Shop's
    sales, an interview's parts), from the top levels when there are too many; with no headings, its first
    paragraphs. Never the summary again, a picture's empty line, or the greeting and sign-off."""
    kept = [(tag, text) for tag, text in blocks(body)
            if not _same(text, summary) and not BOILERPLATE.match(text) and re.search(r"\w", text)]
    if len(kept) <= MAX_HIGHLIGHTS:
        return [clip(text) for _, text in kept]
    heads: list[tuple[str, str]] = []
    for tag, text in kept:
        if tag in HEADINGS and not _same(text, title) and not any(_same(text, h) for _, h in heads):
            heads.append((tag, text))
    levels = sorted({tag for tag, _ in heads})
    while len(heads) > MAX_HIGHLIGHTS and len(levels) > 1:       # "Male Hairs" under each coupon: the coupons
        levels.pop()
        heads = [(tag, text) for tag, text in heads if tag in levels]
    if heads:
        return [clip(text) for _, text in heads[:MAX_HIGHLIGHTS]]
    return [clip(text) for tag, text in kept if tag == "p"][:SHORT_HEADINGS]


def item(e: dict, nid: str, detail: dict | None, old: dict | None) -> dict:
    """One news.json item from Nexon's list entry and its article (detail; None when it couldn't be read: the
    highlights the item had stay, when they were Nexon's)."""
    title = " ".join(html.unescape(str(e.get("name") or "")).split())
    summary = " ".join(html.unescape(str(e.get("summary") or "")).split())
    url = article_url(e)
    if isinstance(detail, dict) and isinstance(detail.get("body"), str):
        points = highlights(detail["body"], summary, title)
    elif isinstance(old, dict) and str(old.get("url") or "").startswith(NEWS_PAGE + "/"):
        points = [str(h) for h in old.get("highlights") or []]       # (never a community site's, from an older KB)
    else:
        points = []
    category = str(e.get("category") or "").strip().lower()
    return {
        "id": nid, "date": str(e["liveDate"])[:10], "title": title, "summary": summary, "highlights": points,
        "commentary": "", "region": "gms", "official": True, "publisher": PUBLISHER,
        "source_url": url, "url": url, "tags": [category] if category else [],
        "mentions": mentions(title, summary, *points),
        "hash": _hash(title, summary),
    }


def stable_ids(old: dict[str, dict]) -> dict[int, str]:
    """Nexon's article id -> the id its item already has in news.json (an older KB's, from its source address or
    its pictures' article), so a player's "read" marks and the Hebrew made for it stay with it."""
    out: dict[int, str] = {}
    for oid, o in old.items():
        m = NEXON_ARTICLE.match(str(o.get("source_url") or ""))
        nx = o.get("nexon") if isinstance(o.get("nexon"), dict) else {}
        for n in (int(m.group(1)) if m else None, nx.get("id")):
            if isinstance(n, int):
                out.setdefault(n, oid)
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
        # the article's body: its highlights (and an older KB's note), when translated from the current English
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
    forward both failed): the next night rewrites it even when the news list can't be fetched and nothing is
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
    """The article's thumbnail on Nexon's media server (its imageThumbnail), else None."""
    url = str(e.get("imageThumbnail") or "").strip()
    if url.startswith("/media/"):
        url = NEXON + url
    return url if NEXON_MEDIA.fullmatch(url) else None


def add_cover(i: dict, src: str | None, old: dict | None, kb: Path, fetch_bytes) -> None:
    """The item's cover picture fields (image, image_w, image_h, image_src) when there is one."""
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
    """The English an item shows: what its pictures must be named in, in the order they come. The title last: it
    names things in short ("AP and SP Reset Scrolls" put the SP Reset Scroll first)."""
    return "\n".join([i.get("summary") or "", *(i.get("highlights") or []), i.get("commentary") or "",
                      i.get("title") or ""])


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


def article_json(fetch, nid: int) -> dict | None:
    """Nexon's article {id, name, summary, body, ...}, or None when it couldn't be read (then a pause)."""
    try:
        page = fetch(NEXON_API.format(id=nid))
        data = json.loads(page) if page else None
    except Exception:          # noqa: BLE001 - one article down never stops the news
        data = None
    time.sleep(PAUSE)
    return data if isinstance(data, dict) and isinstance(data.get("body"), str) else None


_ASK = object()


def add_nexon(i: dict, old: dict | None, kb: Path, fetch, fetch_bytes, article=_ASK) -> None:
    """i["nexon"]: the pictures of Nexon's own article (nexon_pictures), for an item Nexon published. One request
    for the article when the item's text or address changed since the night that matched its pictures, none when
    the caller already has it (article: its JSON, or None when it couldn't be read); the pictures already there are
    kept. A failure keeps what there was."""
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
    data = article_json(fetch, int(m.group(1))) if article is _ASK else article
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
    new, changed or gone (0 when nothing changed). A failed fetch or an unreadable list keeps the news.json there
    is: a site hiccup must not wipe the news, or the next good night announce all of it again.

    fetch_bytes(url) -> bytes | None downloads the pictures (add_cover, add_nexon); without it the items keep the
    pictures they have and get no new ones."""
    cleaned = clean_hebrew(kb)
    path = kb / "news.json"
    try:
        old = {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8")).get("items", [])}
    except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError):
        old = {}
    try:
        text = fetch(NEWS_LIST)
    except Exception:          # noqa: BLE001 - Nexon down or refusing tonight: the news there is stay
        text = None
    if not text:
        print("news: Nexon's news list could not be fetched; keeping the news there is", flush=True)
        return cleaned
    try:
        found = selected(entries(text))
        if not found:
            raise NewsError(f"no Classic World news since {SINCE} in Nexon's list")
    except NewsError as e:
        print(f"news: {e}; keeping the news there is", flush=True)
        return cleaned
    he = translations(kb=kb)
    ids = stable_ids(old)
    items = []
    for e in found:
        nid = ids.get(e["id"], f"nexon-{e['id']}")
        prev = old.get(nid)
        article = article_json(fetch, e["id"])
        if article is None:
            print(f"news: Nexon's article {e['id']} unreadable; keeping its highlights", flush=True)
        i = item(e, nid, article, prev)
        _with_he(i, he)
        add_cover(i, cover_src(e), prev, kb, fetch_bytes)
        add_nexon(i, prev, kb, fetch, fetch_bytes, article)
        items.append(i)
    items.sort(key=lambda i: (i["date"], i["id"]), reverse=True)
    changed = sum(1 for i in items if old.get(i["id"]) != i)
    removed = len(old.keys() - {i["id"] for i in items})
    pruned = prune_pictures(kb, items)            # (a picture deleted is a change too: the KB gets lighter)
    if changed or removed:
        path.write_text(json.dumps({"source": "Nexon (nexon.com/maplestory/news)",
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
    import scrape_meowdb            # (its polite fetch, for Nexon's site too)
    update(scrape_meowdb.KB, scrape_meowdb.fetch, lambda url: scrape_meowdb.fetch(url, binary=True))
