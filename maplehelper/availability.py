"""What is in the live game, read from the knowledge base and nothing else.

The KB is the only source of truth, and the app shows only what it confirms. Nothing here is a list of places
or jobs: everything comes from data/kb at load time.

- The release guide (pages/guide/maplestory-classic-worlds-release-date.md) names what is confirmed for the
  game ("Confirmed content": "Classic maps on Maple Island and Victoria Island ...", "Forgotten Hollow", the
  confirmed bosses) and what is not ("Not at launch": "Ossyria and 3rd job are not initial-launch content").
  It also says that anything it doesn't confirm stays unconfirmed, and unconfirmed content is not shown.
- Every map page says which continent it is on ("Location Maple Road / Maple Island").
- NPC pages give the map they stand on, and quest pages give their NPC and whether the quest has "Ended".

When MeowDB updates those pages (say Ossyria opens), the next KB update brings the change to every player, and
the content appears without a new app version.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RELEASE_GUIDE = "guide/maplestory-classic-worlds-release-date"

_SECTION_END = re.compile(r"^(Level cap|Changes since|Preparing for launch|Not at launch|Confirmed content)\s*$", re.M)
_MAP_LOCATION = re.compile(r"^Location (.+?) / (.+?)\s*$", re.M)
# a map page's location in any form: "Location Maple Road / Maple Island", or one part only ("Location Hidden Street":
# the Free Market, the KPQ stages, the 2nd-job test maps; "Location Victoria Island": the Hollow's instances)
_MAP_LOCATION_ANY = re.compile(r"^Location (.+?)(?: / (.+?))?\s*$", re.M)
# an NPC page's "Location" or "Locations (4)", then its places, one "<map> <street>" line each ("Find path here"
# between them)
_NPC_LOCATION = re.compile(r"^Locations?(?: \(\d+\))?\n", re.M)
# the continent of a map whose page names none: on no continent the KB closes, so in the game unless the release
# guide's "Not at launch" names it (never printed: scope_note leaves it out)
NO_CONTINENT = "(no continent)"
_NO_CONTINENT_SHUT = f"{NO_CONTINENT} · not at launch"
# a map page's "Map type Town" line (the Free Market, Truth Booth); a hunting ground's page has none
_TOWN = re.compile(r"^Map type Town\s*$", re.M)
# an item page's sources and the headings that end them
# ("Dropped By" opens with the community's own list, the monsters players saw drop it in Classic; then the MSEA one)
_ITEM_SOURCES = ("Dropped By", "MSEA Reference Drops", "Where to buy", "Quest Reward", "Quests", "Craftable",
                 "Cash Shop")
_ITEM_STOP = re.compile(r"^(Free Market Prices|Dropped By|Needed By|Recipes|Ingredients|Change history|← Previous|"
                        r"Safe to Sell\?.*|(Similar|Compare) .* items|Craftable \(\d+ recipes?\)|"
                        + "|".join(map(re.escape, _ITEM_SOURCES)) + ")$")
_CRAFTABLE_N = re.compile(r"^Craftable \(\d+ recipes?\)$")      # "Craftable (2 recipes)": Iron Arrows, Processed Leather
_SHOP_PLACE = re.compile(r"^(.+?): (.+?) · (.+)$")          # "Victoria Road: Perion Department Store · Perion"
_PERCENT = re.compile(r"\(\s*[\d.]+\s*%\s*\)")


def _item_sections(lines: list[str]):
    """(heading, its lines) for every source section of an item page."""
    head, body = None, []
    for s in lines:
        if _ITEM_STOP.match(s):
            if head:
                yield head, body
            head, body = ("Craftable" if _CRAFTABLE_N.match(s) else s if s in _ITEM_SOURCES else None), []
        elif head:
            body.append(s)
    if head:
        yield head, body


def _section(text: str, head: str) -> str:
    """The text under a heading line of the release guide, up to the next known heading."""
    m = re.search(rf"^{re.escape(head)}\s*$", text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    end = _SECTION_END.search(rest)
    return rest[:end.start()] if end else rest


class Availability:
    def __init__(self, kb):
        self.kb = kb
        guide = kb.page(RELEASE_GUIDE) if kb.get(RELEASE_GUIDE) else ""
        # a KB with no release guide at all says nothing about what's out (a test's tiny KB): nothing is
        # filtered then. The real KB always has it (tools/kb_release.py refuses to publish one without it).
        self.known = bool(guide)
        self.guide = guide
        # when the KB last checked what is out: the release guide's own date ("2026-09-29T00:00:00.000Z")
        self.verified = str((kb.get(RELEASE_GUIDE) or {}).get("lastmod") or "")[:10]
        self._memo: dict[tuple[str, str], object] = {}       # (what, key) -> answer: pages are read once
        # the guide's own body, not its FAQ/table of contents at the top (which repeats the headings)
        body = guide[guide.rfind("Confirmed content"):] if "Confirmed content" in guide else guide
        self.confirmed_text = _section(body, "Confirmed content")
        self.not_at_launch_text = _section(body, "Not at launch")
        # every map's continent and street, from its own page
        self.map_place: dict[str, tuple[str, str]] = {}       # "map name street" (as monster pages write it) -> ...
        self.map_place_by_name: dict[str, set[str]] = {}       # bare map name -> its continents
        self.continents: set[str] = set()
        self.streets: dict[str, set[str]] = {}                 # street -> continents it appears on
        self.map_cell: dict[str, str] = {}                     # map key -> its "name street" cell
        # an instance: a map on no continent that is no town (the KPQ stages, the 2nd-job test rooms). An NPC sends a
        # party or a job candidate in; nobody walks there to hunt. It stays in the game (its row, its NPCs) but feeds
        # no training, spawn or "most EXP" list: the AI put King Slime's <Last Stage> and the Lv 30 test copies first
        # in "best EXP at 30" (review CORE-1). Map cells and bare names both.
        self.instances: set[str] = set()
        found: list[tuple[str, str, str, str | None]] = []
        towns: set[str] = set()
        for key, e in kb.entities.items():
            if e.get("category") != "map":
                continue
            page = kb.page(key)
            m = _MAP_LOCATION_ANY.search(page)
            if m:
                found.append((key, e.get("name", ""), m.group(1).strip(), (m.group(2) or "").strip() or None))
                if _TOWN.search(page):
                    towns.add(key)
        paired = {(s, c) for _, _, s, c in found if c}
        street_names = {s for s, _ in paired}
        continent_names = {c for _, c in paired} - street_names
        for key, name, street, continent in found:
            if continent is None and street in continent_names:
                street, continent = "", street             # "Location Victoria Island": a continent, no street
            elif continent in street_names:
                # "Location Yellow Mushroom House / Hidden Street" (two pages write it reversed): "Hidden Street"
                # is a street on several continents, no continent of its own. Printed as one, the AI was told the
                # 43 Victoria maps on that street (Pig Park, Wild Boar) were not in the game.
                street, continent = continent, None
            if continent is None:
                continent = NO_CONTINENT if not self._named(name, self.not_at_launch_text) else _NO_CONTINENT_SHUT
            cell = f"{name} {street}".strip()
            self.map_cell[key] = cell
            if continent == NO_CONTINENT and key not in towns:
                self.instances |= {cell, name}
            self.map_place[cell] = (continent, street)
            self.map_place_by_name.setdefault(name, set()).add(continent)
            self.continents.add(continent)
            self.streets.setdefault(street, set()).add(continent)
        self.confirmed = {c for c in self.continents if self._named(c, self.confirmed_text)}
        self.not_at_launch = {c for c in self.continents if self._named(c, self.not_at_launch_text)}
        self.confirmed -= self.not_at_launch
        # a map page with no continent (the Free Market, the KPQ stages, the 2nd-job test maps): the release guide
        # confirms KPQ and 2nd job, and these are on no continent the KB closes, so they're in the game
        if NO_CONTINENT in self.continents:
            self.confirmed.add(NO_CONTINENT)
        self._close_areas(kb)
        # monsters the guide names one by one ("Confirmed bosses: Mushmom, Zombie Mushmom, ...", new monsters)
        names = sorted({e["name"] for e in kb.entities.values() if e.get("category") == "monster"}, key=len, reverse=True)
        text, self.named_monsters = self.confirmed_text, set()
        for n in names:                  # longest first: "King Slime" named doesn't name "Slime" too
            if self._named(n, text):
                self.named_monsters.add(n)
                text = re.sub(rf"(?<![\w-]){re.escape(n)}(?![\w-])", " ", text)
        third_out = bool(re.search(r"3rd job", self.not_at_launch_text, re.I))
        third_in = bool(re.search(r"3rd job", self.confirmed_text, re.I))
        # 3rd job opens only once the guide confirms it; until then (out, or not named at all) it stays shut
        self.job_tier = 3 if third_in and not third_out else 2

    def _close_areas(self, kb) -> None:
        """An area inside an open continent that the guide's "Not at launch" names ("Forgotten Hollow is closed during
        Founder's Access"): its maps are the ones the KB's guide to that area lists, and every map on their streets
        ("Shallow Passage", "Deep Passage") is closed with it. It counted as open since Victoria Island is (its
        monsters, quests and the Arcane Station showed). No list here: the guide names the area, its own guide
        page names the maps, and when the release guide stops calling it closed, it opens with the next KB."""
        self.closed_areas: list[str] = []
        streets: set[str] = set()
        guide_maps: set[str] = set()                # every map an area's guide names
        for name in sorted(self.map_place_by_name, key=len, reverse=True):
            if not (self.map_place_by_name[name] & self.confirmed) or not self._named(name, self.not_at_launch_text):
                continue
            mentions = re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])[^.]*\b(closed|not open|not included|"
                                 r"later date|not part)\b", self.not_at_launch_text, re.I)
            if not mentions:
                continue
            self.closed_areas.append(name)
            listed = {name}
            for key, e in kb.entities.items():
                if e.get("category") == "guide" and name.lower() in e.get("name", "").lower():
                    page = kb.page(key)
                    listed |= {m for m in self.map_place_by_name if len(m) > 4 and self._named(m, page)}
            guide_maps |= listed
            # a street is the area's when most of its maps are in the area's guide: Shallow / Deep Passage, not
            # Victoria Road (the guide names Ellinia and Henesys too, and they are no part of it)
            per_street: dict[str, list[bool]] = {}
            for cell, (cont, street) in self.map_place.items():
                if cont in self.confirmed:
                    per_street.setdefault(street, []).append(cell[:-len(street)].strip() in listed)
            for street, hits in per_street.items():
                if sum(hits) * 2 > len(hits):
                    streets.add(street)
        if not self.closed_areas:
            return
        closed = " · ".join(self.closed_areas)

        def shut_map(cell: str, cont: str, street: str, whole_street: bool) -> None:
            shut = f"{cont} · {closed}"            # a continent of its own, never in self.confirmed
            self.map_place[cell] = (shut, street)
            name = cell[:-len(street)].strip() if street else cell
            self.map_place_by_name[name] = (self.map_place_by_name.get(name, set()) - {cont}) | {shut}
            if whole_street:
                self.streets[street] = (self.streets.get(street, set()) - {cont}) | {shut}
            else:
                self.streets.setdefault(street, set()).add(shut)
            self.continents.add(shut)

        for cell, (cont, street) in list(self.map_place.items()):
            name = cell[:-len(street)].strip() if street else cell
            if street in streets:
                shut_map(cell, cont, street, True)
            elif any(self._named(a, name) for a in self.closed_areas):
                shut_map(cell, cont, street, False)     # "Forgotten Hollow Instance 080003000" (Victoria Island)
        # the area's maps on a street it shares with open maps: "Someone Else's Grave" (Rotten Mushmom), "The Valley
        # of Death" sit on Victoria's "Hidden Street" beside Pig Park. One the area's guide names whose every
        # connected map is closed is closed too; the guide's open entrances (The Tree Tunnel At the Forest Up North,
        # Sleepy Dungeon V) lead to open maps and stay open. The portals are routes.json's (a page's "Connected Maps"
        # line leaves out the street of a same-street map, so it can't be read map by map).
        portals = self._portals()
        changed = True
        while changed:
            changed = False
            for key, cell in self.map_cell.items():
                cont, street = self.map_place[cell]
                name = cell[:-len(street)].strip() if street else cell
                to = [self.map_cell.get(t) for t in portals.get(key, ())]
                if cont not in self.confirmed or name not in guide_maps or not to or None in to:
                    continue
                if not any(self.map_open(t) for t in to):
                    shut_map(cell, cont, street, False)
                    changed = True

    def _portals(self) -> dict[str, set[str]]:
        """Map key -> the map keys its portals lead to, from the KB's routes.json ({} without one)."""
        try:
            data = json.loads((Path(self.kb.root) / "routes.json").read_text(encoding="utf-8"))
            out: dict[str, set[str]] = {}
            for m in data.get("maps") or []:
                key = f"map/{m.get('id')}"
                out[key] = {f"map/{p.get('to')}" for p in m.get("portals") or [] if p.get("to")} - {key}
            return out
        except (OSError, ValueError, AttributeError, TypeError):
            return {}

    @staticmethod
    def _named(name: str, text: str) -> bool:
        return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None

    # ------------------------------------------------------------ maps

    def continent_of(self, map_cell: str) -> str | None:
        """The continent of a map as monster pages and NPC pages name it ("Snail Hunting Ground I Maple Road"),
        or a bare map name; None when the KB doesn't say."""
        hit = self.map_place.get(map_cell.strip())
        if hit:
            return hit[0]
        found = self.map_place_by_name.get(map_cell.strip())
        if found and len(found) == 1:
            return next(iter(found))
        return None

    def map_open(self, map_cell: str) -> bool:
        """A map the KB confirms is in the game. Unknown maps (no location in the KB) are not shown."""
        return not self.known or self.continent_of(map_cell) in self.confirmed

    def instance_map(self, map_cell: str) -> bool:
        """A map an NPC sends a party or a job candidate into (no continent, no town): in the game, no hunting
        ground."""
        return self.known and map_cell.strip() in self.instances

    def place_open(self, place: str) -> bool:
        """A town or street name ("El Nath", "Henesys", "Victoria Road"): open unless every continent it is on
        is unconfirmed. A name the KB has no map for at all is judged by the continents named in it."""
        if not self.known:
            return True
        conts = self.streets.get(place) or self.map_place_by_name.get(place)
        if conts:
            return bool(conts & self.confirmed)
        for c in self.continents - self.confirmed:
            if self._named(c, place):
                return False
        for street, cs in self.streets.items():
            if not cs & self.confirmed and self._named(street, place):
                return False
        return True

    # ------------------------------------------------------------ monsters, NPCs, quests

    def monster_open(self, name: str, map_cells: list[str]) -> bool:
        if not self.known:
            return True
        return name in self.named_monsters or any(self.map_open(m) for m in map_cells)

    def _once(self, what: str, key: str, work):
        k = (what, key)
        if k not in self._memo:
            self._memo[k] = work()
        return self._memo[k]

    def monster_key_open(self, key: str) -> bool:
        e = self.kb.get(key)
        if not e:
            return False
        return self._once("monster", key, lambda: self.monster_open(e.get("name", ""), self.kb.all_maps(key)))

    def npc_places(self, key: str) -> list[str]:
        """The maps an NPC page says it stands on ("Location" has one; "Locations (4)" lists every crafting station's
        town: Anvil, Doofus), as "<map> <street>" cells; [] for a dynamically placed one ("Locations (0)")."""
        def work():
            page = self.kb.page(key)
            m = _NPC_LOCATION.search(page)
            places: list[str] = []
            for ln in page[m.end():].splitlines() if m else ():
                ln = ln.strip()
                if ln == "Find path here":
                    continue
                if self.continent_of(ln) is None:
                    break           # past the places: "About", "What <NPC> Says", ...
                places.append(ln)
            return places
        return self._once("npc places", key, work)

    def npc_continent(self, key: str) -> str | None:
        """The continent of the first open place an NPC stands on, else of its first place."""
        def work():
            conts = [self.continent_of(p) for p in self.npc_places(key)]
            return next((c for c in conts if c in self.confirmed), conts[0] if conts else None)
        return self._once("npc", key, work)

    def npc_open(self, key: str) -> bool:
        return not self.known or self.npc_continent(key) in self.confirmed

    def quest_open(self, key: str) -> bool:
        if not self.known:
            return True
        return self._once("quest", key, lambda: self._quest_open(key))

    def _quest_open(self, key: str) -> bool:
        e = self.kb.get(key) or {}
        page = self.kb.page(key)
        # "Ended", or after the quest's kind: "Daily Ended", "Self-Starting Ended" (the ended event quests)
        if re.search(r"^(?:[A-Z][\w-]* )?Ended\s*$", page, re.M):
            return False
        props = e.get("props") or {}
        # the quest's own area first: the El Nath storyline handed out by Victoria's job instructors is El Nath's
        if not self.place_open(str(props.get("Area") or "")):
            return False
        npc = self.kb.npc_key(str(props.get("NPC") or "")) if hasattr(self.kb, "npc_key") else None
        where = self.npc_continent(npc) if npc else None
        return where in self.confirmed if where else True

    def entity_open(self, key: str) -> bool:
        """Any KB entry the AI may be handed: a monster, map, NPC or quest the KB doesn't confirm is not in the
        game; items, skills, guides and the rest are not tied to a place."""
        cat = key.partition("/")[0]
        if not self.known or cat not in ("monster", "map", "npc", "quest"):
            return True
        if cat == "monster":
            return self.monster_key_open(key)
        if cat == "npc":
            return self.npc_open(key)
        if cat == "quest":
            return self.quest_open(key)
        return self._once("map", key, lambda: self._map_key_open(key))

    def _map_key_open(self, key: str) -> bool:
        cell = self.map_cell.get(key)
        return cell is not None and self.map_place.get(cell, ("",))[0] in self.confirmed

    # ------------------------------------------------------------ items

    def item_open(self, key: str) -> bool:
        """An item the KB confirms a player can hold now: at least one source of it is in the game. Sources, all
        read from the KB: a monster that drops it and is open (its monster page, or the item page's community or MSEA
        drop list),
        a shop in a released place ("Where to buy"), an open quest that gives it ("Quest Reward") or asks for it
        ("Quests"), a crafting recipe ("Craftable"), or the Cash Shop selling it. An item with none (Return Scroll
        to Orbis, Dark Jr. Yeti Skin) is not in the game."""
        if not self.known:
            return True
        return self._once("item", key, lambda: self._item_open(key))

    @property
    def _names_by_category(self) -> dict[str, dict[str, list[str]]]:
        found = self._memo.get(("names", ""))
        if found is None:
            found = {"monster": {}, "quest": {}}
            for k, e in self.kb.entities.items():
                if e.get("category") in found and e.get("name"):
                    found[e["category"]].setdefault(e["name"].strip().lower(), []).append(k)
            self._memo[("names", "")] = found
        return found

    def _quest_keys_in(self, line: str) -> list[str]:
        """The quests an item page's line names: "Jane's Final Challenge ( 25 %)", "Taking Out the Alligators 1
        Warrior" (the quest name, then a job or the quest's NPC)."""
        low = _PERCENT.sub("", line).strip().lower()
        quests = self._names_by_category["quest"]
        words = low.split(" ")
        for n in range(len(words), 0, -1):           # the longest name the line starts with
            hit = quests.get(" ".join(words[:n]))
            if hit:
                return hit
        return []

    def _item_open(self, key: str) -> bool:
        e = self.kb.get(key)
        if not e or e.get("category") != "item":
            return False
        droppers = getattr(self.kb, "droppers", None)
        if isinstance(droppers, dict) and droppers.get(key):     # only open monsters are listed there
            return True
        lines = [s.strip() for s in self.kb.page(key).splitlines()]
        monsters = self._names_by_category["monster"]
        for head, body in _item_sections(lines):
            if head in ("Dropped By", "MSEA Reference Drops"):
                if any(self.monster_key_open(m) for s in body for m in monsters.get(s.lower(), ())):
                    return True
            elif head == "Where to buy":
                for s in body:
                    m = _SHOP_PLACE.match(s)
                    if m and self.place_open(m.group(1)) and self.place_open(m.group(3)):
                        return True
            elif head in ("Quest Reward", "Quests"):
                if any(self.quest_open(q) for s in body for q in self._quest_keys_in(s)):
                    return True
            elif head == "Craftable":
                return True
            elif head == "Cash Shop":
                if any(s.endswith("· Available") for s in body[:3]):
                    return True
        return False

    # ------------------------------------------------------------ jobs

    def job_tier_open(self, tier: int) -> bool:
        return tier <= self.job_tier

    # ------------------------------------------------------------ for the AI

    def scope_note(self) -> str:
        """The game's scope as the KB states it, for the AI's prompt."""
        if not self.known:
            return ""      # a KB with no release guide filters nothing: "Maple Island is not in the game" contradicted it
        real = lambda cs: sorted(c for c in cs if not c.startswith(NO_CONTINENT))    # noqa: E731
        shut = real(self.continents - self.confirmed)
        # "Event": the release guide lists the GM events (Coconut Harvest, Physical Fitness...) by date; their maps
        # aren't in the tables, but "not in the game" told the AI the Founder's Access events weren't
        events = [c for c in shut if re.search(rf"^.*\b{re.escape(c)}s\s*$", self.guide, re.M | re.I)]
        shut = [c for c in shut if c not in events]
        parts = [f"Released and confirmed by the knowledge base: {', '.join(real(self.confirmed)) or 'nothing'}."]
        if shut:
            parts.append(f"NOT in the game (the KB does not confirm them): {', '.join(shut)} — never send the player "
                         "there, never suggest their maps, monsters, NPCs or quests, and if asked say they are not in "
                         "the game yet.")
        if events:
            parts.append(f"{', '.join(events)} maps open only during GM events (the release guide lists the dates).")
        # two map pages with a reversed Location line made "Hidden Street" a closed continent, while Pig Park is an
        # open Victoria Island map on it (audit AI-5, KB-22). The examples are the KB's own open maps there (the note
        # named "Monkey Forest", which the KB writes "Monkey Forest I", review CORE-10)
        if len(self.streets.get("Hidden Street", ())) > 1:
            on = sorted((cont, cell[:-len(" Hidden Street")]) for cell, (cont, street) in self.map_place.items()
                        if street == "Hidden Street" and cont in self.confirmed and not cont.startswith(NO_CONTINENT))
            same = [n for c, n in on if on and c == on[0][0]][:2]
            eg = f" (e.g. {same[0]} and {same[1]} on {on[0][0]})" if len(same) == 2 else ""
            parts.append("\"Hidden Street\" is a street name used on several continents, not a place of its own: the "
                         f"maps the tables list on it are in the game{eg}.")
        # said both ways: with only "3rd job is not in the game" the AI answered that the 2nd job isn't out either
        # (to an Assassin, live)
        tiers = ["1st", "2nd", "3rd", "4th"][:max(1, self.job_tier)]
        parts.append(f"Job advancements in the game: {', '.join(tiers)} (players do them now).")
        if self.job_tier < 3:
            parts.append("3rd job advancement is not in the game; never present 3rd-job jobs or skills as available.")
        parts.append("Anything the KB does not confirm is not in the game: say so instead of guessing.")
        return " ".join(parts)


def of(kb) -> Availability:
    """The availability for this knowledge base, worked out once per KB object (a KB update loads a new one)."""
    a = getattr(kb, "_availability", None)
    if a is None or a.kb is not kb:
        a = Availability(kb)
        try:
            kb._availability = a
        except AttributeError:     # a KB stand-in that takes no attributes: worked out each time
            pass
    return a
