"""The KB's guides as a small library: categories, "for you" picks, and a clean reading view.

The scraped pages carry the source site's menus and ads around the article; `parse` keeps the
article (title, intro, pros/cons, sections, tables) so the reader shows only the guide.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field

from . import bidi
from .store import ASSETS

TRANSLATIONS = ASSETS / "guides"     # <lang>/<slug>.json, translated once and shipped with the app
CATEGORIES = ["for_you", "classes", "leveling", "mechanics", "general"]
LEVELING = {"best-grind-maps-every-level", "exp-table-level-1-to-100", "hp-mp-gain-explained",
            "kerning-city-party-quest-kpq-guide", "beginners-guide-first-steps-in-maple-world",
            "forgotten-hollow-the-new-endgame-area"}
MECHANICS = {"explaining-the-damage-formula", "attack-speed-and-animation-times", "attacks-you-can-use-mid-jump",
             "spawn-engine-respawn-and-map-capacity", "speed-jump-and-movement", "weapon-reach", "class-dps-rankings"}
JUNK = ("Explore the database", "Items Monsters Maps", "Plan your character", "Your shortcuts", "Watchlist ›",
        "Free Market", "Guides Tier List", "[ Notice ]", "Ad blocked?", "Buy us a coffee", "Home / MS Classic",
        "← All guides")


@dataclass
class Guide:
    key: str
    title: str
    intro: str = ""
    minutes: int | None = None
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)   # (heading, lines)

    @property
    def slug(self) -> str:
        return self.key.split("/", 1)[1]


def category(key: str) -> str:
    slug = key.split("/", 1)[1]
    if slug.endswith("-class-guide"):
        return "classes"
    if slug in LEVELING:
        return "leveling"
    if slug in MECHANICS:
        return "mechanics"
    return "general"


def _norm(line: str) -> str:
    """Headings are written slightly differently in the contents ("and" / "&", capitals)."""
    return re.sub(r"\s+", " ", line.lower().replace("&", "and")).strip()


_CARD_META = re.compile(r"^\S.{0,40} \d+ min read\b")   # "Mechanics 5 min read · By Nia Meow", other guides' cards
_SITE_LINKS = ("Spot a ", "Use this build in Training Advisor", "Open Grummash's", "See also")


def _junk(line: str) -> bool:
    return (line in ("›", "Guides", "Contents") or line.startswith(JUNK) or line.startswith(_SITE_LINKS)
            or bool(_CARD_META.match(line)))


def own_minutes(page: str) -> int | None:
    """The guide's reading time: the first "N min read" before its contents, not a related card's."""
    head = page.split("\nContents\n", 1)[0]
    m = re.search(r"(\d+) min read", head)
    return int(m.group(1)) if m else None


# the mechanics pages' tab row, flattened onto the article's next line ("... Shop Item Efficiency Contents",
# "... Shop Item Efficiency Every EXP requirement ...")
_MECH_TABS = "Damage Formula Atk Speed Attack Styles HP/MP Gain EXP Table Spawn Rate Shop Item Efficiency "


def _unlabeled_toc(lines: list[str], start: int) -> tuple[int, int] | None:
    """A contents list the page doesn't title "Contents" (forgotten-hollow): a run of short heading-like
    lines, then the first of them again where its section starts. (first, end) of the run."""
    def short(ln: str) -> bool:
        return 0 < len(ln) <= 60 and not ln.endswith((".", ":", "!", "?")) and " | " not in ln
    for i in range(start, len(lines)):
        if not short(lines[i]):
            continue
        j = i + 1
        while j < len(lines) and short(lines[j]) and lines[j] != lines[i]:
            j += 1
        if j - i >= 3 and j < len(lines) and lines[j] == lines[i]:
            return i, j
    return None


def parse(key: str, page: str) -> Guide:
    lines = []
    for ln in (x.strip() for x in page.split("\n---", 2)[-1].splitlines()):
        if ln.startswith(_MECH_TABS):
            lines += ["", ln[len(_MECH_TABS):].strip()]     # the tabs are junk, the rest of the line is the article
        else:
            lines.append(ln)
    title = next((ln[2:] for ln in lines if ln.startswith("# ")), key)
    g = Guide(key, title)
    body_start = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), 0) + 1
    intro = next((ln for ln in lines[body_start:] if ln and not _junk(ln)), "")
    g.intro = intro
    g.minutes = own_minutes(page)

    # the contents list ends where its first heading starts again; a nested heading may share a
    # line with its parent ("Starting at level 30 Recommended citizenship")
    toc: list[str] = []
    if "Contents" in lines:
        i = lines.index("Contents") + 1
        while i < len(lines) and lines[i] and not (toc and _norm(toc[0]).startswith(_norm(lines[i]))):
            toc.append(_norm(lines[i]))
            i += 1
        pre, body = lines[body_start:lines.index("Contents")], lines[i:]
    elif run := _unlabeled_toc(lines, body_start):
        toc = [_norm(ln) for ln in lines[run[0]:run[1]]]
        pre, body = lines[body_start:run[0]], lines[run[1]:]
    else:
        pre, body = [], lines[body_start + 1:]

    # pros / cons sit before the contents on class guides ("Pros" alone, or "Pros <first one>")
    bucket = None
    for ln in pre:
        head, _, rest = ln.partition(" ")
        if head in ("Pros", "Cons"):
            bucket = g.pros if head == "Pros" else g.cons
            if rest:
                bucket.append(rest)
        elif bucket is not None and ln and not _junk(ln):
            bucket.append(ln)

    def is_heading(ln: str) -> bool:
        n = _norm(ln)
        return any(t == n or t.startswith(n + " ") or t.endswith(" " + n) for t in toc)

    current: tuple[str, list[str]] | None = None
    for ln in body:
        if not ln or _junk(ln):
            continue
        if is_heading(ln):
            current = (ln, [])
            g.sections.append(current)
        elif current is not None:
            current[1].append(ln)
    if not g.sections and body:
        g.sections.append(("", [ln for ln in body if ln and not _junk(ln)]))
    # the article's own opening paragraph sits before the contents (exp-table: "Every EXP requirement in ...")
    lead = [ln for ln in pre if len(ln) > 60 and ln.endswith((".", "!", "?")) and not _junk(ln) and ln != g.intro]
    if lead and not (g.pros or g.cons) and g.sections and g.sections[0][0]:
        g.sections.insert(0, ("", lead))
    return g


def _cell(text: str, rtl: bool) -> str:
    """Escaped text; in a Hebrew guide, English names and numbers stay whole blocks."""
    # any Hebrew in it makes it a Hebrew line, even when it opens with an English name ("Warrior, ג'וב 1")
    return html.escape(bidi.plain(text, True) if rtl and bidi._RTL.search(text) else text)


def _table_html(rows: list[list[str]], rtl: bool = False) -> str:
    head, *body = rows
    attrs = " dir='rtl' align='right'" if rtl else ""
    cell = "<p dir='rtl' align='right' style='margin:0'>{}</p>" if rtl else "{}"     # Qt sets direction per paragraph
    return (f"<table cellspacing='0' cellpadding='4'{attrs}><tr>"
            + "".join(f"<th>{cell.format(_cell(c, rtl))}</th>" for c in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{cell.format(_cell(c, rtl))}</td>" for c in r) + "</tr>" for r in body)
            + "</table>")


def to_html(g: Guide, labels: dict, rtl: bool = False) -> str:
    """Readable HTML for QTextBrowser: headings, paragraphs, and real tables for "a | b | c" rows.
    A Hebrew guide reads right to left, with English game names kept as whole blocks."""
    side = " dir='rtl' align='right'" if rtl else ""
    out = []

    def para(text: str) -> str:
        return bidi.paragraph_html(text, "rtl" if rtl and bidi._RTL.search(text) else None)
    if g.intro:      # (no italics in Hebrew: Qt only slants the letters)
        intro = _cell(g.intro, rtl)
        out.append(f"<p{side}>{f'<b>{intro}</b>' if rtl else f'<i>{intro}</i>'}</p>")
    for name, items in ((labels["pros"], g.pros), (labels["cons"], g.cons)):
        if items:
            out.append(f"<h3{side}>{html.escape(name)}</h3><ul{side}>"
                       + "".join(f"<li>{_cell(i, rtl)}</li>" for i in items) + "</ul>")
    for heading, lines in g.sections:
        if heading:
            out.append(f"<h3{side}>{_cell(heading, rtl)}</h3>")
        table: list[list[str]] = []
        for ln in lines:
            if ln.strip() in (g.intro.strip(), g.title.strip()):
                continue        # guides without contents repeat their intro and title in the text
            if " | " in ln:
                # some tables open with an empty icon column: "| Skill | Class | ..."
                table.append([c.strip() for c in ln.strip().strip("|").split(" | ")])
                continue
            if table:
                out.append(_table_html(table, rtl))
                table = []
            out.append(para(ln))
        if table:
            out.append(_table_html(table, rtl))
    return "\n".join(out)


def content_hash(g: Guide) -> str:
    """Hash of what the reader shows (not the page's site banner or other guides' cards)."""
    body = json.dumps([g.title, g.intro, g.pros, g.cons, g.sections], ensure_ascii=False)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def search_text(key: str, page: str) -> str:
    """The guide's own words, for search (not the site menus around it)."""
    g = parse(key, page)
    return " ".join([g.title, g.intro, *g.pros, *g.cons, *(h + " " + " ".join(ls) for h, ls in g.sections)])


def page_hash(page: str) -> str:
    return hashlib.sha1(page.encode("utf-8")).hexdigest()[:12]


def translation(key: str, lang: str) -> dict | None:
    """The shipped translation of a guide, or None (English is the original)."""
    if lang == "en":
        return None
    path = TRANSLATIONS / lang / f"{key.split('/', 1)[1]}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def localized(key: str, page: str, lang: str) -> tuple[Guide, bool, bool]:
    """(guide in the player's language when translated, translated?, English changed since the translation?)"""
    tr = translation(key, lang)
    if not tr:
        return parse(key, page), False, False
    g = Guide(key, tr.get("title") or key, tr.get("intro", ""), parse(key, page).minutes, tr.get("pros", []),
              tr.get("cons", []), [(s.get("heading", ""), s.get("lines", [])) for s in tr.get("sections", [])])
    return g, True, tr.get("source_hash") != content_hash(parse(key, page))


def title(key: str, fallback: str, lang: str) -> str:
    b = book(key, lang)
    if b and b.get("title") and (lang == "en" or b.get("lang") == lang):
        return b["title"]
    tr = translation(key, lang)
    return (tr or {}).get("title") or fallback


# the full guides (headings, tables, pictures), built by tools/build_guides.py ---------------------

IMAGES = TRANSLATIONS / "img"
_ICON = re.compile(r"\[\[img:([\w.-]+)\]\]")
NOTE_COLORS = {"light": {"note": "#FFF4E6", "head": "#F2F2F7", "line": "#E5E5EA", "link": "#C9620A"},
               "dark": {"note": "#3A2C1E", "head": "#2C2C2E", "line": "#3A3A3C", "link": "#FF9F43"}}


def _load(path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def book(key: str, lang: str) -> dict | None:
    """The full guide in the player's language (English when there's no translation), or None when
    this guide wasn't built. Adds "lang" (the language of the text) and "stale" (the English changed
    since the translation)."""
    slug = key.split("/", 1)[1]
    en = _load(TRANSLATIONS / "en" / f"{slug}.json")
    if not en:
        return None
    if lang != "en":
        tr = _load(TRANSLATIONS / lang / f"{slug}.json")
        if tr and tr.get("blocks"):
            return {**en, **tr, "hero": en.get("hero"), "minutes": en.get("minutes"), "lang": lang,
                    "stale": tr.get("source_hash") != en.get("hash")}
    return {**en, "lang": "en", "stale": False}


def book_text(b: dict) -> str:
    """Every word of a guide, for search."""
    parts = [b.get("title", ""), b.get("intro", "")]
    for blk in b.get("blocks", []):
        for k, v in blk.items():
            if k in ("img", "w", "h", "guide"):
                continue
            if isinstance(v, str):
                parts.append(v)
            elif k == "table":
                parts += [c for row in v for c in row]
            elif isinstance(v, list):
                parts += v
    return _ICON.sub(" ", " ".join(parts))


def _rich(text: str, rtl: bool, icon_px: int = 22) -> str:
    """Guide text -> HTML: **bold**, inline icons, line breaks; in a Hebrew guide, English names
    and numbers stay whole blocks (bidi)."""
    icons: list[str] = []

    def keep(m):
        icons.append(m.group(1))
        return chr(0xE000 + len(icons) - 1)     # a neutral private character the bidi pass leaves alone
    text = _ICON.sub(keep, text)
    if rtl:
        text = "\n".join(bidi.isolate_ltr_runs(ln) for ln in text.split("\n"))
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = out.replace("\n", "<br>")
    for i, name in enumerate(icons):
        src = (IMAGES / name).as_uri()
        out = out.replace(chr(0xE000 + i), f"<img src='{src}' height='{icon_px}' style='vertical-align: middle'> ")
    return out


def has_book(slug: str) -> bool:
    """A guide the reader can open: its English book is shipped."""
    return (TRANSLATIONS / "en" / f"{slug}.json").is_file()


def book_html(b: dict, mode: str = "light", rtl_ui: bool = False) -> str:
    """The reader's HTML (QTextBrowser): headings, paragraphs, notes, lists, tables, pictures,
    links to other guides (href="guide:<slug>")."""
    he = b.get("lang") != "en"
    col = NOTE_COLORS.get(mode, NOTE_COLORS["light"])

    def rtl_of(text: str) -> bool:
        return he and bool(bidi._RTL.search(text))

    def para(text: str, tag: str = "p", style: str = "margin: 4px 0 8px 0;") -> str:
        r = rtl_of(text)
        side = "dir='rtl' align='right'" if r else "dir='ltr' align='left'"
        return f"<{tag} {side} style='{style}'>{_rich(text, r)}</{tag}>"

    side = "dir='rtl' align='right'" if he else ""
    out = []
    if b.get("hero"):
        out.append(f"<img src='{(IMAGES / b['hero']).as_uri()}' align='{'left' if he else 'right'}' height='120'>")
    if b.get("intro"):
        r = rtl_of(b["intro"])
        # Hebrew has no italics (Qt slants the letters, a fake italic): a Hebrew intro stands out by weight instead
        intro = f"<span style='font-weight: 500;'>{_rich(b['intro'], r)}</span>" if r else f"<i>{_rich(b['intro'], r)}</i>"
        out.append(f"<p {'dir=rtl align=right' if r else ''} style='margin: 4px 0 10px 0;'>{intro}</p>")
    for blk in b.get("blocks", []):
        if "h2" in blk:
            out.append(para(blk["h2"], "h2", "margin: 18px 0 6px 0;"))
        elif "h3" in blk:
            out.append(para(blk["h3"], "h3", "margin: 12px 0 4px 0;"))
        elif "p" in blk:
            out.append(para(blk["p"]))
        elif "note" in blk:
            r = rtl_of(blk["note"])
            out.append(f"<table width='100%' cellpadding='10' cellspacing='0' style='margin: 6px 0 10px 0;'>"
                       f"<tr><td bgcolor='{col['note']}'><p {'dir=rtl align=right' if r else ''} style='margin:0'>"
                       f"{_rich(blk['note'], r)}</p></td></tr></table>")
        elif "ul" in blk or "ol" in blk:
            tag = "ul" if "ul" in blk else "ol"
            # a little air between the bullets: a run of long items read as one block
            items = "".join(f"<li {'dir=rtl align=right' if rtl_of(i) else ''} style='margin-bottom: 4px;'>"
                            f"{_rich(i, rtl_of(i))}</li>" for i in blk[tag])
            out.append(f"<{tag} {side} style='margin: 2px 0 8px 0;'>{items}</{tag}>")
        elif "table" in blk:
            rows = blk["table"]
            cells = []
            for n, row in enumerate(rows):
                tag, bg = ("th", f" bgcolor='{col['head']}'") if n == 0 else ("td", "")
                cells.append("<tr>" + "".join(
                    f"<{tag}{bg}><p {'dir=rtl align=right' if rtl_of(c) else ''} style='margin:0'>{_rich(c, rtl_of(c), 18)}</p></{tag}>"
                    for c in row) + "</tr>")
            out.append(f"<table {side} width='100%' cellspacing='0' cellpadding='5' border='1' style='border-color: {col['line']};"
                       f" border-style: solid; margin: 4px 0 10px 0;'>{''.join(cells)}</table>")
        elif "img" in blk:
            cap = f"<br><span style='font-size: small;'>{_rich(blk['cap'], rtl_of(blk['cap']))}</span>" if blk.get("cap") else ""
            out.append(f"<p align='center' style='margin: 6px 0 10px 0;'><img src='{(IMAGES / blk['img']).as_uri()}'"
                       f" width='{blk.get('w', 0)}' height='{blk.get('h', 0)}'>{cap}</p>")
        elif "guide" in blk:
            if not has_book(blk["guide"]):
                continue        # a card for a guide that wasn't built (the site's own pages): a link to nowhere
            r = rtl_of(blk["text"])
            arrow = "←" if he else "→"
            out.append(f"<p {'dir=rtl align=right' if r else ''} style='margin: 4px 0;'>"
                       f"<a href='guide:{html.escape(blk['guide'])}' style='color: {col['link']}; text-decoration: none;'>"
                       f"<b>{_rich(blk['text'], r)} {arrow}</b></a></p>")
    return "\n".join(out)


def text_of(key: str, lang: str) -> str:
    """All the translated text of a guide, for search."""
    tr = translation(key, lang)
    if not tr:
        return ""
    parts = [tr.get("title", ""), tr.get("intro", ""), *tr.get("pros", []), *tr.get("cons", [])]
    for s in tr.get("sections", []):
        parts += [s.get("heading", ""), *s.get("lines", [])]
    return " ".join(parts)


def all_guides(kb) -> list[dict]:
    """[{key, title, category, minutes}] for every guide in the KB, sorted by title."""
    out = []
    for key, e in kb.entities.items():
        if e.get("category") == "guide":
            out.append({"key": key, "title": e.get("name", key), "category": category(key),
                        "minutes": own_minutes(kb.page(key))})
    return sorted(out, key=lambda g: g["title"])


def for_you(kb, c) -> list[str]:
    """The guides that fit this character right now, most useful first."""
    from . import plan
    picks = []
    if c:
        picks.append(plan.class_guide(kb, c.base_class, c.job))
        picks.append(plan.class_guide(kb, c.base_class, c.base_class))
        if c.level < 15:
            picks.append("guide/beginners-guide-first-steps-in-maple-world")
        picks.append("guide/best-grind-maps-every-level")
        # KPQ opens at Lv. 21 with no level cap in Classic (kerning-city-party-quest-kpq-guide: "You unlock KPQ at
        # level 21. There doesn't appear to be any level cap"; the old game's cap of 30 is gone)
        if c.level >= 21:
            picks.append("guide/kerning-city-party-quest-kpq-guide")
        picks.append("guide/exp-table-level-1-to-100")
        # the Hollow opens at Lv. 39 (its guide), not 60; while the release guide calls it closed (Founder's Access,
        # 10-06) it's no pick for a player who can't get in
        from . import availability
        if c.level >= 39 and "Forgotten Hollow" not in availability.of(kb).closed_areas:
            picks.append("guide/forgotten-hollow-the-new-endgame-area")
    else:
        picks += ["guide/beginners-guide-first-steps-in-maple-world", "guide/best-grind-maps-every-level"]
    seen = []
    for k in picks:
        if k and kb.get(k) and k not in seen:
            seen.append(k)
    return seen
