"""MapleStory Classic news in the app (news.py): the strip at the top of the chat when there is unread news, and the
News tab of the patch notes window.

Titles stay as published (English, one left-to-right block in a Hebrew line); the summary is in Hebrew when there
is a translation of its current text, else the English one under a Hebrew note that says so.
"""
from __future__ import annotations

import re
import webbrowser

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .. import bidi, news
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


def title_label(i: dict, rtl: bool, name: str = "CardName") -> QLabel:
    """The title as published: an English one is one left-to-right block, right-aligned in Hebrew."""
    lb = QLabel(bidi.ltr_name(i["title"], rtl), objectName=name)
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
        head = t("news_strip_head", date=news.short_date(i))
        if len(unread) > 1:
            head += " · " + t("news_strip_more", n=len(unread) - 1)
        self.head.setText(bidi.plain(head, rtl))
        self.title.setText(bidi.ltr_name(i["title"], rtl))
        for lb in (self.head, self.title):
            lb.setAlignment(_align(rtl))
        text, _ = news.summary(i, t.lang)
        self.setToolTip(sentences(text, rtl) if text else "")
        self.close_btn.setToolTip(t("news_dismiss"))
        self.close_btn.setAccessibleName(t("news_dismiss"))
        self.setAccessibleName(f"{head}: {i['title']}")
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
    """One news item: chips (source, region, new), the title as published, date, summary, links."""

    def __init__(self, t, i: dict, unread: bool = False):
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
        date = QLabel(bidi.plain(news.short_date(i), rtl), objectName="CardSub")
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
        read = QPushButton(bidi.plain(t("news_read_meowdb"), rtl), objectName="Link")
        read.setCursor(Qt.PointingHandCursor)
        read.clicked.connect(lambda _=False, u=i.get("url"): webbrowser.open(u))
        links.addWidget(read)
        src = i.get("source_url") or ""
        if src.startswith("https://") and i.get("publisher"):
            orig = QPushButton(bidi.plain(t("news_read_source", who=i["publisher"]), rtl), objectName="Link")
            orig.setCursor(Qt.PointingHandCursor)
            orig.clicked.connect(lambda _=False, u=src: webbrowser.open(u))
            links.addWidget(orig)
        links.addStretch(1)
        col.addLayout(links)


def news_page(t, kb, unread_ids=()) -> QWidget:
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
            lay.addWidget(NewsCard(t, i, i["id"] in unread))
        if len(rows) > SHOWN:
            more = QPushButton(bidi.plain(t("news_more_site", n=len(rows) - SHOWN), rtl), objectName="Link")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: webbrowser.open(news.NEWS_PAGE))
            lay.addWidget(more, 0, _align(rtl))
    return page
