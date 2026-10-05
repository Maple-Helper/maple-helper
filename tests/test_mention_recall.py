"""Entity recall of KnowledgeBase.find_mentions: the names a player's question says, written as players write them.

The rules one by one on tiny knowledge bases, then the whole set of player questions (tools/mention_recall.py)
against the real one: found, an answer needs no tool call (3-14 s); missed, the AI searches for 20 s - 4 min."""
import json
import re
from pathlib import Path

import pytest

import mention_recall
from maplehelper import brain
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
# measured on tools/mention_recall.py's questions: 159 of 159 names found, no name found that isn't said
RECALL_FLOOR = 0.99


def small_kb(tmp_path, entities: list[tuple[str, str]], aliases: dict | None = None) -> KnowledgeBase:
    """A knowledge base of just these (key, name) entities."""
    (tmp_path / "index.json").write_text(json.dumps([{"key": k, "name": n, "category": k.split("/")[0], "props": {}}
                                                     for k, n in entities]), encoding="utf-8")
    (tmp_path / "aliases.json").write_text(json.dumps(aliases or {}, ensure_ascii=False), encoding="utf-8")
    return KnowledgeBase(tmp_path)


def names(kb, text, n=8, answer=False):
    return [kb.get(k)["name"] for k in kb.find_mentions(text, n, answer=answer)]


# ------------------------------------------------------------------ spellings: case, apostrophes, hyphens

def test_apostrophes_and_hyphens_typed_or_not(tmp_path):
    kb = small_kb(tmp_path, [("skill/1", "Amazon's Judgement"), ("monster/2", "Tick-Tock"), ("item/3", "Lupin's Banana"),
                             ("monster/4", "Lupin"), ("monster/5", "Jr. Boogie")])
    for text in ("amazons judgement?", "Amazon judgement level", "amazon's judgement"):
        assert names(kb, text) == ["Amazon's Judgement"]
    assert names(kb, "ticktock drops") == names(kb, "tick tock hp") == ["Tick-Tock"]
    assert names(kb, "lupins banana") == ["Lupin's Banana"]          # not Lupin
    assert names(kb, "JR BOOGIE hp") == ["Jr. Boogie"]


def test_an_answer_still_needs_the_exact_name(tmp_path):
    kb = small_kb(tmp_path, [("skill/1", "Amazon's Judgement"), ("item/2", "Ilbi Throwing Stars"),
                             ("class/hermit", "Hermit skills")])
    assert names(kb, "amazons judgement, ilbi or hermit", answer=True) == []
    assert names(kb, "Ilbi Throwing Stars", answer=True) == ["Ilbi Throwing Stars"]


# ------------------------------------------------------------------ base names: a suffix, "The", a numeral

def test_the_part_that_marks_one_entity_of_a_family(tmp_path):
    kb = small_kb(tmp_path, [("skill/1", "Arrow Bomb: Bow"), ("skill/2", "Soul Arrow: Bow"),
                             ("skill/3", "Soul Arrow: Crossbow"), ("map/4", "The Pig Beach"), ("monster/5", "Pig"),
                             ("class/hermit", "Hermit skills"), ("class/chief-bandit", "Chief Bandit skills"),
                             ("class/bandit", "Bandit"), ("item/6", "Crusader T-Shirt"),
                             ("class/crusader", "Crusader skills")])
    assert names(kb, "is arrow bomb worth it") == ["Arrow Bomb: Bow"]
    assert names(kb, "soul arrow worth it?") == []                    # Bow or Crossbow: no guess
    assert names(kb, "soul arrow bow mp") == ["Soul Arrow: Bow"]
    assert names(kb, "how do i get to pig beach") == ["The Pig Beach"]   # not Pig
    assert names(kb, "hermit or chief bandit") == ["Chief Bandit skills", "Hermit skills"]   # not Bandit
    assert names(kb, "crusader") == ["Crusader skills"] and names(kb, "crusader t-shirt") == ["Crusader T-Shirt"]


def test_a_shared_base_names_nothing_and_hides_only_other_kinds(tmp_path):
    kb = small_kb(tmp_path, [("map/1", "The Cave of Evil Eye I"), ("map/2", "The Cave of Evil Eye II"),
                             ("monster/3", "Evil Eye"), ("map/4", "Henesys Hunting Ground I"),
                             ("map/5", "Henesys Hunting Ground II"), ("map/6", "Henesys"), ("map/7", "Ant Tunnel I"),
                             ("map/8", "Ant Tunnel II"), ("map/9", "Land of Wild Boar"),
                             ("map/10", "The Land of Wild Boar II"), ("item/11", "The Judgement")])
    assert names(kb, "where is the cave of evil eye") == []           # I or II, and no Evil Eye
    assert names(kb, "where is evil eye") == ["Evil Eye"]
    assert names(kb, "the henesys hunting ground") == ["Henesys"]     # a map family: the town still counts
    assert names(kb, "where is ant tunnel") == []
    assert names(kb, "the cave of evil eye ii") == ["The Cave of Evil Eye II"]
    assert names(kb, "the land of wild boar") == ["Land of Wild Boar"]   # a name of its own, not II
    assert names(kb, "what is judgement") == []                      # a one-word base is no name


# ------------------------------------------------------------------ a name's first word alone

def test_a_unique_first_word_names_its_entity(tmp_path):
    kb = small_kb(tmp_path, [("item/1", "Ilbi Throwing Stars"), ("item/2", "Subi Throwing Stars"),
                             ("npc/3", "Athena Pierce"), ("item/4", "Athena Pierce's Letter"), ("item/5", "Work Gloves"),
                             ("item/6", "Mithril Ore"), ("item/7", "Mithril Axe"), ("map/8", "Florina Beach"),
                             ("map/9", "Lith Harbor"), ("item/10", "Tobi Throwing Stars"), ("item/11", "Zakum Helmet")])
    assert names(kb, "Ilbi vs Subi") == ["Ilbi Throwing Stars", "Subi Throwing Stars"]
    assert names(kb, "ilbis or tobis") == ["Ilbi Throwing Stars", "Tobi Throwing Stars"]
    assert names(kb, "where is athena") == ["Athena Pierce"]          # her letter starts with her whole name
    assert names(kb, "i work all day") == []                          # an everyday word (COMMON_LEAD_WORDS)
    assert names(kb, "mithril price") == []                           # two names start so
    assert names(kb, "is florina road safe") == []                    # a different place
    assert names(kb, "boat from lith") == ["Lith Harbor"]
    assert names(kb, "when is zakum coming") == []                    # the boss, not the helmet


def test_a_word_the_kb_writes_in_lower_case_is_no_short_name(tmp_path):
    kb = small_kb(tmp_path, [("item/1", "Spinning Piglet")])
    (tmp_path / "pages" / "quest").mkdir(parents=True)
    (tmp_path / "pages" / "quest" / "1.md").write_text("The wheel keeps spinning all night.", encoding="utf-8")
    assert names(kb, "i keep spinning") == []


# ------------------------------------------------------------------ plurals

def test_item_names_in_the_plural(tmp_path):
    kb = small_kb(tmp_path, [("item/1", "Red Potion"), ("item/2", "Red Snail Shell"), ("monster/3", "Red Snail"),
                             ("item/4", "Sword")])
    assert names(kb, "where to farm red potions") == ["Red Potion"]
    assert names(kb, "red snail shells drop") == ["Red Snail Shell"]   # not Red Snail
    assert names(kb, "which swords are best") == []                    # any sword, not the item "Sword"


# ------------------------------------------------------------------ Hebrew

def test_an_alias_that_is_a_prefix_and_another_alias(tmp_path):
    kb = small_kb(tmp_path, [("map/1", "Ellinia"), ("map/2", "To Ellinia")], {"map/1": ["אלינה"], "map/2": ["לאלינה"]})
    assert names(kb, "איך מגיעים לאלינה") == ["Ellinia"]
    assert kb.resolve_names("איך מגיעים לאלינה") == "איך מגיעים ל-Ellinia"


def test_english_names_in_hebrew_letters(tmp_path):
    kb = small_kb(tmp_path, [("item/1", "Ilbi Throwing Stars"), ("item/2", "Subi Throwing Stars"),
                             ("item/3", "Steely Throwing Knives"), ("skill/4", "Power Strike"),
                             ("skill/5", "Lucky Seven"), ("item/6", "Slime Hat"), ("skill/7", "Panic"),
                             ("map/8", "Marr's Forest"), ("npc/9", "Eurek the Alchemist"), ("npc/10", "Grendel the Really Old")])
    assert names(kb, "מה ההבדל בין אילבי לסובי") == ["Ilbi Throwing Stars", "Subi Throwing Stars"]
    assert names(kb, "אילבי או סטילי") == ["Ilbi Throwing Stars", "Steely Throwing Knives"]
    assert names(kb, "כמה נזק עושה פאוור סטרייק") == ["Power Strike"]
    assert names(kb, "לאקי סבן") == ["Lucky Seven"]
    assert names(kb, "איפה גרנדל") == ["Grendel the Really Old"]
    # everyday Hebrew that reads like a name: "completes the", "in front of you", "fast", "extends"
    for text in ("משלים את הקווסט", "המוב שלפניך", "תבוא מהר", "זה מאריך את הבאף"):
        assert names(kb, text) == [], text


def test_the_jobs_hebrew_names(tmp_path):
    kb = small_kb(tmp_path, [("class/hermit", "Hermit skills"), ("class/chief-bandit", "Chief Bandit skills"),
                             ("class/bowman", "Bowman"), ("class/cleric", "Cleric")])
    assert names(kb, "מה יותר טוב, הרמיט או צ'יף בנדיט") == ["Chief Bandit skills", "Hermit skills"]
    assert names(kb, "מה ההבדל בין מג' לקלריק") == ["Cleric"]
    assert names(kb, "אני רוצה להיות קשת") == []                      # a 1st job's Hebrew name is an everyday word


# ------------------------------------------------------------------ the cap: a comparison names more

def test_a_comparison_may_name_more_entities_each_page_shorter():
    assert brain.mention_cap("compare mano, mushmom, king slime, jr balrog and crimson balrog") == brain.LIST_MENTIONS
    assert brain.mention_cap("Ilbi vs Subi") == brain.mention_cap("הרמיט או צ'יף בנדיט") == brain.LIST_MENTIONS
    assert brain.mention_cap("how much hp does mano have") == brain.mention_cap("כמה חיים יש למאנו") == brain.MENTIONS
    assert brain.page_chars(1) == brain.page_chars(brain.MENTIONS) == brain.PAGE_CHARS
    assert brain.page_chars(8) * 8 <= brain.PAGE_CHARS * brain.MENTIONS


@needs_kb
def test_a_long_comparison_prefetches_every_page_at_about_the_same_length(real, monkeypatch):
    q = "compare mano, mushmom, king slime, jr balrog, crimson balrog and stump hp"
    p = brain.build_prompt(q, None, None, real, has_screenshot=False)
    keys = real.find_mentions(q, brain.mention_cap(q))
    assert len(keys) == 6 and all(f"[{k}]" in p for k in keys)
    ctx = lambda s: len(re.search(r"<kb_context>(.*)</kb_context>", s, re.S).group(1))  # noqa: E731
    monkeypatch.setattr(brain, "LIST_MENTIONS", brain.MENTIONS)
    four = brain.build_prompt(q, None, None, real, has_screenshot=False)
    assert sum(f"[{k}]" in four for k in keys) == 4
    assert ctx(p) <= ctx(four) * 1.1     # six pages (and their drop lists) in the room four had


# ------------------------------------------------------------------ the real knowledge base

@pytest.fixture(scope="module")
def real():
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_player_questions_name_what_they_say(real):
    r = mention_recall.measure(real)
    assert r["recall"] >= RECALL_FLOOR, r["misses"]
    assert r["wrong"] == {}               # no name a question doesn't say, in the questions and the negatives
    assert r["ms"] < 25                   # a few ms a question once the indexes are built


@needs_kb
def test_the_measurement_set_is_valid(real):
    every = {e["name"] for e in real.entities.values()}
    assert len(mention_recall.CASES) >= 80 and len(mention_recall.NEGATIVES) >= 30
    assert sum(bool(re.search("[א-ת]", q)) for q, _ in mention_recall.CASES) >= 30
    assert all(n in every for _, ns in mention_recall.CASES for n in ns)
