"""NiaMeowDB's list pages that aren't entity pages: the COT skill changes, the pets and the class tier list.

Each is one server-rendered page, read into one JSON file next to index.json (data/kb/skill_changes.json, pets.json,
tiers.json) that maplehelper/sitedata.py reads. Run by tools/scrape_meowdb.py after the entity pages, every night:
one request per page, one at a time, a second apart.

The Training Advisor is not here: its page holds no recommendations. The browser works them out itself (a damage
simulation of the guide builds in /_data/training-quick-start.json against every map), and the community's votes
on its picks are only ever sent (POST /msclassic/api/training/pick-vote), never published.

A page that reads as nothing (the site down, a new layout) leaves the file from the night before as it is, so one
broken page never empties a section; tools/kb_release.py validate checks each file's shape before a publish.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

BASE = "https://meowdb.com/msclassic"
SKILL_CHANGES = "skill_changes.json"
PETS = "pets.json"
TIERS = "tiers.json"
FILES = (SKILL_CHANGES, PETS, TIERS)
# below these a page read went wrong (37 skills, 12 pets and 10 classes on 2026-10-04)
MIN_ROWS = {SKILL_CHANGES: 10, PETS: 5, TIERS: 5}
_RANK = re.compile(r"(\d)(?:st|nd|rd|th) Job", re.I)


def _text(fragment: str) -> str:
    """A fragment's readable text: tags and React's "<!-- -->" gone, spaces folded."""
    t = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def _json_ld(page: str) -> list[dict]:
    found = []
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            found.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return found


def _skill_key(href: str) -> str:
    """ "/msclassic/skills/fighter/final-attack-sword" -> "skill/fighter__final-attack-sword" (scrape_meowdb.slug_of)."""
    parts = href.split("/msclassic/skills/", 1)[-1].strip("/").split("/")
    return "skill/" + "__".join(parts)


# ---------------------------------------------------------------- skill changes

def parse_skill_changes(page: str) -> dict:
    """{"before": "COT1", "after": "COT2", "skills": [{key, name, job, rank, tier, changes: [{field, before, after,
    delta}], note}]}: every skill card of the comparison. The two builds are read from the page ("37 named skills
    with verified COT1 to COT2 changes."), never assumed: a Launch comparison names itself the same way."""
    before = after = ""
    for d in _json_ld(page):
        m = re.search(r"verified (\S+) to (\S+) changes", str(d.get("description") or ""))
        if m:
            before, after = m.group(1), m.group(2)
            break
    if not after:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        h1 = _text(m.group(1)) if m else ""
        m = re.search(r"(\S+) skill changes", h1, re.I)
        after = m.group(1) if m else ""
    skills, seen = [], set()
    for art in re.findall(r"<article\b[^>]*>(.*?)</article>", page, re.S):
        link = re.search(r'href="([^"]*/msclassic/skills/[^"]+)"', art)
        name = re.search(r'<span class="block font-bold[^"]*">(.*?)</span>', art, re.S)
        sub = re.search(r'<span class="block text-2xs[^"]*">(.*?)</span>', art, re.S)
        if not (link and name):
            continue
        key = _skill_key(link.group(1))
        if key in seen:
            continue
        seen.add(key)
        job, _, rank = _text(sub.group(1)).partition(" · ") if sub else ("", "", "")
        tier = _RANK.search(rank)
        changes = []
        for dt, dd in re.findall(r"<dt\b[^>]*>(.*?)</dt>\s*<dd\b[^>]*>(.*?)</dd>", art, re.S):
            old = re.search(r'line-through[^"]*">(.*?)</span>', dd, re.S)
            rest = dd.split("→", 1)[-1] if "→" in dd else ""
            delta = re.search(r"\(\s*(?:<!--.*?-->)?(.*?)(?:<!--.*?-->)?\s*\)", rest, re.S)
            new = _text(rest[:delta.start()] if delta else rest)
            if old and new:
                changes.append({"field": _text(dt), "before": _text(old.group(1)), "after": new,
                                "delta": _text(delta.group(1)) if delta else ""})
        note = " ".join(_text(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", art, re.S)).strip()
        if not (changes or note):
            continue
        skills.append({"key": key, "name": _text(name.group(1)), "job": job.strip(), "rank": rank.strip(),
                       "tier": int(tier.group(1)) if tier else None, "changes": changes, "note": note})
    return {"source": f"{BASE}/skill-changes", "before": before, "after": after, "skills": skills}


# ---------------------------------------------------------------- pets

def _commands(text: str) -> int | None:
    """ "~25.0k" -> 25000."""
    m = re.fullmatch(r"~?\s*([\d.,]+)\s*([kKmM]?)", text.strip())
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return round(n * {"k": 1000, "m": 1_000_000}.get(m.group(2).lower(), 1))


def parse_pets(page: str) -> dict:
    """{"pets": [{key, name, lifespan, hunger, level, commands, commands_text, availability, sold, closed_test}]}:
    closed_test names the fields the page marks "(closed test)" (a value from the closed tests, not launch)."""
    pets, seen = [], set()
    for li in re.findall(r"<li>(.*?)</li>", page, re.S):
        link = re.search(r'href="[^"]*/msclassic/item-db/(\d+)"', li)
        name = re.search(r'<span class="text-\[15px\][^"]*">(.*?)</span>', li, re.S)
        if not (link and name):
            continue
        key = f"item/{link.group(1)}"
        if key in seen:
            continue
        seen.add(key)
        fields, closed = {}, []
        for label, value, tail in re.findall(r"<span>(\w[\w ]*?)(?:<!--.*?-->)?\s*<b[^>]*>(.*?)</b>([^<]*)</span>", li, re.S):
            label = _text(label).lower()
            fields[label] = _text(value)
            if "closed test" in tail.lower():
                closed.append(label)
        lv = re.search(r"Lv (\d+) in (~?[\d.,]+[kKmM]?) commands", _text(li))
        avail = re.search(r'<span class="mt-auto[^"]*">(.*?)</span>', li, re.S)
        avail_text = _text(avail.group(1)) if avail else ""
        hunger = fields.get("hunger", "")
        pets.append({"key": key, "name": _text(name.group(1)), "lifespan": fields.get("lifespan", ""),
                     "hunger": int(hunger) if hunger.isdigit() else hunger,
                     "level": int(lv.group(1)) if lv else None,
                     "commands": _commands(lv.group(2)) if lv else None,
                     "commands_text": lv.group(2) if lv else "",
                     "availability": avail_text,
                     # the page's own wording: "In Cash Shop" (sold now) or "Not sold right now"
                     "sold": bool(re.search(r"\bin cash shop\b", avail_text, re.I)),
                     "closed_test": closed})
    return {"source": f"{BASE}/pets", "pets": pets}


# ---------------------------------------------------------------- tier list

def _class_key(name: str) -> str:
    """ "F/P Wizard" -> "class/f-p-wizard" (the KB's class pages)."""
    return "class/" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_tiers(page: str) -> dict:
    """{"level": 70, "columns": [...], "rows": [{key, name, line, variant, cells: {column: {value, grade}}}]}.

    A row's "variant" is set on the shield rows the page puts under Fighter and Page ("1H + Shield"); a class the
    comparison leaves out (Islander, every cell "N/A") is not kept."""
    m = re.search(r"<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>", page, re.S)
    intro = _text(m.group(1)) if m else ""
    lv = re.search(r"\bat level (\d+)", intro) or re.search(r"Level (\d+) ·", _text(page[:20000]))
    table = re.search(r"<table\b.*?</table>", page, re.S)
    columns, rows = [], []
    if table:
        head = re.search(r"<thead\b.*?</thead>", table.group(0), re.S)
        ths = re.findall(r"<th\b[^>]*>(.*?)</th>", head.group(0) if head else "", re.S)
        columns = [_text(th).replace("↓", "").replace("↑", "").strip() for th in ths][1:]
        body = re.search(r"<tbody\b.*?</tbody>", table.group(0), re.S)
        last = None
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body.group(0) if body else "", re.S):
            tds = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
            if len(tds) != len(columns) + 1:
                continue
            divs = re.findall(r"<div class=\"(?:font-medium|text-maple-faint)[^\"]*\">(.*?)</div>", tds[0], re.S)
            if len(divs) < 2:
                continue
            first, second = _text(divs[0]), _text(divs[1])
            cells = {}
            for col, td in zip(columns, tds[1:]):
                grade = re.search(r'aria-label="([A-Z])\b[^"]*grade"', td)
                value = _text(re.sub(r"<span[^>]*aria-label=\"[^\"]*grade\"[^>]*>.*?</span>", "", td, flags=re.S))
                cells[col] = {"value": value, "grade": grade.group(1) if grade else ""}
            if all(c["value"] in ("N/A", "") for c in cells.values()):
                continue
            if "↳" in tds[0] and last:
                rows.append({**{k: last[k] for k in ("key", "name", "line")}, "variant": first, "gear": second,
                             "cells": cells})
            else:
                last = {"key": _class_key(first), "name": first, "line": second, "variant": "", "gear": "",
                        "cells": cells}
                rows.append(last)
    credit = re.search(r"(\w+) created the (\d+) Realistic level \d+ builds", _text(page))
    return {"source": f"{BASE}/tier-list", "level": int(lv.group(1)) if lv else None, "columns": columns,
            "rows": rows, "builds_by": credit.group(1) if credit else ""}


# ---------------------------------------------------------------- the nightly run

PAGES = ((SKILL_CHANGES, "skill-changes", parse_skill_changes, "skills"),
         (PETS, "pets", parse_pets, "pets"),
         (TIERS, "tier-list", parse_tiers, "rows"))


def rows_of(name: str, data: dict) -> list:
    return next((data.get(field) or [] for n, _, _, field in PAGES if n == name), [])


def scrape(kb: Path, fetch, delay: float = 1.0) -> int:
    """Fetch and parse each page into its file under `kb`; the number of files whose content changed."""
    changed = 0
    for name, path, parse, _ in PAGES:
        page = fetch(f"{BASE}/{path}")
        time.sleep(delay)
        data = parse(page) if page else {}
        if len(rows_of(name, data)) < MIN_ROWS[name]:
            print(f"::warning::{path}: {len(rows_of(name, data))} rows read, the previous {name} stays", flush=True)
            continue
        target = kb / name
        text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        old = target.read_text(encoding="utf-8") if target.exists() else ""
        if text != old:
            target.write_text(text, encoding="utf-8")
            changed += 1
        print(f"{path}: {len(rows_of(name, data))} rows" + (" (changed)" if text != old else ""), flush=True)
    return changed
