"""The MapleStory Classic job tree, and turning what the AI read off the HUD into a consistent class + job."""
from __future__ import annotations

import re

# base class -> [(job, min level)], from the KB: the 1st job at level 10 for every class, Magician too
# (pages/guide/maplestory-classic-glossary.md: "The first real job you pick at level 10: Warrior, Magician, Bowman,
# Thief"), the 2nd job at 30 (pages/class/<class>.md: "... branch into ... at level 30"), the 3rd at 70
# (pages/class/bowman.md: "Bowmen advance again at level 70"). tests/test_data_fixes.py and tests/test_official.py
# check this against the KB and the official facts.
JOBS = {
    "Beginner": [("Beginner", 1)],
    "Warrior": [("Beginner", 1), ("Warrior", 10), ("Fighter", 30), ("Page", 30), ("Spearman", 30),
                ("Crusader", 70), ("White Knight", 70), ("Dragon Knight", 70)],
    "Magician": [("Beginner", 1), ("Magician", 10), ("F/P Wizard", 30), ("I/L Wizard", 30), ("Cleric", 30),
                 ("F/P Mage", 70), ("I/L Mage", 70), ("Priest", 70)],
    "Bowman": [("Beginner", 1), ("Bowman", 10), ("Hunter", 30), ("Crossbowman", 30), ("Ranger", 70), ("Sniper", 70)],
    "Thief": [("Beginner", 1), ("Thief", 10), ("Assassin", 30), ("Bandit", 30), ("Hermit", 70), ("Chief Bandit", 70)],
}
# Hebrew names, as Israeli players say them (the 1st jobs as the class cards translate them, the rest spelled
# out: "פייטר", "קלריק"). Only read, in a question (kb aliases, planner): the app shows and keeps the English
# name the game uses (the owner: job names in English).
JOB_HE = {"Beginner": "ביגינר", "Warrior": "לוחם", "Magician": "קוסם", "Bowman": "קשת", "Thief": "גנב",
          "Fighter": "פייטר", "Page": "פייג'", "Spearman": "ספירמן", "Crusader": "קרוסיידר",
          "White Knight": "וייט נייט", "Dragon Knight": "דרגון נייט", "F/P Wizard": "ויזארד אש ורעל",
          "I/L Wizard": "ויזארד קרח וברק", "Cleric": "קלריק", "F/P Mage": "מייג' אש ורעל",
          "I/L Mage": "מייג' קרח וברק", "Priest": "פריסט", "Hunter": "האנטר", "Crossbowman": "קרוסבואומן",
          "Ranger": "ריינג'ר", "Sniper": "סנייפר", "Assassin": "אסאסין", "Bandit": "בנדיט", "Hermit": "הרמיט",
          "Chief Bandit": "צ'יף בנדיט"}


def job_label(job: str, lang: str) -> str:
    """How a job is shown: the game's English name in both languages, as everywhere else in the app ("Fighter")."""
    return job


# other names for the same job: older clients and servers print these on the HUD (an Old School HUD says "Archer"),
# and players type them ("FP Wizard", "Bowmen"); keys are compared lowercase without punctuation (_key)
ALIASES = {"archer": "Bowman", "bowmen": "Bowman", "swordman": "Warrior", "swordsman": "Warrior", "rogue": "Thief",
           "mage": "Magician", "wizard": "Magician", "crossbow man": "Crossbowman", "crossbowmen": "Crossbowman",
           "spear man": "Spearman", "fire poison wizard": "F/P Wizard", "wizard fire poison": "F/P Wizard",
           "fp wizard": "F/P Wizard", "ice lightning wizard": "I/L Wizard", "wizard ice lightning": "I/L Wizard",
           "il wizard": "I/L Wizard", "fire poison mage": "F/P Mage", "fp mage": "F/P Mage",
           "ice lightning mage": "I/L Mage", "il mage": "I/L Mage",
           # the KB writes "Fire/Poison Wizard" ("/" is dropped by _key), a HUD may run the words together
           "firepoison wizard": "F/P Wizard", "icelightning wizard": "I/L Wizard", "firepoison mage": "F/P Mage",
           "icelightning mage": "I/L Mage", "mage fire poison": "F/P Mage", "mage ice lightning": "I/L Mage",
           "whiteknight": "White Knight", "chiefbandit": "Chief Bandit"}


def _key(name: str) -> str:
    """'Wizard (Fire,Poison)' -> 'wizard fire poison', 'F/P Wizard' -> 'fp wizard': no punctuation or brackets."""
    n = str(name).lower().replace("/", "")
    return " ".join(re.sub(r"[^\w\s]", " ", n).split())


_JOB_KEYS = {_key(job): job for jobs in JOBS.values() for job, _ in jobs}
_ALIAS_KEYS = {_key(a): v for a, v in ALIASES.items()}


def canonical_job(name: str) -> str | None:
    """'Archer' -> 'Bowman', 'assassin' -> 'Assassin', 'Wizard (Fire,Poison)' -> 'F/P Wizard';
    None when it's no job of the tree."""
    n = _key(name)
    n = _key(_ALIAS_KEYS.get(n, n))
    return _JOB_KEYS.get(n)


def class_of(job: str) -> str | None:
    """The base class a job belongs to (Beginner belongs to every class: None)."""
    if job == "Beginner":
        return None
    return next((c for c, jobs in JOBS.items() if any(j == job for j, _ in jobs)), None)


def _line(job: str) -> tuple[str, int, int] | None:
    """(class, tier 1-3, branch) of a job: Assassin -> ("Thief", 2, 0), Hermit -> ("Thief", 3, 0)."""
    for c, jobs in JOBS.items():
        if c == "Beginner":
            continue
        levels = sorted({lv for _, lv in jobs if lv > 1})
        for j, lv in jobs:
            if j == job and lv > 1:
                tier = levels.index(lv) + 1
                return c, tier, [x for x, y in jobs if y == lv].index(j)
    return None


def tier(job: str | None) -> int | None:
    """Which advancement a job is: Beginner 0, Thief 1, Assassin 2, Hermit 3; None for no job of the tree."""
    if job == "Beginner":
        return 0
    line = _line(job or "")
    return line[1] if line else None


def advances(old: str | None, new: str | None) -> bool:
    """True when `new` is the character's job `old` or a later job of the same line (Thief -> Assassin -> Hermit,
    Fighter -> Crusader, never Fighter -> White Knight). Beginner, the 1st job again, or the other 2nd job of the
    class are no advancement: a misread HUD made an Assassin a Beginner, asked nothing (audit AI-2)."""
    if not new or not old or new == old or old == "Beginner":
        return True
    a, b = _line(old), _line(new)
    if not a or not b:
        return new != "Beginner"
    if a[0] != b[0] or b[1] <= a[1]:
        return False
    # the 1st job may go to any branch; a 2nd job only to its own 3rd (the lists keep the branches in one order)
    return a[1] == 1 or a[2] == b[2]


def canonical_class(name: str) -> str | None:
    n = _key(name)
    n = _key(_ALIAS_KEYS.get(n, n))
    return next((c for c in JOBS if c.lower() == n), None) or class_of(canonical_job(name) or "")


def first_job(base_class: str, level: int) -> str:
    """The job a character of this class has at least: its 1st job once the level allows it, else Beginner."""
    jobs = JOBS.get(base_class, [])
    return jobs[1][0] if len(jobs) > 1 and level >= jobs[1][1] else "Beginner"


def open_tier(kb=None) -> int:
    """How far the job tree is open in the game: up to the 2nd job until the KB confirms 3rd job
    (availability.py reads it from the release guide). With no KB at hand, only what is always there."""
    if kb is None:
        return 2
    from . import availability
    return availability.of(kb).job_tier


def tier_levels(base_class: str, kb=None) -> list[int]:
    """The levels of the class's advancements, open ones only: [1, 10, 30] while 3rd job isn't out."""
    levels = sorted({lv for _, lv in JOBS.get(base_class, [])})
    return levels[:open_tier(kb) + 1]


def open_jobs(base_class: str, kb=None) -> list[tuple[str, int]]:
    """The class's jobs that are in the game, with their levels (no 3rd job until the KB confirms it)."""
    levels = set(tier_levels(base_class, kb))
    return [(j, lv) for j, lv in JOBS.get(base_class, []) if lv in levels]
