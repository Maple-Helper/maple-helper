"""The launch audit's chat-window fixes (OVL-*, VIS-21), offscreen: each test is the case the audit reproduced."""
import os
import sys
import time
import unicodedata
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402

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
    ov.hide()
    ov.bubble.hide()
    ov.deleteLater()


def pump(app, ms):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def last_row(ov):
    return ov.feed_lay.itemAt(ov.feed_lay.count() - 2).widget()


def shown(text: str) -> str:
    """The words, without the direction marks bidi.plain adds."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def wait_answer(ov):
    end = time.time() + 5
    while ov.busy and time.time() < end:
        pump(ov.app, 20)
    pump(ov.app, 50)


# ------------------------------------------------------------------ OVL-4: a tag the new KB dropped

def test_a_tag_the_updated_kb_dropped_does_not_crash_the_next_question(overlay):
    overlay.set_tags(["monster/100100"])
    old = overlay.kb

    class Updated:                                # the nightly KB without that monster
        def get(self, k):
            return None if k == "monster/100100" else old.get(k)

        def __getattr__(self, name):
            return getattr(old, name)
    overlay.kb = Updated()
    overlay.apply_language()                      # _render_tags indexed None
    assert overlay.ask("where is it?") is True    # ask() did too
    wait_answer(overlay)
    overlay.set_tags(["monster/100100"])          # App.reload_kb filters through set_tags
    assert overlay.focus_keys == []


# ------------------------------------------------------------------ OVL-5: no reference to a deleted thread

def test_a_finished_answers_thread_is_forgotten(overlay):
    assert overlay.ask("where is Mano?")
    wait_answer(overlay)
    pump(overlay.app, 100)
    assert overlay._thread is None               # isRunning() on it raised in App.shutdown


# ------------------------------------------------------------------ OVL-9 / 12: the busy line tells the truth

def test_a_question_during_a_screen_read_says_reading_not_answering(overlay):
    overlay._syncing = True
    overlay._say_busy()
    assert shown(last_row(overlay).text()) == overlay.t("busy_reading")
    overlay._syncing = False
    overlay.busy = True
    overlay._say_busy()
    assert shown(last_row(overlay).text()) == overlay.t("busy_wait")


def test_ask_while_busy_says_so_once(overlay):
    overlay.busy = True
    before = overlay.feed_lay.count()
    assert overlay.ask("again?") is False
    assert overlay.ask("again?") is False
    assert overlay.feed_lay.count() - before == 1        # the voice / tip / "Ask anyway" paths went silent
    overlay.voice_text("from the mic", send=True)
    assert overlay.feed_lay.count() - before == 1


# ------------------------------------------------------------------ OVL-10: ⟳ during the quiet minute read

def test_refresh_during_the_quiet_read_takes_it_over(overlay):
    overlay._syncing, overlay._sync_auto = True, True
    overlay.sync_profile()
    assert overlay._sync_auto is False                   # its result is now reported like a ⟳ read
    overlay._syncing, overlay._sync_auto = False, False


# ------------------------------------------------------------------ OVL-11: the read's error says which

def test_a_refresh_error_names_the_problem(overlay):
    overlay._syncing, overlay._sync_cid = True, overlay.profiles.active_id
    overlay._on_sync_done(Answer(error="not_logged_in"))
    t = overlay.t
    assert shown(last_row(overlay).text()) == t.p("err_not_logged_in", overlay.settings["provider"])
    assert overlay._error_key("timeout") == "err_timeout" and overlay._error_key("api_error") == "err_generic"
    assert overlay._error_key("usage_limit") == "err_usage_limit"


# ------------------------------------------------------------------ OVL-13: a portrait that can't be saved

def test_a_failing_portrait_save_still_ends_the_read(overlay, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(overlay.profiles, "set_avatar", boom)
    got = []
    overlay._on_avatar_cropped((overlay.profiles.active_id, b"png", got.append))
    assert got == [False]


# ------------------------------------------------------------------ OVL-14: a closed update bar stays closed

def test_a_dismissed_update_bar_stays_closed_after_a_language_switch(overlay):
    overlay.show_update("9.9.9", "available")
    assert overlay.update_bar.isVisible()
    overlay.update_close.click()
    overlay.settings["language"] = "en" if overlay.t.rtl else "he"
    overlay.apply_language()
    assert not overlay.update_bar.isVisible()
    overlay.show_update("9.9.9", "failed")            # a new state still shows it
    assert overlay.update_bar.isVisible()


# ------------------------------------------------------------------ OVL-15: F9 while stepping aside

def test_the_chat_stepping_aside_for_a_shot_is_still_open(overlay):
    overlay._step_aside(True)
    assert overlay.is_open() and overlay.windowOpacity() == 0.0
    overlay._step_aside(False)
    assert overlay.is_open() and overlay.windowOpacity() == 1.0


# ------------------------------------------------------------------ OVL-16: an overrunning maintenance

def test_maintenance_past_its_end_time_promises_no_time(overlay):
    from maplehelper.serverstatus import Status
    now = time.time()
    overlay._server = None
    overlay._on_server_status(Status("maintenance", notice_end=now - 3600, checked=now))
    t = overlay.t
    assert shown(last_row(overlay).text()) == t("server_maint_started")


# ------------------------------------------------------------------ OVL-18: a failed answer leaves no question

def test_a_failed_answer_takes_its_question_out_of_the_history(overlay):
    from maplehelper.store import History
    h = History(overlay.profiles.active_id)
    overlay.brain.ask.return_value = Answer(error="offline")
    assert overlay.ask("where is Mano?")
    wait_answer(overlay)
    assert [r["text"] for r in h.recent()] == []
    overlay.brain.ask.return_value = Answer(text="At the bridge.")
    assert overlay.ask("where is Mano?")
    wait_answer(overlay)
    assert [r["role"] for r in h.recent()] == ["user", "assistant"]


def test_drop_last_if_user_only_takes_back_that_question(isolated_store):
    h = isolated_store.History("c-drop")
    h.append("user", "q1")
    h.append("assistant", "a1")
    assert h.drop_last_if_user("q1") is False          # answered: stays
    h.append("user", "q2")
    assert h.drop_last_if_user("other") is False
    assert h.drop_last_if_user("q2") is True
    assert [r["text"] for r in h.recent()] == ["q1", "a1"]


# ------------------------------------------------------------------ OVL-19: one settings write per drag

def test_a_drag_resize_saves_the_geometry_once(overlay, monkeypatch):
    calls = []
    monkeypatch.setattr(overlay, "save_geometry", lambda: calls.append(1))
    for i in range(20):
        overlay.resize(520 + i, 680)
        overlay.app.processEvents()
    pump(overlay.app, 450)
    assert len(calls) == 1


# ------------------------------------------------------------------ OVL-20: the mini bubble and a right-click

def test_the_mini_bubble_ignores_a_right_click():
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui.minibubble import MiniBubble
    QApplication.instance() or QApplication([])
    b = MiniBubble()
    got = []
    b.clicked.connect(lambda: got.append(1))

    def ev(kind, button):
        p = QPointF(20, 20)
        return QMouseEvent(kind, p, p, button, button, Qt.NoModifier)
    b.mousePressEvent(ev(QEvent.MouseButtonPress, Qt.RightButton))
    b.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, Qt.RightButton))
    assert got == []
    b.mousePressEvent(ev(QEvent.MouseButtonPress, Qt.LeftButton))
    b.mouseReleaseEvent(ev(QEvent.MouseButtonRelease, Qt.LeftButton))
    assert got == [1]
    assert b._icon.width() >= 256                       # OVL-26: the sharp source, not the 64 px one


# ------------------------------------------------------------------ OVL-21: redrawn on a language / AI switch

def test_ask_ai_anyway_names_the_current_ai_after_a_switch(overlay):
    from PySide6.QtWidgets import QPushButton
    qa = Mock(text="Mano: Level 20.", entities=[], drop_groups=[], sources=())
    overlay._show_quick(qa, "mano level", None)
    overlay.settings["provider"] = "codex"
    overlay.apply_language()
    links = [b for b in overlay.findChildren(QPushButton) if b.objectName() == "Link"]
    link = next(b for b in links if "ChatGPT" in b.text() or "Claude" in b.text())
    assert "ChatGPT" in link.text()        # it would ask ChatGPT, and said "Ask Claude anyway"


def test_thinking_follows_a_language_switch(overlay):
    overlay.busy = True
    overlay._pending_bubble = overlay.add_bubble(overlay.t("thinking"), "assistant")
    overlay.settings["language"] = "en" if overlay.t.rtl else "he"
    overlay.apply_language()
    assert overlay._pending_bubble._text == overlay.t("thinking")
    overlay.busy = False


# ------------------------------------------------------------------ OVL-22: the pin goes to the asking character

def test_a_pin_after_switching_character_goes_to_the_one_asked_for(overlay):
    from PySide6.QtWidgets import QToolButton
    from maplehelper import pins
    first = overlay.profiles.active_id
    assert overlay.ask("where is Mano?")
    wait_answer(overlay)
    other = overlay.profiles.add("Kalimero", "Warrior", "Fighter", 30)
    overlay.profiles.set_active(other.id)
    pin = [b for b in overlay._pending_bubble.findChildren(QToolButton)][-1]
    pin.click()
    assert len(pins.items(overlay.settings, first)) == 1 and pins.items(overlay.settings, other.id) == []


# ------------------------------------------------------------------ OVL-6: a long link can't widen the chat

def test_a_long_notice_link_shrinks_instead_of_widening_the_chat():
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui.widgets import NoticeCard
    QApplication.instance() or QApplication([])
    long = "This is the same character (update the name) " * 2
    card = NoticeCard("Another character is in the game.", long.strip(), False, action2="Add as a new character")
    card.resize(300, 200)
    card.show()
    QApplication.processEvents()
    assert card.minimumSizeHint().width() <= 300
    assert card.btn.text() == long.strip() and card.btn.toolTip() == long.strip()   # elided, all of it in the tip
    card.close()


# ------------------------------------------------------------------ OVL-7: a long word wraps inside the bubble

def test_a_long_unbroken_word_can_wrap():
    from maplehelper.ui.widgets import ZWSP, soft_breaks
    url = "https://meowdb.com/items/" + "x" * 80
    out = soft_breaks(f"ראו {url} שם")
    assert out.replace(ZWSP, "") == f"ראו {url} שם" and ZWSP in out
    assert soft_breaks("**" + "a" * 40 + "**").count("**") == 2           # the bold markup stays whole
    assert soft_breaks("short words only") == "short words only"


# ------------------------------------------------------------------ OVL-8: the tour card fits its text

@pytest.mark.skipif(sys.platform != "win32", reason="measured with the Windows fonts")
def test_the_tour_card_is_tall_enough_at_the_large_font(isolated_store, kb):
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui import theme
    from maplehelper.ui.overlay import Overlay
    QApplication.instance() or QApplication([])
    s = isolated_store.Settings()
    s["language"] = "en"
    ov = Overlay(s, isolated_store.Profiles(), kb, None)
    ov.setStyleSheet(theme.stylesheet(theme.load_fonts(), 16))
    ov.setGeometry(QRect(-3000, -3000, 600, 760))
    ov.show()
    ov.start_tour()
    tr = ov._tour
    for _ in range(len(tr.steps)):
        QApplication.processEvents()
        body = tr.body
        if body.isVisible():
            assert body.height() >= body.heightForWidth(body.width()), tr.steps[tr.i][1]
        tr.go(tr.i + 1)
        if ov._tour is None:
            break
    ov.hide()


# ------------------------------------------------------------------ VIS-21: a press dragged off a card

def test_releasing_off_a_card_does_not_tag_it(kb):
    from PySide6.QtWidgets import QApplication

    from maplehelper.ui.widgets import SELECTION, EntityCard
    QApplication.instance() or QApplication([])
    card = EntityCard(kb, "monster/100100", "en")
    card.resize(300, 120)
    got = []
    SELECTION.picked.connect(got.append)
    try:
        def release(x, y):
            p = QPointF(x, y)
            return QMouseEvent(QEvent.MouseButtonRelease, p, p, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        card.mouseReleaseEvent(release(500, 500))
        assert got == []
        card.mouseReleaseEvent(release(10, 10))
        assert got == ["monster/100100"]
    finally:
        SELECTION.picked.disconnect(got.append)
