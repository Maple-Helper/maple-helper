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
    meter = {}
    d = ToolsDialog(KnowledgeBase(REAL_KB), p, st.Settings(), "he", "", meter, "exp")
    monkeypatch.setattr(d, "_step_aside", lambda then: None)       # the read never comes back (window closed)
    # no EXP bar in the read: no session
    d._grind_read("start")
    d.grind_read((c.id, {"level": 24}, {"map": "Ant Tunnel I"}))
    d.sync_done(True)
    assert d.grind.session(c.id) is None and "EXP" in d.grind_status.text()
    # a read lost on the way keeps the buttons off only for a while
    d._grind_read("start")
    assert not d.grind_start.isEnabled()
    meter["pending"] = meter["pending"][:2] + (time.time() - d.READ_WAIT - 1,)
    d.refresh("exp")
    assert d.grind_start.isEnabled() and "pending" not in meter
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
