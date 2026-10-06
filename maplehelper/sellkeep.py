"""Sell or keep: each item an inventory read found (inventory.py), sorted by what the KB says about it, with no AI and
nothing guessed. Kept: an item a quest the player can do now asks for, an ingredient of a recipe of a profession they
work, an item they starred, equipment they can wear (its page's REQ LEV and JOB). Sold: what an NPC pays for the rest
(the item page's sell price). An item the read couldn't name is said so, never named by a guess."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import farm, market

# the page's "JOB Mage" means a Magician (jobs.py's class name)
_JOB_WORD = {"Mage": "Magician"}


@dataclass
class Verdict:
    kind: str           # quest | recipe | wish | wear | not_yet | other_job | fm | sell | no_price | unknown
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


def _sell_back(kb, slot, name: str) -> int:
    """What an NPC pays for the slot's item: its page's price, else that of a copy of it the read matched too (the
    same name, a picture as close). The KB has some prices on one copy's page only: Jr. Sentinel Shellpiece read as
    item/347 said "no price" while item/2584 pays 26 mesos."""
    for k, _ in slot.matches:
        if k == slot.matches[0][0] or (kb.get(k) or {}).get("name") == name:
            sell = market.npc_prices(kb, k).sell_back
            if sell:
                return sell
    return 0


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
            sell = _sell_back(kb, s, name)
            out.append(Verdict(kind, key, name, str(lv or ""), sell, s.index))
            continue
        sell = _sell_back(kb, s, name)
        out.append(Verdict("sell" if sell else "no_price", key, name, price=sell, slot=s.index))
    return _sorted(out)


ORDER = ["quest", "recipe", "wish", "wear", "not_yet", "other_job", "fm", "sell", "no_price", "unknown"]


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
