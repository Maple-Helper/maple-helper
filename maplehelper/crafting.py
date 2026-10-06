"""Crafting professions from the KB's crafting pages (Smithing, Weaponcrafting, Tailoring, Woodcrafting,
Leatherworking, Arcforge): every recipe by profession level, with its ingredients, EXP and cost."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

PROFESSIONS = ("smithing", "weaponcrafting", "tailoring", "woodcrafting", "leatherworking", "arcforge")
NAMES = {p: p.capitalize() for p in PROFESSIONS}


@dataclass
class Recipe:
    name: str
    level: int                      # profession level that unlocks it
    exp: int
    catalyst: int                   # mesos paid to craft
    net: int                        # mesos gained (+) or burned (-) counting ingredients at NPC value
    exp_per_meso: float
    mats: str                       # "Farm only", "Mixed", ...
    ingredients: list[tuple[int, str]] = field(default_factory=list)   # (count, name)


@dataclass
class Level:
    level: int
    needs_exp: int | None           # profession EXP from this level to the next (pages/formula/leveling.md)
    char_level: int | None          # character level it asks for
    recipes: list[Recipe] = field(default_factory=list)


def _int(text: str) -> int:
    m = re.search(r"[-+]?\s*[\d,]+", text or "")
    return int(m.group(0).replace(",", "").replace(" ", "")) if m else 0


def _float(text: str) -> float:
    m = re.search(r"\d+(\.\d+)?", text or "")
    return float(m.group(0)) if m else 0.0


@lru_cache(maxsize=8)
def _levels(page: str) -> tuple[Level, ...]:
    lines = [ln.strip() for ln in page.split("\n---", 2)[-1].splitlines()]
    out: list[Level] = []
    cur: Level | None = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.fullmatch(r"Lv\. (\d+)", ln)
        if m:
            cur = next((lv for lv in out if lv.level == int(m.group(1))), None)
            if cur is None:
                cur = Level(int(m.group(1)), None, None)
                out.append(cur)
            need = re.match(r"needs ([\d,]+) EXP · char Lv\. (\d+)", lines[i + 1] if i + 1 < len(lines) else "")
            if need:
                cur.needs_exp, cur.char_level = _int(need.group(1)), int(need.group(2))
            i += 1
            continue
        row = re.fullmatch(r"(\d+) \| (.+)", ln)
        if cur and row and i + 2 < len(lines) and lines[i + 2].startswith("|"):
            ing = [(int(n), name.strip()) for n, name in re.findall(r"(\d+) x (.+?)(?=\s+\d+ x |$)", lines[i + 1])]
            vals = [v.strip() for v in lines[i + 2].strip("| ").split("|")]
            # the same row twice is skipped, but another recipe for the same item (other materials) is kept
            if len(vals) >= 7 and not any((r.name, r.ingredients) == (row.group(2).strip(), ing) for r in cur.recipes):
                cur.recipes.append(Recipe(row.group(2).strip(), cur.level, _int(vals[0]), _int(vals[1]), _int(vals[5]),
                                          _float(vals[6]), vals[7] if len(vals) > 7 else "", ing))
            i += 3
            continue
        i += 1
    return tuple(out)


def levels(kb, profession: str) -> tuple[Level, ...]:
    return _levels(kb.page(f"crafting/efficiency__{profession}"))


def max_level(kb, profession: str) -> int:
    return max((lv.level for lv in levels(kb, profession)), default=1)


def for_level(kb, profession: str, level: int) -> tuple[Level | None, Level | None]:
    """(the recipes at your profession level, the ones the next level opens), best EXP per meso first."""
    lv = {x.level: x for x in levels(kb, profession)}
    now, nxt = lv.get(level), lv.get(level + 1)
    for x in (now, nxt):
        if x:
            x.recipes.sort(key=lambda r: (-r.exp_per_meso, -r.exp))
    return now, nxt


@lru_cache(maxsize=4)
def _exp_table(page: str) -> dict[int, tuple[int, int]]:
    out, inside = {}, False
    for line in page.splitlines():
        cols = [c.strip() for c in line.split("|")]
        if cols[0] == "Crafting level":
            inside = True
            continue
        if inside and len(cols) >= 4 and cols[0].isdigit():
            out[int(cols[0])] = (_int(cols[1]), _int(cols[3]))
        elif inside and out:
            break
    return out


def exp_table(kb) -> dict[int, tuple[int, int]]:
    """Crafting level -> (EXP to the next level, the character level this level asks for), from the KB's
    "Crafting Levels" table (pages/formula/leveling.md: "1 | 50 | 0 | 10"): the same for every profession."""
    return _exp_table(kb.page("formula/leveling"))


def next_level(kb, profession: str, level: int) -> tuple[int, int, int | None] | None:
    """(the next level, the EXP from this level to it, the character level it asks for), or None at the top.
    The efficiency page's "Lv. N needs X EXP" is from N to N+1, so it was shown one level late (Smithing 1 said
    115 EXP to level 2, the KB says 50) and vanished at a level the page has no block for (audit GAM-1)."""
    table = exp_table(kb)
    if level + 1 in table and level in table:
        return level + 1, table[level][0], table[level + 1][1]
    lv = {x.level: x for x in levels(kb, profession)}
    now, nxt = lv.get(level), lv.get(level + 1)
    if now and nxt and now.needs_exp:
        return level + 1, now.needs_exp, nxt.char_level
    return None


def up_to(kb, profession: str, level: int) -> list[Recipe]:
    """Every recipe you can craft at your profession level (not only the ones that level opened):
    the newest level first, best EXP per meso first within a level."""
    out = [r for x in levels(kb, profession) if x.level <= level for r in x.recipes]
    return sorted(out, key=lambda r: (-r.level, -r.exp_per_meso, -r.exp))


# ------------------------------------------------------------------ who teaches it, where to work

STATIONS = {"smithing": "Anvil", "weaponcrafting": "Weaponcrafting Station", "tailoring": "Sewing Machine",
            "woodcrafting": "Woodworking Station", "leatherworking": "Leatherworking Station", "arcforge": "Arcane Station"}
MASTER_WORD = {"smithing": "Blacksmith", "weaponcrafting": "Weaponcrafter", "tailoring": "Tailor",
               "woodcrafting": "Carpenter", "leatherworking": "Leatherworker", "arcforge": "Arcforger"}


@dataclass
class Info:
    teacher: str = ""
    teacher_key: str = ""
    teacher_town: str = ""
    start_quest: str = ""               # "<teacher> in Need of an Apprentice" (Lv. 10)
    start_level: int | None = None
    master_quest: str = ""              # "A <title> in My Own Right!" (Lv. 25)
    master_level: int | None = None
    station: str = ""
    station_towns: list[str] = field(default_factory=list)


def _town(kb, key: str) -> str:
    """The first location of an NPC page ("Location Perion Victoria Road" -> "Perion")."""
    lines = [ln.strip() for ln in kb.page(key).splitlines()]
    for i, ln in enumerate(lines):
        if ln in ("Location", "Locations") and i + 1 < len(lines):
            return lines[i + 1].replace(" Victoria Road", "").replace(" Dungeon", "").strip()
    return ""


def info(kb, profession: str) -> Info:
    """The profession's teacher (and town), its start and mastery quests, and its work stations."""
    from . import combat, quests
    out = Info()
    word = MASTER_WORD.get(profession, "")
    for k, e in kb.entities.items():
        if e.get("category") != "quest":
            continue
        q = quests.quest(kb, k)
        if not q or q.area != "Crafting":
            continue
        if word and e["name"].startswith(f"A {word} in My Own Right") or e["name"].startswith(f"An {word} in My Own Right"):
            out.master_quest, out.master_level, out.teacher = e["name"], q.level, q.npc
    if out.teacher:
        out.teacher_key = kb._npc_by_name.get(out.teacher.lower(), "")
        out.teacher_town = _town(kb, out.teacher_key) if out.teacher_key else ""
        # the first lesson: "<teacher> in Need of an Apprentice", else the teacher's lowest crafting quest
        mine = [(q.level or 0, e["name"] != f"{out.teacher} in Need of an Apprentice", e["name"], q.level)
                for k, e in kb.entities.items() if e.get("category") == "quest"
                for q in [quests.quest(kb, k)] if q and q.area == "Crafting" and q.npc == out.teacher
                and e["name"] != out.master_quest]
        if mine:
            _, _, out.start_quest, out.start_level = min(mine, key=lambda m: (m[1], m[0]))
    out.station = STATIONS.get(profession, "")
    key = kb._npc_by_name.get(out.station.lower())
    if key:
        lines = [ln.strip() for ln in kb.page(key).splitlines()]
        if any(ln.startswith("Locations") for ln in lines):
            i = next(i for i, ln in enumerate(lines) if ln.startswith("Locations")) + 1
            while i < len(lines) and lines[i] != "About":
                ln = lines[i]
                if ln and ln != "Find path here":
                    town = ln.replace(" Victoria Road", "").replace(" Dungeon", "").replace(" Shallow Passage", "").strip()
                    if combat.released(kb, town) and town not in out.station_towns:
                        out.station_towns.append(town)
                i += 1
    return out


def made_from(kb, name: str) -> list[tuple[str, int, str]]:
    """How a crafting material is made: [(profession, level, "10 x Tree Branch")], lowest level first, from the
    professions' recipe tables (pages/crafting/efficiency__*.md: "5 | Processed Wood" then its ingredients)."""
    memo = kb.__dict__.setdefault("_made_from", {})
    if name in memo:
        return memo[name]
    out = []
    for key, e in kb.entities.items():
        if e.get("category") != "crafting" or not key.startswith("crafting/efficiency__"):
            continue
        profession = key.split("__", 1)[1].replace("-", " ").title()
        level = 1
        lines = kb.page(key).split("\n")
        for n, ln in enumerate(lines):
            m = re.match(r"^Lv\. (\d+)$", ln.strip())
            if m:
                level = int(m.group(1))
            m = re.match(r"^\d+ \| (.+?)(?: x [\d,]+)?$", ln.strip())
            if m and m.group(1).strip().lower() == name.lower() and n + 1 < len(lines):
                out.append((profession, level, lines[n + 1].strip()))
    memo[name] = sorted(out, key=lambda r: r[1])
    return memo[name]
