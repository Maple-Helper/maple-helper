"""The night's KB reached the player hours late: the start-up check failed without a word (the AI's warm process
held the KB folder) and the next try was the 3-hourly one. Now the failure is logged, the warm process is really
gone before the swap, and a failed or postponed update tries again within minutes."""
import logging
from types import SimpleNamespace

import pytest

from maplehelper import app, updater


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_a_failed_swap_says_why_in_the_log(tmp_path, monkeypatch, caplog):
    import hashlib
    import io
    import zipfile
    user_kb = tmp_path / "kb"
    user_kb.mkdir()
    (user_kb / "meta.json").write_text('{"version": "2026.01.01.0000"}', encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.json", '[{"key": "monster/1", "category": "monster"}]')
    data = buf.getvalue()
    url = "https://github.com/Maple-Helper/maple-helper/releases/download/v1/kb.zip"
    manifest = f'{{"version": "2026.02.01.0000", "url": "{url}", "sha256": "{hashlib.sha256(data).hexdigest()}"}}'
    net = {"m": manifest.encode(), url: data}
    monkeypatch.setattr(updater, "USER_KB", user_kb)
    monkeypatch.setattr(updater, "kb_dir", lambda: user_kb)
    monkeypatch.setattr(updater, "MANIFEST_URL", "m")
    monkeypatch.setattr(updater, "_get", lambda u, timeout=30: net.get(u))
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    real_rename = type(user_kb).rename

    def busy(self, target):
        if self == user_kb:
            raise PermissionError("in use by another process")
        return real_rename(self, target)
    monkeypatch.setattr(type(user_kb), "rename", busy)
    with caplog.at_level(logging.WARNING, logger="maplehelper"):
        assert updater.fetch_kb() == "failed"
    assert "can't be swapped" in caplog.text and "in use by another process" in caplog.text


def test_an_offline_check_is_logged_too(monkeypatch, caplog):
    monkeypatch.setattr(updater, "MANIFEST_URL", "m")
    monkeypatch.setattr(updater, "_get", lambda u, timeout=30: None)
    with caplog.at_level(logging.WARNING, logger="maplehelper"):
        assert updater.fetch_kb() == "failed"
    assert "manifest didn't download" in caplog.text


def test_the_warm_process_has_exited_before_the_swap():
    from maplehelper.providers.claude import ClaudeBackend
    events = []

    class Proc:
        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            events.append(("wait", timeout))
    b = ClaudeBackend.__new__(ClaudeBackend)
    import threading
    b._warm, b._warm_lock = Proc(), threading.Lock()
    b.drop_warm()
    assert events == ["kill", ("wait", 5)] and b._warm is None


def _fake_app(qapp, monkeypatch):
    from PySide6.QtCore import QObject, QTimer
    shots = []
    monkeypatch.setattr(app.QTimer, "singleShot", lambda ms, ctx, fn: shots.append(ms))
    fake = SimpleNamespace(main_thread=QObject(), _update_timer=QTimer(interval=3 * 60 * 60 * 1000))
    fake._update_kb_in_background = lambda interactive: None
    return fake, shots


@pytest.mark.parametrize("status", ["failed", "postponed"])
def test_a_failed_or_postponed_update_tries_again_within_minutes_then_less_often(qapp, monkeypatch, status):
    fake, shots = _fake_app(qapp, monkeypatch)
    for _ in range(10):
        app.MapleHelperApp._retry_kb_update(fake, status)
    minute = 60 * 1000
    assert shots == [5 * minute, 10 * minute, 20 * minute, 40 * minute, 80 * minute, 160 * minute]


def test_a_success_starts_the_retries_over(qapp, monkeypatch):
    fake, shots = _fake_app(qapp, monkeypatch)
    app.MapleHelperApp._retry_kb_update(fake, "failed")
    app.MapleHelperApp._retry_kb_update(fake, "failed")
    app.MapleHelperApp._retry_kb_update(fake, "updated")
    app.MapleHelperApp._retry_kb_update(fake, "failed")
    assert shots == [5 * 60 * 1000, 10 * 60 * 1000, 5 * 60 * 1000]


def test_a_deleted_picker_never_fits_its_list(qapp, monkeypatch):
    """An uncaught 'Internal C++ object (EntityPicker) already deleted' from a timer that outlived the picker."""
    import sys

    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    from maplehelper.ui.tools import EntityPicker
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    p = EntityPicker([("Fire Boar", "Fire Boar", None)], "Monster")
    p.setText("Fire")
    p.completer().complete()                 # the list shows: _fit_popup waits for the next turn of the loop
    p.completer().popup().show()
    shiboken6.delete(p)                      # the tools window closed meanwhile
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
    assert errors == []


def test_an_open_app_checks_every_hour_and_retries_within_it(qapp, monkeypatch):
    assert app.KB_CHECK_MS == 60 * 60 * 1000
    fake, shots = _fake_app(qapp, monkeypatch)
    fake._update_timer.setInterval(app.KB_CHECK_MS)
    for _ in range(10):
        app.MapleHelperApp._retry_kb_update(fake, "failed")
    assert shots == [5 * 60 * 1000, 10 * 60 * 1000, 20 * 60 * 1000, 40 * 60 * 1000]
