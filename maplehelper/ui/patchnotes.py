"""Patch notes for knowledge-base updates: exactly what changed, so players know what's new."""
from __future__ import annotations

import time
from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import bidi, dates, recent
from ..i18n import NBSP, STRINGS, I18n
from ..kb import KnowledgeBase
from . import newsview
from .controls import Section, Segmented, rtl_buttons
from .glass import GlassDialog
from .widgets import EntityCard, Selectable, fit_picture, load_lazy_picture, zoom_on_hover

SHOWN = 12   # cards a list shows first; "Show more" adds MORE at a time (80 at once held the window back 1.3-1.8 s)
MORE = 40
FIRST_CARDS = 8      # made before the window shows; the rest follow a few at a time once it is up (PERF-04)


KINDS = ("added", "changed", "updated", "removed")


def summary(t: I18n, entries: list[dict]) -> str:
    """'3 new, 12 changed' over one or more updates."""
    total = totals(entries)
    news = sum(int((e.get("counts") or {}).get("news") or 0) for e in entries)       # new news items (scrape_news)
    return ", ".join([t(f"pn_n_{k}", n=total[k]) for k in KINDS if total[k]] + ([t("pn_n_news", n=news)] if news else []))


def update_notice(t: I18n, entries: list[dict], kb, char=None, wished=()) -> str:
    """The chat's note about a KB update: the changes that affect the active character first, by name
    ("3 database changes affect you: Long Sword, Ribbon Pig, Snail Shell"), then how many others."""
    mine, rest = recent.split(entries, kb, char, wished)
    if not mine:
        return t("patch_notes_summary", summary=summary(t, entries))
    # each name one unbreakable block: "Fire Boar" wrapped as "Fire" / "Boar" at the end of a Hebrew line
    names = [bidi.ltr_block((r.get("name") or r.get("key", "")).replace(" ", NBSP), t.rtl) for _, _, r in mine[:3]]
    if len(mine) > 3:
        names.append(t("pn_more", n=len(mine) - 3))
    text = t("patch_notes_affects", n=len(mine), names=", ".join(names))
    more = summary(t, rest)
    return text + ("\n" + t("pn_and_more", summary=more) if more else "")


def totals(entries: list[dict]) -> dict[str, int]:
    """How many pages each kind of change touched over these updates, each page once: a page updated in two of
    them is one updated page ("12 pages updated" for 9 pages), one added and then updated is a new page, and
    one added and then removed is nothing. Rows a long update didn't list are counted as it counted them."""
    if len(entries) == 1:
        counts = entries[0].get("counts") or {}
        return {k: counts.get(k, len(entries[0].get(k) or [])) for k in KINDS}
    seen: dict[str, list[str]] = {}            # key -> its kinds, oldest update first
    unlisted = dict.fromkeys(KINDS, 0)
    for e in sorted(entries, key=lambda e: str(e.get("version", ""))):
        counts = e.get("counts") or {}
        for kind in KINDS:
            rows = e.get(kind) or []
            unlisted[kind] += max(0, counts.get(kind, len(rows)) - len(rows))
            for r in rows:
                seen.setdefault(r.get("key") or r.get("name") or id(r), []).append(kind)
    total = dict(unlisted)
    for kinds in seen.values():
        if kinds[0] == "added":
            if kinds[-1] != "removed":
                total["added"] += 1
        elif kinds[-1] == "removed":
            total["removed"] += 1
        else:
            total["changed" if "changed" in kinds else "updated"] += 1
    return total


def _category(t: I18n, cat: str) -> str:
    return t(f"cat_{cat}") if f"cat_{cat}" in STRINGS else cat


def _date(e: dict, rtl: bool) -> str:
    """2026-10-02 -> 2.10 / Oct 2 (dates.day); the version when there's no date."""
    try:
        return dates.day(date.fromisoformat(str(e.get("date") or "")), rtl)
    except ValueError:
        return str(e.get("version", ""))


def _value(v) -> str:
    return "—" if v is None or v == "" else str(v)


class ChangeCard(Selectable, QFrame):
    """A chat-style card (picture, name, category) with what changed underneath."""

    def __init__(self, kb: KnowledgeBase, r: dict, sub: str, lines: list[str], rtl: bool, lazy: bool = False):
        """lazy: the picture waits for load_picture()."""
        super().__init__()
        self.setObjectName("Card")
        if kb.get(r["key"]):
            self._init_selectable(r["key"])      # tap to ask about it, like the cards in the chat
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(48, 48)
        pic.setAlignment(Qt.AlignCenter)
        img = kb.picture(r["key"])
        self._lazy = (pic, img) if lazy else None
        pm = QPixmap(str(img)) if img and not lazy else QPixmap()
        if not pm.isNull():
            # without the sprite's empty margins: Trixter (a small bug in a 67x81 canvas) drew half the size of
            # Jr. Sentinel on the next card (VIS-22)
            pic.setPixmap(fit_picture(pm, 48, 48, pic, trim=True))
            zoom_on_hover(pic, img)
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        for text, name in [(r["name"], "CardName"), (sub, "CardSub")] + [(ln, "CardStat") for ln in lines]:
            lb = QLabel(text if name == "CardName" else bidi.plain(text, rtl), objectName=name)
            lb.setWordWrap(True)
            lb.setAlignment(align)
            col.addWidget(lb)
        row.addLayout(col, 1)

    def mouseReleaseEvent(self, ev):
        if hasattr(self, "key"):
            super().mouseReleaseEvent(ev)

    def load_picture(self) -> None:
        load_lazy_picture(self, 48, trim=True)       # without the sprite's empty margins, as above


def gutter(rtl: bool) -> tuple[int, int, int, int]:
    """A feed's margins: the room before the scrollbar is on the scrollbar's side, the left in Hebrew (margins
    don't mirror, so the Hebrew cards touched the bar while the gap sat on the empty right side)."""
    return (6, 0, 0, 0) if rtl else (0, 0, 6, 0)


class WhatsNewDialog(GlassDialog):
    """What changed in the app itself, version by version."""

    def __init__(self, notes: list[dict], lang: str, stylesheet: str):
        self.notes = notes            # (opened again as it is after a language or theme switch)
        self.t = t = I18n(lang or "he")
        super().__init__(t("whats_new"), t.rtl)
        self.setStyleSheet(stylesheet)
        self.resize(480, 560)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(t.rtl))
        lay.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        if not notes:
            # no notes (the bundled file missing or unreadable): a line, not an empty window with only Close (VIS-7)
            empty = QLabel(bidi.plain(t("whats_new_empty"), t.rtl), objectName="DialogBody")
            empty.setWordWrap(True)
            lay.addWidget(empty)
        for n in notes:
            sec = Section(t("version_title", version=n["version"]), t.rtl)
            for line in n.get(t.lang) or n.get("en") or []:
                sec.add_row("•  " + line)
            lay.addWidget(sec)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 0)
        row.addStretch(1)
        ok = QPushButton(t("close"), objectName="Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(160)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch(1)
        outer.addLayout(row)
        rtl_buttons(self, t.rtl)


class PatchNotesDialog(GlassDialog):
    """char / wished: the active character and its wishlist. The changes that matter to them (recent.split: gear for
    their class and level, monsters in their training range, wished items) come first, the rest under them.
    A second tab lists MapleStory Classic news (the KB's news.json, ui/newsview.py); tab: the one it opens on,
    unread: the news ids the chat hasn't shown as read yet (marked "New")."""

    news_seen = Signal(list)       # the News tab was shown: these unread ids are read now

    def __init__(self, entries: list[dict], lang: str, stylesheet: str, kb: KnowledgeBase, char=None,
                 wished=(), tab: str = "changes", unread=()):
        self.entries = entries        # (opened again as it is after a language or theme switch)
        self.char, self.wished = char, list(wished or ())
        self.unread = list(unread or ())
        self.t = t = I18n(lang or "he")
        super().__init__(t("patch_notes"), t.rtl)
        self.kb = kb
        self.setStyleSheet(stylesheet)
        self.resize(520, 680)
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        # database changes | news, one window: both are "what's new" since the player last looked
        self.tabs = Segmented([(t("pn_tab_changes"), "changes"), (t("pn_tab_news"), "news")],
                              tab if tab in ("changes", "news") else "changes", t.rtl)
        self.tabs.set_label(t("pn_tabs_a11y"))
        outer.addWidget(self.tabs)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(*gutter(t.rtl))
        lay.setSpacing(18)
        scroll.setWidget(body)
        self.stack.addWidget(scroll)
        news_scroll = QScrollArea()
        news_scroll.setWidgetResizable(True)
        news_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        news_body = QWidget(objectName="Feed")
        nl = QVBoxLayout(news_body)
        nl.setContentsMargins(*gutter(t.rtl))
        nl.addWidget(newsview.news_page(t, kb, self.unread))
        nl.addStretch(1)
        news_scroll.setWidget(news_body)
        self.stack.addWidget(news_scroll)
        self.tabs.changed.connect(self._show_tab)
        self.stack.setCurrentIndex(1 if self.tabs.value() == "news" else 0)
        if self.tabs.value() == "news":
            QTimer.singleShot(0, lambda: self._show_tab("news"))      # once the opener has connected news_seen

        if not entries:
            empty = QLabel(bidi.plain(t("patch_notes_empty"), t.rtl), objectName="DialogBody")
            empty.setWordWrap(True)
            lay.addWidget(empty)
        # the headings and lists are laid out now, their cards queued: the first few are made before the window
        # shows, the rest a few at a time once it is up, the pictures last (it was blank for 1.3-1.8 s, PERF-04)
        self._jobs: list = []          # (layout, make card)
        self._pics: list = []          # cards whose picture is still to load
        mine, rest = recent.split(entries, kb, char, self.wished) if (char or self.wished) else ([], entries)
        self.mine = mine
        if mine:
            box = QVBoxLayout()
            box.setSpacing(8)
            box.addWidget(QLabel(bidi.plain(t("pn_affects", n=len(mine)), t.rtl), objectName="ProfileName"))
            self._list(box, [lambda reason=reason, kind=kind, r=r: self._card(kind, r, reason)
                             for reason, kind, r in mine], len(mine))
            lay.addLayout(box)
            if any(any((e.get("counts") or {}).get(k) for k in KINDS) for e in rest):
                lay.addWidget(QLabel(bidi.plain(t("pn_more_changes"), t.rtl), objectName="ProfileName"))
        for e in rest:
            self._entry(lay, e)
        lay.addStretch(1)
        self._build_cards(FIRST_CARDS)

        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 0)
        row.addStretch(1)
        ok = QPushButton(t("close"), objectName="Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(160)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch(1)
        outer.addLayout(row)
        rtl_buttons(self, t.rtl)

    def _list(self, box: QVBoxLayout, makers: list, n: int) -> None:
        """A list's cards (a maker each) under its heading: the first SHOWN, then "and N more" with "Show more",
        which adds the next MORE (the count alone was a dead end, TOOL-21). n: every row, the ones an update
        counted without listing them too (those have no card to show)."""
        cards = QVBoxLayout()
        cards.setSpacing(8)
        box.addLayout(cards)
        foot = QWidget()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        left = QLabel(objectName="RowHint")
        left.setAlignment(Qt.AlignHCenter)
        fl.addWidget(left)
        # the number in the line above, not on the button (a button lays a Hebrew label with a number out of order)
        more = QPushButton(bidi.plain(self.t("pn_show_more"), self.t.rtl), objectName="Link")
        more.setCursor(Qt.PointingHandCursor)
        more.setAutoDefault(False)
        fl.addWidget(more, 0, Qt.AlignHCenter)
        box.addWidget(foot)
        state = {"shown": 0}

        def show(count: int) -> None:
            for make in makers[state["shown"]:state["shown"] + count]:
                self._jobs.append((cards, make))
            state["shown"] = min(len(makers), state["shown"] + count)
            hidden = n - state["shown"]
            foot.setVisible(hidden > 0)
            left.setText(bidi.plain(self.t("pn_more", n=hidden), self.t.rtl))
            more.setVisible(state["shown"] < len(makers))

        def show_more() -> None:
            show(MORE)
            self._build_cards()
        more.clicked.connect(lambda *_: show_more())
        show(SHOWN)

    def _build_cards(self, first: int = 0) -> None:
        """The queued cards: `first` of them now (before the window shows), else as many as fit in 30 ms, again
        each turn of the event loop until all are made; then their pictures, the same way."""
        try:
            if self.__dict__.get("_closed"):
                return
            if first:
                for _ in range(min(first, len(self._jobs))):
                    self._make_one()
            else:
                start = time.perf_counter()
                while self._jobs and time.perf_counter() - start < 0.03:
                    self._make_one()
            if self._jobs:
                QTimer.singleShot(0, self._build_cards)
            elif self._pics:
                QTimer.singleShot(0, self._load_pictures)
        except RuntimeError:       # the window closed meanwhile
            pass

    def _make_one(self) -> None:
        layout, make = self._jobs.pop(0)
        card = make()
        layout.addWidget(card)
        if getattr(card, "_lazy", None):
            self._pics.append(card)

    def _load_pictures(self) -> None:
        try:
            if self.__dict__.get("_closed"):
                return
            start = time.perf_counter()
            while self._pics and time.perf_counter() - start < 0.02:
                self._pics.pop(0).load_picture()
            if self._pics:
                QTimer.singleShot(0, self._load_pictures)
        except RuntimeError:       # the window closed meanwhile
            pass

    def closeEvent(self, e):
        self._closed = True
        super().closeEvent(e)

    @property
    def tab(self) -> str:
        return "news" if self.stack.currentIndex() == 1 else "changes"

    def _show_tab(self, tab) -> None:
        self.stack.setCurrentIndex(1 if tab == "news" else 0)
        if tab == "news" and self.unread:
            self.news_seen.emit(list(self.unread))      # the "New" chips stay until the window closes

    def _card(self, kind: str, r: dict, reason: str = "") -> QWidget:
        """A changed entry's card: what changed in it; a new, updated or removed entry's own card."""
        t, rtl = self.t, self.t.rtl
        sub = _category(t, r.get("category", ""))
        if reason:
            sub += " · " + t(f"pn_why_{reason}")          # why it is among the changes that affect the player
        if kind == "changed":
            # "Weapon Attack: 30 → 33 (COT2 → Launch)" reads left to right even in Hebrew (the arrow points from old
            # to new), with the builds when the page's change history shows that very change
            rc = recent.Recent(r["key"], r.get("name") or r["key"], "", {f: [a, b] for f, a, b in r.get("props", [])},
                               list(r.get("drops_added") or []), list(r.get("drops_removed") or []),
                               r.get("old_name") or "", list(r.get("community_added") or []),
                               list(r.get("community_removed") or []), list(r.get("mesos") or []))
            return ChangeCard(self.kb, r, sub, recent.lines(t, self.kb, rc), rtl, lazy=True)
        if self.kb.get(r["key"]) and not reason:
            return EntityCard(self.kb, r["key"], t.lang, lazy=True)     # exactly the chat's card
        return ChangeCard(self.kb, r, sub, [], rtl, lazy=True)

    def _entry(self, lay: QVBoxLayout, e: dict):
        t, rtl = self.t, self.t.rtl
        counts = e.get("counts") or {}
        if not any(counts.get(k, len(e.get(k) or [])) for k in KINDS):
            return            # every change of this update is among the player's own, above
        title = QLabel(bidi.plain(t("pn_update", date=_date(e, rtl)), rtl), objectName="ProfileName")
        lay.addWidget(title)

        def section(kind: str, rows: list[dict], card_fn):
            n = counts.get(kind, len(rows))
            if not n:
                return
            box = QVBoxLayout()
            box.setSpacing(8)
            box.addWidget(QLabel(bidi.plain(t(f"pn_{kind}", n=n), rtl), objectName="SectionHeader"))
            self._list(box, [lambda r=r: card_fn(r) for r in rows], max(n, len(rows)))
            lay.addLayout(box)

        for kind in KINDS:
            section(kind, e.get(kind, []), lambda r, kind=kind: self._card(kind, r))
