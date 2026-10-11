"""The gear upgrade advisor's window over the game (the toolbar's Gear button, ui/gametoolbar.py): what the
active character wears per slot, and what the KB says beats it — wearable now, or a few levels away — with the
stat deltas that rank each row, the NPC price and seller, or the monsters that drop it, and a "Plan scrolls"
hand-off to the scroll planner. Every number comes from the KB's tables via gearadvice.py; nothing is guessed
here. Built like the other windows over the game (the NPCs here window, the searches): the same dark glass at
the opacity the player picked for them (npc_overlay_opacity), the title row that drags it, muted status text
and the same rounded rows. It takes the keyboard while its stat boxes or the worn-item search are typed in
(Esc gives it back), keeps its place in "gear_advisor_geom", and its ✕ turns "gear_advisor" off."""
from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from .. import bidi, gearadvice, itemterms
from ..gearadvice import STAT_KEYS, SlotPlan, Upgrade, delta_text
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Character, Profiles, Settings
from . import mapview, theme
from .gamesearch import _Row, _SearchBox
from .glass import EdgeResize
from .npcoverlay import (BOTTOM, FILL1, FILL2, GEOM_DEBOUNCE_MS, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN,
                         SIDE, TEXT, TOP, _Header, _paint_glass, set_capturable)

MIN_W, MIN_H = 320, 360             # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 380, 560     # what opens under the toolbar before it is ever resized
GAP = 8                             # between the toolbar and the window that opens under it
SHOW_KEY, GEOM_KEY = "gear_advisor", "gear_advisor_geom"
TYPE_DEBOUNCE_MS = 150              # a pause in the picker's typing before its rows are read again
PICK_ROWS = 8                       # rows the picker lists before the "and N more" line
STAT_DEBOUNCE_MS = 350              # a pause in a stat box before the character is saved and the advice re-read
SCROLLBAR_W = 8                     # the body's scrollbar (QSS below)
GENDERS = ("", "Male", "Female")    # what a player can say about their character ("" = not said)

QSS = f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#Char {{ color: {TEXT}; font-weight: 600; }}
#Hint {{ color: {MUTED}; font-size: 11px; }}
#StatName {{ background: transparent; color: {MUTED}; font-size: 11px; font-weight: 600; }}
QLineEdit#Stat {{ padding: 6px 4px; }}
QLineEdit {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 6px 10px; }}
QLineEdit:focus {{ background: {FILL2}; }}
QComboBox {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 6px 10px; }}
QComboBox:hover {{ background: {FILL2}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; border-right: 5px solid transparent;
                         border-top: 6px solid {MUTED}; margin-right: 7px; }}
QComboBox QAbstractItemView {{ background: #242427; color: {TEXT}; selection-background-color: {FILL2};
                              border-radius: 8px; outline: none; }}
QFrame#Row {{ background: {FILL1}; border: none; border-radius: 8px; }}
QFrame#Row:hover {{ background: {FILL2}; }}
QFrame#Head {{ background: {FILL1}; border: none; border-radius: 8px; }}
QFrame#Head:hover {{ background: {FILL2}; }}
#Name {{ background: transparent; color: {TEXT}; font-weight: 600; }}
#Sub {{ background: transparent; color: {MUTED}; }}
#Slot {{ background: transparent; color: {TEXT}; font-weight: 600; }}
#Fold {{ background: transparent; color: {MUTED}; font-size: 11px; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 6px 10px; text-align: left; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton#Worn {{ padding: 4px 10px; font-weight: 600; }}
QPushButton#Plan {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
                   padding: 2px 8px; font-size: 11px; }}
QPushButton#Plan:hover {{ background: {FILL2}; color: {TEXT}; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
QScrollArea {{ background: transparent; border: none; }}
#Rows {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: {SCROLLBAR_W}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


def _sex_word(t: I18n, sex: str) -> str:
    """"Male"/"Female" as the player says it: the game's English word, Hebrew's זכר/נקבה."""
    return t("ga_gender_female") if sex == "Female" else t("ga_gender_male") if sex == "Male" else sex


def _slot_word(t: I18n, slot: str) -> str:
    """A slot's name in the player's language (itemterms' own translations: Claw -> טופר)."""
    return itemterms.kind(slot, t.lang)

def _match(name: str, query: str) -> bool:
    """A picker row for this query: every word of it in the item's name, case aside (the searches' own rule)."""
    words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
    n = str(name).lower()
    return not words or all(w in n for w in words)


class _UpRow(QFrame):
    """One advised item: its name with a Plan scrolls button beside it, under it the level and the deltas that
    rank it (plus what to flag: a stat requirement not checked, a gendered item), then where to get it — the
    NPC's price, or the droppers the drops table names, or the KB has neither."""

    def __init__(self, t: I18n, u: Upgrade, on_plan):
        super().__init__(objectName="Row")
        rtl = t.rtl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(1)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        name = QLabel(bidi.ltr_name(str(u.row.get("item") or ""), rtl), objectName="Name", wordWrap=True)
        top.addWidget(name, 1)
        if isinstance(u.row.get("slots"), int) and u.row["slots"] > 0:
            plan = QPushButton(bidi.plain(t("ga_plan_scrolls"), rtl), objectName="Plan")
            plan.setCursor(Qt.PointingHandCursor)
            plan.setFocusPolicy(Qt.NoFocus)
            plan.setToolTip(t("ga_plan_tip"))
            plan.setAccessibleName(t("ga_plan_tip"))
            plan.clicked.connect(lambda _=False, k=str(u.row.get("key")): on_plan(k))
            top.addWidget(plan, 0, Qt.AlignTop)
        lay.addLayout(top)
        req = u.row.get("req_lv")
        lv = t("lv_short", n=req)                                      # the app's own "Lv. 30" / "רמה 30"
        bits = [t("ga_soon_at", n=req) if u.soon else lv]
        if u.deltas:
            bits.append(bidi.ltr_block(delta_text(u.deltas), rtl))
        for flag in u.flags:
            if flag == "gender":
                bits.append(bidi.plain(t("ga_gender_only", g=_sex_word(t, str(u.row.get("gender") or ""))), rtl))
            elif flag.startswith("stat:"):
                _, st, need = flag.split(":", 2)
                bits.append(bidi.plain(t("ga_needs", stat=bidi.ltr_block(
                    f"{gearadvice.LABELS.get(st, st.upper())} {need}", rtl)), rtl))
        sub = QLabel(bidi.plain(" · ".join(bits), rtl), objectName="Sub", wordWrap=True)
        lay.addWidget(sub)
        if u.buy is not None:                                       # the whole price line one Latin block
            where = t("ga_buy", price=bidi.ltr_block(f"{u.buy:,} mesos · {u.seller}", rtl))
        elif u.drops:
            where = t("ga_drops", list=" · ".join(f"{bidi.ltr_name(m, rtl)} ({t('lv_short', n=lv)})"
                                                  for m, lv in u.drops))
        else:
            where = t("ga_no_price")
        src = QLabel(bidi.plain(where, rtl), objectName="Sub", wordWrap=True)
        lay.addWidget(src)


class _SlotHead(QFrame):
    """A slot's title row: the slot's name (clicking the row folds the slot's list), the folded badge of how
    many upgrades wait in it, and on the end the button naming what is worn (clicking it picks something else)."""

    fold_clicked = Signal(str)

    def __init__(self, slot: str):
        super().__init__(objectName="Head")
        self.setCursor(Qt.PointingHandCursor)
        self._slot = slot
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 4, 10, 4)
        self.lay.setSpacing(6)
        self.lay.addWidget(QLabel(slot, objectName="Slot"))
        self.badge = QLabel(objectName="Sub")
        self.lay.addWidget(self.badge, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.fold_clicked.emit(self._slot)
        super().mouseReleaseEvent(e)


class GearAdvisor(EdgeResize, QWidget):
    """The window itself. Over the game, in the NPCs window's dark glass, taking the keyboard while a stat box
    or the worn-item search is typed in (Esc or the ✕ gives it back). Shows while settings["gear_advisor"] is
    on; its place is kept in gear_advisor_geom. Clicking a slot's row folds it; the worn button opens that
    slot's picker; a chosen item is saved to the character's gear and the advice follows at once."""

    closed = Signal()               # the ✕ turned the setting off: the toolbar's Gear button unchecks
    scroll_requested = Signal(str)  # an item's KB key: "plan scrolls for this" (the scroll planner opens on it)

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
        self._c: Character | None = None     # the active character, re-read on every show / change
        self._plans: list[SlotPlan] = []
        self._sections: dict[str, QWidget] = {}
        self._folded: set[str] | None = None  # the slots the player folded (first open: the empty ones)
        self._pick_slot: str | None = None    # the slot whose picker is open
        self._pick_rows: list[QWidget] = []
        self._badge_of: dict[str, QLabel] = {}      # a folded slot's head: its upgrade count
        self._body_of: dict[str, QWidget] = {}      # each slot's rows, hidden when the slot is folded
        self._opacity = OPACITY_DEFAULT
        self._placed = False                  # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None      # the toolbar's rect, where the window first opens
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._fill_picker)
        self._stats_typing = QTimer(self, singleShot=True, interval=STAT_DEBOUNCE_MS, timeout=self._stats_saved)
        self.setStyleSheet(QSS)
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
        self._char = QLabel(objectName="Char", wordWrap=True)
        col.addWidget(self._char)
        self._stats = QWidget()
        srow = QHBoxLayout(self._stats)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(6)
        self._editors: dict[str, _SearchBox] = {}
        for st in STAT_KEYS:
            pair = QWidget()
            prow = QHBoxLayout(pair)
            prow.setContentsMargins(0, 0, 0, 0)
            prow.setSpacing(4)
            prow.addWidget(QLabel(gearadvice.LABELS[st], objectName="StatName"))    # still readable once filled
            box = _SearchBox(objectName="Stat", maxLength=3)
            box.setMinimumWidth(40)                    # three digits ("120") whole; the gender box keeps its word
            box.setMaximumWidth(60)
            box.setAlignment(Qt.AlignCenter)
            box.textChanged.connect(lambda _t: self._stat_typed())
            prow.addWidget(box, 1)
            srow.addWidget(pair, 1)
            self._editors[st] = box
        self._gender = QComboBox()
        self._gender.setCursor(Qt.PointingHandCursor)
        self._gender.addItems(list(GENDERS))
        self._gender.currentIndexChanged.connect(lambda _i: self._gender_picked())
        self._gender.setSizeAdjustPolicy(QComboBox.AdjustToContents)     # "Female" whole, never clipped
        srow.addWidget(self._gender)
        col.addWidget(self._stats)
        self._hint = QLabel(objectName="Hint", wordWrap=True)
        col.addWidget(self._hint)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(rows)
        col.addWidget(self._scroll, 1)

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ the contract (app.py wires exactly this)

    def refresh(self) -> None:
        """Shown while settings["gear_advisor"] is on; the character is re-read every time it shows."""
        if bool(self._settings[SHOW_KEY]):
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
        elif self.isVisible():
            self.hide()

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._rebuild()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._folded = None            # the slots themselves may all have moved
        self._rebuild()

    def character_changed(self) -> None:
        self._rebuild()

    # ------------------------------------------------------------ text (all of it, for apply_language: a rebuild)

    def _retext_chrome(self) -> None:
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t("ga_title"), rtl))
        self._close.setAccessibleName(t("ga_close"))
        self._close.setToolTip(t("ga_close"))
        for st, box in self._editors.items():
            box.setPlaceholderText("—")                    # its label beside it names it; "—" not typed yet
            box.setAccessibleName(gearadvice.LABELS[st])
        self._gender.setToolTip(t("ga_gender_tip"))
        self._gender.setAccessibleName(t("ga_gender"))
        self._gender.blockSignals(True)                       # its choices re-worded, its pick kept
        idx = self._gender.currentIndex()
        self._gender.clear()
        self._gender.addItem("—")
        self._gender.addItems([_sex_word(t, sex) for sex in GENDERS[1:]])
        self._gender.setCurrentIndex(idx)
        self._gender.blockSignals(False)

    def _rebuild(self) -> None:
        """Every row again, from the active character: the character line, the stat boxes, then a section per
        equip slot (gearadvice.upgrades). All of it — apply_language is just this."""
        t, rtl = self.t, self.t.rtl
        self._pick_slot = None          # the picker is part of the rows; the rows are new
        self._pick_widget = None        # ... and its widgets went with them
        self._pick_rows = []
        self._c = self._profiles.active
        self._retext_chrome()
        bar = self._scroll.verticalScrollBar()
        was = bar.value()
        self._clear_rows()
        if self._c is None:
            self._char.setText(bidi.plain(t("ga_no_character"), rtl))
            self._stats.setVisible(False)
            self._hint.setText("")
            self._hint.setVisible(False)
            return
        c = self._c
        self._char.setText(bidi.plain(f"{bidi.ltr_name(f'{c.name} · {c.job}', rtl)} · {t('lv_short', n=c.level)}",
                                      rtl))
        self._stats.setVisible(True)
        for st, box in self._editors.items():
            box.blockSignals(True)
            box.setText(str(c.ap.get(st, "")) if c.ap.get(st) is not None else "")
            box.blockSignals(False)
        self._gender.blockSignals(True)
        self._gender.setCurrentIndex(GENDERS.index(c.gender) if c.gender in GENDERS else 0)
        self._gender.blockSignals(False)
        hint = bidi.plain(t("ga_stats_hint"), rtl) if not any(c.ap.get(st) for st in STAT_KEYS) else ""
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))
        self._plans = gearadvice.upgrades(self._kb, c)
        if self._folded is None:        # the first draw: the empty slots fold, the rest open
            self._folded = {p.slot for p in self._plans if not p.now and not p.soon and not p.worn}
        for plan in self._plans:
            self._add_section(plan)
        bar.setValue(was)

    def _clear_rows(self) -> None:
        self._sections = {}
        while self._rows_lay.count() > 1:
            item = self._rows_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    # ------------------------------------------------------------ a slot's section

    def _add_section(self, plan: SlotPlan) -> None:
        t, rtl = self.t, self.t.rtl
        section = QWidget()
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = _SlotHead(bidi.plain(_slot_word(t, plan.slot), rtl))    # Claw -> טופר, per itemterms
        head.fold_clicked.connect(lambda s=plan.slot: self._fold_clicked(s))
        self._badge_of[plan.slot] = head.badge
        worn = QPushButton(objectName="Worn")
        worn.setCursor(Qt.PointingHandCursor)
        worn.setFocusPolicy(Qt.NoFocus)
        worn.setToolTip(t("ga_worn_tip"))
        worn.setText(bidi.ltr_name(str(plan.worn.get("item") or "") if plan.worn else t("ga_nothing"), rtl))
        worn.clicked.connect(lambda _=False, s=plan.slot: self._worn_clicked(s))
        head.lay.addWidget(worn)
        lay.addWidget(head)
        body = QWidget()
        blay = QVBoxLayout(body)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(4)
        if plan.slot == "Overall":
            note = QLabel(bidi.plain(t("ga_overall_note"), rtl), objectName="Note", wordWrap=True)
            blay.addWidget(note)
        if plan.worn_gone:
            blay.addWidget(QLabel(bidi.plain(t("ga_worn_gone"), rtl), objectName="Note", wordWrap=True))
        for u in plan.now:
            blay.addWidget(_UpRow(t, u, self.scroll_requested.emit))
        for u in plan.soon:
            blay.addWidget(_UpRow(t, u, self.scroll_requested.emit))
        if not plan.now and not plan.soon and not plan.worn_gone:
            blay.addWidget(QLabel(bidi.plain(t("ga_none_better"), rtl), objectName="Note", wordWrap=True))
        lay.addWidget(body)
        self._body_of[plan.slot] = body
        self._apply_fold(plan)
        self._sections[plan.slot] = section
        self._rows_lay.insertWidget(self._rows_lay.count() - 1, section)

    def _apply_fold(self, plan: SlotPlan) -> None:
        t = self.t
        folded = plan.slot in (self._folded or set())
        self._body_of[plan.slot].setVisible(not folded)
        self._badge_of[plan.slot].setText("" if not folded or not plan.now else
                                          bidi.plain(t("ga_upgrades", n=bidi.ltr_block(len(plan.now), t.rtl)), t.rtl))

    def _fold_clicked(self, slot: str) -> None:
        self._folded = (self._folded or set()) ^ {slot}
        self._apply_fold(next(p for p in self._plans if p.slot == slot))

    def _worn_clicked(self, slot: str) -> None:
        """The worn button: that slot's picker opens (the same slot again: closed)."""
        if self._pick_slot == slot:
            self._close_picker()
            return
        self._close_picker()
        self._pick_slot = slot
        t = self.t
        pick = QWidget()
        lay = QVBoxLayout(pick)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._pick_box = _SearchBox()
        self._pick_box.setClearButtonEnabled(True)
        self._pick_box.setPlaceholderText(bidi.plain(t("ga_pick_ph"), t.rtl))
        self._pick_box.textChanged.connect(lambda _t: self._typing.start())
        lay.addWidget(self._pick_box)
        self._pick_list = QScrollArea()
        self._pick_list.setWidgetResizable(True)
        self._pick_list.setFrameShape(QFrame.NoFrame)
        self._pick_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pick_list.setMaximumHeight(240)
        rows = QWidget(objectName="Rows")
        self._pick_rows_lay = QVBoxLayout(rows)
        self._pick_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._pick_rows_lay.setSpacing(4)
        self._pick_rows_lay.addStretch(1)
        self._pick_list.setWidget(rows)
        lay.addWidget(self._pick_list)
        self._pick_more = QLabel(objectName="Sub", wordWrap=True)
        lay.addWidget(self._pick_more)
        self._pick_widget = pick
        section = self._sections[slot]
        section.layout().insertWidget(1, pick)
        self._fill_picker()
        self._pick_box.setFocus()

    def _fill_picker(self) -> None:
        """The picker's rows for what is typed: the slot's items (the character's class's, any level), and
        nothing at the top to take off what is worn."""
        if self._pick_slot is None:
            return
        t, rtl = self.t, self.t.rtl
        q = self._pick_box.text() if hasattr(self, "_pick_box") else ""
        while self._pick_rows:
            w = self._pick_rows.pop()
            self._pick_rows_lay.removeWidget(w)
            w.hide()
            w.deleteLater()
        plan = next((p for p in self._plans if p.slot == self._pick_slot), None)
        if plan is None:
            return
        none_row = _Row(bidi.plain(t("ga_nothing"), rtl), "", t("ga_worn_tip"))
        none_row.clicked.connect(lambda _=False: self._worn_set(self._pick_slot, None))
        self._pick_rows.append(none_row)
        self._pick_rows_lay.insertWidget(self._pick_rows_lay.count() - 1, none_row)
        rows = [r for r in plan.picker if _match(r.get("item") or "", q)]
        for r in rows[:PICK_ROWS]:
            bits = [t("lv_short", n=r.get("req_lv") or 0)]
            bits += [bidi.ltr_block(" ".join(
                x for x in (gearadvice.LABELS[st], str(r[f"req_{st}"])) if x), rtl)
                for st in STAT_KEYS if isinstance(r.get(f"req_{st}"), int) and r[f"req_{st}"]]
            if r.get("gender"):
                bits.append(bidi.plain(t("ga_gender_only", g=_sex_word(t, str(r["gender"]))), rtl))
            row = _Row(bidi.ltr_name(str(r.get("item") or ""), rtl), bidi.plain(" · ".join(bits), rtl))
            row.clicked.connect(lambda _=False, k=str(r.get("key")): self._worn_set(self._pick_slot, k))
            self._pick_rows.append(row)
            self._pick_rows_lay.insertWidget(self._pick_rows_lay.count() - 1, row)
        left = len(rows) - min(len(rows), PICK_ROWS)
        self._pick_more.setText(bidi.plain(t("ga_more", n=bidi.ltr_block(left, rtl)), rtl) if left else "")
        if not rows:
            self._pick_more.setText(bidi.plain(t("ga_pick_none"), rtl))

    def _close_picker(self) -> None:
        if self._pick_slot is None:
            return
        pick = getattr(self, "_pick_widget", None)
        if pick is not None:
            pick.setParent(None)
            pick.hide()
            pick.deleteLater()
            self._pick_widget = None
        self._pick_rows = []
        self._pick_slot = None

    def _worn_set(self, slot: str | None, key: str | None) -> None:
        """A picker row clicked: what is worn in this slot is saved to the character (nothing: taken off) and
        the advice follows at once."""
        c = self._profiles.active
        if c is None or slot is None:
            return
        if key:
            c.gear[slot] = key
        else:
            c.gear.pop(slot, None)
        self._profiles.save()
        self._rebuild()

    # ------------------------------------------------------------ the player's own numbers

    def _stat_typed(self) -> None:
        """A keystroke in a stat box: the saving waits for the pause (the advice is re-read whole)."""
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

    # ------------------------------------------------------------ closing

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's Gear button follows), hidden until switched back on."""
        self._close_picker()
        self._settings[SHOW_KEY] = False
        self.hide()
        self.closed.emit()

    def hideEvent(self, e) -> None:
        for timer in (self._typing, self._stats_typing):   # nothing fires once hidden: no re-read behind the back
            timer.stop()
        self._close_picker()
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
        """Just under the toolbar (the bar's Gear button opens this), kept on that screen; without one — the
        bar never placed, or gone — the primary screen's top-left corner."""
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
