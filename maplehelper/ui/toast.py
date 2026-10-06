"""Branded notifications (instead of the Windows toast, which shows the host process name)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from .. import bidi, osapi
from ..store import ASSETS
from . import theme
from .glass import SHADOW, paint_glass

MARGIN = 16
WIDTH = 360


class Toast(QWidget):
    """A small card in the bottom corner: logo, title, message. Never steals focus from the game."""

    _live: list["Toast"] = []

    def __init__(self, title: str, message: str, rtl: bool, font_family: str, timeout_ms: int = 5000, screen=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)   # the app is never frontmost on macOS
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.setFixedWidth(WIDTH + 2 * SHADOW)
        self._font_family, self._screen = font_family, screen
        self._slot: QRect | None = None          # where it sits once it has slid in
        self.restyle()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        card = QFrame(objectName="Card")
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        accent = QFrame(objectName="Accent")
        accent.setFixedWidth(4)
        row.addWidget(accent)

        icon = QLabel()
        pm = QPixmap(str(ASSETS / "brand" / "icon-64.png"))
        if not pm.isNull():
            icon.setPixmap(pm.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        row.addWidget(icon, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)
        brand = QLabel("Maple Helper", objectName="Brand")
        brand.setAlignment((Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute)
        col.addWidget(brand)
        t = QLabel(bidi.plain(title, rtl), objectName="Title")
        t.setWordWrap(True)
        col.addWidget(t)
        if message:
            b = QLabel(bidi.plain(message, rtl), objectName="Body")
            b.setWordWrap(True)
            col.addWidget(b)
        row.addLayout(col, 1)

        close = QToolButton(text="✕")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        row.addWidget(close, 0, Qt.AlignTop)
        # the text column's exact width before any adjustSize(): the height then comes from the wrapped text
        # (measured at a guessed width, the message's last line was cut off)
        close.ensurePolished()
        col_w = WIDTH - 2 * 12 - 4 - 40 - close.sizeHint().width() - 3 * 12
        for lb in card.findChildren(QLabel):
            if lb.wordWrap():
                lb.setFixedWidth(col_w)

        self._timer = QTimer(self, singleShot=True, interval=timeout_ms, timeout=self.dismiss)

    def restyle(self) -> None:
        """The text colors of the current theme (the glass itself is painted from it each time): a toast up during
        a theme switch kept white text on the new light glass."""
        c = theme.P()
        self.setStyleSheet(f"""
            * {{ font-family: "{self._font_family}"; color: {c['text']}; }}
            #Card {{ background: transparent; border: none; }}
            #Accent {{ background: {theme.ORANGE}; border-radius: 2px; }}
            #Title {{ font-size: 14px; font-weight: 600; color: {c['text']}; }}
            #Body {{ font-size: 13px; color: {c['text']}; }}
            #Brand {{ font-size: 11px; color: {c['muted']}; }}
            QToolButton {{ background: transparent; border: none; color: {c['muted']}; font-size: 14px; }}
            QToolButton:hover {{ color: {c['text']}; }}
        """)
        self.update()

    def show_toast(self):
        self.adjustSize()
        # the monitor the player is looking at (App.toast picks it), else the primary one
        area = (self._screen or QGuiApplication.primaryScreen()).availableGeometry()
        Toast._live = [o for o in Toast._live if o.isVisible() and o._slot is not None]
        while True:
            y = self._free_y(area)
            if y is not None or not Toast._live:
                break
            Toast._live[0].dismiss()           # no room left on the screen: the oldest toast makes way
        x = area.right() - self.width() - MARGIN + SHADOW
        y = y if y is not None else area.bottom() - self.height() - MARGIN + SHADOW
        self._slot = QRect(x, y, self.width(), self.height())
        Toast._live.append(self)
        self.setWindowOpacity(0.0)
        self.move(QPoint(x, y + 12))
        self.show()
        osapi.float_over_fullscreen(int(self.winId()))   # macOS: over a fullscreen game's Space too
        self._anim(1.0, QPoint(x, y))
        self._timer.start()

    def _free_y(self, area: QRect) -> int | None:
        """The lowest free slot in the corner: up past the toasts it would overlap only, so a gap left by a toast
        that went away is used again (a new toast always went above the highest one, and on past the top of the
        screen). None when there's no room left on the screen."""
        h = self.height()
        y = area.bottom() - h - MARGIN + SHADOW
        for other in sorted(Toast._live, key=lambda o: -o._slot.y()):       # bottom first
            r = other._slot
            if y < r.y() + r.height() + 8 and y + h + 8 > r.y():
                y = r.y() - h - 8
        return y if y >= area.top() + MARGIN - SHADOW else None

    def _anim(self, opacity: float, pos: QPoint, on_done=None):
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(220)
        self._fade.setEndValue(opacity)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(220)
        self._slide.setEndValue(pos)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        if on_done:
            self._fade.finished.connect(on_done)
        self._fade.start()
        self._slide.start()

    def dismiss(self):
        self._timer.stop()
        if self in Toast._live:
            Toast._live.remove(self)
        self._anim(0.0, QPoint(self.x(), self.y() + 12), self.close)

    def mouseReleaseEvent(self, e):
        self.dismiss()

    def closeEvent(self, e):
        # closed any other way than dismiss() (it deletes itself on close): out of the stack too
        if self in Toast._live:
            Toast._live.remove(self)
        super().closeEvent(e)

    def paintEvent(self, e):
        paint_glass(self, None, radius=18)


def notify(title: str, message: str = "", rtl: bool = True, font_family: str | None = None, timeout_ms: int = 5000,
           screen=None):
    t = Toast(title, message, rtl, font_family or theme.FONT_FAMILY, timeout_ms, screen)
    t.show_toast()
    return t
