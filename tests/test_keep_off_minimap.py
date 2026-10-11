"""The windows over the game never sit on the drawn minimap box (the owner, 2026-10-11): captures see them now, and
the minimap reads would read them. A drag onto the box stops at its edge; the NPC guide docks away from it."""
import os

import pytest
from PySide6.QtCore import QPoint, QSize

from maplehelper.i18n import I18n

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_a_bar_dragged_onto_the_minimap_box_stops_at_its_edge(qapp, isolated_store):
    from maplehelper.ui import gametoolbar, npcoverlay
    s = isolated_store.Settings()
    s["minimap_region"] = {"x": 300, "y": 200, "w": 200, "h": 150}      # 1:1 on the offscreen screen
    w = gametoolbar.GameToolbar(s, I18n("en"))
    try:
        w.refresh()
        w.move(320, 220)                                            # its panel right on the box
        box = npcoverlay.minimap_box(s)
        assert not npcoverlay._panel(w).intersects(box)
        w.move(40, 40)                                              # well away: only kept on the screen
        assert w.pos() == npcoverlay._on_screen(w, QPoint(40, 40))
    finally:
        w.close()


def test_without_a_box_nothing_moves(qapp, isolated_store):
    from maplehelper.ui import gametoolbar, npcoverlay
    s = isolated_store.Settings()
    w = gametoolbar.GameToolbar(s, I18n("en"))
    try:
        w.refresh()
        w.move(320, 220)
        assert w.pos() == npcoverlay._on_screen(w, QPoint(320, 220))     # only kept on the screen (a narrow one: macOS)
    finally:
        w.close()


def test_the_guide_docks_right_when_its_left_would_cover_the_box(qapp, isolated_store):
    from PySide6.QtWidgets import QWidget
    from maplehelper.ui import npcoverlay
    s = isolated_store.Settings()
    s["minimap_region"] = {"x": 100, "y": 100, "w": 200, "h": 200}
    owner, guide = QWidget(), QWidget()
    owner.setGeometry(310, 100, 300, 400)                           # the list just right of the box
    guide.resize(QSize(270, 200))
    npcoverlay.dock_beside(guide, owner, settings=s)
    assert guide.x() > owner.x()                                    # right of the list, off the box
    s["minimap_region"] = None
    npcoverlay.dock_beside(guide, owner, settings=s)
    assert guide.x() < owner.x()                                    # no box: its left, as before


def test_a_drag_into_the_box_stops_at_its_edge_without_going_in(qapp, isolated_store):
    """The drag asks for the allowed spot first: the window never enters the box to be pushed back (it flickered)."""
    from maplehelper.ui import gametoolbar, npcoverlay
    s = isolated_store.Settings()
    s["minimap_region"] = {"x": 300, "y": 200, "w": 200, "h": 150}
    w = gametoolbar.GameToolbar(s, I18n("en"))
    try:
        w.refresh()
        w.move(40, 40)
        seen = []
        w.moveEvent = lambda e, f=w.moveEvent: (seen.append(w.pos()), f(e))
        for x in range(40, 400, 20):                                # dragged right across the box, step by step
            npcoverlay.drag_to(w, QPoint(x, 230))
            assert not npcoverlay._panel(w).intersects(npcoverlay.minimap_box(s))
        assert all(not npcoverlay._panel(w, p).intersects(npcoverlay.minimap_box(s)) for p in seen)
    finally:
        w.close()


def test_a_drag_past_the_screen_edge_keeps_the_window_on_it(qapp, isolated_store):
    from PySide6.QtGui import QGuiApplication
    from maplehelper.ui import gametoolbar, npcoverlay
    s = isolated_store.Settings()
    w = gametoolbar.GameToolbar(s, I18n("en"))
    try:
        w.refresh()
        room = QGuiApplication.primaryScreen().availableGeometry()
        for at in (QPoint(-5000, 10), QPoint(10, -5000), QPoint(room.right() + 5000, room.bottom() + 5000)):
            npcoverlay.drag_to(w, at)
            assert room.contains(npcoverlay._panel(w)), at
    finally:
        w.close()


def test_a_bar_saved_off_the_screen_comes_back_on_it(qapp, isolated_store):
    """A place saved half off the screen (the bar got stuck out of reach): it opens whole on the screen."""
    from PySide6.QtGui import QGuiApplication
    from maplehelper.ui import gametoolbar, npcoverlay
    s = isolated_store.Settings()
    room = QGuiApplication.primaryScreen().availableGeometry()
    s["game_toolbar_pos"] = {"x": room.right() - 30, "y": room.top() - 20}
    w = gametoolbar.GameToolbar(s, I18n("en"))
    try:
        w.refresh()
        assert room.contains(npcoverlay._panel(w))
    finally:
        w.close()
