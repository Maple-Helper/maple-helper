"""The grind tracker's helpers beyond the counts (no Qt): the next level and when it comes at this rate, potions
running low and the nearest NPC shop that sells them, the monsters killed (named by their EXP), the last session on
the same map to compare with, and whether to sell an ETC to an NPC, to players, or keep it. Every figure is the
KB's or the player's own logs and reads; nothing is guessed. No Qt of its own (a route comes from ui/mapview)."""
from __future__ import annotations

from dataclasses import dataclass

LOW_POTIONS = 20            # a potion used this session with this many left or fewer is running low
KILL_LEVELS = 15            # a session with no map read: its level's monsters this close, by a unique EXP


# ---------------------------------------------------------------- the next level

@dataclass
class LevelGoal:
    level: int
    left: int                   # EXP still to go to the next level
    seconds: float | None       # at the session's EXP per hour; None: no rate yet


def level_goal(kb, level: int | None, exp: int | None, exp_h: int | None) -> LevelGoal | None:
    """The EXP to the next level (the KB's EXP table less the bar's points) and how long it takes at exp_h."""
    from . import plan
    if not level or exp is None:
        return None
    need = plan.exp_table(kb).get(level)
    if not need or exp > need:
        return None
    left = need - exp
    return LevelGoal(level, left, left / exp_h * 3600 if exp_h and exp_h > 0 else None)


# ---------------------------------------------------------------- potions running low

@dataclass
class LowPotion:
    key: str
    name: str
    left: int
    shop: tuple[str, str, int] | None       # (NPC, town, price) of the nearest open shop that sells it
    npc_key: str | None                     # that NPC's KB key (its "How to get there")
    hops: int | None                        # maps on the way there from the player's map (None: no route known)


def _npc_key(kb, name: str) -> str | None:
    """The NPC a shop line names: "Arturo Grocer" is Arturo (the page adds the NPC's role after the name)."""
    words = (name or "").split()
    for n in range(len(words), 0, -1):
        key = kb.npc_key(" ".join(words[:n]))
        if key:
            return key
    return None


def nearest_shop(kb, key: str, here_map: str | None) -> tuple[tuple[str, str, int] | None, str | None, int | None]:
    """The open NPC shop selling the item that is the fewest maps away from here_map (the cheapest when the way
    isn't known): ((NPC name, town, price), NPC key, maps on the way)."""
    from . import market
    from .combat import released
    shops = [s for s in market.npc_prices(kb, key).shops if released(kb, s[1])]
    if not shops:
        return None, None, None
    best = None
    for shop in shops:
        npc = _npc_key(kb, shop[0])
        hops = None
        if npc and here_map:
            try:
                from .ui.mapview import way_from_here
                way = way_from_here(kb, npc, here_map)
                hops = len(way.route.legs) if way is not None else None
            except Exception:  # noqa: BLE001 - no route: the shop still counts, by its price
                hops = None
        rank = (hops if hops is not None else 10**6, shop[2])
        if best is None or rank < best[0]:
            best = (rank, shop, npc, hops)
    (npc_name, where, price), npc, hops = best[1], best[2], best[3]
    # as the player looks for it: the NPC's own name ("Dr. Faymus", not "Dr. Faymus Grocer") and the town alone
    town = where.rpartition("·")[2].strip() or where
    name = (kb.get(npc) or {}).get("name") or npc_name if npc else npc_name
    return (name, town, price), npc, hops


def low_potions(kb, counts: dict[str, int], used: set[str], here_map: str | None) -> list[LowPotion]:
    """Potions the session used (so the player is drinking them) with LOW_POTIONS or fewer left, fewest first."""
    out = []
    for key, n in sorted(counts.items(), key=lambda kv: kv[1]):
        if key not in used or n > LOW_POTIONS:
            continue
        e = kb.get(key)
        shop, npc, hops = nearest_shop(kb, key, here_map)
        out.append(LowPotion(key, e["name"] if e else key, n, shop, npc, hops))
    return out


# ---------------------------------------------------------------- selling: to an NPC, to players

def market_price(kb, key: str) -> tuple[int | None, bool]:
    """(MesoWatch's usual sale price, it is a guess on few sales); (None, False) with none on hand."""
    from . import mesowatch
    try:
        _, s = mesowatch.for_item(kb, key, refresh=False)
    except Exception:  # noqa: BLE001 - no market on hand: no players' price
        return None, False
    if s is None or not s.price:
        return None, False
    return int(s.price), bool(s.low_data or s.scrolled_only)


def best_sale(kb, key: str, npc_price: int | None) -> tuple[int | None, str]:
    """(the price each, where it sells for that: "npc" | "fm"): whichever pays more, an NPC or the players
    (MesoWatch's usual sale price, a real one: never a guess on few sales). Checked as each item is picked up, and
    that price is the one every total counts (the owner: "whoever pays more is shown"). (None, "") with neither."""
    mp, low = market_price(kb, key) if key else (None, False)
    if mp and not low and mp > (npc_price or 0):
        return mp, "fm"
    if npc_price:
        return npc_price, "npc"
    return None, ""


@dataclass
class LootValue:
    """The loot picked up, at the best price each: how many and what it brings, ETC and the other items apart."""
    etc_n: int = 0
    etc_v: int = 0
    items_n: int = 0
    items_v: int = 0

    @property
    def value(self) -> int:
        return self.etc_v + self.items_v


def loot_value(kb, items) -> LootValue:
    """items: summary rows (name, count, key, NPC price each)."""
    from . import lootlog
    out = LootValue()
    for _name, n, key, price in items:
        each, _ = best_sale(kb, key, price)
        if lootlog.is_etc(kb, key):
            out.etc_n += n
            out.etc_v += n * (each or 0)
        else:
            out.items_n += n
            out.items_v += n * (each or 0)
    return out


# ---------------------------------------------------------------- HP or MP

KINDS = ("hp", "mp", "both")


def potion_kind(kb, key: str) -> str:
    """"hp", "mp" or "both" (an Elixir) as the KB's consumables table gives it (its HP and MP columns); "" when it
    has neither (a buff food)."""
    from . import tables
    from .kb import memo
    seen = memo(kb, "_potion_kinds")
    if not seen:
        for r in tables.rows(kb, "consumables"):
            hp, mp = bool(str(r.get("hp") or "").strip()), bool(str(r.get("mp") or "").strip())
            if r.get("key"):
                seen[r["key"]] = "both" if hp and mp else "hp" if hp else "mp" if mp else ""
        seen.setdefault("", "")
    return seen.get(key, "")


def by_kind(kb, potions) -> dict[str, tuple[int, int]]:
    """kind -> (used, what they cost) over summary potions [(name, used, key, price)]."""
    out: dict[str, tuple[int, int]] = {}
    for _name, n, key, price in potions:
        k = potion_kind(kb, key) or "hp"
        used, cost = out.get(k, (0, 0))
        out[k] = (used + n, cost + n * (price or 0))
    return out


@dataclass
class Money:
    """A session's figures as the tracker's three tables show them (the card, the full tracker and the history
    alike): EXP and mesos so far and per hour; the ETC, the other items (each at whichever pays more: best_sale)
    and the HP and MP potions, how many and what they made or cost (an Elixir, both: half each); then the income,
    what the potions cost and the net."""
    exp: int = 0
    exp_h: int | None = None
    mesos: int = 0
    mesos_h: int | None = None
    etc_n: int = 0
    etc_v: int = 0
    items_n: int = 0
    items_v: int = 0
    hp_n: int = 0
    hp_cost: int = 0
    mp_n: int = 0
    mp_cost: int = 0

    @property
    def income(self) -> int:
        return self.mesos + self.etc_v + self.items_v

    @property
    def spent(self) -> int:
        return self.hp_cost + self.mp_cost

    @property
    def net(self) -> int:
        return self.income - self.spent


def money(kb, exp: int, mesos: int, items, potions, seconds: float) -> Money:
    """items, potions: summary rows (name, count, key, price each); seconds: the session's length."""
    from . import lootlog
    loot = loot_value(kb, items)
    kinds = by_kind(kb, potions)
    both_n, both = kinds.get("both", (0, 0))
    hp_n, hp = kinds.get("hp", (0, 0))
    mp_n, mp = kinds.get("mp", (0, 0))
    return Money(exp, lootlog.per_hour(exp, seconds), mesos, lootlog.per_hour(mesos, seconds),
                 loot.etc_n, loot.etc_v, loot.items_n, loot.items_v,
                 hp_n + both_n, hp + both // 2, mp_n + both_n, mp + both - both // 2)


# ---------------------------------------------------------------- the monsters killed

def kills(kb, exp_counts: dict[int, int], maps: list[str], level: int | None = None) -> list[tuple[str, str, int]]:
    """(monster key, name, kills), most first: each "You received EXP (+N)" is one kill, and N is the EXP the KB gives
    the monster (seen live: 78 EXP x401 = Lorang, 75 x273 = Lupin; 10 x3 = Stump, 12 x2 = Slime). The monsters
    compared are the ones that spawn on the session's maps; with no map read, the ones within KILL_LEVELS of its
    level whose EXP no other such monster shares. An EXP no monster there gives (a quest, a party's share) is no kill."""
    from . import gamelookup, tables
    exp_of: dict[str, int] = {}
    for r in tables.rows(kb, "monsters"):
        v = r.get("exp")
        if r.get("key") and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            exp_of[r["key"]] = int(v)
    names: dict[str, str] = {}
    by_exp: dict[int, list[str]] = {}
    pool = [(k, n) for mid in maps for k, n, _lv, _c in gamelookup.map_monsters(kb, mid)]
    if not pool and level:
        pool = [(r["key"], r.get("monster") or r["key"]) for r in tables.rows(kb, "monsters")
                if isinstance(r.get("level"), (int, float)) and abs(int(r["level"]) - level) <= KILL_LEVELS]
    for k, n in pool:
        if k in exp_of and k not in names:
            names[k] = n
            by_exp.setdefault(exp_of[k], []).append(k)
    out: dict[str, int] = {}
    for amount, n in exp_counts.items():
        ks = by_exp.get(int(amount))
        if ks and len(ks) == 1:             # two monsters there with this EXP: no telling which
            out[ks[0]] = out.get(ks[0], 0) + n
    return sorted(((k, names[k], n) for k, n in out.items()), key=lambda r: (-r[2], r[1]))


# ---------------------------------------------------------------- the last time on this map

def previous_on_map(sessions, mid: str, exclude: int | None = None):
    """The newest finished session (lootdb.Session) whose main map (the most time) is mid, other than `exclude`."""
    for s in sessions:
        if s.id != exclude and s.ended is not None and s.maps and next(iter(s.maps)) == mid and s.seconds >= 60:
            return s
    return None


def change_pct(now: float | None, before: float | None) -> int | None:
    if now is None or not before or before <= 0:
        return None
    return round((now - before) / before * 100)


# ---------------------------------------------------------------- sell or keep

@dataclass
class Advice:
    verdict: str                # "keep" | "players" | "npc" | ""
    quests: int = 0             # current quests that need it
    recipes: int = 0            # crafting recipes that need it
    tip: str = ""               # what needs it, a line each (quest / recipe names), for a tooltip


def sell_advice(kb, key: str, npc_price: int | None, market_price: int | None, low_data: bool = False) -> Advice:
    """Keep it when a current quest or recipe needs it (NiaMeowDB's Safe to Sell? list), else sell it where it pays
    more (best_sale's rule: players when MesoWatch's usual sale price, a real one, beats the NPC's), else to an NPC.
    No list in this KB: no keep advice, only the prices."""
    from . import sitedata
    sell = sitedata.sell_list(kb)
    need = sell.needs.get(key) if sell is not None and key else None
    if need and (need.quests or need.recipes):
        tip = "\n".join([q[1] for q in need.quests[:8]] + [r[1] for r in need.recipes[:8]])
        return Advice("keep", len(need.quests), len(need.recipes), tip)
    if market_price and not low_data and market_price > (npc_price or 0):
        return Advice("players")
    if npc_price:
        return Advice("npc")
    return Advice("players" if market_price and not low_data else "")
