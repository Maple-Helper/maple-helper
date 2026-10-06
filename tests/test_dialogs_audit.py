"""Onboarding, character form, settings and the shared controls: fixes from the dialogs audit (offscreen Qt, no real
CLI, no sign-in, no AI call)."""
import gc
import os
import sys
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(qapp, isolated_store, kb, monkeypatch):
    from maplehelper import providers
    # every account check answers at once: Claude installed but signed out, ChatGPT the same
    for name in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "logged_out", "email": None, "method": None})
        monkeypatch.setattr(type(providers.get(name)), "status", lambda self: "logged_out")
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = "claude"
    return s, isolated_store.Profiles(), kb


class WaitingLogin:
    """A sign-in process still waiting for the browser (what the CLI does until the player finishes there)."""
    returncode = None

    def __init__(self):
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True


# --- API-key mode -----------------------------------------------------------------------------------------------

def test_a_later_signed_out_check_keeps_the_api_key_mode(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    s.set_api_key_mode("claude", True)          # the key checked out (what _on_key_checked stores)
    dlg._ai_ok = True
    for _ in range(2):                          # "Check connection" twice: the CLI itself is signed out
        dlg._on_status("claude", "logged_out")
        assert s.api_key_mode("claude") and dlg._ai_ok and dlg.next.isEnabled() is dlg._current_ok()
    assert "Connected" in dlg.status_label.text()
    dlg.close()


def test_a_real_sign_in_still_ends_the_api_key_mode(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    s.set_api_key_mode("claude", True)
    dlg._on_status("claude", "ok")              # signed in with the account: answers go through it
    assert not s.api_key_mode("claude") and dlg._ai_ok
    dlg.close()


# --- sign-in retry after the timeout --------------------------------------------------------------------------

def test_onboarding_retry_after_the_timeout_starts_a_new_sign_in(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    started = []

    def login(self):
        p = WaitingLogin()
        started.append(p)
        base._login = p                          # what open_login keeps for login_waiting()
        return p
    monkeypatch.setattr(type(providers.get("claude")), "login", login)
    monkeypatch.setattr(base, "_login", None)
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._start_login()
    dlg._poll_left = 1
    dlg._poll_tick()                             # three minutes passed: "click the sign-in button to try again"
    assert "try again" in dlg.login_hint.text() and started[0].killed and not base.login_waiting()
    dlg._start_login()                           # ...and that click really starts a new one
    assert len(started) == 2 and dlg._poll_timer.isActive()
    dlg.close()


def test_settings_retry_after_the_timeout_starts_a_new_sign_in(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    started = []

    def login(self):
        p = WaitingLogin()
        started.append(p)
        base._login = p
        return p
    monkeypatch.setattr(type(providers.get("claude")), "login", login)
    monkeypatch.setattr(base, "_login", None)
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._account_status = "logged_out"
    dlg._switch_account()
    dlg._login_left = 1
    dlg._login_tick()
    assert started[0].killed and dlg._login_proc is None and not base.login_waiting()
    dlg._switch_account()
    assert len(started) == 2 and dlg._login_timer.isActive()
    dlg.close()


# --- language restart -----------------------------------------------------------------------------------------

def test_switching_language_keeps_the_typed_character(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    f = dlg.form
    f.name.setText("Hero")
    f.class_group.buttons()[1].setChecked(True)               # Warrior
    f.level.setValue(35)
    f.job.setCurrentText("Fighter")
    f._job_picked = True                                      # (the player chose it from the menu)
    dlg.key_edit.setText("sk-ant-typed")
    he = next(b for b in dlg.lang_group.buttons() if b.property("lang") == "he")
    dlg._on_language(he)
    assert s["language"] == "he"
    again = Onboarding(s, profiles, kb, lambda *_: "")         # what app.run_onboarding builds next
    again.restart_on_language()
    g = again.form
    assert g.values() == ("Hero", "Warrior", "Fighter", 35) and g.valid()
    assert again.key_edit.text() == "sk-ant-typed" and again.stack.currentIndex() == 1
    assert Onboarding._carried is None                        # used once: a later dialog starts empty
    dlg.close()
    again.close()


def test_switching_language_before_typing_anything_starts_empty(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_language(next(b for b in dlg.lang_group.buttons() if b.property("lang") == "he"))
    again = Onboarding(s, profiles, kb, lambda *_: "")
    again.restart_on_language()
    assert again.form.values() == ("", None, "", 1) and not again.key_edit.text()
    dlg.close()
    again.close()


# --- character form -------------------------------------------------------------------------------------------

def test_edit_dialog_is_titled_edit(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    c = profiles.add("Hero", "Warrior", "Fighter", 35)
    cid = c.id if hasattr(c, "id") else profiles.characters[-1].id
    assert Onboarding(s, profiles, kb, lambda *_: "", edit_id=cid).windowTitle() == "Edit character"
    assert Onboarding(s, profiles, kb, lambda *_: "", only_character=True).windowTitle() == "Add character"


def test_level_field_bound_is_the_saved_profiles_bound_not_a_game_cap(env):
    from maplehelper import store
    from maplehelper.ui.dialogs import LEVEL_FIELD_MAX, Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)      # kept: the form is its child
    form = dlg.form
    assert form.level.hi == LEVEL_FIELD_MAX
    # what the field accepts survives the profile's own repair on the next load
    repaired = store._repair({"id": "x", "name": "Hero", "base_class": "Warrior", "job": "Fighter",
                              "level": LEVEL_FIELD_MAX})
    assert repaired["level"] == LEVEL_FIELD_MAX


def test_character_form_fields_have_screen_reader_names(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)      # kept: the form is its child
    form = dlg.form
    assert form.name.accessibleName() == "Character name"
    assert form.level.edit.accessibleName() == "Current level"
    assert form.level.minus.accessibleName() == "Decrease by one"
    assert form.level.plus.accessibleName() == "Increase by one"
    form.class_group.buttons()[1].setChecked(True)
    form.level.setValue(35)
    form.job.setCurrentText("Page")
    assert form.job.accessibleName() == "Current job: Page"


def test_stepper_steps_with_the_arrow_keys(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper.ui.controls import Stepper
    st = Stepper(1, 5, 4)
    QTest.keyClick(st.edit, Qt.Key_Up)
    QTest.keyClick(st.edit, Qt.Key_Up)                      # stops at the top
    assert st.value() == 5
    QTest.keyClick(st.edit, Qt.Key_Down)
    assert st.value() == 4 and st.edit.text() == "4"


# --- settings -------------------------------------------------------------------------------------------------

def test_f12_is_not_offered_on_windows_and_a_saved_f12_falls_back(env, monkeypatch):
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    monkeypatch.setattr(sys, "platform", "win32")
    assert dialogs.hotkey_choices()[-1] == "F11"
    s["hotkey_toggle"] = "F12"
    dlg = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
    assert "F12" not in dlg.hk_toggle._items
    assert dlg.hk_toggle.currentText() == "F9"                 # the default, not the list's first (F1)
    dlg.close()
    monkeypatch.setattr(sys, "platform", "darwin")
    assert dialogs.hotkey_choices()[-1] == "F12"               # macOS has it
    assert "F11" not in dialogs.hotkey_choices()               # macOS shows the desktop on F11
    assert "F11" in dialogs.hotkey_choices(("F11", "F10"))     # a saved F11 is still shown


def test_mac_keys_say_fn_once_under_the_pickers(env, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from maplehelper.i18n import I18n
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    hint = I18n("en")("hotkey_fn_mac")
    for mac in (True, False):
        monkeypatch.setattr(I18n, "mac", mac)
        dlg = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
        shown = [lb.text() for lb in dlg.findChildren(QLabel) if "fn+F9" in lb.text()]
        assert shown == ([hint] if mac else [])
        assert ("F11" not in hint) and dlg.hk_voice.accessibleDescription() == (hint if mac else "")
        dlg.close()


def test_settings_controls_have_screen_reader_names(env):
    from maplehelper.ui.controls import Switch
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    assert all(sw.accessibleName() for sw in dlg.findChildren(Switch))
    assert dlg.telemetry.accessibleDescription()                # its hint comes along
    assert dlg.hk_toggle.accessibleName() == "Open/close key: F9"
    assert dlg.appearance.accessibleName() == "Appearance"
    assert [b.accessibleName() for b in dlg.font.group.buttons()] == ["Small text", "Medium text", "Large text"]
    dlg.close()


def test_closed_settings_windows_are_freed(env, qapp):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    refs = []
    for _ in range(3):
        dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
        refs.append(weakref.ref(dlg))
        dlg.deleteLater()
        del dlg
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()
    gc.collect()
    assert [r for r in refs if r() is not None] == []


# --- section separators -----------------------------------------------------------------------------------------

def test_a_hidden_row_leaves_no_separator_behind(qapp):
    from PySide6.QtWidgets import QFrame, QLabel
    from maplehelper.ui.controls import Section
    sec = Section("H", rtl=False)
    sec.add_widget(QLabel("shown"))
    hint = QLabel("hint")
    hint.hide()
    sec.add_widget(hint)
    sec.show()
    sep = [f for f in sec.findChildren(QFrame, "Separator")]
    assert len(sep) == 1 and not sep[0].isVisible()
    hint.show()
    assert sep[0].isVisible()
    hint.hide()
    assert not sep[0].isVisible()
    sec.close()


def test_a_hidden_first_row_leaves_no_separator_on_top(qapp):
    from PySide6.QtWidgets import QFrame, QLabel
    from maplehelper.ui.controls import Section
    sec = Section("H", rtl=False)
    first = QLabel("first")
    first.hide()
    sec.add_widget(first)
    sec.add_widget(QLabel("second"))
    sec.show()
    assert not sec.findChildren(QFrame, "Separator")[0].isVisible()
    sec.close()


# --- keyboard -------------------------------------------------------------------------------------------------

def test_wrap_link_is_reachable_and_runs_from_the_keyboard(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper.ui.controls import WrapLink
    link = WrapLink("Read my stats from the screen")
    hits = []
    link.clicked.connect(lambda: hits.append(1))
    assert link.focusPolicy() & Qt.TabFocus
    assert link.accessibleName() == "Read my stats from the screen"
    QTest.keyClick(link, Qt.Key_Return)
    QTest.keyClick(link, Qt.Key_Space)
    assert hits == [1, 1]


def test_a_long_microphone_name_never_widens_settings(env, monkeypatch):
    """"Microphone (Logitech PRO X Wireless Gaming Headset)" pushed the window wider than the screen showed."""
    from PySide6.QtWidgets import QScrollArea
    from maplehelper import voice
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    monkeypatch.setattr(voice, "input_devices",
                        lambda: ["Microphone (USB microphone)", "Microphone (Logitech PRO X Wireless Gaming Headset)"])
    s.data["microphone"] = "Microphone (Logitech PRO X Wireless Gaming Headset)"
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg.show()
    area = dlg.findChild(QScrollArea)
    assert area.widget().minimumSizeHint().width() <= area.viewport().width()
    assert dlg.mic.currentIndex() == 2 and dlg._mic_value() == s.data["microphone"]
    assert "Microphone (" not in dlg.mic.text()
    dlg.mic.addItems(["Microphone Array (Realtek(R) High Definition Audio with a very long driver name)"])
    assert dlg.mic.sizeHint().width() <= dlg.mic.text_width + 48
    dlg.close()
