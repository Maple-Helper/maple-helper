"""The query planner (maplehelper/planner.py): a list / filter question's intent and slots, Hebrew and English, the
exact rows it retrieves (tiny tables here, the real KB's below), the block build_prompt adds, and the questions it
must leave alone."""
from pathlib import Path

import pytest

from maplehelper import brain, planner, tables
from maplehelper.kb import KnowledgeBase
from maplehelper.store import Character

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")

THIEF = Character(id="t", name="Amit", base_class="Thief", job="Assassin", level=31)


# ---------------------------------------------------------------- slots

@pytest.mark.parametrize("question, fams", [
    ("איזה משימות נותנות לי גלימות כשאני מסיים אותן?", ["Cape"]),
    ("איזה כפפות אני יכול ללבוש ברמה 30?", ["Gloves"]),
    ("איזה כובעים יש ל-Thief בין רמה 20 ל-35?", ["Hat"]),
    ("which quests reward scrolls?", ["Scroll"]),
    ("who sells arrows for crossbows", ["Arrow", "Crossbow"]),
    ("סקרולים לכפפות עם התקפה", ["Scroll", "Gloves"]),
    ("where do I get a return scroll", ["Return Scroll"]),        # not a scroll for a slot
    ("best throwing stars", ["Throwing Star"]),
    ("השיקויים הכי משתלמים", ["Potion"]),
    ("what can I wear", []),
    ("אוכל ללבוש את זה?", []),                                     # "אוכל" is "I can" here, not food
    ("איך מגיעים למטה?", []),                                      # "למטה" is down, not a staff
])
def test_item_families(question, fams):
    assert [f for f, _ in planner.families(question)] == fams


@pytest.mark.parametrize("question, want", [
    ("איזה כפפות אני יכול ללבוש ברמה 30?", (30, None, None)),
    ("איזה כובעים יש ל-Thief בין רמה 20 ל-35?", (None, 20, 35)),
    ("איזה מפלצות נותנות הכי הרבה EXP בין רמה 30 ל-40?", (None, 30, 40)),
    ("monsters level 30-40", (None, 30, 40)),
    ("gloves between 20 and 30", (None, 20, 30)),
    ("best bow for Lv. 30", (30, None, None)),
    ("where should I train at 20", (20, None, None)),
    ("איפה לעשות גריינד ב-45", (45, None, None)),
    ("מרמה 10 עד 20", (None, 10, 20)),
    ("it costs 500 mesos at 20% off", (None, None, None)),
    ("what drops 50 mesos", (None, None, None)),
])
def test_levels(question, want):
    assert planner.levels(question) == want


@pytest.mark.parametrize("question, job", [
    ("Hunter skills", "Hunter"), ("הסקילים של אסאסין", "Assassin"), ("hats for thieves", "Thief"),
    ("כובעים לגנב", "Thief"), ("נעליים לקוסם", "Magician"), ("gloves for archers", "Bowman"),
    ("F/P Wizard skills", "F/P Wizard"), ("Page skills", "Page"), ("which page shows drops", None),
    ("ספירמן", "Spearman"), ("what can I wear", None),
])
def test_jobs(question, job):
    assert planner.job_named(question) == job


# ---------------------------------------------------------------- retrieval on tiny tables

class FakeKB:
    def __init__(self, mentions: dict[str, list[str]] | None = None, names: dict[str, str] | None = None):
        self.mentions, self.names = mentions or {}, names or {}

    def find_mentions(self, text, max_results=5, answer=False):
        return next((keys for word, keys in self.mentions.items() if word.lower() in text.lower()), [])

    def get(self, key):
        return {"name": self.names[key]} if key in self.names else None

    def page(self, key):
        return ""


def eq(item, key, slot, job, req_lv, **stats):
    return {"item": item, "key": key, "slot": slot, "job": job, "req_lv": req_lv, **stats}


TINY = {
    "equips": [eq("Work Gloves", "item/1", "Gloves", "Any", None, wdef=2),
               eq("Dark Briggon", "item/2", "Gloves", "Thief", 30, wdef=10, req_luk=50),
               eq("Bronze Missel", "item/3", "Gloves", "Warrior", 30, wdef=21),
               eq("Steal Gloves", "item/4", "Gloves", "Thief", 35, wdef=15),
               eq("Garnier", "item/5", "Claw", "Thief", 10, watk=10),
               eq("Meba", "item/6", "Claw", "Thief", 25, watk=19),
               eq("Fish Spear", "item/7", "Spear", "Any", 20, watk=39),
               eq("War Bow", "item/8", "Bow", "Bowman", 10, watk=30)],
    "rewards": [
        {"quest": "Stranger's Identity", "quest_level": 23, "quest_key": "quest/1", "area": "Kerning City",
         "item": "Old Raggedy Cape", "count": 1, "item_type": "Equip / Cape", "item_key": "item/20", "kind": "sure",
         "for": None},
        {"quest": "What Pia Borrowed", "quest_level": 42, "quest_key": "quest/2", "area": "Henesys",
         "item": "Red Cape", "count": 1, "item_type": "Etc / Quest Item", "item_key": "item/21", "kind": "sure",
         "for": None},
        {"quest": "Flying Medicine", "quest_level": 46, "quest_key": "quest/3", "area": "Kerning City",
         "item": "Green Icarus Cape", "count": 1, "item_type": "Equip / Cape", "item_key": "item/22",
         "kind": "random 33.3%", "for": "Any Class"},
        {"quest": "Flying Medicine", "quest_level": 46, "quest_key": "quest/3", "area": "Kerning City",
         "item": "Blue Icarus Cape", "count": 1, "item_type": "Equip / Cape", "item_key": "item/23",
         "kind": "random 33.3%", "for": "Any Class"},
        {"quest": "Pia's Gift", "quest_level": 42, "quest_key": "quest/4", "area": "Henesys",
         "item": "Cape STR Scroll: Greater", "count": 1, "item_type": "Use / Scroll", "item_key": "item/24",
         "kind": "random 25%", "for": "Any Class"},
        {"quest": "Hat Quest", "quest_level": 16, "quest_key": "quest/5", "area": "Henesys",
         "item": "Hat Accuracy Scroll: Lesser", "count": 2, "item_type": "Use / Scroll", "item_key": "item/25",
         "kind": "sure", "for": None}],
    "shops": [
        {"npc": "Arturo", "npc_key": "npc/1", "item": "Arrows for Bows", "item_key": "item/30",
         "item_type": "Use / Arrow", "price": 1, "place": "Victoria Road: Perion Department Store · Perion",
         "label": "COT2", "rank": None},
        {"npc": "Luna", "npc_key": "npc/2", "item": "Arrows for Bows", "item_key": "item/30",
         "item_type": "Use / Arrow", "price": 1, "place": "Victoria Road: Henesys Department Store · Henesys",
         "label": "COT2", "rank": None},
        {"npc": "Arturo", "npc_key": "npc/1", "item": "Arrows for Crossbows", "item_key": "item/31",
         "item_type": "Use / Arrow", "price": 1, "place": "Victoria Road: Perion Department Store · Perion",
         "label": "COT2", "rank": None},
        {"npc": "Raymond", "npc_key": "npc/3", "item": "Bronze Arrows for Bows", "item_key": "item/32",
         "item_type": "Use / Arrow", "price": 2, "place": "Victoria Road: Henesys Town Hall · Henesys",
         "label": "COT2", "rank": "Helpful Stranger"}],
    "maps": [{"map": "Ant Tunnel I", "key": "map/1", "street": "Dungeon", "exp_hr": 100},
             {"map": "Ant Tunnel Park", "key": "map/2", "street": "Dungeon", "exp_hr": 300},
             {"map": "Deep Ant Tunnel I", "key": "map/3", "street": "Dungeon", "exp_hr": 200},
             {"map": "Henesys", "key": "map/4", "street": "Victoria Road", "exp_hr": None},
             {"map": "Line 1 <Area 1>", "key": "map/5", "street": "Kerning City Subway", "exp_hr": 400},
             {"map": "Mixed Swamp", "key": "map/6", "street": "Warning Street", "exp_hr": 900}],
    "spawns": [
        {"monster": "Horny Mushroom", "monster_key": "monster/1", "level": 22, "map": "Ant Tunnel I", "map_key": "map/1",
         "count": 15, "respawn": 7.5},
        {"monster": "Zombie Mushroom", "monster_key": "monster/2", "level": 24, "map": "Ant Tunnel I",
         "map_key": "map/1", "count": 11, "respawn": 7.5},
        {"monster": "Evil Eye", "monster_key": "monster/3", "level": 27, "map": "Deep Ant Tunnel I", "map_key": "map/3",
         "count": 21, "respawn": 7.5},
        {"monster": "Zombie Mushroom", "monster_key": "monster/2", "level": 24, "map": "Ant Tunnel Park",
         "map_key": "map/2", "count": 23, "respawn": 7.5},
        {"monster": "Bubbling", "monster_key": "monster/4", "level": 15, "map": "Line 1 <Area 1>", "map_key": "map/5",
         "count": 64, "respawn": 7.5},
        {"monster": "Croco", "monster_key": "monster/5", "level": 52, "map": "Mixed Swamp", "map_key": "map/6",
         "count": 24, "respawn": 7.5},
        {"monster": "Ligator", "monster_key": "monster/6", "level": 31, "map": "Mixed Swamp", "map_key": "map/6",
         "count": 10, "respawn": 7.5}],
    "monsters": [{"monster": n, "key": f"monster/{i}", "level": lv, "hp": 100 * i, "exp": e, "maps": "x"}
                 for i, (n, lv, e) in enumerate((("Horny Mushroom", 22, 41), ("Zombie Mushroom", 24, 47),
                                                 ("Evil Eye", 27, 55), ("Bubbling", 15, 28), ("Croco", 52, 150),
                                                 ("Ligator", 31, 70)), 1)],
    "quests": [
        {"quest": "Thief Thing", "key": "quest/10", "level": 25, "area": "Kerning City", "npc": "Dark Lord",
         "npc_key": "npc/9", "turn_in": None, "job": "Thief only", "exp": 900},
        {"quest": "Mage Thing", "key": "quest/11", "level": 25, "area": "Ellinia", "npc": "Grendel",
         "npc_key": "npc/8", "turn_in": None, "job": "Magician only", "exp": 5000},
        {"quest": "Big One", "key": "quest/12", "level": 30, "area": "Henesys", "npc": "Jane Doe",
         "npc_key": "npc/7", "turn_in": None, "job": None, "exp": 3000},
        {"quest": "Too High", "key": "quest/13", "level": 40, "area": "Henesys", "npc": "Pia",
         "npc_key": "npc/6", "turn_in": "Jane Doe", "job": None, "exp": 9000},
        {"quest": "Done Already", "key": "quest/14", "level": 10, "area": "Henesys", "npc": "Pia",
         "npc_key": "npc/6", "turn_in": None, "job": None, "exp": 8000}],
    "npcs": [{"npc": "Jane Doe", "key": "npc/7", "role": "Quest Giver", "map": "Niora Hospital", "map_key": "map/9",
              "street": "Victoria Road"}],
    "quest_reqs": [], "recipes": [], "skills": [], "scrolls": [], "consumables": [],
}


def tiny(name):
    return TINY[name]


def plan(question, kb=None, character=THIEF, rows=tiny):
    return planner.plan_for(question, kb or FakeKB(), character, rows)


def test_gloves_i_can_wear_are_the_players_class_up_to_the_level():
    p = plan("איזה כפפות אני יכול ללבוש ברמה 30?")
    assert p.intent == "equips"
    assert [r["item"] for r in p.blocks[0].rows] == ["Dark Briggon", "Work Gloves"]     # no Warrior's, none above 30
    assert 'complete="yes"' in p.render()


def test_weapons_sort_by_attack_and_a_class_gets_its_own_weapons():
    p = plan("מה הנשק הכי טוב לגנב ברמה 25?", character=None)
    assert [r["item"] for r in p.blocks[0].rows] == ["Meba", "Garnier"]               # no Fish Spear for a Thief
    p = plan("איזה קשת הכי טובה לרמה 30?", character=None)
    assert [r["item"] for r in p.blocks[0].rows] == ["War Bow"]


def test_quest_rewards_one_row_per_quest_equips_only():
    p = plan("איזה משימות נותנות לי גלימות כשאני מסיים אותן?")
    assert p.intent == "quest_rewards"
    rows = p.blocks[0].rows
    assert [r["quest"] for r in rows] == ["Stranger's Identity", "Flying Medicine"]     # no quest item "Red Cape"
    assert rows[1]["items"] == "Green Icarus Cape (random 33.3%); Blue Icarus Cape (random 33.3%)"
    assert rows[1]["item_keys"] == "item/22,item/23"
    p = plan("which quests give cape scrolls?")
    assert [r["quest"] for r in p.blocks[0].rows] == ["Pia's Gift"]
    p = plan("which quests reward scrolls?")
    assert [r["quest"] for r in p.blocks[0].rows] == ["Hat Quest", "Pia's Gift"]
    assert p.blocks[0].rows[0]["items"] == "Hat Accuracy Scroll: Lesser x2 (sure)"


def test_arrow_sellers_grouped_per_item_for_the_bow():
    p = plan("איזה NPC מוכר חצים לקשת?")
    assert p.intent == "shops"
    rows = p.blocks[0].rows
    assert [r["item"] for r in rows] == ["Arrows for Bows", "Bronze Arrows for Bows"]   # no crossbow arrows
    assert rows[0]["sellers"] == "Arturo (Perion); Luna (Henesys)"
    assert "citizen rank Helpful Stranger" in rows[1]["sellers"]
    assert [r["item"] for r in plan("who sells arrows for crossbows").blocks[0].rows] == ["Arrows for Crossbows"]
    assert len(plan("who sells arrows").blocks[0].rows) == 3


def test_monsters_in_a_map_family_by_its_words():
    p = plan("איזה מפלצות יש ב-Ant Tunnel?")
    assert p.intent == "monsters_in_map"
    rows = p.blocks[0].rows
    assert [r["monster"] for r in rows] == ["Horny Mushroom", "Zombie Mushroom", "Evil Eye"]
    assert rows[1]["maps"] == "Ant Tunnel Park x23; Ant Tunnel I x11"
    # a street: all its maps, though find_mentions found the town in it
    kb = FakeKB({"Kerning City": ["map/99"]})
    p = plan("monsters in Kerning City Subway", kb)
    assert [r["monster"] for r in p.blocks[0].rows] == ["Bubbling"]


def test_training_maps_follow_the_question_level_and_skip_mixed_maps():
    p = plan("where should I train at 24")
    assert p.intent == "training_maps" and p.level == 24
    assert [r["map"] for r in p.blocks[0].rows] == ["Ant Tunnel Park", "Deep Ant Tunnel I", "Ant Tunnel I"]
    p = plan("איפה הכי כדאי לעשות גריינד ברמה 31?")
    assert "Mixed Swamp" not in [r["map"] for r in p.blocks[0].rows]      # mostly Lv 52 Crocos
    assert plan("where should I train?", character=None) is None          # no level at all


def test_monsters_by_exp_in_a_range():
    p = plan("איזה מפלצות נותנות הכי הרבה EXP בין רמה 20 ל-30?")
    assert p.intent == "monsters_by_level" and p.level == 25
    assert [r["monster"] for r in p.blocks[0].rows] == ["Evil Eye", "Zombie Mushroom", "Horny Mushroom"]
    assert [r["rank"] for r in p.blocks[0].rows] == [1, 2, 3]


def test_top_n_regular_monsters_leave_the_bosses_out():
    rows = dict(TINY, monsters=TINY["monsters"] + [{"monster": "Mushmom", "key": "monster/9", "level": 25, "hp": 9,
                                                    "exp": 376, "boss": "yes", "maps": "x"}])
    for q in ("which regular monsters (not bosses) between level 20 and 30 give the most EXP? top 2",
              "אילו 2 מפלצות רגילות (לא בוסים) בין רמה 20 ל-30 נותנות הכי הרבה EXP?"):
        p = plan(q, rows=rows.__getitem__)
        assert p.intent == "monsters_by_level" and p.top == 2
        assert [r["monster"] for r in p.blocks[0].rows][:2] == ["Evil Eye", "Zombie Mushroom"]
        assert "first 2 rows" in p.render()
    p = plan("which monsters give the most exp between level 20 and 30", rows=rows.__getitem__)
    assert p.blocks[0].rows[0]["monster"] == "Mushmom" and p.top is None       # bosses stay, marked


def test_a_common_word_is_no_map_name():
    rows = dict(TINY, maps=TINY["maps"] + [{"map": "Regular Sauna", "key": "map/7", "street": "Dungeon"}])
    assert planner.named_maps("which regular monsters are there", [], rows.__getitem__) == []
    assert planner.named_maps("monsters in Regular Sauna", [], rows.__getitem__) == ["map/7"]


def test_quests_now_by_exp_fit_the_job_level_and_skip_done():
    me = Character(id="t", name="Amit", base_class="Thief", job="Assassin", level=31, quests_done=["quest/14"])
    p = plan("איזה קווסטים כדאי לי לעשות עכשיו שנותנים הכי הרבה EXP?", character=me)
    assert p.intent == "quests"
    assert [r["quest"] for r in p.blocks[0].rows] == ["Big One", "Thief Thing"]


def test_an_npcs_quests_and_place():
    kb = FakeKB({"Jane Doe": ["npc/7"]})
    p = plan("where is Jane Doe and her quests", kb)
    assert p.intent == "npc_quests"
    assert [b.table for b in p.blocks] == ["npcs", "quests", "quests"]
    # the quests it gives apart from the ones only turned in to it
    assert [r["quest"] for r in p.blocks[1].rows] == ["Big One"]
    assert [r["quest"] for r in p.blocks[2].rows] == ["Too High"]
    assert "GIVES" in p.blocks[1].what and "TURNED IN" in p.blocks[2].what


def test_a_long_list_is_cut_with_the_count_and_the_grep():
    rows = dict(TINY, equips=[eq(f"Hat {i}", f"item/{i}", "Hat", "Any", i % 30) for i in range(100)])
    text = planner.plan_for("which hats", FakeKB(), None, rows.__getitem__).render()
    assert 'rows="40 of 100" complete="no"' in text
    assert "(60 more rows, sorted after these: grep" in text
    assert text.count("\n") == 40 + 3


@pytest.mark.parametrize("question", [
    "היי מה קורה", "תודה!", "my gloves broke lol", "מה הכפפות שאני לובש בתמונה?", "what's on my screen? gloves?",
    "which monsters drop gloves?", "מאיזה מפלצות נופלות כפפות?", "is Work Gloves good for me?",
    "how many quests are there in the game?", "who is Jane Doe", "Which job should I pick, warrior or thief?",
])
def test_leaves_other_questions_alone(question):
    kb = FakeKB({"Work Gloves": ["item/1"], "Jane Doe": ["npc/7"]}, {"item/1": "Work Gloves"})
    assert plan(question, kb) is None


def test_a_planner_bug_never_stops_a_question(monkeypatch):
    monkeypatch.setattr(planner, "plan_for", lambda *a, **k: 1 / 0)
    assert planner.context("which hats", FakeKB(), THIEF) == ("", None)


# ---------------------------------------------------------------- build_prompt

def test_build_prompt_adds_the_rows_and_reads_only_built_tables(tmp_path):
    from test_tables import make_kb
    kb = make_kb(tmp_path)
    bowman = Character(id="b", name="B", base_class="Bowman", job="Bowman", level=20)
    # tables not built yet: nothing (a test's prompt on data/kb must never build into it)
    assert "<table_rows table=" not in brain.build_prompt("which bows can I use?", bowman, None, kb, False)
    assert not (tmp_path / "equips.tsv").exists()
    assert tables.ensure(kb)
    prompt = brain.build_prompt("which bows can I use?", bowman, None, kb, False)
    assert '<table_rows table="equips.tsv"' in prompt and "War Bow" in prompt and "item/663" in prompt
    assert "Arturo's Errand" in brain.build_prompt("which quests give bows?", bowman, None, kb, False)
    assert '<table_rows complete="yes"> block' in prompt          # the reply rule


def test_level_digest_follows_the_questions_level(monkeypatch, tmp_path):
    from test_tables import make_kb
    kb = make_kb(tmp_path)
    seen = []
    monkeypatch.setattr(kb, "level_digest", lambda lv, *a, **k: seen.append(lv) or "")
    monkeypatch.setattr(planner, "context", lambda *a, **k: ("", planner.Plan("training_maps", [], 45)))
    brain.build_prompt("where to grind at level 45", THIEF, None, kb, False)
    assert seen == [45]


# ---------------------------------------------------------------- the real knowledge base

@pytest.fixture(scope="module")
def real():
    kb = KnowledgeBase(REAL_KB)
    made = {n: tables.parse(n, tables.tsv(n, r)) for n, r in tables.generate(kb).items()}    # nothing written
    return kb, made.__getitem__


def ask(real, question, character=THIEF):
    kb, rows = real
    return planner.plan_for(question, kb, character, rows, reverse=brain.is_reverse(question, kb))


@needs_kb
@pytest.mark.parametrize("question, intent", [
    ("איזה משימות נותנות לי גלימות כשאני מסיים אותן?", "quest_rewards"),
    ("which quests reward scrolls?", "quest_rewards"),
    ("which quests give hats", "quest_rewards"),
    ("quests that give Old Raggedy Cape", "quest_rewards"),
    ("איזה כפפות אני יכול ללבוש ברמה 30?", "equips"),
    ("איזה כובעים יש ל-Thief בין רמה 20 ל-35?", "equips"),
    ("איזה קשת הכי טובה לרמה 30?", "equips"),
    ("show me all claws for thief level 25", "equips"),
    ("איזה נעליים הכי טובות לקוסם ברמה 25?", "equips"),
    ("gloves for warriors between 20 and 30", "equips"),
    ("איזה אוברולים יש לגנב?", "equips"),
    ("איזה NPC מוכר חצים לקשת?", "shops"),
    ("who sells throwing stars", "shops"),
    ("איפה קונים שיקויים?", "shops"),
    ("what does Arturo sell?", "shops"),
    ("איזה מפלצות יש ב-Ant Tunnel?", "monsters_in_map"),
    ("what monsters are in Ant Tunnel Park", "monsters_in_map"),
    ("מה יש ב-Sleepy Dungeon?", "monsters_in_map"),
    ("monsters in Kerning City Subway", "monsters_in_map"),
    ("איפה הכי כדאי לעשות גריינד ברמה 31?", "training_maps"),
    ("best place to grind at level 45", "training_maps"),
    ("where should I train at 20", "training_maps"),
    ("איזה מפלצות נותנות הכי הרבה EXP בין רמה 30 ל-40?", "monsters_by_level"),
    ("which monsters give the most exp for my level", "monsters_by_level"),
    ("מפלצות בין רמה 10 ל-20", "monsters_by_level"),
    ("איזה קווסטים כדאי לי לעשות עכשיו שנותנים הכי הרבה EXP?", "quests"),
    ("quests in Henesys for level 20", "quests"),
    ("איזה קווסטים יש בפריון?", "quests"),
    ("what uses Bronze Ore", "ingredient_of"),
    ("what can I make with Bronze Ore?", "ingredient_of"),
    ("how do I craft Bronze Ingot", "recipe"),
    ("מה צריך כדי להכין Bronze Ingot?", "recipe"),
    ("Hunter skills", "skills"),
    ("הסקילים של אסאסין", "skills"),
    ("quests that need Zombie Mushroom kills", "quest_needs"),
    ("איזה קווסטים דורשים להרוג Zombie Mushroom?", "quest_needs"),
    ("where is Jane Doe and her quests", "npc_quests"),
    ("cheapest HP potion per meso", "consumables"),
    ("what are good stars for a level 30 assassin", "consumables"),
    ("which arrows for crossbow", "consumables"),
    ("cape scrolls", "scrolls"),
    ("סקרולים לכפפות עם התקפה", "scrolls"),
    # nothing: chit-chat, one entity, drops, screenshots, the scope, judgement calls
    ("היי מה קורה", None), ("תודה!", None), ("כמה HP יש ל-Blue Snail?", None), ("מה Mano מפיל?", None),
    ("who drops Snail Shell", None), ("which monsters drop gloves?", None), ("מאיזה מפלצות נופלות כפפות?", None),
    ("מה רואים במסך?", None), ("מה הכפפות שאני לובש בתמונה?", None), ("איך מגיעים לסליפיווד?", None),
    ("מי ראש הממשלה?", None), ("write me a python script", None), ("tell me about Work Gloves", None),
    ("is Work Gloves good for me?", None), ("my gloves broke lol", None), ("אני ברמה 30 עכשיו", None),
    ("what's my next job advancement", None), ("how many quests are there in the game?", None),
    ("Where is Mano?", "where_monster"), ("איפה יש סטירג'", "where_monster"), ("where is Jane Doe", "where_npc"), ("how do I become a magician", "job_advance"), ("איך נהיים קשת?", "job_advance"), ("ספר לי על Kerning City", None), ("Is Hunter better than Crossbowman?", None),
    ("לאיזה ג'ובים אפשר להתקדם מקשת", None), ("כדאי לי לגרינד בלו סנייל?", None), ("who is Jane Doe", None),
    ("which regular monsters (not bosses) between level 30 and 40 give the most EXP? top 3", "monsters_by_level"),
    ("איך מכינים Steel Plate?", None),          # no such item in the KB: nothing to list
    # the 2nd job with no job named: the player's class (the AI said "level 20" and "not out yet", live)
    ("באיזה לבל עושים ג'וב שני", "job_advance"), ("at what level is the 2nd job?", "job_advance"),
    ("מה צריך בשביל ה-job advancement השני?", "job_advance"),
])
def test_real_intents(real, question, intent):
    p = ask(real, question)
    assert (p.intent if p else None) == intent


# The real-KB checks below hold the planner's answer to what tonight's tables say (the exact rows of a frozen KB
# are the tests above): a balance patch or a new map changes the rows, not whether the answer matches them.

@needs_kb
def test_real_second_job_names_its_level_and_choices(real):
    p = ask(real, "באיזה לבל עושים ג'וב שני")
    text = p.render()
    assert "the 2nd job, at level 30, one of Assassin, Bandit" in text      # jobs.JOBS, the official tree
    teacher = p.blocks[0].rows[0]                                          # the KB's own instructor, by role
    assert "Instructor" in teacher["role"] and teacher["npc"] in text


@needs_kb
def test_real_capes_from_quests(real):
    from maplehelper import availability
    kb, rows = real
    a = availability.of(kb)
    p = ask(real, "איזה משימות נותנות לי גלימות כשאני מסיים אותן?")
    text = p.render()
    want = {r["quest"] for r in rows("rewards") if r["item_type"] == "Equip / Cape" and a.quest_open(r["quest_key"])}
    assert want and {r["quest"] for r in p.blocks[0].rows} == want and 'complete="yes"' in text
    # nothing from what isn't out (El Nath while the guide keeps it closed)
    assert all(a.item_open(k) for r in p.blocks[0].rows for k in r["item_keys"].split(","))
    if not a.place_open("El Nath"):
        assert "El Nath" not in text


@needs_kb
def test_real_gloves_a_level_30_thief_can_wear(real):
    p = ask(real, "איזה כפפות אני יכול ללבוש ברמה 30?")
    rows = p.blocks[0].rows
    assert rows and all(r["slot"] == "Gloves" and r["req_lv"] <= 30 for r in rows)
    assert all("Thief" in r["job"].split("/") or r["job"] == "Any" for r in rows)
    kb, all_rows = real
    every = [r for r in all_rows("equips") if r["slot"] == "Gloves" and (r["req_lv"] or 0) <= 30
             and ("Thief" in r["job"].split("/") or r["job"] == "Any")]
    assert {r["key"] for r in rows} == {r["key"] for r in every}          # all of them


@needs_kb
def test_real_ant_tunnel_monsters(real):
    kb, rows = real
    names = {r["monster"] for r in ask(real, "איזה מפלצות יש ב-Ant Tunnel?").blocks[0].rows}
    # every monster the spawn table puts on an Ant Tunnel map (Horny / Zombie Mushroom, Evil Eye today)
    assert names and names == {r["monster"] for r in rows("spawns") if "Ant Tunnel" in r["map"]}


@needs_kb
def test_real_top_regular_monsters_and_manjis_quests(real):
    kb, rows = real
    p = ask(real, "which regular monsters (not bosses) between level 30 and 40 give the most EXP? top 3")
    got = p.blocks[0].rows
    assert got and all(30 <= r["level"] <= 40 and not r["boss"] for r in got)
    # the top 3 by EXP of tonight's regular level 30-40 monsters (Cold Eye, Glowshroom, Lorang today)
    regular = sorted((r["exp"] for r in rows("monsters") if 30 <= (r["level"] or 0) <= 40 and not r["boss"]),
                     reverse=True)
    assert [r["exp"] for r in got] == sorted((r["exp"] for r in got), reverse=True)
    assert [r["exp"] for r in got][:3] == regular[:3]
    p = ask(real, "איפה מנג'י ואיזה קווסטים הוא נותן?")
    gives = {r["quest"] for r in rows("quests") if r["npc"] == "Manji"}
    assert gives and {r["quest"] for r in p.blocks[1].rows} == gives


@needs_kb
def test_real_where_a_monster_lives_every_map(real):
    kb, rows = real
    p = ask(real, "איפה יש סטירג'")
    maps = [r["map"] for r in p.blocks[0].rows]
    every = [r["map"] for r in rows("spawns") if r["monster"] == "Stirge"]
    assert maps and sorted(maps) == sorted(every) and 'complete="yes"' in p.render()


@needs_kb
def test_real_becoming_a_magician(real):
    text = ask(real, "how do I become a magician").render()
    assert "Grendel the Really Old" in text and "Ellinia" in text and "level 10 (official)" in text
    assert "can't change class" in text                         # the player is a Thief


@needs_kb
def test_real_leads_say_the_answer(real):
    kb, rows = real
    made = next(r for r in rows("recipes") if r["product"] == "Steel Guards")
    lead = f"Steel Guards IS crafted: {made['discipline']} Lv {made['prof_lv']}"
    assert lead in ask(real, "how do I craft Steel Guards?").render()
    hunter = Character(id="h", name="H", base_class="Bowman", job="Hunter", level=35)
    bows = [r for r in rows("equips") if r["slot"] == "Bow" and (r["req_lv"] or 0) <= 35
            and ("Bowman" in r["job"].split("/") or r["job"] == "Any")]
    best = max(bows, key=lambda r: r["watk"] or 0)                 # Red Viper today
    text = ask(real, "what's the best bow I can equip?", hunter).render()
    assert f"Recommend row 1, {best['item']} (req_lv {best['req_lv']}" in text


@needs_kb
def test_real_guides_for_a_place_and_the_release_date():
    kb = KnowledgeBase(REAL_KB)
    named = kb.find_mentions("what is Forgotten Hollow", 4)
    assert brain.guides_for("what is Forgotten Hollow", kb, named) == ["guide/forgotten-hollow-the-new-endgame-area"]
    release = "when does MapleStory Classic World release"
    assert brain.guides_for(release, kb, []) == ["guide/maplestory-classic-worlds-release-date"]
    assert brain.guides_for("how do I get to Kerning City", kb, kb.find_mentions("Kerning City", 4)) == []
    assert brain.guides_for("what drops from Mano", kb, []) == []


@needs_kb
def test_real_arrow_sellers(real):
    kb, all_rows = real
    rows = ask(real, "איזה NPC מוכר חצים לקשת?").blocks[0].rows
    bows = next(r for r in rows if r["item"] == "Arrows for Bows")
    # every NPC the shop table has selling them (Arturo in Perion, Luna in Henesys, ... today)
    sellers = {r["npc"] for r in all_rows("shops") if r["item"] == "Arrows for Bows"}
    assert sellers and all(n in bows["sellers"] for n in sellers)
    assert not [r for r in rows if "Crossbow" in r["item"]]


@needs_kb
def test_real_timing(real):
    import time
    qs = ["איזה משימות נותנות לי גלימות כשאני מסיים אותן?", "איפה הכי כדאי לעשות גריינד ברמה 31?",
          "איזה מפלצות יש ב-Ant Tunnel?", "איזה כובעים יש ל-Thief בין רמה 20 ל-35?", "היי מה קורה"]
    for q in qs:
        ask(real, q)              # warm: find_mentions' name tables
    t0 = time.perf_counter()
    for q in qs * 4:
        ask(real, q)
    assert (time.perf_counter() - t0) / (len(qs) * 4) < 0.05      # ~5 ms measured; generous for a slow CI
