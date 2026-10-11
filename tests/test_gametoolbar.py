"""The bar over the game (ui/gametoolbar.py): shown from its own setting, only what is about the player's map — its
NPCs, Monsters and Loot Log buttons each flipping its window's setting (the window follows the signal) — and a
Tools button asking for the app's Tools window, the ✕ turning the bar off, and its place on the screen — above
the drawn minimap box, at the saved spot, and the spot kept after a drag. Its width is the player's too: the
buttons wrap into as many rows as the width leaves room for, and the ⚙ panel's choices (the glass tint, the
accent, icons instead of words, the transparency) are written and followed live — by the bar, and by the NPCs
and Monsters windows over the game."""
import os

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton
from PySide6.QtTest import QTest

from maplehelper import bidi
from maplehelper.i18n import I18n

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _todays_look():
    """The glass tint every window paints is module state: a test that picks a preset puts today's dark back,
    or the next test file's windows paint midnight."""
    from maplehelper.ui import npcoverlay
    yield
    npcoverlay.apply_look({"overlay_tint": "dark"})


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


def test_the_tools_button_opens_a_menu_of_tabs_then_the_whole_window(bar):
    """One click from the game to a Tools tab (the owner, 2026-10-11); the last entry opens the window as it is."""
    from maplehelper.ui import gametoolbar
    w, _ = bar
    seen = []
    w.tools_requested.connect(seen.append)
    w._tools.click()
    acts = [a for a in w._menu.actions() if not a.isSeparator()]
    assert len(acts) == len(gametoolbar.QUICK) + 1 and not w._tools.isCheckable()
    acts[gametoolbar.QUICK.index("gear")].trigger()
    acts[-1].trigger()
    assert seen == ["gear", ""]
    w._menu.hide()


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


# ------------------------------------------------------------ its width (the buttons wrap)

def test_a_narrower_bar_wraps_its_buttons_into_rows(bar):
    """The buttons wrap into as many rows as the width leaves room for, the bar's height following; at the
    narrowest width it goes — its widest single button — a button or two a row, not all on one."""
    w, s = bar
    w.refresh()
    rows = lambda: len({b.y() for b, _ in w._buttons()})
    one_row = w.height()
    assert rows() == 1
    s["game_toolbar_width"] = w._width_limits()[0]      # the narrowest: the widest single button fits, alone
    w.refresh()
    assert rows() >= 3
    assert w.height() > one_row


def test_the_dragged_width_is_kept_and_reopened(bar):
    """A width dragged at the bar's side settles into game_toolbar_width, and the next bar opens at it."""
    from maplehelper.ui import gametoolbar
    w, s = bar
    w.refresh()
    w._widths.stop()                    # the debounced save, as the 400 ms timer would fire it
    w.resize(420, w.height())
    w._remember_width()
    assert s["game_toolbar_width"] == 420
    again = gametoolbar.GameToolbar(s, I18n("en"))
    again.refresh()
    assert again.width() == 420
    again.close()


def test_the_width_clamps_to_the_screen(bar):
    w, s = bar
    screen = QGuiApplication.primaryScreen().geometry()
    s["game_toolbar_width"] = screen.width() * 2
    w.refresh()
    assert w.width() <= screen.width()


def test_the_width_clamps_to_the_widest_single_button(bar):
    from maplehelper.ui import gametoolbar
    w, s = bar
    s["game_toolbar_width"] = 1
    w.refresh()
    assert w.width() == w._width_limits()[0]
    assert w.width() >= max(b.width() for b, _ in w._buttons()) + 2 * gametoolbar.SIDE   # the widest fits too


# ------------------------------------------------------------ the ⚙ panel

def test_the_gear_button_toggles_the_settings_panel(bar):
    w, _ = bar
    w.refresh()
    w._gear.click()
    assert w._panel is not None and w._panel.isVisible()
    w._gear.click()
    assert not w._panel.isVisible()


def test_the_panels_esc_and_x_close_it(bar):
    w, _ = bar
    w.refresh()
    w._gear.click()
    QTest.keyClick(w._panel, Qt.Key_Escape)
    assert not w._panel.isVisible()
    w._gear.click()
    w._panel._close.click()
    assert not w._panel.isVisible()


def test_a_tint_choice_is_written_and_painted_by_the_glass(bar):
    """The tint every window's _paint_glass paints follows the choice at once (npcoverlay's module state)."""
    from maplehelper.ui import npcoverlay
    w, s = bar
    w.refresh()
    w._gear.click()
    w._panel._pick_tint("midnight")
    assert s["overlay_tint"] == "midnight"
    assert npcoverlay.current_tint() == npcoverlay.TINTS["midnight"][1]


def test_an_accent_choice_restyles_the_bar(bar):
    from maplehelper.ui import npcoverlay
    w, s = bar
    w.refresh()
    w._gear.click()
    w._panel._pick_accent("blue")
    assert s["overlay_accent"] == "blue"
    assert npcoverlay.ACCENTS["blue"][1] in w.styleSheet()           # the lit buttons' new colour


def test_the_panel_rings_the_chosen_swatches(bar):
    w, s = bar
    s["overlay_tint"], s["overlay_accent"] = "wine", "pink"
    w._gear.click()
    assert [b.property("key") for b in w._panel._tints.findChildren(QPushButton) if b.isChecked()] == ["wine"]
    assert [b.property("key") for b in w._panel._accents.findChildren(QPushButton) if b.isChecked()] == ["pink"]


def test_the_icons_choice_swaps_the_words_for_icons(bar):
    """Each of the four buttons shows its icon, its name in the tooltip and the accessible name; off, the words
    are back."""
    from maplehelper.ui import gametoolbar
    w, s = bar
    w.refresh()
    w._gear.click()
    w._panel._icons.click()
    assert s["game_toolbar_icons"] is True and w._panel._icons.isChecked()
    npcs = w._windows["npc_overlay"]
    assert npcs.text() == "\U0001F4AC"
    assert npcs.toolTip() == "NPCs" and npcs.accessibleName() == "NPCs"
    assert [b.text() for b, _ in w._buttons()] == [gametoolbar.ICONS[k] for k in
                                                   ("tb_npcs_here", "tb_monsters", "tb_loot", "tb_tools")]
    assert w._tools.toolTip() == "Tools" and w._windows["monster_overlay"].toolTip() == "Monsters"
    assert len(set(gametoolbar.ICONS.values())) == 4                   # each its own
    w._panel._icons.click()
    assert s["game_toolbar_icons"] is False
    assert npcs.text() == "NPCs" and npcs.toolTip() == w.t("tb_npcs_here_tip")


def test_the_settings_dialogs_icons_switch_reaches_an_open_bar(bar):
    """Settings > Minimap's switch (saved, then the app refreshes the windows): the bar's words turn to icons on
    that refresh, not on the next start."""
    w, s = bar
    w.refresh()
    s["game_toolbar_icons"] = True
    w.refresh()
    assert w._tools.text() == "\U0001F6E0\uFE0F"


def test_an_opacity_choice_writes_the_shared_setting(bar):
    """The panel's transparency is the same npc_overlay_opacity the Settings dialog offers."""
    w, s = bar
    w.refresh()
    w._gear.click()
    w._panel._pick_opacity(0.5)
    assert s["npc_overlay_opacity"] == 0.5
    assert w._opacity == 0.5


def test_the_panel_and_settings_offer_the_same_opacity_steps():
    from maplehelper.ui.dialogs import OPACITY_CHOICES
    from maplehelper.ui.npcoverlay import OPACITY_STEPS
    assert OPACITY_CHOICES == OPACITY_STEPS


def test_a_panel_choice_signals_the_app_to_restyle_every_window(bar):
    w, _ = bar
    seen = []
    w.look_changed.connect(lambda: seen.append(True))
    w.refresh()
    w._gear.click()
    w._panel._pick_accent("teal")
    assert seen == [True]


def test_the_default_look_is_todays(bar):
    """Nothing picked: today's dark glass, today's green lit buttons, today's words on the buttons."""
    from maplehelper.ui import npcoverlay
    w, _ = bar
    w.refresh()
    assert npcoverlay.current_tint() == npcoverlay.PANEL
    assert npcoverlay.ACCENTS["green"][1] in w.styleSheet()
    assert w._windows["npc_overlay"].text() == "NPCs"


# ------------------------------------------------------------ its place on the screen

def _spare_x(s):
    """An x the bar fits at on the offscreen screen, whatever its natural width there (the fonts differ by platform:
    macOS draws the same buttons wider): the width it opens at is set through its own setting, so the place the
    test asks for is never the clamped one."""
    screen = QGuiApplication.primaryScreen().geometry()
    s["game_toolbar_width"] = 500
    room = screen.width() - 500
    assert room > 1
    return screen.left() + room // 2


def test_it_opens_just_above_the_drawn_minimap_box(bar):
    from maplehelper.ui import gametoolbar
    w, s = bar
    x = _spare_x(s)
    s["minimap_region"] = {"x": x, "y": 300, "w": 200, "h": 200}      # 1:1 on the offscreen screen
    w.refresh()
    assert w.pos() == QPoint(x, 300 - w.height() - gametoolbar.GAP)   # its left edge lined up with the box's


def test_the_place_is_kept_when_moved_and_reopened_there(bar):
    w, s = bar
    x = _spare_x(s)
    w.refresh()
    w.move(x, 70)
    w._remember()                            # the debounced save, as the 400 ms timer would fire it
    assert s["game_toolbar_pos"] == {"x": x, "y": 70}
    w.hide()
    w.refresh()
    assert w.pos() == QPoint(x, 70)


def test_it_opens_at_a_saved_spot(bar):
    w, s = bar
    x = _spare_x(s)
    s["game_toolbar_pos"] = {"x": x, "y": 60}
    w.refresh()
    assert w.pos() == QPoint(x, 60)
