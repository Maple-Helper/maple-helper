"""How to get from one map to another, step by step, from the knowledge base alone.

The graph:
- portals: routes.json (tools/scrape_meowdb.py, from the map data NiaMeowDB's own Pathfinder reads) gives every map's
  portals with the map each one leads to, and the NPCs standing on it. A portal goes one way; the way back is the
  other map's own portal, when it has one.
- taxis: the Pathfinder's taxi towns (routes.json "taxi"), each with its cab NPC on the map.
- boats: a KB guide that says so ("take the boat from Shanks at the dock to Lith Harbor"), one way.
- NPC trips: an NPC whose own words offer one ("Want to head over to Florina Beach?"), and the NPC there who takes
  you "back to where you were before".
Only maps the KB confirms are in the game (availability.py) are on it: a route never passes through Ossyria, an
event map or a map without a known continent. The KB lists no fares: a route says which steps cost mesos, never how
many.
"""
from __future__ import annotations

import heapq
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import availability

ROUTES_FILE = "routes.json"
# what a step costs in the search: a short walk beats a cab ride, a long one doesn't
COST = {"portal": 1, "npc": 2, "boat": 3, "taxi": 4}
PAID = ("taxi", "boat")         # steps a player pays mesos for (the KB doesn't say how many)
_BOAT = re.compile(r"take the (?:boat|ship) from ((?:[A-Z][\w.'-]*\s?)+?)\b[^.]{0,40}? to ((?:[A-Z][\w'-]*\s?)+)")
# an NPC offering a trip ("Want to head over to Florina Beach?"), and the one who brings you back
_OFFER = re.compile(r"[Ww]ant to (?:head|go|travel) (?:over |off )?to ((?:[A-Z][\w'-]*\s?)+)")
_BACK = re.compile(r"\bback to where you were\b", re.I)
_SAYS_END = re.compile(r"^(Similar NPCs|What They Do|Quests Given|Related Quests|About|Shop|Sells)\b", re.M)


@dataclass(frozen=True)
class Leg:
    """One step: from a map to the next, how (portal / taxi / boat / npc), and where on the first map's minimap."""
    frm: str
    to: str
    kind: str
    via: str = ""                        # the portal's or the NPC's name
    npc: str = ""                        # the NPC's KB key ("npc/112")
    spot: tuple[float, float] | None = None     # (0-1, 0-1) on the first map's minimap


@dataclass
class Route:
    start: str
    end: str
    legs: list[Leg] = field(default_factory=list)

    @property
    def maps(self) -> list[str]:
        return [self.start] + [leg.to for leg in self.legs]

    @property
    def paid(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.kind in PAID]


@dataclass
class MapInfo:
    id: str
    name: str
    street: str
    town: bool
    continent: str
    minimap: list | None
    npcs: list[dict]


class Graph:
    def __init__(self, kb, data: dict | None = None):
        self.kb = kb
        if data is None:
            path = Path(kb.root) / ROUTES_FILE
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, AttributeError):
                data = {}
        open_ = availability.of(kb)
        self.maps: dict[str, MapInfo] = {}
        raw = {}
        for m in data.get("maps") or []:
            mid = str(m.get("id") or "")
            key = f"map/{mid}"
            if not kb.get(key) or not open_.entity_open(key):
                continue      # not in the game, as the KB says: never on a route
            raw[mid] = m
            place = availability._MAP_LOCATION.search(kb.page(key))
            self.maps[mid] = MapInfo(mid, m.get("name") or kb.get(key)["name"], m.get("street") or "",
                                     bool(m.get("town")), place.group(2).strip() if place else "",
                                     m.get("minimap"), list(m.get("npcs") or []))
        self.edges: dict[str, list[Leg]] = {mid: [] for mid in self.maps}
        for mid, m in raw.items():
            seen = set()
            for p in m.get("portals") or []:
                to = str(p.get("to") or "")
                if to in self.maps and to != mid and to not in seen:
                    seen.add(to)       # the first portal there, as the Pathfinder does
                    self._add(Leg(mid, to, "portal", p.get("name") or "", spot=self._spot(mid, p)))
        self._taxis([t for t in data.get("taxi") or [] if t in self.maps])
        self._boats()
        self._npc_trips()

    # ------------------------------------------------------------ building

    def _add(self, leg: Leg) -> None:
        if not any(e.to == leg.to and e.kind == leg.kind for e in self.edges[leg.frm]):
            self.edges[leg.frm].append(leg)

    def _spot(self, mid: str, thing: dict) -> tuple[float, float] | None:
        """Where a portal or an NPC is on the map's minimap picture, as fractions of its size."""
        mm = self.maps[mid].minimap
        if not mm or not mm[0] or not mm[1]:
            return None
        x, y = (thing.get("x") or 0) + mm[2], (thing.get("y") or 0) + mm[3]
        fx, fy = x / mm[0], y / mm[1]
        return (fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None

    def _npc_on(self, mid: str, test) -> dict | None:
        return next((n for n in self.maps[mid].npcs if test(n.get("name") or "")), None)

    def _taxis(self, towns: list[str]) -> None:
        """Every taxi town to every other, by the cab standing in it (a town with no cab in the data: none)."""
        for a in towns:
            cab = self._npc_on(a, lambda n: "Cab" in n)
            if not cab:
                continue
            for b in towns:
                if b != a:
                    self._add(Leg(a, b, "taxi", cab["name"], self._npc_key(cab), self._spot(a, cab)))

    def _npc_key(self, npc: dict) -> str:
        key = f"npc/{npc.get('id')}"
        return key if self.kb.get(key) else ""

    def find_town(self, name: str) -> str | None:
        hits = [m for m in self.maps.values() if m.name == name.strip()]
        hits.sort(key=lambda m: (not m.town, -len(self.edges[m.id]), m.id))
        return hits[0].id if hits else None

    def _boats(self) -> None:
        """"take the boat from Shanks at the dock to Lith Harbor": the KB's guides, one way."""
        for key, e in self.kb.entities.items():
            if e.get("category") != "guide":
                continue
            for m in _BOAT.finditer(self.kb.page(key)):
                captain, to = m.group(1).strip(), self.find_town(m.group(2))
                if not to:
                    continue
                for mid in self.maps:
                    npc = self._npc_on(mid, lambda n, c=captain: n == c)
                    if npc and mid != to:
                        self._add(Leg(mid, to, "boat", npc["name"], self._npc_key(npc), self._spot(mid, npc)))

    def _says(self, npc_key: str) -> str:
        page = self.kb.page(npc_key)
        m = re.search(r"^What .+ Says\s*$", page, re.M)
        if not m:
            return ""
        rest = page[m.end():]
        end = _SAYS_END.search(rest)
        return rest[:end.start()] if end else rest

    def _npc_trips(self) -> None:
        """An NPC who offers to take you somewhere ("Want to head over to Florina Beach?"), and back."""
        trips: list[Leg] = []
        for mid, m in self.maps.items():
            for npc in m.npcs:
                key = self._npc_key(npc)
                said = self._says(key) if key else ""
                for o in _OFFER.finditer(said):
                    to = self.find_town(o.group(1))
                    if to and to != mid:
                        trips.append(Leg(mid, to, "npc", npc["name"], key, self._spot(mid, npc)))
        for leg in trips:
            self._add(leg)
            back = next(((n, k) for n in self.maps[leg.to].npcs if (k := self._npc_key(n)) and _BACK.search(self._says(k))),
                        None)
            if back:
                self._add(Leg(leg.to, leg.frm, "npc", back[0]["name"], back[1], self._spot(leg.to, back[0])))

    # ------------------------------------------------------------ routing

    def route(self, start: str, end: str, taxi: bool = True) -> Route | None:
        """The cheapest way (COST per step) from one map to another; taxi=False walks (no cab fare)."""
        if start not in self.maps or end not in self.maps:
            return None
        if start == end:
            return Route(start, end)
        best = {start: 0}
        came: dict[str, Leg] = {}
        queue = [(0, 0, start)]
        n = 0
        while queue:
            cost, _, here = heapq.heappop(queue)
            if here == end:
                break
            if cost > best.get(here, cost):
                continue
            for leg in self.edges[here]:
                if leg.kind == "taxi" and not taxi:
                    continue
                c = cost + COST[leg.kind]
                if c < best.get(leg.to, c + 1):
                    best[leg.to], came[leg.to] = c, leg
                    n += 1
                    heapq.heappush(queue, (c, n, leg.to))
        if end not in came:
            return None
        legs, at = [], end
        while at != start:
            legs.append(came[at])
            at = came[at].frm
        return Route(start, end, legs[::-1])

    def nearest_town(self, end: str, taxi_towns_only: bool = True) -> Route | None:
        """The shortest way to a map from the town closest to it (for a player whose map isn't known)."""
        towns = [m for m, legs in self.edges.items() if any(e.kind == "taxi" for e in legs)] if taxi_towns_only \
            else [m.id for m in self.maps.values() if m.town]
        found = [r for t in towns if (r := self.route(t, end, taxi=False))]
        return min(found, key=lambda r: (len(r.legs), r.start)) if found else None

    # ------------------------------------------------------------ names

    def name(self, mid: str) -> str:
        m = self.maps.get(mid)
        return m.name if m else mid

    def duplicates(self, mid: str) -> bool:
        n = self.name(mid)
        return sum(1 for m in self.maps.values() if m.name == n) > 1

    def find(self, text: str) -> str | None:
        """A map by its name as a player or the AI writes it ("Henesys", "henesys", "Ant Tunnel I"), or the map an
        NPC named so stands on."""
        t = (text or "").strip()
        if not t:
            return None
        if t.isdigit() and t.zfill(9) in self.maps:
            return t.zfill(9)
        hit = self.exact(t)
        if hit:
            return hit
        for key in self.kb.find_mentions(text, max_results=3):
            mid = self.of_key(key)
            if mid:
                return mid
        return None

    def exact(self, name: str) -> str | None:
        """The map with exactly this name (any case); of two, the town, then the one with more ways out."""
        t = re.sub(r"\s+", " ", name.strip()).lower()
        hits = [m for m in self.maps.values() if m.name.lower() == t]
        hits.sort(key=lambda m: (not m.town, -len(self.edges[m.id]), m.id))
        return hits[0].id if hits else None

    def not_in_game(self, name: str) -> bool:
        """A map the KB has by this name, none of them in the game (Orbis, El Nath)."""
        t = re.sub(r"\s+", " ", name.strip()).lower()
        return not self.exact(name) and any(e.get("category") == "map" and e.get("name", "").lower() == t
                                            for e in self.kb.entities.values())

    def of_key(self, key: str) -> str | None:
        """The map a KB entity is: a map's own key, or the map an NPC stands on."""
        cat, _, slug = key.partition("/")
        if cat == "map":
            return slug if slug in self.maps else None
        if cat == "npc":
            return next((mid for mid, m in self.maps.items() if any(str(n.get("id")) == slug for n in m.npcs)), None)
        return None

    def minimap(self, mid: str) -> Path | None:
        return self.kb.image_path(f"map/{mid}")


def side(spot: tuple[float, float] | None) -> str:
    """'left', 'right' or 'middle' of the map, for a step's words."""
    if not spot:
        return ""
    return "left" if spot[0] < 0.3 else "right" if spot[0] > 0.7 else "middle"


def of(kb) -> Graph:
    """The route graph of this knowledge base, built once per KB object (a KB update loads a new one)."""
    g = getattr(kb, "_routes", None)
    if g is None or g.kb is not kb:
        g = Graph(kb)
        try:
            kb._routes = g
        except AttributeError:
            pass
    return g


# ---------------------------------------------------------------- for the AI

# "how do I get to Sleepywood", "איך מגיעים לסליפיווד", "what's the way from Ellinia to Perion"
ROUTE_WORDS = re.compile(
    r"\bhow\s+(?:do|can|should|would|could)\s+(?:i|you|we|one)\s+(?:get|go|reach|travel|walk|head)\b|"
    r"\bhow\s+(?:to|i)\s+(?:get|go|reach|travel)\b|\b(?:way|route|path|directions?)\s+(?:to|from)\b|"
    r"\bhow\s+far\b|\bget\s+(?:to|from|there|here)\b|"
    r"איך\s+(?:\S+\s+){0,2}?(?:מגיע|מגיעה|מגיעים|להגיע|הולך|הולכת|הולכים|ללכת|עובר|עוברת|עוברים|לעבור|נוסע|נוסעים|לנסוע)|"
    r"(?:ה)?(?:דרך|מסלול)\s+(?:ל|אל|מ)|כמה\s+רחוק",
    re.I)
# a level is no place: "how do I get to level 30?", "איך מגיעים ללבל 30 מהר?"
_LEVEL_GOAL = re.compile(r"\b(?:get|reach|go)\s+(?:to\s+)?(?:level|lvl|lv)\b|(?:להגיע|מגיעים|מגיע)\s+(?:ל|ל-)?(?:לבל|רמה)", re.I)
_FROM_WORDS = {"from", "מ", "מה", "מן"}


def is_route_question(text: str) -> bool:
    return bool(ROUTE_WORDS.search(text or "")) and not _LEVEL_GOAL.search(text or "")


def endpoints(kb, graph: Graph, question: str, here: str = "") -> tuple[str | None, str | None]:
    """(from, to) map ids a route question names: "from X" is the start, the other place named the destination;
    with no start named, the player's map (`here`, as the profile has it)."""
    from .kb import _norm
    words = _norm(question).split()          # the words mention_spans counts
    start = end = None
    for key, w0, w1 in sorted(kb.mention_spans(question, max_results=6), key=lambda s: s[1]):
        mid = graph.of_key(key)
        if not mid and key.startswith("map/"):
            # a name inside a longer one that isn't in the game: "the way from Lith Harbor to Ellinia" names the
            # Ossyria boat ride "To Ellinia", and means Ellinia
            mid = next((m for i in range(w0 + 1, w1) if (m := graph.exact(" ".join(words[i:w1])))), None)
        if not mid:
            continue
        if _after_from(kb, key, words, w0) and start is None:
            start = mid
        elif end is None and mid != start:
            end = mid
        elif start is None and mid != end:
            start = mid
    if start is None and here:
        start = graph.find(here)
    return start, end


def _after_from(kb, key: str, words: list[str], w0: int) -> bool:
    """The name is the trip's start: "from Henesys", "מ-Henesys", or a Hebrew name with "מ" glued on ("מהניסיס")."""
    if 0 < w0 <= len(words) and words[w0 - 1] in _FROM_WORDS:
        return True
    first = words[w0] if w0 < len(words) else ""
    if not first.startswith("מ") or not re.match(r"[א-ת]", first):
        return False
    from .kb import _norm
    # the place's own Hebrew names: one starting with "מ" itself means no prefix is glued on
    own = [a for a, k in kb.aliases.items() if k == key] + [_norm((kb.get(key) or {}).get("name", ""))]
    return not any(a.startswith("מ") for a in own)


def describe(graph: Graph, r: Route) -> list[str]:
    """A route's steps in plain English, for the AI's context: one line a step, names exactly as in the game."""
    out = []
    for i, leg in enumerate(r.legs, 1):
        at, to = graph.name(leg.frm), graph.name(leg.to)
        where = side(leg.spot)
        if leg.kind == "portal":
            bit = f"take the portal{' on the ' + where + ' side' if where and where != 'middle' else ''} to {to}"
        elif leg.kind == "taxi":
            bit = f"talk to {leg.via} and take the taxi to {to} (costs mesos)"
        elif leg.kind == "boat":
            bit = f"talk to {leg.via} and take the boat to {to} (costs mesos)"
        else:
            bit = f"talk to {leg.via}, who takes you to {to}"
        out.append(f"{i}. In {at}: {bit}.")
    return out


def ai_context(kb, question: str, character=None, tagged=()) -> str:
    """The route a "how do I get to X" question asks for, worked out from the KB, for the prompt; "" otherwise.
    tagged: the cards the player tagged ("how do I get here?" on a map's card)."""
    if not is_route_question(question):
        return ""
    graph = of(kb)
    if not graph.maps:
        return ""
    start, end = endpoints(kb, graph, question, getattr(character, "map", "") or "")
    if not end:
        end = next((mid for k in tagged or () if (mid := graph.of_key(k)) and mid != start), None)
    if not end:
        return ""
    head = ("Route (worked out by the app from the knowledge base's map connections, portals, taxis and boats; only "
            "maps that are in the game; fares are not in the KB):")
    if not start:
        r = graph.nearest_town(end)
        if not r:
            return f"{head}\nThe KB has no known way to {graph.name(end)} from any town (an NPC, quest or event map)."
        return "\n".join([f"{head}\nThe player's current map is unknown. From the nearest taxi town, "
                          f"{graph.name(r.start)}, to {graph.name(end)} ({len(r.legs)} steps, walking):",
                          *describe(graph, r)])
    r = graph.route(start, end)
    if r is None:
        return (f"{head}\nThe KB has no known way from {graph.name(start)} to {graph.name(end)} (it may be reached "
                "only through an NPC, a quest or an event): say so, don't invent one.")
    if not r.legs:
        return f"{head}\nThe player is already in {graph.name(end)}."
    lines = [f"{head}\nFrom {graph.name(start)} to {graph.name(end)}, {len(r.legs)} steps:", *describe(graph, r)]
    if any(leg.kind == "taxi" for leg in r.legs):
        walk = graph.route(start, end, taxi=False)
        if walk:
            lines.append(f"Walking instead (free): {len(walk.legs)} steps: "
                         + " → ".join(graph.name(m) for m in walk.maps))
    return "\n".join(lines)
