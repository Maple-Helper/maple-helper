"""A local query planner: a list, filter or reverse question turned into exact lookups in the KB tables (tables.py),
and every matching row put into the prompt.

"Which quests give capes", "gloves a Lv. 30 Thief can wear", "what lives in Ant Tunnel", "who sells arrows": with no
pre-fetch for them the AI grepped and read pages, 6-39 tool calls and 20 s to 4 minutes, and its answers still
missed items or named ones not in the game. Here the question's intent (quest rewards, shops, recipes, spawns,
training maps, equipment, monsters by EXP, quests, skills, scrolls, potions) and its slots (an item family or slot,
a level or a range, a job, the entities it names) pick a table and its filter; the rows go into the prompt in a
<table_rows> block marked complete when it holds every match, so the AI only phrases the answer.

Precision first: a question that fits no intent, or one the app already answers another way (a monster's drops, a
screenshot, one named entity's details), gets nothing; the AI still has the tables to grep.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import combat, jobs, plan, tables

HE = "א-ת"
CAP = 60            # every row up to this many: the answer must be complete
SHOWN = 40          # above CAP: the best this many, and how to grep the rest
CELL = 110          # a long text cell (a quest's rewards, a skill's effect) cut here
CUT = {"rewards", "effect", "after"}     # the cells cut: what a list question doesn't ask for
NEAR_SHARE = 0.6    # a training map: this much of its spawns within the level range
TRAIN_CAP = 25      # training maps: the best this many (exp_hr): the rest are worse by the sort
DANGER_ABOVE = 10   # a training map left out: a monster this many levels over the range's top ...
DANGER_SHARE = 0.05  # ... at least this much of its spawns


def _he(words: str) -> str:
    """Hebrew words with up to two glued prefix letters ("לגלימות", "והכפפות"), whole words only."""
    return rf"(?<![{HE}])[והבלמשכ]{{0,2}}(?:{words})(?![{HE}])"


def _en(words: str) -> str:
    return rf"\b(?:{words})\b"


def _rx(he: str, en: str) -> re.Pattern:
    return re.compile(_he(he) + "|" + _en(en), re.I)


WEAPONS = ("One-Handed Sword", "Two-Handed Sword", "One-Handed Axe", "Two-Handed Axe", "One-Handed Blunt Weapon",
           "Two-Handed Blunt Weapon", "Spear", "Polearm", "Bow", "Crossbow", "Claw", "Dagger", "Wand", "Staff")
MAGIC_WEAPONS = ("Wand", "Staff")
CLASS_WEAPONS = {"Warrior": {"One-Handed Sword", "Two-Handed Sword", "One-Handed Axe", "Two-Handed Axe",
                             "One-Handed Blunt Weapon", "Two-Handed Blunt Weapon", "Spear", "Polearm"},
                 "Magician": {"Wand", "Staff"}, "Bowman": {"Bow", "Crossbow"}, "Thief": {"Claw", "Dagger"}}
# a 2nd (or 3rd) job's own weapons: sorted by W.ATK a dagger always beat a claw (stars add a claw's damage), so an
# Assassin was told to use a Reef Claw dagger and a Fighter a Mithril Pole Arm
_SWORDS = {"One-Handed Sword", "Two-Handed Sword"}
JOB_WEAPONS = {"Fighter": _SWORDS | {"One-Handed Axe", "Two-Handed Axe"},
               "Page": _SWORDS | {"One-Handed Blunt Weapon", "Two-Handed Blunt Weapon"},
               "Spearman": {"Spear", "Polearm"}, "Hunter": {"Bow"}, "Crossbowman": {"Crossbow"},
               "Assassin": {"Claw"}, "Bandit": {"Dagger"}}
JOB_WEAPONS.update({"Crusader": JOB_WEAPONS["Fighter"], "White Knight": JOB_WEAPONS["Page"],
                    "Dragon Knight": JOB_WEAPONS["Spearman"], "Ranger": {"Bow"}, "Sniper": {"Crossbow"},
                    "Hermit": {"Claw"}, "Chief Bandit": {"Dagger"}})

# family -> (Hebrew words, English words, kind, the table values it stands for): equips.slot, consumables.type, or
# a scroll. Earlier ones win a span ("סקרול חזרה" is no scroll for a slot, "crossbow" no bow). Hebrew words that
# are something else too are left out: "מטה" (down), "אלה" (these), "אוכל" ("I can").
FAMILIES: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("Return Scroll", "סקרול(?:י)? חזרה|מגיל(?:ת|ות) חזרה", r"return scrolls?|town scrolls?", "use", ("Return Scroll",)),
    ("Throwing Star", "כוכב(?:ים)?|כוכבי זריקה|שוריקן(?:ים)?|סטאר(?:ים|ס)?", r"(?:throwing )?stars?|shurikens?",
     "use", ("Throwing Star",)),
    ("Arrow", "חץ|חצים|חיצים", r"arrows?", "use", ("Arrow",)),
    ("Potion", "שיקוי(?:ים)?|פוטיון(?:ים)?|פוטים|פוטס", r"potions?|pots", "use", ("Potion", "Food")),
    ("Food", "מזון", r"food", "use", ("Food",)),
    ("Scroll", "סקרול(?:ים)?|מגיל(?:ה|ות)", r"scrolls?", "scroll", ()),
    ("Cape", "גלימ(?:ה|ות|ת)|קייפ(?:ים)?", r"capes?", "equip", ("Cape",)),
    ("Gloves", "כפפ(?:ה|ות|ת)", r"gloves?", "equip", ("Gloves",)),
    ("Hat", "כוב(?:ע|עים)|קסד(?:ה|ות)", r"hats?|helm(?:et)?s?", "equip", ("Hat",)),
    ("Shoes", "נעל(?:יים|י|ים)?|מגפ(?:יים|ים)", r"shoes?|boots?", "equip", ("Shoes",)),
    ("Shield", "מגן|מגנים|מגינים", r"shields?", "equip", ("Shield",)),
    ("Earring", "עגיל(?:ים)?", r"earrings?", "equip", ("Earring",)),
    ("Top", "חולצ(?:ה|ות)", r"tops|shirts?", "equip", ("Top",)),
    ("Bottom", "מכנס(?:יים|ים)?", r"bottoms|pants|trousers", "equip", ("Bottom",)),
    ("Overall", "אוברול(?:ים)?", r"overalls?", "equip", ("Overall",)),
    ("Armor", "שריון|שריונות|בגד(?:ים)?", r"armou?rs?", "equip", ("Top", "Bottom", "Overall")),
    ("Crossbow", "קרוסבו(?:אים)?|קשת(?:ות)? צולב(?:ת|ות)", r"crossbows?|x-?bows?", "equip", ("Crossbow",)),
    ("Bow", "קשתות", r"bows?", "equip", ("Bow",)),
    ("Claw", "טופר|טפרים|ציפורן|קלו(?:אים)?", r"claws?", "equip", ("Claw",)),
    ("Dagger", "פגיון|פגיונות|סכין|סכינים|דאגר(?:ים)?", r"daggers?", "equip", ("Dagger",)),
    ("Sword", "חרב|חרבות", r"swords?", "equip", ("One-Handed Sword", "Two-Handed Sword")),
    ("Axe", "גרזן|גרזנים", r"axes?", "equip", ("One-Handed Axe", "Two-Handed Axe")),
    ("Blunt", "פטיש(?:ים)?|מקבת", r"maces?|blunts?|hammers?|clubs?", "equip",
     ("One-Handed Blunt Weapon", "Two-Handed Blunt Weapon")),
    ("Spear", "חנית(?:ות)?", r"spears?", "equip", ("Spear",)),
    ("Polearm", "פולארם", r"pole-?arms?", "equip", ("Polearm",)),
    ("Wand", "שרביט(?:ים)?|וונד(?:ים)?", r"wands?", "equip", ("Wand",)),
    ("Staff", "סטאף(?:ים)?|מטה קסם", r"staffs?|staves", "equip", ("Staff",)),
    ("Weapon", "נשק|נשקים", r"weapons?", "equip", WEAPONS),
]
_FAMILY_RX = [(name, re.compile(_he(he) + "|" + _en(en), re.I), kind, values) for name, he, en, kind, values in FAMILIES]
# "Shield Mastery", "Claw Booster": a skill's name (some, like Shield Mastery, aren't in the KB's skills)
_SKILL_WORD = re.compile(r"\b[A-Za-z]+(?:\s+Weapon)?\s+(?:mastery|booster|boost|expert|acceleration)\b", re.I)
# "קשת" is a bow and a Bowman: the job when another family is named ("חצים לקשת", "כובעים לקשת") or skills
_KESHET = re.compile(_he("קשת"))
# a scroll's slot: the equip slot as the scroll names write it ("Topwear", "Overall Armor", "Two-handed Sword")
_SCROLL_SLOT = {"Top": "Topwear", "Bottom": "Bottomwear", "Overall": "Overall Armor"}
_SCROLL_STAT = [(re.compile(_he(he) + "|" + _en(en), re.I), stat) for he, en, stat in (
    ("התקפה|אטק", r"attack|atk|att", "Attack"), ("הגנה|דיפנס", r"def|defense|defence", "DEF"),
    ("דיוק|אקיורסי", r"acc|accuracy", "Accuracy"), ("התחמקות|אבויד", r"avoid|evasion", "Evasion"),
    ("מהירות|ספיד", r"speed", "Speed"), ("קפיצה|ג'אמפ", r"jump", "Jump"), ("חיים", r"hp", "HP"), ("מאנה", r"mp", "MP"),
    ("כוח", r"str", "STR"), ("זריזות", r"dex", "DEX"), ("אינטליגנציה|תבונה", r"int", "INT"), ("מזל", r"luk|luck", "LUK"))]

# what the question asks of them
QUEST = _rx("משימ(?:ה|ות)|קווסט(?:ים)?|קוסט(?:ים)?", r"quests?|missions?")
# more than the one quest the question names ("other quests like Sam's Suggestion", "which quests after it")
QUEST_LIST = _rx("משימות|קווסטים|קוסטים|רשימה|עוד|אחרות|אחרים", r"quests|missions|list|other|more|all")
GIVE = _rx("נותנ(?:ת|ות|ים)?|נותן|מקבל(?:ים)?|לקבל|פרס(?:ים)?|תגמול(?:ים)?|מעניק(?:ה|ות|ים)?",
           r"gives?|giving|rewards?|rewarding|get|gets|award")
SELL = _rx("מוכר(?:ת|ים|ות)?|קונים|לקנות|קונה|אקנה|חנות|חנויות", r"sells?|selling|sold|buy|buying|shops?|store|vendor")
# "which recipes need Screw" is Screw as an ingredient, not Screw's own recipe (CRAFT's "recipes")
USES = re.compile(_he("משמש(?:ת|ים)?|משתמשים ב|מצרך|מצרכים") + r"|(?:להכין|לקרפט|ליצור)\s+(?:עם|מ-?)|"
                  + _he(r"מתכונ(?:ים|ות)?\s+(?:ש|אשר\s+)?(?:צריכים|צריך|דורשים|דורש|משתמשים)") + "|"
                  + _en(r"what uses|uses|used (?:for|in)|ingredient(?: of| for)?|make with|craft with|"
                        r"(?:recipes?|crafts?|items?)\s+(?:that\s+|which\s+)?(?:use|need|needs|requires?|calls? for)"),
                  re.I)
CRAFT = _rx("מכינים|להכין|מכין|ליצור|יוצרים|מתכון|מתכונים|קראפט|לקרפט", r"craft|crafting|crafted|make|made|recipes?")
NEED = _rx("צריך|צריכים|דורש(?:ת|ות|ים)?|להרוג|לאסוף|הריגות|הריגה|לצוד",
           r"need|needs|require|requires|required|kill|kills|killing|collect|hunt")
MONSTER = _rx("מפלצ(?:ת|ות)|מונסטר(?:ים)?|מוב(?:ים)?|יצורים", r"monsters?|mobs?")
WHATS_IN = re.compile(_he("מה יש|מי חי|מי גר") + "|" + _en(r"what(?:'s| is| lives)? in|what spawns|lives? in"), re.I)
TRAIN = re.compile(_he("גריינד|גרינד|להתאמן|אימון|לטחון|לעלות רמה|לעלות רמות|לעלות לבל|לאמן|לצוד|לעשות לבל") + "|"
                   + rf"(?<![{HE}])[הל]?מפ(?:ה|ות)(?![{HE}]).{{0,30}}?אקספי|"     # "המפה הכי טובה לאקספי"
                   + _en(r"grind(?:ing)?|train(?:ing)?|level(?:ing)? up|lvl up|exp maps?|hunt(?:ing)?|farm exp|"
                         r"farm(?:ing)?(?!\s+(?:mesos?|items?|drops?|ores?))|level(?:ing)? at|to level|maps? for (?:exp|xp|leveling)|exp spots?"), re.I)
WHERE = _rx("איפה|לאן|מקום|מקומות|מפה|מפות", r"where|maps?|spots?|places?")
EXP = re.compile(_he("אקספי|ניסיון|נסיון") + r"|\b(?:exp|xp|experience)\b", re.I)
BEST = _rx("הכי|מומלץ|מומלצים|כדאי|עדיף|טוב(?:ה|ים|ות)?|חזק(?:ה|ים|ות)?", r"best|most|top|highest|good|strongest|recommended?")
CHEAP = _rx("זול(?:ה|ים|ות)?|משתלמ(?:ים|ת|ות)?|משתלם|מחיר", r"cheap|cheapest|per meso|value|price|cost")
SKILL = _rx("סקיל(?:ים|ז)?|כישור(?:ים)?|מיומנו(?:ת|יות)", r"skills?")
LISTQ = _rx("איזה|אילו|איזו|מה|כל|רשימה|יש", r"which|what|list|all|any|show|best")
# the question is about jobs ("לאיזה ג'ובים אפשר להתקדם מקשת"): its "קשת" is the Bowman
JOB_TALK = _rx("ג'וב(?:ים)?|מקצוע(?:ות)?|קלאס(?:ים)?|להתקדם|התקדמות|אדבנס", r"jobs?|class(?:es)?|advance(?:ment)?")
NOW = _rx("עכשיו|כרגע", r"now|currently")
# the player speaks of themselves: the profile's job and level fill what the question leaves out
PERSONAL = _rx("לי|אני|שלי|אותי|בשבילי|עבורי|אוכל|אנחנו|לנו", r"i|i'm|im|me|my|mine|we")
# "regular monsters (not bosses)", "מפלצות רגילות (לא בוסים)": the bosses left out of the list
NO_BOSS = re.compile(_he("לא בוס(?:ים)?|בלי בוס(?:ים)?|רגיל(?:ות|ים|ה)?|ללא בוס(?:ים)?") + "|"
                     + _en(r"not (?:a )?boss(?:es)?|no boss(?:es)?|non-?boss(?:es)?|regular|normal|excluding boss(?:es)?"),
                     re.I)
# "top 3", "3 best", "אילו 3 מפלצות": how many rows the answer is
TOP_N = re.compile(rf"\btop\s*(\d{{1,2}})\b|\b(\d{{1,2}})\s+(?:best|top|most)\b|(?<![{HE}])(?:איזה|אילו|איזו)\s+(\d{{1,2}})"
                   rf"(?!\d)|(?<![\d\-])(\d{{1,2}})\s+(?:ה)?(?:מפלצות|מובים|קווסטים|משימות|מפות|הכי)(?![{HE}])", re.I)
# "how do I become a magician", "איך נהיים קוסם": the class's instructor and the job advancement; never "how to be a
# better assassin" or "how do I get to the thieves hideout" (audit AI-10)
BECOME = re.compile(_he("איך נהיים|איך נהיה|איך הופכים ל|איך להיות|איך נעשים|איך מתקדמים ל|להתקדם ל|ג'וב אדבנס|"
                        "אדבנסמנט|התקדמות ל|ג'וב שני|ג'וב 2|ג'וב ראשון|ג'וב 1") + r"|advancement\s+ה?שני|"
                    + _en(r"how (?:do|can|to) (?:i |you |we )?(?:become|turn into|be(?!\s+(?:an?\s+)?(?:better|stronger|"
                          r"good|great)\b))|become an?|"
                          r"job advance(?:ment)?|advance to|(?:1st|2nd|first|second) job"), re.I)
SECOND_JOB = re.compile(_he("ג'וב שני|ג'וב 2|אדבנס שני|אדבנסמנט השני") + "|" + _en(r"2nd job|second job")
                        + r"|advancement\s+ה?שני", re.I)      # "ה-job advancement השני"
# "איפה יש סטירג'", "where can I find Stirge": the named monster's / NPC's place
WHERE_IS = _rx("איפה|באיזה מקום|באיזו מפה|באיזה מפה|איפה אפשר למצוא|מיקום", r"where|location|find|spawns?")
# "מאיזה מפלצות נופלות כפפות": drops, which brain's drop groups answer (its DROP_WORDS miss the glued "מאיזה")
DROPPED = re.compile(_he("נופל(?:ת|ים|ות)?") + r"|\bdropp(?:ed|ing)\b", re.I)
# the screenshot is the subject: what is on screen, never the tables'
SCREEN = re.compile(_he("במסך|בתמונה|בצילום|על המסך|בצילום מסך") + "|" + _en(r"screenshot|on (?:my |the )?screen|"
                                                                                r"in the picture"), re.I)
HEAL = re.compile(r"\b(?:hp|mp|heal(?:s|ing)?|recover(?:y|s)?)\b|" + _he("חיים|מאנה|ריפוי|מרפא|ריפוי"), re.I)

_LVW = r"(?:רמ(?:ה|ות)|לבל(?:ים)?|levels?|lvl|lv\.?)"
RANGE = re.compile(rf"(?:(?<![{HE}])בין|\bbetween|\bfrom)\s*(?:[בל]?{_LVW}\s*)?(\d{{1,3}})\s*(?:-|–|~|עד|ל-?|and|to)\s*"
                   rf"(?:ל?{_LVW}\s*)?(\d{{1,3}})(?!\d)|(?<![A-Za-z{HE}])[במל]?{_LVW}\s*-?\s*(\d{{1,3}})\s*"
                   rf"(?:-|–|~|עד|ל-|to)\s*(\d{{1,3}})(?!\d)|"
                   # "מפלצות מ-20 עד 30"
                   rf"(?<![{HE}\w])מ-?\s*(\d{{1,3}})\s*עד\s*(?:ל?{_LVW}\s*)?(\d{{1,3}})(?!\d|\s*(?:%|mesos?|k\b))", re.I)
LEVEL = re.compile(rf"(?<![A-Za-z{HE}])[במלה]?{_LVW}\s*-?\s*(\d{{1,3}})(?!\d)", re.I)
# "where should I train at 20", "לגריינד ב-20": a bare number after at / for / ב-, never a price or a percent
# (never a time or a count: "train for 2 hours" was a Lv 2 training list and the Lv 2 digest)
LOOSE_LEVEL = re.compile(rf"(?:\b(?:at|for)\s+(?:a\s+)?|(?<![{HE}])ב-?)(\d{{1,3}})(?!\d|\s*(?:%|k\b|mesos?|meso|hp|mp|"
                         rf"ACC|דקות|שניות|minutes?|seconds?|min\b|sec\b|x\b|hours?\b|hrs?\b|h\b|days?\b|people|"
                         rf"players?\b|persons?\b|times\b|שעות|שעה|ימים|יום|אנשים|שחקנים|פעמים))(?![\d.,])", re.I)
MY_LEVEL = re.compile(rf"[בל]?{_LVW}\s+(?:שלי|שלנו)|\bmy (?:level|lvl|lv)\b", re.I)

# the English job names and the ones players type ("archers", "thieves"), and the Hebrew ones (jobs.JOB_HE and the
# usual spellings); "Page" only capitalized ("which page")
_JOB_EN = {**{j.lower(): j for js in jobs.JOBS.values() for j, _ in js if j != "Page"}, **jobs.ALIASES,
           "warriors": "Warrior", "magicians": "Magician", "mages": "Magician", "archers": "Bowman",
           "thieves": "Thief", "fighters": "Fighter", "hunters": "Hunter", "assassins": "Assassin",
           "bandits": "Bandit", "clerics": "Cleric", "beginners": "Beginner", "spearmen": "Spearman",
           "crossbowmen": "Crossbowman", "fp wizard": "F/P Wizard", "il wizard": "I/L Wizard"}
_JOB_HE = {**{he: j for j, he in jobs.JOB_HE.items() if he != "קשת"},
           "גנבים": "Thief", "לוחמים": "Warrior", "קוסמים": "Magician", "מג'": "Magician", "מייג'": "Magician",
           "קשתים": "Bowman", "ארצ'ר": "Bowman", "אססין": "Assassin", "אסאסינים": "Assassin", "הנטר": "Hunter",
           "קרוסבו מן": "Crossbowman", "ביגינרים": "Beginner", "מתחיל": "Beginner", "מתחילים": "Beginner"}
_JOB_RX = re.compile(r"(?<![\w/])(" + "|".join(sorted((re.escape(k).replace(r"\ ", r"\s+") for k in _JOB_EN),
                                                         key=len, reverse=True)) + r")(?![\w/])|(?<![\w])((?-i:Page))s?\b"
                     + "|" + _he("|".join(sorted(map(re.escape, _JOB_HE), key=len, reverse=True))), re.I)

# Hebrew names of the towns (quests.tsv's area column)
AREAS_HE = {"הניסיס": "Henesys", "הנסיס": "Henesys", "קרנינג": "Kerning City", "קרנינג סיטי": "Kerning City",
            "אלינייה": "Ellinia", "אליניה": "Ellinia", "אלניה": "Ellinia", "פריון": "Perion", "פריאון": "Perion",
            "סליפיווד": "Sleepywood", "סליפי": "Sleepywood", "מייפל איילנד": "Maple Island",
            "פלורינה": "Florina Beach", "לית' הארבור": "Lith Harbor", "לית הארבור": "Lith Harbor"}
# a word run naming no map (an acronym or a game word the question uses)
_NOT_MAP = {"exp", "npc", "npcs", "hp", "mp", "mesos", "meso", "lv", "level", "quest", "quests", "boss", "the", "what",
            "which", "where", "monsters", "monster", "mobs", "in", "map", "maps", "is", "are", "a", "of", "at", "on"}


@dataclass
class Slots:
    families: list[str] = field(default_factory=list)       # family names, in question order
    level: int | None = None
    lo: int | None = None
    hi: int | None = None
    job: str | None = None            # a canonical job ("Assassin", "Thief")
    personal: bool = False
    said_level: bool = False          # the question names a level (not the profile's)
    entities: list[str] = field(default_factory=list)       # KB keys the question names (kb.find_mentions)
    maps: list[str] = field(default_factory=list)           # map keys the question names, a family of them too
    area: str | None = None
    question: str = ""


@dataclass
class Block:
    table: str
    cols: tuple[str, ...]
    rows: list[dict]
    what: str                  # the filter, in words
    sort: str
    grep: str = ""             # the exact grep for the rows left out
    note: str = ""
    cap: int = CAP
    lead: str = ""             # the answer in a line, right under the head ("Steel Guards IS crafted: ...")
    # a relation's empty side is an answer too ("what quests does Robin give?": none), said as complete: dropped,
    # the AI went looking or guessed
    keep_empty: bool = False

    def render(self) -> str:
        total = len(self.rows)
        shown = self.rows if total <= self.cap else self.rows[:min(SHOWN, self.cap)]
        whole = len(shown) == total
        table = f"{self.table}.tsv" if self.table in tables.TABLES else self.table     # "jobs": the app's job tree
        head = (f'<table_rows table="{table}" match="{self.what}" sort="{self.sort}" '
                f'rows="{total if whole else f"{len(shown)} of {total}"}" complete="{"yes" if whole else "no"}">')
        lines = [head, *([self.lead] if self.lead else []), "\t".join(self.cols)]
        lines += ["\t".join(tables._cell(_short(r.get(c)) if c in CUT else r.get(c)) for c in self.cols)
                  for r in shown]
        if not total:
            lines.append("(no rows: there are none)")
        if not whole:
            lines.append(f"({total - len(shown)} more rows, sorted after these: {self.grep})")
        if self.note:
            lines.append(self.note)
        lines.append("</table_rows>")
        return "\n".join(lines)


@dataclass
class Plan:
    intent: str
    blocks: list[Block]
    level: int | None = None      # the level the question is about (the level digest follows it)

    top: int | None = None        # "top 3", "אילו 3 מפלצות": the answer is the first rows, in order

    def render(self) -> str:
        text = "\n".join(b.render() for b in self.blocks if b.rows or b.keep_empty)
        if self.top and text:
            text += (f"\nThe question asks for {self.top}: answer with the first {self.top} rows of the block above, "
                     "in its order, skipping none.")
        return text


def _short(v):
    if isinstance(v, str) and len(v) > CELL:
        return v[:CELL - 1].rstrip() + "…"
    return v


# ---------------------------------------------------------------- slots

def families(question: str, masked: list[tuple[int, int]] = ()) -> list[tuple[str, int]]:
    """(family, position) of every item family the question names, an earlier family's span kept. masked: spans
    that are another name ("Claw Mastery", "Arrow Blow"), never a family."""
    taken: list[tuple[int, int]] = list(masked)
    out = []
    for name, rx, _, _ in _FAMILY_RX:
        for m in rx.finditer(question):
            if any(m.start() < b and a < m.end() for a, b in taken):
                continue
            taken.append(m.span())
            out.append((name, m.start()))
    return sorted(out, key=lambda f: f[1])


def _kind(name: str) -> str:
    return next(k for n, _, k, _ in _FAMILY_RX if n == name)


def _values(name: str) -> tuple[str, ...]:
    return next(v for n, _, _, v in _FAMILY_RX if n == name)


def job_named(question: str) -> str | None:
    m = _JOB_RX.search(question)
    if not m:
        return None
    if m.group(2):
        return "Page"
    word = " ".join(m.group(0).split())
    he = None
    if re.match(f"[{HE}]", word):
        # the word as typed first: a name may start with a prefix letter itself ("לוחם", "בנדיט", "הנטר"), and
        # stripped first it was "חם", "נדיט", "נטר" and no job
        cut = [word[n:] for n in (1, 2) if re.match(rf"[והבלמשכ]{{{n}}}", word)]
        he = next((_JOB_HE[w] for w in (word, *cut) if w in _JOB_HE), None) or next(
            (j for k, j in _JOB_HE.items() if word.endswith(k)), None)
    return he or jobs.canonical_job(_JOB_EN.get(word.lower(), word)) or jobs.canonical_class(_JOB_EN.get(
        word.lower(), word))


def levels(question: str) -> tuple[int | None, int | None, int | None]:
    """(level, lo, hi): a single level ("ברמה 30", "Lv. 30") or a range ("בין רמה 20 ל-35", "level 30-40")."""
    m = RANGE.search(question)
    if m:
        a, b = (int(g) for g in m.groups() if g)
        if 1 <= a <= 250 and 1 <= b <= 250:
            lo, hi = min(a, b), max(a, b)
            return None, lo, hi
    for rx in (LEVEL, LOOSE_LEVEL):
        m = rx.search(question)
        if m and 1 <= int(m.group(1)) <= 250:
            return int(m.group(1)), None, None
    return None, None, None


def _map_rows(rows) -> list[dict]:
    return rows("maps")


def named_maps(question: str, mentions: list[str], rows) -> list[str]:
    """Map keys the question names: a map find_mentions found, else the maps a run of the question's English words
    is part of ("Ant Tunnel": Ant Tunnel I-IV, Ant Tunnel Park, Deep Ant Tunnel I-II), or a street's maps."""
    maps = _map_rows(rows)
    keys = [k for k in mentions if k.startswith("map/")]
    name = {r["key"]: r["map"] for r in maps}
    named = {name[k] for k in keys if k in name}
    phrase, hit = _phrase_maps(question, maps)
    # "Kerning City Subway" names the street, not the town find_mentions found in it
    if hit and len(phrase) > max((len(n) for n in named), default=0):
        return hit
    # every map of that name (Henesys has 3 "Henesys" maps), the found one first
    return list(dict.fromkeys(keys + [r["key"] for r in maps if r["map"] in named]))


def _phrase_maps(question: str, maps: list[dict]) -> tuple[str, list[str]]:
    """The longest run of the question's English words that is a street or part of maps' names, and those maps."""
    for run in re.findall(r"[A-Za-z][A-Za-z'.<>]*(?:[ -][A-Za-z0-9'.<>]+)*", question):
        words = run.split()
        for n in range(len(words), 0, -1):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n])
                # one word only as a name ("Henesys", "Ellinia"): "regular monsters" is no "Regular Sauna"
                if all(w.lower() in _NOT_MAP for w in words[i:i + n]) or \
                        (n == 1 and (len(phrase) < 5 or not phrase[0].isupper())):
                    continue
                pat = re.compile(rf"(?<![\w']){re.escape(phrase)}(?![\w'])", re.I)
                street = [r["key"] for r in maps if (r.get("street") or "").lower() == phrase.lower()]
                hit = street or [r["key"] for r in maps if pat.search(r["map"] or "")
                                 and (n > 1 or (r["map"] or "").lower().startswith(phrase.lower()))]
                if hit:
                    return phrase, hit
    return "", []


def _names_in(question: str, names) -> list[tuple[int, int]]:
    """The spans of these names in the question (any case, whole words)."""
    out = []
    for n in {str(n) for n in names if n and len(str(n)) > 2}:
        out += [m.span() for m in re.finditer(rf"(?<![\w']){re.escape(n)}(?![\w'])", question, re.I)]
    return out


def _not_items(question: str, kb, mentions: list[str], rows) -> list[tuple[int, int]]:
    """Spans of names that hold an item family's word but are no item: a skill ("Magic Claw", "Arrow Bomb: Bow",
    typed "Arrow Bomb"), another entity the question names, "<word> Mastery/Booster". "what does Claw Mastery do"
    got every claw, "Recommend row 1, Blue Scarab", as the answer data."""
    names = [(kb.get(k) or {}).get("name", "") for k in mentions if not k.startswith("item/")]
    # a shop map's family word is what it sells: "what does the Henesys weapon store sell" (audit AI-9)
    names = [n for n in names if not re.search(r"\b(?:Store|Shop)$", n)]
    if rows is not None:
        for r in rows("skills"):
            names += [r["skill"], str(r["skill"]).split(":")[0]]
    return _names_in(question, names) + [m.span() for m in _SKILL_WORD.finditer(question)]


_AUX = re.compile(r"(?:can|do|should|could|would|will|did|shall|may|must|am|have|where|what|how|when|if|so|and|"
                  r"that|which|who|why|than|as)\s*$", re.I)


def _personal(question: str) -> bool:
    """The player speaks of themselves. A map's "I" isn't them: "Henesys Hunting Ground I" (the end of a name: the
    question's end, a comma, "and II") put the profile's level on the list; "can I", "should I?" still are."""
    for m in PERSONAL.finditer(question):
        if m.group(0).lower() == "i" and re.match(r"\s*(?:$|[?.!,;:)]|(?:and|or|&)\b)", question[m.end():], re.I) \
                and re.search(r"[A-Za-z]\s+$", question[:m.start()]) and not _AUX.search(question[:m.start()]):
            continue
        return True
    return False


def slots(question: str, kb, character=None, rows=None) -> Slots:
    s = Slots(question=question)
    mentions = kb.find_mentions(question, max_results=6)
    fams = [f for f, _ in families(question, _not_items(question, kb, mentions, rows))]
    keshet = _KESHET.search(question)
    s.job = job_named(question)
    if keshet:
        if fams or SKILL.search(question) or JOB_TALK.search(question) or BECOME.search(question):
            s.job = s.job or "Bowman"
        elif "Bow" not in fams:
            fams.append("Bow")
    s.families = fams
    s.level, s.lo, s.hi = levels(question)
    s.said_level = s.level is not None or s.lo is not None
    s.personal = _personal(question) or bool(MY_LEVEL.search(question))
    if character and (s.personal or NOW.search(question)) and s.level is None and s.lo is None:
        s.level = int(character.level or 0) or None
    # an item named like a family ("Crossbow", "Arrow") is the family's word here, not one item
    s.entities = [k for k in mentions
                  if not (k.startswith("item/") and any(rx.fullmatch(str((kb.get(k) or {}).get("name", "")))
                                                        for _, rx, _, _ in _FAMILY_RX))]
    low = question.lower()
    s.area = next((a for a in ("Henesys", "Kerning City", "Ellinia", "Perion", "Sleepywood", "Maple Island",
                               "Florina Beach", "Lith Harbor") if a.lower() in low), None) or \
        next((v for k, v in AREAS_HE.items() if re.search(_he(re.escape(k)), question)), None)
    if rows is not None:
        s.maps = named_maps(question, s.entities, rows)
    return s


def _base(job: str | None) -> str | None:
    if not job:
        return None
    return job if job in jobs.JOBS else jobs.class_of(job)


def _player_class(s: Slots, character) -> str | None:
    """The class an equipment list is for: the one the question names, else the player's when they speak of
    themselves ("אני", "my")."""
    if s.job:
        return _base(s.job) or "Beginner"
    if character and s.personal:
        return character.base_class or None
    return None


# ---------------------------------------------------------------- retrieval

def _num(v) -> int:
    return v if isinstance(v, int) else 0


def _stats(r: dict) -> str:
    labels = (("watk", "W.ATK"), ("matk", "M.ATK"), ("wdef", "W.DEF"), ("mdef", "M.DEF"), ("acc", "ACC"),
              ("avoid", "AVOID"), ("speed", "SPEED"), ("jump", "JUMP"), ("hp", "HP"), ("mp", "MP"), ("str", "STR"),
              ("dex", "DEX"), ("int", "INT"), ("luk", "LUK"), ("crit", "CRIT%"))
    return ", ".join(f"{label} {r[c]:+d}" for c, label in labels if isinstance(r.get(c), int) and r[c])


def _reqs(r: dict) -> str:
    return ", ".join(f"{s.upper()} {r[f'req_{s}']}" for s in ("str", "dex", "int", "luk") if r.get(f"req_{s}"))


def _wears(job_cell: str, base: str | None) -> bool:
    if not base:
        return True
    jobs_ = (job_cell or "Any").split("/")
    return "Any" in jobs_ or base in jobs_


def equips(rows, s: Slots, base: str | None, job: str | None = None, level: int | None = None) -> Block:
    """job: the 2nd job whose weapons "the best weapon" means; level: the profile's, when the question names none."""
    slots_ = {v for f in s.families if _kind(f) == "equip" for v in _values(f)}
    if s.families == ["Weapon"] and base in CLASS_WEAPONS:
        slots_ &= CLASS_WEAPONS[base]       # "the best weapon for a Thief": claws and daggers, not a Fish Spear
        if job in JOB_WEAPONS and jobs.class_of(job) == base:
            slots_ &= JOB_WEAPONS[job]
    level = s.level if s.level is not None else level if s.lo is None else None
    out = []
    for r in rows("equips"):
        if r["slot"] not in slots_ or not _wears(r["job"], base):
            continue
        lv = _num(r.get("req_lv"))
        if (s.lo is not None and not s.lo <= lv <= s.hi) or (level is not None and lv > level):
            continue
        out.append({**r, "req_lv": lv, "req": _reqs(r), "stats": _stats(r)})
    weapon = bool(slots_) and slots_ <= set(WEAPONS)
    if weapon:
        magic = slots_ <= set(MAGIC_WEAPONS)
        out.sort(key=lambda r: (-_num(r.get("matk" if magic else "watk")), -r["req_lv"], r["item"]))
        sort = ("matk" if magic else "watk") + " desc"
    else:
        out.sort(key=lambda r: (-r["req_lv"], -_num(r.get("wdef")), r["item"]))
        sort = "req_lv desc"
    what = "slot " + "/".join(sorted(slots_)) if len(slots_) < 4 else "weapons"
    what += f", job {base} or Any" if base else ""
    what += (f", req_lv {s.lo}-{s.hi}" if s.lo is not None else f", req_lv <= {level}" if level is not None else "")
    cols = ("item", *(("slot",) if len(slots_) > 1 else ()), "job", "req_lv", "req", "stats", "slots",
            *(("attack_speed",) if weapon else ()), "buy", "seller", "key")
    # "the best claw I can use": the model picked a Lv 25 Meba over the Lv 30 Guards, unsure of the player's LUK
    # (and a Lv 35 Hunter to the Lv 30 Ryden with Red Viper the first row): the pick is said outright. "The best
    # the player can wear" only under a level: unfiltered, row 1 is a Lv 70 claw
    fits = level is not None or s.lo is not None
    lead = (f"Recommend row 1, {out[0]['item']} (req_lv {out[0]['req_lv']}"
            + (f", {out[0]['req']}" if out[0]["req"] else "")
            + ("): the best the player can wear; with no stats in the profile, name its req and a lower row only as "
               "the fallback." if fits else f"): the highest {'M.ATK' if sort.startswith('matk') else 'W.ATK'} in the "
               "game; check its req_lv against the player's level.") if weapon and out else "")
    return Block("equips", cols, out, what, sort, grep=f"grep -P '\\t({'|'.join(sorted(slots_))})\\t' equips.tsv",
                 lead=lead)


def _item_match(s: Slots, kind_ok) -> tuple[set[str], set[str], str]:
    """(item keys, item_type values, description) a relation question is about: the items it names, else the
    families' types ("Equip / Cape", "Use / Scroll")."""
    named = {k for k in s.entities if k.startswith("item/")}
    if named:
        return named, set(), "items " + ", ".join(sorted(named))
    types = set()
    # "crossbow arrows", "stars for my claw": the weapon only says which ammo (_arrows_fit); it listed 5 crossbows
    ammo = {"Arrow", "Throwing Star"} & set(s.families)
    for f in s.families:
        kind = _kind(f)
        if not kind_ok(kind) or (ammo and f in ("Bow", "Crossbow", "Claw", "Weapon")):
            continue
        if kind == "equip":
            types |= {f"Equip / {v}" for v in _values(f)}
        elif kind == "scroll":
            types.add("Use / Scroll")
        else:
            types |= {f"Use / {v}" for v in _values(f)}
    return set(), types, "item_type " + "/".join(sorted(types))


def _slot_filter(s: Slots):
    """A scroll for a slot ("cape scrolls", "סקרול לכפפות"): the item's name must say the slot."""
    if "Scroll" not in s.families:
        return lambda name: True
    words = [_SCROLL_SLOT.get(v, v) for f in s.families if _kind(f) == "equip" for v in _values(f)]
    if not words:
        return lambda name: True
    return lambda name: any(str(name).lower().startswith(w.lower()) for w in words)


def _grep(keys: set[str], types: set[str], table: str) -> str:
    """The grep for the rows left out: by the items' keys, else their types (a named item has no type here: it was
    "grep '' rewards.tsv", every line)."""
    terms = sorted(keys) or sorted(types)
    return f"grep -P '\\t({'|'.join(terms)})\\t' {table}" if len(terms) > 1 else f"grep '{terms[0]}' {table}"


def rewards(rows, s: Slots) -> Block | None:
    keys, types, what = _item_match(s, lambda k: True)
    if not keys and not types:
        return None
    named = _slot_filter(s)
    # one row per quest: "which quests give scrolls" is 95 reward rows of 38 quests
    per: dict[str, dict] = {}
    for r in rows("rewards"):
        if not (r["item_key"] in keys if keys else r["item_type"] in types) or not named(r["item"]):
            continue
        q = per.setdefault(r["quest_key"], {"quest": r["quest"], "quest_level": r["quest_level"], "area": r["area"],
                                            "items": [], "quest_key": r["quest_key"], "item_keys": []})
        who = r["for"] if r["for"] and r["for"] != "Any Class" else ""
        count = f" x{r['count']}" if _num(r["count"]) > 1 else ""
        q["items"].append(f"{r['item']}{count} ({r['kind']}{', ' + who if who else ''})")
        q["item_keys"].append(r["item_key"])
    out = sorted(per.values(), key=lambda r: (_num(r["quest_level"]), r["quest"]))
    for r in out:
        r["items"], r["item_keys"] = "; ".join(r["items"]), ",".join(dict.fromkeys(k for k in r["item_keys"] if k))
    return Block("rewards", ("quest", "quest_level", "area", "items", "quest_key", "item_keys"), out, what,
                 "quest_level", grep=_grep(keys, types, "rewards.tsv"),
                 note="items' kind: sure = always given, pick one = the player chooses, random = one of them by chance")


def _arrows_fit(s: Slots, item: str) -> bool:
    """Arrows for the weapon the question means: "חצים לקשת" are the bow's, a Crossbowman's the crossbow's."""
    if "Arrow" not in s.families or "Arrow" not in str(item):
        return True
    xbow = "Crossbow" in s.families or s.job == "Crossbowman"
    if xbow or "Bow" in s.families or s.job in ("Bowman", "Hunter"):
        return ("for Crossbows" in item) == xbow
    return True


def shops(rows, s: Slots, base: str | None = None) -> Block | None:
    keys, types, what = _item_match(s, lambda k: True)
    npcs = [k for k in s.entities if k.startswith("npc/")]
    if not keys and not types and npcs:
        out = [r for r in rows("shops") if r["npc_key"] in npcs]
        out.sort(key=lambda r: (r["npc"], str(r["item_type"] or ""), _num(r["price"]), r["item"] or ""))
        return Block("shops", ("npc", "item", "item_type", "price", "place", "label", "rank", "item_key"), out,
                     "npc " + ", ".join(npcs), "item_type", grep=f"grep '{npcs[0]}' shops.tsv")
    if not keys and not types:
        return None
    out = [r for r in rows("shops") if (r["item_key"] in keys if keys else r["item_type"] in types)]
    if not keys:
        out = [r for r in out if _arrows_fit(s, r["item"])]
    # "איזה כובע כדאי לי לקנות" (an Assassin, level 31) got every hat sold, any class, a Lv 5 one first; "the
    # Henesys weapon store" every town's (audit AI-9): the class and level the question is for, the town it names
    if not keys and any(t.startswith("Equip / ") for t in types) and (base or s.level is not None):
        gear = {r["key"]: r for r in rows("equips")}
        out = [r for r in out if (e := gear.get(r["item_key"])) is None
               or (_wears(e["job"], base) and (s.level is None or _num(e.get("req_lv")) <= s.level))]
        what += (f", job {base} or Any" if base else "") + (f", req_lv <= {s.level}" if s.level is not None else "")
    if s.area and (here := [r for r in out if s.area.lower() in str(r["place"] or "").lower()]):
        out = here
        what += f", in {s.area}"
    named = _slot_filter(s)
    # one row per item, its sellers cheapest first: "where can I buy potions" is 115 shop rows of 30 items
    per: dict[str, dict] = {}
    for r in sorted(out, key=lambda r: (_num(r["price"]) or 10 ** 9, r["npc"])):
        if not named(r["item"]):
            continue
        i = per.setdefault(r["item_key"] or r["item"], {"item": r["item"], "item_type": r["item_type"],
                                                         "price": r["price"], "sellers": [], "label": set(),
                                                         "item_key": r["item_key"], "npc_keys": []})
        town = str(r["place"] or "").rsplit(" · ", 1)[-1]
        price = f" {r['price']}" if r["price"] is not None and r["price"] != i["price"] else ""
        rank = f", citizen rank {r['rank']}" if r["rank"] else ""
        i["sellers"].append(f"{r['npc']} ({town}{rank}){price}")
        i["label"].add(r["label"] or "")
        i["npc_keys"].append(r["npc_key"])
    rows_ = sorted(per.values(), key=lambda i: (str(i["item_type"]), _num(i["price"]), i["item"]))
    for i in rows_:
        i["sellers"], i["label"] = "; ".join(i["sellers"]), "/".join(sorted(x for x in i["label"] if x))
        i["npc_keys"] = ",".join(dict.fromkeys(k for k in i["npc_keys"] if k))
    return Block("shops", ("item", "price", "sellers", "label", "item_key", "npc_keys"), rows_, what,
                 "item_type, price", grep=_grep(keys, types, "shops.tsv"),
                 note="price: the cheapest, in mesos; a seller with another price has it after its name. Sorted by "
                      "price, not by quality: the first row is the cheapest, never \"the best\"")


def recipes(rows, s: Slots, uses: bool) -> list[Block]:
    items = [k for k in s.entities if k.startswith("item/")]
    if not items:
        return []
    if not uses:
        out = [r for r in rows("recipes") if r["product_key"] in items]
        cols = ("product", "recipe", "makes", "discipline", "prof_lv", "craft_exp", "meso_cost", "ingredient", "qty",
                "optional", "ingredient_key", "product_key")
        # the item page's "Needed By" (what it is an ingredient of) made the answer "it isn't crafted directly"
        lead = (f"{', '.join(sorted({r['product'] for r in out}))} IS crafted: {out[0]['discipline']} Lv "
                f"{out[0]['prof_lv']}, {out[0]['meso_cost']} mesos, the ingredients below." if out else "")
        return [Block("recipes", cols, out, "product " + ", ".join(items), "recipe",
                      grep=f"grep '{items[0]}' recipes.tsv", lead=lead)]
    out = [r for r in rows("recipes") if r["ingredient_key"] in items]
    out.sort(key=lambda r: (r["discipline"], _num(r["prof_lv"]), r["product"]))
    used = [r for r in rows("quest_reqs") if r["target_key"] in items]
    used.sort(key=lambda r: (_num(r["quest_level"]), r["quest"]))
    return [Block("recipes", ("product", "recipe", "discipline", "prof_lv", "ingredient", "qty", "optional",
                              "product_key"), out, "ingredient " + ", ".join(items), "discipline, prof_lv",
                  grep=f"grep '{items[0]}' recipes.tsv", keep_empty=True),
            Block("quest_reqs", ("quest", "quest_level", "kind", "target", "count", "quest_key"), used,
                  "target " + ", ".join(items), "quest_level", keep_empty=True)]


def quest_needs(rows, s: Slots) -> Block | None:
    targets = [k for k in s.entities if k.startswith(("monster/", "item/"))]
    if not targets:
        return None
    names = {str((r.get("monster") or "")).lower() for r in rows("monsters") if r["key"] in targets}
    out = [r for r in rows("quest_reqs")
           if r["target_key"] in targets or (r["kind"] == "defeat" and str(r["target"]).lower() in names)]
    out.sort(key=lambda r: (_num(r["quest_level"]), r["quest"]))
    return Block("quest_reqs", ("quest", "quest_level", "kind", "target", "count", "quest_key", "target_key"), out,
                 "target " + ", ".join(targets), "quest_level", grep=f"grep '{targets[0]}' quest_reqs.tsv")


_QUEST_COLS = ("quest", "level", "area", "npc", "job", "exp", "mesos", "cycle", "after", "rewards", "key")


def npc_quests(rows, s: Slots) -> list[Block]:
    npcs = [k for k in s.entities if k.startswith("npc/")]
    if not npcs:
        return []
    who = [r for r in rows("npcs") if r["key"] in npcs]
    names = {r["npc"] for r in who}
    # the quests the NPC gives, and apart from them the ones only turned in to it: one mixed list made the answer
    # drop a quest Manji gives ("Getting Arcon's Blood") and name one he only takes back
    given = sorted((r for r in rows("quests") if r["npc_key"] in npcs), key=lambda r: (_num(r["level"]), r["quest"]))
    taken = sorted((r for r in rows("quests") if r["turn_in"] in names and r["npc_key"] not in npcs),
                   key=lambda r: (_num(r["level"]), r["quest"]))
    who_ = ", ".join(sorted(names))
    cols = (*_QUEST_COLS[:4], "turn_in", *_QUEST_COLS[4:])
    return [Block("npcs", ("npc", "role", "map", "street", "key", "map_key"), who, "npc " + ", ".join(npcs), "-"),
            Block("quests", cols, given, f"quests {who_} GIVES (npc {who_})", "level",
                  grep=f"grep '{next(iter(names), '')}' quests.tsv", keep_empty=True),
            Block("quests", cols, taken, f"quests other NPCs give that are only TURNED IN to {who_}", "level",
                  note=f"not quests {who_} gives", keep_empty=True)]


def where_monster(rows, mons: list[str]) -> Block:
    out = sorted((r for r in rows("spawns") if r["monster_key"] in mons),
                 key=lambda r: (-_num(r["count"]), r["map"]))
    names = sorted({r["monster"] for r in out})
    return Block("spawns", ("monster", "level", "map", "street", "count", "share", "respawn", "map_key"), out,
                 "monster " + ", ".join(names), "count desc",
                 note="count: how many spawn on that map; share %: of the map's monsters", lead=(
                     f"{', '.join(names)}: every map in the game, {len(out)} of them, the most spawns first."
                     if out else ""))


def _npc_rows(rows, keys: list[str]) -> list[dict]:
    """NPC rows with the maps their map connects to: "Magic Library" alone doesn't say it is in Ellinia."""
    maps = {r["key"]: r for r in rows("maps")}
    return [{**r, "connects": (maps.get(r["map_key"]) or {}).get("connects", "")} for r in rows("npcs")
            if r["key"] in keys]


def where_npc(rows, npcs: list[str]) -> Block:
    return Block("npcs", ("npc", "role", "map", "street", "connects", "key", "map_key"), _npc_rows(rows, npcs),
                 "npc " + ", ".join(npcs), "-", note="connects: the maps next to it")


_ORDINAL = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def _open_tier(kb) -> int:
    try:
        return jobs.open_tier(kb)
    except Exception:          # noqa: BLE001 - a KB without the release guide: only what is always there
        return jobs.open_tier()


def _step(questline) -> int:
    """A quest's step in its questline ("2/4" -> 2); one with none goes after the steps."""
    m = re.match(r"\s*(\d+)\s*/", str(questline or ""))
    return int(m.group(1)) if m else 999


def job_advance(rows, s: Slots, character=None, second: bool = False, kb=None) -> list[Block]:
    """How to become a job: the class's instructor (the 1st job, and the 2nd job's quest) and the job-advancement
    quests, at the level the official facts give."""
    from . import official
    base = _base(s.job)
    first = (official.value("first_job_level") or {}).get(base) or next(
        (lv for j, lv in jobs.JOBS.get(base, []) if j == base), None)
    tier = next((lv for j, lv in jobs.JOBS.get(base, []) if j == s.job), None)
    rank = jobs.tier(s.job) or 1
    if rank > _open_tier(kb):
        # "how do I become a Hermit" got "Hermit: the 2nd job, at level 70", Dark Lord and the 2nd-job quests, and
        # Sonnet repeated it (audit AI-3): an advancement the KB doesn't confirm is said to be out of the game
        tree = [{"job": j, "level": lv, "advancement": _ORDINAL.get(jobs.tier(j) or 0, "Beginner"),
                 "in_game": "yes" if (jobs.tier(j) or 0) <= _open_tier(kb) else "no"}
                for j, lv in jobs.JOBS.get(base, []) if j != "Beginner"]
        lead = (f"{s.job}: the {base} line's {_ORDINAL[rank]} job, at level {tier}. The {_ORDINAL[rank]} job advancement is "
                "NOT in the game yet (the knowledge base doesn't confirm it): say so, and give no NPC, quest or "
                "place for it.")
        return [Block("jobs", ("job", "level", "advancement", "in_game"), tree, f"the {base} job line", "level",
                      lead=lead)]
    keys = [r["key"] for r in rows("npcs")
            if f"{base} Instructor" in str(r.get("role") or "") or r["npc"] == f"{base} Job Instructor"]
    # in the order they're done: all are level 30, and by name "Finding the Instructor" came before the questline's
    # first step (audit P84A-7)
    quests_ = sorted((r for r in rows("quests") if r["area"] == "Job Advancement" and r["job"] == f"{base} only"),
                     key=lambda r: (_num(r["level"]), _step(r.get("questline")), r["quest"]))
    who = _npc_rows(rows, keys)
    seconds = [(j, lv) for j, lv in jobs.JOBS.get(base, []) if lv == 30]
    if second and s.job == base and seconds:
        # "באיזה לבל עושים ג'וב שני" names no job: the player's class, its 2nd jobs and their level (from memory the AI
        # said level 20, live)
        head = (f"{base}: the 2nd job, at level {seconds[0][1]}, one of {', '.join(j for j, _ in seconds)} "
                f"(the 1st job, {base}, at level {first})")
    else:
        head = (f"{s.job}: the {_ORDINAL.get(rank, '1st')} job, at level {tier if s.job != base else first}"
                + (" (official)" if s.job == base and official.value("first_job_level") else ""))
    lead = (head
            + (f"; the job advancement is with {who[0]['npc']} ({who[0]['map']}, by {who[0]['connects']})" if who else "")
            + (f". The player's {character.base_class} can't change class: a new character is needed"
               if character and character.base_class not in (base, "Beginner") else "") + ".")
    return [Block("npcs", ("npc", "role", "map", "street", "connects", "key", "map_key"), who,
                  f"the {base} instructors", "-", lead=lead),
            # these are the 2nd job's quests: under a 1st-job lead they say so (audit AI-4)
            Block("quests", ("quest", "level", "questline", "npc", "turn_in", "after", "key"), quests_,
                  f"{base} job advancement quests (2nd job)" if second or rank == 2 else
                  f"{base} job advancement quests, for the later 2nd job only (not needed for the 1st job)", "level")]


def _job_ok(job_cell, character, s: Slots) -> bool:
    """A quest's "Thief only" against the class the question is for (named, else the player's)."""
    if not job_cell:
        return True
    if s.job:
        base = _base(s.job) or "Beginner"
    elif character:          # a Thief who is still a Beginner does the "Beginner only" quests
        base = "Beginner" if character.job == "Beginner" else character.base_class
    else:
        return True
    return job_cell.split()[0] == base


def quest_list(rows, s: Slots, character, by_exp: bool) -> Block | None:
    if s.level is None and s.lo is None and not s.area:
        return None
    done = set(getattr(character, "quests_done", None) or ()) if character else set()
    out = []
    for r in rows("quests"):
        lv = _num(r["level"])
        if (s.area and r["area"] != s.area) or r["key"] in done or not _job_ok(r["job"], character, s):
            continue
        if (s.lo is not None and not s.lo <= lv <= s.hi) or (s.level is not None and lv > s.level):
            continue
        out.append(r)
    if by_exp:
        out.sort(key=lambda r: (-_num(r["exp"]), r["quest"]))
    else:
        out.sort(key=lambda r: (-_num(r["level"]), r["quest"]))
    what = ", ".join(x for x in (f"area {s.area}" if s.area else "",
                                 f"level {s.lo}-{s.hi}" if s.lo is not None else
                                 f"level <= {s.level}" if s.level is not None else "",
                                 "job fits" if character or s.job else "", "not done" if done else "") if x)
    return Block("quests", _QUEST_COLS, out, what, "exp desc" if by_exp else "level desc",
                 grep="grep the quests table by its level column")


def spawns_in(rows, s: Slots) -> Block | None:
    if not s.maps:
        return None
    here = [r for r in rows("spawns") if r["map_key"] in s.maps]
    if not here:
        return None
    mons = {r["key"]: r for r in rows("monsters")}
    per: dict[str, dict] = {}
    for r in sorted(here, key=lambda r: -_num(r["count"])):
        m = per.setdefault(r["monster_key"], {"monster": r["monster"], "level": r["level"],
                                              "hp": (mons.get(r["monster_key"]) or {}).get("hp"),
                                              "exp": (mons.get(r["monster_key"]) or {}).get("exp"),
                                              "boss": (mons.get(r["monster_key"]) or {}).get("boss"),
                                              "maps": [], "key": r["monster_key"]})
        m["maps"].append(f"{r['map']} x{r['count']}")
    out = sorted(per.values(), key=lambda m: (_num(m["level"]), m["monster"]))
    for m in out:
        m["maps"] = "; ".join(m["maps"])
    names = sorted({r["map"] for r in here})
    return Block("spawns", ("monster", "level", "hp", "exp", "boss", "maps", "key"), out,
                 "maps " + ", ".join(names[:8]) + (" ..." if len(names) > 8 else ""), "level",
                 note="maps: the map and the monster's spawn count there")


def training_maps(rows, s: Slots, kb) -> Block | None:
    lv = s.level if s.level is not None else (s.lo + s.hi) // 2 if s.lo is not None else None
    if lv is None:
        return None
    lo, hi = (s.lo, s.hi) if s.lo is not None else (max(1, lv - combat.SPOT_BELOW), lv + combat.SPOT_ABOVE)
    info ={r["key"]: r for r in rows("maps")}
    per: dict[str, list[dict]] = {}
    for r in rows("spawns"):
        if (r.get("respawn") or 0) < combat.BOSS_RESPAWN and not combat.special_monster(r["monster"]):
            per.setdefault(r["map_key"], []).append(r)
    out = []
    for key, mobs in per.items():
        m = info.get(key)
        if not m or combat._NOT_GRIND.search(m["map"]) or m.get("street") == "Hidden Street" and not m.get("exp_hr"):
            continue
        count = sum(_num(x["count"]) for x in mobs) or 1
        near = sum(_num(x["count"]) for x in mobs if lo <= _num(x["level"]) <= hi)
        if near / count < NEAR_SHARE:
            continue        # Dangerous Croko II: Lv 21 Jr. Neckis among the Lv 52 Crocos is no Lv 31 map
        # Sleepy Dungeon V was a Lv 31's first map, its EXP/hr from 19 Lv 58 Dark Stone Golems among the spawns
        if any(_num(x["level"]) > hi + DANGER_ABOVE and _num(x["count"]) >= DANGER_SHARE * count for x in mobs):
            continue
        mean = sum(_num(x["level"]) * _num(x["count"]) for x in mobs) / count
        mobs.sort(key=lambda x: -_num(x["count"]))
        out.append({**m, "mob_level": round(mean),
                    "monsters": "; ".join(f"{x['monster']} Lv {x['level']} x{x['count']}" for x in mobs)})
    out.sort(key=lambda r: (-_num(r.get("exp_hr")), -_num(r.get("spawn_points"))))
    guide = plan.spots_for(kb, lv, 5)
    note = ("KB guide's best grind maps for Lv " + str(lv) + ": "
            + "; ".join(f"{g.map} ({g.mob} Lv {g.mob_level}, {g.exp_hr:,} EXP/hr)" for g in guide)) if guide else ""
    return Block("maps", ("map", "street", "mob_level", "exp_hr", "exp_rank", "spawn_points", "monsters", "key"), out,
                 f"most spawns level {lo}-{hi}", "exp_hr desc", grep="grep maps.tsv / spawns.tsv by level",
                 note=note, cap=TRAIN_CAP)


def monsters_by(rows, s: Slots, by_exp: bool) -> Block | None:
    if s.lo is not None:
        lo, hi = s.lo, s.hi
    elif s.level is not None:
        lo, hi = max(1, s.level - combat.SPOT_BELOW), s.level + combat.SPOT_ABOVE
    else:
        return None
    no_boss = bool(NO_BOSS.search(s.question))
    out = [r for r in rows("monsters") if isinstance(r["level"], int) and lo <= r["level"] <= hi
           and not combat.special_monster(r["monster"]) and r.get("maps") and not (no_boss and r.get("boss"))]
    if by_exp:
        out.sort(key=lambda r: (-_num(r["exp"]), r["monster"]))
    else:
        out.sort(key=lambda r: (_num(r["level"]), r["monster"]))
    out = [{**r, "rank": i} for i, r in enumerate(out, 1)]
    cols = ("rank", "monster", "level", "hp", "exp", "hp_per_exp", "acc_needed", "element", "mesos", "boss", "maps",
            "key")
    return Block("monsters", cols, out, f"level {lo}-{hi}" + (", bosses left out" if no_boss else ""),
                 "exp desc" if by_exp else "level", grep="grep monsters.tsv by its level column")


def skills_of(rows, s: Slots, character) -> Block | None:
    job = s.job or (character.job if character and s.personal else None)
    if not job:
        return None
    if job in jobs.JOBS:          # a class: its jobs that are out ("Thief": Thief, Assassin, Bandit)
        wanted = {j for j, _ in jobs.JOBS[job] if j != "Beginner"} or {job}
    elif s.job:
        wanted = {job}
    else:                         # the player's own: their job line so far (Thief and Assassin, never Bandit)
        wanted = {j for j in (character.base_class, job) if j and j != "Beginner"} or {"Beginner"}
    out = [r for r in rows("skills") if r["job"] in wanted]
    if not out:
        return None
    cols = ("skill", "job", "rank", "max_lv", "kind", "mp", "damage", "targets", "cooldown", "element", "weapon",
            "prerequisite", "effect", "key")
    return Block("skills", cols, out, "job " + "/".join(sorted(wanted)), "as listed")


def scrolls(rows, s: Slots) -> Block | None:
    slot_words = [_SCROLL_SLOT.get(v, v).lower() for f in s.families if _kind(f) == "equip" for v in _values(f)]
    stats = [stat for rx, stat in _SCROLL_STAT if rx.search(s.question)]
    if not slot_words and not stats:
        return None
    out = []
    for r in rows("scrolls"):
        if slot_words and str(r.get("slot") or "").lower() not in slot_words:
            continue
        if stats and not any(re.search(rf"(?<!Magic ){re.escape(st)}\b", r["scroll"]) for st in stats):
            continue
        out.append(r)
    out.sort(key=lambda r: (r.get("slot") or "", r["scroll"]))
    what = ", ".join(x for x in ("slot " + "/".join(slot_words) if slot_words else "",
                                 "stat " + "/".join(stats) if stats else "") if x)
    return Block("scrolls", ("scroll", "slot", "grade", "success", "stats", "buy", "seller", "key"), out, what, "slot",
                 grep="grep scrolls.tsv")


def consumables(rows, s: Slots, cheap: bool) -> Block | None:
    types = {v for f in s.families if _kind(f) == "use" for v in _values(f)}
    if not types:
        return None
    # "HP potion": HP per meso, an MP one MP per meso, else both together
    want_hp = bool(re.search(r"\bhp\b|" + _he("חיים"), s.question, re.I))
    want_mp = bool(re.search(r"\bmp\b|" + _he("מאנה|מנה"), s.question, re.I))
    out = []
    for r in rows("consumables"):
        if r["type"] not in types or not _arrows_fit(s, r["item"]):
            continue
        hp = int(r["hp"]) if str(r.get("hp") or "").isdigit() else None
        mp = int(r["mp"]) if str(r.get("mp") or "").isdigit() else None
        if (want_hp and not want_mp and not hp) or (want_mp and not want_hp and not mp):
            continue
        heal = (hp or 0) * (want_hp or not want_mp) + (mp or 0) * (want_mp or not want_hp)
        buy = r.get("buy")
        per = round(heal / buy, 1) if buy and heal else None
        if s.level is not None and _num(r.get("req_lv")) > s.level:
            continue
        out.append({**r, "per_meso": per})
    if cheap:
        out.sort(key=lambda r: (r["per_meso"] is None, -(r["per_meso"] or 0)))
        healed = "HP" if want_hp and not want_mp else "MP" if want_mp and not want_hp else "HP+MP"
        sort = f"per_meso desc ({healed} per meso at the cheapest NPC)"
    else:
        out.sort(key=lambda r: (r["type"], -_num(r.get("req_lv")), r["item"]))
        sort = "type, req_lv desc"
    return Block("consumables", ("item", "type", "hp", "mp", "effect", "req_lv", "buy", "per_meso", "seller", "key"),
                 out, "type " + "/".join(sorted(types)), sort, grep="grep consumables.tsv")


# ---------------------------------------------------------------- intent

def _top(q: str) -> int | None:
    """The row count a question asks for ("top 3"), never a level: "monsters at level 15 most exp" asked for no 15
    rows (audit AI-11)."""
    levels_ = {m.span(g) for rx in (LEVEL, LOOSE_LEVEL, RANGE) for m in rx.finditer(q)
               for g in range(1, rx.groups + 1) if m.group(g)}
    for m in TOP_N.finditer(q):
        g = next(i for i in range(1, TOP_N.groups + 1) if m.group(i))
        if m.span(g) not in levels_:
            return int(m.group(g))
    return None


# "should I grind or quest": a judgement call, no list to fetch (it got 156 quest rows, audit AI-12)
JUDGE = re.compile(r"\bshould\s+(?:i|we)\b[^?]*\bor\b", re.I)


def _equip_fams(s: Slots) -> bool:
    return any(_kind(f) == "equip" for f in s.families)


def plan_for(question: str, kb, character=None, rows=None, reverse: bool = False) -> Plan | None:
    """The intent and its table blocks, or None when the question is no list question this planner is sure of.
    rows: name -> the table's rows (tables.rows on the KB's folder, never building them, by default).
    reverse: brain's "which monsters drop X" (its drop groups answer that)."""
    from .brain import DROP_WORDS
    q = " ".join(question.split())
    if not q or reverse or SCREEN.search(q) or DROP_WORDS.search(q) or DROPPED.search(q):
        return None
    if JUDGE.search(q) and not LISTQ.search(q) and not BEST.search(q):
        return None
    if rows is None:
        def rows(name, _kb=kb):
            return tables.rows(_kb, name, build=False)
    s = slots(q, kb, character, rows)
    quest, give, sell = bool(QUEST.search(q)), bool(GIVE.search(q)), bool(SELL.search(q))
    named_items = [k for k in s.entities if k.startswith("item/")]

    top = _top(q)

    def made(intent: str, *blocks, level=None) -> Plan | None:
        blocks = [b for b in blocks if b is not None]
        return Plan(intent, blocks, level, top if top and 1 <= top <= 20 else None) \
            if any(b.rows for b in blocks) else None

    if BECOME.search(q) and s.job and s.job != "Beginner" and not s.families:
        # "ג'וב שני לגנב", "second job for magician": the class named, its 2nd job asked (it got "Thief: the 1st job")
        return made("job_advance", *job_advance(rows, s, character, second=bool(SECOND_JOB.search(q)), kb=kb))
    # "2nd job" with no job named: the player's own class
    if BECOME.search(q) and not s.job and not s.families and SECOND_JOB.search(q) and character \
            and character.base_class in jobs.JOBS and character.base_class != "Beginner":
        s.job = character.base_class
        return made("job_advance", *job_advance(rows, s, character, second=True, kb=kb))
    if quest:
        if any(k.startswith("npc/") for k in s.entities):
            return made("npc_quests", *npc_quests(rows, s))
        if NEED.search(q) and any(k.startswith(("monster/", "item/")) for k in s.entities):
            return made("quest_needs", quest_needs(rows, s))
        if (give or s.families) and (s.families or named_items):
            return made("quest_rewards", rewards(rows, s))
        # "where do I turn in Sam's Suggestion quest" is about that quest (its page): 40 other quests were noise
        if any(k.startswith("quest/") for k in s.entities) and not QUEST_LIST.search(q):
            return None
        if EXP.search(q) or BEST.search(q) or NOW.search(q) or s.level is not None or s.lo is not None or s.area:
            return made("quests", quest_list(rows, s, character, bool(EXP.search(q) or BEST.search(q))),
                        level=s.level)
        return None
    if SKILL.search(q) and (s.job or (character and s.personal and LISTQ.search(q))) and not named_items:
        return made("skills", skills_of(rows, s, character))
    if USES.search(q) and named_items:
        return made("ingredient_of", *recipes(rows, s, uses=True))
    if CRAFT.search(q) and named_items:
        return made("recipe", *recipes(rows, s, uses=False))
    if sell and (s.families or named_items or any(k.startswith("npc/") for k in s.entities)):
        return made("shops", shops(rows, s, _player_class(s, character)))
    if s.maps and (MONSTER.search(q) or WHATS_IN.search(q)) and not s.families:
        return made("monsters_in_map", spawns_in(rows, s))
    # "איפה יש סטירג'", "where is Jane Doe": every map it is on (the page's map table was cut off in the prompt)
    if WHERE_IS.search(q) and not s.families and not named_items:
        mons = [k for k in s.entities if k.startswith("monster/")]
        npcs = [k for k in s.entities if k.startswith("npc/")]
        if mons:
            return made("where_monster", where_monster(rows, mons))
        if npcs:
            return made("where_npc", where_npc(rows, npcs))
    # "should I grind Blue Snail?" is about that monster: its page answers it. "where do I hunt for exp at 31 with a
    # claw" is a training question too, not the claw list (audit AI-12)
    gear_only = bool(EXP.search(q)) and all(_kind(f) == "equip" for f in s.families)
    if TRAIN.search(q) and (WHERE.search(q) or BEST.search(q) or LISTQ.search(q)) \
            and (not s.families or gear_only) and not s.maps \
            and not any(k.startswith(("monster/", "map/", "item/")) for k in s.entities):
        if character and s.level is None and s.lo is None:
            s.level = int(character.level or 0) or None     # "איפה הכי טוב לגריינד?" says no "לי": still theirs
        b = training_maps(rows, s, kb)
        return made("training_maps", b, level=s.level if s.level is not None else
                    (s.lo + s.hi) // 2 if s.lo is not None else None)
    if MONSTER.search(q) and (EXP.search(q) or s.lo is not None or (s.level is not None and BEST.search(q))) \
            and not s.families and not named_items and not s.maps:
        return made("monsters_by_level", monsters_by(rows, s, bool(EXP.search(q) or BEST.search(q))),
                    level=s.level if s.level is not None else (s.lo + s.hi) // 2 if s.lo is not None else None)
    if "Scroll" in s.families:
        return made("scrolls", scrolls(rows, s))
    if any(_kind(f) == "use" for f in s.families) and not named_items and \
            (CHEAP.search(q) or BEST.search(q) or LISTQ.search(q) or s.said_level):
        return made("consumables", consumables(rows, s, bool(CHEAP.search(q) or HEAL.search(q) and BEST.search(q))))
    if _equip_fams(s) and not named_items and (LISTQ.search(q) or BEST.search(q) or s.said_level):
        return made("equips", _equips_for(rows, s, character))
    return None


def _equips_for(rows, s: Slots, character) -> Block:
    """equips() for the player: a question naming no level or class is about the profile's ("best wand" from a
    Lv 45 Cleric was answered with the Lv 50 Cromi); when that leaves nothing (a Thief asking for wands, a Lv 8
    asking for claws), the class and then the level the question didn't name are dropped."""
    base = _player_class(s, character)
    job = s.job if s.job else character.job if character and base and base == character.base_class else None
    # another class named ("best weapon for a fighter" from a Lv 31 Assassin): not the player's level either; a
    # Beginner asking about the class they're about to pick still is (a Lv 8 got a Lv 70 sledge, review2 LOG-5)
    if not character or s.said_level or (s.job and character.base_class not in ("", "Beginner")
                                          and base != character.base_class):
        return equips(rows, s, base, job)
    level = int(character.level or 0) or None
    mine = base or character.base_class or None
    slots_ = {v for f in s.families if _kind(f) == "equip" for v in _values(f)}
    if not base and slots_ <= set(WEAPONS) and not slots_ & CLASS_WEAPONS.get(mine, set(WEAPONS)):
        mine = None             # a Thief asking for wands: the wands, not "job Thief or Any"
    for cls, lv in ((mine, level), (base, level), (base, None)):
        b = equips(rows, s, cls, job if cls == base else character.job, lv)
        if b.rows:
            return b
    return b


def context(question: str, kb, character=None, rows=None, reverse: bool = False) -> tuple[str, Plan | None]:
    """The prompt block for a question ("" when the planner has nothing sure to add), and its plan."""
    try:
        p = plan_for(question, kb, character, rows, reverse)
    except Exception:          # noqa: BLE001 - the planner only adds context: a bug in it never stops a question
        import logging
        logging.getLogger("maplehelper").warning("query planner failed", exc_info=True)
        return "", None
    return (p.render(), p) if p else ("", None)
