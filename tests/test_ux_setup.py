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
