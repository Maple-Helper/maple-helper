"""The crafting advisor over the game (the toolbar's Crafting button opens it), in two windows like the site it is
modelled on (maplestorycrafting.com/planner.html): inputs on one side, the Forge plan on the other.

"Crafting" (CraftAdvisor) takes the inputs on three tabs: what to craft — a table of the gear the class can hold
with its level, the class's attack stat, the recipe, the craft cost from raw mats and what crafting saves against
the NPC's price, a Make count each, the search finding every other recipe; the bag — what the plan uses plus what
is carried, a count each; the professions — the character's level, each profession's level and Mastery bar %, and
the crafting EXP per level with the player's own measured values. A one-line summary under the title says where
the plan stands on every tab.

"Forge plan" (ForgePlanWindow) is a window of its own docked beside it: the plan craftplan.py makes of the inputs —
its totals, what to farm first and where it drops, then the crafts in order (materials, the profession level-ups
the plan needs, the final items), every number from the KB's tables or the player's own input. Ticking a step as
crafted moves its mats and products into the bag. It opens by itself once the plan has something to make, follows
the main window, hides with it, and its ✕ hides only the plan ("Show plan" brings it back).

Built like the bar's other windows: the NPCs window's dark glass at the opacity and in the colours the ⚙ panel
picks, a title row that drags each, the main window's place kept in craft_advisor_geom and the plan's size in
craft_plan_size. The main window takes the keyboard while its boxes are used (Esc or the ✕ gives it back), shows
while settings["craft_advisor"] is on, and its ✕ turns that off."""

from __future__ import annotations

import logging
import re
from html import escape

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import bidi, craftplan, itemterms
from ..craftplan import CATEGORY_ORDER, DEFAULT_MODE, DISCIPLINE_KEY, FarmItem, Plan, Row, Stage, Step
from ..i18n import NBSP, I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles, Settings
from . import mapview, theme
from .controls import Segmented
from .gamesearch import QSS as _GLASS_QSS, SCROLLBAR_W, _SearchBox
from .glass import EdgeResize
from .npcoverlay import (
    BOTTOM,
    FILL1,
    FILL2,
    GEOM_DEBOUNCE_MS,
    MUTED,
    OPACITY_DEFAULT,
    OPACITY_MAX,
    OPACITY_MIN,
    SIDE,
    TEXT,
    TOP,
    _flags,
    _Header,
    _paint_glass,
    accent_of,
    dock_beside,
    set_capturable,
)
from .widgets import fit_picture, zoom_on_hover

log = logging.getLogger("maplehelper")

MIN_W, MIN_H = 440, 420  # the smallest the player can drag the main window to (the table's columns still fit)
DEFAULT_W, DEFAULT_H = 460, 640  # what opens under the toolbar before it is ever resized
PLAN_W, PLAN_H = 420, 700  # the plan window until the player resizes it (shorter when the plan is)
PLAN_MIN_W, PLAN_MIN_H = 340, 200
GAP = 8  # between the toolbar and the window that opens under it
SHOW_KEY, GEOM_KEY, PLAN_SIZE_KEY = "craft_advisor", "craft_advisor_geom", "craft_plan_size"
TYPE_DEBOUNCE_MS = 150  # a pause in a search before its rows are read again
EDIT_DEBOUNCE_MS = 350  # a pause in a number box before the character is saved and re-planned
ITEM_ROWS = 30  # item rows listed before the "and N more" line
BAG_FOUND = 12  # items a bag search offers to add
MAX_MAKE = 50  # the Make steppers' ceiling
PIC = 28  # the item pictures: the table, the bag, the farm list
STEP_PIC = 24  # a craft step's product picture
ROW_PAD = 8  # a table row's own padding, left and right
COL_GAP = 6  # between the table's columns
GUTTER = 4  # between the rows and the scrollbar
ITEM_MIN = 110  # the item column's width the Recipe column must leave before it gets a column of its own
TABS = ("items", "bag", "prof")
# the table's fixed-width columns after the item's own, start to end (the header and every row share their widths)
COLS = ("lv", "stat", "recipe", "cost", "shop", "make")
NUMERIC = {"lv", "stat", "cost", "shop"}  # right-aligned (mirrored in Hebrew), like the site's number columns
CAT_KEYS = {
    "ore": "cp_cat_ore",
    "gem": "cp_cat_gem",
    "material": "cp_cat_material",
    "drop": "cp_cat_drop",
    "other": "cp_cat_other",
}
KIND_KEYS = {
    "ore": "cp_kind_ore",
    "gem": "cp_kind_gem",
    "material": "cp_kind_material",
    "drop": "cp_kind_drop",
    "other": "cp_kind_other",
}
MODE_KEYS = (("least", "cp_mode_least"), ("cheapest", "cp_mode_cheapest"), ("highest", "cp_mode_highest"))
BADGE_KEYS = {"prep": "cp_badge_prep", "level": "cp_badge_level", "final": "cp_badge_final"}
LOSS = "#FF8A80"  # crafting dearer than the shop: a warm red that reads on every glass tint
# the stage kinds' badges, readable on the dark glass whatever accent is picked (the accent lights what is chosen)
BADGE_COLORS = {"prep": "#64B5FF", "level": "#FFB066", "final": "#4CD07D"}


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})"


def qss(settings) -> str:
    """Both windows' own style at the accent the settings pick: the lit tab and mode, the picked table rows, the
    savings, the ticked boxes (npcoverlay.ACCENTS; the glass itself is painted in the tint by _paint_glass).
    Rebuilt whenever the accent is picked, so the windows follow with no more code of their own."""
    fill, on_fill = accent_of(settings)
    badges = "\n".join(
        f"#Kind{kind.title()} {{ color: {c}; background: {_rgba(c, 0.16)}; border-radius: 6px; padding: 1px 7px;"
        f" font-size: 11px; font-weight: 700; }}"
        for kind, c in BADGE_COLORS.items()
    )
    return (
        _GLASS_QSS
        + f"""
QLabel {{ background: transparent; }}
#Sub {{ color: {MUTED}; font-size: 12px; }}
#Note {{ color: {MUTED}; font-size: 11px; }}
#Summary {{ color: {TEXT}; font-size: 12px; }}
#Section {{ color: {TEXT}; font-size: 15px; font-weight: 700; padding-top: 6px; }}
QPushButton#PlanBtn {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 5px 12px;
                       font-size: 12px; font-weight: 600; text-align: center; }}
QPushButton#PlanBtn:hover {{ background: {FILL2}; }}
#Segmented {{ background: {FILL1}; border: none; border-radius: 10px; }}
QPushButton#Segment {{ background: transparent; color: {MUTED}; border: none; border-radius: 8px; min-height: 28px;
                       max-height: 28px; padding: 0 10px; font-size: 13px; font-weight: 600; text-align: center; }}
QPushButton#Segment:hover:!checked {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Segment:checked {{ background: {fill}; color: {on_fill}; font-weight: 700; }}
#ColHead {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QFrame#ItemRow, QFrame#BagRow {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#ItemRow:hover, QFrame#BagRow:hover {{ background: {FILL1}; }}
QFrame#ItemRow[picked="true"] {{ background: {_rgba(fill, 0.16)}; }}
#ItemName {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
#ItemType {{ color: {MUTED}; font-size: 11px; }}
#Cell {{ color: {TEXT}; font-size: 13px; }}
#CellMuted {{ color: {MUTED}; font-size: 13px; }}
#Recipe {{ color: {TEXT}; font-size: 12px; }}
#Saves {{ color: {fill}; font-size: 13px; font-weight: 700; }}
#SavesWord {{ color: {fill}; font-size: 11px; }}
#Loses {{ color: {LOSS}; font-size: 13px; font-weight: 700; }}
#LosesWord {{ color: {LOSS}; font-size: 11px; }}
#NotSold {{ color: {MUTED}; font-size: 11px; }}
#GroupName {{ color: {TEXT}; font-size: 13px; font-weight: 700; padding-top: 6px; }}
#GroupMeta {{ color: {MUTED}; font-size: 11px; padding-top: 6px; }}
#InPlan {{ color: {fill}; border: 1px solid {fill}; border-radius: 6px; padding: 0 6px; font-size: 11px; font-weight: 600; }}
QLineEdit#Num {{ padding: 4px 6px; }}
QFrame#ProfCard {{ background: {FILL1}; border: none; border-radius: 10px; }}
#ProfName {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#FieldName {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
#FieldLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; }}
QFrame#FoldHead {{ background: {FILL1}; border: none; border-radius: 8px; }}
QFrame#FoldHead:hover {{ background: {FILL2}; }}
#FoldTitle {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#FoldMark {{ color: {MUTED}; font-size: 13px; font-weight: 700; }}
#Mine {{ color: {fill}; font-size: 11px; font-weight: 600; }}
#Stepper {{ background: {FILL1}; border: none; border-radius: 8px; }}
QToolButton#StepBtn {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px; padding: 0;
                       min-width: 18px; max-width: 18px; min-height: 22px; max-height: 22px; font-size: 14px;
                       font-weight: 600; }}
QToolButton#StepBtn:hover {{ background: {FILL2}; }}
QToolButton#StepBtn:disabled {{ color: rgba(235,235,245,0.28); }}
#StepNum {{ color: {TEXT}; font-size: 13px; font-weight: 700; min-width: 16px; }}
#HeadBadge {{ color: {MUTED}; font-size: 12px; }}
QFrame#StatCard {{ background: {FILL1}; border: none; border-radius: 10px; }}
#BigNum {{ color: {TEXT}; font-size: 20px; font-weight: 700; }}
#CardLabel {{ color: {MUTED}; font-size: 11px; }}
QFrame#FarmRow {{ background: {FILL1}; border: none; border-radius: 10px; }}
#Kind {{ color: {MUTED}; font-size: 11px; }}
#FarmCount {{ color: {TEXT}; font-size: 18px; font-weight: 700; }}
#Muted {{ color: {MUTED}; font-size: 12px; }}
QFrame#Stage {{ background: {FILL1}; border: none; border-radius: 12px; }}
#StageNo {{ color: {MUTED}; font-size: 11px; font-weight: 700; }}
#StageMeta {{ color: {MUTED}; font-size: 12px; }}
#StageTitle {{ color: {TEXT}; font-size: 14px; font-weight: 700; }}
{badges}
QFrame#Step {{ background: transparent; border: none; border-top: 1px solid rgba(255,255,255,0.07); }}
#StepNo {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
#StepName {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
#Fee {{ color: {TEXT}; font-size: 12px; font-weight: 600; }}
QPushButton#Swap {{ background: {FILL1}; color: {MUTED}; border: none; border-radius: 6px; padding: 2px 8px;
                   font-size: 11px; text-align: center; }}
QPushButton#Swap:hover {{ background: {FILL2}; color: {TEXT}; }}
QCheckBox {{ background: transparent; color: {TEXT}; spacing: 0; }}
QCheckBox::indicator {{ background: {FILL1}; border: 1px solid rgba(255,255,255,0.25); border-radius: 5px;
                        width: 14px; height: 14px; }}
QCheckBox::indicator:checked {{ background: {fill}; border-color: {fill}; }}
"""
    )


def _match(name: str, query: str) -> bool:
    """A row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in query.lower().split() if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


def _mesos(v: int) -> str:
    return f"{int(v):,}"


def _type_word(kb, key: str, lang: str) -> str:
    """The item's type word ("Wand", "Crafting Material") from its KB type ("Equip / Wand"), in the UI's language."""
    typ = str((kb.get(key) or {}).get("type") or "") if key else ""
    last = re.split(r"\s*[/·]\s*", typ)[-1].strip() if typ else ""
    return itemterms.kind(last, lang) if last else ""


def _clear(lay) -> None:
    """Every widget out of a layout (the layouts this file clears hold widgets and stretches only)."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w is not None:
            w.hide()  # gone now: deleteLater alone left the old rows drawn under the new ones
            w.deleteLater()


def _scroller() -> tuple[QScrollArea, QVBoxLayout]:
    """A body that scrolls, in the overlay's own look: its rows' layout for the caller to fill."""
    scroll = QScrollArea(objectName="Body")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    rows = QWidget(objectName="Rows")
    lay = QVBoxLayout(rows)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    scroll.setWidget(rows)
    return scroll, lay


def _label(text: str, name: str, wrap: bool = False, align=None) -> QLabel:
    w = QLabel(text, objectName=name)
    w.setWordWrap(wrap)
    if align is not None:
        w.setAlignment(align)
    return w


def _min_width(w: QWidget) -> int:
    """The width a cell needs: a wrapping label (a column's header) its longest word, anything else its own hint."""
    if isinstance(w, QLabel) and w.wordWrap():
        fm = w.fontMetrics()
        return max((fm.horizontalAdvance(word) for word in w.text().split()), default=0) + 2
    return w.sizeHint().width()


def _pieces(items, rtl: bool, sep: str = ", ") -> str:
    """English pieces for one line ("6 Mithril Ingot, 4 Aquamarine"): a line wraps between them, never inside one
    ("2" at one line's end, "Iron Ingot" on the next); a long one keeps at least its count with its first word. In
    Hebrew each is a left-to-right block of its own, so the list reads right to left — one block across a Hebrew
    line's wrap had its start cut off and its brackets mirrored."""
    out = []
    for p in items:
        p = str(p)
        p = p.replace(" ", NBSP) if len(p) <= bidi.KEEP_TOGETHER else p.replace(" ", NBSP, 1)
        # its trailing count stays with the name: "Bronze Ore" / "×10" across a Hebrew wrap read "10×"
        head, sp, tail = p.rpartition(" ")
        p = head + NBSP + tail if sp and len(tail) <= 6 else p
        out.append(bidi.ltr_block(p, rtl))
    return sep.join(out)


class _Pictures:
    """Each item's sprite, fitted once per size: the table, the bag and the plan are drawn again on every edit, and
    each row would read its sprite from disk and scale it again."""

    def __init__(self, kb):
        self._kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple[str, int, float], QPixmap] = {}

    def label(self, key: str, size: int) -> QLabel:
        """The picture in a fixed square (an item with no KB key: the square empty, the columns still line up),
        shown large on hover."""
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
            zoom_on_hover(pic, path)
        return pic


class _Stepper(QFrame):
    """A small − n + counter in the overlay's own look: a spin box's arrows don't survive the dark glass, and the
    counts are usually small clicks."""

    changed = Signal(int)

    def __init__(self, value: int = 0, hi: int = MAX_MAKE):
        super().__init__(objectName="Stepper")
        self._hi = hi
        self._v = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)
        self._minus = QToolButton(objectName="StepBtn", text="\u2212")
        self._num = QLabel(objectName="StepNum", alignment=Qt.AlignCenter)
        self._plus = QToolButton(objectName="StepBtn", text="+")
        for b, d in ((self._minus, -1), (self._plus, 1)):
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, d=d: self.setValue(self._v + d))
        row.addWidget(self._minus)
        row.addWidget(self._num)
        row.addWidget(self._plus)
        self.setValue(value)

    def value(self) -> int:
        return self._v

    def setValue(self, v: int) -> None:
        v = min(self._hi, max(0, int(v)))
        self._v = v
        self._num.setText(str(v))
        self._minus.setEnabled(v > 0)
        self._plus.setEnabled(v < self._hi)
        self.changed.emit(v)


class _FoldHead(QFrame):
    """A foldable section's title row: its name and a +/– mark; a click folds or unfolds what is under it."""

    clicked = Signal()

    def __init__(self):
        super().__init__(objectName="FoldHead")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(6)
        self.title = QLabel(objectName="FoldTitle")
        self.mark = QLabel(objectName="FoldMark")
        row.addWidget(self.title, 1)
        row.addWidget(self.mark)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class CraftAdvisor(EdgeResize, QWidget):
    """The main window, "Crafting": the summary strip under the title, the three tabs (what to craft, the bag, the
    professions), and the Forge plan in a window of its own beside it (plan_window, made the first time it shows).
    It owns the plan: every edit here or there plans again and both windows follow."""

    closed = Signal()  # the ✕ turned the setting off: the toolbar's Crafting button unchecks

    def __init__(self, kb: KnowledgeBase, settings: Settings, profiles: Profiles, t: I18n):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)  # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self._kb = kb
        self._settings = settings
        self._profiles = profiles
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._pics = _Pictures(kb)
        self._pool: list[tuple[str, str]] | None = None  # every item the recipes use or make: the bag search's list
        self._c: Character | None = None
        self._rows: tuple[Row, ...] = ()
        self._plan: Plan | None = None
        self._had_plan = False  # the last plan had something to make (the plan window opens when it starts to)
        self._mode = DEFAULT_MODE
        self._choices: dict[str, int] = {}  # product name casefold -> the recipe number the player swapped to
        self._tab = TABS[0]
        self._tabs: Segmented | None = None
        self._cols: dict[str, list[QWidget]] = {}  # the table's cells per fixed column, the header's first
        self._col_w: dict[str, int] = {}
        self._types: list[tuple[QLabel, str, str]] = []  # each row's type line: (label, with the recipe, without)
        self._wide = False  # the Recipe column of its own (a wide window), else under each item's name
        self._recipe_placed = False  # the table filled since the Recipe column was last placed
        self._has_stat = False  # the class has an attack stat column (a Beginner's gear has none to rank by)
        self._item_rows: dict[str, QFrame] = {}  # an item row by its KB key (its Make stepper lights it)
        self._bag_boxes: dict[str, _SearchBox] = {}
        self._exp_boxes: dict[int, _SearchBox] = {}
        self._exp_marks: dict[int, QLabel] = {}
        self._exp_folded = True  # the EXP table folded under the professions until the player opens it
        self._opacity = OPACITY_DEFAULT
        self._placed = False  # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None  # the toolbar's rect, where the window first opens
        self.plan_window: ForgePlanWindow | None = None
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_items)
        self._bag_typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_bag)
        self._edit = QTimer(self, singleShot=True, interval=EDIT_DEBOUNCE_MS, timeout=self._edits_saved)
        self.setStyleSheet(qss(self._settings))
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
        self._close.clicked.connect(self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self._summary = QLabel(objectName="Summary", wordWrap=True)
        self._plan_btn = QPushButton(objectName="PlanBtn")
        self._plan_btn.setCursor(Qt.PointingHandCursor)
        self._plan_btn.setFocusPolicy(Qt.NoFocus)
        self._plan_btn.clicked.connect(self._plan_btn_clicked)
        strip.addWidget(self._summary, 1)
        strip.addWidget(self._plan_btn, 0, Qt.AlignVCenter)
        col.addLayout(strip)
        self._tabs_slot = QVBoxLayout()
        self._tabs_slot.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._tabs_slot)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_items())
        self._stack.addWidget(self._build_bag())
        self._stack.addWidget(self._build_prof())
        col.addWidget(self._stack, 1)
        self._clear_of_scrollbar()

    def _build_items(self) -> QWidget:
        """What to craft: the search box, the table's header (it stays put while the rows scroll) and the rows."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self._search = _SearchBox(objectName="Search")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self._typing.start())
        v.addWidget(self._search)
        self._head = QWidget()
        self._head_lay = QHBoxLayout(self._head)
        self._head_lay.setSpacing(COL_GAP)
        v.addWidget(self._head)
        self._items_scroll, self._items_lay = _scroller()
        bar = self._items_scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _a, _b: QTimer.singleShot(0, self, self._head_inset))
        v.addWidget(self._items_scroll, 1)
        return page

    def _build_bag(self) -> QWidget:
        """The bag: a hint, the search that adds anything else, the groups (filled by _fill_bag)."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self._bag_hint = _label("", "Note", wrap=True)
        self._bag_search = _SearchBox(objectName="Search")
        self._bag_search.setClearButtonEnabled(True)
        self._bag_search.textChanged.connect(lambda _t: self._bag_typing.start())
        self._bag_scroll, self._bag_lay = _scroller()
        v.addWidget(self._bag_hint)
        v.addWidget(self._bag_search)
        v.addWidget(self._bag_scroll, 1)
        return page

    def _build_prof(self) -> QWidget:
        """The professions: the character level, the six professions' level and bar % boxes (built once, refilled
        from the character), then the EXP table folded under them."""
        scroll, lay = _scroller()
        self._prof_lay = lay
        lay.setSpacing(8)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        self._char_word = _label("", "FieldName")
        self._lv_box = _SearchBox(objectName="Num", maxLength=3)
        self._lv_box.setFixedWidth(56)
        self._lv_box.setAlignment(Qt.AlignCenter)
        self._lv_box.textChanged.connect(lambda _t: self._edit.start())
        h.addWidget(self._char_word)
        h.addWidget(self._lv_box)
        h.addStretch(1)
        lay.addWidget(row)
        self._char_hint = _label("", "Note", wrap=True)
        lay.addWidget(self._char_hint)
        self._prof_hint = _label("", "Note", wrap=True)
        lay.addWidget(self._prof_hint)
        cards = QWidget()
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        # each profession: its name (the game's own word), the level box, the Mastery bar % box
        self._prof_boxes: dict[str, tuple[QLabel, QLabel, _SearchBox, QLabel, _SearchBox]] = {}
        for i, disc in enumerate(sorted(DISCIPLINE_KEY, key=lambda d: DISCIPLINE_KEY[d])):
            card = QFrame(objectName="ProfCard")
            cv = QVBoxLayout(card)
            cv.setContentsMargins(10, 8, 10, 10)
            cv.setSpacing(6)
            name = _label("", "ProfName")
            cv.addWidget(name)
            boxes = QHBoxLayout()
            boxes.setSpacing(6)
            lv_word, bar_word = _label("", "FieldLabel"), _label("", "FieldLabel")
            lv = _SearchBox(objectName="Num", maxLength=2)
            lv.setFixedWidth(40)
            pct = _SearchBox(objectName="Num", maxLength=3)
            pct.setFixedWidth(46)
            for box in (lv, pct):
                box.setAlignment(Qt.AlignCenter)
                box.textChanged.connect(lambda _t: self._edit.start())
            boxes.addWidget(lv_word)
            boxes.addWidget(lv)
            boxes.addStretch(1)
            boxes.addWidget(bar_word)
            boxes.addWidget(pct)
            cv.addLayout(boxes)
            grid.addWidget(card, i // 2, i % 2)
            self._prof_boxes[disc] = (name, lv_word, lv, bar_word, pct)
        lay.addWidget(cards)
        self._exp_head = _FoldHead()
        self._exp_head.clicked.connect(self._fold_exp)
        lay.addWidget(self._exp_head)
        self._exp_body = QWidget()
        self._exp_lay = QVBoxLayout(self._exp_body)
        self._exp_lay.setContentsMargins(0, 0, 0, 0)
        self._exp_lay.setSpacing(2)
        self._exp_body.setVisible(not self._exp_folded)
        lay.addWidget(self._exp_body)
        lay.addStretch(1)
        self._prof_scroll = scroll
        return scroll

    def _clear_of_scrollbar(self) -> None:
        """The bodies keep a little room on their scrollbar's side (right, or left in Hebrew, where Qt moves it), and
        the table's header follows the rows' inset."""
        rtl = self.t.rtl
        for lay in (self._items_lay, self._bag_lay, self._prof_lay):
            lay.setContentsMargins(GUTTER if rtl else 0, 0, 0 if rtl else GUTTER, 0)
        self._head_inset()

    def _head_inset(self) -> None:
        """The header's columns over the rows' own: as far in as the rows sit, the scrollbar's width (the QSS's)
        counted while the scroll area shows it (with no scrollbar the rows reach the scroll area's edge)."""
        shown = self._items_scroll.verticalScrollBar().isVisibleTo(self._items_scroll)
        side = GUTTER + (SCROLLBAR_W if shown else 0)
        rtl = self.t.rtl
        self._head_lay.setContentsMargins(ROW_PAD + (side if rtl else 0), 0, ROW_PAD + (0 if rtl else side), 0)

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ the contract (app.py wires exactly this)

    def refresh(self) -> None:
        """Shown while settings["craft_advisor"] is on; the character is read again every time it shows. The look
        (accent, tint, opacity) is taken by both windows every time."""
        if bool(self._settings[SHOW_KEY]):
            self._restyle()
            try:
                self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
            except (TypeError, ValueError):
                self._opacity = OPACITY_DEFAULT
            if not self.isVisible():
                self._rebuild()
                self._place()
                self.show()
                set_capturable(self, self._settings)
                self._sync_plan(opening=True)
            self.raise_()
            self.update()  # the glass repaints in the tint the settings pick (npcoverlay.apply_look)
            if self.plan_window is not None and self.plan_window.isVisible():
                self.plan_window.raise_()
                self.plan_window.update()
        elif self.isVisible():
            self.hide()

    def _restyle(self) -> None:
        """The accent the settings pick, in both windows' style sheets (their glass is painted in the tint by
        _paint_glass); skipped when it is the same — a restyle re-polishes every row."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        if self.plan_window is not None:
            self.plan_window.restyle(sheet)

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        if self.plan_window is not None:
            self.plan_window.apply_language(t)  # its direction and words first: the rebuild draws its plan again
        self._clear_of_scrollbar()
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A new KB: its pictures, recipes and prices; both windows drawn again from it."""
        self._kb = kb
        self._pics = _Pictures(kb)
        self._pool = None
        self._choices = {}
        self._rebuild()

    def character_changed(self) -> None:
        self._choices = {}
        self._rebuild()

    # ------------------------------------------------------------ everything again (apply_language is just this)

    def _rebuild(self) -> None:
        """Every tab and the plan again, from the active character."""
        self._c = self._profiles.active
        self._retext_chrome()
        c = self._c
        self._tabs.setVisible(c is not None)
        self._stack.setVisible(c is not None)
        if c is None:
            self._rows = ()
            self._fill_items()
            self._fill_exp()
            self._replan()
            return
        self._rows = craftplan.craftable(self._kb, c)
        self._retext_prof()
        self._fill_exp()
        self._fill_items()
        self._replan()
        QTimer.singleShot(0, self, self._show_picked)

    def _show_picked(self) -> None:
        """The table scrolled to its first picked item, its Make count in view (the class's gear runs ~80 rows): how
        the window opens."""
        c = self._c
        first = next((self._item_rows[k] for k in (c.craft_targets if c else ()) if k in self._item_rows), None)
        if first is not None:
            self._items_scroll.ensureWidgetVisible(first, 0, 60)

    def _retext_chrome(self) -> None:
        """The words that are not rows: the title, the tabs, the boxes' hints, the professions' labels."""
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t("cp_title"), rtl))
        self._close.setAccessibleName(t("cp_close"))
        self._close.setToolTip(t("cp_close"))
        self._plan_btn.setToolTip(t("cp_plan_btn_tip"))
        self._retext_plan_btn()
        if self._tabs is not None:
            self._tabs_slot.removeWidget(self._tabs)
            self._tabs.hide()
            self._tabs.deleteLater()
        words = {"items": "cp_items", "bag": "cp_bag", "prof": "cp_tab_prof"}
        self._tabs = Segmented([(t(words[k]), k) for k in TABS], self._tab, rtl)
        self._tabs.set_label(t("cp_tabs_a11y"))
        for b in self._tabs.group.buttons():
            b.setFocusPolicy(Qt.NoFocus)
        self._tabs.changed.connect(self._tab_set)
        self._tabs_slot.addWidget(self._tabs)
        self._stack.setCurrentIndex(TABS.index(self._tab))
        self._search.setPlaceholderText(bidi.plain(t("cp_search_ph"), rtl))
        self._bag_search.setPlaceholderText(bidi.plain(t("cp_bag_search_ph"), rtl))
        self._bag_hint.setText(bidi.plain(t("cp_bag_hint"), rtl))
        self._char_word.setText(bidi.plain(t("cp_char"), rtl))
        self._char_hint.setText(bidi.plain(t("cp_char_tip"), rtl))
        self._lv_box.setAccessibleName(t("cp_char"))
        self._lv_box.setToolTip(t("cp_char_tip"))
        self._prof_hint.setText(bidi.plain(t("cp_prof_hint"), rtl))
        for disc, (name, lv_word, lv, bar_word, pct) in self._prof_boxes.items():
            word = bidi.ltr_name(disc, rtl)
            name.setText(word)
            lv_word.setText(bidi.plain(t("cp_prof_lv"), rtl))
            bar_word.setText(bidi.plain(t("cp_bar"), rtl))
            lv.setAccessibleName(t("cp_prof_tip", disc=disc))
            lv.setToolTip(t("cp_prof_tip", disc=disc))
            pct.setAccessibleName(t("cp_bar_tip"))
            pct.setToolTip(t("cp_bar_tip"))
        self._exp_head.title.setText(bidi.plain(t("cp_exp_table"), rtl))
        self._exp_head.mark.setText("+" if self._exp_folded else "\u2013")

    def _retext_prof(self) -> None:
        """The professions' boxes filled from the character (signals off: a re-read is not the player's edit). An
        unlearned profession's box shows the 1 the plan counts it as, greyed (its placeholder)."""
        c = self._c
        self._lv_box.blockSignals(True)
        self._lv_box.setText(str(c.level) if c.level else "")
        self._lv_box.blockSignals(False)
        for disc, (_name, _lw, lv, _bw, pct) in self._prof_boxes.items():
            key = DISCIPLINE_KEY[disc]
            for box, have, hint in ((lv, c.crafts.get(key), "1"), (pct, c.craft_pct.get(key), "0")):
                box.blockSignals(True)
                box.setText(str(have) if have else "")
                box.setPlaceholderText(hint)
                box.blockSignals(False)

    def _retext_summary(self) -> None:
        """The strip under the title: the job, the level, how many items the plan makes and what it costs."""
        t, rtl = self.t, self.t.rtl
        c = self._c
        if c is None:
            self._summary.setText(bidi.plain(t("cp_no_character"), rtl))
            return
        parts = [bidi.ltr_block(c.job_label or c.base_class, rtl), t("lv_short", n=c.level)]
        n = len(c.craft_targets)
        if not n:
            parts.append(t("cp_sum_none"))
        else:
            parts.append(t("cp_sum_targets", n=n))
            if self._plan is not None and self._plan.stages:
                parts.append(t("cp_mesos", n=bidi.ltr_block(_mesos(self._plan.cost), rtl)))
        self._summary.setText(bidi.plain(" · ".join(parts), rtl))

    def _retext_plan_btn(self) -> None:
        shown = self.plan_window is not None and self.plan_window.isVisible()
        self._plan_btn.setText(bidi.plain(self.t("cp_hide_plan" if shown else "cp_show_plan"), self.t.rtl))

    def _tab_set(self, key: str) -> None:
        self._tab = key
        self._stack.setCurrentIndex(TABS.index(key))

    # ------------------------------------------------------------ what to craft: the table

    def _fill_items(self) -> None:
        """The table for what is typed, each row's Make stepper wired to the character's craft_targets. Nothing typed:
        the class's gear — its weapons first (the stat column is theirs), then the rest, each by level as the site
        lists them — up to ITEM_ROWS, then "and N more" (the search finds them); a picked item the cut left out goes
        on top, so it can still be unpicked. The rows are filled again on typing without a rebuild, so the keyboard
        stays in the search box."""
        t, rtl = self.t, self.t.rtl
        bar = self._items_scroll.verticalScrollBar()
        at = bar.value()
        _clear(self._items_lay)
        _clear(self._head_lay)
        self._cols = {k: [] for k in COLS}
        self._types = []
        self._item_rows = {}
        c = self._c
        if c is None:
            return
        query = self._search.text().strip()
        if query:
            rows = [r for r in self._rows if _match(r.name, query)]
            shown = rows[:ITEM_ROWS]
        else:
            gear = sorted((r for r in self._rows if r.equip), key=lambda r: not isinstance(r.stat, int))
            cut = {r.key for r in gear[:ITEM_ROWS]}
            picked = [r for r in self._rows if r.key in c.craft_targets and r.key not in cut]
            rows, shown = picked + gear, picked + gear[:ITEM_ROWS]
        stat_word = next((r.stat_word for r in self._rows if r.stat_word), "")
        self._has_stat = bool(stat_word)
        self._fill_head(stat_word)
        for r in shown:
            row = self._item_row(r, self._has_stat)
            self._item_rows[r.key] = row
            self._items_lay.addWidget(row)
        if len(rows) > len(shown):
            self._items_lay.addWidget(_label(bidi.plain(t("cp_more", n=len(rows) - len(shown)), rtl), "Note"))
        if query and not rows:
            self._items_lay.addWidget(_label(bidi.plain(t("cp_nothing_found"), rtl), "Sub", wrap=True))
        self._items_lay.addSpacing(6)
        self._items_lay.addWidget(_label(bidi.plain(t("cp_items_hint"), rtl), "Note", wrap=True))
        self._items_lay.addStretch(1)
        if not self._has_stat:
            for w in self._cols["stat"]:
                w.setVisible(False)  # a class with no attack stat to rank by: no column for it
        self._measure_cols()
        if not query:
            QTimer.singleShot(0, self, lambda: bar.setValue(at))

    def _fill_head(self, stat_word: str) -> None:
        """The header row: the column names, muted and small, aligned as their columns are."""
        t, rtl = self.t, self.t.rtl
        spacer = QWidget()
        spacer.setFixedWidth(PIC)
        self._head_lay.addWidget(spacer)
        self._head_lay.addWidget(_label(bidi.plain(t("cp_col_item"), rtl), "ColHead", align=Qt.AlignBottom), 1)
        words = {
            "lv": bidi.plain(t("cp_col_lv"), rtl),
            "stat": bidi.ltr_name(stat_word, rtl) if stat_word else "",
            "recipe": bidi.plain(t("cp_col_recipe"), rtl),
            "cost": bidi.plain(t("cp_col_cost"), rtl),
            "shop": bidi.plain(t("cp_col_shop"), rtl),
            "make": bidi.plain(t("cp_make"), rtl),
        }
        for k in COLS:
            align = Qt.AlignRight if k in NUMERIC else Qt.AlignHCenter if k == "make" else Qt.AlignLeft
            w = _label(words[k], "ColHead", wrap=True, align=align | Qt.AlignBottom)
            self._cols[k].append(w)
            self._head_lay.addWidget(w)

    def _item_row(self, r: Row, has_stat: bool) -> QFrame:
        """One row: the picture, the name over its type word, the level, the class's stat, the recipe, the craft
        cost, what it saves against the shop, and the Make stepper (a picked row lit in the accent)."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="ItemRow")
        row.setAttribute(Qt.WA_Hover, True)
        row.setProperty("picked", self._c.craft_targets.count(r.key) > 0)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(ROW_PAD, 8, ROW_PAD, 8)
        lay.setSpacing(COL_GAP)
        lay.addWidget(self._pics.label(r.key, PIC), 0, Qt.AlignVCenter)
        item = QWidget()
        iv = QVBoxLayout(item)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(1)
        iv.addWidget(_label(bidi.ltr_name(r.name, rtl), "ItemName", wrap=True))
        kind = _type_word(self._kb, r.key, t.lang)
        recipe = f"{r.discipline} {r.prof_lv}"
        kind_txt = bidi.plain(kind, rtl) if kind else ""
        narrow = bidi.plain(" · ".join(x for x in (kind_txt, bidi.ltr_block(recipe, rtl)) if x), rtl)
        type_lab = _label(narrow, "ItemType", wrap=True)
        self._types.append((type_lab, narrow, kind_txt))
        iv.addWidget(type_lab)
        lay.addWidget(item, 1)
        num = Qt.AlignRight | Qt.AlignVCenter

        def cell(text: str, muted: bool = False) -> QLabel:
            return _label(bidi.ltr_block(text, rtl), "CellMuted" if muted else "Cell", align=num)

        lv_ok = isinstance(r.lv, int)
        stat_ok = has_stat and isinstance(r.stat, int)
        cells = {
            "lv": cell(str(r.lv) if lv_ok else "\u2014", muted=not lv_ok),
            "stat": cell(str(r.stat) if stat_ok else "\u2014", muted=not stat_ok),
            "recipe": _label(bidi.ltr_name(recipe, rtl), "Recipe", align=Qt.AlignLeft | Qt.AlignVCenter),
            "cost": cell(_mesos(r.cost)),
            "shop": self._shop_cell(r),
        }
        step = _Stepper(self._c.craft_targets.count(r.key))
        step.setToolTip(t("cp_make_tip"))
        step.setAccessibleName(t("cp_make_tip"))
        step.changed.connect(lambda v, k=r.key: self._make_set(k, v))
        cells["make"] = step
        for k in COLS:
            self._cols[k].append(cells[k])
            lay.addWidget(cells[k], 0, Qt.AlignVCenter)
        return row

    def _shop_cell(self, r: Row) -> QWidget:
        """vs shop: what crafting saves against the NPC's price (in the accent), what it loses (a warm red), or that
        no NPC sells it (muted) — the word over the number, so the column stays narrow."""
        t, rtl = self.t, self.t.rtl
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        align = Qt.AlignRight | Qt.AlignVCenter
        if r.save is None:
            v.addWidget(_label(bidi.plain(t("cp_not_sold"), rtl), "NotSold", align=align))
            return box
        saves = r.save >= 0
        word, num = ("cp_saves", "Saves") if saves else ("cp_loses", "Loses")
        v.addWidget(_label(bidi.plain(t(word), rtl), num + "Word", align=align))
        v.addWidget(_label(bidi.ltr_block(_mesos(abs(r.save)), rtl), num, align=align))
        return box

    def _measure_cols(self) -> None:
        """Every fixed column as wide as its widest cell (the header's word counted), so the header and the rows
        line up; then the Recipe column placed for the window's width."""
        self._col_w = {}
        for k in COLS:
            cells = self._cols[k]
            for w in cells:
                w.ensurePolished()
            self._col_w[k] = max((_min_width(w) for w in cells), default=0)
            for w in self._cols[k]:
                w.setFixedWidth(self._col_w[k])
        self._recipe_placed = False
        self._place_recipe()

    def _place_recipe(self) -> None:
        """The Recipe column gets a column of its own when the window is wide enough to leave the item's name
        ITEM_MIN of room; narrower, the recipe joins the type word under the name ("Wand · Arcforge 6") — at the
        default width the eight columns left the name ~70 px."""
        if not self._col_w:
            return
        shown = [k for k in COLS if k != "recipe" and (k != "stat" or self._has_stat)]
        fixed = PIC + sum(self._col_w[k] for k in shown) + COL_GAP * (len(shown) + 1)
        room = self.width() - 2 * SIDE - SCROLLBAR_W - GUTTER - 2 * ROW_PAD
        wide = room - fixed - self._col_w["recipe"] - COL_GAP >= ITEM_MIN
        if self._recipe_placed and wide == self._wide:
            return  # (a drag-resize calls this on every step)
        self._wide, self._recipe_placed = wide, True
        for w in self._cols["recipe"]:
            w.setVisible(wide)
        for lab, narrow, kind in self._types:
            text = kind if wide else narrow
            lab.setText(text)
            lab.setVisible(bool(text))

    def _make_set(self, key: str, v: int) -> None:
        """The Make stepper: craft_targets holds a key once per make (duplicates are the count); the row lights
        while it is picked, and the plan follows."""
        c = self._profiles.active
        if c is None:
            return
        have = c.craft_targets.count(key)
        if v == have:
            return
        if v > have:
            c.craft_targets.extend([key] * (v - have))
        else:
            for _ in range(have - v):
                c.craft_targets.remove(key)
        self._profiles.save()
        row = self._item_rows.get(key)
        if row is not None:
            row.setProperty("picked", v > 0)
            row.style().unpolish(row)
            row.style().polish(row)
        self._replan()

    # ------------------------------------------------------------ the plan (both windows follow it)

    def _replan(self) -> None:
        """The plan made again from the character: the summary, the bag (its "in plan" marks) and the plan window
        follow."""
        self._plan = None
        c = self._c
        if c is not None and c.craft_targets:
            try:
                self._plan = craftplan.plan(
                    self._kb,
                    c,
                    list(c.craft_targets),
                    dict(c.craft_bag),
                    self._mode,
                    self._settings["craft_exp_table"],
                    self._choices,
                )
            except Exception:  # a plan must never take the window down
                log.warning("craft plan failed", exc_info=True)
                self._plan = None
        self._retext_summary()
        self._fill_bag()
        self._sync_plan()

    def _has_plan(self) -> bool:
        return self._plan is not None and any(st.steps for st in self._plan.stages)

    def _sync_plan(self, opening: bool = False) -> None:
        """The plan window opens by itself when the plan starts to have something to make (or the main window opens
        on one); otherwise, shown, it is drawn again. The player's ✕ on it holds until then."""
        has = self._has_plan()
        win = self.plan_window
        if self.isVisible() and has and (opening or not self._had_plan):
            self._open_plan()
        elif win is not None and win.isVisible():
            win.redraw()
        self._had_plan = has

    def _open_plan(self) -> None:
        if self.plan_window is None:
            self.plan_window = ForgePlanWindow(self)
        self.plan_window.redraw()
        self.plan_window.show_beside()

    def _plan_btn_clicked(self, _=False) -> None:
        """Show plan / Hide plan: the plan window, whatever the plan holds (an empty plan says what to do)."""
        if self.plan_window is not None and self.plan_window.isVisible():
            self.plan_window.hide()
        else:
            self._open_plan()

    def _key_of(self, name: str, fallback: str = "") -> str:
        """An item's KB key: the recipes table's product key, else the KB's own name lookup."""
        for rec in craftplan.recipes(self._kb).get(name.casefold(), ()):
            if rec.key:
                return rec.key
        got = getattr(self._kb, "_item_by_name", {}).get(name.lower(), "")
        return f"item/{got}" if got else fallback

    def _ticked(self, s: Step, on: bool) -> None:
        """A step ticked as crafted: its ingredients leave the bag (what is there of them), its products enter it,
        and the plan follows — its shapes shrink around the new bag, like the site's."""
        c = self._profiles.active
        if c is None or not on:
            return
        for ing in s.ingredients:
            key = ing.key or self._key_of(ing.name)
            if not key:
                continue
            have = int(c.craft_bag.get(key, 0) or 0)
            used = min(have, ing.qty * s.crafts)
            if used:
                left = have - used
                if left > 0:
                    c.craft_bag[key] = left
                else:
                    c.craft_bag.pop(key, None)
        pkey = s.key or self._key_of(s.name)
        if pkey:
            c.craft_bag[pkey] = c.craft_bag.get(pkey, 0) + s.crafts * s.makes
        self._profiles.save()
        self._replan()

    def _swap(self, s: Step) -> None:
        """The Swap button: the next of this product's recipes (round the ring), the plan follows."""
        variants = craftplan.recipes(self._kb).get(s.name.casefold(), ())
        if len(variants) < 2:
            return
        nums = [v.num for v in variants]
        cur = self._choices.get(s.name.casefold())
        if cur not in nums:
            cur = next((v.num for v in variants if v.discipline == s.discipline and v.prof_lv == s.prof_lv), nums[0])
        self._choices[s.name.casefold()] = nums[(nums.index(cur) + 1) % len(nums)]
        self._replan()

    def _mode_set(self, mode: str) -> None:
        self._mode = mode
        self._replan()

    # ------------------------------------------------------------ the bag

    def _bag_pool_items(self) -> list[tuple[str, str]]:
        """Every item a recipe uses or makes (name, KB key), by name: what the bag search can add — anything else the
        plan would never spend."""
        if self._pool is None:
            seen: dict[str, tuple[str, str]] = {}
            for variants in craftplan.recipes(self._kb).values():
                for rec in variants:
                    if rec.key:
                        seen.setdefault(rec.product.casefold(), (rec.product, rec.key))
                    for ing in rec.ingredients:
                        if ing.key:
                            seen.setdefault(ing.name.casefold(), (ing.name, ing.key))
            self._pool = sorted(seen.values(), key=lambda p: p[0].casefold())
        return self._pool

    def _fill_bag(self) -> None:
        """The bag: what the plan uses plus what is carried, in the site's groups (ores, gem ores, refined materials,
        mob drops, other), a count box each; what the search finds above them, to add. The box being typed in keeps
        the keyboard across the refill."""
        t, rtl = self.t, self.t.rtl
        focus = next(((k, b.cursorPosition()) for k, b in self._bag_boxes.items() if b.hasFocus()), None)
        bar = self._bag_scroll.verticalScrollBar()
        at = bar.value()
        _clear(self._bag_lay)
        self._bag_boxes = {}
        c = self._c
        if c is None:
            return
        entries: dict[str, tuple[str, str, bool]] = {}  # name casefold -> (name, KB key, in the plan)
        if self._plan is not None:
            for f in self._plan.farm:
                entries.setdefault(f.name.casefold(), (f.name, f.key, True))
            for st in self._plan.stages:
                for s in st.steps:
                    if not s.final:
                        entries.setdefault(s.name.casefold(), (s.name, s.key or self._key_of(s.name), True))
        for k, n in c.craft_bag.items():
            name = str((self._kb.get(k) or {}).get("name") or "")
            if name and int(n or 0) > 0:
                entries.setdefault(name.casefold(), (name, k, False))
        query = self._bag_search.text().strip()
        if query:
            found = [(n, k) for n, k in self._bag_pool_items() if n.casefold() not in entries and _match(n, query)]
            self._bag_lay.addWidget(_label(bidi.plain(t("cp_bag_found"), rtl), "GroupName"))
            if not found:
                self._bag_lay.addWidget(_label(bidi.plain(t("cp_bag_none_found"), rtl), "Sub", wrap=True))
            for name, key in found[:BAG_FOUND]:
                self._bag_lay.addWidget(self._bag_row(name, key, False))
        if not entries and not query:
            self._bag_lay.addWidget(_label(bidi.plain(t("cp_bag_empty"), rtl), "Sub", wrap=True))
        cats = craftplan.categories(self._kb)
        groups: dict[str, list[tuple[str, str, bool]]] = {}
        for name, key, in_plan in entries.values():
            if key:  # (the bag holds KB keys: an item the KB has no page for can't be counted)
                groups.setdefault(cats.get(name.casefold(), "other"), []).append((name, key, in_plan))
        for cat in sorted(groups, key=lambda g: CATEGORY_ORDER.get(g, 9)):
            items = sorted(groups[cat], key=lambda g: g[0].casefold())
            self._bag_lay.addWidget(self._group_head(cat, items))
            for name, key, in_plan in items:
                self._bag_lay.addWidget(self._bag_row(name, key, in_plan))
        self._bag_lay.addStretch(1)
        if focus is not None and focus[0] in self._bag_boxes:
            box = self._bag_boxes[focus[0]]
            box.setFocus()
            box.setCursorPosition(focus[1])
        QTimer.singleShot(0, self, lambda: bar.setValue(at))

    def _group_head(self, cat: str, items: list[tuple[str, str, bool]]) -> QWidget:
        """A group's name and its counts: "Ores  3 items · 3 in plan · 2 in bag"."""
        t, rtl = self.t, self.t.rtl
        bag = self._c.craft_bag
        head = QWidget()
        h = QHBoxLayout(head)
        h.setContentsMargins(0, 4, 0, 0)
        h.setSpacing(8)
        h.addWidget(_label(bidi.plain(t(CAT_KEYS.get(cat, "cp_cat_other")), rtl), "GroupName"))
        bits = [t("cp_targets", n=len(items)), t("cp_g_plan", n=sum(1 for i in items if i[2]))]
        carried = sum(1 for i in items if int(bag.get(i[1], 0) or 0) > 0)
        if carried:
            bits.append(t("cp_g_bag", n=carried))
        h.addWidget(_label(bidi.plain(" · ".join(bits), rtl), "GroupMeta"), 1)
        return head

    def _bag_row(self, name: str, key: str, in_plan: bool) -> QWidget:
        """One bag item: its picture, its name, "in plan" when the plan uses it, and its count box."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="BagRow")
        row.setAttribute(Qt.WA_Hover, True)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(ROW_PAD, 5, ROW_PAD, 5)
        lay.setSpacing(8)
        lay.addWidget(self._pics.label(key, PIC), 0, Qt.AlignVCenter)
        lay.addWidget(_label(bidi.ltr_name(name, rtl), "ItemName", wrap=True), 1)
        if in_plan:
            lay.addWidget(_label(bidi.plain(t("cp_in_plan"), rtl), "InPlan"), 0, Qt.AlignVCenter)
        box = _SearchBox(objectName="Num", maxLength=6)
        box.setFixedWidth(64)
        box.setAlignment(Qt.AlignCenter)
        have = int(self._c.craft_bag.get(key, 0) or 0)
        box.setText(str(have) if have else "")
        box.setPlaceholderText("0")
        box.setToolTip(t("cp_bag_tip"))
        box.setAccessibleName(t("cp_bag_tip"))
        box.textChanged.connect(lambda _t: self._edit.start())
        self._bag_boxes[key] = box
        lay.addWidget(box, 0, Qt.AlignVCenter)
        return row

    # ------------------------------------------------------------ the professions' EXP table

    def _fill_exp(self) -> None:
        """One row per crafting level: the EXP to the next (a box — the player's measured value replaces the KB's)
        and whose number it is, saved to settings["craft_exp_table"]."""
        t, rtl = self.t, self.t.rtl
        _clear(self._exp_lay)
        self._exp_boxes = {}
        self._exp_marks = {}
        self._exp_lay.addWidget(_label(bidi.plain(t("cp_exp_hint"), rtl), "Note", wrap=True))
        arrow = "\u2190" if rtl else "\u2192"  # the way the levels read: right to left in Hebrew
        for e in craftplan.exp_needs(self._kb, self._settings["craft_exp_table"]):
            if e.level >= craftplan.LEVEL_CAP or e.exp is None:
                continue
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(ROW_PAD, 2, ROW_PAD, 2)
            h.setSpacing(8)
            words = f"{t('lv_short', n=e.level)} {arrow} {t('lv_short', n=e.level + 1)}"
            h.addWidget(_label(bidi.plain(words, rtl), "Cell"), 1)
            box = _SearchBox(objectName="Num", maxLength=6)
            box.setFixedWidth(72)
            box.setAlignment(Qt.AlignCenter)
            box.setText(str(e.exp))
            box.setToolTip(t("cp_exp_tip"))
            box.setAccessibleName(t("cp_exp_tip"))
            box.textChanged.connect(lambda _t: self._edit.start())
            mark = _label("", "Note")
            self._exp_boxes[e.level] = box
            self._exp_marks[e.level] = mark
            h.addWidget(box)
            h.addWidget(mark)
            self._exp_lay.addWidget(row)
        if self._exp_marks:
            # every mark as wide as the wider of its two words (bold, as the player's own is), so the boxes line up
            probe = next(iter(self._exp_marks.values()))
            probe.ensurePolished()
            bold = QFont(probe.font())
            bold.setBold(True)
            fm = QFontMetrics(bold)
            width = max(fm.horizontalAdvance(bidi.plain(t(k), rtl)) for k in ("cp_exp_kb", "cp_exp_yours")) + 4
            for mark in self._exp_marks.values():
                mark.setFixedWidth(width)
        self._mark_exp()

    def _mark_exp(self) -> None:
        """Each EXP row's mark: the player's own number (in the accent) or the KB's."""
        t, rtl = self.t, self.t.rtl
        mine = {e.level for e in craftplan.exp_needs(self._kb, self._settings["craft_exp_table"]) if e.measured}
        for lv, mark in self._exp_marks.items():
            name = "Mine" if lv in mine else "Note"
            mark.setText(bidi.plain(t("cp_exp_yours" if lv in mine else "cp_exp_kb"), rtl))
            if mark.objectName() != name:
                mark.setObjectName(name)
                mark.style().unpolish(mark)
                mark.style().polish(mark)

    def _fold_exp(self) -> None:
        self._exp_folded = not self._exp_folded
        self._exp_body.setVisible(not self._exp_folded)
        self._exp_head.mark.setText("+" if self._exp_folded else "\u2013")

    # ------------------------------------------------------------ the player's own numbers

    def _edits_saved(self) -> None:
        """The pause held: the level box, the profession boxes, the bag counts and the EXP boxes saved (an empty box
        unsets), then the plan made again. Nothing is rebuilt under the box being typed in."""
        c = self._profiles.active
        if c is None:
            return
        v = self._lv_box.text().strip()
        if v.isdigit() and 0 < int(v) <= 250:
            c.level = int(v)
        for disc, (_name, _lw, lv, _bw, pct) in self._prof_boxes.items():
            key = DISCIPLINE_KEY[disc]
            a, b = lv.text().strip(), pct.text().strip()
            if a.isdigit() and 0 < int(a) <= craftplan.LEVEL_CAP:
                c.crafts[key] = int(a)
            elif key in c.crafts:
                c.crafts.pop(key, None)
            if b.isdigit() and 0 <= int(b) <= 100:
                c.craft_pct[key] = int(b)
            elif key in c.craft_pct:
                c.craft_pct.pop(key, None)
        for key, box in self._bag_boxes.items():
            n = box.text().strip()
            if n.isdigit() and int(n) > 0:
                c.craft_bag[key] = int(n)
            else:
                c.craft_bag.pop(key, None)
        table = dict(self._settings["craft_exp_table"] or {})
        kb_exp = {e.level: e.exp for e in craftplan.exp_needs(self._kb, None)}
        for lv, box in self._exp_boxes.items():
            n = box.text().strip()
            if n.isdigit() and int(n) > 0 and int(n) != kb_exp.get(lv):
                table[str(lv)] = int(n)
            else:
                table.pop(str(lv), None)
        self._settings["craft_exp_table"] = table or None
        self._profiles.save()
        self._mark_exp()
        self._replan()

    # ------------------------------------------------------------ closing

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's Crafting button follows), hidden until switched back on."""
        self._settings[SHOW_KEY] = False
        self.hide()
        self.closed.emit()

    def hideEvent(self, e) -> None:
        for timer in (self._typing, self._bag_typing, self._edit):  # nothing fires once hidden
            timer.stop()
        if self.plan_window is not None:
            self.plan_window.hide()  # the plan goes with it (not the player's ✕: it opens again with it)
        if self._placed:  # hidden (the toolbar, Esc, the ✕): keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    # ------------------------------------------------------------ place, move, resize

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: under the toolbar again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings[GEOM_KEY]
        size = (
            mapview.clamp_size(saved["w"], saved["h"], screens, self.minimumSize())
            if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int)
            else QSize(self.width(), self.height())
        )
        spot = mapview.saved_spot(saved, size, screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(size), size))
        self._placed = True

    def _default_spot(self, size: QSize) -> QPoint:
        """Just under the toolbar (the bar's Crafting button opens this), kept on that screen; without one — the bar
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

    def _dock_plan(self) -> None:
        if self.plan_window is not None and self.plan_window.isVisible():
            self.plan_window.dock()  # the plan follows the main window

    def moveEvent(self, e) -> None:
        self._geom.start()
        self._dock_plan()
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        self._dock_plan()  # a left-edge drag moves the main window's left side
        self._place_recipe()
        super().resizeEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings[GEOM_KEY] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}


class ForgePlanWindow(EdgeResize, QWidget):
    """The Forge plan, a window of its own docked beside the main one (its left, or its right where the screen has
    no room): the totals, what to farm first, the crafts in order. It never takes the keyboard from the game (its
    boxes and buttons still click), moves and hides with the main window, and its ✕ hides only it. The size the
    player drags it to is kept (craft_plan_size); until then it is PLAN_W wide and as tall as the plan, up to
    PLAN_H, the body scrolling past that."""

    def __init__(self, owner: CraftAdvisor):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)  # hovering the rim shows the resize cursor
        self.setMinimumSize(PLAN_MIN_W, PLAN_MIN_H)
        self._owner = owner
        self._fit_size = QSize()  # the size the window last gave itself (a resize to it isn't the player's)
        self._sized = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._resized)
        self.setLayoutDirection(owner.layoutDirection())
        self.setStyleSheet(owner.styleSheet())
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        self._head = _Header()
        hrow = QHBoxLayout(self._head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(8)
        self._title = QLabel(objectName="Title")
        self._badge = QLabel(objectName="HeadBadge")
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.clicked.connect(lambda _=False: self.hide())
        hrow.addWidget(self._title)
        hrow.addWidget(self._badge, 1, Qt.AlignVCenter)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(self._head)
        self._scroll, self._rows_lay = _scroller()
        self._rows_lay.setSpacing(8)
        col.addWidget(self._scroll, 1)
        self._retext_chrome()
        self.resize(self._saved_size() or QSize(PLAN_W, PLAN_H))

    # ------------------------------------------------------------ the owner's calls

    def redraw(self) -> None:
        """The plan drawn again (its scroll kept): a new plan, the language, the KB."""
        bar = self._scroll.verticalScrollBar()
        at = bar.value()
        _clear(self._rows_lay)
        self._draw()
        self._rows_lay.addStretch(1)
        self._fit()
        QTimer.singleShot(0, self, lambda: bar.setValue(at))
        if self.isVisible():
            self.dock()
            self.update()

    def show_beside(self) -> None:
        """Shown (or kept) docked beside the main window."""
        self.dock()
        if not self.isVisible():
            self.show()
        set_capturable(self, self._owner._settings)
        self.raise_()
        self.update()

    def dock(self) -> None:
        dock_beside(self, self._owner)

    def restyle(self, sheet: str) -> None:
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        self.update()  # the tint

    def apply_language(self, t: I18n) -> None:
        """The new language's direction and title; the owner's rebuild then draws the plan in it (a hidden plan is
        drawn when it next shows)."""
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext_chrome()

    def _retext_chrome(self) -> None:
        t, rtl = self._owner.t, self._owner.t.rtl
        self._title.setText(bidi.plain(t("cp_plan"), rtl))
        self._close.setAccessibleName(t("cp_plan_close"))
        self._close.setToolTip(t("cp_plan_close"))
        rows_side = (GUTTER if rtl else 0, 0, 0 if rtl else GUTTER, 0)
        self._rows_lay.setContentsMargins(*rows_side)

    # ------------------------------------------------------------ the plan

    def _add(self, w: QWidget) -> QWidget:
        self._rows_lay.addWidget(w)
        return w

    def _text(self, text: str, name: str = "Sub") -> QLabel:
        return self._add(_label(bidi.plain(text, self._owner.t.rtl), name, wrap=True))

    def _draw(self) -> None:
        o = self._owner
        t = o.t
        c, plan = o._c, o._plan
        n = len(c.craft_targets) if c is not None else 0
        self._badge.setText(bidi.plain(t("cp_targets", n=n), t.rtl) if n else "")
        if c is None:
            self._text(t("cp_no_character"))
            return
        if not o._has_plan():
            key = "cp_plan_empty" if not n else "cp_plan_failed" if plan is None else "cp_plan_done"
            self._text(t(key))
            return
        self._add(self._totals(plan))
        self._farm(plan)
        self._order(plan)
        if plan.leftovers:
            pairs = _pieces((f"{name} ×{q}" for name, q in plan.leftovers), t.rtl)
            self._text(t("cp_leftover", list=pairs))
        self._notes(plan)

    def _totals(self, plan: Plan) -> QWidget:
        """The three totals in a row: crafting cost, craft actions, equip level — a big number over a small word."""
        t, rtl = self._owner.t, self._owner.t.rtl
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for value, word in (
            (_mesos(plan.cost), "cp_total_cost"),
            (f"{plan.crafts:,}", "cp_total_crafts"),
            (str(plan.equip_lv) if plan.equip_lv else "\u2014", "cp_equip_lv"),
        ):
            card = QFrame(objectName="StatCard")
            v = QVBoxLayout(card)
            v.setContentsMargins(12, 9, 12, 9)
            v.setSpacing(0)
            v.addWidget(_label(bidi.ltr_block(value, rtl), "BigNum"))
            v.addWidget(_label(bidi.plain(t(word), rtl), "CardLabel", wrap=True))
            h.addWidget(card, 1)
        return box

    def _farm(self, plan: Plan) -> None:
        """1 · Farm these first: what is still missing, the total in brackets when the bag covers some, where each
        comes from; then what the bag gives."""
        t, rtl = self._owner.t, self._owner.t.rtl
        self._text(t("cp_farm"), "Section")
        farm = [f for f in plan.farm if f.farm > 0]
        if farm:
            self._text(t("cp_farm_hint"), "Note")
            for f in farm:
                self._add(self._farm_row(f))
        else:
            self._text(t("cp_farm_none"))
        if plan.bag_used:
            pairs = _pieces((f"{name} ×{q}" for name, q in plan.bag_used), rtl)
            self._text(t("cp_from_bag", list=pairs))

    def _farm_row(self, f: FarmItem) -> QWidget:
        """One raw mat to farm: its picture, its name and kind, its top droppers or the NPC selling it under them,
        and how many are still missing big on the end."""
        o = self._owner
        t, rtl = o.t, o.t.rtl
        row = QFrame(objectName="FarmRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(10)
        h.addWidget(o._pics.label(f.key, PIC), 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_label(bidi.ltr_name(f.name, rtl), "ItemName"))
        top.addWidget(_label(bidi.plain(t(KIND_KEYS.get(f.category, "cp_kind_other")), rtl), "Kind"), 1)
        mid.addLayout(top)
        bits = []
        if f.droppers:
            # each dropper whole ("Snail (Lv. 1)"): the line wraps between them, never between a name and its level
            drops = " · ".join(
                f"{bidi.ltr_block(m.replace(' ', NBSP), rtl)}{NBSP}({t('lv_short', n=lv).replace(' ', NBSP)})"
                for m, lv in f.droppers
            )
            bits.append(t("cp_drops_from", list=drops))
        if f.sellers:
            npc, place, price = f.sellers[0]
            bits.append(t("cp_buy_at", who=_pieces((npc, place, _mesos(price)), rtl, " · ")))
        if f.price is None and not f.droppers:
            bits.append(t("cp_no_price"))
        for bit in bits:
            mid.addWidget(_label(bidi.plain(bit, rtl), "Muted", wrap=True))
        h.addLayout(mid, 1)
        end = QVBoxLayout()
        end.setSpacing(0)
        end_align = Qt.AlignRight | Qt.AlignTop
        end.addWidget(_label(bidi.ltr_block(_mesos(f.farm), rtl), "FarmCount", align=end_align))
        if f.bag > 0:
            total = t("cp_farm_total", n=bidi.ltr_block(_mesos(f.need), rtl))
            end.addWidget(_label(bidi.plain(f"({total})", rtl), "Note", align=end_align))
        end.addStretch(1)
        h.addLayout(end)
        return row

    def _order(self, plan: Plan) -> None:
        """2 · Then craft in this order: how recipe variants are picked, then each stage as a card."""
        o = self._owner
        t, rtl = o.t, o.t.rtl
        self._text(t("cp_order"), "Section")
        mode = Segmented([(t(key), m) for m, key in MODE_KEYS], o._mode, rtl)
        mode.set_label(t("cp_mode_tip"))
        mode.setToolTip(t("cp_mode_tip"))
        for b in mode.group.buttons():
            b.setFocusPolicy(Qt.NoFocus)
        mode.changed.connect(o._mode_set)
        self._add(mode)
        stages = [st for st in plan.stages if st.steps]
        for i, st in enumerate(stages, 1):
            self._add(self._stage(i, st))

    def _stage(self, no: int, st: Stage) -> QWidget:
        """A stage's card: "Stage N", its kind's badge and "N crafts · X mesos" on top, its title, a level-up's reason
        and EXP, then its numbered steps."""
        o = self._owner
        t, rtl = o.t, o.t.rtl
        card = QFrame(objectName="Stage")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 6)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_label(bidi.plain(t("cp_stage_no", n=no), rtl), "StageNo"))
        top.addWidget(_label(bidi.plain(t(BADGE_KEYS[st.kind]), rtl), f"Kind{st.kind.title()}"))
        top.addStretch(1)
        fee = t("cp_free") if st.fee == 0 else t("cp_mesos", n=bidi.ltr_block(_mesos(st.fee), rtl))
        top.addWidget(_label(bidi.plain(f"{t('cp_crafts', n=st.crafts)} · {fee}", rtl), "StageMeta"))
        v.addLayout(top)
        v.addWidget(_label(self._stage_title(st), "StageTitle", wrap=True))
        for line in self._stage_lines(st):
            v.addWidget(_label(bidi.plain(line, rtl), "Muted", wrap=True))
        for i, s in enumerate(st.steps, 1):
            v.addWidget(self._step(i, s))
        return card

    def _stage_title(self, st: Stage) -> str:
        t, rtl = self._owner.t, self._owner.t.rtl
        if st.kind == "level":
            # the discipline and its levels one Latin block: as two islands a Hebrew line swapped their ends
            return bidi.plain(
                t("cp_stage_level", lv=bidi.ltr_block(f"{st.discipline} {st.level_from} → {st.level_to}", rtl)), rtl
            )
        if st.kind == "prep":
            return bidi.plain(t("cp_stage_prep"), rtl)
        finals = [s for s in st.steps if s.final]
        if len(finals) == 1:
            return bidi.plain(t("cp_stage_final_name", name=bidi.ltr_block(finals[0].name, rtl)), rtl)
        return bidi.plain(t("cp_stage_final", n=len(finals)), rtl)

    def _stage_lines(self, st: Stage) -> list[str]:
        """The muted lines under a stage's title: a level-up's reason (or what blocks it) and its recipe's EXP; the
        other stages' professions."""
        t, rtl = self._owner.t, self._owner.t.rtl
        if st.kind != "level":
            return [_pieces(st.disciplines, rtl, " · ")] if st.disciplines else []
        if st.blocked.startswith("char:"):
            return [t("cp_blocked_char", b=bidi.ltr_block(st.blocked.split(":", 1)[1], rtl))]
        if st.blocked == "exp":
            return [t("cp_blocked_exp")]
        lines = [f"{t('cp_needed_next')} · {t('cp_level_fees', n=bidi.ltr_block(_mesos(st.fee), rtl))}"]
        lift = next((s for s in st.steps if s.level_up), None)
        if lift is not None and st.exp_each:
            lines.append(f"{bidi.ltr_block(lift.name, rtl)} · {t('cp_exp_step', exp=st.exp_each)}")
        return lines

    def _step(self, i: int, s: Step) -> QWidget:
        """One craft: its number, the tick box (ticking it moves its mats and products into the bag), the product's
        picture, "13× Bronze Ingot" (what one craft makes muted beside it), the ingredients under it, the fee on the
        end and Swap when the product has more than one recipe."""
        o = self._owner
        t, rtl = o.t, o.t.rtl
        row = QFrame(objectName="Step")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 8, 0, 8)
        h.setSpacing(8)
        h.addWidget(_label(bidi.plain(f"{i}.", rtl), "StepNo"), 0, Qt.AlignTop)
        box = QCheckBox()
        box.setCursor(Qt.PointingHandCursor)
        box.setFocusPolicy(Qt.NoFocus)
        box.setToolTip(t("cp_step_tip"))
        box.setAccessibleName(t("cp_step_tip"))
        box.toggled.connect(lambda on, s=s: o._ticked(s, on))
        h.addWidget(box, 0, Qt.AlignTop)
        h.addWidget(o._pics.label(s.key or o._key_of(s.name), STEP_PIC), 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        # "13× Bronze Ingot (makes 10)": one label, the aside muted in it — two labels side by side wrapped the name
        # early ("104× Processed" / "Parchment": a wrapping label's own width hint is short of its text)
        name = escape(bidi.ltr_name(f"{s.crafts}× {s.name}", rtl))
        if s.makes > 1:
            aside = escape(bidi.plain(f"({t('cp_makes', n=s.makes)})", rtl))
            name += f' <span style="color:{MUTED}; font-size:12px; font-weight:400">{aside}</span>'
        title = _label(name, "StepName", wrap=True)
        title.setTextFormat(Qt.RichText)
        mid.addWidget(title)
        line = _pieces([f"{s.discipline} {s.prof_lv}"], rtl)
        if s.ingredients:
            line += " · " + _pieces((f"{ing.qty * s.crafts} {ing.name}" for ing in s.ingredients), rtl)
        mid.addWidget(_label(bidi.plain(line, rtl), "Muted", wrap=True))
        if s.optional:
            opts = _pieces((f"{ing.qty} {ing.name}" for ing in s.optional), rtl)
            mid.addWidget(_label(bidi.plain(f"{t('cp_optional')}: {opts}", rtl), "Note", wrap=True))
        h.addLayout(mid, 1)
        end = QVBoxLayout()
        end.setSpacing(4)
        fee = t("cp_free") if s.fee_total == 0 else bidi.ltr_block(_mesos(s.fee_total), rtl)
        end.addWidget(_label(bidi.plain(fee, rtl), "Fee", align=Qt.AlignRight | Qt.AlignTop), 0, Qt.AlignRight)
        if len(craftplan.recipes(o._kb).get(s.name.casefold(), ())) > 1:
            swap = QPushButton(bidi.plain(t("cp_swap"), rtl), objectName="Swap")
            swap.setCursor(Qt.PointingHandCursor)
            swap.setFocusPolicy(Qt.NoFocus)
            swap.setToolTip(t("cp_swap_tip"))
            swap.clicked.connect(lambda _=False, s=s: o._swap(s))
            end.addWidget(swap, 0, Qt.AlignRight)
        end.addStretch(1)
        h.addLayout(end)
        return row

    def _notes(self, plan: Plan) -> None:
        """The muted footer: the raw mats no NPC sells (counted at 0 mesos), recipe loops, how level-ups are picked."""
        t, rtl = self._owner.t, self._owner.t.rtl
        unpriced = [arg for code, arg in plan.notes if code == "no_price"]
        if unpriced:
            self._text(t("cp_note_unpriced", list=_pieces(unpriced, rtl)), "Note")
        for code, arg in plan.notes:
            if code == "cycle":
                self._text(t("cp_note_cycle", name=bidi.ltr_block(arg, rtl)), "Note")
        if any(st.kind == "level" and st.steps for st in plan.stages):
            self._text(t("cp_note_levelups"), "Note")

    # ------------------------------------------------------------ the panel

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._owner._opacity)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._owner._retext_plan_btn()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._owner._retext_plan_btn()

    def _saved_size(self) -> QSize | None:
        """The size the player dragged the window to (craft_plan_size), kept inside the screens; None: never."""
        saved = self._owner._settings[PLAN_SIZE_KEY]
        if not (isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int)):
            return None
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        return mapview.clamp_size(saved["w"], saved["h"], screens, QSize(PLAN_MIN_W, PLAN_MIN_H))

    def _fit(self) -> None:
        """The player's size when they resized it (the body scrolls inside); else PLAN_W wide and as tall as the plan
        up to PLAN_H."""
        size = self._saved_size()
        if size is None:
            inner = PLAN_W - 2 * SIDE
            head = self._need(self._head, inner)
            body = self._need(self._rows_lay, inner - SCROLLBAR_W - GUTTER)
            size = QSize(PLAN_W, max(PLAN_MIN_H, min(PLAN_H, TOP + BOTTOM + 8 + head + body)))
        self._fit_size = size
        if self.size() != size:
            self.resize(size)

    @staticmethod
    def _need(item, width: int) -> int:
        """The height a widget or layout needs at this width: the wrapped way when it has one, else its hint."""
        h = item.heightForWidth(width)
        return h if h >= 0 else item.sizeHint().height()

    def resizeEvent(self, e) -> None:
        """The player dragging an edge: kept once it settles (_resized). The window's own sizing (_fit), and the
        resize Qt sends when it first shows, are not theirs."""
        if self.isVisible() and e.size() != self._fit_size:
            self._sized.start()
        super().resizeEvent(e)

    def _resized(self) -> None:
        """A drag settled: its size kept, and docked beside the main window again (a left-edge drag pulled its right
        side off it)."""
        self._owner._settings[PLAN_SIZE_KEY] = {"w": self.width(), "h": self.height()}
        self._fit_size = self.size()
        if self.isVisible():
            self.dock()
