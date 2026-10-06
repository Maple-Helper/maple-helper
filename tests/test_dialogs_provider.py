"""Choosing Claude or Codex in onboarding and settings (offscreen Qt, no real CLI calls)."""
import os
import threading

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
    # every account check answers instantly: Claude signed in, Codex installed but signed out
    monkeypatch.setattr(type(providers.get("claude")), "account", lambda self: {"status": "ok", "email": "a@b.c"})
    monkeypatch.setattr(type(providers.get("codex")), "account",
                        lambda self: {"status": "logged_out", "email": None, "method": None})
    s = isolated_store.Settings()
    s["language"] = "en"
    from PySide6.QtWidgets import QApplication
    windows = set(QApplication.topLevelWidgets())
    before = set(threading.enumerate())
    yield s, isolated_store.Profiles(), kb
    # the dialogs' account checks run on threads: they end before their dialog is freed (an emit into a deleted
    # dialog crashed the next file's first event pump, test_overlay_audit's, the review UI-6)
    for th in set(threading.enumerate()) - before:
        th.join(timeout=10)
    # a dialog a test left open kept its sign-in timer: it fired in the next file's first event pump, with this
    # file's fakes gone, and the run died of an access violation (test_overlay_audit after these, the review UI-6)
    from PySide6.QtCore import QTimer
    for w in set(QApplication.topLevelWidgets()) - windows:
        for timer in w.findChildren(QTimer):
            timer.stop()
        w.close()


def test_onboarding_relabels_the_connect_page_for_codex(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    assert dlg.install_btn.text() == "Install Claude Code (Anthropic's official tool)"
    dlg._on_provider("codex")
    assert s["provider"] == "codex"
    assert dlg.install_btn.text() == "Install ChatGPT (Codex, OpenAI's official tool)"
    assert dlg.login_btn.text() == "Sign in with ChatGPT"
    assert "OpenAI" in dlg.key_edit.placeholderText()
    assert "OpenAI" in dlg.privacy_label.text()


def test_onboarding_relabels_the_connect_page_for_gemini(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    monkeypatch.setattr(type(providers.get("gemini")), "account", lambda self: {"status": "not_installed", "email": None})
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("gemini")
    assert s["provider"] == "gemini"
    assert dlg.install_btn.text() == "Install Gemini (Google Antigravity, Google's official tool)"
    assert dlg.login_btn.text() == "Sign in with Google"
    assert "AIza" in dlg.key_edit.placeholderText()
    assert "the AI you chose (currently Google's Gemini)" in dlg.privacy_label.text()


def test_offline_says_so_and_offers_no_sign_in(env, monkeypatch):
    """Gemini can't tell offline whether it's signed in: "no connection", never "sign in" (that fails too)."""
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding, SettingsDialog
    monkeypatch.setattr(type(providers.get("gemini")), "account", lambda self: {"status": "offline", "email": None})
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("gemini")
    dlg._on_status("gemini", "offline")
    assert "Couldn't reach Gemini" in dlg.status_label.text() and dlg.login_btn.isHidden()
    assert dlg.install_btn.isHidden() and not dlg._ai_ok
    s["provider"] = "gemini"
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_account({"status": "offline", "email": None, "provider": "gemini"})
    assert "Couldn't reach Gemini" in dlg.account_label.text()
    assert dlg.switch_btn.isHidden() and dlg.install_btn.isHidden() and dlg.logout_btn.isHidden()


def test_onboarding_ignores_a_late_status_for_the_other_provider(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    dlg._on_status("claude", "ok")         # the check started before the switch
    assert not dlg._ai_ok
    dlg._on_status("codex", "ok")
    assert dlg._ai_ok


def test_settings_account_text_follows_the_provider(env):
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    s["provider"] = "codex"
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_account({"status": "ok", "email": "a@b.c", "provider": "claude"})   # stale: ignored
    assert "a@b.c" not in dlg.account_label.text()
    dlg._on_account({"status": "ok", "email": None, "method": "chatgpt", "provider": "codex"})
    assert dlg.account_label.text() == "Signed in with ChatGPT"


def test_settings_switching_provider_tells_the_app_on_save(env):
    """UX-6: the provider waits for Save like every other setting, then the app moves over to it."""
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    seen = []
    dlg.account_changed.connect(lambda: seen.append(s["provider"]))
    dlg._on_provider("codex")
    assert seen == [] and s["provider"] == "claude" and dlg.unsaved()
    dlg._save()
    assert seen == ["codex"] and s["provider"] == "codex"


def test_settings_model_pick_applies_on_save(env):
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    s["provider"] = "claude"
    s["last_model"] = {"claude": "claude-sonnet-5"}
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    seen = []
    dlg.account_changed.connect(lambda: seen.append(s["model"]))
    assert dlg.model_pick.text() == "Sonnet (recommended)" and "Sonnet 5" in dlg.model_hint.text()
    dlg._on_model(dlg._model_values.index("opus"))
    assert s["model"] != "opus" and seen == [] and dlg.unsaved()
    dlg._save()
    assert s["model"] == "opus" and seen == ["opus"]


def test_onboarding_sign_in_lets_the_login_window_show_and_offers_reinstall(env, monkeypatch):
    from PySide6.QtCore import Qt
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: object())
    dlg._start_login()
    assert not dlg.windowFlags() & Qt.WindowStaysOnTopHint      # the browser opens over it
    assert not dlg.login_hint.isHidden() and "ChatGPT" in dlg.login_hint.text()
    assert not dlg.install_btn.isHidden()
    dlg._on_status("codex", "ok")
    assert dlg.windowFlags() & Qt.WindowStaysOnTopHint          # back on top, connected
    assert dlg.login_hint.isHidden() and dlg.install_btn.isHidden()
    dlg.close()


def test_onboarding_sign_in_that_cannot_start_says_so(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: None)
    dlg._start_login()
    assert "The ChatGPT sign-in didn't finish" in dlg.login_hint.text()
    assert not dlg.install_btn.isHidden()
    dlg.close()


def test_onboarding_reports_a_sign_in_that_ended_in_failure(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")

    class Ended:
        returncode = 1

        def poll(self):
            return 1
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: Ended())
    dlg._start_login()
    dlg._poll_tick()
    assert "The ChatGPT sign-in didn't finish" in dlg.login_hint.text()
    assert not dlg.install_btn.isHidden()
    dlg.close()


def _wait(qapp, done, seconds=5.0):
    import time
    end = time.time() + seconds
    while not done() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_onboarding_enter_moves_on_and_esc_does_not_quit(env, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    page = dlg.pages.index(dlg.form.parentWidget())
    dlg.stack.setCurrentIndex(page)
    dlg._update_nav()
    assert dlg.enter_button is dlg.next and not dlg.back.autoDefault()
    QTest.keyClicks(dlg.form.name, "Bob")
    QTest.keyClick(dlg.form.name, Qt.Key_Return)          # no class yet: stays, and never goes Back
    assert dlg.stack.currentIndex() == page
    dlg.form.class_group.buttons()[1].setChecked(True)     # Warrior, Lv. 10: one possible job
    QTest.keyClick(dlg.form.name, Qt.Key_Return)
    assert dlg.stack.currentIndex() == page + 1
    closed = []
    dlg.rejected.connect(lambda: closed.append(True))
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert not closed                                      # Esc would have quit the app
    dlg.close()


def test_adding_a_character_can_still_be_cancelled_with_esc(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)
    closed = []
    dlg.rejected.connect(lambda: closed.append(True))
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert closed


def test_onboarding_enter_in_the_key_field_checks_the_key_off_the_gui_thread(env, qapp, monkeypatch):
    import threading
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    seen = []
    monkeypatch.setattr(type(providers.get("claude")), "test_api_key",
                        lambda self, k: seen.append((k, threading.current_thread() is threading.main_thread())))
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg.stack.setCurrentIndex(1)
    QTest.keyClicks(dlg.key_edit, "sk-ant-x")
    QTest.keyClick(dlg.key_edit, Qt.Key_Return)
    _wait(qapp, lambda: dlg.key_btn.isEnabled())
    assert seen == [("sk-ant-x", False)] and dlg.stack.currentIndex() == 1
    assert "didn't work" in dlg.key_hint.text() and not dlg._ai_ok
    dlg.key_edit.setText("sk-ключ")                          # used to raise inside the request, only logged
    dlg._check_key()
    assert "characters that don't belong" in dlg.key_hint.text() and len(seen) == 1
    dlg.close()


def test_onboarding_sign_in_that_times_out_says_try_again(env, monkeypatch):
    from PySide6.QtCore import Qt
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: object())
    dlg._start_login()
    dlg._poll_left = 1
    dlg._poll_tick()
    assert not dlg._poll_timer.isActive() and not dlg._signing_in
    assert dlg.windowFlags() & Qt.WindowStaysOnTopHint
    assert "try again" in dlg.login_hint.text() and "by itself" not in dlg.login_hint.text()
    dlg.close()


def test_job_is_not_guessed_when_there_is_a_choice(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)      # kept: the form is its child
    form = dlg.form
    form.name.setText("Bob")
    form.class_group.buttons()[1].setChecked(True)         # Warrior
    form.level.setValue(35)                                # Warrior, Fighter, Page, Spearman
    assert form.current_job() == "" and not form.valid()
    form.job.setCurrentText("Page")
    assert form.current_job() == "Page" and form.valid()


def test_settings_offers_install_and_says_when_a_sign_in_timed_out(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    s["provider"] = "codex"
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_account({"status": "not_installed", "email": None, "provider": "codex"})
    assert not dlg.install_btn.isHidden() and dlg.install_btn.text() == "Install ChatGPT (Codex, OpenAI's official tool)"
    dlg._on_account({"status": "logged_out", "email": None, "provider": "codex"})
    assert dlg.install_btn.isHidden()

    class Waiting:                        # the sign-in still waits for the browser
        returncode = None

        def poll(self):
            return None
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: Waiting())
    dlg._start_login()
    dlg._login_left = 1
    dlg._login_tick()
    assert not dlg._login_timer.isActive() and "try again" in dlg.account_hint.text()
    assert not dlg.account_hint.isHidden()
    dlg.close()


def test_settings_esc_keeps_unsaved_changes_and_keys_must_differ(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    closed = []
    dlg.rejected.connect(lambda: closed.append(True))
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert not closed
    dlg.hk_voice.setCurrentText(dlg.hk_toggle.currentText())
    assert not dlg.save_btn.isEnabled() and not dlg.keys_error.isHidden()
    dlg._save()
    assert s["hotkey_voice"] != s["hotkey_toggle"]           # not saved like that
    dlg.hk_voice.setCurrentText("F11")            # (F12 is not offered on Windows: it never registers)
    assert dlg.save_btn.isEnabled() and dlg.keys_error.isHidden()
    dlg.close()


def test_tall_windows_fit_the_screen(env):
    from PySide6.QtGui import QGuiApplication
    from maplehelper.ui.dialogs import Onboarding, SettingsDialog
    s, profiles, kb = env
    avail = QGuiApplication.primaryScreen().availableGeometry().height()
    for dlg in (SettingsDialog(s, profiles, kb, lambda *_: ""), Onboarding(s, profiles, kb, lambda *_: "")):
        assert dlg.height() <= max(320, avail - 48)
        dlg.close()


class FakeInstall:
    def __init__(self):
        import threading
        self.lines, self.code, self.done = [], None, threading.Event()

    def status(self):
        return self.lines[-1] if self.lines else ""

    def error(self, n=4):
        return "\n".join(self.lines[-n:])

    def finish(self, code, *lines):
        self.lines += lines
        self.code = code
        self.done.set()


def test_install_runs_inside_the_app_and_shows_why_it_failed(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    inst = FakeInstall()
    monkeypatch.setattr(type(providers.get("codex")), "install", lambda self: inst)
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    dlg._start_install()
    assert not dlg.install_panel.isHidden() and "Installing ChatGPT" in dlg.install_panel.title.text()
    assert dlg.install_btn.isHidden()
    inst.lines.append("Downloading Codex 1.2.3")
    dlg.install_panel._tick()
    assert dlg.install_panel.detail.text() == "Downloading Codex 1.2.3"
    inst.finish(1, "ERROR: Could not fetch GitHub release metadata.")
    dlg.install_panel._tick()
    dlg._on_status("codex", "not_installed")                   # the check after it: not there
    assert "didn't install" in dlg.install_panel.title.text()
    assert "Could not fetch GitHub release metadata" in dlg.install_panel.error.text()
    assert not dlg.install_btn.isHidden()                      # try again


def test_an_install_that_worked_moves_on_to_sign_in(env, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    inst = FakeInstall()
    monkeypatch.setattr(type(providers.get("codex")), "install", lambda self: inst)
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    dlg._start_install()
    inst.finish(0, "Codex installed")
    dlg.install_panel._tick()
    dlg._on_status("codex", "logged_out")
    assert dlg.install_panel.isHidden() and not dlg.login_btn.isHidden()
    # "worked" but the CLI isn't there: said plainly, with the installer offered again
    dlg._start_install()
    inst2 = dlg.install_panel.inst
    inst2.done.set()
    inst2.code = 0
    dlg.install_panel._tick()
    dlg._on_status("codex", "not_installed")
    assert "isn't on this PC" in dlg.install_panel.error.text() and not dlg.install_btn.isHidden()


def test_an_installer_that_errs_after_installing_counts_as_installed(env, monkeypatch):
    """Antigravity's installer reported -1 although agy was in place: what decides is whether the CLI is there."""
    from maplehelper import providers
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    inst = FakeInstall()
    monkeypatch.setattr(type(providers.get("codex")), "install", lambda self: inst)
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    dlg._start_install()
    inst.finish(4294967295)
    dlg.install_panel._tick()
    dlg._on_status("codex", "logged_out")
    assert dlg.install_panel.isHidden() and not dlg.login_btn.isHidden()


def test_brain_finds_a_cli_installed_after_it_started(kb, monkeypatch):
    from maplehelper import providers
    from maplehelper.brain import Brain
    found = [None]
    monkeypatch.setattr(type(providers.get("codex")), "find_exe", lambda self: found[0])
    b = Brain(kb, provider="codex")
    b.backend.exe = None
    assert not b.available()
    found[0] = __file__                      # installed from Settings meanwhile
    assert b.available() and b.backend.exe == __file__


def test_a_second_sign_in_click_does_not_open_a_second_browser(env, monkeypatch):
    """Grok's sign-in opened the browser twice: "Sign in" signed out first (seconds), and a second click meanwhile
    started a second sign-in. Signed out, it now goes straight to the sign-in, and a waiting one isn't restarted."""
    from maplehelper import providers
    from maplehelper.providers import base
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    s["provider"] = "codex"
    starts, logouts = [], []

    class Waiting:
        returncode = None

        def poll(self):
            return None

        def kill(self):
            pass
    monkeypatch.setattr(type(providers.get("codex")), "login",
                        lambda self: starts.append(1) or setattr(base, "_login", Waiting()) or base._login)
    monkeypatch.setattr(type(providers.get("codex")), "logout", lambda self: logouts.append(1) or True)
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_account({"status": "logged_out", "email": None, "provider": "codex"})
    dlg._switch_account()
    dlg._switch_account()                    # a second click while the first waits for the browser
    assert starts == [1] and logouts == []
    base._login = None


def test_closing_settings_during_a_sign_in_stops_its_timer(env, monkeypatch):
    """Settings closed while a sign-in waited kept its 3-second timer: at its timeout it showed the closed window
    again (_set_on_top), and its account checks ran on (the review, UI-6)."""
    from maplehelper import providers
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb = env
    s["provider"] = "codex"

    class Waiting:
        returncode = None

        def poll(self):
            return None

        def kill(self):
            pass
    monkeypatch.setattr(type(providers.get("codex")), "login", lambda self: Waiting())
    dlg = SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_account({"status": "logged_out", "email": None, "provider": "codex"})
    dlg._start_login()
    assert dlg._login_timer.isActive() and dlg.isVisible()
    dlg.close()
    assert not dlg._login_timer.isActive()
