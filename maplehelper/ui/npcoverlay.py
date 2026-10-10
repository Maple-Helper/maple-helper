"""The "NPCs on this map" window over the game: a see-through, always-on-top panel listing every NPC the KB knows
on the player's map (the map itself comes from the minimap reads, ui/location.LOCATION.here). Clicking one guides
the player to it: LOCATION.guide carries its spot (the game-minimap dots window rings it there), and the panel
draws the map's picture with the NPC's green dot, the player's blue one and the way between them. Never takes the
keyboard (the game keeps it) and is never seen by the minimap reads (SetWindowDisplayAffinity, as the portal dots)."""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget

from .. import bidi, routes
from ..i18n import I18n
from ..kb import KnowledgeBase
from ..store import Settings
from . import mapview, theme
from .glass import SHADOW, EdgeResize
from .location import LOCATION
from .regionpick import from_capture

log = logging.getLogger("maplehelper")

MIN_W, MIN_H = 220, 260             # the smallest the player can drag the window to
DEFAULT_W, DEFAULT_H = 320, 440     # what opens beside the minimap box before it is ever resized
RADIUS = 16                         # the panel's own rounding (a touch tighter than the app's windows)
GAP = 8                             # kept between it and the minimap box it opens beside
SIDE, TOP, BOTTOM = SHADOW + 18, SHADOW + 10, SHADOW + 8    # content margins, past the edge-resize rim
GUIDE_PAD = 10                      # the guide panel's own padding, inside those
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles
PICS_DEBOUNCE_MS = 120              # the guide picture is redrawn at the new width the same way
H_NEAR, V_NEAR = 60, 90             # map units: nearer than this aside / above-below is "right by you"
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.3, 1.0, 0.85
WDA_EXCLUDEFROMCAPTURE = 0x11       # Windows 10 2004+: screen captures (the minimap reads) never see this window

# the panel floats over the game, whatever theme the app's own windows are in: its own dark glass and light text
PANEL = (24, 24, 27)
TEXT = "#F5F5F7"
MUTED = "rgba(235,235,245,0.64)"
FILL1 = "rgba(255,255,255,0.08)"
FILL2 = "rgba(255,255,255,0.14)"
SEL_TEXT = "#07230F"                # on the selected row's green, as the NPC's dot is ringed dark green

QSS = f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#GuideLine {{ color: {TEXT}; }}
#Guide {{ background: {FILL1}; border-radius: 10px; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 6px 10px; text-align: left; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton[sel="true"] {{ background: {theme.GOOD_TEXT_LIGHT}; color: {SEL_TEXT}; font-weight: 600; }}
QToolButton {{ background: transparent; color: {MUTED}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {FILL2}; color: {TEXT}; }}
QScrollArea {{ background: transparent; border: none; }}
#Rows {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


@dataclass(frozen=True)
class NpcHere:
    """One row: an NPC on the map, and where it stands on the map's minimap picture (as fractions of its size,
    None when the KB can't place it: no picture, or it stands off it)."""
    key: str                            # its KB key, "npc/<id>"
    name: str
    spot: tuple[float, float] | None


def npcs_on(g, mid: str) -> list[NpcHere]:
    """Every NPC the KB knows on this map, one row per id (the first place it stands — the same cab drawn twice
    is one cab), sorted by name. [] for a map the KB doesn't have."""
    m = g.known.get(mid)
    if m is None:
        return []
    out, seen = [], set()
    for n in m.npcs:
        nid = str(n.get("id") or "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(NpcHere(f"npc/{nid}", str(n.get("name") or nid), g._spot(mid, n)))
    out.sort(key=lambda x: x.name.casefold())
    return out


def direction(g, mid: str, you, spot) -> str:
    """The i18n key saying where the NPC is from the player, measured in map units (the fraction between them
    times the minimap's own size, so it reads the same on small and large maps): 'left'/'right' only from 60
    units aside, 'up'/'down' from 90 — nearer than both is "right by you". The screen's y grows downward, so an
    NPC above the player (the smaller fraction) is 'up'. No player dot, no spot for the NPC, or no minimap to
    measure on: npc_guide_nowhere."""
    if you is None or spot is None:
        return "npc_guide_nowhere"
    m = g.known.get(mid)
    mm = m.minimap if m is not None else None
    if not mm or not mm[0] or not mm[1]:
        return "npc_guide_nowhere"
    dx, dy = (spot[0] - you[0]) * mm[0], (spot[1] - you[1]) * mm[1]
    h = "left" if dx <= -H_NEAR else "right" if dx >= H_NEAR else ""
    v = "up" if dy <= -V_NEAR else "down" if dy >= V_NEAR else ""
    if h and v:
        return f"npc_guide_{h}_{v}"
    return f"npc_guide_{h or v}" if h or v else "npc_guide_here"


class _Header(QWidget):
    """The title row: dragging it moves the window (the ✕ in it still clicks)."""

    def __init__(self):
        super().__init__()
        self._grab = None          # (its window is self.window(), not kept)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._grab = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._grab is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._grab)

    def mouseReleaseEvent(self, e):
        self._grab = None


class NpcOverlay(EdgeResize, QWidget):
    """The window itself. Clicks work (rows are picked, edges resize) but it never takes the keyboard from the
    game, and the minimap reads never see it. Its place is kept in npc_overlay_geom; it sets LOCATION.guide —
    the ring on the game's own minimap is drawn elsewhere (ui/portaldots.py)."""

    def __init__(self, kb: KnowledgeBase, settings: Settings, t: I18n):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)          # hovering the rim shows the resize cursor
        self.setMinimumSize(MIN_W, MIN_H)
        self.resize(DEFAULT_W, DEFAULT_H)
        self._kb = kb
        self._settings = settings
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._opacity = OPACITY_DEFAULT
        self._mid: str | None = None         # the map the list is of
        self._npcs: list[NpcHere] = []
        self._rows: list[QPushButton] = []
        self._sel: NpcHere | None = None
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._excluded = False
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self._pics = QTimer(self, singleShot=True, interval=PICS_DEBOUNCE_MS, timeout=self._redraw_guide)
        self.setStyleSheet(QSS)
        self._build()
        LOCATION.changed.connect(self.refresh)

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
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.clicked.connect(self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        self._status = QLabel(objectName="Status")       # the map unknown / no NPCs / the pick hint
        self._status.setWordWrap(True)
        col.addWidget(self._status)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 0, 0, 0)
        self._rows_lay.setSpacing(4)
        self._rows_lay.addStretch(1)
        self._scroll.setWidget(rows)
        col.addWidget(self._scroll, 1)
        self._guide = QFrame(objectName="Guide")         # the way to the picked NPC
        self._guide.setVisible(False)
        glow = QVBoxLayout(self._guide)
        glow.setContentsMargins(GUIDE_PAD, GUIDE_PAD, GUIDE_PAD, GUIDE_PAD)
        glow.setSpacing(6)
        self._line = QLabel(objectName="GuideLine")
        self._line.setWordWrap(True)
        self._pic = QLabel()
        self._pic.setAlignment(Qt.AlignHCenter)
        self._clear = QPushButton()
        self._clear.setCursor(Qt.PointingHandCursor)
        self._clear.setFocusPolicy(Qt.NoFocus)
        self._clear.clicked.connect(self._clear_pick)
        glow.addWidget(self._line)
        glow.addWidget(self._pic, 0, Qt.AlignHCenter)
        glow.addWidget(self._clear)
        col.addWidget(self._guide)
        self._retext()

    def paintEvent(self, e) -> None:
        """The panel's own glass: the soft shadow, then a dark rounded fill at the player's chosen opacity, so
        the game shows through as much as they asked (settings: npc_overlay_opacity)."""
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        for i in range(SHADOW, 0, -2):
            sh = QPainterPath()
            sh.addRoundedRect(QRectF(self.rect()).adjusted(SHADOW - i, SHADOW - i + 3, -(SHADOW - i), -(SHADOW - i) + 3),
                              RADIUS + i, RADIUS + i)
            p.fillPath(sh, QColor(0, 0, 0, int(26 * (1 - i / SHADOW)) + 2))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(SHADOW + 0.5, SHADOW + 0.5, -SHADOW - 0.5, -SHADOW - 0.5),
                            RADIUS, RADIUS)
        fill = QColor(*PANEL)
        fill.setAlphaF(self._opacity)
        p.fillPath(path, fill)
        rim = QLinearGradient(0, SHADOW, 0, self.height() - SHADOW)
        rim.setColorAt(0.0, QColor(255, 255, 255, 60))
        rim.setColorAt(0.4, QColor(255, 255, 255, 22))
        rim.setColorAt(1.0, QColor(255, 255, 255, 11))
        p.setPen(QPen(rim, 1))
        p.drawPath(path)
        p.end()

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        name = routes.of(self._kb).name(self._mid) if self._mid else ""
        title = t("npc_overlay_title")
        self._title.setText(bidi.plain(f"{title} · {name}" if name and name != self._mid else title, rtl))
        self._close.setAccessibleName(t("npc_overlay_close"))
        self._close.setToolTip(t("npc_overlay_close"))
        self._clear.setText(bidi.plain(t("npc_overlay_clear"), rtl))
        for row, npc in zip(self._rows, self._npcs):
            row.setText(bidi.ltr_name(npc.name, rtl))
        self._retext_status()
        self._redraw_guide()

    def _retext_status(self) -> None:
        t = self.t
        key = ("npc_overlay_unknown" if self._mid is None else
               "npc_overlay_none" if not self._npcs else
               "" if self._sel is not None else "npc_overlay_pick")
        self._status.setText(bidi.plain(t(key), t.rtl) if key else "")

    # ------------------------------------------------------------ the list

    def _rebuild(self, mid: str | None) -> None:
        """The rows for this map (a Qt slot too: LOCATION.changed names the map). A new map — or none — lets the
        picked NPC and its guide go: the old spot is on another map's picture."""
        self._mid = mid
        self._npcs = npcs_on(routes.of(self._kb), mid) if mid else []
        while self._rows:
            row = self._rows.pop()
            self._rows_lay.removeWidget(row)
            row.deleteLater()
        self._sel = None
        LOCATION.set_guide(None)
        for npc in self._npcs:
            row = QPushButton(bidi.ltr_name(npc.name, self.t.rtl))
            row.setFocusPolicy(Qt.NoFocus)
            row.setCursor(Qt.PointingHandCursor)
            row.clicked.connect(lambda _=False, n=npc: self._pick(n))
            self._rows.append(row)
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._guide.setVisible(False)
        self._retext()

    def _pick(self, npc: NpcHere) -> None:
        """A row clicked: guide the player to this NPC; the row clicked again lets it go."""
        if self._sel is not None and self._sel.key == npc.key:
            self._clear_pick()
            return
        self._sel = npc
        LOCATION.set_guide((self._mid, npc.spot, npc.key) if npc.spot else None)
        self._mark_rows()
        self._retext_status()
        self._redraw_guide()

    def _clear_pick(self) -> None:
        self._sel = None
        LOCATION.set_guide(None)
        self._mark_rows()
        self._retext_status()
        self._guide.setVisible(False)

    def _mark_rows(self) -> None:
        for row, npc in zip(self._rows, self._npcs):
            on = self._sel is not None and npc.key == self._sel.key
            if row.property("sel") != on:
                row.setProperty("sel", on)
                row.style().unpolish(row)
                row.style().polish(row)

    # ------------------------------------------------------------ the guide panel

    def _redraw_guide(self) -> None:
        """The picked NPC's way: the sentence and the map picture with its green dot and the player's blue one,
        redrawn while the player moves (a read of the same map) and at the panel's current width."""
        if self._sel is None:
            self._guide.setVisible(False)
            return
        g = routes.of(self._kb)
        here = LOCATION.here
        you = here.spot if here is not None and here.map == self._mid else None
        self._line.setText(bidi.plain(self.t(direction(g, self._mid, you, self._sel.spot), name=self._sel.name),
                                      self.t.rtl))
        pm = mapview.route_picture(g.minimap(self._mid), self._sel.spot, True, you, cap_w=self._pic_cap())
        if pm.isNull():
            self._pic.clear()
        else:
            self._pic.setPixmap(pm)
        self._guide.setVisible(True)

    def _pic_cap(self) -> int:
        return max(120, self.width() - 2 * SIDE - 2 * GUIDE_PAD - 4)

    # ------------------------------------------------------------ place, move, resize

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: beside the minimap box again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings["npc_overlay_geom"]
        size = QSize(self.width(), self.height())
        if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int):
            size = mapview.clamp_size(saved["w"], saved["h"], screens, self.minimumSize())
        spot = mapview.saved_spot(saved, size, screens)
        self.setGeometry(QRect(spot if spot is not None else self._default_spot(size), size))
        self._placed = True

    def _default_spot(self, size: QSize) -> QPoint:
        """Just right of the drawn minimap box (the window is about the map in it), else the primary screen's
        top-left corner."""
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
                x = min(max(box.right() + 1 + GAP, screen.left()), screen.right() - size.width() + 1)
                y = min(max(box.top(), screen.top()), screen.bottom() - size.height() + 1)
                return QPoint(x, y)
        return primary.topLeft() + QPoint(SHADOW + 4, SHADOW + 4)

    def moveEvent(self, e) -> None:
        self._geom.start()
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        self._pics.start()          # the guide picture follows the panel's new width once the drag settles
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        if self._placed:             # turned off or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:
            self._settings["npc_overlay_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": self.height()}

    # ------------------------------------------------------------ the rest of the window's life

    def refresh(self, _what: object = None) -> None:
        """Show or hide from the setting, and follow the player's map (a Qt slot too: LOCATION.changed). The same
        map keeps the list and the picked NPC — only the guide panel follows the player's dot; a new one rebuilds
        the list and lets the guide go."""
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        if not self._settings["npc_overlay"]:
            if self.isVisible():
                self.hide()
            return
        here = LOCATION.here
        mid = here.map if here is not None else None
        if mid != self._mid:
            self._rebuild(mid)
        else:
            self._retext_status()
            self._redraw_guide()
        if not self.isVisible():
            self._place()
            self.show()
            self._exclude_from_capture()
        self.update()

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._rebuild(self._mid)    # the ids may all have moved: the list is read again, the pick let go
        self.refresh()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the Settings switch follows), the guide gone, the window hidden."""
        self._settings["npc_overlay"] = False
        self._clear_pick()
        self.hide()

    def _exclude_from_capture(self) -> None:
        """Keep the window out of the screenshots the minimap reads take: it may sit over the very box they read.
        Older Windows draws it into the shot, where the reader's picture matching may still survive it."""
        if self._excluded or sys.platform != "win32":
            return
        self._excluded = True
        try:
            import ctypes
            if not ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE):
                log.debug("npc overlay: capture exclusion not available")
        except Exception as e:  # noqa: BLE001 - the window still shows; only the reads may see it
            log.debug("npc overlay: capture exclusion failed: %r", e)
