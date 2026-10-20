"""The grind tracker's helpers beyond the counts (no Qt): the next level and when it comes at this rate, potions
running low and the nearest NPC shop that sells them, the rate over the session (for the graph), the last session on
the same map to compare with, and whether to sell an ETC to an NPC, to players, or keep it. Every figure is the
KB's or the player's own logs and reads; nothing is guessed. No Qt of its own (a route comes from ui/mapview)."""
from __future__ import annotations

from dataclasses import dataclass

LOW_POTIONS = 20            # a potion used this session with this many left or fewer is running low
GRAPH_STEP_S = 60           # the rate graph: a point a minute ...
GRAPH_WINDOW_S = 5 * 60     # ... each the rate over the last five minutes (a single pickup is no rate)
MARKET_BETTER = 1.5         # players pay this much more than an NPC (after the market's own cost): sell to players


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


# ---------------------------------------------------------------- the rate over the session

def rate_series(kb, events, start: float, end: float) -> list[tuple[float, int, int]]:
    """(minutes since the start, EXP per hour, net mesos per hour) a point a GRAPH_STEP_S, each over the last
    GRAPH_WINDOW_S (or the session so far, in its first minutes). Net: mesos + what an NPC pays for the items -
    what the potions cost."""
    from . import lootlog
    if end - start < GRAPH_STEP_S:
        return []
    price: dict[str, int] = {}

    def worth(e) -> int:
        if e.kind == "mesos":
            return e.amount
        if e.kind in ("item", "potion") and e.key:
            if (e.kind, e.key) not in price:
                p = lootlog.sell_price(kb, e.key) if e.kind == "item" else lootlog.potion_cost(kb, e.key)
                price[(e.kind, e.key)] = p or 0
            v = e.amount * price[(e.kind, e.key)]
            return v if e.kind == "item" else -v
        return 0
    evs = sorted(events, key=lambda e: e.t)
    out = []
    t = start + GRAPH_STEP_S
    while t <= end + 1:
        lo = max(start, t - GRAPH_WINDOW_S)
        span = t - lo
        inside = [e for e in evs if lo < e.t <= t]
        exp = sum(e.amount for e in inside if e.kind == "exp")
        net = sum(worth(e) for e in inside)
        out.append(((t - start) / 60, round(exp * 3600 / span), round(net * 3600 / span)))
        t += GRAPH_STEP_S
    return out


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
    """Keep it when a current quest or recipe needs it (NiaMeowDB's Safe to Sell? list), else sell it to players
    when they pay clearly more than an NPC (MesoWatch's usual sale price, a real one: not a guess on few sales), else
    to an NPC. No list in this KB: no keep advice, only the prices."""
    from . import sitedata
    sell = sitedata.sell_list(kb)
    need = sell.needs.get(key) if sell is not None and key else None
    if need and (need.quests or need.recipes):
        tip = "\n".join([q[1] for q in need.quests[:8]] + [r[1] for r in need.recipes[:8]])
        return Advice("keep", len(need.quests), len(need.recipes), tip)
    if market_price and not low_data and market_price >= (npc_price or 0) * MARKET_BETTER and market_price > 0:
        return Advice("players")
    if npc_price:
        return Advice("npc")
    return Advice("players" if market_price and not low_data else "")
