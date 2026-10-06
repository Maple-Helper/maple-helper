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


def test_a_partial_answer_kept_after_stop_ends_in_an_ellipsis(overlay):
    """review3 OVL2-b: what streamed before Stop stays, marked as cut off."""
    release = threading.Event()

    def slow_ask(*a, **kw):
        release.wait(5)
        return Answer(text="late")
    overlay.brain.ask.side_effect = slow_ask
    assert overlay.ask("where is Mano?")
    bubble = overlay._pending_bubble
    bubble.set_text("Mano is at the Turtle Bridge, ")     # streamed so far
    overlay.stop_answer()
    release.set()
    assert shown(bubble._text) == "Mano is at the Turtle Bridge, …"
    assert wait_until(overlay.app, lambda: overlay.brain.cancel.called)


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
def test_every_cli_run_started_after_stop_is_killed(name, monkeypatch, tmp_path, kb, caplog):
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
    # a Stop is no AI failure in the log ("Report a problem" showed one, review3 OVL2-a)
    assert not [r for r in caplog.records if r.levelname == "WARNING" and "no answer" in r.getMessage()]
    killed.clear()
    be._proc = made[0]
    be.cancel()
    assert killed == [made[0]]


# ------------------------------------------------------------------ PRF-1: warm only while the chat is in use

def test_no_warm_process_until_the_chat_opens_and_none_after_a_long_close(kb, monkeypatch):
    from maplehelper import brain as brain_mod
    from maplehelper.brain import Brain
    b = Brain(kb, provider="claude")
    b.backend = Mock(exe="claude.exe")
    b.prewarm()                                  # a --background start, settings saved, a KB update...
    assert not b.backend.prewarm.called
    b.chat_shown(True)
    b.prewarm()
    assert b.backend.prewarm.call_count == 1
    b.chat_shown(False)
    b.prewarm()                                  # closed a moment ago: still warm for the next F9
    assert b.backend.prewarm.call_count == 2
    now = time.monotonic()
    monkeypatch.setattr(brain_mod.time, "monotonic", lambda: now + brain_mod.WARM_IDLE_S + 1)
    b.prewarm()
    assert b.backend.prewarm.call_count == 2 and not b.wants_warm()


def test_a_chat_left_open_with_no_question_stops_being_warmed_and_typing_warms_again(kb, monkeypatch):
    from maplehelper import brain as brain_mod
    from maplehelper.brain import Brain
    b = Brain(kb, provider="claude")
    b.backend = Mock(exe="claude.exe")
    clock = [1000.0]
    monkeypatch.setattr(brain_mod.time, "monotonic", lambda: clock[0])
    b.chat_shown(True)
    assert b.wants_warm()
    clock[0] += brain_mod.WARM_IDLE_S - 60
    assert b.wants_warm()                        # open, opened 59 min ago: still renewed
    clock[0] += 120
    assert not b.wants_warm()                    # open for an hour with no question: the 15-min renewal stops
    assert b.note_use() is True                  # typing again: the overlay warms one
    assert b.wants_warm() and b.note_use() is False
    clock[0] += brain_mod.WARM_IDLE_S + 1
    b.backend.exe = ""
    monkeypatch.setattr(b, "_find_cli", lambda: None)
    assert b.ask("hi", None, None, None).error == "not_installed"     # a question also counts as use
    assert b.wants_warm()


def test_typing_after_a_quiet_hour_warms_the_ai_again(overlay):
    overlay.brain.note_use.return_value = False
    overlay.brain.prewarm.reset_mock()
    overlay.input.setText("where is")
    overlay.app.processEvents()
    assert overlay.brain.note_use.called and not overlay.brain.prewarm.called
    overlay.brain.note_use.return_value = True
    overlay.input.setText("where is mano")
    assert wait_until(overlay.app, lambda: overlay.brain.prewarm.called)


def test_the_15_minute_renewal_stops_once_the_chat_was_closed_long(monkeypatch):
    from types import SimpleNamespace

    from maplehelper.providers import claude
    be = claude.ClaudeBackend.__new__(claude.ClaudeBackend)
    import threading as th
    be._warm_lock = th.Lock()
    proc = Mock()
    proc.poll.return_value = None
    be._warm, be.brain = proc, SimpleNamespace(wants_warm=lambda: False)
    be.prewarm = Mock()
    be._refresh(proc)
    assert not be.prewarm.called and proc.kill.called and be._warm is None
    be._warm, be.brain = proc, SimpleNamespace(wants_warm=lambda: True)
    be._refresh(proc)
    assert be.prewarm.called


def test_opening_the_chat_warms_and_closing_it_tells_the_brain(overlay):
    overlay.hide()
    overlay.open_overlay(None, None)
    overlay.brain.chat_shown.assert_called_with(True)
    assert wait_until(overlay.app, lambda: overlay.brain.prewarm.called)
    overlay.close_overlay()
    overlay.brain.chat_shown.assert_called_with(False)


def test_a_background_start_starts_no_ai_process():
    import inspect

    from maplehelper import app
    src = inspect.getsource(app.MapleHelperApp.start)
    assert "brain.prewarm" not in src


# ------------------------------------------------------------------ UX-12: the speech model's download is asked first

def test_the_first_voice_press_asks_before_any_download(monkeypatch, tmp_path):
    from maplehelper import voice
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "has_nvidia", lambda: True)
    vc = voice.VoiceController()
    asked, started = [], []
    vc.need_download.connect(asked.append)
    vc.started.connect(lambda: started.append(True))
    monkeypatch.setattr(vc.transcriber, "download", lambda *a: pytest.fail("nothing is fetched before a yes"))
    vc.toggle()
    assert asked == [voice.MODEL_BYTES + voice.CUBLAS_BYTES] and not started and vc._stream is None


def test_the_download_reports_progress_and_can_be_cancelled(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from maplehelper import voice
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "has_nvidia", lambda: False)
    vc = voice.VoiceController()
    gate = threading.Event()

    def fake_download(cancel, on_bytes=None):
        blobs = voice.Transcriber.snapshot().parent.parent / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "abc.incomplete").write_bytes(b"x" * (voice.MODEL_BYTES // 2000))
        gate.wait(5)
        if cancel.is_set():
            raise voice.DownloadCancelled()
    monkeypatch.setattr(vc.transcriber, "download", fake_download)
    pcts, ends, states = [], [], []
    vc.download_progress.connect(pcts.append)
    vc.download_done.connect(ends.append)
    vc.state.connect(states.append)
    vc.download()
    assert vc.downloading() and pcts[0] == 0 and states == ["downloading"]
    vc.toggle()                                  # the talk key meanwhile: no second download, no recording
    assert vc._stream is None
    vc._report_progress()
    assert pcts[-1] == 0 or 0 < pcts[-1] < 99
    vc.cancel_download()
    gate.set()
    assert wait_until(app, lambda: ends == ["cancelled"])
    assert not vc.downloading() and states[-1] == "idle"


def test_a_missing_cublas_is_asked_too_and_never_fetched_silently(monkeypatch, tmp_path):
    """review3 UX12-a: the model on disk without cuBLAS on an NVIDIA PC: asked with its size, "not now" runs on
    the CPU, and loading the model never downloads it."""
    import sys
    import types

    from maplehelper import voice
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "has_nvidia", lambda: True)
    snap = voice.Transcriber.snapshot()
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    monkeypatch.setattr(voice, "download_gpu_libs", lambda *a: pytest.fail("no cuBLAS download without a yes"))
    vc = voice.VoiceController()
    gpu, full = [], []
    vc.need_gpu_download.connect(gpu.append)
    vc.need_download.connect(full.append)
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Mock()))
    vc.toggle()
    assert gpu == [voice.CUBLAS_BYTES] and not full and vc._stream is None
    vc.skip_gpu()                                # "not now"
    vc.toggle()
    assert gpu == [voice.CUBLAS_BYTES] and vc._stream is not None       # recording, on the CPU
    vc._stream = None
    devices = []

    class Model:
        def __init__(self, src, device, **kw):
            devices.append(device)
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Model))
    vc.transcriber.load()
    assert devices == ["cpu"]


def test_a_failed_cublas_part_of_the_agreed_download_falls_back_to_the_cpu(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from maplehelper import voice
    monkeypatch.setattr(voice, "DATA_DIR", tmp_path)
    monkeypatch.setattr(voice, "has_nvidia", lambda: True)
    snap = voice.Transcriber.snapshot()
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    tries = []

    def broken(progress=None):
        tries.append(True)
        raise ConnectionError("network blip")
    monkeypatch.setattr(voice, "download_gpu_libs", broken)
    vc = voice.VoiceController()
    ends, asked = [], []
    vc.download_done.connect(ends.append)
    vc.need_gpu_download.connect(asked.append)
    vc.download()
    assert wait_until(app, lambda: ends == ["done"])
    assert tries == [True] and vc._gpu_skipped
    import sys
    import types
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Mock()))
    vc.toggle()
    assert not asked and vc._stream is not None and tries == [True]      # no second, silent try


def test_a_finished_download_row_drops_its_cancel_and_reads_100(overlay):
    """review3 UX12-c: it kept "37%" and a faint Cancel chip."""
    from maplehelper.ui.widgets import SystemLine
    overlay.voice_download_progress(0)
    overlay.voice_download_progress(37)
    dl = overlay._voice_dl_row
    overlay.voice_download_finished("done")
    assert not dl.isEnabled() and not any(b.isVisible() for b in dl.chips)
    assert "100%" in shown(dl.findChild(SystemLine).text())


def test_the_chat_asks_for_the_nvidia_part_with_its_size(overlay):
    from maplehelper.ui.widgets import SystemLine
    yes, no = [], []
    overlay.voice_download_requested.connect(lambda: yes.append(True))
    overlay.voice_gpu_declined.connect(lambda: no.append(True))
    overlay.offer_voice_gpu_download(553_162_896)
    row = overlay._voice_offer
    assert "553 MB" in shown(row.findChild(SystemLine).text()).replace("\xa0", " ")
    row.chips[1].click()
    assert no == [True] and not yes and not _in_feed(overlay, row)


def _in_feed(overlay, w) -> bool:
    return any(overlay.feed_lay.itemAt(i).widget() is w for i in range(overlay.feed_lay.count()))


def test_not_now_takes_the_question_away_and_the_mic_asks_again(overlay):
    """"Not now" left the question in the chat, greyed out: it looked like the click did nothing."""
    yes = []
    overlay.voice_download_requested.connect(lambda: yes.append(True))
    overlay.offer_voice_download(1_621_700_000)
    row = overlay._voice_offer
    row.chips[1].click()
    assert not yes and not _in_feed(overlay, row)
    overlay.offer_voice_download(1_621_700_000)              # the mic again: asked again
    assert overlay._voice_offer is not row and _in_feed(overlay, overlay._voice_offer)


def test_the_chat_asks_with_the_size_then_shows_progress_and_cancel(overlay):
    from maplehelper.ui.widgets import SystemLine
    yes, cancel = [], []
    overlay.voice_download_requested.connect(lambda: yes.append(True))
    overlay.voice_download_cancel.connect(lambda: cancel.append(True))
    overlay.offer_voice_download(1_621_700_000)
    overlay.offer_voice_download(1_621_700_000)          # pressed again before answering: asked once
    row = overlay._voice_offer
    assert "1.6 GB" in shown(row.findChild(SystemLine).text()).replace("\xa0", " ")
    row.chips[0].click()
    assert yes == [True] and not row.isEnabled()
    overlay.voice_download_progress(0)
    overlay.voice_download_progress(42)
    dl = overlay._voice_dl_row
    assert "42%" in shown(dl.findChild(SystemLine).text())
    dl.chips[0].click()
    assert cancel == [True]
    overlay.voice_download_progress(43)                   # a late report: no new row
    assert overlay._voice_dl_row is dl
    overlay.voice_download_finished("cancelled")
    rows = [overlay.feed_lay.itemAt(i).widget() for i in range(overlay.feed_lay.count())]
    assert shown(next(w for w in reversed(rows) if isinstance(w, SystemLine)).text()) == overlay.t("voice_dl_stopped")


# ------------------------------------------------------------------ OVL-23 / UX-20: the tour's header steps

@pytest.mark.parametrize("lang", ["en", "he"])
def test_the_tour_shows_the_news_button_and_walks_the_header_in_reading_order(overlay, lang):
    from maplehelper.i18n import I18n
    from maplehelper.ui import tour
    overlay.settings["language"] = lang
    overlay.t = I18n(lang)
    overlay.apply_language()
    pump(overlay.app, 50)
    header = ("history_btn", "news_btn", "guides_btn", "wish_btn", "tools_btn", "settings_btn", "min_btn",
              "close_btn")
    steps = [p for p, _ in tour.STEPS if p in header]
    assert steps == list(header) and ("news_btn", "tour_news") in tour.STEPS
    xs = [getattr(overlay, p).mapTo(overlay, getattr(overlay, p).rect().center()).x() for p in steps]
    assert xs == sorted(xs, reverse=(lang == "he"))      # the light moves one way along the header
    overlay.start_tour()
    tr = overlay._tour
    seen = [k for _, k in tr.steps]
    assert "tour_news" in seen
    tr.go(seen.index("tour_news"))
    assert shown(tr.title.text()) == overlay.t("tour_news_title")
    assert tr.count.text() == f"{seen.index('tour_news')} / {len(seen) - 2}"
    tr.finish()


# ------------------------------------------------------------------ VIS-11: the tag line follows its question

@pytest.mark.parametrize("question,ui_rtl,rtl", [("מה הוא מפיל?", True, True), ("what does it drop?", True, False),
                                                 ("what does it drop?", False, False)])
def test_a_bubbles_tag_line_sits_on_its_questions_side(question, ui_rtl, rtl):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.ui.widgets import Bubble
    b = Bubble(question, "user", ui_rtl, tag="Orange Mushroom, Blue Snail")
    label = b.tag_label
    assert bool(label.alignment() & Qt.AlignRight) == rtl and label.alignment() & Qt.AlignAbsolute
    assert ("↪" if rtl else "↩") in label.text()
    assert label.text().startswith("‏") == rtl          # an RTL line, though it starts with an English name


# ------------------------------------------------------------------ VIS-18: a narrow chat's input hint keeps the talk key

def test_a_narrow_field_shows_the_short_hint_with_the_talk_key(overlay):
    from maplehelper.i18n import I18n
    overlay.settings["language"] = "en"
    overlay.t = I18n("en")
    overlay.apply_language()
    field = overlay.input
    field.resize(900, field.height())
    pump(overlay.app, 20)
    assert shown(field.placeholderText()) == overlay.t("input_placeholder")
    full = field.fontMetrics().horizontalAdvance(overlay.t("input_placeholder"))
    field.resize(full - 20, field.height())                # (470 px at the large font)
    pump(overlay.app, 20)
    assert shown(field.placeholderText()) == overlay.t("input_placeholder_short") and "F10" in shown(field.placeholderText())
    overlay.voice_state("listening")
    assert shown(field.placeholderText()) != overlay.t("input_placeholder_short")
    overlay.voice_state("idle")                            # back to the field's own hint, short as before
    assert shown(field.placeholderText()) == overlay.t("input_placeholder_short")


# ------------------------------------------------------------------ VIS-6 / VIS-7: empty windows say why

def _labels(w):
    from PySide6.QtWidgets import QLabel
    return [shown(lb.text()) for lb in w.findChildren(QLabel) if lb.text() and not lb.isHidden()]


@pytest.mark.parametrize("lang", ["he", "en"])
def test_an_empty_history_says_nothing_was_asked_yet(lang):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.i18n import I18n
    from maplehelper.ui.pinsview import HistoryDialog
    t = I18n(lang)
    d = HistoryDialog([], "Elipaz", lang, "")
    texts = _labels(d)
    assert t("history_empty") in texts
    assert t("history_none") not in texts and t("history_count", n=0) not in texts
    d.search.setText("Mano")                              # a search in an empty history: still the same line
    d._new_search() if hasattr(d, "_new_search") else d._fill()
    assert t("history_empty") in _labels(d)
    d.close()
    full = HistoryDialog([{"q": "where?", "a": "there", "t": time.time()}], "Elipaz", lang, "")
    full.search.setText("zzz")
    full._fill()
    assert t("history_none") in _labels(full) and t("history_empty") not in _labels(full)
    full.close()


@pytest.mark.parametrize("lang", ["he", "en"])
def test_whats_new_with_no_notes_says_so(lang):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.i18n import I18n
    from maplehelper.ui.patchnotes import WhatsNewDialog
    d = WhatsNewDialog([], lang, "")
    assert I18n(lang)("whats_new_empty") in _labels(d)
    d.close()
