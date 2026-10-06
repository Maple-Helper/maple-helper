"""Farming (farm.py, the Farm tab): who drops an item, what pays at a level, and the loot a session counts."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import combat, farm, grind, plan, sources

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


@pytest.fixture(scope="module")
def kb():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


# ---------------------------------------------------------------- from the KB

@needs_kb
def test_farmable_items_are_the_ones_monsters_in_the_game_drop(kb):
    keys = farm.farmable(kb)
    assert keys and all(k in kb.droppers for k in keys)
    names = [kb.get(k)["name"] for k in keys]
    assert len(names) == len(set(names))                   # each name once
    assert names == sorted(names, key=str.lower)


@needs_kb
def test_droppers_put_community_sightings_first_and_say_how_they_fit(kb):
    key = farm.item_key(kb, "Bronze Ore")
    ds = farm.droppers(kb, key, 10)
    assert {d.key for d in ds} == set(kb.droppers[key])
    srcs = [d.source for d in ds]
    assert srcs == sorted(srcs, key=lambda s: s != sources.COMMUNITY)       # players' sightings first
    for d in ds:
        assert d.source == (kb.drop_source(d.key, key) or sources.MSEA)
        assert d.fit == farm.fit(10, d.level)
        if d.source == sources.COMMUNITY:
            assert d.vote == kb.community_vote(d.key, key)


@needs_kb
def test_a_monster_only_in_a_party_quest_says_where_it_is(kb):
    """King Slime lives only on KPQ's last stage: it was listed with no map as a farm (audit GAM-11)."""
    for item in ("item/429", "item/1368"):                       # Coupon, Squishy Shoes (the players' list)
        king = next(d for d in farm.droppers(kb, item, 25) if d.key == "monster/800003")
        assert king.closed and king.boss and "Accompaniment" in king.map


def test_fit_by_level():
    assert farm.fit(30, 30) == "easy" and farm.fit(30, 12) == "easy"
    assert farm.fit(30, 30 + combat.SPOT_ABOVE) == "range"
    assert farm.fit(30, 31 + combat.SPOT_ABOVE) == "hard"
    assert farm.fit(None, 50) == "range"                   # no level known: nothing is judged


@needs_kb
def test_targets_rank_by_the_best_npc_price_in_the_level_band(kb):
    level = 34
    rows = farm.targets(kb, level, n=8)
    assert rows
    assert [r.best for r in rows] == sorted((r.best for r in rows), reverse=True)
    for r in rows:
        m = combat.monster(kb, r.key)
        assert level - farm.FARM_BELOW <= r.level <= level + combat.SPOT_ABOVE
        assert m.maps and not m.boss and not combat.special_monster(r.name)
        assert r.drops and all(d.value and d.value.price == farm.value(kb, d.key).price for d in r.drops)
        assert all(d.key in kb.monster_drops(r.key) for d in r.drops)       # its own drop lists, nothing else
        prices = [d.value.price for d in r.drops]
        assert prices == sorted(prices, reverse=True)
    assert len({r.name for r in rows}) == len(rows)


@needs_kb
def test_value_is_the_item_pages_npc_sell_price(kb):
    from maplehelper import market
    key = farm.item_key(kb, "Bronze Ore")
    v = farm.value(kb, key)
    assert v and v.price == market.npc_prices(kb, key).sell_back
    assert v.source == sources.source_of(kb, key)
    assert farm.value(kb, None) is None and farm.value(kb, "item/does-not-exist") is None


# ---------------------------------------------------------------- the loot a session counts

PRICES = {"Horny Mushroom Cap": farm.Value(50, "COT2"), "Leather": farm.Value(10, "COT2")}


@pytest.fixture
def priced(monkeypatch):
    monkeypatch.setattr(plan, "exp_table", lambda kb: {20: 1000, 21: 1200})
    monkeypatch.setattr(plan, "monster_exp", lambda kb, name: {"Horny Mushroom": 10}.get(name))
    monkeypatch.setattr(farm, "item_key", lambda kb, name: name)
    monkeypatch.setattr(farm, "value", lambda kb, key: PRICES.get(key))
    return SimpleNamespace(monster_keys=lambda name: [])


def read(minutes, pct=None, etc=None, equip=None, t0=1_000_000.0):
    return grind.Reading(t0 + minutes * 60, 20 if pct is not None else None, pct, "Ant Tunnel I", "", None, None,
                         etc is not None or equip is not None, etc, equip)


def session(*reads, monster="Horny Mushroom"):
    s = grind.Session(monster=monster)
    for r in reads:
        s.add(r)
    return s


def test_reading_takes_the_etc_and_equip_tabs():
    r = grind.reading(5.0, {}, {"etc": {"Leather": "12", " Leather ": 3, "": 4, "Bad": "lots"},
                                "equip": {"Bronze Sword": 1}})
    assert r.etc == {"Leather": 15} and r.equip == {"Bronze Sword": 1} and r.inventory
    closed = grind.reading(5.0, {}, {"inventory_open": False, "etc": []})
    assert closed.etc is None and closed.equip is None and not closed.inventory


def test_loot_gained_between_the_first_and_last_tab_reads(priced):
    s = session(read(0, 10.0, etc={"Leather": 5, "Snail Shell": 30}),
                read(30, 50.0, etc={"Leather": 9, "Horny Mushroom Cap": 4, "Snail Shell": 10}))
    sm = grind.summarize(priced, s, now=1_000_000 + 30 * 60)
    assert sm.loot_read
    # 4 new Leather and 4 Caps; the shells sold off aren't loot and aren't held against it
    assert sm.loot == [("Horny Mushroom Cap", 4, 50, "COT2"), ("Leather", 4, 10, "COT2")]
    assert sm.loot_value == 240 and sm.loot_h == 480
    assert sm.kills == 40                     # 40% of Lv. 20's 1,000 EXP / 10 EXP a kill


def test_loot_needs_two_reads_of_a_tab(priced):
    sm = grind.summarize(priced, session(read(0, 10.0, etc={"Leather": 5}), read(30, 50.0)))
    assert not sm.loot_read and sm.loot == [] and sm.loot_value is None
    # an Etc read then an Equip read measure nothing either: each tab on its own
    sm = grind.summarize(priced, session(read(0, 10.0, etc={"Leather": 5}), read(30, 50.0, equip={"Sword": 1})))
    assert not sm.loot_read


def test_unpriced_loot_is_listed_without_a_value(priced):
    sm = grind.summarize(priced, session(read(0, 10.0, equip={}), read(30, 50.0, equip={"Old Sword": 1})))
    assert sm.loot == [("Old Sword", 1, None, "")] and sm.loot_value == 0


def test_a_loot_only_session_is_saved_with_its_loot(priced, tmp_path, monkeypatch):
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    st = grind.Store()
    st.start("c1", read(0, etc={"Leather": 1}), "Horny Mushroom", picked=True)
    st.add("c1", read(10, etc={"Leather": 6}))
    rec = st.end("c1", priced, now=1_000_000 + 10 * 60)
    assert rec and rec["loot"] == [["Leather", 5]] and rec["loot_value"] == 50
    assert grind.Store().recent("c1")[0]["loot"] == [["Leather", 5]]


def test_records_count_every_session_on_the_monster():
    rows = [{"monster": "Horny Mushroom", "kills": 100, "loot": [["Cap", 4], ["Leather", 1]]},
            {"monster": "Horny Mushroom", "kills": 100, "loot": []},          # read, nothing dropped: kills count
            {"monster": "Horny Mushroom", "kills": None, "loot": [["Cap", 9]]},  # no kills: not counted
            {"monster": "", "kills": 50, "loot": [["Cap", 9]]},
            {"monster": "Slime", "kills": 30, "loot": None}]                   # no loot read
    out = farm.records(rows)
    assert [(r.item, r.got, r.kills, r.sessions, r.every) for r in out] == [
        ("Cap", 4, 200, 2, 50), ("Leather", 1, 200, 2, 200)]


def test_a_drop_rate_at_or_above_one_a_kill_is_per_kill():
    """3 drops in 1 kill said nothing, 3 in 2 "once every 2 kills" (audit GAM-10)."""
    assert (farm.Record("x", "y", 3, 1, 1).every, farm.Record("x", "y", 3, 1, 1).per_kill) == (None, 3.0)
    assert (farm.Record("x", "y", 3, 2, 1).every, farm.Record("x", "y", 3, 2, 1).per_kill) == (None, 1.5)
    assert (farm.Record("x", "y", 2, 3, 1).every, farm.Record("x", "y", 2, 3, 1).per_kill) == (2, None)


def test_inventory_hint_names_etc_and_equip_slots(monkeypatch):
    items = {"item/1": {"name": "Red Potion", "type": "Use / Potion"},
             "item/2": {"name": "Leather", "type": "Etc / Monster Drop"},
             "item/3": {"name": "Bronze Sword", "type": "Equip / One-Handed Sword"}}
    kb = SimpleNamespace(get=items.get)
    monkeypatch.setattr(grind, "is_potion", lambda kb, key: key == "item/1")
    slots = [SimpleNamespace(status="certain", matches=[(k, 1.0)], index=i) for i, k in enumerate(items, 1)]
    hint = grind.inventory_hint(slots + [SimpleNamespace(status="ambiguous", matches=[("item/2", 2.0)], index=9)], kb)
    assert "slot 1: Red Potion" in hint and "slot 2: Leather" in hint and "slot 3: Bronze Sword" in hint
    assert "slot 9" not in hint and '"etc"' in hint


def test_the_grind_read_asks_for_the_etc_and_equip_tabs():
    from maplehelper.ui.overlay import Overlay
    q = Overlay.GRIND_QUESTION
    assert '"etc"' in q and '"equip"' in q and "Etc tab" in q and "Equip tab" in q


# ---------------------------------------------------------------- the Farm tab

@needs_kb
def test_farm_tab_flow(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store, wishlist
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 34)
    s = store.Settings()
    kb = KnowledgeBase(REAL_KB)
    ore = farm.item_key(kb, "Bronze Ore")
    wishlist.toggle(s, c.id, ore)
    d = ToolsDialog(kb, p, s, "he", "", {}, page="farm")
    app.processEvents()
    assert PAGES[d.stack.currentIndex()] == "farm"

    def texts(layout_owner):
        import re
        raw = " ".join(w.text() for kind in (QLabel, QPushButton) for w in layout_owner.findChildren(kind)
                       if w.isVisibleTo(layout_owner))
        # the words as read: no markup, no direction marks, plain spaces
        return re.sub(r"[‎‏‪-‮⁦-⁩]", "", re.sub(r"<[^>]+>", "", raw)
                      .replace("&nbsp;", " ").replace(" ", " ").replace("&amp;", "&"))

    # nothing picked yet: a hint, the wishlist's item as a chip, monsters worth farming at Lv. 34
    page = d.pages["farm"]
    assert d.farm_wished.count() == 1 and d.farm_targets.count() == len(farm.targets(kb, 34, n=6))
    # the wishlist chip picks the item: its droppers, one card each, and the target is saved per character
    d.farm_wished.itemAt(0).widget().click()
    app.processEvents()
    assert s["farm_target"] == {c.id: ore}
    assert d.farm_input.text() == "Bronze Ore"
    shown = texts(page)
    for m in kb.droppers[ore]:
        assert kb.get(m)["name"] in shown
    # typing part of a name takes the shortest item monsters drop; an unknown name says so and clears the target
    d.farm_input.setText("zzz no such item")
    d._farm_pick()
    assert s["farm_target"] == {} and "zzz no such item" in texts(page)
    # "Farm it" makes the monster the next session's, on the grind tracker too
    d._farm_hunt("Horny Mushroom")
    assert d._grind_choice == "Horny Mushroom"
    d.show_page(PAGES.index("exp"))
    assert d.grind_monster.text() == "Horny Mushroom"
    # a session with two Etc reads: the loot lines and, once it ends, the player's own drop counts
    st = d.grind
    t0 = 1_000_000.0
    st.start(c.id, grind.Reading(t0, 34, 10.0, "Ant Tunnel I", "Horny Mushroom", None, None, True,
                                 {"Horny Mushroom Cap": 2}), "Horny Mushroom", picked=True)
    st.add(c.id, grind.Reading(t0 + 1800, 34, 30.0, "Ant Tunnel I", "", None, None, True,
                               {"Horny Mushroom Cap": 12}))
    d.show_page(PAGES.index("farm"))
    assert "Horny Mushroom Cap ×10" in texts(page)
    st.end(c.id, kb, now=t0 + 1800)
    d.grind_changed()
    assert "Horny Mushroom: Horny Mushroom Cap ×10" in texts(page)
    d.close()


@pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
def test_worth_farming_puts_what_the_player_needs_first():
    # farming isn't only for an NPC's price (the owner): a drop a quest of the player's needs comes first, tagged
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    wanted = farm.needs(kb, 20, "Bowman", "Bowman", [], None, [])
    assert wanted.get("stirge wing", ("", ""))[0] == "quest"
    rows = farm.targets(kb, 20, n=6, wanted=wanted)
    assert rows and rows[0].needed >= 1 and rows[0].drops[0].need
    assert [r.needed for r in rows] == sorted((r.needed for r in rows), reverse=True)
    plain = farm.targets(kb, 20, n=6)                   # nothing wanted: by the NPC price, as before
    assert all(not d.need for r in plain for d in r.drops)


def test_a_misspelt_search_offers_the_names_close_to_it():
    # "stelly" found nothing in a list holding Steely Throwing Knives (the owner)
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    qapp = QApplication.instance() or QApplication(sys.argv)

    from maplehelper.ui.tools import EntityPicker
    names = ["Steely Throwing Knives", "Steel Ore", "Subi Throwing-Stars", "Blue Snail Shell"]
    p = EntityPicker([(n, n, None) for n in names], "x")
    p.show()
    p.setFocus()
    QTest.keyClicks(p, "stelly")
    qapp.processEvents()
    c = p.completer()
    assert [c.completionModel().index(i, 0).data() for i in range(c.completionCount())] == ["Steely Throwing Knives"]
    p.setText(c.completionModel().index(0, 0).data(c.completionRole()))
    p._chosen()
    assert p.text() == "Steely Throwing Knives"           # the name, not the misspelling it was found by
    p.close()


@needs_kb
def test_the_pages_link_to_each_other(monkeypatch, tmp_path):
    # what a card shows, a tap from the page about it (the owner): a monster's map and hit & damage, a grind
    # session on it, an item's droppers and price, an NPC's map
    from PySide6.QtWidgets import QApplication, QPushButton
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    p.add("Kiwi", "Bowman", "Bowman", 20)
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, store.Settings(), "en", "", {}, page="train")
    try:
        app.processEvents()
        card = next(w for i in range(d.train_list.count()) if (w := d.train_list.itemAt(i).widget())
                    and w.objectName() == "Card")
        links = [b.text().replace("&&", "&") for b in card.findChildren(QPushButton) if b.objectName() == "Link"]
        assert links[:3] == ["How to get there", "Hit & damage", "Grind tracker"]
        d._nav("calc:Stirge")
        assert PAGES[d.stack.currentIndex()] == "calc" and d.calc_input.text() == "Stirge"
        d._nav("track:Stirge")
        assert PAGES[d.stack.currentIndex()] == "exp" and d.grind_monster.text() == "Stirge"
        d._nav("farm:Stirge%20Wing")
        assert PAGES[d.stack.currentIndex()] == "farm"
        d._nav("price:Stirge%20Wing")
        assert PAGES[d.stack.currentIndex()] == "prices" and d.price_input.text() == "Stirge Wing"
        d._nav("route:Mrs.%20Ming%20Ming")              # an NPC: its map
        assert PAGES[d.stack.currentIndex()] == "route" and d.route_to.text()
        d._nav("route:Stirge")                          # a monster: its busiest map
        assert d.route_to.text() and d.route_to.text() != "Stirge"
        d._go_farm_hunt("Stirge")                       # "farm it" from hit & damage: the monster on top
        from PySide6.QtWidgets import QLabel
        assert PAGES[d.stack.currentIndex()] == "farm"
        top = [d.farm_item.itemAt(i).widget() for i in range(d.farm_item.count())]
        assert any(w is not None and any("Stirge" in lb.text() for lb in w.findChildren(QLabel, "CardName"))
                   for w in top)
        assert "item:" in d._gear_links("[[img:x.png]]Hunter's Bow 42 W.ATK", "<img src='x.png'> Hunter's Bow 42 W.ATK")
    finally:
        d.close()


@needs_kb
def test_a_quest_says_how_you_get_it_and_what_to_do():
    # a citizenship quest with no NPC and nothing to bring said nothing about how to do it (the owner)
    from maplehelper import quests
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    q = quests.quest(kb, "quest/506000")
    assert q.self_start and "Henesys" in q.task and quests.town_of(kb, q) == "Henesys"
    rina = quests.quest(kb, "quest/506001")
    assert not rina.self_start and "greet Rina" in rina.task


def test_the_search_box_x_shows_however_the_text_got_there():
    # an item picked from a card fills the box with its signals held back: the X showed only after typing (the owner)
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui.tools import EntityPicker
    app = QApplication.instance() or QApplication(sys.argv)
    p = EntityPicker([("Bronze Helmet", "Bronze Helmet", None)], "x")
    p.blockSignals(True)
    p.setText("Bronze Helmet")
    p.blockSignals(False)
    assert p._clear.isVisible()
    p.blockSignals(True)
    p.clear()
    p.blockSignals(False)
    assert not p._clear.isVisible() and app


@needs_kb
def test_a_quest_jump_from_crafting_opens_the_quests_page(monkeypatch, tmp_path):
    # "go to the quest" on a profession's quest did nothing on the crafting page (the owner)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    from maplehelper.ui.widgets import zoom_on_hover
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    p.add("Kiwi", "Warrior", "Fighter", 30)
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, store.Settings(), "he", "", {}, page="crafting")
    try:
        app.processEvents()
        d._goto_quest("Silas Irons in Need of an Apprentice")       # Smithing's own quest: its card right there
        assert PAGES[d.stack.currentIndex()] == "crafting"
        d._goto_quest("Jane and the Mushroom")                      # any other: the quests page
        assert PAGES[d.stack.currentIndex()] == "quests"
        from PySide6.QtWidgets import QLabel
        lb = QLabel()
        zoom_on_hover(lb, REAL_KB / "index.json", "x")      # a picture's hover shows it large
        assert "<img" in lb.toolTip() and "x" in lb.toolTip()
    finally:
        d.close()


@needs_kb
def test_what_to_do_reads_in_hebrew():
    # the quest journal showed in English in the Hebrew app (the owner): every line has its Hebrew
    from maplehelper import quests
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    rina = quests.quest(kb, "quest/506001")
    assert "Rina" in quests.task_text(rina, "he") and "ביקש" in quests.task_text(rina, "he")
    assert quests.task_text(rina, "en") == rina.task and not rina.task.startswith(("⌄", "02"))
    talk = [q for k, e in kb.entities.items() if e.get("category") == "quest"
            for q in [quests.quest(kb, k)] if q and q.task and not q.needs]
    assert talk and all(quests.task_text(q, "he") != q.task for q in talk)


@needs_kb
def test_a_quest_says_who_it_is_finished_with():
    # the opener starts on its own and ends with Arthur; Kerning City's starts with Arthur and ends with Roxy
    from maplehelper import quests
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    kerning = quests.quest(kb, "quest/506100")
    assert kerning.npc == "Arthur" and kerning.turn_in == "Roxy"
    assert quests.quest(kb, "quest/506000").turn_in == ""


@needs_kb
def test_a_citizenship_quest_jump_stays_on_citizenship(monkeypatch, tmp_path):
    # Kerning City's opener asks for "To Henesys" first: its jump went to the quests page, "not on your lists"
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    c = p.add("Kiwi", "Thief", "Thief", 15)
    c.town = "Kerning City"
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, store.Settings(), "he", "", {}, page="town")
    try:
        app.processEvents()
        d._goto_quest("To Henesys, the Prairie Town")
        assert PAGES[d.stack.currentIndex()] == "town" and d.town_pick.value() == "Henesys"
        assert d.town_search.text() == "To Henesys, the Prairie Town"
    finally:
        d.close()


def test_a_done_daily_or_weekly_quest_comes_back_without_an_invented_reset():
    # the KB names no reset time (the owner: don't make things up): a daily one is back the next calendar day,
    # a weekly one seven days after it was marked
    from datetime import datetime

    from maplehelper import quests
    done = datetime(2026, 10, 3, 15, 30).timestamp()
    assert quests.back_at("daily", done) == datetime(2026, 10, 4).timestamp()
    assert quests.back_at("weekly", done) == done + 7 * 86400


@needs_kb
def test_a_daily_quest_comes_back_after_the_reset():
    # 71 of the 88 citizenship quests are daily or weekly: marked done they left the list for good
    from types import SimpleNamespace

    from maplehelper import quests
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    # "Asking After Rina" is daily; "First Greeting with Rina", tagged Daily too, is what it asks for first: once
    assert quests.quest(kb, "quest/506002").cycle == "daily" and quests.quest(kb, "quest/506001").cycle == ""
    import time
    now = time.time()
    c = SimpleNamespace(quests_done=["quest/506002", "quest/506000"], cycle_done={"quest/506002": now - 2 * 86400})
    assert quests.expire_cycles(kb, c) and c.quests_done == ["quest/506000"] and not c.cycle_done
    c = SimpleNamespace(quests_done=["quest/506002"], cycle_done={"quest/506002": now})
    assert not quests.expire_cycles(kb, c) and c.quests_done == ["quest/506002"]


@needs_kb
def test_sell_or_keep_sorts_the_bag_by_the_kb():
    # the inventory check's answer on the page, by what the KB says of each item: no AI, nothing guessed
    from maplehelper import sellkeep
    from maplehelper.inventory import Slot
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    key = lambda n: kb._item_by_name[n.lower()]  # noqa: E731
    slots = [Slot(1, b"", [(key("Stirge Wing"), 0.0)]),                # a quest of a level-20 Bowman asks for it
             Slot(2, b"", [(key("Adamantium Knuckle"), 0.0)]),         # Warrior gloves, level 40
             Slot(3, b"", [], "unknown")]
    v = {x.slot: x for x in sellkeep.classify(kb, slots, 20, "Bowman", "Bowman")}
    assert v[1].kind == "quest" and v[1].why
    assert v[2].kind == "other_job"
    assert v[3].kind == "unknown" and not v[3].name


def test_an_item_the_free_market_pays_more_for_is_sold_there():
    from maplehelper import sellkeep
    v = [sellkeep.Verdict("sell", "item/1", "A", price=10), sellkeep.Verdict("no_price", "item/2", "B"),
         sellkeep.Verdict("sell", "item/3", "C", price=500), sellkeep.Verdict("quest", "item/4", "D")]
    out = {x.key: x for x in sellkeep.with_market(v, {"item/1": 900, "item/2": 50, "item/3": 100, "item/4": 9999})}
    assert (out["item/1"].kind, out["item/1"].fm) == ("fm", 900) and out["item/2"].kind == "fm"
    assert out["item/3"].kind == "sell" and out["item/4"].kind == "quest"     # an NPC pays more; a quest keeps it
