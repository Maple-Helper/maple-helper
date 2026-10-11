"""The loot database (lootdb.py): every log and pickup kept for good, and a day's totals read back from it."""
import datetime as dt
import os
import sqlite3
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from maplehelper import lootdb, lootlog

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
LEAF = "item/358"


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


def _at(date: dt.date, hour: int, minute: int = 0) -> float:
    return dt.datetime.combine(date, dt.time(hour, minute)).timestamp()


def _rows(sql: str, *args):
    with sqlite3.connect(lootdb.PATH) as con:
        return con.execute(sql, args).fetchall()


def test_a_day_counts_its_own_pickups_and_its_part_of_a_log_over_midnight(isolated_store, kb):
    d1 = dt.date(2026, 10, 10)
    d2 = d1 + dt.timedelta(days=1)
    a = lootdb.open_log("c1", "Kiwi", _at(d1, 23, 30))
    lootdb.add_events(a, [lootlog.Event(_at(d1, 23, 40), "exp", 21), lootlog.Event(_at(d1, 23, 41), "mesos", 27),
                          lootlog.Event(_at(d1, 23, 42), "item", 2, "Leaf", LEAF),
                          lootlog.Event(_at(d2, 0, 10), "mesos", 100)], kb)
    lootdb.end_log(a, _at(d2, 0, 30))
    other = lootdb.open_log("c2", "Other", _at(d1, 22))
    lootdb.add_events(other, [lootlog.Event(_at(d1, 22, 5), "mesos", 9999)], kb)
    lootdb.end_log(other, _at(d1, 22, 10))

    first = lootdb.day("c1", d1)
    from maplehelper import farm
    leaf = farm.value(kb, LEAF).price
    assert (first.exp, first.mesos) == (21, 27)
    assert first.items == [("Leaf", 2, LEAF, leaf)] and first.value == 27 + 2 * leaf
    assert first.seconds == pytest.approx(30 * 60)          # 23:30 to midnight
    second = lootdb.day("c1", d2)
    assert (second.exp, second.mesos, second.items, second.seconds) == (0, 100, [], pytest.approx(30 * 60))
    assert lootdb.day("c1", d2 + dt.timedelta(days=1)).value == 0
    assert [d.date for d in lootdb.days("c1")] == [d2, d1]
    assert [d.date for d in lootdb.days("c1", limit=1)] == [d2]


def test_a_new_log_closes_one_left_open_at_its_last_pickup(isolated_store, kb):
    """The app closed mid-log and the log never came back: its row ends where its pickups stop."""
    a = lootdb.open_log("c1", "Kiwi", 1000.0)
    lootdb.add_events(a, [lootlog.Event(1100.0, "exp", 5)], kb)
    lootdb.open_log("c1", "Kiwi", 5000.0)
    assert _rows("SELECT ended FROM logs WHERE id = ?", a) == [(1100.0,)]


def test_deleting_a_character_deletes_its_logs_and_pickups(isolated_store, kb):
    p = isolated_store.Profiles()
    c = p.add("Kiwi", "Magician", "Magician", 20)
    keep = p.add("Other", "Warrior", "Warrior", 20)
    a = lootdb.open_log(c.id, "Kiwi", 1000.0)
    lootdb.add_events(a, [lootlog.Event(1001.0, "exp", 5)], kb)
    b = lootdb.open_log(keep.id, "Other", 2000.0)
    lootdb.add_events(b, [lootlog.Event(2001.0, "exp", 7)], kb)
    p.remove(c.id)
    assert _rows("SELECT character_id FROM logs") == [(keep.id,)]
    assert _rows("SELECT amount FROM events") == [(7,)]


# ------------------------------------------------------------ written by the running log

@pytest.fixture
def scanner(isolated_store, kb):
    from maplehelper.ui.lootscan import LootScanner
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 20)
    s = LootScanner(kb, p, isolated_store.Settings())
    yield s, p
    s._timer.stop()
    s._save_timer.stop()


def test_a_log_writes_a_pickup_once_no_read_can_change_it(scanner):
    """A pickup's value can still be corrected by the next reads of its line ("+28" read for "+26"): it goes in the
    database once that can't happen any more, with the corrected value; the log's end writes the rest."""
    s, p = scanner
    s.start()
    log_id = s._log.db_id
    assert _rows("SELECT character_id, character_name, ended FROM logs") == [(p.active.id, "Kiwi", None)]
    now = time.time()
    settled, fresh = lootlog.Event(now - 100, "mesos", 28), lootlog.Event(now, "exp", 21)
    s._log.add([settled, fresh])
    settled.amount = 26                     # a later read corrected it, before it settled
    s._save()
    assert _rows("SELECT kind, amount FROM events WHERE log_id = ?", log_id) == [("mesos", 26)]
    s.end()
    assert sorted(_rows("SELECT kind, amount FROM events WHERE log_id = ?", log_id)) == [("exp", 21), ("mesos", 26)]
    assert _rows("SELECT ended IS NOT NULL FROM logs WHERE id = ?", log_id) == [(1,)]
    assert lootdb.day(p.active.id, dt.date.today()).value == 26


def test_closing_the_app_writes_every_pickup_and_the_log_goes_on_after(scanner, kb):
    from maplehelper.ui.lootscan import LootScanner
    s, p = scanner
    s.start()
    s._log.add([lootlog.Event(time.time(), "exp", 8)])
    s.stop()
    assert _rows("SELECT amount FROM events") == [(8,)]
    again = LootScanner(kb, p, s._settings)                 # the next start: the same log, nothing written twice
    try:
        assert again.running(p.active.id) and again._log.db_id == s._log.db_id
        again.end()
        assert _rows("SELECT amount FROM events") == [(8,)]
    finally:
        again._timer.stop()


def test_a_log_from_before_the_database_gets_its_row_when_it_resumes(scanner, kb):
    """A log running in loot.json from before this version: on the next start it gets a row, with its pickups."""
    from maplehelper.ui.lootscan import LootScanner
    s, p = scanner
    old = lootlog.Log(start=time.time() - 600, events=[lootlog.Event(time.time() - 500, "mesos", 40)])
    s.store.put(p.active.id, old)
    again = LootScanner(kb, p, s._settings)
    try:
        assert again._log.db_id is not None
        assert _rows("SELECT kind, amount FROM events") == [("mesos", 40)]
    finally:
        again._timer.stop()
