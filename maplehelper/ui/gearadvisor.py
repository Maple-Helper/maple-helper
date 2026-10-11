"""The gear upgrade advisor's window over the game (the toolbar's Gear button, ui/gametoolbar.py), made to be read at
a glance, top to bottom:

- the character: name · job · level, the gender box, and "Your stats" — the STR/DEX/INT/LUK boxes the gear's
  requirements are checked against (saved to the character), folded to one line once typed;
- your gear: a tile per equip slot the class uses, laid out like the game's equip window — the worn item's picture
  (an empty slot: its outline and the slot's word) and an accent badge counting the upgrades wearable now. A click
  picks the slot (ringed in the accent); clicking it again opens the picker that sets what is worn there;
- the picked slot: a one-line answer (gearadvice.slot_summary's facts, worded here), what you wear (its picture and
  stats; Change opens the picker: a search over the slot's items, with pictures), then the upgrades as cards — the
  best first and marked — each with its picture, its level, its stat changes as coloured chips (gearadvice.changes:
  a gain green, a loss red, the same muted), where to get it (the NPC's price and seller; else the usual Free
  Market price, live from MeowDB, and the monsters that drop it) and "Plan scrolls" for an item with upgrade slots
  (the scroll planner opens on it); the ones a few levels away under "Soon";
- "All upgrades": the best upgrade of every slot, a compact row each (a click opens that slot).

Every number comes from the KB's tables via gearadvice.py, from the player's own input, or from MeowDB's live market
(that line says so); nothing is guessed here. Built like the other windows over the game: the NPCs window's glass in
the tint, accent and opacity the ⚙ panel picks, the title row that drags it, its place kept in "gear_advisor_geom",
its ✕ turning "gear_advisor" off. It takes the keyboard while a stat box or the worn-item search is typed in (Esc
gives it back)."""
from __future__ import annotations

import logging
import re
import threading

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QStyle, QStyleOptionComboBox, QToolButton, QVBoxLayout, QWidget)

from .. import bidi, gearadvice, itemterms, market
from ..gearadvice import LABELS, SPEED, SPEED_LABEL, STAT_KEYS, SlotPlan, Summary, Upgrade
from ..i18n import NBSP, I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles, Settings
from . import mapview, theme
from .controls import FlowLayout, Segmented
from .gamesearch import QSS as _GLASS_QSS, _SearchBox
from .glass import EdgeResize
from .npcoverlay import (BOTTOM, FILL1, FILL2, GEOM_DEBOUNCE_MS, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN,
                         SIDE, TEXT, TOP, _Header, _paint_glass, accent_of, set_capturable)
from .widgets import fit_picture, zoom_on_hover

log = logging.getLogger("maplehelper")

MIN_W, MIN_H = 380, 520             # the smallest the player can drag the window to (five tiles a row still fit)
DEFAULT_W, DEFAULT_H = 460, 720     # what opens under the toolbar before it is ever resized
GAP = 8                             # between the toolbar and the window that opens under it
SHOW_KEY, GEOM_KEY = "gear_advisor", "gear_advisor_geom"
TYPE_DEBOUNCE_MS = 150              # a pause in the picker's typing before its rows are read again
PICK_ROWS = 8                       # rows the picker lists before the "and N more" line
STAT_DEBOUNCE_MS = 350              # a pause in a stat box before the character is saved and the advice re-read
GUTTER = 4                          # between the panel's cards and its scrollbar
GRID_COLS = 5                       # tiles a row: an Assassin's ten slots in two rows
TILE_H, TILE_MIN_W = 66, 56         # a tile's height, and the narrowest it gets (at MIN_W)
TILE_PIC, CARD_PIC, ROW_PIC = 36, 40, 30   # the pictures: the grid's, the cards', the picker's and overview's rows
BADGE = 16                          # the tile's upgrade-count badge
VIEWS = ("slot", "all")             # the panel: the picked slot, or every slot's best upgrade
GENDERS = ("", "Male", "Female")    # what a player can say about their character ("" = not said)
GAIN, LOSS = "#4CD07D", "#FF8A80"   # a stat gained / given up: readable on every glass tint, whatever the accent
# where to get it, as an icon-like tag before the line: an NPC sells it, the Free Market, monsters drop it
SHOP, MARKET, DROPPED = "\U0001F6D2", "\U0001F3F7\ufe0f", "\u2694\ufe0f"
# the grid's order, the game's equip window's read in rows: what is held, the head and the back, then the body top
# to toe (a class's weapon types share one tile, the first; a slot the class never uses has no tile)
GRID_ORDER = gearadvice.WEAPON_SLOTS + ("Shield", "Hat", "Earring", "Cape", "Top", "Overall", "Bottom", "Gloves",
                                        "Shoes")


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})"


def qss(settings) -> str:
    """The window's own style at the accent the settings pick: the picked tile's ring, the badges, the lit view
    switch, the answer and the best card, the Plan scrolls buttons (npcoverlay.ACCENTS; the glass itself is painted
    in the tint by _paint_glass). Built again whenever the accent is picked, so the window follows with no code of
    its own."""
    fill, on_fill = accent_of(settings)
    chip = "border-radius: 6px; padding: 1px 6px; font-size: 11px; font-weight: 700;"
    return _GLASS_QSS + f"""
QLabel {{ background: transparent; }}
#Char {{ color: {TEXT}; font-weight: 600; }}
#Hint, #Note {{ color: {MUTED}; font-size: 11px; }}
#StatsHead {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
#StatsLine {{ color: {TEXT}; font-size: 12px; }}
#StatName {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLineEdit#Stat {{ padding: 5px 4px; }}
QComboBox {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 4px 10px;
             min-height: 20px; }}
QComboBox:hover {{ background: {FILL2}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: none; border: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: #242427; color: {TEXT}; selection-background-color: {FILL2};
                              border-radius: 8px; outline: none; }}
QPushButton#Small {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 7px; padding: 3px 10px;
                    font-size: 12px; font-weight: 600; text-align: center; }}
QPushButton#Small:hover {{ background: {FILL2}; }}
QPushButton#Plan, QPushButton#Act {{ background: transparent; color: {fill}; border: 1px solid {fill};
                                     border-radius: 7px; padding: 3px 10px; font-size: 12px; font-weight: 700;
                                     text-align: center; }}
QPushButton#Plan:hover, QPushButton#Act:hover {{ background: {_rgba(fill, 0.18)}; }}
QFrame#Tile {{ background: {FILL1}; border: 1px solid rgba(255,255,255,0.10); border-radius: 10px; }}
QFrame#Tile:hover {{ background: {FILL2}; }}
QFrame#Tile[empty="true"] {{ background: transparent; border: 1px dashed rgba(255,255,255,0.30); }}
QFrame#Tile[empty="true"]:hover {{ background: {FILL1}; }}
QFrame#Tile[sel="true"] {{ border: 2px solid {fill}; background: {_rgba(fill, 0.14)}; }}
#TileWord {{ color: {MUTED}; font-size: 10px; font-weight: 600; }}
#Badge {{ background: {fill}; color: {on_fill}; border-radius: {BADGE // 2}px; padding: 0 4px; font-size: 10px;
          font-weight: 700; }}
#Segmented {{ background: {FILL1}; border: none; border-radius: 10px; }}
QPushButton#Segment {{ background: transparent; color: {MUTED}; border: none; border-radius: 8px; min-height: 26px;
                       max-height: 26px; padding: 0 10px; font-size: 13px; font-weight: 600; text-align: center; }}
QPushButton#Segment:hover:!checked {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Segment:checked {{ background: {fill}; color: {on_fill}; font-weight: 700; }}
#Answer {{ background: {_rgba(fill, 0.16)}; color: {TEXT}; border-radius: 10px; padding: 8px 10px;
           font-size: 13px; }}
QFrame#Card {{ background: {FILL1}; border: 1px solid transparent; border-radius: 12px; }}
QFrame#Card[best="true"] {{ background: {_rgba(fill, 0.10)}; border: 1px solid {fill}; }}
#Caption {{ color: {MUTED}; font-size: 10px; font-weight: 700; }}
#BestCaption {{ color: {fill}; font-size: 10px; font-weight: 700; }}
#ItemName {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#ItemMuted {{ color: {MUTED}; font-size: 13px; }}
#Chip, #ChipLv {{ color: {TEXT}; background: rgba(255,255,255,0.10); {chip} }}
#ChipSoon {{ color: {MUTED}; background: transparent; border: 1px solid rgba(255,255,255,0.22); {chip} }}
#ChipGain {{ color: {GAIN}; background: {_rgba(GAIN, 0.15)}; {chip} }}
#ChipLoss {{ color: {LOSS}; background: {_rgba(LOSS, 0.15)}; {chip} }}
#ChipSame {{ color: {MUTED}; background: rgba(255,255,255,0.06); {chip} }}
#Where {{ color: {TEXT}; font-size: 12px; }}
#WhereMuted {{ color: {MUTED}; font-size: 12px; }}
#SoonHead {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#SoonSub {{ color: {MUTED}; font-size: 11px; }}
#EmptyPic {{ border: 1px dashed rgba(255,255,255,0.30); border-radius: 8px; }}
QFrame#Row[worn="true"] {{ background: {_rgba(fill, 0.18)}; }}
QFrame#AllRow {{ background: {FILL1}; border: none; border-radius: 10px; }}
QFrame#AllRow:hover {{ background: {FILL2}; }}
#AllSlot {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
"""


def _sex_word(t: I18n, sex: str) -> str:
    """"Male"/"Female" as the player says it: the game's English word, Hebrew's זכר/נקבה."""
    return t("ga_gender_female") if sex == "Female" else t("ga_gender_male") if sex == "Male" else sex


def _slot_word(t: I18n, slot: str) -> str:
    """A slot's name in the player's language (itemterms' own translations: Claw -> טופר)."""
    return itemterms.kind(slot, t.lang)


def _name(row: dict | None) -> str:
    return str((row or {}).get("item") or "")


def _match(name: str, query: str) -> bool:
    """A picker row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


def _market_id(key: str) -> int | None:
    """The item's MeowDB id behind its KB key ("item/690" -> 690: the site's own item-db number), else None."""
    cat, _, slug = str(key).partition("/")
    return int(slug) if cat == "item" and slug.isdigit() else None


def _whole(text: str) -> str:
    """A short English piece ("Mithril Guards", "+10 W.ATK", "69,900 mesos") that a line never wraps inside: in a
    Hebrew line its halves landed on two lines in mirrored order. A long one may still wrap."""
    return text.replace(" ", NBSP) if len(text) <= bidi.KEEP_TOGETHER else text


def _block(text: str, rtl: bool) -> str:
    """An English piece whole (_whole) and, in Hebrew, one left-to-right block."""
    return bidi.ltr_block(_whole(text), rtl)


def _pieces(items, rtl: bool) -> str:
    """English pieces for one line ("27,600 mesos · Cutthroat Manny (Kerning City)"), each whole and its own block
    in Hebrew: the line wraps between them, and a Hebrew line reads them right to left."""
    return " · ".join(_block(p, rtl) for p in items if p)


def _seller(seller: str) -> str:
    """The seller as a card says it, "Cutthroat Manny (Kerning City)": the equips table's price label ("COT2
    price") is left to the line's tooltip, which has the table's whole line."""
    m = re.fullmatch(r"(.*?)\s*\((.*)\)\s*", seller)
    if not m:
        return seller
    kept = [p for p in m.group(2).split(", ") if not p.endswith(" price")]
    return f"{m.group(1)} ({', '.join(kept)})" if kept else m.group(1)


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    w = QLabel(text, objectName=name)
    w.setWordWrap(wrap)
    return w


def _clear(lay) -> None:
    """Every widget out of a layout (the layouts this file clears hold widgets and stretches only)."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w is not None:
            w.hide()            # gone now: deleteLater alone left the old cards drawn under the new ones
            w.deleteLater()


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class _Pictures:
    """Each item's sprite, fitted once per size: the grid and the panel are drawn again on every pick and edit, and
    each would read its sprite from disk and scale it again."""

    def __init__(self, kb):
        self._kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple[str, int, float], QPixmap] = {}

    def label(self, key: str, size: int, zoom: bool = True) -> QLabel:
        """The picture in a fixed square (no key: the square empty, the columns still line up), shown large on
        hover unless zoom is off (a grid tile's own tooltip names it)."""
        pic = QLabel(objectName="Pic")
        pic.setFixedSize(size, size)
        pic.setAlignment(Qt.AlignCenter)
        if not key:
            return pic
        if key not in self._paths:
            self._paths[key] = self._kb.picture(key)
        path = self._paths[key]
        if not path:
            return pic
        dpr = pic.devicePixelRatioF()
        pm = self._fitted.get((key, size, dpr))
        if pm is None:
            raw = QPixmap(str(path))
            pm = fit_picture(raw, size, size, pic) if not raw.isNull() else QPixmap()
            self._fitted[(key, size, dpr)] = pm
        if not pm.isNull():
            pic.setPixmap(pm)
            if zoom:
                zoom_on_hover(pic, path)
        return pic


def _outline(size: int) -> QLabel:
    """An empty slot's picture: its dashed outline."""
    w = QLabel(objectName="EmptyPic")
    w.setFixedSize(size, size)
    return w


class _Combo(QComboBox):
    """A drop-down in the overlay's look (the gender, a weapon type), its arrow a drawn chevron: a style sheet's
    border triangle came out as a grey box on the glass. Focused by a click only (opening the window gave it the
    keyboard: the arrow keys changed the gender), and the mouse wheel scrolls the panel it is in, not its choice."""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.ClickFocus)

    def wheelEvent(self, e) -> None:
        e.ignore()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        r = self.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxArrow, self)
        x, y = r.center().x() - 1, r.center().y() + 1
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(235, 235, 245, 163), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))     # MUTED
        p.drawPolyline([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)])
        p.end()


class _Press(QFrame):
    """A row that clicks (a picker row, an overview row)."""

    clicked = Signal()

    def __init__(self, name: str):
        super().__init__(objectName=name)
        self.setAttribute(Qt.WA_Hover, True)          # its QSS :hover
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _Tile(QFrame):
    """One slot of the equipment grid: the worn item's picture over the slot's word (an empty slot: its dashed
    outline with the word inside), and in its top corner the accent badge counting the upgrades wearable now. The
    weapon's tile stands for every weapon type the class holds (group), as the game's equip window has one weapon
    slot; slot is the type it shows."""

    clicked = Signal(str)

    def __init__(self, slot: str, group: frozenset[str], word: str, pic: QLabel | None, count: int, tip: str):
        super().__init__(objectName="Tile")
        self.slot = slot
        self.group = group
        self.pic = pic
        self._word = word
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("empty", pic is None)
        self.setProperty("sel", False)
        self.setFixedHeight(TILE_H)
        self.setMinimumWidth(TILE_MIN_W)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 6, 3, 4)
        lay.setSpacing(2)
        self.word = QLabel(objectName="TileWord")
        self.word.setAlignment(Qt.AlignCenter)
        if pic is not None:
            lay.addWidget(pic, 0, Qt.AlignHCenter)
            lay.addWidget(self.word)                   # one line, cut to the tile's width (resizeEvent)
        else:
            self.word.setWordWrap(True)                # "Two-Handed Sword" whole, over two lines
            self.word.setText(word)
            lay.addWidget(self.word, 1)
        self.badge: QLabel | None = None
        if count:
            self.badge = QLabel(str(count), self, objectName="Badge")
            self.badge.setAlignment(Qt.AlignCenter)
            self.badge.setFixedHeight(BADGE)
            self.badge.setMinimumWidth(BADGE)

    def set_selected(self, on: bool) -> None:
        if bool(self.property("sel")) != on:
            self.setProperty("sel", on)
            _repolish(self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.pic is not None:
            self.word.ensurePolished()
            self.word.setText(self.word.fontMetrics().elidedText(self._word, Qt.ElideRight, max(12, self.width() - 8)))
        if self.badge is not None:            # the top corner on the reading end: right, or left in Hebrew
            self.badge.ensurePolished()
            w = max(BADGE, self.badge.sizeHint().width())
            self.badge.resize(w, BADGE)
            self.badge.move(3 if self.isRightToLeft() else self.width() - w - 3, 3)
            self.badge.raise_()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.slot)
        super().mouseReleaseEvent(e)


class GearAdvisor(EdgeResize, QWidget):
    """The window itself. Over the game, in the NPCs window's glass, taking the keyboard while a stat box or the
    worn-item search is typed in (Esc or the ✕ gives it back). Shows while settings["gear_advisor"] is on; its place
    is kept in gear_advisor_geom. A tile picks its slot; the worn card's button (or the picked tile clicked again)
    opens that slot's picker; a chosen item is saved to the character's gear and the advice follows at once."""

    closed = Signal()               # the ✕ turned the setting off: the toolbar's Gear button unchecks
    scroll_requested = Signal(str)  # an item's KB key: "plan scrolls for this" (the scroll planner opens on it)
    market_ready = Signal(object)   # a Free Market lookup's answer, from its thread: (read, key, usual, days, offline)

    def __init__(self, kb: KnowledgeBase, settings: Settings, profiles: Profiles, t: I18n):
        # not the NPCs window's _flags(): the stat boxes and the picker take the keyboard
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self._kb = kb
        self._settings = settings
        self._profiles = profiles
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._pics = _Pictures(kb)
        self._c: Character | None = None     # the active character, re-read on every show / change
        self._cid: str | None = None          # ... whose stats row and picker state these are
        self._plans: list[SlotPlan] = []      # one per slot (gearadvice.upgrades), the grid's order
        self._slot: str | None = None         # the picked slot (a weapon type, for the weapon tile)
        self._weapon_pick: str | None = None  # the weapon type last picked: the weapon tile shows it when none is worn
        self._view = VIEWS[0]
        self._views: Segmented | None = None
        self._tiles: list[_Tile] = []
        self._stats_open: bool | None = None  # the stat boxes shown (None: shown while nothing is typed)
        self._pick_open = False               # the picked slot's picker is open
        self._pick_box: _SearchBox | None = None
        self._pick_rows: list[QWidget] = []
        self._worn_btn: QPushButton | None = None   # the worn card's Change / Pick what you wear
        self._answer: QLabel | None = None    # the picked slot's one-line answer
        self._types: QComboBox | None = None  # the weapon type box, for a class that holds several
        self._fm: dict[str, tuple[int | None, int]] = {}    # item key -> (usual Free Market price, the days it spans)
        self._fm_bad: set[str] = set()        # ... MeowDB couldn't be reached for it (asked again on the next show)
        self._fm_seen: set[str] = set()       # ... asked
        self._fm_read = 0                     # a KB swap's answers from before it are dropped
        self._fm_lines: dict[str, list[tuple[QLabel, Upgrade]]] = {}   # the cards' Free Market lines, by item key
        self._opacity = OPACITY_DEFAULT
        self._placed = False                  # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None      # the toolbar's rect, where the window first opens
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_picker)
        self._stats_typing = QTimer(self, singleShot=True, interval=STAT_DEBOUNCE_MS, timeout=self._stats_saved)
        self.market_ready.connect(self._on_market)
        self.setStyleSheet(qss(settings))
        self._build()

    # ------------------------------------------------------------ the panel

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        head = _Header()
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(8)
        self._title = QLabel(objectName="Title")
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFocusPolicy(Qt.NoFocus)   # the window opening took the keyboard to it: an orange focus frame
        self._close.clicked.connect(self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        # the character: name · job · level, and the gender box on its end
        crow = QHBoxLayout()
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(8)
        self._char = _label("", "Char", wrap=True)
        crow.addWidget(self._char, 1)
        self._gender = _Combo()
        self._gender.setCursor(Qt.PointingHandCursor)
        self._gender.addItems(list(GENDERS))
        self._gender.currentIndexChanged.connect(lambda _i: self._gender_picked())
        self._gender.setSizeAdjustPolicy(QComboBox.AdjustToContents)     # "Female" whole, never clipped
        crow.addWidget(self._gender, 0, Qt.AlignVCenter)
        col.addLayout(crow)
        col.addWidget(self._build_stats())
        # the equipment grid (its tiles made in _fill_grid)
        self._grid_box = QWidget()
        self._grid = QGridLayout(self._grid_box)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        for c in range(GRID_COLS):
            self._grid.setColumnStretch(c, 1)
        col.addWidget(self._grid_box)
        # the view switch: the picked slot or every slot's best (made in _make_views: its words are the slot's)
        self._views_slot = QVBoxLayout()
        self._views_slot.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._views_slot)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Rows")
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setSpacing(8)
        self._scroll.setWidget(body)
        col.addWidget(self._scroll, 1)

    def _build_stats(self) -> QWidget:
        """"Your stats": its title, the typed stats on one line (folded) or the four boxes (open), the Edit / Done
        button, and the hint while nothing is typed."""
        self._stats = QWidget()
        v = QVBoxLayout(self._stats)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._stats_head = _label("", "StatsHead")
        self._stats_line = _label("", "StatsLine", wrap=True)     # four stats typed never widen the window
        self._stats_btn = QPushButton(objectName="Small")
        self._stats_btn.setCursor(Qt.PointingHandCursor)
        self._stats_btn.setFocusPolicy(Qt.NoFocus)
        self._stats_btn.clicked.connect(self._stats_toggled)
        row.addWidget(self._stats_head, 0, Qt.AlignVCenter)
        row.addWidget(self._stats_line, 1, Qt.AlignVCenter)
        row.addWidget(self._stats_btn, 0, Qt.AlignVCenter)
        v.addLayout(row)
        self._stats_edit = QWidget()
        srow = QHBoxLayout(self._stats_edit)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(6)
        self._editors: dict[str, _SearchBox] = {}
        for st in STAT_KEYS:
            pair = QWidget()
            prow = QHBoxLayout(pair)
            prow.setContentsMargins(0, 0, 0, 0)
            prow.setSpacing(4)
            prow.addWidget(_label(LABELS[st], "StatName"))      # still readable once filled
            box = _SearchBox(objectName="Stat", maxLength=3)
            box.setMinimumWidth(40)                    # three digits ("120") whole
            box.setMaximumWidth(64)
            box.setAlignment(Qt.AlignCenter)
            box.textChanged.connect(lambda _t: self._stat_typed())
            prow.addWidget(box, 1)
            srow.addWidget(pair, 1)
            self._editors[st] = box
        v.addWidget(self._stats_edit)
        self._hint = _label("", "Hint", wrap=True)
        v.addWidget(self._hint)
        return self._stats

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ the contract (app.py wires exactly this)

    def refresh(self) -> None:
        """Shown while settings["gear_advisor"] is on; the character is read again every time it shows. The look
        (accent, tint, opacity) is taken every time: the ⚙ panel's choices land here."""
        if bool(self._settings[SHOW_KEY]):
            self._restyle()
            try:
                self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
            except (TypeError, ValueError):
                self._opacity = OPACITY_DEFAULT
            if not self.isVisible():
                # each opening: the Free Market asked again (market.py caches 10 minutes, so a quick re-open costs
                # nothing) and the stats folded again once typed
                self._fm_seen.clear()
                self._fm_bad.clear()
                self._stats_open = None
                self._rebuild()
                self._place()
                self.show()
                set_capturable(self, self._settings)
                plan = self._plan(self._slot)
                if plan is not None and self._view == "slot":
                    self._queue_fm(plan)     # (the rebuild above came before the show: nothing was asked yet)
            self.raise_()
            self.update()             # the glass repaints in the tint the settings now give (npcoverlay.apply_look)
        elif self.isVisible():
            self.hide()

    def _restyle(self) -> None:
        """The accent the settings pick, in the window's style sheet; skipped when it is the same (a restyle
        re-polishes every card)."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A new KB: its equips, pictures and prices; the market answers asked under the old one are dropped."""
        self._kb = kb
        self._pics = _Pictures(kb)
        self._fm, self._fm_bad, self._fm_seen, self._fm_lines = {}, set(), set(), {}
        self._fm_read += 1
        self._rebuild()

    def character_changed(self) -> None:
        self._rebuild()

    # ------------------------------------------------------------ everything again (apply_language is just this)

    def _retext_chrome(self) -> None:
        """The words that are not the advice: the title, the ✕, the stat boxes and the gender box."""
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t("ga_title"), rtl))
        self._close.setAccessibleName(t("ga_close"))
        self._close.setToolTip(t("ga_close"))
        self._stats_head.setText(bidi.plain(t("ga_your_stats"), rtl))
        for st, box in self._editors.items():
            box.setPlaceholderText("—")                    # its label beside it names it; "—" not typed yet
            box.setAccessibleName(LABELS[st])
        self._gender.setToolTip(t("ga_gender_tip"))
        self._gender.setAccessibleName(t("ga_gender"))
        self._gender.blockSignals(True)                       # its choices re-worded, its pick kept
        idx = self._gender.currentIndex()
        self._gender.clear()
        self._gender.addItem("—")
        self._gender.addItems([_sex_word(t, sex) for sex in GENDERS[1:]])
        self._gender.setCurrentIndex(idx)
        self._gender.blockSignals(False)
        # the cards keep a little room on the scrollbar's side (right, or left in Hebrew, where Qt moves it)
        self._body_lay.setContentsMargins(GUTTER if rtl else 0, 0, 0 if rtl else GUTTER, 0)

    def _rebuild(self) -> None:
        """Everything again from the active character: its line, its stats, the grid (gearadvice.upgrades) and the
        panel. The picked slot and view stay; another character opens on its own first slot, its stats row and
        picker afresh."""
        t, rtl = self.t, self.t.rtl
        self._typing.stop()
        c = self._c = self._profiles.active
        if c is not None and c.id != self._cid:
            self._cid, self._stats_open, self._pick_open = c.id, None, False
            self._slot, self._weapon_pick, self._view = None, None, VIEWS[0]
        self._retext_chrome()
        for w in (self._gender, self._stats, self._grid_box, self._scroll):
            w.setVisible(c is not None)
        if c is None:
            self._char.setText(bidi.plain(t("ga_no_character"), rtl))
            self._plans = []
            self._fill_grid()
            self._make_views()
            self._fill_panel()
            return
        self._char.setText(bidi.plain(f"{bidi.ltr_name(f'{c.name} · {c.job}', rtl)} · {t('lv_short', n=c.level)}",
                                      rtl))
        for st, box in self._editors.items():
            if box.hasFocus():
                continue                                  # the box being typed in keeps what is typed
            box.blockSignals(True)
            box.setText(str(c.ap.get(st, "")) if c.ap.get(st) is not None else "")
            box.blockSignals(False)
        self._gender.blockSignals(True)
        self._gender.setCurrentIndex(GENDERS.index(c.gender) if c.gender in GENDERS else 0)
        self._gender.blockSignals(False)
        self._retext_stats()
        order = {s: i for i, s in enumerate(GRID_ORDER)}
        self._plans = sorted(gearadvice.upgrades(self._kb, c), key=lambda p: order.get(p.slot, len(order)))
        if self._slot not in {p.slot for p in self._plans}:
            self._slot, self._pick_open = self._first_slot(), False
        self._fill_grid()
        self._make_views()
        bar = self._scroll.verticalScrollBar()
        at = bar.value()
        self._fill_panel()
        QTimer.singleShot(0, self, lambda: bar.setValue(at))

    def _weapons(self) -> list[SlotPlan]:
        """The weapon types the class holds, one plan each (a slot the KB names anew counts as one, as rank_key does):
        those its own class's items fill first (a 1st-job Thief's claws and daggers before the swords any class can
        hold), each group in WEAPON_SLOTS' order."""
        base = str(getattr(self._c, "base_class", "") or "")

        def own(p: SlotPlan) -> bool:
            return any(str(r.get("job") or "") == base for r in p.picker)    # the class's alone, not "Warrior/Thief"
        return sorted((p for p in self._plans if p.slot not in gearadvice.ARMOUR_SLOTS), key=lambda p: not own(p))

    def _weapon_shown(self) -> SlotPlan | None:
        """The weapon type the grid's one weapon tile shows: the one worn (the first, in _weapons' order), else the one
        last picked, else the first."""
        wp = self._weapons()
        return (next((p for p in wp if p.worn is not None), None)
                or next((p for p in wp if p.slot == self._weapon_pick), None)
                or (wp[0] if wp else None))

    def _entries(self) -> list[SlotPlan]:
        """The grid's tiles, each by the plan it shows: the one weapon tile first, then the armour in GRID_ORDER."""
        shown = self._weapon_shown()
        return ([shown] if shown is not None else []) + [p for p in self._plans if p.slot in gearadvice.ARMOUR_SLOTS]

    def _group(self, slot: str | None) -> frozenset[str]:
        """The slots a tile stands for: every weapon type for the weapon tile, else the slot alone."""
        weapons = frozenset(p.slot for p in self._weapons())
        return weapons if slot in weapons else frozenset((slot,))

    def _first_slot(self) -> str | None:
        """The slot the window opens on: the first tile's worn one with an upgrade, else the first with one, else
        the first."""
        entries = self._entries()
        for has in (lambda p: p.worn is not None and p.now, lambda p: p.now, lambda p: True):
            found = next((p.slot for p in entries if has(p)), None)
            if found is not None:
                return found
        return None

    def _plan(self, slot: str | None) -> SlotPlan | None:
        return next((p for p in self._plans if p.slot == slot), None)

    # ------------------------------------------------------------ the player's own numbers

    def _retext_stats(self) -> None:
        """Your stats: open (the boxes, and the hint while nothing is typed) or folded to "STR 4 · DEX 50 · LUK 120"."""
        t, rtl = self.t, self.t.rtl
        c = self._c
        typed = [(st, c.ap.get(st)) for st in STAT_KEYS if c.ap.get(st)]
        open_ = self._stats_open if self._stats_open is not None else not typed
        self._stats_edit.setVisible(open_)
        self._hint.setText(bidi.plain(t("ga_stats_hint"), rtl))
        self._hint.setVisible(open_ and not typed)
        # each stat its own piece: in Hebrew they read right to left, as the boxes stand
        line = _pieces([f"{LABELS[st]} {v}" for st, v in typed], rtl)
        self._stats_line.setText("" if open_ else bidi.plain(line or t("ga_stats_none"), rtl))
        self._stats_btn.setText(bidi.plain(t("ga_stats_done" if open_ else "ga_stats_edit"), rtl))
        self._stats_btn.setToolTip(t("ga_stats_tip"))

    def _stats_toggled(self, _=False) -> None:
        """Edit / Done: the boxes open (the first empty one takes the keyboard) or fold to their line — a number
        typed just before Done saved first, so the line has it."""
        c = self._c
        if c is None:
            return
        typed = any(c.ap.get(st) for st in STAT_KEYS)
        self._stats_open = not (self._stats_open if self._stats_open is not None else not typed)
        if self._stats_typing.isActive():
            self._stats_typing.stop()
            self._stats_saved()
        else:
            self._retext_stats()
        if self._stats_open:
            box = next((b for st, b in self._editors.items() if not c.ap.get(st)), self._editors[STAT_KEYS[0]])
            box.setFocus()

    def _stat_typed(self) -> None:
        """A keystroke in a stat box: the row stays open while typed in; the saving waits for the pause."""
        self._stats_open = True
        self._stats_typing.start()

    def _stats_saved(self) -> None:
        """The pause held: every stat box saved to the character (empty: not said) and the advice re-read."""
        c = self._profiles.active
        if c is None:
            return
        for st, box in self._editors.items():
            v = box.text().strip()
            if v.isdigit() and int(v) > 0:
                c.ap[st] = int(v)
            else:
                c.ap.pop(st, None)
        self._profiles.save()
        self._rebuild()

    def _gender_picked(self) -> None:
        c = self._profiles.active
        if c is None:
            return
        c.gender = GENDERS[self._gender.currentIndex()]
        self._profiles.save()
        self._rebuild()

    # ------------------------------------------------------------ your gear: the grid

    def _fill_grid(self) -> None:
        """A tile per slot, five a row (mirrored in Hebrew: the grid's columns run right to left), the weapon types
        one tile."""
        _clear(self._grid)
        self._tiles = []
        for i, plan in enumerate(self._entries()):
            tile = self._tile(plan)
            self._grid.addWidget(tile, i // GRID_COLS, i % GRID_COLS)
            self._tiles.append(tile)

    def _tile(self, plan: SlotPlan) -> _Tile:
        t, rtl = self.t, self.t.rtl
        group = self._group(plan.slot)
        # the weapon tile of a class with several weapon types and none worn says "Weapon", not one type's word
        word = t("ga_weapon") if len(group) > 1 and plan.worn is None else _slot_word(t, plan.slot)
        worn = bidi.ltr_block(_name(plan.worn), rtl) if plan.worn else t("ga_nothing_set")
        tip = [bidi.plain(f"{_slot_word(t, plan.slot)}: {worn}", rtl)]
        if plan.now:
            tip.append(bidi.plain(t("ga_upgrades", n=len(plan.now)), rtl))
        pic = self._pics.label(str(plan.worn.get("key") or ""), TILE_PIC, zoom=False) if plan.worn else None
        tile = _Tile(plan.slot, group, word, pic, len(plan.now), "\n".join(tip))
        tile.set_selected(self._slot in group)
        tile.clicked.connect(self._tile_clicked)
        return tile

    def _tile_clicked(self, slot: str) -> None:
        """A tile picks its slot; the picked one clicked again opens (or closes) its picker."""
        if self._view == "slot" and self._slot in self._group(slot):
            self._worn_clicked(self._slot)
        else:
            self._select(slot)

    def _select(self, slot: str) -> None:
        """The slot picked: its tile ringed, the view switch named after it, the panel showing it from the top."""
        self._slot, self._view, self._pick_open = slot, "slot", False
        if len(self._group(slot)) > 1 and slot != self._weapon_pick:
            self._weapon_pick = slot
            self._fill_grid()                  # none worn: the weapon tile shows the type picked, its count
        for tile in self._tiles:
            tile.set_selected(slot in tile.group)
        self._make_views()
        self._fill_panel()
        self._scroll.verticalScrollBar().setValue(0)

    def _make_views(self) -> None:
        """The switch between the picked slot (its own word: "Claw") and All upgrades, made again whenever its
        words change (another slot, another language)."""
        t, rtl = self.t, self.t.rtl
        if self._views is not None:
            self._views_slot.removeWidget(self._views)
            self._views.hide()
            self._views.deleteLater()
            self._views = None
        if not self._plans or self._slot is None:
            return
        # a class with several weapon types: "Weapon" (the type box under it says which)
        word = t("ga_weapon") if len(self._group(self._slot)) > 1 else _slot_word(t, self._slot)
        self._views = Segmented([(word, "slot"), (t("ga_view_all"), "all")], self._view, rtl)
        self._views.set_label(t("ga_view_a11y"))
        for b in self._views.group.buttons():
            b.setFocusPolicy(Qt.NoFocus)
        self._views.changed.connect(self._view_set)
        self._views_slot.addWidget(self._views)

    def _view_set(self, view: str) -> None:
        """The picked slot or All upgrades: the panel shows it from the top (and the switch says so)."""
        self._view, self._pick_open = view, False
        if self._views is not None:
            self._views.set_value(view)
        self._fill_panel()
        self._scroll.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------ the panel: the picked slot, or all of them

    def _fill_panel(self) -> None:
        _clear(self._body_lay)
        self._fm_lines = {}
        self._pick_box, self._pick_rows, self._worn_btn, self._answer, self._types = None, [], None, None, None
        plan = self._plan(self._slot)
        if self._c is not None and plan is not None:
            if self._view == "all":
                for p in self._entries():
                    self._body_lay.addWidget(self._all_row(p))
            else:
                self._fill_slot(plan)
        self._body_lay.addStretch(1)

    def _fill_slot(self, plan: SlotPlan) -> None:
        """The weapon types (a class that holds several), the plain answer, what is worn (and the picker under it),
        the upgrades best first, then the soon ones."""
        t, rtl = self.t, self.t.rtl
        add = self._body_lay.addWidget
        if len(self._group(plan.slot)) > 1:
            add(self._type_row())
        self._answer = _label(bidi.plain(self._answer_text(plan, gearadvice.slot_summary(plan)), rtl), "Answer",
                              wrap=True)
        add(self._answer)
        if plan.slot == "Overall":
            add(_label(bidi.plain(t("ga_overall_note"), rtl), "Note", wrap=True))
        if plan.worn_gone:
            add(_label(bidi.plain(t("ga_worn_gone"), rtl), "Note", wrap=True))
        add(self._worn_card(plan))
        if self._pick_open:
            add(self._picker())
        for i, u in enumerate(plan.now):
            add(self._card(plan, u, best=i == 0))
        if plan.soon:
            head = QWidget()
            h = QHBoxLayout(head)
            h.setContentsMargins(0, 4, 0, 0)
            h.setSpacing(8)
            h.addWidget(_label(bidi.plain(t("ga_soon_head"), rtl), "SoonHead"))
            h.addWidget(_label(bidi.plain(t("ga_soon_sub", n=gearadvice.SOON_LEVELS), rtl), "SoonSub"), 1)
            add(head)
            for u in plan.soon:
                add(self._card(plan, u))
        self._queue_fm(plan)

    def _answer_text(self, plan: SlotPlan, s: Summary) -> str:
        """The slot's one line, from slot_summary's facts: the best item now and the stat it wins by over what is
        worn, how many more open soon; else the next item that will beat it; else that nothing does. Nothing worn:
        pick it to compare, and the best item to wear now (or the next one)."""
        t, rtl = self.t, self.t.rtl
        word = _slot_word(t, plan.slot)
        word = word if rtl else word.lower()             # "your best claw now"

        def item(u: Upgrade) -> str:
            return _block(_name(u.row), rtl)

        def lv(u: Upgrade) -> str:
            return self._lv(u.row.get("req_lv") or 0)

        if not s.worn:
            text = t("ga_sum_pick")
            if s.best is not None:
                text += " " + t("ga_sum_pick_best", item=item(s.best))
            elif s.next is not None:
                text += " " + t("ga_sum_next", item=item(s.next), lv=lv(s.next))
            return text
        if s.best is not None:
            gain = gearadvice.change_text(*s.best.why, s.best.row) if s.best.why else ""
            text = t("ga_sum_best", item=item(s.best), slot=word, gain=_block(gain, rtl), worn=_block(s.worn, rtl))
            if s.soon:
                text += " " + t("ga_sum_soon_at" if s.soon_from == s.soon_to else "ga_sum_soon_from", n=s.soon,
                                lv=self._lv(s.soon_from))
            return text
        if s.next is not None:
            return t("ga_sum_none_yet") + " " + t("ga_sum_next", item=item(s.next), lv=lv(s.next))
        return t("ga_sum_top", worn=_block(s.worn, rtl))

    def _lv(self, n) -> str:
        """ "Lv. 35" / "רמה 35" that a line never wraps inside (it broke "ברמה" / "35" over two lines)."""
        return self.t("lv_short", n=n).replace(" ", NBSP)

    def _type_row(self) -> QWidget:
        """The weapon type the panel shows, for a class that holds several (a 1st-job Thief: claws, daggers, and
        the swords any class can hold): one drop-down of them all with their upgrade counts, the class's own first —
        ten chips took five rows."""
        t, rtl = self.t, self.t.rtl
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        h.addWidget(_label(bidi.plain(t("ga_weapon_type"), rtl), "StatsHead"))
        box = _Combo()
        box.setObjectName("Types")
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # as narrow as the window needs (its text cut short there), not as wide as "Two-Handed Blunt Weapon · …"
        box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(10)
        box.setAccessibleName(t("ga_weapon_type"))
        types = self._weapons()
        for p in types:
            word = _slot_word(t, p.slot)
            box.addItem(bidi.plain(t("ga_type_n", type=word, n=len(p.now)) if p.now else word, rtl), p.slot)
        box.setCurrentIndex(next((i for i, p in enumerate(types) if p.slot == self._slot), 0))
        # after the pick has closed the drop-down: picking rebuilds the panel this box is in
        box.activated.connect(lambda i: QTimer.singleShot(0, self, lambda: self._select(types[i].slot)))
        h.addWidget(box, 1)
        self._types = box
        return row

    def _caption(self, key: str) -> str:
        """A card's small caption ("YOU WEAR", "BEST UPGRADE"): capitals in English, Hebrew has none."""
        text = self.t(key)
        return bidi.plain(text if self.t.rtl else text.upper(), self.t.rtl)

    def _chips(self, chips: list[tuple[str, str]], a11y: str = "") -> QWidget:
        """A row of chips (text, its QSS name) that wraps onto more lines rather than widen the window."""
        box = QWidget()
        flow = FlowLayout(box, spacing=4, line_spacing=4)
        for text, name in chips:
            flow.addWidget(_label(text, name))
        if a11y:
            box.setAccessibleName(a11y)
        return box

    def _stat_chips(self, row: dict) -> list[tuple[str, str]]:
        """What is worn, its own numbers as neutral chips: the level, then the columns that rank it (attack speed
        with them), then the rest of its stats."""
        rtl = self.t.rtl
        ranked = gearadvice.rank_stats(self._c.base_class, str(row.get("slot") or ""))
        chips = [(bidi.plain(self.t("lv_short", n=row.get("req_lv") or 0), rtl), "ChipLv")]
        for st in ranked + tuple(s for s in gearadvice.DELTA_ORDER if s not in ranked):
            if st == SPEED:
                if row.get(SPEED):
                    chips.append((bidi.ltr_name(f"{SPEED_LABEL} {row[SPEED]}", rtl), "Chip"))
            elif row.get(st):
                chips.append((bidi.ltr_name(f"{LABELS[st]} {row[st]}", rtl), "Chip"))
        return chips

    def _level_chip(self, u: Upgrade) -> tuple[str, str]:
        """ "Lv. 30" for what is wearable now; "Lv. 35 · in 3 levels", muted, for a soon one."""
        t, rtl = self.t, self.t.rtl
        lv = t("lv_short", n=u.row.get("req_lv") or 0)
        if u.soon:
            return bidi.plain(f"{lv} · {t('ga_in_levels', n=u.soon)}", rtl), "ChipSoon"
        return bidi.plain(lv, rtl), "ChipLv"

    def _change_chips(self, plan: SlotPlan, u: Upgrade, limit: int | None = None) -> list[tuple[str, str]]:
        """Its stat changes against what is worn (gearadvice.changes): a gain green, a loss red, the same muted."""
        out = []
        for st, d in gearadvice.changes(self._c.base_class, plan.worn, u.row)[:limit]:
            name = "ChipGain" if d > 0 else "ChipLoss" if d < 0 else "ChipSame"
            out.append((bidi.ltr_name(gearadvice.change_text(st, d, u.row), self.t.rtl), name))
        return out

    def _worn_card(self, plan: SlotPlan) -> QFrame:
        """You wear: the worn item's picture, name and numbers, and Change (or, with nothing set, Pick what you wear)
        opening the picker under it."""
        t, rtl = self.t, self.t.rtl
        card = QFrame(objectName="Card")
        h = QHBoxLayout(card)
        h.setContentsMargins(10, 8, 10, 9)
        h.setSpacing(10)
        worn = plan.worn
        h.addWidget(self._pics.label(str(worn.get("key") or ""), CARD_PIC) if worn else _outline(CARD_PIC), 0,
                    Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(3)
        h.addLayout(v, 1)              # in the card first: what goes in it is the card's child at once
        v.addWidget(_label(self._caption("ga_you_wear"), "Caption"))
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_label(bidi.ltr_name(_name(worn), rtl) if worn else bidi.plain(t("ga_nothing_set"), rtl),
                             "ItemName" if worn else "ItemMuted", wrap=True), 1)
        btn = QPushButton(objectName="Small" if worn or self._pick_open else "Act")
        btn.setText(bidi.plain(t("ga_pick_close" if self._pick_open else "ga_change" if worn else "ga_set_worn"),
                               rtl))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setToolTip(t("ga_worn_tip"))
        btn.clicked.connect(lambda _=False, s=plan.slot: self._worn_clicked(s))
        top.addWidget(btn, 0, Qt.AlignTop)
        self._worn_btn = btn
        v.addLayout(top)
        if worn:
            v.addWidget(self._chips(self._stat_chips(worn)))
        return card

    def _card(self, plan: SlotPlan, u: Upgrade, best: bool = False) -> QFrame:
        """One upgrade: its picture; the BEST UPGRADE caption on the first; its name with Plan scrolls (an item with
        upgrade slots); its level and stat changes as chips; what to flag; where to get it."""
        t, rtl = self.t, self.t.rtl
        key = str(u.row.get("key") or "")
        card = QFrame(objectName="Card")
        card.setProperty("best", best)
        h = QHBoxLayout(card)
        h.setContentsMargins(10, 8, 10, 9)
        h.setSpacing(10)
        h.addWidget(self._pics.label(key, CARD_PIC), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(4)
        h.addLayout(v, 1)              # in the card first: the Free Market line is shown or hidden as it is made
        if best:
            v.addWidget(_label(self._caption("ga_best"), "BestCaption"))
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_label(bidi.ltr_name(_name(u.row), rtl), "ItemName", wrap=True), 1)
        if isinstance(u.row.get("slots"), int) and u.row["slots"] > 0:
            plan_btn = QPushButton(bidi.plain(t("ga_plan_scrolls"), rtl), objectName="Plan")
            plan_btn.setCursor(Qt.PointingHandCursor)
            plan_btn.setFocusPolicy(Qt.NoFocus)
            plan_btn.setToolTip(t("ga_plan_tip"))
            plan_btn.setAccessibleName(t("ga_plan_tip"))
            plan_btn.clicked.connect(lambda _=False, k=key: self.scroll_requested.emit(k))
            top.addWidget(plan_btn, 0, Qt.AlignTop)
        v.addLayout(top)
        v.addWidget(self._chips([self._level_chip(u)] + self._change_chips(plan, u), gearadvice.delta_text(u.deltas)))
        flags = []
        for flag in u.flags:
            if flag == "gender":
                flags.append(t("ga_gender_only", g=_sex_word(t, str(u.row.get("gender") or ""))))
            elif flag.startswith("stat:"):
                _, st, need = flag.split(":", 2)
                flags.append(t("ga_needs", stat=bidi.ltr_block(f"{LABELS.get(st, st.upper())} {need}", rtl)))
        if flags:
            v.addWidget(_label(bidi.plain(" · ".join(flags), rtl), "Note", wrap=True))
        self._where(u, v)
        return card

    def _where(self, u: Upgrade, lay: QVBoxLayout) -> None:
        """Where to get it, a tagged line each: the NPC's price and seller (the equips table's; its whole seller
        line on hover); else the usual Free Market price (MeowDB, live) and the monsters that drop it (the drops
        table's); with neither, that the database has no NPC price."""
        t, rtl = self.t, self.t.rtl
        if u.buy is not None:
            shop = _label(bidi.plain(f"{SHOP} {_pieces((f'{u.buy:,} mesos', _seller(u.seller)), rtl)}", rtl), "Where",
                          wrap=True)
            shop.setToolTip(bidi.plain(t("ga_shop_tip", who=bidi.ltr_block(u.seller, rtl)), rtl) if u.seller else "")
            lay.addWidget(shop)
            return
        key = str(u.row.get("key") or "")
        if _market_id(key) is not None:
            fm = _label("", "WhereMuted", wrap=True)
            fm.setToolTip(t("ga_fm_tip"))
            lay.addWidget(fm)
            self._fm_lines.setdefault(key, []).append((fm, u))
            self._paint_fm(fm, u)
        if u.drops:
            # each dropper whole ("Cold Eye (Lv. 40)"): the line wraps between them, never inside one
            drops = " · ".join(f"{_block(m, rtl)}{NBSP}({self._lv(lv)})" for m, lv in u.drops)
            lay.addWidget(_label(bidi.plain(f"{DROPPED} {t('ga_drops_from', list=drops)}", rtl), "Where", wrap=True))
        elif _market_id(key) is None:
            lay.addWidget(_label(bidi.plain(t("ga_no_price"), rtl), "WhereMuted", wrap=True))

    def _paint_fm(self, label: QLabel, u: Upgrade) -> None:
        """A card's Free Market line as its lookup stands: checking, the usual price, none lately, or MeowDB out of
        reach — the last two say the database has no NPC price either, and are left out when monsters drop it
        (that line says where to get it)."""
        t, rtl = self.t, self.t.rtl
        key = str(u.row.get("key") or "")
        usual, days = self._fm.get(key, (None, 0))
        if usual:
            text, name = f"{MARKET} " + t("ga_fm", price=_block(f"{usual:,} mesos", rtl), days=days), "Where"
        elif key in self._fm:
            text, name = t("ga_no_price_fm", days=days), "WhereMuted"
        elif key in self._fm_bad:
            text, name = t("ga_fm_offline"), "WhereMuted"
        else:
            text, name = t("ga_fm_checking"), "WhereMuted"
        label.setText(bidi.plain(text, rtl))
        if label.objectName() != name:
            label.setObjectName(name)
            _repolish(label)
        label.setVisible(bool(usual) or not u.drops or not (key in self._fm or key in self._fm_bad))

    # ------------------------------------------------------------ the Free Market prices (MeowDB, live)

    def _queue_fm(self, plan: SlotPlan) -> None:
        """The usual Free Market price of every card the NPCs don't sell, not asked yet, fetched off the GUI thread
        (each item is up to 3 requests of 8 s on a slow line) — only while the window shows: a hidden one is rebuilt
        on a character's or the KB's change, and asks when it opens."""
        if not self.isVisible():
            return
        keys = [k for u in plan.now + plan.soon if u.buy is None for k in (str(u.row.get("key") or ""),)
                if _market_id(k) is not None and k not in self._fm_seen]
        if not keys:
            return
        self._fm_seen.update(keys)
        read = self._fm_read

        def look():
            for key in keys:
                try:
                    m = market.item_market(_market_id(key))
                except Exception:      # noqa: BLE001 - an odd answer reads as "out of reach", not a crash
                    log.warning("gear advisor: market lookup failed", exc_info=True)
                    m = None
                try:
                    self.market_ready.emit((read, key, m.usual if m else None, m.window if m else 14, m is None))
                except RuntimeError:
                    return               # the window was deleted meanwhile: nothing to show it in
        threading.Thread(target=look, daemon=True).start()

    def _on_market(self, r) -> None:
        """A lookup's answer, back on the GUI thread: remembered, and the cards showing that item rewritten. An answer
        from before a KB swap is dropped."""
        read, key, usual, days, offline = r
        if read != self._fm_read:
            return
        if offline:
            self._fm_bad.add(key)
        else:
            self._fm[key] = (usual, days)
        for label, u in self._fm_lines.get(key, ()):
            self._paint_fm(label, u)

    # ------------------------------------------------------------ what is worn: the picker

    def _worn_clicked(self, slot: str) -> None:
        """The worn card's button (or the picked tile clicked again): that slot's picker opens, its search taking the
        keyboard, or closes."""
        if slot != self._slot or self._view != "slot":
            self._select(slot)
        self._pick_open = not self._pick_open
        bar = self._scroll.verticalScrollBar()
        at = bar.value()
        self._fill_panel()
        QTimer.singleShot(0, self, lambda: bar.setValue(at))
        box = self._pick_box
        if box is not None:     # only here: a rebuild while a stat box is typed in leaves the keyboard in that box
            QTimer.singleShot(0, self, lambda: box.setFocus() if self._pick_box is box else None)

    def _picker(self) -> QWidget:
        """The picker under the worn card: the search box, then the slot's items (pictures, levels, needs)."""
        t, rtl = self.t, self.t.rtl
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        search = _SearchBox(objectName="PickSearch")
        search.setClearButtonEnabled(True)
        search.setPlaceholderText(bidi.plain(t("ga_pick_ph"), rtl))
        search.textChanged.connect(lambda _t: self._typing.start())
        v.addWidget(search)
        rows = QWidget()
        self._pick_rows_lay = QVBoxLayout(rows)
        self._pick_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._pick_rows_lay.setSpacing(4)
        v.addWidget(rows)
        self._pick_more = _label("", "Note", wrap=True)
        v.addWidget(self._pick_more)
        self._pick_box = search
        self._fill_picker()
        return box

    def _fill_picker(self) -> None:
        """The picker's rows for what is typed: nothing (to take off what is worn) first, then the slot's items the
        character's class can hold (any level), what is worn lit."""
        if self._pick_box is None:
            return
        t, rtl = self.t, self.t.rtl
        plan = self._plan(self._slot)
        if plan is None:
            return
        for w in self._pick_rows:
            self._pick_rows_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        self._pick_rows = []
        worn = str((plan.worn or {}).get("key") or "")
        none_row = self._pick_row("", bidi.plain(t("ga_nothing"), rtl), "", not worn)
        none_row.clicked.connect(lambda: self._worn_set(plan.slot, None))
        rows = [r for r in plan.picker if _match(_name(r), self._pick_box.text())]
        for r in rows[:PICK_ROWS]:
            bits = [t("lv_short", n=r.get("req_lv") or 0)]
            bits += [bidi.ltr_block(f"{LABELS[st]} {r[f'req_{st}']}", rtl)
                     for st in STAT_KEYS if isinstance(r.get(f"req_{st}"), int) and r[f"req_{st}"]]
            if r.get("gender"):
                bits.append(t("ga_gender_only", g=_sex_word(t, str(r["gender"]))))
            key = str(r.get("key") or "")
            row = self._pick_row(key, bidi.ltr_name(_name(r), rtl), bidi.plain(" · ".join(bits), rtl), key == worn)
            row.clicked.connect(lambda k=key: self._worn_set(plan.slot, k))
        left = len(rows) - min(len(rows), PICK_ROWS)
        self._pick_more.setText(bidi.plain(t("ga_pick_none"), rtl) if not rows else
                                bidi.plain(t("ga_more", n=left), rtl) if left else "")

    def _pick_row(self, key: str, name: str, sub: str, worn: bool) -> _Press:
        """A picker row: the item's picture, its name over its level and needs."""
        row = _Press("Row")
        row.setProperty("worn", worn)
        h = QHBoxLayout(row)
        h.setContentsMargins(8, 5, 10, 5)
        h.setSpacing(8)
        h.addWidget(self._pics.label(key, ROW_PIC), 0, Qt.AlignVCenter)
        v = QVBoxLayout()
        v.setSpacing(1)
        row.name = _label(name, "Name", wrap=True)
        v.addWidget(row.name)
        if sub:
            v.addWidget(_label(sub, "Sub", wrap=True))
        h.addLayout(v, 1)
        self._pick_rows.append(row)
        self._pick_rows_lay.addWidget(row)
        return row

    def _worn_set(self, slot: str, key: str | None) -> None:
        """A picker row clicked: what is worn in this slot is saved to the character (nothing: taken off) and the
        advice follows at once."""
        c = self._profiles.active
        if c is None:
            return
        if key:
            c.gear[slot] = key
        else:
            c.gear.pop(slot, None)
        self._profiles.save()
        self._pick_open = False
        self._rebuild()

    # ------------------------------------------------------------ All upgrades: every slot's best

    def _all_row(self, plan: SlotPlan) -> QWidget:
        """One slot in a compact row: the slot and what is worn over the best upgrade's picture, name, level and
        first changes; with none, what is worn and the next item that will beat it. A click opens the slot."""
        t, rtl = self.t, self.t.rtl
        row = _Press("AllRow")
        row.setToolTip(t("ga_all_tip"))
        row.clicked.connect(lambda s=plan.slot: self._select(s))
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 7, 10, 8)
        h.setSpacing(10)
        best = plan.now[0] if plan.now else None
        shown = best.row if best is not None else plan.worn
        h.addWidget(self._pics.label(str(shown.get("key") or ""), ROW_PIC, zoom=False) if shown else
                    _outline(ROW_PIC), 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(3)
        h.addLayout(v, 1)
        word = _slot_word(t, plan.slot)
        if plan.worn is None:
            word = f"{word} · {t('ga_nothing_set')}"
        elif best is not None:
            word = t("ga_all_worn", slot=word, item=bidi.ltr_block(_name(plan.worn), rtl))
        v.addWidget(_label(bidi.plain(word, rtl), "AllSlot", wrap=True))
        if best is not None:
            v.addWidget(_label(bidi.ltr_name(_name(best.row), rtl), "ItemName", wrap=True))
            v.addWidget(self._chips([self._level_chip(best)] + self._change_chips(plan, best, 3)))
            return row
        v.addWidget(_label(bidi.ltr_name(_name(plan.worn), rtl), "ItemName", wrap=True) if plan.worn else
                    _label(bidi.plain(t("ga_nothing_set"), rtl), "ItemMuted"))
        s = gearadvice.slot_summary(plan)
        line = t("ga_all_none") if s.next is None else t(
            "ga_all_next", item=bidi.ltr_block(_name(s.next.row), rtl), lv=self._lv(s.next.row.get("req_lv") or 0))
        v.addWidget(_label(bidi.plain(line, rtl), "Note", wrap=True))
        return row

    # ------------------------------------------------------------ closing

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's Gear button follows), hidden until switched back on."""
        self._settings[SHOW_KEY] = False
        self.hide()
        self.closed.emit()

    def hideEvent(self, e) -> None:
        for timer in (self._typing, self._stats_typing):   # nothing fires once hidden: no re-read behind the back
            timer.stop()
        self._pick_open = False
        if self._placed:            # hidden (the toolbar, Esc, the ✕): keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    # ------------------------------------------------------------ place, move, resize

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: under the toolbar again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings[GEOM_KEY]
        size = mapview.clamp_size(saved["w"], saved["h"], screens, self.minimumSize()) \
            if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int) \
            else QSize(self.width(), self.height())
        spot = mapview.saved_spot(saved, size, screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(size), size))
        self._placed = True

    def _default_spot(self, size: QSize) -> QPoint:
        """Just under the toolbar (the bar's Gear button opens this), kept on that screen; without one — the bar
        never placed, or gone — the primary screen's top-left corner."""
        primary = QGuiApplication.primaryScreen().availableGeometry()
        screens = [s.geometry() for s in QGuiApplication.screens()]
        under = self._under
        if isinstance(under, QRect) and not under.isNull():
            screen = next((g for g in screens if g.intersects(under)), primary)
            x = min(max(under.left(), screen.left()), max(screen.left(), screen.right() - size.width() + 1))
            y = min(max(under.bottom() + 1 + GAP, screen.top()), max(screen.top(), screen.bottom() - size.height() + 1))
            return QPoint(x, y)
        return primary.topLeft() + QPoint(12, 12)

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        super().resizeEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings[GEOM_KEY] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}
