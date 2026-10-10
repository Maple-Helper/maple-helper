"""Where game things are and how to reach them, as plain data for the in-game overlay windows (agent B):
an NPC's place (its own map, or the town door when it stands in a building off it), the route legs from the
player's map, an item's droppers and sellers, and what spawns on a map. Pure data, no Qt: everything Qt needs
(mapview.route_picture, LOCATION, i18n) happens in the window layer, not here."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import routes, tables
from .kb import memo

_CACHE = "_game_lookup"     # the kb.memo key holding this module's per-KB table indexes

# the door the game names in00/in01_1/jobin00...: the same rule as ui.npcoverlay.buildings_off, kept here in words
# (npcoverlay imports Qt, this module must stay importable without it)
_DOOR = re.compile(r"in\d")
_UNKNOWN_LEVEL = 999        # a monster without a Level in its props, sorted after every known one


@dataclass(frozen=True)
class Place:
    """Where to go on the picture shown: the map id ("010003000"), its name and street, the spot on that map's
    picture (fractions, None when the KB can't place it), and a building's map id when `map` is the town and
    `spot` is the building's door."""
    map: str
    map_name: str
    street: str
    spot: tuple[float, float] | None
    inside: str = ""


@dataclass(frozen=True)
class Way:
    """The way from the player's map to a target map: the player's map (None when unknown), the routes.Leg steps
    there (() when already there), and whether a way exists at all (False when here is None or no route does)."""
    here: str | None
    legs: tuple
    known: bool


@dataclass(frozen=True)
class Dropper:
    """A monster that drops an item: its key, name, and level."""
    key: str
    name: str
    level: int


@dataclass(frozen=True)
class Seller:
    """An NPC selling an item: its KB key ("" when the table didn't resolve it), name, price (None when the KB
    names no price) and where it sells."""
    key: str
    name: str
    price: int | None
    place: str


def _buildings_off(g, mid: str) -> list:
    """The buildings entered from this map, as the portal legs that lead in: the same rule as
    ui.npcoverlay.buildings_off (a door the game names in00/in01_1/jobin00, into a map that leads back here),
    answered here so this module stays Qt-free."""
    edges = getattr(g, "edges", {})
    return [leg for leg in edges.get(mid, ())
            if leg.kind == "portal" and _DOOR.search(leg.via or "") and leg.to in g.known
            and any(back.to == mid for back in edges.get(leg.to, ()))]


def _street(g, mid: str) -> str:
    info = g.known.get(mid)
    return info.street if info is not None else ""


def npc_place(kb, key: str, here_map: str | None = None) -> Place | None:
    """Where the NPC "npc/<id>" stands: its own map and routes.Graph.picture_spot there. When here_map is a town
    the NPC's map is a building off (npcoverlay.buildings_off has a leg to it), the town Place instead, its spot
    the door's and `inside` the building's map id. None for an NPC on no known map (or no NPC key at all)."""
    if not str(key or "").startswith("npc/"):
        return None
    slug = key.partition("/")[2]
    if not slug:
        return None
    g = routes.of(kb)
    mids: list[str] = []     # every map the NPC stands on, the open ones first (its first place, as npcs_on shows)
    for pool in (g.maps, g.known):
        for mid, info in pool.items():
            if mid not in mids and any(str(n.get("id")) == slug for n in info.npcs):
                mids.append(mid)
    if not mids:
        return None
    if here_map:
        doors = {leg.to: leg for leg in _buildings_off(g, here_map)}
        inside = next((mid for mid in mids if mid in doors), None)
        if inside is not None:
            return Place(here_map, g.name(here_map), _street(g, here_map), doors[inside].spot, inside)
    mid = mids[0]
    npc = next((n for n in g.known[mid].npcs if str(n.get("id")) == slug), None)
    return Place(mid, g.name(mid), _street(g, mid), g.picture_spot(mid, npc) if npc else None)


def way_to(kb, here_map: str | None, to_map: str) -> Way:
    """The routes.of(kb).route(here, to) as a Way: unknown here or no route is known False with no legs."""
    if here_map is None:
        return Way(None, (), False)
    r = routes.of(kb).route(here_map, to_map)
    if r is None:
        return Way(here_map, (), False)
    return Way(here_map, tuple(r.legs), True)


def _monster_level(kb, key: str) -> int:
    lvl = ((kb.get(key) or {}).get("props") or {}).get("Level")
    if isinstance(lvl, bool):
        return _UNKNOWN_LEVEL
    if isinstance(lvl, (int, float)):
        return int(lvl)
    m = re.search(r"\d+", str(lvl or ""))
    return int(m.group(0)) if m else _UNKNOWN_LEVEL


def item_sources(kb, key: str) -> tuple[list[Dropper], list[Seller]]:
    """An item's sources: its droppers (kb.droppers' in-game monsters, each name once, lowest level first) and
    its sellers (shops.tsv's rows for it, each NPC once at its cheapest price, cheapest first, unpriced last).
    ([], []) for an item the KB knows nothing of."""
    return _droppers(kb, key), list(_sellers_by_item(kb).get(key, []))


def _droppers(kb, key: str) -> list[Dropper]:
    out, seen = [], set()
    for m in kb.droppers.get(key, []):
        name = str((kb.get(m) or {}).get("name") or m)
        if name in seen:
            continue
        seen.add(name)
        out.append(Dropper(m, name, _monster_level(kb, m)))
    return sorted(out, key=lambda d: d.level)


def _sellers_by_item(kb) -> dict[str, list[Seller]]:
    """item key -> its sellers, built once per KB (shops.tsv is read once, never per call)."""
    cache = memo(kb, _CACHE)
    if "sellers" not in cache:
        by_item: dict[str, dict[str, Seller]] = {}
        for r in tables.rows(kb, "shops", build=False):
            ikey = str(r.get("item_key") or "")
            who = str(r.get("npc_key") or "") or str(r.get("npc") or "")
            if not ikey or not who:
                continue
            price = r.get("price")
            price = int(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
            row = Seller(str(r.get("npc_key") or ""), str(r.get("npc") or ""), price, str(r.get("place") or ""))
            prev = by_item.setdefault(ikey, {}).get(who)
            if prev is None or (row.price is not None and (prev.price is None or row.price < prev.price)):
                by_item[ikey][who] = row
        cache["sellers"] = {k: sorted(v.values(), key=lambda s: (s.price is None, s.price or 0,
                                                                 s.name.casefold()))
                            for k, v in by_item.items()}
    return cache["sellers"]


def map_monsters(kb, map_key_or_id: str) -> list[tuple[str, str, int, int]]:
    """(monster key, name, level, how many spawn) on a map, from the KB's spawns table, the most spawns first;
    each monster once (its most-populated row stands for it). "map/<id>" or a bare id; [] for a map with no
    spawns."""
    v = str(map_key_or_id or "")
    mid = v.partition("/")[2] if v.startswith("map/") else v
    rows = _spawns_by_map(kb).get(f"map/{mid.zfill(9)}", []) if mid else []
    return list(rows)


def _spawns_by_map(kb) -> dict[str, list[tuple[str, str, int, int]]]:
    """map key -> what spawns on it, built once per KB (spawns.tsv is read once, never per call)."""
    cache = memo(kb, _CACHE)
    if "spawns" not in cache:
        by_map: dict[str, dict[str, tuple[str, str, int, int]]] = {}
        for r in tables.rows(kb, "spawns", build=False):
            mk, mkey = str(r.get("map_key") or ""), str(r.get("monster_key") or "")
            if not mk or not mkey:
                continue
            lvl = r.get("level")
            lvl = int(lvl) if isinstance(lvl, (int, float)) and not isinstance(lvl, bool) else 0
            n = r.get("count")
            n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) else 0
            row = (mkey, str(r.get("monster") or mkey), lvl, n)
            prev = by_map.setdefault(mk, {}).get(mkey)
            if prev is None or n > prev[3]:
                by_map[mk][mkey] = row
        cache["spawns"] = {k: sorted(v.values(), key=lambda t: (-t[3], t[1].casefold()))
                           for k, v in by_map.items()}
    return cache["spawns"]
