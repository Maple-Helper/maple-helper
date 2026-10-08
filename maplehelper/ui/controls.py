"""iOS-style controls: switch, segmented control, grouped section rows."""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QEvent, QObject, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import bidi
from . import theme

DISABLED_OPACITY = 0.45      # a Switch / Select that can't be used now


class Switch(QAbstractButton):
    """iOS switch: the knob slides with a critically damped ease; mirrors in RTL."""

    def __init__(self, checked: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(46, 28)

    def _animate(self, on: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)      # from the current on-screen value
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_knob(self):
        return self._pos

    def set_knob(self, v):
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(DISABLED_OPACITY)              # greyed, as iOS does (it looked live while it did nothing)
        w, h = 46, 28
        track = QRectF(0, (self.height() - h) / 2, w, h)
        off = QColor(120, 120, 128, 90) if theme.MODE == "dark" else QColor(120, 120, 128, 60)
        on = QColor(52, 199, 89)                       # iOS system green
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t), int(off.alpha() + (255 - off.alpha()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, h / 2, h / 2)
        rtl = self.layoutDirection() == Qt.RightToLeft
        x0, x1 = 2, w - h + 2
        x = x0 + (x1 - x0) * (1 - t if rtl else t)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRectF(x, track.top() + 3, h - 4, h - 4))
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, track.top() + 2, h - 4, h - 4))


class Segmented(QFrame):
    """Segmented control: one capsule, the chosen segment lifted as a solid pill."""

    changed = Signal(object)

    def __init__(self, options: list[tuple[str, object]], current, rtl: bool):
        super().__init__(objectName="Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for label, value in options:
            b = QPushButton(bidi.plain(label, rtl), objectName="Segment")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("value", value)
            b.setChecked(value == current)
            self.group.addButton(b)
            lay.addWidget(b, 1)
        self.group.buttonClicked.connect(lambda b: self.changed.emit(b.property("value")))

    def showEvent(self, e):
        # the chosen segment is bold: reserve that width, or "Kerning City" loses a letter when picked.
        # Measured once styled (the stylesheet's size and letter spacing), not with the default font
        for b in self.group.buttons():
            self._fit(b)
        super().showEvent(e)

    @staticmethod
    def _fit(b) -> None:
        from PySide6.QtGui import QFontMetrics
        b.ensurePolished()
        bold = b.font()
        bold.setBold(True)
        b.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(b.text()) + 30)

    def set_text(self, i: int, text: str) -> None:
        """A segment's new text, its width measured again ("Quests for level 31" was cut to the width of the
        label it had before the level was known)."""
        b = self.group.buttons()[i]
        b.setText(text)
        self._fit(b)

    def value(self):
        b = self.group.checkedButton()
        return b.property("value") if b else None

    def set_value(self, value) -> None:
        """Pick a segment without the changed signal (a page opened from elsewhere names its subject)."""
        for b in self.group.buttons():
            if b.property("value") == value:
                b.setChecked(True)

    def set_label(self, label: str) -> None:
        """What a screen reader says for it: the row's label for the control, and as context on each segment
        (alone, "Dark" or "A" says nothing about what it sets)."""
        self.setAccessibleName(label)
        for b in self.group.buttons():
            b.setAccessibleDescription(label)


class BalancedRow(QWidget):
    """Buttons on one row when they fit at their own widths, else in equal rows (six: two of three): never one
    left alone on a second row, never cut (the crafting professions, the owner)."""

    def __init__(self, buttons: list, spacing: int = 4):
        super().__init__()
        from PySide6.QtWidgets import QGridLayout
        self._buttons = list(buttons)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        self._spacing = spacing
        self._per_row = 0
        self._place(len(self._buttons))

    def _need(self) -> int:
        return sum(self._width(b) for b in self._buttons) + self._spacing * (len(self._buttons) - 1)

    @staticmethod
    def _width(b) -> int:
        """The width a button needs when picked: its text in bold (picked, "Leatherworking" lost its last letter)."""
        from PySide6.QtGui import QFontMetrics
        b.ensurePolished()
        bold = b.font()
        bold.setBold(True)
        return max(b.sizeHint().width(), QFontMetrics(bold).horizontalAdvance(b.text()) + 14)

    def showEvent(self, e):
        for b in self._buttons:
            b.setMinimumWidth(self._width(b))
        super().showEvent(e)

    def _place(self, per_row: int) -> None:
        if per_row == self._per_row:
            return
        self._per_row = per_row
        for b in self._buttons:
            self._grid.removeWidget(b)
        for i, b in enumerate(self._buttons):
            self._grid.addWidget(b, i // per_row, i % per_row)
        for c in range(len(self._buttons)):
            self._grid.setColumnStretch(c, 1 if c < per_row else 0)

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        widest = max((self._width(b) for b in self._buttons), default=0)
        return QSize(widest * 3 + self._spacing * 2, super().minimumSizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        n = len(self._buttons)
        self._place(n if e.size().width() >= self._need() else (n + 1) // 2)


class Section(QFrame):
    """A grouped card of rows (iOS Settings): label on the leading side, control on the trailing side."""

    def __init__(self, header: str = "", rtl: bool = True):
        super().__init__()
        self.rtl = rtl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self.header = None
        if header:
            self.header = QLabel(objectName="SectionHeader")
            self.set_header(header)
            outer.addWidget(self.header)
        self.card = QFrame(objectName="Group")
        self.rows = QVBoxLayout(self.card)
        self.rows.setContentsMargins(14, 4, 14, 4)
        self.rows.setSpacing(0)
        outer.addWidget(self.card)
        self._items: list[tuple[QFrame | None, QWidget]] = []    # each row, and the separator above it

    def set_header(self, header: str) -> None:
        if self.header is not None:
            self.header.setText(bidi.plain(header.upper() if not self.rtl else header, self.rtl))

    def add_row(self, label: str, control: QWidget | None = None, hint: str = "", hint_below: bool = False) -> QWidget:
        """hint_below: the hint goes under the whole row (label and control), not squeezed beside the control."""
        row = QWidget()
        if hint_below:
            whole = QVBoxLayout(row)
            whole.setContentsMargins(0, 8, 0, 8)
            whole.setSpacing(4)
            lay = QHBoxLayout()
            whole.addLayout(lay)
        else:
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 8, 0, 8)
        col = QVBoxLayout()
        col.setSpacing(1)
        lb = QLabel(bidi.plain(label, self.rtl), objectName="RowLabel")
        lb.setWordWrap(True)
        col.addWidget(lb)
        if hint:
            hl = QLabel(bidi.plain(hint, self.rtl), objectName="RowHint")
            hl.setWordWrap(True)
            (whole if hint_below else col).addWidget(hl)
        lay.addLayout(col, 1)
        if control is not None:
            control.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            lay.addWidget(control, 0, Qt.AlignVCenter)
            # the label is a separate QLabel beside it: without this a screen reader said only "check box"
            # or "button", never what the switch or the pick is for
            if hasattr(control, "set_label"):
                control.set_label(label)
            else:
                control.setAccessibleName(label)
            if hint:
                control.setAccessibleDescription(hint)
        self.add_widget(row)
        return row

    def add_widget(self, w: QWidget):
        sep = None
        if self._items:
            sep = QFrame(objectName="Separator")
            sep.setFixedHeight(1)
            self.rows.addWidget(sep)
        if isinstance(w, QPushButton) and w.objectName() in ("Link", "LinkDanger"):
            # a link at its own width on the leading side (the layout mirrors it): full width, its "text-align:
            # left" isn't mirrored, and in Hebrew every link sat at the far end from its section's header
            self.rows.addWidget(w, 0, Qt.AlignLeading | Qt.AlignVCenter)
        else:
            if isinstance(w, QLabel) and w.contentsMargins().isNull():
                # a plain text row gets the rows' own top and bottom room (add_row's 8 px): the pets' intro sat
                # 4 px from the card's edge
                w.setContentsMargins(0, 8, 0, 8)
            self.rows.addWidget(w)
        self._items.append((sep, w))
        # a row that is hidden for now (a sign-in hint, the installer's progress) left its divider behind, an
        # empty line at the bottom of the card: each divider shows only when its row and one above it do
        w.installEventFilter(self)
        self._sync_separators()

    @staticmethod
    def _shown(w: QWidget) -> bool:
        """Shown in the card (or will be once the window opens): only an explicit hide() counts."""
        return not (w.isHidden() and w.testAttribute(Qt.WA_WState_ExplicitShowHide))

    def _sync_separators(self) -> None:
        above = False
        for sep, w in self._items:
            shown = self._shown(w)
            if sep is not None:
                sep.setVisible(shown and above)
            above = above or shown

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if e.type() in (QEvent.Show, QEvent.Hide):
            self._sync_separators()
        return False

    def showEvent(self, e):
        # rows hidden or shown before the window opened sent no Show/Hide event to the filter
        self._sync_separators()
        super().showEvent(e)


def track_slider(slider, rtl: bool) -> None:
    """Qt stylesheets always paint 'sub-page' on the visual left. In a mirrored (RTL) slider the
    filled part must grow from the right, so the two track colors swap sides."""
    if not rtl:
        return
    track = "rgba(255,255,255,0.22)" if theme.MODE == "dark" else "rgba(0,0,0,0.13)"
    slider.setStyleSheet(f"""
        QSlider::sub-page:horizontal {{ background: {track}; border-radius: 3px; }}
        QSlider::add-page:horizontal {{ background: {theme.ORANGE}; border-radius: 3px; }}
    """)


# ---------------------------------------------------------------- pop-up button (replaces QComboBox)

from PySide6.QtCore import QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QAction, QPainterPath, QPen  # noqa: E402
from PySide6.QtWidgets import QMenu  # noqa: E402


# the room around a Select's value: theme.py's "padding: 0 28px" and its 1px border, both sides. 48 left 10px too few:
# at font size 16 the Hebrew "ברירת המחדל של המערכת" fit the cap, wasn't cut, and lost its first letter at the edge
SELECT_PAD = 58


class Select(QPushButton):
    """macOS-style pop-up button: shows the value with ⌃⌄ chevrons; opens a rounded glass menu
    anchored to itself, the current choice checked. API mirrors the bits of QComboBox we use."""

    currentIndexChanged = Signal(int)
    picked = Signal(int)          # only when the user chooses from the menu

    def __init__(self, items: list[str] | None = None):
        super().__init__(objectName="Select")
        self.setCursor(Qt.PointingHandCursor)
        self._items: list[str] = []
        self._index = -1
        self._label = ""
        self.text_width = None        # cap on the shown value's width: a longer one is cut with "…" (the menu shows it whole)
        self.clicked.connect(self._open)
        if items:
            self.addItems(items)

    # QComboBox-compatible surface
    def addItems(self, items):
        self._items.extend(items)
        if self._index < 0 and self._items:
            self.setCurrentIndex(0)
        self.updateGeometry()

    def clear(self):
        self._items, self._index = [], -1
        self.setText("")
        self._name()

    def show_none(self, prompt: str):
        """No item chosen yet: show a prompt ("Pick a job") until the player picks one."""
        self._index = -1
        self.setText(prompt)
        self._name()

    def set_label(self, label: str) -> None:
        """What the pick is for ("Open/close key"): a screen reader heard only the value."""
        self._label = label
        self._name()

    def _name(self) -> None:
        if self._label:
            self.setAccessibleName(f"{self._label}: {self.text()}" if self.text() else self._label)

    def count(self):
        return len(self._items)

    def currentText(self):
        return self._items[self._index] if 0 <= self._index < len(self._items) else ""

    def currentIndex(self) -> int:
        return self._index

    def setCurrentIndex(self, i: int):
        if 0 <= i < len(self._items) and i != self._index:
            self._index = i
            self.setText(self._shown(self._items[i]))
            self._name()
            self.currentIndexChanged.emit(i)

    def setCurrentText(self, text: str):
        if text in self._items:
            self.setCurrentIndex(self._items.index(text))

    def _shown(self, text: str) -> str:
        if self.text_width is None:
            return text
        room = min(self.text_width, self.width() - SELECT_PAD) if self.isVisible() else self.text_width
        cut = self.fontMetrics().elidedText(text, Qt.ElideRight, max(room, 20))
        # an English name in a Hebrew window: one LTR block, or the "…" jumped to its left end
        return bidi.ltr_name(cut, self.layoutDirection() == Qt.RightToLeft)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.text_width is not None and 0 <= self._index < len(self._items):
            self.setText(self._shown(self._items[self._index]))      # the room the layout really gave it

    def sizeHint(self):
        s = super().sizeHint()
        longest = max((self.fontMetrics().horizontalAdvance(t) for t in self._items), default=40)
        if self.text_width is not None:
            longest = min(longest, self.text_width)
            s.setWidth(longest + SELECT_PAD)        # the button's own hint measured the uncut text
        return s.expandedTo(QSize(longest + SELECT_PAD, 30))

    def _open(self):
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setLayoutDirection(self.layoutDirection())
        for i, t in enumerate(self._items):
            a = QAction(t, menu, checkable=True, checked=(i == self._index))
            a.triggered.connect(lambda _=False, i=i: (self.setCurrentIndex(i), self.picked.emit(i)))
            menu.addAction(a)
        if 0 <= self._index < len(self._items):
            menu.setActiveAction(menu.actions()[self._index])
        # open over the button so the current item lines up with it (macOS behaviour)
        rtl = self.layoutDirection() == Qt.RightToLeft
        anchor = self.mapToGlobal(QPoint(self.width() if rtl else 0, 0))
        menu.adjustSize()
        x = anchor.x() - menu.width() if rtl else anchor.x()
        menu.exec(QPoint(x, anchor.y()))

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rtl = self.layoutDirection() == Qt.RightToLeft
        cx = 14 if rtl else self.width() - 14          # chevrons on the trailing side
        cy = self.height() / 2
        col = QColor(235, 235, 245, 160) if theme.MODE == "dark" else QColor(60, 60, 67, 160)
        if not self.isEnabled():
            p.setOpacity(DISABLED_OPACITY)              # the chevrons grey out with the value (theme.py)
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        up, down = QPainterPath(), QPainterPath()
        up.moveTo(QPointF(cx - 3.5, cy - 2))
        up.lineTo(QPointF(cx, cy - 5.5))
        up.lineTo(QPointF(cx + 3.5, cy - 2))
        down.moveTo(QPointF(cx - 3.5, cy + 2))
        down.lineTo(QPointF(cx, cy + 5.5))
        down.lineTo(QPointF(cx + 3.5, cy + 2))
        p.drawPath(up)
        p.drawPath(down)


class Stepper(QFrame):
    """iOS-style stepper: − value +. Hold a button to repeat; the value can also be typed."""

    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, value: int):
        super().__init__(objectName="Stepper")
        from PySide6.QtGui import QIntValidator
        from PySide6.QtWidgets import QLineEdit, QToolButton
        self.lo, self.hi, self._v = lo, hi, value
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(0)
        self.minus = QToolButton(objectName="StepBtn", text="−")
        self.plus = QToolButton(objectName="StepBtn", text="+")
        for b, d in ((self.minus, -1), (self.plus, 1)):
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(60)
            b.clicked.connect(lambda _=False, d=d: self.setValue(self._v + d))
        self.edit = QLineEdit(str(value), objectName="StepValue")
        self.edit.setValidator(QIntValidator(lo, hi, self))
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.setFixedWidth(46)
        self.edit.textEdited.connect(self._typed)
        self.edit.editingFinished.connect(lambda: self.edit.setText(str(self._v)))
        # Up/Down in the field step like the buttons (the buttons are a mouse target; typing is the keyboard way)
        self.edit.installEventFilter(self)
        # minus sits on the leading side, plus on the trailing side (mirrors in RTL)
        lay.addWidget(self.minus)
        lay.addWidget(self.edit)
        lay.addWidget(self.plus)
        self._sync()

    def value(self) -> int:
        return self._v

    def set_label(self, label: str, less: str = "", more: str = "") -> None:
        """Screen-reader names: the field is the value of `label`; the - and + buttons say what they do
        (alone they were read as "minus" and "plus" with nothing to tie them to the field)."""
        self.setAccessibleName(label)
        self.edit.setAccessibleName(label)
        self.minus.setAccessibleName(less or self.minus.text())
        self.plus.setAccessibleName(more or self.plus.text())
        for b in (self.minus, self.plus):
            b.setAccessibleDescription(label)

    def eventFilter(self, obj, e) -> bool:
        if obj is self.edit and e.type() == QEvent.KeyPress and e.key() in (Qt.Key_Up, Qt.Key_Down):
            self.setValue(self._v + (1 if e.key() == Qt.Key_Up else -1))
            return True
        return super().eventFilter(obj, e)

    def setMinimum(self, lo: int):
        """Raise/lower the floor; a value below it moves up to it (and emits)."""
        self.lo = lo
        from PySide6.QtGui import QIntValidator
        self.edit.setValidator(QIntValidator(lo, self.hi, self))
        if self._v < lo:
            self.setValue(lo)
        self._sync()

    def setMaximum(self, hi: int):
        """Raise/lower the ceiling: the typed-value check and the + button follow (a value above moves down, emits)."""
        self.hi = hi
        from PySide6.QtGui import QIntValidator
        self.edit.setValidator(QIntValidator(self.lo, hi, self))
        if self._v > hi:
            self.setValue(hi)
        self._sync()

    def setValue(self, v: int):
        v = max(self.lo, min(self.hi, int(v)))
        if v != self._v:
            self._v = v
            self.edit.setText(str(v))
            self._sync()
            self.valueChanged.emit(v)

    def _typed(self, text: str):
        if text.isdigit():
            v = max(self.lo, min(self.hi, int(text)))
            if v != self._v:
                self._v = v
                self._sync()
                self.valueChanged.emit(v)

    def _sync(self):
        self.minus.setEnabled(self._v > self.lo)
        self.plus.setEnabled(self._v < self.hi)


from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QLayout  # noqa: E402


class FlowLayout(QLayout):
    """Items in a row from the leading edge (right in Hebrew), wrapping onto the next line when the row is full:
    a row of tags or chips never makes the window wider than it is (one long row pushed it past 470 px)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6, line_spacing: int | None = None,
                 per_row: int = 0):
        super().__init__(parent)
        self._items = []
        self._per_row = per_row           # at most this many a line (0: as many as fit)
        self._h = spacing
        self._v = spacing if line_spacing is None else line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    @staticmethod
    def _width(item) -> int:
        """An item's width; a checkable chip's room for its text in bold, as it's drawn when picked (a picked
        "All levels" was sized for its regular text and showed "ll levels")."""
        w = item.sizeHint().width()
        b = item.widget()
        if isinstance(b, QAbstractButton) and b.isCheckable() and b.text():
            from PySide6.QtGui import QFontMetrics
            b.ensurePolished()
            bold = b.font()
            bold.setBold(True)
            w += max(0, QFontMetrics(bold).horizontalAdvance(b.text()) - QFontMetrics(b.font()).horizontalAdvance(b.text()))
        return w

    def _rtl(self) -> bool:
        w = self.parentWidget()
        return (w.layoutDirection() if w is not None else Qt.LeftToRight) == Qt.RightToLeft

    def _arrange(self, rect, apply: bool) -> int:
        """Lines of items that fit the width; each line's items centered on its height. Returns the height used."""
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines, line, x = [], [], 0
        for item in self._items:
            if item.isEmpty():
                continue
            w = min(self._width(item), max(1, area.width()))
            if line and (x + self._h + w > area.width() or (self._per_row and len(line) >= self._per_row)):
                lines.append(line)
                line, x = [], 0
            x += (self._h if line else 0) + w
            line.append((item, w))
        if line:
            lines.append(line)
        y, rtl = area.y(), self._rtl()
        for line in lines:
            h = max(item.sizeHint().height() for item, _ in line)
            x = 0
            for item, w in line:
                ih = item.sizeHint().height()
                left = area.right() - x - w + 1 if rtl else area.x() + x
                if apply:
                    item.setGeometry(QRect(left, y + (h - ih) // 2, w, ih))
                x += w + self._h
            y += h + self._v
        used = y - self._v - area.y() if lines else 0
        return used + m.top() + m.bottom()


class AdaptiveRow(QWidget):
    """A wide control (a text field) with a button: side by side when there is room, else the button goes under
    the field at the leading edge (beside a field at 470 px it pushed the window wider)."""

    def __init__(self, main: QWidget, side: QWidget, main_min: int = 240, spacing: int = 8):
        super().__init__()
        from PySide6.QtWidgets import QBoxLayout
        self._main, self._side, self._main_min = main, side, main_min
        self._lay = QBoxLayout(QBoxLayout.TopToBottom, self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(spacing)
        self._lay.addWidget(main)
        self._lay.addWidget(side, 0, Qt.AlignLeft)        # AlignLeft: the leading edge (mirrored in Hebrew)
        self._wide = None

    def minimumSizeHint(self):
        return QSize(max(self._main.minimumSizeHint().width(), self._side.sizeHint().width()),
                     super().minimumSizeHint().height())

    def wide_enough(self, width: int) -> bool:
        return width >= self._main_min + self._lay.spacing() + self._side.sizeHint().width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place(self.width())

    def _place(self, width: int):
        from PySide6.QtWidgets import QBoxLayout
        wide = self.wide_enough(width)
        if wide == self._wide:
            return
        self._wide = wide
        self._lay.setDirection(QBoxLayout.LeftToRight if wide else QBoxLayout.TopToBottom)
        self._lay.setStretchFactor(self._main, 1 if wide else 0)
        self._lay.setAlignment(self._side, Qt.AlignVCenter if wide else Qt.AlignLeft)


class WrapLink(QLabel):
    """A link-styled action whose text wraps (a QPushButton#Link never does: a long one widened the window)."""

    clicked = Signal()

    def __init__(self, text: str = "", rtl: bool = False):
        super().__init__()
        self.setObjectName("WrapLink")
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setCursor(Qt.PointingHandCursor)
        # reachable with Tab and run with Enter or Space, like the buttons around it (a QLabel takes no focus:
        # "Read my stats from the screen" was mouse-only)
        self.setFocusPolicy(Qt.StrongFocus)
        self._rtl = rtl
        self.set_text(text)

    def set_text(self, text: str) -> None:
        import html
        self.setAccessibleName(text)        # the plain words, not the rich-text markup around them
        d, side = ("rtl", "right") if self._rtl else ("ltr", "left")
        self.setText(f"<div dir='{d}' align='{side}'><span style='color:{theme.accent_text()}; font-weight:500;'>"
                     f"{html.escape(bidi.plain(text, self._rtl))}</span></div>")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not e.isAutoRepeat():
            self.clicked.emit()
            return
        super().keyPressEvent(e)


def follow_typing(edit, rtl: bool) -> None:
    """A search box in Hebrew: the cursor waits on the right, by the hint (Qt put it on the left of an empty
    box); a name typed in English, as the game writes it, runs from the left. Only the text moves: the clear
    button and any arrow keep their side."""
    def follow(*_):
        text = edit.text()
        right = rtl and (not text or bool(bidi._RTL.search(text)))
        edit.setAlignment((Qt.AlignRight if right else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter)
    edit.textChanged.connect(follow)
    follow()


def rtl_buttons(root, rtl: bool) -> None:
    """Qt lays push-button text out left-to-right whatever the widget direction, which throws
    final punctuation ("!", "?") to the wrong side. Re-mark every button label for RTL."""
    if not rtl:
        return
    for b in root.findChildren(QPushButton):
        t = b.text()
        if t and not t.startswith("\u200f") and b.objectName() not in ("Select",):
            b.setText(bidi.plain(t, True))
