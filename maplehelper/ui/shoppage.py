"""The Play tools' Shop page (חנות): what the Cash Shop sells right now and for how many NX, by tab (wear, coupons,
other, and the Pets page as its own tab). The list is MesoWatch's, read from the game's own Cash Shop list (the owner
chose it, 2026-10-11: live items and real NX prices; NiaMeowDB's pages give the closed test's 100 NX). Each row is the
item's picture, name, what it is (its KB description) and its NX price; a click opens the item's details window.

It follows the game by itself: the app refreshes MesoWatch every 15 minutes (mesowatch.py) and a newer list redraws
the page, so an item the Cash Shop starts selling shows up and one it stops selling drops out."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import bidi, itemdetails, mesowatch, sources, translations
from .controls import FlowLayout, Segmented
from .widgets import ITEM_REQUESTS, fit_picture, source_tag

DESC_CHARS = 110               # a description cut to about two lines
POLL_MS = 60 * 1000            # how often the page looks for a newer list (the refresh itself: every 15 minutes)
TABS = ("all", "wear", "coupons", "pets", "other")
PETS = "pets"


def shorten(text: str, limit: int = DESC_CHARS) -> str:
    """About two lines of a description, cut at a word with "…"; the whole text is in the item's window."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"


def tab_of(kb, key: str | None, name: str) -> str:
    """The item's tab, by the KB's item type ("Cash Equip / Cash Hat": wear, "Cash / Coupon": coupons, "Cash / Pet":
    pets); an item the KB lacks goes by its name."""
    kind = str((kb.get(key) or {}).get("type") or "") if key else ""
    if kind.startswith("Cash Equip"):
        return "wear"
    if kind == "Cash / Pet":
        return "pets"
    if kind == "Cash / Coupon" or "coupon" in name.lower():
        return "coupons"
    return "other"


class ShopRow(QFrame):
    """One item: picture, name and description on the reading side, the NX price on the other; click: its window."""

    def __init__(self, page: "ShopPage", s: mesowatch.Sales, key: str | None):
        super().__init__(objectName="Card")
        t = page.t
        self.key, self.sales = key, s
        if key:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(t("shop_open_item"))
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        self.pic = QLabel()
        self.pic.setFixedSize(44, 44)
        self.pic.setAlignment(Qt.AlignCenter)
        path = page.kb.picture(key) if key else None
        if path:
            self.set_picture(path)
        else:
            page.want_icon(self)
        row.addWidget(self.pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        name = (page.kb.get(key) or {}).get("name") if key else None
        title = QLabel(bidi.ltr_name(name or s.name, t.rtl), objectName="ShopName")
        title.setAlignment(page.align)
        col.addWidget(title)
        desc = page.describe(key)
        if desc:
            lb = QLabel(desc, objectName="ShopText")
            lb.setWordWrap(True)
            lb.setAlignment(page.align)
            col.addWidget(lb)
        row.addLayout(col, 1)
        price = QLabel(bidi.ltr_block(f"{s.nx:,} NX", t.rtl) if s.nx else "-", objectName="ShopPrice")
        row.addWidget(price, 0, Qt.AlignVCenter)

    def set_picture(self, path) -> None:
        pm = QPixmap(str(path))
        if not pm.isNull():
            self.pic.setPixmap(fit_picture(pm, 40, 40, self.pic))

    def mousePressEvent(self, e):
        if self.key and e.button() == Qt.LeftButton:
            ITEM_REQUESTS.requested.emit(self.key)
        super().mousePressEvent(e)


class ShopPage(QWidget):
    ready = Signal(object)          # the snapshot (or None) from the background read
    icon_ready = Signal(object, object)     # (row, path): an item's MesoWatch icon is on disk

    def __init__(self, kb, t, pets: QWidget | None = None):
        super().__init__()
        from . import theme
        self.kb, self.t = kb, t
        self.align = (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        self.snap, self.keys, self._desc, self._version, self.tab = None, {}, {}, -1, "all"
        c = theme.P()
        # no small text (the owner): everything at the body size or larger
        self.setStyleSheet(f"#ShopPrice {{ font-size: 17px; font-weight: 700; color: {theme.accent_text()}; }}"
                           f"#ShopName {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}"
                           f"#ShopText {{ font-size: 14px; color: {c['muted']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        top = QHBoxLayout()
        intro = QLabel(bidi.plain(t("shop_intro"), t.rtl), objectName="ShopText")
        intro.setWordWrap(True)
        intro.setAlignment(self.align)
        top.addWidget(intro, 1)
        top.addWidget(source_tag(t, sources.MESOWATCH), 0, Qt.AlignTop)
        lay.addLayout(top)
        holder = QWidget()
        holder.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        flow = FlowLayout(holder)
        self.tab_buttons: dict[str, QPushButton] = {}
        for tab in TABS:
            b = QPushButton(bidi.plain(t(f"shop_tab_{tab}"), t.rtl), objectName="Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=tab: self.pick(x))
            flow.addWidget(b)
            self.tab_buttons[tab] = b
        lay.addWidget(holder)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        listing = QWidget(objectName="Feed")
        ml = QVBoxLayout(listing)
        from .patchnotes import gutter
        ml.setContentsMargins(*gutter(t.rtl))
        ml.setSpacing(8)
        # the sort on its own line: beside the status it made the list wider than the window in English ("NX" cut)
        self.sort = Segmented([(t("shop_sort_name"), "name"), (t("shop_sort_cheap"), "cheap"),
                               (t("shop_sort_dear"), "dear")], "name", t.rtl)
        self.sort.set_label(t("shop_sort"))
        self.sort.changed.connect(lambda *_: self._refill())
        ml.addWidget(self.sort, 0, self.align)
        self.status = QLabel(bidi.plain(t("shop_loading"), t.rtl), objectName="ShopText")
        self.status.setAlignment(self.align)
        ml.addWidget(self.status)
        self.list = QVBoxLayout()
        self.list.setSpacing(8)
        ml.addLayout(self.list)
        ml.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(listing)
        self.stack.addWidget(self.scroll)
        self.pets = pets or QWidget()
        self.stack.addWidget(self.pets)
        self.ready.connect(self._on_ready)
        self.icon_ready.connect(lambda row, path: self._set_icon(row, path))
        self._icons: list[ShopRow] = []
        self.timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self.timer.start()
        self.pick("all")
        self.load()

    # ------------------------------------------------------------ data

    def load(self) -> None:
        """The list in the background (the first one of a run may download it)."""
        def run():
            try:
                snap = mesowatch.snapshot()
            except Exception:      # noqa: BLE001 - shown as "can't reach MesoWatch"
                snap = None
            try:
                self.ready.emit(snap)
            except RuntimeError:
                pass                # the window was closed meanwhile
        threading.Thread(target=run, daemon=True, name="mesowatch-shop").start()

    def _poll(self) -> None:
        """A newer list came in (the app's 15-minute refresh): the page follows it."""
        if mesowatch.version() != self._version:
            self.load()

    def _on_ready(self, snap) -> None:
        self._version = mesowatch.version()
        self.snap = snap
        self.items = mesowatch.cash_shop(snap)
        self.keys = {}
        for k in getattr(self.kb, "entities", {}):
            if k.startswith("item/"):
                gid = mesowatch.match(snap, self.kb, k) if snap else None
                if gid and gid not in self.keys and any(s.game_id == gid for s in self.items):
                    self.keys[gid] = k
        counts = {tab: 0 for tab in TABS}
        for s in self.items:
            counts[tab_of(self.kb, self.keys.get(s.game_id), s.name)] += 1
        counts["all"] = len(self.items)
        for tab, b in self.tab_buttons.items():
            n = counts.get(tab, 0)
            b.setText(bidi.plain(f"{self.t(f'shop_tab_{tab}')} · {n}" if n and tab != PETS else
                                 self.t(f"shop_tab_{tab}"), self.t.rtl))
        self._refill()

    def describe(self, key: str | None) -> str:
        """What the item is: its KB description, in Hebrew when the app or the KB has one (cached)."""
        if not key:
            return ""
        if key not in self._desc:
            try:
                en = itemdetails.details(self.kb, key).description or ""
            except Exception:      # noqa: BLE001 - a page the parser didn't expect: no description
                en = ""
            text = (translations.he(getattr(self.kb, "root", None), "item_desc", key, en) or en) if self.t.rtl else en
            text = shorten(text)
            self._desc[key] = bidi.plain(text, self.t.rtl) if self.t.rtl and any("֐" <= ch <= "׿" for ch in text) \
                else bidi.ltr_block(text, self.t.rtl)
        return self._desc[key]

    def want_icon(self, row: ShopRow) -> None:
        """An item the KB has no picture of: MesoWatch's icon, fetched in the background."""
        self._icons.append(row)

    def _fetch_icons(self) -> None:
        rows, self._icons = self._icons, []
        if not rows:
            return

        def run():
            for row in rows:
                path = mesowatch.icon_file(row.sales)
                if path is not None:
                    try:
                        self.icon_ready.emit(row, path)
                    except RuntimeError:
                        return
        threading.Thread(target=run, daemon=True, name="mesowatch-icons").start()

    @staticmethod
    def _set_icon(row: ShopRow, path) -> None:
        try:
            row.set_picture(path)
        except RuntimeError:
            pass                    # redrawn meanwhile

    # ------------------------------------------------------------ the list

    def pick(self, tab: str) -> None:
        self.tab = tab
        for x, b in self.tab_buttons.items():
            b.setChecked(x == tab)
        self.stack.setCurrentIndex(1 if tab == PETS else 0)
        if tab != PETS:
            self._refill()

    def rows(self) -> list[mesowatch.Sales]:
        items = list(getattr(self, "items", []))
        if self.tab != "all":
            items = [s for s in items if tab_of(self.kb, self.keys.get(s.game_id), s.name) == self.tab]
        how = self.sort.value()
        if how == "cheap":
            items.sort(key=lambda s: (s.nx is None, s.nx or 0, s.name))
        elif how == "dear":
            items.sort(key=lambda s: (s.nx is None, -(s.nx or 0), s.name))
        else:
            items.sort(key=lambda s: str((self.kb.get(self.keys.get(s.game_id, "")) or {}).get("name") or s.name))
        return items

    def _refill(self) -> None:
        from .tools import clear, market_ago
        if self.tab == PETS:
            return
        clear(self.list)
        t = self.t
        if self.snap is None:
            self.status.setText(bidi.plain(t("shop_loading") if self._version < 0 else t("mw_offline"), t.rtl))
            return
        items = self.rows()
        self.status.setText(bidi.plain(t("shop_status", n=len(items), ago=market_ago(t, self.snap.generated)), t.rtl))
        for s in items:
            self.list.addWidget(ShopRow(self, s, self.keys.get(s.game_id)))
        if not items:
            empty = QLabel(bidi.plain(t("shop_empty"), t.rtl), objectName="ShopText")
            empty.setAlignment(self.align)
            self.list.addWidget(empty)
        self._fetch_icons()
