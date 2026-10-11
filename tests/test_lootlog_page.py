"""The Loot log tab: the messages area, Start / End, and the totals of the running log (ui/tools.py, ui/lootscan.py);
and its window over the game: the state chip, the three tiles, the items and the latest pickups (ui/lootoverlay.py)."""
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import lootlog  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
# five minutes of live pickups (test_lootlog's loot_reads_live.json): its EXP, mesos and items
LIVE_EXP, LIVE_MESOS = 358, 327
LIVE_ITEMS = (("Firewood", 5), ("Snail Shell", 2), ("Tree Branch", 2), ("Leaf", 2), ("Red Snail Shell", 1))
MARKS = dict.fromkeys(map(ord, "\u2066\u2069\u200e\u200f\u202a\u202c"))     # bidi marks aside


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture
def page(isolated_store, kb):
    from maplehelper.ui.tools import ToolsDialog
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 21)
    s = isolated_store.Settings()
    d = ToolsDialog(kb, p, s, "en", "", {}, "loot")
    yield d, s
    d.loot.stop()
    d.close()


def _cell(d, key):
    return d.loot_cells[key][1].text()


def test_the_area_button_asks_the_app_to_draw_the_box(page):
    d, _ = page
    asked = []
    d.loot_area_requested.connect(lambda: asked.append(True))
    d.loot_pick.click()
    assert asked == [True]


def test_the_messages_are_found_on_their_own_unless_a_box_was_drawn(page):
    """Start needs no box; a drawn one shows "Find it on its own", which forgets it."""
    d, s = page
    d.loot_changed()
    assert d.loot_start.isEnabled() and not d.loot_auto.isVisibleTo(d)
    s["loot_region"] = {"x": 0, "y": 0, "w": 400, "h": 260}
    d.loot_changed()
    assert d.loot_auto.isVisibleTo(d) and d.loot.region() is not None
    d.loot_auto.click()
    assert s["loot_region"] is None and d.loot.region() is None and not d.loot_auto.isVisibleTo(d)


def test_a_running_log_shows_its_totals_and_ends_into_recent(page):
    d, s = page
    d.loot_changed()
    d.loot_start.click()
    assert d.loot.running(d.c.id) and d.loot_end.isVisibleTo(d) and not d.loot_start.isVisibleTo(d)
    d.loot._log.add([lootlog.Event(1.0, "exp", 21), lootlog.Event(2.0, "mesos", 27),
                     lootlog.Event(3.0, "item", 2, "Leaf", "item/358")])
    d.loot.changed.emit()
    assert _cell(d, "exp") == "21" and _cell(d, "mesos") == "27" and _cell(d, "picked") == "2"
    assert any("Leaf" in w.text() for w in d.findChildren(type(d.loot_state)) if w.isVisibleTo(d))
    d.loot_end.click()
    assert not d.loot.running(d.c.id) and d.loot.store.recent(d.c.id)[0]["exp"] == 21


def test_the_card_over_the_game_follows_the_log_and_its_switch(page, kb):
    """Its own window over the game, at the other windows' opacity: a log starting opens it, its ✕ hides it and the
    page's switch follows, and a stopped log stays in it to read."""
    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    s["npc_overlay_opacity"] = 0.5
    card = LootOverlay(d.loot, s, kb)
    d.loot.changed.connect(card.sync)
    try:
        assert card.isWindow() and card.parent() is None and not card.isVisible()
        d.loot_start.click()
        assert card.isVisible() and card._opacity == 0.5 and s["loot_overlay"] is True
        d.loot._log.add([lootlog.Event(1.0, "mesos", 1234), lootlog.Event(2.0, "item", 1, "Leaf", "item/358")])
        d.loot.changed.emit()
        texts = card.lines()
        assert any("1,234" in x for x in texts) and any("Leaf" in x for x in texts)
        card._close.click()
        assert not card.isVisible() and s["loot_overlay"] is False and not d.loot_overlay_switch.isChecked()
        assert d.loot.running(d.c.id)                    # hiding the window never stops the log
        d.loot_overlay_switch.setChecked(True)
        assert card.isVisible()
        d.loot_end.click()
        assert card.isVisible() and any("1,234" in x for x in card.lines())
    finally:
        card.close()


def test_the_windows_buttons_start_stop_reset_and_fold(page, kb):
    """Start when no log runs; Stop and Reset while one does (Reset: the log so far saved, a new one from zero);
    the – folds it to its title row, with what the log made in the title, and the fold is kept."""
    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    card = LootOverlay(d.loot, s, kb)
    d.loot.changed.connect(card.sync)
    s["loot_overlay"] = True
    card.sync()
    try:
        assert card._start.isVisibleTo(card) and not card._stop.isVisibleTo(card)
        card._start.click()
        assert d.loot.running(d.c.id) and card._stop.isVisibleTo(card) and card._reset.isVisibleTo(card)
        d.loot._log.add([lootlog.Event(1.0, "mesos", 500)])
        card._reset.click()
        assert d.loot.running(d.c.id) and d.loot._log.totals()[1] == 0
        assert d.loot.store.recent(d.c.id)[0]["mesos"] == 500
        d.loot._log.add([lootlog.Event(2.0, "mesos", 70)])
        card.refresh()
        full = card.height()
        card._fold.click()
        assert s["loot_overlay_folded"] is True and card.height() < full and not card._body.isVisibleTo(card)
        assert "70" in card._title.text()
        card._fold.click()
        assert s["loot_overlay_folded"] is False and card.height() == full
        card._stop.click()
        assert not d.loot.running(d.c.id) and card._start.isVisibleTo(card) and card.isVisible()
    finally:
        card.close()


@pytest.fixture
def card(page, kb):
    """The window over the game in English, following the page's scanner as the app wires it."""
    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    w = LootOverlay(d.loot, s, kb, I18n("en"))
    d.loot.changed.connect(w.sync)
    yield w
    d.loot.changed.disconnect(w.sync)
    _delete(w)


def _delete(w) -> None:
    """Closed and gone now (its 5 s clock with it): deleteLater alone waits for an event loop the tests never run."""
    from PySide6.QtCore import QEvent
    w.close()
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _key(kb, name: str) -> str:
    return lootlog.item_names(kb)[name.lower()]


def _live_log(scan, kb, now: float, minutes: int = 30) -> None:
    """The running log as the live session counted it, started `minutes` ago: per hour is then 60 / minutes times
    each total."""
    scan._log.start = now - minutes * 60
    scan._log.add([lootlog.Event(now - 300, "exp", LIVE_EXP), lootlog.Event(now - 290, "mesos", LIVE_MESOS)]
                  + [lootlog.Event(now - 280 + i, "item", n, name, _key(kb, name))
                     for i, (name, n) in enumerate(LIVE_ITEMS)])


def _shown(card, rows) -> list:
    return [r for r in rows if r.isVisibleTo(card)]


def test_the_windows_tiles_give_exp_mesos_and_the_items_npc_value_each_per_hour(page, card, kb):
    """EXP, mesos and what an NPC pays for the items (not the mesos again: the summary's value counts them), each
    with its per-hour figure; under a minute there is no rate yet."""
    d, _ = page
    d.loot_start.click()
    now = time.time()
    _live_log(d.loot, kb, now)
    card.refresh()
    sm = lootlog.summarize(kb, d.loot._log)
    worth = sum(n * (price or 0) for _, n, _, price in sm.items)
    assert worth and sm.value == worth + LIVE_MESOS
    tiles = {k: (name.text(), value.text(), rate.text()) for k, (_tile, name, value, rate) in card._tiles.items()}
    assert tiles == {"exp": ("EXP", "358", "716/h"), "mesos": ("Mesos", "327", "654/h"),
                     "items": ("Items value", f"{worth:,}", f"{2 * worth:,}/h")}
    d.loot._log.start = time.time() - 30
    card.refresh()
    assert [rate.text() for _tile, _name, _value, rate in card._tiles.values()] == ["–/h"] * 3
    assert card._tiles["exp"][3].toolTip() == I18n("en")("loot_tip_wait")


def test_the_windows_items_show_their_pictures_counts_and_npc_value_in_place(page, card, kb):
    """Most picked first, each with its picture, a count badge and what an NPC pays for them; a refresh fills the
    same rows in place (a picture only changes with the item), more items than rows end in "and N more items", and
    an item the database doesn't know shows "?" with no price."""
    from maplehelper import farm
    d, _ = page
    d.loot_start.click()
    now = time.time()
    _live_log(d.loot, kb, now)
    card.refresh()
    rows = _shown(card, card._rows)
    assert [(r.name.full(), r.badge.text()) for r in rows] == [
        ("Firewood", "×5"), ("Leaf", "×2"), ("Snail Shell", "×2"), ("Tree Branch", "×2"), ("Red Snail Shell", "×1")]
    assert all(not r.pic.pixmap().isNull() for r in rows)
    assert rows[0].worth.text() == f"{5 * farm.value(kb, _key(kb, 'Firewood')).price:,}"
    assert not card._more.isVisibleTo(card)
    pictures = [r.pic.pixmap().cacheKey() for r in rows]
    d.loot._log.add([lootlog.Event(now, "item", 1, "Leaf", _key(kb, "Leaf"))])
    card.refresh()
    assert _shown(card, card._rows) == rows and [r.pic.pixmap().cacheKey() for r in rows] == pictures
    assert rows[1].badge.text() == "×3"
    d.loot._log.add([lootlog.Event(now, "item", 9, "Srail Shel", "")])      # a name read in part
    card.refresh()
    top = card._rows[0]
    assert (top.name.full(), top.badge.text(), top.pic.text(), top.worth.text()) == ("Srail Shel", "×9", "?", "–")
    assert top.pic.pixmap().isNull()
    assert card._more.isVisibleTo(card) and card._more.text() == "and 1 more item"
    assert card._more.toolTip() == "Red Snail Shell ×1"
    from maplehelper.ui.lootoverlay import LootOverlay
    he = LootOverlay(d.loot, card.settings, kb, I18n("he"))
    try:
        he.refresh()
        badges = [r.badge.text().translate(MARKS) for r in _shown(he, he._rows)]
        assert badges[:2] == ["9 יח'", "5 יח'"] and not any("×" in b for b in badges)  # no "×5" beside a Hebrew word
        feed = [r.text.full().translate(MARKS) for r in _shown(he, he._feed)]
        assert feed[1] == "Leaf · יחידה אחת" and not any("1 יחידות" in x for x in feed)     # one is "one"
    finally:
        _delete(he)


def test_the_windows_latest_pickups_are_newest_first_with_a_picture_each(page, card, kb):
    """Newest on top, each with the item's picture (the mesos' coin, the EXP's sparkle) and how long ago; older
    lines fainter."""
    d, _ = page
    d.loot_start.click()
    now = time.time()
    d.loot._log.add([lootlog.Event(now - 150, "exp", 21), lootlog.Event(now - 40, "item", 1, "Leaf", _key(kb, "Leaf")),
                     lootlog.Event(now - 20, "mesos", 27), lootlog.Event(now - 1, "item", 2, "Firewood",
                                                                         _key(kb, "Firewood"))])
    card.refresh()
    feed = _shown(card, card._feed)
    assert [r.text.full() for r in feed] == ["Firewood ×2", "+27 mesos", "Leaf ×1", "+21 EXP"]
    assert feed[0].ago.text() == "just now" and feed[1].ago.text().endswith(" s ago")
    assert feed[3].ago.text() == "2 min ago"
    assert all(not r.icon.pixmap().isNull() for r in feed)
    alpha = [r.text.palette().color(QPalette.WindowText).alphaF() for r in feed]
    assert alpha == sorted(alpha, reverse=True) and alpha[0] > alpha[-1]
    d.loot._log.add([lootlog.Event(time.time(), "mesos", 5)])
    card.refresh()
    assert [r.text.full() for r in _shown(card, card._feed)] == ["+5 mesos", "Firewood ×2", "+27 mesos", "Leaf ×1"]


def test_the_windows_state_chip_follows_start_stop_and_the_accent(page, card):
    """● Recording in the accent while the log runs (Start lights in it too, Stop is red), Stopped muted after, the
    reason nothing counts while the game is covered; folded, the title keeps what the log made, with the accent's dot
    while it records."""
    from maplehelper.ui import npcoverlay
    from maplehelper.ui.lootoverlay import STOP
    d, s = page
    s["loot_overlay"] = True
    card.sync()
    assert card._chip_off.isVisibleTo(card) and card._chip_off.text() == "The log isn't running"
    assert not card._chip_on.isVisibleTo(card) and not card._elapsed.isVisibleTo(card)
    card._start.click()
    assert card._chip_on.isVisibleTo(card) and card._chip_on.text() == "● Recording"
    assert not card._chip_off.isVisibleTo(card) and card._elapsed.text() == "0 s"
    s["overlay_accent"] = "blue"
    card.refresh()
    blue = npcoverlay.ACCENTS["blue"][1]
    sheet = card.styleSheet()
    assert f"#ChipOn {{ color: {blue};" in sheet and f"QPushButton#Go {{ background: {blue};" in sheet
    assert card._stop.objectName() == "Stop" and f"QPushButton#Stop {{ background: rgba(255,138,128,0.16); color: {STOP}; }}" in sheet
    d.loot.state = "covered"
    card.refresh()
    assert card._status.isVisibleTo(card) and "covers the game" in card._status.text()
    d.loot.state = "ok"
    d.loot._log.add([lootlog.Event(time.time(), "mesos", 70)])
    card._fold.click()
    assert card._dot.isVisibleTo(card) and card._title.text() == "Loot Log · 70 made"
    card._fold.click()
    card._stop.click()
    assert card._chip_off.isVisibleTo(card) and card._chip_off.text() == "■ Stopped" and not card._chip_on.isVisibleTo(card)
    assert not card._status.isVisibleTo(card)
    card._fold.click()
    assert not card._dot.isVisibleTo(card) and card._title.text() == "Loot Log · 70 made"


def test_reads_follow_the_game_window_and_skip_a_covered_one(page, monkeypatch):
    """No box drawn: each read takes the messages box from the game's window as it is now (moved, resized), and
    reads nothing while another app's window covers it (its text was read live), saying so on the page."""
    from maplehelper import capture
    from maplehelper.ui import lootscan
    d, _ = page
    scan = d.loot
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
    d.loot_changed()
    assert "covers the game" in d.loot_status.text()
    scan.end()
