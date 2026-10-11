"""Scroll planning over the knowledge base: which scrolls an equip can take, and the exact odds of using them.

The scrolls table carries every scroll the KB confirms a player can hold (availability.py: a source of it is
in the game). Chaos scrolls' pages exist but none of them has a source yet, so the table — and this module —
leave them out; the window says so instead of offering them. plan() still models a destroy-on-fail chance,
for the day the KB confirms a scroll that has one (its success and its stats come from the table, the destroy
chance would come from that scroll's page — never from memory).

No Qt in here: scrolls_for/find_equips read the tables, plan/plans are closed-form probability (no simulation).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import tables
from .kb import memo

GRADES = ("Lesser", "Intermediate", "Greater")        # the KB's grades, least risky first (100 % / 60 % / 10 %)

# the two tables' own words for the same slot, where a plain word-for-word match misses. Case and hyphens never
# matter (the equips table writes "Two-Handed Axe", the scrolls table "Two-handed Axe"); "Topwear"/"Bottomwear"
# wear a suffix the equips table drops, and an overall is "Overall Armor" to the scrolls. Every equip slot in
# the real KB that has scrolls meets one of these (verified by test_scrollplan against the pages' own
# "Scrolls for <item>" tables; the scrolls' "Pet Equip" is the one slot with no equip counterpart).
_SLOT_ALIASES = {"top": "topwear", "bottom": "bottomwear", "overall": "overall armor"}


def _canon(slot: str) -> str:
    """A slot name as the two tables' names are compared: lower case, one space, no hyphens."""
    return " ".join(w for w in re.split(r"[\s-]+", str(slot or "").lower()) if w)


def scroll_slot(equip_slot: str) -> str:
    """The scrolls table's slot for an equip slot, as _canon leaves it ('Top' -> 'topwear',
    'Two-Handed Axe' -> 'two handed axe', 'Gloves' -> 'gloves')."""
    c = _canon(equip_slot)
    return _SLOT_ALIASES.get(c, c)


_EFFECT = re.compile(r"^(.+?) ([+-][\d,]+)$")


def effects(stats) -> tuple[tuple[str, int], ...]:
    """A scroll row's stats cell as its effects: 'Weapon Def. +6, Magic Def. +4' -> (('Weapon Def.', 6),
    ('Magic Def.', 4)). Unparsable text answers () (the window shows the cell as written instead)."""
    out = []
    for part in str(stats or "").split(", "):
        m = _EFFECT.match(part.strip())
        if m:
            out.append((m.group(1), int(m.group(2).replace(",", ""))))
    return tuple(out)


@dataclass(frozen=True)
class Scroll:
    """One grade of one stat, straight from a scrolls-table row."""
    key: str                                     # the KB key, what a price is looked up by
    scroll: str                                  # the whole name, 'Topwear HP Scroll: Lesser'
    grade: str
    success: int                                 # per cent, as the KB states it
    gains: tuple[tuple[str, int], ...]           # its effects: (('Max HP', 10),)
    buy: int | None = None                       # the NPC price the KB has for it (only Lesser scrolls have one)
    seller: str = ""                             # the KB's note on that shop, '' without a price


@dataclass(frozen=True)
class StatGroup:
    """One stat a slot's scrolls add, all the grades the game has of it. The grades may differ in what they
    grant (a Lesser DEF scroll adds W.DEF only, its Intermediate and Greater add M.DEF too): each Scroll
    keeps its own gains, the group is the KB's own word for the stat."""
    stat: str                                    # the KB's own word for it: 'HP', 'DEF', 'Attack'
    grades: tuple[Scroll, ...]                   # safest first (Lesser, Intermediate, Greater)

    @property
    def primary(self) -> str:
        """The effect the ladder and the cost-per-point speak of: the safest grade's first one."""
        return self.grades[0].gains[0][0] if self.grades and self.grades[0].gains else ""


def _stat_word(name: str, slot: str) -> str:
    """The stat a scroll's name carries between its slot and 'Scroll': 'Topwear HP Scroll: Lesser' -> 'HP'.
    '' when the name isn't shaped that way."""
    rest = name[len(slot) + 1:] if slot and name.startswith(slot + " ") else ""
    return rest.split(" Scroll", 1)[0] if rest else ""


@dataclass(frozen=True)
class EquipScrolls:
    """One equip and every scroll the KB says it can take ((), when it takes none)."""
    item: str
    key: str
    slot: str                                    # the equips table's slot word, 'Top'
    slots: int | None                            # its clean upgrade slots, when the KB has the number
    groups: tuple[StatGroup, ...]                # attack-type stats first, then the rest by name


def _groups(rows: list[dict]) -> tuple[StatGroup, ...]:
    """A slot's scroll rows as stat groups, by the stat the KB's own scroll names carry ('Topwear DEF Scroll'
    is one group even though only its Intermediate and Greater grades add M.DEF too), each group's grades
    safest-first, the groups themselves with the stats a player scrolls for first (attack, then by name)."""
    by_stat: dict[str, list[Scroll]] = {}
    for r in rows:
        e = effects(r.get("stats"))
        if not e:
            continue
        scroll = Scroll(key=r["key"], scroll=r["scroll"], grade=str(r.get("grade") or ""),
                        success=int(r.get("success") or 0), gains=e,
                        buy=r.get("buy"), seller=str(r.get("seller") or ""))
        stat = _stat_word(r["scroll"], str(r.get("slot") or ""))
        by_stat.setdefault(stat or ", ".join(label for label, _ in e), []).append(scroll)
    out = []
    for stat, grades in by_stat.items():
        ordered = tuple(sorted(grades, key=lambda s: (-s.success, GRADES.index(s.grade) if s.grade in GRADES
                                                      else len(GRADES), s.grade)))
        out.append(StatGroup(stat=stat, grades=ordered))
    return tuple(sorted(out, key=lambda g: (0 if g.primary.endswith("Attack") else 1, g.stat)))


def _cache(kb) -> dict:
    """The equips and their scrolls read once per KB (a keystroke only ranks them)."""
    cache = memo(kb, "_scroll_planner")
    if "equips" not in cache:
        by_slot: dict[str, list[dict]] = {}
        for r in tables.rows(kb, "scrolls"):
            if r.get("slot"):
                by_slot.setdefault(_canon(r["slot"]), []).append(r)
        groups = {slot: _groups(rows) for slot, rows in by_slot.items()}
        equips, scrolls = [], {}
        for r in tables.rows(kb, "equips"):
            es = EquipScrolls(item=r["item"], key=r["key"], slot=str(r.get("slot") or ""),
                              slots=r.get("slots"), groups=groups.get(scroll_slot(r.get("slot") or ""), ()))
            equips.append(es)
            if es.groups:
                scrolls[r["key"]] = es
        cache["equips"] = sorted(equips, key=lambda e: (re.sub(r"\s+", " ", e.item.lower()).strip(), e.key))
        cache["by_key"] = {e.key: e for e in equips}
        cache["scrolls"] = scrolls
    return cache


def scrolls_for(kb, item_key: str) -> EquipScrolls | None:
    """The equip this KB key is, with the scrolls it takes grouped by stat and its clean upgrade slots.
    None when the key isn't an equip; an EquipScrolls with no groups when it is one nothing scrolls."""
    return _cache(kb)["by_key"].get(item_key)


def scrollable(kb) -> dict[str, EquipScrolls]:
    """Every equip that takes at least one scroll, by KB key (what a character's worn gear is matched by)."""
    return _cache(kb)["scrolls"]


def _fold(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def find_equips(kb, query: str, limit: int = 20) -> list[EquipScrolls]:
    """Equips whose name has all the query's words in it — case aside, the words in any order — ranked the way
    the toolbar's search ranks them: the exact name, then the names the query starts, then the rest. A query
    under two letters (spaces aside) is still being typed: nothing yet."""
    equips = _cache(kb)["equips"]
    q = _fold(query)
    if len(q.replace(" ", "")) < 2:
        return []
    words = q.split(" ")
    exact, starts, rest = [], [], []
    for e in equips:
        n = _fold(e.item)
        if not all(w in n for w in words):
            continue
        (exact if n == q else starts if n.startswith(q) else rest).append(e)
    return (exact + starts + rest)[:max(0, int(limit))]


# ---------------------------------------------------------------- the odds themselves

@dataclass(frozen=True)
class Plan:
    """The exact outcome of using n slots with one scroll: per-slot success p, destroy-on-fail d. dist[k] is
    P(the item survives all n attempts with exactly k successes); a destroyed item is in `destroyed`."""
    n: int
    p: float
    d: float
    dist: tuple[float, ...]                      # k = 0..n
    destroyed: float
    all_pass: float                              # P(every slot succeeds) = p ** n
    expected_successes: float
    expected_attempts: float                     # scrolls used: n when nothing destroys, fewer once one can

    def at_least(self, k: int) -> float:
        """P(alive at the end with k or more successes — destroyed counts as not reaching k)."""
        return sum(self.dist[max(0, k):])


def plan(n: int, p: float, d: float = 0.0) -> Plan:
    """The exact distribution of a scroll session, no simulation. Each of the n slots is one attempt: it
    succeeds with p; failing, it destroys the item with d and the attempts stop (Lesser, Intermediate and
    Greater never destroy — d 0 — so dist is the plain binomial and destroyed is 0)."""
    n = max(0, int(n))
    p = min(1.0, max(0.0, float(p)))
    d = min(1.0, max(0.0, float(d)))
    go = 1.0 - (1.0 - p) * d                     # an attempt's chance the item goes on to the next one
    dist = tuple(math.comb(n, k) * p ** k * ((1.0 - p) * (1.0 - d)) ** (n - k) for k in range(n + 1))
    tried = n if go >= 1.0 else (1.0 - go ** n) / (1.0 - go)      # expected attempts, stops at a destruction
    return Plan(n=n, p=p, d=d, dist=dist, destroyed=1.0 - go ** n, all_pass=p ** n,
                expected_successes=p * tried, expected_attempts=tried)


@dataclass(frozen=True)
class GradePlan:
    """One grade of one stat over n slots: its plan, what it is expected to add per effect, and — given a
    price per scroll — what it is expected to cost, overall and per +1 of the stat."""
    scroll: Scroll
    plan: Plan
    expected: tuple[tuple[str, float], ...]      # (label, expected total gain) per effect
    price: int | None                            # the price the caller chose for this grade, None without one
    expected_cost: float | None                  # price * expected attempts, None without a price
    per_point: float | None                      # expected cost per +1 of the group's first effect


def plans(group: StatGroup, n: int, prices: dict[str, int | None] | None = None) -> tuple[GradePlan, ...]:
    """Every grade of one stat over the same n slots, for the player to compare. prices maps a scroll's KB key
    to the price to use for it (an NPC price, a Free Market one, the player's own — whatever the caller
    settled on); a grade without one still shows its odds, just no cost."""
    prices = prices or {}
    out = []
    for s in group.grades:
        pl = plan(n, s.success / 100.0, 0.0)      # the KB's scrolls never destroy (Chaos isn't confirmed in it)
        price = prices.get(s.key)
        cost = price * pl.expected_attempts if price is not None else None
        first = pl.expected_successes * (s.gains[0][1] if s.gains else 0)
        out.append(GradePlan(scroll=s, plan=pl,
                             expected=tuple((label, pl.expected_successes * gain) for label, gain in s.gains),
                             price=price, expected_cost=cost,
                             per_point=cost / first if cost is not None and first > 0 else None))
    return tuple(out)
