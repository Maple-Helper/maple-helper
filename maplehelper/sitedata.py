"""NiaMeowDB's list pages, as the knowledge base keeps them beside index.json (tools/meowdb_sections.py):

- skill_changes.json: the skills that changed between two builds ("COT1" -> "COT2"), field by field at max level;
- pets.json: every pet's lifespan, hunger rate, commands to Lv 30 and whether the Cash Shop sells it now;
- tiers.json: the class tier list, a benchmark of community level-70 builds (community opinion, tagged so);
- safe_to_sell.json: the Safe to Sell? list, every item a current quest or crafting recipe needs.

Nothing here names a build: the labels come from the file, so when MeowDB compares COT2 with Launch the chips
read "Changed in Launch" by themselves. A 3rd-job skill stays out while availability.py says 3rd job isn't out.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import availability, bidi, sources

SKILL_CHANGES, PETS, TIERS, SAFE_TO_SELL = "skill_changes.json", "pets.json", "tiers.json", "safe_to_sell.json"
# the jobs a later job grew out of, for a character's whole skill line (plan.JOB_BEFORE one step further back)
_JOB_BEFORE = {"Crusader": "Fighter", "White Knight": "Page", "Dragon Knight": "Spearman", "F/P Mage": "F/P Wizard",
               "I/L Mage": "I/L Wizard", "Priest": "Cleric", "Ranger": "Hunter", "Sniper": "Crossbowman",
               "Hermit": "Assassin", "Chief Bandit": "Bandit"}


def _file(kb, name: str) -> dict:
    """A list page's file, read once per KB object (and again when the file changes)."""
    root = getattr(kb, "root", None)
    path = root / name if root else None
    try:
        stamp = path.stat().st_mtime if path else None
    except OSError:
        stamp = None
    memo = kb.__dict__.setdefault("_sitedata", {}) if hasattr(kb, "__dict__") else {}
    if name in memo and memo[name][0] == stamp:
        return memo[name][1]
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if stamp is not None else {}
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    memo[name] = (stamp, data)
    return data


# ---------------------------------------------------------------- skill changes

@dataclass(frozen=True)
class SkillChange:
    key: str
    name: str
    job: str
    tier: int | None
    before: str                                  # the build compared from ("COT1")
    after: str                                   # and to ("COT2"): the skill's values now
    changes: tuple[tuple[str, str, str], ...]    # (field, before, after) at max level
    note: str = ""                               # the site's own words, for a change no number shows


def skill_changes(kb) -> dict[str, SkillChange]:
    """key -> its change, for every skill the KB has whose job is open in the game."""
    data = _file(kb, SKILL_CHANGES)
    if not data.get("skills"):
        return {}
    open_ = availability.of(kb)
    out = {}
    for r in data["skills"]:
        if not isinstance(r, dict) or not kb.get(r.get("key", "")):
            continue
        tier = r.get("tier")
        if isinstance(tier, int) and not open_.job_tier_open(tier):
            continue                     # a 3rd-job skill while 3rd job isn't out
        rows = tuple((str(c.get("field", "")), str(c.get("before", "")), str(c.get("after", "")))
                     for c in r.get("changes") or [] if isinstance(c, dict))
        out[r["key"]] = SkillChange(r["key"], str(r.get("name") or kb.get(r["key"])["name"]), str(r.get("job", "")),
                                    tier, str(data.get("before") or ""), str(data.get("after") or ""), rows,
                                    str(r.get("note") or ""))
    return out


def skill_change(kb, key: str) -> SkillChange | None:
    return skill_changes(kb).get(key) if key else None


def job_line(base_class: str, job: str) -> set[str]:
    """Every job whose skills a character of this job has: its class's 1st job, the jobs before it, its own."""
    line = {base_class, job}
    while job in _JOB_BEFORE:
        job = _JOB_BEFORE[job]
        line.add(job)
    return {j for j in line if j}


def changes_for(kb, base_class: str, job: str) -> list[SkillChange]:
    """The changed skills of a character's job line, longest name first (so "Final Attack: Sword" is found before
    a shorter name inside it)."""
    line = job_line(base_class, job)
    return sorted((c for c in skill_changes(kb).values() if c.job in line), key=lambda c: -len(c.name))


def chip_label(t, ch: SkillChange) -> str:
    """ "השתנה ב-COT2" / "Changed in COT2" (a Launch comparison reads "Changed in Launch")."""
    label = sources.tag(t, ch.after) if ch.after else "?"
    # a Hebrew build name takes the prefix glued on ("בהשקה"), an English one after a hyphen ("ב-COT2")
    return t("skill_changed_word" if bidi._RTL.match(label) else "skill_changed", label=label)


def field_name(t, field: str) -> str:
    """A change's field in the UI language ("Chance" -> "סיכוי"); a field the app has no word for, as written."""
    k = "skf_" + re.sub(r"[^a-z0-9]+", "_", field.lower()).strip("_")
    return t(k) if t(k) != k else field


def change_text(t, ch: SkillChange, sep: str = " · ") -> str:
    """ "סיכוי: 35% → 50% · נזק: 180% → 140%": each value pair one left-to-right block that never wraps inside
    (wrapped, "180%" ended one Hebrew line and "→ 140%" began the next, read backwards)."""
    return sep.join(f"{field_name(t, f)}: {bidi.LRI}{old}\u00a0→\u00a0{new}{bidi.PDI}" for f, old, new in ch.changes)


def note_he(ch: SkillChange, kb=None) -> str | None:
    """The site's note in Hebrew (assets/skill_changes/he.json), only while it was made from this very English: a
    note NiaMeowDB rewrites shows in English until it is translated again."""
    from . import translations
    made = translations.he(getattr(kb, "root", None), "skill_changes", ch.key, ch.note or "")
    if made:
        return made
    try:
        from .store import ASSETS
        tr = json.loads((ASSETS / "skill_changes" / "he.json").read_text(encoding="utf-8")).get(ch.key) or {}
    except (OSError, ValueError, AttributeError):
        return None
    return tr.get("he") if tr.get("en", "").strip() == (ch.note or "").strip() and tr.get("he") else None


def change_tip(t, ch: SkillChange, kb=None) -> str:
    """The chip's tooltip: the skill and the two builds, its changes at the skill's max level, and the site's note
    (in Hebrew when translated; the English one came under a "(in English)" line and read as a jumble)."""
    lines = [t("skill_changed_head", name=bidi.ltr_block(ch.name, t.rtl), before=sources.tag(t, ch.before or "?"),
               after=sources.tag(t, ch.after or "?"))]
    if ch.changes:
        lines.append(t("skill_changed_max") + " " + change_text(t, ch))
    if ch.note:
        he = note_he(ch, kb) if t.rtl else None
        if t.rtl and not he:
            lines.append(t("skill_changed_note"))
        lines.append(he or ch.note)
    return "\n".join(lines)


def ai_skill_lines(kb, keys) -> list[str]:
    """ "Skill change COT1 -> COT2 ...": for the skills in the AI's context, so advice uses the current values."""
    found = skill_changes(kb)
    out = []
    for k in dict.fromkeys(keys):
        ch = found.get(k)
        if not ch:
            continue
        bits = [f"{f} {old} -> {new}" for f, old, new in ch.changes]
        if ch.note:
            bits.append(ch.note)
        out.append(f"Skill change {ch.before} -> {ch.after} ({ch.name}, {ch.job}; values at max level, the "
                   f"{ch.after} ones are current): " + "; ".join(bits))
    return out


# ---------------------------------------------------------------- pets

@dataclass(frozen=True)
class Pet:
    key: str
    name: str
    lifespan: str              # as the site writes it: "7 days", "5 hours"
    hunger: int | None         # hunger rate: a rate-5 pet empties five times faster than a rate-1 pet
    level: int | None          # the level the commands figure is for (30)
    commands: int | None       # about how many obeyed commands to reach it
    commands_text: str         # "~25.0k"
    availability: str          # "In Cash Shop" / "Not sold right now"
    sold: bool
    closed_test: tuple[str, ...] = ()     # fields the site marks as closed-test values ("lifespan")


def cash_price(kb, key: str) -> tuple[int, bool] | None:
    """A Cash Shop item's price in NX from its page ("Cash Shop / 100 NX / Closed-test price"): (NX, from the closed
    test), or None."""
    m = re.search(r"^Cash Shop\n([\d,]+) NX\n(Closed-test price)?", kb.page(key), re.M)
    return (int(m.group(1).replace(",", "")), bool(m.group(2))) if m else None


@dataclass
class PetSkill:
    key: str
    name: str
    text: str                   # what it does, its page's words
    nx: int | None              # its Cash Shop price, None when not sold
    closed_test: bool           # the price is the closed test's
    sold: bool


def pet_skills(kb) -> list[PetSkill]:
    """The Cash Shop's pet skills (items typed "Cash / Pet Skill"): sold ones first, by name."""
    out = []
    for k, e in kb.entities.items():
        if str(e.get("type") or "") != "Cash / Pet Skill":
            continue
        lines = [ln.strip() for ln in kb.page(k).split("\n---", 2)[-1].splitlines()]
        head = lines.index("# " + e["name"]) if "# " + e["name"] in lines else -1
        text = []
        for ln in lines[head + 1:]:
            if ln == e["name"]:
                break
            if ln:
                text.append(ln)
        nx = cash_price(kb, k)
        out.append(PetSkill(k, e["name"], " ".join(text), nx[0] if nx else None, bool(nx and nx[1]),
                            bool(re.search(r"Available in Cash Shop", kb.page(k)))))
    return sorted(out, key=lambda s: (not s.sold, s.name))


_SKILLS_HE: dict | None = None


def pet_skill_text(s: PetSkill, lang: str, kb=None) -> str:
    """Its description in the player's language (assets/pet_skills/he.json while its English is the page's)."""
    global _SKILLS_HE
    if lang != "he":
        return s.text
    from . import translations
    made = translations.he(getattr(kb, "root", None), "pet_skills", s.key, s.text)
    if made:
        return made
    if _SKILLS_HE is None:
        import json
        from pathlib import Path
        try:
            _SKILLS_HE = json.loads((Path(__file__).resolve().parent.parent / "assets" / "pet_skills" / "he.json")
                                    .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _SKILLS_HE = {}
    row = _SKILLS_HE.get(s.key) or {}
    return row["he"] if row.get("en") == s.text and row.get("he") else s.text


def trade(kb, key: str) -> str:
    """How an item trades, by its page: "untradeable", "once" ("Tradeable once") or "tradeable"; "" unsaid."""
    m = re.search(r"^(Untradeable|Tradeable once|Tradeable)\b", kb.page(key), re.M)
    return {"Untradeable": "untradeable", "Tradeable once": "once", "Tradeable": "tradeable"}[m.group(1)] if m else ""


def fastest_commands(kb, key: str) -> list[tuple[str, str]]:
    """A pet page's "Fastest to level: sit / bad at Lv 1-9 (0.40 per try), hand at Lv 10-19 ...": (commands, "1-9")."""
    m = re.search(r"Fastest to level: (.+)", kb.page(key))
    if not m:
        return []
    return [(c.strip(), lv) for c, lv in re.findall(r"([a-z][a-z /]*?) at Lv (\d+-\d+)", m.group(1))]


def hunger_top(kb, key: str) -> int | None:
    """ "Pets range from 1 to 5" on a pet page: the scale's top."""
    m = re.search(r"Pets range from \d+ to (\d+)", kb.page(key))
    return int(m.group(1)) if m else None


def untradeable(kb, key: str) -> bool:
    """The item page says "Untradeable": no NPC buys it and no player can (a pet: the Cash Shop only). Once per item
    and KB: the Tools window's item list asks it of ~650 Cash items each time it opens (1.3 s, PERF-05)."""
    from .kb import memo
    seen = memo(kb, "_untradeable")
    if key not in seen:
        seen[key] = bool(re.search(r"^Untradeable\b", kb.page(key), re.M))
    return seen[key]


def pets(kb) -> list[Pet]:
    """The pets in the order the site lists them, each one the KB has an item page for."""
    out = []
    for r in _file(kb, PETS).get("pets") or []:
        if not isinstance(r, dict) or not kb.get(r.get("key", "")):
            continue
        hunger = r.get("hunger")
        out.append(Pet(r["key"], str(r.get("name") or kb.get(r["key"])["name"]), str(r.get("lifespan") or ""),
                       hunger if isinstance(hunger, int) else None, r.get("level"), r.get("commands"),
                       str(r.get("commands_text") or ""), str(r.get("availability") or ""), bool(r.get("sold")),
                       tuple(r.get("closed_test") or ())))
    return out


def pet(kb, key: str) -> Pet | None:
    return next((p for p in pets(kb) if p.key == key), None) if key else None


def easiest(items: list[Pet]) -> list[Pet]:
    """Fewest commands to the top level first (the site's "Easiest to level")."""
    return sorted(items, key=lambda p: (p.commands is None, p.commands or 0))


_SPAN = re.compile(r"^(\d+)\s*(hours?|days?)$", re.I)


def lifespan_text(t, p: Pet) -> str:
    """ "7 days" -> "7 ימים" in Hebrew; anything else as written."""
    m = _SPAN.match(p.lifespan.strip())
    if not m:
        return p.lifespan
    unit = "hours" if m.group(2).lower().startswith("hour") else "days"
    return t(f"pet_{unit}", n=m.group(1))


def ai_pet_lines(kb, keys=None) -> list[str]:
    """One line per pet (only `keys` when given) for the AI."""
    want = set(keys) if keys is not None else None
    out = []
    # the KB's own word on the launch lifespan ("Lifespans at launch are likely 30 to 90 days."), not a copy of it
    # here that would outlive the page
    page = kb.page("formula/pets") if kb.get("formula/pets") else ""
    expect = re.search(r"Lifespans? at launch[^.\n]*\.", page, re.I)
    note = f" (closed-test value; NiaMeowDB: {expect.group(0).strip()})" if expect else " (closed-test value)"
    for p in pets(kb):
        if want is not None and p.key not in want:
            continue
        life = p.lifespan + (note if "lifespan" in p.closed_test else "")
        out.append(f"Pet {p.name} [{p.key}]: lifespan {life}; hunger rate {p.hunger}; Lv {p.level} in about "
                   f"{p.commands_text.lstrip('~')} commands; {p.availability} (NiaMeowDB pets page)")
    return out


# ---------------------------------------------------------------- tier list

@dataclass(frozen=True)
class TierRow:
    key: str
    name: str                                    # the 2nd job ("Fighter")
    line: str                                    # its class ("Warrior")
    cells: dict                                  # column -> {"value", "grade"}
    ranks: dict                                  # column -> (place, out of)


def tier_data(kb) -> dict:
    return _file(kb, TIERS)


def _value(v: str) -> float | None:
    try:
        return float(re.sub(r"[~,\s]", "", v))
    except ValueError:
        return None


def tier_rows(kb) -> list[TierRow]:
    """Each class's own row (not the shield variants under Fighter and Page), with its place in every column."""
    data = tier_data(kb)
    base = [r for r in data.get("rows") or [] if isinstance(r, dict) and not r.get("variant") and kb.get(r.get("key", ""))]
    cols = list(data.get("columns") or [])
    ranks: dict[str, dict[str, tuple[int, int]]] = {r["key"]: {} for r in base}
    for col in cols:
        vals = [(r["key"], _value(str((r.get("cells") or {}).get(col, {}).get("value", "")))) for r in base]
        vals = [(k, v) for k, v in vals if v is not None]
        for k, v in vals:
            ranks[k][col] = (1 + sum(1 for _, o in vals if o > v), len(vals))
    return [TierRow(r["key"], str(r.get("name", "")), str(r.get("line", "")), dict(r.get("cells") or {}), ranks[r["key"]])
            for r in base]


def tiers_for(kb, base_class: str, job: str) -> list[TierRow]:
    """The rows a character's card shows: its own 2nd job (a 3rd job's: the one it grew out of), or before the
    2nd job the branches its class can pick; nothing for a Beginner."""
    rows = tier_rows(kb)
    line = job_line(base_class, job)
    own = [r for r in rows if r.name in line]
    if own:
        return own
    return [r for r in rows if r.line == base_class and base_class not in ("", "Beginner")]


def ai_tier_lines(kb, rows: list[TierRow] | None = None) -> list[str]:
    data = tier_data(kb)
    rows = tier_rows(kb) if rows is None else rows
    if not rows:
        return []
    who = f" by {data['builds_by']}" if data.get("builds_by") else ""
    head = (f"Community tier list (NiaMeowDB's benchmark of community level-{data.get('level') or '?'} builds{who}, "
            "a 0-defense target; community opinion, not official, say so when citing it; S/A/B = upper/middle/lower "
            "third of each column):")
    lines = [head]
    for r in rows:
        cells = "; ".join(f"{c} {v.get('grade') or '-'} {v.get('value')}" for c, v in r.cells.items())
        lines.append(f"- {r.name} ({r.line}): {cells}")
    return lines


# ---------------------------------------------------------------- safe to sell

@dataclass(frozen=True)
class SellNeeds:
    """An item on the Safe to Sell? list: what still needs it, so it's one to keep."""
    key: str
    name: str
    price: int                                   # what an NPC pays for one (0: the list has no price)
    quests: tuple[tuple[str, str, int, bool], ...]   # (quest key, name, how many, repeatable)
    recipes: tuple[tuple[str, str, int], ...]        # (discipline, recipe, how many)
    shops: tuple[str, ...]                       # NPC shops whose recorded stock has it (it can be bought back)


@dataclass(frozen=True)
class SellList:
    generated: str                               # the day NiaMeowDB made the list ("2026-10-10")
    needs: dict                                  # item key -> SellNeeds


def sell_list(kb) -> SellList | None:
    """The Safe to Sell? list over this KB's items, or None when the KB has no list (one from before it was
    scraped). A row is found by its item id while the KB's item there has the row's name; else by the name alone
    (the site picks one id when two items share a name), a row whose item the KB lacks left out."""
    data = _file(kb, SAFE_TO_SELL)
    rows = data.get("rows")
    if not isinstance(rows, list):
        return None
    memo = kb.__dict__.setdefault("_sitedata", {}) if hasattr(kb, "__dict__") else {}
    seen = memo.get("_sell_list")
    if seen is not None and seen[0] is data:
        return seen[1]
    by_name: dict[str, str] = {}
    for k, e in kb.entities.items():
        if e.get("category") == "item":
            by_name.setdefault(str(e.get("name") or "").strip().lower(), k)
    needs: dict[str, SellNeeds] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name") or "").strip()
        key = r.get("key")
        if str((kb.get(key) or {}).get("name") or "").strip().lower() != name.lower():
            key = by_name.get(name.lower())
        if not key or key in needs:
            continue
        quests = tuple((str(q.get("key") or ""), str(q.get("name") or ""), int(q.get("qty") or 0),
                        bool(q.get("repeatable"))) for q in r.get("quests") or [] if isinstance(q, dict))
        recipes = tuple((str(x.get("discipline") or ""), str(x.get("recipe") or ""), int(x.get("count") or 0))
                        for x in r.get("recipes") or [] if isinstance(x, dict))
        needs[key] = SellNeeds(key, name, int(r.get("price") or 0), quests, recipes,
                               tuple(str(s) for s in r.get("shops") or []))
    out = SellList(str(data.get("generated") or ""), needs)
    memo["_sell_list"] = (data, out)
    return out
