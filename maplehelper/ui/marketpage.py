"""The Play tools' Free Market page: every item MesoWatch saw on the live world's market, by MesoWatch's own tabs
(Equips, Weapons, Scrolls, Use, Etc, Stars, Cash Shop) and the Pets tab (the page it replaced). Each row is the item's
picture, name and what it is (its KB description), its typical sale price and how much it sells; a click opens the
item's full card (ui/marketview.py). The page follows MesoWatch by itself: the app refreshes the market every 15
minutes (mesowatch.py), and a newer market redraws the list, so an item that starts selling shows up and one that
stops drops out; a Cash Shop item says whether the Cash Shop sells it now."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import bidi, itemdetails, mesowatch, sources, translations
from .controls import FlowLayout, Segmented
from .marketview import coin_label, mesos
from .widgets import MARKET_REQUESTS, fit_picture, source_tag

PAGE_ROWS = 40                 # rows drawn at once; "Show more" draws the next ones
DESC_CHARS = 110               # a description cut to about two lines
NEW_DAYS = 2                   # first seen selling this recently: a "New" tag
POLL_MS = 60 * 1000            # how often the page looks for a newer market (the refresh itself is every 15 minutes)
CAT_KEYS = {"Equips": "fm_cat_equips", "Weapons": "fm_cat_weapons", "Scrolls": "fm_cat_scrolls", "Use": "fm_cat_use",
            "Etc": "fm_cat_etc", "Stars": "fm_cat_stars", "Cash Shop": "fm_cat_cash"}
PETS = "pets"


def shorten(text: str, limit: int = DESC_CHARS) -> str:
    """About two lines of a description, cut at a word with "…" (cut by height, a third line showed half drawn);
    the whole text is on the item's card."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return cut + "…"


class ItemRow(QFrame):
    """One item: picture, name and description on the reading side, the price on the other; click: its full card."""

    def __init__(self, page: "MarketPage", s: mesowatch.Sales, key: str | None):
        super().__init__(objectName="Card")
        t = page.t
        self.key = key
        if key:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(t("fm_open_details"))
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(40, 40)
        pic.setAlignment(Qt.AlignCenter)
        path = page.kb.picture(key) if key else None
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(fit_picture(pm, 36, 36, pic))
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        name = (page.kb.get(key) or {}).get("name") if key else None
        title = QLabel(bidi.ltr_name(name or s.name, t.rtl), objectName="FmName")
        head.addWidget(title, 0)
        if s.first_day and page.is_new(s.first_day):
            head.addWidget(QLabel(bidi.plain(t("fm_new"), t.rtl), objectName="TagAccent"), 0)
        if s.low_data:
            head.addWidget(QLabel(bidi.plain(t("mw_few"), t.rtl), objectName="TagWarn"), 0)
        head.addStretch(1)
        col.addLayout(head)
        desc = page.describe(key)
        if desc:
            lb = QLabel(desc, objectName="FmText")
            lb.setWordWrap(True)
            lb.setAlignment(page.align)
            col.addWidget(lb)
        row.addLayout(col, 1)
        end = QVBoxLayout()
        end.setSpacing(1)
        price = QHBoxLayout()
        price.setSpacing(4)
        price.setDirection(QHBoxLayout.LeftToRight)
        price.addStretch(1)
        if s.price:
            price.addWidget(coin_label(15), 0, Qt.AlignVCenter)
        price.addWidget(QLabel(mesos(s.price) if s.price else "-", objectName="FmPrice"), 0, Qt.AlignVCenter)
        end.addLayout(price)
        bits = [t("fm_sold", n=f"{s.sold:,}")] if s.sold else []
        if s.cash and s.cash_on_sale is not None:
            bits.append(t("fm_cash_on") if s.cash_on_sale else t("fm_cash_off"))
        if bits:
            sub = QLabel(bidi.plain(" · ".join(bits), t.rtl), objectName="FmText")
            sub.setAlignment((Qt.AlignLeft if t.rtl else Qt.AlignRight) | Qt.AlignAbsolute)
            end.addWidget(sub)
        row.addLayout(end, 0)

    def mousePressEvent(self, e):
        if self.key and e.button() == Qt.LeftButton:
            MARKET_REQUESTS.requested.emit(self.key)
        super().mousePressEvent(e)


class MarketPage(QWidget):
    ready = Signal(object)          # the snapshot (or None) from the background read

    def __init__(self, kb, t, pets: QWidget | None = None):
        super().__init__()
        self.kb, self.t = kb, t
        self.align = (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        self.snap, self.keys, self._desc = None, {}, {}
        self.category, self.shown, self._version = "Equips", PAGE_ROWS, -1
        from . import theme
        c = theme.P()
        # no small text (the owner): descriptions, counts and the status at the body size
        self.setStyleSheet(f"#FmPrice {{ font-size: 16px; font-weight: 700; color: {c['text']}; }}"
                           f"#FmText {{ font-size: 14px; color: {c['muted']}; }}"
                           f"#FmName {{ font-size: 15px; font-weight: 600; color: {c['text']}; }}"
                           "#TagAccent, #TagWarn { font-size: 13px; padding: 3px 9px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        top = QHBoxLayout()
        intro = QLabel(bidi.plain(t("fm_intro"), t.rtl), objectName="FmText")
        intro.setWordWrap(True)
        intro.setAlignment(self.align)
        top.addWidget(intro, 1)
        top.addWidget(source_tag(t, sources.MESOWATCH), 0, Qt.AlignTop)
        lay.addLayout(top)
        # the tabs: MesoWatch's categories, then Pets
        self.tabs_box = QWidget()
        self.tabs_box.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self.tabs = FlowLayout(self.tabs_box)
        self.tab_buttons: dict[str, QPushButton] = {}
        for cat in list(mesowatch.CATEGORIES) + [PETS]:
            b = QPushButton(bidi.plain(t("tool_pets") if cat == PETS else t(CAT_KEYS[cat]), t.rtl), objectName="Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, c=cat: self.pick(c))
            self.tabs.addWidget(b)
            self.tab_buttons[cat] = b
        lay.addWidget(self.tabs_box)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        # the market list
        market = QWidget()
        ml = QVBoxLayout(market)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        tools = QHBoxLayout()
        self.search = QLineEdit(placeholderText=t("fm_search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self._refill(reset=True))
        tools.addWidget(self.search, 1)
        self.sort = Segmented([(t("fm_sort_sold"), "sold"), (t("fm_sort_cheap"), "cheap"), (t("fm_sort_dear"), "dear")],
                              "sold", t.rtl)
        self.sort.set_label(t("fm_sort"))
        self.sort.changed.connect(lambda *_: self._refill(reset=True))
        tools.addWidget(self.sort, 0)
        ml.addLayout(tools)
        self.status = QLabel(bidi.plain(t("fm_loading"), t.rtl), objectName="FmText")
        self.status.setAlignment(self.align)
        ml.addWidget(self.status)
        self.list = QVBoxLayout()
        self.list.setSpacing(6)
        ml.addLayout(self.list)
        self.more = QPushButton(bidi.plain(t("fm_more"), t.rtl), objectName="Secondary")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.clicked.connect(self._show_more)
        self.more.hide()
        ml.addWidget(self.more, 0, Qt.AlignHCenter)
        ml.addStretch(1)
        from .patchnotes import gutter
        ml.setContentsMargins(*gutter(t.rtl))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        market.setObjectName("Feed")
        self.scroll.setWidget(market)
        self.stack.addWidget(self.scroll)
        self.pets = pets or QWidget()
        self.stack.addWidget(self.pets)
        self.ready.connect(self._on_ready)
        self.timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self.timer.start()
        self.pick("Equips")
        self.load()

    # ------------------------------------------------------------ data

    def load(self) -> None:
        """The market in the background (the first one of a run may download it)."""
        def run():
            try:
                snap = mesowatch.snapshot()
            except Exception:      # noqa: BLE001 - shown as "can't reach MesoWatch"
                snap = None
            try:
                self.ready.emit(snap)
            except RuntimeError:
                pass                # the window was closed meanwhile
        threading.Thread(target=run, daemon=True, name="mesowatch-page").start()

    def _poll(self) -> None:
        """A newer market came in (the app's 15-minute refresh): the list follows it."""
        if mesowatch.version() != self._version:
            self.load()

    def _on_ready(self, snap) -> None:
        self._version = mesowatch.version()
        self.snap = snap
        self.keys = self._match(snap) if snap else {}
        for cat, b in self.tab_buttons.items():
            if cat != PETS:
                n = sum(1 for s in snap.items.values() if s.category == cat) if snap else 0
                b.setText(bidi.plain(f"{self.t(CAT_KEYS[cat])} · {n}" if n else self.t(CAT_KEYS[cat]), self.t.rtl))
        self._refill(reset=False)

    def _match(self, snap) -> dict[int, str]:
        """MesoWatch's game id -> the KB item it is (mesowatch.match, once per market)."""
        out: dict[int, str] = {}
        for k in getattr(self.kb, "entities", {}):
            if k.startswith("item/"):
                gid = mesowatch.match(snap, self.kb, k)
                if gid in snap.items and gid not in out:
                    out[gid] = k
        return out

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

    def is_new(self, day: str) -> bool:
        try:
            first = time.mktime(time.strptime(day[:10], "%Y-%m-%d"))
        except ValueError:
            return False
        return self.snap is not None and self.snap.generated - first < NEW_DAYS * 86400

    # ------------------------------------------------------------ the list

    def pick(self, cat: str) -> None:
        self.category = cat
        for c, b in self.tab_buttons.items():
            b.setChecked(c == cat)
        self.stack.setCurrentIndex(1 if cat == PETS else 0)
        if cat != PETS:
            self._refill(reset=True)

    def rows(self) -> list[mesowatch.Sales]:
        """The items of the picked tab, searched and sorted."""
        if not self.snap:
            return []
        q = " ".join(self.search.text().split()).lower()
        items = [s for s in self.snap.items.values() if s.category == self.category and not s.empty]
        if q:
            items = [s for s in items if q in s.name.lower()
                     or q in str((self.kb.get(self.keys.get(s.game_id, "")) or {}).get("name", "")).lower()]
        how = self.sort.value()
        if how == "cheap":
            items.sort(key=lambda s: (s.price is None, s.price or 0))
        elif how == "dear":
            items.sort(key=lambda s: (s.price is None, -(s.price or 0)))
        else:
            items.sort(key=lambda s: (-s.sold, s.name))
        return items

    def _refill(self, reset: bool) -> None:
        from .tools import clear, market_ago
        if self.category == PETS:
            return
        if reset:
            self.shown = PAGE_ROWS
        clear(self.list)
        t = self.t
        if self.snap is None:
            self.status.setText(bidi.plain(t("fm_loading") if self._version < 0 else t("price_mw_offline"), t.rtl))
            self.more.hide()
            return
        items = self.rows()
        self.status.setText(bidi.plain(t("fm_status", n=f"{len(items):,}", world=self.snap.world,
                                         ago=market_ago(t, self.snap.generated)), t.rtl))
        for s in items[:self.shown]:
            self.list.addWidget(ItemRow(self, s, self.keys.get(s.game_id)))
        if not items:
            empty = QLabel(bidi.plain(t("fm_empty"), t.rtl), objectName="FmText")
            empty.setAlignment(self.align)
            self.list.addWidget(empty)
        left = len(items) - self.shown
        self.more.setVisible(left > 0)
        if left > 0:
            self.more.setText(bidi.plain(t("fm_more_n", n=min(left, PAGE_ROWS)), t.rtl))

    def _show_more(self) -> None:
        self.shown += PAGE_ROWS
        self._refill(reset=False)
