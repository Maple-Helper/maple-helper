"""MapleStory Classic news in the app (news.py): the strip at the top of the chat when there is unread news, and the
News tab of the patch notes window.

Titles stay as published (English, one left-to-right block in a Hebrew line); the summary is in Hebrew when there
is a translation of its current text, else the English one under a Hebrew note that says so.

Pictures (news.cover / header / pictures / entities, from the KB's img/news): a card has NiaMeowDB's cover as a
thumbnail; an article has its picture under the title, the pictures of what it names from Nexon's announcement, and
the KB's own pictures of the items and monsters it names. A picture that is missing or can't be read is left out,
and the page is as it was without pictures.
"""
from __future__ import annotations

import html
import re

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .. import bidi, news
from ..osapi import open_url
from . import theme
from .controls import FlowLayout
from .widgets import fit_picture, zoom_on_hover

SHOWN = 30          # cards per section; the rest are a link away on MeowDB
THUMB = (88, 50)        # a card's cover thumbnail (the covers are 540x304), as wide as a guide card's
RADIUS = 10             # a picture's corners
TILE_W, TILE_PIC = 92, 48     # a named picture's tile under an article


def load(path) -> QPixmap:
    """The picture, or a null one when there is none or Qt can't decode it (the page then goes without it)."""
    return QPixmap(str(path)) if path else QPixmap()


def rounded(pm: QPixmap, radius: float = RADIUS) -> QPixmap:
    """The picture with rounded corners, drawn at its own device pixels (fit_picture's): an antialiased edge, sharp
    on HiDPI."""
    if pm.isNull():
        return pm
    dpr = pm.devicePixelRatio() or 1.0
    src = QPixmap(pm)
    src.setDevicePixelRatio(1)
    out = QPixmap(pm.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pm.width(), pm.height()), radius * dpr, radius * dpr)
    p.fillPath(path, QBrush(src))
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


class Picture(QWidget):
    """A news picture with rounded corners. With a size: a card's thumbnail, the picture fitted and centered in it.
    Without: across the column under an article's title, as wide as the column and at most GROW times its own
    size, from the reading side."""

    GROW = 1.5

    def __init__(self, pm: QPixmap, size: tuple[int, int] | None = None):
        super().__init__()
        self._pm = pm
        self._fixed = bool(size)
        self._cache: tuple | None = None
        if size:
            self.setFixedSize(*size)
        else:
            sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            sp.setHeightForWidth(True)
            self.setSizePolicy(sp)

    def _fit(self, w: int, h: int | None = None) -> QSize:
        pw, ph = max(1, self._pm.width()), max(1, self._pm.height())
        k = min(max(1, w) / pw, self.GROW) if h is None else min(w / pw, h / ph)
        return QSize(max(1, round(pw * k)), max(1, round(ph * k)))

    def hasHeightForWidth(self) -> bool:
        return not self._fixed

    def heightForWidth(self, w: int) -> int:
        return self._fit(w).height()

    def sizeHint(self) -> QSize:
        return self.size() if self._fixed else self._fit(min(self._pm.width(), 440))

    def minimumSizeHint(self) -> QSize:
        return self.minimumSize() if self._fixed else QSize(60, self.heightForWidth(60))

    def paintEvent(self, e):
        s = self._fit(self.width(), self.height() if self._fixed else None)
        key = (s.width(), s.height(), self.devicePixelRatioF())
        if not self._cache or self._cache[0] != key:
            self._cache = (key, rounded(fit_picture(self._pm, s.width(), s.height(), self)))
        if self._fixed:
            x, y = (self.width() - s.width()) // 2, (self.height() - s.height()) // 2
        else:
            x, y = (self.width() - s.width() if self.layoutDirection() == Qt.RightToLeft else 0), 0
        QPainter(self).drawPixmap(x, y, self._cache[1])


def picture(path, size: tuple[int, int] | None = None, name: str = "NewsPicture") -> Picture | None:
    """A Picture of the file, or None when there is none or it can't be decoded (no hole in the page)."""
    pm = load(path)
    if pm.isNull():
        return None
    w = Picture(pm, size)
    w.setObjectName(name)
    return w


def tile(name: str, path, rtl: bool) -> QFrame | None:
    """A named picture under an article: the picture with its name under it (in English, as the game writes it),
    large on hover like the quests' pictures."""
    pm = load(path)
    if pm.isNull():
        return None
    box = QFrame(objectName="NewsTile")
    box.setFixedWidth(TILE_W)
    col = QVBoxLayout(box)
    col.setContentsMargins(2, 4, 2, 4)
    col.setSpacing(4)
    pic = QLabel()
    pic.setFixedSize(TILE_PIC, TILE_PIC)
    pic.setAlignment(Qt.AlignCenter)
    pic.setPixmap(fit_picture(pm, TILE_PIC, TILE_PIC, pic, trim=True))
    zoom_on_hover(pic, path, name, height=min(128, max(64, pm.height() * 2)))
    col.addWidget(pic, 0, Qt.AlignHCenter)
    lb = QLabel(bidi.ltr_name(name, rtl), objectName="CardSub")
    lb.setWordWrap(True)
    lb.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    col.addWidget(lb)
    # the tile's height for its name's lines: the flow layout places it by its size hint, which a wrapped label
    # doesn't give (a two-line name was drawn over the picture and a three-line one cut)
    lb.ensurePolished()
    m = col.contentsMargins()
    box.setFixedHeight(m.top() + TILE_PIC + col.spacing() + lb.heightForWidth(TILE_W - m.left() - m.right())
                       + m.bottom())
    box.setAccessibleName(name)
    return box


def tiles(rows: list[tuple[str, object]], rtl: bool) -> QWidget | None:
    """The named pictures as a grid from the reading side, wrapping at the panel's width; None without any."""
    made = [w for w in (tile(n, p, rtl) for n, p in rows) if w]
    if not made:
        return None
    grid = QWidget()
    grid.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
    flow = FlowLayout(grid, spacing=6)
    tallest = max(w.maximumHeight() for w in made)
    for w in made:
        w.setFixedHeight(tallest)       # one height: the pictures of a line on one level, the names under them
        flow.addWidget(w)
    return grid


# a megaphone: Segoe Fluent Icons' when the app has the icon font, else a plain-text one (theme.SYMBOL_ICONS' way)
GLYPH, SYMBOL = "\ue789", "\U0001F4E3\ufe0e"


def glyph() -> str:
    return SYMBOL if theme.ICON.get("info") == theme.SYMBOL_ICONS.get("info") else GLYPH


def sentences(text: str, rtl: bool) -> str:
    """A Hebrew summary for a plain label, sentence by sentence: bidi.plain over the whole of it grouped the end of one
    sentence with the English start of the next ("…לבל 100. Forgotten Hollow נשאר…" showed "Forgotten Hollow"
    before the "100.")."""
    return " ".join(bidi.plain(x, rtl) for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x)


def _align(rtl: bool):
    return (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute


def _title(i: dict, rtl: bool) -> str:
    """The Hebrew title as a Hebrew line (English names inside it kept whole); an English one as one
    left-to-right block, right-aligned in Hebrew."""
    he = news.title(i, "he" if rtl else "en")
    return sentences(he, rtl) if he != i["title"] else bidi.ltr_name(i["title"], rtl)


def set_title(lb: QLabel, i: dict, rtl: bool) -> None:
    """A translated Hebrew title as right-to-left rich text, sentence by sentence as sentences(): as plain text a
    wrapped title kept the space before its English name at the end of line 1, which then started ~6 px in from
    line 2 and the summary (VIS-14; guides.set_title does the same)."""
    he = news.title(i, "he" if rtl else "en")
    if rtl and he != i["title"]:
        body = " ".join(bidi.isolate_ltr_runs(x) for x in re.split(r"(?<=[.!?])\s+", he.strip()) if x)
        lb.setTextFormat(Qt.RichText)
        lb.setText(f'<p dir="rtl" align="right" style="margin:0;">{html.escape(body, quote=False)}</p>')
    else:
        lb.setTextFormat(Qt.PlainText)
        lb.setText(_title(i, rtl))


def title_label(i: dict, rtl: bool, name: str = "CardName") -> QLabel:
    """The title: in Hebrew when translated (news.title), else as published."""
    lb = QLabel(objectName=name)
    lb.setWordWrap(True)
    set_title(lb, i, rtl)
    lb.setAlignment(_align(rtl))
    return lb


def chip(text: str, rtl: bool, tip: str = "", name: str = "SourceTag") -> QLabel:
    lb = QLabel(bidi.plain(text, rtl), objectName=name)
    lb.setAlignment(Qt.AlignCenter)
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
    if tip:
        lb.setToolTip(tip)
    lb.setAccessibleName(f"{text}: {tip}" if tip else text)
    return lb


def source_chip(t, i: dict) -> QLabel:
    """"Official · Nexon" for the game's operator, "Community · NiaMeowDB" for analysis and press (as sources.py
    tags data: official vs community)."""
    who = i.get("publisher") or "NiaMeowDB"
    if i.get("official"):
        return chip(t("news_official", who=who), t.rtl, t("news_official_tip", who=who))
    return chip(t("news_community", who=who), t.rtl, t("news_community_tip"))


class NewsStrip(QFrame):
    """The newest unread news at the top of the chat: tap to read it (the News tab), ✕ to dismiss that item."""

    opened = Signal()
    dismissed = Signal(str)

    def __init__(self):
        super().__init__(objectName="InfoNote")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 6, 6)
        row.setSpacing(8)
        self.icon = QLabel(glyph(), objectName="InfoIcon")
        row.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.head = QLabel(objectName="InfoText")
        self.head.setStyleSheet("font-weight: 600;")
        col.addWidget(self.head)
        self.title = QLabel(objectName="InfoText")
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        row.addLayout(col, 1)
        self.close_btn = theme.dismiss_button()
        self.close_btn.clicked.connect(lambda: self.dismissed.emit(self._item["id"]) if self._item else None)
        row.addWidget(self.close_btn, 0, Qt.AlignTop)
        self.setCursor(Qt.PointingHandCursor)
        # Tab to it and Enter or Space opens it, like the tip strip
        self.setFocusPolicy(Qt.TabFocus)
        self.setProperty("focus_ring", True)
        self._item = None
        self.hide()

    def show_news(self, unread: list[dict], t) -> None:
        self._item = unread[0] if unread else None
        self.setVisible(self._item is not None)
        if not self._item:
            return
        i, rtl = self._item, t.rtl
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        head = t("news_strip_head", date=news.short_date(i, rtl))
        if len(unread) > 1:
            head += " · " + t("news_strip_more", n=len(unread) - 1)
        self.head.setText(bidi.plain(head, rtl))
        set_title(self.title, i, rtl)
        for lb in (self.head, self.title):
            lb.setAlignment(_align(rtl))
        text, _ = news.summary(i, t.lang)
        self.setToolTip(sentences(text, rtl) if text else "")
        self.close_btn.setToolTip(t("news_dismiss"))
        self.close_btn.setAccessibleName(t("news_dismiss"))
        self.setAccessibleName(f"{head}: {news.title(i, t.lang)}")
        self.setAccessibleDescription(t("news_open_a11y"))

    def keyPressEvent(self, e):
        if self._item and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not e.modifiers():
            self.opened.emit()
            return
        super().keyPressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._item and e.button() == Qt.LeftButton:
            self.opened.emit()
            return
        super().mouseReleaseEvent(e)


class NewsCard(QFrame):
    """One news item: chips (source, region, new), the title (the cover's thumbnail at its side when the KB has it),
    date, summary, links. on_open(item): the full article in the app ("Read the article"); without it the card
    links to MeowDB."""

    def __init__(self, t, i: dict, unread: bool = False, on_open=None, kb=None):
        super().__init__(objectName="Card")
        rtl = t.rtl
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        col = QVBoxLayout(self)
        col.setContentsMargins(12, 10, 12, 8)
        col.setSpacing(4)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        if unread:
            chips.addWidget(chip(t("news_new"), rtl, name="UpdatedTag"))
        chips.addWidget(source_chip(t, i))
        if i.get("region") in ("cms", "tms"):
            chips.addWidget(chip(t(f"news_region_{i['region']}"), rtl))
        date = QLabel(bidi.plain(news.short_date(i, rtl), rtl), objectName="CardSub")
        chips.addWidget(date)
        chips.addStretch(1)
        col.addLayout(chips)
        thumb = picture(news.cover(kb, i), THUMB, "NewsThumb") if kb is not None else None
        if thumb:
            head = QHBoxLayout()
            head.setSpacing(10)
            head.addWidget(thumb, 0, Qt.AlignTop)
            head.addWidget(title_label(i, rtl), 1)
            col.addLayout(head)
        else:
            col.addWidget(title_label(i, rtl))
        text, translated = news.summary(i, t.lang)
        if text:
            if not translated:
                note = QLabel(bidi.plain(t("news_english_summary"), rtl), objectName="RowHint")
                note.setAlignment(_align(rtl))
                col.addWidget(note)
            body = QLabel(sentences(text, rtl) if translated else text, objectName="CardStat")
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            # an English summary in a Hebrew window reads left to right, from the left
            body.setAlignment(_align(rtl and translated))
            if not translated:
                body.setLayoutDirection(Qt.LeftToRight)
            col.addWidget(body)
        links = QHBoxLayout()
        links.setSpacing(16)
        if on_open:
            read = QPushButton(bidi.plain(t("news_read_full"), rtl), objectName="Link")
            read.clicked.connect(lambda _=False, item=i: on_open(item))
        else:
            read = QPushButton(bidi.plain(t("news_read_meowdb"), rtl), objectName="Link")
            read.clicked.connect(lambda _=False, u=i.get("url"): open_url(u))
        read.setCursor(Qt.PointingHandCursor)
        links.addWidget(read)
        src = i.get("source_url") or ""
        if src.startswith("https://") and i.get("publisher"):
            orig = QPushButton(bidi.plain(t("news_read_source", who=i["publisher"]), rtl), objectName="Link")
            orig.setCursor(Qt.PointingHandCursor)
            orig.clicked.connect(lambda _=False, u=src: open_url(u))
            links.addWidget(orig)
        links.addStretch(1)
        col.addLayout(links)


def news_page(t, kb, unread_ids=(), on_open=None) -> QWidget:
    """The News tab's content: Global news, then China and Taiwan, newest first, each section up to SHOWN."""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    found = news.items(kb)
    rtl = t.rtl
    intro = QLabel(bidi.plain(t("news_intro"), rtl), objectName="RowHint")
    intro.setWordWrap(True)
    intro.setAlignment(_align(rtl))
    lay.addWidget(intro)
    if not found:
        empty = QLabel(bidi.plain(t("news_empty"), rtl), objectName="DialogBody")
        empty.setWordWrap(True)
        lay.addWidget(empty)
        return page
    unread = set(unread_ids)
    groups = [("news_section_global", [i for i in found if i.get("region", news.SHOWN_REGION) == news.SHOWN_REGION]),
              ("news_section_other", [i for i in found if i.get("region", news.SHOWN_REGION) != news.SHOWN_REGION])]
    for head, rows in groups:
        if not rows:
            continue
        hl = QLabel(bidi.plain(t(head, n=len(rows)), rtl), objectName="SectionHeader")
        lay.addWidget(hl)
        for i in rows[:SHOWN]:
            lay.addWidget(NewsCard(t, i, i["id"] in unread, on_open, kb))
        if len(rows) > SHOWN:
            more = QPushButton(bidi.plain(t("news_more_site", n=len(rows) - SHOWN), rtl), objectName="Link")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: open_url(news.NEWS_PAGE))
            lay.addWidget(more, 0, _align(rtl))
    return page


def article(t, i: dict, kb=None) -> QWidget:
    """A news item in full, like a guide: chips, title, date, its picture, the summary, every highlight, NiaMeowDB's
    note, the pictures of what it names, and the links to MeowDB and the publisher. Hebrew where it is translated;
    English text reads left to right."""
    rtl = t.rtl
    page = QWidget()
    page.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    chips = QHBoxLayout()
    chips.setSpacing(6)
    chips.addWidget(source_chip(t, i))
    chips.addWidget(QLabel(bidi.plain(news.short_date(i, rtl), rtl), objectName="CardSub"))
    chips.addStretch(1)
    lay.addLayout(chips)
    lay.addWidget(title_label(i, rtl, "ProfileName"))
    top = picture(news.header(kb, i)) if kb is not None else None
    if top:
        top.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        lay.addWidget(top)

    def para(text: str, translated: bool, name: str = "DialogBody") -> QLabel:
        lb = QLabel(sentences(text, rtl) if translated else text, objectName=name)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setAlignment(_align(rtl and translated))
        if not translated:
            lb.setLayoutDirection(Qt.LeftToRight)
        return lb

    text, translated = news.summary(i, t.lang)
    if text:
        lay.addWidget(para(text, translated))
    points, note, body_translated = news.body(i, t.lang)
    if points:
        lay.addWidget(QLabel(bidi.plain(t("news_key_points"), rtl), objectName="SectionHeader"))
        if not body_translated:
            lay.addWidget(para(t("news_english_body"), True, "RowHint"))
        for p in points:
            lay.addWidget(para("• " + p, body_translated))
    if note:
        lay.addWidget(QLabel(bidi.plain(t("news_meowdb_note"), rtl), objectName="SectionHeader"))
        lay.addWidget(para(note, body_translated, "RowLabel"))
    if kb is not None:
        _pictured(t, i, kb, lay)
    links = QHBoxLayout()
    links.setSpacing(16)
    # the source's link needs its publisher's name; MeowDB's own link doesn't (an item without one lost both)
    for label, url, ok in ((t("news_read_meowdb"), i.get("url") or "", True),
                           (t("news_read_source", who=i.get("publisher") or ""), i.get("source_url") or "",
                            bool(i.get("publisher")))):
        if url.startswith("https://") and ok:
            b = QPushButton(bidi.plain(label, rtl), objectName="Link")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, u=url: open_url(u))
            links.addWidget(b)
    links.addStretch(1)
    lay.addLayout(links)
    return page


def _pictured(t, i: dict, kb, lay: QVBoxLayout) -> None:
    """What the article names, in pictures: Nexon's from its announcement (credited, a link to it), then the KB's own
    of the other items and monsters it names (news.entities)."""
    rtl = t.rtl
    named = news.pictures(kb, i)
    grid = tiles(named, rtl)
    if grid:
        lay.addWidget(QLabel(bidi.plain(t("news_pictured"), rtl), objectName="SectionHeader"))
        lay.addWidget(grid)
        who, src = i.get("publisher") or "Nexon", i.get("source_url") or ""
        credit = QPushButton(bidi.plain(t("news_pictures_credit", who=who), rtl), objectName="Link")
        credit.setToolTip(bidi.plain(t("news_pictures_tip", who=who), rtl))
        if src.startswith("https://"):
            credit.setCursor(Qt.PointingHandCursor)
            credit.clicked.connect(lambda _=False, u=src: open_url(u))
        lay.addWidget(credit, 0, _align(rtl))
    keys = news.entities(kb, i, skip=[n for n, _ in named])
    grid = tiles([(kb.get(k)["name"], kb.image_path(k)) for k in keys], rtl)
    if grid:
        lay.addWidget(QLabel(bidi.plain(t("news_in_kb"), rtl), objectName="SectionHeader"))
        lay.addWidget(grid)


def news_dialog(lang: str, stylesheet: str, kb, unread=()):
    """The News window (the megaphone in the chat's header, or the news strip tapped): every news item, newest
    first; the unread ones are marked "New" and count as read once it shows (news_seen)."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QScrollArea, QStackedWidget

    from ..i18n import I18n
    from .glass import GlassDialog
    from .patchnotes import gutter

    class NewsDialog(GlassDialog):
        news_seen = Signal(list)

        def __init__(self):
            self.t = t = I18n(lang or "he")
            super().__init__(t("news_title"), t.rtl)
            self.unread = list(unread or ())
            self.setStyleSheet(stylesheet)
            self.resize(520, 680)
            outer = QVBoxLayout(self.content)
            outer.setContentsMargins(0, 0, 0, 0)
            # the list, and an article opened from it (Back / Esc return to the list, where it was scrolled to)
            self.stack = QStackedWidget()
            self.stack.addWidget(self._scroll(news_page(t, kb, self.unread, self.open_article)))
            outer.addWidget(self.stack, 1)
            if self.unread:      # once the opener has connected news_seen
                QTimer.singleShot(0, lambda: self.news_seen.emit(list(self.unread)))

        def _scroll(self, inner: QWidget) -> QScrollArea:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget(objectName="Feed")
            lay = QVBoxLayout(body)
            lay.setContentsMargins(*gutter(self.t.rtl))
            lay.addWidget(inner)
            lay.addStretch(1)
            scroll.setWidget(body)
            return scroll

        def open_article(self, i: dict) -> None:
            self.close_article()
            t, rtl = self.t, self.t.rtl
            box = QWidget()
            col = QVBoxLayout(box)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(12)
            back = QPushButton(bidi.plain(t("news_back"), rtl), objectName="Link")
            back.setCursor(Qt.PointingHandCursor)
            back.clicked.connect(self.close_article)
            col.addWidget(back, 0, _align(rtl))
            col.addWidget(article(t, i, kb))
            self.stack.addWidget(self._scroll(box))
            self.stack.setCurrentIndex(1)
            back.setFocus()

        def close_article(self) -> None:
            while self.stack.count() > 1:
                w = self.stack.widget(1)
                self.stack.removeWidget(w)
                w.deleteLater()
            self.stack.setCurrentIndex(0)

        def keyPressEvent(self, e):
            if e.key() == Qt.Key_Escape and self.stack.count() > 1:
                self.close_article()          # Esc in an article: back to the list, not out of the window
                return
            super().keyPressEvent(e)

    return NewsDialog()
