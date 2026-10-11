"""The bar over the game (ui/gametoolbar.py): shown from its own setting, only what is about the player's map — its
NPCs, Monsters and Loot Log buttons each flipping its window's setting (the window follows the signal) — and a
Tools button asking for the app's Tools window, the ✕ turning the bar off, and its place on the screen — above
the drawn minimap box, at the saved spot, and the spot kept after a drag. Its width is the player's too: the
buttons wrap into as many rows as the width leaves room for, and the ⚙ panel's choices (the glass, the accent,
the transparency) are written and followed live — by the bar, and by the NPCs and Monsters windows over the game.
By default all of them look like the app itself, light or dark as its appearance, and so does the Tools menu."""
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
    """The glass every window paints is module state: a test that picks a preset puts the default back, or the
    next test file's windows paint midnight."""
    from maplehelper.ui import npcoverlay
    yield
    npcoverlay.apply_look({})


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


def test_the_buttons_always_show_words_and_no_icons_setting_is_left(bar):
    """The icons mode is gone (the owner, 2026-10-11): no setting, no ⚙ panel toggle, no Settings row — the
    buttons show their names, a tooltip each."""
    from maplehelper import report, store
    from maplehelper.i18n import STRINGS
    from maplehelper.ui import gametoolbar
    w, _ = bar
    w.refresh()
    w._gear.click()
    assert "game_toolbar_icons" not in store.DEFAULT_SETTINGS
    assert "game_toolbar_icons" not in report.REPORT_SETTINGS
    assert not hasattr(gametoolbar, "ICONS") and not hasattr(w._panel, "_icons")
    assert [b.text() for b, _ in w._buttons()] == ["NPCs", "Monsters", "Loot Log", "Tools"]
    assert w._tools.toolTip() == w.t("tb_tools_tip")
    assert "Emoji" not in w.styleSheet()
    assert "tbs_icons" not in STRINGS and "tbs_icons_hint" not in STRINGS


def test_an_opacity_choice_writes_the_shared_setting(bar):
    """The panel's transparency writes npc_overlay_opacity, the one setting every window over the game reads."""
    w, s = bar
    w.refresh()
    w._gear.click()
    w._panel._pick_opacity(0.5)
    assert s["npc_overlay_opacity"] == 0.5
    assert w._opacity == 0.5


def test_the_panel_offers_the_opacity_steps(bar):
    """The ⚙ panel is the one place to pick the transparency (Settings > Minimap no longer offers it)."""
    from maplehelper.ui import dialogs
    from maplehelper.ui.npcoverlay import OPACITY_STEPS
    w, _ = bar
    w._gear.click()
    assert list(w._panel._steps) == [f"{v:g}" for v in OPACITY_STEPS]
    assert [b.text() for b in w._panel._steps.values()] == [f"{round(v * 100)}%" for v in OPACITY_STEPS]
    assert not hasattr(dialogs, "OPACITY_CHOICES")


def test_settings_has_no_look_rows(bar, isolated_store, kb, monkeypatch):
    """The windows' glass colour, accent and transparency are the ⚙ panel's alone (the owner, 2026-10-11): Settings
    shows none of them, and saving it leaves the panel's choices as they were."""
    from maplehelper import providers
    from maplehelper.ui.dialogs import SettingsDialog
    for name in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "logged_out", "email": None, "method": None})
        monkeypatch.setattr(type(providers.get(name)), "status", lambda self: "logged_out")
    _, s = bar
    s["overlay_tint"], s["overlay_accent"], s["npc_overlay_opacity"] = "wine", "pink", 0.5
    dlg = SettingsDialog(s, isolated_store.Profiles(), kb, lambda *_: "")
    try:
        for name in ("npc_opacity", "overlay_tint", "overlay_accent", "overlay_shots"):
            assert not hasattr(dlg, name), name
        dlg._save()
    finally:
        dlg.close()
    assert (s["overlay_tint"], s["overlay_accent"], s["npc_overlay_opacity"]) == ("wine", "pink", 0.5)


def test_a_panel_choice_signals_the_app_to_restyle_every_window(bar):
    w, _ = bar
    seen = []
    w.look_changed.connect(lambda: seen.append(True))
    w.refresh()
    w._gear.click()
    w._panel._pick_accent("teal")
    assert seen == [True]


def test_the_default_look_is_the_apps(bar, monkeypatch):
    """Nothing picked: "app" for both — the app's own glass in its appearance, its orange lighting a lit button,
    the words on the buttons."""
    from maplehelper.ui import npcoverlay, theme
    monkeypatch.setattr(theme, "HIGH_CONTRAST", False)
    w, s = bar
    assert s["overlay_tint"] == "app" and s["overlay_accent"] == "app"
    npcoverlay.apply_look(s)
    w.refresh()
    assert npcoverlay.current_tint() == theme.PALETTES[s["appearance"]]["glass"]
    assert npcoverlay.ACCENTS["app"][1] == theme.ORANGE_DEEP and theme.ORANGE_DEEP in w.styleSheet()
    assert w._windows["npc_overlay"].text() == "NPCs"


def _lum(css: str) -> float:
    from maplehelper.ui import theme
    c = theme.qcolor(css)
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


@pytest.mark.parametrize("appearance", ["light", "dark"])
def test_the_default_look_follows_the_apps_appearance(bar, monkeypatch, appearance):
    """Light: light glass and dark text (the app's own light colours); dark: dark glass and light text — in the
    bar's style sheet and in the glass _paint_glass paints, switched live on the app's restyle (apply_look and
    refresh, as app.on_settings_changed runs them after a Save)."""
    from maplehelper.ui import npcoverlay, theme
    monkeypatch.setattr(theme, "HIGH_CONTRAST", False)
    w, s = bar
    s["appearance"] = "dark" if appearance == "light" else "light"
    npcoverlay.apply_look(s)
    w.refresh()
    s["appearance"] = appearance                   # the app's appearance changed and saved
    npcoverlay.apply_look(s)
    w.refresh()
    c = theme.PALETTES[appearance]
    look = npcoverlay.look_palette(s)
    assert npcoverlay.current_tint() == c["glass"] == look["glass"]
    assert look["text"] == c["text"] and look["muted"] == c["muted"]
    assert f"#Title {{ color: {c['text']};" in w.styleSheet()
    glass = sum(c["glass"]) / 3
    if appearance == "light":
        assert glass > 200 and _lum(c["text"]) < 60          # dark text on light glass
    else:
        assert glass < 60 and _lum(c["text"]) > 200          # light text on dark glass


def test_a_dark_tint_keeps_its_light_text_in_the_apps_light_look(bar):
    from maplehelper.ui import npcoverlay
    _, s = bar
    s["appearance"], s["overlay_tint"] = "light", "midnight"
    look = npcoverlay.look_palette(s)
    assert look["glass"] == npcoverlay.TINTS["midnight"][1] and look["text"] == npcoverlay.DARK_LOOK["text"]
    s["overlay_tint"] = "no-such-tint"                         # a stale name: the app's own
    assert npcoverlay.look_palette(s)["glass"] == npcoverlay.look_palette({"appearance": "light"})["glass"]


def _menu_rule(sheet: str, selector: str) -> str:
    import re
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", sheet)
    assert m, selector
    return m.group(1)


@pytest.mark.parametrize("appearance,tint", [("light", "app"), ("dark", "app"), ("light", "dark"),
                                             ("light", "wine")])
def test_the_tools_menu_items_are_in_the_bars_text_colour(bar, appearance, tint):
    """The bug in the owner's screenshot: the menu's rows dark grey on its dark glass (the app's light-look
    QMenu::item colour won over the menu's own). Every row state now sets its colour from the bar's palette."""
    from maplehelper.ui import npcoverlay
    w, s = bar
    s["appearance"], s["overlay_tint"] = appearance, tint
    w._tools.click()
    sheet, look = w._menu.styleSheet(), npcoverlay.look_palette(s)
    assert f"color: {look['text']};" in _menu_rule(sheet, "QMenu::item ")
    assert f"color: {look['text']};" in _menu_rule(sheet, "QMenu::item:selected")
    assert f"color: {look['muted']};" in _menu_rule(sheet, "QMenu::item:disabled")
    w._menu.hide()


def test_the_tools_menu_reads_light_on_dark_glass_under_the_apps_light_style(bar, qapp):
    """Rendered, under the app's own light style sheet (the one whose dark menu text won): the dark glass
    menu's rows draw light pixels, its text."""
    from PySide6.QtGui import QColor

    from maplehelper.ui import theme
    w, s = bar
    before = qapp.styleSheet()
    theme.set_mode("light")
    qapp.setStyleSheet(theme.stylesheet("Segoe UI", 14))
    try:
        s["appearance"], s["overlay_tint"] = "light", "dark"
        w._tools.click()
        img = w._menu.grab().toImage()
        light = sum(1 for x in range(0, img.width(), 2) for y in range(0, img.height(), 2)
                    if QColor(img.pixel(x, y)).lightness() > 180)
        assert light > 20
        w._menu.hide()
    finally:
        qapp.setStyleSheet(before)
        theme.set_mode("dark")


def test_the_tools_menu_mirrors_in_hebrew(bar):
    """Right to left in Hebrew (its rows' padding the same both sides), left to right in English."""
    w, _ = bar
    w.apply_language(I18n("he"))
    w._tools.click()
    assert w._menu.layoutDirection() == Qt.RightToLeft
    assert "padding: 5px 14px;" in _menu_rule(w._menu.styleSheet(), "QMenu::item ")
    w._menu.hide()
    w.apply_language(I18n("en"))
    w._tools.click()
    assert w._menu.layoutDirection() == Qt.LeftToRight
    w._menu.hide()


def test_the_panels_app_swatch_is_the_apps_glass(bar):
    """The "app" chip shows the app's glass as it is now (light here), ringed while it is the one chosen."""
    w, s = bar
    s["appearance"] = "light"
    w._gear.click()
    chips = {b.property("key"): b for b in w._panel._tints.findChildren(QPushButton)}
    assert list(chips)[0] == "app" and chips["app"].isChecked()
    assert "#f2f2f7" in chips["app"].styleSheet()
    assert chips["app"].toolTip() == "Like Maple Helper"


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
