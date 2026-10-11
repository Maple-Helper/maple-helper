"""The scroll planner's window over the game (the toolbar's Scrolls button opens it): pick a piece of gear,
pick the stat and how many of its upgrade slots to use, and every grade of scroll the KB confirms is in the
game is laid out side by side — its per-slot success, the chance all n pass, the expected gain, the exact
odds of every outcome, and the cost per expected +1 (an NPC price from the KB, a Free Market usual price from
MeowDB, or the player's own typed price — never one made up).

Built like the toolbar's other windows (the searches, the loot log): the same dark glass at the opacity the
player picked for them, the title row that drags it, the same rows and rounded controls, its place kept in
scroll_planner_geom. It takes the keyboard while its search box is used (like the search window), shows while
settings["scroll_planner"] is on, and its ✕ turns that off. The Free Market lookups run off the GUI thread."""
from __future__ import annotations

import re
import threading

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QToolButton, QVBoxLayout, QWidget)

from .. import bidi, itemterms, market, scrollplan
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Profiles, Settings
from . import mapview, theme
from .gamesearch import GAP, QSS as _GLASS_QSS, SCROLLBAR_W, TYPE_DEBOUNCE_MS, _Row
from .glass import SHADOW, EdgeResize
from .npcoverlay import (BOTTOM, FILL1, FILL2, GEOM_DEBOUNCE_MS, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN,
                         SIDE, TEXT, TOP, _Header, _paint_glass, set_capturable)

DEFAULT_W, DEFAULT_H = 400, 560             # what opens under the toolbar before it is ever resized
MIN_W, MIN_H = 340, 400                     # the smallest the player can drag the window to
LIMIT = 12                                  # equips listed before the "and N more" line
UNKNOWN_CAP = 30                            # the spinner's ceiling for an equip whose slot count the KB lacks

QSS = _GLASS_QSS + f"""
#Stepper {{ background: {FILL1}; border: none; border-radius: 8px; }}
QToolButton#StepBtn {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px;
                       padding: 3px 9px; font-weight: 600; }}
QToolButton#StepBtn:hover {{ background: {FILL2}; }}
QToolButton#StepBtn:disabled {{ color: {MUTED}; }}
#StepNum {{ color: {TEXT}; font-weight: 600; min-width: 24px; }}
QPushButton#Stat {{ background: {FILL1}; color: {MUTED}; border: none; border-radius: 8px;
                   padding: 4px 10px; text-align: center; }}
QPushButton#Stat:hover {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Stat:checked {{ background: rgba(255,149,51,0.30); color: {TEXT}; font-weight: 600; }}
#GradeName {{ background: transparent; color: {TEXT}; font-weight: 600; }}
#Note {{ color: {MUTED}; font-size: 11px; }}
#YourPrice {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px; padding: 3px 8px; }}
"""


class _Box(QLineEdit):
    """The typing box: Esc drops the results and goes back to the plan (the keyboard goes back to the game at
    once); with nothing typed it closes the window, like the ✕."""

    escaped = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(e)


class _Stepper(QFrame):
    """A small − n + counter in the overlay's own look: a spin box's little arrows don't survive the dark
    glass's style sheet, and the value is too often 7 to leave the player clicking one at a time."""

    changed = Signal(int)

    def __init__(self, lo: int = 1, hi: int = UNKNOWN_CAP, value: int = 1):
        super().__init__(objectName="Stepper")
        self._lo, self._hi, self._v = lo, hi, lo
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

    def setRange(self, lo: int, hi: int) -> None:
        self._lo, self._hi = lo, hi
        self.setValue(self._v)

    def setValue(self, v: int) -> None:
        v = min(self._hi, max(self._lo, int(v)))
        self._v = v
        self._num.setText(str(v))
        self._minus.setEnabled(v > self._lo)
        self._plus.setEnabled(v < self._hi)
        self.changed.emit(v)


def _pct(v: float) -> str:
    """1.0 -> '100%', 0.6 ** 7 -> '2.8%', 0.0000001 -> '<0.1%', exactly 0 -> '0.0%': one decimal, never
    pretending to round to 100, never a true zero reading as a near miss."""
    if v >= 0.9995:
        return "100%"
    if v > 0 and v < 0.001:
        return "<0.1%"
    return f"{v * 100:.1f}%"


def _num(v: float) -> str:
    """70.0 -> '70', 4.2 -> '4.2': an expected gain with no trailing zero."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _mesos(v: float) -> str:
    return f"{round(v):,}"


class _Wrap(QLabel):
    """A word-wrapping label that asks for the height its wrapped text needs: Qt's own size hint inside a
    scroll area lags a width change (and a text set before the first layout), which clipped the second line
    of the outcome ladders."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, wordWrap=True, **kwargs)

    def resizeEvent(self, e) -> None:
        need = self.heightForWidth(self.width())
        if need > 0 and self.minimumHeight() != need:
            self.setMinimumHeight(need)


class _Body(QWidget):
    """The scroll area's content: whenever it is resized (its width changed, the wrapped lines re-broke) it
    re-asks for the height its rows now need — Qt's own word-wrap hints lag a change, and the cards drew over
    each other until some later repaint nudged them apart."""

    def resizeEvent(self, e) -> None:
        lay = self.layout()
        if lay is not None:
            self.setMinimumHeight(lay.totalMinimumSize().height())


class ScrollPlanner(EdgeResize, QWidget):
    """The window itself: over the game, in the searches' dark glass, taking the keyboard while its search box
    is used. A picked equip shows its clean slots, a spinner for how many to use, a choice of the stat to
    scroll, and one card per grade with the odds, the outcome ladder and the costs. The player's typed price
    per scroll is kept for the window's life, never saved."""

    closed = Signal()               # the ✕ (or Esc on an empty box) turned settings["scroll_planner"] off
    market_ready = Signal(object)   # a Free Market lookup's answer, from its own thread

    def __init__(self, kb: KnowledgeBase, settings: Settings, profiles: Profiles, t: I18n):
        # the search window's flags, not the NPCs window's: this one takes the keyboard while its box is used
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self.setWindowTitle("Maple Helper")
        self._kb, self._settings, self._profiles, self.t = kb, settings, profiles, t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._picked: scrollplan.EquipScrolls | None = None    # the player's own pick, kept across switches
        self._default: scrollplan.EquipScrolls | None = None   # what the active character wears, until they pick
        self._stat = 0                     # the chosen stat group, an index into the equip's groups
        self._your: dict[str, int] = {}    # a typed price per scroll's KB key, this window's life only
        self._fm: dict[str, int | None] = {}   # a Free Market usual price per scroll key (None: none on the site)
        self._fm_bad: set[str] = set()     # scroll keys whose lookup couldn't reach the site at all
        self._fm_seen: set[str] = set()    # scroll keys a thread is already fetching
        self._fm_read = 0                  # bumped on a KB swap: answers from before it are dropped
        self._opacity = OPACITY_DEFAULT
        self._placed = False               # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None   # the toolbar's rect, where the window first opens
        self._rows: list[QWidget] = []
        self._cards: list[dict] = []
        self._measure: dict[str, QLabel] = {}     # hidden labels the card texts' lines are measured with
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._run)
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
        self._box = _Box(objectName="Search")
        self._box.setClearButtonEnabled(True)
        self._box.textChanged.connect(lambda _t: self._typing.start())
        self._box.escaped.connect(self._escaped)
        col.addWidget(self._box)
        self._status = QLabel(objectName="Status", wordWrap=True)
        col.addWidget(self._status)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = _Body()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(4)
        self._results = QWidget(objectName="Rows")
        self._results_lay = QVBoxLayout(self._results)
        self._results_lay.setContentsMargins(0, 0, 0, 0)
        self._results_lay.setSpacing(4)
        body_lay.addWidget(self._results)
        self._plan = QWidget(objectName="Rows")
        self._plan_lay = QVBoxLayout(self._plan)
        self._plan_lay.setContentsMargins(0, 0, 0, 0)
        self._plan_lay.setSpacing(6)
        body_lay.addWidget(self._plan)
        body_lay.addStretch(1)
        self._scroll.setWidget(body)
        col.addWidget(self._scroll, 1)
        self._build_plan()
        self.market_ready.connect(self._on_market)
        self._retext()

    def _build_plan(self) -> None:
        """The plan half of the body: the picked equip's header, the spinner and the stat choice, then the
        grade cards (filled in per pick) and the notes."""
        self._head = _Row("", " ", "", clickable=False)     # ' ': its grey line exists, filled per pick
        self._plan_lay.addWidget(self._head)
        knobs = QHBoxLayout()
        knobs.setSpacing(6)
        self._use_label = QLabel(objectName="Sub")
        self._spin = _Stepper()
        self._spin.changed.connect(self._n_changed)
        self._bonus_label = QLabel(objectName="Sub")
        knobs.addWidget(self._use_label)
        knobs.addWidget(self._spin)
        knobs.addStretch(1)
        self._plan_lay.addLayout(knobs)
        self._stats_lay = QGridLayout()
        self._stats_lay.setSpacing(4)
        self._plan_lay.addLayout(self._stats_lay)
        self._cards_lay = QVBoxLayout()
        self._cards_lay.setSpacing(6)
        self._plan_lay.addLayout(self._cards_lay)
        self._summary = _Wrap(objectName="Note")
        self._plan_lay.addWidget(self._summary)
        self._chaos = _Wrap(objectName="Note")
        self._chaos.setContentsMargins(0, 4, 0, 0)
        self._plan_lay.addWidget(self._chaos)
        self._source = _Wrap(objectName="Note")
        self._plan_lay.addWidget(self._source)
        self._hint = _Wrap(objectName="Sub")
        self._plan_lay.addWidget(self._hint)

    # ------------------------------------------------------------ shown, hidden, replaced

    def refresh(self) -> None:
        """Shown while settings["scroll_planner"] is on (the bar's button flips it): placed where the player
        left it or under the bar, at the overlays' opacity, out of screen captures, the active character read."""
        want = bool(self._settings["scroll_planner"])
        if want and not self.isVisible():
            self._read_opacity()
            self._place()
            self.show()
            set_capturable(self, self._settings)
        if want:
            if self._sync_default():
                self._picked = None        # nothing of the player's own: the worn gear replaced
                self._show_plan()
            self._paint_cards()
        elif self.isVisible():
            self.hide()

    def open_item(self, key: str) -> None:
        """Jump straight to one equip (the gear advisor's 'plan scrolls for this'): picked, shown, the setting
        on. A key the KB doesn't know as an equip only says so in the status line."""
        es = scrollplan.scrolls_for(self._kb, key)
        if es is None:
            self._status.setText(bidi.plain(self.t("sp_not_equip"), self.t.rtl))
            return
        self._pick(es)
        self._settings["scroll_planner"] = True
        if not self.isVisible():
            self._read_opacity()
            self._place()
            self.show()
            set_capturable(self, self._settings)
        self.raise_()
        self.activateWindow()
        self._show_plan()

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A KB update swapped the KB: everything is read again, the market answers of the old one dropped."""
        self._kb = kb
        self._fm.clear()
        self._fm_bad.clear()
        self._fm_seen.clear()
        self._fm_read += 1
        if self._picked is not None:
            again = scrollplan.scrolls_for(kb, self._picked.key)
            self._picked = again or None
        self._default = None
        self._sync_default()
        self._run()
        self._show_plan()

    def character_changed(self) -> None:
        """The active character switched or was edited: what it wears is read again (it is the plan's pick
        until the player picks their own)."""
        if self._sync_default() and self._picked is None:
            self._show_plan()

    def _sync_default(self) -> bool:
        """The active character's first scrollable worn equip as the plan's default pick. True when it
        changed. The player's own pick always wins over it."""
        ch = self._profiles.active if self._profiles is not None else None
        gear = getattr(ch, "gear", None) or {}
        found = None
        for key in gear.values():
            es = scrollplan.scrolls_for(self._kb, key) if key else None
            if es is not None and es.groups:
                found = es
                break
        was, self._default = self._default, found
        return was is not found or (was is not None and found is not None and was.key != found.key)

    def place_under(self, rect: QRect) -> None:
        """Where the window opens the first time (no place kept yet): just under the toolbar's rect."""
        self._under = QRect(rect) if isinstance(rect, QRect) else None

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext()
        self._run()
        self._paint_head()
        self._build_stats()
        self._rebuild_cards()

    # ------------------------------------------------------------ the search

    def _escaped(self) -> None:
        if self._box.text():
            self._box.clear()          # textChanged drops the results, the plan is back
        else:
            self._close_clicked()

    def _run(self) -> None:
        """The equips for what is typed (the typing debounce's work too)."""
        t, rtl = self.t, self.t.rtl
        q = self._box.text()
        picks = scrollplan.find_equips(self._kb, q, LIMIT) if q.strip() else []
        self._clear_rows()
        for es in picks:
            sub = " · ".join(x for x in (itemterms.kind(es.slot, t.lang), self._slots_text(es)) if x)
            row = _Row(bidi.ltr_name(es.item, rtl), bidi.plain(sub, rtl) if sub else "", t("sp_pick_tip"))
            row.clicked.connect(lambda _=False, e=es: self._pick(e))
            self._rows.append(row)
            self._results_lay.insertWidget(self._results_lay.count(), row)
        self._results.setVisible(bool(picks))
        self._plan.setVisible(not picks)
        if len(q.replace(" ", "")) < 2:
            text = t("sp_type_more") if q.strip() else ""
        elif not picks:
            text = t("sp_none", q=bidi.ltr_block(q, rtl))
        elif (total := len(scrollplan.find_equips(self._kb, q, 10_000))) > len(picks):
            text = t("sp_more", n=bidi.ltr_block(f"{total - len(picks):,}", rtl))
        else:
            text = ""
        self._status.setText(bidi.plain(text, rtl) if text else "")

    def _clear_rows(self) -> None:
        while self._rows:
            w = self._rows.pop()
            self._results_lay.removeWidget(w)
            w.hide()
            w.deleteLater()

    def _slots_text(self, es: scrollplan.EquipScrolls) -> str:
        t = self.t
        if es.slots is None:
            return t("sp_slots_none")
        return t("sp_slots_one") if es.slots == 1 else t("sp_slots", n=bidi.ltr_block(f"{es.slots}", t.rtl))

    def _pick(self, es: scrollplan.EquipScrolls) -> None:
        """The equip to plan: picked, the spinner set to its clean slots, the first stat group chosen, the
        typed prices of the last equip dropped."""
        self._picked = es
        self._stat = 0
        self._your.clear()
        self._box.clear()
        self._show_plan()

    def _show_plan(self) -> None:
        """The plan half drawn for the equip in hand (the player's pick, else the character's worn one)."""
        t, rtl = self.t, self.t.rtl
        es = self._picked or self._default
        self._clear_rows()
        self._results.hide()
        self._status.setText("")
        self._plan.setVisible(es is not None)
        if es is None:
            self._hint.setText(bidi.plain(t("sp_pick"), rtl))
            self._hint.setVisible(True)
            self._set_cards_visible(False)
            return
        self._hint.setVisible(False)
        self._paint_head()
        if not es.groups:
            self._set_cards_visible(False)
            self._hint.setText(bidi.plain(t("sp_no_scrolls"), rtl))
            self._hint.setVisible(True)
            return
        cap = es.slots if isinstance(es.slots, int) and es.slots > 0 else UNKNOWN_CAP
        self._spin.setRange(1, cap)
        self._spin.setValue(min(cap, es.slots or 1))
        self._n = self._spin.value()
        self._build_stats()
        self._rebuild_cards()

    def _paint_head(self) -> None:
        """The equip in hand's own two lines: its name, and its slot (in the player's language, per
        itemterms' own translations) with its clean upgrade slots."""
        t, rtl = self.t, self.t.rtl
        es = self._picked or self._default
        self._head.name.setText(bidi.ltr_name(es.item, rtl) if es else "")
        sub = " · ".join(x for x in ((itemterms.kind(es.slot, t.lang), self._slots_text(es)) if es else ()) if x)
        self._head.sub.setText(bidi.plain(sub, rtl) if sub else "")
        self._head.sub.setVisible(bool(sub))

    def _build_stats(self) -> None:
        """The equip's stats as a wrap of little pills, the chosen one lit (a combo's arrow doesn't survive
        the dark glass's style sheet, and pills read at a glance over the game)."""
        es = self._equip()
        while (btn := self._stats_lay.takeAt(0)) is not None:
            if btn.widget() is not None and btn.widget() is not self._bonus_label:   # the caption stays
                btn.widget().hide()
                btn.widget().deleteLater()
        self._stats_lay.addWidget(self._bonus_label, 0, 0)      # the caption leads the pills it names
        self._stats: list[QPushButton] = []
        for i, g in enumerate((es.groups if es else ()) or ()):
            b = QPushButton(bidi.plain(g.stat, self.t.rtl), objectName="Stat", checkable=True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(self.t("sp_bonus"))
            b.setAccessibleName(self.t("sp_bonus"))
            b.setChecked(i == self._stat)
            b.clicked.connect(lambda _=False, i=i: self._stat_changed(i))
            self._stats_lay.addWidget(b, (i + 1) // 4, (i + 1) % 4)
            self._stats.append(b)
        if self._stats:
            self._stat = min(self._stat, len(self._stats) - 1)

    def _set_cards_visible(self, on: bool) -> None:
        for w in ("_use_label", "_spin", "_bonus_label", "_summary", "_chaos", "_source"):
            getattr(self, w).setVisible(on)
        self._clear_cards()
        self._build_stats()

    # ------------------------------------------------------------ the cards

    def _n_changed(self, n: int) -> None:
        self._n = n
        self._paint_cards()

    def _stat_changed(self, i: int) -> None:
        self._stat = i
        for n, b in enumerate(self._stats):
            b.setChecked(n == i)
        self._rebuild_cards()

    def _equip(self) -> scrollplan.EquipScrolls | None:
        return self._picked or self._default

    def _group(self) -> scrollplan.StatGroup | None:
        es = self._equip()
        if es is None or not es.groups or not 0 <= self._stat < len(es.groups):
            return None
        return es.groups[self._stat]

    def _rebuild_cards(self) -> None:
        """One card per grade of the chosen stat (their texts filled by _paint_cards, so a price that arrives
        or a spinner turn only rewrites them — a typed price is never interrupted)."""
        self._clear_cards()
        group = self._group()
        if group is None:
            return
        self._set_cards_visible(True)
        for s in group.grades:
            card = QFrame(objectName="Row")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 6, 10, 6)
            lay.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(8)
            name = QLabel(objectName="GradeName")
            price = QWidget()
            price_lay = QVBoxLayout(price)
            price_lay.setContentsMargins(0, 0, 0, 0)
            price_lay.setSpacing(0)
            top.addWidget(name, 0)
            top.addWidget(price, 1)
            lay.addLayout(top)
            odds = QWidget()
            odds_lay = QVBoxLayout(odds)
            odds_lay.setContentsMargins(0, 0, 0, 0)
            odds_lay.setSpacing(0)
            lay.addWidget(odds)
            ladder = QWidget()
            ladder_lay = QVBoxLayout(ladder)
            ladder_lay.setContentsMargins(0, 0, 0, 0)
            ladder_lay.setSpacing(0)
            lay.addWidget(ladder)
            bottom = QHBoxLayout()
            bottom.setSpacing(8)
            cost = QWidget()
            cost_lay = QVBoxLayout(cost)
            cost_lay.setContentsMargins(0, 0, 0, 0)
            cost_lay.setSpacing(0)
            edit = QLineEdit(objectName="YourPrice")
            edit.setFixedWidth(150)
            edit.setMinimumHeight(edit.sizeHint().height())
            edit.textChanged.connect(lambda txt, k=s.key: self._your_changed(k, txt))
            bottom.addWidget(cost, 1)
            bottom.addWidget(edit, 0, Qt.AlignBottom)
            lay.addLayout(bottom)
            self._cards_lay.addWidget(card)
            self._cards.append({"scroll": s, "card": card, "name": name, "price": price_lay, "odds": odds_lay,
                                "ladder": ladder_lay, "cost": cost_lay, "edit": edit})
        self._paint_cards()

    def _clear_cards(self) -> None:
        while self._cards:
            c = self._cards.pop()
            c["card"].hide()
            c["card"].deleteLater()
        self._summary.setText("")
        for c in (self._chaos, self._source):
            c.setText("")

    def _your_changed(self, key: str, text: str) -> None:
        m = re.fullmatch(r"\s*([\d,]+)\s*", text or "")
        if m:
            self._your[key] = int(m.group(1).replace(",", ""))
        else:
            self._your.pop(key, None)
        self._paint_cards()

    def _prices(self, group: scrollplan.StatGroup) -> dict[str, int]:
        """The price each grade's cost is figured at: the player's own typed one, else the KB's NPC price,
        else the Free Market's usual price. A grade without any shows its odds with no cost, never a made-up
        number."""
        out = {}
        for s in group.grades:
            if s.key in self._your:
                out[s.key] = self._your[s.key]
            elif isinstance(s.buy, int):
                out[s.key] = s.buy
            elif self._fm.get(s.key) is not None:
                out[s.key] = self._fm[s.key]
        return out

    def _line_fm(self, obj: str):
        """A hidden label of one card text's own look, for measuring its lines (kept for the window's life)."""
        if obj not in self._measure:
            lb = QLabel(self, objectName=obj)
            lb.hide()
            lb.ensurePolished()
            self._measure[obj] = lb
        return self._measure[obj].fontMetrics()

    @staticmethod
    def _wrap_tokens(tokens: list[str], sep: str, fm, max_w: int) -> list[str]:
        """Tokens (ladder entries, ' · '-separated phrases) as lines no wider than max_w: greedy, a token
        never cut in half, measured here — Qt's own wrapped-label hints lag inside a scroll area and the
        rows drew over each other."""
        out, cur = [], ""
        for tok in tokens:
            cand = cur + sep + tok if cur else tok
            if cur and fm.horizontalAdvance(cand) > max_w:
                out.append(cur)
                cur = tok
            else:
                cur = cand
        return out + [cur] if cur else out

    @staticmethod
    def _set_lines(lay, lines: list[str], obj: str, right: bool = False) -> None:
        """A card's text as exactly as many single-line labels as it has lines — never a wrapping label:
        one line per label, and Qt's layout always knows the height it needs."""
        while lay.count() > len(lines):
            w = lay.takeAt(lay.count() - 1).widget()
            w.hide()
            w.deleteLater()
        while lay.count() < len(lines):
            lb = QLabel(objectName=obj)
            lb.setAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignVCenter)
            lay.addWidget(lb)
        for i, text in enumerate(lines):
            lay.itemAt(i).widget().setText(text)

    def _paint_cards(self) -> None:
        """Every text of the grade cards, the comparison line and the notes, from the plan's own math."""
        t, rtl = self.t, self.t.rtl
        group = self._group()
        if group is None:
            return
        got = scrollplan.plans(group, self._n, self._prices(group))
        inner = self._scroll.viewport().width() - SCROLLBAR_W - 20     # a card's own width, rim aside
        for card, gp in zip(self._cards, got):
            s = gp.scroll
            name = bidi.plain(f"{s.grade} · {bidi.ltr_block(f'{s.success}%', rtl)}", rtl)
            card["name"].setText(name)
            price_w = max(120, inner - self._line_fm("GradeName").horizontalAdvance(name) - 8)
            key, pieces = self._price_bits(s)
            if pieces:
                lines = self._wrap_tokens(pieces, " · ", self._line_fm("Sub"), price_w)
                texts = [bidi.plain(t(key, p=bidi.ltr_block(lines[0], rtl)), rtl)]      # the key opens the
                texts += [bidi.plain(bidi.ltr_block(ln, rtl), rtl) for ln in lines[1:]]  # price, not each line
            else:
                texts = [bidi.plain(t(key), rtl)]
            self._set_lines(card["price"], texts, "Sub", right=not rtl)
            # each part is laid out whole, then blocked whole: "0.6 ** 7" showed as "2.8" next to a stray "%",
            # and "+70 Max HP" as "Max HP +70", while the parts floated apart in the Hebrew lines
            exp = " · ".join(f"+{_num(v)} {label}" for label, v in gp.expected)
            parts = [bidi.plain(t("sp_all_pass", n=bidi.ltr_block(str(self._n), rtl),
                                  p=bidi.ltr_block(_pct(gp.plan.all_pass), rtl)), rtl),
                     bidi.plain(t("sp_expected", v=bidi.ltr_block(exp, rtl) if exp else "—"), rtl)]
            self._set_lines(card["odds"], self._wrap_tokens(parts, " · ", self._line_fm("Sub"), inner), "Sub")
            gain = s.gains[0][1] if s.gains else 0
            entries = [f"+{k * gain}: {_pct(pr)}" for k, pr in enumerate(gp.plan.dist) if pr > 0]
            word = bidi.plain(t("sp_ladder"), rtl)
            head = f"({group.primary}):" if entries else ""
            budget = inner - (self._line_fm("Sub").horizontalAdvance(word) + 6 if rtl and head else 0)
            # the head leads its first entry (never a ' · ' between them: "(Max HP): · +70: 100%")
            first = [f"{head} {entries[0]}"] if head and entries else ([head] if head else [])
            lines = self._wrap_tokens(first + entries[1:], " · ", self._line_fm("Note"), budget)
            texts = ([f"{word} {bidi.ltr_block(lines[0], rtl)}"] if lines else []) \
                    + [bidi.ltr_block(ln, rtl) for ln in lines[1:]]
            self._set_lines(card["ladder"], [bidi.plain(x, rtl) for x in texts], "Note")
            cost = (bidi.plain(t("sp_expected_cost", p=bidi.ltr_block(
                f"{_mesos(gp.expected_cost)} mesos", rtl)), rtl)
                    if gp.expected_cost is not None else bidi.plain(t("sp_price_none"), rtl))
            self._set_lines(card["cost"], [cost], "Sub")
            if s.key in self._your:
                card["edit"].setPlaceholderText("")
                if card["edit"].text() != f"{self._your[s.key]:,}":
                    card["edit"].setText(f"{self._your[s.key]:,}")
            else:
                card["edit"].setPlaceholderText(t("sp_your_price"))
        bits = []
        for gp in got:
            if gp.per_point is not None:
                bits.append(bidi.ltr_block(f"{gp.scroll.grade} {_mesos(gp.per_point)}", rtl))
            elif rtl:
                bits.append(f"{t('sp_no_price_short')} {bidi.ltr_block(gp.scroll.grade, rtl)}")
            else:
                bits.append(f"{gp.scroll.grade} {t('sp_no_price_short')}")
        self._summary.setText(bidi.plain(
            t("sp_per_point", point=bidi.ltr_block(f"+1 {group.stat}", rtl), list=" · ".join(bits)), rtl))
        self._chaos.setText(bidi.plain(t("sp_chaos_note"), rtl))
        self._source.setText(bidi.plain(t("sp_source_note"), rtl))
        self._queue_fm(group)

    def _price_bits(self, s: scrollplan.Scroll) -> tuple[str, list[str]]:
        """(the line's own key, its ' · '-separated pieces): the pieces are wrapped into lines first and each
        wrapped line becomes one left-to-right block, whole — pieced-out numbers scrambled in the Hebrew
        lines ("mesos 15,000 :NPC-מה", seen in the PNG review)."""
        if s.key in self._your:
            return "sp_your_price", [f"{self._your[s.key]:,}"]
        if isinstance(s.buy, int):
            return "sp_price_npc", [f"{s.buy:,} mesos", s.seller]
        if s.key in self._fm:
            if self._fm[s.key] is not None:
                return "sp_price_fm", [f"{self._fm[s.key]:,} mesos"]
            return "sp_price_none", []
        if s.key in self._fm_bad:
            return "sp_price_offline", []
        return "sp_price_loading", []

    # ------------------------------------------------------------ the Free Market prices

    def _queue_fm(self, group: scrollplan.StatGroup) -> None:
        """The usual Free Market price for every grade neither the KB's NPC price nor an earlier fetch covers,
        fetched off the GUI thread (each item is up to 3 requests of 8 s on a slow line)."""
        keys = [s.key for s in group.grades if not isinstance(s.buy, int) and s.key not in self._fm_seen]
        if not keys:
            return
        self._fm_seen.update(keys)
        read = self._fm_read            # only a KB swap starts a new round: a new pick never drops the
                                        # answers of keys still in _fm_seen (they'd stay "loading" for good)

        def look():
            for key in keys:
                slug = key.partition("/")[2]
                usual, offline = None, False
                if slug.isdigit():
                    try:
                        m = market.item_market(int(slug))
                    except Exception:      # noqa: BLE001 - an odd answer shows "no price", not a crash
                        m = None
                    if m is None:
                        offline = True
                    else:
                        usual = m.usual
                try:
                    self.market_ready.emit((read, key, usual, offline))
                except RuntimeError:
                    return               # the window was deleted meanwhile: nothing to show it in
        threading.Thread(target=look, daemon=True).start()

    def _on_market(self, r) -> None:
        """A lookup's answer, back on the GUI thread: remembered (also for the other grades' cards) and the
        texts rewritten. An answer from before the last pick or KB swap is dropped."""
        read, key, usual, offline = r
        if read != self._fm_read:
            return
        if offline:
            self._fm_bad.add(key)
        else:
            self._fm[key] = usual
        if self.isVisible():
            self._paint_cards()

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        self._title.setText(bidi.plain(t("sp_title"), rtl))
        self._close.setAccessibleName(t("sp_close"))
        self._close.setToolTip(t("sp_close"))
        self._box.setPlaceholderText(bidi.plain(t("sp_search_ph"), rtl))
        self._use_label.setText(bidi.plain(t("sp_use"), rtl))
        self._bonus_label.setText(bidi.plain(t("sp_bonus"), rtl))
        self._spin.setAccessibleName(t("sp_use"))
        self._bonus_label.setText(bidi.plain(t("sp_bonus"), rtl))
        self._hint.setText(bidi.plain(t("sp_pick"), rtl))

    # ------------------------------------------------------------ close, place, move, resize

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's button follows), hidden until switched back on."""
        self._settings["scroll_planner"] = False
        self.hide()
        self.closed.emit()

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    def _read_opacity(self) -> None:
        """The NPCs window's own slider sets it: the same dark glass, as see-through as the player asked."""
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: under the toolbar again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings["scroll_planner_geom"]
        size = QSize(self.width(), self.height())
        if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int):
            size = mapview.clamp_size(saved["w"], saved["h"], screens, QSize(MIN_W, MIN_H))
        spot = mapview.saved_spot(saved, size, screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(size), size))
        self._placed = True

    def _default_spot(self, size: QSize) -> QPoint:
        """Just under the toolbar (its button opens this), kept on that screen; without one — the bar never
        placed, or gone — the primary screen's top-left corner."""
        primary = QGuiApplication.primaryScreen().availableGeometry()
        screens = [s.geometry() for s in QGuiApplication.screens()]
        under = self._under
        if isinstance(under, QRect) and not under.isNull():
            screen = next((g for g in screens if g.intersects(under)), primary)
            x = min(max(under.left(), screen.left()), max(screen.left(), screen.right() - size.width() + 1))
            y = min(max(under.bottom() + 1 + GAP, screen.top()), max(screen.top(), screen.bottom() - size.height() + 1))
            return QPoint(x, y)
        return primary.topLeft() + QPoint(SHADOW + 4, SHADOW + 4)

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        self._typing.stop()       # nothing fires once it is hidden: the rows are not read again behind the back
        if self._placed:             # hidden (the ✕, the bar's button, the app closing): keep the place now
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["scroll_planner_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(),
                                                     "h": self.height()}
