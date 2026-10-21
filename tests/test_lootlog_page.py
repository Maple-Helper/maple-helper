"""The grind tracker's window (ui/grindtracker.py) and its card over the game (ui/lootoverlay.py): Start / Stop /
Reset, the summary table so far and per hour, the messages area, and the reads that follow the game (ui/lootscan.py)."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import lootlog  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture
def page(isolated_store, kb):
    from maplehelper.ui.grindtracker import GrindTracker
    from maplehelper.ui.lootscan import LootScanner
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 21)
    s = isolated_store.Settings()
    scan = LootScanner(kb, p, s)
    d = GrindTracker(scan, kb, s, "en", "")
    yield d, s
    scan.stop()
    d.close()


def _text(d) -> str:
    return " | ".join(d.lines())


def test_a_running_session_shows_its_table_and_ends_into_the_history(page):
    d, _ = page
    scan = d.scanner
    d.start_btn.click()
    assert scan.running(scan.cid()) and d.stop_btn.isVisibleTo(d) and not d.start_btn.isVisibleTo(d)
    scan._log.add([lootlog.Event(1.0, "exp", 21), lootlog.Event(2.0, "mesos", 27),
                   lootlog.Event(3.0, "item", 2, "Leaf", "item/358")])
    scan.changed.emit()
    text = _text(d)
    for word in ("So far", "Per hour (est.)", "Qty", "Value", "EXP", "Mesos", "HP potions", "MP potions",
                 "Total income", "Spent on potions", "Net profit", "ETC you picked up", "Leaf"):
        assert word in text, word
    d.stop_btn.click()
    assert not scan.running(scan.cid()) and scan.store.recent(scan.cid())[0]["exp"] == 21
    assert "saved in the History" in d.state.text()
    d.show_tab("history")
    assert "EXP" in _text(d) and "Details" in _text(d)


def test_a_session_that_counted_nothing_is_not_kept(page):
    """Reset, then Stop at once: no empty session in the history, and the window says it isn't tracking."""
    from maplehelper import lootdb
    d, _ = page
    scan = d.scanner
    d.start_btn.click()
    scan._log.add([lootlog.Event(1.0, "mesos", 50)])
    d.reset_btn.click()
    d.stop_btn.click()
    assert [s.mesos for s in lootdb.sessions(scan.cid())] == [50]
    assert scan.log_for(scan.cid()) is None and "Not tracking" in d.state.text()


def test_the_card_over_the_game_follows_the_log_and_its_switch(page, kb):
    """Its own window over the game, at the other windows' opacity: a log starting opens it, its ✕ hides it and the
    tracker's switch follows, and a stopped session stays in it to read."""
    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    s["npc_overlay_opacity"] = 0.5
    card = LootOverlay(d.scanner, s, kb, I18n("en"))
    d.scanner.changed.connect(card.sync)
    try:
        assert card.isWindow() and card.parent() is None and not card.isVisible()
        d.start_btn.click()
        assert card.isVisible() and card._opacity == 0.5 and s["loot_overlay"] is True
        # the live tracking is the card's (not the full tracker's), there from the start
        assert "Live tracking" in card.lines() and any("the moment it's picked up" in x for x in card.lines())
        assert "Live tracking" not in _text(d)
        d.scanner._log.add([lootlog.Event(1.0, "mesos", 1234), lootlog.Event(2.0, "item", 1, "Leaf", "item/358")])
        d.scanner.changed.emit()
        texts = card.lines()
        assert any("1,234" in x for x in texts) and any("Leaf" in x for x in texts)
        assert not any("the moment it's picked up" in x for x in texts)
        # the pickup's worth at the better price, tagged NPC or FM; Stop is red, here and in the full tracker
        assert any("Leaf" in x and (">NPC</span>" in x or ">FM</span>" in x) for x in texts)
        assert card._stop.objectName() == "Stop" and d.stop_btn.objectName() == "TrkStop"
        assert any(x.startswith("Net") for x in texts)
        card._close.click()
        assert not card.isVisible() and s["loot_overlay"] is False
        assert d.scanner.running(d.scanner.cid())          # hiding the window never stops the log
        s["loot_overlay"] = True                           # (the bar's Grind tracker button)
        card.sync()
        assert card.isVisible()
        d.stop_btn.click()
        assert card.isVisible() and any("1,234" in x for x in card.lines())
        assert any("saved in the History" in x for x in card.lines())
    finally:
        card.close()


def test_the_cards_buttons_start_stop_reset_fold_and_open_the_full_tracker(page, kb):
    """Start when no log runs; Stop and Reset while one does (Reset: the session so far saved, a new one from zero);
    the – folds it to its title row, with what the session made in the title; "The full tracker" asks for the
    window. Its – and ✕ are the app's own icon buttons."""
    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    scan = d.scanner
    card = LootOverlay(scan, s, kb, I18n("en"))
    scan.changed.connect(card.sync)
    s["loot_overlay"] = True
    card.sync()
    full = []
    card.full_requested.connect(lambda: full.append(True))
    try:
        assert card._start.isVisibleTo(card) and not card._stop.isVisibleTo(card)
        assert (card._fold.objectName(), card._close.objectName()) == ("Icon", "IconClose")
        assert card._full.text() == "The full tracker"
        card._start.click()
        assert scan.running(scan.cid()) and card._stop.isVisibleTo(card) and card._reset.isVisibleTo(card)
        scan._log.add([lootlog.Event(1.0, "mesos", 500)])
        card._reset.click()
        assert scan.running(scan.cid()) and scan._log.totals()[1] == 0
        assert scan.store.recent(scan.cid())[0]["mesos"] == 500
        scan._log.add([lootlog.Event(2.0, "mesos", 70)])
        card.refresh()
        height = card.height()
        card._fold.click()
        assert s["loot_overlay_folded"] is True and card.height() < height and not card._body.isVisibleTo(card)
        assert "70" in card._title.text()
        card._fold.click()
        assert s["loot_overlay_folded"] is False and card.height() == height
        card._full.click()
        assert full == [True]
        card._stop.click()
        assert not scan.running(scan.cid()) and card._start.isVisibleTo(card) and card.isVisible()
    finally:
        card.close()


def test_reads_follow_the_game_window_and_skip_a_covered_one(page, monkeypatch):
    """No box drawn: each read takes the messages box from the game's window as it is now (moved, resized), and
    reads nothing while another app's window covers it (its text was read live), saying so in the window."""
    from maplehelper import capture
    from maplehelper.ui import lootscan
    d, _ = page
    scan = d.scanner
    grabbed, cover = [], [False]
    windows = iter([(0, 0, 1922, 1112), (500, 300, 1368, 800), (500, 300, 1368, 800)])
    monkeypatch.setattr(lootscan.GameArea, "find", lambda self: next(windows))
    monkeypatch.setattr(lootscan.GameArea, "covered", lambda self, box: cover[0])
    monkeypatch.setattr(capture, "grab_image", lambda x, y, w, h: grabbed.append((x, y, w, h)) or
                        __import__("PIL.Image", fromlist=["Image"]).new("RGB", (w, h)))
    monkeypatch.setattr(lootlog, "ocr_rows", lambda arr: [])
    got = []
    scan._read_done.disconnect()
    scan._read_done.connect(lambda rows, at, read: (got.append(read), scan._deliver(rows, at, read)))
    scan.start()
    scan._read(None)
    scan._read(None)
    assert grabbed == [tuple(lootlog.auto_box((0, 0, 1922, 1112)).values()),
                       tuple(lootlog.auto_box((500, 300, 1368, 800)).values())]
    cover[0] = True
    scan._read(None)
    assert len(grabbed) == 2 and got[-1][1] == "covered" and scan.state == "covered"
    d.refresh()
    assert "covers the game" in d.now_status.text()
    scan.end()
