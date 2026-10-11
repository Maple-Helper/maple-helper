"""The Free Market details window: everything MesoWatch knows about one item's sales, laid out like MesoWatch's own
item card (meso.watch): the typical sale, a chart of the sales (their spread, or the daily medians over time), the
last sale, the last three's average and the sale count, then where to buy it (the shops seen listing it, cheapest
first, with their owner's name: that is how a player finds the shop) and the newest sales.

The price card in the Play tools keeps the short version and opens this beside the tools window, then wherever the
player last left it (one window: another item's button shows that item here)."""
from __future__ import annotations

import math
import threading

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from .. import bidi, mesowatch, sources
from ..i18n import I18n
from . import theme
from .controls import Segmented, rtl_buttons
from .glass import GlassDialog
from .itemview import beside, saved_spot
from .patchnotes import gutter
from .widgets import fit_picture, source_tag

POS_SETTING = "market_window_pos"     # where the player last left the window: {"x", "y"}
WIDTH, MAX_HEIGHT = 460, 820
SHOPS_SHOWN = 8
SALES_SHOWN = 10


def compact(n) -> str:
    """229000 -> "229K", 1080000 -> "1.08M", 0.75 -> "0.75": the chart's axis and marks, as MesoWatch writes them."""
    if n is None:
        return ""
    if n >= 1_000_000:
        v = f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{v}M"
    if n >= 1000:
        v = f"{n / 1000:.1f}".rstrip("0").rstrip(".") if n < 10_000 else f"{round(n / 1000)}"
        return f"{v}K"
    return f"{n:g}" if isinstance(n, float) else str(n)


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


def face_pixmap(path, px: int = 34) -> QPixmap | None:
    """A seller's character (MesoWatch's picture of the whole character) cut to the head and shoulders, in a
    round frame as MesoWatch shows it."""
    img = QImage(str(path))
    if img.isNull():
        return None
    side = min(img.width(), img.height())
    head = img.copy(QRect((img.width() - side) // 2, 0, side, side))     # the top square: the head
    ratio = 2
    out = QPixmap(px * ratio, px * ratio)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)       # pixel art stays crisp
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, px * ratio, px * ratio))
    p.setClipPath(clip)
    p.fillRect(out.rect(), QColor(255, 149, 51, 40))
    p.drawImage(QRectF(px * ratio * 0.08, px * ratio * 0.06, px * ratio * 0.84, px * ratio * 0.84), head)
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


# ---------------------------------------------------------------- the chart

def density(values: list[float], lo: float, hi: float, steps: int = 120) -> list[tuple[float, float]]:
    """A smooth curve of where the sale prices fall (a Gaussian kernel density, Silverman's bandwidth): MesoWatch's
    spread chart. [(price, height 0-1)] from lo to hi."""
    vals = [v for v in values if v is not None]
    if not vals or hi <= lo:
        return []
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0
    bw = max(1.06 * sd * n ** -0.2, (hi - lo) / 40)
    pts = []
    for i in range(steps + 1):
        x = lo + (hi - lo) * i / steps
        pts.append((x, sum(math.exp(-0.5 * ((x - v) / bw) ** 2) for v in vals)))
    top = max(y for _, y in pts) or 1
    return [(x, y / top) for x, y in pts]


class SalesChart(QWidget):
    """MesoWatch's chart, drawn here: "spread" (where the sales fall, the middle half shaded, the typical sale
    marked, each sale a dot and each asking price a ring) or "time" (the daily median with its low-high band)."""

    def __init__(self, t, sales: mesowatch.Sales):
        super().__init__()
        self.t, self.s, self.mode = t, sales, "spread"
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setLayoutDirection(Qt.LeftToRight)       # an axis of prices reads left to right in Hebrew too

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        try:
            (self._time if self.mode == "time" else self._spread)(p)
        finally:
            p.end()

    # colors from the theme: the brand orange for the sales, the text colors for the axes
    def _colors(self):
        c = theme.P()
        return theme.qcolor(theme.ORANGE), theme.qcolor(c["muted"]), theme.qcolor(c["faint"]), theme.qcolor(c["hair"])

    def _font(self, px: int, bold: bool = False) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(px)
        f.setBold(bold)
        return f

    def _spread(self, p: QPainter) -> None:
        orange, muted, faint, hair = self._colors()
        s = self.s
        sales = [r.price for r in s.recent if r.price]
        asks = [x.price for x in s.shops if x.price]
        everything = sales + asks + [v for v in (s.p25, s.p75, s.price) if v]
        if not everything:
            self._empty(p)
            return
        top = sorted(everything)[max(0, math.ceil(len(everything) * 0.97) - 1)]   # one wild ask doesn't squash it
        hi = max(top, s.p75 or 0, s.price or 0) * 1.12
        lo = 0.0
        w, h = self.width(), self.height()
        left, right, plot_top, plot_bottom = 50, w - 10, 22, h - 74     # the rows' names left of the plot

        def x_of(v: float) -> float:
            return left + (min(v, hi) - lo) / (hi - lo) * (right - left)
        curve = density(sales or everything, lo, hi)
        # the middle half, then the curve over it
        if s.p25 and s.p75:
            band = QColor(orange)
            band.setAlpha(60)
            p.fillRect(QRectF(x_of(s.p25), plot_top, max(2.0, x_of(s.p75) - x_of(s.p25)), plot_bottom - plot_top), band)
        if curve:
            path = QPainterPath(QPointF(x_of(curve[0][0]), plot_bottom))
            for x, y in curve:
                path.lineTo(QPointF(x_of(x), plot_bottom - y * (plot_bottom - plot_top) * 0.92))
            path.lineTo(QPointF(x_of(curve[-1][0]), plot_bottom))
            fill = QColor(orange)
            fill.setAlpha(38)
            p.fillPath(path, fill)
            p.setPen(QPen(orange, 2))
            line = QPainterPath(QPointF(x_of(curve[0][0]), plot_bottom - curve[0][1] * (plot_bottom - plot_top) * 0.92))
            for x, y in curve[1:]:
                line.lineTo(QPointF(x_of(x), plot_bottom - y * (plot_bottom - plot_top) * 0.92))
            p.drawPath(line)
        p.setPen(QPen(hair, 1))
        p.drawLine(QPointF(left, plot_bottom), QPointF(right, plot_bottom))
        # the typical sale
        if s.price:
            x = x_of(s.price)
            pen = QPen(orange, 1.5, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, plot_top - 4), QPointF(x, plot_bottom))
            p.setFont(self._font(11, True))
            label = f"{self.t('mw_typical')} {bidi.LRI}{compact(s.price)}{bidi.PDI}"
            tw = p.fontMetrics().horizontalAdvance(label)
            p.drawText(QRectF(min(max(x - tw / 2, left), right - tw), 2, tw + 2, 16), Qt.AlignCenter, label)
        # a dot per sale, a ring per asking price, on their own rows under the curve
        rows = ((self.t("mw_sales_row"), sales, True), (self.t("mw_asks_row"), asks, False))
        p.setFont(self._font(10))
        for i, (name, values, filled) in enumerate(rows):
            y = plot_bottom + 16 + i * 18
            p.setPen(muted)
            p.drawText(QRectF(0, y - 8, left - 8, 16), Qt.AlignLeft | Qt.AlignVCenter, name)
            for v in values:
                cx = x_of(v)
                if filled:
                    dot = QColor(orange)
                    dot.setAlpha(200)
                    p.setPen(Qt.NoPen)
                    p.setBrush(dot)
                    p.drawEllipse(QPointF(cx, y), 4, 4)
                else:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(muted, 1.5))
                    p.drawEllipse(QPointF(cx, y), 4.5, 4.5)
            p.setBrush(Qt.NoBrush)
        # the axis: four prices from 0 to the top
        p.setPen(faint)
        p.setFont(self._font(10))
        for i in range(4):
            v = lo + (hi - lo) * i / 3
            text = compact(round(v)) if v >= 1 else compact(round(v, 2))
            x = x_of(v)
            align = Qt.AlignLeft if i == 0 else Qt.AlignRight if i == 3 else Qt.AlignHCenter
            rect = QRectF(x - (0 if i == 0 else 60 if i == 3 else 30), h - 18, 60, 16)
            p.drawText(rect, align | Qt.AlignVCenter, text)

    def _time(self, p: QPainter) -> None:
        orange, muted, faint, hair = self._colors()
        days = self.s.history
        if not days:
            self._empty(p)
            return
        vals = [v for d in days for v in (d.low, d.high, d.median) if v]
        lo, hi = min(vals) * 0.9, max(vals) * 1.08
        if hi <= lo:
            hi = lo + 1
        w, h = self.width(), self.height()
        left, right, plot_top, plot_bottom = 44, w - 12, 14, h - 30

        def x_of(i: int) -> float:
            return (left + right) / 2 if len(days) == 1 else left + i * (right - left) / (len(days) - 1)

        def y_of(v: float) -> float:
            return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - plot_top)
        p.setPen(QPen(hair, 1))
        for i in range(3):
            y = plot_top + i * (plot_bottom - plot_top) / 2
            p.drawLine(QPointF(left, y), QPointF(right, y))
        p.setPen(faint)
        p.setFont(self._font(10))
        for i in range(3):
            v = hi - i * (hi - lo) / 2
            p.drawText(QRectF(0, plot_top + i * (plot_bottom - plot_top) / 2 - 8, left - 6, 16),
                       Qt.AlignRight | Qt.AlignVCenter, compact(round(v)) if v >= 1 else compact(round(v, 2)))
        # the low-high band, the median line, a dot per day sized by its sales
        band = QPainterPath()
        ups = [QPointF(x_of(i), y_of(d.high or d.median)) for i, d in enumerate(days)]
        downs = [QPointF(x_of(i), y_of(d.low or d.median)) for i, d in enumerate(days)]
        band.moveTo(ups[0])
        for q in ups[1:]:
            band.lineTo(q)
        for q in reversed(downs):
            band.lineTo(q)
        band.closeSubpath()
        fill = QColor(orange)
        fill.setAlpha(45)
        p.fillPath(band, fill)
        p.setPen(QPen(orange, 2.2))
        line = QPainterPath(QPointF(x_of(0), y_of(days[0].median)))
        for i, d in enumerate(days[1:], 1):
            line.lineTo(QPointF(x_of(i), y_of(d.median)))
        p.drawPath(line)
        most = max(d.sold for d in days) or 1
        p.setPen(Qt.NoPen)
        p.setBrush(orange)
        for i, d in enumerate(days):
            p.drawEllipse(QPointF(x_of(i), y_of(d.median)), 2.5 + 3 * d.sold / most, 2.5 + 3 * d.sold / most)
        p.setBrush(Qt.NoBrush)
        p.setPen(faint)
        step = max(1, math.ceil(len(days) / 6))
        for i in range(0, len(days), step):
            dd = days[i].date[8:10].lstrip("0") + "." + days[i].date[5:7].lstrip("0")
            p.drawText(QRectF(x_of(i) - 24, h - 20, 48, 16), Qt.AlignCenter, dd)

    def _empty(self, p: QPainter) -> None:
        p.setPen(self._colors()[1])
        p.drawText(self.rect(), Qt.AlignCenter, self.t("mw_no_chart"))


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

    @staticmethod
    def _style() -> str:
        c = theme.P()
        ot = theme.accent_text()
        tile = "rgba(255,255,255,0.05)" if theme.MODE == "dark" else "#FFF8EE"
        return f"""
        #MwName {{ font-size: 19px; font-weight: 700; color: {c['text']}; }}
        #MwSub {{ font-size: 13px; color: {c['muted']}; }}
        #MwPrice {{ font-size: 30px; font-weight: 700; letter-spacing: -0.5px; color: {c['text']}; }}
        #MwPriceLabel {{ font-size: 14px; color: {c['muted']}; }}
        #MwRange {{ font-size: 13px; color: {c['muted']}; }}
        QFrame#MwTile {{ background: {tile}; border: 1px solid rgba(255,149,51,0.35); border-radius: 12px; }}
        #MwTileValue {{ font-size: 18px; font-weight: 700; color: {c['text']}; }}
        #MwTileGlyph {{ font-family: "{theme.ICON_FONT}"; font-size: 15px; color: {ot}; }}
        #MwTileLabel {{ font-size: 11px; font-weight: 600; letter-spacing: 0.4px; color: {c['muted']}; }}
        QFrame#MwList {{ background: {c['fill1']}; border: 1px solid {c['hair']}; border-radius: 14px; }}
        QFrame#MwRow {{ background: transparent; border: none; border-bottom: 1px solid {c['hair']}; }}
        #MwRowName {{ font-size: 15px; font-weight: 600; color: {c['text']}; }}
        #MwRowSub {{ font-size: 12px; color: {c['muted']}; }}
        #MwRowPrice {{ font-size: 15px; font-weight: 700; color: {c['text']}; }}
        #MwRowUnit {{ font-size: 11px; color: {c['muted']}; }}
        #MwAvatar {{ background: rgba(255,149,51,0.16); color: {ot}; border-radius: 17px; font-size: 14px;
                     font-weight: 700; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; }}
        #MwLegend {{ font-size: 12px; color: {c['muted']}; }}
        #MwFoot {{ font-size: 12px; color: {c['faint']}; }}
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
        self._avatars = {}
        self._header(key, name, s)
        if snap is None:
            self._note(t("price_mw_offline"))
        elif s is None or s.empty:
            self._note(t("price_mw_none"))
        else:
            self._price(s)
            self._chart(s)
            self._tiles(s)
            self._facts(s)
            self._lists(s)
            self._fetch_faces()
            foot = QLabel(bidi.plain(t("price_mw_updated", ago=ago(t, snap.generated)) + f" · {snap.world}", t.rtl),
                          objectName="MwFoot")
            foot.setAlignment(self._align)
            self.lay.addWidget(foot)
        self.lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _note(self, text: str) -> None:
        lb = QLabel(bidi.plain(text, self.t.rtl), objectName="RowHint")
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
        label = QLabel(bidi.plain(t("mw_typical_sale"), t.rtl), objectName="MwPriceLabel")
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

    def _chart(self, s) -> None:
        t = self.t
        box = QFrame(objectName="MwList")
        col = QVBoxLayout(box)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        top = QHBoxLayout()
        legend = QLabel(bidi.plain(t("mw_legend"), t.rtl), objectName="MwLegend")
        top.addWidget(legend, 1)
        chart = SalesChart(t, s)
        seg = Segmented([(t("mw_over_time"), "time"), (t("mw_spread"), "spread")], "spread", t.rtl)
        seg.changed.connect(lambda v: (chart.set_mode(v),
                                       legend.setText(bidi.plain(t("mw_legend" if v == "spread" else "mw_legend_time"),
                                                                 t.rtl))))
        seg.set_label(t("mw_chart"))
        top.addWidget(seg, 0)
        col.addLayout(top)
        col.addWidget(chart)
        self.chart, self.chart_mode = chart, seg
        self.lay.addWidget(box)

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

    def _facts(self, s) -> None:
        """The trend, what shops ask and the Cash Shop, as small tags under the tiles."""
        t = self.t
        tags = []
        if s.trend_pct is not None and s.trend_days:
            arrow = "▲" if s.trend_pct > 0 else "▼" if s.trend_pct < 0 else "="
            tags.append((t("price_mw_trend", trend=f"{arrow} {abs(s.trend_pct)}%", days=s.trend_days, n=s.trend_days),
                         "TagGood" if s.trend_pct > 0 else "TagWarn" if s.trend_pct < 0 else "Tag"))
        if s.ask:
            tags.append((t("price_mw_ask", price=mesos(s.ask)), "Tag"))
        if s.cash and s.cash_on_sale is not None:
            tags.append((t("price_mw_cash_nx", nx=f"{s.nx:,}") if s.cash_on_sale and s.nx
                         else t("price_mw_cash_on") if s.cash_on_sale else t("price_mw_cash_off"), "Tag"))
        if s.scrolled_only:
            tags.append((t("price_mw_scrolled"), "TagWarn"))
        if not tags:
            return
        from .controls import FlowLayout
        holder = QWidget()
        flow = FlowLayout(holder)
        holder.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        for text, kind in tags:
            flow.addWidget(QLabel(bidi.plain(text, t.rtl), objectName=kind))
        self.lay.addWidget(holder)

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
            lb = QLabel(bidi.plain(empty, self.t.rtl), objectName="RowHint")
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
        if x.location:
            bits.append(bidi.ltr_block(x.location, t.rtl))
        if x.stock:
            bits.append(t("price_mw_stock", n=x.stock))
        return self._row(x.seller, " · ".join(bits), x.price, t("mw_per_set_short") if s.per_set else t("mw_each"))

    def _sale_row(self, r: mesowatch.Sale, s) -> QFrame:
        t = self.t
        bits = [bidi.ltr_block(r.location, t.rtl)] if r.location else []
        if r.time:
            bits.append(ago(t, r.time))
        note = t("mw_qty", n=r.qty) if r.qty > 1 else (t("mw_per_set_short") if s.per_set else t("mw_each"))
        return self._row(r.seller, " · ".join(bits), r.price, note)


