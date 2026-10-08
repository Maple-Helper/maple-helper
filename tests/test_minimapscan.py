"""The minimap scanner (ui/minimapscan.py) and its Settings row: reads run on the interval, nothing runs without
a box, a bad read is "unknown" (never a crash), and the interval saves like every other setting."""
import logging
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
    # the account checks answer at once: installed but signed out (no CLI, no network)
    for name in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(name)), "account",
                            lambda self: {"status": "logged_out", "email": None, "method": None})
        monkeypatch.setattr(type(providers.get(name)), "status", lambda self: "logged_out")
    s = isolated_store.Settings()
    s["language"] = "en"
    s["provider"] = "claude"
    before = set(threading.enumerate())
    yield s, isolated_store.Profiles(), kb
    for th in set(threading.enumerate()) - before:
        th.join(timeout=10)


@pytest.fixture
def clean_location():
    from maplehelper.ui.location import LOCATION
    old_here, old_state = LOCATION.here, LOCATION.state
    LOCATION.here, LOCATION.state = None, ""
    yield LOCATION
    LOCATION.here, LOCATION.state = old_here, old_state


def _make_scanner(s, kb):
    from maplehelper.ui.minimapscan import MinimapScanner
    return MinimapScanner(kb, s)


BOX = {"x": 10, "y": 20, "w": 100, "h": 50}


def test_no_box_scans_nothing(env, clean_location, qapp):
    from maplehelper.minimap import Here
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        clean_location.set(Here("010001000", (0.5, 0.5)))
        clean_location.set_state("unknown")
        sc.restart()
        assert not sc._timer.isActive()
        assert clean_location.here is None and clean_location.state == ""
    finally:
        sc.stop()


def test_a_box_scans_on_the_interval(env, clean_location, qapp):
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        sc.restart()
        assert sc._timer.isActive() and sc._timer.interval() == 1000
        s["minimap_scan_interval"] = 2.5
        sc.restart()
        assert sc._timer.interval() == 2500
    finally:
        sc.stop()


def test_the_interval_is_clamped(env, clean_location, qapp):
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        for value, ms in ((0.01, 200), (600.0, 60000), ("junk", 1000), (None, 1000), (float("nan"), 1000)):
            s["minimap_scan_interval"] = value
            sc.restart()
            assert sc._timer.interval() == ms, value
    finally:
        sc.stop()


def test_a_kb_update_drops_the_locator(env, clean_location, qapp):
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        sc._locator, sc._graph = object(), object()
        sc.set_kb(kb)
        assert sc._locator is None and sc._graph is None
    finally:
        sc.stop()


def test_restart_resets_the_locator_lock(env, clean_location, qapp):
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        resets = []
        sc._locator = type("L", (), {"reset": lambda self: resets.append(1)})()
        s["minimap_region"] = dict(BOX)
        sc.restart()
        assert resets == [1]
    finally:
        sc.stop()


def test_a_tick_skips_while_a_read_runs(env, clean_location, qapp, monkeypatch):
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        sc.restart()
        made = []

        class FakeThread:
            def __init__(self, *a, **k):
                made.append(1)

            def start(self):
                pass

        monkeypatch.setattr(threading, "Thread", FakeThread)
        sc._reading = True
        sc._tick()
        assert made == []
        sc._reading = False
        sc._tick()
        assert made == [1]
    finally:
        sc.stop()


class _Locator:
    def __init__(self, answer):
        self.answer = answer

    def locate(self, img):
        return self.answer

    def reset(self):
        pass


class _Graph:
    def name(self, mid):
        return "Henesys"


def test_an_answer_reaches_location(env, clean_location, qapp, monkeypatch, caplog):
    from maplehelper.minimap import Here
    from maplehelper.ui import minimapscan
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        sc.restart()
        sc._locator, sc._graph = _Locator(Here("010001000", (0.5, 0.5))), _Graph()
        monkeypatch.setattr(minimapscan.capture, "grab_image", lambda *a: object())
        with caplog.at_level(logging.INFO, logger="maplehelper"):
            sc._read(dict(BOX))
            assert clean_location.here == Here("010001000", (0.5, 0.5)) and clean_location.state == ""
            sc._read(dict(BOX))             # the same map again: logged once, not twice
        assert [r for r in caplog.records if r.name == "maplehelper" and "Henesys" in r.getMessage()] != []
        infos = [r for r in caplog.records
                 if r.name == "maplehelper" and r.levelno == logging.INFO and "minimap:" in r.getMessage()]
        assert len(infos) == 1
    finally:
        sc.stop()


def test_an_unrecognized_box_reads_as_unknown(env, clean_location, qapp, monkeypatch):
    from maplehelper.ui import minimapscan
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        sc.restart()
        sc._locator, sc._graph = _Locator(None), _Graph()
        monkeypatch.setattr(minimapscan.capture, "grab_image", lambda *a: object())
        sc._read(dict(BOX))
        assert clean_location.here is None and clean_location.state == "unknown"
    finally:
        sc.stop()


def test_a_failed_read_logs_once_and_reads_unknown(env, clean_location, qapp, monkeypatch, caplog):
    from maplehelper.ui import minimapscan
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        sc.restart()
        sc._locator, sc._graph = _Locator(None), _Graph()
        monkeypatch.setattr(minimapscan.capture, "grab_image", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
        with caplog.at_level(logging.WARNING, logger="maplehelper"):
            sc._read(dict(BOX))
            sc._read(dict(BOX))             # the same error again: logged once, not twice
            assert clean_location.here is None and clean_location.state == "unknown"
        warnings = [r for r in caplog.records
                    if r.name == "maplehelper" and r.levelno == logging.WARNING and "boom" in r.getMessage()]
        assert len(warnings) == 1
    finally:
        sc.stop()


def test_misses_back_off_and_hits_reset(env, clean_location, qapp, monkeypatch):
    from maplehelper.minimap import Here
    from maplehelper.ui import minimapscan
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        s["minimap_scan_interval"] = 1.0
        sc.restart()
        sc._locator, sc._graph = _Locator(None), _Graph()
        grabs = []
        monkeypatch.setattr(minimapscan.capture, "grab_image", lambda *a: grabs.append(1) or object())
        now = [1000.0]
        monkeypatch.setattr(minimapscan.time, "monotonic", lambda: now[0])
        started = []

        class FakeThread:
            def __init__(self, *a, **k):
                started.append(1)

            def start(self):
                pass

        monkeypatch.setattr(threading, "Thread", FakeThread)
        sc._read(dict(BOX))                     # a miss: unknown, and no read for max(5 s, 5 x 1 s)
        assert clean_location.state == "unknown" and sc._cooldown_until == 1005.0
        sc._tick()
        assert started == [] and not sc._reading
        now[0] = 1004.9
        sc._tick()
        assert started == []
        now[0] = 1005.0
        sc._tick()
        assert started == [1] and sc._reading
        sc._reading = False
        sc._locator = _Locator(Here("010001000", (0.5, 0.5)))
        sc._read(dict(BOX))                     # a hit: back to reading every interval
        assert sc._cooldown_until == 0.0
        sc._tick()
        assert started == [1, 1]
    finally:
        sc.stop()


def test_restart_clears_the_backoff_and_failures_back_off(env, clean_location, qapp, monkeypatch):
    from maplehelper.ui import minimapscan
    s, _, kb = env
    sc = _make_scanner(s, kb)
    try:
        s["minimap_region"] = dict(BOX)
        s["minimap_scan_interval"] = 10.0     # the backoff is max(5 s, 5 x 10 s) = 50 s
        sc.restart()
        now = [2000.0]
        monkeypatch.setattr(minimapscan.time, "monotonic", lambda: now[0])
        sc._complain("RuntimeError: boom")    # a failing read backs off too
        assert sc._cooldown_until == 2050.0 and clean_location.state == "unknown"
        sc.restart()                          # a new box or interval: read right away
        assert sc._cooldown_until == 0.0
    finally:
        sc.stop()


# --- the Settings row ---------------------------------------------------------------------------------------------


def test_the_scan_row_saves_and_detects_changes(env, qapp):
    from maplehelper.ui.dialogs import SettingsDialog
    s, p, kb = env
    dlg = SettingsDialog(s, p, kb, lambda *_: "")
    try:
        assert (dlg.scan.minimum(), dlg.scan.maximum(), dlg.scan.singleStep()) == (0.2, 60.0, 0.5)
        assert dlg.scan.value() == 1.0 and not dlg.unsaved()
        dlg.scan.setValue(2.5)
        assert dlg.unsaved()
        dlg._save()                 # stores it (Save closes the dialog, so _initial stays as it was, like every row)
        assert s["minimap_scan_interval"] == 2.5 and not dlg.isVisible()
    finally:
        dlg.close()


def test_the_scan_row_clamps_a_wild_setting(env, qapp):
    from maplehelper.ui.dialogs import SettingsDialog
    s, p, kb = env
    s["minimap_scan_interval"] = 600.0
    dlg = SettingsDialog(s, p, kb, lambda *_: "")
    try:
        assert dlg.scan.value() == 60.0 and not dlg.unsaved()
    finally:
        dlg.close()
