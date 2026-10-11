"""The bar over the game (ui/gametoolbar.py): shown from its own setting, only what is about the player's map — its
NPCs, Monsters and Loot Log buttons each flipping its window's setting (the window follows the signal) — and a
Tools button asking for the app's Tools window, the ✕ turning the bar off, and its place on the screen — above
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


def test_only_the_map_windows_loot_log_and_tools_are_on_the_bar(bar):
    """One place for each thing (the owner, 2026-10-11): no searches, scrolls or gear over the game — those are the
    Tools window's."""
    from PySide6.QtWidgets import QPushButton
    w, _ = bar
    texts = [b.text() for b in w.findChildren(QPushButton)]
    assert texts == ["NPCs", "Monsters", "Loot Log", "Tools"]


@pytest.mark.parametrize("key", ["npc_overlay", "monster_overlay", "loot_overlay"])
def test_each_window_button_is_its_windows_setting(bar, key):
    """The button flips the setting and names it in window_toggled; refresh follows the setting (the window's own
    ✕, or a log starting, changed it); the other buttons stay as they were."""
    w, s = bar
    s[key] = False
    w.refresh()
    seen = []
    w.window_toggled.connect(lambda k: seen.append((k, s[k])))
    assert not w._windows[key].isChecked()
    w._windows[key].click()
    w._windows[key].click()
    assert seen == [(key, True), (key, False)]
    s[key] = True
    w.refresh()
    assert [k for k, b in w._windows.items() if b.isChecked()] == [key]


def test_the_tools_button_asks_for_the_tools_window(bar):
    w, _ = bar
    seen = []
    w.tools_requested.connect(lambda: seen.append(True))
    w._tools.click()
    assert seen == [True] and not w._tools.isCheckable()


def test_the_x_turns_the_setting_off_and_hides(bar):
    w, s = bar
    w.refresh()
    w._close.click()
    assert s["game_toolbar"] is False and not w.isVisible()


def test_apply_language_retexts_and_mirrors(bar):
    w, _ = bar
    w.apply_language(I18n("he"))
    assert [w._windows[k].text() for k in ("npc_overlay", "monster_overlay")] == [
        bidi.plain("NPC", True), bidi.plain("מפלצות", True)]
    assert w._tools.text() == bidi.plain("כלים", True)
    assert w.layoutDirection() == Qt.RightToLeft
    w.apply_language(I18n("en"))
    assert w._windows["npc_overlay"].text() == "NPCs" and w.layoutDirection() == Qt.LeftToRight


# ------------------------------------------------------------ its place on the screen

def _spare_x(w):
    """An x the bar fits at on the offscreen screen, whatever its width there (the fonts differ by platform: macOS
    draws the same buttons wider), so the place the test asks for is never the clamped one. A bar wider than the
    800 px offscreen screen is narrowed to half the screen, the place being about its size, not its buttons."""
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
