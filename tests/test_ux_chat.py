"""The owner-approved chat UX items (OVL-2, PRF-1, PRF-2, UX-12, OVL-23/UX-20, VIS-6, VIS-7, VIS-11, VIS-18),
offscreen: each test is the case the audit described."""
import os
import threading
import time
import unicodedata
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QRect  # noqa: E402

from maplehelper.brain import Answer  # noqa: E402


@pytest.fixture
def overlay(isolated_store, kb, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from maplehelper import osapi
    from maplehelper.ui import terms
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    monkeypatch.setattr(terms, "LANG", terms.LANG)
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["instant_answers"] = False
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    brain = Mock()
    brain.ask.return_value = Answer(text="Mano is at the Turtle Bridge.")
    ov = Overlay(s, p, kb, brain)
    ov.setGeometry(QRect(-3000, -3000, 520, 680))
    ov.show()
    ov.app = app
    yield ov
    from PySide6.QtCore import QThread
    for th in ov.findChildren(QThread):
        th.quit()
        th.wait(2000)
    ov.hide()
    ov.bubble.hide()
    ov.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def pump(app, ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def shown(text: str) -> str:
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def wait_until(app, cond, ms=5000):
    end = time.time() + ms / 1000
    while not cond() and time.time() < end:
        pump(app, 20)
    return cond()


# ------------------------------------------------------------------ OVL-2: Stop while an answer runs

def test_send_becomes_stop_and_stopping_frees_the_chat(overlay):
    from maplehelper.ui import theme
    release = threading.Event()

    def slow_ask(*a, **kw):
        release.wait(5)
        return Answer(text="a late answer that must not show")
    overlay.brain.ask.side_effect = slow_ask
    assert overlay.ask("where is Mano?")
    assert overlay.busy
    assert overlay.send_btn.isEnabled() and overlay.send_btn.text() == theme.ICON["stop"]
    assert overlay.send_btn.accessibleName() == overlay.t("stop_answer")
    bubble = overlay._pending_bubble
    overlay.send_btn.click()
    assert not overlay.busy
    assert wait_until(overlay.app, lambda: overlay.brain.cancel.called)
    assert shown(bubble._text) == overlay.t("answer_stopped")
    assert overlay.send_btn.text() == theme.ICON["send"] and not overlay.send_btn.isEnabled()
    # the stopped run ends late: its answer goes nowhere, and a new question is taken at once
    overlay.brain.ask.side_effect = None
    overlay.brain.ask.return_value = Answer(text="Mano is at the Turtle Bridge.")
    assert overlay.ask("and Stumpy?")
    new = overlay._pending_bubble
    release.set()
    assert wait_until(overlay.app, lambda: not overlay.busy)
    pump(overlay.app, 100)
    assert shown(new._text) == "Mano is at the Turtle Bridge."
    assert shown(bubble._text) == overlay.t("answer_stopped")


def test_enter_while_busy_never_stops_the_answer(overlay):
    overlay.busy = True
    overlay.input.setText("again?")
    overlay._send_typed()                      # Enter in the field
    assert overlay.busy and not overlay.brain.cancel.called


def test_a_stop_before_the_ai_run_starts_skips_it(kb):
    from maplehelper.brain import Brain
    b = Brain(kb, provider="codex")
    b.backend.exe = "codex.exe"
    b.backend.run = Mock(side_effect=AssertionError("no run after Stop"))
    # (the fixture KB's tables are never built here: that would write into tests/fixtures)
    b.kb.ensure_drop_table = b.cancel          # the player pressed Stop while the prompt was being built
    try:
        assert b.ask("where is Mano?", None, None, None).error == "cancelled"
    finally:
        del b.kb.ensure_drop_table


@pytest.mark.parametrize("name", ["codex", "gemini", "grok"])
def test_every_cli_run_started_after_stop_is_killed(name, monkeypatch, tmp_path, kb):
    import importlib
    import io
    from types import SimpleNamespace
    mod = importlib.import_module(f"maplehelper.providers.{name}")
    backend = getattr(mod, {"codex": "CodexBackend", "gemini": "GeminiBackend", "grok": "GrokBackend"}[name])
    be = backend(SimpleNamespace(kb=kb, api_key=None, model=None))
    be.exe = "cli.exe"
    killed, made = [], []
    monkeypatch.setattr(mod.base, "kill", lambda p: killed.append(p))

    class Proc:
        def __init__(self, *a, **kw):
            self.stdin, self.stdout, self.stderr = io.BytesIO(), iter([]), io.BytesIO(b"")
            self.stdin.close = lambda: None
            self.pid = None
            made.append(self)

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(mod.subprocess, "Popen", Proc)
    be.cancel()                                 # Stop, while nothing runs yet
    assert be._stopped
    # a run (or a retry: Gemini's blocked read, Codex's unknown feature, a bad model) spawned after Stop dies at once
    if name == "codex":
        be._exec_once(["codex"], "q", str(tmp_path), None, None, True)
    elif name == "gemini":
        be._run_in("agent", "q", None, None, True, None, tmp_path)
    else:
        q = tmp_path / "q.txt"
        q.write_text("q", encoding="utf-8")
        be._once("", q, None, False, None, True, None)
    assert made and made[0] in killed
    killed.clear()
    be._proc = made[0]
    be.cancel()
    assert killed == [made[0]]
