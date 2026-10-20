"""The grind tracker's rate graph: EXP per hour and net mesos per hour over the session (lootextras.rate_series), two
lines on one card, each on its own scale (left: EXP, right: mesos), so a slowdown shows the moment it starts."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

EXP_COLOR = theme.ORANGE_DEEP     # the app's orange
NET_COLOR = "#2E9E5B"       # the net profit's green


def _short(n: float) -> str:
    a = abs(n)
    text = f"{a / 1_000_000:.1f}M" if a >= 1_000_000 else f"{a / 1000:.0f}K" if a >= 10_000 else f"{a:,.0f}"
    return ("-" if n < 0 else "") + text


class RateGraph(QWidget):
    def __init__(self, labels: tuple[str, str, str], rtl: bool):
        """labels: (EXP/h, net/h, minutes) as the UI says them."""
        super().__init__()
        self.labels, self.rtl = labels, rtl
        self.points: list[tuple[float, int, int]] = []
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setLayoutDirection(Qt.LeftToRight)     # a time axis runs left to right in both languages

    def set_points(self, points) -> None:
        self.points = list(points)
        self.setAccessibleDescription("; ".join(f"{m:.0f}: {e} / {n}" for m, e, n in self.points[-10:]))
        self.update()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text = QColor(theme.PALETTES[theme.MODE]["text"])       # the app's text, light or dark
        muted = QColor(text)
        muted.setAlpha(140)
        grid = QColor(text)
        grid.setAlpha(28)
        f = QFont(self.font())
        f.setPixelSize(12)
        p.setFont(f)
        w, h = self.width(), self.height()
        left, right, top, bottom = 52, 52, 26, 24
        area = QRectF(left, top, max(10, w - left - right), max(10, h - top - bottom))
        # the legend, on top
        x = left
        for label, color in ((self.labels[0], EXP_COLOR), (self.labels[1], NET_COLOR)):
            p.setPen(QPen(QColor(color), 3))
            p.drawLine(QPointF(x, 12), QPointF(x + 16, 12))
            p.setPen(muted)
            p.drawText(QPointF(x + 22, 16), label)
            x += 30 + p.fontMetrics().horizontalAdvance(label) + 18
        p.setPen(QPen(grid, 1))
        for i in range(5):
            y = area.top() + area.height() * i / 4
            p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
        if len(self.points) < 2:
            p.setPen(muted)
            p.drawText(area, Qt.AlignCenter, "…")
            return
        mins = [m for m, _, _ in self.points]
        span = max(1e-6, mins[-1] - mins[0])

        def line(values, color, axis_x, align):
            lo, hi = min(0, min(values)), max(values)
            hi = hi if hi > lo else lo + 1
            path = QPainterPath()
            for i, (m, v) in enumerate(zip(mins, values)):
                pt = QPointF(area.left() + area.width() * (m - mins[0]) / span,
                             area.bottom() - area.height() * (v - lo) / (hi - lo))
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(QColor(color), 2.4))
            p.drawPath(path)
            p.setPen(QColor(color))
            for v, y in ((hi, area.top()), (lo, area.bottom())):
                r = QRectF(axis_x, y - 8, left - 6, 16)
                p.drawText(r, align | Qt.AlignVCenter, _short(v))
        line([e for _, e, _ in self.points], EXP_COLOR, 0, Qt.AlignRight)
        line([n for _, _, n in self.points], NET_COLOR, area.right() + 6, Qt.AlignLeft)
        # the time axis: start, middle and end, in minutes
        p.setPen(muted)
        for frac in (0, 0.5, 1):
            m = mins[0] + span * frac
            xx = area.left() + area.width() * frac
            p.drawText(QRectF(xx - 40, area.bottom() + 4, 80, 18), Qt.AlignHCenter | Qt.AlignTop,
                       f"{m:.0f} {self.labels[2]}")
