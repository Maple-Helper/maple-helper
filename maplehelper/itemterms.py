"""An item's kind and stat lines in Hebrew: NiaMeowDB writes them in English ("Etc · Monster Drop", "NPC Sell-back
(per unit) 2 mesos", "REQ LEV 35 REQ STR 25 JOB Bowman"), and a Hebrew app shows everything in Hebrew. A fixed set:
every line shape the knowledge base has (97 of them) is covered here; a word this doesn't know stays as it is.
Stats, jobs and "mesos" stay in English, as everywhere in the app (STR, W.DEF, Bowman)."""
from __future__ import annotations

import re

# the kind's parts ("Equip · Shield", the card's "Etc / Monster Drop")
KIND_HE = {
    "Equip": "ציוד", "Use": "שימוש", "Etc": "שונות", "Setup": "התקנה", "Cash": "Cash", "Cash Equip": "ציוד Cash",
    "Scroll": "מגילה", "Arrow": "חצים", "Box": "קופסה", "Buff": "חיזוק", "EXP Coupon": "קופון EXP", "Food": "אוכל",
    "Meso Pouch": "שקיק mesos", "Misc": "שונות", "Pet Food": "אוכל לחיית מחמד", "Potion": "שיקוי",
    "Quest Item": "פריט קווסט", "Return Scroll": "מגילת חזרה", "Summoning Sack": "שק זימון",
    "Throwing Star": "כוכב זריקה", "Crafting Material": "חומר קראפטינג", "Gem": "אבן חן",
    "Monster Drop": "דרופ ממפלצת", "Ore & Mineral": "עפרות ומינרלים", "Stimulator": "מאיץ",
    "One-Handed Sword": "חרב ליד אחת", "Dagger": "פגיון", "One-Handed Axe": "גרזן ליד אחת",
    "One-Handed Blunt Weapon": "נשק קהה ליד אחת", "Two-Handed Sword": "חרב לשתי ידיים",
    "Two-Handed Axe": "גרזן לשתי ידיים", "Two-Handed Blunt Weapon": "נשק קהה לשתי ידיים", "Spear": "חנית",
    "Polearm": "רומח", "Wand": "שרביט", "Staff": "מטה", "Bow": "קשת", "Crossbow": "קשת צולבת", "Claw": "טופר",
    "Hat": "כובע", "Cape": "גלימה", "Earring": "עגיל", "Shield": "מגן", "Top": "חולצה", "Overall": "אוברול",
    "Bottom": "חלק תחתון", "Shoes": "נעליים", "Gloves": "כפפות", "Coupon": "קופון", "Megaphone": "מגפון",
    "Pet": "חיית מחמד", "Pet Skill": "סקיל לחיית מחמד", "Storage": "אחסון", "Store Permit": "רישיון חנות",
    "Weather Effect": "אפקט מזג אוויר", "Other": "אחר", "Cash Hat": "כובע Cash",
    "Cash Face Accessory": "אביזר פנים Cash", "Cash Eye Accessory": "אביזר עיניים Cash", "Cash Top": "חולצה Cash",
    "Cash Overall": "אוברול Cash", "Cash Bottom": "חלק תחתון Cash", "Cash Shoes": "נעליים Cash",
    "Cash Cape": "גלימה Cash", "Cash Ring": "טבעת Cash", "Cash Weapon": "נשק Cash", "Cash Earring": "עגיל Cash",
    "Cash Gloves": "כפפות Cash", "Cash Shield": "מגן Cash", "Cash Pet Equip": "ציוד לחיית מחמד Cash",
    "Monster": "מפלצת", "Boss Monster": "בוס",
}
_KIND_SPLIT = re.compile(r"\s*(·|/)\s*")


def kind(text: str, lang: str) -> str:
    """"Etc · Monster Drop" -> "שונות · דרופ ממפלצת" in Hebrew; a part it doesn't know stays as it is."""
    if lang != "he" or not text:
        return text
    return "".join(" · " if p in ("·", "/") else KIND_HE.get(p, p) for p in _KIND_SPLIT.split(text.strip()))


# a stat line's words, longest first (a line is replaced piece by piece; numbers and stat names stay)
_PHRASES = [
    ("NPC Sell-back (per unit)", "מחיר מכירה ל-NPC (ליחידה)"),
    ("NPC Sell-back", "מחיר מכירה ל-NPC"),
    ("Max per Stack", "מקסימום בערימה"),
    ("Upgrade Slots", "סלוטים לשדרוג"),
    ("Attack Speed Faster", "מהירות התקפה: מהירה מאוד"),
    ("Attack Speed Fast", "מהירות התקפה: מהירה"),
    ("Attack Speed Normal", "מהירות התקפה: רגילה"),
    ("Attack Speed Slow", "מהירות התקפה: איטית"),
    ("Special Restrictions", "הגבלות:"),
    ("Special Restriction", "הגבלה:"),
    ("Time-limited", "מוגבל בזמן"),
    ("Cannot be dropped", "אי אפשר לזרוק"),
    ("Quest item", "פריט קווסט"),
    ("Required Ammunition", "תחמושת נדרשת:"),
    ("Throwing Stars", "כוכבי זריקה"),
    ("Arrows for Crossbows", "חצים לקשת צולבת"),
    ("Arrows for Bows", "חצים לקשת"),
    ("Required Level", "רמה נדרשת"),
    ("Shield Guard Chance", "סיכוי לחסום עם המגן"),
    ("Eligible regular physical hits only", "רק נגד מכות פיזיות רגילות"),
    ("Hunger Rate", "קצב רעב"),
    ("Pets range from", "בחיות המחמד: מ-"),
    ("Duration", "משך"),
    ("Lifespan", "תוחלת חיים"),
    ("Recharge Cost", "עלות מילוי"),
    ("mesos / unit", "mesos ליחידה"),
    ("Attack Power", "כוח התקפה"),
    ("Footing No sliding on ice", "אחיזה: לא מחליקים על קרח"),
    ("Fits over", "מתאים מעל"),
    ("EXP Bonus", "בונוס EXP"),
    ("Fullness", "שובע"),
    ("Use Cooldown", "זמן המתנה בין שימושים"),
    ("HP Recovery", "שחזור HP"),
    ("MP Recovery", "שחזור MP"),
    ("Recovery", "שחזור"),
    ("Shared with", "משותף עם"),
    ("Closed test only. Likely", "רק בטסט הסגור. כנראה"),
    ("days at launch", "ימים בהשקה"),
    ("REQ LEV", "רמה נדרשת"),
    ("REQ FAME", "פיים נדרש"),
    ("JOB", "· ג'וב:"),
    ("1H Sword", "חרב ליד אחת"), ("1H Axe", "גרזן ליד אחת"), ("1H Blunt", "נשק קהה ליד אחת"),
    ("2H Sword", "חרב לשתי ידיים"), ("2H Axe", "גרזן לשתי ידיים"), ("2H Blunt", "נשק קהה לשתי ידיים"),
    ("Sword", "חרב"), ("Dagger", "פגיון"), ("Wand", "שרביט"), ("Staff", "מטה"), ("Bow", "קשת"),
    ("Crossbow", "קשת צולבת"), ("Claw", "טופר"), ("Spear", "חנית"),
    (" & ", " ו"), (" and ", " ו"),
]
_REQ_STAT = re.compile(r"\bREQ (STR|DEX|INT|LUK) (\d+)")
_UNITS = [(re.compile(r"(\d) min\b"), r"\1 דק'"), (re.compile(r"(\d) days\b"), r"\1 ימים"),
          (re.compile(r"(\d)s\b"), r"\1 שנ'"), (re.compile(r"(\d)m\b"), r"\1 דק'"),
          (re.compile(r"(\d) to (\d)"), r"\1 עד \2"), (re.compile(r"\bx(\d)"), r"פי \1"),
          (re.compile(r"(?<=\s)ו(?=[^א-ת\s])"), "ו-"),      # "ו" before a number or English letters takes a hyphen
          (re.compile(r"מ- (\d)"), r"מ-\1")]


def stat_line(line: str, lang: str) -> str:
    """One stat line in Hebrew ("REQ LEV 35 REQ STR 25 JOB Bowman" -> "רמה נדרשת 35 · STR נדרש 25 · ג'וב: Bowman")."""
    if lang != "he" or not line:
        return line
    s = _REQ_STAT.sub(lambda m: f"· {m.group(1)} נדרש {m.group(2)}", line)
    # "Crossbow" before "Bow": the table is longest-first for each family, so check whole words
    for en, he in _PHRASES:
        if en.strip() in ("&", "and"):
            s = s.replace(en, he)
        else:
            s = re.sub(rf"(?<![\w-]){re.escape(en)}(?![\w-])", he, s)
    for rx, he in _UNITS:
        s = rx.sub(he, s)
    return re.sub(r"\s+", " ", s).strip()


def tradeable(text: str, lang: str) -> str:
    he = {"Tradeable": "אפשר לסחור בו", "Untradeable": "אי אפשר לסחור בו"}
    return he.get(text, text) if lang == "he" else text
