"""The chat says why there's no screenshot: the game is covered (bring it to the front) or, on macOS, Screen
Recording is off; it never sends another window as the game (offscreen Qt, the OS layer faked)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402

from maplehelper import capture  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402


@pytest.fixture
def overlay(isolated_store, kb, monkeypatch):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper import osapi
    from maplehelper.ui import terms
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(terms, "LANG", terms.LANG)

    def covered_game():
        capture.LAST_PROBLEM = None
        return 77

    def covered_capture(hwnd):
        capture.LAST_PROBLEM = "covered"
        return None
    monkeypatch.setattr(osapi, "find_game_window", covered_game)
    monkeypatch.setattr(osapi, "capture_game", covered_capture)
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    p.set_active(p.add("Elipaz", "Thief", "Assassin", 32).id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 460, 640))
    ov.show()
    yield ov
    capture.LAST_PROBLEM = None
    ov.hide()
    ov.bubble.hide()
    ov.deleteLater()


def lines(ov):
    from maplehelper.ui.overlay import SystemLine
    return [w.text() for w in ov.findChildren(SystemLine)]


def test_recapture_of_a_covered_game_asks_to_bring_it_forward(overlay):
    overlay._do_recapture()
    covered = I18n("he")("shot_game_covered")
    assert overlay.shot is None
    assert any(covered[:20] in t for t in lines(overlay))
    assert I18n("he")("shot_game_covered").split("(")[0][:20] in overlay.shot_hint.text()


def test_profile_sync_of_a_covered_game_says_so(overlay):
    overlay._syncing = True
    overlay._sync_capture()
    assert any(I18n("he")("shot_game_covered")[:20] in t for t in lines(overlay))
    assert not any(I18n("he")("sync_no_game")[:20] in t for t in lines(overlay))


def test_mac_without_screen_recording_says_how_to_grant_it(overlay, monkeypatch):
    from maplehelper import osapi

    def no_grant(hwnd):
        capture.LAST_PROBLEM = "screen_permission"
        return None
    monkeypatch.setattr(osapi, "capture_game", no_grant)
    overlay._do_recapture()
    assert any(I18n("he")("perm_screen_body")[:20] in t for t in lines(overlay))


def test_login_item_refused_on_a_disk_image_tells_the_player(monkeypatch):
    from types import SimpleNamespace

    from maplehelper import app, osapi
    toasts = []
    me = SimpleNamespace(settings={"start_with_windows": True, "language": "en"},
                         toast=lambda title, body="", timeout_ms=0: toasts.append(body))
    monkeypatch.setattr(osapi, "set_autostart", lambda on, args: False)
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)     # a source run leaves the Run value alone
    app.MapleHelperApp.apply_autostart(me)
    assert toasts == [I18n("en")("start_at_login_move")]
    monkeypatch.setattr(osapi, "set_autostart", lambda on, args: None)      # Windows: nothing to say
    app.MapleHelperApp.apply_autostart(me)
    assert len(toasts) == 1
