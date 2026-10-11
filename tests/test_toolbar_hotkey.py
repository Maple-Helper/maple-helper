"""Bringing the bar over the game back after its ✕ (the owner closed it and found no way back): its show/hide key
(game_toolbar_hotkey: keys.py names it, the OS layers bind it, the app toggles the bar on it and lets go of it while
a key box listens), the tray's Show/Hide the toolbar, the toast after the ✕, and an open Settings window following
the bar instead of saving it back on (its switch kept reading On after the ✕)."""
import os
import sys

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest

from maplehelper import keys
from maplehelper.i18n import I18n

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------ the key's name

def test_a_key_name_splits_and_joins_in_one_order():
    assert keys.split("Shift+F11") == (frozenset({"Shift"}), "F11")
    assert keys.split("Alt+Ctrl+T") == (frozenset({"Ctrl", "Alt"}), "T")
    assert keys.join({"Shift", "Alt", "Ctrl"}, "F5") == "Ctrl+Alt+Shift+F5"
    for bad in ("", None, "Shift+", "Hyper+F1", "Shift+Shift+F1", "F25", "Ctrl+!"):
        assert keys.split(bad) is None, bad


@pytest.mark.parametrize("name,ok", [("Shift+F11", True), ("F11", False), ("Ctrl+T", True), ("Shift+T", False),
                                     ("Alt+7", True), ("Meta+PgUp", True), ("Ctrl+F24", True), ("", False)])
def test_a_usable_key_has_the_modifier_the_game_and_typing_leave_free(name, ok):
    """The game binds the plain F-keys, and Shift alone with a letter would take the capitals from every program."""
    assert keys.usable(name, mac=False) is ok


def test_the_key_the_system_keeps_is_refused():
    """Windows never lets a program register F12; macOS shows the desktop on F11 and still says it registered."""
    assert not keys.usable("Shift+F12", mac=False) and keys.usable("Shift+F12", mac=True)
    assert not keys.usable("Shift+F11", mac=True) and keys.usable("Shift+F11", mac=False)


def test_the_key_reads_as_each_system_writes_it():
    assert keys.label("Ctrl+Alt+Shift+Meta+F2", mac=False) == "Ctrl+Alt+Shift+Win+F2"
    assert keys.label("Ctrl+Alt+Shift+Meta+F2", mac=True) == "⌃⌥⇧⌘F2"
    assert keys.label("", mac=False) == ""


def test_a_native_code_names_the_key_qt_names_by_its_character():
    assert keys.from_native(0x31, mac=False) == "1" and keys.from_native(0x7A, mac=False) == "F11"
    assert keys.from_native(17, mac=True) == "T" and keys.from_native(0xFFFF, mac=False) is None


def test_the_default_key_is_one_this_system_can_bind():
    from maplehelper.store import DEFAULT_SETTINGS
    key = DEFAULT_SETTINGS["game_toolbar_hotkey"]
    assert keys.usable(key)
    assert key == ("Shift+F10" if sys.platform == "darwin" else "Shift+F11")


def test_the_key_is_reported_with_the_settings():
    from maplehelper import report
    assert "game_toolbar_hotkey" in report.REPORT_SETTINGS


# ------------------------------------------------------------ the OS layers

@pytest.mark.skipif(sys.platform != "win32", reason="RegisterHotKey is Windows'")
def test_windows_binds_the_key_with_its_modifiers(monkeypatch):
    from maplehelper import winapi
    calls = []

    class User32:
        def RegisterHotKey(self, hwnd, hid, mods, vk):
            calls.append((hid, mods, vk))
            return 1

    monkeypatch.setattr(winapi, "user32", User32())
    assert winapi.register_hotkey(5, 1, "Ctrl+Shift+F11")
    assert calls == [(1, winapi.MOD_NOREPEAT | winapi.MOD_CONTROL | winapi.MOD_SHIFT, 0x7A)]
    assert winapi.register_hotkey(5, 1, "Alt+Meta+K")
    assert calls[-1] == (1, winapi.MOD_NOREPEAT | winapi.MOD_ALT | winapi.MOD_WIN, ord("K"))
    assert not winapi.register_hotkey(5, 1, "Shift+F12") and not winapi.register_hotkey(5, 1, "T")
    assert len(calls) == 2                      # never asked Windows for a key it can't be


def test_a_mac_binds_the_key_by_its_code_and_carbon_modifiers():
    from maplehelper import macapi
    assert macapi.carbon_combo("Shift+F10") == (109, 1 << 9)
    assert macapi.carbon_combo("Ctrl+Alt+T") == (17, (1 << 8) | (1 << 11))     # Qt's Ctrl is ⌘ on a Mac
    assert macapi.carbon_combo("Meta+F1") == (122, 1 << 12)
    assert macapi.carbon_combo("Shift+F11") is None and macapi.carbon_combo("Ctrl+F21") is None


# ------------------------------------------------------------ the app: the key, the tray item, the ✕

class FakeHotkeys(QObject):
    pressed = Signal(int)

    def __init__(self, ok: bool = True):
        super().__init__()
        self.ok, self.calls = ok, []

    def register(self, hotkey_id, key_name):
        self.calls.append(("register", hotkey_id, key_name))
        return self.ok

    def unregister(self, hotkey_id):
        self.calls.append(("unregister", hotkey_id))

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def app(qapp, isolated_store, monkeypatch):
    """The app's own methods on a bare instance: a real bar, a stand-in for the OS hotkeys, toasts collected."""
    from maplehelper.app import MapleHelperApp
    from maplehelper.ui.gametoolbar import GameToolbar
    from maplehelper.ui.hotkeybox import CAPTURE
    me = MapleHelperApp.__new__(MapleHelperApp)
    me.qapp = qapp
    me.settings = isolated_store.Settings()
    me.settings["language"] = "en"
    me.game_toolbar = GameToolbar(me.settings, I18n("en"))
    me.game_toolbar.closed.connect(me._game_toolbar_closed)
    me.game_toolbar.hotkey_changed.connect(me._toolbar_hotkey_changed)
    me.game_toolbar.refresh()
    me.hotkeys = FakeHotkeys()
    me.hotkeys.pressed.connect(me._on_hotkey)
    CAPTURE.listening.connect(me._hotkey_capture)
    me.toasts = []
    me.toast = lambda title, body="", timeout_ms=0, on_click=None: me.toasts.append((title, body))
    yield me
    CAPTURE.listening.disconnect(me._hotkey_capture)
    if getattr(me, "tray", None) is not None:
        me.tray.hide()
    me.game_toolbar.close()
    if me.game_toolbar._panel is not None:
        me.game_toolbar._panel.close()


def _label(me) -> str:
    return keys.label(me.settings["game_toolbar_hotkey"])


def test_the_key_is_bound_once_and_a_new_one_in_its_place(app):
    from maplehelper.app import HOTKEY_TOOLBAR
    app.register_toolbar_hotkey()
    app.register_toolbar_hotkey()                 # every Save runs it: the same key isn't bound again
    assert app.hotkeys.calls == [("register", HOTKEY_TOOLBAR, app.settings["game_toolbar_hotkey"])]
    app.settings["game_toolbar_hotkey"] = "Ctrl+Alt+B"
    app.register_toolbar_hotkey()
    assert app.hotkeys.calls[-1] == ("register", HOTKEY_TOOLBAR, "Ctrl+Alt+B")
    app.settings["game_toolbar_hotkey"] = ""
    app.register_toolbar_hotkey()
    assert app.hotkeys.calls[-1] == ("unregister", HOTKEY_TOOLBAR) and app.toasts == []


def test_a_taken_key_says_so_once(app):
    app.hotkeys.ok = False
    app.register_toolbar_hotkey()
    app.register_toolbar_hotkey()
    body = (f"{_label(app)} is taken by the system or another program. Pick another in Settings > Minimap or with the "
            "toolbar's gear button.")
    assert app.toasts == [("The toolbar shortcut isn't set", body)]


def test_a_save_in_settings_binds_the_key_picked_there():
    from unittest.mock import MagicMock

    from maplehelper import app as app_mod
    fake = MagicMock()
    fake.settings = {"language": "en", "provider": "claude", "telemetry": False, "presence": False,
                     "saver_mode": False, "appearance": "dark", "font_size": 14}
    fake._look, fake._look_provider = ("en", "dark", 14), "claude"
    app_mod.MapleHelperApp.on_settings_changed(fake)
    assert fake.register_toolbar_hotkey.called


def test_the_key_shows_and_hides_the_bar_and_its_setting(app):
    from maplehelper.app import HOTKEY_TOOLBAR
    assert app.game_toolbar.isVisible() and app.settings["game_toolbar"] is True
    app.hotkeys.pressed.emit(HOTKEY_TOOLBAR)
    assert not app.game_toolbar.isVisible() and app.settings["game_toolbar"] is False
    app.hotkeys.pressed.emit(HOTKEY_TOOLBAR)
    assert app.game_toolbar.isVisible() and app.settings["game_toolbar"] is True
    app.hotkeys.pressed.emit(99)                  # not the bar's: nothing happens
    assert app.game_toolbar.isVisible()


def test_the_tray_item_says_what_a_click_does_and_does_it(app):
    app.make_tray()
    item = app._tray_toolbar
    assert item in app._tray_menu.actions()
    assert item.text() == f"Hide the toolbar  ·  {_label(app)}"
    item.trigger()
    assert not app.game_toolbar.isVisible() and app.settings["game_toolbar"] is False
    assert item.text() == f"Show the toolbar  ·  {_label(app)}"
    item.trigger()
    assert app.game_toolbar.isVisible() and item.text().startswith("Hide the toolbar")
    app.game_toolbar._close.click()               # the bar's own ✕: the item follows
    assert item.text().startswith("Show the toolbar")
    app.settings["game_toolbar_hotkey"] = ""      # no key: the item names none
    app._tray_menu.aboutToShow.emit()
    assert item.text() == "Show the toolbar"


def test_the_tray_item_comes_with_the_bar(app):
    """A tray made before the bar exists (the first-run setup) has no item to break on."""
    bar = app.game_toolbar
    del app.game_toolbar
    try:
        app.make_tray()
        assert app._tray_toolbar is None
        assert [a.text() for a in app._tray_menu.actions() if "toolbar" in a.text()] == []
    finally:
        app.game_toolbar = bar


@pytest.mark.parametrize("mac", [False, True])
def test_the_x_says_how_to_bring_the_bar_back(app, monkeypatch, mac):
    monkeypatch.setattr(I18n, "mac", mac)
    app.game_toolbar._close.click()
    title, body = app.toasts[-1]
    assert title == "The toolbar is hidden"
    assert ("click Maple Helper's icon in the menu bar" if mac else "right-click Maple Helper's icon by the clock") in body
    assert "Show the toolbar" in body and f"press {_label(app)}" in body and "Settings > Minimap" in body
    app.settings["game_toolbar_hotkey"] = ""
    app.settings["game_toolbar"] = True
    app.game_toolbar.refresh()
    app.game_toolbar._close.click()
    assert "press" not in app.toasts[-1][1] and "Show the toolbar" in app.toasts[-1][1]


def test_a_key_box_listening_has_the_app_let_go_of_the_key(app):
    """Pressed while bound, the key would toggle the bar instead of reaching the box: let go, then bound again."""
    from maplehelper.app import HOTKEY_TOOLBAR
    app.register_toolbar_hotkey()
    app.game_toolbar._gear.click()
    box = app.game_toolbar._panel._hotkey
    QTest.mouseClick(box.box, Qt.LeftButton)
    assert app.hotkeys.calls[-1] == ("unregister", HOTKEY_TOOLBAR)
    QTest.keyClick(box.box, Qt.Key_Escape)
    assert app.hotkeys.calls[-1] == ("register", HOTKEY_TOOLBAR, app.settings["game_toolbar_hotkey"])


def test_a_key_that_wont_bind_is_not_toasted_again_by_a_key_box(app):
    """The player opens the key box to move off a key another program holds: no second "taken" toast meanwhile,
    and the new key is bound the moment it's picked (never the old one in between)."""
    from maplehelper.app import HOTKEY_TOOLBAR
    app.hotkeys.ok = False
    app.register_toolbar_hotkey()
    assert len(app.toasts) == 1
    app.hotkeys.ok = True
    app.game_toolbar._gear.click()
    box = app.game_toolbar._panel._hotkey
    QTest.mouseClick(box.box, Qt.LeftButton)
    QTest.keyClick(box.box, Qt.Key_Escape)
    assert len(app.toasts) == 1 and app.hotkeys.calls == [("register", HOTKEY_TOOLBAR, app.settings["game_toolbar_hotkey"])]
    QTest.mouseClick(box.box, Qt.LeftButton)
    QTest.keyClick(box.box, Qt.Key_F7, Qt.ShiftModifier)
    assert app.hotkeys.calls[1:] == [("register", HOTKEY_TOOLBAR, "Shift+F7")] and len(app.toasts) == 1


def test_a_key_picked_in_the_panel_is_bound_and_named_in_the_tray(app):
    from maplehelper.app import HOTKEY_TOOLBAR
    app.register_toolbar_hotkey()
    app.make_tray()
    app.game_toolbar._gear.click()
    box = app.game_toolbar._panel._hotkey
    QTest.mouseClick(box.box, Qt.LeftButton)
    QTest.keyClick(box.box, Qt.Key_F8, Qt.ControlModifier)
    assert app.settings["game_toolbar_hotkey"] == "Ctrl+F8"
    assert ("register", HOTKEY_TOOLBAR, "Ctrl+F8") in app.hotkeys.calls
    assert app._tray_toolbar.text() == f"Hide the toolbar  ·  {keys.label('Ctrl+F8')}"


def test_quitting_lets_go_of_the_key(app, monkeypatch):
    """shutdown() frees the key for other programs (the rest of shutdown is the chat's, stubbed here)."""
    from unittest.mock import MagicMock

    from maplehelper import app as app_mod
    monkeypatch.setattr(app_mod.telemetry, "flush", lambda: None)
    monkeypatch.setattr(app_mod.presence, "stop", lambda: None)
    fake = MagicMock()
    fake.hotkeys = app.hotkeys
    fake.pending_installer = None
    app_mod.MapleHelperApp.shutdown(fake)
    assert app.hotkeys.calls[-1] == ("close",)


# ------------------------------------------------------------ Settings > Minimap

@pytest.fixture
def settings_dialog(app, kb, isolated_store, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import SettingsDialog
    for name in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "logged_out", "email": None, "method": None})
        monkeypatch.setattr(type(providers.get(name)), "status", lambda self: "logged_out")
    app.settings["provider"] = "claude"
    dlg = SettingsDialog(app.settings, isolated_store.Profiles(), kb, lambda *_: "")
    app._windows = {"settings": dlg}
    # the app's Save path, as open_settings wires it: the windows over the game follow the saved settings
    dlg.changed.connect(app._overlay_look_changed)
    app._apply_overlay_capture = lambda: None
    yield dlg
    dlg.close()


def test_an_open_settings_window_follows_the_bars_x_and_its_save_shows_it_again(app, settings_dialog):
    """The owner's "Settings says the toolbar is on but I don't see it": the ✕ turned it off while Settings was
    open, whose switch kept reading On, so closing Settings did nothing. Now the switch follows (and nothing is
    left unsaved); turning it On and saving shows the bar again."""
    dlg = settings_dialog
    assert dlg.game_toolbar.isChecked() and app.game_toolbar.isVisible()
    app.game_toolbar._close.click()
    assert not dlg.game_toolbar.isChecked() and not dlg.unsaved()
    dlg.game_toolbar.setChecked(True)
    assert dlg.unsaved()
    dlg._save()
    assert app.settings["game_toolbar"] is True and app.game_toolbar.isVisible()


def test_an_open_settings_window_follows_the_key_and_the_tray(app, settings_dialog):
    from maplehelper.app import HOTKEY_TOOLBAR
    dlg = settings_dialog
    app.make_tray()
    app._tray_toolbar.trigger()                   # hidden from the tray
    assert not dlg.game_toolbar.isChecked() and not dlg.unsaved()
    app.hotkeys.pressed.emit(HOTKEY_TOOLBAR)      # back with the key
    assert dlg.game_toolbar.isChecked() and not dlg.unsaved()
    app.game_toolbar._gear.click()                # a key picked in the panel: Settings' box shows it
    box = app.game_toolbar._panel._hotkey
    QTest.mouseClick(box.box, Qt.LeftButton)
    QTest.keyClick(box.box, Qt.Key_G, Qt.ControlModifier | Qt.ShiftModifier)
    assert dlg.toolbar_hotkey.key() == "Ctrl+Shift+G" and not dlg.unsaved()
    dlg._save()
    assert app.settings["game_toolbar_hotkey"] == "Ctrl+Shift+G"      # not put back to the old key


def test_settings_keeps_the_bars_switch_and_key_and_leaves_its_look_to_the_bar(app, settings_dialog):
    """The NPCs-here window's switch, the glass and accent colours, the icons mode and the transparency moved to
    the bar and its ⚙ panel: a Settings window open meanwhile no longer saves the old ones back over them."""
    dlg = settings_dialog
    for gone in ("npc_overlay", "npc_opacity", "overlay_tint", "overlay_accent", "toolbar_icons"):
        assert not hasattr(dlg, gone), gone
    assert dlg.toolbar_hotkey.key() == app.settings["game_toolbar_hotkey"]
    app.settings["overlay_tint"], app.settings["npc_overlay"], app.settings["npc_overlay_opacity"] = "wine", True, 0.5
    app.settings["game_toolbar_icons"], app.settings["overlay_accent"] = True, "teal"
    dlg.hidden_portals.setChecked(not dlg.hidden_portals.isChecked())
    dlg._save()
    assert (app.settings["overlay_tint"], app.settings["npc_overlay"], app.settings["npc_overlay_opacity"],
            app.settings["game_toolbar_icons"], app.settings["overlay_accent"]) == ("wine", True, 0.5, True, "teal")


def test_settings_key_box_picks_a_key_that_waits_for_save(app, settings_dialog):
    dlg = settings_dialog
    box = dlg.toolbar_hotkey
    QTest.mouseClick(box.box, Qt.LeftButton)
    QTest.keyClick(box.box, Qt.Key_F9, Qt.AltModifier)
    assert box.key() == "Alt+F9" and dlg.unsaved() and app.settings["game_toolbar_hotkey"] != "Alt+F9"
    dlg._save()
    assert app.settings["game_toolbar_hotkey"] == "Alt+F9"
