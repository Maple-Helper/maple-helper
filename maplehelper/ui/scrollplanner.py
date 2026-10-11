"""The scroll planner's window over the game (the toolbar's Scrolls button opens it): pick a piece of gear, the stat
to scroll and how many of its upgrade slots to use, and it answers in plain words first — which grade is the safe
pick, what it adds and costs, what the riskier grades average and how rarely every slot passes, and what the prices
can't tell yet — then gives every grade the database confirms is in the game a card of its own: the scroll's
picture, its success rate and what one scroll adds, the expected total big beside the chance all n pass, the odds of
every final total as a small bar chart, and the cost (an NPC price from the database, the Free Market's usual price
from MeowDB, or the player's own typed price — never one made up; a grade without one says so, and the answer never
ranks it as if it had one).

Built like the other windows over the game (the crafting advisor, the searches): the NPCs window's dark glass at the
opacity and in the colours the ⚙ panel picks, the title row that drags it, cards in the same rounded fills, item
pictures shown large on hover, its place kept in scroll_planner_geom. It takes the keyboard while its boxes are used
(like the search window), shows while settings["scroll_planner"] is on, and its ✕ turns that off. The Free Market
lookups run off the GUI thread."""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from html import escape

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPixmap,
                           QRegularExpressionValidator)
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget)

from .. import bidi, itemterms, market, scrollplan
from ..i18n import NBSP, I18n
from ..kb import KnowledgeBase
from ..store import Profiles, Settings
from . import mapview, theme
from .gamesearch import GAP, QSS as _GLASS_QSS, TYPE_DEBOUNCE_MS
from .glass import SHADOW, EdgeResize
from .npcoverlay import (BOTTOM, FILL1, FILL2, GEOM_DEBOUNCE_MS, MUTED, OPACITY_DEFAULT, OPACITY_MAX, OPACITY_MIN,
                         SIDE, TEXT, TOP, _Header, _paint_glass, accent_of, set_capturable)
from .widgets import fit_picture, zoom_on_hover

DEFAULT_W, DEFAULT_H = 440, 720             # what opens under the toolbar before it is ever resized
MIN_W, MIN_H = 360, 420                     # the smallest the player can drag the window to
LIMIT = 12                                  # equips listed before the "and N more" line
UNKNOWN_CAP = 30                            # the stepper's ceiling for an equip whose slot count the KB lacks
ITEM_PIC, ROW_PIC, SCROLL_PIC = 44, 32, 32  # the picked equip's picture, a search row's, a grade card's scroll's
GUTTER = 4                                  # between the cards and the scrollbar
LOW_ODDS = 0.5                              # under this, "all 7 pass only 2.8% of the time"
SEGMENT_PAD = 24                            # a stat segment's padding around its (bold) word, as the QSS gives it
# a grade's success badge: how risky its scroll is reads at a glance, whatever accent the player picked
RISK_COLORS = {"sure": "#4CD07D", "risky": "#FFB066", "long": "#FF8A80"}
_MUTED = QColor(235, 235, 245, 163)         # MUTED for painting (a QColor can't read an "rgba(...)" string)


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})"


def qss(settings) -> str:
    """The window's own style at the accent the settings pick (npcoverlay.ACCENTS): the lit stat, the answer card,
    a typed price's tag and the set-price button; the glass itself is painted in the tint by _paint_glass. Rebuilt
    whenever the accent is picked, so the window follows with no more code of its own."""
    fill, on_fill = accent_of(settings)
    risks = "\n".join(f'QLabel#Badge[risk="{risk}"] {{ color: {c}; background: {_rgba(c, 0.16)}; }}\n'
                      f'QLabel#Dot[risk="{risk}"] {{ color: {c}; }}'
                      for risk, c in RISK_COLORS.items())
    return _GLASS_QSS + f"""
QLabel {{ background: transparent; }}
#Sub {{ color: {MUTED}; font-size: 12px; }}
#Note {{ color: {MUTED}; font-size: 11px; }}
#Hint {{ color: {MUTED}; padding: 8px 2px; }}
QFrame#ItemCard {{ background: {FILL1}; border: none; border-radius: 12px; }}
#ItemName {{ color: {TEXT}; font-size: 15px; font-weight: 700; }}
#SlotsNum {{ color: {TEXT}; font-size: 22px; font-weight: 700; }}
#SlotsWord {{ color: {MUTED}; font-size: 11px; }}
#Knob {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
#Stepper {{ background: {FILL1}; border: none; border-radius: 8px; }}
QToolButton#StepBtn {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px; padding: 0;
                       min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px; font-size: 15px;
                       font-weight: 600; }}
QToolButton#StepBtn:hover {{ background: {FILL2}; }}
QToolButton#StepBtn:disabled {{ color: rgba(235,235,245,0.28); }}
#StepNum {{ color: {TEXT}; font-size: 14px; font-weight: 700; min-width: 22px; }}
#Segmented {{ background: {FILL1}; border: none; border-radius: 10px; }}
QPushButton#Segment {{ background: transparent; color: {MUTED}; border: none; border-radius: 8px; min-height: 28px;
                       max-height: 28px; padding: 0 10px; font-size: 13px; font-weight: 600; text-align: center; }}
QPushButton#Segment:hover:!checked {{ background: {FILL2}; color: {TEXT}; }}
QPushButton#Segment:checked {{ background: {fill}; color: {on_fill}; font-weight: 700; }}
QFrame#Answer {{ background: {_rgba(fill, 0.15)}; border: 1px solid {_rgba(fill, 0.50)}; border-radius: 12px; }}
#AnswerTitle {{ color: {TEXT}; font-size: 15px; font-weight: 700; }}
#AnswerText {{ color: {TEXT}; font-size: 13px; }}
#AnswerNote {{ color: {MUTED}; font-size: 12px; }}
QLabel#Dot {{ font-size: 9px; padding-top: 4px; }}
QFrame#Grade {{ background: {FILL1}; border: none; border-radius: 12px; }}
#GradeName {{ color: {TEXT}; font-size: 14px; font-weight: 700; }}
QLabel#Badge {{ border-radius: 6px; padding: 1px 7px; font-size: 11px; font-weight: 700; }}
{risks}
#Big {{ color: {TEXT}; }}
#BigOdds {{ color: {TEXT}; font-size: 20px; font-weight: 700; }}
#BigLabel {{ color: {MUTED}; font-size: 11px; }}
#Caption {{ color: {MUTED}; font-size: 11px; }}
QFrame#Rule {{ background: rgba(255,255,255,0.08); border: none; }}
QLabel#Tag {{ color: {TEXT}; background: rgba(255,255,255,0.10); border-radius: 6px; padding: 2px 7px;
             font-size: 11px; font-weight: 600; }}
QLabel#Tag[kind="yours"] {{ color: {fill}; background: {_rgba(fill, 0.16)}; }}
QLabel#Tag[kind="none"], QLabel#Tag[kind="loading"], QLabel#Tag[kind="offline"] {{
             color: {MUTED}; background: transparent; padding: 2px 0; font-weight: 400; }}
QPushButton#PriceBtn {{ background: transparent; color: {TEXT}; border: none; border-radius: 6px; padding: 3px 6px;
                       font-size: 12px; font-weight: 600; text-align: center; }}
QPushButton#PriceBtn:hover {{ background: {FILL2}; }}
QPushButton#SetPrice {{ background: {_rgba(fill, 0.20)}; color: {TEXT}; border: none; border-radius: 6px;
                       padding: 3px 10px; font-size: 12px; font-weight: 600; text-align: center; }}
QPushButton#SetPrice:hover {{ background: {_rgba(fill, 0.34)}; }}
QLineEdit#PriceEdit {{ padding: 3px 8px; font-size: 12px; border-radius: 6px; }}
#Total {{ color: {TEXT}; font-size: 13px; font-weight: 700; }}
#PerPoint {{ color: {MUTED}; font-size: 11px; }}
QFrame#FoldHead {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#FoldHead:hover {{ background: {FILL1}; }}
#FoldTitle {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
#FoldIcon {{ color: {MUTED}; font-family: "{theme.ICON_FONT}"; font-size: 12px; }}
#FoldMark {{ color: {MUTED}; font-size: 13px; font-weight: 700; }}
"""


def _set_prop(w: QWidget, name: str, value) -> None:
    """A style property (a badge's risk, a tag's kind), the widget re-polished only when it changed."""
    if w.property(name) != value:
        w.setProperty(name, value)
        w.style().unpolish(w)
        w.style().polish(w)


def _pct(v: float) -> str:
    """1.0 -> '100%', 0.6 -> '60%', 0.6 ** 7 -> '2.8%', 0.0000001 -> '<0.1%', exactly 0 -> '0%': one decimal at
    most, never a 100% that isn't certain, never a true zero reading as a near miss."""
    if v >= 1.0:
        return "100%"
    if v >= 0.9995:
        return ">99.9%"
    if v > 0 and v < 0.001:
        return "<0.1%"
    return f"{v * 100:.1f}".removesuffix(".0") + "%"


def _exact(v: float) -> str:
    """A chance as exactly as a bar's hover says it: 29.03%, 0.17%, 0.00001% — two decimals, or two significant
    digits under 0.1%, and never a 100% that isn't certain."""
    if v >= 1.0:
        return "100%"
    pct = v * 100
    digits = 2 if pct >= 0.1 else max(2, 1 - math.floor(math.log10(pct))) if pct > 0 else 1
    s = f"{pct:.{digits}f}".rstrip("0").rstrip(".")
    return f"{'99.99' if s == '100' else s}%"


def _num(v: float) -> str:
    """70.0 -> '70', 4.2 -> '4.2': an expected gain with no trailing zero."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _mesos(v: float) -> str:
    return f"{round(v):,}"


def _risk(success: int) -> str:
    return "sure" if success >= 100 else "risky" if success >= 50 else "long"


def _whole(piece: str, rtl: bool) -> str:
    """A short English piece ('+70 Max HP', '105,000 mesos') that never wraps inside itself, one left-to-right block
    in a Hebrew line: wrapped, '105,000' ended one line and 'mesos.' began the next."""
    return bidi.ltr_block(piece.replace(" ", NBSP) if len(piece) <= bidi.KEEP_TOGETHER else piece, rtl)


def _say(text: str, rtl: bool) -> str:
    """A sentence of the answer for its label: bidi.plain, and a Hebrew prefix's hyphen kept with the block after
    it ('ב-' at one line's end, its number on the next)."""
    return bidi.plain(_JOIN.sub("-\u2060", text) if rtl else text, rtl)


_JOIN = re.compile(f"(?<=[\u05d0-\u05ea])-(?={bidi.LRI})")


def _big(value: str, unit: str, rtl: bool) -> str:
    """'+63 Max HP' as a card shows it: the number big, its stat after it a size down, one left-to-right block in a
    Hebrew line (as two runs it read 'Max HP +63'), the line itself right to left there (an RLM first: with the block
    alone the paragraph read as English and sat on the left of its column)."""
    html = f'<span style="font-size:20px; font-weight:700">{escape(value)}</span>'
    if unit:
        html += f'<span style="font-size:13px; font-weight:600">&nbsp;{escape(unit)}</span>'
    return f"{bidi.RLM}{bidi.LRI}{html}{bidi.PDI}{bidi.RLM}" if rtl else html


class _Pictures:
    """Each picture fitted once per size (the search rows are made again on every keystroke, the cards on every
    pick and stat), read from disk and scaled once, as the crafting windows' own cache does."""

    def __init__(self, kb):
        self._kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple[str, int, float], QPixmap] = {}

    def show(self, pic: QLabel, key: str, size: int) -> None:
        """This KB key's picture in a fixed square label, large on hover (no key: the square left empty)."""
        pic.setFixedSize(size, size)
        pic.setAlignment(Qt.AlignCenter)
        pic.clear()
        pic.setToolTip("")
        if not key:
            return
        if key not in self._paths:
            self._paths[key] = self._kb.picture(key)
        path = self._paths[key]
        if not path:
            return
        dpr = pic.devicePixelRatioF()
        pm = self._fitted.get((key, size, dpr))
        if pm is None:
            raw = QPixmap(str(path))
            pm = fit_picture(raw, size, size, pic) if not raw.isNull() else QPixmap()
            self._fitted[(key, size, dpr)] = pm
        if not pm.isNull():
            pic.setPixmap(pm)
            zoom_on_hover(pic, path)

    def label(self, key: str, size: int) -> QLabel:
        pic = QLabel(objectName="Pic")
        self.show(pic, key, size)
        return pic


class _Box(QLineEdit):
    """The typing box: Esc drops the results and goes back to the plan (the keyboard goes back to the game at
    once); with nothing typed it closes the window, like the ✕."""

    escaped = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(e)


class _PriceBox(QLineEdit):
    """A grade's price box: Enter or a click elsewhere keeps what is typed, Esc puts back what was there."""

    finished = Signal()
    cancelled = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.finished.emit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.isVisible():
            self.finished.emit()


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
            b.setFocusPolicy(Qt.NoFocus)
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


class _StatPicker(QFrame):
    """The stats an equip's scrolls add as one segmented capsule, the chosen one lit in the accent: on one row when
    they fit, else in as few rows as the width allows, the segments shared out evenly (a cape's seven: four and three,
    never six and one left alone), each row's spare width shared among its segments. Placed by hand, not by a
    layout: a grid's own height hint won over the rows this asks for, and the second row was drawn over the first."""

    changed = Signal(int)
    PAD, GAP, ROW = 2, 2, 28            # the capsule's rim, between the segments, a segment's height (the QSS's)

    def __init__(self, words: list[str], tips: list[str], current: int):
        super().__init__(objectName="Segmented")
        sp = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)      # (as a wrapping label's: its rows by hfw)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: list[QPushButton] = []
        for i, (word, tip) in enumerate(zip(words, tips)):
            b = QPushButton(word, self, objectName="Segment")
            b.setCheckable(True)
            b.setChecked(i == current)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tip)
            b.setAccessibleDescription(tip)
            self.group.addButton(b, i)
            self.buttons.append(b)
        self.group.idClicked.connect(self.changed.emit)

    def _widths(self) -> list[int]:
        """Each segment's width when lit (its word in bold, or the lit one loses a letter)."""
        out = []
        for b in self.buttons:
            b.ensurePolished()
            bold = QFont(b.font())
            bold.setBold(True)
            out.append(QFontMetrics(bold).horizontalAdvance(b.text()) + SEGMENT_PAD)
        return out

    @staticmethod
    def _split(n: int, rows: int) -> list[int]:
        """n segments over this many rows, as evenly as can be, the fuller rows first (7 over 2: 4 and 3)."""
        return [n // rows + (1 if r < n % rows else 0) for r in range(rows)]

    def _need(self, widths: list[int]) -> int:
        return sum(widths) + self.GAP * (len(widths) - 1) + 2 * self.PAD

    def _rows_for(self, width: int) -> list[list[int]]:
        """The segments' indexes row by row: the fewest rows whose every row fits this width."""
        n = len(self.buttons)
        widths = self._widths()
        for rows in range(1, n + 1):
            out, at = [], 0
            for size in self._split(n, rows):
                out.append(list(range(at, at + size)))
                at += size
            if rows == n or all(self._need([widths[i] for i in row]) <= width for row in out):
                return out
        return [[i] for i in range(n)]

    def _height(self, rows: int) -> int:
        return rows * self.ROW + (rows - 1) * self.GAP + 2 * self.PAD

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._height(len(self._rows_for(w))) if self.buttons else 0

    def sizeHint(self) -> QSize:
        widths = self._widths()
        return QSize(self._need(widths) if widths else 0, self._height(1))

    def minimumSizeHint(self) -> QSize:
        return QSize(max(self._widths(), default=0) + 2 * self.PAD, self._height(1))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._arrange()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._arrange()

    def _arrange(self) -> None:
        """Each row's segments side by side from the reading side (the right, right to left), each grown alike
        into its row's spare width."""
        if not self.buttons:
            return
        width = self.width()
        widths = self._widths()
        rtl = self.layoutDirection() == Qt.RightToLeft
        for r, row in enumerate(self._rows_for(width)):
            spare = max(0, width - self._need([widths[i] for i in row]))
            x, y = self.PAD, self.PAD + r * (self.ROW + self.GAP)
            for j, i in enumerate(row):
                w = widths[i] + spare // len(row) + (1 if j < spare % len(row) else 0)
                self.buttons[i].setGeometry(QRect(width - x - w if rtl else x, y, w, self.ROW))
                x += w + self.GAP


@dataclass(frozen=True)
class Bar:
    """One final total a grade's slots can end on (a total that can't happen has no bar)."""
    chance: float               # the exact chance of ending on it, never 0
    label: str                  # under the bar, its total: '+60'
    tip: str                    # on hover: its exact chance, and the chance of it or more


class _Outcomes(QWidget):
    """A grade's outcomes as a small bar chart: a bar per final total, as tall as its chance (the likeliest the
    tallest), the likeliest lit in the accent with its chance over it, a tiny chance still a sliver, the totals under
    them (thinned out when they would touch); hovering a bar tells its exact chance. Right to left, the totals run
    from the right."""

    TOP, BARS, LABELS = 16, 42, 16          # the likeliest bar's chance over it, the tallest bar, the totals under
    MIN_BAR = 2.0                           # the least a bar is drawn tall: a 0.01% chance still shows
    BAR_W = 26                              # the widest a bar gets

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setFixedHeight(self.TOP + self.BARS + self.LABELS)
        self.setMinimumWidth(120)
        self.bars: list[Bar] = []
        self._accent = QColor(theme.GOOD_TEXT_LIGHT)
        self._hover = -1

    def set_bars(self, bars: list[Bar]) -> None:
        self.bars = list(bars)
        self._hover = -1
        self.setAccessibleDescription("; ".join(f"{b.label}: {_pct(b.chance)}" for b in self.bars))
        self.update()

    def set_accent(self, color: QColor) -> None:
        self._accent = QColor(color)
        self.update()

    def peak(self) -> int:
        """The likeliest total's bar (the first of a tie); -1 with no bars."""
        return max(range(len(self.bars)), key=lambda i: self.bars[i].chance) if self.bars else -1

    def column(self, i: int) -> QRectF:
        """Bar i's column, the totals running from the reading side (the right, right to left)."""
        n = len(self.bars)
        w = self.width() / n
        j = n - 1 - i if self.layoutDirection() == Qt.RightToLeft else i
        return QRectF(j * w, 0, w, self.height())

    def bar_rect(self, i: int) -> QRectF:
        """Bar i as drawn: as tall as its chance against the likeliest's, never under MIN_BAR (a sliver still
        shows), centred in its column, standing on the baseline."""
        col = self.column(i)
        top = self.bars[self.peak()].chance
        bw = min(self.BAR_W, max(3.0, col.width() * 0.6))
        h = max(self.MIN_BAR, self.bars[i].chance / top * self.BARS) if top > 0 else self.MIN_BAR
        return QRectF(col.center().x() - bw / 2, self.TOP + self.BARS - h, bw, h)

    def _at(self, x: float) -> int:
        n = len(self.bars)
        if not n or self.width() <= 0:
            return -1
        j = min(n - 1, max(0, int(x / (self.width() / n))))
        return n - 1 - j if self.layoutDirection() == Qt.RightToLeft else j

    def _labelled(self, fm: QFontMetrics) -> set[int]:
        """The bars whose total is written under them: all of them when they fit, else the likeliest, the two
        ends and evenly spaced ones between that keep clear of each other."""
        n = len(self.bars)
        if not n:
            return set()
        col = self.width() / n
        widest = max(fm.horizontalAdvance(b.label) for b in self.bars) + 6
        if widest <= col:
            return set(range(n))
        step = math.ceil(widest / col) if col > 0 else n
        out: list[int] = []
        for i in (self.peak(), 0, n - 1, *range(0, n, step)):
            if i not in out and all(abs(i - j) >= step for j in out):
                out.append(i)
        return set(out)

    def paintEvent(self, e) -> None:
        if not self.bars:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setLayoutDirection(Qt.LeftToRight)       # "+60" and "29%" read left to right in Hebrew too (not "60+")
        peak = self.peak()
        base = self.TOP + self.BARS
        p.fillRect(QRectF(0, base, self.width(), 1), QColor(255, 255, 255, 36))
        small = QFont(self.font())
        small.setPixelSize(10)
        bold = QFont(small)
        bold.setBold(True)
        labelled = self._labelled(QFontMetrics(bold))
        for i, b in enumerate(self.bars):
            rect = self.bar_rect(i)
            color = QColor(self._accent) if i == peak else QColor(255, 255, 255, 120 if i == self._hover else 66)
            if i == peak and i == self._hover:
                color = color.lighter(120)
            path = QPainterPath()
            radius = min(3.0, rect.width() / 2, rect.height() / 2)
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, color)
            if i in labelled:
                font = bold if i == peak else small
                p.setFont(font)
                p.setPen(QColor(TEXT) if i == peak else _MUTED)
                self._text(p, QFontMetrics(font), b.label, rect.center().x(), base + 2, self.LABELS - 2, top_align=True)
        p.setFont(bold)
        p.setPen(QColor(TEXT))
        self._text(p, QFontMetrics(bold), _pct(self.bars[peak].chance), self.column(peak).center().x(), 0, self.TOP - 2)
        p.end()

    def _text(self, p: QPainter, fm: QFontMetrics, text: str, cx: float, y: float, h: float,
              top_align: bool = False) -> None:
        """Text centred on x, kept inside the chart at its ends."""
        tw = fm.horizontalAdvance(text) + 2
        x = min(max(0.0, cx - tw / 2), max(0.0, self.width() - tw))
        p.drawText(QRectF(x, y, tw, h), Qt.AlignHCenter | (Qt.AlignTop if top_align else Qt.AlignBottom), text)

    def mouseMoveEvent(self, e) -> None:
        i = self._at(e.position().x())
        if i != self._hover:
            self._hover = i
            self.update()
        if i >= 0:
            QToolTip.showText(e.globalPosition().toPoint(), self.bars[i].tip, self)
        super().mouseMoveEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = -1
        self.update()
        QToolTip.hideText()
        super().leaveEvent(e)


class _ItemHead(QFrame):
    """The picked equip: its picture (large on hover), its name, its slot word and level, and its clean upgrade
    slots big on the end."""

    def __init__(self):
        super().__init__(objectName="ItemCard")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 10, 14, 10)
        h.setSpacing(12)
        self.pic = QLabel(objectName="Pic")
        self.pic.setFixedSize(ITEM_PIC, ITEM_PIC)
        h.addWidget(self.pic, 0, Qt.AlignVCenter)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        mid.addStretch(1)
        self.name = QLabel(objectName="ItemName", wordWrap=True)
        self.sub = QLabel(objectName="Sub", wordWrap=True)
        mid.addWidget(self.name)
        mid.addWidget(self.sub)
        mid.addStretch(1)
        h.addLayout(mid, 1)
        end = QVBoxLayout()
        end.setSpacing(0)
        right = Qt.AlignRight | Qt.AlignVCenter
        self.slots_num = QLabel(objectName="SlotsNum", alignment=right)
        self.slots_word = QLabel(objectName="SlotsWord", alignment=right)
        end.addStretch(1)
        end.addWidget(self.slots_num)
        end.addWidget(self.slots_word)
        end.addStretch(1)
        h.addLayout(end)


class _PickRow(QFrame):
    """A search result: the equip's picture, its name, and its slot word, level and upgrade slots under it."""

    clicked = Signal()

    def __init__(self, pic: QLabel, name: str, sub: str, tip: str):
        super().__init__(objectName="Row")
        self.setAttribute(Qt.WA_Hover, True)          # its QSS :hover
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 6, 10, 6)
        h.setSpacing(10)
        self.pic = pic
        h.addWidget(pic, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.name = QLabel(name, objectName="Name", wordWrap=True)
        self.sub = QLabel(sub, objectName="Sub", wordWrap=True)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        h.addLayout(col, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _GradeCard(QFrame):
    """One grade's card: its scroll's picture, its name with its success rate as a badge and what one scroll adds;
    the expected total big beside the chance every slot passes; the outcomes chart; then the cost, two by two — where
    the price comes from as a small tag over the price per scroll (a click types your own), the expected total over
    the cost per +1 on the end — or, with no price, a set-price button that opens a price box. The window fills in
    its texts (_paint_card), so a price that arrives or a stepper turn only rewrites them, and a price being typed
    is never interrupted."""

    price_typed = Signal(str, str)      # (its scroll's KB key, the text in its price box)
    edit_done = Signal()                # the price box closed: the row shows the price (or the button) again

    def __init__(self, s: scrollplan.Scroll, pics: _Pictures):
        super().__init__(objectName="Grade")
        self.scroll = s
        self.editing = False
        self.typed: int | None = None    # the player's own price for it, the price box's text when it opens
        self._before: int | None = None
        self._quiet = False
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.pic = pics.label(s.key, SCROLL_PIC)
        top.addWidget(self.pic, 0, Qt.AlignVCenter)
        who = QVBoxLayout()
        who.setSpacing(1)
        line = QHBoxLayout()
        line.setSpacing(6)
        self.name = QLabel(objectName="GradeName")
        self.badge = QLabel(objectName="Badge")
        line.addWidget(self.name)
        line.addWidget(self.badge, 0, Qt.AlignVCenter)
        line.addStretch(1)
        who.addLayout(line)
        self.each = QLabel(objectName="Sub", wordWrap=True)
        who.addWidget(self.each)
        top.addLayout(who, 1)
        v.addLayout(top)
        nums = QHBoxLayout()
        nums.setSpacing(10)
        gain = QVBoxLayout()
        gain.setSpacing(0)
        self.expected = QLabel(objectName="Big", textFormat=Qt.RichText)
        self.expected_word = QLabel(objectName="BigLabel", wordWrap=True)
        gain.addWidget(self.expected)
        gain.addWidget(self.expected_word)
        nums.addLayout(gain, 1)
        odds = QVBoxLayout()
        odds.setSpacing(0)
        end = Qt.AlignRight | Qt.AlignBottom
        self.odds = QLabel(objectName="BigOdds", alignment=end)
        self.odds_word = QLabel(objectName="BigLabel", alignment=Qt.AlignRight | Qt.AlignTop)
        odds.addWidget(self.odds)
        odds.addWidget(self.odds_word)
        nums.addLayout(odds)
        v.addLayout(nums)
        self.caption = QLabel(objectName="Caption")
        v.addWidget(self.caption)
        self.chart = _Outcomes()
        v.addWidget(self.chart)
        rule = QFrame(objectName="Rule")
        rule.setFixedHeight(1)
        v.addWidget(rule)
        cost = QGridLayout()
        cost.setContentsMargins(0, 0, 0, 0)
        cost.setHorizontalSpacing(8)
        cost.setVerticalSpacing(3)
        self.tag = QLabel(objectName="Tag")
        self.price_btn = QPushButton(objectName="PriceBtn")
        # the pencil in the icon font, not "✎" in the text: inside a Hebrew line it fell back to no font (a box)
        self.price_btn.setIcon(theme.glyph_icon("edit", MUTED, 12))
        self.set_btn = QPushButton(objectName="SetPrice")
        self.edit = _PriceBox(objectName="PriceEdit")
        self.edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[\d,]{0,13}"), self.edit))
        self.edit.setFixedWidth(118)
        self.edit.hide()
        for b in (self.price_btn, self.set_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(self.start_edit)
        self.edit.textChanged.connect(self._typed)
        self.edit.finished.connect(self.end_edit)
        self.edit.cancelled.connect(self._cancel)
        start, end = Qt.AlignLeft | Qt.AlignVCenter, Qt.AlignRight | Qt.AlignVCenter
        self.total = QLabel(objectName="Total", alignment=end)
        self.per_point = QLabel(objectName="PerPoint", alignment=end)
        cost.addWidget(self.tag, 0, 0, start)
        cost.addWidget(self.total, 0, 2, end)
        price = QHBoxLayout()                 # the price, its button or its box: one at a time
        price.setContentsMargins(0, 0, 0, 0)
        price.setSpacing(0)
        for w in (self.price_btn, self.set_btn, self.edit):
            price.addWidget(w)
        cost.addLayout(price, 1, 0, start)
        cost.addWidget(self.per_point, 1, 2, end)
        cost.setColumnStretch(1, 1)
        v.addLayout(cost)

    def start_edit(self, _=False) -> None:
        """The price (or Set price) clicked: a price box in its place, holding the player's own price if they gave
        one, the keyboard in it."""
        self.editing = True
        self._before = self.typed
        self._quiet = True
        self.edit.setText(f"{self.typed:,}" if self.typed is not None else "")
        self._quiet = False
        self.price_btn.hide()
        self.set_btn.hide()
        self.edit.show()
        self.edit.setFocus(Qt.MouseFocusReason)
        self.edit.selectAll()

    def end_edit(self) -> None:
        if not self.editing:
            return
        self.editing = False
        self.edit.clearFocus()          # hidden with the focus, Qt moved it on to the next control (the ✕, ringed)
        self.edit.hide()
        self.edit_done.emit()

    def _cancel(self) -> None:
        """Esc in the price box: the price it had before it opened, the box closed."""
        self.price_typed.emit(self.scroll.key, f"{self._before}" if self._before is not None else "")
        self.end_edit()

    def _typed(self, text: str) -> None:
        if not self._quiet:
            self.price_typed.emit(self.scroll.key, text)


class _Answer(QFrame):
    """The plain answer over the cards, lit in the accent: the verdict in one line, a sentence per grade (a dot in
    its badge's colour before it, as on its card), then what the prices can and can't tell."""

    def __init__(self):
        super().__init__(objectName="Answer")
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 10, 14, 11)
        v.setSpacing(6)
        self.title = QLabel(objectName="AnswerTitle", wordWrap=True)
        v.addWidget(self.title)
        self._lines_lay = QVBoxLayout()
        self._lines_lay.setContentsMargins(0, 0, 0, 0)
        self._lines_lay.setSpacing(3)
        v.addLayout(self._lines_lay)
        self.note = QLabel(objectName="AnswerNote", wordWrap=True)
        v.addWidget(self.note)
        self.lines: list[QLabel] = []
        self._rows: list[tuple[QWidget, QLabel]] = []

    def set_lines(self, lines: list[tuple[str, str]]) -> None:
        """(risk, text) per sentence: the rows reused, made or dropped to match."""
        while len(self._rows) > len(lines):
            row, _ = self._rows.pop()
            self._lines_lay.removeWidget(row)
            row.hide()
            row.deleteLater()
        while len(self._rows) < len(lines):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(7)
            dot = QLabel("\u25cf", objectName="Dot")
            h.addWidget(dot, 0, Qt.AlignTop)
            h.addWidget(QLabel(objectName="AnswerText", wordWrap=True), 1)
            self._lines_lay.addWidget(row)
            self._rows.append((row, dot))
        for (row, dot), (risk, text) in zip(self._rows, lines):
            _set_prop(dot, "risk", risk)
            row.layout().itemAt(1).widget().setText(text)
        self.lines = [row.layout().itemAt(1).widget() for row, _ in self._rows]


class _FoldHead(QFrame):
    clicked = Signal()

    def __init__(self):
        super().__init__(objectName="FoldHead")
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(6)
        self.icon = QLabel(theme.ICON["info"], objectName="FoldIcon")      # its own label: "ⓘ" in a Hebrew line was a box
        self.title = QLabel(objectName="FoldTitle")
        self.mark = QLabel(objectName="FoldMark")
        row.addWidget(self.icon)
        row.addWidget(self.title, 1)
        row.addWidget(self.mark)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _About(QWidget):
    """"ⓘ About these numbers": a muted line under the cards that opens the notes (where the numbers come from,
    why no Chaos scrolls, how long a typed price is kept) — folded, they stay out of the way of the answer."""

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self.head = _FoldHead()
        self.head.clicked.connect(self.toggle)
        v.addWidget(self.head)
        self.body = QLabel(objectName="Note", wordWrap=True)
        self.body.setContentsMargins(8, 0, 8, 4)
        self.body.hide()
        v.addWidget(self.body)
        self._mark()

    def toggle(self) -> None:
        self.body.setVisible(not self.body.isVisibleTo(self))
        self._mark()

    def _mark(self) -> None:
        self.head.mark.setText("\u2013" if self.body.isVisibleTo(self) else "+")


class _Body(QWidget):
    """The scroll area's content: whenever its rows change or it is resized it asks for the height they need at its
    width — Qt's own word-wrap hints lag a change inside a scroll area, and cards drew over each other until some
    later repaint nudged them apart."""

    def event(self, e) -> bool:
        done = super().event(e)
        if e.type() in (QEvent.LayoutRequest, QEvent.Resize):
            self.fit()
        return done

    def fit(self) -> None:
        lay = self.layout()
        if lay is None:
            return
        need = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        if need > 0 and self.minimumHeight() != need:
            self.setMinimumHeight(need)


class ScrollPlanner(EdgeResize, QWidget):
    """The window itself: over the game, in the overlays' dark glass, taking the keyboard while its boxes are used.
    A picked equip (or, until the player picks one, the active character's first scrollable worn one) shows its
    header, the slots-to-use stepper and the stat to scroll, the plain answer, and a card per grade. A price the
    player types is kept for the window's life (until another equip is picked), never saved."""

    closed = Signal()               # the ✕ (or Esc on an empty box) turned settings["scroll_planner"] off
    market_ready = Signal(object)   # a Free Market lookup's answer, from its own thread

    def __init__(self, kb: KnowledgeBase, settings: Settings, profiles: Profiles, t: I18n):
        # the search window's flags, not the NPCs window's: this one takes the keyboard while its boxes are used
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self.setWindowTitle("Maple Helper")
        self._kb, self._settings, self._profiles, self.t = kb, settings, profiles, t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._pics = _Pictures(kb)
        self._picked: scrollplan.EquipScrolls | None = None    # the player's own pick, kept across switches
        self._default: scrollplan.EquipScrolls | None = None   # what the active character wears, until they pick
        self._stat = 0                     # the chosen stat group, an index into the equip's groups
        self._n = 1                        # the slots to use
        self._your: dict[str, int] = {}    # a typed price per scroll's KB key, this window's life only
        self._fm: dict[str, int | None] = {}   # a Free Market usual price per scroll key (None: none on the site)
        self._fm_bad: set[str] = set()     # scroll keys whose lookup couldn't reach the site at all
        self._fm_seen: set[str] = set()    # scroll keys a thread is already fetching
        self._fm_read = 0                  # the KB swaps so far: an answer asked for under an older KB is dropped
        self._opacity = OPACITY_DEFAULT
        self._accent = QColor(accent_of(settings)[0])
        self._placed = False               # geometry applied at least once: there is a place to remember
        self._under: QRect | None = None   # the toolbar's rect, where the window first opens
        self._rows: list[_PickRow] = []
        self._cards: list[_GradeCard] = []
        self._picker: _StatPicker | None = None
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._typing = QTimer(self, singleShot=True, interval=TYPE_DEBOUNCE_MS, timeout=self._run)
        self.setStyleSheet(qss(settings))
        self._build()
        # what the character wears, read now: read first on a later refresh, it counted as the worn gear changing
        # and dropped the item the gear advisor's "Plan scrolls" had just opened
        self._sync_default()
        self._show_plan()

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
        self._body = _Body(objectName="Rows")
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setSpacing(4)
        self._results = QWidget(objectName="Rows")
        self._results_lay = QVBoxLayout(self._results)
        self._results_lay.setContentsMargins(0, 0, 0, 0)
        self._results_lay.setSpacing(4)
        self._body_lay.addWidget(self._results)
        self._plan = QWidget(objectName="Rows")
        self._plan_lay = QVBoxLayout(self._plan)
        self._plan_lay.setContentsMargins(0, 0, 0, 0)
        self._plan_lay.setSpacing(10)
        self._body_lay.addWidget(self._plan)
        self._body_lay.addStretch(1)
        self._scroll.setWidget(self._body)
        col.addWidget(self._scroll, 1)
        self._build_plan()
        self.market_ready.connect(self._on_market)
        self._gutter()
        self._retext()

    def _build_plan(self) -> None:
        """The plan half of the body: the equip's header, the stepper and the stat choice, the answer, the grade
        cards (made per pick and stat), the notes folded under them, and the hint shown with nothing to plan."""
        self._item = _ItemHead()
        self._plan_lay.addWidget(self._item)
        self._knobs = QWidget()
        grid = QGridLayout(self._knobs)
        grid.setContentsMargins(2, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self._use_label = QLabel(objectName="Knob", alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self._use_label.setMinimumHeight(32)
        self._spin = _Stepper()
        self._spin.changed.connect(self._n_changed)
        self._of = QLabel(objectName="Sub")
        use = QHBoxLayout()
        use.setSpacing(8)
        use.addWidget(self._spin)
        use.addWidget(self._of)
        use.addStretch(1)
        grid.addWidget(self._use_label, 0, 0, Qt.AlignTop)
        grid.addLayout(use, 0, 1)
        self._stat_label = QLabel(objectName="Knob", alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self._stat_label.setMinimumHeight(32)
        grid.addWidget(self._stat_label, 1, 0, Qt.AlignTop)
        self._stat_slot = QVBoxLayout()
        self._stat_slot.setContentsMargins(0, 0, 0, 0)
        grid.addLayout(self._stat_slot, 1, 1)
        grid.setColumnStretch(1, 1)
        self._plan_lay.addWidget(self._knobs)
        self._answer = _Answer()
        self._plan_lay.addWidget(self._answer)
        self._cards_lay = QVBoxLayout()
        self._cards_lay.setContentsMargins(0, 0, 0, 0)
        self._cards_lay.setSpacing(8)
        self._plan_lay.addLayout(self._cards_lay)
        self._about = _About()
        self._plan_lay.addWidget(self._about)
        self._hint = QLabel(objectName="Hint", wordWrap=True)
        self._plan_lay.addWidget(self._hint)

    def _gutter(self) -> None:
        """The body keeps a little room on its scrollbar's side (right, or left in Hebrew, where Qt moves it)."""
        rtl = self.t.rtl
        self._body_lay.setContentsMargins(GUTTER if rtl else 0, 0, 0 if rtl else GUTTER, 0)

    # ------------------------------------------------------------ shown, hidden, replaced

    def refresh(self) -> None:
        """Shown while settings["scroll_planner"] is on (the bar's button flips it): placed where the player left it
        or under the bar, in the look the ⚙ panel picks (accent, tint, opacity), out of screen captures, the active
        character read."""
        want = bool(self._settings["scroll_planner"])
        if not want:
            if self.isVisible():
                self.hide()
            return
        self._restyle()
        self._read_opacity()              # the ⚙ panel or Settings may change the look while it shows
        if not self.isVisible():
            self._place()
            self.show()
            set_capturable(self, self._settings)
        if self._sync_default():
            self._picked = None        # nothing of the player's own: the worn gear replaced
            self._show_plan()
        self._paint_cards()
        self.update()                     # the glass repaints in the tint the settings now give

    def _restyle(self) -> None:
        """The accent the settings pick, in the window's style sheet and its charts' likeliest bars; the style sheet
        is set again only when it changed (a restyle re-polishes every card)."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        accent = QColor(accent_of(self._settings)[0])
        if accent != self._accent:
            self._accent = accent
            for card in self._cards:
                card.chart.set_accent(accent)

    def open_item(self, key: str) -> None:
        """Jump straight to one equip (the gear advisor's 'plan scrolls for this'): picked, shown, the setting
        on. A key the KB doesn't know as an equip only says so in the status line."""
        es = scrollplan.scrolls_for(self._kb, key)
        if es is None:
            self._status.setText(bidi.plain(self.t("sp_not_equip"), self.t.rtl))
            self._status.show()
            return
        self._pick(es)
        self._settings["scroll_planner"] = True
        if not self.isVisible():
            self._restyle()
            self._read_opacity()
            self._place()
            self.show()
            set_capturable(self, self._settings)
        self.raise_()
        self.activateWindow()

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A KB update swapped the KB: everything is read again, the market answers of the old one dropped."""
        self._kb = kb
        self._pics = _Pictures(kb)
        self._fm.clear()
        self._fm_bad.clear()
        self._fm_seen.clear()
        self._fm_read += 1
        if self._picked is not None:
            again = scrollplan.scrolls_for(kb, self._picked.key)
            self._picked = again or None
        self._default = None
        self._sync_default()
        self._show_plan(keep=True)
        if self._box.text():
            self._run()

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
        """Every word again in the new language, right to left in Hebrew: the plan drawn again as it stood (the
        stat, the slots to use and the typed prices kept), and the results if a search is typed."""
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._gutter()
        self._retext()
        self._show_plan(keep=True)
        if self._box.text():
            self._run()

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
            row = _PickRow(self._pics.label(es.key, ROW_PIC), bidi.ltr_name(es.item, rtl),
                           bidi.plain(self._equip_line(es, slots=True), rtl), t("sp_pick_tip"))
            row.clicked.connect(lambda e=es: self._pick(e))
            self._rows.append(row)
            self._results_lay.addWidget(row)
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
        self._status.setVisible(bool(text))

    def _clear_rows(self) -> None:
        while self._rows:
            w = self._rows.pop()
            self._results_lay.removeWidget(w)
            w.hide()
            w.deleteLater()

    def _equip_line(self, es: scrollplan.EquipScrolls, slots: bool) -> str:
        """'Hat · Lv. 5' (a search row adds '· 7 upgrade slots'): the slot word in the player's language (itemterms),
        the level in the app's own words, a slot count the KB lacks said so."""
        t = self.t
        bits = [itemterms.kind(es.slot, t.lang)] if es.slot else []
        if es.req_lv:
            bits.append(t("lv_short", n=es.req_lv))
        if es.slots is None:
            bits.append(t("sp_slots_none"))
        elif slots:
            bits.append(t("sp_slots_one") if es.slots == 1 else t("sp_slots", n=bidi.ltr_block(f"{es.slots}", t.rtl)))
        return " · ".join(bits)

    def _pick(self, es: scrollplan.EquipScrolls) -> None:
        """The equip to plan: picked, the stepper set to its clean slots, the first stat group chosen, the
        typed prices of the last equip dropped."""
        self._picked = es
        self._stat = 0
        self._your.clear()
        self._box.blockSignals(True)       # the results go now, not after the typing debounce
        self._box.clear()
        self._box.blockSignals(False)
        self._typing.stop()
        self._show_plan()

    # ------------------------------------------------------------ the plan

    def _equip(self) -> scrollplan.EquipScrolls | None:
        return self._picked or self._default

    def _group(self) -> scrollplan.StatGroup | None:
        es = self._equip()
        if es is None or not es.groups or not 0 <= self._stat < len(es.groups):
            return None
        return es.groups[self._stat]

    def _show_plan(self, keep: bool = False) -> None:
        """The plan half drawn for the equip in hand (the player's pick, else the character's worn one): its header,
        the stepper at its clean slots (keep: the slots the player chose, a language switch), the stats, the cards."""
        t, rtl = self.t, self.t.rtl
        es = self._equip()
        self._clear_rows()
        self._results.hide()
        self._status.setText("")
        self._status.hide()
        self._plan.show()
        self._clear_cards()
        has = es is not None and bool(es.groups)
        self._item.setVisible(es is not None)
        for w in (self._knobs, self._answer, self._about):
            w.setVisible(has)
        if es is not None:
            self._paint_head(es)
        if not has:
            self._build_stats()
            self._hint.setText(bidi.plain(t("sp_no_scrolls" if es is not None else "sp_pick"), rtl))
            self._hint.show()
            return
        self._hint.hide()
        cap = es.slots if isinstance(es.slots, int) and es.slots >= 0 else UNKNOWN_CAP
        self._spin.blockSignals(True)     # the cards are made below, once
        self._spin.setRange(min(1, cap), cap)
        self._spin.setValue(self._n if keep else (es.slots if es.slots is not None else 1))
        self._spin.blockSignals(False)
        self._n = self._spin.value()
        self._of.setText(bidi.plain(t("sp_of", n=bidi.ltr_block(str(cap), rtl)), rtl) if es.slots is not None else "")
        self._of.setVisible(es.slots is not None)
        self._build_stats()
        self._rebuild_cards()

    def _paint_head(self, es: scrollplan.EquipScrolls) -> None:
        """The equip in hand: its picture, name, slot word and level, and its clean upgrade slots on the end."""
        t, rtl = self.t, self.t.rtl
        self._pics.show(self._item.pic, es.key, ITEM_PIC)
        self._item.name.setText(bidi.ltr_name(es.item, rtl))
        sub = self._equip_line(es, slots=False)
        self._item.sub.setText(bidi.plain(sub, rtl) if sub else "")
        self._item.sub.setVisible(bool(sub))
        known = isinstance(es.slots, int)
        # a number alone is a right-to-left line in Hebrew (ltr_name): as a bare block Qt aligned it as English
        self._item.slots_num.setText(bidi.ltr_name(str(es.slots), rtl) if known else "")
        self._item.slots_word.setText(bidi.plain(t("sp_slots_word", n=es.slots), rtl) if known else "")
        self._item.slots_num.setVisible(known)
        self._item.slots_word.setVisible(known)

    def _build_stats(self) -> None:
        """The equip's stats as a segmented choice, the chosen one lit; hovering one says what each grade adds."""
        if self._picker is not None:
            self._stat_slot.removeWidget(self._picker)
            self._picker.hide()
            self._picker.deleteLater()
            self._picker = None
        es = self._equip()
        groups = es.groups if es is not None else ()
        if not groups:
            return
        t, rtl = self.t, self.t.rtl
        self._stat = min(self._stat, len(groups) - 1)
        tips = []
        for g in groups:
            adds = " · ".join(f"{s.grade} " + ", ".join(f"+{v} {label}" for label, v in s.gains) for s in g.grades)
            tips.append(bidi.plain(t("sp_stat_tip", list=bidi.ltr_block(adds, rtl)), rtl))
        self._picker = _StatPicker([bidi.ltr_name(g.stat, rtl) for g in groups], tips, self._stat)
        self._picker.setAccessibleName(t("sp_stat"))
        self._picker.changed.connect(self._stat_changed)
        if len(groups) == 1:           # one stat: a segment as wide as its word, not a bar across the window
            self._stat_slot.addWidget(self._picker, 0, Qt.AlignLeft | Qt.AlignVCenter)
            self._picker.setMaximumWidth(self._picker.sizeHint().width())     # (measured in the window's style)
        else:
            self._stat_slot.addWidget(self._picker)

    def _n_changed(self, n: int) -> None:
        self._n = n
        self._paint_cards()

    def _stat_changed(self, i: int) -> None:
        self._stat = i
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        """One card per grade of the chosen stat (their texts filled by _paint_cards, so a price that arrives or a
        stepper turn only rewrites them — a price being typed is never interrupted)."""
        self._clear_cards()
        group = self._group()
        if group is None:
            return
        for s in group.grades:
            card = _GradeCard(s, self._pics)
            card.price_typed.connect(self._your_changed)
            card.edit_done.connect(self._paint_cards)
            card.chart.set_accent(self._accent)
            self._cards.append(card)
            self._cards_lay.addWidget(card)
        self._paint_cards()

    def _clear_cards(self) -> None:
        while self._cards:
            c = self._cards.pop()
            self._cards_lay.removeWidget(c)
            c.hide()
            c.deleteLater()

    def _your_changed(self, key: str, text: str) -> None:
        m = re.fullmatch(r"\s*([\d,]*\d[\d,]*)\s*", text or "")
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

    def _price_kind(self, s: scrollplan.Scroll) -> str:
        """Where a grade's price comes from: 'yours', 'npc', 'fm'; or why it has none: 'none' (the site has no
        price), 'offline' (it couldn't be reached), 'loading' (still asking)."""
        if s.key in self._your:
            return "yours"
        if isinstance(s.buy, int):
            return "npc"
        if s.key in self._fm:
            return "fm" if self._fm[s.key] is not None else "none"
        return "offline" if s.key in self._fm_bad else "loading"

    def _paint_cards(self) -> None:
        """Every text of the grade cards and the answer over them, from the plan's own math."""
        group = self._group()
        if group is None or not self._cards:
            return
        got = scrollplan.plans(group, self._n, self._prices(group))
        if [c.scroll.key for c in self._cards] != [gp.scroll.key for gp in got]:
            return                     # (the cards of another stat, about to be made again)
        for card, gp in zip(self._cards, got):
            card.setVisible(self._n > 0)      # no slots to use: nothing to roll, the answer says so
            self._paint_card(card, gp)
        self._paint_answer(got)
        self._queue_fm(group)

    def _paint_card(self, card: _GradeCard, gp: scrollplan.GradePlan) -> None:
        t, rtl = self.t, self.t.rtl
        s, pl = gp.scroll, gp.plan
        card.name.setText(bidi.ltr_name(s.grade, rtl))
        card.badge.setText(bidi.ltr_name(f"{s.success}%", rtl))
        _set_prop(card.badge, "risk", _risk(s.success))
        adds = " · ".join(_whole(f"+{v} {label}", False) for label, v in s.gains)
        card.each.setText(_say(t("sp_per_scroll", gain=bidi.ltr_block(adds, rtl)), rtl))
        label, value = gp.expected[0] if gp.expected else ("", 0.0)
        card.expected.setText(_big(f"+{_num(value)}", label, rtl))
        more = " · ".join(_whole(f"+{_num(v)} {lb}", False) for lb, v in gp.expected[1:])
        word = "sp_guaranteed" if pl.p >= 1.0 else "sp_expected"
        card.expected_word.setText(_say(t(f"{word}_also", gain=bidi.ltr_block(more, rtl)) if more else t(word), rtl))
        card.odds.setText(bidi.ltr_name(_pct(pl.all_pass), rtl))
        card.odds_word.setText(bidi.plain(t("sp_all_pass", n=pl.n), rtl))
        card.caption.setText(bidi.plain(t("sp_chart"), rtl))
        card.chart.setAccessibleName(t("sp_chart"))
        card.chart.set_bars(self._bars(gp))
        self._paint_cost(card, gp)

    def _bars(self, gp: scrollplan.GradePlan) -> list[Bar]:
        """The chart's bars: one per final total with a chance above 0, its hover the exact chances."""
        t, rtl = self.t, self.t.rtl
        gains = gp.scroll.gains
        first = gains[0][1] if gains else 0
        n = gp.plan.n
        out = []
        for k, chance in enumerate(gp.plan.dist):
            if chance <= 0:
                continue
            total = k * first
            what = " · ".join(f"+{k * g} {label}" for label, g in gains) or f"+{total}"
            tip = [bidi.plain(t("sp_bar_tip", total=bidi.ltr_block(what, rtl), p=bidi.ltr_block(_exact(chance), rtl)),
                              rtl)]
            if 0 < k < n:
                tip.append(bidi.plain(t("sp_bar_tip_more", total=bidi.ltr_block(f"+{total}", rtl),
                                        p=bidi.ltr_block(_exact(gp.plan.at_least(k)), rtl)), rtl))
            out.append(Bar(chance=chance, label=f"+{total}", tip="\n".join(tip)))
        return out

    def _paint_cost(self, card: _GradeCard, gp: scrollplan.GradePlan) -> None:
        """The cost: the price's source as a tag (hover: who, or why there is none) over the price per scroll, the
        expected total over the cost per +1; with no price, the button that opens a price box."""
        t, rtl = self.t, self.t.rtl
        s = gp.scroll
        kind = self._price_kind(s)
        who = re.sub(r"\s*\(.*$", "", s.seller).strip() or s.seller
        words = {"yours": t("sp_your_price"),
                 "npc": t("sp_price_npc", who=bidi.ltr_block(who, rtl)),
                 "fm": t("sp_price_fm"),
                 "none": t("sp_price_none"),
                 "loading": t("sp_price_loading"),
                 "offline": t("sp_price_offline")}
        tips = {"npc": t("sp_price_npc_tip", who=bidi.ltr_block(s.seller, rtl)),
                "fm": t("sp_price_fm_tip"),
                "none": t("sp_price_none_tip")}
        card.tag.setText(bidi.plain(words[kind], rtl))
        card.tag.setToolTip(bidi.plain(tips[kind], rtl) if kind in tips else "")
        _set_prop(card.tag, "kind", kind)
        card.typed = self._your.get(s.key)
        priced = gp.price is not None
        if priced:
            card.price_btn.setText(bidi.plain(t("sp_each", p=bidi.ltr_block(_mesos(gp.price), rtl)), rtl))
            card.total.setText(bidi.ltr_name(f"{_mesos(gp.expected_cost)} mesos", rtl))
            label = gp.expected[0][0] if gp.expected else ""
            each = bidi.ltr_block(_mesos(gp.per_point), rtl) if gp.per_point is not None else ""
            # the card is the stat's already: "+1" alone keeps the line short (the hover names the stat)
            card.per_point.setText(bidi.plain(t("sp_per_point", p=each, point=bidi.ltr_block("+1", rtl)), rtl)
                                   if each else "")
            card.per_point.setToolTip(bidi.plain(t("sp_per_point", p=each, point=bidi.ltr_block(f"+1 {label}", rtl)),
                                                 rtl) if each else "")
        else:
            card.total.setText("")
            card.per_point.setText("")
        card.set_btn.setText(bidi.plain(t("sp_set_price"), rtl))
        for b in (card.price_btn, card.set_btn):
            b.setToolTip(t("sp_price_tip"))
        card.edit.setPlaceholderText(bidi.plain(t("sp_price_ph"), rtl))
        card.edit.setAccessibleName(t("sp_price_tip"))
        card.price_btn.setVisible(priced and not card.editing)
        card.set_btn.setVisible(not priced and not card.editing)
        card.edit.setVisible(card.editing)
        card.total.setVisible(priced)
        card.per_point.setVisible(priced and gp.per_point is not None)

    # ------------------------------------------------------------ the plain answer

    def _paint_answer(self, got: tuple[scrollplan.GradePlan, ...]) -> None:
        """The answer card: scrollplan.summary's facts in words — the verdict, a sentence per grade, what the
        prices can and can't tell."""
        rtl = self.t.rtl
        title, lines, note = self._answer_words(scrollplan.summary(got), got)
        self._answer.title.setText(_say(title, rtl))
        self._answer.set_lines([(risk, _say(text, rtl)) for risk, text in lines])
        self._answer.note.setText(_say(note, rtl) if note else "")
        self._answer.note.setVisible(bool(note))

    def _answer_words(self, sm: scrollplan.Summary,
                      got: tuple[scrollplan.GradePlan, ...]) -> tuple[str, list[tuple[str, str]], str]:
        """(the verdict, (risk, sentence) per grade — the lead's first —, the prices' note)."""
        t, rtl = self.t, self.t.rtl

        def block(x: str) -> str:
            return _whole(x, rtl)

        lead = sm.lead
        if lead is None:
            return t("sp_sum_none"), [], ""
        grade = block(lead.scroll.grade)
        key = ("sp_sum_only" if not sm.others else "sp_sum_best" if sm.best
               else "sp_sum_safe" if sm.sure else "sp_sum_most")
        gain = block(f"+{_num(scrollplan.gain(lead, sm.effect))} {sm.effect}")
        if not sm.sure:
            first = t("sp_sum_lead", gain=gain, odds=self._odds(lead))
        elif lead.expected_cost is not None:
            first = t("sp_sum_sure", gain=gain, cost=block(f"{_mesos(lead.expected_cost)} mesos"))
        else:
            first = t("sp_sum_sure_np", gain=gain)
        lines = [(_risk(lead.scroll.success), first)]
        for gp in sm.others:
            lines.append((_risk(gp.scroll.success),
                          t("sp_sum_more" if gp in sm.more else "sp_sum_avg", grade=block(gp.scroll.grade),
                            gain=block(f"+{_num(scrollplan.gain(gp, sm.effect))}"), odds=self._odds(gp))))
        point = block(f"+1 {sm.effect}")
        note = ""
        if sm.cheapest is not None and sm.others:
            note = t("sp_sum_cheapest", point=point, grade=block(sm.cheapest.scroll.grade),
                     p=block(f"{_mesos(sm.cheapest.per_point)} mesos"))
        elif sm.unpriced:
            names = self._and([block(gp.scroll.grade) for gp in sm.unpriced])
            if all(self._price_kind(gp.scroll) == "loading" for gp in sm.unpriced):
                note = t("sp_sum_loading", list=names)
            elif not sm.others:
                note = ""              # one grade: nothing to compare, its card says it has no price yet
            elif len(sm.unpriced) == len(got):
                note = t("sp_sum_no_prices")
            else:
                note = t("sp_sum_unpriced", list=names, point=point)
        return t(key, grade=grade), lines, note

    def _odds(self, gp: scrollplan.GradePlan) -> str:
        """'all 7 pass only 2.8% of the time' ('it passes 60% of the time' for one slot)."""
        p = gp.plan.all_pass
        return self.t("sp_odds_low" if p < LOW_ODDS else "sp_odds", n=gp.plan.n,
                      p=bidi.ltr_block(_pct(p), self.t.rtl))

    def _and(self, names: list[str]) -> str:
        """'Intermediate and Greater', 'A, B and C'."""
        if len(names) < 2:
            return "".join(names)
        return self.t("sp_and", a=", ".join(names[:-1]), b=names[-1])

    # ------------------------------------------------------------ the Free Market prices

    def _queue_fm(self, group: scrollplan.StatGroup) -> None:
        """The usual Free Market price for every grade neither the KB's NPC price nor an earlier fetch covers,
        fetched off the GUI thread (each item is up to 3 requests of 8 s on a slow line)."""
        keys = [s.key for s in group.grades if not isinstance(s.buy, int) and s.key not in self._fm_seen]
        if not keys:
            return
        self._fm_seen.update(keys)
        # (not counted up per lookup: a stat switched mid-lookup dropped the first stat's answers, and its grades
        # stayed "checking" for good, their keys already seen)
        read = self._fm_read

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
        """A lookup's answer, back on the GUI thread: remembered (also for the other grades' cards and stats) and the
        texts rewritten. An answer asked for under the KB before a swap is dropped."""
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
        self._spin.setAccessibleName(t("sp_use"))
        self._stat_label.setText(bidi.plain(t("sp_stat"), rtl))
        self._about.head.title.setText(bidi.plain(t("sp_about"), rtl))
        self._about.body.setText("\n".join(bidi.plain(t(k), rtl)
                                           for k in ("sp_source_note", "sp_chaos_note", "sp_typed_note")))

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
        for card in self._cards:   # a price box open when it hides: what is typed is kept, the box closed
            card.end_edit()
        self._typing.stop()       # nothing fires once it is hidden: the rows are not read again behind the back
        if self._placed:             # hidden (the ✕, the bar's button, the app closing): keep the place now
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["scroll_planner_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(),
                                                     "h": self.height()}
