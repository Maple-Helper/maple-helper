"""Farming: hunting a monster for what it drops rather than for its EXP.

Everything here is the KB's: who drops an item (kb.droppers, on the community's Classic list or the MSEA reference
list), what an NPC pays for it (the item page's sell price, with the build it is labelled with), where the monster
lives and the mesos players reported. The KB has no drop rates, so nothing here guesses one: the only rate shown is
what the player's own farm sessions counted (the loot a session's inventory reads gained, over its kills, grind.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import availability, combat, market, sources

# a monster this many levels below the player is still a farming spot: one hit, no risk (farming isn't about EXP,
# so the band reaches lower than the training spots' combat.SPOT_BELOW)
FARM_BELOW = 20
MAX_LOOT_KEPT = 12          # the items a finished session's record keeps


@dataclass
class Value:
    price: int                  # what an NPC pays for one
    source: str                 # the build the item page's values carry ("COT2"), else MeowDB's own


def value(kb, key: str | None) -> Value | None:
    """What an NPC pays for one of an item (the KB's item page), or None when the page has no sell price."""
    if not key or not kb.get(key):
        return None
    sell = market.npc_prices(kb, key).sell_back
    return Value(sell, sources.source_of(kb, key)) if sell else None


def item_key(kb, name: str) -> str | None:
    """The KB item a read's name means: exact, else the shortest item name containing it."""
    q = (name or "").strip().lower()
    if not q:
        return None
    key = kb._item_by_name.get(q)
    if not key:
        part = sorted((n for n in kb._item_by_name if q in n), key=lambda n: (len(n), n))
        key = kb._item_by_name[part[0]] if part else None
    return key


def farmable(kb) -> list[str]:
    """Every item some monster in the game drops (kb.droppers keeps only those), once per name, by name."""
    seen: dict[str, str] = {}
    for k in kb.droppers:
        e = kb.get(k)
        if e and e["name"].strip():
            seen.setdefault(e["name"].strip(), k)
    return [seen[n] for n in sorted(seen, key=str.lower)]


def fit(level: int | None, mob_level: int) -> str:
    """How a monster sits against the player's level: "easy" (not above it), "range" (the training spots' reach
    above it) or "hard"."""
    if not level:
        return "range"
    if mob_level <= level:
        return "easy"
    return "range" if mob_level <= level + combat.SPOT_ABOVE else "hard"


def _level(kb, key: str) -> int:
    lv = ((kb.get(key) or {}).get("props") or {}).get("Level")
    return int(lv) if isinstance(lv, (int, float)) else 0


def _home(kb, key: str) -> str:
    """Where a monster lives: its busiest map people hunt on, else any map the KB confirms is in the game."""
    m = combat.monster(kb, key)
    if m and m.maps:
        return m.maps[0][0]
    open_ = availability.of(kb)
    return next((mp for mp in kb.all_maps(key) if open_.map_open(mp)), "")


@dataclass
class Dropper:
    key: str
    name: str
    level: int
    map: str                    # its busiest map ("Ant Tunnel IV Dungeon", kb.map_label splits it), or ""
    source: str                 # the list the drop is on: sources.COMMUNITY or sources.MSEA
    vote: dict | None           # the players' votes on a community drop (kb.community_vote)
    fit: str                    # fit(): "easy" | "range" | "hard"
    boss: bool                  # a boss or an hourly spawn: one try now and then, not a farm
    mesos: tuple | None = None  # the players' mesos reports (kb.community_mesos)


def droppers(kb, item: str, level: int | None) -> list[Dropper]:
    """Every monster in the game that drops an item: the ones players saw drop it first, then the ones the player
    can farm (not too strong, not a boss), weakest first."""
    out = []
    for m in kb.droppers.get(item, []):
        e = kb.get(m) or {}
        lv = _level(kb, m)
        mob = combat.monster(kb, m)
        out.append(Dropper(m, e.get("name", m), lv, _home(kb, m), kb.drop_source(m, item) or sources.MSEA,
                           kb.community_vote(m, item), fit(level, lv), bool(mob and mob.boss),
                           kb.community_mesos(m)))
    out.sort(key=lambda d: (d.source != sources.COMMUNITY, d.fit == "hard", d.boss, d.level, d.name))
    return out


@dataclass
class Drop:
    key: str
    name: str
    value: Value | None
    source: str                 # the list it is on
    need: tuple[str, str] | None = None     # what the player needs it for: ("quest", name) / ("recipe", name) / ("wish", "")


@dataclass
class Target:
    """A monster worth farming at the player's level, for what its drops sell for."""
    key: str
    name: str
    level: int
    map: str
    fit: str
    drops: list[Drop] = field(default_factory=list)     # the best-paying first
    mesos: tuple | None = None

    @property
    def best(self) -> int:
        return max((d.value.price for d in self.drops if d.value), default=0)

    @property
    def needed(self) -> int:
        return sum(1 for d in self.drops if d.need)


def needs(kb, level: int, base_class: str = "", job: str = "", done: list[str] | None = None,
          crafts: dict | None = None, wished: list[str] | None = None) -> dict[str, tuple[str, str]]:
    """What the player wants an item for, by item name (lower case): a quest they can do now, a recipe of a
    profession they work, their wishlist. Farming isn't only for an NPC's price (the owner)."""
    from . import crafting, quests
    out: dict[str, tuple[str, str]] = {}
    for k in wished or []:
        e = kb.get(k)
        if e:
            out.setdefault(e["name"].lower(), ("wish", ""))
    for q in quests.for_level(kb, level, base_class, job, done, crafts=crafts or None)["now"]:
        for line in q.needs:
            m = quests._ITEM.fullmatch(line.strip())
            if m and not m.group(1).startswith(("Defeat ", "Collect ")):
                out.setdefault(m.group(1).strip().lower(), ("quest", q.name))
    for prof, lv in (crafts or {}).items():
        try:
            recipes = crafting.up_to(kb, prof, int(lv))
        except (KeyError, ValueError, TypeError):
            continue
        for r in recipes:
            for _, name in r.ingredients:
                out.setdefault(name.lower(), ("recipe", r.name))
    return out


def _target(kb, m, level: int, wanted: dict, per_monster: int) -> Target | None:
    """One monster as a farming target: its drops that are worth something or needed, needed first."""
    drops = []
    for k in kb.monster_drops(m.key):
        e = kb.get(k)
        if e:
            drops.append(Drop(k, e["name"], value(kb, k), kb.drop_source(m.key, k) or sources.MSEA,
                              wanted.get(e["name"].lower())))
    drops = [d for d in drops if d.value or d.need]
    if not drops or not m.maps:
        return None
    drops.sort(key=lambda d: (not d.need, -(d.value.price if d.value else 0), d.source != sources.COMMUNITY, d.name))
    return Target(m.key, m.name, m.level, m.maps[0][0], fit(level, m.level), drops[:per_monster],
                  kb.community_mesos(m.key))


def target_for(kb, name: str, level: int, wanted: dict | None = None, per_monster: int = 6) -> Target | None:
    """A monster picked by name ("farm it" from another page): its card, whatever its level."""
    n = name.strip().lower()
    for m in combat.monsters(kb):
        if m.name.lower() == n:
            t = _target(kb, m, level, wanted or {}, per_monster)
            if t is not None:
                return t
    return None


def targets(kb, level: int, n: int = 8, below: int = FARM_BELOW, above: int = combat.SPOT_ABOVE,
            per_monster: int = 3, wanted: dict[str, tuple[str, str]] | None = None) -> list[Target]:
    """The monsters around the player's level worth farming, best first: the ones that drop what the player needs
    (wanted: needs()) first, then by what an NPC pays for their best drop. Each with its drops that count, needed
    first (both lists, each said for which it is on). The KB has no drop rates to weigh them by; a monster
    players saw drop it (community list) wins a tie."""
    wanted = wanted or {}
    out: dict[str, Target] = {}
    open_ = availability.of(kb)
    for m in combat.monsters(kb):
        if not (level - below <= m.level <= level + above) or not m.maps or m.boss or combat.special_monster(m.name):
            continue
        if not open_.monster_key_open(m.key):
            continue
        t = _target(kb, m, level, wanted, per_monster)
        if t is None:
            continue
        # the same monster twice (another version of it): the one on more maps, as the training spots keep it
        if m.name not in out or (t.needed, t.best) > (out[m.name].needed, out[m.name].best):
            out[m.name] = t
    seen = lambda t: any(d.source == sources.COMMUNITY for d in t.drops)  # noqa: E731
    return sorted(out.values(), key=lambda t: (-t.needed, -t.best, not seen(t), t.level))[:n]


# ---------------------------------------------------------------- the player's own drop counts

@dataclass
class Record:
    monster: str
    item: str
    got: int                    # how many the sessions on this monster gained
    kills: int                  # ~ the kills of those sessions (EXP / the monster's EXP)
    sessions: int

    @property
    def every(self) -> int | None:
        """~ one drop every this many kills, or None without kills or at a drop or more a kill (per_kill then):
        3 drops in 1 kill said nothing, 3 in 2 said "once every 2 kills" (audit GAM-10)."""
        return max(1, round(self.kills / self.got)) if self.got and self.kills and self.got < self.kills else None

    @property
    def per_kill(self) -> float | None:
        """~ drops a kill when there's one or more a kill (a stack of them per monster), else None."""
        return self.got / self.kills if self.kills and self.got >= self.kills else None


def records(rows: list[dict]) -> list[Record]:
    """What the finished sessions' inventory reads counted, per monster and item, across sessions: the player's own
    drop rate (estimated kills), most dropped first. Every session on a monster whose inventory was read counts
    toward its kills, the ones where an item didn't drop too. A session without a monster or kills isn't counted."""
    got: dict[tuple[str, str], int] = {}
    kills: dict[str, list[int]] = {}           # monster -> [kills, sessions]
    for r in rows:
        mob, k, loot = r.get("monster"), r.get("kills"), r.get("loot")
        if not mob or not isinstance(k, int) or isinstance(k, bool) or k <= 0 or not isinstance(loot, list):
            continue
        tot = kills.setdefault(mob, [0, 0])
        tot[0] += k
        tot[1] += 1
        for row in loot:
            if isinstance(row, list) and len(row) >= 2 and isinstance(row[0], str) and isinstance(row[1], int) \
                    and not isinstance(row[1], bool) and row[1] > 0:
                got[(mob, row[0])] = got.get((mob, row[0]), 0) + row[1]
    out = [Record(mob, item, n, kills[mob][0], kills[mob][1]) for (mob, item), n in got.items()]
    out.sort(key=lambda r: (r.monster, -r.got, r.item))
    return out
