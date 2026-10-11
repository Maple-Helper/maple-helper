"""The "NPCs on this map" window over the game: a see-through, always-on-top panel listing every NPC the KB knows
on the player's map (the map itself comes from the minimap reads, ui/location.LOCATION.here), each with its picture,
what it is as the KB's tables say (a coloured chip: Quest, Shop, Storage...) and, for one inside a building off the
map, which: each building's NPCs under a heading of its own that folds them away; a filter box over a long list.
Clicking one guides the player to it: LOCATION.guide carries its spot (the game-minimap dots window rings it there),
and a small window of its own, docked left of the list, says the way beside an arrow pointing it and draws the map's
picture with the NPC's green dot, the player's blue one and where to go. Neither takes the keyboard (the game keeps
it; only the filter box, once clicked, borrows it while the player types) nor is seen by the minimap reads
(SetWindowDisplayAffinity, as the portal dots)."""
from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QBoxLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QSizePolicy, QStyle, QStyleOptionButton, QStylePainter, QToolButton, QVBoxLayout,
                               QWidget)

from .. import bidi, routes, tables
from ..i18n import I18n
from ..kb import KnowledgeBase, memo
from ..store import Settings
from . import mapview, theme
from .controls import FlowLayout
from .glass import SHADOW, EdgeResize
from .location import LOCATION
from .regionpick import from_capture
from .widgets import fit_picture, zoom_on_hover

log = logging.getLogger("maplehelper")

MIN_W, MIN_H = 220, 260             # the smallest the player can drag the window to
QWIDGETSIZE_MAX = 16777215           # Qt's "no maximum" (the fold's fixed height lifted again)
DEFAULT_W, DEFAULT_H = 320, 440     # what opens beside the minimap box before it is ever resized
RADIUS = 16                         # the panel's own rounding (a touch tighter than the app's windows)
GAP = 8                             # kept between it and the minimap box it opens beside
SIDE, TOP, BOTTOM = SHADOW + 18, SHADOW + 10, SHADOW + 8    # content margins, past the edge-resize rim
GUIDE_W = 300                       # the guide window's width (its height follows the picture)
GUIDE_GAP = 6                       # between the guide's panel and the list's
GEOM_DEBOUNCE_MS = 400              # a move/resize saves the window's place once it settles
H_NEAR, V_NEAR = 60, 90             # map units: nearer than this aside / above-below is "right by you"
OPACITY_MIN, OPACITY_MAX, OPACITY_DEFAULT = 0.3, 1.0, 0.85
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11   # Windows 10 2004+: screen captures see / never see the window
PIC = 34                            # a row's picture of the NPC (an empty square without one: the names line up)
GUIDE_PIC = 44                      # the guide's picture of it, beside its name
FILTER_FROM = 8                     # more NPCs than this on the map: a filter box over the list
ROLES_RETRY_S = 5.0                 # the KB's tables not ready yet: the NPCs' kinds asked for again this often
KEYS_CHECK_MS = 400                 # after the filter box asked for the keyboard: still not given, it stops asking
SYMBOL_FONT = "Segoe UI Symbol"     # the glyphs below in one font (Segoe UI lacks ↖ and ↘: a mix looked uneven)
DOOR = "\U0001F6AA\ufe0e"           # a building's heading (U+FE0E: the plain glyph, not the colour emoji)
NO_MAP, NO_NPCS = "\U0001F5FA\ufe0e", "\U0001F465\ufe0e"     # the empty list's card: a map, two people
MAGNIFIER = "\U0001F50D\ufe0e"      # in the filter box
DOOR_COLOR = "#F5B642"              # a door's orange, as the guide's picture dots it (mapview's portal)
# the way to the picked NPC, by direction()'s key: an arrow pointing it on the screen (the same in Hebrew: the
# screen's left is the player's left), or, with no way to point, a mark for where it is
ARROWS = {"npc_guide_left": "←", "npc_guide_right": "→", "npc_guide_up": "↑", "npc_guide_down": "↓",
          "npc_guide_left_up": "↖", "npc_guide_right_up": "↗", "npc_guide_left_down": "↙",
          "npc_guide_right_down": "↘"}
MARKS = {"npc_guide_here": "◎", "npc_guide_marked": "⌖", "npc_guide_unplaced": "?"}
# an NPC's role in the KB's npcs table ends in what it is, the words before that its title ("Weapon Seller ·
# Shopkeeper", "Thief Instructor · Quest Giver · Job Instructor"): each such word the table uses, as a kind
KINDS = {"Job Instructor": "job", "Transport": "transport", "Storage": "storage", "Shopkeeper": "shop",
         "Quest Giver": "quest", "Environmental": "object", "Townsfolk": "townsfolk"}
# each kind's chip colour, in the order a row shows them: readable on every glass tint (the accent lights the pick)
KIND_COLORS = {"job": "#FF8A80", "transport": "#4DD0C8", "storage": "#C39BFF", "shop": "#64B5FF",
               "quest": "#FFC857", "object": "#D9B98C", "townsfolk": "#B7BBC5"}

# the panel floats over the game, whatever theme the app's own windows are in: its own dark glass and light text
PANEL = (24, 24, 27)
TEXT = "#F5F5F7"
MUTED = "rgba(235,235,245,0.64)"
FILL1 = "rgba(255,255,255,0.08)"
FILL2 = "rgba(255,255,255,0.14)"
SEL_TEXT = "#07230F"                # on the selected row's green, as the NPC's dot is ringed dark green

# the colours every window over the game follows (the bar's ⚙ panel picks them): the
# glass tints, all dark enough for the light text, and the accents that light a lit or selected button, each
# with the text colour that reads on it. "dark"/"green" are today's look, the ones a stale setting falls back to.
TINTS = {"dark": ("tbs_tint_dark", (24, 24, 27)),
         "midnight": ("tbs_tint_midnight", (17, 24, 43)),
         "forest": ("tbs_tint_forest", (13, 31, 22)),
         "wine": ("tbs_tint_wine", (38, 17, 27)),
         "slate": ("tbs_tint_slate", (36, 39, 44))}
ACCENTS = {"green": ("tbs_accent_green", "#2E9E5B", "#07230F"),
           "blue": ("tbs_accent_blue", "#3D8BD6", "#082138"),
           "purple": ("tbs_accent_purple", "#8265D6", "#180C2C"),
           "orange": ("tbs_accent_orange", "#E08A3C", "#2F1706"),
           "pink": ("tbs_accent_pink", "#D65C8E", "#2F081A"),
           "teal": ("tbs_accent_teal", "#2FA8A0", "#062623")}
OPACITY_STEPS = (0.3, 0.5, 0.7, 0.85, 1.0)   # the transparency's choices on the ⚙ panel's slider


def _picked(settings, key: str):
    """A setting as Settings or a plain dict gives it; None when neither has it (a test's stub settings)."""
    if settings is None:
        return None
    try:
        return settings[key]
    except (KeyError, TypeError):
        return None


def tint_of(settings) -> tuple[int, int, int]:
    """The glass fill the settings pick (a name TINTS doesn't know: today's dark)."""
    name = _picked(settings, "overlay_tint")
    entry = TINTS.get(name) if isinstance(name, str) else None
    return entry[1] if entry is not None else PANEL


def accent_of(settings) -> tuple[str, str]:
    """The colour that lights a lit or selected button, and the text colour on it (a name ACCENTS doesn't
    know: today's green)."""
    name = _picked(settings, "overlay_accent")
    entry = ACCENTS.get(name) if isinstance(name, str) else None
    return (entry[1], entry[2]) if entry is not None else (theme.GOOD_TEXT_LIGHT, SEL_TEXT)


_tint = PANEL       # the tint _paint_glass paints right now: set from the settings by apply_look


def apply_look(settings) -> None:
    """The one place the windows-over-the-game look follows the settings: the glass tint every _paint_glass
    paints (the accent each window takes itself in its own style sheet, from accent_of). The app calls this
    at start and whenever the tint is picked, so every window repaints with none of its own code."""
    global _tint
    _tint = tint_of(settings)


def current_tint() -> tuple[int, int, int]:
    """The tint _paint_glass is painting (the tests ask; a window never needs it)."""
    return _tint


def _rgba(color: str, alpha: float) -> str:
    """A "#RRGGBB" colour at this opacity, for a tinted fill."""
    c = color.lstrip("#")
    return f"rgba({int(c[0:2], 16)},{int(c[2:4], 16)},{int(c[4:6], 16)},{alpha})"


def qss(settings) -> str:
    """The window's own style at the accent the settings pick: the picked row, the guide's arrow and the filter box
    being typed in light in it (the rest of the look is every window's; each kind's chip has its own colour).
    Rebuilt whenever the accent is picked, so a window follows with no code of its own."""
    accent, on_accent = accent_of(settings)
    chips = "\n".join(f'QLabel#Chip[kind="{kind}"] {{ color: {c}; background: {_rgba(c, 0.16)}; }}'
                      for kind, c in KIND_COLORS.items())
    return f"""
#Title {{ color: {TEXT}; font-weight: 600; }}
#Status {{ color: {MUTED}; }}
#Status[empty="true"] {{ color: {TEXT}; font-size: 14px; font-weight: 600; }}
#Hint {{ color: {MUTED}; font-size: 12px; }}
QFrame#Empty[empty="true"] {{ background: {FILL1}; border-radius: 12px; }}
#EmptyIcon {{ color: {TEXT}; background: {FILL2}; border-radius: 18px; font-family: "{SYMBOL_FONT}";
             font-size: 19px; }}
QPushButton {{ background: {FILL1}; color: {TEXT}; border: none; border-radius: 8px;
              padding: 6px 10px; text-align: left; }}
QPushButton:hover {{ background: {FILL2}; }}
QPushButton#NpcRow {{ border: 1px solid transparent; border-radius: 10px; padding: 0; }}
QPushButton#NpcRow[sel="true"] {{ background: {_rgba(accent, 0.26)}; border: 1px solid {accent}; }}
QPushButton#NpcRow[sel="true"]:hover {{ background: {_rgba(accent, 0.34)}; }}
QLabel#NpcPic {{ background: rgba(255,255,255,0.06); border-radius: 8px; }}
QLabel#NpcName {{ color: {TEXT}; font-size: 13px; font-weight: 600; }}
QLabel#NpcSub {{ color: {MUTED}; font-size: 11px; }}
QLabel#Chip {{ border-radius: 6px; padding: 1px 6px; font-size: 11px; font-weight: 700; }}
{chips}
QPushButton#Section {{ background: transparent; border: none; border-radius: 8px; margin-top: 6px; padding: 0; }}
QPushButton#Section:hover {{ background: {FILL1}; }}
QLabel#SecIcon {{ color: {DOOR_COLOR}; font-family: "{SYMBOL_FONT}"; font-size: 18px; }}
QLabel#SecName {{ color: {TEXT}; font-size: 12px; font-weight: 700; }}
QLabel#SecCount {{ color: {MUTED}; background: {FILL2}; border-radius: 8px; padding: 0 6px; font-size: 11px;
                   font-weight: 700; }}
QLabel#SecMark {{ color: {MUTED}; font-family: "{SYMBOL_FONT}"; font-size: 11px; }}
QLineEdit#Filter {{ background: {FILL1}; color: {TEXT}; border: 1px solid transparent; border-radius: 8px;
                    padding: 4px 4px; }}
QLineEdit#Filter:focus {{ background: {FILL2}; border: 1px solid {accent}; }}
#GuideName {{ color: {TEXT}; font-size: 15px; font-weight: 700; }}
QFrame#Way {{ background: {FILL1}; border-radius: 12px; }}
#Arrow {{ background: {accent}; color: {on_accent}; border-radius: 20px; font-family: "{SYMBOL_FONT}";
         font-size: 24px; font-weight: 700; }}
#Arrow[still="true"] {{ background: {FILL2}; color: {TEXT}; }}
#GuideLine {{ color: {TEXT}; font-size: 15px; font-weight: 600; }}
#RowHint {{ color: {MUTED}; font-size: 11px; }}
QPushButton#StopBtn {{ background: {FILL2}; color: {TEXT}; border: 1px solid rgba(255,255,255,0.16);
                       border-radius: 9px; padding: 7px 12px; font-weight: 600; text-align: center; }}
QPushButton#StopBtn:hover {{ background: rgba(255,255,255,0.22); border-color: rgba(255,255,255,0.28); }}
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


def building_name(g, mid: str, inside: str) -> str:
    """A building's name in its town's list: the town's own name off its front ("Kerning City Pharmacy" in Kerning
    City's list is its "Pharmacy"); the whole name when it doesn't start with it, or nothing would be left."""
    name, town = g.name(inside), g.name(mid)
    rest = name[len(town):].strip() if town and name.startswith(town + " ") else ""
    return rest or name


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


@dataclass(frozen=True)
class Role:
    """What the KB's tables say an NPC is: its kinds (KIND_COLORS' order) and its titles ("Weapon Seller")."""
    kinds: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()


def roles_of(kb) -> dict[str, Role] | None:
    """Every NPC's Role by its KB key, from the npcs table's role column, plus "shop" for anyone the shops table
    has selling (the Pharmacy's Dr. Faymus is "Grocer · Townsfolk" there); "townsfolk" only when nothing else is
    said. Read once per KB and never built here (a test's KB folder must stay as it is): None while its tables
    aren't ready — the app builds them at start-up, and the window asks again."""
    cache = memo(kb, "_npc_roles")
    if "roles" in cache:
        return cache["roles"]
    if not tables.ready(kb):
        return None
    sells = {str(r.get("npc_key") or "") for r in tables.rows(kb, "shops", build=False)} - {""}
    out: dict[str, Role] = {}
    for r in tables.rows(kb, "npcs", build=False):
        key = str(r.get("key") or "")
        parts = [p.strip() for p in str(r.get("role") or "").split("·") if p.strip()]
        kinds = {KINDS[p] for p in parts if p in KINDS} | ({"shop"} if key in sells else set())
        if len(kinds) > 1:
            kinds.discard("townsfolk")
        out[key] = Role(tuple(k for k in KIND_COLORS if k in kinds), tuple(p for p in parts if p not in KINDS))
    for key in sells - out.keys():
        out[key] = Role(("shop",))
    cache["roles"] = out
    return out


def _role_bits(t: I18n, role: Role | None, place: str) -> tuple[list[tuple[str, str]], str]:
    """A row's chips, (kind, its word in the UI's language), and its muted line: the NPC's titles, then, for one
    inside a building (place: its name), which — the KB's English names each one block in a Hebrew line."""
    rtl = t.rtl
    chips = [(k, bidi.plain(t(f"npc_overlay_role_{k}"), rtl)) for k in (role.kinds if role else ())]
    bits = [bidi.ltr_block(x, rtl) for x in (role.titles if role else ())]
    if place:
        bits.append(t("npc_overlay_inside", place=bidi.ltr_block(place, rtl)))
    return chips, bidi.plain(" · ".join(bits), rtl) if bits else ""


def _haystack(t: I18n, name: str, role: Role | None, place: str) -> str:
    """What the filter box finds an NPC by, case aside: its name, its building, its titles and kinds — the table's
    own words ("Shopkeeper") and the chips' ("Shop", "חנות")."""
    words = [name, place]
    if role is not None:
        words += [*role.titles, *(w for w, k in KINDS.items() if k in role.kinds),
                  *(t(f"npc_overlay_role_{k}") for k in role.kinds)]
    return " ".join(words).casefold()


def _paint_glass(w: QWidget, opacity: float) -> None:
    """A floating panel's own glass: the soft shadow, then a rounded fill in the tint the player picked (the
    ⚙ panel's colour, apply_look) at the opacity they asked, so the game shows through as much
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
    fill = QColor(*_tint)
    fill.setAlphaF(opacity)
    p.fillPath(path, fill)
    rim = QLinearGradient(0, SHADOW, 0, w.height() - SHADOW)
    rim.setColorAt(0.0, QColor(255, 255, 255, 60))
    rim.setColorAt(0.4, QColor(255, 255, 255, 22))
    rim.setColorAt(1.0, QColor(255, 255, 255, 11))
    p.setPen(QPen(rim, 1))
    p.drawPath(path)
    p.end()


def set_capturable(w: QWidget, settings) -> None:
    """Whether screen captures see this window over the game: by default never (WDA_EXCLUDEFROMCAPTURE), since it
    may sit over the very box the minimap reads take (the hidden-portal dots over the player's yellow dot hid it from
    the read) and the chat's game screenshots would carry it too; with settings["overlays_in_screenshots"] on they
    do, so the player can screenshot what the overlays show. Set on every show, and by the app when the setting
    flips. Older Windows draws it into the shot either way, where the reader's matching leaves the markers out."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        mode = WDA_NONE if settings["overlays_in_screenshots"] else WDA_EXCLUDEFROMCAPTURE
        if not ctypes.windll.user32.SetWindowDisplayAffinity(int(w.winId()), mode):
            log.debug("overlay: display affinity not available")
    except Exception as e:  # noqa: BLE001 - the window still shows; only who captures it changes
        log.debug("overlay: display affinity failed: %r", e)


def _flags() -> Qt.WindowType:
    """Over the game, frameless, and never taking the keyboard from it (clicks still work)."""
    return Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus


def dock_beside(win: QWidget, owner: QWidget, gap: int = GUIDE_GAP) -> None:
    """A small panel of its own docked beside its owner (its left, or its right where the owner's screen has no
    room left of it): top edges lined up, kept on that screen top to bottom."""
    left = owner.x() + 2 * SHADOW - gap - win.width()
    right = owner.x() + owner.width() - 2 * SHADOW + gap
    screen = QGuiApplication.screenAt(owner.geometry().center()) or QGuiApplication.primaryScreen()
    room = screen.availableGeometry()
    x = left if left + SHADOW >= room.left() else right
    y = min(max(owner.y(), room.top()), max(room.top(), room.bottom() - win.height() + 1))
    if win.pos() != QPoint(x, y):
        win.move(x, y)


def _repolish(*widgets: QWidget) -> None:
    """Styled again after a dynamic property its style sheet selects on changed."""
    for w in widgets:
        w.style().unpolish(w)
        w.style().polish(w)


def _wrapping(name: str) -> QLabel:
    """A label that wraps to the width it is given and never asks for more (a long name, a narrow window)."""
    label = QLabel(objectName=name)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return label


def _glyph_icon(glyph: str, color: QColor, px: int = 16) -> QIcon:
    """A font glyph as a small icon (the filter box's magnifier and ✕), drawn at the screen's own pixels."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pm = QPixmap(round(px * ratio), round(px * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    font = QFont(SYMBOL_FONT)
    font.setPixelSize(round(px * 0.75))
    p.setFont(font)
    p.setPen(color)
    p.drawText(QRectF(0, 0, px, px), Qt.AlignCenter, glyph)
    p.end()
    return QIcon(pm)


def _foreground() -> int:
    """The window that has the keyboard now (the game, while the player plays); 0 off Windows or unknown."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001 - only where the keyboard goes back to is lost: the player clicks the game
        return 0


def _to_foreground(hwnd: int) -> None:
    """The keyboard back to that window (Windows lets it: the player's last input was this app's)."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    except Exception as e:  # noqa: BLE001 - the player clicks the game to take it back
        log.debug("overlay: keyboard not given back: %r", e)


def _accept_keys(w: QWidget, on: bool) -> None:
    """The window able to take the keyboard (on) or never (off, as _flags() made it), changed where it is: QWidget's
    setWindowFlag hides the window to apply it; the QWindow's own flags change the native window as it stands
    (Windows asks Qt on every click whether it may activate, and Qt answers from these)."""
    flags = w.windowFlags() & ~Qt.WindowDoesNotAcceptFocus if on else w.windowFlags() | Qt.WindowDoesNotAcceptFocus
    w.overrideWindowFlags(flags)
    if w.windowHandle() is not None:
        w.windowHandle().setFlags(flags)
    w.update()


class _Pictures:
    """Each NPC's own picture in the KB, fitted once per size (the list is built again on every map). None: the
    square stays empty, so the names still line up (kb.picture's stand-in, the orange NPC card, shouted from a
    list of sprites)."""

    def __init__(self, kb):
        self._kb = kb
        self._paths: dict[str, object] = {}
        self._fitted: dict[tuple[str, int, float], QPixmap] = {}

    def label(self, key: str, size: int, name: str = "") -> QLabel:
        pic = QLabel(objectName="NpcPic")
        pic.setFixedSize(size, size)
        pic.setAlignment(Qt.AlignCenter)
        self.fill(pic, key, name)
        return pic

    def fill(self, pic: QLabel, key: str, name: str = "") -> None:
        """The picture into this square, a little in from its rounded edge, and large on hover with the name under
        it; the square cleared when there is none."""
        pic.clear()
        pic.setToolTip("")
        if key not in self._paths:
            self._paths[key] = self._kb.image_path(key) if key else None
        path = self._paths[key]
        if not path:
            return
        side = pic.width() - 6
        dpr = pic.devicePixelRatioF()
        pm = self._fitted.get((key, side, dpr))
        if pm is None:
            raw = QPixmap(str(path))
            pm = fit_picture(raw, side, side, pic, trim=True) if not raw.isNull() else QPixmap()
            self._fitted[(key, side, dpr)] = pm
        if not pm.isNull():
            pic.setPixmap(pm)
            zoom_on_hover(pic, path, name)


class _Chips(QWidget):
    """The kind chips: on one line while there is room, wrapping onto more (controls.FlowLayout) rather than
    widening a narrow window."""

    GAP = 4

    def __init__(self):
        super().__init__()
        self._flow = FlowLayout(self, spacing=self.GAP, line_spacing=3)

    def set(self, chips: list[tuple[str, str]]) -> None:
        """These chips, (kind, word), each in its kind's colour (qss)."""
        while self._flow.count():
            w = self._flow.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        for kind, word in chips:
            chip = QLabel(word, objectName="Chip")
            chip.setProperty("kind", kind)
            self._flow.addWidget(chip)
            chip.ensurePolished()       # its style's small bold font now: the row is measured before it shows
        self.setVisible(bool(chips))
        self.updateGeometry()

    def labels(self) -> list[QLabel]:
        return [self._flow.itemAt(i).widget() for i in range(self._flow.count())]

    def sizeHint(self) -> QSize:
        """All on one line (the flow's own hint is its widest chip: beside a name that was a chip a line)."""
        hints = [label.sizeHint() for label in self.labels()]
        return QSize(sum(h.width() for h in hints) + self.GAP * max(0, len(hints) - 1),
                     max((h.height() for h in hints), default=0))


class _Row(QPushButton):
    """A push button that draws only its panel — the hover, the [sel] accent — and its own labels the rest. Its text
    stays what it says (the NPC's name, "Inside <building>"), for screen readers and the tests."""

    def __init__(self, name: str):
        super().__init__(objectName=name)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)    # as tall as its wrapped lines

    def paintEvent(self, e) -> None:
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = ""
        p.drawControl(QStyle.CE_PushButton, opt)

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()

    def minimumSizeHint(self) -> QSize:
        return self.layout().minimumSize()


class _NpcRow(_Row):
    """One NPC: its picture, its name in bold with a chip for each kind the KB gives it, and a muted line — its title
    and, inside a building, which. The chips sit beside the name while both fit on its line, else under it (a long
    name, a narrow window): never a name broken over two lines to make room, never a chip cut off."""

    MARGINS = (7, 6, 10, 6)
    SPACING = 10
    CHIP_GAP = 6

    def __init__(self, npc: NpcHere, pic: QLabel):
        super().__init__("NpcRow")
        self.npc = npc
        self.pic = pic
        self.haystack = ""              # what the filter box finds it by (_haystack)
        self._name_w = 0                # the name's width on one line
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*self.MARGINS)
        lay.setSpacing(self.SPACING)
        lay.addWidget(pic, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self._top = QBoxLayout(QBoxLayout.LeftToRight)     # (mirrored in Hebrew, as any row)
        self._top.setContentsMargins(0, 0, 0, 0)
        self._top.setSpacing(self.CHIP_GAP)
        self.name = _wrapping("NpcName")
        self.chips = _Chips()
        self._top.addWidget(self.name, 1)
        self._top.addWidget(self.chips, 0, Qt.AlignTop)
        self.sub = _wrapping("NpcSub")
        col.addStretch(1)               # the name and its lines as one block, centred on the picture
        col.addLayout(self._top)
        col.addWidget(self.sub)
        col.addStretch(1)
        lay.addLayout(col, 1)

    def retext(self, name: str, chips: list[tuple[str, str]], sub: str, haystack: str) -> None:
        self.setText(name)
        self.name.setText(name)
        self.name.ensurePolished()
        self._name_w = self.name.fontMetrics().horizontalAdvance(name) + 2
        self.chips.set(chips)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.haystack = haystack
        self.setAccessibleDescription(" · ".join([word for _, word in chips] + ([sub] if sub else [])))
        self._fit_chips()

    def _fit_chips(self) -> None:
        """The chips beside the name when the line holds both, else on a line of their own under it."""
        m = self.MARGINS
        room = self.width() - m[0] - m[2] - self.pic.width() - self.SPACING
        beside = self.chips.isHidden() or self._name_w + self.CHIP_GAP + self.chips.sizeHint().width() <= room
        way = QBoxLayout.LeftToRight if beside else QBoxLayout.TopToBottom
        if self._top.direction() != way:
            self._top.setDirection(way)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if e.size().width() != e.oldSize().width():
            self._fit_chips()


class _Section(_Row):
    """A building's heading over its NPCs: the door, the building's name, how many of its NPCs are listed, and a
    mark — a click on the heading folds them away, and back."""

    def __init__(self):
        super().__init__("Section")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(7, 6 + 5, 9, 5)          # (6: the QSS margin over its hover panel)
        lay.setSpacing(8)
        self.door = QLabel(DOOR, objectName="SecIcon")
        self.name = _wrapping("SecName")
        self.count = QLabel(objectName="SecCount", alignment=Qt.AlignCenter)
        self.mark = QLabel(objectName="SecMark")
        lay.addWidget(self.door, 0, Qt.AlignVCenter)
        lay.addWidget(self.name, 1, Qt.AlignVCenter)
        lay.addWidget(self.count, 0, Qt.AlignVCenter)
        lay.addWidget(self.mark, 0, Qt.AlignVCenter)

    def retext(self, heading: str, name: str) -> None:
        self.setText(heading)
        self.name.setText(name)

    def show_state(self, n: int, folded: bool, t: I18n) -> None:
        """How many of its NPCs are listed (the filter's matches), and whether they are folded away."""
        self.count.setText(str(n))
        self.mark.setText(("◂" if t.rtl else "▸") if folded else "▾")
        self.setToolTip(t("npc_overlay_unfold" if folded else "npc_overlay_fold"))


class _FilterBox(QLineEdit):
    """The filter over a long list. Its window never takes the keyboard, so a click in here asks for it while the
    player types (wants_keys); Esc empties it and gives the keyboard back (done), Enter picks the first NPC it
    leaves (chosen)."""

    wants_keys = Signal()
    done = Signal()
    chosen = Signal()

    def mousePressEvent(self, e) -> None:
        self.wants_keys.emit()
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.clear()
            self.done.emit()
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.chosen.emit()
        else:
            super().keyPressEvent(e)


class GuideWindow(QWidget):
    """The way to the picked NPC, a small window of its own docked beside the list (its left, or its right where
    the screen has no room): the NPC's picture, name and kind with a ✕; the way in big words beside an arrow
    pointing it; the map's picture with the NPC's green dot (a door's orange) and the player's blue one, and what
    the dots are; and Stop guiding. It moves with the list; its height follows the picture."""

    def __init__(self, owner: NpcOverlay):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)      # the picture's zoom while the game has the keyboard
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(GUIDE_W)
        self.setStyleSheet(qss(owner._settings))
        self._owner = owner
        self.about: tuple | None = None     # whose picture, chips and line the header shows (NpcOverlay._redraw_guide)
        self.legend: mapview.MapLegend | None = None
        self._legend_for: tuple | None = None   # (language, the NPC's own dot or a door's) the legend was made for
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.face = QLabel(objectName="NpcPic")
        self.face.setFixedSize(GUIDE_PIC, GUIDE_PIC)
        self.face.setAlignment(Qt.AlignCenter)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(4)
        self.title = _wrapping("GuideName")
        self.chips = _Chips()
        self.where = _wrapping("NpcSub")
        names.addWidget(self.title)
        names.addWidget(self.chips)
        names.addWidget(self.where)
        self.close_btn = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        self.close_btn.clicked.connect(lambda _=False: owner._clear_pick())
        head.addWidget(self.face, 0, Qt.AlignTop)
        head.addLayout(names, 1)
        head.addWidget(self.close_btn, 0, Qt.AlignTop)
        col.addLayout(head)
        way = QFrame(objectName="Way")
        row = QHBoxLayout(way)
        row.setContentsMargins(10, 10, 12, 10)
        row.setSpacing(12)
        self.arrow = QLabel(objectName="Arrow", alignment=Qt.AlignCenter)
        self.arrow.setFixedSize(40, 40)
        self.line = _wrapping("GuideLine")
        row.addWidget(self.arrow, 0, Qt.AlignVCenter)
        row.addWidget(self.line, 1, Qt.AlignVCenter)
        col.addWidget(way)
        self.pic = QLabel()
        self.pic.setAlignment(Qt.AlignHCenter)
        col.addWidget(self.pic, 0, Qt.AlignHCenter)
        self._legend_slot = QVBoxLayout()
        self._legend_slot.setContentsMargins(0, 0, 0, 0)
        col.addLayout(self._legend_slot)
        self.clear = QPushButton(objectName="StopBtn")
        self.clear.setCursor(Qt.PointingHandCursor)
        self.clear.setFocusPolicy(Qt.NoFocus)
        self.clear.clicked.connect(lambda _=False: owner._clear_pick())
        col.addWidget(self.clear)

    def pic_cap(self) -> int:
        return GUIDE_W - 2 * SIDE - 4

    def paintEvent(self, e) -> None:
        _paint_glass(self, self._owner._opacity)

    def show_beside(self) -> None:
        """Shown (or kept) docked to the list, as tall as its contents need at its own width (adjustSize measured
        the wrapped lines at the contents' wider hint, and the way's last line was cut off). Every part polished
        first: one made since (a chip, the legend) has its style's font only once polished."""
        for w in self.findChildren(QWidget):
            w.ensurePolished()
        lay = self.layout()
        lay.activate()
        self.resize(GUIDE_W, lay.totalHeightForWidth(GUIDE_W) if lay.hasHeightForWidth()
                    else lay.totalSizeHint().height())
        self.dock()
        if not self.isVisible():
            self.show()
        set_capturable(self, self._owner._settings)
        self.update()

    def dock(self) -> None:
        """Beside the list's panel, top edges lined up: on its left, or on its right when the list's screen has
        no room left of it; kept on that screen top to bottom."""
        dock_beside(self, self._owner)

    def set_legend(self, t: I18n, npc: bool, you: bool, target: bool, shown: bool) -> None:
        """What the picture's dots are (mapview.MapLegend: "You are here", "The NPC" or a door's "The portal"),
        made again only for another language or dot; the player's entry while the read has a dot for them, the
        NPC's while it has a place."""
        if self._legend_for != (t.lang, npc):
            if self.legend is not None:
                self._legend_slot.removeWidget(self.legend)
                self.legend.hide()
                self.legend.deleteLater()
            self.legend = mapview.MapLegend(t, npc, you)
            self._legend_slot.addWidget(self.legend)
            self._legend_for = (t.lang, npc)
        self.legend.show_you(you)
        self.legend.target.setVisible(target)
        self.legend.setVisible(shown)


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
    """The window itself. Clicks work (rows are picked, headings fold, edges resize) but it never takes the keyboard
    from the game — only while the player types in its filter box — and the minimap reads never see it. Its place
    is kept in npc_overlay_geom; it sets LOCATION.guide — the ring on the game's own minimap is drawn elsewhere
    (ui/portaldots.py)."""

    closed = Signal()               # the ✕ turned the setting off: the toolbar's NPCs-here button unchecks

    def __init__(self, kb: KnowledgeBase, settings: Settings, t: I18n):
        super().__init__(None, _flags())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)      # a picture's zoom while the game has the keyboard
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
        self._rows: list[_NpcRow] = []
        self._heads: list[tuple[_Section, str]] = []   # each building's heading over its NPCs, with its map id
        self._sel: NpcHere | None = None
        self._pics = _Pictures(kb)
        self._roles: dict[str, Role] | None = None     # roles_of: None while the KB's tables aren't ready
        self._roles_at = 0.0                 # when roles_of was last asked
        self._folded: set[str] = set()       # the buildings whose NPCs the player folded away (kept this session)
        self._shown = 0                      # NPCs the filter box leaves
        self._typing = False                 # the filter box has the keyboard (_take_keys)
        self._keys_from = 0                  # ...and the window that had it before, the game (_give_keys)
        self._placed = False                 # geometry applied at least once: there is a place to remember
        self._collapsed = False              # folded to the header: no NPCs to list (_fit)
        self._full_h: int | None = None      # the height it opens back to from the fold
        self._geom = QTimer(self, singleShot=True, interval=GEOM_DEBOUNCE_MS, timeout=self._remember)
        self.setStyleSheet(qss(self._settings))
        self._build()
        LOCATION.changed.connect(self.refresh)
        LOCATION.status.connect(self.refresh)    # the minimap recognized or not: the empty card's hint follows

    # ------------------------------------------------------------ the panel

    def _build(self) -> None:
        col = QVBoxLayout(self)
        col.setContentsMargins(SIDE, TOP, SIDE, BOTTOM)
        col.setSpacing(8)
        head = _Header()
        hrow = QHBoxLayout(head)
        hrow.setContentsMargins(0, 0, 0, 0)
        hrow.setSpacing(8)
        self._title = _wrapping("Title")                 # a narrow window wraps the map's name, never cuts it
        self._close = QToolButton(objectName="Close", text=theme.SYMBOL_ICONS["close"])
        self._close.setCursor(Qt.PointingHandCursor)
        self._close.setFocusPolicy(Qt.NoFocus)
        self._close.clicked.connect(self._close_clicked)
        hrow.addWidget(self._title, 1)
        hrow.addWidget(self._close, 0, Qt.AlignTop)
        col.addWidget(head)
        self._filter = _FilterBox(objectName="Filter")     # over a long list (FILTER_FROM)
        self._filter.setFocusPolicy(Qt.ClickFocus)         # never the focus a shown window hands its first box
        muted = QColor(235, 235, 245, 163)                  # MUTED, for a QPainter
        self._filter.addAction(_glyph_icon(MAGNIFIER, muted), QLineEdit.LeadingPosition)
        self._filter_clear = self._filter.addAction(_glyph_icon(theme.SYMBOL_ICONS["close"], muted),
                                                    QLineEdit.TrailingPosition)
        self._filter_clear.setVisible(False)
        self._filter_clear.triggered.connect(self._filter.clear)
        self._filter.textChanged.connect(self._filtered)
        self._filter.wants_keys.connect(self._take_keys)
        self._filter.done.connect(self._give_keys)
        self._filter.chosen.connect(self._choose_first)
        self._filter.hide()
        col.addWidget(self._filter)
        # the line over the list (pick one; nothing matches), or a card saying why there is nothing to list
        self._empty = QFrame(objectName="Empty")
        card = QHBoxLayout(self._empty)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(12)
        self._empty_icon = QLabel(objectName="EmptyIcon", alignment=Qt.AlignCenter)
        self._empty_icon.setFixedSize(36, 36)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(3)
        self._status = _wrapping("Status")
        self._hint = _wrapping("Hint")
        words.addWidget(self._status)
        words.addWidget(self._hint)
        card.addWidget(self._empty_icon, 0, Qt.AlignTop)
        card.addLayout(words, 1)
        col.addWidget(self._empty)
        self._scroll = QScrollArea(objectName="Body")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        rows = QWidget(objectName="Rows")
        self._rows_lay = QVBoxLayout(rows)
        self._rows_lay.setContentsMargins(0, 0, 4, 0)      # clear of the scrollbar
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
        g = routes.of(self._kb)
        name = g.name(self._mid) if self._mid else ""
        title = t("npc_overlay_title")
        self._title.setText(bidi.plain(f"{title} · {name}" if name and name != self._mid else title, rtl))
        self._close.setAccessibleName(t("npc_overlay_close"))
        self._close.setToolTip(t("npc_overlay_close"))
        self._clear.setText(bidi.plain(t("npc_overlay_clear"), rtl))
        self._guide.close_btn.setAccessibleName(t("npc_overlay_clear"))
        self._guide.close_btn.setToolTip(t("npc_overlay_clear"))
        self._filter.setPlaceholderText(bidi.plain(t("npc_overlay_filter"), rtl))
        self._filter.setAccessibleName(t("npc_overlay_filter"))
        roles = self._roles or {}
        for row, npc in zip(self._rows, self._npcs):
            role = roles.get(npc.key)
            place = building_name(g, self._mid, npc.inside) if npc.inside else ""
            chips, sub = _role_bits(t, role, place)
            hay = _haystack(t, npc.name, role, g.name(npc.inside) if npc.inside else "")
            row.retext(bidi.ltr_name(npc.name, rtl), chips, sub, hay)
        for head, inside in self._heads:
            head.retext(bidi.plain(t("npc_overlay_inside", place=bidi.ltr_block(g.name(inside), rtl)), rtl),
                        bidi.ltr_name(building_name(g, self._mid, inside), rtl))
        self._apply_filter()
        self._retext_status()
        self._redraw_guide()

    def _retext_status(self) -> None:
        """The line over the list — pick one, or nothing matches what the filter box has — or, with nothing to list,
        a card saying why: the map not known yet (and how it gets known), or no NPC on it."""
        t, rtl = self.t, self.t.rtl
        query = self._filter.text().strip()
        icon = hint = ""
        if self._mid is None:
            key, icon = "npc_overlay_unknown", NO_MAP
            hint = ("npc_overlay_unknown_box" if isinstance(_picked(self._settings, "minimap_region"), dict)
                    else "npc_overlay_unknown_nobox")
        elif not self._npcs:
            key, icon, hint = "npc_overlay_none", NO_NPCS, "npc_overlay_none_hint"
        elif query and not self._shown:
            key = "npc_overlay_no_match"
        else:
            key = "" if self._sel is not None else "npc_overlay_pick"
        self._status.setText(bidi.plain(t(key, q=bidi.ltr_block(query, rtl)), rtl) if key else "")
        self._hint.setText(bidi.plain(t(hint), rtl) if hint else "")
        self._hint.setVisible(bool(hint))
        self._empty_icon.setText(icon)
        self._empty_icon.setVisible(bool(icon))
        self._empty.setVisible(bool(key))
        empty = bool(icon)
        if self._empty.property("empty") != empty:
            self._empty.setProperty("empty", empty)
            self._status.setProperty("empty", empty)
            self._empty.layout().setContentsMargins(*((14, 12, 14, 12) if empty else (0, 0, 0, 0)))
            _repolish(self._empty, self._status)
        self._fit()

    def _fit(self) -> None:
        """No NPCs to list (a map with none, or the map not known yet): the window folds to its header and the card
        saying so, and can't be stretched taller; NPCs again, and it opens back to the height it had."""
        empty = not self._npcs
        self._scroll.setVisible(not empty)
        if empty:
            if not self._collapsed:
                self._collapsed = True
                self._full_h = self.height()
                self.setMinimumHeight(0)
            lay = self.layout()
            lay.activate()
            self.setFixedHeight(lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth()
                                else lay.totalSizeHint().height())
        elif self._collapsed:
            self._collapsed = False
            self.setMaximumHeight(QWIDGETSIZE_MAX)
            self.setMinimumHeight(MIN_H)
            self.resize(self.width(), max(MIN_H, self._full_h or DEFAULT_H))

    # ------------------------------------------------------------ the list

    def _rebuild(self, mid: str | None) -> None:
        """The rows for this map (a Qt slot too: LOCATION.changed names the map), each building's under a heading
        of its own, and the filter box over them when they are many. A new map — or none — lets the picked NPC and
        its guide go (the old spot is on another map's picture), and what was typed in the filter box."""
        self._mid = mid
        self._npcs = npcs_on(routes.of(self._kb), mid) if mid else []
        while self._rows or self._heads:
            w = self._rows.pop() if self._rows else self._heads.pop()[0]
            self._rows_lay.removeWidget(w)
            w.hide()                    # gone now: deleteLater alone left the old rows drawn under the new ones
            w.deleteLater()
        self._sel = None
        LOCATION.set_guide(None)
        self._load_roles()
        self._give_keys()
        self._filter.blockSignals(True)
        self._filter.clear()
        self._filter.blockSignals(False)
        self._filter_clear.setVisible(False)
        self._filter.setVisible(len(self._npcs) > FILTER_FROM)
        inside = ""
        for npc in self._npcs:
            if npc.inside != inside:
                inside = npc.inside
                head = _Section()
                head.clicked.connect(lambda _=False, b=inside: self._fold(b))
                self._heads.append((head, inside))
                self._rows_lay.insertWidget(self._rows_lay.count() - 1, head)
            row = _NpcRow(npc, self._pics.label(npc.key, PIC, npc.name))
            row.clicked.connect(lambda _=False, n=npc: self._pick(n))
            self._rows.append(row)
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)
        self._guide.hide()
        self._retext()

    def _load_roles(self) -> None:
        """What the KB's tables say each NPC is (roles_of); None while they aren't ready."""
        self._roles = roles_of(self._kb)
        self._roles_at = time.monotonic()

    def _roles_came(self) -> bool:
        """The NPCs' kinds read now, the KB's tables ready since the list was built (asked again at most every
        ROLES_RETRY_S: a check that isn't ready can hash the whole KB folder)."""
        if self._roles is not None or not self._npcs or time.monotonic() - self._roles_at < ROLES_RETRY_S:
            return False
        self._load_roles()
        return self._roles is not None

    def _apply_filter(self) -> None:
        """The rows the filter box leaves (every word typed in the NPC's name, its building, its titles or kinds,
        case aside), each building's heading over its own with how many; a folded building keeps its rows away,
        its heading and count still there."""
        words = self._filter.text().casefold().split()
        counts: dict[str, int] = {}
        self._shown = 0
        for row, npc in zip(self._rows, self._npcs):
            hit = all(w in row.haystack for w in words)
            counts[npc.inside] = counts.get(npc.inside, 0) + hit
            self._shown += hit
            row.setVisible(hit and npc.inside not in self._folded)
        for head, inside in self._heads:
            head.show_state(counts.get(inside, 0), inside in self._folded, self.t)
            head.setVisible(counts.get(inside, 0) > 0)

    def _filtered(self, text: str = "") -> None:
        self._filter_clear.setVisible(bool(text))
        self._apply_filter()
        self._retext_status()

    def _fold(self, inside: str) -> None:
        """A building's heading clicked: its NPCs folded away, or back."""
        self._folded ^= {inside}
        self._apply_filter()

    def _choose_first(self) -> None:
        """Enter in the filter box: the first NPC it leaves is picked (kept, when it already is), and the keyboard
        goes back to the game."""
        row = next((r for r in self._rows if not r.isHidden()), None)
        if row is not None and (self._sel is None or self._sel.key != row.npc.key):
            self._pick(row.npc)
        self._give_keys()

    def _take_keys(self) -> None:
        """The filter box clicked: the one time this window takes the keyboard, while the player types in it. It
        goes back to the window that had it (the game) on Esc, Enter or a pick; a click on another window takes it
        there."""
        if self._typing:
            return
        self._typing = True
        self._keys_from = _foreground()
        _accept_keys(self, True)
        self.activateWindow()
        self._filter.setFocus(Qt.MouseFocusReason)
        QTimer.singleShot(KEYS_CHECK_MS, self, self._keys_given)

    def _keys_given(self) -> None:
        """Windows may refuse the activation (another program holds the foreground): then the window goes back to
        never taking the keyboard — left able to, a click on a row would take it from the game."""
        if self._typing and not self.isActiveWindow():
            self._give_keys(back=False)

    def _give_keys(self, back: bool = True) -> None:
        """The window never takes the keyboard again until the box is clicked; back: to the window that had it."""
        if not self._typing:
            return
        self._typing = False
        self._filter.clearFocus()
        _accept_keys(self, False)
        if back:
            _to_foreground(self._keys_from)
        self._keys_from = 0

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.ActivationChange and self._typing and not self.isActiveWindow():
            self._give_keys(back=False)     # the player clicked another window: the keyboard is there already
        super().changeEvent(e)

    def _pick(self, npc: NpcHere) -> None:
        """A row clicked: guide the player to this NPC (one inside a building: to its door); the row clicked again
        lets it go. The keyboard goes back to the game if the filter box had it: the player walks there next."""
        self._give_keys()
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
                _repolish(row)

    # ------------------------------------------------------------ the guide window

    def _redraw_guide(self) -> None:
        """The picked NPC's way, in its own window beside the list: its picture, name and kind; the sentence beside
        an arrow pointing the way; the map picture with its green dot and the player's blue one — redrawn while the
        player moves (a read of the same map). One inside a building: which building, and the way to its door
        (orange, as the map window marks a portal)."""
        if self._sel is None or not self.isVisible():
            self._guide.hide()
            return
        g = routes.of(self._kb)
        here = LOCATION.here
        you = here.spot if here is not None and here.map == self._mid else None
        t, rtl, sel, gd = self.t, self.t.rtl, self._sel, self._guide
        place = g.name(sel.inside) if sel.inside else ""
        gd.title.setText(bidi.ltr_name(sel.name, rtl))
        about = (sel.key, sel.inside, t.lang, self._roles is not None)
        if gd.about != about:           # the header once per pick (and language), not on every step the player takes
            gd.about = about
            self._pics.fill(gd.face, sel.key, sel.name)
            chips, sub = _role_bits(t, (self._roles or {}).get(sel.key), "")    # (the line under says where)
            gd.chips.set(chips)
            gd.where.setText(sub)
            gd.where.setVisible(bool(sub))
        key = direction(g, self._mid, you, sel.spot)
        # the names as they are: in Hebrew I18n keeps a short one whole and bidi.plain sets it in the line
        way = t(key, name=t("npc_guide_door", place=place) if sel.inside else sel.name)
        if sel.inside:
            way = t("npc_guide_inside", name=sel.name, place=place) + " " + way
        self._line.setText(bidi.plain(way, rtl))
        gd.arrow.setText(ARROWS.get(key) or MARKS.get(key, ""))
        still = key not in ARROWS
        if gd.arrow.property("still") != still:
            gd.arrow.setProperty("still", still)
            _repolish(gd.arrow)
        pm = mapview.route_picture(g.minimap(self._mid), sel.spot, not sel.inside, you, cap_w=gd.pic_cap())
        if pm.isNull():
            self._pic.clear()
        else:
            self._pic.setPixmap(pm)
        self._pic.setVisible(not pm.isNull())
        gd.set_legend(t, npc=not sel.inside, you=you is not None, target=sel.spot is not None, shown=not pm.isNull())
        gd.show_beside()

    # ------------------------------------------------------------ place, move, resize

    def _place(self) -> None:
        """Where the window opens: where the player left it, when that is still on a screen (a monitor since
        unplugged: beside the minimap box again)."""
        screens = [s.geometry() for s in QGuiApplication.screens()]
        saved = self._settings["npc_overlay_geom"]
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
        self._geom.start()
        if self._guide.isVisible():
            self._guide.dock()      # the guide moves with the list
        super().moveEvent(e)

    def resizeEvent(self, e) -> None:
        self._geom.start()
        if self._guide.isVisible():
            self._guide.dock()      # a left-edge drag moves the list's left side
        if self._collapsed and e.size().width() != e.oldSize().width():
            self._fit()             # folded: as tall as the card's lines wrap at the new width
        super().resizeEvent(e)

    def hideEvent(self, e) -> None:
        self._give_keys(back=False)  # (Windows gives the keyboard to the next window by itself)
        self._guide.hide()
        if self._placed:             # turned off or the app closing: keep the place now, not 400 ms later
            self._geom.stop()
            self._remember()
        super().hideEvent(e)

    def _remember(self) -> None:
        if self._placed:            # folded, the height kept is the one it opens back to
            h = self._full_h if self._collapsed and self._full_h else self.height()
            self._settings["npc_overlay_geom"] = {"x": self.x(), "y": self.y(), "w": self.width(), "h": h}

    # ------------------------------------------------------------ the rest of the window's life

    def refresh(self, _what: object = None) -> None:
        """Show or hide from the setting, and follow the player's map (a Qt slot too: LOCATION.changed, and
        LOCATION.status for the empty card's hint). The same map keeps the list and the picked NPC — only the guide
        panel follows the player's dot, and the NPCs' kinds fill in once the KB's tables are ready; a new one
        rebuilds the list and lets the guide go, except into the building the picked NPC is in: there the NPC is
        picked again, now guided to where it stands inside."""
        self._restyle()
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
        elif self._roles_came():
            self._retext()          # the KB's tables built since the list was: its chips and titles now
        else:
            self._retext_status()
            self._redraw_guide()
        self.update()
        self._guide.update()

    def _restyle(self) -> None:
        """The accent the settings pick, in this window's and the guide's own style sheets (the glass itself is
        painted in the tint by _paint_glass); skipped when it is the same, a restyle re-polishing every row."""
        sheet = qss(self._settings)
        if sheet != self.styleSheet():
            self.setStyleSheet(sheet)
        if sheet != self._guide.styleSheet():
            self._guide.setStyleSheet(sheet)

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._pics = _Pictures(kb)
        self._guide.about = None
        self._rebuild(self._mid)    # the ids may all have moved: the list is read again, the pick let go
        self.refresh()

    def apply_language(self, t: I18n) -> None:
        self.t = t
        self.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._guide.setLayoutDirection(Qt.RightToLeft if t.rtl else Qt.LeftToRight)
        self._retext()

    def _close_clicked(self, _=False) -> None:
        """The ✕: the setting off (the bar's NPCs Here button follows), the guide gone, the window hidden."""
        self._settings["npc_overlay"] = False
        self._clear_pick()
        self.hide()
        self.closed.emit()

    def closeEvent(self, e) -> None:
        self._guide.close()
        super().closeEvent(e)
