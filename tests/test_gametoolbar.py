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
    # "&&" shows as "&": a lone "&" was Qt's shortcut mark, the label read "Cabs _Teleports" (render, 2026-10-10)
    assert w._rides.text() == "Cabs && Teleports" and w._rides.isChecked() is False and s["game_rides"] is False
    w._rides.click()
    w._rides.click()
    assert seen == [True, False]
    s["game_rides"] = True              # a setting kept from last time: the button opens on
    w.refresh()
    assert w._rides.isChecked() is True


# ------------------------------------------------------------ the searches

@pytest.mark.parametrize("kind,text", [("monster_npc", "Monsters & NPCs"), ("map", "Maps"), ("item", "Items"),
                                       ("sell", "Safe To Sell")])
def test_each_search_button_names_its_search(bar, kind, text):
    w, _ = bar
    assert w._search[kind].text().replace("&&", "&") == text      # ("&&": Qt shows one "&", not a shortcut)
    seen = []
    w.search_requested.connect(seen.append)
    w._search[kind].click()
    assert seen == [kind]


def test_the_search_buttons_in_order(bar):
    """Monsters and NPCs one button, then Maps, Items and Safe to sell?, in that order on the bar."""
    w, _ = bar
    assert list(w._search) == ["monster_npc", "map", "item", "sell"]


def test_set_search_mode_lights_only_that_button(bar):
    w, _ = bar
    w.set_search_mode("item")
    assert [w._search[k].property("on") for k in ("monster_npc", "item", "sell")] == [False, True, False]
    w.set_search_mode("")
    assert [w._search[k].property("on") for k in ("monster_npc", "item", "sell")] == [False, False, False]


def test_the_loot_log_button_is_the_loot_windows_setting(bar):
    """As NPCs here is its window's: the button flips settings["loot_overlay"] and says so; refresh follows the
    setting (the window's ✕, or a log starting, changed it)."""
    w, s = bar
    seen = []
    w.loot_toggled.connect(lambda: seen.append(s["loot_overlay"]))
    assert w._loot.text() == "Loot Log" and not w._loot.isChecked()
    w._loot.click()
    assert seen == [True] and s["loot_overlay"] is True
    w._loot.click()
    assert seen == [True, False]
    s["loot_overlay"] = True
    w.refresh()
    assert w._loot.isChecked()


@pytest.mark.parametrize("key,text", [("scroll_planner", "Scrolls"), ("gear_advisor", "Gear")])
def test_each_tool_button_is_its_windows_setting(bar, key, text):
    """Scrolls and Gear, after Loot Log: each flips its window's setting and names it in tool_toggled, and refresh
    follows the setting (the window's ✕ turned it off)."""
    w, s = bar
    w.refresh()
    seen = []
    w.tool_toggled.connect(lambda k: seen.append((k, s[k])))
    assert w._tools[key].text() == text and not w._tools[key].isChecked()
    w._tools[key].click()
    w._tools[key].click()
    assert seen == [(key, True), (key, False)]
    s[key] = True
    w.refresh()
    assert w._tools[key].isChecked()
    assert [k for k in w._tools if w._tools[k].isChecked()] == [key]     # the other one stays off


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
    assert w._npcs.text() == "NPCs Here" and w.layoutDirection() == Qt.LeftToRight


# ------------------------------------------------------------ its place on the screen

def _spare_x(w):
    """An x the bar fits at on the offscreen screen, whatever its width there (the fonts differ by platform: macOS
    draws the same buttons wider), so the place the test asks for is never the clamped one. The whole bar (ten
    buttons, ~885 px) is wider than the 800 px offscreen screen: it is narrowed to half the screen, the place being
    about its size, not its buttons."""
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen().geometry()
    if w.width() >= screen.width() - 1:
        w.setFixedWidth(screen.width() // 2)
    room = screen.width() - w.width()
    assert room > 1
    return screen.left() + room // 2


def test_it_opens_just_above_the_drawn_minimap_box(bar):
    from maplehelper.ui import gametoolbar
    w, s = bar
    x = _spare_x(w)
    s["minimap_region"] = {"x": x, "y": 100, "w": 200, "h": 200}      # 1:1 on the offscreen screen
    w.refresh()
    assert w.pos() == QPoint(x, 100 - w.height() - gametoolbar.GAP)    # its left edge lined up with the box's


def test_the_place_is_kept_when_moved_and_reopened_there(bar):
    w, s = bar
    w.refresh()
    x = _spare_x(w)
    w.move(x, 70)
    w._remember()                            # the debounced save, as the 400 ms timer would fire it
    assert s["game_toolbar_pos"] == {"x": x, "y": 70}
    w.hide()
    w.refresh()
    assert w.pos() == QPoint(x, 70)


def test_it_opens_at_a_saved_spot(bar):
    w, s = bar
    x = _spare_x(w)
    s["game_toolbar_pos"] = {"x": x, "y": 60}
    w.refresh()
    assert w.pos() == QPoint(x, 60)
