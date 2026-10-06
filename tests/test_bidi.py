"""Mixed Hebrew/English rendering, measured on real glyph positions from Qt's text engine.

Each case lists pairs (a, b) that must satisfy: a is drawn to the LEFT of b.
In RTL text, brackets are mirrored, so the logical "(" is drawn on the right.

Run: .venv\\Scripts\\python -m pytest tests -q
"""
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication

from maplehelper import bidi

app = QApplication.instance() or QApplication(sys.argv)

CASES = [
    ("בונוס +5 STR לכובע.", [("+", "5"), ("5", "S")]),
    ("עליתי ללבל 33!", [("!", "3"), ("3", "ע")]),
    ("מחובר כ-name95@gmail.com", [("n", "@"), ("@", "g"), ("m", "מ")]),
    ("אפשר לבחור את ה-AI: Claude או Codex.", [("C", "A"), ("C", ":")]),   # AI, then ':', then Claude
    ("מתאפסת בשעה 11:41 היום.", [("1", ":"), (":", "4")]),
    ("נוצלו ב-84% מהמכסה.", [("8", "-"), ("-", "ב"), ("8", "%")]),       # the prefix hyphen is not a minus
    ("קיבלת מ-1,000 ל-500 mesos.", [("1", "-"), ("5", "m")]),
    ("עולה ב-$79.99 בחנות.", [("$", "7"), ("7", "-")]),
    ("לבל 31–35: Line 2 <Area 1> ממש טובה", [("L", "·"), ("·", "A")]),   # shown as "Line 2 · Area 1"
    ("סיום, בואו נשחק!", [("!", "ס")]),
    ("סיכוי 10–20% לדרופ.", [("1", "–"), ("–", "%")]),
    ("מפלצת (Lv. 10) חזקה.", [("L", "1"), (")", "1"), ("L", "(")]),
    ("תלך ל-Red Snail.", [(".", "R"), ("R", "S")]),
    ("בונוס של 5% ל-HP ו-MP.", [(".", "M"), ("M", "ו"), ("H", "P")]),
    ("Red Snail הוא יעד טוב.", [("R", "S"), (".", "R")]),
    ("המפה Henesys Hunting Ground I היא הכי טובה", [("H", "G"), ("G", "I")]),
    ("לחץ F9 כדי לפתוח ו-F10 כדי לדבר.", [(".", "F")]),
    ("צריך 30 EXP כדי לעלות לבל.", [(".", "צ"), ("3", "E")]),
    ("ה-Orange Mushroom מפיל Mushroom Cap?", [("?", "M"), ("O", "M")]),
    ("עלית ללבל 35! עכשיו לך ל-Perion.", [(".", "P"), ("3", "5")]),
    ("הסקיל Power Strike (Lv. 20) הכי חשוב.", [("P", "S"), ("L", "2")]),
    ("מחיר: 1,500 mesos בחנות.", [("1", "5"), ("5", "m")]),
    ("בכפפות האלה יש ATT +3 ו-DEF +10.", [("A", "3"), (".", "D")]),
    ("נמצא ב-The Forest North of Ellinia (Victoria Road).", [(".", "T"), ("T", "V"), ("E", "R")]),
    ("כדאי לגרינד על Axe Stump (לבל 17, 371 HP, 32 EXP).", [("A", "S"), ("3", "H"), (".", "E")]),
    ("המפות East Rocky Mountain II/III מעולות.", [("E", "M"), ("M", "/")]),
    ("קיבלת 1,250 mesos ו-3 Red Potion.", [(".", "3"), ("R", "P"), ("1", "m")]),
    ("עלות: 400 mesos לכל Blue Potion", [("4", "m"), ("B", "P")]),
    ("לך ל-Kerning City ותדבר עם Dark Lord.", [(".", "D"), ("K", "C"), ("D", "L")]),
    ("הדרופ של Mano הוא Snail Shell (100%).", [("S", "l"), ("1", "%")]),
    ("ב-Lv. 30 עושים ג'וב שני.", [("L", "3")]),
    ("צריך STR 35 ו-DEX 25 לנשק הזה.", [("S", "3"), ("D", "2")]),
    ("המקש F9 פותח, F10 מדבר.", [("F", "9")]),
    ("הסקיל Magic Claw גורם 2x נזק.", [("M", "C"), ("2", "x")]),
    ("אחרי Perion תעבור ל-Ellinia (Magician).", [("E", "M")]),
    ("NPC בשם Sera נמצאת ב-Maple Island.", [("M", "I")]),
    ("זמן ריספון: ~7.5s במפה.", [("7", "s")]),
    ("המפה Henesys Hunting Ground II מלאה ב-Orange Mushroom.", [("H", "G"), ("O", "M")]),
    ("מחיר ב-FM: 2.5m עד 3m.", [("2", "m")]),
    ("השתמש ב-Power Elixir כשה-HP מתחת ל-30%.", [("P", "E"), ("3", "%")]),
    ("ה-ACC שלך 45 וה-AVOID של המפלצת 20.", [("A", "C")]),
    ("הקווסט Mai's Training דורש Lv 3+.", [("M", "T"), ("L", "3")]),
    ("יש לך 3/5 Snail Shell.", [("3", "/"), ("/", "5"), ("S", "h")]),
    ("עשית 12,345 נזק ב-Lucky Seven!", [("1", "5"), ("L", "S")]),
    ("בחר בין Fighter, Page או Spearman.", [("F", "r")]),
    ("תקנה Work Gloves ב-Henesys Armor Store.", [("W", "G"), ("H", "A")]),
    ("EXP לשעה: בערך 20k-25k.", [("2", "k")]),
    ("יש Scroll for Gloves for ATT 60% בחנות?", [("S", "G"), ("6", "%")]),
    ("המפלצת Jr. Necki מופיעה ב-Swamp Region.", [("J", "N"), ("S", "R")]),
    ("השרת Scania עמוס, נסה Bera.", [("S", "c")]),
    ("הלבל שלך: 42 (Hermit בעוד 28 לבלים).", [("H", "t")]),
    ("התשובה: לא, Orange Mushroom לא מפיל Maple Leaf.", [("O", "M"), ("L", "O")]),
    ("תעשה Party Quest ב-Kerning (KPQ) מלבל 21.", [("P", "Q"), ("K", "P")]),
    ("יחס HP/EXP של 8.50 זה מצוין.", [("H", "/"), ("8", "5")]),
    ("משחקים ב-1920x1080 במצב Borderless.", [("1", "x"), ("B", "s")]),
    ("הפריט Blue Diamond Throwing-Stars עולה הרבה.", [("B", "D"), ("T", "S")]),
    ("עברת מ-Southperry ל-Lith Harbor.", [("L", "H"), (".", "L")]),
]


def glyph_x(text: str) -> dict[int, float]:
    lay = QTextLayout(text, QFont("Arial", 20))
    opt = QTextOption()
    opt.setTextDirection(Qt.RightToLeft)
    lay.setTextOption(opt)
    lay.beginLayout()
    line = lay.createLine()
    line.setLineWidth(3000)
    lay.endLayout()
    # one character at a time: with font fallback (Rubik + Segoe UI) the string indexes of a whole-line
    # glyph run come back shifted, so a whole-line map misses characters and places others wrongly
    flags = QTextLayout.GlyphRunRetrievalFlag.RetrieveGlyphPositions
    pos: dict[int, float] = {}
    for i in range(len(text)):
        xs = [p.x() for run in lay.glyphRuns(i, 1, flags) for p in run.positions()]
        if xs:
            pos[i] = min(xs)
    return pos


@pytest.mark.parametrize("sentence,checks", CASES)
def test_mixed_sentence_renders_in_reading_order(sentence, checks):
    assert bidi.direction(sentence) == "rtl"
    shown = bidi.isolate_ltr_runs(sentence)
    x = glyph_x(shown)
    for a, b in checks:
        assert x[shown.index(a)] < x[shown.index(b)], f"{a!r} should be left of {b!r} in {sentence!r}"


def test_direction_per_paragraph():
    assert bidi.direction("where is Henesys?") == "ltr"
    assert bidi.direction("איפה Henesys?") == "rtl"
    assert bidi.direction("Red Snail הוא יעד טוב לגרינד") == "rtl"


def test_html_direction_follows_the_message():
    he = bidi.to_html("**Red Snail** הוא יעד טוב.\n• HP: 371\nThis drop is really rare in Classic.")
    assert he.count('dir="rtl"') == 2 and he.count('dir="ltr"') == 1 and "<b>" in he
    en = bidi.to_html("Go grind **Red Snail** now.\nIt drops Red Potion.")
    assert 'dir="rtl"' not in en


# lines inside a Hebrew answer: the message decides the direction (RTL), and punctuation between
# two English/number blocks must stay in the Hebrew flow instead of gluing the blocks together
HEBREW_MESSAGE_LINES = [
    ("Mano (לבל 20) - Subi Throwing Stars", [("S", "2"), ("2", "ל"), ("ל", "M")]),
    ("Fire Boar (לבל 32, The Burnt Land) - Wolbi Throwing Stars", [("W", "3"), ("3", "ל"), ("ל", "F")]),
    ("Red Snail, Blue Snail", [("B", "R")]),
]


def _reading_order(shown: str) -> str:
    """The visible characters of a laid-out line, left to right."""
    x = glyph_x(shown)
    marks = (bidi.RLM, bidi.LRE, bidi.PDF, bidi.LRI, bidi.PDI)
    return "".join(ch for _, ch in sorted((x[i], ch) for i, ch in enumerate(shown)
                                          if i in x and ch.strip() and ch not in marks))


def test_english_quest_name_with_brackets_stays_whole():
    name = "[Construction Site B1] Shumi's Lost Coin"
    # run by run, the brackets went to the other end: "Shumi'sLostCoin]ConstructionSiteB1["
    assert _reading_order(bidi.ltr_name(name, True)) == name.replace(" ", "")
    # inside a Hebrew sentence too ("After [Construction Site B1] Shumi's Lost Coin")
    sentence = bidi.isolate_ltr_runs("אחרי הקווסט " + bidi.ltr_block(name, True))
    assert _reading_order(sentence).startswith(name.replace(" ", ""))
    assert bidi.ltr_name("קווסט Mai's Training", True) == bidi.plain("קווסט Mai's Training", True)
    assert bidi.ltr_name(name, False) == name


# KB names whose ", " / ": " / "[ ]" split the English runs: "Tree Dungeon, Monkey Forest I" came out as
# "Monkey Forest I, Tree Dungeon" and "Final Attack: Sword" as "Sword: Final Attack" in a Hebrew answer
KB_NAMES = ["Tree Dungeon, Monkey Forest I", "[Construction Site B1] Shumi's Lost Coin", "Final Attack: Sword",
            "One-Handed Sword Attack Scroll: Lesser", "MapleStory Classic Warrior Guide: Lv 1-30"]
NAME_SENTENCES = ["הכי כדאי לאמן ב-{} או בבית.", "את הקווסט {} לוקחים מ-Shumi.", "בלבל 30 כדאי ללמוד {}.",
                  "{} הוא הכי טוב", "קראו את {}, Red Snail ו-Blue Snail."]


@pytest.fixture
def kb_names():
    bidi.set_names(KB_NAMES + ["Henesys", "קווסט בעברית: שם"])
    yield
    bidi.set_names([])


@pytest.mark.parametrize("name", KB_NAMES)
@pytest.mark.parametrize("sentence", NAME_SENTENCES)
def test_kb_name_with_punctuation_stays_whole_in_hebrew(kb_names, name, sentence):
    line = sentence.format(name)      # a line of a Hebrew answer: RTL (paragraph_direction)
    assert bidi.paragraph_direction(line, "rtl") == "rtl"
    shown = bidi.isolate_ltr_runs(line)
    order = _reading_order(shown)
    assert name.replace(" ", "") in order, f"{name!r} scrambled in {line!r}: {order}"
    if "Red Snail" in line:   # the other English names in the line still read in Hebrew order
        assert order.index("BlueSnail") < order.index("RedSnail")


def test_kb_names_in_answer_html(kb_names):
    body = bidi.to_html("הכי כדאי לאמן ב-Tree Dungeon, Monkey Forest I.\nאחר כך Final Attack: Sword.")
    assert f"{bidi.LRI}Tree Dungeon, Monkey Forest I{bidi.PDI}" in body
    assert f"{bidi.LRI}Final Attack: Sword{bidi.PDI}" in body
    # only names that need it (plain "Henesys" stays a run), never a Hebrew one, never inside a longer word
    assert bidi.LRI not in bidi.isolate_ltr_runs("לכו ל-Henesys")
    assert bidi.LRI not in bidi.isolate_ltr_runs("Final Attack: Swordsman ב-Henesys")
    # an English answer is left alone
    assert bidi.LRI not in bidi.to_html("Learn Final Attack: Sword at level 30.")


def test_kb_registers_names_for_hebrew_answers(tmp_path):
    import json

    from maplehelper.kb import KnowledgeBase
    rows = [{"key": f"map/{i}", "name": n, "category": "map"} for i, n in enumerate(KB_NAMES + ["Henesys"])]
    (tmp_path / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    try:
        bidi.set_names([])
        KnowledgeBase(tmp_path)
        for n in KB_NAMES:
            assert bidi.isolate_ltr_runs(f"לכו ל-{n} עכשיו").count(bidi.LRI) == 1
        assert bidi.LRI not in bidi.isolate_ltr_runs("לכו ל-Henesys עכשיו")
    finally:
        bidi.set_names([])


@pytest.mark.parametrize("line,name", [
    ("את הקווסט [Construction Site B1] Shumi's Lost Coin לוקחים מ-Shumi.", "[ConstructionSiteB1]Shumi'sLostCoin"),
    ('השלט "Lith Harbor" ליד הנמל.', '"LithHarbor"'),
    ("המפה [Area 1] Line 2 טובה", "[Area1]Line2"),
])
def test_brackets_and_quotes_inside_a_run(line, name):
    # without any KB names: "[ ]" and '"' belong to the English run (no bracket thrown to the other end)
    assert name in _reading_order(bidi.isolate_ltr_runs(line))


@pytest.mark.parametrize("line,checks", HEBREW_MESSAGE_LINES)
def test_line_inside_hebrew_message(line, checks):
    shown = bidi.isolate_ltr_runs(line)
    x = glyph_x(shown)
    for a, b in checks:
        assert x[shown.index(a)] < x[shown.index(b)], f"{a!r} should be left of {b!r} in {line!r}"



def test_a_hebrew_prefix_hyphen_never_ends_a_line():
    """"עלה מ-7,000 ל-10,500 mesos" broke after "ל-" and the number went to the next line (the owner's report)."""
    out = bidi.isolate_ltr_runs("רק מחיר החנות עלה מ-7,000 ל-10,500 mesos")
    assert "מ-\u2060" in out and "ל-\u2060" in out
    assert "\u2060" not in bidi.isolate_ltr_runs("Lv. 20 - Warrior")          # a dash, not a prefix


@pytest.mark.parametrize("sentence,old,new", [("הסיכוי עלה מ-10% → 25%.", "10%", "25%"),
                                              ("הנזק השתנה COT1 -> COT2 בבילד", "COT1", "COT2"),
                                              ("ה-DEX עלה (30 → 33) ברמה הזו", "30", "33")])
def test_an_old_to_new_arrow_reads_left_to_right(sentence, old, new):
    """"מ-10% → 25%" as two runs showed "25% → 10%": the arrow pointed at the old value (the audit, HEB-7)."""
    shown = bidi.isolate_ltr_runs(sentence)
    assert shown.count(bidi.LRI) == 1
    x = glyph_x(shown)
    arrow = shown.index("→" if "→" in shown else "->")
    assert x[shown.index(old)] < x[arrow] < x[shown.index(new)]
