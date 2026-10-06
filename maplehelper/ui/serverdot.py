"""The game-server dot in the chat's footer: green up, orange maintenance (or players reporting
trouble), grey not open yet / unknown. Its tooltip says what it means and when it was checked; a tap opens
NiaMeowDB's server-status page.

Live data from MeowDB, not the knowledge base (serverstatus.py says why): the poller asks every POLL_MINUTES while
the chat is open, backs off up to MAX_MINUTES while the site can't be reached, and never asks while the chat is
closed or minimized (Overlay.showEvent / hideEvent start and stop it).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .. import serverstatus
from ..osapi import open_url
from . import theme

POLL_MINUTES = 5
MAX_MINUTES = 60
MIN_GAP_SECONDS = 60      # reopening the chat right after a check doesn't ask again
COLORS = {"up": "#34C759", "maintenance": theme.ORANGE, "issues": theme.ORANGE}     # the rest: grey


class StatusPoller(QObject):
    """Asks serverstatus.fetch on a background thread; `status` delivers each answer (None: unreachable) on the
    GUI thread."""

    status = Signal(object)
    _answered = Signal(object)        # from the worker thread; queued onto the GUI thread

    def __init__(self, fetch=None, parent=None):
        super().__init__(parent)
        self._fetch = fetch or serverstatus.fetch
        self._timer = QTimer(self, singleShot=True, timeout=self._ask)
        self._answered.connect(self._done)
        self._minutes = POLL_MINUTES
        self._last = 0.0              # when the last check started
        self._busy = False
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        wait = max(0.0, MIN_GAP_SECONDS - (time.time() - self._last))
        self._timer.start(int(wait * 1000))

    def stop(self) -> None:
        self.running = False
        self._timer.stop()

    def _ask(self) -> None:
        if not self.running or self._busy:
            return
        self._busy, self._last = True, time.time()

        def work():
            found = self._fetch()
            try:
                self._answered.emit(found)
            except RuntimeError:
                pass                  # the app is quitting: the poller is gone
        threading.Thread(target=work, daemon=True).start()

    def _done(self, found) -> None:
        self._busy = False
        # back off while the site is unreachable (5, 10, 20, 40, 60 minutes), back to every 5 once it answers
        self._minutes = POLL_MINUTES if found is not None else min(MAX_MINUTES, self._minutes * 2)
        if self.running:
            self._timer.start(self._minutes * 60 * 1000)
        self.status.emit(found)


def when(ts: float | None) -> str:
    """A local time a player reads at a glance: "21:00" today, "6.10, 21:00" another day."""
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    return d.strftime("%H:%M") if d.date() == datetime.now().date() else f"{d.day}.{d.month}, {d:%H:%M}"


def tip(t, st: serverstatus.Status | None, now: float | None = None, asked: bool = True) -> str:
    """The dot's tooltip: the state in words, then where it comes from and when it was checked. asked=False: no
    answer yet this session (the first check is on its way), not "unreachable"."""
    if st is None and not asked:
        return t("server_checking")
    if st is None:
        head = t("server_unknown")
    elif st.state == "up":
        head = t("server_scheduled", time=when(st.notice_start)) if st.scheduled else t("server_up")
    elif st.state == "maintenance":
        head = t("server_maintenance_until", time=when(st.notice_end)) if st.notice_end and not st.notice_done \
            and st.notice_end > (now or time.time()) else t("server_maintenance")
    elif st.state == "issues":
        head = t("server_issues")
    elif st.state == "prelaunch":
        head = t("server_prelaunch_at", time=when(st.opens_at)) if st.opens_at else t("server_prelaunch")
    else:
        head = t("server_unknown")
    # the time of the check, not "just now": the tooltip is set when the answer comes and stays until the next one
    src = t("server_source", time=when(st.checked)) if st else t("server_source_offline")
    return f"{head}\n{src}"


class ServerDot(QWidget):
    """A small colored dot; the tooltip and accessible name say what it means."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "unknown"
        self.setFixedSize(QSize(12, 17))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)       # the tooltip is the information; the header's Tab order stays

    def set_status(self, st: serverstatus.Status | None, t, asked: bool = True) -> None:
        self.state = st.state if st else "unknown"
        text = tip(t, st, asked=asked)
        self.setToolTip(text)
        self.setAccessibleName(text.replace("\n", ". "))
        self.update()

    def color(self) -> QColor:
        c = COLORS.get(self.state)
        return QColor(c) if c else QColor(142, 142, 147)        # Apple's system grey reads on both appearances

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color())
        d = 8.0
        p.drawEllipse(QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d))
        p.end()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            open_url(serverstatus.PAGE)
        super().mouseReleaseEvent(e)
