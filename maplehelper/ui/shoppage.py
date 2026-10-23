"""The Play tools' Shop page (חנות): what the Cash Shop sells right now, laid out as the game's own Cash Shop is (the
owner's screenshots, 2026-10-11): Special (the Founder's Mystery Fashion Crate and what it can give), Fashion (Hats,
Outfits, Tops, Bottoms, Shoes), Beauty (Hairstyles, Faces, Misc), Pets (Pets, Pet Equip, Pet Care, and the pets guide
that was its own page) and Utility (Convenience, Social, Atmospheric Effect). Each item: its picture, name, what it
is, its NX price and how long it lasts; a click opens its details window.

All of it follows the game by itself, from two sources the owner has permission for: the items and their prices from
OSMS Data Explorer (osms.py, the game client's own Cash Shop list, checked every hour), the crate's chances from
MesoWatch (mesowatch.py, every 15 minutes), else from the KB (NiaMeowDB's chances on each item's page, nightly). An
item the Cash Shop adds shows up, one it drops goes away."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import bidi, itemdetails, mesowatch, osms, sources, translations
from ..shopnames import fold, kb_crates, kb_hebrew, kb_names, same_text  # noqa: F401 - the page's names, tests read them here
from .controls import FlowLayout, Segmented
from .widgets import ITEM_REQUESTS, fit_picture, source_tag

POLL_MS = 60 * 1000            # how often the page looks for a newer list (the checks: hourly / every 15 minutes)
GUIDE = "guide"                # the Pets tab's pets guide (the old Pets page)
CRATE_COLUMNS = 4              # the crate's tiles a row (the owner)


class ShopRow(QFrame):
    """One item: picture, name and description on the reading side, the NX price and how long it lasts on the
    other; click: its details window."""

    def __init__(self, page: "ShopPage", item: osms.Item, key: str | None):
        super().__init__(objectName="Card")
        t = page.t
        self.key, self.item = key, item
        if key:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(t("shop_open_item"))
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        self.pic = QLabel()
        self.pic.setFixedSize(48, 48)
        self.pic.setAlignment(Qt.AlignCenter)
        page.picture(self.pic, key, item, 44)
        row.addWidget(self.pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        name = (page.kb.get(key) or {}).get("name") if key else None
        title = QLabel(bidi.ltr_name(name or item.name, t.rtl), objectName="ShopName")
        title.setAlignment(page.align)
        col.addWidget(title)
        desc = page.describe(item.name, item.description, item.game_id)
        if desc:
            lb = QLabel(desc, objectName="ShopText")
            lb.setWordWrap(True)
            lb.setAlignment(page.align)
            col.addWidget(lb)
        row.addLayout(col, 1)
        end = QVBoxLayout()
        end.setSpacing(2)
        price = QLabel(bidi.ltr_block(f"{item.nx:,} NX", t.rtl), objectName="ShopPrice")
        price.setAlignment((Qt.AlignLeft if t.rtl else Qt.AlignRight) | Qt.AlignAbsolute)
        end.addWidget(price)
        lasts = QLabel(bidi.plain(t("shop_days", n=item.days) if item.days else t("shop_permanent"), t.rtl),
                       objectName="ShopText")
        lasts.setAlignment((Qt.AlignLeft if t.rtl else Qt.AlignRight) | Qt.AlignAbsolute)
        end.addWidget(lasts)
        row.addLayout(end, 0)

    def mousePressEvent(self, e):
        if self.key and e.button() == Qt.LeftButton:
            ITEM_REQUESTS.requested.emit(self.key)
        super().mousePressEvent(e)


class CrateTile(QFrame):
    """One thing the crate can give: its picture, name and chance."""

    def __init__(self, page: "ShopPage", name: str, odds: float, key: str | None):
        super().__init__(objectName="Card")
        t = page.t
        self.key = key
        self.setFixedHeight(156)             # room for a two-line name under the picture; four share a row
        if key:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(t("shop_open_item"))
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 8, 6, 8)
        col.setSpacing(3)
        pic = QLabel()
        pic.setFixedSize(48, 48)
        pic.setAlignment(Qt.AlignCenter)
        page.picture(pic, key, None, 44)
        col.addWidget(pic, 0, Qt.AlignHCenter)
        name = ((page.kb.get(key) or {}).get("name") if key else None) or name
        lb = QLabel(bidi.ltr_name(name, t.rtl), objectName="ShopTile")
        lb.setAlignment(Qt.AlignCenter)
        lb.setWordWrap(True)
        lb.setFixedHeight(40)
        col.addWidget(lb)
        odds = QLabel(bidi.plain(t("shop_chance", pct=f"{odds:g}%"), t.rtl), objectName="ShopText")
        odds.setAlignment(Qt.AlignCenter)
        col.addWidget(odds)

    def mousePressEvent(self, e):
        if self.key and e.button() == Qt.LeftButton:
            ITEM_REQUESTS.requested.emit(self.key)
        super().mousePressEvent(e)


class ShopPage(QWidget):
    ready = Signal(object, object, object)  # (OSMS's items or None, MesoWatch's snapshot or None, the KB's crates)
    picture_ready = Signal(object, object)  # (label, path): an OSMS picture is on disk

    def __init__(self, kb, t, pets: QWidget | None = None):
        super().__init__()
        from . import theme
        self.kb, self.t = kb, t
        self.align = (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        self.items, self.snap, self._desc, self._loaded = None, None, {}, False
        self.crates: dict[str, list[tuple[str, float]]] = {}
        self._versions = (-1, -1)
        self.tab, self.section = "fashion", "hats"
        self._by_name = self._kb_names()
        c = theme.P()
        # no small text (the owner): everything at the body size or larger
        self.setStyleSheet(f"#ShopPrice {{ font-size: 17px; font-weight: 700; color: {theme.accent_text()}; }}"
                           f"#ShopName {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}"
                           f"#ShopTile {{ font-size: 14px; font-weight: 600; color: {c['text']}; }}"
                           f"#ShopText {{ font-size: 14px; color: {c['muted']}; }}"
                           f"#ShopHead {{ font-size: 16px; font-weight: 700; color: {c['text']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        top = QHBoxLayout()
        intro = QLabel(bidi.plain(t("shop_intro"), t.rtl), objectName="ShopText")
        intro.setWordWrap(True)
        intro.setAlignment(self.align)
        top.addWidget(intro, 1)
        chips = QVBoxLayout()
        chips.setSpacing(4)
        chips.addWidget(source_tag(t, sources.OSMS), 0, Qt.AlignRight)
        top.addLayout(chips, 0)
        lay.addLayout(top)
        # the credit and link OSMS's terms ask for (a label: a button laid the Hebrew words out reversed)
        credit = QLabel(objectName="ShopText")
        credit.setTextFormat(Qt.RichText)
        credit.setOpenExternalLinks(True)
        from html import escape
        d = "rtl" if t.rtl else "ltr"
        before = escape(t("shop_credit", link="@LINK@")).split("@LINK@")
        credit.setText(f'<p dir="{d}" align="{"right" if t.rtl else "left"}">{before[0]}'
                       f'<a href="{osms.SITE}" style="color:{theme.accent_text()}; text-decoration:none;">'
                       f'<span dir="ltr">{escape(osms.CREDIT)}</span></a>{before[-1]}</p>')
        credit.setAlignment(self.align)
        self.credit = credit
        lay.addWidget(credit)
        # the game's tabs, then the open tab's sections
        holder = QWidget()
        holder.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        flow = FlowLayout(holder)
        self.tab_buttons: dict[str, QPushButton] = {}
        for tab in osms.TABS:
            b = QPushButton(bidi.plain(t(f"shop_tab_{tab}"), t.rtl), objectName="Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=tab: self.pick(x))
            flow.addWidget(b)
            self.tab_buttons[tab] = b
        lay.addWidget(holder)
        self.sections_box = QVBoxLayout()
        lay.addLayout(self.sections_box)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        listing = QWidget(objectName="Feed")
        ml = QVBoxLayout(listing)
        from .patchnotes import gutter
        ml.setContentsMargins(*gutter(t.rtl))
        ml.setSpacing(8)
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
        self.picture_ready.connect(self._set_picture)
        self._wanted: list[tuple[QLabel, osms.Item]] = []
        self.timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self.timer.start()
        self.pick("fashion")
        self.load()

    # ------------------------------------------------------------ data

    def load(self) -> None:
        """Both lists in the background (the first of a run may download them), and the KB's crates."""
        def run():
            try:
                found = osms.items()
            except Exception:      # noqa: BLE001 - shown as "can't reach"
                found = None
            try:
                snap = mesowatch.snapshot()
            except Exception:      # noqa: BLE001 - no crate chances
                snap = None
            try:
                crates = kb_crates(self.kb)
            except Exception:      # noqa: BLE001 - a KB page the parser didn't expect: MesoWatch's chances only
                crates = {}
            try:
                self.ready.emit(found, snap, crates)
            except RuntimeError:
                pass                # the window was closed meanwhile
        threading.Thread(target=run, daemon=True, name="shop").start()

    def _poll(self) -> None:
        """A different list came in (OSMS's hourly check, MesoWatch's 15-minute one): the page follows it."""
        if (osms.version(), mesowatch.version()) != self._versions:
            self.load()

    def _on_ready(self, found, snap, crates) -> None:
        self._versions = (osms.version(), mesowatch.version())
        self.items, self.snap, self.crates, self._loaded = found, snap, crates or {}, True
        self._refill()

    def _kb_names(self) -> dict[str, list[str]]:
        return kb_names(self.kb)

    def key_of(self, name: str) -> str | None:
        keys = self._by_name.get(fold(name)) or []
        return keys[0] if keys else None

    def describe(self, name: str, english: str = "", game_id: int | None = None) -> str:
        """What the item is, whole (the owner: never cut). The game's own words (OSMS) win over the KB's, which can be
        the closed test's: in a Hebrew window, the Hebrew made from OSMS's words (the app's file, then the KB's nightly
        "cash_desc"), or the KB's Hebrew of a text that says the same; for an item OSMS has no words for (most
        equips), the KB's Hebrew; else English. Cached."""
        cache = (name, english)
        if cache not in self._desc:
            keys = self._by_name.get(fold(name)) or []
            root = getattr(self.kb, "root", None)
            text = None
            if self.t.rtl:
                if english:
                    text = (translations.he(root, "cash_desc", f"cash/{game_id}", english) if game_id else None) \
                        or kb_hebrew(self.kb, root, keys, english)
                else:
                    text = kb_hebrew(self.kb, root, keys)
            if not text:
                text = english
                for k in keys:
                    if text:
                        break
                    try:
                        text = itemdetails.details(self.kb, k).description or ""
                    except Exception:      # noqa: BLE001 - a page the parser didn't expect: no description
                        text = ""
            text = " ".join(str(text or "").split())
            hebrew = any("\u0590" <= ch <= "\u05ff" for ch in text)
            self._desc[cache] = bidi.plain(text, self.t.rtl) if hebrew else bidi.ltr_block(text, self.t.rtl)
        return self._desc[cache]

    def picture(self, label: QLabel, key: str | None, item: osms.Item | None, px: int) -> None:
        """The KB's picture of the item; else OSMS's, fetched in the background."""
        path = self.kb.picture(key) if key else None
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                label.setPixmap(fit_picture(pm, px, px, label))
                return
        if item is not None and item.thumbnail:
            self._wanted.append((label, item))

    def _fetch_pictures(self) -> None:
        wanted, self._wanted = self._wanted, []
        if not wanted:
            return

        def run():
            for label, item in wanted:
                path = osms.thumbnail_file(item)
                if path is not None:
                    try:
                        self.picture_ready.emit(label, path)
                    except RuntimeError:
                        return
        threading.Thread(target=run, daemon=True, name="osms-pictures").start()

    @staticmethod
    def _set_picture(label: QLabel, path) -> None:
        try:
            pm = QPixmap(str(path))
            if not pm.isNull():
                label.setPixmap(fit_picture(pm, 44, 44, label))
        except RuntimeError:
            pass                    # redrawn meanwhile

    # ------------------------------------------------------------ the tabs

    def pick(self, tab: str, section: str | None = None) -> None:
        from .tools import clear
        self.tab = tab
        self.section = section or osms.SECTIONS[tab][0]
        for x, b in self.tab_buttons.items():
            b.setChecked(x == tab)
        clear(self.sections_box)
        secs = osms.SECTIONS[tab]
        if len(secs) > 1:
            seg = Segmented([(self.t(f"shop_sec_{s}"), s) for s in secs], self.section, self.t.rtl)
            seg.set_label(self.t(f"shop_tab_{tab}"))
            seg.changed.connect(lambda s, x=tab: self.pick(x, s))
            self.sections_box.addWidget(seg, 0, self.align)
            self.sections = seg
        self.stack.setCurrentIndex(1 if self.section == GUIDE else 0)
        if self.section != GUIDE:
            self._refill()

    def rows(self) -> list[osms.Item]:
        return sorted((i for i in self.items or [] if osms.place(i) == (self.tab, self.section)),
                      key=lambda i: (i.nx, i.name))

    def _refill(self) -> None:
        from .tools import clear
        if self.section == GUIDE:
            return
        clear(self.list)
        t = self.t
        if not self._loaded:
            self.status.setText(bidi.plain(t("shop_loading"), t.rtl))
            return
        if self.items is None:
            self.status.setText(bidi.plain(t("shop_offline"), t.rtl))
            return
        self.status.setText(bidi.plain(t("shop_status", n=len(self.items)), t.rtl))
        rows = self.rows()
        for item in rows:
            self.list.addWidget(ShopRow(self, item, self.key_of(item.name)))
        if self.section == "crate":
            self._crate()
        elif not rows:
            empty = QLabel(bidi.plain(t("shop_empty"), t.rtl), objectName="ShopText")
            empty.setAlignment(self.align)
            self.list.addWidget(empty)
        self._fetch_pictures()

    def _crate(self) -> None:
        """What the crate can give, each with its chance: MesoWatch's (Nexon's published rates), else the KB's for
        each crate on sale (NiaMeowDB's, on each item's page). MesoWatch alone used to leave this empty whenever its
        market couldn't be read (blocked, or older than mesowatch.STALE_SECONDS), though the KB has the same chances."""
        t = self.t
        inside = [(s.name, s.crate_odds, self.key_of(s.name)) for s in mesowatch.crate(self.snap)]
        groups, source = ([("", inside)] if inside else []), sources.MESOWATCH
        if not groups:
            source = sources.MEOWDB
            for item in self.rows():
                got = self.crates.get(fold(item.name))
                if got:
                    groups.append((item.name, [((self.kb.get(k) or {}).get("name") or k, p, k) for k, p in got]))
        head = QHBoxLayout()
        title = QLabel(bidi.plain(t("shop_crate_head"), t.rtl), objectName="ShopHead")
        title.setAlignment(self.align)
        head.addWidget(title, 1)
        head.addWidget(source_tag(t, source), 0, Qt.AlignVCenter)
        self.list.addLayout(head)
        if not groups:
            note = QLabel(bidi.plain(t("shop_crate_none"), t.rtl), objectName="ShopText")
            note.setAlignment(self.align)
            self.list.addWidget(note)
            return
        for crate, tiles in groups:
            if crate:                       # the KB's: each crate by name (the Cash Shop can sell a few at once)
                name = QLabel(bidi.ltr_name(crate, t.rtl), objectName="ShopName")
                name.setAlignment(self.align)
                self.list.addWidget(name)
            holder = QWidget()
            holder.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)  # the first tile on the reading side
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(8)
            for n, (name, odds, key) in enumerate(tiles):
                grid.addWidget(CrateTile(self, name, odds, key), n // CRATE_COLUMNS, n % CRATE_COLUMNS)
            for c in range(CRATE_COLUMNS):
                grid.setColumnStretch(c, 1)
            self.list.addWidget(holder)
