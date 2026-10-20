"""The loot log's money side and its history: ETC on its own, the potions used (read off the quick slots) at what an
NPC shop asks, the net profit; every log kept with its maps (lootdb.py), the maps compared and what that shows
(lootinsights.py), and the Loot log page's History and "Where it pays best" (ui/tools.py)."""
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import lootdb, lootinsights, lootlog, quickslot  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
LEAF = "item/358"           # Etc / Monster Drop, an NPC pays 11
GLADIUS = "item/542"        # an equip with a sell price
RED, LEMON = "item/270", "item/240"
A, B = "000000001", "000000010"


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


def test_the_summary_has_etc_potions_and_the_net(kb):
    lg = lootlog.Log(start=0, ended=600)
    lg.add([lootlog.Event(1, "mesos", 1000), lootlog.Event(2, "item", 10, "Leaf", LEAF),
            lootlog.Event(3, "item", 1, "Old Gladius", GLADIUS), lootlog.Event(4, "potion", 4, "Red Potion", RED),
            lootlog.Event(5, "potion", 2, "Red Potion", RED), lootlog.Event(6, "potion", 1, "Lemon", LEMON)])
    sm = lootlog.summarize(kb, lg)
    leaf, red, lemon = lootlog.sell_price(kb, LEAF), lootlog.potion_cost(kb, RED), lootlog.potion_cost(kb, LEMON)
    assert sm.etc == 10 and sm.etc_value == 10 * leaf
    assert sm.other_value == lootlog.sell_price(kb, GLADIUS) and sm.value == 1000 + sm.etc_value + sm.other_value
    assert sm.potions == [("Red Potion", 6, RED, red), ("Lemon", 1, LEMON, lemon)] and sm.used == 7
    assert sm.potions_cost == 6 * red + lemon and sm.net == sm.value - sm.potions_cost
    assert sm.net_h == round(sm.net * 6)
    assert sm.picked == 11                      # potions used are no pickups


def test_a_log_keeps_its_potions_maps_and_level_through_a_restart():
    lg = lootlog.Log(start=0, level=34)
    lg.add([lootlog.Event(1, "potion", 3, "Red Potion", RED, map=A)])
    lg.on_map(A, 30)
    lg.on_map(A, 15)
    lg.on_map("", 99)                           # an unread map counts nowhere
    back = lootlog.Log.from_json(lg.to_json())
    assert back.potions() == {"Red Potion": [3, RED]} and back.maps == {A: 45} and back.level == 34
    assert back.events[0].map == A


def _db_log(kb, cid, start, seconds, level, maps, events):
    lid = lootdb.open_log(cid, "Kiwi", start, level)
    lootdb.add_events(lid, events, kb)
    lootdb.set_spots(lid, maps)
    lootdb.end_log(lid, start + seconds)
    return lid


def test_sessions_and_spots_add_up_what_each_map_gave(isolated_store, kb):
    _db_log(kb, "c1", 1000, 3600, 30, {A: 3600}, [
        lootlog.Event(1100, "exp", 5000, map=A), lootlog.Event(1200, "mesos", 2000, map=A),
        lootlog.Event(1300, "item", 10, "Leaf", LEAF, map=A), lootlog.Event(1400, "potion", 20, "Red Potion", RED, map=A)])
    _db_log(kb, "c1", 9000, 1800, 32, {A: 600, B: 1200}, [
        lootlog.Event(9100, "exp", 100, map=A), lootlog.Event(9700, "exp", 3000, map=B),
        lootlog.Event(9800, "mesos", 4000, map=B)])
    _db_log(kb, "c2", 5000, 600, 50, {A: 600}, [lootlog.Event(5100, "mesos", 99999, map=A)])
    leaf, red = lootlog.sell_price(kb, LEAF), lootlog.potion_cost(kb, RED)

    ss = lootdb.sessions("c1", kb)
    assert [s.started for s in ss] == [9000, 1000]                  # newest first, only this character's
    first = ss[1]
    assert (first.exp, first.mesos, first.etc_value, first.potions_cost, first.potions) == (5000, 2000, 10 * leaf,
                                                                                           20 * red, 20)
    assert first.net == 2000 + 10 * leaf - 20 * red and first.level == 30 and first.maps == {A: 3600}
    assert first.items == [("Leaf", 10, LEAF, leaf)] and first.potions_seen
    assert list(ss[0].maps) == [B, A]                               # longest first

    spots = {sp.map: sp for sp in lootdb.spots("c1", kb)}
    assert spots[A].seconds == 4200 and spots[A].sessions == 2 and spots[A].levels == (30, 32)
    assert spots[A].exp == 5100 and spots[A].potions_cost == 20 * red
    assert spots[B].seconds == 1200 and (spots[B].exp, spots[B].mesos) == (3000, 4000) and not spots[B].potions_seen
    assert spots[B].per_hour(spots[B].exp) == 9000


def test_a_version_1_database_is_upgraded_with_its_rows(isolated_store, kb):
    with sqlite3.connect(lootdb.PATH) as con:
        con.executescript("""
CREATE TABLE logs (id INTEGER PRIMARY KEY, character_id TEXT NOT NULL, character_name TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL, ended REAL);
CREATE TABLE events (id INTEGER PRIMARY KEY, log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    t REAL NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('exp', 'mesos', 'item')), amount INTEGER NOT NULL,
    item TEXT NOT NULL DEFAULT '', item_key TEXT NOT NULL DEFAULT '', npc_price INTEGER);
CREATE INDEX events_t ON events(t);
INSERT INTO logs VALUES (1, 'c1', 'Kiwi', 100, 700);
INSERT INTO events VALUES (1, 1, 200, 'mesos', 55, '', '', NULL);
INSERT INTO events VALUES (2, 1, 300, 'item', 2, 'Leaf', 'item/358', 11);
PRAGMA user_version = 1;
""")
    ss = lootdb.sessions("c1", kb)
    assert len(ss) == 1 and ss[0].mesos == 55 and ss[0].etc_value == 22 and ss[0].level is None
    lid = lootdb.open_log("c1", "Kiwi", 1000, 40)                   # the new columns and kinds work
    lootdb.add_events(lid, [lootlog.Event(1001, "potion", 1, "Red Potion", RED, map=A)], kb)
    lootdb.end_log(lid, 1100)
    with sqlite3.connect(lootdb.PATH) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == lootdb.VERSION
        assert con.execute("SELECT kind, map FROM events WHERE log_id = ?", (lid,)).fetchall() == [("potion", A)]
    lootdb.forget("c1")                                             # the cascade still holds
    assert lootdb.sessions("c1", kb) == []


def _spot(mid, hours, exp=0, mesos=0, etc=0, potions=0, levels=(30, 30), seen=True):
    return lootdb.Spot(seconds=hours * 3600, exp=exp, mesos=mesos, etc_value=etc, potions_cost=potions,
                       potions_seen=seen, map=mid, sessions=1, levels=levels)


def _keys(found):
    return [i.key for i in found]


def test_one_map_better_at_both_says_so():
    found = lootinsights.insights([_spot(A, 1, exp=20000, mesos=50000), _spot(B, 1, exp=10000, mesos=30000)])
    assert found[0] == lootinsights.Insight("loot_ins_best_both", {"map": A, "exp": 20000, "net": 50000})


def test_exp_on_one_map_and_mesos_on_another_is_a_tradeoff():
    found = lootinsights.insights([_spot(A, 1, exp=20000, mesos=10000), _spot(B, 2, exp=20000, mesos=60000)])
    assert _keys(found)[:3] == ["loot_ins_best_exp", "loot_ins_best_net", "loot_ins_tradeoff"]
    assert found[0].args == {"map": A, "exp": 20000, "pct": 100, "other": B}
    assert found[1].args == {"map": B, "net": 30000, "pct": 200, "other": A}


def test_maps_too_close_or_too_short_make_no_conclusion():
    found = lootinsights.insights([_spot(A, 1, exp=10200, mesos=10100), _spot(B, 1, exp=10000, mesos=10000),
                                   _spot("short", 0.1, exp=999999)])
    assert "loot_ins_best_both" not in _keys(found) and "loot_ins_best_exp" not in _keys(found)
    assert lootinsights.insights([_spot(A, 0.1, exp=5)]) == []
    assert _keys(lootinsights.insights([_spot(A, 1, exp=5, mesos=5)])) == ["loot_ins_single"]


def test_potions_etc_losses_and_levels_get_their_lines():
    found = lootinsights.insights([
        _spot(A, 1, exp=1000, mesos=10000, potions=5000, levels=(30, 31)),     # potions took half
        _spot(B, 1, exp=500, mesos=1000, etc=9000, levels=(38, 38)),           # mostly ETC
        _spot("c", 1, exp=100, mesos=100, potions=900)])                        # lost mesos
    keys = _keys(found)
    assert {"map": A, "pct": 50} in [i.args for i in found if i.key == "loot_ins_potions"]
    assert {"map": B, "pct": 90} in [i.args for i in found if i.key == "loot_ins_etc"]
    assert {"map": "c", "net": -800} in [i.args for i in found if i.key == "loot_ins_loss"]
    assert lootinsights.Insight("loot_ins_levels", {"lo": 30, "hi": 38}) in found
    assert "loot_ins_no_potions" not in keys


def test_no_potions_read_anywhere_is_said():
    found = lootinsights.insights([_spot(A, 1, exp=1, mesos=1, seen=False), _spot(B, 1, exp=2, mesos=2, seen=False)])
    assert "loot_ins_no_potions" in _keys(found)


@pytest.fixture
def page(isolated_store, kb):
    from maplehelper.ui.tools import ToolsDialog
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 21)
    s = isolated_store.Settings()
    d = ToolsDialog(kb, p, s, "en", "", {}, "loot")
    yield d
    d.loot.stop()
    d.close()


def _texts(layout):
    from PySide6.QtWidgets import QLabel
    out = []
    for i in range(layout.count()):
        it = layout.itemAt(i)
        if it.widget() is not None:
            out += [it.widget().text()] if isinstance(it.widget(), QLabel) else \
                [w.text() for w in it.widget().findChildren(QLabel)]
        elif it.layout() is not None:
            out += _texts(it.layout())
    return out


def test_the_page_shows_the_history_and_compares_the_maps(page, kb):
    d = page
    cid = d.c.id
    _db_log(kb, cid, 1000, 3600, 21, {A: 3600}, [lootlog.Event(1100, "exp", 20000, map=A),
                                                  lootlog.Event(1200, "mesos", 9000, map=A)])
    _db_log(kb, cid, 9000, 3600, 22, {B: 3600}, [lootlog.Event(9100, "exp", 8000, map=B),
                                                  lootlog.Event(9200, "mesos", 30000, map=B),
                                                  lootlog.Event(9300, "potion", 10, "Red Potion", RED, map=B)])
    d._loot_hist_sig = None
    d.loot_changed()
    hist = " | ".join(_texts(d.loot_recent))
    assert "Mushroom Town" in hist and "EXP 20,000" in hist and "Potions −" in hist
    cmp_ = " | ".join(_texts(d.loot_compare))
    assert "Most EXP" in cmp_ and "Pays best" in cmp_ and "trade-off" in cmp_
    assert not d.loot_more.isVisibleTo(d)


def test_the_page_and_the_card_show_potions_and_the_net(page, kb):
    from maplehelper.ui.lootoverlay import LootOverlay
    d = page
    d.loot_changed()
    d.loot_start.click()
    d.loot._log.add([lootlog.Event(1.0, "mesos", 1000), lootlog.Event(2.0, "item", 2, "Leaf", LEAF),
                     lootlog.Event(3.0, "potion", 3, "Red Potion", RED)])
    d.loot.changed.emit()
    red = lootlog.potion_cost(kb, RED)
    assert d.loot_cells["potions"][1].text() == f"-{3 * red:,}"
    assert d.loot_cells["net"][1].text() == f"{1000 + 22 - 3 * red:,}"
    assert d.loot_cells["etc"][1].text() == "22"
    assert any("Red Potion" in x for x in _texts(d.loot_items))
    from maplehelper.i18n import I18n
    card = LootOverlay(d.loot, d.settings, kb, I18n("en"))
    try:
        card.refresh()
        assert any(x.startswith("Potions") and "3" in x for x in card.lines())
        assert any(x.startswith("ETC") for x in card.lines()) and any(x.startswith("Net") for x in card.lines())
    finally:
        card.close()
    d.loot.end()


def test_the_scanner_counts_a_potion_used_on_the_map_it_was_on(page, kb):
    from maplehelper.minimap import Here
    from maplehelper.ui.location import LOCATION
    d = page
    scan = d.loot
    scan.start()
    LOCATION.set(Here(A))
    try:
        for t, n in ((1.0, 50), (2.0, 50), (3.0, 48), (4.0, 48)):
            scan._deliver_quick(quickslot.Read({1: (RED, n)}), t)
        assert scan.quick_state == "ok"
        assert [(e.kind, e.amount, e.key, e.map) for e in scan._log.events] == [("potion", 2, RED, A)]
        scan._deliver_quick(quickslot.Read(), 5.0)
        assert scan.quick_state == "none"
        scan._reading = True                    # (a read "still going": the ticks start no real screen read)
        scan._tick_at = None
        scan._tick()
        scan._tick_at -= 3.0                    # three seconds later
        scan._tick()
        assert scan._log.maps[A] == pytest.approx(3.0, abs=0.5)
    finally:
        scan._reading = False
        LOCATION.set(None)
        scan.end()
