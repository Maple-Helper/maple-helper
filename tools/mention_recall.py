"""Entity recall of KnowledgeBase.find_mentions on realistic player questions, against the real KB (data/kb).

    python tools/mention_recall.py            # recall / precision / timing, and every miss and false positive

brain.build_prompt pre-fetches the pages of the entities find_mentions finds in a question: found, the answer
takes 3-14 s with no tool call; missed, the AI greps and reads pages for 20 s - 4 min. CASES are questions as
players type them (Hebrew and English, slang, typos, partial names, plurals, comparisons), each with the KB names
it really says; NEGATIVES name no entity at all (everyday words that collide with entity names, ambiguous partial
names that must not be guessed). tests/test_mention_recall.py holds the numbers to what this measures.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KB = ROOT / "data" / "kb"

# question -> the KB names it says (an entity a name is shared by, "Haste" or "Mano", counts by its name)
CASES: list[tuple[str, list[str]]] = [
    # ---- English: partial names, short forms, lowercase, missing punctuation
    ("Ilbi vs Subi", ["Ilbi Throwing Stars", "Subi Throwing Stars"]),
    ("how much do ilbi cost", ["Ilbi Throwing Stars"]),
    ("who drops subi throwing stars", ["Subi Throwing Stars"]),
    ("is arrow bomb worth it for hunter", ["Arrow Bomb: Bow", "Hunter"]),
    ("steely or ilbis for a lv 30 sin", ["Steely Throwing Knives", "Ilbi Throwing Stars"]),
    ("jr boogie hp", ["Jr. Boogie 1"]),
    ("where do jr balrogs spawn", ["Jr. Balrog"]),
    ("what does orange mushroom drop", ["Orange Mushroom"]),
    ("best place to farm red potions", ["Red Potion"]),
    ("how do i get to pig beach", ["The Pig Beach"]),
    ("hermit or chief bandit at 70?", ["Hermit skills", "Chief Bandit skills"]),
    ("ranger vs sniper which is better", ["Ranger skills", "Sniper skills"]),
    ("amazons judgement or arrow bomb", ["Amazon's Judgement", "Arrow Bomb: Bow"]),
    ("is lucky seven better than double stab", ["Lucky Seven", "Double Stab"]),
    ("what level is tick tock", ["Tick-Tock"]),
    ("ticktock drops", ["Tick-Tock"]),
    ("zombie lupins exp", ["Zombie Lupin"]),
    ("where to find evil eyes and curse eyes", ["Evil Eye", "Curse Eye"]),
    ("compare mano, mushmom, king slime, jr balrog and crimson balrog hp",
     ["Mano", "Mushmom", "King Slime", "Jr. Balrog", "Crimson Balrog"]),
    ("who drops wild kargo eye", ["Wild Kargo Eye"]),
    ("where is athena", ["Athena Pierce"]),
    ("where can i find grendel", ["Grendel the Really Old"]),
    ("power strike vs slash blast", ["Power Strike", "Slash Blast"]),
    ("magic claw or energy bolt at level 10", ["Magic Claw", "Energy Bolt"]),
    ("how do i get to sleepywood from henesys", ["Sleepywood", "Henesys"]),
    ("how far is kerning from perion", ["Kerning City", "Perion"]),
    ("how much does a mana elixir cost", ["Mana Elixir"]),
    ("work gloves stats", ["Work Gloves"]),
    ("soul arrow bow mp cost", ["Soul Arrow: Bow"]),
    ("final attack for fighter?", ["Fighter"]),
    ("fire arrow or poison breath", ["Fire Arrow", "Poison Breath"]),
    ("where is dances with balrog", ["Dances with Balrog"]),
    ("where is mr goldstein", ["Mr. Goldstein"]),
    ("bronze identity stats", ["Bronze Identity"]),
    ("cold eye hp", ["Cold Eye"]),
    ("horny mushrooms or zombie mushrooms for exp", ["Horny Mushroom", "Zombie Mushroom"]),
    ("what lvl for ant tunnel park", ["Ant Tunnel Park"]),
    ("copper drakes vs drakes", ["Copper Drake", "Drake"]),
    ("jr necki skin drop rate", ["Jr. Necki Skin"]),
    ("wolbi vs mokbi throwing stars", ["Wolbi Throwing Stars", "Mokbi Throwing Stars"]),
    ("hwabi price", ["Hwabi Throwing Stars"]),
    ("what does shadow partner do", ["Shadow Partner"]),
    ("teleport mp cost", ["Teleport"]),
    ("haste for assassin", ["Haste", "Assassin"]),
    ("iron arrow for crossbowman", ["Iron Arrow: Crossbow", "Crossbowman"]),
    ("stone golems or dark stone golems", ["Stone Golem", "Dark Stone Golem"]),
    ("where do i buy steely", ["Steely Throwing Knives"]),
    ("red snail shells drop", ["Red Snail Shell"]),
    ("mithril ore and bronze ore where", ["Mithril Ore", "Bronze Ore"]),
    ("elixir vs power elixir", ["Elixir", "Power Elixir"]),
    ("orange potions or white potions", ["Orange Potion", "White Potion"]),
    ("shroom or snail exp", ["Shroom", "Snail"]),
    ("compare red snail, blue snail, slime, pig, orange mushroom and stump",
     ["Red Snail", "Blue Snail", "Slime", "Pig", "Orange Mushroom", "Stump"]),
    ("hunter vs crossbowman vs assassin vs bandit vs cleric dps",
     ["Hunter", "Crossbowman", "Assassin", "Bandit", "Cleric"]),
    ("amazon judgement level", ["Amazon's Judgement"]),
    ("lupins banana", ["Lupin's Banana"]),
    ("is the white knight good", ["White Knight skills"]),
    ("where is the cave of evil eye", []),     # I-IV: a guess would be wrong half the time (precision)
    # ---- Hebrew, and Hebrew with English names
    ("מה ההבדל בין אילבי לסובי", ["Ilbi Throwing Stars", "Subi Throwing Stars"]),
    ("כמה עולה Ilbi", ["Ilbi Throwing Stars"]),
    ("מי מפיל subi", ["Subi Throwing Stars"]),
    ("איפה מוצאים את מאנו", ["Mano"]),
    ("מה מפיל חזיר בר", ["Wild Boar"]),
    ("כמה חיים יש לג'וניור בלרוג", ["Jr. Balrog"]),
    ("איך מגיעים מהנסיס לסליפיווד", ["Henesys", "Sleepywood"]),
    ("מה עדיף arrow bomb או amazon's judgement", ["Arrow Bomb: Bow", "Amazon's Judgement"]),
    ("השוואה בין מאנו, מושמום, קינג סליים, ג'וניור בלרוג ובלרוג אדום",
     ["Mano", "Mushmom", "King Slime", "Jr. Balrog", "Crimson Balrog"]),
    ("איפה לעשות גריינד על לופינים", ["Lupin"]),
    ("Hermit או Chief Bandit ברמה 70", ["Hermit skills", "Chief Bandit skills"]),
    ("כמה נזק עושה lucky seven", ["Lucky Seven"]),
    ("מה הדרופים של עין מקוללת", ["Curse Eye"]),
    ("איך מגיעים לחוף החזירים", ["The Pig Beach"]),
    ("steely או ilbi", ["Steely Throwing Knives", "Ilbi Throwing Stars"]),
    ("איך מגיעים לאלינה", ["Ellinia"]),
    ("כמה עולה red potion בהנסיס", ["Red Potion", "Henesys"]),
    ("מי זה athena pierce", ["Athena Pierce"]),
    ("מה עושה power strike", ["Power Strike"]),
    ("סקיל magic claw כמה mp", ["Magic Claw"]),
    ("איפה מוצאים סטון גולם", ["Stone Golem"]),
    ("פטריה כתומה או פטריה ירוקה", ["Orange Mushroom", "Green Mushroom"]),
    ("כמה אקספי נותן קולד איי", ["Cold Eye"]),
    ("שווה לקנות work gloves?", ["Work Gloves"]),
    ("jr boogie או jr wraith", ["Jr. Boogie 1", "Jr. Wraith"]),
    ("איפה zombie lupins", ["Zombie Lupin"]),
    ("מה עדיף hermit או ranger", ["Hermit skills", "Ranger skills"]),
    ("אילבי או סטילי", ["Ilbi Throwing Stars", "Steely Throwing Knives"]),
    ("לאקי סבן או דאבל סטאב", ["Lucky Seven", "Double Stab"]),
    ("כמה נזק עושה פאוור סטרייק", ["Power Strike"]),
    ("מה יותר טוב, הרמיט או צ'יף בנדיט", ["Hermit skills", "Chief Bandit skills"]),
    ("איפה גרנדל", ["Grendel the Really Old"]),
    ("השוואה: סליים, חזיר, תמנון, פטריה כתומה, גדם", ["Slime", "Pig", "Octopus", "Orange Mushroom", "Stump"]),
    ("כמה עולה מאנה אליקסיר", ["Mana Elixir"]),
    # the Anvil is the game's Smithing station (an NPC since the official launch): the player means it
    ("i need an anvil to craft", ["Anvil"]),
]

# questions that name no entity: everyday words that are (or start) entity names, and partial names that more than
# one entity share (a guess is a wrong page in the prompt and a wrong card)
NEGATIVES: list[str] = [
    "what is the max level", "is my max hp too low", "how do i get more mesos", "the river looks nice from here",
    "is it going to rain in game", "should i use silver or gold", "how do i exit the game", "i need a break from crafting",
    "where can i sell my stuff", "is this the right map", "which skill should i max first",
    "what's a good training spot", "lucky me, i got a rare drop", "dark is my favorite theme",
    "i keep spinning in circles", "how do i return to town", "magic is so cool", "steel or wood for the house",
    "where are the mushrooms", "who is the best player", "my iron is low", "final boss when?",
    "soul arrow worth it?", "is the final attack skill good", "where is the ant tunnel", "dark marble for 2nd job",
    "omok piece price", "return scroll price", "how many stars do i need", "golden hour event when",
    "is the ticket expensive", "the shadow of the tree", "a power nap", "how do i get to victoria island",
    "is florina road safe", "the summer event is fun", "i work all day", "who is nemi", "when is zakum coming",
    "אני רוצה לקנות סטים חדשים", "יש לי שאלה על הסקילים", "מה עדיף, לקנות או לחכות", "אני בפארק עם החברים",
    "הסוס שלי רץ מהר", "אני צריך לסובב את המצלמה", "אילו כוכבים הכי טובים", "מה יותר טוב, חרב או גרזן",
    "כמה זמן לוקח להגיע לרמה 30", "האם השרת עולה היום", "נשארו לי 5 שעות", "מה אורך הבאף", "זה מאריך את הבאף",
    "המוב שלפניך", "משלים את הקווסט", "זה נוראי", "אני הולך לישון עכשיו", "מישהו רוצה לעשות פארטי?",
    "תודה רבה על העזרה", "איך מורידים את הלאג", "המחשב שלי איטי", "החבר שלי קנה סקין חדש", "יש לי בעיה עם הצ'אט",
]
SENTENCES = ROOT / "tests" / "fixtures" / "hebrew_gamer_sentences.txt"
# the fixture's sentences that do say a game name (tests/test_data_fixes.py's list)
SENTENCE_NAMES = {
    "כמה אייץ' פי יש לחילזון": ["Snail"], "כמה אייץ' פי יש למאנו": ["Mano"],
    "אני רוצה ללכת לקרנינג": ["Kerning City"], "זה כמו בטי בופ": ["Betty"], "נבה זה שם יפה": ["Neve"],
    "רנה שלחה לי הודעה": ["Rene"], "יש לי חזיר בבית": ["Pig"], "יש פה עין מרושעת": ["Evil Eye"],
    "ראיתי סרט על זומבי קטן": ["Minor Zombie"], "זה שעון רפאים?": ["Phantom Watch"],
    "תמנון זה חיה מגניבה": ["Octopus"], "מה ההבדל בין מג' לקלריק": ["Cleric"],
}


def cap_for(question: str) -> int:
    """The number of mentions build_prompt asks for (brain.mention_cap when it exists, 4 before it did)."""
    from maplehelper import brain
    return brain.mention_cap(question) if hasattr(brain, "mention_cap") else 4


def measure(kb, cases=CASES, negatives=NEGATIVES) -> dict:
    """{"recall", "precision", "expected", "found", "right", "misses": {q: [names]}, "wrong": {q: [names]},
    "ms": mean milliseconds per find_mentions call}. Negatives count as questions expecting nothing."""
    fixture = [s.strip() for s in SENTENCES.read_text(encoding="utf-8").splitlines() if s.strip()] \
        if SENTENCES.exists() else []
    every = list(cases) + [(q, []) for q in negatives] + [(s, SENTENCE_NAMES.get(s, [])) for s in fixture]
    expected = found = right = 0
    misses: dict[str, list[str]] = {}
    wrong: dict[str, list[str]] = {}
    spent = 0.0
    for q, want in every:
        cap = cap_for(q)
        t0 = time.perf_counter()
        keys = kb.find_mentions(q, cap)
        spent += time.perf_counter() - t0
        got = [kb.get(k)["name"] for k in keys]
        hit = [n for n in want if n in got]
        expected += len(want)
        found += len(got)
        right += len(hit)
        if len(hit) < len(want):
            misses[q] = [n for n in want if n not in got]
        if extra := [n for n in got if n not in want]:
            wrong[q] = extra
    return {"recall": right / expected if expected else 1.0, "precision": right / found if found else 1.0,
            "expected": expected, "found": found, "right": right, "misses": misses, "wrong": wrong,
            "ms": spent / len(every) * 1000, "questions": len(every)}


def main() -> int:
    from maplehelper.kb import KnowledgeBase
    t0 = time.perf_counter()
    kb = KnowledgeBase(KB)
    kb.find_mentions("warm up")          # the lazy indexes are built once, on the first call
    built = (time.perf_counter() - t0) * 1000
    r = measure(kb)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"{len(CASES)} questions with names, {len(NEGATIVES)} + fixture sentences without: {r['questions']} in all")
    print(f"recall {r['recall']:.1%} ({r['right']}/{r['expected']})   precision {r['precision']:.1%} "
          f"({r['right']}/{r['found']})   {r['ms']:.2f} ms a question (KB load + indexes {built:.0f} ms)")
    for q, names in r["misses"].items():
        print(f"  missed  {q!r}: {names}")
    for q, names in r["wrong"].items():
        print(f"  WRONG   {q!r}: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
