"""The "NPCs on this map" window over the game: a see-through, always-on-top panel listing every NPC the KB knows
on the player's map (the map itself comes from the minimap reads, ui/location.LOCATION.here). Clicking one guides
the player to it: LOCATION.guide carries its spot (the game-minimap dots window rings it there), and a small window
of its own, docked left of the list, draws the map's picture with the NPC's green dot, the player's blue one and
where to go. Neither takes the keyboard (the game keeps it) nor is seen by the minimap reads (SetWindowDisplayAffinity,
as the portal dots)."""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
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
QWIDGETSIZE_MAX = 16777215           # Qt's "no maximum" (the fold's fixed height lifted again)
DEFAULT_W, DEFAULT_H = 320, 440     # what opens beside the minimap box before it is ever resized
RADIUS = 16                         # the panel's own rounding (a touch tighter than the app's windows)
GAP = 8                             # kept between it and the minimap box it opens beside
SIDE, TOP, BOTTOM = SHADOW + 18, SHADOW + 10, SHADOW + 8    # content margins, past the edge-resize rim
GUIDE_W = 270                       # the guide window's width (its height follows the picture)
GUIDE_GAP = 6                       # between the guide's panel and the list's
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles
H_NEAR, V_NEAR = 60, 90             # map units: nearer than this aside / above-below is "right by you"
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.3, 1.0, 0.85
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11   # Windows 10 2004+: screen captures see / never see the window

# the dark glass presets' own look: light text and see-through white fills, whatever theme the app is in
PANEL = (24, 24, 27)
DARK_LOOK = {"text": "#F5F5F7", "muted": "rgba(235,235,245,0.64)", "fill1": "rgba(255,255,255,0.08)",
             "fill2": "rgba(255,255,255,0.14)", "stroke": "rgba(255,255,255,0.14)", "scroll": "rgba(255,255,255,0.25)",
             "ring": "rgba(255,255,255,0.30)", "rim": (60, 22)}

# the colours every window over the game follows (the bar's ⚙ panel and Settings > Minimap pick them): the
# glass, and the accent that lights a lit or selected button with the text colour that reads on it. "app" (the
# default of both) is the app's own look — its light or dark glass, text and fills (look_palette), its orange —
# the rest are dark tints for the light text, and the accents besides. A stale name falls back to "app".
TINTS = {"app": ("tbs_tint_app", None),
         "dark": ("tbs_tint_dark", PANEL),
         "midnight": ("tbs_tint_midnight", (17, 24, 43)),
         "forest": ("tbs_tint_forest", (13, 31, 22)),
         "wine": ("tbs_tint_wine", (38, 17, 27)),
         "slate": ("tbs_tint_slate", (36, 39, 44))}
ACCENTS = {"app": ("tbs_accent_app", theme.ORANGE_DEEP, theme.ON_ORANGE),
           "green": ("tbs_accent_green", "#2E9E5B", "#07230F"),
           "blue": ("tbs_accent_blue", "#3D8BD6", "#082138"),
           "purple": ("tbs_accent_purple", "#8265D6", "#180C2C"),
           "orange": ("tbs_accent_orange", "#E08A3C", "#2F1706"),
           "pink": ("tbs_accent_pink", "#D65C8E", "#2F081A"),
           "teal": ("tbs_accent_teal", "#2FA8A0", "#062623")}
OPACITY_STEPS = (0.3, 0.5, 0.7, 0.85, 1.0)   # the ⚙ panel's transparency choices


def _picked(settings, key: str):
    """A setting as Settings or a plain dict gives it; None when neither has it (a test's stub settings)."""
    if settings is None:
        return None
    try:
        return settings[key]
    except (KeyError, TypeError):
        return None


def app_palette(settings) -> dict:
    """The app's own colours (theme.PALETTES) in the appearance the settings give: Windows high contrast when it
    is on (theme.set_mode found it), else settings["appearance"], else the mode the app is styled in now."""
    if theme.HIGH_CONTRAST:
        return theme.P()
    mode = _picked(settings, "appearance")
    return theme.PALETTES[mode] if mode in theme.PALETTES else theme.P()


def look_palette(settings) -> dict:
    """Every colour a window over the game draws in, for the glass the settings pick: "glass" (the rgb
    _paint_glass fills), "text", "muted", "fill1" (a button), "fill2" (its hover), "stroke" (a border),
    "scroll", "ring" (an unchosen swatch's ring) and "rim" (the glass edge's top and side alpha). The "app"
    glass is the app's own, light or dark as its appearance: its panel colour, text, muted text, and its
    chips' fills (fill2 a button, fill3 its hover, as its #Chip); a dark tint keeps the light text."""
    name = _picked(settings, "overlay_tint")
    entry = TINTS.get(name) if isinstance(name, str) else None
    if entry is not None and entry[1] is not None:
        return {**DARK_LOOK, "glass": entry[1]}
    c = app_palette(settings)
    light = sum(c["glass"]) > 3 * 128
    return {"glass": c["glass"], "text": c["text"], "muted": c["muted"], "fill1": c["fill2"], "fill2": c["fill3"],
            "stroke": c["stroke"], "scroll": c["scroll"], "ring": "rgba(0,0,0,0.25)" if light else DARK_LOOK["ring"],
            "rim": (c["rim_top"], c["rim"])}


def tint_of(settings) -> tuple[int, int, int]:
    """The glass fill the settings pick (a name TINTS doesn't know: the app's own)."""
    return look_palette(settings)["glass"]


def accent_of(settings) -> tuple[str, str]:
    """The colour that lights a lit or selected button, and the text colour on it (a name ACCENTS doesn't
    know: the app's orange)."""
    name = _picked(settings, "overlay_accent")
    entry = (ACCENTS.get(name) if isinstance(name, str) else None) or ACCENTS["app"]
    return entry[1], entry[2]


_look = look_palette(None)      # the look _paint_glass paints right now: set from the settings by apply_look


def apply_look(settings) -> None:
    """The one place the windows-over-the-game look follows the settings: the glass every _paint_glass paints
    (the text, fills and accent each window takes itself in its own style sheet, from look_palette and
    accent_of). The app calls this at start, whenever the glass is picked and whenever its own appearance
    changes, so every window repaints with none of its own code."""
    global _look
    _look = look_palette(settings)


def current_tint() -> tuple[int, int, int]:
    """The tint _paint_glass is painting (the tests ask; a window never needs it)."""
    return _look["glass"]


def qss(settings) -> str:
    """The window's own style at the look the settings pick: its text and fills in the glass's palette, [sel]
    rows lit in the accent. Rebuilt whenever the look is picked or the app's appearance changes, so a window
    follows with no code of its own."""
    c = look_palette(settings)
    accent, on_accent = accent_of(settings)
    return f"""
QLabel {{ color: {c['text']}; }}
#Title {{ color: {c['text']}; font-weight: 600; }}
#Status {{ color: {c['muted']}; }}
#Inside {{ color: {c['muted']}; font-size: 11px; font-weight: 600; padding: 6px 2px 0 2px; }}
#GuideLine {{ color: {c['text']}; }}
QPushButton {{ background: {c['fill1']}; color: {c['text']}; border: none; border-radius: 8px;
              padding: 6px 10px; text-align: left; }}
QPushButton:hover {{ background: {c['fill2']}; }}
QPushButton[sel="true"] {{ background: {accent}; color: {on_accent}; font-weight: 600; }}
QToolButton {{ background: transparent; color: {c['muted']}; border: none; border-radius: 6px;
              padding: 2px 7px; font-size: 13px; }}
QToolButton:hover {{ background: {c['fill2']}; color: {c['text']}; }}
QScrollArea {{ background: transparent; border: none; }}
#Rows {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


@dataclass(frozen=True)
class NpcHere:
    """One row: an NPC on the map, and where it stands on the map's minimap picture (as fractions of its size,
    None when the KB can't place it: no picture, or it stands off it). One inside a building off the map (a shop,
    the hospital) has that building's map id in `inside`, and its spot is the building's door on this map."""
    key: str                            # its KB key, "npc/<id>"
    name: str
    spot: tuple[float, float] | None
    inside: str = ""


_DOOR = re.compile(r"in\d")             # the game names a building's door portal in00, in01_1, jobin00...


def buildings_off(g, mid: str) -> list:
    """The buildings entered from this map, as the portal legs that lead in (Leg.to the building, Leg.spot its door
    here): a door the game names in00/in01_1/jobin00, into a map that leads back here. That's Kerning City's shops,
    hospital, hideout and civic center, not its subway or the construction site (live data, 2026-10-10)."""
    edges = getattr(g, "edges", {})
    return [leg for leg in edges.get(mid, ())
            if leg.kind == "portal" and _DOOR.search(leg.via or "") and leg.to in g.known
            and any(back.to == mid for back in edges.get(leg.to, ()))]


def npcs_on(g, mid: str) -> list[NpcHere]:
    """Every NPC the KB knows on this map, one row per id (the first place it stands — the same cab drawn twice
    is one cab), sorted by name, placed on the map's picture (its minimap, or a shop's room: Graph.picture_spot);
    then those inside each building off it (buildings_off), building by building (sorted by its name), each guided
    to its door. [] for a map the KB doesn't have."""
    m = g.known.get(mid)
    if m is None:
        return []
    seen: set[str] = set()

    def rows(of, spot_of, inside="") -> list[NpcHere]:
        out = []
        for n in g.known[of].npcs:
            nid = str(n.get("id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            out.append(NpcHere(f"npc/{nid}", str(n.get("name") or nid), spot_of(n), inside))
        return sorted(out, key=lambda x: x.name.casefold())

    out = rows(mid, lambda n: g.picture_spot(mid, n))
    for leg in sorted(buildings_off(g, mid), key=lambda leg: g.name(leg.to).casefold()):
        out += rows(leg.to, lambda n, door=leg.spot: door, leg.to)
    return out


def direction(g, mid: str, you, spot) -> str:
    """The i18n key saying where the NPC is from the player, measured in map units (the fraction between them
    times the picture's own size in map units, so it reads the same on small and large maps): 'left'/'right' only
    from 60 units aside, 'up'/'down' from 90 — nearer than both is "right by you". The screen's y grows downward,
    so an NPC above the player (the smaller fraction) is 'up'. No spot for the NPC: npc_guide_unplaced. No player
    dot (inside a shop the game folds its minimap, so there never is one) or nothing to measure on:
    npc_guide_marked, its dot on the picture is the guide."""
    if spot is None:
        return "npc_guide_unplaced"
    m = g.known.get(mid)
    mm = (m.minimap or m.scene) if m is not None else None
    if you is None or not mm or not mm[0] or not mm[1]:
        return "npc_guide_marked"
    dx, dy = (spot[0] - you[0]) * mm[0], (spot[1] - you[1]) * mm[1]
    h = "left" if dx <= -H_NEAR else "right" if dx >= H_NEAR else ""
    v = "up" if dy <= -V_NEAR else "down" if dy >= V_NEAR else ""
    if h and v:
        return f"npc_guide_{h}_{v}"
    return f"npc_guide_{h or v}" if h or v else "npc_guide_here"


def _paint_glass(w: QWidget, opacity: float) -> None:
    """A floating panel's own glass: the soft shadow, then a rounded fill in the tint the player picked (the
    ⚙ panel's or Settings' colour, apply_look) at the opacity they asked, so the game shows through as much
    as they asked (settings: npc_overlay_opacity)."""
    p = QPainter(w)
    p.setRenderHint(QPainter.Antialiasing)
    for i in range(SHADOW, 0, -2):
        sh = QPainterPath()
        sh.addRoundedRect(QRectF(w.rect()).adjusted(SHADOW - i, SHADOW - i + 3, -(SHADOW - i), -(SHADOW - i) + 3),
                          RADIUS + i, RADIUS + i)
        p.fillPath(sh, QColor(0, 0, 0, int(26 * (1 - i / SHADOW)) + 2))
    path = QPainterPath()
    path.addRoundedRect(QRectF(w.rect()).adjusted(SHADOW + 0.5, SHADOW + 0.5, -SHADOW - 0.5, -SHADOW - 0.5),
                        RADIUS, RADIUS)
    fill = QColor(*_look["glass"])
    fill.setAlphaF(opacity)
    p.fillPath(path, fill)
    top, side = _look["rim"]
    rim = QLinearGradient(0, SHADOW, 0, w.height() - SHADOW)
    rim.setColorAt(0.0, QColor(255, 255, 255, top))
    rim.setColorAt(0.4, QColor(255, 255, 255, side))
    rim.setColorAt(1.0, QColor(255, 255, 255, side // 2))
    p.setPen(QPen(rim, 1))
    p.drawPath(path)
    p.end()


def set_capturable(w: QWidget, settings, keep_out: bool = False) -> None:
    """Screen captures see the windows over the game (the owner, 2026-10-11: players screenshot and stream them, no
    setting). keep_out: the one layer drawn ON the game's minimap (the hidden-portal dots and the guide's ring,
    ui/portaldots.py) stays out of every capture (WDA_EXCLUDEFROMCAPTURE), or the minimap reads would see their own
    dots over the player's yellow one. Set on every show. Older Windows draws it into the shot either way, where the
    reader's matching leaves the markers out."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        mode = WDA_EXCLUDEFROMCAPTURE if keep_out else WDA_NONE
        if not ctypes.windll.user32.SetWindowDisplayAffinity(int(w.winId()), mode):
            log.debug("overlay: display affinity not available")
    except Exception as e:  # noqa: BLE001 - the window still shows; only who captures it changes
        log.debug("overlay: display affinity failed: %r", e)


def _flags() -> Qt.WindowType:
    """Over the game, frameless, and never taking the keyboard from it (clicks still work)."""
    return Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus


def minimap_box(settings) -> QRect | None:
    """The drawn minimap box on the screen (Qt's coordinates), or None with no box drawn."""
    region = _picked(settings, "minimap_region")
    if not isinstance(region, dict):
        return None
    screens = [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]
    try:
        box, _ = from_capture(region, screens, scale=sys.platform != "darwin")
    except (KeyError, TypeError, ValueError):
        return None
    return box


def _panel(w: QWidget, at: QPoint | None = None) -> QRect:
    """The window's drawn panel (its rect less the transparent shadow rim), at `at` or where it is."""
    r = QRect(at if at is not None else w.pos(), w.size())
    return r.adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)


def keep_off_minimap(w: QWidget, settings) -> bool:
    """A window over the game dragged onto the drawn minimap box stops at its edge: it would cover the very box the
    minimap reads take (now that captures see the windows, the owner's 2026-10-11). Moved out by the shortest way;
    True when it was moved."""
    box = minimap_box(settings)
    panel = _panel(w)
    if box is None or not panel.intersects(box) or w.__dict__.get("_nudging"):
        return False
    shifts = [QPoint(box.left() - 1 - panel.right(), 0), QPoint(box.right() + 1 - panel.left(), 0),
              QPoint(0, box.top() - 1 - panel.bottom()), QPoint(0, box.bottom() + 1 - panel.top())]
    w._nudging = True
    try:
        w.move(w.pos() + min(shifts, key=lambda d: abs(d.x()) + abs(d.y())))
    finally:
        w._nudging = False
    return True


def dock_beside(win: QWidget, owner: QWidget, gap: int = GUIDE_GAP, settings=None) -> None:
    """A small panel of its own docked beside its owner (its left, or its right where the owner's screen has no
    room left of it, or where its left would cover the drawn minimap box): top edges lined up, kept on that screen
    top to bottom."""
    left = owner.x() + 2 * SHADOW - gap - win.width()
    right = owner.x() + owner.width() - 2 * SHADOW + gap
    screen = QGuiApplication.screenAt(owner.geometry().center()) or QGuiApplication.primaryScreen()
    room = screen.availableGeometry()
    y = min(max(owner.y(), room.top()), max(room.top(), room.bottom() - win.height() + 1))
    box = minimap_box(settings)
    covers = box is not None and _panel(win, QPoint(left, y)).intersects(box)
    x = left if left + SHADOW >= room.left() and not covers else right
    if win.pos() != QPoint(x, y):
        win.move(x, y)


class GuideWindow(QWidget):
    """The way to the picked NPC, a small window of its own docked beside the list (its left, or its right where
    the screen has no room): the NPC's name with a ✕, the sentence saying where it is, the map's picture with its
    green dot and the player's blue one, and Stop guiding. It moves with the list; its height follows the picture."""

    def __init__(self, owner: NpcOverlay):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(GUIDE_W)
        self.setStyleSheet(qss(owner._settings))
        self._owner = owner
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(objectName="Title")
        self.title.setWordWrap(True)
        self.close_btn = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.clicked.connect(lambda _=False: owner._clear_pick())
        head.addWidget(self.title, 1)
        head.addWidget(self.close_btn, 0, Qt.AlignTop)
        col.addLayout(head)
        self.line = QLabel(objectName="GuideLine")
        self.line.setWordWrap(True)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignHCenter)
        self.clear = QPushButton()
        self.clear.setCursor(Qt.PointingHandCursor)
        self.clear.setFocusPolicy(Qt.NoFocus)
        self.clear.clicked.connect(lambda _=False: owner._clear_pick())
        col.addWidget(self.line)
        col.addWidget(self.pic, 0, Qt.AlignHCenter)
        col.addWidget(self.clear)

    def pic_cap(self) -> int:
        return GUIDE_W - 2 * SIDE - 4

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._owner._opacity)

    def show_beside(self) -> None:
        """Shown (or kept) docked to the list at the size its contents need."""
        self.adjustSize()
        self.dock()
        if not self.isVisible():
            self.show()
        set_capturable(self, self._owner._settings)
        self.update()

    def dock(self) -> None:
        """Beside the list's panel, top edges lined up: on its left, or on its right when the list's screen has
        no room left of it; kept on that screen top to bottom."""
        dock_beside(self, self._owner, settings=self._owner._settings)


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

    closed = Signal()               # the ✕ turned the setting off: the toolbar's NPCs-here button unchecks
    SHOW_KEY, GEOM_KEY = "npc_overlay", "npc_overlay_geom"     # its setting and its place (MonsterOverlay: its own)

    def __init__(self, kb: KnowledgeBase, settings: Settings, t: I18n):
        super().__init__(None, _flags())
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
        self._heads: list[tuple[QLabel, str]] = []     # each building's heading over its NPCs, with its map id
        self._sel: NpcHere | None = None
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._collapsed = False              # folded to the header: no NPCs to list (_fit)
        self._full_h: int | None = None      # the height it opens back to from the fold
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self.setStyleSheet(self._qss())
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
        self._guide = GuideWindow(self)                  # the way to the picked NPC, a window of its own
        self._line, self._pic, self._clear = self._guide.line, self._guide.pic, self._guide.clear
        self._guide.setLayoutDirection(self.layoutDirection())
        self._retext()

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._opacity)

    # ------------------------------------------------------------ text (all of it, for apply_language)

    def _retext(self) -> None:
        t, rtl = self.t, self.t.rtl
        name = routes.of(self._kb).name(self._mid) if self._mid else ""
        title = t("npc_overlay_title")
        self._title.setText(bidi.plain(f"{title} · {name}" if name and name != self._mid else title, rtl))
        self._close.setAccessibleName(t("npc_overlay_close"))
        self._close.setToolTip(t("npc_overlay_close"))
        self._clear.setText(bidi.plain(t("npc_overlay_clear"), rtl))
        self._guide.close_btn.setAccessibleName(t("npc_overlay_clear"))
        self._guide.close_btn.setToolTip(t("npc_overlay_clear"))
        for row, npc in zip(self._rows, self._npcs):
            row.setText(bidi.ltr_name(npc.name, rtl))
        g = routes.of(self._kb)
        for head, inside in self._heads:
            head.setText(bidi.plain(t("npc_overlay_inside", place=g.name(inside)), rtl))
        self._retext_status()
        self._redraw_guide()

    def _retext_status(self) -> None:
        t = self.t
        key = ("npc_overlay_unknown" if self._mid is None else
               "npc_overlay_none" if not self._npcs else
               "" if self._sel is not None else "npc_overlay_pick")
        self._status.setText(bidi.plain(t(key), t.rtl) if key else "")
        self._fit()

    def _fit(self) -> None:
        """No NPCs to list (a map with none, or the map not known yet): the window folds to its header and the
        line saying so, and can't be stretched taller; NPCs again, and it opens back to the height it had."""
        empty = not self._npcs
        if empty == self._collapsed:
            return
        self._collapsed = empty
        self._scroll.setVisible(not empty)
        if empty:
            self._full_h = self.height()
            self.setMinimumHeight(0)
            self.layout().activate()
            self.setFixedHeight(self.layout().sizeHint().height())
        else:
            self.setMaximumHeight(QWIDGETSIZE_MAX)
            self.setMinimumHeight(MIN_H)
            self.resize(self.width(), max(MIN_H, self._full_h or DEFAULT_H))

    # ------------------------------------------------------------ the list

    def _rebuild(self, mid: str | None) -> None:
        """The rows for this map (a Qt slot too: LOCATION.changed names the map), each building's under a heading
        of its own. A new map — or none — lets the picked NPC and its guide go: the old spot is on another map's
        picture."""
        self._mid = mid
        self._npcs = npcs_on(routes.of(self._kb), mid) if mid else []
        while self._rows or self._heads:
            w = self._rows.pop() if self._rows else self._heads.pop()[0]
            self._rows_lay.removeWidget(w)
            w.deleteLater()
        self._sel = None
        LOCATION.set_guide(None)
        inside = ""
        for npc in self._npcs:
            if npc.inside != inside:
                inside = npc.inside
                head = QLabel(objectName="Inside")
                head.setWordWrap(True)
                self._heads.append((head, inside))
                self._rows_lay.insertWidget(self._rows_lay.count() - 1, head)
            row = QPushButton(bidi.ltr_name(npc.name, self.t.rtl))
            row.setFocusPolicy(Qt.NoFocus)
            row.setCursor(Qt.PointingHandCursor)
            row.clicked.connect(lambda _=False, n=npc: self._pick(n))
            self._rows.append(row)
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._guide.hide()
        self._retext()

    def _pick(self, npc: NpcHere) -> None:
        """A row clicked: guide the player to this NPC (one inside a building: to its door); the row clicked again
        lets it go."""
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
        self._guide.hide()

    def _mark_rows(self) -> None:
        for row, npc in zip(self._rows, self._npcs):
            on = self._sel is not None and npc.key == self._sel.key
            if row.property("sel") != on:
                row.setProperty("sel", on)
                row.style().unpolish(row)
                row.style().polish(row)

    # ------------------------------------------------------------ the guide window

    def _redraw_guide(self) -> None:
        """The picked NPC's way: the sentence and the map picture with its green dot and the player's blue one,
        redrawn while the player moves (a read of the same map), in its own window beside the list. One inside a
        building: which building, and the way to its door (orange, as the map window marks a portal)."""
        if self._sel is None or not self.isVisible():
            self._guide.hide()
            return
        g = routes.of(self._kb)
        here = LOCATION.here
        you = here.spot if here is not None and here.map == self._mid else None
        t, rtl, sel = self.t, self.t.rtl, self._sel
        self._guide.title.setText(bidi.ltr_name(sel.name, rtl))
        way = t(direction(g, self._mid, you, sel.spot), name=t("npc_guide_door", place=g.name(sel.inside))
                if sel.inside else sel.name)
        if sel.inside:
            way = t("npc_guide_inside", name=sel.name, place=g.name(sel.inside)) + " " + way
        self._line.setText(bidi.plain(way, rtl))
        pm = mapview.route_picture(g.minimap(self._mid), sel.spot, not sel.inside, you, cap_w=self._guide.pic_cap())
        if pm.isNull():
            self._pic.clear()
        else:
            self._pic.setPixmap(pm)
        self._guide.show_beside()

    # ------------------------------------------------------------ place, move, resize

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: beside the minimap box again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings[self.GEOM_KEY]
        size = QSize(self.width(), self._full_h if self._collapsed and self._full_h else self.height())
        if isinstance(saved, dict) and isinstance(saved.get("w"), int) and isinstance(saved.get("h"), int):
            size = mapview.clamp_size(saved["w"], saved["h"], screens, QSize(MIN_W, MIN_H))
        spot = mapview.saved_spot(saved, size, screens)
        if self._collapsed:             # folded: the saved height waits for a map with NPCs
            self._full_h = size.height()
            size = QSize(size.width(), self.height())
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
        if keep_off_minimap(self, self._settings):
            return                  # moved again, off the minimap box: that move's own event follows
        self._geom.start()
        if self._guide.isVisible():
            self._guide.dock()      # the guide moves with the list
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        if self._guide.isVisible():
            self._guide.dock()      # a left-edge drag moves the list's left side
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        self._guide.hide()
        if self._placed:             # turned off or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:            # folded, the height kept is the one it opens back to
            h = self._full_h if self._collapsed and self._full_h else self.height()
            self._settings[self.GEOM_KEY] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": h}

    # ------------------------------------------------------------ the rest of the window's life

    def refresh(self, _what: object = None) -> None:
        """Show or hide from the setting, and follow the player's map (a Qt slot too: LOCATION.changed). The same
        map keeps the list and the picked NPC — only the guide panel follows the player's dot; a new one rebuilds
        the list and lets the guide go, except into the building the picked NPC is in: there the NPC is picked
        again, now guided to where it stands inside."""
        self._restyle()
        try:
            self._opacity = min(OPACITY_MAX, max(OPACITY_MIN, float(self._settings["npc_overlay_opacity"])))
        except (TypeError, ValueError):
            self._opacity = OPACITY_DEFAULT
        if not self._settings[self.SHOW_KEY]:
            if self._sel is not None:    # turned off from the bar or Settings: the ring on the game's minimap goes too
                self._clear_pick()
            if self.isVisible():
                self.hide()
            return
        here = LOCATION.here
        mid = here.map if here is not None else None
        if not self.isVisible():
            self._place()
            self.show()
            set_capturable(self, self._settings)
        if mid != self._mid:
            going = self._sel.key if self._sel is not None and mid and self._sel.inside == mid else None
            self._rebuild(mid)
            again = next((n for n in self._npcs if n.key == going), None)
            if again is not None:
                self._pick(again)
        else:
            self._retext_status()
            self._redraw_guide()
        self.update()
        self._guide.update()

    def _restyle(self) -> None:
        """The accent the settings pick, in this window's and the guide's own style sheets (the glass itself is
        painted in the tint by _paint_glass); skipped when it is the same, a restyle re-polishing every row."""
        sheet = self._qss()
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        guide = qss(self._settings)
        if guide != self._guide.styleSheet():
            self._guide.setStyleSheet(guide)

    def _qss(self) -> str:
        """This window's own style sheet at the accent the settings pick (MonsterOverlay adds its rows' look)."""
        return qss(self._settings)

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._rebuild(self._mid)    # the ids may all have moved: the list is read again, the pick let go
        self.refresh()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._guide.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the Settings switch follows), the guide gone, the window hidden."""
        self._settings[self.SHOW_KEY] = False
        self._clear_pick()
        self.hide()
        self.closed.emit()

    def closeEvent(self, e) -> None:
        self._guide.close()
        super().closeEvent(e)
