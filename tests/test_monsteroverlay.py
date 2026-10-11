"""The Monsters-on-this-map window (ui/monsteroverlay.py): the list it builds from the player's map (the KB's spawns,
each monster's level, HP, EXP and how many spawn), that it never touches the NPCs window's guide, and its life as a
window — shown from its own setting, kept where the player leaves it, closed by its ✕."""
import os
import re
from types import SimpleNamespace

import pytest

from maplehelper.i18n import I18n

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

BIDI_MARKS = re.compile("[%s]" % "".join(map(chr, (0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)))))
MID, OTHER = "000000040", "000000041"
MOBS = {MID: [("monster/2", "Snail", 1, 40), ("monster/3", "Blue Snail", 2, 12)], OTHER: []}
STATS = {"monster/2": SimpleNamespace(level=1, hp=8, exp=3), "monster/3": SimpleNamespace(level=2, hp=15, exp=4)}


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clean_location():
    from maplehelper.ui.location import LOCATION
    old = (LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide)
    LOCATION.here = LOCATION.follow = LOCATION.guide = None
    LOCATION.state = ""
    yield LOCATION
    LOCATION.here, LOCATION.state, LOCATION.follow, LOCATION.guide = old


@pytest.fixture
def overlay(qapp, isolated_store, kb, clean_location, monkeypatch):
    from maplehelper.ui import monsteroverlay
    names = {MID: "Snail Hunting Ground I", OTHER: "Snail Hunting Ground II"}
    monkeypatch.setattr(monsteroverlay.routes, "of", lambda kb: SimpleNamespace(name=lambda mid: names.get(mid, mid)))
    monkeypatch.setattr(monsteroverlay.gamelookup, "map_monsters", lambda kb, mid: MOBS.get(mid, []))
    monkeypatch.setattr(monsteroverlay.combat, "monster", lambda kb, key: STATS.get(key))
    s = isolated_store.Settings()
    s["monster_overlay"] = True
    w = monsteroverlay.MonsterOverlay(kb, s, I18n("en"))
    yield w, s, clean_location
    clean_location.changed.disconnect(w.refresh)
    w.close()


def _here(mid=MID, spot=(0.5, 0.5)):
    from maplehelper.minimap import Here, View
    return Here(mid, spot, View(0, 0, 100, 100, (0, 0, 200, 200)))


def test_it_lists_the_monsters_on_the_players_map_with_their_numbers(overlay):
    w, _, loc = overlay
    loc.set(_here())
    assert w.isVisible()
    assert w._title.text() == "Monsters Here · Snail Hunting Ground I"
    assert [r.text() for r in w._rows] == ["Snail\nLv. 1 · 8 HP · 3 EXP · 40 on the map",
                                           "Blue Snail\nLv. 2 · 15 HP · 4 EXP · 12 on the map"]
    assert w._status.text() == ""


def test_a_new_map_lists_its_own_and_one_with_none_says_so(overlay):
    w, _, loc = overlay
    loc.set(_here())
    loc.set(_here(OTHER))
    assert not w._rows and w._status.text() == "No monsters on this map"


def test_without_a_read_it_says_the_map_isnt_known(overlay):
    w, _, _ = overlay
    w.refresh()
    assert w._status.text() == "Your map isn't known yet"


def test_it_never_touches_the_npcs_windows_guide(overlay):
    """The ring on the game's minimap is the NPCs window's: closing this one leaves it."""
    w, s, loc = overlay
    loc.set(_here())
    loc.set_guide((MID, (0.5, 0.5), "npc/1"))
    w._close.click()
    assert s["monster_overlay"] is False and not w.isVisible() and loc.guide == (MID, (0.5, 0.5), "npc/1")


def test_it_shows_only_while_its_own_setting_is_on(overlay):
    w, s, _ = overlay
    s["npc_overlay"] = True
    s["monster_overlay"] = False
    w.refresh()
    assert not w.isVisible()


def test_its_place_is_its_own(overlay):
    w, s, _ = overlay
    w.refresh()
    w.move(40, 50)
    w._remember()
    assert s["monster_overlay_geom"]["x"] == 40 and s["npc_overlay_geom"] is None


def test_hebrew_retexts_and_mirrors(overlay):
    from PySide6.QtCore import Qt
    w, _, loc = overlay
    loc.set(_here())
    w.apply_language(I18n("he"))
    assert w.layoutDirection() == Qt.RightToLeft
    plain = [BIDI_MARKS.sub("", x) for x in (w._title.text(), w._rows[0].text())]
    assert "מפלצות במפה" in plain[0]
    assert "רמה 1" in plain[1] and "40 במפה" in plain[1]
