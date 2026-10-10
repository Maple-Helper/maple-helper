"""The toolbar's search window (ui/gamesearch.py): the search itself over the KB's monsters, NPCs and items
and where a monster lives, and its life as a window — opened in a mode (the same mode again a toggle), filled
as the player types, its rows opening the app's map and item windows, a monster's maps inside it with the way
back, and the place on the screen it is kept in."""
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])

@pytest.fixture
def settings(isolated_store):
    return isolated_store.Settings()


def pump(qapp, ms):
    """Wait out a debounce (the typing, the geometry) the way a live event loop would."""
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def delete_window(qapp, w):
    """A GameSearch a test is done with, gone before the next one: hidden, its debounces stopped, deleted and
    the deletion carried out — another file's event pumping must never meet this test's windows (the overlay
    fixture in test_ux_chat.py and the env fixture in test_dialogs_provider.py do the same)."""
    from PySide6.QtCore import QEvent
    w.hide()                                     # hideEvent: the typing stopped, the place saved now
    w.close()
    for timer in (w._typing, w._geom):
        timer.stop()
    w.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def win(qapp, settings, kb):
    from maplehelper.i18n import I18n
    from maplehelper.ui.gamesearch import GameSearch
    w = GameSearch(kb, settings, I18n("en"))
    yield w
    delete_window(qapp, w)


REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")


# ---------------------------------------------------------------- the search itself (no window needed)

def test_exact_before_prefix_before_contains_and_ties_by_name(kb):
    from maplehelper.kb import memo
    from maplehelper.ui.gamesearch import Hit, search
    # the list is seeded for the ladder's sake: the fixture KB happens to hold no name another one starts with
    memo(kb, "_game_search")["item"] = [Hit("item/x", "Blue Snail Shell", ""), Hit("item/y", "Snail Shell", ""),
                                        Hit("item/z", "Snail", ""), Hit("item/w", "A Snail", "")]
    hits, total = search(kb, "item", "snail")
    assert [h.name for h in hits] == ["Snail", "Snail Shell", "A Snail", "Blue Snail Shell"] and total == 4


def test_a_prefix_beats_a_mere_contains(kb):
    from maplehelper.ui.gamesearch import search
    hits, _ = search(kb, "monster", "sna")
    assert [h.name for h in hits] == ["Snail", "Blue Snail", "Red Snail"]      # Snail starts "sna", the rest only have it


def test_the_words_match_in_any_order(kb):
    from maplehelper.ui.gamesearch import search
    one = search(kb, "monster", "snail blue")
    assert one == search(kb, "monster", "blue snail")
    assert [h.name for h in one[0]] == ["Blue Snail"] and one[1] == 1


def test_under_two_letters_finds_nothing(kb):
    from maplehelper.ui.gamesearch import search
    for q in ("", " ", "s", " s ", "a"):
        assert search(kb, "monster", q) == ([], 0)


def test_the_limit_and_the_total(kb):
    from maplehelper.ui.gamesearch import search
    hits, total = search(kb, "monster", "snail", limit=1)
    assert [h.name for h in hits] == ["Snail"] and total == 3


def test_each_list_is_built_once_per_kb(kb, monkeypatch):
    from maplehelper.ui import gamesearch
    calls = []
    build = gamesearch._BUILDERS["monster"]
    monkeypatch.setitem(gamesearch._BUILDERS, "monster", lambda kb: calls.append(1) or build(kb))
    gamesearch.search(kb, "monster", "snail")
    gamesearch.search(kb, "monster", "snail blue")
    gamesearch.search(kb, "monster", "red")
    assert calls == [1]


def test_where_a_monster_lives(kb):
    from maplehelper.ui.gamesearch import monster_maps
    assert monster_maps(kb, "monster/1210100") == [("map/100000002", "Henesys Hunting Ground II", "Victoria Road", 6)]
    assert monster_maps(kb, "map/100000000") == []            # not a monster at all


# ---------------------------------------------------------------- the window

def test_open_shows_that_mode_and_the_same_mode_again_toggles_off(win):
    modes = []
    win.mode_changed.connect(modes.append)
    win.open("monster")
    assert win.isVisible() and win._kind == "monster"
    assert win._title.text() == "Find a monster" and win._box.placeholderText() == "Monster name…"
    assert win._status.text() == "Type at least 2 letters" and not win._rows
    win.open("monster")                                        # the same mode again: off
    assert not win.isVisible()
    assert modes == ["monster", ""]


def test_typing_fills_the_rows(win, qapp):
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)                                            # the typing debounce
    assert [r.name.text() for r in win._rows] == ["Snail", "Blue Snail", "Red Snail"]
    assert win._rows[0].sub.text() == "Lv. 1"
    assert win._status.text() == ""                            # all of them shown: no "and N more"
    win._box.setText("zzz")
    pump(qapp, 200)
    assert not win._rows and win._status.text() == "Nothing found for “zzz”"


@needs_kb
def test_clicking_an_npc_row_opens_the_map_window(qapp, settings):
    """The fixture KB's release guide says none of its NPCs are in the game: the rows are asked of the real
    one, the way the NPCs window's own click test does."""
    from maplehelper.i18n import I18n
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.gamesearch import GameSearch
    from maplehelper.ui.widgets import MAP_REQUESTS
    w = GameSearch(KnowledgeBase(REAL_KB), settings, I18n("en"))
    try:
        w.open("npc")
        w._box.setText("roger")
        pump(qapp, 200)
        assert [(r.name.text(), r.sub.text()) for r in w._rows] == [("Roger", "Mushroom Town · Maple Road")]
        assert w._rows[0].toolTip() == "Click to see where it is and the way there"
        seen = []
        MAP_REQUESTS.requested.connect(seen.append)
        try:
            w._rows[0].clicked.emit()
        finally:
            MAP_REQUESTS.requested.disconnect(seen.append)
        assert seen == ["npc/3"]
    finally:
        delete_window(qapp, w)


def test_clicking_an_item_row_opens_its_details(win, qapp):
    from maplehelper.ui.widgets import ITEM_REQUESTS
    win.open("item")
    assert win._box.placeholderText() == "Item name…"
    win._box.setText("red")
    pump(qapp, 200)
    assert [r.name.text() for r in win._rows] == ["Red Potion"]
    seen = []
    ITEM_REQUESTS.requested.connect(seen.append)
    try:
        win._rows[0].clicked.emit()
    finally:
        ITEM_REQUESTS.requested.disconnect(seen.append)
    assert seen == ["item/2000000"]


def test_a_monster_row_shows_where_it_lives_and_a_map_click_opens_it(win, qapp):
    from PySide6.QtWidgets import QLabel, QPushButton
    from maplehelper.ui import gamesearch
    from maplehelper.ui.widgets import MAP_REQUESTS
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win._rows[0].clicked.emit()                                # Snail: its own page in the window
    assert win._detail is not None and win._detail.name == "Snail"
    assert [b.text() for b in win._rows if isinstance(b, QPushButton)] == ["Back to the results"]
    words = " ".join(w.text() for w in win._rows if isinstance(w, (QLabel, QPushButton)))
    assert "Lv. 1 · 8 HP · 3 EXP" in words and "Where Snail lives" in words
    maps = [r for r in win._rows if isinstance(r, gamesearch._Row)]
    assert [(m.name.text(), m.sub.text(), m.toolTip()) for m in maps] == [
        ("Snail Garden  ·  Victoria Road", "6 on the map", "Click a map to see the way there")]
    seen = []
    MAP_REQUESTS.requested.connect(seen.append)
    try:
        maps[0].clicked.emit()
    finally:
        MAP_REQUESTS.requested.disconnect(seen.append)
    assert seen == ["map/100000003"]


def test_back_returns_to_the_results_with_the_query_kept(win, qapp):
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win._rows[0].clicked.emit()
    win._back()
    assert win._box.text() == "snail"
    assert [r.name.text() for r in win._rows] == ["Snail", "Blue Snail", "Red Snail"]


def test_switching_modes_keeps_each_ones_query(win, qapp):
    modes = []
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win.mode_changed.connect(modes.append)
    win.open("npc")                                            # another mode while it shows: a switch, not a toggle
    assert win.isVisible() and win._kind == "npc" and win._box.text() == "" and modes == ["npc"]
    win._box.setText("roger")      # the fixture KB's NPCs are all shut by its release guide: no rows, but the
    pump(qapp, 200)                # query is kept all the same
    win.open("monster")
    assert win._box.text() == "snail" and [r.name.text() for r in win._rows] == ["Snail", "Blue Snail", "Red Snail"]
    win.open("npc")
    assert win._box.text() == "roger" and not win._rows
    assert modes == ["npc", "monster", "npc"]


def test_esc_in_the_box_hides(win, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    modes = []
    win.mode_changed.connect(modes.append)
    win.open("npc")
    QTest.keyClick(win._box, Qt.Key_Escape)
    assert not win.isVisible() and modes == ["npc", ""]


def test_apply_language_retexts_and_mirrors(win, qapp):
    from PySide6.QtCore import Qt
    from maplehelper import bidi
    from maplehelper.i18n import I18n
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win.apply_language(I18n("he"))
    assert win.layoutDirection() == Qt.RightToLeft
    assert win._title.text() == bidi.plain("חיפוש מפלצת", True)
    assert win._box.placeholderText() == bidi.plain("שם המפלצת…", True)
    assert [r.name.text() for r in win._rows] == [bidi.ltr_name(n, True) for n in ("Snail", "Blue Snail", "Red Snail")]      # rebuilt, still there


# ---------------------------------------------------------------- its place on the screen

def test_it_first_opens_just_under_the_toolbar(win):
    from PySide6.QtCore import QPoint, QRect
    from maplehelper.ui import gamesearch
    win.place_under(QRect(100, 60, 200, 40))
    win.open("monster")
    assert win.pos() == QPoint(100, 60 + 40 + gamesearch.GAP)


def test_it_reopens_where_the_player_left_it(win, settings):
    from PySide6.QtCore import QPoint, QSize
    settings["game_search_geom"] = {"x": 90, "y": 70, "w": 280, "h": 330}
    win.open("monster")
    assert win.pos() == QPoint(90, 70) and win.size() == QSize(280, 330)


def test_the_place_is_saved_once_the_move_settles(win, qapp, settings):
    win.open("monster")
    win.move(90, 70)
    win.resize(280, 330)
    pump(qapp, 500)                                            # the geometry debounce
    assert settings["game_search_geom"] == {"x": 90, "y": 70, "w": 280, "h": 330}


# ---------------------------------------------------------------- the real knowledge base


@pytest.fixture(scope="module")
def real():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_snail_is_found_first_with_its_level(real):
    from maplehelper.ui.gamesearch import search
    hits, total = search(real, "monster", "snail")
    assert hits[0].name == "Snail" and hits[0].sub == "Lv. 1" and hits[0].key == "monster/2"
    assert {"Blue Snail", "Red Snail"} <= {h.name for h in hits} and total >= 3


@needs_kb
def test_where_snail_lives_is_every_map_most_spawns_first(real):
    from maplehelper.ui.gamesearch import monster_maps
    maps = monster_maps(real, "monster/2")
    assert maps[0] == ("map/000000040", "Snail Hunting Ground I", "Maple Road", 40)
    assert maps == sorted(maps, key=lambda m: -m[3])            # the most spawns first
    assert len({m[0] for m in maps}) == len(maps)               # each map once


@needs_kb
def test_an_npc_is_found_with_the_map_it_stands_on(real):
    from maplehelper.ui.gamesearch import search
    hits, _ = search(real, "npc", "roger")
    assert hits[0].name == "Roger" and hits[0].key == "npc/3"
    assert "Mushroom Town" in hits[0].sub and "Maple Road" in hits[0].sub


@needs_kb
def test_an_item_is_found_with_its_type(real):
    from maplehelper.ui.gamesearch import search
    hits, _ = search(real, "item", "red potion")
    assert hits[0].name == "Red Potion" and hits[0].sub == "Use / Potion"
