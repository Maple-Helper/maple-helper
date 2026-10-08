"""The minimap box picker: a dimmed full-desktop overlay the player drags a rectangle on.

The widget lives in logical pixels (what Qt mouse events use); the `picked` payload is in capture
coordinates (what `maplehelper.capture.grab_image` takes), converted by `to_capture`."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import bidi
from . import theme

MIN_SIDE = 12  # a smaller release is a misclick, not a minimap box: keep picking


def to_capture(rect: QRect, screens: list[tuple[QRect, float]], scale: bool = True) -> dict:
    """`rect` (logical pixels, global desktop coordinates) → capture coordinates.

    `screens` pairs each screen's logical geometry with its devicePixelRatio. Windows capture
    coordinates are physical pixels, and Qt maps each screen from its own native origin, so the box
    is scaled on the screen holding its centre (a box straddling two screens follows its middle).
    With `scale=False` (macOS: capture coordinates are points) the rect passes through unchanged.
    """
    box = rect.normalized()
    x, y, w, h = box.x(), box.y(), box.width(), box.height()
    if not scale or not screens:
        return {"x": x, "y": y, "w": w, "h": h}
    cx, cy = box.center().x(), box.center().y()
    geo, ratio = screens[0]
    for g, r in screens:
        if g.contains(cx, cy):
            geo, ratio = g, r
            break
    o = geo.topLeft()
    return {
        "x": round(o.x() + (x - o.x()) * ratio),
        "y": round(o.y() + (y - o.y()) * ratio),
        "w": round(w * ratio),
        "h": round(h * ratio),
    }


class RegionPicker(QWidget):
    """Frameless stay-on-top overlay covering every screen; drag a box, Esc/right-click cancels."""

    picked = Signal(dict)
    cancelled = Signal()

    def __init__(self, hint: str, rtl: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._hint = bidi.plain(hint, rtl)
        self._rtl = rtl
        self._screens = [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]
        union = QRect()
        for geo, _ in self._screens:
            union = union.united(geo)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
        self.setGeometry(union)
        self._origin: QPoint | None = None
        self._current: QRect | None = None
        # the hint sits near the top-centre of the screen under the cursor when picking starts, so the
        # player sees it where they are already looking (in widget-local coordinates, like painting)
        at = QCursor.pos()
        self._hint_at = at - union.topLeft()
        self._hint_geo = QRect(0, 0, union.width(), union.height())
        for geo, _ in self._screens:
            if geo.contains(at):
                self._hint_geo = QRect(geo.topLeft() - union.topLeft(), geo.size())
                break

    def start(self) -> None:
        """Show the overlay and take the keyboard so Esc cancels (released again on close)."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.PopupFocusReason)
        self.grabKeyboard()

    def _box(self) -> QRect | None:
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

    # -- input ---------------------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.RightButton:
            self.cancelled.emit()
            self.close()
        elif e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._current = e.position().toPoint()
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if self._origin is not None:
            self._current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.LeftButton or self._origin is None:
            return
        box = QRect(self._origin, e.position().toPoint()).normalized()
        self._origin, self._current = None, None
        if box.width() < MIN_SIDE or box.height() < MIN_SIDE:
            self.update()  # a misclick: stay open, let the player drag again
            return
        top_left = self.geometry().topLeft()  # widget-local → global logical, then capture coords
        payload = to_capture(box.translated(top_left), self._screens, scale=sys.platform != "darwin")
        self.picked.emit(payload)
        self.close()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e) -> None:
        try:
            self.releaseKeyboard()
        except RuntimeError:  # closed twice meanwhile
            pass
        super().closeEvent(e)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 89))  # dim everything ~35%, the box stays clear
        box = self._box()
        if box is not None and not box.isNull():
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(box, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(theme.ORANGE), 2))
            p.drawRect(box.adjusted(1, 1, -1, -1))
            p.setPen(QColor(255, 255, 255))
            anchor = box.bottomLeft() + QPoint(0, 20)
            p.drawText(anchor.x(), anchor.y(), f"{box.width()} × {box.height()}")
        p.setPen(QColor(255, 255, 255))
        p.drawText(self._hint_geo.adjusted(12, 12, -12, -12),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.AlignAbsolute, self._hint)
        p.end()
