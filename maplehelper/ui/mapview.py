"""The "where is it on the map" window: a map's way in, drawn as NiaMeowDB draws it ("Leads back here"): the map next
door, with an orange dot on the portal that leads to this one. Opened from a map's ◎ in the chat (a map card, or a
tile under "Maps")."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .. import bidi, routes
from ..i18n import I18n
from . import theme
from .controls import rtl_buttons
from .glass import SHADOW, GlassDialog
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


def has_location(kb, key: str) -> bool:
    """A map with a way in this window can draw: the ◎ is shown only then."""
    g = routes.of(kb)
    mid = g.of_key(key) if key.startswith("map/") else None
    return bool(mid and g.entrances(mid))


def marked_picture(path, spot: tuple[float, float]) -> QPixmap:
    """The map's picture, sized for the window, with the orange dot where the portal is (spot: 0-1 of its size)."""
    pm = QPixmap(str(path))
    if pm.isNull():
        return pm
    grow = max(1, min(3, PICTURE_W // max(1, pm.width()), PICTURE_H // max(1, pm.height())))
    if grow > 1:
        pm = pm.scaled(pm.width() * grow, pm.height() * grow, Qt.KeepAspectRatio, Qt.FastTransformation)
    if pm.width() > PICTURE_W or pm.height() > PICTURE_H:
        pm = pm.scaled(PICTURE_W, PICTURE_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    at = QPointF(spot[0] * pm.width(), spot[1] * pm.height())
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    glow = QColor(theme.ORANGE)
    glow.setAlpha(110)
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(at, DOT + 5, DOT + 5)              # the glow, so the dot stands out on a busy map
    p.setPen(QPen(QColor(58, 42, 5), 2))             # NiaMeowDB's dark ring
    p.setBrush(QColor(245, 182, 66))                 # and its orange
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
        outer = QVBoxLayout(self.content)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(objectName="Feed")
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(*gutter(t.rtl))
        self.lay.setSpacing(10)
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
        if self.settings is not None:
            p = self.pos()
            self.settings[POS_SETTING] = {"x": p.x(), "y": p.y()}

    def show_map(self, key: str) -> None:
        """This map's ways in (one window: a second ◎ shows its map here instead of opening another)."""
        from .tools import clear
        t, rtl, g = self.t, self.t.rtl, routes.of(self.kb)
        self.key = key
        clear(self.lay)
        mid = g.of_key(key)
        name = g.name(mid) if mid else (self.kb.get(key) or {}).get("name", key)
        self.title_label.setText(bidi.plain(t("map_where_title", name=bidi.ltr_block(name, rtl)), rtl))
        self.setWindowTitle(name)
        for leg in (g.entrances(mid) if mid else [])[:MAX_ENTRANCES]:
            self.lay.addWidget(self._entrance(g, leg, name))
        self.lay.addStretch(1)

    def _entrance(self, g, leg: routes.Leg, name: str) -> QFrame:
        t, rtl = self.t, self.t.rtl
        card = QFrame(objectName="Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 12, 10)
        col.setSpacing(6)
        title = QLabel(bidi.ltr_name(g.name(leg.frm), rtl), objectName="CardName")
        title.setWordWrap(True)
        col.addWidget(title)
        m = g.maps[leg.frm]
        where = "  ·  ".join(x for x in (m.street, m.continent) if x)
        if where:
            col.addWidget(QLabel(bidi.ltr_block(where, rtl), objectName="CardSub"))
        says = QLabel(bidi.plain(t("map_where_says", name=bidi.ltr_block(name, rtl)), rtl), objectName="RowLabel")
        says.setWordWrap(True)
        col.addWidget(says)
        pic = QLabel()
        pic.setPixmap(marked_picture(g.minimap(leg.frm), leg.spot))
        pic.setAccessibleName(t("map_where_says", name=name))
        col.addWidget(pic, 0, Qt.AlignHCenter)
        return card
