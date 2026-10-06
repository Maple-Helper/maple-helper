"""The player's plan, straight from the knowledge base: EXP to the next level, where to train,
the next job advancement, and the one tip worth showing right now.

Sources are the KB's own guides (EXP table, best grind maps) and the job tree, so everything
here is instant and costs no Claude usage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

EXP_GUIDE = "guide/exp-table-level-1-to-100"
GRIND_GUIDE = "guide/best-grind-maps-every-level"
# main stat first; the second only as much as gear and accuracy need
STATS = {"Beginner": ("STR", "DEX"), "Warrior": ("STR", "DEX"), "Magician": ("INT", "LUK"),
         "Bowman": ("DEX", "STR"), "Thief": ("LUK", "DEX")}
JOB_SOON = 2          # levels before an advancement when the tip appears
MAP_TOO_EASY = 8      # the map's monsters this many levels below the player: suggest moving on
# a new character stays on Maple Island until about level 8, then takes the boat to Lith Harbor
# (pages/guide/beginners-guide-first-steps-in-maple-world.md: "By the time you finish all the beginner quests on
# Maple Island, you'll likely be lv 8. That's when you take the boat ... to Lith Harbor")
LEAVE_ISLAND = 8
ISLAND_REGION = "Maple Road"      # Maple Island's maps: "Location Maple Road / Maple Island" (pages/map/*.md)


@dataclass
class Spot:
    map: str
    street: str
    exp_hr: int
    mob: str
    mob_level: int
    why: str


@dataclass
class Bracket:
    lo: int
    hi: int
    spots: list[Spot] = field(default_factory=list)


def _int(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


@lru_cache(maxsize=4)
def _exp_table(page: str) -> dict[int, int]:
    table = {}
    for line in page.splitlines():
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 3 and cols[0].isdigit():
            need = _int(cols[1])
            if need:
                table[int(cols[0])] = need
    return table


def exp_table(kb) -> dict[int, int]:
    """level -> EXP needed for the next level."""
    return _exp_table(kb.page(EXP_GUIDE))


@lru_cache(maxsize=4)
def _brackets(page: str) -> tuple[Bracket, ...]:
    out: list[Bracket] = []
    current = None
    for line in page.splitlines():
        m = re.fullmatch(r"Level (\d+)-(\d+)", line.strip())
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            current = next((b for b in out if (b.lo, b.hi) == (lo, hi)), None)
            if current is None:
                current = Bracket(lo, hi)
                out.append(current)
            continue
        cols = [c.strip() for c in line.split(" | ")]
        if current is None or len(cols) < 6 or cols[0] == "Map":
            continue
        mob = re.fullmatch(r"(.+?) \(lv(\d+)\)", cols[3])
        exp_hr = _int(cols[2])
        if mob and exp_hr and not any(s.map == cols[0] for s in current.spots):
            current.spots.append(Spot(cols[0], cols[1], exp_hr, mob.group(1), int(mob.group(2)), cols[5]))
    for b in out:
        b.spots.sort(key=lambda s: -s.exp_hr)
    return tuple(out)


def brackets(kb) -> tuple[Bracket, ...]:
    return _brackets(kb.page(GRIND_GUIDE))


def spots_for(kb, level: int, n: int = 3) -> list[Spot]:
    b = next((b for b in brackets(kb) if b.lo <= level <= b.hi), None)
    return b.spots[:n] if b else []


def route(kb, level: int, levels_ahead: int = 8) -> list[tuple[Bracket, Spot]]:
    """The best spot of each level bracket from now to `levels_ahead` levels on."""
    out = []
    for b in brackets(kb):
        if b.hi >= level and b.lo <= level + levels_ahead and b.spots:
            out.append((b, b.spots[0]))
    return out


def monster_exp(kb, name: str) -> int | None:
    key = next((k for k, e in kb.entities.items() if e["category"] == "monster" and e["name"] == name), None)
    exp = ((kb.get(key) or {}).get("props") or {}).get("EXP") if key else None
    return exp if isinstance(exp, (int, float)) and exp > 0 else None


def island_monster(kb, level: int) -> tuple[str, int] | None:
    """(name, EXP) of the best Maple Island monster for this level: the strongest one that lives only on the
    island (its maps are all in ISLAND_REGION) and is at most 2 levels above the player."""
    from .combat import special_monster
    top = getattr(kb, "_top_maps", None)
    best = None
    for k, e in kb.entities.items():
        p = e.get("props") or {}
        lv, exp = p.get("Level"), p.get("EXP")
        if e.get("category") != "monster" or not isinstance(lv, (int, float)) or not isinstance(exp, (int, float)) \
                or exp <= 0 or lv > level + 2 or special_monster(e["name"]):
            continue
        maps = top(k) if top else []
        if maps and all(m.endswith(" " + ISLAND_REGION) for m in maps) and (best is None or (lv, exp) > best[0]):
            best = ((lv, exp), e["name"], int(exp))
    return (best[1], best[2]) if best else None


def progress(kb, level: int, exp_pct: float | None) -> dict | None:
    """EXP left to the next level and how many of the bracket's main monster that is (on Maple Island, an island
    monster: the grind guide's level 1-10 maps are on Victoria Island)."""
    need = exp_table(kb).get(level)
    if not need or exp_pct is None:
        return None
    left = max(0, round(need * (1 - exp_pct / 100)))
    out = {"pct": exp_pct, "need": need, "left": left}
    island = island_monster(kb, level) if level < LEAVE_ISLAND else None
    if island:
        out.update(mob=island[0], kills=-(-left // island[1]))
        return out
    spot = (spots_for(kb, level, 1) or [None])[0]
    if spot:
        mexp = monster_exp(kb, spot.mob)
        if mexp:
            out.update(mob=spot.mob, kills=-(-left // int(mexp)))
    return out


def next_job(base_class: str, job: str, level: int, kb=None) -> tuple[list[str], int] | None:
    """The next advancement: (job names to choose from, level), or None at the end of the tree
    (the end of what's open in the game: no 3rd job until the KB confirms it, jobs.open_tier)."""
    from .jobs import JOBS, tier_levels
    tree = JOBS.get(base_class, [])
    if base_class == "Beginner":
        lv = min(jobs[1][1] for c, jobs in JOBS.items() if c != "Beginner")    # level 10 for every class
        # still a Beginner past level 10: the choice is still ahead (the tip vanished at 10-13, audit GAM-4)
        return [c for c in JOBS if c != "Beginner"], lv
    current = next((lv for j, lv in tree if j == job), 0)
    later = [lv for lv in tier_levels(base_class, kb) if lv > current]
    if not later:
        return None
    lv = later[0]
    return [j for j, need in tree if need == lv], lv


# a 3rd job's own 2nd job: its guide is the closest one there is (the class guide stops at 1st-job tables)
JOB_BEFORE = {"Crusader": "Fighter", "White Knight": "Page", "Dragon Knight": "Spearman",
              "F/P Mage": "F/P Wizard", "I/L Mage": "I/L Wizard", "Priest": "Cleric",
              "Ranger": "Hunter", "Sniper": "Crossbowman", "Hermit": "Assassin", "Chief Bandit": "Bandit"}


def class_guide(kb, base_class: str, job: str) -> str | None:
    """The KB guide for the player's job ("F/P Wizard" -> guide/fp-wizard-class-guide), else for the job it
    came from (Crusader -> Fighter), else for the class."""
    for name in filter(None, (job, JOB_BEFORE.get(job), base_class)):
        key = "guide/" + re.sub(r"[^a-z0-9]+", "-", name.lower().replace("/", "")).strip("-") + "-class-guide"
        if kb.get(key):
            return key
    return None


@dataclass
class Tip:
    kind: str          # "job" | "map"
    key: str           # i18n key of the text
    args: dict
    question: str      # what the chat asks Claude when the tip is tapped (player's language)


def tip(kb, c, t, dismissed: dict | None = None) -> Tip | None:
    """The single most useful tip right now, or None. A dismissed tip stays away until the next level."""
    dismissed = dismissed or {}

    def fresh(kind):
        return dismissed.get(kind) != c.level

    nxt = next_job(c.base_class, c.job, c.level, kb)
    if nxt and fresh("job") and nxt[1] - JOB_SOON <= c.level < nxt[1] + 3:
        jobs, lv = nxt
        names = " / ".join(jobs)
        key = "tip_job_now" if c.level >= lv else "tip_job_soon"
        return Tip("job", key, {"n": lv - c.level, "jobs": names, "level": lv},
                   t("tip_job_q", jobs=names, level=lv))
    if c.map and fresh("map"):
        here = [s for b in brackets(kb) for s in b.spots if s.map.lower() == c.map.lower()]
        best = (spots_for(kb, c.level, 1) or [None])[0]
        if here and best and here[0].mob_level <= c.level - MAP_TOO_EASY and best.map != here[0].map:
            return Tip("map", "tip_map", {"map": best.map, "mob": best.mob},
                       t("tip_map_q", map=best.map))
    return None


def exp_position(kb, level: int, pct: float) -> float | None:
    """Total EXP earned from level 1 (to compare two readings across a level-up)."""
    table = exp_table(kb)
    if level not in table:
        return None
    return sum(table.get(lv, 0) for lv in range(1, level)) + table[level] * pct / 100


def exp_rate(kb, start: tuple[float, int, float], end: tuple[float, int, float]) -> dict | None:
    """Two readings (time, level, EXP %) -> {"per_hour": EXP/hour, "pct_hour": % of the current level per hour,
    "to_level": seconds to the next level, "minutes": minutes measured}; None if they can't be compared."""
    (t0, lv0, p0), (t1, lv1, p1) = start, end
    a, b = exp_position(kb, lv0, p0), exp_position(kb, lv1, p1)
    hours = (t1 - t0) / 3600
    if a is None or b is None or hours <= 0 or b <= a:
        return None
    per_hour = (b - a) / hours
    need = exp_table(kb).get(lv1)
    out = {"per_hour": round(per_hour), "minutes": round(hours * 60, 1)}
    if need:
        out["pct_hour"] = round(per_hour / need * 100, 1)
        out["to_level"] = round(need * (1 - p1 / 100) / per_hour * 3600)
    return out
