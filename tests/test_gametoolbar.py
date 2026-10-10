"""The bar over the game (ui/gametoolbar.py): shown from its own setting, its NPCs-here button flipping the
NPCs window's setting (the window follows the signal), its three search buttons naming their search, the
mode lit back on the button that opened it, the ✕ turning the bar off, and its place on the screen — above
the drawn minimap box, at the saved spot, and the spot kept after a drag."""
import os

import pytest
from PySide6.QtCore import QPoint, Qt

from maplehelper import bidi
from maplehelper.i18n import I18n

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bar(qapp, isolated_store):
    from maplehelper.ui import gametoolbar
    s = isolated_store.Settings()
    w = gametoolbar.GameToolbar(s, I18n("en"))
    yield w, s
    w.close()


# ------------------------------------------------------------ its setting

def test_it_shows_only_while_the_setting_is_on(bar):
    w, s = bar
    s["game_toolbar"] = False
    w.refresh()
    assert not w.isVisible()
    s["game_toolbar"] = True
    w.refresh()
    assert w.isVisible()


def test_the_npcs_here_button_flips_the_windows_setting_both_ways(bar):
    w, s = bar
    s["npc_overlay"] = False
    w.refresh()
    assert w._npcs.isChecked() is False
    w._npcs.click()
    assert s["npc_overlay"] is True and w._npcs.isChecked() is True
    w._npcs.click()
    assert s["npc_overlay"] is False and w._npcs.isChecked() is False


def test_flipping_the_npcs_here_button_signals_it(bar):
    w, _ = bar
    w.refresh()
    seen = []
    w.npc_toggled.connect(lambda: seen.append(True))
    w._npcs.click()
    assert seen == [True]


def test_refresh_keeps_the_button_in_step_with_the_setting(bar):
    """The window's own ✕ turned the setting off (its closed signal refreshes the bar): the button follows."""
    w, s = bar
    w.refresh()
    w._npcs.click()                     # on, by the button itself
    s["npc_overlay"] = False            # ...then off somewhere else
    w.refresh()
    assert w._npcs.isChecked() is False


def test_the_cabs_and_teleports_button_is_its_setting_off_at_first(bar):
    """Off on a new install: the way to a found NPC walks until the player turns cabs and NPC teleports on."""
    w, s = bar
    w.refresh()
    seen = []
    w.rides_toggled.connect(lambda: seen.append(s["game_rides"]))
    assert w._rides.text() == "Cabs & teleports" and w._rides.isChecked() is False and s["game_rides"] is False
    w._rides.click()
    w._rides.click()
    assert seen == [True, False]
    s["game_rides"] = True              # a setting kept from last time: the button opens on
    w.refresh()
    assert w._rides.isChecked() is True


# ------------------------------------------------------------ the searches

@pytest.mark.parametrize("kind,text", [("monster", "Monsters"), ("npc", "NPCs"), ("item", "Items")])
def test_each_search_button_names_its_search(bar, kind, text):
    w, _ = bar
    assert w._search[kind].text() == text
    seen = []
    w.search_requested.connect(seen.append)
    w._search[kind].click()
    assert seen == [kind]


def test_set_search_mode_lights_only_that_button(bar):
    w, _ = bar
    w.set_search_mode("npc")
    assert [w._search[k].property("on") for k in ("monster", "npc", "item")] == [False, True, False]
    w.set_search_mode("")
    assert [w._search[k].property("on") for k in ("monster", "npc", "item")] == [False, False, False]


def test_the_x_turns_the_setting_off_and_hides(bar):
    w, s = bar
    w.refresh()
    w._close.click()
    assert s["game_toolbar"] is False and not w.isVisible()


def test_apply_language_retexts_and_mirrors(bar):
    w, _ = bar
    w.apply_language(I18n("he"))
    assert w._npcs.text() == bidi.plain("דמויות כאן", True)
    assert w.layoutDirection() == Qt.RightToLeft
    w.apply_language(I18n("en"))
    assert w._npcs.text() == "NPCs here" and w.layoutDirection() == Qt.LeftToRight


# ------------------------------------------------------------ its place on the screen

def test_it_opens_just_above_the_drawn_minimap_box(bar):
    from maplehelper.ui import gametoolbar
    w, s = bar
    s["minimap_region"] = {"x": 100, "y": 100, "w": 200, "h": 200}    # 1:1 on the offscreen screen
    w.refresh()
    assert w.pos() == QPoint(100, 100 - w.height() - gametoolbar.GAP)   # its left edge lined up with the box's


def test_the_place_is_kept_when_moved_and_reopened_there(bar):
    w, s = bar
    w.refresh()
    w.move(90, 70)
    w._remember()                            # the debounced save, as the 400 ms timer would fire it
    assert s["game_toolbar_pos"] == {"x": 90, "y": 70}
    w.hide()
    w.refresh()
    assert w.pos() == QPoint(90, 70)


def test_it_opens_at_a_saved_spot(bar):
    w, s = bar
    s["game_toolbar_pos"] = {"x": 120, "y": 60}
    w.refresh()
    assert w.pos() == QPoint(120, 60)
