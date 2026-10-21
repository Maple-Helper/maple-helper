"""The Free Market details window: everything MesoWatch knows about one item's sales, laid out like MesoWatch's own
item card (meso.watch): the usual sale price, the last sale, the last three's average and the sale count, then
where to buy it (the shops seen listing it, cheapest
first, with their owner's name: that is how a player finds the shop) and the newest sales.

The price card in the Play tools keeps the short version and opens this beside the tools window, then wherever the
player last left it (one window: another item's button shows that item here)."""
from __future__ import annotations

import threading

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import bidi, mesowatch, sources
from ..i18n import I18n
from . import theme
from .controls import Segmented, rtl_buttons
from .glass import GlassDialog
from .itemview import beside, saved_spot
from .patchnotes import gutter
from .widgets import fit_picture, source_tag

POS_SETTING = "market_window_pos"     # where the player last left the window: {"x", "y"}
AVATAR = 40                           # a seller's picture, px
POLL_MS = 60 * 1000                   # how often an open window looks for a newer market (the refresh: 15 minutes)
WIDTH, MAX_HEIGHT = 460, 820
SHOPS_SHOWN = 8
SALES_SHOWN = 10


def mesos(n) -> str:
    return f"{n:,}" if n is not None else "-"


def ago(t, ts: float | None) -> str:
    from .tools import market_ago
    return market_ago(t, ts) if ts else ""


# ---------------------------------------------------------------- pictures

_coins: dict[int, QPixmap] = {}


def coin(px: int = 16) -> QPixmap:
    """A gold meso coin, drawn (MesoWatch puts one before every price)."""
    if px in _coins:
        return _coins[px]
    ratio = 2
    pm = QPixmap(px * ratio, px * ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(1, 1, px * ratio - 2, px * ratio - 2)
    g = QRadialGradient(r.center() - QPointF(r.width() * 0.18, r.height() * 0.2), r.width() * 0.7)
    g.setColorAt(0, QColor("#FFF3B0"))
    g.setColorAt(0.45, QColor("#FFC93C"))
    g.setColorAt(1, QColor("#D98E04"))
    p.setPen(QPen(QColor("#B87300"), ratio))
    p.setBrush(g)
    p.drawEllipse(r)
    inner = r.adjusted(r.width() * 0.22, r.height() * 0.22, -r.width() * 0.22, -r.height() * 0.22)
    shine = QLinearGradient(inner.topLeft(), inner.bottomRight())
    shine.setColorAt(0, QColor(255, 255, 255, 150))
    shine.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(QPen(QColor(184, 115, 0, 120), ratio * 0.8))
    p.setBrush(shine)
    p.drawEllipse(inner)
    p.end()
    pm.setDevicePixelRatio(ratio)
    _coins[px] = pm
    return pm


def coin_label(px: int = 16) -> QLabel:
    lb = QLabel()
    lb.setPixmap(coin(px))
    lb.setFixedSize(px, px)
    return lb


def face_pixmap(path, px: int = AVATAR) -> QPixmap | None:
    """A seller's whole character (MesoWatch's picture of it, never cut), fitted into a round, softly tinted frame as
    MesoWatch shows it."""
    img = QImage(str(path))
    if img.isNull():
        return None
    ratio = 2
    size = px * ratio
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 149, 51, 38))
    p.drawEllipse(QRectF(0, 0, size, size))
    clip = QPainterPath()       # a picture with its own background stays round too; the figure is whole inside
    clip.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(clip)
    room = size * 0.86                           # the whole figure inside the circle, its height or width filling it
    scale = min(room / img.width(), room / img.height())
    w, h = img.width() * scale, img.height() * scale
    p.drawImage(QRectF((size - w) / 2, (size - h) / 2, w, h), img)
    p.end()
    out.setDevicePixelRatio(ratio)
    return out


def price_box(text: str, name: str, px: int, rtl: bool) -> QHBoxLayout:
    """A coin and a price, always coin first in reading order of the number (left of it)."""
    row = QHBoxLayout()
    row.setSpacing(5)
    row.setDirection(QHBoxLayout.LeftToRight)
    row.addWidget(coin_label(px), 0, Qt.AlignVCenter)
    lb = QLabel(text, objectName=name)
    row.addWidget(lb, 0, Qt.AlignVCenter)
    return row


# ---------------------------------------------------------------- the window

class MarketDetailsDialog(GlassDialog):
    face_ready = Signal(str, object)      # (seller, path) from the background picture fetch

    def __init__(self, kb, lang: str, stylesheet: str, settings=None, anchor: QRect | None = None):
        self.t = t = I18n(lang or "he")
        super().__init__(t("mw_window"), t.rtl)
        self.setStyleSheet(stylesheet + self._style())
        self.fit_screen(WIDTH, MAX_HEIGHT)
        self.kb, self.settings, self.key, self.sales = kb, settings, None, None
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(*gutter(t.rtl))
        self.lay.setSpacing(10)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 0)
        self.site = QPushButton(bidi.plain(t("mw_open_site"), t.rtl), objectName="Secondary")
        self.site.setCursor(Qt.PointingHandCursor)
        self.site.clicked.connect(self._open_site)
        ok = QPushButton(t("close"), objectName="Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(120)
        ok.clicked.connect(self.accept)
        row.addWidget(self.site)
        row.addStretch(1)
        row.addWidget(ok)
        outer.addLayout(row)
        rtl_buttons(self, t.rtl)
        self._align = (Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute
        self._place(anchor)
        self.finished.connect(lambda *_: self._remember())
        self._avatars: dict[str, list[QLabel]] = {}
        self.face_ready.connect(self._on_face)
        self._version = mesowatch.version()
        self._poll = QTimer(self, interval=POLL_MS, timeout=self._follow)
        self._poll.start()

    def _follow(self) -> None:
        """A newer market came in: the item on screen shows it (the tab and the scroll stay where they were)."""
        if self.key and mesowatch.version() != self._version:
            self._version = mesowatch.version()
            tab = self.tabs.value() if getattr(self, "tabs", None) is not None else None
            at = self.scroll.verticalScrollBar().value()
            self.show_item(self.key)
            if tab and getattr(self, "tabs", None) is not None and tab != self.tabs.value():
                self.tabs.group.buttons()[0 if tab == "buy" else 1].click()
            self.scroll.verticalScrollBar().setValue(at)

    @staticmethod
    def _style() -> str:
        c = theme.P()
        ot = theme.accent_text()
        tile = "rgba(255,255,255,0.05)" if theme.MODE == "dark" else "#FFF8EE"
        return f"""
        #MwName {{ font-size: 19px; font-weight: 700; color: {c['text']}; }}
        #MwSub {{ font-size: 14px; color: {c['muted']}; }}
        #MwPrice {{ font-size: 30px; font-weight: 700; letter-spacing: -0.5px; color: {c['text']}; }}
        #MwPriceLabel {{ font-size: 15px; color: {c['muted']}; }}
        #MwRange {{ font-size: 14px; color: {c['muted']}; }}
        QFrame#MwTile {{ background: {tile}; border: 1px solid rgba(255,149,51,0.35); border-radius: 12px; }}
        #MwTileValue {{ font-size: 19px; font-weight: 700; color: {c['text']}; }}
        #MwTileGlyph {{ font-family: "{theme.ICON_FONT}"; font-size: 15px; color: {ot}; }}
        #MwTileLabel {{ font-size: 13px; font-weight: 600; letter-spacing: 0.4px; color: {c['muted']}; }}
        QFrame#MwList {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px; }}
        QFrame#MwRow {{ background: transparent; border: none; border-bottom: 1px solid {c['hair']}; }}
        #MwRowName {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}
        #MwRowSub {{ font-size: 14px; color: {c['muted']}; }}
        #MwRowPrice {{ font-size: 16px; font-weight: 700; color: {c['text']}; }}
        #MwRowUnit {{ font-size: 13px; color: {c['muted']}; }}
        #MwAvatar {{ background: rgba(255,149,51,0.16); color: {ot}; border-radius: 20px; font-size: 15px;
                     font-weight: 700; min-width: 40px; max-width: 40px; min-height: 40px; max-height: 40px; }}
        #TagWarn, #Tag {{ font-size: 13px; padding: 3px 9px; }}
        #MwFoot {{ font-size: 14px; color: {c['faint']}; }}
        """

    # ------------------------------------------------------------ where it opens

    def _place(self, anchor: QRect | None) -> None:
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        size = self.frameGeometry().size()
        spot = saved_spot(self.settings[POS_SETTING], size, screens) if self.settings is not None else None
        if spot is None and anchor is not None and anchor.isValid():
            spot = beside(anchor, size, screens)
        if spot is not None:
            self.move(spot)

    def _remember(self) -> None:
        if self.settings is not None:
            pt = self.pos()
            self.settings[POS_SETTING] = {"x": pt.x(), "y": pt.y()}

    def _open_site(self) -> None:
        import webbrowser
        webbrowser.open(mesowatch.item_url(self.sales.mw_id if self.sales else None))

    # ------------------------------------------------------------ what it shows

    def show_item(self, key: str) -> None:
        """This item's sales (the copy on hand: the app keeps it within 15 minutes of the site)."""
        from .tools import clear
        t = self.t
        self.key = key
        clear(self.lay)
        e = self.kb.get(key) or {}
        name = e.get("name", key)
        self.setWindowTitle(name)
        snap, s = mesowatch.for_item(self.kb, key, refresh=False)
        self.sales, self.snap = s, snap
        if s is not None and not s.detailed:
            # the item's shops and newest sales are in its own file: fetched in the background, and the version it
            # moves redraws this window (_follow)
            threading.Thread(target=mesowatch.details, args=(s,), daemon=True, name="mesowatch-item").start()
        self._avatars = {}
        self._header(key, name, s)
        if snap is None:
            self._note(t("mw_offline"))
        elif s is None or s.empty:
            self._note(t("mw_none"))
        else:
            self._price(s)
            self._tiles(s)
            self._lists(s)
            self._fetch_faces()
            foot = QLabel(bidi.plain(t("price_mw_updated", ago=ago(t, snap.generated)) + f" · {snap.world}", t.rtl),
                          objectName="MwFoot")
            foot.setAlignment(self._align)
            self.lay.addWidget(foot)
        self.lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _note(self, text: str) -> None:
        lb = QLabel(bidi.plain(text, self.t.rtl), objectName="DialogBody")
        lb.setWordWrap(True)
        lb.setAlignment(self._align)
        self.lay.addWidget(lb)

    def _header(self, key: str, name: str, s) -> None:
        t = self.t
        row = QHBoxLayout()
        row.setSpacing(12)
        pic = QLabel(objectName="MwTile")
        pic.setFixedSize(60, 60)
        pic.setAlignment(Qt.AlignCenter)
        path = self.kb.picture(key)
        if path:
            pm = QPixmap(str(path))
            if not pm.isNull():
                pic.setPixmap(fit_picture(pm, 44, 44, pic))
        row.addWidget(pic, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(bidi.ltr_name(name, t.rtl), objectName="MwName")
        title.setAlignment(self._align)
        title.setWordWrap(True)
        col.addWidget(title)
        if s is not None and (s.asks or s.sold):
            sub = QLabel(bidi.plain(t("mw_counts", asks=f"{s.asks:,}", n=f"{s.sold:,}"), t.rtl), objectName="MwSub")
            sub.setAlignment(self._align)
            col.addWidget(sub)
        row.addLayout(col, 1)
        row.addWidget(source_tag(t, sources.MESOWATCH), 0, Qt.AlignTop)
        self.lay.addLayout(row)

    def _price(self, s) -> None:
        t = self.t
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addLayout(price_box(mesos(s.price), "MwPrice", 28, t.rtl))
        # "usual sale price ?": the "?" says it is the median (the glossary's term, as on the price card)
        from . import terms
        label = terms.label(bidi.paragraph_html(t("mw_typical_sale"), "rtl" if t.rtl else "ltr"), t.lang,
                            "MwPriceLabel")
        row.addWidget(label, 0, Qt.AlignBottom)
        row.addStretch(1)
        if s.low_data:
            row.addWidget(QLabel(bidi.plain(t("mw_few"), t.rtl), objectName="TagWarn"), 0, Qt.AlignVCenter)
        if s.per_set:
            row.addWidget(QLabel(bidi.plain(t("mw_per_set"), t.rtl), objectName="Tag"), 0, Qt.AlignVCenter)
        self.lay.addLayout(row)
        if s.p25 and s.p75 and s.p25 != s.p75:
            rng = QLabel(bidi.plain(t("price_mw_range", low=mesos(s.p25), high=mesos(s.p75)), t.rtl), objectName="MwRange")
            rng.setAlignment(self._align)
            self.lay.addWidget(rng)

    def _tiles(self, s) -> None:
        t = self.t
        grid = QGridLayout()
        grid.setSpacing(8)
        cells = [(mesos(s.last.price) if s.last else "-", t("mw_last_sale"), True),
                 (mesos(s.last3) if s.last3 else "-", t("mw_last3"), True),
                 (f"{s.sold:,}", t("mw_sales_days", days=s.days) if s.days != 1 else t("mw_sales_day"), False)]
        for i, (value, label, money) in enumerate(cells):
            tile = QFrame(objectName="MwTile")
            v = QVBoxLayout(tile)
            v.setContentsMargins(6, 10, 6, 10)
            v.setSpacing(2)
            top = QHBoxLayout()
            top.setDirection(QHBoxLayout.LeftToRight)
            top.addStretch(1)
            if money and value != "-":
                top.addWidget(coin_label(16), 0, Qt.AlignVCenter)
            elif not money:
                top.addWidget(QLabel(theme.ICON["timer"], objectName="MwTileGlyph"), 0, Qt.AlignVCenter)
            top.addWidget(QLabel(value, objectName="MwTileValue"), 0, Qt.AlignVCenter)
            top.addStretch(1)
            b = QLabel(bidi.plain(label, t.rtl), objectName="MwTileLabel")
            b.setAlignment(Qt.AlignCenter)
            b.setWordWrap(True)
            v.addLayout(top)
            v.addWidget(b)
            grid.addWidget(tile, 0, (2 - i) if t.rtl else i)
        self.lay.addLayout(grid)

    def _lists(self, s) -> None:
        t = self.t
        shops = sorted(s.shops, key=lambda x: x.price)[:SHOPS_SHOWN]
        tabs = Segmented([(t("mw_where_to_buy", n=f"{max(s.listing_shops, len(s.shops)):,}"), "buy"),
                          (t("mw_recent_sales"), "sold")], "buy" if shops else "sold", t.rtl)
        tabs.set_label(t("mw_lists"))
        self.lay.addWidget(tabs)
        buy, sold = self._list([self._shop_row(x, s) for x in shops], t("mw_no_shops")), \
            self._list([self._sale_row(r, s) for r in s.recent[:SALES_SHOWN]], t("mw_no_sales"))
        self.lay.addWidget(buy)
        self.lay.addWidget(sold)
        buy.setVisible(bool(shops))
        sold.setVisible(not shops)
        tabs.changed.connect(lambda v: (buy.setVisible(v == "buy"), sold.setVisible(v == "sold")))
        self.tabs, self.buy_list, self.sold_list = tabs, buy, sold

    def _list(self, rows: list[QWidget], empty: str) -> QFrame:
        box = QFrame(objectName="MwList")
        col = QVBoxLayout(box)
        col.setContentsMargins(4, 2, 4, 2)
        col.setSpacing(0)
        for r in rows:
            col.addWidget(r)
        if not rows:
            lb = QLabel(bidi.plain(empty, self.t.rtl), objectName="DialogBody")
            lb.setAlignment(self._align)
            lb.setContentsMargins(10, 10, 10, 10)
            col.addWidget(lb)
        return box

    def _row(self, who: str, sub: str, price, qty_note: str) -> QFrame:
        t = self.t
        row = QFrame(objectName="MwRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(10)
        avatar = QLabel((who or "?")[:1].upper(), objectName="MwAvatar")
        avatar.setAlignment(Qt.AlignCenter)
        h.addWidget(avatar, 0, Qt.AlignVCenter)
        if who:
            self._avatars.setdefault(who, []).append(avatar)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        name = QLabel(bidi.ltr_name(who or t("mw_unknown_shop"), t.rtl), objectName="MwRowName")
        name.setAlignment(self._align)
        mid.addWidget(name)
        if sub:
            info = QLabel(bidi.plain(sub, t.rtl), objectName="MwRowSub")
            info.setAlignment(self._align)
            info.setWordWrap(True)
            mid.addWidget(info)
        h.addLayout(mid, 1)
        end = QVBoxLayout()
        end.setSpacing(0)
        val = price_box(mesos(price), "MwRowPrice", 16, t.rtl)
        val.insertStretch(0 if not t.rtl else -1, 1)
        unit = QLabel(bidi.plain(qty_note, t.rtl), objectName="MwRowUnit")
        unit.setAlignment((Qt.AlignRight if not t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
        end.addLayout(val)
        end.addWidget(unit)
        h.addLayout(end, 0)
        return row

    def _fetch_faces(self) -> None:
        """The sellers' character pictures, in the background (the initials show until they're in)."""
        sellers, snap = list(self._avatars), self.snap

        def run():
            for who in sellers:
                path = mesowatch.face_file(snap, who)
                if path is not None:
                    try:
                        self.face_ready.emit(who, path)
                    except RuntimeError:
                        return          # the window was closed meanwhile
        threading.Thread(target=run, daemon=True, name="mesowatch-faces").start()

    def _on_face(self, who: str, path) -> None:
        pm = face_pixmap(path)
        if pm is None:
            return
        for lb in self._avatars.get(who, []):
            try:
                lb.setText("")
                lb.setPixmap(pm)
                lb.setStyleSheet("background: transparent;")
            except RuntimeError:
                pass                    # redrawn for another item

    def _shop_row(self, x: mesowatch.Shop, s) -> QFrame:
        t = self.t
        bits = [t("mw_spotted", ago=ago(t, x.seen))] if x.seen else []
        if x.location:      # one block that never breaks: "Free Market" | "2" over two lines read wrong
            bits.append(bidi.ltr_block(x.location.replace(" ", " "), t.rtl))
        if x.stock:
            bits.append(t("price_mw_stock", n=x.stock))
        return self._row(x.seller, " · ".join(bits), x.price, t("mw_per_set_short") if s.per_set else t("mw_each"))

    def _sale_row(self, r: mesowatch.Sale, s) -> QFrame:
        t = self.t
        bits = [bidi.ltr_block(r.location.replace(" ", " "), t.rtl)] if r.location else []
        if r.time:
            bits.append(ago(t, r.time))
        note = t("mw_qty", n=r.qty) if r.qty > 1 else (t("mw_per_set_short") if s.per_set else t("mw_each"))
        return self._row(r.seller, " · ".join(bits), r.price, note)


