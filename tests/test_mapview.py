"""The map window: where it opens (beside the chat, then where the player left it) and what it shows for a map, an NPC
and a quest."""
from pathlib import Path

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


REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")


@pytest.fixture(scope="module")
def real():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_an_npc_is_where_it_stands_and_one_in_a_shop_is_behind_the_shops_door(real):
    """Mr. Kim stands on Lith Harbor: a green dot there. Mina is inside the Department Store, which has no minimap of
    its own: Lith Harbor with the store's door, the orange dot NiaMeowDB draws (81.2%, 80.2%)."""
    from maplehelper import routes
    g = routes.of(real)
    kim, mina = real.npc_key("Mr. Kim"), real.npc_key("Mina")
    if not (kim and mina and g.of_key(kim) == "010000000" and g.of_key(mina) == "010000002"):
        pytest.skip("Lith Harbor's NPCs aren't where this test knows them")
    (spot,) = mapview.locations(real, kim)
    assert spot.map == "010000000" and spot.npc and spot.says == "npc_where_says"
    (spot,) = mapview.locations(real, mina)
    assert (spot.map, spot.npc, spot.inside) == ("010000000", False, "Lith Harbor Department Store")
    assert spot.at == pytest.approx((0.812, 0.802), abs=0.001)


@needs_kb
def test_an_npc_in_a_building_with_a_picture_shows_its_door_then_where_it_stands(real):
    """Arthur is in Henesys Town Hall, which has a picture of its own: first Henesys with the hall's door (orange),
    then the hall with Arthur (green), so the player knows which building to go into."""
    from maplehelper import routes
    g = routes.of(real)
    arthur = real.npc_key("Arthur")
    hall = g.of_key(arthur) if arthur else None
    if not hall or g.name(hall) != "Henesys Town Hall" or not g.npc_spot(arthur):
        pytest.skip("Arthur isn't in Henesys Town Hall by this knowledge base")
    door, inside = mapview.locations(real, arthur)
    assert g.name(door.map) == "Henesys" and not door.npc and door.inside == "Henesys Town Hall"
    assert inside.map == hall and inside.npc
    # a town's own NPC (Mr. Kim, in the open on Lith Harbor) has no door to show
    assert not mapview.in_building(g, "010000000")


@needs_kb
def test_a_quest_shows_its_giver_then_who_it_is_turned_in_to(real):
    from maplehelper import quests
    qs = [q for k, e in real.entities.items() if e["category"] == "quest" and (q := quests.quest(real, k))]
    q = next(q for q in qs if q.turn_in and q.turn_in != q.npc and mapview.locations(real, q.key)
             and len(mapview.locations(real, q.key)) == 2)
    start, end = mapview.locations(real, q.key)
    assert (start.role, start.name) == ("quest_where_start", real.get(real.npc_key(q.npc))["name"])
    assert (end.role, end.name) == ("quest_where_end", real.get(real.npc_key(q.turn_in))["name"])
    # one NPC both gives it and takes it back: shown once
    same = next(q for q in qs if not q.turn_in and mapview.locations(real, q.key))
    assert [s.role for s in mapview.locations(real, same.key)] == ["quest_where_start"]


@needs_kb
def test_the_window_is_as_tall_as_its_pictures(real):
    """Arthur's door and the hall with him in it: both seen at once, no scrolling (the owner had to scroll to the
    second); one picture after them makes the window shorter again."""
    arthur = real.npc_key("Arthur")
    if not arthur or len(mapview.locations(real, arthur)) != 2:
        pytest.skip("Arthur isn't in a building with a picture by this knowledge base")
    d = mapview.MapLocationDialog(real, "en", "")
    d.show()
    d.show_map(arthur)
    app.processEvents()
    tall = d.height()
    assert d.scroll.verticalScrollBar().maximum() == 0
    d.show_map(real.npc_key("Mr. Kim"))
    app.processEvents()
    assert d.height() < tall and d.scroll.verticalScrollBar().maximum() == 0
    d.close()
