"""Game-data fixes: job tree, quest parsing, Hebrew name matching, instant answers, bosses, prices, guides.

Values the app keeps as constants are checked against the real knowledge base when it is present. The nightly runs
these on the KB it is about to publish: a legitimate game change (a balance patch, a reworded guide, an area opening)
must not hold it back, so the real KB is checked for what holds whatever the numbers are, exact parses are pinned on
frozen page copies, and a guide sentence an app constant was taken from is reported (a warning) when it changes."""
import json
import re
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import combat, crafting, glossary, jobs, plan, quests, quick
from maplehelper.i18n import I18n
from maplehelper.kb import ALIAS_DROP, KnowledgeBase, _norm

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
SENTENCES = Path(__file__).parent / "fixtures" / "hebrew_gamer_sentences.txt"
t = I18n("en")


@pytest.fixture(scope="module")
def real():
    return KnowledgeBase(REAL_KB)


def small_kb(tmp_path, entities: list[dict], aliases: dict, pages: dict | None = None) -> KnowledgeBase:
    """A knowledge base of just these entities (key, name, category, props) and aliases."""
    (tmp_path / "index.json").write_text(json.dumps(entities), encoding="utf-8")
    (tmp_path / "aliases.json").write_text(json.dumps(aliases, ensure_ascii=False), encoding="utf-8")
    for key, text in (pages or {}).items():
        cat, slug = key.split("/")
        (tmp_path / "pages" / cat).mkdir(parents=True, exist_ok=True)
        (tmp_path / "pages" / cat / f"{slug}.md").write_text(text, encoding="utf-8")
    return KnowledgeBase(tmp_path)


def ent(key, name, **props):
    return {"key": key, "name": name, "category": key.split("/")[0], "props": props}


def kb_says(page: str, pattern: str, what: str) -> None:
    """An app constant taken from a guide sentence: a rewritten guide is reported, not a failed night."""
    if not re.search(pattern, page, re.I):
        warnings.warn(f"the knowledge base no longer says {pattern!r}: check {what}", stacklevel=2)


# ------------------------------------------------------------------ 1, 7, 17: the job tree

def test_magician_first_job_is_level_10_like_every_class():
    assert all(js[1][1] == 10 for c, js in jobs.JOBS.items() if c != "Beginner")
    assert jobs.first_job("Magician", 9) == "Beginner" and jobs.first_job("Magician", 10) == "Magician"
    assert plan.next_job("Beginner", "Beginner", 8) == (["Warrior", "Magician", "Bowman", "Thief"], 10)
    from maplehelper.ui import dialogs
    assert dialogs.JOBS is jobs.JOBS                   # one tree, not a copy that drifts


def test_next_job_stops_at_the_second_job_while_third_job_is_closed(monkeypatch):
    assert plan.next_job("Thief", "Thief", 29) == (["Assassin", "Bandit"], 30)
    assert plan.next_job("Thief", "Assassin", 34) is None
    assert plan.next_job("Magician", "Cleric", 69) is None
    monkeypatch.setattr(jobs, "open_tier", lambda kb=None: 3)     # the day the KB confirms 3rd job
    assert plan.next_job("Thief", "Assassin", 34) == (["Hermit", "Chief Bandit"], 70)


@pytest.mark.parametrize("raw,job", [
    ("Wizard (Fire,Poison)", "F/P Wizard"), ("FP Wizard", "F/P Wizard"), ("IL Wizard", "I/L Wizard"),
    ("Fire Poison Wizard", "F/P Wizard"), ("Wizard (Ice, Lightning)", "I/L Wizard"), ("Bowmen", "Bowman"),
    ("Crossbowmen", "Crossbowman"), ("Spear man", "Spearman"), ("f/p wizard", "F/P Wizard"), ("Archer", "Bowman"),
    ("Chief  Bandit", "Chief Bandit"), ("Pirate", None),
    # the KB's own spelling and run-together HUD words (audit GAM-5)
    ("Fire/Poison Wizard", "F/P Wizard"), ("Ice/Lightning Wizard", "I/L Wizard"), ("Mage (Fire,Poison)", "F/P Mage"),
    ("Ice/Lightning Mage", "I/L Mage"), ("WhiteKnight", "White Knight"), ("ChiefBandit", "Chief Bandit"),
])
def test_canonical_job_names(raw, job):
    assert jobs.canonical_job(raw) == job


@needs_kb
def test_job_tree_matches_the_knowledge_base(real):
    classes = {e["name"].removesuffix(" skills") for e in real.entities.values() if e["category"] == "class"}
    assert {j for js in jobs.JOBS.values() for j, _ in js} <= classes
    kb_says(real.page("guide/maplestory-classic-glossary"), r"first real job you pick at level 10", "jobs.JOBS")
    for cls in ("warrior", "magician", "bowman", "thief"):
        assert real.page(f"class/{cls}")
        kb_says(real.page(f"class/{cls}"), r"\blevel 30\b", "jobs.JOBS")
    kb_says(real.page("class/bowman"), r"\blevel 70\b", "jobs.JOBS")
    assert {lv for js in jobs.JOBS.values() for _, lv in js} == {1, 10, 30, 70}
    # the job tier is the release guide's (pinned both ways on fixture guides below): 3rd job opens the night the
    # guide confirms it, and the app follows with no release
    from maplehelper import availability
    a = availability.of(real)
    assert a.known and a.not_at_launch_text and jobs.open_tier(real) == a.job_tier in (2, 3)


@pytest.mark.parametrize("guide,tier", [
    ("Confirmed content\nClassic maps on Victoria Island.\nNot at launch\nOssyria and 3rd job are "
     "not initial-launch content.\n", 2),
    ("Confirmed content\nClassic maps on Victoria Island. 3rd job advancements are confirmed.\nNot at launch\n"
     "Ossyria is not initial-launch content.\n", 3),
    ("Confirmed content\nClassic maps on Victoria Island.\nNot at launch\nOssyria.\n", 2),     # not named: shut
])
def test_third_job_opens_only_when_the_guide_confirms_it(tmp_path, guide, tier):
    from maplehelper import availability
    kb = small_kb(tmp_path, [ent("guide/maplestory-classic-worlds-release-date", "Release")], {},
                  {"guide/maplestory-classic-worlds-release-date": "---\n{}\n---\n\n# Release\n\n" + guide})
    assert availability.of(kb).job_tier == tier and jobs.open_tier(kb) == tier


# ------------------------------------------------------------------ 2, 3, 10, 11, 23: quests

QUEST = """---
{}
---

# Test
Pre-requisites
Level Lv. 12+
Henesys: Citizenship grade 9 Level 52+ to complete Quest Complete First Greeting with Chief Stan
Quest Complete Another Quest
Profession Smithing Lv. 5+ (learn crafting)
Requirements
Defeat Snail x 10
Rewards
4,387 EXP 1,053 Mesos + 3 Fame
Red Potion x 5
Pick one (class-specific):
Any Class
Hero's Gladius x 1 Skull Earrings x 1
Random reward - one of:
Beginner
Old Wisconsin x 1 100 %
Warrior
Bronze Ore x 7 16.7 % Iron Ore x 7 16.7 %
Description
01 Words.
"""


class FakeKB:
    """Just enough of a KnowledgeBase for quests (hashable, like the real one: quests caches per kb)."""

    def __init__(self, entities: dict, pages: dict):
        self.entities, self._pages, self._npc_by_name = entities, pages, {}

    def get(self, key):
        return self.entities.get(key)

    def page(self, key):
        return self._pages.get(key, "")


def quest_kb(**props):
    e = {"category": "quest", "name": "Test", "props": {"Minimum Level": 12, "Area": "Citizenship", **props}}
    return FakeKB({"quest/1": e}, {"quest/1": QUEST})


def test_quest_page_parsing():
    quests._quest.cache_clear()
    q = quests.quest(quest_kb(), "quest/1")
    assert (q.level, q.complete_level, q.opens_at()) == (12, 52, 52)
    assert q.grade == ("Henesys", 9) and q.profession == ("Smithing", 5) and q.fame == 3
    assert q.afters == ["First Greeting with Chief Stan", "Another Quest"]          # every prerequisite
    assert q.rewards == ["Red Potion x 5"]                                           # the sure ones only
    assert q.rewards_pick("Thief") == ["Hero's Gladius x 1", "Skull Earrings x 1"]   # "Any Class": pick ONE
    assert q.rewards_random("Warrior") == ["Bronze Ore x 7 (16.7%)", "Iron Ore x 7 (16.7%)"]
    assert q.rewards_random("Beginner") == ["Old Wisconsin x 1 (100%)"] and q.rewards_random("Thief") == []
    quests._quest.cache_clear()


def test_a_quest_finished_at_a_higher_level_waits_for_it():
    quests._quest.cache_clear()
    kb = quest_kb(Area="Victoria Island")
    assert quests.for_level(kb, 20)["now"] == []                                  # taken at 12, done at 52
    assert [q.key for q in quests.for_level(kb, 50)["later"]] == ["quest/1"]                 # 2 levels up: later
    assert [q.key for q in quests.for_level(kb, 51)["soon"]] == ["quest/1"]                  # the next level
    assert [q.key for q in quests.for_level(kb, 52)["now"]] == ["quest/1"]
    kb = quest_kb()
    # the quest asks Henesys citizenship grade 9: a Henesys quest, whatever its NPC page says
    assert quests.citizenship(kb, "Henesys", 30) == [] and quests.citizenship(kb, "Henesys", 52)
    quests._quest.cache_clear()


def test_profession_quests_follow_the_craft_level():
    quests._quest.cache_clear()
    kb = quest_kb(Area="Crafting")
    assert quests.for_level(kb, 55, crafts={"smithing": 4})["now"] == []
    assert quests.for_level(kb, 55, crafts={"smithing": 5})["now"]
    assert quests.for_level(kb, 55, crafts=None)["now"]               # no craft levels set: shown, labelled
    quests._quest.cache_clear()


def test_quest_without_a_level_line_is_level_1():
    quests._quest.cache_clear()
    e = {"category": "quest", "name": "Mirror", "props": {"EXP Reward": 2}}
    kb = FakeKB({"quest/9": e}, {"quest/9": "---\n{}\n---\nPre-requisites\nQuest Complete A\nRewards\n2 EXP\n"})
    q = quests.quest(kb, "quest/9")
    assert q.level == 1 and q.afters == ["A"]
    assert [x.key for x in quests.for_level(kb, 3)["now"]] == ["quest/9"]
    quests._quest.cache_clear()


def test_beginner_quests_for_a_character_still_a_beginner():
    q = quests.Quest("quest/2", "Mai's Training", 3, job="Beginner only")
    assert quests.job_fits(q, "Beginner", "Beginner")
    assert quests.job_fits(q, "Magician", "Beginner")          # class picked ahead, still a Beginner
    assert not quests.job_fits(q, "Magician", "Magician")


def test_second_job_quests_only_for_the_first_job():
    """A Fighter 35 had "The Warrior's Next Journey" under missed quests, a Beginner 30 under now (audit GAM-2)."""
    q = quests.Quest("quest/3", "The Warrior's Next Journey", 30, area="Job Advancement", job="Warrior only")
    assert quests.job_fits(q, "Warrior", "Warrior")
    assert not quests.job_fits(q, "Warrior", "Fighter") and not quests.job_fits(q, "Warrior", "Beginner")
    assert not quests.job_fits(q, "Thief", "Thief")


@needs_kb
def test_real_quest_rewards(real):
    """Every quest page parses, and what the parser finds holds together on all of them (the exact parse of each
    kind, pick-one, random with odds, fame, "to complete" level, grade, profession, is pinned on QUEST above)."""
    quests._quest.cache_clear()
    keys = [k for k, e in real.entities.items() if e["category"] == "quest"]
    rows = [quests.quest(real, k) for k in keys]
    assert len(rows) > 200 and all(rows)                                  # every quest page parses
    odds = re.compile(r"\((\d+(?:\.\d+)?)%\)$")
    for q in rows:
        for cls in q.random_rewards:                                      # one of a set: the odds add up to 100 %
            got = [odds.search(r) for r in q.rewards_random(cls)]
            assert all(got) and 95 <= sum(float(m.group(1)) for m in got) <= 105, (q.key, cls)
        assert not q.complete_level or q.complete_level >= q.level, q.key
        assert not q.grade or (q.grade[0] in quests.TOWNS and 1 <= q.grade[1] <= 20), q.key
        assert not q.profession or (q.profession[0].lower() in crafting.PROFESSIONS and q.profession[1] > 0), q.key
        if q.complete_level > q.level and q.area == "Citizenship" and q.grade:      # taken early, done later
            town = quests.town_of(real, q)
            assert q.key not in {x.key for x in quests.citizenship(real, town, q.complete_level - 1)}, q.key
    # the parsers still find each kind somewhere (a page layout change that hides them all fails here)
    assert all(any(f(q) for q in rows) for f in (lambda q: q.random_rewards, lambda q: q.class_rewards,
                                                lambda q: q.fame, lambda q: q.complete_level, lambda q: q.grade,
                                                lambda q: q.profession))
    # a "First Greeting with X" that an "Asking After X" follows is a step done once, never daily (audit GAM-7: Athena
    # Pierce's "Asking After" page names no prerequisite)
    norm = {q.key: " ".join(q.name.split()).rstrip(".") for q in rows}
    asked = {n.removeprefix("Asking After ") for n in norm.values() if n.startswith("Asking After ")}
    greet = [q for q in rows if norm[q.key].startswith("First Greeting with ")
             and norm[q.key].removeprefix("First Greeting with ") in asked]
    assert greet and all(q.cycle == "" for q in greet), [q.name for q in greet if q.cycle]
    quests._quest.cache_clear()


# ------------------------------------------------------------------ 4, 5, 13, 14: names in Hebrew text

def test_hebrew_quote_marks_fold():
    assert _norm("ג׳וניור") == _norm("ג'וניור") == _norm("ג`וניור") == "ג'וניור"
    assert _norm('צה״ל') == 'צה"ל' and _norm('"Mar" the Fairy') == "mar the fairy"


def test_short_aliases_have_no_loose_form_and_no_prefix(tmp_path):
    kb = small_kb(tmp_path, [ent("npc/1", "Pia"), ent("npc/2", "Anne"), ent("monster/3", "Snail", Level=1),
                             ent("npc/4", "Ali"), ent("map/5", "Henesys")],
                  {"npc/1": ["פיה"], "npc/2": ["אן"], "monster/3": ["חילזון"], "npc/4": ["אלי"], "map/5": ["הנסיס"]})
    assert kb.find_mentions("כמה אייץ' פי יש לחילזון") == ["monster/3"]     # "פי" is no loose "פיה"
    assert kb.find_mentions("לאן ללכת") == [] and kb.find_mentions("מה יש כאן") == []   # no ל/כ + "אן"
    assert kb.find_mentions("תכתוב אלי") == []                                # a stop-listed word
    assert kb.find_mentions("איפה הפיה") == []                                # article only before long aliases
    assert kb.find_mentions("מה יש בהנסיס") == ["map/5"]
    assert kb.find_mentions("איפה החילזון") == ["monster/3"]                  # long alias: the article goes
    assert kb.resolve_names("מה יש כאן ולאן") == "מה יש כאן ולאן"
    assert kb.resolve_names("מה מפיל חילזון בהנסיס") == "מה מפיל Snail ב-Henesys"


def test_generic_words_are_dropped_and_overrides_apply(tmp_path):
    kb = small_kb(tmp_path, [ent("npc/507", "River"), ent("monster/62", "Rotten Mushroom", Level=56),
                             ent("monster/700003", "Rotten Mushmom", Level=60), ent("monster/1", "Tutorial Jr. Sentinel"),
                             ent("monster/1001", "Jr. Sentinel", Level=26)],
                  {"npc/507": ["ריבר", "נהר"], "monster/62": ["פטריה רקובה"],
                   "monster/700003": ["מושמום רקוב", "פטרייה רקובה"],
                   "monster/1001": ["ג'וניור סנטינל"], "monster/1": ["ג'וניור סנטינל"]})
    assert "נהר" in ALIAS_DROP and kb.find_mentions("הנהר כאן יפה") == [] and kb.find_mentions("ריבר") == ["npc/507"]
    assert kb.find_mentions("מה הלבל של פטרייה רקובה") == ["monster/62"]
    assert kb.find_mentions("מה הלבל של פטריה רקובה") == ["monster/62"]
    assert kb.find_mentions("כמה חיים יש לג׳וניור סנטינל") == ["monster/1001"]


def test_a_name_inside_a_longer_one_counts_once(tmp_path):
    kb = small_kb(tmp_path, [ent("monster/2", "Snail", Level=1), ent("monster/4", "Red Snail", Level=4)],
                  {"monster/2": ["חילזון"], "monster/4": ["חילזון אדום"]})
    assert kb.find_mentions("Red Snail") == ["monster/4"]
    assert kb.find_mentions("החילזון האדום") == ["monster/4"]            # not also Snail from the loose copy
    assert kb.find_mentions("חילזון אדום") == ["monster/4"]
    assert kb.find_mentions("חילזון אדום וחילזון") == ["monster/4", "monster/2"]   # a second, separate one counts


def test_monster_names_in_the_plural(tmp_path):
    kb = small_kb(tmp_path, [ent("monster/12", "Octopus", Level=12), ent("monster/9", "Fire Boar", Level=32)],
                  {"monster/12": ["תמנון"]})
    assert kb.find_mentions("איפה יש תמנונים") == ["monster/12"]
    assert kb.find_mentions("where to find fire boars") == ["monster/9"]


def test_answer_text_matches_exact_names_only(tmp_path):
    kb = small_kb(tmp_path, [ent("npc/428", "Max"), ent("npc/507", "River"), ent("map/1", "Perion"),
                             ent("monster/1090", "Bain")], {"monster/1090": ["ביין"]})
    assert kb.find_mentions("Your max HP is 500. Max HP grows.", answer=True) == []
    assert kb.find_mentions("It is a long way to the river, go to Perion.", answer=True) == ["map/1"]
    assert kb.find_mentions("הכי טוב לגרינד בין לבל 10 ל-20", answer=True) == []
    assert kb.find_mentions("what is the max level") == [] and kb.find_mentions("where is Max") == ["npc/428"]


@needs_kb
def test_ordinary_hebrew_sentences_name_nothing(real):
    """~100 everyday gamer sentences: only real game names (as players say them) may match."""
    allowed = {  # sentence -> the names it really says
        "כמה אייץ' פי יש לחילזון": ["Snail"], "כמה אייץ' פי יש למאנו": ["Mano"],
        "אני רוצה ללכת לקרנינג": ["Kerning City"], "זה כמו בטי בופ": ["Betty"], "נבה זה שם יפה": ["Neve"],
        "רנה שלחה לי הודעה": ["Rene"], "יש לי חזיר בבית": ["Pig"], "יש פה עין מרושעת": ["Evil Eye"],
        "ראיתי סרט על זומבי קטן": ["Minor Zombie"], "זה שעון רפאים?": ["Phantom Watch"],
        "תמנון זה חיה מגניבה": ["Octopus"],
        "מה ההבדל בין מג' לקלריק": ["Cleric"],       # jobs.JOB_HE's Hebrew name of the job
    }
    lines = [s.strip() for s in SENTENCES.read_text(encoding="utf-8").splitlines() if s.strip()]
    assert len(lines) >= 100
    wrong = {}
    for s in lines:
        got = [real.get(k)["name"] for k in real.find_mentions(s)]
        if got != allowed.get(s, []):
            wrong[s] = got
    assert wrong == {}


@needs_kb
@pytest.mark.parametrize("text,name", [
    ("כמה חיים יש לחילזון", "Snail"), ("מה הלבל של החילזון האדום", "Red Snail"), ("מה יש בהנסיס", "Henesys"),
    ("איפה יש תמנונים", "Octopus"), ("כמה חיים יש לג׳וניור סנטינל", "Jr. Sentinel"),
    ("מה הלבל של פטרייה רקובה", "Rotten Mushroom"), ("איפה ליטי", "Leatty"), ("מה מפיל לטי", "Leatty"),
    ("כמה חיים לג'וניור בוגי", "Jr. Boogie 1"), ("Jr Boogie hp", "Jr. Boogie 1"), ("מה מפיל מאנו", "Mano"),
])
def test_real_names_still_match(real, text, name):
    assert [real.get(k)["name"] for k in real.find_mentions(text)] == [name]


@needs_kb
def test_voice_resolution_uses_the_same_safety(real):
    assert real.resolve_names("מה מפיל מאנו") == "מה מפיל Mano"
    for s in ("מה יש כאן", "לאן ללכת עכשיו", "תגיד אלי", "בחודש מאי", "זה כפול", "הנהר כאן"):
        assert real.resolve_names(s) == s
    assert real.resolve_names("ובלו סנייל בהנסיס") == "ו-Blue Snail ב-Henesys"


# ------------------------------------------------------------------ 6, 12, 15, 16, 25: instant answers

def test_an_unknown_word_before_the_name_is_no_sure_answer(kb):
    assert quick.answer("Red Snail hp", kb, t)
    assert quick.answer("Mossy Snail hp", kb, t) is None and quick.answer("Ghost Snail hp", kb, t) is None
    assert quick.answer("what is the hp of Snail", kb, t)


def test_drop_answers_carry_the_caveat_and_say_1_item(kb, monkeypatch):
    monkeypatch.setattr(kb, "drop_lists", lambda key: {"community": [], "MSEA": ["item/2000000"]})
    a = quick.answer("Red Snail drops", kb, t)
    assert a and a.text.startswith("Red Snail drops 1 item:") and t("quick_drops_note") in a.text
    assert a.sources == ["MSEA"]
    assert I18n("he")("quick_drops", name="X", n=1) == "X מפיל פריט אחד:"


@needs_kb
def test_instant_answers_on_the_real_kb(real):
    c = SimpleNamespace(level=30, base_class="Warrior")
    acc = quick.answer("accuracy needed for lupin", real, t, c)
    lupin = combat.monster(real, next(k for k, e in real.entities.items() if e["name"] == "Lupin"))
    assert acc and f"**{combat.acc_needed(30, lupin.level, lupin.avoid)} ACC**" in acc.text and "Lv. 30" in acc.text
    assert quick.answer("כמה דיוק צריך בשביל לופין", real, t, c).text == acc.text
    assert quick.answer("lupin avoid", real, t).text == f"Lupin · Avoid: {lupin.avoid}"
    assert "M.DEF" in quick.answer("Lupin magic defense", real, t).text
    from maplehelper import availability, market
    a = availability.of(real)
    sells = quick.answer("who sells red potion", real, t)
    shops = [s for s in market.npc_prices(real, real._item_by_name["red potion"]).shops if a.place_open(s[1].rsplit(" · ", 1)[-1])]
    assert sells and sells.text.startswith("Where to buy Red Potion:") and f"{min(s[2] for s in shops):,} mesos" in sells.text
    assert not any(town in sells.text for town in ("El Nath", "Orbis") if not a.place_open(town))   # not out: not sold
    where = quick.answer("where is Red Snail", real, t)
    assert where and " · " in where.text.split("\n")[1]                  # "map · region" (kb.map_label)
    # a monster only on a continent that isn't out (Leatty, Jr. Sentinel: Ossyria) is said to be not in the game,
    # until the guide opens it
    leatty, sentinel = (next(k for k in real.monster_keys(n)) for n in ("Leatty", "Jr. Sentinel"))
    assert ("isn't in the game" in quick.answer("where is Leatty", real, t).text) == (not a.monster_key_open(leatty))
    assert ("עוד לא נמצא במשחק" in quick.answer("כמה חיים לג׳וניור סנטינל", real, I18n("he")).text) == \
        (not a.monster_key_open(sentinel))
    king = next(k for k in real.monster_keys("King Slime"))
    if all("<" in m or "Stage" in m for m in real.all_maps(king)):      # only its party quest stage: no "where"
        assert quick.answer("where is King Slime", real, t) is None
    assert quick.answer("Jr Boogie hp", real, t).text.startswith("Jr. Boogie 1 · HP:")
    assert quick.answer("Ghost Stump level", real, t) is None
    assert quick.answer("איפה יש תמנונים", real, t).entities == [
        next(k for k, e in real.entities.items() if e["name"] == "Octopus" and e["category"] == "monster")]
    hp = re.fullmatch(r"Mano · HP: ([\d,]+)", quick.answer("what is the max hp of mano", real, t).text)
    assert hp and int(hp.group(1).replace(",", "")) in {real.get(k)["props"].get("HP") for k in real.monster_keys("Mano")}


# ------------------------------------------------------------------ AI answers: cards, reverse drops, stated level

def test_a_named_monster_makes_no_reverse_drop_question(kb):
    from maplehelper import brain
    assert not brain.is_reverse("what drops does Red Snail have?", kb)
    assert brain.is_reverse("which monsters drop Red Potion?", kb)


@pytest.mark.parametrize("text,level", [
    ("how long to hit level 30?", None), ("best way to hit level 70", None), ("how fast can I hit lv 30", None),
    ("which monsters hit level 20 players hard", None), ("now lv 30 quests?", None),
    ("I'm now level 31", 31), ("just hit lvl 70!", 70), ("Im level 30", 30),
])
def test_stated_level_reads_news_not_questions(text, level):
    from maplehelper import brain
    assert brain.stated_level(text) == level


# ------------------------------------------------------------------ 8, 24: training spots and the island

def test_respawn_cells_read_as_seconds():
    assert combat.respawn_seconds("~7.5s") == 7.5 and combat.respawn_seconds("1m + ~7.5s") == 67.5
    assert combat.respawn_seconds("30s-2m") == 30 and combat.respawn_seconds("1h-1h 30m") == 3600
    assert combat.respawn_seconds("3h") == 10800 and combat.respawn_seconds("") is None
    assert combat.Monster("m/1", "Boss", 80, 1, 1, respawn=10800).boss and not combat.Monster("m/2", "Mob", 1, 1, 1).boss


@needs_kb
def test_bosses_are_no_training_spot(real):
    mobs = combat.monsters(real)
    bosses = {m.name for m in mobs if m.boss}
    assert len(bosses) >= 3 and len(bosses) < len(mobs) / 3              # the respawn column is read, and sane
    for lv in (20, 45, 55, 80):
        assert not any(s.monster.boss for s in combat.spots(real, lv, n=20))
    # the respawn column the rule reads (pages/monster/<id>.md "Map Locations"): an hours-long one on the page
    for m in mobs:
        if m.boss:
            assert re.search(r"\|\s*~?\d+(?:\.\d+)?h\b", real.page(m.key)), m.key


@needs_kb
def test_below_level_8_the_plan_stays_on_maple_island(real):
    for lv in (1, 4, 7):
        p = plan.progress(real, lv, 50.0)
        mob = next(k for k, e in real.entities.items() if e["name"] == p["mob"] and e["category"] == "monster")
        # Maple Island is Maple Road and Rainbow Street (pages/map/*.md "/ Maple Island", audit GAM-3)
        assert any(m.endswith((" Maple Road", " Rainbow Street")) for m in real._top_maps(mob))
    assert plan.progress(real, 7, 50.0)["mob"] == "Orange Mushroom"         # not 280 Snails
    assert plan.progress(real, 9, 50.0)["mob"] == plan.spots_for(real, 9, 1)[0].mob
    kb_says(real.page("guide/beginners-guide-first-steps-in-maple-world"), r"lv 8", "plan's Maple Island levels")


# ------------------------------------------------------------------ 18-19: glossary

def test_glossary_sp_and_acc():
    assert "1 per level up" in glossary.explain("SP", "en") and "3 per level" in glossary.explain("SP", "en")
    assert "נקודה אחת בכל עליית רמה" in glossary.explain("SP", "he")
    assert "3x its Avoid" in glossary.explain("ACC", "en") and "פי 3" in glossary.explain("ACC", "he")


@needs_kb
def test_glossary_numbers_come_from_the_kb(real):
    """The glossary's SP and ACC numbers are the guides': a changed guide is reported for the glossary text."""
    kb_says(real.page("guide/beginners-guide-first-steps-in-maple-world"), r"1 per level-up", "glossary SP")
    kb_says(real.page("guide/maplestory-classic-glossary"), r"3 SP per level", "glossary SP")
    # never-miss ACC over Avoid at an equal level, from the class guide's table: a bit over 3x every time
    rows = re.findall(r"^[\w. ]+ \| \d+ \| (\d+) \| (\d+) ACC$", real.page("guide/cleric-class-guide"), re.M)
    if not rows or not all(3 < int(acc) / int(avoid) < 3.5 for avoid, acc in rows):
        warnings.warn("the Cleric guide's ACC table no longer reads as ~3x Avoid: check glossary ACC", stacklevel=2)


# ------------------------------------------------------------------ 21: crafting

def test_recipes_with_the_same_output_and_other_materials_are_kept():
    page = ("---\n{}\n---\nLv. 1\nneeds 50 EXP · char Lv. 10\n"
            "1 | Bronze Plate\n2 x Bronze Ore\n| 5 | 100 | x | x | x | -10 | 0.5 | Farm only |\n"
            "1 | Bronze Plate\n3 x Iron Ore\n| 5 | 100 | x | x | x | -10 | 0.5 | Farm only |\n"
            "1 | Bronze Plate\n2 x Bronze Ore\n| 5 | 100 | x | x | x | -10 | 0.5 | Farm only |\n")
    crafting._levels.cache_clear()
    recipes = crafting._levels(page)[0].recipes
    assert [r.ingredients for r in recipes] == [[(2, "Bronze Ore")], [(3, "Iron Ore")]]


@needs_kb
def test_recipe_counts_match_the_pages(real):
    for prof in crafting.PROFESSIONS:
        total = re.search(r"(\d+) recipes", real.page(f"crafting/efficiency__{prof}"))
        assert sum(len(lv.recipes) for lv in crafting.levels(real, prof)) == int(total.group(1)), prof


# ------------------------------------------------------------------ 22: prices

@needs_kb
def test_released_filters_unreleased_shops(real):
    from maplehelper import market
    from maplehelper import availability
    a = availability.of(real)
    red_cross = market.npc_prices(real, real._item_by_name["red cross shield"])
    open_shops = [s for s in red_cross.shops if combat.released(real, s[1])]
    assert open_shops and all(a.place_open(s[1].rsplit(" · ", 1)[-1]) for s in open_shops)
    # a town is released exactly when the guide has opened its continent (El Nath: Ossyria)
    for town in ("El Nath", "Orbis", "Henesys", "Perion"):
        assert combat.released(real, town) == a.place_open(town)
    assert combat.released(real, "Henesys")


# ------------------------------------------------------------------ items in the game (availability.item_open)

def _guide_kb(tmp_path, items: dict[str, str]) -> KnowledgeBase:
    """A KB with a release guide (Victoria Island in, Ossyria out), two towns, a quest at each, and these item pages."""
    guide = ("Confirmed content\nClassic maps on Victoria Island are confirmed.\n"
             "Not at launch\nOssyria and 3rd job are not initial-launch content.\n")
    pages = {"guide/maplestory-classic-worlds-release-date": guide,
             "map/1": "# Henesys\nLocation Victoria Road / Victoria Island\n",
             "map/2": "# Orbis\nLocation Orbis / Ossyria\n",
             "npc/1": "# Rina\nLocation\nHenesys\n", "npc/2": "# Lisa\nLocation\nOrbis\n",
             "quest/1": "# Rina's Errand\n", "quest/2": "# Lisa's Errand\n", **items}
    entities = [ent("guide/maplestory-classic-worlds-release-date", "Release"), ent("map/1", "Henesys"),
                ent("map/2", "Orbis"), ent("npc/1", "Rina"), ent("npc/2", "Lisa"),
                ent("quest/1", "Rina's Errand", NPC="Rina", Area="Victoria Island"),
                ent("quest/2", "Lisa's Errand", NPC="Lisa", Area="Ossyria")]
    entities += [ent(k, f"Item {k}") for k in items]
    return small_kb(tmp_path, entities, {}, pages)


def test_an_item_is_in_the_game_when_one_of_its_sources_is(tmp_path):
    from maplehelper import availability
    shop = "Where to buy\n{npc} Grocer cheapest\n{place}\n50\nmesos\nCOT2 prices\nDropped By\nCommunity sourced\n"
    kb = _guide_kb(tmp_path, {
        "item/1": shop.format(npc="Rina", place="Victoria Road: Henesys Shop · Henesys"),
        "item/2": shop.format(npc="Lisa", place="Orbis: Orbis Department Store · Orbis"),
        "item/3": "Quest Reward\nRina's Errand ( 25 %)\nSimilar Scroll items\n",
        "item/4": "Quest Reward\nLisa's Errand\nSimilar Scroll items\n",
        "item/5": "Craftable\nArcforge Consumables Produces × 1\nIngredients\n",
        "item/6": "Cash Shop\n100 NX\nClosed-test price · Available\n",
        "item/7": "Cash Shop\n100 NX\nClosed-test price · Unavailable\n",
        "item/8": "Dropped By\nCommunity sourced\nLoading…\nFree Market Prices\n",
        "item/9": "Needed By\n1 quest\nQuests\nQuest | Lv | Qty\nRina's Errand Rina\n| 17 | 3\n",
    })
    a = availability.of(kb)
    assert {k: a.item_open(k) for k in (f"item/{i}" for i in range(1, 10))} == {
        "item/1": True, "item/2": False,          # a shop in Henesys / only one in Orbis
        "item/3": True, "item/4": False,          # a reward of an open / a shut quest
        "item/5": True,                           # a recipe makes it
        "item/6": True, "item/7": False,          # the Cash Shop sells it / doesn't
        "item/8": False,                          # no source the KB confirms
        "item/9": True,                           # an open quest asks for it
    }


@needs_kb
def test_items_in_the_game_on_the_real_kb(real):
    from maplehelper import availability
    a = availability.of(real)
    name = {e["name"]: k for k, e in real.entities.items() if e["category"] == "item"}
    for n in ("Red Potion", "Snail Shell", "Apple", "Gloves Attack Scroll: Lesser", "Elixir", "Green Skullcap"):
        assert a.item_open(name[n]), n
    if not a.place_open("Orbis") and not a.place_open("El Nath"):        # Ossyria not out: its items aren't either
        for n in ("Return Scroll to Orbis", "Dark Jr. Yeti Skin", "Firebomb Flame", "Cerebes Tooth"):
            assert not a.item_open(name[n]), n


# ------------------------------------------------------------------ 26: guide captions

def test_figure_captions_keep_their_lines(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import build_guides as bg
    images = bg.Images(tmp_path)
    monkeypatch.setattr(images, "get", lambda src, max_w=360: ("p.png", 43, 70))
    page = ("<html><body><main><h1>G</h1><p>Intro.</p><p>Body.</p><figure><svg></svg><img src='/a.png' width='43'>"
            "<figcaption><strong>Skill Lv1</strong><span>500 x 300 px</span></figcaption>"
            "<p>250 px left and right, 150 px up and down.</p></figure></main></body></html>")
    blocks = bg.convert(page, images)["blocks"]
    assert blocks == [{"p": "Body."}, {"img": "p.png", "w": 43, "h": 70,
                       "cap": "**Skill Lv1** 500 x 300 px\n250 px left and right, 150 px up and down."}]


def test_haste_captions_are_whole_in_both_languages():
    for slug in ("assassin-class-guide", "bandit-class-guide", "fighter-class-guide"):
        for lang in ("en", "he"):
            g = json.loads((ROOT / "assets" / "guides" / lang / f"{slug}.json").read_text(encoding="utf-8"))
            caps = [b["cap"] for b in g["blocks"] if b.get("img") == "8ea44ba66a1c56.png"]
            assert len(caps) == 2 and all("\n" in c and "px" in c.split("\n")[-1] for c in caps), (slug, lang)
            assert not any(re.search(r"Lv\d+\d{3} x", c) for c in caps)


# ------------------------------------------------------------------ 27: names, drops and the level digest (audit)

RELEASE = ("---\n{}\n---\n\n# Release\n\nConfirmed content\nClassic maps on Victoria Island.\n"
           "Not at launch\nOssyria and 3rd job are not initial-launch content.\n")


def test_a_shared_alias_means_the_plain_entity(tmp_path):
    """aliases.json gives some Hebrew names to every variant of a town or NPC: the last one won (an instance arena,
    a PQ stage NPC). The plain one wins now, variants alone mean their plain name's entity, and a real tie is no alias."""
    kb = small_kb(tmp_path, [
        ent("map/1", "Forgotten Hollow"), ent("map/2", "Forgotten Hollow Instance 2"), ent("map/3", "Forgotten Hollow Instance 3"),
        ent("npc/1", "Zelya"), ent("npc/2", "Zelya (Free Market)"), ent("npc/3", "Pason"), ent("npc/4", "Pison"),
    ], {"map/1": ["פורגוטן הולו"], "map/2": ["פורגוטן הולו", "החלל הנשכח"], "map/3": ["פורגוטן הולו", "החלל הנשכח"],
        "npc/2": ["זליה"], "npc/1": ["זליה"], "npc/3": ["פייסון"], "npc/4": ["פייסון"]})
    assert kb.find_mentions("איך מגיעים לפורגוטן הולו?") == ["map/1"]
    assert kb.find_mentions("איפה החלל הנשכח") == ["map/1"]
    assert kb.find_mentions("איפה זליה") == ["npc/1"]
    assert kb.find_mentions("איפה פייסון") == []


def test_a_dropped_alias_hides_the_shorter_alias_inside_it(tmp_path):
    """"טיק טוק" is dropped (TikTok): its "טיק" answered with Tick's stats. "למיין" (to sort) is no Myen."""
    kb = small_kb(tmp_path, [ent("monster/1", "Tick", Level=34), ent("npc/1", "Myen")],
                  {"monster/1": ["טיק"], "npc/1": ["מיין"]})
    # "טיק" alone is an everyday word too now ("זה היה טיק קטן", "כל טיק שרת"): Tick goes by its English name
    assert kb.find_mentions("מה הלבל של טיק-טוק") == [] and kb.find_mentions("מה הלבל של טיק") == []
    assert kb.find_mentions("what level is Tick") == ["monster/1"]
    assert quick.answer("מה הלבל של טיק-טוק", kb, t) is None
    assert kb.find_mentions("איך למיין את האינבנטורי?") == []
    assert kb.resolve_names("כדאי למיין את הפריטים") == "כדאי למיין את הפריטים"
    assert kb.resolve_names("ראיתי טיק טוק") == "ראיתי טיק טוק" and kb.resolve_names("ראיתי טיק") == "ראיתי טיק"


def test_a_drop_line_picks_the_item_its_level_and_page_name(tmp_path):
    """Two items share a name: the "Lv N" line under the drop picks the equipment, and the item page's own
    "Dropped By" list breaks a tie the line can't."""
    drops = "Drops (MS Classic)\nEquipment\nBlue Moon\nLv 50 · Thief\nDark Shadow\nLv 40 · Thief\nMap Locations\n"
    kb = small_kb(tmp_path, [
        {**ent("item/1", "Blue Moon", **{"Level Requirement": 40}), "type": "Equip / Earrings"},
        {**ent("item/2", "Blue Moon", **{"Level Requirement": 50}), "type": "Equip / Top"},
        {**ent("item/3", "Dark Shadow", **{"Level Requirement": 40}), "type": "Equip / Top"},
        {**ent("item/4", "Dark Shadow", **{"Level Requirement": 40}), "type": "Equip / Top"},
        ent("monster/1", "Iron Hog", Level=28),
    ], {}, {"monster/1": drops, "item/3": "Dropped By\nYeti\n", "item/4": "Dropped By\nIron Hog\nLv 28\n"})
    assert kb.monster_drops("monster/1") == ["item/2", "item/4"]


@needs_kb
def test_instant_acc_answer_uses_the_level_the_question_names(real):
    """"acc needed for lupin at level 25" answered for the character's level 50 (86 ACC, far too low)."""
    m = combat.monster(real, "monster/35")
    a = quick.answer("acc needed for lupin at level 25", real, t, SimpleNamespace(level=50))
    assert a and "Lv. 25" in a.text and f"**{combat.acc_needed(25, m.level, m.avoid)} ACC**" in a.text
    he = quick.answer("כמה דיוק צריך ללופין בלבל 25", real, t, SimpleNamespace(level=50))
    assert he and "Lv. 25" in he.text
    assert "Lv. 50" in quick.answer("acc needed for lupin", real, t, SimpleNamespace(level=50)).text


def test_unreleased_pages_are_marked_in_the_ai_context(tmp_path):
    """A question naming El Nath pre-fetched its page, which reads like any town's: the AI sent players there."""
    from maplehelper import brain
    kb = small_kb(tmp_path, [ent("guide/maplestory-classic-worlds-release-date", "Release"),
                             ent("map/1", "El Nath"), ent("map/2", "Henesys")], {},
                  {"guide/maplestory-classic-worlds-release-date": RELEASE,
                   "map/1": "El Nath\nLocation El Nath / Ossyria\n", "map/2": "Henesys\nLocation Victoria Road / Victoria Island\n"})
    p = brain.build_prompt("how do I get to El Nath from Henesys?", None, None, kb, has_screenshot=False)
    assert f"[map/1] ({brain.NOT_OUT})" in p and "[map/2]\n" in p


def test_drop_table_comes_with_a_names_table(kb_copy):
    """index.json is one 1.3 MB line Gemini's grep can't read: names.tsv has one entity per line."""
    kb = KnowledgeBase(kb_copy)
    kb.ensure_drop_table()
    rows = (kb_copy / "names.tsv").read_text(encoding="utf-8").split("\n")
    assert rows[0] == "key\tcategory\tname\ttype" and "monster/130101\tmonster\tRed Snail\t" in rows
    assert len(rows) == len(kb.entities) + 1


def test_scraper_writes_one_entity_per_line(tmp_path):
    import scrape_meowdb
    entries = [{"key": "monster/1", "name": "Snail"}, {"key": "item/1", "name": "Snail Shell"}]
    scrape_meowdb.write_index(tmp_path / "index.json", entries)
    text = (tmp_path / "index.json").read_text(encoding="utf-8")
    assert json.loads(text) == entries and len(text.splitlines()) == 4


@needs_kb
def test_real_level_digest_lists_only_monsters_a_player_can_train_on(real):
    from maplehelper import availability
    a = availability.of(real)
    shut = [w for w in ("Orbis", "El Nath") if not a.place_open(w)] + (["Ludibrium"] if not a.place_open("Orbis") else [])
    for lv in range(1, 71):
        rows = [r.split(" | ") for r in real.level_digest(lv).split("\n")[1:]]
        names = [r[0] for r in rows]
        assert not any(combat.special_monster(n) for n in names), lv
        assert len(names) == len(set(names)) or all(r[4] for r in rows if names.count(r[0]) > 1), lv
        assert not any(w in r[4] for r in rows for w in shut), lv
    # the maps as names, not glued to their street ("Drake's Meal Table Dungeon", KB-21); the Hollow's boss is out
    # while the guide keeps the Hollow closed (KB-1)
    d50 = real.level_digest(50)
    assert "Meal Table Dungeon" not in d50 and ("Drake's Meal Table" not in d50 or "Drake's Meal Table · Dungeon" in d50)
    if "Forgotten Hollow" in a.closed_areas:
        assert "Rotten Mushmom" not in real.level_digest(60)


@needs_kb
def test_real_shared_and_dropped_aliases(real):
    assert real.find_mentions("איך מגיעים לפורגוטן הולו?") == ["map/010006000"]
    assert real.find_mentions("איפה זליה") == ["npc/701"] and real.find_mentions("איפה נלה") == ["npc/406"]
    assert real.find_mentions("איך למיין את האינבנטורי?") == []
    assert quick.answer("מה הלבל של טיק-טוק", real, t) is None


@needs_kb
def test_real_drops_of_duplicate_named_items(real):
    """Iron Hog's "Blue Moon / Lv 50" is the Lv 50 top (item/1088), not the Lv 40 earring of the same name: on every
    monster page, a drop line whose name several items share is the item of the level written under it."""
    shared = {}
    for k, e in real.entities.items():
        if e["category"] == "item":
            shared.setdefault(e["name"], []).append(k)
    checked = 0
    for k, e in real.entities.items():
        if e["category"] != "monster":
            continue
        lines = real.page(k).split("\n")
        lv = {lines[i].strip(): int(m.group(1)) for i in range(len(lines) - 1)
              if len(shared.get(lines[i].strip(), [])) > 1 and (m := re.match(r"Lv (\d+)\b", lines[i + 1]))}
        for item in real.monster_drops(k):
            if real.get(item)["name"] in lv:
                checked += 1
                assert real.get(item)["props"].get("Level Requirement") == lv[real.get(item)["name"]], (k, item)
    assert checked                                     # the KB still has such drops, so the rule is exercised


@needs_kb
def test_real_instant_shop_answer_names_the_citizen_grade(real):
    # "Gloves Attack Scroll: Lesser ... Guardian of the Village +": whichever items a citizen grade sells tonight
    from maplehelper import tables
    graded = [r for r in tables.generate(real)["shops"] if r["rank"]][:10]
    assert graded
    for r in graded:
        a = quick.answer(f"who sells {r['item']}", real, t)
        assert a and r["rank"] in a.text, r["item"]


def test_item_page_droppers_not_in_the_game_are_marked(tmp_path):
    """The item page's MSEA "Dropped By" list named Jr. Sentinel (Orbis), and the AI gave it as a source."""
    from maplehelper import brain
    kb = small_kb(tmp_path, [ent("guide/maplestory-classic-worlds-release-date", "Release"),
                             ent("item/1", "Subi Throwing Stars"), ent("monster/1", "Mano", Level=20),
                             ent("monster/2", "Jr. Sentinel", Level=26), ent("map/1", "Thicket"), ent("map/2", "Tower")], {},
                  {"guide/maplestory-classic-worlds-release-date": RELEASE,
                   "item/1": "Dropped By\nMSEA Reference Drops\nMano\nLv 20\nJr. Sentinel\nLv 26\nFree Market Prices\n",
                   "monster/1": "Map Locations\nMap | Count\n--- | ---\nThicket Victoria Road | 1 | x\n",
                   "monster/2": "Map Locations\nMap | Count\n--- | ---\nTower Orbis | 1 | x\n",
                   "map/1": "Location Victoria Road / Victoria Island\n", "map/2": "Location Orbis / Ossyria\n"})
    p = brain.build_prompt("which monsters drop Subi Throwing Stars?", None, None, kb, has_screenshot=False)
    assert "\nMano\nLv 20\nJr. Sentinel (not in the game)\nLv 26" in p


def test_no_card_for_what_is_not_in_the_game(tmp_path):
    from maplehelper import brain
    from maplehelper.providers.base import RawResult
    kb = small_kb(tmp_path, [ent("guide/maplestory-classic-worlds-release-date", "Release"),
                             ent("map/1", "El Nath"), ent("map/2", "Henesys")], {},
                  {"guide/maplestory-classic-worlds-release-date": RELEASE,
                   "map/1": "Location El Nath / Ossyria\n", "map/2": "Location Victoria Road / Victoria Island\n"})
    b = brain.Brain(kb)
    b.backend = SimpleNamespace(exe="fake", run=lambda *a, **k: RawResult(
        text='El Nath isn\'t out yet; stay around Henesys.\n@@META@@\n{"entities": ["map/1", "map/2"]}'))
    assert b.ask("how do I get to El Nath?", None, None, None).entities == ["map/2"]



@needs_kb
def test_an_area_the_guide_calls_closed_is_closed_with_its_streets(real):
    """"Forgotten Hollow is closed during Founder's Access": its maps (Shallow / Deep Passage) and their monsters are
    out, while the rest of Victoria Island (whose towns the Hollow's guide also names) stays in."""
    from maplehelper import availability, combat
    o = availability.of(real)
    # whatever the guide closes tonight is closed (no skip once it opens the Hollow: the nightly must still check
    # that its towns and monsters stay in)
    assert all(not o.place_open(area) for area in o.closed_areas)
    assert o.place_open("Ellinia") and o.place_open("Henesys")
    assert all(o.monster_key_open(m.key) for m in combat.monsters(real) if m.name in ("Blue Snail", "Ligator"))
    if "Forgotten Hollow" in o.closed_areas:
        assert not any(o.monster_key_open(m.key) for m in combat.monsters(real) if m.name in ("Myewood", "Sporewood"))


@needs_kb
def test_real_level_digest_merges_twins_and_skips_mapless(real):
    """audit AI-26: "Jr. Boogie 1" and "2" both listed, a map-less King Slime among the nearby monsters."""
    for lv in (30, 33):
        rows = [r.split(" | ") for r in real.level_digest(lv).split("\n")[1:]]
        assert all(r[5].strip() for r in rows), lv
        assert sum("Jr. Boogie" in r[0] for r in rows) <= 1, lv


@needs_kb
def test_the_hollows_hidden_maps_on_a_shared_street_are_closed_too(real):
    """Someone Else's Grave (Rotten Mushmom), Collision of Ice and Fire and The Valley of Death sit on Victoria's
    "Hidden Street" beside Pig Park, and every portal of theirs leads into the Hollow: closed with it, and so are their
    monsters. The Hollow guide's open entrances stay open, and so do the Hollow's map keys' own checks."""
    from maplehelper import availability
    o = availability.of(real)
    if "Forgotten Hollow" not in o.closed_areas:
        pytest.skip("the release guide no longer calls Forgotten Hollow closed")
    for key in ("map/010006121", "map/010006022", "map/010006031", "map/010006000", "map/010006120",
                "map/080003000"):
        assert not o.entity_open(key), key
    for key in ("monster/700003", "monster/62", "monster/54", "monster/57", "monster/59"):
        assert not o.entity_open(key), (key, real.get(key)["name"])
    for key in ("map/010002071", "map/010005054", "map/010002000", "map/010004091"):      # entrances, Ellinia, Boar
        assert o.entity_open(key), key
    assert o.entity_open("npc/800015")           # the Arcane Station: in Ellinia and Sleepywood too


@needs_kb
def test_maps_whose_page_names_no_continent_are_in_the_game(real):
    """The Free Market, the KPQ stages and the 2nd-job test maps write only "Location Hidden Street"; two more pages
    write the line reversed ("Location Truth Booth / Hidden Street"). On no continent the KB closes, they're in the
    game, with their NPCs. The set is pinned: a KB update that adds one gets a human look first."""
    from maplehelper import availability
    o = availability.of(real)
    unplaced = sorted(k for k, c in o.map_cell.items() if o.map_place[c][0] == availability.NO_CONTINENT)
    assert unplaced == sorted([f"map/080000{n}00" for n in range(7)] + [f"map/080001{n}00" for n in range(4)]
                              + [f"map/0800020{n:02}" for n in range(12)] + ["map/088000000", "map/089000000"])
    for key in ("map/080002000", "map/080000000", "map/080001000", "npc/800008", "npc/800001", "npc/800003"):
        assert o.entity_open(key), key
    # "Hidden Street" is a street (43 Victoria maps on it), never a continent the AI is told is closed
    assert "Hidden Street" not in o.continents
    note = o.scope_note()
    assert "Hidden Street" not in note.split("NOT in the game", 1)[1].split("—", 1)[0]
    assert "(no continent)" not in note
    # the Founder's Access GM events are in the guide: their maps aren't listed as "not in the game"
    assert "Event" not in note.split("NOT in the game", 1)[1].split("—", 1)[0]
    assert "Event maps open only during GM events" in note
    assert not o.entity_open("npc/900016")       # the event maps stay out of the tables and routes


@needs_kb
def test_npcs_with_several_locations_are_placed(real):
    """"Locations (4)" pages (every crafting station, Doofus, Eurek): read like "Location", open when any place is."""
    from maplehelper import availability
    o = availability.of(real)
    for key in ("npc/800010", "npc/800011", "npc/800012", "npc/800013", "npc/800014", "npc/800015", "npc/209",
                "npc/605"):
        assert o.entity_open(key), (key, real.get(key)["name"])
    assert o.npc_places("npc/800010")[0] == "Perion Victoria Road"
    assert not o.entity_open("npc/1002")         # "Locations (0)": dynamically placed, no map to show
    # ended event quests say so after their kind: "Daily Ended", "Self-Starting Ended"
    assert not o.entity_open("quest/500005") and not o.entity_open("quest/500006")


def test_scope_note_says_nothing_without_the_release_guide(tmp_path):
    """No release guide filters nothing, so the prompt mustn't say Maple Island is not in the game."""
    from maplehelper import availability
    kb = small_kb(tmp_path, [ent("map/1", "Henesys")], {}, {"map/1": "Location Victoria Road / Victoria Island\n"})
    assert availability.of(kb).scope_note() == ""


@needs_kb
@pytest.mark.parametrize("text", [
    "איפה אפשר להרוג פיה", "מה מפילה פיה", "זה היה טיק קטן", "יש לי פול HP", "יש לו לוק חדש לדמות",
    "ברי לי שזה לא שווה", "אורה גדולה", "יונה עפה מעל הבית", "אני לין אצל חבר",
])
def test_everyday_hebrew_words_name_no_npc_or_monster(real, text):
    assert real.find_mentions(text) == [] and real.find_mentions(text, answer=True) == []
    assert real.resolve_names(text) == text


@needs_kb
def test_first_name_aliases_count_only_when_the_player_asks_for_the_npc(real):
    """"אלון חבר שלי" is a friend: an AI answer and dictated text keep the name, a question asking for Oak finds it."""
    for text in ("אלון חבר שלי משחק איתי", "החבר שלי מאיה עזרה לי", "אני ואלכס משחקים ביחד", "מאיה אמרה לי לבוא"):
        assert real.find_mentions(text, answer=True) == [] and real.resolve_names(text) == text
    assert real.find_mentions("איפה מאיה") == [real.npc_key("Maya")]


@needs_kb
@pytest.mark.parametrize("text", [
    "Max HP is important for warriors.", "Rain or shine, you should grind at Ant Tunnel.",
    "Jack of all trades builds are weak.", "Exit the map and talk to the cab.", "The Oak tree near the river looks nice.",
    "Silver is a good color for armor.", "Pan your camera to the left.", "Go to Henesys and talk to the Chef there.",
    "which is better for me, a sword or an axe", "I want max level fast. Max HP or Max MP?",
    "Sword or Axe for a lvl 20 fighter?", "Sword vs Axe?", "Crossbow or Bow for a bowman?",
    "Spear or Polearm for a spearman?", "the Sword or the Axe for my fighter", "Silver in the Ores?", "Jack in a box",
])
def test_a_sentence_start_or_a_generic_word_is_no_npc_or_item(real, text):
    """Two weapon families compared name no starter item (review2 LOG-3); "in a box" is no place (LOG-8)."""
    assert not {k for k in real.find_mentions(text) if k.startswith(("npc/", "item/"))}


@needs_kb
def test_a_common_word_npc_written_as_a_name_still_counts(real):
    assert real.find_mentions("where is Max") == [real.npc_key("Max")]
    assert real.npc_key("Max") in real.find_mentions("talk to Max in Henesys")
    assert real.npc_key("Rain") in real.find_mentions("Where is Rain?")


@needs_kb
@pytest.mark.parametrize("text, name", [
    ("where is the Anvil", "Anvil"), ("Anvil location", "Anvil"), ("where is the Anvil in Perion?", "Anvil"),
    ("Max", "Max"), ("Exit", "Exit"), ("Max?", "Max"),
    ("Sword stats", "Sword"), ("how much does the Spear cost", "Spear"), ("Spear vs Fork on a Stick", "Spear"),
    ("what does the Chef sell", "Chef"),
])
def test_a_plain_question_about_a_common_word_npc_or_item_still_finds_it(real, text, name):
    """KB-8/KB-31's rule (a sentence start or "the <Name>" is no name) dropped "where is the Anvil", "Max" asked
    alone and "Sword stats" (review CORE-2). The everyday sentences above still find nothing."""
    keys = real.find_mentions(text)
    assert any((real.get(k) or {}).get("name") == name for k in keys), keys


def test_a_hebrew_plural_of_a_name_ending_in_a_final_letter(tmp_path):
    """"גדם" + "ים" is written "גדמים": the final mem turns plain, and the plural still names Stump."""
    kb = small_kb(tmp_path, [ent("monster/1", "Stump", Level=4)], {"monster/1": ["גדם"]})
    assert kb.find_mentions("איפה יש גדמים") == ["monster/1"] and kb.find_mentions("איפה יש גדם") == ["monster/1"]


def test_a_damaged_or_missing_page_reads_without_failing(tmp_path):
    kb = small_kb(tmp_path, [ent("monster/1", "Stump"), ent("monster/2", "Slime")], {}, {"monster/1": "# Stump\n"})
    (tmp_path / "pages" / "monster" / "1.md").write_bytes(b"# Stump\n\xff\xfe broken")
    assert kb.page("monster/1").startswith("# Stump") and kb.page("monster/2") == ""


@needs_kb
def test_scope_note_names_hidden_street_maps_the_kb_has(real):
    """The note named "Monkey Forest", a map the KB doesn't have ("Monkey Forest I/II"): its examples are now
    the KB's own open maps on that street (review CORE-10)."""
    import re as re_
    from maplehelper import availability
    o = availability.of(real)
    m = re_.search(r"in the game \(e\.g\. (.+?) and (.+?) on (.+?)\)", o.scope_note())
    assert m, o.scope_note()
    for name in m.group(1, 2):
        assert o.map_place.get(f"{name} Hidden Street", ("",))[0] == m.group(3) and o.map_open(f"{name} Hidden Street")
    assert m.group(3) in o.confirmed
