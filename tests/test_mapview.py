"""The map window: where it opens (beside the chat, then where the player left it) and what it shows for a map, an NPC
and a quest."""
import json
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from maplehelper import routes
from maplehelper.i18n import I18n
from maplehelper.minimap import Here
from maplehelper.ui import mapview
from maplehelper.ui.location import LOCATION

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


HENESYS, HG1, HG2, GARDEN = "100000000", "100000001", "100000002", "100000003"
PERION, SHOP, SLEEPY, ANTT = "100000100", "100000004", "100000300", "100000301"


def _page(kb, key: str, name: str, body: str) -> None:
    """One more KB page (mirrors test_routes' helper: this file must not import another test module)."""
    cat, _, slug = key.partition("/")
    (kb / "pages" / cat).mkdir(parents=True, exist_ok=True)
    front = json.dumps({"name": name, "category": cat, "props": {}}, indent=1)
    (kb / "pages" / cat / f"{slug}.md").write_text(f"---\n{front}\n---\n\n# {name}\n\n{body}\n", encoding="utf-8")
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    index.append({"key": key, "id": slug, "name": name, "category": cat, "props": {}})
    (kb / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _picture(kb, key: str) -> None:
    """A stand-in minimap picture, so entrances and NPC dots count this map (image_path needs the file)."""
    cat, _, slug = key.partition("/")
    rel = f"img/{cat}/{slug}.png"
    p = kb / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    assert QPixmap(16, 12).save(str(p))
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    next(e for e in index if e["key"] == key)["image"] = rel
    (kb / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _map(mid, name, portals=(), npcs=(), town=False, street="Victoria Road", region="VictoriaIsland", minimap=True):
    return {"id": mid, "name": name, "street": street, "region": region, "town": town, "return": mid,
            "minimap": [1600, 800, 800, 400] if minimap else None,
            "portals": [{"to": to, "name": f"p{i}", "x": 700, "y": 0} for i, to in enumerate(portals)],
            "npcs": [{"id": i, "name": n, "x": -700, "y": 0} for i, n in npcs]}


@pytest.fixture
def tiny(kb_copy):
    """Henesys to Perion on foot (Hunting Ground I between them), a cab in each town, an NPC on Perion, one in a
    picture-less shop off Henesys, a quest from the first to a second NPC on Henesys, and two connected maps no
    way from Henesys reaches."""
    _page(kb_copy, f"map/{PERION}", "Perion", "Location Victoria Road / Victoria Island\nMap type Town")
    _page(kb_copy, f"map/{SHOP}", "Henesys Hair Salon", "Location Victoria Road / Victoria Island\nMap type Field")
    _page(kb_copy, f"map/{SLEEPY}", "Sleepywood", "Location Victoria Road / Victoria Island\nMap type Field")
    _page(kb_copy, f"map/{ANTT}", "Ant Tunnel I", "Location Victoria Road / Victoria Island\nMap type Field")
    _page(kb_copy, "npc/9001", "Regular Cab", "What Regular Cab Says\nTake a cab.\nSimilar NPCs")
    _page(kb_copy, "npc/9002", "Regular Cab", "What Regular Cab Says\nTake a cab.\nSimilar NPCs")
    _page(kb_copy, "npc/2001", "Mina", "What Mina Says\nHello.\nSimilar NPCs")
    _page(kb_copy, "npc/2002", "Vivi", "What Vivi Says\nHello.\nSimilar NPCs")
    _page(kb_copy, "npc/2003", "Rina", "What Rina Says\nHello.\nSimilar NPCs")
    _page(kb_copy, "quest/3001", "Mina's Errand", "Start: Mina · Turn in: Rina\n01 Talk to Mina.")
    for key in (f"map/{HENESYS}", f"map/{HG1}", f"map/{PERION}", f"map/{SLEEPY}"):
        _picture(kb_copy, key)
    data = {"taxi": [HENESYS, PERION], "maps": [
        _map(HENESYS, "Henesys", [HG1, SHOP], [("9001", "Regular Cab"), ("2003", "Rina")], town=True),
        _map(HG1, "Henesys Hunting Ground I", [HENESYS, PERION]),
        _map(HG2, "Henesys Hunting Ground II", [HG1, GARDEN]),
        _map(GARDEN, "Snail Garden", [HENESYS]),
        _map(PERION, "Perion", [HG1], [("9002", "Regular Cab"), ("2001", "Mina")], town=True),
        _map(SHOP, "Henesys Hair Salon", [HENESYS], [("2002", "Vivi")], minimap=None),
        _map(SLEEPY, "Sleepywood", [ANTT]),
        _map(ANTT, "Ant Tunnel I", []),
    ]}
    (kb_copy / routes.ROUTES_FILE).write_text(json.dumps(data), encoding="utf-8")
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(kb_copy)
    return kb, routes.of(kb)


def test_the_way_from_here_to_a_map(tiny):
    """Henesys to Perion on foot: through Hunting Ground I, no cab, no tail cards."""
    kb, _ = tiny
    way = mapview.way_from_here(kb, f"map/{PERION}", HENESYS)
    assert way is not None and way.dest == PERION and way.npc == "" and way.tail == ()
    assert [(leg.kind, leg.frm, leg.to) for leg in way.route.legs] == [
        ("portal", HENESYS, HG1), ("portal", HG1, PERION)]


def test_already_there_is_an_empty_way(tiny):
    kb, _ = tiny
    way = mapview.way_from_here(kb, f"map/{HENESYS}", HENESYS)
    assert way is not None and way.dest == HENESYS and way.route.legs == []


def test_no_live_map_or_unknown_map_is_no_way(tiny):
    kb, _ = tiny
    assert mapview.way_from_here(kb, f"map/{PERION}", None) is None
    assert mapview.way_from_here(kb, f"map/{PERION}", "999999999") is None
    assert mapview.way_from_here(kb, "map/999999999", HENESYS) is None


def test_no_way_falls_back_to_the_entrances(tiny):
    """Ant Tunnel is reached only from Sleepywood, no way from Henesys: None, while the window's usual view
    still shows Sleepywood's door."""
    kb, g = tiny
    assert g.route(HENESYS, ANTT) is None
    assert mapview.way_from_here(kb, f"map/{ANTT}", HENESYS) is None
    (spot,) = mapview.locations(kb, f"map/{ANTT}")
    assert (spot.map, spot.npc) == (SLEEPY, False)


def test_the_way_to_an_npc(tiny):
    kb, _ = tiny
    mina = kb.npc_key("Mina")
    way = mapview.way_from_here(kb, mina, HENESYS)
    assert way is not None and way.dest == PERION and way.npc == mina and way.tail == ()


def test_an_npc_with_no_picture_keeps_its_door(tiny):
    """Vivi's shop has no minimap of its own: the way ends at Henesys, whose door card stays as the tail."""
    kb, g = tiny
    assert g.npc_spot(kb.npc_key("Vivi")) is None
    way = mapview.way_from_here(kb, kb.npc_key("Vivi"), HENESYS)
    assert way is not None and way.dest == SHOP
    assert [s.map for s in way.tail] == [HENESYS] and not any(s.npc for s in way.tail)


def test_the_way_to_a_quest_giver_keeps_the_turn_in(tiny):
    """Mina gives it on Perion, Rina takes it on Henesys: the way goes to Perion, Rina's card stays as the tail."""
    kb, _ = tiny
    way = mapview.way_from_here(kb, "quest/3001", HENESYS)
    assert way is not None and way.dest == PERION and way.npc == kb.npc_key("Mina")
    (tail,) = way.tail
    assert (tail.role, tail.map, tail.npc) == ("quest_where_end", HENESYS, True)


def test_step_lines(tiny):
    """The window says what the route page says: portal side, cab, fare, arrival."""
    _, g = tiny
    t = I18n("en")
    portal = g.route(HENESYS, HG1).legs[0]
    assert routes.side(portal.spot) == "right"
    says = mapview.route_says(t, g, portal).replace(" ", " ")
    assert "**right**" in says and "Henesys Hunting Ground I" in says
    shown = mapview.says_html(t, says)
    assert "<b>right</b>" in shown and "**" not in shown
    cab = routes.Leg(HENESYS, PERION, "taxi", "Regular Cab", "npc/9001", (0.0625, 0.5))
    assert "Regular Cab" in mapview.route_says(t, g, cab) and "Perion" in mapview.route_says(t, g, cab)
    assert "300" in mapview.route_says(t, g, routes.Leg(HENESYS, PERION, "boat", "Shanks", "", None, 300))
    assert mapview.route_says(t, g, None) == t("route_arrive")


def test_has_location_ignores_the_live_map(tiny):
    """The ◎ set is stable: knowing where the player is never adds or removes it."""
    kb, _ = tiny
    keys = [f"map/{PERION}", kb.npc_key("Mina"), "quest/3001", f"map/{ANTT}"]
    before = [mapview.has_location(kb, k) for k in keys]
    assert before == [True, True, True, True]
    LOCATION.set(Here(HENESYS, (0.5, 0.5)))
    try:
        assert [mapview.has_location(kb, k) for k in keys] == before
    finally:
        LOCATION.set(None)


def test_the_window_shows_the_way_from_the_live_map(tiny, isolated_store):
    """Perion's ◎ with the player on Henesys: "From Henesys to Perion", a step and the arrival; on Perion:
    already here; with no live map: the generic way in again."""
    kb, _ = tiny
    LOCATION.set(Here(HENESYS, (0.5, 0.5)))
    d = mapview.MapLocationDialog(kb, "en", "", isolated_store.Settings())

    def cards():
        return [w for w in d.findChildren(QFrame, "Card") if w.isVisibleTo(d)]
    try:
        d.show()
        d.show_map(f"map/{PERION}")
        app.processEvents()
        assert d.title_label.text() == "From Henesys to Perion"
        assert len(cards()) == 3                                # two steps and the arrival
        rows = [lb.text() for lb in d.findChildren(QLabel, "RowLabel")]
        assert any("right" in r for r in rows) and not any("**" in r for r in rows)
        LOCATION.set(Here(PERION, (0.2, 0.3)))                # walking in re-renders: already here
        app.processEvents()
        assert d.title_label.text() == "From Perion to Perion"
        assert len(cards()) == 1
        shown = " ".join(lb.text() for lb in d.findChildren(QLabel))
        assert "already here" in shown
        LOCATION.set(Here(PERION, (0.8, 0.8)))                # the dot moved: no rebuild, just its picture
        app.processEvents()
        assert len(cards()) == 1 and d.title_label.text() == "From Perion to Perion"
        LOCATION.set(None)                                    # no live map: the generic way in again
        app.processEvents()
        assert d.title_label.text() == "The way into Perion"
    finally:
        LOCATION.set(None)
        d.close()
