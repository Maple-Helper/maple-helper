"""Build the guide library shipped with the app: assets/guides/en/<slug>.json + assets/guides/img/.

The knowledge base keeps each guide as flat text (good for answering questions, but its headings,
tables and pictures are gone). This tool reads the guide pages themselves and keeps their structure:
headings, paragraphs, lists, tables, notes and the pictures (character art, skill and item icons,
monster sprites, skill animations), so the in-app reader looks like a real guide.

Blocks (one dict each, text uses **bold**, [[img:<file>]] for an inline icon, "\\n" for a line break):
  {"h2": text} {"h3": text} {"p": text} {"note": text} {"ul": [text]} {"ol": [text]}
  {"table": [[cell, ...], ...]}            first row is the header
  {"img": file, "w": px, "h": px, "cap": text}
  {"guide": slug, "text": title}           a link to another guide

Translations (assets/guides/<lang>/<slug>.json) have the same blocks with the text translated, and
"source_hash" = the English file's hash, so the reader knows when the English guide changed.

Run: python tools/build_guides.py [slug ...]     (all guides in data/kb when no slug is given)
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "assets" / "guides"
SITE = "https://meowdb.com"
UA = "Mozilla/5.0 (Maple Helper guide builder; https://github.com/Maple-Helper/maple-helper)"
MAX_W = 360          # wide pictures are scaled down to the reader's width
ICON_MAX = 40        # an image this small inside text is an inline icon

VOID = {"img", "br", "hr", "input", "meta", "link", "source", "wbr", "line", "rect", "path", "circle", "col"}
BLOCK = {"p", "pre", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "div", "section", "article", "figure", "header",
         "details", "summary", "aside", "nav", "blockquote", "li", "dl", "dt", "dd", "figcaption", "main", "footer"}
SKIP_TAGS = {"script", "style", "svg", "button", "nav", "noscript", "aside", "form", "input", "select", "iframe"}
SKIP_CLASSES = ("correction-loop", "toc", "button-row")
NOTE_CLASSES = ("callout", "formula", "source-note", "checkpoint")


class Node:
    __slots__ = ("tag", "attrs", "parent", "kids")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids = tag, dict(attrs), parent, []

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("root", {}, None)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.kids.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, d):
        self.cur.kids.append(d)


def find(n: Node, pred) -> Node | None:
    for k in n.kids:
        if isinstance(k, Node):
            if pred(k):
                return k
            r = find(k, pred)
            if r:
                return r
    return None


def plain(n) -> str:
    if isinstance(n, str):
        return n
    if n.tag in SKIP_TAGS:
        return ""
    return "".join(plain(k) for k in n.kids)


def squash(s: str) -> str:
    s = re.sub(r"[ \t\r\f\v ]+", " ", s)
    s = re.sub(r" *\n[\n ]*", "\n", s)
    # "**a** **b**" -> "**a b**", but never across a line break: "= **60**\n**Basic attack MAX**" stays two
    # lines (joined, it read "= **60Basic attack MAX**")
    s = re.sub(r"\*\*[ \t]*\*\*", "", s)
    s = re.sub(r"\s+([,.;:!?)])", r"\1", s)     # "Razor ." -> "Razor."
    s = re.sub(r"\(\s+", "(", s)
    return s.strip(" \n")


def hidden(n: Node) -> bool:
    c = n.classes
    if n.attrs.get("aria-hidden") == "true" or "data-nitro-ad" in n.attrs:
        return True
    if "sm:hidden" in c:                     # the phone-only copy of a table
        return True
    if "hidden" in c and not any(x.startswith(("sm:", "md:", "lg:")) for x in c):
        return True
    ident = n.attrs.get("id") or ""
    return "banner" in ident or any(s in x for x in c for s in SKIP_CLASSES)


class PictureMissing(Exception):
    """A picture of the guide didn't download: the guide keeps its shipped file this time."""


class Images:
    """Downloads each picture once, scales it, stores it as PNG named by its URL's hash."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.used: set[str] = set()

    def get(self, src: str, max_w: int = MAX_W) -> tuple[str, int, int] | None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QImageReader
        url = src if src.startswith("http") else SITE + src
        name = hashlib.sha1(f"{url}|{max_w}".encode()).hexdigest()[:14] + ".png"
        path = self.folder / name
        if not path.exists():
            try:
                data = fetch(url, binary=True)
            except Exception as e:
                # a hiccup used to drop the figure in silence (and mark the Hebrew guide outdated): the guide waits
                raise PictureMissing(f"{url}: {e}") from e
            from PySide6.QtCore import QBuffer, QByteArray
            buf = QBuffer()
            buf.setData(QByteArray(data))
            reader = QImageReader(buf)
            frames = reader.imageCount()
            if frames > 2:                      # an animation: its middle frame shows the skill best
                reader.jumpToImage(frames // 2)
            img: QImage = reader.read()
            if img.isNull():
                return None
            if img.width() > max_w:
                img = img.scaledToWidth(max_w, Qt.SmoothTransformation)
            img.save(str(path), "PNG")
        img = QImage(str(path))
        self.used.add(name)
        return name, img.width(), img.height()


def fetch(url: str, binary: bool = False):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            time.sleep(0.3)
            return data if binary else data.decode("utf-8", errors="replace")
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


class Converter:
    def __init__(self, images: Images, known: set[str] | None = None):
        self.images = images
        self.known = known         # the guides that exist (KB guide slugs); None = keep every link
        self.blocks: list[dict] = []
        self.started = False       # content starts after the <h1>
        self.stopped = False       # ...and ends at "More guides"

    # inline text with **bold**, icons and line breaks
    def inline(self, n) -> str:
        if isinstance(n, str):
            return re.sub(r"\s+", " ", n)        # line breaks in the HTML source are just spaces
        if n.tag in SKIP_TAGS:
            return ""
        if hidden(n):
            # a separator hidden from screen readers still parts the words on screen: "<time>October 21,
            # 2026</time><span aria-hidden> · </span>Starts" was "October 21, 2026Starts"
            sep = plain(n) if n.attrs.get("aria-hidden") == "true" else ""
            return sep if sep.strip() and not re.search(r"\w", sep) else (" " if sep else "")
        if n.tag == "br":
            return "\n"
        if n.tag == "pre":
            # a formula block: its own line breaks are the formula's lines, and it is a block of its own
            # ("... x 0.8</pre><pre>MIN = ..." read "x 0.8MIN = ...")
            return "\n" + "".join(k if isinstance(k, str) else self.inline(k) for k in n.kids) + "\n"
        if n.tag == "img":
            return self.icon(n)
        inner = "".join(self.inline(k) for k in n.kids)
        if n.tag in ("strong", "b") and inner.strip() and "\n" not in inner.strip():
            lead, core, trail = re.match(r"(\s*)(.*?)(\s*)$", inner, re.S).groups()
            return f"{lead}**{core.strip()}**{trail}"
        if n.tag in BLOCK:
            return "\n" + inner + "\n"
        if n.tag in ("span", "small"):
            return " " + inner + " "             # "**+0.50**<span>Base ACC</span>" keeps a space
        return inner

    def icon(self, n: Node) -> str:
        src = n.attrs.get("src") or ""
        if not src or "/emojis/" in src:
            return ""
        if "/classes/renders/" in src:
            return ""                 # the hero picture, shown at the top
        got = self.images.get(src, max_w=ICON_MAX)
        return f"[[img:{got[0]}]]" if got else ""

    def is_inline_only(self, n: Node) -> bool:
        return not find(n, lambda k: k.tag in BLOCK and not hidden(k))

    def add(self, kind: str, value):
        if isinstance(value, str):
            value = squash(value)
            if not value or not re.search(r"\w", value.replace("[[img:", "")):
                if kind != "p" or "[[img:" not in value:
                    return
        if self.started and not self.stopped:
            self.blocks.append({kind: value})

    def link(self, href: str, title: str):
        """A link to another guide, kept only when that guide exists: the reader can't open any other
        ("best-buy-shop-efficiency" is a site tool, not a guide, and the link did nothing)."""
        slug = href.rstrip("/").split("/")[-1].split("#")[0]
        if self.started and not self.stopped and title and (self.known is None or slug in self.known):
            self.blocks.append({"guide": slug, "text": title})

    def walk(self, n: Node):
        for k in n.kids:
            if self.stopped:
                return
            if isinstance(k, str):
                if k.strip():
                    self.add("p", k)
                continue
            self.node(k)

    def node(self, n: Node):
        t = n.tag
        if t in SKIP_TAGS or hidden(n):
            return
        if t == "h1":
            self.started = True
            return
        if not self.started:
            self.walk(n)
            return
        text = squash(plain(n))
        if t in ("p", "div", "strong", "span") and text == "More guides":
            self.stopped = True
            return
        if t in ("h2", "h3", "h4"):
            self.add("h2" if t == "h2" else "h3", plain(n))
            return
        if t == "a" and (h := find(n, lambda k: k.tag in ("h3", "h4", "p"))) is not None \
                and "/guides/" in (n.attrs.get("href") or ""):
            self.link(n.attrs["href"], squash(plain(find(n, lambda k: k.tag in ("h3", "h4")) or h)))
            return
        if t == "a" and "button-link" in n.classes:
            href = n.attrs.get("href") or ""
            if "/guides/" in href:
                self.link(href, squash(plain(n)))
            return
        if t in ("ul", "ol"):
            items = [squash(self.inline(li)) for li in n.kids if isinstance(li, Node) and li.tag == "li"
                     and not hidden(li)]
            items = [i for i in items if i]
            if items:
                self.add(t, items)
            return
        if t == "table":
            self.table(n)
            return
        if t == "img":
            self.picture(n, None)
            return
        if t == "figure":
            img = find(n, lambda k: k.tag == "img")
            cap = find(n, lambda k: k.tag == "figcaption")
            # the caption as inline text (its spans and lines kept apart: "Skill Lv1" / "500 x 300 px" were glued
            # into "Skill Lv1500 x 300 px"), then any other text of the figure ("250 px left and right, ...")
            def other(k) -> bool:
                return isinstance(k, str) or (k is not cap and k.tag != "img" and
                                              not find(k, lambda x: x is cap or x.tag == "img"))
            parts = ([self.inline(cap)] if cap is not None else []) + [self.inline(k) for k in n.kids if other(k)]
            text = squash("\n".join(p for p in parts if p.strip())) or None
            if img is not None:
                self.picture(img, text)
            elif text:
                self.add("p", text)
            return
        if "link-row" in n.classes:
            # buttons to the site's tools ("Open Accuracy Simulator") go; links to other guides stay
            for a in iter_tag(n, "a"):
                href = a.attrs.get("href") or ""
                if "/guides/" in href:
                    self.link(href, squash(plain(a)))
            return
        if t in BLOCK and text == "" and (img := find(n, lambda k: k.tag == "img")) is not None \
                and not find(n, lambda k: k.tag == "img" and k is not img):
            self.picture(img, None)      # a box holding just one picture (character art)
            return
        if t in ("strong", "b", "em", "i", "span", "a", "small", "code"):
            self.add("p", self.inline(n))    # inline text sitting directly in a box ("Pros")
            return
        if any(c.startswith(NOTE_CLASSES) for c in n.classes):
            self.note(n)
            return
        if t in ("p", "summary", "dt", "dd", "blockquote", "figcaption") or (t in BLOCK and self.is_inline_only(n)):
            self.add("p", self.inline(n))
            return
        self.walk(n)

    def note(self, n: Node):
        self.add("note", self.inline(n))

    def picture(self, img: Node, cap: str | None):
        src = img.attrs.get("src") or ""
        if not src or "/emojis/" in src:
            return
        if "/classes/renders/" in src:
            return
        try:
            w = int(img.attrs.get("width") or 0)
        except ValueError:
            w = 0
        got = self.images.get(src, max_w=min(MAX_W, w) if w else MAX_W)
        if not got:
            return
        name, pw, ph = got
        if pw <= ICON_MAX and ph <= ICON_MAX:
            self.add("p", f"[[img:{name}]]")
            return
        b = {"img": name, "w": pw, "h": ph}
        alt = squash(cap or "")
        if alt:
            b["cap"] = alt
        if self.started and not self.stopped:
            self.blocks.append(b)

    def table(self, n: Node):
        # a full-width sub-heading row (one cell with colspan) stays a shorter row: the reader spans its last
        # cell over the rest (maplehelper/guides.py), and translators keep the same row shape
        rows = []
        for tr in iter_tag(n, "tr"):
            cells = [squash(self.inline(c)).replace("\n", " ") for c in tr.kids
                     if isinstance(c, Node) and c.tag in ("td", "th") and not hidden(c)]
            if any(cells):
                rows.append(cells)
        if rows:
            self.add("table", rows)


def iter_tag(n: Node, tag: str):
    for k in n.kids:
        if isinstance(k, Node) and not hidden(k):
            if k.tag == tag:
                yield k
            else:
                yield from iter_tag(k, tag)


def convert(page_html: str, images: Images, known: set[str] | None = None) -> dict:
    tree = Tree()
    tree.feed(page_html)
    main = find(tree.root, lambda k: k.tag == "main") or tree.root
    h1 = find(main, lambda k: k.tag == "h1")
    title = squash(plain(h1)) if h1 is not None else ""
    c = Converter(images, known)
    c.walk(main)
    blocks = merge(c.blocks)
    hero = None
    header = find(main, lambda k: k.tag == "header")
    art = header is not None and find(header, lambda k: k.tag == "img" and "/classes/renders/" in (k.attrs.get("src") or ""))
    if art:                           # the class art beside the title (not the site menu's little icons)
        got = images.get(art.attrs["src"], max_w=130)
        hero = got[0] if got else None
    while blocks and "img" in blocks[0]:
        blocks.pop(0)                 # decoration beside the title (a monster sprite)
    intro = ""
    if blocks and "p" in blocks[0] and "[[img:" not in blocks[0]["p"]:
        intro = blocks.pop(0)["p"]
    if not intro:
        m = re.search(r'<meta name="description" content="([^"]*)"', page_html)
        intro = squash(html.unescape(m.group(1))) if m else ""
    minutes = re.search(r"(\d+) min read", page_html)
    return {"title": title, "intro": intro, "hero": hero, "minutes": int(minutes.group(1)) if minutes else None,
            "blocks": blocks}


# the labels of the site's live widgets (a countdown, the DPS charts' "Target matchup" switch): the reader
# doesn't carry the widgets, so their labels alone would tell players to tap bars that aren't there
CHART_LABELS = {"Days", "Hours", "Minutes", "Seconds", "Target matchup"}


def merge(blocks: list[dict]) -> list[dict]:
    """Icon-only paragraphs join the next paragraph ("[[img:x]] Power Strike"); repeats are dropped,
    and so is the "See also" list of site pages at the end."""
    out: list[dict] = []
    skip_list = False
    for b in blocks:
        if b.get("p") == "See also":
            skip_list = True
            continue
        if skip_list and ("ul" in b or "ol" in b):
            skip_list = False
            continue
        skip_list = False
        p = b.get("p", "")
        if p in ("Loading…", "Loading..."):
            # a list the browser fills ("Possible Contents / Community sourced / Items players have received ...
            # / Loading…"): the reader would show its heading over a "Loading" that never ends
            if len(out) >= 2 and out[-2].get("p") == "Community sourced":
                del out[-2:]
            elif out and out[-1].get("p") == "Community sourced":
                del out[-1:]
            if out and ("h2" in out[-1] or "h3" in out[-1]):
                out.pop()
            continue
        if p in CHART_LABELS or re.fullmatch(r"(-- \w+ ?)+", p) or p.startswith(("Tap a milestone", "Tap a bar")):
            continue                  # a live countdown / interactive chart's controls, meaningless without it
        if out and re.fullmatch(r"[\d.,]+( ?(px|%))?", p) and "p" in out[-1] and len(out[-1]["p"]) < 60:
            out[-1] = {"p": f"{out[-1]['p']}: **{p}**"}     # a bar chart's label and value
            continue
        if out and "p" in b and "p" in out[-1] and re.fullmatch(r"(\[\[img:[^\]]+\]\]\s*)+", out[-1]["p"]):
            out[-1] = {"p": out[-1]["p"] + " " + b["p"]}
            continue
        if out and b == out[-1]:
            continue
        out.append(b)
    return out


def content_hash(guide: dict) -> str:
    body = json.dumps([guide.get("title"), guide.get("intro"), guide.get("blocks")], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def _bare(text: str) -> str:
    """Text reduced to its letters and digits: markup, icons, punctuation and spacing differ between the
    reader's blocks and the KB's flat copy of the same page."""
    text = re.sub(r"\[\[img:[^\]]*\]\]", "", text)
    return re.sub(r"[\W_]+", "", text).lower()


def guide_texts(guide: dict):
    yield guide.get("title") or ""
    yield guide.get("intro") or ""
    for b in guide.get("blocks", []):
        if "guide" in b:
            continue             # a link: its title is the other guide's
        for kind, v in b.items():
            if kind in ("img", "w", "h"):
                continue
            if isinstance(v, str):
                yield v
            elif kind == "table":
                yield from (cell for row in v for cell in row)
            else:
                yield from v


def kb_gaps(guide: dict, kb_page: str) -> list[str]:
    """The guide's texts that its KB page doesn't have. The KB is the app's only source of game facts and the
    live page can be newer than the KB snapshot: such text waits for a KB update instead of shipping."""
    page = _bare(kb_page)
    return [t for t in guide_texts(guide) if _bare(t) and _bare(t) not in page]


def guide_slugs() -> list[str]:
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase()
    return sorted(k.split("/", 1)[1] for k, e in kb.entities.items() if e.get("category") == "guide")


def main(argv: list[str]) -> None:
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase()
    known = set(guide_slugs())
    slugs = argv or sorted(known)
    images = Images(OUT / "img")
    (OUT / "en").mkdir(parents=True, exist_ok=True)
    held = []
    for slug in slugs:
        page = fetch(f"{SITE}/msclassic/guides/{slug}")
        try:
            g = convert(page, images, known)
        except PictureMissing as e:   # the shipped file stays as it is
            held.append(slug)
            print(f"{slug}: NOT written, a picture didn't download ({e}); run it again")
            continue
        gaps = kb_gaps(g, kb.page(f"guide/{slug}"))
        if gaps:          # the shipped file stays as it is
            held.append(slug)
            print(f"{slug}: NOT written, {len(gaps)} text(s) its KB page doesn't have (update data/kb first):")
            for t in gaps[:5]:
                print("   ", t[:120])
            continue
        g["source"] = f"{SITE}/msclassic/guides/{slug}"
        g["hash"] = content_hash(g)
        (OUT / "en" / f"{slug}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"{slug}: {len(g['blocks'])} blocks")
    if held:
        print(f"{len(held)} guide(s) kept as they were: {', '.join(held)}")
    elif not argv:   # a full build: drop pictures no guide uses any more
        for f in (OUT / "img").glob("*.png"):
            if f.name not in images.used:
                f.unlink()


if __name__ == "__main__":
    main(sys.argv[1:])
