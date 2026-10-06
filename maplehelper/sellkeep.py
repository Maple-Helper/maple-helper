"""Sell or keep: each item an inventory read found (inventory.py), sorted by what the KB says about it, with no AI and
nothing guessed. Kept: an item a quest the player can do now asks for, an ingredient of a recipe of a profession they
work, an item they starred, the supplies they play with (potions, food, buffs, Return Scrolls, their class's ammo, by
the item's type), equipment they can wear (its page's REQ LEV and JOB). Sold: what an NPC pays for the rest
(the item page's sell price). An item the read couldn't name is said so, never named by a guess."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import farm, market

# the page's "JOB Mage" means a Magician (jobs.py's class name)
_JOB_WORD = {"Mage": "Magician"}


@dataclass
class Verdict:
    kind: str           # quest | recipe | wish | supply | wear | not_yet | other_job | fm | sell | no_price | unknown
    key: str = ""
    name: str = ""
    why: str = ""        # the quest or recipe it's kept for; the level it's worn from
    price: int = 0       # what an NPC pays for one
    slot: int = 0        # the inventory slot, 1-based
    picture: bytes = b""  # the icon as the game showed it (an item the read couldn't name)
    fm: int = 0          # the usual Free Market price, by the players' reports on NiaMeowDB


def _wear(kb, key: str) -> tuple[int, list[str]] | None:
    """(the level it asks, the classes it's for) from an equipment page's "REQ LEV 40 ... JOB Warrior"."""
    e = kb.get(key) or {}
    if not str(e.get("type") or "").startswith("Equip"):
        return None
    page = kb.page(key)
    lv = (e.get("props") or {}).get("Level Requirement")
    m = re.search(r"REQ LEV (\d+)", page)
    level = int(m.group(1)) if m else int(lv) if isinstance(lv, (int, float)) else 0
    j = re.search(r"\bJOB ([A-Za-z/]+)", page)
    jobs = [_JOB_WORD.get(x, x) for x in j.group(1).split("/")] if j else []
    return level, jobs


# Use items a player spends while playing, by the KB item's type: never "sell" (an audit found the page telling a Thief
# to sell the stars they attack with and everyone their potions and Return Scrolls)
_SUPPLY_TYPES = ("Use / Potion", "Use / Food", "Use / Buff", "Use / Return Scroll")
# a plain "Use" item whose page says it's a buff (Supreme potions) or a return scroll (Return Scroll to Orbis)
_SUPPLY_PAGE = re.compile(r"^(?:Shared item buff slot|Returns you to\b)", re.M)
# the class's ammunition: the KB's class pages say Bandits fight with daggers, Assassins/Hermits throw stars
_AMMO = {"Thief": "Use / Throwing Star", "Bowman": "Use / Arrow"}
_NO_AMMO_JOBS = ("Bandit", "Chief Bandit")
_ARROWS = {"Hunter": "for bows", "Ranger": "for bows", "Crossbowman": "for crossbows", "Sniper": "for crossbows"}


def _supply(kb, key: str, base_class: str = "", job: str = "") -> bool:
    """Potions, elixirs, food, buffs, Return Scrolls and the player's own class's ammo (by the KB's item types)."""
    from . import grind
    e = kb.get(key) or {}
    kind = str(e.get("type") or "")
    if not kind.startswith("Use"):
        return False
    if kind in _SUPPLY_TYPES or grind.is_potion(kb, key):
        return True
    if kind == _AMMO.get(base_class) and job not in _NO_AMMO_JOBS:
        # after the 2nd job a Bowman shoots only one weapon: bow arrows or crossbow bolts
        arrows = _ARROWS.get(job)
        return not arrows or arrows in str(e.get("name", "")).lower()
    return kind == "Use" and bool(_SUPPLY_PAGE.search(kb.page(key)))


def classify(kb, slots: list, level: int, base_class: str = "", job: str = "", done: list[str] | None = None,
             crafts: dict | None = None, wished: list[str] | None = None) -> list[Verdict]:
    """One verdict per inventory slot the read found, in the order of the groups the page shows."""
    wanted = farm.needs(kb, level, base_class, job, done, crafts, wished)
    out: list[Verdict] = []
    for s in slots:
        if s.status == "hovered":
            continue
        if s.status != "certain" or not s.matches:
            out.append(Verdict("unknown", slot=s.index, picture=s.picture))
            continue
        key = s.matches[0][0]
        e = kb.get(key) or {}
        name = e.get("name", key)
        need = wanted.get(name.lower())
        if need:
            out.append(Verdict(need[0], key, name, need[1], slot=s.index))
            continue
        wear = _wear(kb, key)
        if wear is not None:
            lv, jobs = wear
            if jobs and base_class and base_class not in jobs:
                kind = "other_job"
            elif lv and level < lv:
                kind = "not_yet"
            else:
                kind = "wear"
            sell = market.npc_prices(kb, key).sell_back or 0
            out.append(Verdict(kind, key, name, str(lv or ""), sell, s.index))
            continue
        if _supply(kb, key, base_class, job):
            out.append(Verdict("supply", key, name, slot=s.index))
            continue
        sell = market.npc_prices(kb, key).sell_back or 0
        out.append(Verdict("sell" if sell else "no_price", key, name, price=sell, slot=s.index))
    return _sorted(out)


ORDER = ["quest", "recipe", "wish", "supply", "wear", "not_yet", "other_job", "fm", "sell", "no_price", "unknown"]


def _sorted(out: list[Verdict]) -> list[Verdict]:
    return sorted(out, key=lambda v: (ORDER.index(v.kind), -max(v.fm, v.price), v.slot))


def for_market(kb, verdicts: list[Verdict]) -> list[str]:
    """The items worth a Free Market lookup: ones to sell or without an NPC price that players can trade."""
    from . import sitedata
    keys = []
    for v in verdicts:
        if v.kind in ("sell", "no_price") and v.key not in keys and not sitedata.untradeable(kb, v.key):
            keys.append(v.key)
    return keys


def with_market(verdicts: list[Verdict], usual: dict[str, int]) -> list[Verdict]:
    """An item the Free Market usually pays more for than an NPC: sell it there (usual: key -> the site's usual
    price over its window). No report: as it was."""
    for v in verdicts:
        price = usual.get(v.key) or 0
        if v.kind in ("sell", "no_price") and price > v.price:
            v.kind, v.fm = "fm", price
    return _sorted(verdicts)
