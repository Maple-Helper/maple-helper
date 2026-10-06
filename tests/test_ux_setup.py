"""Owner-approved setup/settings UX fixes (the audit's UX-3, 6, 8, 10, 11, DLG-15, 20, VIS-20, SEC-12): offscreen."""
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
    # every account check answers at once, and never runs a real CLI
    for name in providers.PROVIDERS:
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "not_installed", "email": None})
    s = isolated_store.Settings()
    s["language"] = "en"
    from PySide6.QtWidgets import QApplication
    windows = set(QApplication.topLevelWidgets())
    before = set(threading.enumerate())
    yield s, isolated_store.Profiles(), kb
    for th in set(threading.enumerate()) - before:
        th.join(timeout=10)
    from PySide6.QtCore import QTimer
    for w in set(QApplication.topLevelWidgets()) - windows:
        for timer in w.findChildren(QTimer):
            timer.stop()
        w.close()


# --- VIS-20: no job field before a class is picked ------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["he", "en"])
def test_the_job_field_waits_for_a_class(qapp, kb, lang):
    from maplehelper.i18n import I18n
    from maplehelper.ui.dialogs import CharacterForm
    f = CharacterForm(I18n(lang), kb)
    assert f.job.isHidden() and f.job_fixed.isHidden() and f.job_label.isHidden()
    next(b for b in f.class_group.buttons() if b.property("cls") == "Warrior").setChecked(True)
    assert not f.job_label.isHidden()
    assert not f.job.isHidden() or not f.job_fixed.isHidden()


# --- UX-3: a first run follows the system's language, never over a language the player chose ------------------------

@pytest.mark.parametrize("ui, want", [(["he-IL", "en-US"], "he"), (["iw"], "he"), (["en-GB"], "en"), (["fr-FR"], "en"),
                                      ([], "en")])
def test_system_language(monkeypatch, ui, want):
    from PySide6.QtCore import QLocale
    from maplehelper import i18n

    class Fake:
        def uiLanguages(self):
            return ui
    monkeypatch.setattr(QLocale, "system", staticmethod(lambda: Fake()))
    assert i18n.system_language() == want


@pytest.mark.parametrize("system", ["he", "en"])
def test_first_run_opens_in_the_system_language(env, monkeypatch, system):
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    s["language"] = None
    monkeypatch.setattr(dialogs, "system_language", lambda: system)
    dlg = dialogs.Onboarding(s, profiles, kb, lambda *_: "")
    assert dlg.t.lang == system and dlg.lang_group.checkedButton().property("lang") == system
    assert s["language"] is None              # not stored until the player goes on (Next) or picks one


def test_a_chosen_language_wins_over_the_system(env, monkeypatch):
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    s["language"] = "he"
    monkeypatch.setattr(dialogs, "system_language", lambda: "en")
    dlg = dialogs.Onboarding(s, profiles, kb, lambda *_: "")
    assert dlg.t.lang == "he" and dlg.lang_group.checkedButton().property("lang") == "he"


# --- UX-11: each install button names the tool it installs ----------------------------------------------------------

@pytest.mark.parametrize("provider, tool", [("claude", "Claude Code"), ("codex", "Codex"),
                                            ("gemini", "Google Antigravity"), ("grok", "Grok Build")])
def test_install_buttons_name_the_tool(provider, tool):
    from maplehelper.i18n import I18n
    for lang in ("he", "en"):
        text = I18n(lang).p("ob_install", provider).replace(" ", " ")      # (names are kept whole)
        assert tool in text, (lang, text)
    # Hebrew: a bracket beside an English word at either end turns around right to left
    he = I18n("he").p("ob_install", provider)
    i, j = he.index("("), he.index(")")
    assert "֐" <= he[i + 1] <= "׿" and j == len(he) - 1


# --- DLG-20: one "not installed" wording for every AI ---------------------------------------------------------------

@pytest.mark.parametrize("provider", ["claude", "codex", "gemini", "grok"])
def test_not_installed_reads_the_same_for_every_ai(provider):
    from maplehelper.i18n import I18n
    assert I18n("en").p("ob_not_installed", provider).endswith(" isn't installed on this computer")
    assert I18n("he").p("ob_not_installed", provider).endswith(" לא מותקן במחשב")


# --- DLG-15: a failed sign-in no longer blames only the install -----------------------------------------------------

@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_sign_in_failure_names_the_connection_and_account_first(provider):
    from maplehelper.i18n import I18n
    en = I18n("en").p("ob_login_failed", provider)
    assert "online" in en and "account" in en and en.index("online") < en.index("reinstall")
    he = I18n("he").p("ob_login_failed", provider)
    assert "חיבור לאינטרנט" in he and "חשבון" in he and he.index("חיבור") < he.index("מחדש")


# --- UX-8: a problem report says where to send it --------------------------------------------------------------------

def test_report_points_to_github_issues(env, monkeypatch):
    from PySide6.QtWidgets import QPushButton
    from maplehelper import osapi
    from maplehelper.i18n import I18n
    from maplehelper.ui import dialogs
    assert dialogs.ISSUES_URL == "https://github.com/Maple-Helper/maple-helper/issues"
    for lang in ("he", "en"):
        body = I18n(lang)("report_saved_body", name="report.zip")
        assert "github.com/Maple-Helper/maple-helper/issues" in body and "report.zip" in body
    opened = []
    monkeypatch.setattr(osapi, "open_url", lambda url: opened.append(url) or True)
    s, profiles, kb = env
    dlg = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
    btn = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Open an issue on GitHub")
    btn.click()
    assert opened == [dialogs.ISSUES_URL]


# --- UX-6: "Don't save" keeps the AI the player had -----------------------------------------------------------------

def test_dont_save_restores_the_previous_ai(env, monkeypatch):
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    s["provider"] = "claude"
    dlg = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
    seen = []
    dlg.account_changed.connect(lambda: seen.append(s["provider"]))
    dlg._on_provider("grok")
    assert dlg._ai().name == "grok" and "Grok" in dlg.install_btn.text()     # the page shows Grok's account
    assert s["provider"] == "claude" and dlg.unsaved()

    class Answer:                     # the "Save your changes?" question, answered "Don't save"
        def __init__(self, *a, **k):
            self.choice = "no"

        def exec(self):
            return 0
    monkeypatch.setattr(dialogs, "ConfirmDialog", Answer)
    dlg._close_clicked()
    assert s["provider"] == "claude" and seen == []


def test_switching_back_before_save_is_no_change(env):
    from maplehelper.ui import dialogs
    s, profiles, kb = env
    s["provider"] = "claude"
    dlg = dialogs.SettingsDialog(s, profiles, kb, lambda *_: "")
    dlg._on_provider("codex")
    dlg._on_provider("claude")
    assert not dlg.unsaved()


# --- UX-10: the connect step says what each AI needs, and lets a player on without one ------------------------------

def test_plan_texts_say_what_each_ai_costs():
    from maplehelper.i18n import I18n
    en = I18n("en")
    assert "free Claude plan doesn't include" in en.p("ob_need_plan", "claude")
    assert "check what your plan includes" in en.p("ob_need_plan", "codex")
    assert "free" in en.p("ob_need_plan", "gemini") and "free" in en.p("ob_need_plan", "grok")
    he = I18n("he")
    for p in ("claude", "codex", "gemini", "grok"):
        assert he.p("ob_need_plan", p) != en.p("ob_need_plan", p)          # translated, not the English fallback
    assert "Gemini" in he("ob_plans_overview") and "ו-Grok" in he("ob_plans_overview")


def test_connect_step_explains_and_offers_a_way_on_without_an_ai(env):
    from maplehelper.ui.dialogs import Onboarding
    s, profiles, kb = env
    dlg = Onboarding(s, profiles, kb, lambda *_: "")
    dlg._go_next()
    assert dlg.stack.currentIndex() == 1 and not dlg.next.isEnabled()
    assert not dlg.no_ai_note.isHidden() and "Play tools" in dlg.no_ai_note.text()
    assert not dlg.skip_ai_btn.isHidden()
    tips = {b.property("value"): b.toolTip() for b in dlg.provider_pick.group.buttons()}
    assert "Claude Code" in tips["claude"] and "free" in tips["grok"]
    dlg._on_status("claude", "ok")                       # connected: Next is the way on, no skip offered
    assert dlg.next.isEnabled() and dlg.no_ai_note.isHidden() and dlg.skip_ai_btn.isHidden()
    dlg._on_status("claude", "not_installed")
    dlg.skip_ai_btn.click()
    assert dlg.pages[dlg.stack.currentIndex()] is dlg.pages[2]          # the character step
    dlg.close()
