"""Grind tracker: the session math (level-ups, closed inventory, missing reads), what's saved, and the page's flow."""
import json
import time
from pathlib import Path

import pytest

from maplehelper import grind, plan

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")

TABLE = {20: 1000, 21: 1200, 22: 1500, 23: 2000}          # level -> EXP to the next one
PRICES = {"Red Potion": (50, "COT2"), "Blue Potion": (220, "COT2")}


class FakeKB:
    def __init__(self, mesos=None):
        if mesos is not None:
            self.community_mesos = lambda key: mesos

    def monster_keys(self, name):
        return ["monster/1"] if name else []


@pytest.fixture
def math(monkeypatch):
    monkeypatch.setattr(plan, "exp_table", lambda kb: TABLE)
    monkeypatch.setattr(plan, "monster_exp", lambda kb, name: {"Horny Mushroom": 10}.get(name))
    monkeypatch.setattr(grind, "potion_price", lambda kb, name: PRICES.get(name))
    return FakeKB()


def read(minutes, level=None, pct=None, mesos=None, potions=None, monster="", t0=1_000_000.0):
    return grind.Reading(t0 + minutes * 60, level, pct, "Ant Tunnel I", monster, mesos, potions,
                         mesos is not None or potions is not None)


def session(*reads, monster=""):
    s = grind.Session(monster=monster)
    for r in reads:
        s.add(r)
    return s


def test_exp_on_one_level_and_per_hour(math):
    s = session(read(0, 21, 10.0), read(30, 21, 60.0))
    sm = grind.summarize(math, s, now=1_000_000 + 30 * 60)
    assert sm.exp == 600 and sm.exp_h == 1200          # half of level 21's 1,200 EXP in half an hour
    assert sm.pct_h == 100.0 and sm.to_level == 1440      # 40% of the level left at 100% an hour: 24 minutes
    assert sm.seconds == 1800


def test_exp_across_a_level_up_uses_the_table(math):
    # 90% of Lv. 20 (100 left) + all of Lv. 21 (1,200) + 10% of Lv. 22 (150)
    sm = grind.summarize(math, session(read(0, 20, 90.0), read(60, 22, 10.0)))
    assert sm.exp == 100 + 1200 + 150 and sm.exp_h == 1450
    assert (sm.level_from, sm.level_to) == (20, 22)


def test_one_read_no_gain_and_past_the_table(math):
    assert grind.summarize(math, session(read(0, 21, 10.0))).exp_note == "one_read"
    sm = grind.summarize(math, session(read(0, 21, 10.0), read(20, 21, 10.0)))
    assert sm.exp == 0 and sm.exp_note == "no_gain" and sm.exp_h is None
    assert grind.summarize(math, session(read(0, 23, 50.0), read(20, 24, 1.0))).exp_note == "no_table"
    # no EXP bar ever read: nothing about EXP, the rest still works
    sm = grind.summarize(math, session(read(0, mesos=100), read(30, mesos=400)))
    assert sm.exp is None and sm.exp_note == "no_exp" and sm.mesos == 300 and sm.mesos_h == 600


def test_one_misread_at_either_end_is_not_the_sessions_gain(math):
    """A level or a mesos sum misread at the first or last read (audit SCR-6)."""
    ok = (read(0, 21, 10.0, mesos=1_234_567), read(10, 21, 30.0, mesos=1_300_000), read(20, 21, 60.0, mesos=1_350_000))
    sm = grind.summarize(math, session(*ok))
    assert sm.exp == 600 and sm.mesos == 115_433
    # Lv. 12 dropped (600); the second's 70% is a fine read (720), only its mesos are off
    for bad_end, exp in ((read(30, 12, 70.0, mesos=11_350_000), 600), (read(30, 21, 70.0, mesos=135_000), 720)):
        sm = grind.summarize(math, session(*ok, bad_end))
        assert sm.exp == exp and sm.mesos == 115_433 and sm.level_to == 21
    sm = grind.summarize(math, session(read(0, 23, 10.0, mesos=12_345_670), *ok[1:]))     # the first read misread
    assert sm.exp == 360 and sm.mesos == 50_000 and sm.level_from == 21
    # a real level up still counts, two of them in half an hour too
    sm = grind.summarize(math, session(read(0, 20, 90.0), read(30, 22, 10.0)))
    assert sm.level_to == 22 and sm.exp


def test_a_real_big_change_mid_session_does_not_freeze_the_session(math):
    """A shop trip with most of the mesos, or two level-ups between reads, broke with the read before it and every
    later read was dropped: the session's gain froze (review PLT-3). The run after it counts; the trip doesn't."""
    sm = grind.summarize(math, session(*(read(m, mesos=v) for m, v in enumerate(
        (1_000_000, 1_010_000, 1_020_000, 150_000, 160_000, 170_000, 180_000)))))
    assert sm.mesos == 20_000 + 30_000
    # one misread in the middle still counts for nothing
    sm = grind.summarize(math, session(read(0, mesos=1_000_000), read(1, mesos=11_010_000), read(2, mesos=1_020_000)))
    assert sm.mesos == 20_000
    # Lv. 20 -> 22 a minute apart, then 22 -> 23: the levels after the jump are kept
    sm = grind.summarize(math, session(read(0, 20, 10.0), read(1, 20, 20.0), read(2, 22, 10.0), read(3, 22, 60.0),
                                       read(4, 23, 0.0)))
    assert sm.level_to == 23 and sm.exp == 100 + 1350       # 10% of Lv. 20, then Lv. 22 from 10% to Lv. 23
    assert sm.exp_h == round(sm.exp * 15)


def test_a_read_that_missed_the_exp_bar_keeps_the_last_one_that_had_it(math):
    s = session(read(0, 21, 10.0), read(30, 21, 60.0), read(40, None, None, mesos=5))
    sm = grind.summarize(math, s, now=1_000_000 + 40 * 60)
    assert sm.exp == 600 and sm.exp_h == 1200 and sm.seconds == 2400


def test_without_the_inventory_nothing_about_mesos_or_potions(math):
    sm = grind.summarize(math, session(read(0, 21, 10.0), read(30, 21, 60.0)))
    assert sm.mesos is None and sm.mesos_h is None and sm.net is None
    assert not sm.potions_read and sm.potions_cost is None


def test_inventory_opened_later_measures_from_that_read(math):
    s = session(read(0, 21, 10.0), read(20, 21, 30.0, mesos=10_000, potions={"Red Potion": 100}),
                read(50, 21, 60.0, mesos=13_000, potions={"Red Potion": 70}))
    sm = grind.summarize(math, s)
    assert sm.mesos == 3000 and sm.mesos_h == 6000         # over the 30 minutes between the two inventory reads
    assert sm.potions == [("Red Potion", 30, 50, "COT2")] and sm.potions_cost == 1500
    assert sm.net == 1500


def test_potions_used_restocked_gone_and_unpriced(math):
    s = session(read(0, 21, 10.0, mesos=0, potions={"Red Potion": 100, "Blue Potion": 20, "Mana Elixir": 5,
                                                    "White Potion": 3}),
                read(30, 21, 60.0, mesos=0, potions={"Red Potion": 150, "Mana Elixir": 2, "White Potion": 3}))
    sm = grind.summarize(math, s)
    assert sm.restocked == ["Red Potion"]                       # bought more: its use can't be told
    # Blue Potion's stack is gone (all 20 used); Mana Elixir has no shop price (left out of the cost)
    assert sm.potions == [("Blue Potion", 20, 220, "COT2"), ("Mana Elixir", 3, None, "")]
    assert sm.potions_cost == 20 * 220 and sm.net == -4400


def test_kills_are_an_estimate_from_the_monster_exp(math):
    s = session(read(0, 21, 10.0), read(30, 21, 60.0), monster="Horny Mushroom")
    sm = grind.summarize(math, s)
    assert sm.monster_exp == 10 and sm.kills == 60 and sm.kills_h == 120
    assert sm.expected is None                                  # no community mesos reports in this KB
    assert grind.summarize(math, session(read(0, 21, 10.0), read(30, 21, 60.0), monster="Nobody")).kills is None
    assert grind.summarize(math, session(read(0, 21, 10.0), read(30, 21, 60.0))).kills is None


@pytest.mark.parametrize("chance", [0.5, 50])          # a fraction or a percentage
def test_community_mesos_expected_from_the_kills(math, monkeypatch, chance):
    kb = FakeKB(mesos=(20, 40, chance, 12))
    sm = grind.summarize(kb, session(read(0, 21, 10.0), read(30, 21, 60.0), monster="Horny Mushroom"))
    assert sm.expected == 60 * 15 and sm.reports == 12         # 60 kills x 30 mesos x 50%
    broken = FakeKB(mesos=("a lot", None, None, 0))
    assert grind.summarize(broken, session(read(0, 21, 10.0), read(30, 21, 60.0), monster="Horny Mushroom")).expected \
        is None


def test_the_ai_reply_is_cleaned_never_guessed():
    r = grind.reading(5.0, {"level": "24", "exp_percent": 31.8},
                      {"map": "Victoria Road: Ant Tunnel I ", "monster": "Horny Mushroom", "inventory_open": True,
                       "mesos": "1,234,567", "potions": {"Red Potion": "120", "Blue Potion": 40.0, "": 3,
                                                         "Elixir": "lots", "White Potion": True}})
    assert (r.level, r.exp_pct, r.map, r.monster, r.mesos) == (24, 31.8, "Ant Tunnel I", "Horny Mushroom", 1_234_567)
    assert r.potions == {"Red Potion": 120, "Blue Potion": 40} and r.inventory
    closed = grind.reading(5.0, {"exp_percent": 140, "map": "Henesys"}, {"inventory_open": False, "potions": []})
    assert closed.exp_pct is None and closed.level is None and closed.map == "Henesys"
    assert closed.mesos is None and closed.potions is None and not closed.inventory
    assert grind.reading(5.0, None, None) == grind.Reading(5.0)
    # the Use tab read and showing no potion is a reading (all used up), not a missing one
    assert grind.reading(5.0, {}, {"inventory_open": True, "potions": {}}).potions == {}


def test_inventory_hint_names_only_recognised_potions(kb):
    from types import SimpleNamespace as S
    key = next(k for k, e in kb.entities.items() if e.get("category") == "item")
    assert grind.inventory_hint([S(index=1, status="ambiguous", matches=[(key, 1.0)])], kb) == ""
    assert grind.inventory_hint([], kb) == ""


# ---------------------------------------------------------------- saved per character

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    return grind.Store


def test_sessions_are_saved_per_character_and_survive_a_restart(store, math):
    st = store()
    st.start("a", read(0, 21, 10.0), "Horny Mushroom", picked=True)
    st.add("a", read(20, 21, 40.0, monster="Zombie Mushroom"))      # the player's pick stays
    st.add("b", read(1, 30, 1.0))                                     # no session for b: nothing happens
    again = store()
    s = again.running("a")
    assert s and len(s.reads) == 2 and s.monster == "Horny Mushroom" and s.picked
    assert again.session("b") is None
    rec = again.end("a", math, now=1_000_000 + 20 * 60)
    assert rec["exp"] == 360 and rec["exp_h"] == 1080 and rec["monster"] == "Horny Mushroom"
    assert store().recent("a") == [rec] and store().recent("b") == []
    assert store().running("a") is None and store().session("a").ended      # the ended one is still shown


def test_ai_monster_is_taken_until_the_player_picks_one(store):
    st = store()
    st.start("a", read(0, 21, 10.0, monster="Horny Mushroom"))
    assert st.running("a").monster == "Horny Mushroom"
    st.set_monster("a", "Zombie Mushroom")
    st.add("a", read(5, 21, 12.0, monster="Horny Mushroom"))
    assert st.running("a").monster == "Zombie Mushroom"
    st.set_monster("a", "")                          # cleared: the next read names it again
    st.add("a", read(9, 21, 14.0, monster="Horny Mushroom"))
    assert st.running("a").monster == "Horny Mushroom"


def test_recent_sessions_are_bounded_and_short_ones_skipped(store, math):
    st = store()
    for i in range(grind.MAX_RECENT + 5):
        t0 = 1_000_000.0 + i * 10_000
        st.start("a", read(0, 21, 10.0, t0=t0))
        st.add("a", read(10, 21, 20.0 + i, t0=t0))
        assert st.end("a", math, now=t0 + 600)
    recent = store().recent("a")
    assert len(recent) == grind.MAX_RECENT and recent[0]["start"] > recent[-1]["start"]     # newest first
    st.start("a", read(0, 21, 10.0))
    assert st.end("a", math, now=1_000_000 + 30) is None              # half a minute, one read: not saved
    assert len(store().recent("a")) == grind.MAX_RECENT
    st.forget("a")
    assert store().recent("a") == [] and store().session("a") is None


def test_a_damaged_file_or_session_is_dropped_not_fatal(store, tmp_path):
    (tmp_path / "grind.json").write_text("{not json", encoding="utf-8")
    assert store().recent("a") == []
    (tmp_path / "grind.json").write_text(json.dumps({"active": {"a": {"reads": [{"bogus": 1}, "x"]}},
                                                     "recent": {"a": ["x", {"exp_h": 5}]}}), encoding="utf-8")
    st = store()
    assert st.session("a") is None and st.recent("a") == [{"exp_h": 5}]


def test_deleting_a_character_deletes_its_sessions(isolated_store, store):
    p = isolated_store.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 24)
    store().start(c.id, read(0, 24, 1.0))
    p.remove(c.id)
    assert store().session(c.id) is None


@needs_kb
def test_potion_prices_come_from_the_shops_with_their_label():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    price, src = grind.potion_price(kb, "Orange Potion")
    assert price > 0 and src                              # the KB labels shop prices with a build ("COT2")
    assert grind.potion_price(kb, "orange pot") == (price, src)        # part of a name, as the AI may write it
    assert grind.potion_price(kb, "Snail Shell") is None                # not a Use item
    assert grind.is_potion(kb, kb._item_by_name["red potion"]) and not grind.is_potion(kb, kb._item_by_name["snail shell"])


# ---------------------------------------------------------------- the page

@needs_kb
def test_tracker_page_start_update_end(tmp_path, monkeypatch, store):
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store as st
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import PAGES, ToolsDialog
    monkeypatch.setattr(st.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(st.Settings, "path", tmp_path / "settings.json")
    p = st.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 24)
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, st.Settings(), "en", "", {}, "exp")
    monkeypatch.setattr(d, "_step_aside", lambda then: then())
    reads = []
    d.grind_sync_requested.connect(lambda: reads.append(1))
    asked = []
    d.ask_requested.connect(lambda q, shot: asked.append((q, shot)))
    assert d.stack.currentIndex() == PAGES.index("exp") and not d.grind_start.isHidden()
    assert "Start session" in d.grind_status.text()

    clock = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    # the inventory closed at the start: the session starts on EXP alone, and says how to measure mesos
    d.grind_start.click()
    assert reads == [1] and not d.grind_start.isEnabled() and "Reading" in d.grind_status.text()
    d.grind_read((c.id, {"level": 24, "exp_percent": 10.0}, {"map": "Ant Tunnel I", "monster": "Horny Mushroom"}))
    d.sync_done(True)
    assert d.grind_start.isHidden() and not d.grind_update.isHidden() and d.grind_update.isEnabled()
    assert "inventory was closed" in d.grind_status.text() and d.grind_monster.text() == "Horny Mushroom"
    # auto-update is on by default: the minute timer runs, and its line says when the numbers were read
    assert d.grind_auto.isChecked() and d.runner.timer.isActive() and "Updated" in d.grind_auto_state.text()
    d.runner.waiting = "no_game"
    d._fill_auto_state()
    assert "Waiting for the game" in d.grind_auto_state.text()
    d.runner.waiting = ""
    # an Update that couldn't read the game changes nothing
    clock[0] += 600
    d.grind_update.click()
    d.sync_done(False)
    assert "read the game" in d.grind_status.text() and len(d.grind.running(c.id).reads) == 1
    # an Update with the inventory open, then the End
    d.grind_update.click()
    d.grind_read((c.id, {"level": 24, "exp_percent": 30.0}, {"inventory_open": True, "mesos": 50_000,
                                                            "potions": {"Orange Potion": 100}}))
    d.sync_done(True)
    assert d.grind_cells["exp"][1].text() not in ("–", "") and d.grind_cells["mesos"][1].text() == "–"
    clock[0] += 1200
    d.grind_end.click()
    d.grind_read((c.id, {"level": 25, "exp_percent": 2.0}, {"inventory_open": True, "mesos": 62_000,
                                                           "potions": {"Orange Potion": 60}}))
    d.sync_done(True)
    assert not d.grind_start.isHidden() and d.grind_update.isHidden()
    assert "saved" in d.grind_status.text()
    rec = d.grind.recent(c.id)[0]
    assert rec["level_from"] == 24 and rec["level_to"] == 25 and rec["mesos"] == 12_000
    assert rec["kills"] and rec["potions_cost"] > 0 and rec["net"] == 12_000 - rec["potions_cost"]
    assert d.grind_table.count() > 4 and not d.grind_ask_row.isHidden()
    d._grind_ask()
    assert asked and "Ant Tunnel I (Horny Mushroom)" in asked[0][0] and asked[0][1] is False
    d.close()
    app.processEvents()


@needs_kb
def test_tracker_page_missing_reads_and_a_lost_read(tmp_path, monkeypatch, store):
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper import store as st
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import ToolsDialog
    monkeypatch.setattr(st.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(st.Settings, "path", tmp_path / "settings.json")
    p = st.Profiles()
    c = p.add("Kiwi", "Thief", "Assassin", 24)
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, st.Settings(), "he", "", None, "exp")
    monkeypatch.setattr(d, "_step_aside", lambda then: None)       # the read never comes back (window closed)
    # no EXP bar in the read: no session
    d._grind_read("start")
    d.grind_read((c.id, {"level": 24}, {"map": "Ant Tunnel I"}))
    d.sync_done(True)
    assert d.grind.session(c.id) is None and "EXP" in d.grind_status.text()
    # a read lost on the way keeps the buttons off only for a while
    d._grind_read("start")
    assert not d.grind_start.isEnabled()
    r = d.runner
    r.pending = r.pending[:2] + (time.time() - r.READ_WAIT - 1,) + r.pending[3:]
    d.refresh("exp")
    assert d.grind_start.isEnabled() and r.pending is None
    # another character's read is never taken
    d._grind_read("start")
    d.grind_read(("someone-else", {"level": 50, "exp_percent": 5.0}, {}))
    d.sync_done(True)
    assert d.grind.session(c.id) is None            # the profile has no EXP % either: nothing to start from
    d.close()
    app.processEvents()


def test_a_grind_read_asks_for_the_inventory_and_hands_the_reply_over(isolated_store, kb, monkeypatch):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication
    from maplehelper.brain import Answer
    from maplehelper.ui import overlay

    qapp = QApplication.instance() or QApplication([])
    profiles = isolated_store.Profiles()
    c = profiles.add("Kiwi", "Thief", "Assassin", 24)
    brain = Mock()
    brain.ask.return_value = Answer(profile_update={"exp_percent": 31.8},
                                    grind={"inventory_open": True, "mesos": 5000, "map": "Ant Tunnel I"})
    win = overlay.Overlay(isolated_store.Settings(), profiles, kb, brain)
    win.add_system = Mock()
    win.refresh_profile_chip = Mock()
    win.profile_card.set_busy = Mock()
    got, finished = [], []
    win.grind_read.connect(got.append)
    win.sync_finished.connect(finished.append)
    monkeypatch.setattr(overlay.osapi, "find_game_window", lambda: 123)
    monkeypatch.setattr(overlay.osapi, "capture_game", lambda hwnd: b"screenshot")
    try:
        win.sync_profile(grind=True)
        deadline = time.monotonic() + 5
        while not finished and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert '"grind"' in brain.ask.call_args.args[0] and "mesos" in brain.ask.call_args.args[0]
        assert got == [(c.id, {"exp_percent": 31.8}, {"inventory_open": True, "mesos": 5000, "map": "Ant Tunnel I"})]
        assert finished == [True] and profiles.active.exp_pct == 31.8
        # the chat's own refresh (the card's ⟳) is a plain read: no grind question, nothing handed over
        got.clear()
        finished.clear()
        win.sync_profile()
        deadline = time.monotonic() + 5
        while not finished and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert '"grind"' not in brain.ask.call_args.args[0] and got == []
    finally:
        from shiboken6 import isValid
        thread = getattr(win, "_sync_thread", None)
        if thread is not None and isValid(thread):
            thread.quit()
            thread.wait(2000)
        win.deleteLater()


def test_the_meta_grind_object_must_be_an_object():
    from maplehelper.brain import split_meta
    assert split_meta('Read.\n@@META@@ {"grind": {"mesos": 5}}')[1]["grind"] == {"mesos": 5}
    assert "grind" not in split_meta('Read.\n@@META@@ {"grind": [5]}')[1]


# ---------------------------------------------------------------- auto-update every minute

@pytest.fixture
def runner(isolated_store, math):
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui.grindrunner import GrindRunner
    QApplication.instance() or QApplication([])
    profiles = isolated_store.Profiles()
    c = profiles.add("Kiwi", "Thief", "Assassin", 21)
    r = GrindRunner(math, profiles, isolated_store.Settings())
    asked = []
    r.auto_requested.connect(lambda: asked.append(1))
    r.asked, r.cid = asked, c.id
    yield r
    r.stop()


def test_the_minute_timer_runs_only_with_a_session_and_auto_on(runner, isolated_store):
    r = runner
    r.sync()
    assert not r.timer.isActive()                        # no session
    assert r.ask("start", r.cid) == "read"
    r.grind_read((r.cid, {"level": 21, "exp_percent": 10.0}, {}))
    r.sync_done(True)
    assert r.store.running(r.cid) and r.timer.isActive() and r.timer.interval() == 60_000
    r.set_auto(False)
    assert not r.timer.isActive() and isolated_store.Settings()["grind_auto"] is False     # remembered
    r.set_auto(True)
    assert r.timer.isActive() and isolated_store.Settings()["grind_auto"] is True
    # another character: its own session (none), so no reads
    other = r.profiles.add("Mango", "Warrior", "Fighter", 30)
    r.sync()
    assert not r.timer.isActive()
    r.profiles.set_active(r.cid)
    r.sync()
    assert r.timer.isActive()
    assert r.ask("end", r.cid) == "read"
    r.sync_done(False)
    assert r.store.running(r.cid) is None and not r.timer.isActive()
    assert other.id != r.cid


def test_a_tick_reads_quietly_and_skips_while_a_read_runs(runner):
    r = runner
    r.store.start(r.cid, grind.Reading(time.time(), 21, 10.0))
    r.sync()
    r._tick()
    assert r.asked == [1] and r.busy()[0] == "auto"
    r._tick()                                             # the last read is still on its way: skipped, not queued
    assert r.asked == [1]
    # Update pressed meanwhile rides on that read: no second screenshot
    assert r.ask("update", r.cid) == "joined" and r.busy()[0] == "update"
    r.grind_read((r.cid, {"level": 21, "exp_percent": 20.0}, {}))
    r.sync_done(True)
    assert len(r.store.running(r.cid).reads) == 2 and r.note == ["grind_updated", "grind_no_inv"]
    # an automatic read adds what it read and says nothing
    r._tick()
    r.grind_read((r.cid, {"level": 21, "exp_percent": 30.0}, {}))
    r.sync_done(True)
    assert len(r.store.running(r.cid).reads) == 3 and r.note == ["grind_updated", "grind_no_inv"]
    # one that failed or read nothing adds nothing
    r._tick()
    r.sync_done(False)
    r._tick()
    r.grind_read((r.cid, {}, {}))
    r.sync_done(True)
    assert len(r.store.running(r.cid).reads) == 3 and r.busy() is None


def test_a_tick_with_no_game_waits_quietly(runner):
    r = runner
    r.store.start(r.cid, grind.Reading(time.time(), 21, 10.0))
    r.sync()
    for reason in ("no_game", "covered"):
        r._tick()
        r.skipped(reason)
        assert r.busy() is None and r.waiting == reason
    r._tick()
    r.skipped("busy")                                     # the chat was answering: the next minute tries again
    assert r.busy() is None and r.waiting == "covered" and r.timer.isActive()
    r._tick()
    r.grind_read((r.cid, {"level": 21, "exp_percent": 12.0}, {}))
    r.sync_done(True)
    assert r.waiting == ""


def test_a_session_left_idle_does_not_start_reading_by_itself(runner):
    r = runner
    r.store.start(r.cid, grind.Reading(time.time() - r.IDLE - 60, 21, 10.0))
    r.sync()
    assert not r.timer.isActive() and r.idle(r.store.running(r.cid))
    assert r.ask("update", r.cid) == "read"               # Update resumes it
    r.grind_read((r.cid, {"level": 21, "exp_percent": 12.0}, {}))
    r.sync_done(True)
    assert r.timer.isActive()


@pytest.mark.parametrize("case", ["busy", "modal", "no_game", "covered", "read"])
def test_the_chat_auto_read_skips_or_reads_quietly(isolated_store, kb, monkeypatch, case):
    from unittest.mock import Mock

    from PySide6.QtWidgets import QApplication
    from maplehelper import capture
    from maplehelper.brain import Answer
    from maplehelper.ui import overlay

    qapp = QApplication.instance() or QApplication([])
    profiles = isolated_store.Profiles()
    profiles.add("Kiwi", "Thief", "Assassin", 24)
    brain = Mock()
    brain._provider.saver_model = "haiku"
    brain.ask.return_value = Answer(profile_update={"exp_percent": 40.0}, grind={"map": "Ant Tunnel I"})
    win = overlay.Overlay(isolated_store.Settings(), profiles, kb, brain)
    win.add_system = Mock()
    win._update_avatar = Mock()
    skipped, got, finished = [], [], []
    win.grind_skipped.connect(skipped.append)
    win.grind_read.connect(got.append)
    win.sync_finished.connect(finished.append)
    monkeypatch.setattr(overlay.osapi, "find_game_window", lambda: None if case == "no_game" else 123)
    monkeypatch.setattr(overlay.osapi, "window_rect", lambda hwnd: (5000, 5000, 800, 600))   # beside our windows

    def grab(hwnd):
        capture.LAST_PROBLEM = "covered" if case == "covered" else None
        return None if case == "covered" else b"screenshot"
    monkeypatch.setattr(overlay.osapi, "capture_game", grab)
    win.setWindowOpacity(1.0)
    if case == "busy":
        win.busy = True
    dialog = None
    if case == "modal":            # Edit character open: hiding it would cancel its exec() (audit OVL-1)
        from PySide6.QtWidgets import QDialog
        dialog = QDialog()
        dialog.setModal(True)
        dialog.show()
    try:
        win.auto_grind_read()
        deadline = time.monotonic() + 5
        while not (skipped or finished) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        win.add_system.assert_not_called()                # no chat line a minute, whatever happened
        win._update_avatar.assert_not_called()
        assert win.windowOpacity() == 1.0 and not getattr(win, "_syncing", False)
        if case == "read":
            assert skipped == [] and finished == [True] and got[0][1] == {"exp_percent": 40.0}
            assert brain.ask.call_args.kwargs.get("model") is None and brain.ask.call_args.kwargs["light"]
        else:
            assert skipped == ["busy" if case == "modal" else case] and finished == [] and not brain.ask.called
        if dialog is not None:
            assert dialog.isVisible()
    finally:
        if dialog is not None:
            dialog.close()
        from shiboken6 import isValid
        thread = getattr(win, "_sync_thread", None)
        if thread is not None and isValid(thread):
            thread.quit()
            thread.wait(2000)
        win.deleteLater()


def test_windows_over_maps_each_screen_from_its_own_origin(monkeypatch):
    """A window on a scaled second monitor: native = origin + (logical - origin) * ratio (audit SCR-4)."""
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui import overlay
    monkeypatch.setattr(overlay.osapi, "SCREEN_COORDS_ARE_PHYSICAL", True)
    screen = SimpleNamespace(geometry=lambda: QRect(2560, 0, 1707, 960), devicePixelRatio=lambda: 1.5)
    win = SimpleNamespace(isVisible=lambda: True, windowOpacity=lambda: 1.0, isMinimized=lambda: False,
                          frameGeometry=lambda: QRect(QPoint(2627, 100), QPoint(2627 + 299, 299)), screen=lambda: screen)
    monkeypatch.setattr(QApplication, "topLevelWidgets", staticmethod(lambda: [win]))
    assert overlay.windows_over((2600, 0, 400, 400)) == [win]        # native x 2660: over the game there
    assert overlay.windows_over((3900, 0, 400, 400)) == []           # not where logical * 1.5 put it


def test_windows_over_the_game_step_aside(monkeypatch):
    from PySide6.QtWidgets import QApplication, QWidget

    from maplehelper.ui import overlay
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(overlay.osapi, "SCREEN_COORDS_ARE_PHYSICAL", False)
    w = QWidget()
    w.setGeometry(100, 100, 300, 200)
    w.show()
    try:
        assert w in overlay.windows_over((0, 0, 1000, 1000))
        assert w not in overlay.windows_over((2000, 0, 800, 600))
        w.hide()
        overlay.show_quietly(w)
        assert w.isVisible() and not w.testAttribute(overlay.Qt.WA_ShowWithoutActivating)
    finally:
        w.close()


def test_the_same_level_misread_twice_starts_no_run(math):
    """PLT-3's runs let two identical misreads in a row ("35" as "53" while a tooltip covers the HUD) start a run of
    their own: Lv 35 -> 53 and EXP/h a quarter too high (review2 LOG-1). A new EXP run starts only at the same or a
    little higher level."""
    clean = grind.summarize(math, session(read(0, 21, 10.0), read(10, 21, 30.0), read(20, 21, 50.0),
                                          read(30, 21, 60.0)))
    for bad in ((read(10, 12, 30.0), read(15, 12, 40.0)),):
        middle = grind.summarize(math, session(read(0, 21, 10.0), read(5, 21, 20.0), *bad, read(20, 21, 50.0),
                                               read(30, 21, 60.0)))
        end = grind.summarize(math, session(read(0, 21, 10.0), read(10, 21, 30.0), read(20, 21, 50.0),
                                            read(30, 21, 60.0), read(31, 12, 61.0), read(32, 12, 62.0)))
        assert middle.exp == clean.exp == end.exp == 600 and middle.level_to == end.level_to == 21
