"""The loot log over the game: a small see-through card that stays on top of the game on its own (not inside a Maple
Helper window), with the running log's EXP, mesos and items, per hour, and the latest pickups.

It never takes focus from the game, drags from anywhere on it, and keeps its place (settings "loot_overlay_pos").
On Windows it is left out of screen captures: the loot log reads the game's messages from a screenshot of their box,
and a card dragged over that box would otherwise be read as messages (and the chat's screenshots would show it)."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import bidi, lootlog
from ..i18n import I18n

WIDTH = 260
PAD = 12
FEED = 4                    # the latest pickups shown under the totals
BG = QColor(18, 20, 26, 190)
FG = QColor(240, 240, 240)
DIM = QColor(175, 180, 190)
ACCENT = QColor(255, 140, 26)
CLOSE = 18                  # the "×" in the top corner: hides the card (the Loot log page's switch shows it again)


def exclude_from_capture(w: QWidget) -> None:
    """Windows 10 2004+: the window shows on screen but not in captures (WDA_EXCLUDEFROMCAPTURE)."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(int(w.winId()), 0x11)
    except (AttributeError, OSError):
        pass


def _short(n: int | None) -> str:
    if n is None:
        return "–"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 100_000:
        return f"{n / 1000:.1f}K"
    return f"{n:,}"


class LootOverlay(QWidget):
    def __init__(self, scanner, settings, kb):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
        self.setWindowTitle("Maple Helper")
        self.setCursor(Qt.SizeAllCursor)
        self.scanner, self.settings, self.kb = scanner, settings, kb
        self._grab: QPoint | None = None
        self._lines: list[tuple[str, QColor, bool]] = []
        self.setFixedWidth(WIDTH)
        # the clock and the per-hour figures move between pickups too
        self._clock = QTimer(self, interval=5000, timeout=lambda: self.isVisible() and self.refresh())
        self._clock.start()

    def sync(self) -> None:
        """Shown while a log runs and the player keeps it on (settings "loot_overlay"), hidden otherwise."""
        want = bool(self.settings["loot_overlay"]) and self.scanner.running(self.scanner.cid())
        if want and not self.isVisible():
            self.show_on_game()
        elif want:
            self.refresh()
        elif self.isVisible():
            self.hide()

    # ---------------------------------------------------------------- what it shows

    def lines(self) -> list[tuple[str, QColor, bool]]:
        """(text, colour, bold) rows, top to bottom, for the running log of the scanner's character."""
        t = I18n(self.settings["language"] or "he")
        cid = self.scanner.cid()
        lg = self.scanner.log_for(cid)
        rtl = t.rtl
        if lg is None or not self.scanner.running(cid):
            return [(t("tool_loot"), ACCENT, True), (t("loot_overlay_idle"), DIM, False)]
        sm = lootlog.summarize(self.kb, lg)
        m = int(sm.seconds) // 60
        clock = t("grind_hours", h=m // 60, m=f"{m % 60:02d}") if m >= 60 else t("grind_minutes", m=m)
        out = [(f"{t('tool_loot')} · {clock}", ACCENT, True)]
        num = lambda v: bidi.ltr_block(v, rtl)      # a number stays one block beside Hebrew words
        out = out + [(t("loot_overlay_exp", n=num(_short(sm.exp)), h=num(_short(sm.exp_h))), FG, False),
                     (t("loot_overlay_mesos", n=num(_short(sm.mesos)), h=num(_short(sm.mesos_h))), FG, False),
                     (t("loot_overlay_items", n=num(f"{sm.picked:,}"), v=num(_short(sm.value))), FG, False)]
        for e in lg.events[-FEED:][::-1]:
            if e.kind == "item":
                text = t("loot_feed_item", name=bidi.ltr_block(e.name, rtl), n=f"{e.amount:,}")
            else:
                text = t(f"loot_feed_{e.kind}", n=f"{e.amount:,}")
            out.append((text, DIM, False))
        if self.scanner.state in ("no_game", "covered"):
            out.append((t(f"loot_{self.scanner.state}"), DIM, False))
        return out

    def refresh(self) -> None:
        self._lines = self.lines()
        fm = QFontMetrics(self._font(False))
        self.setFixedHeight(2 * PAD + len(self._lines) * (fm.height() + 4))
        self.update()

    def _font(self, bold: bool) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        f.setBold(bold)
        return f

    # ---------------------------------------------------------------- show / place

    def show_on_game(self) -> None:
        self.refresh()
        pos = self.settings["loot_overlay_pos"]
        if isinstance(pos, dict) and all(isinstance(pos.get(k), int) for k in ("x", "y")) and self._on_screen(pos):
            self.move(pos["x"], pos["y"])
        else:
            scr = QGuiApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - WIDTH - 40, scr.top() + 160)
        self.show()
        exclude_from_capture(self)

    def _on_screen(self, pos: dict) -> bool:
        r = QRect(pos["x"], pos["y"], WIDTH, max(40, self.height()))
        return any(s.availableGeometry().intersects(r) for s in QGuiApplication.screens())

    # ---------------------------------------------------------------- input

    def _close_rect(self) -> QRect:
        rtl = (self.settings["language"] or "he") == "he"
        x = PAD - 4 if rtl else self.width() - PAD - CLOSE + 4
        return QRect(x, PAD - 4, CLOSE, CLOSE)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._close_rect().contains(e.position().toPoint()):
            self.settings["loot_overlay"] = False
            self.hide()
            self.scanner.changed.emit()        # the Loot log page's switch follows
            return
        self._grab = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._grab is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e):
        if self._grab is not None:
            self._grab = None
            self.settings["loot_overlay_pos"] = {"x": self.x(), "y": self.y()}

    # ---------------------------------------------------------------- painting

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)
        p.fillPath(path, BG)
        rtl = (self.settings["language"] or "he") == "he"
        align = (Qt.AlignRight if rtl else Qt.AlignLeft) | Qt.AlignAbsolute | Qt.AlignVCenter
        fm = QFontMetrics(self._font(False))
        y = PAD
        for i, (text, colour, bold) in enumerate(self._lines):
            p.setFont(self._font(bold))
            p.setPen(colour)
            room = self.width() - 2 * PAD - (CLOSE if i == 0 else 0)
            x = PAD + (CLOSE if i == 0 and rtl else 0)
            shown = QFontMetrics(self._font(bold)).elidedText(bidi.plain(text, rtl), Qt.ElideRight, room)
            p.drawText(QRect(x, y, room, fm.height() + 2), align, shown)
            y += fm.height() + 4
        c = self._close_rect().adjusted(5, 5, -5, -5)
        p.setPen(QPen(DIM, 1.6))
        p.drawLine(c.topLeft(), c.bottomRight())
        p.drawLine(c.topRight(), c.bottomLeft())
        p.end()
