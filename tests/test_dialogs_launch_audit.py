"""Onboarding, Settings and the other dialogs: fixes from the launch audit (offscreen Qt, no real CLI, no sign-in,
no keychain, no AI call)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(qapp, isolated_store, kb, monkeypatch):
    from maplehelper import providers, voice
    saved = []
    for p in providers.PROVIDERS.values():
        monkeypatch.setattr(type(p), "account", lambda self: {"status": "not_installed", "email": None, "method": None})
        monkeypatch.setattr(type(p), "status", lambda self: "not_installed")
        monkeypatch.setattr(type(p), "models", lambda self: [(None, "")])
        monkeypatch.setattr(type(p), "read_limits", lambda self, *a, **k: None)
        monkeypatch.setattr(type(p), "save_api_key", lambda self, k: saved.append(k))
        monkeypatch.setattr(type(p), "find_exe", lambda self: None)
    monkeypatch.setattr(voice, "input_devices", lambda: [])
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = "claude"
    return s, isolated_store.Profiles(), kb, saved


def _onboarding(env, provider="claude"):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb, _ = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg.stack.setCurrentIndex(1)
    dlg._on_provider(provider)
    return dlg


# --- DLG-1: an API key without the AI's CLI is not "connected" ------------------------------------------------------

@pytest.mark.parametrize("provider", ["claude", "codex", "gemini", "grok"])
def test_a_good_key_without_the_cli_asks_to_install_it(env, provider):
    s = env[0]
    dlg = _onboarding(env, provider)
    dlg._on_key_checked(provider, "sk-test", True)
    assert s.api_key_mode(provider)                       # the key is kept for when the CLI is there
    assert not dlg._ai_ok and not dlg.next.isEnabled()
    assert "Connected" not in dlg.status_label.text()
    assert not dlg.install_btn.isHidden() and "install" in dlg.key_hint.text().lower()
    dlg._on_status(provider, "not_installed")            # a later check still says so
    assert not dlg._ai_ok and not dlg.install_btn.isHidden()
    dlg.close()


def test_a_good_key_with_the_cli_connects(env, monkeypatch):
    from maplehelper import providers
    monkeypatch.setattr(type(providers.get("claude")), "find_exe", lambda self: "claude.exe")
    dlg = _onboarding(env)
    dlg._on_key_checked("claude", "sk-ant-test", True)
    assert dlg._ai_ok and dlg.next.isEnabled() and "Connected" in dlg.status_label.text()
    dlg._on_status("claude", "logged_out")
    assert dlg._ai_ok
    dlg.close()


def test_settings_offers_the_installer_for_a_key_without_the_cli(env):
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb, _ = env
    s.set_api_key_mode("claude", True)
    sd = SettingsDialog(s, profiles, kb, lambda *_: "")
    sd._on_account({"status": "not_installed", "email": None, "provider": "claude"})
    assert not sd.install_btn.isHidden()
    assert "not installed" in sd.account_label.text()
    assert not sd.logout_btn.isHidden()                  # the saved key can still be dropped
    sd._on_account({"status": "logged_out", "email": None, "provider": "claude"})
    assert "API key" in sd.account_label.text() and sd.install_btn.isHidden()
    sd.close()


# --- DLG-12: a key the keychain refuses ---------------------------------------------------------------------------

def test_a_key_the_keychain_refuses_says_so(env, monkeypatch):
    from maplehelper import providers

    def refuse(self, key):
        raise RuntimeError("keychain locked")
    monkeypatch.setattr(type(providers.get("claude")), "save_api_key", refuse)
    s = env[0]
    dlg = _onboarding(env)
    dlg._on_key_checked("claude", "sk-ant-test", True)
    assert not s.api_key_mode("claude") and not dlg._ai_ok
    assert "Couldn't save the key" in dlg.key_hint.text() and not dlg.key_hint.isHidden()
    dlg.close()


# --- DLG-2 / DLG-3: the AI's language follows the app's ----------------------------------------------------------

def test_the_preselected_language_is_stored_without_a_click(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb, _ = env
    s["language"] = None                                 # a first run: nothing stored yet, the page shows Hebrew
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    assert dlg.lang_group.checkedButton().property("lang") == "he"
    dlg._go_next()
    assert s["language"] == "he" and dlg.stack.currentIndex() == 1
    dlg.close()


def test_a_language_saved_in_settings_reaches_the_ai_at_once(qapp):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from maplehelper import app
    fake = MagicMock()
    fake.settings = {"language": "en", "telemetry": False, "saver_mode": False, "hotkey_voice": "F10"}
    fake.brain = SimpleNamespace(ui_lang="he", prewarm=lambda: None)
    app.MapleHelperApp.on_settings_changed(fake)
    assert fake.brain.ui_lang == "en"
    fake.settings["language"] = None
    app.MapleHelperApp.on_settings_changed(fake)
    assert fake.brain.ui_lang == "he"
