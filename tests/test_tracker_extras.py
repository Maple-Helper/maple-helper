"""The grind tracker's extras (lootextras.py, ui/grindtracker.py, ui/lootoverlay.py): the next level and its time at
this rate, potions running low with the nearest shop, the monsters killed, the last session on the same map, sell or
keep, and the history's Delete."""
import os
import re
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from maplehelper import lootdb, lootextras, lootlog, quickslot  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
LEAF, GLADIUS, LEMON, RED = "item/358", "item/542", "item/240", "item/270"


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture(scope="module")
def ant(kb):
    from maplehelper import routes
    g = routes.of(kb)
    return next(m for m in g.maps if g.name(m) == "Ant Tunnel I")


def test_the_next_level_and_its_time_at_this_rate(kb):
    from maplehelper import plan
    need = plan.exp_table(kb)[40]
    g = lootextras.level_goal(kb, 40, need - 3000, 6000)
    assert (g.level, g.left) == (40, 3000) and g.seconds == pytest.approx(1800)
    assert lootextras.level_goal(kb, 40, need - 3000, None).seconds is None
    assert lootextras.level_goal(kb, None, 10, 6000) is None and lootextras.level_goal(kb, 40, need + 1, 1) is None


def test_the_nearest_shop_is_found_by_the_npcs_own_name_and_its_town(kb, ant):
    """The item page lists "Dr. Faymus Grocer": the NPC is Dr. Faymus; from Ant Tunnel I the Kerning City shop is the
    nearest of the shops selling lemons; with no map known, the cheapest (the first) one."""
    shop, npc, hops = lootextras.nearest_shop(kb, LEMON, ant)
    assert shop[1] == "Kerning City" and npc and kb.get(npc)["name"] == shop[0] and hops is not None
    shop2, _, hops2 = lootextras.nearest_shop(kb, LEMON, None)
    assert hops2 is None and shop2[2] <= shop[2]


def test_only_potions_used_this_session_and_running_low_raise_the_alert(kb, ant):
    low = lootextras.low_potions(kb, {LEMON: 12, RED: 0, "item/238": 300}, {LEMON, "item/238"}, ant)
    assert [(x.key, x.left) for x in low] == [(LEMON, 12)]      # Red Potion 0 was never used: no alarm
    assert low[0].shop and low[0].npc_key


def test_the_monsters_killed_are_named_by_their_exp_on_the_maps(kb, ant):
    """Each EXP message is a kill: 41 EXP on Ant Tunnel I is a Horny Mushroom (the KB's EXP for it); an amount no
    monster there gives (a quest's) is none. With no map read, the level's monsters with a unique EXP."""
    from maplehelper import tables
    exp = {r["key"]: r["exp"] for r in tables.rows(kb, "monsters")}
    horny, zombie = exp["monster/19"], exp["monster/21"]
    got = lootextras.kills(kb, {horny: 30, zombie: 4, 99999: 2}, [ant])
    assert [(k, n) for k, _, n in got] == [("monster/19", 30), ("monster/21", 4)]
    assert got[0][1] == "Horny Mushroom"
    assert lootextras.kills(kb, {horny: 3}, []) == []                  # no map, no level: nothing to name
    assert lootextras.kills(kb, {}, [ant]) == []


def test_the_last_session_on_the_same_map(isolated_store, kb, ant):
    a = lootdb.open_log("c1", "K", 1000, 30)
    lootdb.add_events(a, [lootlog.Event(1100, "exp", 6000, map=ant)], kb)
    lootdb.set_spots(a, {ant: 3600})
    lootdb.end_log(a, 4600)
    b = lootdb.open_log("c1", "K", 9000, 30)
    lootdb.add_events(b, [lootlog.Event(9100, "exp", 99, map="other")], kb)
    lootdb.set_spots(b, {"other": 600})
    lootdb.end_log(b, 9600)
    prev = lootextras.previous_on_map(lootdb.sessions("c1", kb), ant, exclude=99)
    assert prev.id == a and prev.per_hour(prev.exp) == 6000
    assert lootextras.previous_on_map(lootdb.sessions("c1", kb), ant, exclude=a) is None
    assert lootextras.change_pct(6600, 6000) == 10 and lootextras.change_pct(1, 0) is None


def test_sell_or_keep(kb):
    from maplehelper import sitedata
    sell = sitedata.sell_list(kb)
    if sell is None:
        pytest.skip("this KB has no Safe to Sell? list")
    needed = next(k for k, n in sell.needs.items() if n.quests)
    assert lootextras.sell_advice(kb, needed, 10, None).verdict == "keep"
    free = next(k for k, e in kb.entities.items() if e.get("category") == "item" and k not in sell.needs
                and lootlog.sell_price(kb, k))
    assert lootextras.sell_advice(kb, free, 10, 100).verdict == "players"
    assert lootextras.sell_advice(kb, free, 10, 12).verdict == "players"      # whoever pays more
    assert lootextras.sell_advice(kb, free, 10, 9).verdict == "npc"
    assert lootextras.sell_advice(kb, free, 10, 100, low_data=True).verdict == "npc"    # a guess on few sales


@pytest.fixture
def window(isolated_store, kb):
    from maplehelper.ui.grindtracker import GrindTracker
    from maplehelper.ui.lootscan import LootScanner
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 40)
    s = isolated_store.Settings()
    scan = LootScanner(kb, p, s)
    d = GrindTracker(scan, kb, s, "en", "")
    yield d
    scan.stop()
    d.close()


def test_the_window_shows_the_goal_the_alert_the_kills_and_the_advice(window, kb, ant):
    from maplehelper import plan
    from maplehelper.hud import Hud
    from maplehelper.minimap import Here
    from maplehelper.ui.location import LOCATION
    from maplehelper.ui.widgets import MAP_REQUESTS
    d = window
    scan = d.scanner
    scan.start()
    LOCATION.set(Here(ant))
    asked = []
    MAP_REQUESTS.requested.connect(asked.append)
    try:
        now = time.time()
        scan._log.start = now - 900
        scan._log.add([lootlog.Event(now - 800 + 60 * i, "exp", 500) for i in range(12)] +
                      [lootlog.Event(now - 300, "item", 3, "Leaf", LEAF), lootlog.Event(now - 200, "potion", 2,
                                                                                         "Lemon", LEMON)])
        scan._log.on_map(ant, 900)
        need = plan.exp_table(kb)[40]
        scan._deliver_hud(Hud(40, "Magician", "Kiwi", need - 5000, 50.0))
        for t, n in ((1.0, 14), (2.0, 14)):
            scan._deliver_quick(quickslot.Read({1: (LEMON, n)}), t)
        assert scan.hud.level == 40 and scan.potion_counts == {LEMON: 14}
        d.refresh()
        text = " | ".join(d.lines())
        assert "To level 41:" in text and "5,000 EXP to go" in text and "at this rate in" in text
        assert "Only 14 Lemon left" in text and "Buy them from" in text
        assert "Where to sell" in text and "Monsters you killed" in text
        route = next(b for b in d.findChildren(type(d.start_btn)) if b.text() == "How to get there")
        route.click()
        assert asked and asked[-1].startswith("npc/")
        # the card over the game says the same, short
        from maplehelper.i18n import I18n
        from maplehelper.ui.lootoverlay import LootOverlay
        card = LootOverlay(scan, d.settings, kb, I18n("en"))
        try:
            card.refresh()
            lines = card.lines()
            assert any("Only 14 Lemon left" in x for x in lines) and any("To level 41" in x for x in lines)
            assert not any("**" in x for x in lines)
        finally:
            card.close()
    finally:
        MAP_REQUESTS.requested.disconnect(asked.append)
        LOCATION.set(None)
        scan.end()


def test_the_history_shows_each_sessions_monsters_and_deletes_with_a_second_press(window, kb, ant):
    from maplehelper import tables
    horny = next(r["exp"] for r in tables.rows(kb, "monsters") if r["key"] == "monster/19")
    d = window
    cid = d.scanner.cid()
    for i in range(2):
        lid = lootdb.open_log(cid, "Kiwi", 1000 + 10_000 * i, 22)
        lootdb.add_events(lid, [lootlog.Event(1100 + 10_000 * i + k, "exp", horny, map=ant) for k in range(5)], kb)
        lootdb.set_spots(lid, {ant: 600})
        lootdb.end_log(lid, 1600 + 10_000 * i)
    d.show_tab("history")
    text = " | ".join(d.lines())
    assert "Horny Mushroom" in text and "×5" in text and "Details" in text
    newest = lootdb.sessions(cid, kb)[0].id
    d._delete(newest)
    assert "Delete? Press again" in " | ".join(d.lines()) and len(lootdb.sessions(cid, kb)) == 2
    d._delete(newest)
    assert [s.id for s in lootdb.sessions(cid, kb)] != [] and newest not in [s.id for s in lootdb.sessions(cid, kb)]


def test_a_refresh_moves_the_numbers_in_place_never_a_new_table(window):
    """The window flickered while a session ran: every second it built its tables anew. Now the summary's numbers
    change in its table, and a section is built again only when what it shows changed."""
    d = window
    scan = d.scanner
    scan.start()
    try:
        scan._log.add([lootlog.Event(time.time() - 30, "mesos", 100)])
        d.refresh()
        box = lambda: [d.etc_box.itemAt(i).widget() for i in range(d.etc_box.count())]  # noqa: E731
        table, items = d._sum_card, box()
        scan._log.start -= 60                   # a minute later, nothing new picked up
        d.refresh()
        assert d._sum_card is table and box() == items
        scan._log.add([lootlog.Event(time.time(), "mesos", 50), lootlog.Event(time.time(), "item", 1, "Leaf", LEAF)])
        d.refresh()
        assert d._sum_card is table and "150" in table._cells[("mesos", 1)].text()
        assert box() != items
    finally:
        scan.end()


def test_each_item_counts_at_whoever_pays_more(kb, monkeypatch):
    """An NPC or the players (MesoWatch's usual sale price), whichever pays more; a players' guess on few sales never
    wins; no price at all counts nothing."""
    prices = {LEAF: (30, False), GLADIUS: (5, False)}
    monkeypatch.setattr(lootextras, "market_price", lambda kb, key: prices.get(key, (None, False)))
    leaf, glad = lootlog.sell_price(kb, LEAF), lootlog.sell_price(kb, GLADIUS)
    assert lootextras.best_sale(kb, LEAF, leaf) == ((30, "fm") if 30 > leaf else (leaf, "npc"))
    assert lootextras.best_sale(kb, GLADIUS, glad) == ((glad, "npc") if glad >= 5 else (5, "fm"))
    assert lootextras.best_sale(kb, LEMON, None) == (None, "")
    items = [("Leaf", 10, LEAF, leaf), ("Old Gladius", 1, GLADIUS, glad), ("Lemon", 2, LEMON, None)]
    v = lootextras.loot_value(kb, items)
    assert (v.etc_n, v.etc_v) == (10, 10 * max(30, leaf)) and v.items_n == 3 and v.items_v == max(glad, 5)
    prices[LEAF] = (999, True)                                 # few sales: a guess, not the better price
    assert lootextras.best_sale(kb, LEAF, leaf) == (leaf, "npc")


def test_the_bars_monsters_button_shows_a_real_monster(kb):
    from maplehelper.i18n import I18n
    from maplehelper.store import Settings
    from maplehelper.ui.gametoolbar import GameToolbar
    bar = GameToolbar(Settings(), I18n("en"))
    try:
        bar.set_monster_picture(kb.picture("monster/9"))
        assert bar._mob_pic is not None and not bar._windows["monster_overlay"].icon().isNull()
        bar.set_monster_picture(None)                          # no picture: the icon font's glyph stays
        assert bar._mob_pic is None and not bar._windows["monster_overlay"].icon().isNull()
    finally:
        bar.close()


def test_the_summary_counts_etc_and_items_at_the_better_price_with_tags(window, kb, monkeypatch):
    """ETC and items in the session's table: what they're worth (each at whoever pays more) and how many, no per
    hour; income, potions and net no per hour either (the owner); the item tables tag each NPC or FM."""
    monkeypatch.setattr(lootextras, "market_price", lambda kb, key: (40, False) if key == LEAF else (None, False))
    d = window
    scan = d.scanner
    scan.start()
    try:
        scan._log.add([lootlog.Event(time.time(), "mesos", 100), lootlog.Event(time.time(), "item", 3, "Leaf", LEAF),
                       lootlog.Event(time.time(), "item", 1, "Old Gladius", GLADIUS)])
        d.refresh()
        leaf, glad = lootlog.sell_price(kb, LEAF), lootlog.sell_price(kb, GLADIUS)
        cell = lambda k, c: re.sub("[\u200e\u200f\u2066-\u2069]", "", d._sum_card._cells[(k, c)].text())  # noqa: E731
        assert cell("etc", 1) == "3" and cell("etc", 2) == f"{3 * max(40, leaf):,}"       # how many, then worth
        assert cell("items", 1) == "1" and cell("items", 2) == f"{glad:,}"
        assert cell("income", 2) == f"{100 + 3 * max(40, leaf) + glad:,}"
        assert ("income", 1) not in d._sum_card._cells               # the totals: a number each, no per hour
        assert cell("exp", 2) and cell("mesos", 2)                    # EXP and mesos keep their per hour
        text = " | ".join(d.lines())
        assert "Where to sell" in text and "Profit by how you sell" not in text
        tags = {w.objectName() for w in d.findChildren(QLabel) if w.objectName().startswith("TrkTag")}
        assert {"TrkTagFm", "TrkTagNpc"} <= tags          # (and Keep beside a tag, for a quest's item)
    finally:
        scan.end()


def test_the_three_tables_colour_money_in_green_and_out_in_red():
    """Mesos, the loot's worth and the income green; the potions and the spending red; the net as it stands; EXP,
    the counts and a 0 plain. The same widget in the card, the full tracker and the history."""
    from maplehelper.i18n import I18n
    from maplehelper.ui.moneytable import CARD, TRACKER, MoneyTable
    m = lootextras.Money(exp=500, exp_h=1000, mesos=300, mesos_h=600, etc_n=4, etc_v=80, items_n=0, items_v=0,
                         hp_n=3, hp_cost=150, mp_n=0, mp_cost=0)
    for look in (TRACKER, CARD):
        w = MoneyTable(I18n("en"), look)
        w.set_money(m)
        name = lambda k, c, w=w: w._cells[(k, c)].objectName()  # noqa: E731
        assert name("mesos", 1) == name("etc", 2) == look.gain and name("income", 2) == look.good
        assert name("hp", 2) == name("spent", 2) == look.bad and name("net", 2) == look.good   # 380 - 150 > 0
        assert name("exp", 1) == name("etc", 1) == name("items", 2) == name("mp", 2) == look.num
        assert w._cells[("hp", 2)].text().endswith("-150") and "230" in w._cells[("net", 2)].text()
        w.set_money(lootextras.Money(mesos=10, hp_cost=50))
        assert w._cells[("net", 2)].objectName() == look.bad
        w.close()
