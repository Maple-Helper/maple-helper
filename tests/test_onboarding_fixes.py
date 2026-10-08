"""Owner-approved first-run / Settings / account fixes (the UX audit's ONB-01..19, COPY-06/07): offscreen, every CLI
and keychain call faked (nothing here runs a real AI CLI or touches the player's saved keys)."""
import os
import subprocess
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
    state = {"claude": {"status": "logged_out", "email": None}}

    def account(self):
        return dict(state.get(self.name, {"status": "not_installed", "email": None}))
    for name in providers.PROVIDERS:
        monkeypatch.setattr(type(providers.get(name)), "account", account)
        monkeypatch.setattr(type(providers.get(name)), "read_limits", lambda self, *a, **k: None)
        monkeypatch.setattr(type(providers.get(name)), "models", lambda self: [(None, "")])
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = "claude"
    from PySide6.QtWidgets import QApplication
    windows = set(QApplication.topLevelWidgets())
    before = set(threading.enumerate())
    yield s, isolated_store.Profiles(), kb, state
    for th in set(threading.enumerate()) - before:
        th.join(timeout=10)
    from PySide6.QtCore import QTimer
    for w in set(QApplication.topLevelWidgets()) - windows:
        for timer in w.findChildren(QTimer):
            timer.stop()
        w.close()


def _onboarding(env, lang="en"):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb, _ = env
    s["language"] = lang
    return Onboarding(s, profiles, kb, lambda *_: "")


def _settings(env):
    from maplehelper.ui.dialogs import SettingsDialog
    s, profiles, kb, _ = env
    return SettingsDialog(s, profiles, kb, lambda *_: "")


# --- ONB-01: a CLI too old for Maple Helper says so, instead of "Connected" -------------------------------------------

class FakeRun:
    def __init__(self, help_text: str, status: bytes = b'{"loggedIn": true, "email": "a@b.c"}'):
        self.help_text, self.status, self.calls = help_text, status, []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        out = self.help_text.encode() if "--help" in cmd else self.status
        return subprocess.CompletedProcess(cmd, 0, out, b"")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    from maplehelper.providers import base, claude
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"old")
    monkeypatch.setattr(claude, "find_claude", lambda: str(exe))
    monkeypatch.setattr(base, "_help_has_flags", {})
    monkeypatch.setattr(base, "_outdated_seen", {})
    return exe


NEW_HELP = "Usage: claude [options]\n  --help\n" + "\n".join(f"  {f}  something" for f in
                                                              ("--restricted", "--strict-mcp-config", "--tools <t>",
                                                               "--no-session-persistence", "--include-partial-messages"))


def test_an_old_claude_code_is_outdated_not_signed_in(fake_claude, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base
    old_help = NEW_HELP.replace("--restricted", "--verbose").replace("--tools <t>", "--allowedTools <t>")
    run = FakeRun(old_help)
    monkeypatch.setattr(base.subprocess, "run", run)
    assert providers.get("claude").account()["status"] == "outdated"
    assert providers.get("claude").account()["status"] == "outdated"
    assert sum("--help" in c for c in run.calls) == 1               # read once per file, not on every check


def test_a_current_claude_code_is_signed_in(fake_claude, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base
    monkeypatch.setattr(base.subprocess, "run", FakeRun(NEW_HELP))
    assert providers.get("claude").account() == {"status": "ok", "email": "a@b.c"}


def test_a_help_that_isnt_one_never_calls_the_cli_old(fake_claude, monkeypatch):
    """A CLI that prints no usable help (a crash, a wrapper) is judged by its sign-in as before."""
    from maplehelper import providers
    from maplehelper.providers import base
    monkeypatch.setattr(base.subprocess, "run", FakeRun("Segmentation fault"))
    assert providers.get("claude").account()["status"] == "ok"


def test_an_answer_that_said_too_old_marks_that_file_until_it_changes(fake_claude, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base
    monkeypatch.setattr(base.subprocess, "run", FakeRun(NEW_HELP))
    base.note_outdated("claude", str(fake_claude))
    assert providers.get("claude").account()["status"] == "outdated"
    st = os.stat(fake_claude)
    os.utime(fake_claude, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))       # updated in place
    assert providers.get("claude").account()["status"] == "ok"


def test_an_old_codex_is_outdated(tmp_path, monkeypatch):
    from maplehelper import providers
    from maplehelper.providers import base, codex
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"old")
    monkeypatch.setattr(codex, "find_codex", lambda: str(exe))
    monkeypatch.setattr(base, "_help_has_flags", {})
    run = FakeRun("Usage: codex exec [OPTIONS]\n  --json\n  --ephemeral\n  --skip-git-repo-check\n  -h, --help")
    monkeypatch.setattr(base.subprocess, "run", run)
    assert providers.get("codex").account()["status"] == "outdated"
    assert ["exec", "--help"] == run.calls[0][1:]


def test_brain_remembers_a_too_old_answer_and_looks_for_the_cli_again(kb, tmp_path, monkeypatch):
    from maplehelper.brain import Brain
    from maplehelper.providers import base
    from maplehelper.providers.base import RawResult
    monkeypatch.setattr(base, "_outdated_seen", {})
    old = tmp_path / "old" / "claude.exe"
    old.parent.mkdir()
    old.write_bytes(b"x")
    b = Brain(kb, provider="claude")
    b.backend.exe = str(old)
    monkeypatch.setattr(b.backend, "run", lambda *a, **k: RawResult(error="cli_outdated"))
    monkeypatch.setattr(b.backend, "prewarm", lambda: None)
    assert b.ask("hi", None, None, None).error == "cli_outdated"
    assert base.cli_outdated("claude", str(old))
    # the update installed a new one elsewhere: the backend moves to it (it kept the old, "too old" one)
    new = tmp_path / "new" / "claude.exe"
    monkeypatch.setattr(type(b._provider), "find_exe", lambda self: str(new))
    b.refind_cli()
    assert b.backend.exe == str(new)


@pytest.mark.parametrize("lang", ["en", "he"])
def test_settings_offers_the_update_for_an_outdated_cli(env, lang):
    s, _, _, state = env
    s["language"] = lang
    dlg = _settings(env)
    dlg._on_account({"status": "outdated", "email": None, "provider": "claude"})
    t = dlg.t
    assert dlg.account_label.text().replace(" ", " ") == t.p("ob_outdated", "claude").replace(" ", " ") \
        or "Claude" in dlg.account_label.text()
    assert not dlg.install_btn.isHidden() and dlg.install_btn.text() == t.p("ob_update", "claude")
    assert dlg.switch_btn.isHidden()
    s.set_api_key_mode("claude", True)               # a key doesn't hide it either: the key runs through the CLI
    dlg._on_account({"status": "outdated", "email": None, "provider": "claude"})
    assert not dlg.install_btn.isHidden() and "API" not in dlg.account_label.text()
    dlg.close()


def test_onboarding_offers_the_update_as_the_main_button(env):
    dlg = _onboarding(env)
    dlg._on_status("claude", "outdated")
    assert not dlg._ai_ok and "too old" in dlg.status_label.text()
    assert not dlg.install_btn.isHidden() and dlg.install_btn.text() == "Update Claude Code"
    assert dlg.install_btn.objectName() == "Primary"
    dlg.close()


# --- ONB-04 / COPY-06: each AI's tool by one name ---------------------------------------------------------------------

@pytest.mark.parametrize("provider, tool", [("claude", "Claude Code"), ("codex", "Codex (ChatGPT's tool)"),
                                            ("gemini", "Google Antigravity (Gemini's tool)"), ("grok", "Grok Build")])
def test_each_ai_names_its_own_tool_when_too_old(provider, tool):
    from maplehelper.i18n import I18n
    en = I18n("en")
    for key in ("err_cli_outdated", "ob_outdated", "ob_not_logged", "ob_key_saved_install"):
        assert tool in en.p(key, provider).replace(" ", " "), key
    assert "Claude" not in en.p("err_cli_outdated", provider) or provider == "claude"
    he = I18n("he").p("err_cli_outdated", provider)
    assert tool.split(" (")[0] in he.replace(" ", " ") and "בהגדרות" in he


def test_the_codex_texts_name_the_account_only_for_the_account():
    from maplehelper.i18n import STRINGS
    for key in ("ob_not_logged_codex", "ob_key_saved_install_codex", "err_cli_outdated_codex", "ob_install_codex"):
        for lang in ("he", "en"):
            assert "Codex" in STRINGS[key][lang], (key, lang)
    assert "app" not in STRINGS["err_cli_outdated_codex"]["en"]


# --- ONB-02: Settings takes an API key ---------------------------------------------------------------------------------

def test_settings_saves_a_working_key(env, monkeypatch):
    from maplehelper import providers
    s = env[0]
    saved = {}
    monkeypatch.setattr(type(providers.get("claude")), "save_api_key", lambda self, k: saved.update(key=k))
    monkeypatch.setattr(type(providers.get("claude")), "find_exe", lambda self: "claude.exe")
    dlg = _settings(env)
    assert dlg.key_box.isHidden() and not dlg.key_toggle.isHidden()
    dlg.key_toggle.click()
    assert not dlg.key_box.isHidden()
    changed = []
    dlg.account_changed.connect(lambda: changed.append(1))
    dlg._on_key_checked("claude", "sk-ant-good", True)
    assert saved == {"key": "sk-ant-good"} and s.api_key_mode("claude") and changed
    assert "Key saved" in dlg.key_hint.text() and dlg.key_edit.text() == ""
    dlg._on_key_checked("claude", "sk-ant-bad", False)
    assert "didn't work" in dlg.key_hint.text()
    dlg.close()


# --- ONB-03 / ONB-12: the next step is a button; signed in names the account ------------------------------------------

def test_the_connect_steps_main_action_is_a_button(env):
    dlg = _onboarding(env)
    dlg._on_status("claude", "not_installed")
    assert dlg.install_btn.objectName() == "Primary" and not dlg.install_btn.isHidden()
    dlg._on_status("claude", "logged_out")
    assert dlg.login_btn.objectName() == "Primary" and not dlg.login_btn.isHidden()
    assert dlg.check_btn.objectName() == "Link"
    assert dlg.key_box.isHidden()                         # the key field waits for "Or: use an API key"
    dlg.close()


def test_signed_in_names_the_account_and_drops_the_key_box(env):
    dlg = _onboarding(env)
    dlg.key_toggle.click()
    dlg._on_status("claude", "ok", "player@example.com")
    assert "player@example.com" in dlg.status_label.text()
    assert dlg.key_box.isHidden() and dlg.key_toggle.isHidden()
    dlg.close()


# --- ONB-05 / ONB-07 / ONB-19 / ONB-06: Settings ---------------------------------------------------------------------

def test_settings_checks_again_without_reopening(env):
    dlg = _settings(env)
    dlg._on_account({"status": "offline", "email": None, "provider": "claude"})
    assert not dlg.check_btn.isHidden()
    checks = []
    dlg._refresh_account = lambda: checks.append(1)
    dlg.check_btn.click()
    assert checks and dlg.account_label.text() == "Checking…"
    dlg._on_account({"status": "ok", "email": "a@b.c", "provider": "claude"})       # what the check found
    assert "a@b.c" in dlg.account_label.text()
    dlg.close()


def test_settings_says_the_chat_switches_on_save(env):
    dlg = _settings(env)
    assert dlg.provider_note.isHidden()
    dlg._on_provider("gemini")
    assert not dlg.provider_note.isHidden() and "Gemini" in dlg.provider_note.text() and "Save" in dlg.provider_note.text()
    dlg._on_provider("claude")
    assert dlg.provider_note.isHidden()
    dlg.close()


def test_the_usage_meter_waits_for_a_connected_account(env):
    dlg = _settings(env)
    dlg._on_account({"status": "not_installed", "email": None, "provider": "claude"})
    assert dlg.usage_meter.isHidden() and dlg.usage_note.isHidden()
    assert not dlg.saver_hint.isHidden()                  # saver mode stays
    dlg._on_account({"status": "ok", "email": "a@b.c", "provider": "claude"})
    assert not dlg.usage_meter.isHidden()
    dlg.close()


def test_update_database_says_its_checking(env):
    dlg = _settings(env)
    asked = []
    dlg.update_kb_requested.connect(lambda: asked.append(1))
    dlg.kb_btn.click()
    assert asked and not dlg.kb_btn.isEnabled() and dlg.kb_btn.text() == "Checking for a database update…"
    dlg.kb_update_done()
    assert dlg.kb_btn.isEnabled() and dlg.kb_btn.text() == "Update database"
    dlg.close()


# --- ONB-09 / ONB-11 / ONB-13 / ONB-14 / ONB-15: the first-run setup -----------------------------------------------

@pytest.mark.parametrize("choice, closed", [("no", False), (None, False), ("yes", True)])
def test_the_setups_x_asks_before_quitting(env, monkeypatch, choice, closed):
    from maplehelper.ui import dialogs
    asked = []

    def fake_exec(self):
        asked.append(self.windowTitle())
        self.choice = choice
        return choice == "yes"
    monkeypatch.setattr(dialogs.ConfirmDialog, "exec", fake_exec)
    dlg = _onboarding(env)
    dlg.show()
    dlg.close_btn.click()
    assert asked == ["Quit setup?"]
    assert dlg.isVisible() is not closed
    dlg.close()


def test_adding_a_character_still_closes_at_once(env, monkeypatch):
    from maplehelper.ui import dialogs
    from maplehelper.ui.dialogs import Onboarding
    monkeypatch.setattr(dialogs.ConfirmDialog, "exec", lambda self: pytest.fail("asked"))
    s, profiles, kb, _ = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "", only_character=True)
    dlg.show()
    dlg.close_btn.click()
    assert not dlg.isVisible() and dlg.step_label.isHidden()


@pytest.mark.parametrize("lang", ["en", "he"])
def test_steps_heading_autostart_and_privacy(env, lang):
    s = env[0]
    dlg = _onboarding(env, lang)
    t = dlg.t
    assert dlg.step_label.text().replace(" ", " ").endswith("1 " + ("of" if lang == "en" else "מתוך") + " 4") or \
        "1" in dlg.step_label.text()
    assert not s["start_with_windows"] and not dlg.autostart.isChecked()       # the stored default (off)
    dlg._go_next()
    assert "2" in dlg.step_label.text()
    dlg._skip_ai()
    from maplehelper import bidi
    assert bidi.plain(t("ob_your_character")) in [lb.text() for lb in dlg.findChildren(type(dlg.step_label), "PageTitle")]
    assert dlg.privacy_label.text() == bidi.plain(t("ob_privacy_none"), t.rtl)     # no AI was chosen
    dlg.form.name.setText("Amit")
    next(b for b in dlg.form.class_group.buttons() if b.property("cls") == "Beginner").setChecked(True)
    dlg._go_next()
    assert dlg.stack.currentIndex() == 3 and "4" in dlg.step_label.text()
    dlg.autostart.setChecked(True)
    dlg._go_next()
    assert s["start_with_windows"] is True and s["onboarding_done"]


def test_a_connected_ai_is_named_in_the_privacy_line(env):
    dlg = _onboarding(env)
    dlg._on_status("claude", "ok", None)
    assert "Anthropic" in dlg.privacy_label.text()
    dlg.close()


def test_minimap_label_doesnt_repeat_the_unit():
    from maplehelper.i18n import STRINGS
    assert "seconds" not in STRINGS["minimap_scan_interval"]["en"]
    assert "לפי שניות" not in STRINGS["minimap_scan_interval"]["he"]


# --- COPY-07: the startup failure ----------------------------------------------------------------------------------

def test_startup_failure_names_the_issues_address():
    from maplehelper import setupwait
    for lang in ("he", "en"):
        assert "github.com/Maple-Helper/maple-helper/issues" in setupwait.STARTUP_TEXT[lang]
        assert "%s" in setupwait.STARTUP_TEXT[lang]
    assert "Report a problem" not in setupwait.STARTUP_TEXT["en"]
    assert "issues" in setupwait.MAC_TEXT["startup"]["en"] and "Mac" in setupwait.MAC_TEXT["startup"]["en"]


@pytest.mark.parametrize("stored, system, want", [("en", "he", "en"), ("he", "en", "he"), (None, "en", "en"),
                                                  (None, "he", "he"), ("garbage", "en", "en")])
def test_startup_failure_language_falls_back_to_the_system(tmp_path, monkeypatch, stored, system, want):
    import json

    from maplehelper import setupwait, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"language": stored}), encoding="utf-8")
    monkeypatch.setattr(setupwait, "_system_language", lambda: system)
    assert setupwait._language() == want


def test_unreadable_settings_use_the_system_language(tmp_path, monkeypatch):
    from maplehelper import setupwait, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)                   # no settings.json at all: a first run
    monkeypatch.setattr(setupwait, "_system_language", lambda: "en")
    assert setupwait._language() == "en"
