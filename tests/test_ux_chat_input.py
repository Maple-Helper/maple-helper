"""The chat's input row and around it (owner-approved UX items CHAT-04, 05, 08, 10, 11, 16, 17, PERF-08), offscreen."""
import os
import time
import unicodedata

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QMimeData, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper.brain import Answer  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        time.sleep(0.005)


def shown(text: str) -> str:
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


@pytest.fixture
def overlay(isolated_store, kb, monkeypatch):
    from maplehelper import osapi
    from maplehelper.ui import terms
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    monkeypatch.setattr(terms, "LANG", terms.LANG)
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["language"] = "en"
    s["tour_done"] = True
    s["instant_answers"] = False
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 470, 700))
    ov.show()
    yield ov
    from PySide6.QtCore import QThread
    for th in ov.findChildren(QThread):
        th.quit()
        th.wait(2000)
    ov.hide()
    ov.deleteLater()
    pump(10)


@pytest.fixture
def fake_worker(monkeypatch):
    from test_chat_audit import FakeWorker

    from maplehelper.ui import overlay as ov_mod
    FakeWorker.made = []
    monkeypatch.setattr(ov_mod, "AskWorker", FakeWorker)
    return FakeWorker


def lines(ov):
    from maplehelper.ui.widgets import SystemLine
    pump(20)                # (a new row shows at the next event loop)
    return [shown(w.text()) for w in ov.feed.findChildren(SystemLine) if not w.isHidden()]


# ------------------------------------------------------------------ CHAT-04: clear, with Undo

def test_clear_can_be_undone_and_the_ai_forgets_only_when_it_expires(overlay, isolated_store):
    from maplehelper.ui.widgets import NoticeCard
    ov = overlay
    h = isolated_store.History(ov.profiles.active_id)
    h.append("user", "where do snails live?")
    h.append("assistant", "Snail Park.")
    ov.add_bubble("where do snails live?", "user")
    ov.add_bubble("Snail Park.", "assistant")
    ov.set_tags(["monster/100100"])
    ov.clear_btn.click()
    pump(20)
    notes = [n for n in ov.feed.findChildren(NoticeCard) if not n.isHidden()]
    assert ov.feed_lay.count() == 2 and len(notes) == 1               # the note, and the stretch
    assert shown(notes[0].btn.text()) == "Undo" and not ov.focus_keys
    assert h.conversation()                    # not forgotten yet
    notes[0].btn.click()
    pump(20)
    assert ov.feed_lay.count() == 3 and ov.focus_keys == ["monster/100100"]
    assert ov.feed_lay.itemAt(0).widget() is None                  # the stretch stays first
    assert all(not ov.feed_lay.itemAt(i).widget().isHidden() for i in (1, 2))
    assert h.conversation()
    # cleared again and left alone: final after UNDO_SECONDS
    ov.clear_btn.click()
    assert ov._clear_timer.isActive() and ov._clear_timer.interval() == ov.UNDO_SECONDS * 1000
    ov._clear_timer.timeout.emit()
    pump(20)
    assert ov.feed_lay.count() == 1 and not h.conversation()


def test_a_question_within_the_undo_seconds_starts_the_new_conversation(overlay, fake_worker, isolated_store):
    ov = overlay
    h = isolated_store.History(ov.profiles.active_id)
    h.append("user", "where do snails live?")
    ov.add_bubble("where do snails live?", "user")
    ov.clear_btn.click()
    assert ov.ask("what about pigs?")
    assert [r["text"] for r in h.conversation()] == ["what about pigs?"]
    assert getattr(ov, "_cleared", None) is None and not ov._clear_timer.isActive()


def test_clear_mid_answer_asks_and_no_keeps_the_answer(overlay, fake_worker):
    ov = overlay
    assert ov.ask("how do I get to Ellinia?")
    ov.clear_btn.click()
    ov.clear_btn.click()                       # asked once, not twice
    asks = [ln for ln in lines(ov) if ln == I18n("en")("clear_busy_ask")]
    assert ov.busy and len(asks) == 1
    ov._clear_ask.chips[1].click()             # Cancel
    assert ov.busy
    fake_worker.made[-1].done.emit(Answer(text="Take the boat."))
    pump(20)
    from maplehelper.ui.widgets import Bubble
    assert "Take the boat." in [b._text for b in ov.feed.findChildren(Bubble)]


# ------------------------------------------------------------------ CHAT-05: the screenshot hint, one line after the first time

def test_the_shot_hint_is_whole_once_then_one_line_with_a_tooltip(overlay):
    ov = overlay
    t = I18n("en")
    ov.open_overlay(None, None)
    assert "The game isn't open, so there's no screenshot" in shown(ov.shot_hint.text())
    assert ov.shot_hint.toolTip() == ""
    ov.hide()
    ov.open_overlay(None, None)                # the next open: one line, all of it on hover
    assert shown(t("shot_hint_no_game_short")) in shown(ov.shot_hint.text())
    assert "With the game open" in ov.shot_hint.toolTip()
    ov.shot, ov.game_hwnd, ov.shot_used = b"jpeg", 1, False    # a new state: whole, the first time
    ov._update_shot_hint()
    assert "taken as the chat opened" in ov.shot_hint.text()
    assert ov.shot_hint.toolTip() == ""
    ov.shot_used = True
    ov._update_shot_hint()
    assert "shot:now" in ov.shot_hint.text()   # the retake link stays in both forms
    ov.hide()
    ov.open_overlay(b"jpeg", 1)
    ov.shot_used = True
    ov._update_shot_hint()
    assert "shot:now" in ov.shot_hint.text() and "see the screen now" in ov.shot_hint.toolTip()


@pytest.mark.parametrize("lang", ["he", "en"])
def test_short_shot_hints_exist_in_both_languages(lang):
    t = I18n(lang)
    for k in ("shot_hint_no_game", "shot_hint_ready", "shot_hint_used"):
        assert t(k + "_short") != k + "_short" and len(t(k + "_short")) < len(t(k))


# ------------------------------------------------------------------ CHAT-08: the voice state outside the placeholder

def test_voice_state_shows_above_the_capsule_and_the_mic_says_stop(overlay):
    ov = overlay
    ov.input.setText("where is")               # the placeholder is hidden now
    ov.voice_state("listening")
    assert ov.voice_chip.isVisibleTo(ov) and "Listening" in shown(ov.voice_chip.text())
    assert ov.capsule.property("voice") == "true"
    assert ov.mic_btn.toolTip() == "Stop and send" and ov.mic_btn.accessibleName() == "Stop and send"
    ov.voice_state("transcribing")
    assert ov.voice_chip.isVisibleTo(ov) and "Transcribing" in shown(ov.voice_chip.text())
    assert ov.capsule.property("voice") == "false" and ov.mic_btn.toolTip() == I18n("en")("mic_tip")
    ov.voice_state("idle")
    assert not ov.voice_chip.isVisibleTo(ov)


# ------------------------------------------------------------------ CHAT-10: a multi-line question field

def test_the_field_grows_enter_sends_shift_enter_adds_a_line(overlay, fake_worker):
    ov = overlay
    one = ov.input.height()
    cap = ov.capsule.height()
    ov.input.setFocus()
    QTest.keyClicks(ov.input, "first line")
    QTest.keyClick(ov.input, Qt.Key_Return, Qt.ShiftModifier)
    QTest.keyClicks(ov.input, "second")
    assert ov.input.text() == "first line\nsecond"
    assert ov.input.height() > one and ov.capsule.height() > cap
    ov.input.setText("x " * 400)                # never taller than MAX_LINES
    assert ov.input.height() <= ov.input.MAX_LINES * ov.input.fontMetrics().lineSpacing() + 10
    ov.input.setText("first line\nsecond")
    QTest.keyClick(ov.input, Qt.Key_Return)
    assert fake_worker.made and fake_worker.made[-1].question == "first line\nsecond"
    assert ov.input.text() == "" and ov.input.height() == one


def test_paste_keeps_line_breaks(overlay):
    md = QMimeData()
    md.setText("Quest: Jr. Necki\r\nBring 50 skins\n")
    overlay.input.insertFromMimeData(md)
    assert overlay.input.text() == "Quest: Jr. Necki\nBring 50 skins"


def test_the_field_keeps_the_send_button_and_direction_rules(overlay):
    ov = overlay
    assert not ov.send_btn.isEnabled()
    ov.input.setText("איפה מאנו?")
    assert ov.send_btn.isEnabled()
    assert ov.input.alignment() & Qt.AlignRight and ov.input.layoutDirection() == Qt.RightToLeft
    ov.input.setText("where is Mano?")
    assert ov.input.alignment() & Qt.AlignLeft and ov.input.layoutDirection() == Qt.LeftToRight
    ov.input.clear()
    assert not ov.send_btn.isEnabled()


# ------------------------------------------------------------------ CHAT-11: the pin follows the pinned list

def test_pin_stays_usable_after_not_now_and_after_unpin(overlay):
    from maplehelper import pins
    ov = overlay
    cid = ov.profiles.active_id
    for i in range(pins.MAX_PINS):
        pins.add(ov.settings, cid, f"q{i}", f"a{i}")
    b = ov.add_bubble("the answer", "assistant")
    b.add_pin(lambda: ov.pin_answer("the question", "the answer", cid), "Pin")
    last_row = lambda: ov.feed_lay.itemAt(ov.feed_lay.count() - 1).widget()      # noqa: E731
    b.pin_btn.click()                          # the list is full: asked first
    assert b.pin_btn.isEnabled()
    last_row().chips[1].click()                # "Not now"
    assert b.pin_btn.isEnabled()
    b.pin_btn.click()
    last_row().chips[0].click()                # "Pin it"
    assert not b.pin_btn.isEnabled()
    ov._unpin("the answer")
    assert b.pin_btn.isEnabled()


# ------------------------------------------------------------------ CHAT-16 / CHAT-17

def test_card_tip_says_tag_and_the_field_names_the_tag(overlay):
    assert I18n("en")("card_ask_tip") == "Click to tag it for your next question"
    assert I18n("he")("card_ask_tip") == "לחצו כדי לתייג לשאלה הבאה"
    ov = overlay
    name = ov.kb.get("monster/100100")["name"]
    ov.set_tags(["monster/100100"])
    assert shown(ov.input._hint) == f"Ask about {name}…"
    ov.set_tags([])
    assert shown(ov.input._hint) == I18n("en")("input_placeholder")


def test_character_menu_says_why_it_is_grey_mid_answer(overlay, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from maplehelper.ui import widgets
    ov = overlay
    seen = {}

    def fake_exec(menu, *a):
        seen["notes"] = [shown(lb.text()) for lb in menu.findChildren(QLabel, "MenuNote")]
    monkeypatch.setattr(widgets.SplitMenu, "exec", fake_exec)
    ov.busy = True
    ov.character_menu()
    assert seen["notes"] == ["Available once the answer finishes"]
    ov.busy = False
    ov._menu_closed_at = 0
    ov.character_menu()
    assert seen["notes"] == []


# ------------------------------------------------------------------ PERF-08: the inventory check says it is on it

@pytest.mark.parametrize("ready", [True, False])
def test_inventory_check_shows_a_line_until_the_items_are_named(overlay, fake_worker, monkeypatch, ready):
    import threading

    from maplehelper import capture, inventory
    from maplehelper.ui import overlay as ov_mod
    ov = overlay
    go = threading.Event()

    def slow_read(full, cursor, kb):
        go.wait(5)
        return [], [], ""
    monkeypatch.setattr(ov_mod, "read_inventory", slow_read)
    monkeypatch.setattr(inventory, "index_ready", lambda kb: ready)

    def fake_shot():
        ov.shot, ov.shot_used = b"jpeg", False
        capture.LAST_FULL = object()
    monkeypatch.setattr(ov, "_fresh_shot", fake_shot)
    ov._capture_and_ask("check my bag", detail=True, shown="Inventory check")
    t = I18n("en")
    want = shown(t("inv_checking" if ready else "inv_checking_cold"))
    assert want in lines(ov)
    go.set()
    end = time.time() + 5
    while ov._reading_inventory and time.time() < end:
        pump(20)
    pump(20)
    assert want not in lines(ov) and fake_worker.made
