"""The map window's place: beside the chat the first time, then where the player left it."""
import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication

from maplehelper.ui import mapview

app = QApplication.instance() or QApplication([])
SCREEN = QRect(0, 0, 1920, 1040)
SIZE = QSize(584, 484)


def test_opens_beside_the_chat_on_the_side_with_room():
    chat = QRect(1296, 60, 624, 664)                    # the chat's default spot: the screen's top-right
    at = mapview.beside(chat, SIZE, [SCREEN])
    assert at == QPoint(chat.left() - mapview.GAP - SIZE.width(), 60)       # its left, top edges lined up
    assert at.x() + SIZE.width() <= chat.left() + 2 * mapview.SHADOW       # side by side: only the shadows overlap
    chat = QRect(0, 200, 624, 664)                      # moved to the left edge: the window goes on its right
    assert mapview.beside(chat, SIZE, [SCREEN]) == QPoint(chat.right() + 1 + mapview.GAP, 200)


def test_stays_on_the_chats_screen():
    second = QRect(1920, 0, 1920, 1040)
    chat = QRect(2200, 700, 1500, 600)                  # on the second monitor, low, with little room either side
    at = mapview.beside(chat, SIZE, [SCREEN, second])
    assert second.contains(QRect(at, SIZE))


@pytest.mark.parametrize("saved,expected", [
    ({"x": 300, "y": 120}, QPoint(300, 120)),          # where the player left it
    ({"x": 5000, "y": 120}, None),                      # on a monitor since unplugged: beside the chat instead
    (None, None), ({"x": "300"}, None),                 # never moved, or a hand-edited setting
])
def test_saved_spot(saved, expected):
    assert mapview.saved_spot(saved, SIZE, [SCREEN]) == expected


def test_the_window_reopens_where_the_player_left_it(kb_copy, isolated_store):
    from maplehelper.kb import KnowledgeBase
    from maplehelper.store import Settings
    settings, kb = Settings(), KnowledgeBase(kb_copy)
    chat = QRect(1296, 60, 624, 664)
    dlg = mapview.MapLocationDialog(kb, "en", "", settings, chat)
    first = dlg.pos()
    assert first.y() == 60 and first.x() < chat.left()
    dlg.move(200, 150)                                  # the player drags it away, then closes it
    dlg.reject()
    assert Settings()[mapview.POS_SETTING] == {"x": 200, "y": 150}
    again = mapview.MapLocationDialog(kb, "en", "", Settings(), chat)
    assert again.pos() == QPoint(200, 150)
