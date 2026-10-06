"""MapleStory Classic news in the app (news.py): the strip at the top of the chat when there is unread news, and the
News tab of the patch notes window.

Titles stay as published (English, one left-to-right block in a Hebrew line); the summary is in Hebrew when there
is a translation of its current text, else the English one under a Hebrew note that says so.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .. import bidi, news
from ..osapi import open_url
from . import theme

SHOWN = 30          # cards per section; the rest are a link away on MeowDB
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


def title_label(i: dict, rtl: bool, name: str = "CardName") -> QLabel:
    """The title: in Hebrew when translated (news.title), else as published."""
    lb = QLabel(_title(i, rtl), objectName=name)
    lb.setWordWrap(True)
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
        self.close_btn = QToolButton(objectName="Icon", text=theme.ICON["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
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
        self.title.setText(_title(i, rtl))
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
    """One news item: chips (source, region, new), the title, date, summary, links. on_open(item): the full
    article in the app ("Read the article"); without it the card links to MeowDB."""

    def __init__(self, t, i: dict, unread: bool = False, on_open=None):
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
            lay.addWidget(NewsCard(t, i, i["id"] in unread, on_open))
        if len(rows) > SHOWN:
            more = QPushButton(bidi.plain(t("news_more_site", n=len(rows) - SHOWN), rtl), objectName="Link")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: open_url(news.NEWS_PAGE))
            lay.addWidget(more, 0, _align(rtl))
    return page


def article(t, i: dict) -> QWidget:
    """A news item in full, like a guide: chips, title, date, the summary, every highlight, NiaMeowDB's note and the
    links to MeowDB and the publisher. Hebrew where it is translated; English text reads left to right."""
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
            col.addWidget(article(t, i))
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
