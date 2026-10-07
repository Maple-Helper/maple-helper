"""The Skills tab of Play tools' "Build & Skills": every class line's skill books, a tab per job (the 1st job and each
2nd job; Beginner its own), each skill with its picture, what it does, its max level, what it needs first and what
it does at level 1 and at its max level.

The skills are the KB's own pages, read by the skills table's parser (tables._skill), so a skill the table leaves
out (not in the game, a job tier that isn't out) is out here too. Only the jobs up to the 2nd: 3rd job isn't in
the game (the owner), and availability.py closes it too until the KB says it is out.

Hebrew: assets/skills/he.json (the owner's), then the KB's he.json (tools/translate_kb.py, kind "skill_desc"),
each text only while it was made from the very English the page has now (translations.py). A key per text:
"<skill key>" its description, "<skill key>#1" and "<skill key>#max" its level 1 and max level effects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import availability, jobs, tables, translations

KIND = "skill_desc"         # translations.py's kind (assets/skills/he.json, the KB's he.json "skill_desc")
TOP_TIER = 2                # the last job tier shown: the 2nd (the owner: no 3rd job, it isn't in the game)
CLASSES = ("Beginner", "Warrior", "Magician", "Bowman", "Thief")
PART_KEYS = {"desc": "", "lv1": "#1", "max": "#max"}     # a text's translation key: the skill's key + this


@dataclass(frozen=True)
class Skill:
    key: str
    name: str
    job: str
    max_lv: int | None
    picture: Path | None
    desc: str               # the page's own words
    lv1: str                # what it does at level 1 ("MP -3; Damage 15")
    max: str                # ... and at its max level
    prereq: tuple[str, int] | None      # ("Claw Mastery", 5): at least that level on that skill first


class _Pages:
    """What tables._skill reads: a page's lines and the game's availability (tables._Ctx without its map lookups)."""

    def __init__(self, kb):
        self.kb = kb
        self.open = availability.of(kb)

    def lines(self, key: str) -> list[str]:
        return tables.page_lines(self.kb, key)


def _prereq(text: str) -> tuple[str, int] | None:
    """ "At least Level 5 on Claw Mastery" -> ("Claw Mastery", 5)."""
    m = re.fullmatch(r"At least Level (\d+) on (.+)", (text or "").strip())
    return (m.group(2).strip(), int(m.group(1))) if m else None


def class_jobs(kb=None) -> list[tuple[str, list[str]]]:
    """[(class line, its jobs shown)]: Beginner alone, every other class its 1st job and its 2nd jobs."""
    out = []
    for c in CLASSES:
        shown = [j for j, _ in jobs.open_jobs(c, kb) if (jobs.tier(j) or 0) <= TOP_TIER]
        out.append((c, ["Beginner"] if c == "Beginner" else [j for j in shown if j != "Beginner"]))
    return out


def book(kb) -> dict[str, list[Skill]]:
    """job -> its skills in the game, in the skill book's order (the KB's), for every job class_jobs shows. Read once
    per KB object."""
    memo = kb.__dict__.get("_skillbook") if hasattr(kb, "__dict__") else None
    if memo is not None:
        return memo
    wanted = {j for _, js in class_jobs(kb) for j in js}
    ctx = _Pages(kb)
    out: dict[str, list[Skill]] = {j: [] for j in wanted}
    for k, e in kb.entities.items():
        if e.get("category") != "skill" or (e.get("props") or {}).get("Job") not in wanted:
            continue
        try:
            r = tables._skill(ctx, k, e)
        except Exception:          # noqa: BLE001 - a page that doesn't parse is left out, as in the table
            r = None
        if not r or r["job"] not in wanted or not r["desc"]:
            continue
        max_lv = r["max_lv"] if isinstance(r["max_lv"], int) else tables._int(r["max_lv"])
        out[r["job"]].append(Skill(k, r["skill"], r["job"], max_lv, kb.picture(k), r["desc"], r["effect_lv1"],
                                   r["effect"], _prereq(r["prerequisite"])))
    if hasattr(kb, "__dict__"):
        kb.__dict__["_skillbook"] = out
    return out


def text(kb, s: Skill, part: str, lang: str) -> str:
    """A skill's description ("desc") or effect ("lv1", "max") in the player's language: Hebrew while a translation
    made from this very English is there, else the page's English."""
    en = getattr(s, part)
    if lang != "he":
        return en
    return translations.he(getattr(kb, "root", None), KIND, s.key + PART_KEYS[part], en) or en


def texts(s: Skill) -> dict[str, str]:
    """translation key -> English, every text of a skill the tab shows (the ones translate_kb.py and the asset
    cover)."""
    return {s.key + suffix: getattr(s, part) for part, suffix in PART_KEYS.items() if getattr(s, part)}


def default_tab(base_class: str | None, job: str | None, kb=None) -> tuple[str, str]:
    """The class line and job tab to open on: the character's own when it is one shown, else Beginner."""
    if job and job != "Beginner":
        from .sitedata import _JOB_BEFORE
        for c, js in class_jobs(kb):
            if c == base_class or job in js:
                # a later job's tab is the 2nd job it grew out of (a Hermit's is Assassin)
                while job not in js and job in _JOB_BEFORE:
                    job = _JOB_BEFORE[job]
                return c, job if job in js else js[0]
    return "Beginner", "Beginner"
