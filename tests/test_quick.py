"""Instant answers from the KB: only simple, unambiguous factual questions; the rest goes to Claude."""
import pytest

from maplehelper import quick
from maplehelper.i18n import I18n

t = I18n("en")


def first_monster(kb):
    return next(k for k, e in kb.entities.items() if e["category"] == "monster" and (e.get("props") or {}).get("HP"))


def test_stat_question_is_answered_from_the_kb(kb):
    key = first_monster(kb)
    e = kb.get(key)
    ans = quick.answer(f"how much HP does {e['name']} have?".replace("how ", "what's the "), kb, t)
    assert ans and f"HP: {e['props']['HP']}" in ans.text and ans.entities == [key]


def test_hebrew_stat_question(kb):
    key = first_monster(kb)
    e = kb.get(key)
    ans = quick.answer(f"כמה HP יש ל-{e['name']}?", kb, t)
    assert ans and str(e["props"]["HP"]) in ans.text


@pytest.mark.parametrize("q", [
    "how should I level my Assassin?",      # judgement
    "מה כדאי לעשות בלבל 30?",                 # judgement, nothing named
    "what is this monster?",                 # needs the screenshot
    "tell me everything about the game and all the monsters you know please",  # long
])
def test_everything_else_goes_to_claude(kb, q):
    assert quick.answer(q, kb, t) is None


def test_hebrew_words_match_whole_words_only():
    assert quick.WHO.search("מי מפיל את זה")
    assert not quick.WHO.search("מימון")


def test_a_name_containing_a_stat_word_is_not_a_stat_question():
    # "לבלו סנייל" (Blue Snail) contains "לבל" (level) but doesn't ask about the level
    level = next(rx for rx, key, _ in quick.STATS if key == "Level")
    assert not level.search("כמה HP יש לבלו סנייל?")
    assert level.search("באיזה לבל Mano?") and level.search("what level is Mano")


def test_how_much_is_a_number_question(kb):
    # the module docstring's own example used to fall through to Claude because of "how"
    ans = quick.answer("How much HP does Red Snail have?", kb, t)
    assert ans and "HP: 45" in ans.text
    assert quick.answer("how do I kill Red Snail?", kb, t) is None


def test_hebrew_stat_word_with_the_article(kb):
    # "מה הלבל של..." / "מה הדיוק של...": the definite article is glued to the stat word
    ans = quick.answer("מה הלבל של רד סנייל?", kb, t)
    assert ans and "Level: 4" in ans.text


def test_defense_reads_physical_defense(kb):
    # monsters carry "Physical Defense" (and "Magic Defense"), never a plain "Defense"
    kb.get("monster/130101")["props"]["Physical Defense"] = 20
    ans = quick.answer("Red Snail defense", kb, t)
    assert ans and "P.DEF: 20" in ans.text


def test_two_questions_in_one_go_to_claude(kb):
    # answering only the drops of "level and drops" would look like the whole answer
    assert quick.answer("Red Snail level and drops", kb, t) is None
    assert quick.answer("where is Red Snail and what's its HP", kb, t) is None


def test_the_players_level_and_the_damage_they_deal_are_not_the_monsters_stats(kb):
    """audit AI-18: "... at level 40" added the monster's own Level, "כמה נזק עושים ל-X" gave its attack."""
    key = first_monster(kb)
    e = kb.get(key)
    if (e.get("props") or {}).get("EXP") in (None, ""):
        pytest.skip("no EXP on the first monster")
    ans = quick.answer(f"how much exp does {e['name']} give at level 40", kb, t)
    assert ans and "Level:" not in ans.text
    assert quick.answer(f"כמה נזק עושים ל-{e['name']}", kb, t) is None
