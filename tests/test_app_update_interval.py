"""A release reached an open app up to an hour late: the app looked for one only with the hourly KB check.
Now it looks for a new release every 15 minutes on its own timer; the KB check stays hourly."""
from types import SimpleNamespace

from maplehelper import app, updater


class _InlineThread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _fake_app(monkeypatch, frozen=True, mac=False, installed=True):
    monkeypatch.setattr(app.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(app.osapi, "IS_MAC", mac)
    monkeypatch.setattr(updater, "installed_copy", lambda: installed)
    monkeypatch.setattr(app.threading, "Thread", _InlineThread)
    calls = []
    fake = SimpleNamespace(pending_installer=None, _downloading=False)
    fake.main_thread = SimpleNamespace(call=SimpleNamespace(emit=lambda fn: fn()))
    fake.update_found = lambda version: calls.append(("found", version))
    fake.announce_update = lambda version, url: calls.append(("announce", version))
    fake._update_kb_in_background = lambda interactive: calls.append(("kb", interactive))
    fake.check_app_update_silently = lambda: app.MapleHelperApp.check_app_update_silently(fake)
    return fake, calls


def test_an_open_app_looks_for_a_release_every_15_minutes():
    assert app.APP_CHECK_MS == 15 * 60 * 1000
    assert app.KB_CHECK_MS == 60 * 60 * 1000


def test_the_release_check_finds_and_downloads_a_new_version(monkeypatch):
    fake, calls = _fake_app(monkeypatch)
    monkeypatch.setattr(updater, "newer_release", lambda current: ("9.9.9", "url"))
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls == [("found", "9.9.9")]           # and no KB fetch: that stays on the hourly timer


def test_the_release_check_points_at_the_release_on_macos(monkeypatch):
    fake, calls = _fake_app(monkeypatch, mac=True)
    monkeypatch.setattr(updater, "newer_release", lambda current: ("9.9.9", "url"))
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls == [("announce", "9.9.9")]


def test_a_check_during_a_download_or_with_an_installer_waiting_does_nothing(monkeypatch):
    asked = []
    monkeypatch.setattr(updater, "newer_release", lambda current: asked.append(current) or ("9.9.9", "url"))
    fake, calls = _fake_app(monkeypatch)
    fake._downloading = True
    app.MapleHelperApp.check_app_update_silently(fake)
    fake._downloading, fake.pending_installer = False, "setup.exe"
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls == [] and asked == []


def test_a_run_from_source_never_looks_for_a_release(monkeypatch):
    fake, calls = _fake_app(monkeypatch, frozen=False)
    monkeypatch.setattr(updater, "newer_release", lambda current: ("9.9.9", "url"))
    app.MapleHelperApp.check_app_update_silently(fake)
    assert calls == []


def test_the_start_up_check_looks_for_both(monkeypatch):
    fake, calls = _fake_app(monkeypatch)
    monkeypatch.setattr(updater, "newer_release", lambda current: ("9.9.9", "url"))
    app.MapleHelperApp.check_kb_update_silently(fake)
    assert calls == [("found", "9.9.9"), ("kb", False)]

