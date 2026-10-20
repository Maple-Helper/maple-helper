"""The grind tracker's extras (lootextras.py, ui/grindtracker.py, ui/lootoverlay.py): the next level and its time at
this rate, potions running low with the nearest shop, the rate graph, the last session on the same map, sell or keep,
sharing a session as a picture, and the history's filters and Delete."""
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

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


def test_the_rate_series_is_a_point_a_minute_over_the_last_five(kb):
    start = 1_000_000.0
    evs = [lootlog.Event(start + 30 + 60 * i, "exp", 100) for i in range(10)]
    evs.append(lootlog.Event(start + 90, "potion", 2, "Red Potion", RED))
    pts = lootextras.rate_series(kb, evs, start, start + 600)
    assert len(pts) == 10 and pts[0][0] == 1
    assert pts[0][1] == 6000                                   # 100 EXP in the first minute: 6,000 an hour
    assert pts[-1][1] == round(500 * 3600 / 300)               # the last five minutes' 500 EXP
    assert pts[1][2] < 0                                       # the potions' cost in the net
    assert lootextras.rate_series(kb, evs, start, start + 30) == []


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
    assert lootextras.sell_advice(kb, free, 10, 12).verdict == "npc"
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


def test_the_window_shows_the_goal_the_alert_the_graph_and_the_advice(window, kb, ant):
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
        assert len(d.graph.points) >= 10 and "What to do" in text
        route = next(b for b in d.findChildren(type(d.share_btn)) if b.text() == "How to get there")
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
        # sharing: a picture on the clipboard, then Save as picture shows
        d._share_now()
        assert not QApplication.clipboard().pixmap().isNull() and d.save_btn.isVisibleTo(d)
    finally:
        MAP_REQUESTS.requested.disconnect(asked.append)
        LOCATION.set(None)
        scan.end()


def test_the_history_filters_and_deletes_with_a_second_press(window, kb, ant):
    d = window
    cid = d.scanner.cid()
    for i, (mid, lvl) in enumerate(((ant, 35), ("other", 42), (ant, 41))):
        lid = lootdb.open_log(cid, "Kiwi", 1000 + 10_000 * i, lvl)
        lootdb.add_events(lid, [lootlog.Event(1100 + 10_000 * i, "mesos", 100 + i, map=mid)], kb)
        lootdb.set_spots(lid, {mid: 600})
        lootdb.end_log(lid, 1600 + 10_000 * i)
    d.show_tab("history")
    assert len(d._all_sessions) == 3
    d.map_filter.setCurrentIndex(d.map_filter.findData(ant))
    shown = [lb for lb in d.lines() if "Ant Tunnel I" in lb]
    assert len(shown) == 2
    d.level_filter.setCurrentIndex(d.level_filter.findData(40))
    assert len([lb for lb in d.lines() if "Ant Tunnel I" in lb]) == 1
    newest = lootdb.sessions(cid, kb)[0].id
    d._delete(newest)
    assert "Delete? Press again" in " | ".join(d.lines()) and len(lootdb.sessions(cid, kb)) == 3
    d._delete(newest)
    assert newest not in [s.id for s in lootdb.sessions(cid, kb)] and len(lootdb.sessions(cid, kb)) == 2
