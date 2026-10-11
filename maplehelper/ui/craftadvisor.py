"""The crafting advisor's window over the game (the toolbar's Crafting button opens it): pick what to craft
(every recipe product, the class's equips first), say what is in the bag and where each profession stands
(level and Mastery bar %), and the forge plan follows — what to farm first and where it drops, then the
crafts in order: materials, the profession level-ups the plan itself needs (the cheapest recipe per EXP,
the player's own measured EXP per level winning over the KB's table), the final items — every number from
the KB's tables or the player's own input (craftplan.py). Ticking a step as crafted moves its mats and
products into the bag, like the site this is modelled on (maplestorycrafting.com/planner.html).

Built like the bar's other windows: the NPCs window's dark glass at the opacity the player picked, the
title row that drags it, the same rows and steppers, its place kept in craft_advisor_geom. It takes the
keyboard while its search and number boxes are used (Esc or the ✕ gives it back), shows while
settings["craft_advisor"] is on, and its ✕ turns that off."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import bidi, craftplan
from ..craftplan import CATEGORY_ORDER, DEFAULT_MODE, DISCIPLINE_KEY, FarmItem, MODES, Plan, Row, Stage, Step
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles, Settings
from . import mapview, theme
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
    _Header,
    _paint_glass,
    accent_of,
    set_capturable,
)

MIN_W, MIN_H = 360, 420  # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 420, 760  # what opens under the toolbar before it is ever resized
GAP = 8  # between the toolbar and the window that opens under it
SHOW_KEY, GEOM_KEY = "craft_advisor", "craft_advisor_geom"
TYPE_DEBOUNCE_MS = 150  # a pause in the search before its rows are read again
EDIT_DEBOUNCE_MS = 350  # a pause in a number box before the character is saved and re-planned
ITEM_ROWS = 30  # item rows listed before the "and N more" line
MAX_MAKE = 50  # the Make steppers' ceiling
# the sections, top to bottom; the ones after "items" are rebuilt together on every plan change
SECTIONS = ("items", "bag", "mode", "plan", "exp")
CAT_KEYS = {
    "ore": "cp_cat_ore",
    "gem": "cp_cat_gem",
    "material": "cp_cat_material",
    "drop": "cp_cat_drop",
    "other": "cp_cat_other",
}


def qss(settings) -> str:
    """The window's own style at the accent the settings pick: the mode button and the ticked check boxes
    light in it (npcoverlay.ACCENTS; the glass itself is painted in the tint by _paint_glass). Rebuilt
    whenever the accent is picked, so the window follows with no more code of its own."""
    fill, on_fill = accent_of(settings)
    return (
        _GLASS_QSS
        + f"""
#Stepper {{ background: {FILL1}; border: none; border-radius: 8px; }}
QToolButton#StepBtn {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px;
                       padding: 2px 8px; font-weight: 600; }}
QToolButton#StepBtn:hover {{ background: {FILL2}; }}
QToolButton#StepBtn:disabled {{ color: {MUTED}; }}
#StepNum {{ color: {TEXT}; font-weight: 600; min-width: 22px; }}
QLineEdit#Num {{ padding: 3px 6px; }}
QPushButton#Mode {{ background: {FILL1}; color: {MUTED}; border: none; border-radius: 8px;
                   padding: 4px 10px; text-align: center; }}
QPushButton#Mode:hover {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Mode:checked {{ background: {fill}; color: {on_fill}; font-weight: 600; }}
QPushButton#Swap {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
                   padding: 2px 8px; font-size: 11px; }}
QPushButton#Swap:hover {{ background: {FILL2}; color: {TEXT}; }}
#Head {{ background: {FILL1}; border: none; border-radius: 8px; }}
#HeadLabel {{ background: transparent; color: {TEXT}; font-weight: 600; }}
#Badge {{ background: transparent; color: {MUTED}; font-size: 11px; }}
#StatName {{ background: transparent; color: {MUTED}; font-size: 11px; font-weight: 600; }}
#Big {{ background: transparent; color: {TEXT}; font-size: 16px; font-weight: 700; }}
#Note {{ background: transparent; color: {MUTED}; font-size: 11px; }}
#Warn {{ background: transparent; color: {fill}; font-size: 11px; font-weight: 600; }}
QCheckBox {{ background: transparent; color: {TEXT}; spacing: 6px; }}
QCheckBox::indicator {{ background: {FILL1}; border: 1px solid rgba(255,255,255,0.25); border-radius: 5px;
                        width: 14px; height: 14px; }}
QCheckBox::indicator:checked {{ background: {fill}; border-color: {fill}; }}
"""
    )


log = logging.getLogger("maplehelper")


def _match(name: str, query: str) -> bool:
    """A row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in query.lower().split() if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


def _mesos(v: int) -> str:
    return f"{int(v):,}"


class _Stepper(QFrame):
    """A small − n + counter in the overlay's own look (the scroll planner's): a spin box's arrows don't
    survive the dark glass, and the counts are usually small clicks."""

    changed = Signal(int)

    def __init__(self, value: int = 0, hi: int = MAX_MAKE):
        super().__init__(objectName="Stepper")
        self._hi = hi
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


class _SectionHead(QFrame):
    """A section's title row: its name, a muted badge on the end, clicking the row folds the section."""

    fold_clicked = Signal()

    def __init__(self, label: str):
        super().__init__(objectName="Head")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(6)
        row.addWidget(QLabel(label, objectName="HeadLabel"))
        self.badge = QLabel(objectName="Badge")
        row.addWidget(self.badge, 1)
        self.mark = QLabel(objectName="Badge")
        row.addWidget(self.mark)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.fold_clicked.emit()
        super().mouseReleaseEvent(e)


class CraftAdvisor(EdgeResize, QWidget):
    """The window itself: the character's crafting strip on top (level, each profession's level and Mastery
    bar %), then the sections — what to craft (search finds every recipe), the bag, the recipe mode, the
    forge plan, the EXP table — every one foldable, the whole body scrolling under ~420 px."""

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
        self._c: Character | None = None
        self._rows: tuple[Row, ...] = ()
        self._plan: Plan | None = None
        self._mode = DEFAULT_MODE
        self._choices: dict[str, int] = {}  # product name casefold -> the recipe number the player swapped to
        self._folded: set[str] = set()
        self._section_widgets: dict[str, list[QWidget]] = {}
        self._badges: dict[str, QLabel] = {}
        self._bag_boxes: dict[str, _SearchBox] = {}
        self._exp_boxes: dict[int, _SearchBox] = {}
        self._opacity = OPACITY_DEFAULT
        self._placed = False  # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None  # the toolbar's rect, where the window first opens
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_items)
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
        self._char = QLabel(objectName="Sub", wordWrap=True)
        col.addWidget(self._char)
        self._strip = QWidget()
        grid = QGridLayout(self._strip)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self._char_word = QLabel(objectName="StatName")
        self._lv_box = _SearchBox(objectName="Num", maxLength=3)
        self._lv_box.setFixedWidth(44)
        self._lv_box.setAlignment(Qt.AlignCenter)
        self._lv_box.textChanged.connect(lambda _t: self._edit.start())
        grid.addWidget(self._char_word, 0, 0, Qt.AlignVCenter)
        grid.addWidget(self._lv_box, 0, 1, Qt.AlignVCenter)
        self._prof_boxes: dict[str, tuple[_SearchBox, _SearchBox]] = {}
        # two professions a row: their name (the game's own word), the level box, a % mark, the bar box
        for i, disc in enumerate(sorted(DISCIPLINE_KEY, key=lambda d: DISCIPLINE_KEY[d])):
            lv = _SearchBox(objectName="Num", maxLength=2)
            lv.setFixedWidth(28)
            lv.setAlignment(Qt.AlignCenter)
            pct = _SearchBox(objectName="Num", maxLength=3)
            pct.setFixedWidth(30)
            pct.setAlignment(Qt.AlignCenter)
            for box in (lv, pct):
                box.textChanged.connect(lambda _t: self._edit.start())
            pair = QWidget()
            prow = QHBoxLayout(pair)
            prow.setContentsMargins(0, 0, 0, 0)
            prow.setSpacing(3)
            prow.addWidget(QLabel(disc, objectName="StatName"), 1)
            prow.addWidget(lv)
            prow.addWidget(QLabel("%", objectName="StatName"))
            prow.addWidget(pct)
            grid.addWidget(pair, 1 + i // 2, i % 2)
            self._prof_boxes[disc] = (lv, pct)
        col.addWidget(self._strip)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setSpacing(4)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(rows)
        self._clear_of_scrollbar()
        col.addWidget(self._scroll, 1)

    def _clear_of_scrollbar(self) -> None:
        """The body's rows keep the scrollbar's width clear on its side (right in a left-to-right layout,
        left in Hebrew, where Qt moves it) — a row's last word was clipping under it."""
        rtl = self.t.rtl if hasattr(self, "t") else False
        self._rows_lay.setContentsMargins(SCROLLBAR_W if rtl else 0, 0, 0 if rtl else SCROLLBAR_W, 0)

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ the contract (app.py wires exactly this)

    def refresh(self) -> None:
        """Shown while settings["craft_advisor"] is on; the character is re-read every time it shows."""
        if bool(self._settings[SHOW_KEY]):
            self._restyle()
            try:
                self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
            except (TypeError, ValueError):
                self._opacity = OPACITY_DEFAULT
            self._rebuild()
            if not self.isVisible():
                self._place()
                self.show()
                set_capturable(self, self._settings)
            self.raise_()
            self.update()  # the glass repaints in the tint the settings pick (npcoverlay.apply_look)
        elif self.isVisible():
            self.hide()

    def _restyle(self) -> None:
        """The accent the settings pick, in the window's own style sheet (its glass is painted in the tint
        by _paint_glass); skipped when it is the same — a restyle re-polishes every row."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._clear_of_scrollbar()
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._choices = {}
        self._rebuild()

    def character_changed(self) -> None:
        self._choices = {}
        self._rebuild()

    # ------------------------------------------------------------ text (all of it, for apply_language: a rebuild)

    def _rebuild(self) -> None:
        """Every section again, from the active character: the strip, the items, the bag, the mode, the
        plan, the EXP table. All of it — apply_language is just this."""
        t, rtl = self.t, self.t.rtl
        self._c = self._profiles.active
        self._title.setText(bidi.plain(t("cp_title"), rtl))
        self._close.setAccessibleName(t("cp_close"))
        self._close.setToolTip(t("cp_close"))
        bar, was = self._scroll.verticalScrollBar(), self._scroll.verticalScrollBar().value()
        self._clear_rows()
        self._bag_boxes = {}
        self._exp_boxes = {}
        if self._c is None:
            self._char.setText(bidi.plain(t("cp_no_character"), rtl))
            self._strip.setVisible(False)
            return
        c = self._c
        self._strip.setVisible(True)
        self._char.setText(bidi.plain(f"{bidi.ltr_name(f'{c.name} · {c.job}', rtl)} · {t('lv_short', n=c.level)}", rtl))
        self._retext_strip()
        self._rows = craftplan.craftable(self._kb, c)
        self._add_items()
        self._add_rest()
        bar.setValue(was)

    def _retext_strip(self) -> None:
        """The strip's boxes filled from the character (signals off: a re-read is not a player's edit)."""
        t = self.t
        self._char_word.setText(bidi.plain(t("cp_char"), t.rtl))
        self._lv_box.blockSignals(True)
        self._lv_box.setText(str(self._c.level) if self._c.level else "")
        self._lv_box.blockSignals(False)
        self._lv_box.setAccessibleName(t("cp_char"))
        self._lv_box.setToolTip(t("cp_char_tip"))
        for disc, (lv, pct) in self._prof_boxes.items():
            key = DISCIPLINE_KEY[disc]
            word = bidi.ltr_name(disc, t.rtl)
            lv.blockSignals(True)
            lv.setText(str(self._c.crafts[key]) if self._c.crafts.get(key) else "")
            lv.blockSignals(False)
            lv.setAccessibleName(t("cp_prof_tip", disc=word))
            lv.setToolTip(t("cp_prof_tip", disc=word))
            pct.blockSignals(True)
            pct.setText(str(self._c.craft_pct[key]) if self._c.craft_pct.get(key) else "")
            pct.blockSignals(False)
            pct.setAccessibleName(t("cp_bar"))
            pct.setToolTip(t("cp_bar_tip"))

    def _clear_rows(self) -> None:
        self._section_widgets = {}
        self._badges = {}
        while self._rows_lay.count() > 1:
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _clear_from(self, first: str) -> None:
        """Drop the sections from `first` on (their widgets go, their fold choice stays)."""
        started = False
        for key in SECTIONS:
            if key == first:
                started = True
            if not started:
                continue
            for w in self._section_widgets.pop(key, ()):
                self._rows_lay.removeWidget(w)
                w.hide()
                w.deleteLater()
            self._badges.pop(key, None)

    def _section(self, key: str, label: str, badge: str = "") -> QVBoxLayout:
        """One foldable section: its head row and the body layout the caller fills. Folded once, it stays
        folded across rebuilds (the player's own choice)."""
        t = self.t
        folded = key in self._folded
        head = _SectionHead(bidi.plain(label, t.rtl))
        head.badge.setText(bidi.plain(badge, t.rtl) if badge else "")
        head.mark.setText("–" if folded else "+")
        head.fold_clicked.connect(lambda k=key: self._fold(k))
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        for w in (head, body):
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, w)
        self._section_widgets[key] = [head, body]
        self._badges[key] = head.badge
        body.setVisible(not folded)
        return lay

    def _fold(self, key: str) -> None:
        self._folded ^= {key}
        folded = key in self._folded
        widgets = self._section_widgets.get(key, [])
        if len(widgets) > 1:
            widgets[1].setVisible(not folded)
            widgets[0].mark.setText("–" if folded else "+")

    # ------------------------------------------------------------ the sections

    def _add_items(self) -> None:
        """What to craft: the search box and the item rows (the default table, or the search's every
        product). The rows re-fill on typing without a rebuild, so the keyboard stays where it is."""
        t, rtl = self.t, self.t.rtl
        lay = self._section(
            "items", t("cp_items"), t("cp_targets", n=len(self._c.craft_targets)) if self._c.craft_targets else ""
        )
        lay.addWidget(QLabel(bidi.plain(t("cp_items_hint"), rtl), objectName="Note", wordWrap=True))
        self._search = _SearchBox()
        self._search.setClearButtonEnabled(True)
        self._search.setPlaceholderText(bidi.plain(t("cp_search_ph"), rtl))
        self._search.textChanged.connect(lambda _t: self._typing.start())
        lay.addWidget(self._search)
        self._item_rows = QVBoxLayout()
        self._item_rows.setSpacing(4)
        lay.addLayout(self._item_rows)
        self._item_more = QLabel(objectName="Note", wordWrap=True)
        lay.addWidget(self._item_more)
        self._fill_items()

    def _fill_items(self) -> None:
        """The item rows for what is typed (nothing typed: the class's equips), each with its Make stepper
        wired to the character's craft_targets list."""
        t, rtl = self.t, self.t.rtl
        c = self._c
        if c is None:
            return
        query = self._search.text() if hasattr(self, "_search") else ""
        rows = [r for r in self._rows if _match(r.name, query)] if query else [r for r in self._rows if r.equip]
        while self._item_rows.count():
            item = self._item_rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for r in rows[:ITEM_ROWS]:
            self._item_rows.addWidget(self._item_row(r))
        left = len(rows) - min(len(rows), ITEM_ROWS)
        self._item_more.setText(bidi.plain(t("cp_more", n=left), rtl) if left else "")

    def _item_row(self, r: Row) -> QWidget:
        """One item of the table: its name over the level, the class's stat, the recipe and the costs; the
        Make stepper on the end."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="Row")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel(bidi.ltr_name(r.name, rtl), objectName="Name", wordWrap=True), 1)
        step = _Stepper(self._c.craft_targets.count(r.key))
        step.setToolTip(t("cp_make_tip"))
        step.setAccessibleName(t("cp_make_tip"))
        step.changed.connect(lambda v, k=r.key: self._make_set(k, v))
        top.addWidget(step, 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = []
        if isinstance(r.lv, int):
            bits.append(t("lv_short", n=r.lv))
        if r.stat_word and isinstance(r.stat, int):
            bits.append(bidi.ltr_block(f"{r.stat_word} {r.stat}", rtl))
        bits.append(bidi.ltr_block(f"{r.discipline} {r.prof_lv}", rtl))
        bits.append(bidi.ltr_block(_mesos(r.cost), rtl))
        if r.save is not None:
            bits.append(bidi.plain(t("cp_saves", n=_mesos(r.save)), rtl))
        elif r.buy is None:
            bits.append(bidi.plain(t("cp_not_sold"), rtl))
        lay.addWidget(QLabel(bidi.plain(" · ".join(bits), rtl), objectName="Sub", wordWrap=True))
        return row

    def _make_set(self, key: str, v: int) -> None:
        """The Make stepper: craft_targets holds a key once per make (duplicates are the count)."""
        c = self._profiles.active
        if c is None:
            return
        have = c.craft_targets.count(key)
        if v > have:
            c.craft_targets.extend([key] * (v - have))
        else:
            for _ in range(have - v):
                c.craft_targets.remove(key)
        self._profiles.save()
        self._add_rest()
        t, rtl = self.t, self.t.rtl
        if self._badges.get("items") is not None:
            self._badges["items"].setText(bidi.plain(t("cp_targets", n=len(c.craft_targets)), rtl) if c.craft_targets else "")

    def _add_rest(self) -> None:
        """The sections after the items — the bag, the mode, the plan, the EXP table — rebuilt together:
        they all follow the plan, and the items (whose search box holds the keyboard) stay put."""
        self._clear_from("bag")
        self._bag_boxes = {}
        self._exp_boxes = {}
        if self._c is None:
            return
        self._plan = None
        if self._c.craft_targets:
            try:
                self._plan = craftplan.plan(
                    self._kb,
                    self._c,
                    list(self._c.craft_targets),
                    dict(self._c.craft_bag),
                    self._mode,
                    self._settings["craft_exp_table"],
                    self._choices,
                )
            except Exception:  # a plan must never take the window down
                log.warning("craft plan failed", exc_info=True)
                self._plan = None
        self._add_bag()
        self._add_mode()
        self._add_plan()
        self._add_exp()

    def _add_bag(self) -> None:
        """The bag: what the plan uses plus what is carried, under the KB's own group words, a count box
        each. Only the plan's items and the carried ones — a whole item list would drown the plan."""
        t, rtl = self.t, self.t.rtl
        c = self._c
        lay = self._section("bag", t("cp_bag"))
        lay.addWidget(QLabel(bidi.plain(t("cp_bag_hint"), rtl), objectName="Note", wordWrap=True))
        entries: dict[str, tuple[str, str, bool]] = {}  # name casefold -> (name, KB key, in the plan)
        if self._plan is not None:
            for f in self._plan.farm:
                entries.setdefault(f.name.casefold(), (f.name, f.key, True))
            for st in self._plan.stages:
                for s in st.steps:
                    entries.setdefault(s.name.casefold(), (s.name, s.key, True))
        for k in c.craft_bag:
            name = str(self._kb.entities.get(k, {}).get("name") or "")
            if name:
                entries.setdefault(name.casefold(), (name, k, False))
        if not entries:
            return
        cats = craftplan.categories(self._kb)
        groups: dict[str, list[tuple[str, str, bool]]] = {}
        for name, key, in_plan in entries.values():
            groups.setdefault(cats.get(name.casefold(), "other"), []).append((name, key, in_plan))
        for cat in sorted(groups, key=lambda g: CATEGORY_ORDER.get(g, 9)):
            lay.addWidget(QLabel(bidi.plain(t(CAT_KEYS.get(cat, "cp_cat_other")), rtl), objectName="Note"))
            for name, key, in_plan in sorted(groups[cat], key=lambda g: g[0].casefold()):
                lay.addWidget(self._bag_row(name, key, in_plan))

    def _bag_row(self, name: str, key: str, in_plan: bool) -> QWidget:
        """One bag item: its name, a count box, and the "in plan" badge when the plan uses it."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="Row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(6)
        lay.addWidget(QLabel(bidi.ltr_name(name, rtl), objectName="Name"), 1)
        if in_plan:
            lay.addWidget(QLabel(bidi.plain(t("cp_in_plan"), rtl), objectName="Note"))
        box = _SearchBox(objectName="Num", maxLength=6)
        box.setFixedWidth(64)
        box.setAlignment(Qt.AlignCenter)
        have = int(self._c.craft_bag.get(key, 0) or 0)
        box.setText(str(have) if have else "")
        box.setToolTip(t("cp_bag_tip"))
        box.setAccessibleName(t("cp_bag_tip"))
        box.textChanged.connect(lambda _t: self._edit.start())
        self._bag_boxes[key] = box
        lay.addWidget(box)
        return row

    def _add_mode(self) -> None:
        """The three recipe modes; the plan follows at once."""
        t, rtl = self.t, self.t.rtl
        lay = self._section("mode", t("cp_mode"))
        row = QWidget()
        rlay = QHBoxLayout(row)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(4)
        words = {"highest": "cp_mode_highest", "cheapest": "cp_mode_cheapest", "least": "cp_mode_least"}
        for mode in MODES:
            b = QPushButton(bidi.plain(t(words[mode]), rtl), objectName="Mode")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCheckable(True)
            b.setChecked(mode == self._mode)
            b.setToolTip(t("cp_mode_tip"))
            b.clicked.connect(lambda _=False, m=mode: self._mode_set(m))
            rlay.addWidget(b, 1)
        lay.addWidget(row)

    def _mode_set(self, mode: str) -> None:
        self._mode = mode
        self._add_rest()

    # ------------------------------------------------------------ the plan

    def _add_plan(self) -> None:
        t, rtl = self.t, self.t.rtl
        lay = self._section("plan", t("cp_plan"))
        plan = self._plan
        if plan is None or not plan.stages:
            lay.addWidget(QLabel(bidi.plain(t("cp_plan_empty"), rtl), objectName="Note", wordWrap=True))
            return
        # the totals: cost, craft actions, equip level
        totals = QWidget()
        tlay = QGridLayout(totals)
        tlay.setContentsMargins(10, 8, 10, 8)
        tlay.setVerticalSpacing(0)
        tlay.setHorizontalSpacing(8)
        for i, (word, val) in enumerate(
            (
                (t("cp_total_cost"), _mesos(plan.cost)),
                (t("cp_total_crafts"), f"{plan.crafts:,}"),
                (t("cp_equip_lv"), t("lv_short", n=plan.equip_lv) if plan.equip_lv else "—"),
            )
        ):
            tlay.addWidget(QLabel(bidi.plain(word, rtl), objectName="StatName"), 0, i * 2)
            tlay.addWidget(QLabel(bidi.ltr_block(val, rtl), objectName="Big"), 0, i * 2 + 1)
        lay.addWidget(totals)
        # 1 · farm these first
        farm = [f for f in plan.farm if f.farm > 0]
        if farm:
            lay.addWidget(QLabel(bidi.plain(t("cp_farm"), rtl), objectName="HeadLabel", wordWrap=True))
            lay.addWidget(QLabel(bidi.plain(t("cp_farm_hint"), rtl), objectName="Note", wordWrap=True))
            for f in farm:
                lay.addWidget(self._farm_row(f))
            if all(f.price is None for f in farm):
                lay.addWidget(QLabel(bidi.plain(t("cp_no_price"), rtl), objectName="Note", wordWrap=True))
            if plan.bag_used:
                pairs = " · ".join(bidi.ltr_name(f"{n} ×{q}", rtl) for n, q in plan.bag_used)
                lay.addWidget(QLabel(bidi.plain(t("cp_from_bag", list=pairs), rtl), objectName="Sub", wordWrap=True))
        # 2 · then craft in this order
        lay.addWidget(QLabel(bidi.plain(t("cp_order"), rtl), objectName="HeadLabel", wordWrap=True))
        for st in plan.stages:
            for w in self._stage(st):
                lay.addWidget(w)
        if plan.leftovers:
            pairs = " · ".join(bidi.ltr_name(f"{n} ×{q}", rtl) for n, q in plan.leftovers)
            lay.addWidget(QLabel(bidi.plain(t("cp_leftover", list=pairs), rtl), objectName="Sub", wordWrap=True))
        for code, arg in plan.notes:
            if code == "cycle":
                lay.addWidget(
                    QLabel(bidi.plain(t("cp_note_cycle", name=bidi.ltr_name(arg, rtl)), rtl), objectName="Note", wordWrap=True)
                )

    def _farm_row(self, f: FarmItem) -> QWidget:
        """One raw mat to farm: its name, its group word, how many are still missing (the total beside it
        when the bag covered some), and where it comes from — droppers, or the NPC selling it."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="Row")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel(bidi.ltr_name(f.name, rtl), objectName="Name"), 1)
        top.addWidget(QLabel(bidi.plain(t(CAT_KEYS.get(f.category, "cp_cat_other")), rtl), objectName="Note"))
        count = bidi.ltr_block(_mesos(f.farm), rtl)
        if f.bag > 0:
            count += " " + bidi.plain(f"({t('cp_farm_total', n=bidi.ltr_block(_mesos(f.need), rtl))})", rtl)
        top.addWidget(QLabel(count, objectName="Big"))
        lay.addLayout(top)
        bits = []
        if f.droppers:
            drops = " · ".join(f"{m} ({t('lv_short', n=lv)})" for m, lv in f.droppers)
            bits.append(bidi.plain(t("cp_drops_from", list=bidi.ltr_block(drops, rtl)), rtl))
        if f.sellers:
            npc, place, price = f.sellers[0]
            bits.append(bidi.plain(t("cp_buy_at", who=bidi.ltr_block(f"{npc} · {place} · {_mesos(price)}", rtl)), rtl))
        if f.price is None and not f.droppers:
            bits.append(bidi.plain(t("cp_no_price"), rtl))
        lay.addWidget(QLabel(bidi.plain(" · ".join(bits), rtl), objectName="Sub", wordWrap=True))
        return row

    def _stage(self, st: Stage) -> list[QWidget]:
        """One stage: its title row (what it is, how many crafts, the fees), the level-up's own lines (what
        it needs, what blocks it), then its steps, each with its tick box and its Swap."""
        t, rtl = self.t, self.t.rtl
        out: list[QWidget] = []
        head = QFrame(objectName="Head")
        hlay = QVBoxLayout(head)
        hlay.setContentsMargins(10, 5, 10, 5)
        hlay.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        if st.kind == "level":
            # the discipline and its levels are one Latin block: as two islands a Hebrew line read
            # "להעלות Smithing 2 → 1", the pair of them swapping ends
            lv = bidi.ltr_block(f"{st.discipline} {st.level_from} → {st.level_to}", rtl)
            title = bidi.plain(t("cp_stage_level", lv=lv), rtl)
        elif st.kind == "prep":
            title = bidi.plain(t("cp_stage_prep"), rtl)
        else:
            finals = [s for s in st.steps if s.final]
            title = (
                bidi.plain(t("cp_stage_final_name", name=bidi.ltr_name(finals[0].name, rtl)), rtl)
                if len(finals) == 1
                else bidi.plain(t("cp_stage_final", n=len(finals)), rtl)
            )
        top.addWidget(QLabel(title, objectName="HeadLabel", wordWrap=True), 1)
        craft_word = t("cp_crafts", n=st.crafts)
        fee_word = bidi.plain(t("cp_free"), rtl) if st.fee == 0 else bidi.ltr_block(_mesos(st.fee), rtl)
        top.addWidget(QLabel(bidi.plain(f"{craft_word} · {fee_word}", rtl), objectName="Badge", wordWrap=True))
        hlay.addLayout(top)
        hlay.addWidget(QLabel(bidi.plain(" · ".join(st.disciplines), rtl), objectName="Badge"))
        out.append(head)
        if st.kind == "level":
            lines = []
            if st.blocked.startswith("char:"):
                lines.append(
                    bidi.plain(
                        t(
                            "cp_blocked_char",
                            b=bidi.ltr_block(st.blocked.split(":", 1)[1], rtl),
                            a=st.level_from,
                        ),
                        rtl,
                    )
                )
            elif st.blocked == "exp":
                lines.append(bidi.plain(t("cp_blocked_exp"), rtl))
            else:
                lines.append(bidi.plain(t("cp_needed_next"), rtl))
                lines.append(bidi.plain(t("cp_level_fees", n=bidi.ltr_block(_mesos(st.fee), rtl)), rtl))
                if st.exp_each:
                    # the discipline named, as in the title: a bare "1 → 2" read backwards in a Hebrew line
                    lv = bidi.ltr_block(f"{st.discipline} {st.level_from} → {st.level_to}", rtl)
                    lines.append(bidi.plain(t("cp_exp_each", exp=st.exp_each, lv=lv), rtl))
            out.append(QLabel(bidi.plain(" · ".join(lines), rtl), objectName="Sub", wordWrap=True))
        for s in st.steps:
            out.append(self._step_row(s))
        return out

    def _step_row(self, s: Step) -> QWidget:
        """One craft: a tick box (ticking it moves its mats and products into the bag), the craft line
        (how many, what, which discipline and level, the fee), its ingredient totals under it, and a Swap
        button when the product has more than one recipe."""
        t, rtl = self.t, self.t.rtl
        row = QFrame(objectName="Row")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        box = QCheckBox()
        box.setCursor(Qt.PointingHandCursor)
        box.setToolTip(t("cp_step_tip"))
        box.setAccessibleName(t("cp_step_tip"))
        box.toggled.connect(lambda on, s=s: self._ticked(s, on))
        top.addWidget(box, 0, Qt.AlignTop)
        top.addWidget(QLabel(bidi.ltr_name(f"{s.crafts}× {s.name}", rtl), objectName="Name", wordWrap=True), 1)
        variants = craftplan.recipes(self._kb).get(s.name.casefold(), ())
        if len(variants) > 1:
            swap = QPushButton(bidi.plain(t("cp_swap"), rtl), objectName="Swap")
            swap.setCursor(Qt.PointingHandCursor)
            swap.setFocusPolicy(Qt.NoFocus)
            swap.setToolTip(t("cp_swap_tip"))
            swap.clicked.connect(lambda _=False, s=s: self._swap(s))
            top.addWidget(swap, 0, Qt.AlignTop)
        fee = bidi.plain(t("cp_free"), rtl) if s.fee_total == 0 else bidi.ltr_block(_mesos(s.fee_total), rtl)
        top.addWidget(QLabel(fee, objectName="Badge"), 0, Qt.AlignTop)
        lay.addLayout(top)
        bits = [bidi.ltr_block(f"{s.discipline} {s.prof_lv}", rtl)]
        ings = ", ".join(f"{i.name} ×{i.qty * s.crafts}" for i in s.ingredients)
        if ings:
            bits.append(bidi.ltr_block(ings, rtl))
        if s.makes > 1:
            bits.append(bidi.plain(t("cp_makes", n=s.makes), rtl))
        if s.level_up and s.exp:
            bits.append(bidi.plain(t("cp_exp_step", exp=s.exp), rtl))
        lay.addWidget(QLabel(bidi.plain(" · ".join(bits), rtl), objectName="Sub", wordWrap=True))
        if s.optional:
            opts = ", ".join(f"{i.name} ×{i.qty}" for i in s.optional)
            lay.addWidget(
                QLabel(
                    bidi.plain(t("cp_optional"), rtl) + " " + bidi.ltr_block(f"({opts})", rtl), objectName="Note", wordWrap=True
                )
            )
        return row

    # ------------------------------------------------------------ the plan's own edits

    def _key_of(self, name: str, fallback: str = "") -> str:
        """An item's KB key: the recipes table's product key, else the KB's own name lookup."""
        for rec in craftplan.recipes(self._kb).get(name.casefold(), ()):
            if rec.key:
                return rec.key
        got = getattr(self._kb, "_item_by_name", {}).get(name.lower(), "")
        return f"item/{got}" if got else fallback

    def _ticked(self, s: Step, on: bool) -> None:
        """A step ticked as crafted: its ingredients leave the bag (what is there of them), its products
        enter it, the plan follows — its shapes shrink around the new bag, like the site's."""
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
        self._add_rest()

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
        self._add_rest()

    # ------------------------------------------------------------ the EXP table

    def _add_exp(self) -> None:
        """One row per crafting level: the EXP to the next (a box — the player's measured value overrides
        the KB's) with the KB / yours mark, saved to settings["craft_exp_table"]."""
        t, rtl = self.t, self.t.rtl
        lay = self._section("exp", t("cp_exp_table"))
        lay.addWidget(QLabel(bidi.plain(t("cp_exp_hint"), rtl), objectName="Note", wordWrap=True))
        for e in craftplan.exp_needs(self._kb, self._settings["craft_exp_table"]):
            if e.level >= craftplan.LEVEL_CAP or e.exp is None:
                continue
            row = QWidget()
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(10, 2, 10, 2)
            rlay.setSpacing(6)
            rlay.addWidget(
                QLabel(bidi.plain(f"{t('lv_short', n=e.level)} → {t('lv_short', n=e.level + 1)}", rtl), objectName="Sub"), 1
            )
            box = _SearchBox(objectName="Num", maxLength=6)
            box.setFixedWidth(70)
            box.setAlignment(Qt.AlignCenter)
            box.setText(str(e.exp))
            box.setToolTip(t("cp_exp_tip"))
            box.setAccessibleName(t("cp_exp_tip"))
            box.textChanged.connect(lambda _t: self._edit.start())
            self._exp_boxes[e.level] = box
            rlay.addWidget(box)
            rlay.addWidget(
                QLabel(
                    bidi.plain(t("cp_exp_yours" if e.measured else "cp_exp_kb"), rtl), objectName="Warn" if e.measured else "Note"
                )
            )
            lay.addWidget(row)

    # ------------------------------------------------------------ the player's own numbers

    def _edits_saved(self) -> None:
        """The pause held: the level box, the profession boxes, the bag counts and the EXP boxes saved (an
        empty box unsets), then the plan re-read."""
        c = self._profiles.active
        if c is None:
            return
        v = self._lv_box.text().strip()
        if v.isdigit() and 0 < int(v) <= 250:
            c.level = int(v)
        for disc, (lv, pct) in self._prof_boxes.items():
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
        for lv, box in self._exp_boxes.items():
            n = box.text().strip()
            if n.isdigit() and int(n) > 0:
                table[str(lv)] = int(n)
            else:
                table.pop(str(lv), None)
        self._settings["craft_exp_table"] = table or None
        self._profiles.save()
        self._add_rest()

    # ------------------------------------------------------------ closing

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's Crafting button follows), hidden until switched back on."""
        self._settings[SHOW_KEY] = False
        self.hide()
        self.closed.emit()

    def hideEvent(self, e) -> None:
        for timer in (self._typing, self._edit):  # nothing fires once hidden: no re-read behind the back
            timer.stop()
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
        """Just under the toolbar (the bar's Crafting button opens this), kept on that screen; without one —
        the bar never placed, or gone — the primary screen's top-left corner."""
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
