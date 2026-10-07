"""Profile refresh must finish or recover so the player can retry."""
import os
import time
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.mark.parametrize("result", ["success", "no_game", "capture_error", "worker_error", "start_error"])
def test_refresh_releases_busy_state(result, isolated_store, kb, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from maplehelper.brain import Answer
    from maplehelper.ui import overlay

    qapp = QApplication.instance() or QApplication([])
    profiles = isolated_store.Profiles()
    brain = Mock()
    brain.ask.return_value = Answer(profile_update={})
    if result == "worker_error":
        brain.ask.side_effect = RuntimeError("provider failed")
    win = overlay.Overlay(isolated_store.Settings(), profiles, kb, brain)
    win.add_system = Mock()
    win.refresh_profile_chip = Mock()
    win.profile_card.set_busy = Mock()
    finished = Mock()
    win.sync_finished.connect(finished)
    monkeypatch.setattr(overlay.osapi, "find_game_window", lambda: 123)
    capture = Mock(return_value=None if result == "no_game" else b"screenshot")
    if result == "capture_error":
        capture.side_effect = RuntimeError("capture failed")
    monkeypatch.setattr(overlay.osapi, "capture_game", capture)
    if result == "start_error":
        monkeypatch.setattr(overlay, "AskWorker", Mock(side_effect=RuntimeError("startup failed")))

    try:
        win.sync_profile()
        deadline = time.monotonic() + 3
        while win._syncing and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not win._syncing
        assert win.windowOpacity() == 1.0
        win.profile_card.set_busy.assert_called_with(False)
        finished.assert_called_once_with(False)
        if result in ("success", "worker_error"):
            brain.ask.assert_called_once()
            args = brain.ask.call_args.args
            assert "profile_update" in args[0]
            assert args[3] == b"screenshot"
        else:
            brain.ask.assert_not_called()
            win.add_system.assert_called_once()
    finally:
        from shiboken6 import isValid
        thread = getattr(win, "_sync_thread", None)
        if thread is not None and isValid(thread):    # a finished refresh deletes its thread
            thread.quit()
            thread.wait(3000)
        win.close()
