"""The grind tracker's guide (ui/trackerguide.py): a page for every part with its picture, Back / Next / Skip, shown
once (the setting), and opened from the tracker's Guide button."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper.ui import trackerguide  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("lang", ["he", "en"])
def test_every_page_has_its_picture_and_words(lang):
    from maplehelper.i18n import STRINGS
    for key, pic in trackerguide.PAGES:
        if pic:
            assert (trackerguide.DIR / lang / f"{pic}.png").exists(), (lang, pic)
        for part in ("title", "body"):
            assert STRINGS[f"tg_{key}_{part}"][lang].strip(), (key, part)


def test_back_next_skip_and_seen_once():
    s = {}
    d = trackerguide.TrackerGuide(s, "en", "")
    try:
        assert d.stack.count() == len(trackerguide.PAGES) and not d.back.isVisibleTo(d)
        d.next.click()
        assert d.stack.currentIndex() == 1 and d.back.isVisibleTo(d)
        d.back.click()
        assert d.stack.currentIndex() == 0
        d.go(len(trackerguide.PAGES) - 1)
        assert d.next.text() == "Let's grind!" and not d.skip.isVisibleTo(d)
        d.next.click()
        assert s[trackerguide.SEEN] is True
    finally:
        d.close()


def test_the_tracker_window_has_a_guide_button(isolated_store):
    from pathlib import Path

    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.grindtracker import GrindTracker
    from maplehelper.ui.lootscan import LootScanner
    real = Path(__file__).resolve().parent.parent / "data" / "kb"
    if not (real / "index.json").exists():
        pytest.skip("no real knowledge base")
    kb = KnowledgeBase(real)
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 21)
    s = isolated_store.Settings()
    assert s[trackerguide.SEEN] is False
    scan = LootScanner(kb, p, s)
    d = GrindTracker(scan, kb, s, "en", "")
    asked = []
    d.guide_requested.connect(lambda: asked.append(True))
    try:
        d.guide_btn.click()
        assert asked == [True]
    finally:
        scan.stop()
        d.close()


def _fake_app(monkeypatch, settings):
    from types import SimpleNamespace

    from maplehelper import app as app_mod, whatsnew
    popped = []
    ov = SimpleNamespace(add_notice=lambda *a: None)
    fake = SimpleNamespace(settings=settings, overlay=ov, show_whats_new=lambda notes: None,
                           show_tracker_guide=lambda: popped.append(True))
    monkeypatch.setattr(whatsnew, "since", lambda a, b: [])
    monkeypatch.setattr(app_mod.telemetry, "track", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.QTimer, "singleShot", staticmethod(lambda ms, fn: fn()))
    return app_mod, fake, popped


def test_the_guide_pops_up_once_for_a_player_who_updated(monkeypatch):
    """After the update: once, and seen the moment it shows (closing the app over it never brings it back). A start
    with nothing new, or the next one: nothing."""
    s = {"seen_version": "0.1.0", "tracker_guide_seen": False}
    app_mod, fake, popped = _fake_app(monkeypatch, s)
    app_mod.MapleHelperApp.announce_whats_new(fake, False)
    assert popped == [True] and s["tracker_guide_seen"] is True
    s["seen_version"] = "0.1.0"                      # (even another update later: it was seen)
    app_mod.MapleHelperApp.announce_whats_new(fake, False)
    app_mod.MapleHelperApp.announce_whats_new(fake, False)
    assert popped == [True]


def test_a_new_player_gets_no_guide_popup(monkeypatch):
    """A fresh install: no popup at all, then or on opening the tracker (its Guide button has it)."""
    s = {"seen_version": "", "tracker_guide_seen": False}
    app_mod, fake, popped = _fake_app(monkeypatch, s)
    app_mod.MapleHelperApp.announce_whats_new(fake, True)
    assert popped == [] and s["tracker_guide_seen"] is True
    import inspect
    assert "show_tracker_guide" not in inspect.getsource(app_mod.MapleHelperApp.show_tracker).split("def make")[0] \
        .replace("dlg.guide_requested.connect(self.show_tracker_guide)", "")
