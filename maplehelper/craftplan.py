"""The crafting advisor's plan (the Crafting page's Craft plan tab is ui/craftadvisor.py, modelled on
maplestorycrafting.com's planner): what a chosen set of crafts still needs — from the player's bag first, then from monsters' drops or
an NPC's shop — in what order to craft it (materials, the profession level-ups the plan itself needs, the
final items) and what it all costs, every number from the KB's tables or the player's own input.

Qt-free. The choices the plan makes, all deterministic (same inputs → same plan):

* Recipe variants. A product can have several recipes (Screw from ingots or from Iron Hog's Metal Hoofs).
  The player's own pick (choices: product → recipe number, the window's Swap button) always wins; otherwise
  the mode picks: "cheapest" the variant whose full craft from raw mats costs fewest mesos, "highest" the
  one needing the highest profession level, "least" the one leaving the fewest raw units that no NPC sells
  (what only farming can get). Ties break by lowest cost, then lowest recipe number.
* The bag is spent before anything is farmed or crafted, at every level of the tree: a carried Mithril Wand
  removes the craft, carried Screws shrink the Screw crafts, carried ores shrink the ingots.
* Makes round up (18 Screws from a recipe that makes 10 → 2 crafts → 20 made, 2 left over) and one craft's
  surplus covers later needs in the same plan (the pool), so two wands sharing screws craft them once.
* Optional ingredients (the "Optional" catalysts) are not required: shown on the step, never farmed.
* Level-ups run on the cheapest recipe by mesos per EXP — the crafting fee plus the NPC value of the mats a
  single craft takes, with spare bag mats (what the plan's own needs left over) counting free — among the
  recipes the character's current profession level already allows. The EXP still needed starts from the
  Mastery bar % the player typed (of the current level's own requirement), minus the EXP of the plan's own
  earlier crafts in that discipline (the material crafts; a level-up stage's own sub-crafts are not counted,
  they only help), and asks for ceil(remaining / EXP per craft) crafts. A level-up the plan's material
  crafts need comes before them (then only the crafts already possible count their EXP); one only the final
  crafts need comes between materials and finals. A character level the KB's Crafting Levels table asks for
  (level 6 asks 30) blocks the crossing while the character is below it: the stage stays, marked with the
  level it needs. The player's measured EXP per level (settings["craft_exp_table"]) overrides the KB's.
* Costs count every craft's fee plus the NPC value of the raw mats it consumes (bag-covered mats are free).
  A raw mat no NPC sells counts 0 mesos and is listed to farm — the plan's notes say so. Plan.cost is
  exactly the sum of every step's cost (plus the value of raw mats a picked item itself is, which have no
  step of their own).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import crafting, gearadvice, tables

MODES = ("highest", "cheapest", "least")  # how recipe variants are picked, in the window's own order
DEFAULT_MODE = "least"  # the KB's own "From Scratch" pick too: fewest raw units to farm
DISCIPLINE_KEY = {crafting.NAMES[p]: p for p in crafting.PROFESSIONS}  # "Arcforge" -> "arcforge"
DROPPERS_SHOWN = 3  # droppers listed per farm item, best-voted first
LEVEL_CAP = 10  # "Crafting currently caps at level 10" (the KB's Crafting Levels table)

# the bag's groups, from the KB's own item types ("Etc / Ore & Mineral", "Etc / Gem", ...)
CATEGORIES = (("ore", "Ore & Mineral"), ("gem", "Gem"), ("material", "Crafting Material"), ("drop", "Monster Drop"))
CATEGORY_ORDER = {"ore": 0, "gem": 1, "material": 2, "drop": 3, "other": 4}


# ------------------------------------------------------------------ the KB's data, read once per kb


@dataclass(frozen=True)
class Ingredient:
    """One line of a recipe: how many of what, per craft. Optional catalysts are kept apart."""

    name: str
    key: str
    qty: int
    optional: bool = False


@dataclass(frozen=True)
class Recipe:
    """One variant of a product's recipe (the recipes table's rows for one (product, recipe) pair)."""

    product: str
    key: str
    makes: int  # how many the recipe produces per craft
    num: int  # which of the product's recipes this is (1, 2, ...)
    discipline: str  # "Arcforge"
    prof_lv: int  # profession level the recipe needs
    exp: int  # crafting EXP per craft
    fee: int  # mesos paid per craft
    ingredients: tuple[Ingredient, ...] = ()


def recipes(kb) -> dict[str, tuple[Recipe, ...]]:
    """Every recipe by product name (casefold), variants in the table's own order. Read once per kb."""
    out = kb.__dict__.get("_craftplan_recipes")
    if out is not None:
        return out
    grouped: dict[tuple[str, int], dict] = {}
    for r in tables.rows(kb, "recipes"):
        head = grouped.setdefault(
            (str(r.get("product") or "").casefold(), int(r.get("recipe") or 1)),
            {
                "product": str(r.get("product") or ""),
                "key": str(r.get("product_key") or ""),
                "makes": max(1, int(r.get("makes") or 1)),
                "discipline": str(r.get("discipline") or ""),
                "prof_lv": int(r.get("prof_lv") or 1),
                "exp": int(r.get("craft_exp") or 0),
                "fee": int(r.get("meso_cost") or 0),
                "ings": [],
            },
        )
        head["ings"].append(
            Ingredient(
                str(r.get("ingredient") or ""),
                str(r.get("ingredient_key") or ""),
                int(r.get("qty") or 0),
                str(r.get("optional") or "") == "yes",
            )
        )
    by_name: dict[str, list[Recipe]] = {}
    for (name, num), h in sorted(grouped.items()):
        by_name.setdefault(name, []).append(
            Recipe(h["product"], h["key"], h["makes"], num, h["discipline"], h["prof_lv"], h["exp"], h["fee"], tuple(h["ings"]))
        )
    out = {k: tuple(v) for k, v in by_name.items()}
    kb.__dict__["_craftplan_recipes"] = out
    return out


def buy_prices(kb) -> dict[str, int]:
    """Item name (casefold) → its cheapest NPC price, from the shops table. Unsold items are absent."""
    out = kb.__dict__.get("_craftplan_prices")
    if out is not None:
        return out
    out = {}
    for s in tables.rows(kb, "shops"):
        name, price = str(s.get("item") or "").casefold(), s.get("price")
        if name and isinstance(price, int) and price > 0:
            out[name] = min(out.get(name, price), price)
    kb.__dict__["_craftplan_prices"] = out
    return out


def _votes(text) -> int:
    m = re.match(r"\s*(\d+) up", str(text or ""))
    return int(m.group(1)) if m else 0


@dataclass(frozen=True)
class Sources:
    """Where a raw mat comes from: its best-voted droppers and the NPCs that sell it."""

    droppers: dict[str, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    sellers: dict[str, tuple[tuple[str, str, int], ...]] = field(default_factory=dict)


def sources(kb) -> Sources:
    """Droppers (monster, its level; best-voted first, each once) and sellers (npc, place, price; cheapest
    first) per item name (casefold), read once per kb from the drops and shops tables."""
    got = kb.__dict__.get("_craftplan_sources")
    if got is not None:
        return got
    src = Sources(droppers={}, sellers={})
    for d in tables.rows(kb, "drops"):
        name = str(d.get("item") or "")
        if not name:
            continue
        row = (str(d.get("monster") or ""), int(d.get("monster_level") or 0), _votes(d.get("votes")))
        if row[0]:
            src.droppers.setdefault(name.casefold(), []).append(row)
    for k, rows in src.droppers.items():
        seen, out = set(), []
        for m in sorted(rows, key=lambda r: (-r[2], r[1], r[0])):
            if (m[0], m[1]) not in seen:
                seen.add((m[0], m[1]))
                out.append((m[0], m[1]))
        src.droppers[k] = tuple(out[:DROPPERS_SHOWN])
    for s in tables.rows(kb, "shops"):
        name, price = str(s.get("item") or ""), s.get("price")
        if name and isinstance(price, int) and price > 0:
            src.sellers.setdefault(name.casefold(), []).append((str(s.get("npc") or ""), str(s.get("place") or ""), price))
    for k, rows in src.sellers.items():
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: (r[2], r[0])):
            if r not in seen:
                seen.add(r)
                out.append(r)
        src.sellers[k] = tuple(out)
    kb.__dict__["_craftplan_sources"] = src
    return src


def categories(kb) -> dict[str, str]:
    """Item name (casefold) → its bag group ("ore", "gem", "material", "drop", else "other"). The KB's own
    item types ("Etc / Ore & Mineral", "Etc / Gem", "Etc / Crafting Material", "Etc / Monster Drop"), with
    one refinement: everything the recipes table makes is a refined material — an ingot's type says
    "Ore & Mineral" but it belongs beside the Screws, not beside the ore it came from."""
    out = kb.__dict__.get("_craftplan_cats")
    if out is not None:
        return out
    out = {}
    for e in kb.entities.values():
        typ = str(e.get("type") or "")
        for cat, word in CATEGORIES:
            if word in typ:
                out[str(e.get("name") or "").casefold()] = cat
                break
    for k in recipes(kb):
        if k in out and out[k] != "drop":
            out[k] = "material"
    kb.__dict__["_craftplan_cats"] = out
    return out


@dataclass(frozen=True)
class ExpLevel:
    """One row of the Crafting Levels table, the player's measured EXP overriding the KB's."""

    level: int
    exp: int | None  # EXP from this level to the next
    char_level: int | None  # character level reaching the next level asks for
    measured: bool  # True = the player's own number, False = the KB's


def exp_needs(kb, overrides: dict | None = None) -> tuple[ExpLevel, ...]:
    """The crafting EXP table (crafting.exp_table, the KB's formula/leveling page) with the player's measured
    values (settings["craft_exp_table"], {"7": 1234}) replacing the KB's, per level, and flagged as theirs."""
    table = crafting.exp_table(kb)
    mine = {}
    for k, v in (overrides or {}).items():
        if str(k).strip().isdigit() and isinstance(v, int) and v > 0 and int(k) <= LEVEL_CAP:
            mine[int(k)] = v
    levels = sorted(set(table) | set(mine))
    return tuple(
        ExpLevel(lv, mine.get(lv, table.get(lv, (None, None))[0]), table.get(lv, (None, None))[1], lv in mine) for lv in levels
    )


# ------------------------------------------------------------------ what can be crafted (the item table)


@dataclass(frozen=True)
class Row:
    """One row of the item table: a recipe product the player can plan. Equips carry their level and the
    class's main attack stat; everything a search can find (scrolls, potions, materials) carries neither."""

    key: str
    name: str
    lv: int | None  # the equip's level requirement
    stat: int | None  # the class's main attack stat on it
    stat_word: str  # "W.ATK" / "M.ATK" (gearadvice.LABELS), "" for non-equips
    discipline: str
    prof_lv: int
    variants: int  # how many recipes the product has (a Swap needs more than one)
    cost: int  # crafting it from raw mats: fees plus the NPC value of the raws
    buy: int | None  # the NPC's price for the finished item; None = not sold
    save: int | None  # buy - cost (what crafting saves); None = not sold
    equip: bool  # an equip row (the default table) or a search-only row
    slot: str


def _class_stat(character) -> str:
    """The stat column the class's gear ranks by (gearadvice.MAIN_STAT): Magician M.ATK, the rest W.ATK."""
    stat = gearadvice.MAIN_STAT.get(str(getattr(character, "base_class", "") or ""), "")
    return "matk" if stat == "int" else "watk" if stat else ""


def craftable(kb, character) -> tuple[Row, ...]:
    """Every recipe product as an item-table row. The default table is the equip rows the character's class
    can hold (gearadvice.holds, the same class/job/weapon-type rule the Gear advisor uses; level and stat
    gaps stay the player's to read in the columns, like the site's own table listing levels above the
    character's). Search rows add every other product. Sorted by equip level, then the class's stat, name."""
    if character is None:
        return ()
    stat_col = _class_stat(character)
    stat_word = gearadvice.LABELS.get(stat_col, "")
    equips = {str(r.get("key")): r for r in tables.rows(kb, "equips")}
    others = {str(r.get("key")): r for r in tables.rows(kb, "consumables")}
    prices = buy_prices(kb)
    out: list[Row] = []
    for name, variants in recipes(kb).items():
        rec = _pick(kb, name, variants, None, DEFAULT_MODE, prices)
        if rec is None:
            continue
        eq = equips.get(rec.key or "")
        cost = math.ceil(_fresh(kb, name, rec, prices, DEFAULT_MODE) / rec.makes)  # per unit (a craft may make many)
        buy, lv, stat, slot, equip = None, None, None, "", False
        if eq is not None:
            equip = True
            slot = str(eq.get("slot") or "")
            lv = eq.get("req_lv")
            buy = eq.get("buy")
            stat = eq.get(stat_col) if stat_col else None
            if not gearadvice.holds(character, eq):
                continue  # another class's weapon: not in the default table, a search still finds it
        else:
            other = others.get(rec.key or "")
            buy, lv = (other or {}).get("buy"), (other or {}).get("req_lv")
        out.append(
            Row(
                rec.key,
                rec.product,
                lv,
                stat,
                stat_word if equip else "",
                rec.discipline,
                rec.prof_lv,
                len(variants),
                cost,
                buy,
                (buy - cost) if isinstance(buy, int) else None,
                equip,
                slot,
            )
        )
    return tuple(
        sorted(out, key=lambda r: (r.equip is False, r.lv if isinstance(r.lv, int) else 9999, -(r.stat or 0), r.name.casefold()))
    )


# ------------------------------------------------------------------ the plan's shapes


@dataclass(frozen=True)
class Step:
    """One craft the plan asks for: `crafts` crafts of a product (its materials are steps of their own), the
    fee and EXP per craft, and what the step costs — its fees plus the NPC value of the raw mats it consumed
    (bag-covered mats are free). Level-up steps carry the EXP each craft gives and the levels they cross."""

    name: str
    key: str
    crafts: int
    makes: int  # produced per craft (crafts × makes is what you end up with)
    discipline: str
    prof_lv: int
    fee: int  # per craft; the step's fee share is fee × crafts
    exp: int  # per craft
    depth: int  # 0 = a picked item itself; deeper = a material of a material
    final: bool  # one of the picked items
    level_up: bool  # the craft that earns the profession level
    ingredients: tuple[Ingredient, ...]  # required, per craft
    optional: tuple[Ingredient, ...]  # the "Optional" catalysts, shown but never required
    cost: int  # fees × crafts + the raw mats' value this step consumed

    @property
    def fee_total(self) -> int:
        return self.fee * self.crafts

    @property
    def exp_total(self) -> int:
        return self.exp * self.crafts


@dataclass(frozen=True)
class FarmItem:
    """One raw material the plan needs: what the bag covered, what is still to farm (or buy, when an NPC
    sells it), and where it comes from — its droppers and its sellers."""

    name: str
    key: str
    need: int  # the total the plan uses
    bag: int  # how much of it the bag covered
    farm: int  # still to farm (or buy, when priced)
    price: int | None  # cheapest NPC price; None = no NPC sells it
    category: str  # categories() group, for the bag's headings
    droppers: tuple[tuple[str, int], ...]
    sellers: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class Stage:
    """One stage of "then craft in this order": the material crafts, a profession's level-up, or the final
    items. A level-up a character level blocks stays in the plan with `blocked` naming what blocks it."""

    kind: str  # "prep" | "level" | "final"
    steps: tuple[Step, ...]
    disciplines: tuple[str, ...]
    crafts: int  # Σ crafts
    fee: int  # Σ fees
    level_from: int = 0  # level-ups: the profession level now …
    level_to: int = 0  # … and the one the plan needs
    discipline: str = ""
    exp_each: int = 0  # the level-up recipe's EXP per craft
    exp_needed: int = 0  # the EXP the stage still had to earn (bar % and earlier crafts counted off)
    blocked: str = ""  # "" or "char:<level>" / "exp" (the window words it)


@dataclass(frozen=True)
class Plan:
    """A whole forge plan: stages in craft order, what to farm, what the bag covered, the leftovers the
    makes rounding leaves, the totals, and the plan's notes as (code, arg) pairs the window words."""

    stages: tuple[Stage, ...]
    farm: tuple[FarmItem, ...]
    bag_used: tuple[tuple[str, int], ...]  # (item name, count) in the order the plan spent them
    leftovers: tuple[tuple[str, int], ...]  # (item name, count) the crafts make beyond the plan's needs
    cost: int  # Σ every step's cost (+ raw mats a picked item itself is): fees + bought raws
    crafts: int  # Σ every craft action
    exp: int  # Σ every craft's EXP
    equip_lv: int | None  # the highest equip level among the picked items
    notes: tuple[tuple[str, str], ...]  # ("no_price", name) · ("cycle", name)


@dataclass(frozen=True)
class Tree:
    """One product's expansion from raw mats (expand()): what it costs fresh, how many crafts, the raw mats
    with their quantities and prices, and the leftovers the makes rounding leaves."""

    name: str
    cost: int
    crafts: int
    raws: tuple[tuple[str, str, int, int | None], ...]  # (name, key, qty, NPC price or None)
    leftovers: tuple[tuple[str, int], ...]


# ------------------------------------------------------------------ the engine


class _StepBuild:
    """A Step while it still accumulates crafts (one product's crafts merge across the whole plan)."""

    def __init__(self, rec: Recipe, depth: int, final: bool, parent: str = ""):
        self.rec, self.depth, self.final, self.parent = rec, depth, final, parent
        self.crafts = 0
        self.cost = 0
        self.level_up = False

    def step(self) -> Step:
        rec = self.rec
        return Step(
            rec.product,
            rec.key,
            self.crafts,
            rec.makes,
            rec.discipline,
            rec.prof_lv,
            rec.fee,
            rec.exp,
            self.depth,
            self.final,
            self.level_up,
            tuple(i for i in rec.ingredients if not i.optional),
            tuple(i for i in rec.ingredients if i.optional),
            self.cost,
        )


class _RawBuild:
    """A raw mat (no recipe) the plan consumes: how much, how much of it the bag covered."""

    def __init__(self, name: str, key: str = ""):
        self.name, self.key = name, key
        self.need = 0  # total used by the plan
        self.bag = 0  # covered by the bag
        # (the raw's value is attributed to the step that consumes it, or the engine's orphan_cost)


def _crafts_for(count: int, makes: int) -> int:
    return math.ceil(count / makes)


class _Engine:
    """Walks the recipe tree, spending the bag and already-crafted surplus (the pool) before asking for
    more. One engine per plan; expand() runs one with an empty bag for a single product's fresh cost."""

    def __init__(self, kb, mode: str, choices: dict | None, prices: dict, exps: tuple[ExpLevel, ...] = ()):
        self.kb, self.mode, self.choices, self.prices, self.exps = kb, mode, choices or {}, prices, exps
        self.bag: dict[str, int] = {}  # name casefold -> what is still unspent
        self.pool: dict[str, int] = {}  # crafted surplus later needs in this plan drink from
        self.needed: dict[str, int] = {}  # name casefold -> what crafting had to produce
        self.steps: dict[str, _StepBuild] = {}  # name casefold -> the merged craft
        self.raw: dict[str, _RawBuild] = {}  # name casefold -> the raw mat
        self.bag_used: dict[str, int] = {}
        self.bag_order: list[str] = []
        self.notes: list[tuple[str, str]] = []
        self.orphan_cost = 0  # raw-mat value no step carries (a picked item that is raw)
        self.on_path: set[str] = set()

    # -- the walk ------------------------------------------------------

    def demand(
        self, name: str, count: int, depth: int = 0, parent: str = "", final: bool = False, recipe: Recipe | None = None
    ) -> None:
        """The plan needs `count` more of `name`: the bag and the pool give what they have, a recipe crafts
        the rest (its makes rounded up), and its ingredients are demanded in the recipe's own order."""
        if count <= 0:
            return
        k = name.casefold()
        count = self._spend_bag(k, name, count)
        count = self._spend_pool(k, count)
        if count <= 0:
            return
        self.needed[k] = self.needed.get(k, 0) + count
        rec = recipe or self._pick(k, depth, final)
        if rec is None or k in self.on_path:
            if k in self.on_path and rec is not None:
                self.notes.append(("cycle", rec.product))
            self._raw(k, name, count, parent)
            return
        crafts = _crafts_for(count, rec.makes)
        step = self.steps.get(k)
        if step is None:
            step = self.steps[k] = _StepBuild(rec, depth, final, parent)
        step.crafts += crafts
        self.pool[k] = max(0, step.crafts * rec.makes - self.needed[k])
        self.on_path.add(k)
        for ing in rec.ingredients:
            if not ing.optional:
                self.demand(ing.name, ing.qty * crafts, depth + 1, k, False)
        self.on_path.discard(k)

    # -- spending ------------------------------------------------------

    def _spend_bag(self, k: str, name: str, count: int) -> int:
        have = self.bag.get(k, 0)
        if have <= 0:
            return count
        take = min(have, count)
        self.bag[k] = have - take
        if k not in self.bag_used:
            self.bag_order.append(name)
        self.bag_used[k] = self.bag_used.get(k, 0) + take
        raw = self.raw.setdefault(k, _RawBuild(name, self._key_of(k, name)))
        raw.need += take
        raw.bag += take
        return count - take

    def _spend_pool(self, k: str, count: int) -> int:
        take = min(self.pool.get(k, 0), count)
        if take:
            self.pool[k] -= take
        return count - take

    def _key_of(self, k: str, name: str) -> str:
        for rec in recipes(self.kb).get(k, ()):  # a product's own key, when it has recipes
            if rec.key:
                return rec.key
        return getattr(self.kb, "_item_by_name", {}).get(name.lower(), "") or ""

    def _raw(self, k: str, name: str, count: int, parent: str) -> None:
        raw = self.raw.get(k)
        if raw is None:
            raw = self.raw[k] = _RawBuild(name, self._key_of(k, name))
        raw.need += count
        value = (self.prices.get(k) or 0) * count
        if parent and parent in self.steps:
            self.steps[parent].cost += value  # the craft that consumes it carries its cost
        else:
            self.orphan_cost += value  # a raw mat a picked item itself is: no step of its own

    # -- variant choice ------------------------------------------------

    def _pick(self, k: str, depth: int, final: bool) -> Recipe | None:
        variants = recipes(self.kb).get(k)
        if not variants:
            return None
        del depth, final  # a swapped product uses that recipe wherever the plan crafts it
        return _pick(self.kb, k, variants, self.choices.get(k), self.mode, self.prices)

    # -- level-ups -----------------------------------------------------

    def level_up(
        self, disc: str, current: int, need_lv: int, pct: int, char_lv: int, before: list[_StepBuild]
    ) -> tuple[_StepBuild | None, str, int]:
        """Plan the crafts that lift one discipline from `current` to `need_lv`. Returns (the level-up step
        or None, the block marker, the EXP still needed) and demands the recipe's materials as sub-steps.
        `before` are the plan's own crafts in this discipline that run first (their EXP is counted off)."""
        table = {e.level: e for e in self.exps}
        blocked, exp_needed = "", 0
        for lv in range(current, need_lv):
            e, nxt = table.get(lv), table.get(lv + 1)
            if e is None or e.exp is None:
                blocked = "exp"
                break
            exp_needed += e.exp
            if nxt is not None and nxt.char_level is not None and char_lv < nxt.char_level:
                blocked = f"char:{nxt.char_level}"
        if blocked:
            return None, blocked, exp_needed
        now = table.get(current)
        if now is not None and now.exp:
            exp_needed -= math.floor(now.exp * max(0, min(100, int(pct or 0))) / 100)
        exp_needed -= sum(s.rec.exp * s.crafts for s in before)
        exp_needed = max(0, exp_needed)
        cands = [r for rs in recipes(self.kb).values() for r in rs if r.discipline == disc and r.prof_lv <= current and r.exp > 0]
        if not cands:
            return None, "exp", exp_needed
        spares = dict(self.bag)  # what the plan's own needs left in the bag

        def score(r: Recipe) -> tuple[float, int, int, str]:
            mats = sum(
                max(0, i.qty - spares.get(i.name.casefold(), 0)) * (self.prices.get(i.name.casefold()) or 0)
                for i in r.ingredients
                if not i.optional
            )
            return ((r.fee + mats) / r.exp, -r.exp, r.num, r.product)

        best = min(cands, key=score)
        crafts = _crafts_for(exp_needed, best.exp) if exp_needed > 0 else 0
        if crafts <= 0:
            return None, "", 0
        stage_key = f"{disc.casefold()}#level"
        build = _StepBuild(best, 0, False, stage_key)
        build.crafts = crafts
        build.level_up = True
        self.steps[stage_key] = build
        self.on_path.add(stage_key)
        for ing in best.ingredients:
            if not ing.optional:
                self.demand(ing.name, ing.qty * crafts, 1, stage_key, False)
        self.on_path.discard(stage_key)
        return build, "", exp_needed

    # -- leftovers -----------------------------------------------------

    def leftovers(self) -> tuple[tuple[str, int], ...]:
        out = [(s.rec.product, q) for k, q in self.pool.items() if q > 0 and (s := self.steps.get(k))]
        return tuple(sorted(out, key=lambda x: x[0].casefold()))


def _pick(kb, k: str, variants: tuple[Recipe, ...], chosen: int | None, mode: str, prices: dict) -> Recipe | None:
    """The deterministic variant choice (the module docstring's rules). `chosen` (the player's Swap) wins.
    Recipes are compared per unit they produce (one craft of 10 Screws is not one craft of 3)."""
    if chosen is not None:
        return next((v for v in variants if v.num == chosen), None)
    if len(variants) == 1:
        return variants[0]

    def unit_cost(v: Recipe) -> float:
        return _fresh(kb, k, v, prices) / v.makes

    def unit_unfarmed(v: Recipe) -> float:
        return sum(q for _, _, q, p in _fresh_raws(kb, k, v, prices) if p is None) / v.makes

    if mode == "highest":
        return min(variants, key=lambda v: (-v.prof_lv, unit_cost(v), v.num))
    if mode == "least":
        return min(variants, key=lambda v: (unit_unfarmed(v), unit_cost(v), v.num))
    return min(variants, key=lambda v: (unit_cost(v), v.num))


def _fresh(kb, k: str, rec: Recipe, prices: dict, mode: str = "cheapest") -> int:
    """The cost of crafting one of a product from nothing: every fee plus the NPC value of every raw mat,
    its ingredient variants picked per `mode`. Cached per kb — the item table asks it of every product. A
    recipe chain that loops back on itself (Arrows for Bows from Arrows for Crossbows and back) counts 0
    where it loops: the engine's own walk guards it, this has to guard the walk's variant lookups."""
    cache = kb.__dict__.setdefault("_craftplan_fresh", {})
    got = cache.get((k, rec.num, mode))
    if got is not None:
        return got
    busy = kb.__dict__.setdefault("_craftplan_busy", set())
    if (k, rec.num) in busy:
        return 0
    busy.add((k, rec.num))
    try:
        eng = _Engine(kb, mode, None, prices)
        eng.demand(rec.product, 1, 0, recipe=rec)
        got = sum(s.cost + s.rec.fee * s.crafts for s in eng.steps.values()) + eng.orphan_cost
    finally:
        busy.discard((k, rec.num))
    cache[(k, rec.num, mode)] = got
    return got


def _fresh_cost(kb, k: str, rec: Recipe, prices: dict) -> int:
    return _fresh(kb, k, rec, prices)


def _fresh_raws(kb, k: str, rec: Recipe, prices: dict) -> tuple[tuple[str, str, int, int | None], ...]:
    """The raw mats of one fresh craft, as (name, key, qty, price) — the "least farming" choice."""
    cache = kb.__dict__.setdefault("_craftplan_raws", {})
    got = cache.get((k, rec.num))
    if got is not None:
        return got
    busy = kb.__dict__.setdefault("_craftplan_busy", set())
    if (k, rec.num) in busy:
        return ()
    busy.add((k, rec.num))
    try:
        eng = _Engine(kb, "cheapest", None, prices)
        eng.demand(rec.product, 1, 0, recipe=rec)
        got = tuple((r.name, r.key, r.need, prices.get(r.name.casefold())) for r in eng.raw.values())
    finally:
        busy.discard((k, rec.num))
    cache[(k, rec.num)] = got
    return got


def expand(kb, name: str, count: int = 1, choices: dict | None = None, mode: str = DEFAULT_MODE) -> Tree:
    """`count` of `name` expanded to its raw mats: every craft (makes rounded up), every fee, the raw mats'
    NPC value where an NPC sells them. The Mithril Wand's own page ("From Scratch: Total Meso 7,900 · Craft
    Actions 17 · Mithril Ore × 30 · Aquamarine Ore × 4 · Bronze Ore × 10 · Iron Ore × 10") is this."""
    eng = _Engine(kb, mode, choices, buy_prices(kb))
    eng.demand(name, count, 0, final=True)
    steps = [s for s in eng.steps.values() if s.crafts > 0]
    raws = tuple(
        sorted(
            ((r.name, r.key, r.need, eng.prices.get(r.name.casefold())) for r in eng.raw.values()), key=lambda x: x[0].casefold()
        )
    )
    return Tree(
        name,
        sum(s.cost + s.rec.fee * s.crafts for s in steps) + eng.orphan_cost,
        sum(s.crafts for s in steps),
        raws,
        eng.leftovers(),
    )


def plan(
    kb, character, targets, bag: dict, mode: str = DEFAULT_MODE, exp_overrides: dict | None = None, choices: dict | None = None
) -> Plan:
    """The whole forge plan for `targets` (KB item keys, duplicates = counts, the player's order) from the
    player's `bag` (KB item key → count). Every choice it makes is in the module docstring."""
    prices = buy_prices(kb)
    exps = exp_needs(kb, exp_overrides)
    eng = _Engine(kb, mode if mode in MODES else DEFAULT_MODE, choices, prices, exps)
    item_names = {str(key): str(e.get("name") or "") for key, e in kb.entities.items() if e.get("category") == "item"}
    for key, count in (bag or {}).items():
        name = item_names.get(str(key), "")
        if name and int(count or 0) > 0:
            eng.bag[name.casefold()] = eng.bag.get(name.casefold(), 0) + int(count)
    counts: dict[str, int] = {}
    for key in targets:
        counts[str(key)] = counts.get(str(key), 0) + 1
    equips = {str(r.get("key")): r for r in tables.rows(kb, "equips")}
    equip_lv = None
    for key, n in counts.items():
        name = item_names.get(key, "")
        if not name:
            continue
        eng.demand(name, n, 0, final=True)
        eq = equips.get(key)
        if eq is not None and isinstance(eq.get("req_lv"), int):
            equip_lv = max(equip_lv or 0, eq["req_lv"])
    if not eng.steps:
        return Plan((), (), (), (), 0, 0, 0, None, ())
    # which disciplines the plan lifts, and whether their material crafts need the level first
    cur = {
        d: max(1, int((getattr(character, "crafts", {}) or {}).get(k, 0) or 0))  # unlearned = level 1
        for d, k in DISCIPLINE_KEY.items()
    }
    pct = {d: int((getattr(character, "craft_pct", {}) or {}).get(k, 0) or 0) for d, k in DISCIPLINE_KEY.items()}
    char_lv = int(getattr(character, "level", 0) or 0)
    need: dict[str, int] = {}
    for s in eng.steps.values():
        if s.crafts > 0:
            need[s.rec.discipline] = max(need.get(s.rec.discipline, 0), s.rec.prof_lv)
    lifts = sorted(d for d in need if need[d] > cur.get(d, 0))
    early = [
        d
        for d in lifts
        if any(
            s.crafts > 0 and not s.final and s.rec.discipline == d and s.rec.prof_lv > cur.get(d, 0) for s in eng.steps.values()
        )
    ]
    late = [d for d in lifts if d not in early]
    claimed: dict[str, str] = {}  # step key -> the level stage that owns it (its own sub-crafts)
    level_specs: dict[str, tuple[_StepBuild | None, str, int]] = {}

    def run(disc: str, before: list[_StepBuild]) -> None:
        build, blocked, exp_need = eng.level_up(disc, cur.get(disc, 0), need[disc], pct.get(disc, 0), char_lv, before)
        level_specs[disc] = (build, blocked, exp_need)
        if build is None:
            return
        root = f"{disc.casefold()}#level"
        claimed[root] = disc  # the level-up craft itself belongs to its stage
        pending = [root]
        while pending:
            key = pending.pop()
            for k, s in eng.steps.items():
                if s.parent == key and k not in claimed:
                    claimed[k] = disc
                    pending.append(k)

    for disc in early:
        run(
            disc,
            [
                s
                for s in eng.steps.values()
                if s.crafts > 0 and not s.final and s.rec.discipline == disc and s.rec.prof_lv <= cur.get(disc, 0)
            ],
        )
    prep_keys = [k for k, s in eng.steps.items() if s.crafts > 0 and not s.final and not s.level_up and k not in claimed]
    for disc in late:
        run(disc, [s for k in prep_keys if (s := eng.steps[k]) and s.rec.discipline == disc])

    def stage_steps(keep) -> list[_StepBuild]:
        picked = [s for k, s in eng.steps.items() if s.crafts > 0 and keep(k, s)]
        picked.sort(key=lambda s: (s.rec.discipline, -s.depth, s.rec.prof_lv, s.rec.product.casefold()))
        return picked

    def stage(kind: str, builds: list[_StepBuild], **kw) -> Stage:
        return Stage(
            kind,
            tuple(s.step() for s in builds),
            tuple(dict.fromkeys(s.rec.discipline for s in builds)),
            sum(s.crafts for s in builds),
            sum(s.rec.fee * s.crafts for s in builds),
            **kw,
        )

    stages: list[Stage] = []
    for disc in early:
        builds = stage_steps(lambda k, s, d=disc: claimed.get(k) == d)
        build, blocked, exp_need = level_specs[disc]
        stages.append(
            stage(
                "level",
                builds,
                level_from=cur.get(disc, 0),
                level_to=need[disc],
                discipline=disc,
                exp_each=build.rec.exp if build else 0,
                exp_needed=exp_need,
                blocked=blocked,
            )
        )
    prep = stage_steps(lambda k, s: not s.final and not s.level_up and k not in claimed)
    if prep:
        stages.append(stage("prep", prep))
    for disc in late:
        builds = stage_steps(lambda k, s, d=disc: claimed.get(k) == d)
        build, blocked, exp_need = level_specs[disc]
        stages.append(
            stage(
                "level",
                builds,
                level_from=cur.get(disc, 0),
                level_to=need[disc],
                discipline=disc,
                exp_each=build.rec.exp if build else 0,
                exp_needed=exp_need,
                blocked=blocked,
            )
        )
    finals = stage_steps(lambda k, s: s.final)
    stages.append(stage("final", finals))
    # the farm list, the notes, the totals
    src, cats = sources(kb), categories(kb)
    farm = [
        FarmItem(
            r.name,
            r.key,
            r.need,
            r.bag,
            r.need - r.bag,
            prices.get(k),
            cats.get(k, "other"),
            src.droppers.get(k, ()),
            src.sellers.get(k, ()),
        )
        for k, r in eng.raw.items()
        if r.need > 0
    ]
    farm.sort(key=lambda f: (CATEGORY_ORDER.get(f.category, 9), f.farm <= 0, -f.farm, f.name.casefold()))
    notes = [n for n in eng.notes if n[0] == "cycle"]
    notes += [("no_price", f.name) for f in farm if f.price is None and f.farm > 0]
    all_steps = [s for st in stages for s in st.steps]
    return Plan(
        tuple(stages),
        tuple(farm),
        tuple((n, eng.bag_used[n.casefold()]) for n in eng.bag_order if eng.bag_used.get(n.casefold())),
        eng.leftovers(),
        sum(s.cost + s.fee_total for s in all_steps) + eng.orphan_cost,
        sum(s.crafts for s in all_steps),
        sum(s.exp_total for s in all_steps),
        equip_lv,
        tuple(dict.fromkeys(notes)),
    )
