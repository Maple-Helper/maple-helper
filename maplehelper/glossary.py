"""Game terms explained: a small "?" beside a term shows what it means (hover or click).

Definitions come from the KB's "MapleStory Classic World Glossary" guide (shipped in English and Hebrew,
assets/guides) with a few clearer ones written for the app. Only the terms below get a "?" in running
text; slang from the glossary ("gg", "rip") would mark ordinary words.
"""
from __future__ import annotations

import html
import json
import re
from functools import lru_cache

from . import bidi
from .store import ASSETS

GLOSSARY = "maplestory-classic-glossary"

# term shown in the app -> the glossary entry it uses (or an app definition below).
# Everyday words players already know (Lv., EXP) get no "?" (the player asked for that)
TERMS = {
    "HP": "HP / MP", "MP": "HP / MP", "ACC": "ACC", "Accuracy": "ACC", "Avoid": "Avoid",
    "AVOID": "Avoid", "P.DEF": "P.DEF", "PDEF": "P.DEF", "M.DEF": "M.DEF", "MDEF": "M.DEF",
    "W.ATK": "WATK / W.ATK", "WATK": "WATK / W.ATK", "M.ATK": "Magic / MATK", "AP": "AP", "SP": "SP",
    "STR": "STR / DEX / INT / LUK", "DEX": "STR / DEX / INT / LUK", "INT": "STR / DEX / INT / LUK",
    "LUK": "STR / DEX / INT / LUK", "mesos": "mesos", "NPC": "NPC", "Free Market": "FM", "KPQ": "kPQ",
    "KS": "KS", "Citizenship": "Citizenship", "grind": "grind", "buff": "buff",
    "Booster": "booster", "Mastery": "mastery", "Critical Rate": "crit", "scroll": "scroll", "mob": "mob",
    "catalyst": "catalyst", "Training Advisor": "Training Advisor",
}

# clearer or missing definitions, written for the app
APP = {
    # a monster's own numbers (the calculator's big numbers): the plain "HP" entry is the player's HP / MP
    "Monster P.DEF": ("ההגנה של המפלצת מפני מכות פיזיות: מורידה מהנזק שאתם עושים לה במכות רגילות (לא בקסמים). "
                      "0 = לא מורידה כלום.",
                      "The monster's defense against physical hits: it lowers the damage your basic attacks do to it "
                      "(not magic). 0 = nothing off."),
    "Monster HP": ("כמה נזק צריך כדי להרוג את המפלצת: כל מכה מורידה ממנו, וכשהוא מגיע ל-0 היא מתה.",
                   "How much damage the monster takes before it dies: every hit takes some off, and at 0 it dies."),
    # "a bit over 3x": the class guides' "ACC to never miss at equal level" tables (pages/guide/cleric-class-guide.md:
    # Jr. Wraith Avoid 24 -> 79 ACC, Rotten Mushroom 33 -> 108) and Zombie Mushroom's page (Avoid 14 -> 47)
    "ACC": ("Accuracy: כמה טוב אתם פוגעים. ככל שה-ACC שלכם גבוה יותר מה-Avoid של המפלצת, אתם מפספסים פחות. "
            "כדי לא לפספס בכלל צריך קצת יותר מפי 3 מה-Avoid שלה כשהיא ברמה שלכם, ועוד יותר כשהיא ברמה גבוהה משלכם. "
            "רואים אותו בחלון ה-Stat.",
            "Accuracy: how well you hit. The more your ACC beats a monster's Avoid, the fewer misses. "
            "To never miss you need a bit over 3x its Avoid at your level, more when it's above your level. "
            "It's in the Stat window."),
    "Avoid": ("Avoidability: כמה טוב המפלצת מתחמקת. Avoid גבוה = צריך יותר ACC כדי לפגוע בה. "
              "מפלצת ברמה גבוהה משלכם מתחמקת עוד יותר.",
              "Avoidability: how well a monster dodges. Higher Avoid means you need more ACC to hit it. "
              "A monster above your level dodges even more."),
    "P.DEF": ("Physical Defense: הגנה מפני מכות פיזיות. מורידה מהנזק של מכות רגילות (לא קסמים).",
              "Physical Defense: cuts the damage of physical hits (not magic)."),
    "M.DEF": ("Magic Defense: הגנה מפני קסמים. מורידה מהנזק של מכות קסם.",
              "Magic Defense: cuts the damage of magic hits."),
    "AP": ("Ability Points: 5 נקודות בכל עליית רמה, שמחלקים ל-STR / DEX / INT / LUK.",
           "Ability Points: 5 per level up, spent on STR / DEX / INT / LUK."),
    # pages/guide/beginners-guide-first-steps-in-maple-world.md ("9 SP by the time you hit level 10 (1 per level-up)")
    # and pages/guide/maplestory-classic-glossary.md ("3 SP per level, plus 1 bonus SP at lv 10 advancement")
    "SP": ("Skill Points: נקודות לסקילים. Beginner מקבל נקודה אחת בכל עליית רמה (9 עד רמה 10), "
           "ואחרי הג'וב הראשון מקבלים 3 בכל רמה.",
           "Skill Points: points for your skills. A Beginner gets 1 per level up (9 by level 10), "
           "then 3 per level after the first job."),
    "NPC": ("דמות של המשחק (לא שחקן): חנויות, נותני קווסטים ומדריכי ג'וב.",
            "A character run by the game (not a player): shops, quest givers, job instructors."),
    "Citizenship": ("אזרחות בעיר (Henesys או Kerning City) מרמה 12. תרומות מעלות דרגה, שפותחת הנחות ופריטים בחנויות העיר.",
                    "Citizenship of a town (Henesys or Kerning City) from Lv. 12. Donations raise your grade, "
                    "which opens discounts and items in that town's shops."),
    "Lv.": ("Level: הרמה של הדמות או של המפלצת. כל עליית רמה נותנת AP ו-SP.",
            "Level: of your character or a monster. Every level up gives AP and SP."),
    "mob": ("מפלצת (קיצור של mobile). \"מובים\" = מפלצות.", "A monster (short for mobile)."),
    "catalyst": ("ה-mesos שמשלמים כדי ליצור את הפריט, מעבר לחומרים.", "The mesos paid to craft, on top of the materials."),
    "Training Advisor": ("כלי של NiaMeowDB שממליץ על מפות אימון לפי הדמות והבילד.",
                         "NiaMeowDB's tool that recommends training maps for your character and build."),
}


def _clean(term: str) -> str:
    return re.sub(r"\*\*|\s*\(.*?\)", "", term).strip()


@lru_cache(maxsize=2)
def _book(lang: str) -> dict[str, str]:
    """Glossary entry name -> definition, in one language (English when there's no translation)."""
    out = {}
    try:
        en = json.loads((ASSETS / "guides" / "en" / f"{GLOSSARY}.json").read_text(encoding="utf-8"))
        local = en
        if lang != "en":
            local = json.loads((ASSETS / "guides" / lang / f"{GLOSSARY}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    same = len(local.get("blocks", [])) == len(en.get("blocks", []))
    for i, b in enumerate(en.get("blocks", [])):
        if "table" not in b:
            continue
        lt = local["blocks"][i].get("table") if same else None
        for j, row in enumerate(b["table"][1:], start=1):
            if len(row) < 2:
                continue
            meaning = lt[j][1] if lt and j < len(lt) and len(lt[j]) > 1 else row[1]
            whole = _clean(row[0])
            out[whole] = meaning
            for part in re.split(r"\s*/\s*", whole):     # "FM / Free Market" answers to both
                out.setdefault(part, meaning)
    return out


def explain(term: str, lang: str) -> str | None:
    """What a game term means, in the player's language; None when it isn't a known term."""
    entry = TERMS.get(term, term)
    if entry in APP:
        he, en = APP[entry]
        return en if lang == "en" else he
    book = _book(lang)
    text = book.get(entry) or book.get(term)
    return re.sub(r"\*\*", "", text) if text else None


# what follows a term: ui/terms.py swaps in an orange "?" badge image once Qt runs; text fallback until then
MARK = "<b style='font-size:large;'>&nbsp;?</b>"

_PATTERN = re.compile(r"(?<![\w.])(" + "|".join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True)) + r")(?![\w])")
# a term with its label colon and value ("HP: 7,420"): the "?" goes after the value, never between the label and it
# ("HP ?: 7420", "HP: ? 7420": the instant answer, CHAT-12)
_VALUE = r"\d(?:[\d,.]*\d)?%?"
_TERM_COLON = re.compile(_PATTERN.pattern + rf"(:(?:[ \u00a0]?{_VALUE})?)?")
# the same in a Hebrew line, where the label and the value are two blocks (bidi.isolate_ltr_runs)
_BLOCK_VALUE = re.compile(rf"{bidi.RLM}?:[ \u00a0]?{bidi.LRE}{_VALUE}{bidi.PDF}{bidi.RLM}?")


def annotate(html_text: str, lang: str, color: str = "#F07A12", seen: set | None = None, limit: int = 6) -> str:
    """Put a small "?" link after the first appearance of each known term in an HTML text.
    Links are "g:<term>"; the widget shows explain(term) on hover / click. Tags are left alone."""
    seen = set() if seen is None else seen
    count = [0]

    def link(term: str) -> str:
        return (f"<a href='g:{html.escape(term)}' style='color:{color}; text-decoration:none;'>"
                f"&nbsp;{MARK}</a>")

    def wanted(term: str) -> bool:
        key = TERMS.get(term, term)
        if key in seen or count[0] >= limit or explain(term, lang) is None:
            return False
        seen.add(key)
        count[0] += 1
        return True

    def text_part(part: str) -> str:
        # an English block inside Hebrew (LRE ... PDF, see bidi.py) must stay whole: a link in its middle
        # breaks the embedding ("Avoid 14" showed as "14 Avoid"), so its "?" go right after the block
        out, pos = [], 0
        def sub(m):
            whole = m.group(1) + (m.group(2) or "")
            return whole + link(m.group(1)) if wanted(m.group(1)) else whole

        # a KB name kept whole (LRI ... PDI: "Bottomwear HP Scroll: Chaos") is a name, not a use of its terms
        for run in re.finditer(f"{bidi.LRE}(.*?){bidi.PDF}|{bidi.LRI}.*?{bidi.PDI}", part, re.S):
            out.append(_TERM_COLON.sub(sub, part[pos:run.start()]))
            marks = "".join(link(m.group(1)) for m in _PATTERN.finditer(run.group(1) or "") if wanted(m.group(1)))
            value = _BLOCK_VALUE.match(part, run.end()) if marks else None
            out.append(run.group(0) + (value.group(0) if value else "") + marks)
            pos = value.end() if value else run.end()
        out.append(_TERM_COLON.sub(sub, part[pos:]))
        return "".join(out)

    # only text between tags, never inside a tag or an existing link
    parts = re.split(r"(<a\b.*?</a>|<[^>]+>)", html_text, flags=re.S)
    return "".join(p if p.startswith("<") else text_part(p) for p in parts)


# a term's title in its explanation, when the term isn't the word shown ("Monster HP" explains an "HP")
TITLES = {"Monster HP": "HP", "Monster P.DEF": "P.DEF"}


def term_of(link: str) -> str | None:
    return link[2:] if link.startswith("g:") else None
