"""The overlay's lookup data (maplehelper/gamelookup.py): an NPC's place (its own map, or the town door when
it stands in a building off it), the way from the player's map, an item's droppers and sellers, and what spawns
on a map. The content tests run against the real knowledge base (skipped without it, and skipped when the data
they pin moved); the ordering and caching rules run against the fixture KB with canned table rows."""
from pathlib import Path

import pytest

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")


@pytest.fixture(scope="module")
def real():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


# ---------------------------------------------------------------- an NPC's place


@needs_kb
def test_dr_faymus_stands_in_the_pharmacy(real):
    from maplehelper import routes
    from maplehelper.gamelookup import npc_place
    g = routes.of(real)
    npc = next(n for n in g.known["010003002"].npcs if str(n.get("id")) == "410")
    want = g.picture_spot("010003002", npc)
    if want is None:
        pytest.skip("the KB has no picture placing Dr. Faymus on his own map")
    place = npc_place(real, "npc/410", here_map="010002000")        # elsewhere: Ellinia
    assert place is not None
    assert (place.map, place.map_name, place.inside) == ("010003002", "Kerning City Pharmacy", "")
    assert place.spot == want                                       # on the room's picture (the scene frame)

@needs_kb
def test_dr_faymus_from_kerning_city_guides_to_the_pharmacy_door(real):
    from maplehelper import routes
    from maplehelper.gamelookup import npc_place
    door = next(leg.spot for leg in routes.of(real).edges["010003000"]
                if leg.kind == "portal" and leg.to == "010003002")
    assert door is not None
    place = npc_place(real, "npc/410", here_map="010003000")
    assert place is not None
    assert (place.map, place.map_name, place.inside) == ("010003000", "Kerning City", "010003002")
    assert place.spot == door


def test_an_unknown_npc_has_no_place(kb):
    from maplehelper.gamelookup import npc_place
    assert npc_place(kb, "npc/999999999") is None
    assert npc_place(kb, "map/100000000") is None                  # no NPC key at all
    assert npc_place(kb, "") is None


# ---------------------------------------------------------------- the way there


def test_no_here_is_no_way(kb):
    from maplehelper.gamelookup import Way, way_to
    assert way_to(kb, None, "010003000") == Way(None, (), False)


def test_no_route_is_no_way(kb):
    from maplehelper.gamelookup import Way, way_to
    assert way_to(kb, "000000001", "000000002") == Way("000000001", (), False)


@needs_kb
def test_the_same_map_is_already_there(real):
    from maplehelper.gamelookup import Way, way_to
    assert way_to(real, "010003000", "010003000") == Way("010003000", (), True)


@needs_kb
def test_kerning_city_to_ellinia_has_legs(real):
    from maplehelper.gamelookup import way_to
    way = way_to(real, "010003000", "010002000")
    assert way.here == "010003000" and way.known and way.legs
    assert way.legs[0].frm == "010003000" and way.legs[-1].to == "010002000"


# ---------------------------------------------------------------- an item's sources


def _shop(npc, npc_key, item_key, price, place="Victoria Road: Some Shop · Somewhere"):
    return {"npc": npc, "npc_key": npc_key, "item": "X", "item_key": item_key, "item_type": "Use",
            "price": price, "place": place, "label": "", "rank": ""}


def test_sellers_are_each_npc_once_cheapest_first(kb, monkeypatch):
    from maplehelper import tables
    from maplehelper.gamelookup import Seller, item_sources
    rows = [_shop("Bob", "npc/1", "item/9", 100), _shop("Bob", "npc/1", "item/9", 60),
            _shop("Al", "npc/2", "item/9", None), _shop("Zed", "npc/3", "item/8", 5),
            _shop("Ghost", "", "item/9", 1), _shop("Lost", "npc/4", "", 1)]
    monkeypatch.setattr(tables, "rows", lambda k, name, build=True: list(rows) if name == "shops" else [])
    assert item_sources(kb, "item/9")[1] == [Seller("", "Ghost", 1, rows[4]["place"]),
                                             Seller("npc/1", "Bob", 60, rows[0]["place"]),
                                             Seller("npc/2", "Al", None, rows[2]["place"])]
    assert [s.name for s in item_sources(kb, "item/8")[1]] == ["Zed"]
    assert item_sources(kb, "item/404") == ([], [])


def test_droppers_are_each_name_once_lowest_level_first(kb, monkeypatch):
    from maplehelper.gamelookup import item_sources
    monkeypatch.setattr(kb, "droppers", {"item/9": ["monster/a", "monster/b", "monster/a2"]}, raising=False)
    ents = {"monster/a": {"name": "Same", "props": {"Level": 5}},
            "monster/a2": {"name": "Same", "props": {"Level": 3}},
            "monster/b": {"name": "Low", "props": {"Level": 1}}}
    monkeypatch.setattr(kb, "get", lambda k: ents.get(k, {}))
    assert [(d.key, d.name, d.level) for d in item_sources(kb, "item/9")[0]] == [
        ("monster/b", "Low", 1), ("monster/a", "Same", 5)]         # the first "Same" stands for both


def test_an_unknown_item_has_no_sources(kb):
    from maplehelper.gamelookup import item_sources
    assert item_sources(kb, "item/999999999") == ([], [])


@needs_kb
def test_red_potion_droppers_are_by_level(real):
    from maplehelper.gamelookup import item_sources
    droppers, _ = item_sources(real, "item/270")
    assert droppers, "Red Potion lost all its droppers in this KB"
    assert [d.level for d in droppers] == sorted(d.level for d in droppers)
    assert len({d.name for d in droppers}) == len(droppers)        # each name once
    snail = [d for d in droppers if d.name == "Snail"]
    if not snail:
        pytest.skip("Snail no longer drops Red Potion in this KB")
    assert snail[0].level == 1


@needs_kb
def test_red_potion_sellers_are_cheapest_first(real):
    from maplehelper.gamelookup import item_sources
    _, sellers = item_sources(real, "item/270")
    assert sellers, "Red Potion lost all its sellers in this KB"
    assert len({s.key or s.name for s in sellers}) == len(sellers)  # each NPC once
    assert [s.price is None for s in sellers] == sorted(s.price is None for s in sellers)
    priced = [s.price for s in sellers if s.price is not None]
    assert priced == sorted(priced)                               # cheapest first
    faymus = [s for s in sellers if s.key == "npc/410"]
    if not faymus:
        pytest.skip("Dr. Faymus no longer sells Red Potion in this KB")
    assert faymus[0].price == 50 and "Pharmacy" in faymus[0].place


# ---------------------------------------------------------------- what spawns on a map


def _spawn(monster, monster_key, level, map_key, count):
    return {"monster": monster, "monster_key": monster_key, "level": level, "map": "M", "map_key": map_key,
            "street": "S", "count": count, "share": None, "mob_rate": None, "respawn": None}


def test_map_monsters_are_most_first(kb, monkeypatch):
    from maplehelper import tables
    from maplehelper.gamelookup import map_monsters
    rows = [_spawn("Bee", "monster/20", 5, "map/000000001", 3),
            _spawn("Ant", "monster/21", 2, "map/000000001", 9),
            _spawn("Bee", "monster/20", 5, "map/000000001", 1),    # twice: its most stands for it
            _spawn("Else", "monster/22", 7, "map/000000002", 4)]
    monkeypatch.setattr(tables, "rows", lambda k, name, build=True: list(rows) if name == "spawns" else [])
    assert map_monsters(kb, "map/000000001") == [("monster/21", "Ant", 2, 9), ("monster/20", "Bee", 5, 3)]
    assert map_monsters(kb, "000000001") == map_monsters(kb, "map/000000001")   # a bare id works too
    assert map_monsters(kb, "map/000000002") == [("monster/22", "Else", 7, 4)]
    assert map_monsters(kb, "map/999999999") == []
    assert map_monsters(kb, "") == []


@needs_kb
def test_snail_hunting_ground_lists_snail_first(real):
    from maplehelper.gamelookup import map_monsters
    for variant in ("map/000000040", "000000040"):                 # Snail Hunting Ground I, both key forms
        rows = map_monsters(real, variant)
        snail = [r for r in rows if r[0] == "monster/2"]
        if not snail:
            pytest.skip("Snail no longer spawns on Snail Hunting Ground I in this KB")
        assert [r[3] for r in rows] == sorted((r[3] for r in rows), reverse=True)   # most first
        assert snail[0][1:] == ("Snail", 1, 40)


# ---------------------------------------------------------------- once per KB, never per call


@needs_kb
def test_each_table_is_read_once_per_kb(monkeypatch):
    from maplehelper import tables
    from maplehelper.gamelookup import item_sources, map_monsters
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    calls: list[str] = []
    orig = tables.rows

    def counting(k, name, build=True):
        calls.append(name)
        return orig(k, name, build=build)

    monkeypatch.setattr(tables, "rows", counting)
    item_sources(kb, "item/270")
    item_sources(kb, "item/270")
    map_monsters(kb, "map/000000040")
    map_monsters(kb, "000000040")
    assert calls.count("shops") == 1 and calls.count("spawns") == 1
