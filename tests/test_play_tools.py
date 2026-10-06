"""Play tools: combat math (checked against NiaMeowDB's own numbers), quests, build tables, grind tracker."""
from pathlib import Path

import pytest

from maplehelper import buildplan, combat, plan, quests

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


def test_accuracy_to_never_miss_matches_the_site():
    # Zombie Mushroom (Lv 24, Avoid 14): the monster page lists 47 / 56 / 65 ACC at levels 24 / 19 / 14
    assert [combat.acc_needed(lv, 24, 14) for lv in (24, 19, 14)] == [47, 56, 65]
    assert combat.hit_chance(47, 24, 24, 14) == 1.0 and combat.hit_chance(30, 24, 24, 14) < 0.5


def test_base_accuracy_and_damage():
    assert combat.base_acc("Warrior", 30, dex=30, luk=4) == 49
    assert combat.acc_per_point("Warrior") == pytest.approx(0.48)
    m = combat.Monster("monster/1", "Test", level=30, hp=1000, exp=50, pdef=0)
    assert combat.hits_to_kill(100, 300, m, 30) == (10, 5.0)
    assert combat.level_scale(30, 35) < 1 and combat.level_scale(30, 25) == 1


@needs_kb
def test_training_spots_are_reachable_and_ranked():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    rows = combat.spots(kb, 30, acc=73, dmg=(140, 300), n=6)
    assert rows and all(combat.grind_map(kb, s.map) for s in rows)
    assert not any("Orbis" in s.map or "Warrior's" in s.map for s in rows)
    assert rows == sorted(rows, key=lambda s: -s.score)


@needs_kb
def test_exp_rate_across_a_level_up():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    same = plan.exp_rate(kb, (0, 30, 40.0), (1800, 30, 55.0))
    assert same["pct_hour"] == 30.0 and same["minutes"] == 30.0
    up = plan.exp_rate(kb, (0, 30, 90.0), (1800, 31, 5.0))
    assert up and up["per_hour"] > 0
    assert plan.exp_rate(kb, (0, 30, 50.0), (60, 30, 50.0)) is None


@needs_kb
def test_quests_for_a_level():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    r = quests.for_level(kb, 22, "Warrior", "Warrior", [])
    assert r["now"] and all(q.level <= 22 for q in r["now"]) and all(q.area != "Citizenship" for q in r["now"])
    assert r["now"] == sorted(r["now"], key=lambda q: (-q.exp, q.level))
    first = r["now"][0].key
    assert first not in [q.key for q in quests.for_level(kb, 22, "Warrior", "Warrior", [first])["now"]]
    mai = next(quests.quest(kb, k) for k, e in kb.entities.items() if e["name"] == "Mai's Training")
    assert mai.level == 3 and "Beginner" in mai.job and any("Blue Snail x 10" in n for n in mai.needs)


@needs_kb
def test_build_tables_follow_the_level():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    key, tables = buildplan.tables(kb, "Warrior", "Warrior", 22, "en")
    assert key == "guide/warrior-class-guide"
    kinds = [t.kind for t in tables]
    assert "ap" in kinds and "sp" in kinds
    ap = next(t for t in tables if t.kind == "ap")
    assert "10-30" in ap.heading and ap.current is not None
    assert buildplan.current_row([["Level"], ["10"], ["11-12"], ["20"]], 15) == 2


def test_stats_from_a_screenshot_read(tmp_path, monkeypatch):
    from maplehelper import store
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    p = store.Profiles()
    p.add("Kiwi", "Warrior", "Fighter", 34)
    changed = p.apply_update({"stats": {"acc": 78, "dmg_min": 340, "dmg_max": 160, "bogus": 5, "hp": -3}})
    assert p.active.stats == {"acc": 78, "dmg_min": 160, "dmg_max": 340}      # min/max swapped back, junk dropped
    assert changed and changed[0][0] == "stats"
    assert p.apply_update({"stats": {"acc": 78}}) == []                         # nothing new


@needs_kb
def test_tools_window_builds_every_page(tmp_path, monkeypatch):
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    p = store.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 34)
    c.stats = {"acc": 80, "dmg_min": 150, "dmg_max": 320}
    s = store.Settings()
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, s, "he", "", {})
    for i in range(len(PAGES)):
        d.show_page(i)
        app.processEvents()
    d.calc_input.setText("Zombie Mushroom")
    d._fill_calc()
    d._quest_done(quests.for_level(d.kb, 34, "Thief", "Assassin", [])["now"][0].key)
    assert len(c.quests_done) == 1
    d.close()


@needs_kb
def test_crafting_recipes_by_profession_level():
    from maplehelper import crafting
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    smithing = crafting.levels(kb, "smithing")
    assert sum(len(lv.recipes) for lv in smithing) == 68            # the page says "68 recipes"
    now, nxt = crafting.for_level(kb, "smithing", 2)
    juno = next(r for r in now.recipes if r.name == "Juno")
    assert juno.exp == 40 and juno.catalyst == 1200 and juno.net == -226
    assert (2, "Iron Ingot") in juno.ingredients and (6, "Screw") in juno.ingredients
    assert now.recipes == sorted(now.recipes, key=lambda r: (-r.exp_per_meso, -r.exp))
    assert nxt.level == 3 and nxt.needs_exp == 199
    assert all(crafting.levels(kb, p) for p in crafting.PROFESSIONS)
    # the EXP to the next level is the current level's (pages/formula/leveling.md "1 | 50 | 0 | 10"), and
    # Smithing 8 still has a next level though the efficiency page has no "Lv. 9" block (audit GAM-1)
    assert crafting.next_level(kb, "smithing", 1) == (2, 50, 10)
    assert crafting.next_level(kb, "smithing", 8) == (9, 1187, 45)
    assert crafting.next_level(kb, "smithing", 10) is None


@needs_kb
def test_citizenship_town_and_quests():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    assert buildplan.citizenship_advice(kb, "Warrior", "Fighter", "en")[0] == "Henesys"   # from the Warrior guide
    assert buildplan.citizenship_advice(kb, "Thief", "Assassin", "en")[0] == "Kerning City"
    rows = quests.citizenship(kb, "Henesys", 30)
    assert rows and all(quests.town_of(kb, q) == "Henesys" and q.level <= 30 for q in rows)
    assert quests.citizenship(kb, "Henesys", 11) == [] or all(q.level <= 11 for q in quests.citizenship(kb, "Henesys", 11))


@needs_kb
def test_npc_prices_from_the_item_page():
    from maplehelper import market
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    red = market.npc_prices(kb, kb._item_by_name["red potion"])
    assert red.sell_back == 5 and red.shops and red.shops[0][2] == 50
    assert red.shops == sorted(red.shops, key=lambda s: s[2])


def test_free_market_summary_keeps_the_exact_item():
    from maplehelper import market
    rows = [{"itemName": "Work Gloves", "priceEach": 1000, "createdAt": "2026-10-21T10:00:00Z"},
            {"itemName": "Work Gloves", "priceEach": 3000},
            {"itemName": "Work Gloves (Blue)", "priceEach": 5}]
    m = market.summarize(rows, "Work Gloves")
    assert (m.count, m.median, m.low, m.high) == (2, 2000, 1000, 3000) and m.latest
    assert market.summarize([], "Work Gloves").count == 0


def test_damage_range_typed_by_hand():
    assert combat.damage_range(200, 10) == (10, 200)          # min above max: swapped
    assert combat.damage_range(50, 0) == (50, 50)             # no max: the min
    assert combat.damage_range(0, 100) is None and combat.damage_range(None, None) is None


def test_quest_item_names_with_a_lowercase_x():
    found = quests._ITEM.findall("Defeat Dark Axe Stump x 100 Defeat Drake x 50 Moon Rock x 1")
    assert found == [("Defeat Dark Axe Stump", "100"), ("Defeat Drake", "50"), ("Moon Rock", "1")]
    assert quests._ITEM.findall("Dexterity Potion x 5 Blue Potion x 30") == [("Dexterity Potion", "5"),
                                                                             ("Blue Potion", "30")]
    assert quests._ITEM.findall("Elixir x 15 Bottomwear HP Scroll: Greater x 1")[1] == ("Bottomwear HP Scroll: Greater", "1")


@needs_kb
def test_class_specific_rewards_follow_the_class():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    q = next(quests.quest(kb, k) for k, e in kb.entities.items() if e["name"] == "Welcome to the Hollow")
    assert q.rewards_pick("Magician") == ["Wand Magic Attack Scroll: Greater x 1", "Staff Magic Attack Scroll: Greater x 1"]
    assert "One-Handed Axe Attack Scroll: Greater x 1" in q.rewards_pick("Warrior")
    assert q.rewards_pick("Beginner") == [] and not any("Scroll" in r for r in q.rewards)


@needs_kb
def test_training_spots_never_come_back_empty_for_stats():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    mage = combat.spots(kb, 30, acc=70, dmg=(15, 40), magic=True, n=6)       # the staff swing isn't a spell
    assert mage and all(s.hits is None for s in mage)
    weak = combat.spots(kb, 30, acc=73, dmg=(1, 2), n=6)                     # nothing dies in 12 basic hits
    assert weak and not any(s.fits for s in weak) and weak == sorted(weak, key=lambda s: -s.score)
    for lv in (1, 5, 10, 15):
        assert not any(combat.special_monster(s.monster.name) for s in combat.spots(kb, lv, n=20))


@needs_kb
def test_crafting_lists_every_recipe_up_to_your_level():
    from maplehelper import crafting
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    rows = crafting.up_to(kb, "smithing", 3)
    by_level = {lv.level: len(lv.recipes) for lv in crafting.levels(kb, "smithing")}
    assert len(rows) == sum(n for lv, n in by_level.items() if lv <= 3)
    assert {r.level for r in rows} == {lv for lv in by_level if lv <= 3}
    assert [r.level for r in rows] == sorted((r.level for r in rows), reverse=True)     # newest first


@needs_kb
def test_guides_for_you_and_third_job_build():
    from maplehelper import guides
    from maplehelper.kb import KnowledgeBase
    from maplehelper.store import Character
    kb = KnowledgeBase(REAL_KB)
    hollow = "guide/forgotten-hollow-the-new-endgame-area"
    assert hollow in guides.for_you(kb, Character("1", "A", "Warrior", "Fighter", 39))
    assert hollow not in guides.for_you(kb, Character("1", "A", "Warrior", "Fighter", 38))
    assert plan.class_guide(kb, "Warrior", "Crusader") == "guide/fighter-class-guide"
    assert plan.class_guide(kb, "Magician", "Priest") == "guide/cleric-class-guide"


@needs_kb
def test_tools_enter_quest_undo_and_empty_states(tmp_path, monkeypatch):
    import sys
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    from maplehelper import grind
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    c = p.add("Kiwi", "Magician", "Cleric", 30)
    c.stats = {"acc": 70, "dmg_min": 40, "dmg_max": 15}
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, store.Settings(), "en", "", {}, "prices")
    assert d._stats()[1] == (15, 40)
    QTest.keyClicks(d.price_input, "Red Potion")
    QTest.keyClick(d.price_input, Qt.Key_Return)          # runs this tab's search, doesn't jump to "Where to train"
    assert d.stack.currentIndex() == PAGES.index("prices") and d.price_box.count() == 1
    d.show_page(PAGES.index("calc"))
    d.calc_input.setText("")
    monkeypatch.setattr(d, "_calc_monster", lambda: None)
    d._fill_calc()
    assert "Pick a monster" in d.calc_box.itemAt(0).widget().text()
    monkeypatch.undo()
    d.show_page(PAGES.index("quests"))
    first = quests.for_level(d.kb, 30, "Magician", "Cleric", [])["now"][0].key
    d._quest_done(first)
    assert first in c.quests_done and not d.q_done_toggle.isHidden()
    d.q_done_toggle.setChecked(True)
    # a small header, the one done quest's card, and the spacing under them (tools._add_done)
    from PySide6.QtWidgets import QFrame
    assert [type(d.q_done.itemAt(i).widget()) for i in range(d.q_done.count())].count(QFrame) == 1
    d._quest_undo(first)
    assert first not in c.quests_done and d.q_done_toggle.isHidden()
    d.show_page(PAGES.index("exp"))
    from maplehelper.grind import Reading
    d.grind.start(c.id, Reading(0, 99, 90.0))
    d.grind.add(c.id, Reading(600, 100, 1.0))          # across 99 -> 100: past the KB's EXP table
    d._fill_exp()
    assert "past Lv. 99" in d.grind_cells["exp"][0].toolTip()
    d.close()
    app.processEvents()


@needs_kb
def test_profession_info_names_teacher_town_and_quests():
    from maplehelper import crafting
    from maplehelper.kb import KnowledgeBase
    KB = KnowledgeBase(REAL_KB)
    i = crafting.info(KB, "smithing")
    assert i.teacher == "Silas Irons" and i.teacher_town == "Perion"
    assert i.start_level == 10 and i.master_level == 25
    assert "Perion" in i.station_towns and "El Nath" not in " ".join(i.station_towns)
    assert crafting.info(KB, "leatherworking").start_quest       # no "in Need of an Apprentice": the first one


@needs_kb
def test_quest_search_filters_the_list_for_the_level(tmp_path, monkeypatch):
    """The quests page had no search: finding a quest meant scrolling. The search keeps to the list shown
    (the quests for the character's level), by name, NPC, area, what it asks and what it gives."""
    import sys
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    from maplehelper import quests, store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    kb = KnowledgeBase(REAL_KB)
    p = store.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 32)
    d = ToolsDialog(kb, p, store.Settings(), "en", "", {}, "quests")
    d.q_mode.group.buttons()[1].click()          # the quests skipped from earlier levels: a long list
    level_list = quests.for_level(kb, c.level, c.base_class, c.job, c.quests_done)["missed"]
    assert len(level_list) > 1

    def cards():                                 # quest cards only, not the headings over them
        return sum(1 for i in range(d.q_list.count()) if (w := d.q_list.itemAt(i).widget()) is not None
                   and w.objectName() == "Card")
    d._fill_quests()
    everything = cards()
    target = level_list[1]
    d.q_search.setText(target.name)
    d._fill_quests()
    assert 1 <= cards() < everything
    assert cards() == sum(1 for q in level_list if q.matches(target.name))     # only from the level's list
    d.q_search.setText("zzzz-no-such-quest")
    d._fill_quests()
    assert cards() == 0 and any("No quest like that" in (w.text() if hasattr(w, "text") else "")
                                for i in range(d.q_list.count()) if (w := d.q_list.itemAt(i).widget()))
    d.q_search.clear()
    d._fill_quests()
    assert cards() == everything


def test_quest_matches_name_npc_and_what_it_asks():
    from maplehelper.quests import Quest
    q = Quest(key="quest/1", name="Pio's Collecting Recycled Goods", level=10, npc="Pio", area="Lith Harbor",
              needs=["Green Mushroom Cap x 20"], rewards=["Red Potion x 20"])
    assert q.matches("pio") and q.matches("mushroom cap") and q.matches("red potion") and q.matches("LITH")
    assert not q.matches("mushroom snail")


def test_the_calculators_hp_hint_explains_the_monsters_hp():
    """The "?" beside a monster's HP explained the player's HP / MP and levels (the owner's report)."""
    from maplehelper import glossary
    from maplehelper.ui import terms
    tip = terms.tip_html("Monster HP", "he")
    assert "להרוג את המפלצת" in tip and "<b style" in tip and ">HP<" in tip
    assert "MP" not in glossary.explain("Monster HP", "he")


def test_maple_island_quests_leave_once_a_job_is_taken(kb):
    """Maple Island is behind a one-way boat: its quests show for a Beginner, not for a Lv. 31 Assassin."""
    from maplehelper import quests
    q = quests.Quest(key="quest/x", name="Bringing a Mirror to Heena", level=1, area="Maple Island")
    assert quests.job_fits(q, "Beginner", "Beginner") and not quests.job_fits(q, "Thief", "Assassin")
    assert quests.job_fits(quests.Quest(key="quest/y", name="Y", level=1, area="Victoria Island"), "Thief", "Assassin")


def test_the_later_tab_says_which_areas_are_not_open_yet(kb):
    """Quests in an area the KB doesn't confirm are hidden: the "later" tab names those areas, and the note goes
    once every area is open (the owner)."""
    from maplehelper import quests
    areas = quests.closed_areas(kb)
    assert all("event" not in a.lower() for a in areas)
