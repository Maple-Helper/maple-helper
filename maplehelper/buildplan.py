"""The build plan for the player's job: the AP, SP and equipment tables of the class guide, with the row
for the player's level marked. The tables come from the shipped guides (assets/guides), so they read in
the player's language."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import guides, plan

KINDS = (("ap", re.compile(r"\bAP allocation", re.I)), ("sp", re.compile(r"\bSP allocation", re.I)),
         ("gear", re.compile(r"Recommended equipment", re.I)))


@dataclass
class PlanTable:
    kind: str                 # ap | sp | gear
    heading: str              # in the player's language
    rows: list[list[str]]     # first row = header
    current: int | None       # index (in rows) of the row for the player's level
    head_en: list[str] | None = None     # the English header row (the columns' meaning, whatever the language)


def weapon_types(kb) -> set[str]:
    """Every weapon type the KB's equipment carries ("Bow", "Crossbow", "Claw"), lower case."""
    memo = kb.__dict__.setdefault("_weapon_types", None) if hasattr(kb, "__dict__") else None
    if memo is not None:
        return memo
    found = set()
    for e in kb.entities.values():
        t = str((e.get("props") or {}).get("_type") or e.get("type") or "")
        if t.startswith("Equip / "):
            found.add(t.split(" / ", 1)[1].strip().lower())
    kb.__dict__["_weapon_types"] = found
    return found


def split_paths(kb, table: PlanTable) -> list[tuple[str, PlanTable]]:
    """A first job's table that carries both 2nd-job paths side by side ("Bow AP spend" | "Crossbow AP spend") as
    one table per path, the shared columns ("Levels", "Useful armor") in each: ("", table) when it has one path.
    A path is a column whose English header starts with a weapon type of the KB, two of them or more (the owner:
    "Bowman" over a table of both 2nd jobs)."""
    head = table.head_en or []
    types = weapon_types(kb)
    owner = []
    for h in head:
        words = str(h).replace("[[", " ").split()
        first = words[0].lower() if words else ""
        owner.append(first if first in types else "")
    paths = list(dict.fromkeys(o for o in owner if o))
    if len(paths) < 2:
        return [("", table)]
    out = []
    for path in paths:
        cols = [i for i, o in enumerate(owner) if o in ("", path)]
        rows = [[r[i] for i in cols if i < len(r)] for r in table.rows]
        name = next(str(head[i]).split()[0] for i, o in enumerate(owner) if o == path)
        out.append((name, PlanTable(table.kind, table.heading, rows, table.current, [head[i] for i in cols])))
    return out


def _levels(cell: str) -> tuple[int, int] | None:
    """"10" -> (10, 10), "11-12" / "Levels 30-39" / "Lv 10" -> the range; None when there's no level."""
    m = re.search(r"(\d+)\s*[-–]\s*(\d+)", cell)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\d+", cell)
    return (int(m.group(0)), int(m.group(0))) if m else None


def current_row(rows: list[list[str]], level: int) -> int | None:
    """The row whose level (range) covers the player's level; else the last row already reached."""
    best = None
    for i, row in enumerate(rows[1:], start=1):
        lv = _levels(row[0]) if row else None
        if not lv:
            continue
        lo, hi = lv
        if lo <= level <= hi:
            return i
        if lo <= level:
            best = i
    return best


def tables(kb, base_class: str, job: str, level: int, lang: str) -> tuple[str | None, list[PlanTable]]:
    """(guide key, the plan tables of the player's class guide) in the player's language."""
    key = plan.class_guide(kb, base_class, job)
    if not key:
        return None, []
    en = guides.book(key, "en")
    local = guides.book(key, lang) or en
    if not en:
        return key, []
    same = len(local.get("blocks", [])) == len(en["blocks"])
    out, heading, heading_local, kinds_seen = [], "", "", set()
    for i, b in enumerate(en["blocks"]):
        if "h2" in b or "h3" in b:
            heading = b.get("h2") or b.get("h3")
            lb = local["blocks"][i] if same else b
            heading_local = lb.get("h2") or lb.get("h3") or heading
            continue
        if "table" not in b:
            continue
        kind = next((k for k, rx in KINDS if rx.search(heading)), None)
        if not kind or (kind, heading) in kinds_seen:
            continue
        kinds_seen.add((kind, heading))
        rows = (local["blocks"][i] if same else b).get("table") or b["table"]
        out.append((PlanTable(kind, heading_local, rows, current_row(b["table"], level), list(b["table"][0])),
                    _levels(heading)))
    # per kind, the table for the player's levels ("levels 10-30"), else the latest one already reached
    picked = []
    for kind, _ in KINDS:
        mine = [(t, r) for t, r in out if t.kind == kind]
        fits = [t for t, r in mine if not r or r[0] <= level <= r[1]]
        if not fits:
            reached = [(r[1], t) for t, r in mine if r and r[0] <= level]
            fits = [max(reached, key=lambda x: x[0])[1]] if reached else [t for t, _ in mine[:1]]
        picked += fits
    return key, picked


def citizenship_advice(kb, base_class: str, job: str, lang: str) -> tuple[str | None, list[str]]:
    """(the town the class guide recommends, its "Recommended citizenship" paragraphs in the player's language)."""
    from .quests import TOWNS
    # the job's guide first; a 2nd-job guide often only says "keep the citizenship from first job"
    for name in dict.fromkeys((job, base_class)):
        key = plan.class_guide(kb, base_class, name)
        town, paras = _citizenship_in(kb, key, lang, TOWNS) if key else (None, [])
        if town:
            return town, paras
    return None, []


def _citizenship_in(kb, key: str, lang: str, TOWNS) -> tuple[str | None, list[str]]:
    en = guides.book(key, "en")
    if not en:
        return None, []
    local = guides.book(key, lang) or en
    same = len(local.get("blocks", [])) == len(en["blocks"])
    blocks = en["blocks"]
    start = next((i for i, b in enumerate(blocks) if re.search(r"citizenship", b.get("h2") or b.get("h3") or "", re.I)), None)
    if start is None:
        return None, []
    end = next((i for i in range(start + 1, len(blocks)) if "h2" in blocks[i] or "h3" in blocks[i]), len(blocks))
    text_en = " ".join(b.get("p", "") for b in blocks[start:end]) + " " + (blocks[start].get("h2") or blocks[start].get("h3"))
    hits = [(text_en.find(t), t) for t in TOWNS if t in text_en]
    town = min(hits)[1] if hits else None
    src = local["blocks"] if same else blocks
    paras = [b["p"] for b in src[start + 1:end] if "p" in b]
    return town, paras
