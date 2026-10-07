"""First-run tour of the chat window: one control at a time, lit up, with a card that says what it does.

It runs once, right after the first onboarding (Settings → "Take the tour" plays it again). It is a child
of the chat window that covers it, dims everything but the current control and holds a small card with
Back / Next / Skip, so nothing in the chat itself changes while it runs.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import bidi
from . import theme
from .glass import _TabKeys

# (overlay attribute of the control, or None for a card in the middle; the i18n key of its texts)
STEPS = (
    (None, "tour_welcome"),
    ("profile_card", "tour_card"),
    ("profile_card.refresh", "tour_refresh"),
    ("profile_card.now_btn", "tour_now"),
    ("input", "tour_input"),
    ("recapture_btn", "tour_camera"),
    ("mic_btn", "tour_mic"),
    # the header in its on-screen order (overlay.py: search, news, guides, wishlist, play tools, settings), the News
    # megaphone included: it was skipped and the light jumped back and forth (audit OVL-23, UX-20)
    ("history_btn", "tour_history"),
    ("news_btn", "tour_news"),
    ("guides_btn", "tour_guides"),
    ("wish_btn", "tour_wish"),
    ("tools_btn", "tour_tools"),
    ("settings_btn", "tour_settings"),
    ("min_btn", "tour_min"),
    ("close_btn", "tour_close"),
    (None, "tour_done"),
)

PAD = 5            # room around the lit control


def _resolve(root, path: str | None):
    w = root
    for part in (path or "").split("."):
        if not part:
            return None
        w = getattr(w, part, None)
        if w is None:
            return None
    return w


class Tour(QWidget):
    finished = Signal()          # skipped or done: either way it doesn't come back by itself

    def __init__(self, overlay, t):
        super().__init__(overlay)
        self.overlay, self.t = overlay, t
        _TabKeys.watch()                 # which button the player Tabbed to (Enter on it)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self.steps = [(path, key) for path, key in STEPS if path is None or self._visible(path)]
        self.i = 0

        self.card = QFrame(self, objectName="TourCard")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        self.count = QLabel(objectName="TourCount")
        self.title = QLabel(objectName="TourTitle")
        self.title.setWordWrap(True)
        self.body = QLabel(objectName="TourBody")
        self.body.setWordWrap(True)
        for w in (self.count, self.title, self.body):
            w.setAlignment((Qt.AlignRight if t.rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignTop)
            lay.addWidget(w)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.skip_btn = QPushButton(t("tour_skip"), objectName="Link")
        self.skip_btn.clicked.connect(self.finish)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        self.back_btn = QPushButton(t("ob_back"), objectName="Secondary")
        self.back_btn.clicked.connect(lambda: self.go(self.i - 1))
        row.addWidget(self.back_btn)
        self.next_btn = QPushButton(objectName="Primary")
        self.next_btn.clicked.connect(lambda: self.go(self.i + 1))
        row.addWidget(self.next_btn)
        lay.addLayout(row)
        for b in (self.skip_btn, self.back_btn, self.next_btn):
            b.setAutoDefault(False)
        self._style()
        overlay.installEventFilter(self)

    def _visible(self, path: str) -> bool:
        w = _resolve(self.overlay, path)
        return w is not None and w.isVisibleTo(self.overlay)

    def _style(self):
        c = theme.P()
        self.card.setStyleSheet(f"""
            #TourCard {{ background: {"#2C2C2E" if theme.MODE == "dark" else "#FFFFFF"};
                         border: 1px solid rgba(255,149,51,0.55); border-radius: 14px; }}
            #TourCount {{ color: {theme.accent_text()}; font-size: 11px; font-weight: 600; }}
            #TourTitle {{ color: {c['text']}; font-weight: 700; }}
            #TourBody {{ color: {c['muted']}; }}
        """)

    # ------------------------------------------------------------------ flow

    def start(self):
        self.setGeometry(self.overlay.rect())
        self.show()
        self.raise_()
        self.go(0)
        self.next_btn.setFocus()

    def go(self, i: int):
        if i >= len(self.steps):
            self.finish()
            return
        self.i = max(0, i)
        _, key = self.steps[self.i]
        t, rtl = self.t, self.t.rtl
        last = self.i == len(self.steps) - 1
        # the numbered cards only: the welcome and the last card aren't counted, so it runs 1 / 13 .. 13 / 13 (it
        # started at "2 / 15", UX-20)
        self.count.setText(f"{self.i} / {len(self.steps) - 2}" if 0 < self.i < len(self.steps) - 1 else "")
        self.count.setVisible(bool(self.count.text()))
        self.title.setText(bidi.plain(t(f"{key}_title"), rtl))
        self.body.setText(bidi.plain(t(f"{key}_body"), rtl))
        self.back_btn.setVisible(0 < self.i)
        self.skip_btn.setVisible(not last)
        self.next_btn.setText(bidi.plain(t("tour_start") if self.i == 0 else t("tour_finish") if last
                                         else t("ob_next"), rtl))
        self._place()

    def finish(self):
        self.overlay.removeEventFilter(self)
        self.hide()
        # back to the question box: else focus lands on the first header button (search) and it stays lit
        self.overlay.input.setFocus()
        self.finished.emit()
        self.deleteLater()

    # ------------------------------------------------------------------ geometry & painting

    def target(self) -> QRect | None:
        path, _ = self.steps[self.i]
        w = _resolve(self.overlay, path)
        if w is None or not w.isVisibleTo(self.overlay):
            return None
        top_left = w.mapTo(self.overlay, w.rect().topLeft())
        return QRect(top_left, w.size()).adjusted(-PAD, -PAD, PAD, PAD)

    def _place(self):
        """The card goes under the lit control, or over it when there's no room below (the input row)."""
        panel = self.rect().adjusted(self.overlay.SHADOW + 10, self.overlay.SHADOW + 10,
                                     -self.overlay.SHADOW - 10, -self.overlay.SHADOW - 10)
        width = min(panel.width(), 420)
        self.card.setFixedWidth(width)
        # the height the wrapped text needs at this width: sizeHint() measured the labels before they knew their
        # width, and at the 16 px font the body's last line was cut ("...opens it again anytime, in")
        lay = self.card.layout()
        lay.activate()
        h = max(self.card.sizeHint().height(), lay.totalHeightForWidth(width))
        self.card.resize(width, h)
        x = panel.center().x() - width // 2
        r = self.target()
        if r is None:
            y = panel.center().y() - h // 2
        elif r.bottom() + 12 + h <= panel.bottom():
            y = r.bottom() + 12
        else:
            y = max(panel.top(), r.top() - 12 - h)
        self.card.move(x, y)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shade = QPainterPath()
        shade.addPath(self.overlay._panel_path())
        r = self.target()
        if r is not None:
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(r), 12, 12)
            shade = shade.subtracted(hole)
        p.fillPath(shade, QColor(0, 0, 0, 150))
        if r is not None:
            p.setPen(QPen(QColor(255, 149, 51), 2))
            p.drawRoundedRect(QRectF(r).adjusted(1, 1, -1, -1), 12, 12)

    def mousePressEvent(self, e):
        # a click on the lit control or the dim area doesn't reach the chat while the tour runs
        e.accept()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            self.finish()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            # a Skip or Back the player Tabbed to: Enter does that, not the next step (UX-14)
            w = self.focusWidget()
            if w in (self.skip_btn, self.back_btn) and _TabKeys.by_keyboard(w):
                w.click()
            else:
                self.go(self.i + 1)
        elif k in (Qt.Key_Left, Qt.Key_Right):
            forward = (k == Qt.Key_Left) == self.t.rtl
            self.go(self.i + 1 if forward else self.i - 1)
        else:
            super().keyPressEvent(e)

    def focusNextPrevChild(self, nxt: bool) -> bool:
        """Tab stays on the card's buttons. Passed up to the chat, it went to the search button under the dim
        layer, where Enter and Esc no longer reached the tour."""
        order = [b for b in (self.skip_btn, self.back_btn, self.next_btn) if b.isVisible()]
        if not order:
            return True
        cur = self.focusWidget()
        i = order.index(cur) if cur in order else (-1 if nxt else 0)
        order[(i + (1 if nxt else -1)) % len(order)].setFocus(Qt.TabFocusReason if nxt else Qt.BacktabFocusReason)
        return True

    def eventFilter(self, obj, ev):
        if obj is self.overlay and ev.type() in (QEvent.Resize, QEvent.LayoutRequest):
            self.setGeometry(self.overlay.rect())
            self._place()
        return False


__all__ = ["Tour", "STEPS", "QPointF"]
