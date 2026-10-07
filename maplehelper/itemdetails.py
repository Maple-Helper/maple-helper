"""An item's details, read from its NiaMeowDB page in the knowledge base: the stat lines the page shows under its
header ("REQ LEV 10", "W.DEF +44", "Upgrade Slots 7"...), its description, and the "Meow Notes" (NiaMeowDB's own
note on how the item works, then its editors' posts). The item details window (ui/itemview.py) shows them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tables import page_lines

# the header under the name: "Equip · Shield · No. 0917", "Use · Buff · No. 2022", "Cash Equip · Cash Bottom · No. 2000"
_HEAD = re.compile(r"^((?:Cash )?(?:Equip|Use|Etc|Setup|Cash)(?: · .+?)?) · No\. \d+$")
# a post's byline under its "M" avatar: "MeowDB Admin Aug 10, 2026", "... May 12, 2026 edited", "MeowDB Admin 6h ago"
_BYLINE = re.compile(r"^(.+?) ((?:[A-Z][a-z]{2} \d{1,2}, \d{4})|(?:\d+[smhd] ago))( edited)?$")
# the page's sections after the notes: where they end
_AFTER_NOTES = ("Observed Stat Rolls", "Dropped By", "Needed By", "Stacking & conflicts", "Animations", "Cash Shop",
                "MSEA Reference Drops", "Free Market", "Where to buy", "Recipes", "Quests", "Change history")
NOTE_MIN = 40               # a line this short isn't a note's text (a heading, a label, a list's name)
# where the stat lines under the header end: the gender / trade line, or the page's next block
_STATS_END = ("Male", "Female", "Tradeable", "Untradeable", "Weapon Details", "Worn on", "Try on avatar")


@dataclass
class Post:
    author: str
    when: str                       # as the page says it: "Aug 10, 2026", "6h ago"
    text: str


@dataclass
class Details:
    kind: str = ""                  # "Equip · Shield"
    stats: list[str] = field(default_factory=list)       # "REQ LEV 10", "W.DEF +44", "Upgrade Slots 7", ...
    description: str = ""
    trade: str = ""                 # "Tradeable" | "Untradeable" | "" (the page doesn't say)
    about: list[str] = field(default_factory=list)       # NiaMeowDB's own Meow Notes paragraph(s)
    posts: list[Post] = field(default_factory=list)      # the editors' posts under Meow Notes


def details(kb, key: str) -> Details:
    lines = page_lines(kb, key)
    out = Details()
    at = next((n for n, ln in enumerate(lines) if _HEAD.match(ln)), None)
    out.kind = _HEAD.match(lines[at]).group(1) if at is not None else ""
    if at is not None:
        for ln in lines[at + 1:at + 30]:
            if not ln or ln.startswith(_STATS_END):
                break
            out.stats.append(ln)
        out.trade = next((ln for ln in lines[at + 1:at + 40] if ln in ("Tradeable", "Untradeable")), "")
    title = next((n for n, ln in enumerate(lines) if ln.startswith("# ")), None)
    if title is not None:
        # the line under the "# Name" heading, when it is the description (the stat header comes first otherwise)
        first = next((ln for ln in lines[title + 1:] if ln), "")
        if first and not _HEAD.match(first) and first != (kb.get(key) or {}).get("name"):
            out.description = first
    out.about, out.posts = _notes(lines)
    return out


def note_key(key: str, part: str, n: int) -> str:
    """The key a Meow Notes text is translated under: "item/917#about0", "item/917#post1"."""
    return f"{key}#{part}{n}"


def needed_by(kb, key: str) -> tuple[list[dict], list[dict]]:
    """(quests, recipes) that use the item: [{"name", "key", "level", "count"}], [{"name", "key", "skill", "level",
    "count"}], from the knowledge base's own tables (the page's "Needed By")."""
    from . import tables
    quests = [{"name": r["quest"], "key": r["quest_key"], "level": r["quest_level"], "count": r["count"]}
              for r in tables.rows(kb, "quest_reqs") if r.get("target_key") == key and r.get("kind") == "collect"]
    recipes = [{"name": r["product"], "key": r["product_key"], "skill": r["discipline"], "level": r["prof_lv"],
                "count": r["qty"]} for r in tables.rows(kb, "recipes") if r.get("ingredient_key") == key]
    return quests, recipes


def _notes(lines: list[str]) -> tuple[list[str], list[Post]]:
    if "Meow Notes" not in lines:
        return [], []
    i = lines.index("Meow Notes") + 1
    about: list[str] = []
    while i < len(lines) and lines[i] != "M" and lines[i] not in _AFTER_NOTES and len(lines[i]) >= NOTE_MIN:
        about.append(lines[i])
        i += 1
    posts: list[Post] = []
    while i + 2 < len(lines) and lines[i] == "M" and (m := _BYLINE.match(lines[i + 1])):
        i += 2
        text = []
        # a post's text: its lines up to the next post or the next section (a post can run over several lines,
        # with a short label line in it: "Leaf Points (1,000) Exchange Coupon")
        while i < len(lines) and lines[i] != "M" and lines[i] not in _AFTER_NOTES:
            text.append(lines[i])
            i += 1
        while text and len(text[-1]) < NOTE_MIN:
            text.pop()            # a trailing label before the next section is the page's, not the post's
        if text:
            posts.append(Post(m.group(1), m.group(2), "\n".join(text)))
    return about, posts
