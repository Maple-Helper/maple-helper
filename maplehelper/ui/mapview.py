"""The "where is it on the map" window, drawn as NiaMeowDB draws it. A map: the map next door, with an orange dot on the
portal that leads in ("Leads back here"). An NPC: its map, with a green dot where it stands (one inside a shop with no
picture: the shop's way in). A quest: its giver's spot, then who it's turned in to. Opened from the ◎ on a map, NPC or
quest card in the chat, or on a tile under "Maps", "NPCs" or "Quests"."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import bidi, routes
from ..i18n import I18n
from . import theme
from .controls import rtl_buttons
from .glass import SHADOW, GlassDialog
from .location import LOCATION
from .patchnotes import gutter

PICTURE_W, PICTURE_H = 480, 260      # the most a map takes (a small minimap grows, pixel for pixel, up to 3x)
MAX_ENTRANCES = 3                    # a map with many ways in: the first ones (the town first, see Graph.entrances)
DOT = 7                              # the dot's radius in pixels, as NiaMeowDB's 11 px one on its smaller render
# the frames' gap: the two windows' see-through shadow margins overlap, so the panels show 8 px apart
GAP = 8 - 2 * SHADOW
POS_SETTING = "map_window_pos"       # where the player last left the window: {"x", "y"}


def beside(chat: QRect, size, screens: list[QRect]) -> QPoint:
    """Where the window opens next to the chat: on the side of it with more room (the chat starts at the screen's
    top-right, so usually its left), top edges lined up, kept on the chat's screen."""
    screen = max(screens, key=lambda s: s.intersected(chat).width() * s.intersected(chat).height(), default=chat)
    room_left, room_right = chat.left() - screen.left(), screen.right() - chat.right()
    x = chat.left() - GAP - size.width() if room_left >= room_right else chat.right() + 1 + GAP
    # never off the screen: no room either side (a chat as wide as the monitor) leaves it overlapping, on screen
    x = min(max(x, screen.left()), screen.right() - size.width() + 1)
    y = min(max(chat.top(), screen.top()), screen.bottom() - size.height() + 1)
    return QPoint(x, y)


def saved_spot(saved, size, screens: list[QRect]) -> QPoint | None:
    """Where the player left the window, when it is still on a screen (a monitor since unplugged: None)."""
    if not isinstance(saved, dict) or not isinstance(saved.get("x"), int) or not isinstance(saved.get("y"), int):
        return None
    from .overlay import visible_rect
    spot = visible_rect(QRect(QPoint(saved["x"], saved["y"]), size), screens)
    return spot.topLeft() if spot is not None else None


@dataclass(frozen=True)
class Spot:
    """One picture in the window: a map, the dot on it, and what the dot is."""
    map: str                             # the map the picture is of (routes.json id)
    at: tuple[float, float]              # the dot, as 0-1 of the picture's size
    says: str                            # the string key of the line under the map's name
    name: str                            # what the line names: the place it leads to, or the NPC
    inside: str = ""                     # an NPC inside a building: the building ("npc_where_inside")
    npc: bool = False                    # an NPC's own spot (green, as the route page rings an NPC), else a portal
    role: str = ""                       # a quest's NPC: "quest_where_start" / "quest_where_end" (a heading above it)


def _map_spots(g, mid: str, name: str) -> list[Spot]:
    return [Spot(leg.frm, leg.spot, "map_where_says", name) for leg in g.entrances(mid)[:MAX_ENTRANCES]]


def in_building(g, mid: str) -> bool:
    """A building off a town (a shop, Henesys Town Hall, the Kerning City Civic Center): a town map whose one way out
    leads back to the town it is entered from. Its NPCs need its door shown on the town's map too."""
    ways_in = g.entrances(mid)
    return bool(g.maps[mid].town and len(g.edges[mid]) == 1 and ways_in and g.maps[ways_in[0].frm].town)


def _npc_spots(kb, g, key: str, role: str = "") -> list[Spot]:
    """Where an NPC is: in a building, first the building's door on the map outside (orange), then, when the building
    has a picture, the NPC inside it (green); anywhere else, its map with the green dot."""
    name = (kb.get(key) or {}).get("name", key)
    mid = g.of_key(key)
    if not mid:
        return []
    hit = g.npc_spot(key)
    doors = []
    if hit is None or in_building(g, mid):
        inside = g.name(mid)
        doors = [Spot(s.map, s.at, "npc_where_inside", name, inside=inside, role=role)
                 for s in _map_spots(g, mid, inside)]
    own = [Spot(hit[0], hit[1], "npc_where_says", name, npc=True, role=role)] if hit else []
    return doors + own


def locations(kb, key: str) -> list[Spot]:
    """Every picture the window shows for a map, an NPC or a quest (its giver, then who it's turned in to)."""
    g = routes.of(kb)
    cat = key.partition("/")[0]
    if cat == "map":
        mid = g.of_key(key)
        return _map_spots(g, mid, g.name(mid)) if mid else []
    if cat == "npc":
        return _npc_spots(kb, g, key)
    if cat == "quest":
        from .. import quests
        q = quests.quest(kb, key)
        if q is None:
            return []
        out, seen = [], set()
        for who, role in ((q.npc, "quest_where_start"), (q.turn_in, "quest_where_end")):
            npc = kb.npc_key(who) if who else None
            if npc and npc not in seen:
                seen.add(npc)
                out += _npc_spots(kb, g, npc, role)
        return out
    return []


def has_location(kb, key: str) -> bool:
    """Something this window can draw: the ◎ is shown only then."""
    return bool(locations(kb, key))


def _whole(name: str) -> str:
    """A short name with no-break spaces, so a line wraps before it rather than inside it."""
    return name.replace(" ", " ") if len(name) <= bidi.KEEP_TOGETHER else name


def route_says(t, g, leg) -> str:
    """One step's line, in the UI language: the portal (left/right when at the map's edge) or the NPC to talk to,
    with the fare when a guide names it. The route page says the same (tools.py delegates here)."""
    rtl = t.rtl
    if leg is None:
        return t("route_arrive")
    # a map name of a few words stays on one line: wrapped inside, "The Road to the" ended one line and
    # "Dungeon" began the next
    to = bidi.ltr_block(_whole(g.name(leg.to)), rtl)
    if leg.kind == "portal":
        where = routes.side(leg.spot)
        return t(f"route_portal_{where}" if where in ("left", "right") else "route_portal", to=to)
    said = t(f"route_by_{leg.kind}", npc=bidi.ltr_block(leg.via, rtl), to=to)
    return said + (" " + t("route_fare", n=f"{leg.fare:,}") if leg.fare else "")


def says_html(t, text: str) -> str:
    """A step line as the card shows it: **bold** rendered, aligned to the UI side, as the route page draws it
    (tools._label: bidi.paragraph_html + Qt.RichText)."""
    return bidi.paragraph_html(text, "rtl" if t.rtl else "ltr")


@dataclass(frozen=True)
class WayFromHere:
    """The window's from-here view: the way from the player's map to the destination, then tail cards."""
    dest: str                          # the destination map (in the graph)
    route: routes.Route                # from the player's map (no legs: already there)
    npc: str = ""                      # the NPC key the arrival card greens (a map: "")
    tail: tuple = ()                   # Spot cards after the arrival (a door, a quest's turn-in)


def way_from_here(kb, key: str, here_map: str | None) -> WayFromHere | None:
    """The way from the player's map to this map, NPC or quest giver, or None for today's entrances view (no live
    map, the map unknown to the graph, or no way there). Never empty: has_location stays independent of it."""
    g = routes.of(kb)
    if not here_map or here_map not in g.maps:
        return None
    cat = key.partition("/")[0]
    if cat == "map":
        dest = g.of_key(key)
        if not dest:
            return None
        r = g.route(here_map, dest)
        return WayFromHere(dest, r) if r is not None else None
    if cat == "npc":
        dest = g.of_key(key)
        if not dest:
            return None
        r = g.route(here_map, dest)
        if r is None:
            return None
        # no minimap of its own (a shop inside): the way ends at its door, whose cards stay as the tail
        tail = () if g.npc_spot(key) else tuple(_npc_spots(kb, g, key))
        return WayFromHere(dest, r, npc=key, tail=tail)
    if cat == "quest":
        from .. import quests
        q = quests.quest(kb, key)
        if q is None:
            return None
        giver = kb.npc_key(q.npc) if q.npc else None
        dest = g.of_key(giver) if giver else None
        if not giver or not dest:
            return None
        r = g.route(here_map, dest)
        if r is None:
            return None
        tail = [] if g.npc_spot(giver) else list(_npc_spots(kb, g, giver, "quest_where_start"))
        if q.turn_in and q.turn_in != q.npc:
            turn = kb.npc_key(q.turn_in)
            if turn:
                tail += _npc_spots(kb, g, turn, "quest_where_end")
        return WayFromHere(dest, r, npc=giver, tail=tuple(tail))
    return None


def _scaled(path) -> QPixmap:
    """The map's picture, sized for the window (a small minimap grows, pixel for pixel, up to 3x)."""
    pm = QPixmap(str(path))
    if pm.isNull():
        return pm
    grow = max(1, min(3, PICTURE_W // max(1, pm.width()), PICTURE_H // max(1, pm.height())))
    if grow > 1:
        pm = pm.scaled(pm.width() * grow, pm.height() * grow, Qt.KeepAspectRatio, Qt.FastTransformation)
    if pm.width() > PICTURE_W or pm.height() > PICTURE_H:
        pm = pm.scaled(PICTURE_W, PICTURE_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


def _dot(pm: QPixmap, spot: tuple[float, float], fill: QColor, ring: QColor) -> None:
    """One dot on the picture (spot: 0-1 of its size): the glow, so it stands out on a busy map, then the dot."""
    at = QPointF(spot[0] * pm.width(), spot[1] * pm.height())
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    glow = QColor(fill)
    glow.setAlpha(110)
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(at, DOT + 5, DOT + 5)
    p.setPen(QPen(ring, 2))
    p.setBrush(fill)
    p.drawEllipse(at, DOT, DOT)
    p.end()


def marked_picture(path, spot: tuple[float, float], npc: bool = False) -> QPixmap:
    """The map's picture, sized for the window, with a dot where the portal (orange) or the NPC (green) is (spot: 0-1
    of its size)."""
    pm = _scaled(path)
    if pm.isNull():
        return pm
    fill, ring = (QColor(theme.GOOD_TEXT_LIGHT), QColor(8, 48, 24)) if npc else \
        (QColor(245, 182, 66), QColor(58, 42, 5))        # NiaMeowDB's orange and dark ring
    _dot(pm, spot, fill, ring)
    return pm


YOU_FILL, YOU_RING = QColor(255, 213, 47), QColor(84, 62, 0)      # the player's own dot: yellow, white-ringed


def route_picture(path, spot: tuple[float, float] | None = None, npc: bool = False,
                   you: tuple[float, float] | None = None) -> QPixmap:
    """A step's picture: the portal/NPC dot as marked_picture draws it, plus the player's yellow dot (a white ring,
    so it stands out from the orange and green ones). Either dot missing: just the other."""
    pm = _scaled(path)
    if pm.isNull():
        return pm
    if spot is not None:
        fill, ring = (QColor(theme.GOOD_TEXT_LIGHT), QColor(8, 48, 24)) if npc else \
            (QColor(245, 182, 66), QColor(58, 42, 5))
        _dot(pm, spot, fill, ring)
    if you is not None:
        at = QPointF(you[0] * pm.width(), you[1] * pm.height())
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        glow = QColor(YOU_FILL)
        glow.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(at, DOT + 5, DOT + 5)
        p.setPen(QPen(QColor(255, 255, 255), 3))
        p.setBrush(YOU_FILL)
        p.drawEllipse(at, DOT, DOT)
        p.setPen(QPen(YOU_RING, 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(at, DOT, DOT)
        p.end()
    return pm


class MapLocationDialog(GlassDialog):
    def __init__(self, kb, lang: str, stylesheet: str, settings=None, chat: QRect | None = None):
        """settings: where the window's position is kept; chat: the chat's frame, which it opens beside the
        first time (afterwards where the player left it)."""
        self.t = t = I18n(lang or "he")
        super().__init__(t("card_map_where"), t.rtl)
        self.setStyleSheet(stylesheet)
        self.fit_screen(560, 460)
        self.kb = kb
        self.key: str | None = None
        self._here_map: str | None = None     # the live map the way shown is from (None: the generic ways in)
        self._way: WayFromHere | None = None
        self._first = None                    # (picture, map, leg|None, npc spot|None) of the first card
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(*gutter(t.rtl))
        self.lay.setSpacing(10)
        self.body = body
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 10, 0, 0)
        row.addStretch(1)
        ok = QPushButton(t("close"), objectName="Primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(160)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        row.addStretch(1)
        outer.addLayout(row)
        rtl_buttons(self, t.rtl)
        self.settings = settings
        self._place(chat)
        LOCATION.changed.connect(self._on_location)
        self.finished.connect(lambda *_: self._remember())

    def _place(self, chat: QRect | None) -> None:
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        size = self.frameGeometry().size()
        spot = saved_spot(self.settings[POS_SETTING], size, screens) if self.settings is not None else None
        if spot is None and chat is not None and chat.isValid():
            spot = beside(chat, size, screens)
        if spot is not None:
            self.move(spot)

    def _remember(self) -> None:
        """Closed (its button, the X, Esc): it opens there next time."""
        try:
            LOCATION.changed.disconnect(self._on_location)
        except (RuntimeError, TypeError):
            pass                              # never connected, or already gone with the window
        if self.settings is not None:
            p = self.pos()
            self.settings[POS_SETTING] = {"x": p.x(), "y": p.y()}

    def show_map(self, key: str) -> None:
        """Where a map, an NPC or a quest's NPCs are (one window: a second ◎ shows its thing here instead of opening
        another). With the player's live map known, the way from there instead of the generic ways in."""
        from .tools import clear
        t, rtl, g = self.t, self.t.rtl, routes.of(self.kb)
        self.key = key
        clear(self.lay)
        self._way, self._first = None, None
        mid = g.of_key(key) if key.startswith("map/") else None
        name = g.name(mid) if mid else (self.kb.get(key) or {}).get("name", key)
        here = LOCATION.here
        way = way_from_here(self.kb, key, here.map if here else None)
        if way is None:
            self._here_map = None
            title = "map_where_title" if key.startswith("map/") else "where_title"
            self.title_label.setText(bidi.plain(t(title, name=bidi.ltr_block(name, rtl)), rtl))
            self.setWindowTitle(name)
            role = None
            for spot in locations(self.kb, key):
                if spot.role and spot.role != role:
                    role = spot.role
                    self.lay.addWidget(QLabel(bidi.plain(t(role), rtl), objectName="ToolHeader"))
                self.lay.addWidget(self._card(g, spot))
        else:
            self._here_map = here.map
            self._way = way
            self.title_label.setText(bidi.plain(t("route_from_here_title", here=bidi.ltr_block(g.name(here.map), rtl),
                                                  name=bidi.ltr_block(name, rtl)), rtl))
            self.setWindowTitle(name)
            you = here.spot if here else None
            if not way.route.legs:
                self.lay.addWidget(self._here_card(g, way, you))
            else:
                for i, leg in enumerate(way.route.legs, 1):
                    self.lay.addWidget(self._route_card(g, str(i), leg.frm, leg, you if i == 1 else None, i == 1))
                role = None
                if key.startswith("quest/"):
                    role = "quest_where_start"
                    self.lay.addWidget(QLabel(bidi.plain(t(role), rtl), objectName="ToolHeader"))
                self.lay.addWidget(self._arrival_card(g, way))
                for spot in way.tail:
                    if spot.role and spot.role != role:
                        role = spot.role
                        self.lay.addWidget(QLabel(bidi.plain(t(role), rtl), objectName="ToolHeader"))
                    self.lay.addWidget(self._card(g, spot))
        self.lay.addStretch(1)
        self._fit_height()

    def _on_location(self, here) -> None:
        """The minimap read changed while open: a new map re-renders the way; a moved dot repaints the first
        card's picture only (rebuilding every second flickered the window)."""
        if self.key is None:
            return
        if (here.map if here else None) != self._here_map:
            self.show_map(self.key)
            return
        if self._first is None or self._way is None:
            return                                     # the generic ways in: no live dot on them
        pic, mid, leg, npc_at = self._first
        try:
            you = here.spot if here else None
            g = routes.of(self.kb)
            if leg is not None:
                pm = route_picture(g.minimap(mid), leg.spot, leg.kind != "portal", you)
            else:
                pm = route_picture(g.minimap(mid), npc_at, npc_at is not None, you)
            pic.setPixmap(pm)
        except RuntimeError:
            pass                                       # closed meanwhile

    def _fit_height(self) -> None:
        """As tall as what it shows (a building's door and the NPC inside: both pictures, no scrolling), never taller
        than the screen it is on (a quest's four pictures may scroll there); the width the player sees stays."""
        lay = self.lay
        m = lay.contentsMargins()
        # the cards' own heights at the width they get (a wrapped line under a map counts). Every widget in the layout
        # is this ◎'s (clear() took the previous ones out), counted while still hidden: Qt shows a child added to an
        # open window only on its next layout pass
        rows = [w for i in range(lay.count()) if (w := lay.itemAt(i).widget()) is not None]
        width = max(1, self.scroll.viewport().width() - m.left() - m.right())
        need = sum(w.heightForWidth(width) if w.hasHeightForWidth() else w.sizeHint().height() for w in rows)
        need += lay.spacing() * max(0, len(rows) - 1) + m.top() + m.bottom() + 2 * self.scroll.frameWidth()
        extra = self.height() - self.scroll.viewport().height()      # title bar, Close button, margins, shadow
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        room = screen.availableGeometry() if screen is not None else None
        height = need + extra
        if room is not None:
            height = min(height, room.height())
        self.resize(self.width(), height)
        if room is not None and self.frameGeometry().bottom() > room.bottom():
            self.move(self.x(), max(room.top(), room.bottom() - self.frameGeometry().height() + 1))

    def _card(self, g, spot: Spot) -> QFrame:
        t, rtl = self.t, self.t.rtl
        card = QFrame(objectName="Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        title = QLabel(bidi.ltr_name(g.name(spot.map), rtl), objectName="CardName")
        title.setWordWrap(True)
        col.addWidget(title)
        m = g.maps[spot.map]
        where = "  ·  ".join(x for x in (m.street, m.continent) if x)
        if where:
            col.addWidget(QLabel(bidi.ltr_block(where, rtl), objectName="CardSub"))
        args = {"name": bidi.ltr_block(spot.name, rtl), "inside": bidi.ltr_block(spot.inside, rtl)}
        says = QLabel(bidi.plain(t(spot.says, **args), rtl), objectName="RowLabel")
        says.setWordWrap(True)
        col.addWidget(says)
        pic = QLabel()
        pic.setPixmap(marked_picture(g.minimap(spot.map), spot.at, spot.npc))
        pic.setAccessibleName(t(spot.says, name=spot.name, inside=spot.inside))
        col.addWidget(pic, 0, Qt.AlignHCenter)
        return card


    def _numbered_card(self, number: str, good: bool, mid: str, says: str, pm, accessible: str) -> tuple[QFrame, QLabel]:
        """A step's frame: its number, the map's name, the line, and the picture (the caller keeps the QLabel to
        repaint the live dot)."""
        rtl, g = self.t.rtl, routes.of(self.kb)
        card = QFrame(objectName="Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        num = QLabel(number, objectName="TagGood" if good else "TagAccent")
        num.setAlignment(Qt.AlignCenter)
        num.setMinimumWidth(26)
        top.addWidget(num, 0, Qt.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(1)
        title = QLabel(bidi.ltr_name(g.name(mid), rtl), objectName="CardName")
        title.setWordWrap(True)
        names.addWidget(title)
        m = g.maps[mid]
        where = "  ·  ".join(x for x in (m.street, m.continent) if x)
        if where:
            names.addWidget(QLabel(bidi.ltr_block(where, rtl), objectName="CardSub"))
        top.addLayout(names, 1)
        col.addLayout(top)
        line = QLabel(objectName="RowLabel")              # rich: the step's **words** come out bold, as on the
        line.setTextFormat(Qt.RichText)                   # route page (a plain label showed the asterisks)
        line.setWordWrap(True)
        line.setText(says_html(self.t, says))
        col.addWidget(line)
        pic = QLabel()
        if not pm.isNull():
            pic.setPixmap(pm)
        pic.setAccessibleName(accessible)
        col.addWidget(pic, 0, Qt.AlignHCenter)
        return card, pic

    def _route_card(self, g, number: str, mid: str, leg, you, first: bool = False) -> QFrame:
        """One step of the way: what to do on this map, its minimap with the portal/NPC ringed, and on the first
        card the player's yellow dot with where-you-are."""
        t, rtl = self.t, self.t.rtl
        card, pic = self._numbered_card(number, False, mid, route_says(t, g, leg),
                                        route_picture(g.minimap(mid), leg.spot, leg.kind != "portal", you),
                                        g.name(mid))
        if you is not None:
            yours = QLabel(bidi.plain(t("route_you_are_here"), rtl), objectName="RowHint")
            yours.setWordWrap(True)
            card.layout().insertWidget(2, yours)
        if first:
            self._first = (pic, mid, leg, None)
        return card

    def _npc_at(self, g, npc: str):
        """The NPC's green dot on its map's picture, for the arrival card (None: a map with no picture of its own,
        whose door cards follow as the tail)."""
        if not npc:
            return None
        hit = g.npc_spot(npc)
        return hit[1] if hit else None

    def _arrival_card(self, g, way: WayFromHere) -> QFrame:
        """The destination: arrived, its picture (the NPC's green dot for an NPC target)."""
        t = self.t
        npc_at = self._npc_at(g, way.npc)
        card, _pic = self._numbered_card("✓", True, way.dest, t("route_arrive"),
                                        route_picture(g.minimap(way.dest), npc_at, npc_at is not None, None),
                                        g.name(way.dest))
        return card

    def _here_card(self, g, way: WayFromHere, you) -> QFrame:
        """Already on the destination map: said, with the player's yellow dot (and the NPC's for an NPC)."""
        t = self.t
        npc_at = self._npc_at(g, way.npc)
        card, pic = self._numbered_card("✓", True, way.dest, t("route_here_already"),
                                        route_picture(g.minimap(way.dest), npc_at, npc_at is not None, you),
                                        g.name(way.dest))
        self._first = (pic, way.dest, None, npc_at)
        return card
