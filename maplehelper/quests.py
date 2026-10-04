"""Quests for the player's level, straight from the KB's quest pages: who gives them, what they ask,
what they pay. Done quests are kept per character (Character.quests_done)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import availability

WINDOW_BELOW = 200    # every quest under you that isn't done (a Lv. 13 one skipped at 31 must still show: the
                      # owner); best EXP first, so the old cheap ones sit at the end
WINDOW_ABOVE = 4      # and these coming soon


@dataclass
class Quest:
    key: str
    name: str
    level: int                          # to take it ("Minimum Level"; a page without one: level 1)
    npc: str = ""
    area: str = ""
    exp: int = 0
    mesos: int = 0
    job: str = ""                       # "Beginner only", "Warrior" ... ("" = any)
    afters: list[str] = field(default_factory=list)     # every quest that must be done first ("Quest Complete")
    needs: list[str] = field(default_factory=list)      # "Green Mushroom Cap x 20", "Defeat Blue Snail x 10"
    rewards: list[str] = field(default_factory=list)    # items you surely get (EXP, mesos and fame are separate)
    fame: int = 0
    # "Pick one (class-specific)": the choices per class ("Warrior" -> [...], "Any Class" -> [...]); you pick one
    class_rewards: dict[str, list[str]] = field(default_factory=dict)
    # "Random reward - one of:": the set per class, each with its odds ("Bronze Ore x 7 (16.7%)"); you get one
    random_rewards: dict[str, list[str]] = field(default_factory=dict)
    complete_level: int = 0             # "Level 52+ to complete": taken earlier, finished only from this level
    grade: tuple[str, int] | None = None                # ("Henesys", 9): the citizenship grade it asks
    profession: tuple[str, int] | None = None           # ("Smithing", 5): "Profession Smithing Lv. 5+"
    # a reward that depends on the character's gender, written on the flat Rewards line with the gender before it
    # (pages/quest/10508.md "Male Blue Sauna Robe x 1 Female Red Sauna Robe x 1"): "Male" -> ["Blue Sauna Robe x 1"]
    gender_rewards: dict[str, list[str]] = field(default_factory=dict)
    min_fame: int = 0                   # "Fame 10 +" (pages/quest/10401.md): the Fame it takes to accept
    accept_cost: int = 0                # "Pay 1,000 mesos to accept." (pages/quest/10303.md)
    # any other pre-requisite line, kept word for word so it is never lost ("Must not already have: ...")
    notes: list[str] = field(default_factory=list)
    self_start: bool = False            # "Self-Starting": it opens on its own, no NPC hands it out
    task: str = ""                      # what to do, the game's quest journal ("Arthur asked me to greet Rina ...")

    def matches(self, query: str) -> bool:
        """The quest search: every word of the query in its name, NPC, area, what it asks or what it gives."""
        text = " ".join([self.name, self.npc, self.area, *self.needs, *self.rewards,
                         *(r for rs in self.class_rewards.values() for r in rs),
                         *(r for rs in self.gender_rewards.values() for r in rs)]).lower()
        return all(w in text for w in query.lower().split())

    @property
    def after(self) -> str:
        """The quests to do first, as one line ("A, B")."""
        return ", ".join(self.afters)

    def _for_class(self, table: dict[str, list[str]], base_class: str) -> list[str]:
        own = table.get(base_class, []) if base_class else []
        return own + [x for x in table.get("Any Class", []) if x not in own]

    def rewards_pick(self, base_class: str) -> list[str]:
        """The class-specific reward choices for this class, and the ones for any class (pick one)."""
        return self._for_class(self.class_rewards, base_class)

    def rewards_random(self, base_class: str) -> list[str]:
        """The random reward set for this class, and the one for any class (you get one of them)."""
        return self._for_class(self.random_rewards, base_class)

    def rewards_gender(self, t) -> list[str]:
        """The gender-dependent rewards, each marked with the gender that gets it (the profile has no gender)."""
        return [t(f"q_gender_{g}", item=item) for g, items in self.gender_rewards.items() for item in items]

    def prereq_hints(self, t) -> list[str]:
        """The pre-requisites the card lists beside grade and profession: Fame, a fee to accept, and any line
        the parser has no field for (kept as the KB writes it)."""
        out = []
        if self.min_fame:
            out.append(t("q_min_fame", n=self.min_fame))
        if self.accept_cost:
            out.append(t("q_accept_cost", n=f"{self.accept_cost:,}"))
        return out + self.notes

    def opens_at(self) -> int:
        """The level it can be done at: to take it, and to complete it."""
        return max(self.level, self.complete_level)


def _section(lines: list[str], head: str, stops=("Pre-requisites", "Requirements", "Rewards", "Description",
                                                  "On Acceptance", "Random reward - one of:")) -> list[str]:
    if head not in lines:
        return []
    out = []
    for ln in lines[lines.index(head) + 1:]:
        if ln in stops:
            break
        if ln:
            out.append(ln)
    return out


# "Defeat Dark Axe Stump x 100 Dexterity Potion x 5": each name runs up to its own " x <count>"
# (a lowercase x inside a name, "Axe" or "Dexterity", is part of the name)
_ITEM = re.compile(r"((?:Defeat |Collect )?\S.*?) x ([\d,]+)(?!\S)")
# a random reward with its odds: "Bronze Ore x 7 16.7 %"
_ODDS_ITEM = re.compile(r"(\S.*?) x ([\d,]+) ([\d.]+) %")
# the class headers inside a reward block ("Any Class" and "Beginner" too)
CLASSES = ("Warrior", "Magician", "Bowman", "Thief", "Pirate", "Beginner", "Any Class")
_GRADE = re.compile(r"^(.+?): Citizenship grade (\d+)\s*")
_COMPLETE_LV = re.compile(r"Level (\d+)\+ to complete")
_PROFESSION = re.compile(r"^Profession (\w+) Lv\. (\d+)\+")
_FAME = re.compile(r"^Fame ([\d,]+) ?\+$")                       # "Fame 10 +"
_ACCEPT_COST = re.compile(r"^Pay ([\d,]+) mesos to accept\.?$")    # "Pay 1,000 mesos to accept."
_MIN_LEVEL = re.compile(r"^Level Lv\. \d+\+$")                   # the level line (props "Minimum Level" has it)
GENDERS = ("Male", "Female")


def _split_owner(kb, item: str) -> tuple[str, str] | None:
    """A flat Rewards item that names its class or gender first ("Warrior Dark Knuckle x 1", "Male Blue Sauna
    Robe x 1", pages/quest/10007.md and 10508.md): (owner, "Dark Knuckle x 1"), when the rest is a KB item and the
    whole name is not one ("Warrior Potion" is an item of its own)."""
    owner, _, rest = item.partition(" ")
    if owner not in GENDERS and (owner not in CLASSES or owner == "Any Class"):
        return None
    name = rest.rsplit(" x ", 1)[0].strip().lower()
    items = getattr(kb, "_item_by_name", {})
    if name in items and item.rsplit(" x ", 1)[0].strip().lower() not in items:
        return owner, rest
    return None


def _rewards_by_class(lines: list[str], head: str, parse) -> dict[str, list[str]]:
    """A "Pick one (class-specific):" / "Random reward - one of:" block: class header lines, then their items."""
    out: dict[str, list[str]] = {}
    if head not in lines:
        return out
    cls = "Any Class"
    for ln in lines[lines.index(head) + 1:]:
        if ln in CLASSES:
            cls = ln
            continue
        items = parse(ln)
        if not items:
            break                    # the next section ("Description", "On Acceptance", ...)
        out.setdefault(cls, []).extend(items)
    return out


@lru_cache(maxsize=1024)
def _quest(kb, key: str) -> Quest | None:
    e = kb.get(key)
    if not e or e.get("category") != "quest":
        return None
    p = e.get("props") or {}
    lv = p.get("Minimum Level")
    if not isinstance(lv, (int, float)):
        lv = 1                       # no level line ("Bringing a Mirror to Heena"): anyone can take it
    lines = [ln.strip() for ln in kb.page(key).split("\n---", 2)[-1].splitlines()]
    q = Quest(key, e["name"], int(lv), str(p.get("NPC") or ""), str(p.get("Area") or ""),
              int(p.get("EXP Reward") or 0), int(p.get("Meso Reward") or 0))
    for ln in _section(lines, "Pre-requisites"):
        # "Henesys: Citizenship grade 5 Level 32+ to complete Quest Complete First Greeting with Chief Stan"
        g = _GRADE.match(ln)
        if g:
            q.grade = (g.group(1), int(g.group(2)))
            ln = ln[g.end():]
        c = _COMPLETE_LV.search(ln)
        if c:
            q.complete_level = int(c.group(1))
            ln = ln[c.end():].strip()
        pr = _PROFESSION.match(ln)
        fame, cost = _FAME.match(ln), _ACCEPT_COST.match(ln)
        if ln.startswith("Job "):
            q.job = ln[4:].strip()
        elif ln.startswith("Quest Complete "):
            q.afters.append(ln[len("Quest Complete "):].strip())
        elif pr:
            q.profession = (pr.group(1), int(pr.group(2)))
        elif fame:
            q.min_fame = int(fame.group(1).replace(",", ""))
        elif cost:
            q.accept_cost = int(cost.group(1).replace(",", ""))
        elif ln and not _MIN_LEVEL.match(ln):
            q.notes.append(ln)
    for ln in _section(lines, "Requirements"):
        q.needs += [f"{name.strip()} x {n}" for name, n in _ITEM.findall(ln)] or [ln]
    head = lines[:lines.index("Pre-requisites")] if "Pre-requisites" in lines else lines[:12]
    q.self_start = "Self-Starting" in head
    # the journal's step that says what to do ("02 ..."), else the description's first line ("01 ...")
    for ln in lines:
        if ln.startswith("Quest journal"):
            m = re.search(r"\b02 (.+?)$", ln)
            if m:
                q.task = m.group(1).strip()
            break
    if not q.task:
        first = next((ln for ln in lines if ln.startswith("01 ")), "")
        q.task = first[3:].strip()
    q.task = re.sub(r"^(?:⌄\s*)?(?:\d\d\s+)?", "", q.task).strip()      # "⌄ 02 I met Heena": the page's markers
    pick = False                     # inside "Pick one (class-specific):"
    for ln in _section(lines, "Rewards"):
        fame = re.search(r"\+ ?(\d+) Fame", ln)
        if fame:
            q.fame = int(fame.group(1))
        if re.fullmatch(r"([\d,]+ EXP)?\s*([\d,]+ Mesos)?\s*(\+ ?\d+ Fame)?", ln):
            continue
        if ln.startswith("Pick one (class-specific)"):
            pick = True
            break
        for item in (f"{name.strip()} x {n}" for name, n in _ITEM.findall(ln)):
            owned = _split_owner(kb, item)
            if not owned:
                q.rewards.append(item)
            elif owned[0] in GENDERS:
                q.gender_rewards.setdefault(owned[0], []).append(owned[1])
            else:                    # the class's own item: shown only to that class, under "pick one"
                q.class_rewards.setdefault(owned[0], []).append(owned[1])
    if pick:
        picked = _rewards_by_class(
            lines, next(ln for ln in lines if ln.startswith("Pick one (class-specific)")),
            lambda ln: [f"{name.strip()} x {n}" for name, n in _ITEM.findall(ln)])
        for cls, items in picked.items():
            q.class_rewards.setdefault(cls, []).extend(items)
    q.random_rewards = _rewards_by_class(
        lines, "Random reward - one of:",
        lambda ln: [f"{name.strip()} x {n} ({pct}%)" for name, n, pct in _ODDS_ITEM.findall(ln)])
    return q


_TASKS_HE: dict | None = None


def task_text(q: Quest, lang: str) -> str:
    """What to do, in the player's language: the Hebrew of assets/quest_tasks/he.json while its English is still
    the page's (a changed journal line falls back to the English until it is translated again)."""
    global _TASKS_HE
    if lang != "he" or not q.task:
        return q.task
    if _TASKS_HE is None:
        path = Path(__file__).resolve().parent.parent / "assets" / "quest_tasks" / "he.json"
        try:
            _TASKS_HE = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _TASKS_HE = {}
    row = _TASKS_HE.get(q.key) or {}
    return row.get("he") if row.get("en") == q.task and row.get("he") else q.task


def quest(kb, key: str) -> Quest | None:
    return _quest(kb, key)


def job_fits(q: Quest, base_class: str, job: str) -> bool:
    beginner = base_class == "Beginner" or (job or "") == "Beginner"
    # Maple Island is behind a one-way boat: once a job is taken its quests can't be done (one of them stayed in
    # the list of a Lv. 31 Assassin's skipped quests, the owner)
    if q.area == "Maple Island" and base_class and not beginner:
        return False
    if not q.job:
        return True
    j = q.job.lower()
    if "beginner" in j:
        # a character still a Beginner, whatever class they plan (the profile's class can be set ahead)
        return beginner
    return base_class.lower() in j or (job or "").lower() in j


def craft_fits(q: Quest, crafts: dict | None) -> bool:
    """A "Profession X Lv. N+" quest: the character's level in that profession reaches N (Character.crafts)."""
    if not q.profession or crafts is None:
        return True
    name, lv = q.profession
    return int(crafts.get(name.lower(), 0) or 0) >= lv


def for_level(kb, level: int, base_class: str = "", job: str = "", done: list[str] | None = None,
              crafts: dict | None = None) -> dict:
    """{"now": quests you can take (best EXP first), "level": those opening at this level, "missed": those from
    earlier levels not done, "soon": unlocking at the next level, "later": every one after it,
    "town": the citizenship donations (repeatable, 100 items each), "done": count}.

    A quest finished only from a higher level ("Level 52+ to complete") counts at that level; one that asks a
    profession level the character doesn't have (crafts given) is left out."""
    done_set = set(done or [])
    now, future, town = [], [], []
    open_ = availability.of(kb)
    for k, e in kb.entities.items():
        # only quests the KB confirms are in the game: none in Ossyria, no event the KB marks "Ended"
        if e.get("category") != "quest" or not open_.quest_open(k):
            continue
        q = quest(kb, k)
        if not q or (base_class and not job_fits(q, base_class, job)) or not craft_fits(q, crafts):
            continue
        if k in done_set:
            continue
        lv = q.opens_at()
        if level - WINDOW_BELOW <= lv <= level:
            (town if q.area == "Citizenship" else now).append(q)
        elif lv > level and q.area != "Citizenship":
            future.append(q)
    now.sort(key=lambda q: (-q.exp, q.level))
    future.sort(key=lambda q: (q.opens_at(), -q.exp))
    # the play tools' tabs (the owner's): the next level alone is "coming up", every level after it "later"; one
    # level up, each list moves along (32's become this level's, 33's come up next)
    soon = [q for q in future if q.opens_at() == level + 1]
    later = [q for q in future if q.opens_at() > level + 1]
    town.sort(key=lambda q: -q.exp)
    # the play tools' two lists (the owner's): the quests that open at this very level, and every one from a level
    # before it that isn't done, newest level first (a Lv. 31 quest left undone moves there at 32)
    at_level = [q for q in now if q.opens_at() == level]
    missed = sorted((q for q in now if q.opens_at() < level), key=lambda q: (-q.opens_at(), -q.exp))
    return {"now": now, "level": at_level, "missed": missed, "soon": soon, "later": later, "town": town,
            "done": len(done_set)}


def closed_areas(kb) -> list[str]:
    """The areas whose quests the KB doesn't confirm are in the game yet ("El Nath"), most quests first."""
    from collections import Counter
    open_ = availability.of(kb)
    found = Counter()
    for k, e in kb.entities.items():
        if e.get("category") == "quest" and not open_.quest_open(k):
            q = quest(kb, k)
            if q and q.area and "event" not in q.area.lower():      # an ended event is no area to open
                found[q.area] += 1
    return [a for a, _ in found.most_common()]


# ------------------------------------------------------------------ citizenship

TOWNS = ("Henesys", "Kerning City")          # the towns with citizenship (their donation boards in the KB)


def town_of(kb, q: Quest) -> str:
    """The citizenship town a quest belongs to: the town whose citizenship grade it requires, its board's town,
    the town its name invites you to (the openers, "To Henesys, the Prairie Town" pages/quest/506000.md, and
    "To the Gray City, Kerning City" 506100.md, handed out by Henesys' Arthur), else where its NPC stands
    (Jake and Mr. Goldstein's pages name no town, their quests ask Kerning's grade)."""
    if q.grade and q.grade[0] in TOWNS:
        return q.grade[0]
    m = re.search(r"\((.+)\)", q.npc or "")
    if m and m.group(1) in TOWNS:
        return m.group(1)
    named = [t for t in TOWNS if re.search(rf"\b{re.escape(t)}\b", q.name)]
    if len(named) == 1:
        return named[0]
    key = kb._npc_by_name.get((q.npc or "").lower())
    page = kb.page(key) if key else ""
    hits = [(page.find(t), t) for t in TOWNS if t in page]
    return min(hits)[1] if hits else ""


def citizenship(kb, town: str, level: int, done: list[str] | None = None) -> list[Quest]:
    """The town's citizenship quests (donations and the rest) you can do now, best EXP first
    (one you can take but only complete at a higher level, "Level 52+ to complete", waits for that level)."""
    done_set = set(done or [])
    out = []
    open_ = availability.of(kb)
    for k, e in kb.entities.items():
        if e.get("category") != "quest" or k in done_set or not open_.quest_open(k):
            continue
        q = quest(kb, k)
        if q and q.area == "Citizenship" and q.opens_at() <= level and town_of(kb, q) == town:
            out.append(q)
    return sorted(out, key=lambda q: (-q.exp, q.level))
