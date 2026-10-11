"""What a character can wear now and what would beat what they wear: the KB's equips and drops tables turned
into per-slot upgrade advice. No number is invented here — every stat, price and dropper comes from a table, and
the ranking only compares columns those tables have (main attack first, then the class's main and secondary
stat, then weapon attack speed or W.DEF/HP), in the order the KB's own class guides set. ui/gearadvisor.py only
draws what this finds; combat.py has no damage formula that fits (its maths start from the stat window's damage
range, which the tables cannot give), so nothing here tries to compute damage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import jobs, tables

SOON_LEVELS = 5      # how many levels ahead "soon" reaches (a small constant: the next few levels' gear)
TOP_NOW = 3          # upgrades listed per slot
TOP_SOON = 2         # ... and soon items
DROPS_SHOWN = 3      # droppers named under an item with no NPC price

# the equip slots, as the equips table's own "slot" column names them. Armour in the order the window lists it;
# weapons only ever come from a character's own class, so their order here just breaks ties in slot lists.
ARMOUR_SLOTS = ("Hat", "Top", "Bottom", "Overall", "Gloves", "Shield", "Cape", "Earring", "Shoes")
WEAPON_SLOTS = ("One-Handed Sword", "Two-Handed Sword", "One-Handed Axe", "Two-Handed Axe",
                "One-Handed Blunt Weapon", "Two-Handed Blunt Weapon", "Spear", "Polearm",
                "Bow", "Crossbow", "Claw", "Dagger", "Wand", "Staff")
_SLOTS = frozenset(ARMOUR_SLOTS + WEAPON_SLOTS)

# which weapons each 2nd/3rd job fights with, from the KB's class pages:
#   pages/class/warrior.md: "Warriors branch into Fighters (swords/axes), Pages (swords/blunt weapons), or
#     Spearmen (spears/polearms) at level 30"
#   pages/class/bowman.md: "Bowmen branch into Hunters (bows, Arrow Bomb) or Crossbowmen (crossbows, Iron Arrow)"
#   pages/class/thief.md: "Thieves branch into Assassins (throwing stars, ranged) or Bandits (daggers, melee
#     burst) at level 30"; pages/guide/thief-class-guide.md: claws and daggers both, while a 1st-job Thief
#     (and a Warrior, Bowman or Magician likewise) holds every weapon their class's job column allows
#   pages/guide/fp-wizard-class-guide.md: "F/P Wizards can hold a shield with either a wand or a staff"
_SWORDS_AXES = ("One-Handed Sword", "Two-Handed Sword", "One-Handed Axe", "Two-Handed Axe")
_SWORDS_BLUNT = ("One-Handed Sword", "Two-Handed Sword", "One-Handed Blunt Weapon", "Two-Handed Blunt Weapon")
_WANDS = ("Wand", "Staff")
JOB_WEAPONS = {"Fighter": _SWORDS_AXES, "Crusader": _SWORDS_AXES,
               "Page": _SWORDS_BLUNT, "White Knight": _SWORDS_BLUNT,
               "Spearman": ("Spear", "Polearm"), "Dragon Knight": ("Spear", "Polearm"),
               "Hunter": ("Bow",), "Ranger": ("Bow",),
               "Crossbowman": ("Crossbow",), "Sniper": ("Crossbow",),
               "Assassin": ("Claw",), "Hermit": ("Claw",),
               "Bandit": ("Dagger",), "Chief Bandit": ("Dagger",),
               "F/P Wizard": _WANDS, "I/L Wizard": _WANDS, "Cleric": _WANDS,
               "F/P Mage": _WANDS, "I/L Mage": _WANDS, "Priest": _WANDS}

# each class's main and secondary stat, from the KB's class guides' AP allocations:
#   Warrior: pages/guide/fighter-class-guide.md "put the rest into STR ... Keep total DEX at the minimum"
#   Magician: pages/guide/magician-class-guide.md "INT is both your main damage stat" (LUK its accuracy side)
#   Bowman: pages/guide/bowman-class-guide.md "Put all 45 AP from levels 2-10 into DEX" (STR its weapon side)
#   Thief: pages/guide/bandit-class-guide.md "Why LUK does more damage ... Add 1 DEX and 4 LUK per level"
# A Beginner has no class stat: those columns just drop out of the ranking (armour then ranks by W.DEF/HP).
MAIN_STAT = {"Warrior": "str", "Magician": "int", "Bowman": "dex", "Thief": "luk"}
SECOND_STAT = {"Warrior": "dex", "Magician": "luk", "Bowman": "str", "Thief": "dex"}
STAT_KEYS = ("str", "dex", "int", "luk")

# how a stat shows in a delta line, in the order the deltas list them (the game's own words, so English in
# both languages, like job names)
LABELS = {"watk": "W.ATK", "matk": "M.ATK", "str": "STR", "dex": "DEX", "int": "INT", "luk": "LUK",
          "wdef": "W.DEF", "mdef": "M.DEF", "acc": "ACC", "avoid": "AVOID", "hp": "HP", "mp": "MP",
          "speed": "SPEED", "jump": "JUMP", "crit": "CRIT"}
DELTA_ORDER = ("watk", "matk", "str", "dex", "int", "luk", "wdef", "mdef", "acc", "avoid", "hp", "mp",
               "speed", "jump", "crit")

# "Attack Speed Fast (4)": the number is the stage, lower is faster. Unstated ranks below every stated stage
# at equal stats (two items, one with an unstated speed: the stated one is the surer pick, not the slower one).
_SPEED = re.compile(r"\((\d+)\)")


@dataclass(frozen=True)
class Fit:
    """Can this character wear this item now, and if not, why (machine codes the window translates):
    "level", "job", "gender" or "stat" — plus what to flag even though the item stays wearable: a stat
    requirement the player hasn't typed (missing: (stat, required, have-or-None), have None = not typed) and a
    gendered item on a player who hasn't said theirs (flags: ("gender",))."""
    ok: bool
    reasons: tuple[str, ...] = ()
    level_gap: int = 0            # req_lv - level, when the level is what blocks (else 0)
    missing: tuple[tuple[str, int, int | None], ...] = ()
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Upgrade:
    """One advised item: its equips row, the stat deltas against what is worn (the why of its rank), 0 when it is
    wearable now (else the levels until it opens), the NPC's buy price and seller as the table has them, and the
    droppers the drops table names (best-voted first)."""
    row: dict
    deltas: tuple[tuple[str, int], ...] = ()
    soon: int = 0
    buy: int | None = None
    seller: str = ""
    drops: tuple[tuple[str, int], ...] = ()
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlotPlan:
    """One equip slot: what is worn (the equips row behind character.gear's key, None for nothing; worn_gone:
    a stored key the table no longer has), the items wearable now that beat it, and the soon ones."""
    slot: str
    worn: dict | None = None
    worn_gone: bool = False
    now: tuple[Upgrade, ...] = ()
    soon: tuple[Upgrade, ...] = ()
    picker: tuple[dict, ...] = field(default=())       # every item of the slot the class can ever hold


def holds(character, item: dict) -> bool:
    """The class check the KB's own equips table makes — its job column ("Any" = all classes, else class names
    over "/") — plus which weapons a 2nd/3rd-job character actually fights with (JOB_WEAPONS). A 1st-job
    character holds every weapon their class's column allows (the class guides equip both, e.g. claws and
    daggers to a 1st-job Thief); a Beginner only what no column keeps from them."""
    base = str(getattr(character, "base_class", "") or "")
    job = str(getattr(character, "job", "") or "")
    for_class = str(item.get("job") or "Any")
    if base and for_class != "Any" and base not in for_class.split("/"):
        return False
    slot = str(item.get("slot") or "")
    if slot in WEAPON_SLOTS and (jobs.tier(job) or 0) >= 2:
        return slot in JOB_WEAPONS.get(job, ())
    return True


def wearable(kb, character, item: dict) -> Fit:
    """Level, class/job (weapon type included), gender when the player said one, and stat requirements against
    the player's typed STR/DEX/INT/LUK. A stat they haven't typed doesn't hide the item: the requirement is
    returned in `missing` for the window to flag."""
    reasons: list[str] = []
    level = int(getattr(character, "level", 0) or 0)
    req = item.get("req_lv")
    gap = req - level if isinstance(req, int) and req > level else 0
    if gap:
        reasons.append("level")
    if not holds(character, item):
        reasons.append("job")
    sex = str(item.get("gender") or "")
    mine = str(getattr(character, "gender", "") or "")
    flags: list[str] = []
    if sex:
        if mine and mine != sex:
            reasons.append("gender")
        elif not mine:
            flags.append("gender")
    ap = getattr(character, "ap", None) or {}
    missing: list[tuple[str, int, int | None]] = []
    for st in STAT_KEYS:
        need = item.get(f"req_{st}")
        if isinstance(need, int) and need > 0:
            have = ap.get(st)
            if have is None:
                missing.append((st, need, None))
            elif int(have) < need:
                missing.append((st, need, int(have)))
                reasons.append("stat")
    return Fit(not reasons, tuple(dict.fromkeys(reasons)), gap, tuple(missing), tuple(flags))


def _speed(item: dict) -> tuple[int, int]:
    m = _SPEED.search(str(item.get("attack_speed") or ""))
    return (1, -int(m.group(1))) if m else (0, 0)


def rank_key(base_class: str, item: dict) -> tuple:
    """The transparent ranking of one item in its slot, per the class guides' own order. A weapon: main attack
    (W.ATK; M.ATK for a Magician), the class's main stat, its secondary, then attack speed (faster first, the
    guides' "Spell cast time does not change with weapon speed" notwithstanding — it is the stated tie-break),
    then the table's own later tie-breaks CRIT and ACC (M.ATK is nothing to a physical class, so it never
    enters). Armour: main stat, secondary, then W.DEF and HP, then CRIT and ACC the same way. A Beginner has
    no class stat: those columns drop out."""
    main, second = MAIN_STAT.get(base_class), SECOND_STAT.get(base_class)
    parts = []
    if item.get("slot") not in ARMOUR_SLOTS:                       # a weapon (or a slot the KB names anew)
        parts.append((item.get("matk") or 0) if base_class == "Magician" else (item.get("watk") or 0))
    if main:
        parts.append(item.get(main) or 0)
    if second:
        parts.append(item.get(second) or 0)
    if item.get("slot") not in ARMOUR_SLOTS:
        parts += list(_speed(item))
    else:
        parts += [item.get("wdef") or 0, item.get("hp") or 0]
    return tuple(parts + [item.get("crit") or 0, item.get("acc") or 0])


def deltas(worn: dict | None, item: dict) -> tuple[tuple[str, int], ...]:
    """The non-zero stat differences against what is worn (nothing worn: against zeroes), stat by stat: the why
    of an item's rank, and everything else it gives up or adds."""
    out = []
    for st in DELTA_ORDER:
        d = int(item.get(st) or 0) - int((worn or {}).get(st) or 0)
        if d:
            out.append((st, d))
    return tuple(out)


def delta_text(dts: tuple[tuple[str, int], ...]) -> str:
    """'W.ATK +5 · STR +2' — the game's own stat words, one line (English in both languages, like job names)."""
    return " · ".join(f"{LABELS[st]} {'+' if d > 0 else ''}{d}" for st, d in dts)


def _votes(text: str) -> int:
    m = re.match(r"\s*(\d+) up", str(text or ""))
    return int(m.group(1)) if m else 0


def _droppers(kb, keys: set[str]) -> dict[str, tuple[tuple[str, int], ...]]:
    """The monsters the drops table says drop each item, best-voted first, each monster once, the top few
    (votes: the community's own confirmation of a drop)."""
    found: dict[str, dict[str, tuple[int, int]]] = {k: {} for k in keys}
    for r in tables.rows(kb, "drops"):
        k = str(r.get("item_key") or "")
        if k not in found:
            continue
        monster, lv = str(r.get("monster") or ""), r.get("monster_level")
        lv = lv if isinstance(lv, int) else 0
        best = found[k].get(monster)
        if best is None or _votes(r.get("votes") or "") > best[0]:
            found[k][monster] = (_votes(r.get("votes") or ""), lv)
    out = {}
    for k, mobs in found.items():
        ranked = sorted(mobs.items(), key=lambda m: (-m[1][0], m[1][1], m[0].casefold()))
        out[k] = tuple((name, lv) for name, (_, lv) in ranked[:DROPS_SHOWN])
    return out


def _equips(kb) -> list[dict]:
    return [r for r in tables.rows(kb, "equips") if str(r.get("slot") or "") in _SLOTS]


def slots(kb, character) -> list[str]:
    """The slots worth showing this character: their class's weapon slots (the job's own at 2nd job on), then
    armour — only slots the equips table holds something they can ever hold."""
    rows = _equips(kb)
    have = {s for r in rows if holds(character, r) for s in (str(r["slot"]),)}
    weapons = ([s for s in WEAPON_SLOTS if s in have]
               if (jobs.tier(str(getattr(character, "job", "") or "")) or 0) < 2
               else [s for s in WEAPON_SLOTS if s in JOB_WEAPONS.get(str(getattr(character, "job", "")), ())])
    return weapons + [s for s in ARMOUR_SLOTS if s in have]


def slot_items(kb, character, slot: str) -> list[dict]:
    """Every item of this slot the character's class can ever hold (any level: the picker's list, gendered twins
    both in it), by name then level."""
    rows = [r for r in _equips(kb) if r.get("slot") == slot and holds(character, r)]
    return sorted(rows, key=lambda r: (str(r.get("item") or "").casefold(), r.get("req_lv") or 0, str(r.get("key"))))


def upgrades(kb, character) -> list[SlotPlan]:
    """One SlotPlan per slot: what is worn, the wearable-now items that rank strictly above it (every wearable
    item when nothing is worn), and the soon ones — items only levels away (within SOON_LEVELS) that also rank
    above it. Each Upgrade carries its deltas, its NPC price and seller from the equips table, and its droppers
    from the drops table, so the window never has to guess a source."""
    base = str(getattr(character, "base_class", "") or "")
    rows = _equips(kb)
    by_key = {str(r.get("key")): r for r in rows}
    gear = getattr(character, "gear", None) or {}

    def by_rank(items: list[Upgrade], level_first: bool = False) -> list[Upgrade]:
        """Best rank first (ties: the lower level, then the name); level_first: the soonest level first."""
        items.sort(key=lambda u: str(u.row.get("item") or "").casefold())
        items.sort(key=lambda u: rank_key(base, u.row), reverse=True)
        if level_first:
            items.sort(key=lambda u: u.row.get("req_lv") or 0)
        return items

    plans: list[SlotPlan] = []
    wanted: set[str] = set()
    for slot in slots(kb, character):
        worn = by_key.get(str(gear.get(slot))) if gear.get(slot) else None
        worn_key = rank_key(base, worn) if worn else None
        now: list[Upgrade] = []
        soon: list[Upgrade] = []
        for r in (x for x in rows if x.get("slot") == slot and holds(character, x)):
            if worn is not None and r.get("key") == worn.get("key"):
                continue                                  # what is worn is not its own upgrade
            fit = wearable(kb, character, r)
            if worn_key is not None and rank_key(base, r) <= worn_key:
                continue                                  # must strictly beat what is worn, by the ranking
            if fit.ok:
                now.append(Upgrade(r, deltas(worn, r), 0))
            elif fit.reasons == ("level",) and 0 < fit.level_gap <= SOON_LEVELS:
                soon.append(Upgrade(r, deltas(worn, r), fit.level_gap))
        now, soon = by_rank(now)[:TOP_NOW], by_rank(soon, level_first=True)[:TOP_SOON]
        wanted |= {str(u.row.get("key")) for u in now + soon}
        plans.append(SlotPlan(slot, worn, bool(gear.get(slot)) and worn is None, tuple(now), tuple(soon)))
    droppers = _droppers(kb, wanted)

    def filled(u: Upgrade) -> Upgrade:
        """The price, the seller and the droppers behind a kept row, and the flags the window shows beside it:
        a gendered item on an unsaid gender ("gender"), a stat requirement the player hasn't typed
        ("stat:<str|dex|int|luk>:<need>")."""
        fit = wearable(kb, character, u.row)
        return Upgrade(u.row, u.deltas, u.soon,
                       u.row.get("buy") if isinstance(u.row.get("buy"), int) else None,
                       str(u.row.get("seller") or ""), droppers.get(str(u.row.get("key")), ()),
                       fit.flags + tuple(f"stat:{st}:{need}" for st, need, have in fit.missing if have is None))
    return [SlotPlan(p.slot, p.worn, p.worn_gone, tuple(filled(u) for u in p.now),
                     tuple(filled(u) for u in p.soon), tuple(slot_items(kb, character, p.slot)))
            for p in plans]
