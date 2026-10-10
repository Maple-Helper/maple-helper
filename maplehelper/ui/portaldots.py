"""Blue dots on the game's own minimap where the map's invisible teleports are (press-up and touch portals the game
never draws), and a green ring around the NPC the player asked to be guided to (ui/npcoverlay.py): a click-through
window laid over the minimap box the player drew. The map comes from each minimap read
(ui/location.LOCATION.here), where its picture lies in the box from the fast follow between reads (LOCATION.follow:
the marks move with the minimap's scroll, and go the moment the picture stops matching, a teleport or the loading
screen). Hidden whenever there is nothing to place (no box, no read, a picture that doesn't match); the portal dots
also need their setting, the ring only a guide on the player's own map."""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import routes
from ..kb import KnowledgeBase
from ..store import Settings
from . import theme
from .location import LOCATION
from .regionpick import from_capture

log = logging.getLogger("maplehelper")

DOT_PX = 7                          # the dot's width in screen pixels: a little smaller than the player's own dot,
                                    # so standing on one still shows its yellow around it
FILL = QColor(40, 130, 255)
OUTLINE = QColor(10, 20, 45)        # the dark rim the game gives its own dots
RING_PX = 7                         # the guided NPC's ring radius: wider than the dots, so it reads as the target
RING_PEN = 2.0                      # the green stroke's width, the dark rim drawn a little wider under it
RING_FILL = QColor(theme.GOOD_TEXT_LIGHT)   # the NPC green, as the map windows dot an NPC (mapview._mark_colors)
RING_RIM = QColor(8, 48, 24)        # its dark rim (mapview's), so the ring holds on any minimap
WDA_EXCLUDEFROMCAPTURE = 0x11       # Windows 10 2004+: screen captures (the minimap reads) never see this window


class PortalDots(QWidget):
    """The dots' and the guided NPC's ring's window over the minimap box. Never takes a click, the focus, or a
    place in the minimap reads."""

    def __init__(self, kb: KnowledgeBase, settings: Settings):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._kb = kb
        self._settings = settings
        self._dots: list[QPointF] = []
        self._ring: QPointF | None = None     # the guided NPC, once the follow places it
        self._ratio = 1.0
        self._excluded = False
        LOCATION.changed.connect(self.refresh)
        LOCATION.followed.connect(self.refresh)
        LOCATION.guided.connect(self.refresh)

    def set_kb(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self.refresh()

    def refresh(self, _what: object = None) -> None:
        """Place the dots and the guided NPC's ring from the live follow of the player's map (a Qt slot too:
        LOCATION's signals carry what it re-reads). Never from a read's own view: it can be the last map's while
        the reads wait to confirm a new one, and entering a shop flashed the street's dots back for a second
        (live)."""
        region = self._settings["minimap_region"]
        here = LOCATION.here
        if not isinstance(region, dict) or here is None:
            self._clear()
            return
        follow = LOCATION.follow
        # none, or one of another map (the box shows that one now, not the player's known map): nothing to place
        view = follow[1] if follow is not None and follow[0] == here.map else None
        if view is None:
            self._clear()
            return
        # the portal dots need their setting; the ring, only a guide for the map the player is on
        spots = routes.of(self._kb).hidden_spots(here.map) if self._settings["minimap_hidden_portals"] else []
        at = [p for p in (view.at(s) for s in spots) if p is not None]
        guide = LOCATION.guide
        at_ring = view.at(guide[1]) if guide is not None and guide[0] == here.map else None
        if not at and at_ring is None:
            self._clear()
            return
        screens = [(s.geometry(), s.devicePixelRatio()) for s in QGuiApplication.screens()]
        try:
            rect, ratio = from_capture(region, screens, scale=sys.platform != "darwin")
        except (KeyError, TypeError, ValueError):
            self._clear()
            return
        self._ratio = ratio
        self._dots = [QPointF(x / ratio, y / ratio) for x, y in at]
        self._ring = QPointF(at_ring[0] / ratio, at_ring[1] / ratio) if at_ring is not None else None
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self._exclude_from_capture()
        self.update()

    def _clear(self) -> None:
        self._dots = []
        self._ring = None
        if self.isVisible():
            self.hide()

    def _exclude_from_capture(self) -> None:
        """Keep the dots out of the screenshots the minimap reads take: drawn over the player's yellow dot (standing
        on a portal), they would hide it from the read. Older Windows draws them into the shot, where the reader
        leaves blue marks out of its picture matching anyway."""
        if self._excluded or sys.platform != "win32":
            return
        self._excluded = True
        try:
            import ctypes
            if not ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE):
                log.debug("hidden portal dots: capture exclusion not available")
        except Exception as e:  # noqa: BLE001 - the dots still show; only the reads may see them
            log.debug("hidden portal dots: capture exclusion failed: %r", e)

    def paintEvent(self, e) -> None:
        if not self._dots and self._ring is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(OUTLINE, 1.0 / self._ratio))
        p.setBrush(FILL)
        r = DOT_PX / 2 / self._ratio
        for c in self._dots:
            p.drawEllipse(c, r, r)
        if self._ring is not None:
            rr = RING_PX / self._ratio
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(RING_RIM, (RING_PEN + 2.0) / self._ratio))
            p.drawEllipse(self._ring, rr, rr)
            p.setPen(QPen(RING_FILL, RING_PEN / self._ratio))
            p.drawEllipse(self._ring, rr, rr)
        p.end()
