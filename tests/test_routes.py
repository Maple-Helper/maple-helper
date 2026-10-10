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
# "knowledge base" in the reason: CI (MAPLEHELPER_REQUIRE_REAL_KB) fails a real-KB test that skips. Every published
# kb.zip has routes.json now (each nightly run refreshes it), so a KB without it is a broken one, not an old one
needs_routes = pytest.mark.skipif(not (REAL_KB / routes.ROUTES_FILE).exists(),
                                  reason="no routes.json in the real knowledge base")

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


def test_hidden_portals_are_spots_on_the_minimap(world):
    """A map's invisible teleports as spots on its minimap picture, raised PORTAL_MARK_RISE above their points; one
    off the picture and a map without any give none."""
    kb, _ = world
    data = {"maps": [{**_map(HENESYS, "Henesys", town=True),
                      "hidden": [{"name": "hide01", "x": 0, "y": 0}, {"name": "far", "x": 5000, "y": 0}]},
                     _map(PERION, "Perion", town=True),
                     {**_map(ORBIS, "Orbis", town=True), "hidden": [{"name": "h", "x": 0, "y": 0}]}]}
    g = routes.Graph(kb, data)
    assert g.hidden_spots(HENESYS) == [(0.5, (400 - routes.PORTAL_MARK_RISE) / 800)]
    assert g.hidden_spots(PERION) == []
    # a map off the routes (not in the game, as the KB says) still has its dots: the player may stand on it anyway
    assert ORBIS not in g.maps and g.hidden_spots(ORBIS) == [(0.5, (400 - routes.PORTAL_MARK_RISE) / 800)]


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
             "miniMapCenterY": 40, "portals": [{"name": "out", "type": 2, "toMapId": "000000002", "x": 1, "y": 2},
                                               {"name": "nowhere", "toMapId": "999999999", "x": 0, "y": 0},
                                               {"name": "door", "type": 1, "toMapId": "000000002", "x": 5, "y": 6}],
             "intraPortals": [{"name": "hide01", "type": 1, "x": 7, "y": 8, "toName": "hide01_1"},
                              {"name": "trap", "type": 3, "x": 9, "y": 10, "toName": "h001"},
                              {"name": "dup", "type": 1, "x": 7, "y": 8, "toName": "hide01"},
                              {"name": "shown", "type": 2, "x": 11, "y": 12, "toName": "x"}],
             "npcs": [{"id": "7", "name": "Regular Cab", "x": 3, "y": 4}], "monsters": [1, 2, 3]},
            {"id": "000000002", "name": "B", "portals": [], "npcs": []},
            # a shop: no minimap, its NPCs placed on its room picture by the view rectangle of its terrain file
            {"id": "000000003", "name": "Shop", "hasMinimapImage": False, "portals": [],
             "npcs": [{"id": "8", "name": "Andre", "x": -72, "y": -12}]}]
    page = '<script src="/_next/static/chunks/app/msclassic/%5Blocale%5D/pathfinder/page-abc.js"></script>'
    js = 'let h=["000000001","000000002"];var r=/^[0-9]{9}$/'
    answers = {scrape_meowdb.MAPS_DATA: json.dumps(maps), scrape_meowdb.PATHFINDER: page,
               scrape_meowdb.MAP_TERRAIN.format(id="000000003"): json.dumps({"vr": [-400, -300, 400, 300], "fh": []})}
    monkeypatch.setattr(scrape_meowdb, "fetch", lambda url, binary=False: answers.get(url, js))
    monkeypatch.setattr(scrape_meowdb, "DELAY_SECONDS", 0)
    monkeypatch.setattr(scrape_meowdb, "KB", tmp_path)
    assert scrape_meowdb.scrape_routes() == 1
    data = json.loads((tmp_path / "routes.json").read_text(encoding="utf-8"))
    assert data["taxi"] == ["000000001", "000000002"]
    a = data["maps"][0]
    assert a["portals"] == [{"to": "000000002", "name": "out", "x": 1, "y": 2},      # none to a map it doesn't know
                            {"to": "000000002", "name": "door", "x": 5, "y": 6}]
    # the invisible teleports (type 1 press-up, type 3 touch), to other maps and within it, once per spot; the game
    # draws the visible ones (type 2) itself
    assert a["hidden"] == [{"name": "door", "x": 5, "y": 6}, {"name": "hide01", "x": 7, "y": 8},
                           {"name": "trap", "x": 9, "y": 10}]
    assert data["maps"][1]["hidden"] == [] and "scene" not in data["maps"][1]   # no NPCs: no terrain fetched
    assert a["minimap"] == [160, 80, 80, 40] and a["npcs"][0]["name"] == "Regular Cab" and "monsters" not in a
    assert "scene" not in a                                                           # a minimap: no room frame
    assert data["maps"][2]["minimap"] is None and data["maps"][2]["scene"] == [800, 600, 400, 300]
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
def test_a_maps_way_in_is_where_niameowdb_draws_leads_back_here(real):
    """The map window's dot: Lith Harbor Department Store's way in is on Lith Harbor's minimap where NiaMeowDB's
    page draws its "Leads back here" dot (left 81.2%, top 80.2%); every way in is a portal into that map, on a
    picture the KB has, the town first."""
    kb, g = real
    store, harbor = "010000002", "010000000"
    if store not in g.maps or harbor not in g.maps:
        pytest.skip("Lith Harbor isn't in the game by this knowledge base")
    (way, *_) = g.entrances(store)
    assert way.frm == harbor and way.to == store and way.kind == "portal"
    assert way.spot == pytest.approx((0.812, 0.802), abs=0.001)
    for mid in list(g.maps)[:80]:
        legs = g.entrances(mid)
        assert all(leg.to == mid and leg.frm != mid and leg.spot and g.minimap(leg.frm) for leg in legs)
        towns = [g.maps[leg.frm].town for leg in legs]
        assert towns == sorted(towns, reverse=True)


def _sound(kb, g, r, a, b):
    """A route as it must be whatever the game adds: from a to b, every step a real edge of the graph (a portal one
    of routes.json's own), every map open and on a continent the release guide confirms."""
    from maplehelper import availability
    o = availability.of(kb)
    assert r and g.name(r.end) == b
    assert [leg.frm for leg in r.legs] == r.maps[:-1]                              # one step after another
    assert all(leg in g.edges[leg.frm] for leg in r.legs)
    raw = {str(m["id"]): {str(p.get("to")) for p in m.get("portals") or []}
           for m in json.loads((REAL_KB / routes.ROUTES_FILE).read_text(encoding="utf-8"))["maps"]}
    assert all(leg.to in raw[leg.frm] for leg in r.legs if leg.kind == "portal")
    assert all(o.entity_open(f"map/{m}") and g.maps[m].continent in o.confirmed for m in r.maps)  # never through Ossyria


@needs_routes
@pytest.mark.parametrize("a,b,first", [
    ("Henesys", "Kerning City", "taxi"), ("Lith Harbor", "Sleepywood", "taxi"), ("Ellinia", "Perion", "taxi"),
    ("Southperry", "Lith Harbor", "boat"), ("Henesys", "Florina Beach", "taxi"),
])
def test_real_routes(real, a, b, first):
    kb, g = real
    r = g.route(g.find(a), g.find(b))
    _sound(kb, g, r, a, b)
    # the cab (the boat) is taken while the KB has one there: a fare change or a cab gone doesn't stop the nightly,
    # a router that ignores it does (the exact routing is test_portals_go_one_way_and_a_short_walk_beats_a_cab and the other fixture tests)
    if any(leg.kind == first for leg in g.edges[r.start]):
        assert r.legs[0].kind == first


@needs_routes
def test_real_walks_and_closed_places(real):
    from maplehelper import availability
    kb, g = real
    o = availability.of(kb)
    walk = g.route(g.find("Lith Harbor"), g.find("Sleepywood"), taxi=False)
    _sound(kb, g, walk, "Lith Harbor", "Sleepywood")
    assert all(leg.kind == "portal" for leg in walk.legs) and len(walk.legs) > 1
    # Orbis and El Nath: not found while the guide keeps Ossyria shut, found once it opens
    for town in ("Orbis", "El Nath"):
        assert (g.find(town) is None) == (not o.place_open(town))
    assert g.not_in_game("Orbis") == (not o.place_open("Orbis"))
    # Victoria Island reaches Maple Island only if some step leads there (today: none, Shanks' boat goes one way)
    maple = {m for m, info in g.maps.items() if info.continent == "Maple Island"}
    into = any(leg.to in maple for frm, legs in g.edges.items() if frm not in maple for leg in legs)
    assert (g.route(g.find("Henesys"), g.find("Southperry")) is None) == (not into)
    text = routes.ai_context(kb, "איך מגיעים מהניסיס לסליפיווד?", None)
    assert "From Henesys to Sleepywood" in text
    assert all(c not in text for c in o.continents - o.confirmed if not o.place_open(c))


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
        assert any("Pick a destination" in lb.text() or "בחרו לאן" in lb.text() for lb in d.pages["route"].findChildren(QLabel))
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


def test_a_shorter_last_row_of_tool_chips_fills_the_width(world, qt, isolated_store):
    from maplehelper.ui.tools import PAGES, ToolsDialog
    kb, _ = world
    d = ToolsDialog(kb, isolated_store.Profiles(), isolated_store.Settings(), "en", "", {}, "train")
    try:
        d.resize(470, 800)
        d.show()
        qt.processEvents()
        first, last = d.nav.button(0), d.nav.button(len(PAGES) - 1)
        alone = len(PAGES) % 3 or 3                  # chips on the last row
        assert last.width() > (3 / alone - 0.2) * first.width() and last.y() > d.nav.button(len(PAGES) - alone - 1).y()
    finally:
        d.close()


def test_a_map_card_offers_the_way_there(world, qt):
    from PySide6.QtWidgets import QToolButton

    from maplehelper.ui.widgets import ROUTE_REQUESTS, EntityCard
    kb, _ = world
    got = []
    ROUTE_REQUESTS.requested.connect(got.append)
    card = EntityCard(kb, f"map/{PERION}", "he")
    way = [b for b in card.findChildren(QToolButton) if b.toolTip() == "איך מגיעים לכאן מהמפה שלכם"]
    assert len(way) == 1
    way[0].click()
    assert got == [f"map/{PERION}"]
    closed = EntityCard(kb, f"map/{ORBIS}", "en")                # not in the game: no way there to offer
    assert not [b for b in closed.findChildren(QToolButton) if b.toolTip() == "How to get here from your map"]
    ROUTE_REQUESTS.requested.disconnect(got.append)


@needs_routes
def test_the_no_cab_way_from_maple_island_is_not_called_free():
    """The walk from Southperry still starts with Shanks' boat (300 mesos): "free" only when no step costs."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    g = routes.of(kb)
    text = routes.ai_context(kb, "how do I get from Southperry to Henesys?")
    a, b = g.find("Southperry"), g.find("Henesys")
    r, walk = g.route(a, b), g.route(a, b, taxi=False)
    # by what the walk itself takes (today Shanks' boat first), not by tonight's fares or ferries
    if r and walk and any(leg.kind == "taxi" for leg in r.legs):
        assert f"Without a cab ({_walk_cost(walk)})" in text and ("(free)" in text) == (_walk_cost(walk) == "free")
    else:
        assert "Without a cab" not in text


def _walk_cost(walk) -> str:
    """What the no-cab line must call a walk: a boat costs mesos, an NPC's trip has no fare in the KB (KB-13)."""
    npcs = list(dict.fromkeys(leg.via for leg in walk.legs if leg.kind == "npc"))
    return ("no cab, but the boat still costs mesos" if walk.paid
            else f"no cab; {', '.join(npcs)} takes you part of the way, the KB lists no fare" if npcs else "free")


@needs_routes
def test_a_taxi_town_asked_from_nowhere_is_no_0_step_route_and_pason_is_not_free():
    """"how do I get to Perion" (map unknown) said "from Perion to Perion (0 steps)"; Pason's trip has no fare in the KB,
    so the walk through it isn't "free"; routes.json's "A Hill West of Henesys " keeps no trailing space."""
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    g = routes.of(kb)
    text = routes.ai_context(kb, "how do I get to Perion")
    assert "Perion is a taxi town" in text and "to Perion (0 steps" not in text and "1. In " in text
    text = routes.ai_context(kb, "how do I get to Florina Beach from Ellinia")
    a, b = g.find("Ellinia"), g.find("Florina Beach")
    r, walk = g.route(a, b), g.route(a, b, taxi=False)
    # by the walk the KB has tonight (Pason's trip, no fare), never "free" while a step is an NPC's or a boat
    if r and walk and any(leg.kind == "taxi" for leg in r.legs):
        assert f"Without a cab ({_walk_cost(walk)})" in text
        assert ("(free)" in text) == (_walk_cost(walk) == "free")
    assert not [m.name for m in g.maps.values() if m.name != m.name.strip()]


def test_route_page_dot_follows_the_player_like_the_map_window(world, qt, isolated_store):
    """'How to get there' draws its steps as the ◎ window does: the legend on the player's map's card, and the blue
    dot there moving with the minimap read, without rebuilding the page (the owner's, 2026-10-08)."""
    from maplehelper.minimap import Here
    from maplehelper.ui import mapview
    from maplehelper.ui.location import LOCATION
    from maplehelper.ui.tools import ToolsDialog
    kb, g = world
    p = isolated_store.Profiles()
    p.add("Kiwi", "Warrior", "Fighter", 20)
    old = LOCATION.here
    try:
        LOCATION.set(Here(GARDEN, (0.2, 0.5)))
        d = ToolsDialog(kb, p, isolated_store.Settings(), "en", "", {}, "route")
        try:
            d.route_to_map(f"map/{PERION}")
            mid, dot = d._route_live
            assert mid == GARDEN and dot.legend is not None and dot.legend.you.isVisibleTo(d.pages["route"])
            assert dot.pic._pic_spec[3] == (0.2, 0.5)
            cards = d.pages["route"].findChildren(mapview.MapLegend)
            assert len(cards) == 1
            LOCATION.set(Here(GARDEN, (0.7, 0.4)))         # moved on the same map: the same picture repaints
            assert d._route_live[1] is dot and dot.pic._pic_spec[3] == (0.7, 0.4)
        finally:
            d.close()
    finally:
        LOCATION.set(old)
