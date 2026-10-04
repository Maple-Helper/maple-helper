"""How to get there: the route graph (KB map connections, taxis, boats, NPC trips), only through maps in the game;
routing; the AI's route context; the nightly routes.json (scrape, validation, patch notes); the Play tools page."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from maplehelper import routes  # noqa: E402

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
# (no "knowledge base" in the reason: CI fails a real-KB test that skips, and a published KB from before the nightly
# map-connections scrape has no routes.json yet)
needs_routes = pytest.mark.skipif(not (REAL_KB / routes.ROUTES_FILE).exists(), reason="no routes.json in data/kb")

HENESYS, HG1, HG2, GARDEN = "100000000", "100000001", "100000002", "100000003"
ORBIS, PERION, SOUTHPERRY, FLORINA = "200000000", "100000100", "000000060", "110000000"


def _page(kb: Path, key: str, name: str, body: str) -> None:
    cat, _, slug = key.partition("/")
    (kb / "pages" / cat).mkdir(parents=True, exist_ok=True)
    front = json.dumps({"name": name, "category": cat, "props": {}}, indent=1)
    (kb / "pages" / cat / f"{slug}.md").write_text(f"---\n{front}\n---\n\n# {name}\n\n{body}\n", encoding="utf-8")
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    index.append({"key": key, "id": slug, "name": name, "category": cat, "props": {}})
    (kb / "index.json").write_text(json.dumps(index), encoding="utf-8")


def _map(mid, name, portals=(), npcs=(), town=False, street="Victoria Road", region="VictoriaIsland"):
    return {"id": mid, "name": name, "street": street, "region": region, "town": town, "return": mid,
            "minimap": [1600, 800, 800, 400],
            "portals": [{"to": to, "name": f"p{i}", "x": 700, "y": 0} for i, to in enumerate(portals)],
            "npcs": [{"id": i, "name": n, "x": -700, "y": 0} for i, n in npcs]}


@pytest.fixture
def world(kb_copy):
    """The fixture KB plus: Orbis (Ossyria, not in the game) on the only walk from Henesys to Perion, a cab in each
    of the two towns, Maple Island's Southperry with a boat captain, and a Florina Beach trip from Henesys."""
    _page(kb_copy, f"map/{ORBIS}", "Orbis", "Location Orbis / Ossyria\nMap type Town")
    _page(kb_copy, f"map/{PERION}", "Perion", "Location Victoria Road / Victoria Island\nMap type Town")
    _page(kb_copy, f"map/{SOUTHPERRY}", "Southperry", "Location Maple Road / Maple Island\nMap type Town")
    _page(kb_copy, f"map/{FLORINA}", "Florina Beach", "Location Florina Road / Victoria Island\nMap type Town")
    _page(kb_copy, "npc/9004", "Pason", "What Pason Says\nWant to head over to Florina Beach?\nSimilar NPCs\nShanks")
    _page(kb_copy, "npc/9005", "Pison", "What Pison Says\nIf you want to head back to where you were before, feel "
                                        "free to talk to me.\nSimilar NPCs")
    _page(kb_copy, "guide/first-steps", "First Steps", "That's when you take the boat from Shanks at the dock to "
                                                       "Henesys and continue questing there.")
    data = {"taxi": [HENESYS, PERION], "maps": [
        _map(HENESYS, "Henesys", [HG1], [("9001", "Regular Cab"), ("9004", "Pason")], town=True),
        _map(HG1, "Henesys Hunting Ground I", [HENESYS, HG2]),
        _map(HG2, "Henesys Hunting Ground II", [HG1, ORBIS, GARDEN]),
        _map(GARDEN, "Snail Garden", [HENESYS]),                       # one way: no portal back from Henesys
        _map(ORBIS, "Orbis", [PERION, HG2], town=True, street="Orbis", region="Ossyria"),
        _map(PERION, "Perion", [ORBIS], [("9002", "Regular Cab")], town=True),
        _map(SOUTHPERRY, "Southperry", [], [("9003", "Shanks")], town=True, street="Maple Road",
             region="MapleIsland"),
        _map(FLORINA, "Florina Beach", [], [("9005", "Pison")], town=True, street="Florina Road"),
    ]}
    (kb_copy / routes.ROUTES_FILE).write_text(json.dumps(data), encoding="utf-8")
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(kb_copy)
    return kb, routes.of(kb)


def _path(g, r):
    return [(leg.kind, g.name(leg.to)) for leg in r.legs] if r else None


def test_only_maps_in_the_game_are_on_the_graph(world):
    kb, g = world
    assert ORBIS not in g.maps and {HENESYS, PERION, SOUTHPERRY, FLORINA} <= set(g.maps)
    # the only walk to Perion passes Orbis: on foot there is no way, by cab there is
    assert g.route(HENESYS, PERION, taxi=False) is None
    assert _path(g, g.route(HENESYS, PERION)) == [("taxi", "Perion")]
    assert g.route(HENESYS, ORBIS) is None and g.find("Orbis") is None and g.not_in_game("Orbis")


def test_portals_go_one_way_and_a_short_walk_beats_a_cab(world):
    _, g = world
    assert _path(g, g.route(GARDEN, HENESYS)) == [("portal", "Henesys")]
    assert _path(g, g.route(HENESYS, GARDEN)) == [("portal", "Henesys Hunting Ground I"),
                                                   ("portal", "Henesys Hunting Ground II"), ("portal", "Snail Garden")]
    assert g.route(HENESYS, HENESYS).legs == []
    # the portal and the cab are ringed where they are on the minimap (x 700 + center 800 of 1600: the right edge)
    first = g.route(HENESYS, HG1).legs[0]
    assert first.spot == (0.9375, 0.5) and routes.side(first.spot) == "right"
    cab = g.route(HENESYS, PERION).legs[0]
    assert cab.via == "Regular Cab" and routes.side(cab.spot) == "left"


def test_boats_and_npc_trips_come_from_the_kb_text(world):
    _, g = world
    assert _path(g, g.route(SOUTHPERRY, FLORINA)) == [("boat", "Henesys"), ("npc", "Florina Beach")]
    trip = g.route(SOUTHPERRY, FLORINA).legs[1]
    assert trip.via == "Pason" and trip.npc == "npc/9004"
    assert _path(g, g.route(FLORINA, HENESYS)) == [("npc", "Henesys")]            # Pison, "back to where you were"
    assert g.route(HENESYS, SOUTHPERRY) is None                                     # the boat goes one way
    assert g.route(SOUTHPERRY, HENESYS).legs[0].fare is None                        # this guide names no price


def test_a_guide_that_says_sails_you_to_with_a_price(world):
    """NiaMeowDB rewrote the guide (Oct 2026): "Shanks at the dock sails you to ... for 300 mesos"."""
    kb, _ = world
    _page(kb.root, "guide/first-steps", "First Steps", "Shanks at the dock sails you to Henesys for 300 mesos once "
                                                       "you reach level 7. The boat is one-way.")
    from maplehelper.kb import KnowledgeBase
    g = routes.of(KnowledgeBase(kb.root))
    boat = g.route(SOUTHPERRY, HENESYS).legs[0]
    assert (boat.kind, boat.via, boat.fare) == ("boat", "Shanks", 300)
    assert "300 mesos" in routes.describe(g, g.route(SOUTHPERRY, HENESYS))[0]


def test_names_and_the_ai_context(world):
    kb, g = world
    assert g.find("henesys") == HENESYS and g.find(PERION) == PERION and g.of_key("npc/9004") == HENESYS
    me = SimpleNamespace(map="Snail Garden")
    assert routes.endpoints(kb, g, "how do I get to Perion?", me.map) == (GARDEN, PERION)
    assert routes.endpoints(kb, g, "how do I get from Perion to Henesys?") == (PERION, HENESYS)
    text = routes.ai_context(kb, "how do I get to Perion?", me)
    assert "From Snail Garden to Perion, 2 steps" in text and "take the taxi to Perion (costs mesos)" in text
    assert "Orbis" not in text and "Without a cab" not in text          # no walk: it would cross Orbis
    assert routes.ai_context(kb, "what does Snail drop?", me) == ""
    assert routes.ai_context(kb, "how do I get to level 30?", me) == ""
    # no map known: from the nearest taxi town
    assert "nearest taxi town, Henesys" in routes.ai_context(kb, "how do I get to Snail Garden?", None)
    # a tagged map card: "how do I get there?"
    assert "to Perion" in routes.ai_context(kb, "how do I get there?", me, [f"map/{PERION}"])
    assert "no known way from Henesys to Southperry" in routes.ai_context(kb, "how do I get from Henesys to "
                                                                           "Southperry?", None)


@pytest.mark.parametrize("q,expected", [
    ("איך מגיעים לסליפיווד?", True), ("איך אני מגיע מהניסיס לפריון", True), ("what's the way to Perion", True),
    ("how do i get to kerning city", True), ("מה הדרך לאלינייה?", True), ("איך מגיעים ללבל 30 מהר?", False),
    ("what does Mano drop?", False), ("כמה HP יש ל-Mano?", False),
])
def test_route_questions(q, expected):
    assert routes.is_route_question(q) is expected


def test_the_prompt_carries_the_route(world):
    from maplehelper import brain
    kb, _ = world
    me = SimpleNamespace(map="Snail Garden", level=20, summary=lambda: "Level: 20")
    prompt = brain.build_prompt("how do I get to Perion?", me, None, kb, False)
    assert "Route (worked out by the app" in prompt and "take the taxi to Perion" in prompt
    assert "Route" not in brain.build_prompt("what does Snail drop?", me, None, kb, False).split("<question>")[0]
    assert '"Route" block' in brain.SYSTEM_PROMPT


def test_no_routes_file_means_no_graph(kb):
    g = routes.Graph(kb)
    assert g.maps == {} and g.route(HENESYS, HG1) is None
    assert routes.ai_context(kb, "how do I get to Henesys?", None) == ""


# ---------------------------------------------------------------- the nightly file

def test_scrape_keeps_the_route_data_it_needs(monkeypatch, tmp_path):
    import scrape_meowdb
    maps = [{"id": "000000001", "name": "A", "streetName": "S", "region": "R", "isTown": True, "returnMap": "000000001",
             "hasMinimapImage": True, "minimapWidth": 160, "minimapHeight": 80, "miniMapCenterX": 80,
             "miniMapCenterY": 40, "portals": [{"name": "out", "toMapId": "000000002", "x": 1, "y": 2},
                                               {"name": "nowhere", "toMapId": "999999999", "x": 0, "y": 0}],
             "npcs": [{"id": "7", "name": "Regular Cab", "x": 3, "y": 4}], "monsters": [1, 2, 3]},
            {"id": "000000002", "name": "B", "portals": [], "npcs": []}]
    page = '<script src="/_next/static/chunks/app/msclassic/%5Blocale%5D/pathfinder/page-abc.js"></script>'
    js = 'let h=["000000001","000000002"];var r=/^[0-9]{9}$/'
    answers = {scrape_meowdb.MAPS_DATA: json.dumps(maps), scrape_meowdb.PATHFINDER: page}
    monkeypatch.setattr(scrape_meowdb, "fetch", lambda url, binary=False: answers.get(url, js))
    monkeypatch.setattr(scrape_meowdb, "DELAY_SECONDS", 0)
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    assert scrape_meowdb.scrape_routes() == 1
    data = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    assert data["taxi"] == ["000000001", "000000002"]
    a = data["maps"][0]
    assert a["portals"] == [{"to": "000000002", "name": "out", "x": 1, "y": 2}]     # none to a map it doesn't know
    assert a["minimap"] == [160, 80, 80, 40] and a["npcs"][0]["name"] == "Regular Cab" and "monsters" not in a
    assert scrape_meowdb.scrape_routes() == 0                                         # nothing new: no change
    # the site down: the file we have stays
    monkeypatch.setattr(scrape_meowdb, "fetch", lambda url, binary=False: None)
    assert scrape_meowdb.scrape_routes() == 0 and (tmp_path / "routes.json").exists()


def test_validation_and_patch_notes(world, kb_copy, tmp_path):
    import shutil

    import kb_release
    assert kb_release.validate(kb_copy)
    old = tmp_path / "old"
    shutil.copytree(kb_copy, old)
    data = json.loads((kb_copy / "routes.json").read_text(encoding="utf-8"))
    data["maps"][0]["portals"].append({"to": GARDEN, "name": "new", "x": 0, "y": 0})
    (kb_copy / "routes.json").write_text(json.dumps(data), encoding="utf-8")
    changed = {c["key"]: c for c in kb_release.diff_kb(old, kb_copy)["changed"]}
    assert changed[f"map/{HENESYS}"]["props"] == [["Connected maps", "Henesys Hunting Ground I",
                                                    "Henesys Hunting Ground I, Snail Garden"]]
    data["maps"][0]["portals"].append({"to": "555555555", "name": "bad", "x": 0, "y": 0})
    data["taxi"].append("444444444")
    (kb_copy / "routes.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="portals to unknown maps.*taxi towns that are no maps"):
        kb_release.validate(kb_copy)
    (kb_copy / "routes.json").write_text("{", encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="routes.json unreadable"):
        kb_release.validate(kb_copy)


# ---------------------------------------------------------------- the real knowledge base

@pytest.fixture(scope="module")
def real():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    return kb, routes.of(kb)


@needs_routes
@pytest.mark.parametrize("a,b,first", [
    ("Henesys", "Kerning City", "taxi"), ("Lith Harbor", "Sleepywood", "taxi"), ("Ellinia", "Perion", "taxi"),
    ("Southperry", "Lith Harbor", "boat"), ("Henesys", "Florina Beach", "taxi"),
])
def test_real_routes(real, a, b, first):
    from maplehelper import availability
    kb, g = real
    r = g.route(g.find(a), g.find(b))
    assert r and r.legs[0].kind == first and g.name(r.end) == b
    assert all(availability.of(kb).entity_open(f"map/{m}") for m in r.maps)      # never through Ossyria
    assert all(g.maps[m].continent in ("Victoria Island", "Maple Island") for m in r.maps)


@needs_routes
def test_real_walks_and_closed_places(real):
    kb, g = real
    walk = g.route(g.find("Lith Harbor"), g.find("Sleepywood"), taxi=False)
    assert walk and all(leg.kind == "portal" for leg in walk.legs) and len(walk.legs) > 5
    assert g.find("Orbis") is None and g.not_in_game("Orbis") and g.find("El Nath") is None
    # Victoria Island can't reach Maple Island (the boat from Southperry goes one way)
    assert g.route(g.find("Henesys"), g.find("Southperry")) is None
    text = routes.ai_context(kb, "איך מגיעים מהניסיס לסליפיווד?", None)
    assert "From Henesys to Sleepywood" in text and "Ossyria" not in text


# ---------------------------------------------------------------- Play tools and the chat card

@pytest.fixture
def qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("lang", ["he", "en"])
def test_route_page(world, qt, isolated_store, lang):
    from PySide6.QtWidgets import QFrame, QLabel

    from maplehelper.ui.tools import PAGES, ToolsDialog
    kb, g = world
    p = isolated_store.Profiles()
    c = p.add("Kiwi", "Warrior", "Fighter", 20)
    c.map = "Snail Garden"
    d = ToolsDialog(kb, p, isolated_store.Settings(), lang, "", {}, "route")
    try:
        assert d.route_from.text() == "Snail Garden"            # from the character's map
        assert any("Pick where to" in lb.text() or "בחרו לאן" in lb.text() for lb in d.pages["route"].findChildren(QLabel))
        d.route_to_map(f"map/{PERION}")
        assert d.stack.currentIndex() == PAGES.index("route") and d.route_to.text() == "Perion"
        steps = [w for w in d.pages["route"].findChildren(QFrame, "Card") if w.isVisibleTo(d)]
        assert len(steps) == 3                                   # two steps and the arrival
        shown = " ".join(lb.text() for lb in d.pages["route"].findChildren(QLabel))
        assert "Regular Cab" in shown and "Orbis" not in shown
        d.route_taxi.setChecked(False)                           # on foot: the walk would cross Orbis
        shown = " ".join(lb.text() for lb in d.pages["route"].findChildren(QLabel) if lb.isVisibleTo(d))
        assert "no way" in shown or "אין דרך" in shown
        d.route_to.setText("Orbis")
        d._find_route()
        shown = " ".join(lb.text() for lb in d.pages["route"].findChildren(QLabel) if lb.isVisibleTo(d))
        assert "in the game yet" in shown or "עוד לא במשחק" in shown
        asked = []
        d.ask_requested.connect(lambda q, shot: asked.append(q))
        d._ask_route(GARDEN, PERION)
        assert asked == [d.t("route_q", a="Snail Garden", b="Perion")]
    finally:
        d.close()


def test_the_last_tool_chip_takes_the_whole_row(world, qt, isolated_store):
    from maplehelper.ui.tools import PAGES, ToolsDialog
    kb, _ = world
    d = ToolsDialog(kb, isolated_store.Profiles(), isolated_store.Settings(), "en", "", {}, "train")
    try:
        d.resize(470, 800)
        d.show()
        qt.processEvents()
        first, last = d.nav.button(0), d.nav.button(len(PAGES) - 1)
        assert last.width() > 2.5 * first.width() and last.y() > d.nav.button(len(PAGES) - 2).y()
    finally:
        d.close()


def test_a_map_card_offers_the_way_there(world, qt):
    from PySide6.QtWidgets import QToolButton

    from maplehelper.ui.widgets import ROUTE_REQUESTS, EntityCard
    kb, _ = world
    got = []
    ROUTE_REQUESTS.requested.connect(got.append)
    card = EntityCard(kb, f"map/{PERION}", "he")
    way = [b for b in card.findChildren(QToolButton) if b.toolTip() == "איך מגיעים לכאן מהמפה שלי"]
    assert len(way) == 1
    way[0].click()
    assert got == [f"map/{PERION}"]
    closed = EntityCard(kb, f"map/{ORBIS}", "en")                # not in the game: no way there to offer
    assert not [b for b in closed.findChildren(QToolButton) if b.toolTip() == "How to get here from my map"]
    ROUTE_REQUESTS.requested.disconnect(got.append)


@needs_routes
def test_the_no_cab_way_from_maple_island_is_not_called_free():
    """The walk from Southperry still starts with Shanks' boat (300 mesos): "free" only when no step costs."""
    from maplehelper.kb import KnowledgeBase
    text = routes.ai_context(KnowledgeBase(REAL_KB), "how do I get from Southperry to Henesys?")
    assert "Without a cab (no cab, but the boat still costs mesos)" in text and "(free)" not in text
