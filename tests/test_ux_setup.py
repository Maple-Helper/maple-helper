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
