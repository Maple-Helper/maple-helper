"""The toolbar's search window (ui/gamesearch.py): the search itself over the KB's monsters, NPCs and items
and where a monster lives, and its life as a window — opened in a mode (the same mode again a toggle), filled
as the player types, a click opening the thing's own page in the detail window beside it (an NPC: where it
stands and the way there; a monster: its maps; an item: who drops and sells it; a map: its monsters), the
history those pages push gone back through, and the place on the screen the search is kept in."""
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


@pytest.fixture(scope="module")
def _own_kb(tmp_path_factory) -> Path:
    """This file's own copy of the fixture KB, without the tables any build writes (they are built here once):
    the shared folder's tables are built by every test worker at once, and on Windows a worker replacing a table
    another one has open fails its build and reads nothing (CI, 2026-10-10: every map of Snail missing)."""
    import shutil

    from maplehelper import tables
    dst = tmp_path_factory.mktemp("gamesearch") / "kb"
    shutil.copytree(Path(__file__).parent / "fixtures" / "kb", dst,
                    ignore=shutil.ignore_patterns(*tables.GENERATED, "*.tmp"))
    return dst


@pytest.fixture
def kb(_own_kb):
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(_own_kb)


def pump(qapp, ms):
    """Wait out a debounce (the typing, the geometry) the way a live event loop would."""
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def delete_window(qapp, w):
    """A GameSearch a test is done with, gone before the next one: hidden (its detail window hidden with it,
    the guide let go), its debounces stopped, both windows deleted and the deletion carried out — another
    file's event pumping must never meet this test's windows (the overlay fixture in test_ux_chat.py and the
    env fixture in test_dialogs_provider.py do the same)."""
    from PySide6.QtCore import QEvent
    w.hide()                                     # hideEvent: the typing stopped, the detail hidden, the place saved
    d = getattr(w, "_detail", None)
    if d is not None:
        d.close()
        d.deleteLater()
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


@pytest.fixture
def clean_location():
    from maplehelper.ui.location import LOCATION
    old = (LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide)
    LOCATION.here = LOCATION.follow = LOCATION.guide = None
    LOCATION.state = ""
    yield LOCATION
    LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide = old


# a fake route graph: what the detail window reads of one — names, streets and minimap frames, no pictures
MID, FAR, GARDEN = "010020000", "010020001", "100000003"
MM = [2000, 4000, 1000, 2000]        # its minimap: width, height in map units, then the origin offset


class _FakeGraph:
    def __init__(self):
        from maplehelper.routes import MapInfo
        self.known = {
            MID: MapInfo(MID, "Fake Town", "Fake Street", True, "", MM, []),
            FAR: MapInfo(FAR, "Far Map", "Far Street", True, "", MM, []),
            GARDEN: MapInfo(GARDEN, "Snail Garden", "Victoria Road", True, "", MM, []),
        }

    def name(self, mid):
        m = self.known.get(mid)
        return m.name if m else mid

    def minimap(self, mid):
        return None


@pytest.fixture
def graph(monkeypatch):
    from maplehelper.ui import gamesearch
    g = _FakeGraph()
    monkeypatch.setattr(gamesearch.routes, "of", lambda kb: g)
    return g


def _here(mid=MID, spot=(0.5, 0.5)):
    from maplehelper.minimap import Here
    return Here(mid, spot)


def _kb_name(kb, key):
    """The name the detail window's header shows: the KB's own, else the key's id."""
    return str((kb.get(key) or {}).get("name") or key.partition("/")[2])


def _texts(d):
    """Every line of the detail's body, top to bottom, as the player reads it (a route step's bold markup off)."""
    import re
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    out = []
    for w in d._rows:
        if isinstance(w, QLabel) and w.pixmap().isNull():
            text = w.text()
            out.append(re.sub(r"<[^>]+>", "", text) if w.textFormat() == Qt.RichText else text)
    return out


def _links(d):
    """The detail's clickable rows, top to bottom."""
    from maplehelper.ui.gamesearch import _Row
    return [w for w in d._rows if isinstance(w, _Row)]


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


# ---------------------------------------------------------------- the detail window beside it

def test_clicking_a_monster_opens_the_detail_window_docked_beside(win, qapp, clean_location):
    from PySide6.QtCore import QPoint, QRect
    from maplehelper.ui.glass import SHADOW
    from maplehelper.ui.npcoverlay import GUIDE_GAP
    win.open("monster")
    win.setGeometry(QRect(400, 100, 320, 420))          # room on its left
    win._box.setText("snail")
    pump(qapp, 200)
    win._rows[0].clicked.emit()                         # Snail: its page, in a window of its own
    d = win._detail
    assert d.isVisible() and d.isWindow()               # a separate top-level window, docked to the search's left
    assert d.pos() == QPoint(win.x() + 2 * SHADOW - GUIDE_GAP - d.width(), win.y())
    assert win.isVisible() and [r.name.text() for r in win._rows] == ["Snail", "Blue Snail", "Red Snail"]  # still listed
    assert not d.back.isVisible()                       # nothing to go back to yet
    assert d.title.text() == "Snail"
    words = " ".join(_texts(d))
    assert "Lv. 1 · 8 HP · 3 EXP" in words and "Where Snail lives" in words
    assert [(r.name.text(), r.sub.text(), r.toolTip()) for r in _links(d)] == [
        ("Snail Garden  ·  Victoria Road", "6 on the map", "Click to see this map and the way there")]
    win.move(500, 200)
    assert d.pos() == QPoint(win.x() + 2 * SHADOW - GUIDE_GAP - d.width(), win.y())      # it moves with the search
    win.move(4, 100)                                   # no room on the left any more: docked to its right
    assert d.x() == win.x() + win.width() - 2 * SHADOW + GUIDE_GAP and d.y() == win.y()
    win.hide()
    assert not d.isVisible()                            # hiding the search hides the detail


def test_a_map_row_opens_the_maps_page_and_back_returns(win, qapp, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    monkeypatch.setattr(gamelookup, "map_monsters",
                        lambda kb, key: [("monster/2230104", "Stump", 15, 3)] if key == f"map/{GARDEN}" else [])
    monkeypatch.setattr(gamelookup, "way_to", lambda kb, here, to, rides=True: gamelookup.Way(here=None, legs=(), known=False))
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win._rows[0].clicked.emit()
    d = win._detail
    snail = win._hits[0].key
    assert d._stack == [("monster", snail)]
    _links(d)[0].clicked.emit()                         # Snail Garden: the map's own page
    assert d._stack == [("monster", snail), ("map", f"map/{GARDEN}")]
    assert d.back.isVisible() and d.title.text() == "Snail Garden"
    assert _texts(d) == ["Victoria Road", "The way from here on foot",
                         "Your map isn't known yet, so there's no way from here"]
    assert [(r.name.text(), r.sub.text(), r.toolTip()) for r in _links(d)] == [
        ("Stump", "Lv. 15 · 3 on the map", "Click to see this monster")]
    d.go_back()
    assert d._stack == [("monster", snail)] and not d.back.isVisible()
    assert "Where Snail lives" in " ".join(_texts(d))
    _links(d)[0].clicked.emit()                         # the map again, then a new search click starts over
    win._rows[1].clicked.emit()                         # Blue Snail
    assert d._stack == [("monster", win._hits[1].key)] and not d.back.isVisible()
    d.close_btn.click()
    assert not d.isVisible()


def test_the_next_page_is_measured_at_once_never_folded_to_the_header(win, qapp, clean_location, graph, monkeypatch):
    """A page opened while the window already shows one: its rows were measured before they were shown (0 px) and
    the window folded to its header with the old page drawn under the title (offscreen render, 2026-10-10)."""
    from maplehelper import gamelookup
    monkeypatch.setattr(gamelookup, "map_monsters",
                        lambda kb, key: [("monster/2230104", "Stump", 15, 3)] if key == f"map/{GARDEN}" else [])
    monkeypatch.setattr(gamelookup, "way_to", lambda kb, here, to, rides=True: gamelookup.Way(here=None, legs=(), known=False))
    win.open("monster")
    win._box.setText("snail")
    pump(qapp, 200)
    win._rows[0].clicked.emit()
    d = win._detail
    first = d.height()
    _links(d)[0].clicked.emit()                         # the next page, no event pass in between
    assert d.height() > d._head.sizeHint().height() + 80 and all(w.isVisibleTo(d) for w in d._rows)
    d.go_back()
    assert d.height() == first


def test_an_item_page_lists_droppers_and_sellers_and_a_dropper_opens_the_monster(
        win, qapp, kb, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    monkeypatch.setattr(gamelookup, "item_sources", lambda kb, key: (
        [gamelookup.Dropper("monster/1210100", "Snail", 1), gamelookup.Dropper("monster/2230104", "Stump", 15)],
        [gamelookup.Seller("npc/2", "Al", 50, "Fake Town"), gamelookup.Seller("npc/9", "Bree", None, "Henesys")]))
    win.open("item")
    assert win._box.placeholderText() == "Item name…"
    win._box.setText("red")
    pump(qapp, 200)
    win._rows[0].clicked.emit()                         # Red Potion: its page
    d = win._detail
    assert d._stack == [("item", "item/2000000")] and d.title.text() == "Red Potion"
    assert "Dropped by" in _texts(d) and "Sold by" in _texts(d)
    assert [(r.name.text(), r.sub.text(), r.toolTip()) for r in _links(d)] == [
        ("Snail", "Lv. 1", "Click to see this monster"),
        ("Stump", "Lv. 15", "Click to see this monster"),
        ("Al", "50 mesos · Fake Town", "Click to see this NPC"),
        ("Bree", "Henesys", "Click to see this NPC")]
    _links(d)[0].clicked.emit()                         # who drops it: the monster's page
    assert d._stack == [("item", "item/2000000"), ("monster", "monster/1210100")]
    assert f"Where {_kb_name(kb, 'monster/1210100')} lives" in " ".join(_texts(d))


def test_droppers_and_sellers_are_capped_at_a_dozen_with_and_n_more(win, qapp, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    monkeypatch.setattr(gamelookup, "item_sources", lambda kb, key: (
        [gamelookup.Dropper(f"monster/{1000 + i}", f"M{i}", i) for i in range(14)], []))
    win.open("item")
    win._box.setText("red")
    pump(qapp, 200)
    win._rows[0].clicked.emit()
    d = win._detail
    assert len(_links(d)) == 12 and "and 2 more" in _texts(d)


def test_an_item_nothing_drops_or_sells_says_so(win, qapp, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    monkeypatch.setattr(gamelookup, "item_sources", lambda kb, key: ([], []))
    win.open("item")
    win._box.setText("red")
    pump(qapp, 200)
    win._rows[0].clicked.emit()
    assert _texts(win._detail) == ["No known drops or shops"]     # the fixture KB gives Red Potion no type line


def test_an_npc_on_the_players_map_guides_and_hiding_clears_it(win, qapp, kb, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    from maplehelper.ui.location import LOCATION
    spot = (0.6, 0.5)                                  # 200 map units right of the player
    monkeypatch.setattr(gamelookup, "npc_place",
                        lambda kb, key, here_map=None: gamelookup.Place(MID, "Fake Town", "Fake Street", spot))
    monkeypatch.setattr(gamelookup, "map_monsters", lambda kb, key: [])
    monkeypatch.setattr(gamelookup, "way_to",
                        lambda kb, here, to, rides=True: gamelookup.Way(here="999999999", legs=(), known=False))
    clean_location.set(_here(MID, (0.5, 0.5)))
    win.open("npc")
    win._detail.open_view("npc", "npc/3")
    d = win._detail
    name = _kb_name(kb, "npc/3")
    assert d.isVisible() and LOCATION.guide == (MID, spot, "npc/3")      # the game's own minimap rings it
    assert _texts(d) == [f"{name} is to your right"]
    clean_location.set(_here(MID, (0.86, 0.5)))        # the player walks past it: the sentence follows
    assert _texts(d) == [f"{name} is to your left"] and LOCATION.guide == (MID, spot, "npc/3")
    win.hide()
    assert not d.isVisible() and LOCATION.guide is None                     # hidden: the ring is let go
    clean_location.set(_here(MID, (0.5, 0.5)))
    win.open("npc")
    d.open_view("npc", "npc/3")
    assert LOCATION.guide == (MID, spot, "npc/3")
    clean_location.set(_here("999999999", (0.5, 0.5)))  # another map: the ring goes, the way from there shown
    assert LOCATION.guide is None
    assert _texts(d) == [f"{name} is on Fake Town", "The way from here on foot",
                         "No way on foot from your map is known. Try the toolbar's Cabs & teleports"]
    clean_location.set(_here(MID, (0.5, 0.5)))          # back on it: the map's own page says so
    d.open_view("map", f"map/{MID}")
    assert _texts(d) == ["Fake Street", "You're on this map"] and LOCATION.guide is None


def test_an_npc_elsewhere_shows_the_way_there_line_by_line(win, qapp, kb, clean_location, graph, monkeypatch):
    from maplehelper import gamelookup
    from maplehelper.i18n import I18n
    from maplehelper.routes import Leg
    from maplehelper.ui import mapview
    from maplehelper.ui.location import LOCATION
    walk = (Leg(MID, FAR, "portal", "the door", spot=(0.9, 0.5)),)
    ride = (Leg(MID, "010020002", "taxi", "Regular Cab", npc="npc/8", spot=(0.2, 0.5)),
            Leg("010020002", FAR, "npc", "Bart", npc="npc/9", spot=(0.1, 0.5)))
    monkeypatch.setattr(gamelookup, "npc_place",
                        lambda kb, key, here_map=None: gamelookup.Place(FAR, "Far Map", "Far Street", (0.5, 0.5)))
    monkeypatch.setattr(gamelookup, "way_to", lambda kb, here, to, rides=True:
                        gamelookup.Way(here=MID, legs=ride if rides else walk, known=True))
    clean_location.set(_here(MID, (0.5, 0.5)))
    win.open("npc")
    win._detail.open_view("npc", "npc/3")
    d = win._detail
    t = I18n("en")

    def says(legs):
        return [mapview.route_says(t, graph, leg).replace("**", "") for leg in legs]
    # the toolbar's Cabs & teleports off (the default): on foot
    assert _texts(d) == [f"{_kb_name(kb, 'npc/3')} is on Far Map", "The way from here on foot", *says(walk)]
    # the steps' **bold** names are drawn bold, never shown as asterisks (offscreen render, 2026-10-10)
    assert all("**" not in w.text() for w in d._rows if hasattr(w, "text"))
    assert LOCATION.guide is None                      # never ringed: it isn't where the player is
    # switched on: the page shown finds the way again, by cab and NPC teleport; off again, back on foot
    win._settings["game_rides"] = True
    win.rides_changed()
    assert _texts(d) == [f"{_kb_name(kb, 'npc/3')} is on Far Map", "The way from here by cab and teleport",
                         *says(ride)]
    win._settings["game_rides"] = False
    win.rides_changed()
    assert _texts(d)[1:] == ["The way from here on foot", *says(walk)]


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
