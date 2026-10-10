"""The little bar over the game: the NPCs-here window's own switch, and the three searches — where a monster
lives, any NPC, any item (each asks the app to open ui/gamesearch.py in that mode). Like the NPCs window it
floats over the game: frameless, always on top, never taking the keyboard from it (clicks still work), dark
glass the minimap reads never see. Where the player left it is kept in game_toolbar_pos; with none saved it
opens just above the drawn minimap box."""
from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget

from .. import bidi
from ..i18n import I18n
from ..store import Settings
from . import mapview, theme
from .glass import SHADOW
from .npcoverlay import (
    FILL1,
    FILL2,
    GAP,
    GEOM_DEBOUNCE_MS,
    MUTED,
    OPACITY_DEFAULT,
    OPACITY_MAX,
    OPACITY_MIN,
    SEL_TEXT,
    TEXT,
    _exclude_from_capture,
    _flags,
    _paint_glass,
)
from .regionpick import from_capture

SIDE, TOP, BOTTOM = SHADOW + 12, SHADOW + 8, SHADOW + 8    # content margins, past the glass's shadow rim
GRIP = "⋮⋮"                                               # the drag handle's dots
KINDS = {"monster": "tb_monsters", "npc": "tb_npcs", "item": "tb_items"}   # each search button, and its i18n key
QSS = f"""
#Grip {{ color: {MUTED}; font-size: 13px; font-weight: 600; padding: 0 2px; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 5px 10px; text-align: center; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton:checked, QPushButton[on="true"] {{ background: {theme.GOOD_TEXT_LIGHT}; color: {SEL_TEXT}; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
"""


class _Grip(QLabel):
    """The dots at the bar's end: dragging them moves it (the buttons around them still click)."""

    def __init__(self):
        super().__init__(GRIP, objectName="Grip")
        self.setCursor(Qt.OpenHandCursor)
        self._grab = None          # (its window is self.window(), not kept)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._grab is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e):
        self._grab = None


class GameToolbar(QWidget):
    """The bar itself. The NPCs-here button flips settings["npc_overlay"] (npc_toggled: the window follows), the
    three search buttons ask for their search (search_requested: "monster", "npc" or "item"), and the ✕ turns the
    bar itself off. Clicks work but it never takes the keyboard from the game, and the minimap reads never see
    it. Its place is kept in game_toolbar_pos."""

    search_requested = Signal(str)     # which search to open: "monster" | "npc" | "item"
    npc_toggled = Signal()             # settings["npc_overlay"] flipped: the NPCs-here window follows

    def __init__(self, settings: Settings, t: I18n):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self._settings = settings
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._excluded = False
        self._mode = ""                      # the search window's mode, lit on its button ("" none)
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self.setStyleSheet(QSS)
        self._build()

    # ------------------------------------------------------------ the bar

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._grip = _Grip()
        row.addWidget(self._grip)
        self._npcs = QPushButton(checkable=True)
        self._npcs.setCursor(Qt.PointingHandCursor)
        self._npcs.setFocusPolicy(Qt.NoFocus)
        self._npcs.clicked.connect(self._npcs_clicked)
        row.addWidget(self._npcs)
        self._search: dict[str, QPushButton] = {}
        for kind in KINDS:
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=kind: self.search_requested.emit(k))
            self._search[kind] = b
            row.addWidget(b)
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.clicked.connect(self._close_clicked)
        row.addWidget(self._close)
        col.addLayout(row)
        self._retext()

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        self._grip.setAccessibleName(t("tb_move"))
        self._grip.setToolTip(t("tb_move"))
        self._npcs.setText(bidi.plain(t("tb_npcs_here"), rtl))
        self._npcs.setToolTip(t("tb_npcs_here_tip"))
        for kind, key in KINDS.items():
            self._search[kind].setText(bidi.plain(t(key), rtl))
            self._search[kind].setToolTip(t(key + "_tip"))
        self._close.setAccessibleName(t("tb_close"))
        self._close.setToolTip(t("tb_close"))
        self.adjustSize()

    # ------------------------------------------------------------ the buttons

    def _npcs_clicked(self, _=False) -> None:
        """The checkable button is the setting: the window itself follows the flip (npc_toggled)."""
        self._settings["npc_overlay"] = self._npcs.isChecked()
        self.npc_toggled.emit()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the Settings switch follows), the bar hidden until it's switched back on."""
        self._settings["game_toolbar"] = False
        self.hide()

    def set_search_mode(self, kind: str) -> None:
        """The search window's mode lit on its button (mode_changed: "" when it closed, none lit)."""
        if kind == self._mode:
            return
        self._mode = kind
        for k, b in self._search.items():
            on = k == kind
            if b.property("on") != on:
                b.setProperty("on", on)
                b.style().unpolish(b)
                b.style().polish(b)

    # ------------------------------------------------------------ place and move

    def _place(self) -> None:
        """Where the bar opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: above the minimap box again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        spot = mapview.saved_spot(self._settings["game_toolbar_pos"], self.size(), screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(), self.size()))
        self._placed = True

    def _default_spot(self) -> QPoint:
        """Just above the drawn minimap box (the bar is about the map in it), left edges lined up; else the
        primary screen's top centre."""
        screens = [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]
        region = self._settings["minimap_region"]
        primary = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(region, dict):
            try:
                box, _ = from_capture(region, screens, scale=sys.platform != "darwin")
            except (KeyError, TypeError, ValueError):
                box = None
            if box is not None:
                screen = next((g for g, _ in screens if g.intersects(box)), primary)
                x = min(max(box.left(), screen.left()), screen.right() - self.width() + 1)
                y = min(max(box.top() - self.height() - GAP, screen.top()),
                        max(screen.top(), screen.bottom() - self.height() + 1))
                return QPoint(x, y)
        return QPoint(primary.center().x() - self.width() // 2, primary.top() + SHADOW + 4)

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def hideEvent(self, e) -> None:
        if self._placed:             # turned off or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["game_toolbar_pos"] = {"x": self.x(), "y": self.y()}

    # ------------------------------------------------------------ the rest of the bar's life

    def refresh(self) -> None:
        """Show or hide from the setting, the NPCs-here button back in step with the window's own (its ✕ turned
        it off; the Settings switch may have turned it on), and the glass at the opacity the player picked."""
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        self._npcs.setChecked(bool(self._settings["npc_overlay"]))
        if not self._settings["game_toolbar"]:
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self._place()
            self.show()
            if not self._excluded:
                self._excluded = True
                _exclude_from_capture(self)
        self.update()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext()
