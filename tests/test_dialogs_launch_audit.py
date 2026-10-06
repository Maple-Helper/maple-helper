"""Onboarding, Settings and the other dialogs: fixes from the launch audit (offscreen Qt, no real CLI, no sign-in,
no keychain, no AI call)."""
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
    before = set(threading.enumerate())
    yield s, isolated_store.Profiles(), kb, saved
    # the dialogs' account checks run on threads: let them end before their dialog is freed (an emit into a
    # deleted dialog crashed a later test)
    for th in set(threading.enumerate()) - before:
        th.join(timeout=5)


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


# --- DLG-7: Grok's key prefix in Hebrew ----------------------------------------------------------------------------

def test_every_key_prefix_is_one_left_to_right_block_in_hebrew(env):
    from maplehelper import bidi
    s = env[0]
    s["language"] = "he"
    dlg = _onboarding(env, "grok")
    assert bidi.ltr_block("xai-", True) in dlg.key_edit.placeholderText()
    dlg.close()


# --- DLG-8: Hebrew hints wrap as right-to-left paragraphs -----------------------------------------------------------

def test_hebrew_hints_are_laid_out_as_right_to_left_paragraphs(env):
    from PySide6.QtCore import Qt

    from maplehelper.ui.dialogs import set_hint
    s = env[0]
    s["language"] = "he"
    dlg = _onboarding(env, "gemini")
    # plain text cut the end of a nearly full first line ("...הקוד. יש" lost its last letter at the card's edge)
    set_hint(dlg.login_hint, dlg.t.p("ob_login_wait", "gemini"), True)
    assert dlg.login_hint.textFormat() == Qt.RichText and 'dir="rtl"' in dlg.login_hint.text()
    dlg._key_message(dlg.t("ob_key_failed"))
    assert dlg.key_hint.textFormat() == Qt.RichText
    dlg.close()


# --- DLG-9: a Select's value fits inside its padding -----------------------------------------------------------------

@pytest.mark.parametrize("size", [13, 14, 16])
def test_a_select_value_fits_inside_the_button_padding(qapp, size):
    import re

    from PySide6.QtCore import Qt

    from maplehelper import bidi
    from maplehelper.ui import controls, theme
    css = theme.stylesheet(theme.load_fonts(), size)
    pad = re.search(r"QPushButton#Select \{[^}]*padding: 0 (\d+)px", css)
    assert pad and controls.SELECT_PAD == 2 * int(pad.group(1)) + 2      # padding and the 1px border, both sides
    m = controls.Select()
    m.setStyleSheet(css)
    m.setLayoutDirection(Qt.RightToLeft)
    m.text_width = 190
    m.addItems(["ברירת המחדל של המערכת"])
    m.resize(m.sizeHint())
    m.setAttribute(Qt.WA_DontShowOnScreen)
    m.show()                                          # (its resize event cuts the value to the room)
    shown = re.sub(f"[{bidi.RLM}\u2066-\u2069\u200e]", "", m.text())
    assert m.fontMetrics().horizontalAdvance(shown) + controls.SELECT_PAD <= m.width()
    m.close()


# --- DLG-10: the tour link asks about unsaved changes ----------------------------------------------------------------

@pytest.mark.parametrize("choice", ["yes", "no", None])
def test_the_tour_link_asks_about_unsaved_settings(env, monkeypatch, choice):
    from unittest.mock import MagicMock

    from PySide6.QtCore import Qt

    from maplehelper import app
    from maplehelper.ui import dialogs
    s, profiles, kb, _ = env

    class Answer:
        def __init__(self, *a, **k):
            self.choice = None

        def exec(self):
            self.choice = choice
            return choice == "yes"
    monkeypatch.setattr(dialogs, "ConfirmDialog", Answer)
    monkeypatch.setattr(app.QTimer, "singleShot", lambda *a: None)
    sd = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
    sd.setAttribute(Qt.WA_DontShowOnScreen)
    sd.show()
    was = s["telemetry"]
    sd.telemetry.setChecked(not was)
    fake = MagicMock()
    fake.overlay.is_open.return_value = True
    app.MapleHelperApp.replay_tour(fake, sd)
    assert s["telemetry"] == (not was if choice == "yes" else was)
    assert sd.isVisible() is (choice is None)          # the question closed: Settings stays, no tour
    sd.close()


# --- DLG-11: an unexpected error in an account check still settles the window ----------------------------------------

def test_a_failing_account_check_settles_on_offline(env):
    from types import SimpleNamespace

    from maplehelper.ui import dialogs

    def boom():
        raise ValueError("unexpected")
    claude = SimpleNamespace(name="claude", account=boom, status=boom, logout=boom)
    assert dialogs._safe_status(claude) == "offline"
    assert dialogs._safe_account(claude) == {"status": "offline", "email": None, "provider": "claude"}
    dialogs._safe_logout(claude)                          # logged, not raised
    dlg = _onboarding(env)
    dlg._on_status("claude", dialogs._safe_status(claude))
    assert "Checking" not in dlg.status_label.text() and "reach" in dlg.status_label.text()
    dlg.close()


# --- DLG-16: saver mode turned on from the chat leaves nothing unsaved in Settings ----------------------------------

def test_saver_turned_on_from_the_chat_is_no_unsaved_change(env):
    from unittest.mock import MagicMock

    from maplehelper import app
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb, _ = env
    sd = SettingsDialog(s, profiles, kb, lambda *_: "")
    fake = MagicMock()
    fake.settings = s
    fake.__dict__["_windows"] = {"settings": sd}
    fake.brain.prewarm = lambda: None
    app.MapleHelperApp.turn_on_saver(fake)
    assert s["saver_mode"] and sd.saver.isChecked() and not sd.unsaved()
    sd.close()


# --- DLG-17: a second character with the same name -------------------------------------------------------------------

def test_a_name_another_character_has_is_refused(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb, _ = env
    profiles.add("Amit", "Warrior", "Fighter", 30)
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)
    f = dlg.form
    next(b for b in f.class_group.buttons() if b.property("cls") == "Beginner").setChecked(True)
    f.name.setText(" amit ")
    assert not f.valid() and not f.name_hint.isHidden() and not dlg.next.isEnabled()
    f.name.setText("Amit2")
    assert f.valid() and f.name_hint.isHidden() and dlg.next.isEnabled()
    dlg.close()
    me = profiles.characters[0]
    edit = Onboarding(s, profiles, kb, lambda *_: "", only_character=True, edit_id=me.id)
    assert edit.form.name.text() == "Amit" and edit.form.valid()          # its own name is no clash
    edit.close()


# --- DLG-22 / DLG-23: the unpin button's name, a news item without a publisher ---------------------------------------

def test_the_unpin_button_has_a_name(qapp):
    from PySide6.QtWidgets import QToolButton

    from maplehelper.i18n import I18n
    from maplehelper.ui.pinsview import PinsBar
    bar = PinsBar()
    bar.show_pins([{"q": "Where is Henesys?", "a": "In Victoria Island."}], I18n("en"), False)
    xs = [b for b in bar.findChildren(QToolButton) if b.text() == "✕"]
    assert xs and all(b.accessibleName() == "Unpin" for b in xs)


def test_a_news_item_without_a_publisher_keeps_its_meowdb_link(qapp):
    from PySide6.QtWidgets import QPushButton

    from maplehelper.i18n import I18n
    from maplehelper.ui.newsview import article
    t = I18n("en")
    item = {"id": "x", "title": "Patch", "date": "2026-10-01", "summary": "Something.",
            "url": "https://meowdb.com/msclassic/news/x", "source_url": "https://example.com/x", "publisher": ""}
    page = article(t, item)
    links = [b.text() for b in page.findChildren(QPushButton) if b.objectName() == "Link"]
    assert any("MeowDB" in x for x in links) and len(links) == 1          # the source link needs its publisher


# --- DLG-6: a section's links start on the leading side in Hebrew ---------------------------------------------------

@pytest.mark.parametrize("rtl", [True, False])
def test_a_section_link_sits_on_the_leading_side(qapp, rtl):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    from maplehelper.ui.controls import Section
    sec = Section("Data", rtl)
    sec.setLayoutDirection(Qt.RightToLeft if rtl else Qt.LeftToRight)
    sec.add_row("A row")
    link = QPushButton("עדכון המאגר", objectName="Link")
    sec.add_widget(link)
    sec.setAttribute(Qt.WA_DontShowOnScreen)
    sec.resize(400, 200)
    sec.show()
    sec.layout().activate()
    card = link.parentWidget()
    x = link.mapTo(card, link.rect().topLeft()).x()
    assert link.width() < card.width() / 2                                 # its own width, not the card's
    assert (x + link.width() > card.width() * 0.8) if rtl else (x < card.width() * 0.2)
    sec.close()
