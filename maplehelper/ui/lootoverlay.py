"""The loot log over the game: a window of its own (not inside a Maple Helper window) with the log at a glance. Under
its title a strip says how the log stands (● Recording in the accent, Stopped muted) and how long it has run; three
tiles give the EXP, the mesos and what an NPC pays for the items, each per hour, in a colour of its own; the items
follow, most picked first, each with its picture, its count and its NPC value; then the latest pickups, newest on top
and older ones fainter; and its buttons: Start, or Stop and Reset while a log runs. In its title row a – folds it to
that row (what the log made stays in the title) and a ✕ hides it.

It is built like the other windows over the game (the NPCs here window, the searches): the same dark glass in the
tint and at the opacity the player picked for them (npc_overlay_opacity), the accent lighting what is on, the title
row that drags it, muted secondary text and the same rounded buttons. It shows while settings["loot_overlay"] is on:
the bar's Loot log button and the Loot log page's switch flip it, its ✕ turns it off, and a log starting (from
anywhere) turns it on. A stopped log stays in it, to read what it made. Every 5 s (the clock, the per-hour figures
and the "how long ago" move between pickups too) and on every pickup its rows are filled again in place, never made
anew, so nothing flickers. It never takes the keyboard from the game and keeps its place ("loot_overlay_pos") and its
fold ("loot_overlay_folded"). Like the others it stays out of screen captures unless "Show the overlays in
screenshots" is on (its lines never read as pickup messages anyway: no "received", "gained" or "earned" in them)."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from .. import bidi, lootlog
from ..i18n import I18n
from . import theme
from .glass import SHADOW
from .npcoverlay import (BOTTOM, FILL1, FILL2, GEOM_DEBOUNCE_MS, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN,
                         SIDE, TEXT, TOP, _Header, _flags, _paint_glass, accent_of, set_capturable)
from .widgets import fit_picture, zoom_on_hover

WIDTH = 320 + 2 * SHADOW        # the panel 320 px wide (the NPCs window's), past its shadow rim: three tiles abreast
FEED = 4                        # the latest pickups shown under the items
ITEM_ROWS = 5                   # the items listed (most picked first) before "and N more"
STATS = ("exp", "mesos", "items")
TILE_KEYS = {"exp": "loot_card_exp", "mesos": "loot_card_mesos", "items": "loot_card_value"}
SLOT, PIC = 28, 24              # an item row's slot (like an inventory's) and the picture in it
VALUE_PX, VALUE_MIN_PX = 19, 12  # a tile's big number, stepped down to fit its tile ("12.35M" in Rubik)
FEED_PIC = 16                   # a pickup's picture, or the EXP's sparkle and the mesos' coin
WORTH_W = 48                    # the NPC value column: its numbers line up at the rows' end
# each figure in a colour of its own, in its tile and in its pickups' glyph, readable on every glass tint whatever
# accent is picked (the accent lights what is on: the log's state, Start)
STAT_COLORS = {"exp": "#64B5FF", "mesos": "#FFC940", "items": "#B39DFF"}
COIN_EDGE, COIN_RIM = "#E3A21F", "#A8720F"
STOP = "#FF8A80"                # Stop: a warm red that reads on every tint (the crafting window's loss colour)
WARN = theme.WARN_TEXT["dark"]  # the log can't read: no game window, another window over its corner, no reader
FADE = (1.0, 0.84, 0.7, 0.56)   # the latest pickups, newest first: each older one fainter
DOT, SQUARE = "\u25cf", "\u25a0"   # ● Recording, ■ Stopped (the chips' own marks)
BUTTON_ICON = 12                # the buttons' marks (▶ Start, ■ Stop, ↻ Reset) and the clock, in the icon font


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha:.3g})"


def qss(settings) -> str:
    """The window's own style at the accent the settings pick: the Recording chip, the folded title's dot and Start
    light in it. Rebuilt whenever the accent is picked (refresh follows it), so the window follows with no more code
    of its own."""
    accent, on_accent = accent_of(settings)
    tiles = "".join(
        f"QFrame#Tile_{k} {{ background: {_rgba(c, 0.12)}; border: 1px solid {_rgba(c, 0.26)}; border-radius: 10px; }}\n"
        f"#TileName_{k} {{ color: {c}; font-size: 11px; font-weight: 700; }}\n"
        for k, c in STAT_COLORS.items())
    feed = "".join(
        f"#FeedText{i} {{ color: {_rgba(TEXT, a)}; font-size: 12px; }}\n"
        f"#FeedAgo{i} {{ color: {_rgba(TEXT, 0.5 * a)}; font-size: 11px; }}\n"
        for i, a in enumerate(FADE))
    return f"""
QLabel {{ background: transparent; }}
#Title {{ color: {TEXT}; font-weight: 600; }}
#Dot {{ color: {accent}; font-size: 12px; }}
#ChipOn {{ color: {accent}; background: {_rgba(accent, 0.16)}; border: 1px solid {_rgba(accent, 0.5)};
          border-radius: 10px; padding: 1px 9px; font-size: 12px; font-weight: 700; }}
#ChipOff {{ color: {MUTED}; background: {FILL1}; border: 1px solid {FILL2}; border-radius: 10px; padding: 1px 9px;
           font-size: 12px; font-weight: 600; }}
#Clock {{ color: {MUTED}; font-family: "{theme.ICON_FONT}"; font-size: {BUTTON_ICON}px; }}
#Elapsed {{ color: {TEXT}; font-size: 12px; font-weight: 600; }}
#Status {{ color: {WARN}; font-size: 12px; }}
{tiles}#TileValue {{ color: {TEXT}; font-size: {VALUE_PX}px; font-weight: 700; }}
#TileRate {{ color: {MUTED}; font-size: 11px; }}
#Head {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
#HeadNote {{ color: {MUTED}; font-size: 11px; }}
#Slot {{ background: {FILL1}; border-radius: 6px; color: {MUTED}; font-size: 12px; font-weight: 700; }}
#ItemName {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
#Badge {{ color: {TEXT}; background: {FILL2}; border-radius: 8px; padding: 1px 7px; font-size: 11px;
         font-weight: 700; }}
#Worth {{ color: {MUTED}; font-size: 12px; }}
#More {{ color: {MUTED}; font-size: 11px; }}
#FeedIcon {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
{feed}QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 7px 12px;
              font-weight: 600; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton#Go {{ background: {accent}; color: {on_accent}; }}
QPushButton#Go:hover {{ background: {QColor(accent).lighter(112).name()}; }}
QPushButton#Stop {{ background: {_rgba(STOP, 0.16)}; color: {STOP}; }}
QPushButton#Stop:hover {{ background: {_rgba(STOP, 0.28)}; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
"""


def _short(n: int | None) -> str:
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 100_000:
        return f"{n / 1000:.1f}K"
    return f"{n:,}"


def _elapsed(t: I18n, secs: float) -> str:
    """How long the log has run: seconds in its first minute, then the grind tracker's own "5 min" / "1:05 h"."""
    s = int(secs)
    if s < 60:
        return t("loot_secs", n=s)
    m = s // 60
    return t("grind_hours", h=m // 60, m=f"{m % 60:02d}") if m >= 60 else t("grind_minutes", m=m)


def _ago(t: I18n, secs: float) -> str:
    """How long ago a pickup came, as finely as the window's refresh can tell it."""
    s = max(0, int(secs))
    if s < 10:
        return t("loot_ago_now")
    if s < 60:
        return t("loot_ago_secs", n=s)
    if s < 3600:
        return t("price_fm_ago_min", n=s // 60)
    if s < 86400:
        return t("price_fm_ago_h", n=s // 3600)
    return t("price_fm_ago_d", n=s // 86400)


# ---------------------------------------------------------------- pictures and glyphs

def _faded(pm: QPixmap, alpha: float) -> QPixmap:
    """The picture at this opacity (an older pickup's line)."""
    if alpha >= 1.0 or pm.isNull():
        return pm
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setOpacity(alpha)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def _star(cx: float, cy: float, r: float) -> QPainterPath:
    """A four-pointed sparkle: its points on the axes, its sides curved in."""
    path = QPainterPath(QPointF(cx, cy - r))
    k = 0.18 * r
    for (x, y), (sx, sy) in (((cx + r, cy), (1, -1)), ((cx, cy + r), (1, 1)), ((cx - r, cy), (-1, 1)),
                             ((cx, cy - r), (-1, -1))):
        path.quadTo(QPointF(cx + sx * k, cy + sy * k), QPointF(x, y))
    return path


_GLYPHS: dict[tuple, QPixmap] = {}


def _glyph(kind: str, size: int, widget: QWidget, alpha: float = 1.0) -> QPixmap:
    """The EXP's sparkle or the mesos' coin, painted in their tiles' colours at the screen's own pixels: the KB has
    no picture for either, and an emoji coin is missing from older Windows' fonts."""
    dpr = widget.devicePixelRatioF() or 1.0
    key = (kind, size, dpr, alpha)
    pm = _GLYPHS.get(key)
    if pm is None:
        px = max(1, round(size * dpr))
        pm = QPixmap(px, px)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(alpha)
        p.scale(px / 16, px / 16)              # drawn on a 16-unit square
        if kind == "exp":
            c = QColor(STAT_COLORS["exp"])
            p.fillPath(_star(9.5, 9.5, 6.5), c)
            p.fillPath(_star(3.6, 3.6, 2.8), c.lighter(120))
        else:
            g = QRadialGradient(QPointF(6.0, 5.4), 10.5)
            g.setColorAt(0.0, QColor("#FFF6CC"))
            g.setColorAt(0.45, QColor(STAT_COLORS["mesos"]))
            g.setColorAt(1.0, QColor(COIN_EDGE))
            p.setPen(QPen(QColor(COIN_RIM), 1))
            p.setBrush(g)
            p.drawEllipse(QRectF(1.5, 1.5, 13, 13))
            p.setPen(QPen(QColor(COIN_RIM), 0.9))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(5.0, 5.0, 6, 6))           # the coin's inner ring, as the game's meso coins have
        p.end()
        pm.setDevicePixelRatio(dpr)
        _GLYPHS[key] = pm
    return pm


class _Pictures:
    """Each item's sprite, fitted once per size (and faded once per pickup line): the window refreshes every few
    seconds, and each row would read its sprite from disk and scale it again (as ui/craftadvisor.py's)."""

    def __init__(self, kb):
        self.kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple, QPixmap] = {}

    def path(self, key: str):
        if key not in self._paths:
            self._paths[key] = self.kb.picture(key) if key else None
        return self._paths[key]

    def get(self, key: str, size: int, widget: QWidget, alpha: float = 1.0) -> QPixmap:
        """The item's picture fitted to a size x size square; a null pixmap for an item the KB doesn't know."""
        path = self.path(key)
        if not path:
            return QPixmap()
        dpr = widget.devicePixelRatioF()
        k = (key, size, dpr, alpha)
        pm = self._fitted.get(k)
        if pm is None:
            if alpha < 1.0:
                pm = _faded(self.get(key, size, widget), alpha)
            else:
                raw = QPixmap(str(path))
                pm = fit_picture(raw, size, size, widget) if not raw.isNull() else QPixmap()
            self._fitted[k] = pm
        return pm


# ---------------------------------------------------------------- the rows

class _Elided(QLabel):
    """One line ending in "…" where a name is wider than its room ("Orange Mushroom Cap" in a narrow row), the whole
    name in its tooltip. `compose` makes the line from the name as shown (its count beside it, its bidi marks)."""

    def __init__(self, name: str):
        super().__init__(objectName=name)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._name, self._compose = "", str

    def set(self, name: str, compose) -> None:
        self._name, self._compose = name, compose
        self._fit()

    def full(self) -> str:
        return self._compose(self._name)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        self.ensurePolished()
        fm = self.fontMetrics()
        room = self.width() - fm.horizontalAdvance(self._compose(""))
        shown = fm.elidedText(self._name, Qt.ElideRight, max(1, room)) if self._name else ""
        text = self._compose(shown)
        if text != self.text():
            self.setText(text)
        self.setToolTip(self._name if shown != self._name else "")


class _Fit(QLabel):
    """A tile's line, its font stepped down until it fits the tile: three tiles share the panel, and a wide figure
    ("12.35M", "999.9K/h" in the app's Rubik) ran past its tile's edge at full size. Its height stays the full
    size's, so the window never jumps when a figure grows."""

    def __init__(self, name: str, px: int, min_px: int):
        super().__init__(objectName=name)
        self._px, self._min = px, min_px
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)     # the tiles stay equal, whatever it says

    def setText(self, text: str) -> None:
        super().setText(text)
        self._fit()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        self.ensurePolished()
        f = QFont(self.font())
        f.setPixelSize(self._px)
        self.setMinimumHeight(QFontMetrics(f).height())
        room, px = self.contentsRect().width(), self._px
        while px > self._min and QFontMetrics(f).horizontalAdvance(self.text()) > room:
            px -= 1
            f.setPixelSize(px)
        sheet = f"font-size: {px}px;" if px != self._px else ""       # (its own sheet wins over the window's)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)


class _ItemRow(QWidget):
    """One item the log counted: its picture in a slot like the inventory's, its name, a badge with how many, and
    what an NPC pays for them all. Filled again in place on every refresh; its picture only when another item comes
    to the row."""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.pic = QLabel(objectName="Slot", alignment=Qt.AlignCenter)
        self.pic.setFixedSize(SLOT, SLOT)
        self.name = _Elided("ItemName")
        self.badge = QLabel(objectName="Badge", alignment=Qt.AlignCenter)
        self.worth = QLabel(objectName="Worth", alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.worth.setFixedWidth(WORTH_W)
        lay.addWidget(self.pic)
        lay.addWidget(self.name, 1)
        lay.addWidget(self.badge)
        lay.addWidget(self.worth)
        self.key: str | None = None             # the item whose picture the slot shows

    def fill(self, name: str, n: int, key: str, price: int | None, pics: _Pictures, t: I18n) -> None:
        rtl = t.rtl
        if key != self.key:
            self.key = key
            pm = pics.get(key, PIC, self.pic)
            if pm.isNull():                     # an item the KB doesn't know (a name read in part)
                self.pic.setText("?")
                self.pic.setToolTip("")
            else:
                self.pic.setPixmap(pm)
                zoom_on_hover(self.pic, pics.path(key), name)
        self.name.set(name, lambda s: bidi.ltr_name(s, rtl))
        self.badge.setText(bidi.plain(t("loot_badge", n=f"{n:,}"), rtl))
        self.worth.setText(bidi.plain(_short(n * price), rtl) if price else "–")
        self.worth.setToolTip(t("loot_worth_each", n=f"{price:,}") if price else t("sell_no_npc_price"))


class _FeedRow(QWidget):
    """One of the latest pickups: the item's picture (the EXP's sparkle, the mesos' coin), what came and how long
    ago, fainter the further down it is (its place in the feed: its colours are set once)."""

    def __init__(self, i: int):
        super().__init__()
        self.alpha = FADE[i]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)
        self.icon = QLabel(objectName="FeedIcon", alignment=Qt.AlignCenter)
        self.icon.setFixedSize(FEED_PIC + 2, FEED_PIC + 2)
        self.text = _Elided(f"FeedText{i}")
        self.ago = QLabel(objectName=f"FeedAgo{i}", alignment=Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.icon)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.ago)
        self.what: tuple | None = None          # (kind, item) the icon shows

    def fill(self, e: lootlog.Event, now: float, pics: _Pictures, t: I18n) -> None:
        rtl = t.rtl
        if (e.kind, e.key) != self.what:
            self.what = (e.kind, e.key)
            pm = (pics.get(e.key, FEED_PIC, self.icon, self.alpha) if e.kind == "item"
                  else _glyph(e.kind, FEED_PIC, self.icon, self.alpha))
            if pm.isNull():
                self.icon.setText("?")
            else:
                self.icon.setPixmap(pm)
        n = f"{e.amount:,}"
        if e.kind == "item":
            self.text.set(e.name, lambda s: bidi.plain(t("loot_feed_item", name=bidi.ltr_block(s, rtl), n=n), rtl))
        else:
            self.text.set("", lambda s: bidi.plain(t(f"loot_feed_{e.kind}", n=n), rtl))
        self.ago.setText(bidi.plain(_ago(t, now - e.t), rtl))


# ---------------------------------------------------------------- the window

class LootOverlay(QWidget):
    closed = Signal()               # the ✕ turned the setting off: the bar's Loot log button unchecks

    def __init__(self, scanner, settings, kb, t: I18n | None = None):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Maple Helper")
        self.setFixedWidth(WIDTH)
        self.scanner, self.settings, self.kb = scanner, settings, kb
        self.t = t or I18n(settings["language"] or "he")
        self.setLayoutDirection(Qt.RightToLeft if self.t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # shown at least once: there is a place to remember
        self._was_running = scanner.running(scanner.cid())
        self._pics = _Pictures(kb)
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self.setStyleSheet(qss(self.settings))
        self._build()
        self._button_icons()
        # the clock, the per-hour figures and the pickups' "how long ago" move between pickups too
        self._clock = QTimer(self, interval=5000, timeout=lambda: self.isVisible() and self.refresh())
        self._clock.start()

    # ---------------------------------------------------------------- the panel

    def _tool(self, icon: str, then) -> QToolButton:
        b = QToolButton(text=theme.SYMBOL_ICONS[icon])
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(then)
        return b

    def _button(self, then, name: str = "") -> QPushButton:
        b = QPushButton(objectName=name)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(then)
        return b

    @staticmethod
    def _box(spacing: int, top: int = 0) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, top, 0, 0)
        lay.setSpacing(spacing)
        return w, lay

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        head = _Header()
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(6)
        self._dot = QLabel(DOT, objectName="Dot")       # folded while a log runs: still says it records
        self._title = QLabel(objectName="Title")
        self._fold = self._tool("minimize", self._fold_clicked)
        self._close = self._tool("close", self._close_clicked)
        hrow.addWidget(self._dot)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._fold, 0, Qt.AlignTop)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        self._body, body = self._box(8)
        # the strip: how the log stands and how long it has run
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self._chip_on = QLabel(objectName="ChipOn")
        self._chip_off = QLabel(objectName="ChipOff")
        self._clock_icon = QLabel(theme.ICON["timer"], objectName="Clock")
        self._elapsed = QLabel(objectName="Elapsed")
        strip.addWidget(self._chip_on)
        strip.addWidget(self._chip_off)
        strip.addStretch(1)
        strip.addWidget(self._clock_icon)
        strip.addWidget(self._elapsed)
        body.addLayout(strip)
        self._status = QLabel(objectName="Status")      # why nothing counts: no game, covered, no reader
        self._status.setWordWrap(True)
        body.addWidget(self._status)
        # the three figures, each in its own colour
        self._tiles_box = QWidget()
        trow = QHBoxLayout(self._tiles_box)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(6)
        self._tiles: dict[str, tuple[QFrame, _Fit, _Fit, _Fit]] = {}
        for key in STATS:
            tile = QFrame(objectName=f"Tile_{key}")
            tile.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)      # three equal tiles
            v = QVBoxLayout(tile)
            v.setContentsMargins(8, 7, 8, 8)
            v.setSpacing(0)
            name, value, rate = (_Fit(f"TileName_{key}", 11, 9), _Fit("TileValue", VALUE_PX, VALUE_MIN_PX),
                                 _Fit("TileRate", 11, 9))
            for w in (name, value, rate):
                v.addWidget(w)
            trow.addWidget(tile, 1)
            self._tiles[key] = (tile, name, value, rate)
        body.addWidget(self._tiles_box)
        # the items, most picked first
        self._items_box, items = self._box(4, top=4)
        ihead = QHBoxLayout()
        ihead.setSpacing(6)
        self._items_head = QLabel(objectName="Head")
        self._items_count = QLabel(objectName="HeadNote")
        self._items_worth = QLabel(objectName="HeadNote")
        ihead.addWidget(self._items_head)
        ihead.addWidget(self._items_count)
        ihead.addStretch(1)
        ihead.addWidget(self._items_worth)
        items.addLayout(ihead)
        self._rows = [_ItemRow() for _ in range(ITEM_ROWS)]
        for r in self._rows:
            items.addWidget(r)
        self._more = QLabel(objectName="More")
        items.addWidget(self._more)
        body.addWidget(self._items_box)
        # the latest pickups, newest on top
        self._feed_box, feed = self._box(3, top=4)
        self._feed_head = QLabel(objectName="Head")
        feed.addWidget(self._feed_head)
        self._feed = [_FeedRow(i) for i in range(FEED)]
        for r in self._feed:
            feed.addWidget(r)
        body.addWidget(self._feed_box)
        btns = QHBoxLayout()
        btns.setContentsMargins(0, 4, 0, 0)
        btns.setSpacing(6)
        self._start = self._button(self._start_clicked, "Go")
        self._stop = self._button(self._stop_clicked, "Stop")
        self._reset = self._button(self._reset_clicked)
        for b in (self._start, self._stop, self._reset):
            btns.addWidget(b, 1)
        body.addLayout(btns)
        col.addWidget(self._body)

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ---------------------------------------------------------------- what it shows

    def folded(self) -> bool:
        return bool(self.settings["loot_overlay_folded"])

    def lines(self) -> list[str]:
        """Every text it shows now, top to bottom: the title, the state, the figures, the items, the latest pickups."""
        out = [self._title.text()]
        shown = lambda w: w.isVisibleTo(self)
        out += [w.text() for w in (self._chip_on, self._chip_off, self._elapsed, self._status) if shown(w)]
        for key in STATS:
            tile, name, value, rate = self._tiles[key]
            if shown(tile):
                out.append(f"{name.text()} {value.text()} {rate.text()}")
        if shown(self._items_box):
            out.append(f"{self._items_head.text()} {self._items_count.text()} {self._items_worth.text()}")
            out += [f"{r.name.full()} {r.badge.text()} {r.worth.text()}" for r in self._rows if shown(r)]
            out += [self._more.text()] if shown(self._more) else []
        if shown(self._feed_box):
            out.append(self._feed_head.text())
            out += [f"{r.text.full()} {r.ago.text()}" for r in self._feed if shown(r)]
        return out

    def refresh(self) -> None:
        self._restyle()
        t, rtl = self.t, self.t.rtl
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self.settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        if self._pics.kb is not self.kb:        # the app swapped the KB (a database update): its pictures
            self._pics = _Pictures(self.kb)
            for r in self._rows:
                r.key = None
            for r in self._feed:
                r.what = None
        cid = self.scanner.cid()
        lg = self.scanner.log_for(cid)
        running = self.scanner.running(cid)
        now = time.time()
        sm = lootlog.summarize(self.kb, lg, now) if lg is not None else None
        fold = self.folded()
        title = t("tool_loot")
        if sm is not None and fold:              # folded: what it made, nothing longer
            title += " · " + t("loot_card_made", v=bidi.ltr_block(_short(sm.value), rtl))
        self._title.setText(bidi.plain(title, rtl))
        self._title.setToolTip(t("loot_tip_value") if sm is not None and fold else "")     # what "made" counts
        self._dot.setVisible(fold and running)
        self._body.setVisible(not fold)
        if not fold:
            self._fill_state(sm, running)
            self._fill_tiles(sm)
            self._fill_items(sm)
            self._fill_feed(lg, now)
            self._fill_buttons(running)
        self._fold.setText(theme.SYMBOL_ICONS["add" if fold else "minimize"])
        for b, key in ((self._fold, "loot_card_unfold" if fold else "loot_card_fold"), (self._close, "loot_card_close")):
            b.setAccessibleName(t(key))
            b.setToolTip(t(key))
        lay = self.layout()
        lay.activate()
        self.setFixedHeight(lay.heightForWidth(self.width()) if lay.hasHeightForWidth() else lay.sizeHint().height())
        self.update()

    def _fill_state(self, sm: lootlog.Summary | None, running: bool) -> None:
        """The strip: ● Recording (the accent) while the log runs, ■ Stopped (muted) with a log kept to read, and
        how long it has run; the reason nothing counts while it can't read."""
        t, rtl = self.t, self.t.rtl
        self._chip_on.setVisible(running)
        self._chip_off.setVisible(not running)
        if running:
            self._chip_on.setText(bidi.plain(f"{DOT} {t('loot_chip_on')}", rtl))
        else:
            self._chip_off.setText(bidi.plain(f"{SQUARE} {t('loot_chip_off')}" if sm is not None
                                              else t("loot_overlay_idle"), rtl))
            self._chip_off.setToolTip(t("loot_card_stopped") if sm is not None else "")
        self._clock_icon.setVisible(sm is not None)
        self._elapsed.setVisible(sm is not None)
        self._elapsed.setText(bidi.plain(_elapsed(t, sm.seconds), rtl) if sm is not None else "")
        self._elapsed.setAccessibleName(f"{t('loot_cell_time')}: {self._elapsed.text()}")
        state = self.scanner.state
        status = f"loot_{state}" if running and state in ("no_game", "covered", "no_reader") else ""
        self._status.setText(bidi.plain(t(status), rtl) if status else "")
        self._status.setVisible(bool(status))

    def _fill_tiles(self, sm: lootlog.Summary | None) -> None:
        """EXP, mesos and the items' NPC value (the summary's value counts the mesos too), each per hour from the
        first minute on."""
        t, rtl = self.t, self.t.rtl
        self._tiles_box.setVisible(sm is not None)
        if sm is None:
            return
        worth = sm.value - sm.mesos
        figures = {"exp": (sm.exp, sm.exp_h), "mesos": (sm.mesos, sm.mesos_h),
                   "items": (worth, lootlog.per_hour(worth, sm.seconds))}
        for key in STATS:
            _tile, name, value, rate = self._tiles[key]
            n, h = figures[key]
            name.setText(bidi.plain(t(TILE_KEYS[key]), rtl))
            value.setText(bidi.plain(bidi.ltr_block(_short(n), rtl), rtl))
            rate.setText(bidi.plain(t("loot_card_per_h", h=bidi.ltr_block(_short(h), rtl)), rtl))
            rate.setToolTip("" if h is not None else t("loot_tip_wait"))
            value.setAccessibleName(f"{t(TILE_KEYS[key])}: {_short(n)}, {t('loot_card_per_h', h=_short(h))}")
        self._tiles["items"][0].setToolTip(t("loot_tip_items"))

    def _fill_items(self, sm: lootlog.Summary | None) -> None:
        t, rtl = self.t, self.t.rtl
        items = sm.items if sm is not None else []
        self._items_box.setVisible(bool(items))
        if not items:
            return
        self._items_head.setText(bidi.plain(t("loot_items_head") if rtl else t("loot_items_head").upper(), rtl))
        self._items_count.setText(bidi.plain(t("loot_count", n=f"{sm.picked:,}"), rtl))
        self._items_worth.setText(bidi.plain(t("loot_items_npc"), rtl))
        for i, row in enumerate(self._rows):
            if i < len(items):
                row.fill(*items[i], self._pics, t)
            row.setVisible(i < len(items))
        rest = items[ITEM_ROWS:]
        self._more.setText(bidi.plain(t("loot_more", n=f"{len(rest):,}"), rtl) if rest else "")
        # the rest by name, as the latest pickups word them
        self._more.setToolTip("\n".join(t("loot_feed_item", name=bidi.ltr_block(name, rtl), n=f"{n:,}")
                                        for name, n, _key, _price in rest))
        self._more.setVisible(bool(rest))

    def _fill_feed(self, lg: lootlog.Log | None, now: float) -> None:
        t, rtl = self.t, self.t.rtl
        evs = lg.events[-FEED:][::-1] if lg is not None else []
        self._feed_box.setVisible(bool(evs))
        self._feed_head.setText(bidi.plain(t("loot_feed_head") if rtl else t("loot_feed_head").upper(), rtl))
        for i, row in enumerate(self._feed):
            if i < len(evs):
                row.fill(evs[i], now, self._pics, t)
            row.setVisible(i < len(evs))

    def _fill_buttons(self, running: bool) -> None:
        t, rtl = self.t, self.t.rtl
        self._start.setVisible(not running)
        self._stop.setVisible(running)
        self._reset.setVisible(running)
        for b, key in ((self._start, "loot_start"), (self._stop, "loot_card_stop"), (self._reset, "loot_card_reset")):
            b.setText(bidi.plain(" " + t(key), rtl))          # (a space from its mark: Qt sets them flush)
            b.setAccessibleName(t(key))
        self._reset.setToolTip(t("loot_card_reset_tip"))
        self._stop.setToolTip(t("loot_card_stop_tip"))

    def _button_icons(self) -> None:
        """The buttons' marks drawn from the icon font, each in its button's text colour (Start's follows the accent):
        as text, a "↻" inside a Hebrew label fell back to no font and showed a box."""
        _accent, on_accent = accent_of(self.settings)
        for b, icon, colour in ((self._start, "play", on_accent), (self._stop, "stop", STOP),
                                (self._reset, "refresh", TEXT)):
            b.setIcon(theme.glyph_icon(icon, colour, BUTTON_ICON))
            b.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))

    def _restyle(self) -> None:
        """The accent the settings pick, in the window's own style sheet and Start's mark (its glass is painted in
        the tint by _paint_glass); skipped when it is the same — this refreshes every few seconds while a log runs."""
        sheet = qss(self.settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
            self._button_icons()

    def sync(self) -> None:
        """Shown while settings["loot_overlay"] is on; a log that just started turns it on."""
        running = self.scanner.running(self.scanner.cid())
        if running and not self._was_running and not self.settings["loot_overlay"]:
            self.settings["loot_overlay"] = True
        self._was_running = running
        want = bool(self.settings["loot_overlay"])
        if want and not self.isVisible():
            self.show_on_game()
        elif want:
            self.refresh()
        elif self.isVisible():
            self.hide()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self.refresh()

    # ---------------------------------------------------------------- the buttons

    def _start_clicked(self, _=False) -> None:
        self.scanner.start()

    def _stop_clicked(self, _=False) -> None:
        """The log ends (saved in Recent logs and the database); its totals stay here to read."""
        self.scanner.end()

    def _reset_clicked(self, _=False) -> None:
        """Back to zero: the running log ends (kept, as Stop keeps it) and a new one starts at once."""
        self.scanner.start()

    def _fold_clicked(self, _=False) -> None:
        self.settings["loot_overlay_folded"] = not self.folded()
        self.refresh()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's button and the Loot log page's switch follow), hidden until switched
        back on. A running log goes on counting."""
        self.settings["loot_overlay"] = False
        self.hide()
        self.closed.emit()
        self.scanner.changed.emit()

    # ---------------------------------------------------------------- show / place

    def show_on_game(self) -> None:
        self.refresh()
        pos = self.settings["loot_overlay_pos"]
        if isinstance(pos, dict) and all(isinstance(pos.get(k), int) for k in ("x", "y")) and self._on_screen(pos):
            self.move(pos["x"], pos["y"])
        else:
            scr = QGuiApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - WIDTH - 40, scr.top() + 160)
        self._placed = True
        self.show()
        set_capturable(self, self.settings)

    def _on_screen(self, pos: dict) -> bool:
        r = QRect(pos["x"], pos["y"], WIDTH, max(40, self.height()))
        return any(s.availableGeometry().intersects(r) for s in QGuiApplication.screens())

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def hideEvent(self, e) -> None:
        if self._placed:             # hidden or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self.settings["loot_overlay_pos"] = {"x": self.x(), "y": self.y()}
