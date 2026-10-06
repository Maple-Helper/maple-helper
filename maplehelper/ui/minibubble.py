"""The minimized chat: a small round glass bubble with the app mark. Drag to move, click to reopen."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..store import ASSETS
from . import theme

SIZE = 56
MARGIN = 8          # room for the shadow
DRAG_SLOP = 6       # px of movement before a press becomes a drag (a click stays a click)


class MiniBubble(QWidget):
    clicked = Signal()
    moved = Signal(QPoint)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)   # macOS hides tool windows of inactive apps
        self.setFixedSize(SIZE + 2 * MARGIN, SIZE + 2 * MARGIN)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Maple Helper")
        # the 256 px mark: the 64 px one drawn at 34 px on a 200% screen was upscaled and blurred (as the header was)
        self._icon = QPixmap(str(ASSETS / "brand" / "icon-256.png"))
        self._press: QPoint | None = None
        self._grab: QPoint | None = None
        self._dragging = False
        self._pressed = False

    def paintEvent(self, e):
        c = theme.P()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(MARGIN, MARGIN, SIZE, SIZE)
        for i in range(MARGIN, 0, -2):                       # soft shadow
            sh = QPainterPath()
            sh.addEllipse(r.adjusted(-i, -i + 2, i, i + 2))
            p.fillPath(sh, QColor(0, 0, 0, int(30 * (1 - i / MARGIN)) + 2))
        circle = QPainterPath()
        circle.addEllipse(r)
        tint = QColor(*c["glass"])
        tint.setAlphaF(0.92)
        p.fillPath(circle, tint)
        sheen = QLinearGradient(0, r.top(), 0, r.center().y())
        sheen.setColorAt(0, QColor(255, 255, 255, c["sheen"] + 20))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillPath(circle, sheen)
        p.setPen(QPen(QColor(255, 255, 255, c["rim_top"]), 1))
        p.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5))
        if not self._icon.isNull():
            s = 34 if not self._pressed else 31               # instant press feedback
            dpr = max(1.0, self.devicePixelRatioF())
            px = round(s * dpr)
            icon = self._icon.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon.setDevicePixelRatio(dpr)
            p.drawPixmap(int(r.center().x() - s / 2), int(r.center().y() - s / 2), icon)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._grab = self._press - self.frameGeometry().topLeft()
            self._dragging = False
            self._pressed = True
            self.update()

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        pos = e.globalPosition().toPoint()
        if not self._dragging and (pos - self._press).manhattanLength() > DRAG_SLOP:
            self._dragging = True
        if self._dragging:
            self.move(pos - self._grab)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._press is None:
            return             # a right-click reopened the chat: only a left click (or drag) does anything
        was_drag = self._dragging
        self._press, self._dragging, self._pressed = None, False, False
        self.update()
        if was_drag:
            self.moved.emit(self.pos())
        else:
            self.clicked.emit()
