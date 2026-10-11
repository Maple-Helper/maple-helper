"""A dialog's background checks (account, status, models, usage, API key, sign-out, microphone) never keep the dialog
alive: when the thread held its last reference, the dialog was freed on that thread, off the GUI thread, and PySide6
6.12 crashed the app (the CI crash of 2026-10-08). The thread holds only a bridge, deleted with its dialog."""
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_a_background_emit_reaches_the_bridge_only_on_the_gui_thread(qapp):
    """A thread emitting on a bridge while the GUI thread deleted it read a half-freed C++ object: an access
    violation that killed CI's test runs (test_dialogs_provider, 2026-10-08 to 10-10). The thread's emit now goes
    through a relay that lives as long as the app: the bridge's signal is touched only on the GUI thread, where it is
    alive or already deleted (dropped), never mid-delete."""
    from PySide6.QtCore import QCoreApplication

    from maplehelper.ui import dialogs

    class Signal:                       # stands in for a bridge's signal: records which thread touches it
        def __init__(self, gone=False):
            self.calls, self.gone = [], gone

        def emit(self, *args):
            self.calls.append((args, threading.current_thread() is threading.main_thread()))
            if self.gone:
                raise RuntimeError("Internal C++ object already deleted.")

    live, gone = Signal(), Signal(gone=True)
    th = threading.Thread(target=lambda: (dialogs._emit(live, {"x": 1}), dialogs._emit(gone)))
    th.start()
    th.join(5)
    assert live.calls == [] and gone.calls == []              # nothing touched on the thread
    QCoreApplication.processEvents()
    assert live.calls == [(({"x": 1},), True)]                 # on the GUI thread
    assert gone.calls == [((), True)]                          # there too, and its deleted bridge dropped quietly


@pytest.fixture
def gate(qapp, monkeypatch):
    """Every provider call blocks until the test opens the gate: the check is still running when the dialog goes."""
    from maplehelper import providers
    gate = threading.Event()

    def blocked(result):
        def call(self, *a, **k):
            gate.wait(10)
            return result
        return call
    for p in providers.PROVIDERS.values():
        monkeypatch.setattr(type(p), "account", blocked({"status": "ok", "email": "a@b.c", "method": None}))
        monkeypatch.setattr(type(p), "status", blocked("ok"))
        monkeypatch.setattr(type(p), "models", blocked([(None, "")]))
        monkeypatch.setattr(type(p), "read_limits", blocked(None))
        monkeypatch.setattr(type(p), "test_api_key", blocked(True))
        monkeypatch.setattr(type(p), "logout", blocked(None))
    before = set(threading.enumerate())
    yield gate
    gate.set()
    for th in set(threading.enumerate()) - before:
        th.join(timeout=10)
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.processEvents()           # the late emits land on deleted bridges: dropped, no crash


def _reaches(obj, target, depth=4, seen=None) -> bool:
    """`target` reachable from `obj` through closures, bound methods, defaults and containers."""
    seen = set() if seen is None else seen
    if obj is target:
        return True
    if depth == 0 or id(obj) in seen:
        return False
    seen.add(id(obj))
    nxt = []
    if hasattr(obj, "__self__"):                       # bound method
        nxt.append(obj.__self__)
    if hasattr(obj, "__func__"):
        nxt.append(obj.__func__)
    for cell in getattr(obj, "__closure__", None) or ():
        try:
            nxt.append(cell.cell_contents)
        except ValueError:                             # an empty cell
            pass
    nxt += list(getattr(obj, "__defaults__", None) or ())
    if isinstance(obj, (list, tuple, set)):
        nxt += list(obj)
    elif isinstance(obj, dict):
        nxt += list(obj.values())
    return any(_reaches(o, target, depth - 1, seen) for o in nxt)


def _threads_hold_the_dialog(make, start, monkeypatch) -> list[str]:
    """The threads `start` set running that can reach the dialog: each must hold only a bridge. Targets are taken
    as each thread starts (a check that answers at once has already dropped its target by the time we'd look)."""
    dlg = make()
    started = []
    real_start = threading.Thread.start

    def record(th):
        started.append((th.name, th._target))
        return real_start(th)
    monkeypatch.setattr(threading.Thread, "start", record)
    start(dlg)
    monkeypatch.setattr(threading.Thread, "start", real_start)
    assert started, "no background check started: the test checks nothing"
    held = [name for name, target in started if _reaches(target, dlg)]
    dlg.close()
    return held


def test_onboarding_status_check(gate, isolated_store, kb, monkeypatch):
    from maplehelper.ui.dialogs import Onboarding
    s = isolated_store.Settings()
    s["language"] = "en"
    assert _threads_hold_the_dialog(lambda: Onboarding(s, isolated_store.Profiles(), kb, lambda *_: ""),
                                    lambda d: d._check_status(), monkeypatch) == []


def test_onboarding_api_key_check(gate, isolated_store, kb, monkeypatch):
    from maplehelper.ui.dialogs import Onboarding
    s = isolated_store.Settings()
    s["language"] = "en"

    def start(d):
        d.key_edit.setText("sk-ant-api03-" + "x" * 40)
        d._check_key()
    assert _threads_hold_the_dialog(lambda: Onboarding(s, isolated_store.Profiles(), kb, lambda *_: ""), start, monkeypatch) == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_settings_account_models_and_usage_checks(gate, isolated_store, kb, provider, monkeypatch):
    from maplehelper.ui.dialogs import SettingsDialog
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = provider

    def start(d):
        d._refresh_account()
        d._fill_models()
    assert _threads_hold_the_dialog(lambda: SettingsDialog(s, isolated_store.Profiles(), kb, lambda *_: ""), start, monkeypatch) == []
