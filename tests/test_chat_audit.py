"""The chat window's audit fixes (offscreen Qt): races while an answer streams, the character menu and cards from
the keyboard, live language switches, the feed's cost in a long chat, windows reopened in a new look, toasts."""
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QRect, Qt, Signal  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper.brain import Answer  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms=50):
    from PySide6.QtCore import QEvent
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.DeferredDelete)       # deleteLater() outside a running event loop
        time.sleep(0.005)


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
    s["language"] = "he"
    s["tour_done"] = True
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 460, 700))
    ov.show()
    yield ov
    from PySide6.QtCore import QThread
    for th in ov.findChildren(QThread):      # a fake answer that never came: its thread ends before the window
        th.quit()
        th.wait(2000)
    ov.hide()
    ov.bubble.hide()
    ov.deleteLater()


class FakeWorker(QObject):
    """Stands in for AskWorker: records what the question was sent with, answers when told to."""
    delta = Signal(str)
    done = Signal(object)
    made: list = []

    def __init__(self, brain, question, character, history, shot, focus=None, extra=None, model=None, light=False):
        super().__init__()
        self.question, self.extra, self.shot = question, extra, shot
        FakeWorker.made.append(self)

    def run(self):
        pass


@pytest.fixture
def fake_worker(monkeypatch):
    from maplehelper.ui import overlay as ov_mod
    FakeWorker.made = []
    monkeypatch.setattr(ov_mod, "AskWorker", FakeWorker)
    return FakeWorker


# ------------------------------------------------------------------ races while an answer is on its way

def test_continue_from_another_characters_history_switches_to_it_first():
    from maplehelper.app import MapleHelperApp
    calls = []
    profiles = SimpleNamespace(active=SimpleNamespace(id="B"))

    def switch(cid):
        calls.append(("switch", cid))
        profiles.active = SimpleNamespace(id=cid)
    ov = SimpleNamespace(isVisible=lambda: True, is_open=lambda: True, _is_busy=lambda: False, switch_character=switch,
                         continue_from=lambda *a: calls.append("continue"))
    fake = SimpleNamespace(overlay=ov, profiles=profiles,
                           _windows={"history:A": SimpleNamespace(close=lambda: calls.append("closed A"))})
    MapleHelperApp.continue_conversation(fake, "q", "a", [], "A")
    assert calls == [("switch", "A"), "closed A", "continue"]
    # mid-answer the reply still belongs to the current character: nothing moves
    calls.clear()
    profiles.active = SimpleNamespace(id="B")
    ov._is_busy, ov._say_busy = (lambda: True), (lambda: calls.append("busy"))
    MapleHelperApp.continue_conversation(fake, "q", "a", [], "A")
    assert calls == ["busy"]


def test_ask_ai_anyway_keeps_the_context_an_instant_answer_used_up(overlay, fake_worker, monkeypatch):
    from maplehelper import quick
    ov = overlay
    ov._hidden_context = "<continuing>old exchange</continuing>"
    monkeypatch.setattr(quick, "answer", lambda *a, **k: SimpleNamespace(text="Mano has 20000 HP", entities=[],
                                                                        drop_groups=[]))
    assert ov.ask("mano hp") and not fake_worker.made           # answered instantly
    assert ov._hidden_context is None
    from PySide6.QtWidgets import QPushButton
    again = [b for b in ov.feed.findChildren(QPushButton) if b.objectName() == "Link"][-1]
    again.click()
    assert fake_worker.made and fake_worker.made[-1].extra == "<continuing>old exchange</continuing>"


def test_every_character_who_talked_gets_a_session_summary(overlay, isolated_store):
    ov = overlay
    p = ov.profiles
    a = p.active
    b = p.add("Kiwi", "Magician", "Cleric", 40)
    ov._session_started = time.time() - 5
    isolated_store.History(a.id).append("user", "where is Pio?")
    isolated_store.History(b.id).append("user", "best cleric map?")
    p.set_active(b.id)
    out = ov.end_session()
    assert set(out) == {a.id, b.id} and "Pio" in out[a.id] and "cleric" in out[b.id]
    assert ov.end_session() == {}


def test_a_refresh_during_the_inventory_check_drops_its_context_and_says_so(overlay, fake_worker):
    ov = overlay
    ov._syncing = True                    # a ⟳ read started while the icons were being matched
    ov._reading_inventory = True
    ov.send_btn.setEnabled(False)
    ov._on_inventory_read(("check my inventory", "Inventory check", ov.profiles.active_id, ["TILE"], [],
                           "slot 1: Red Potion"))
    assert not fake_worker.made
    assert ov._hidden_context is None and ov._detail_tiles is None
    from maplehelper.ui.widgets import SystemLine
    assert any(I18n("he")("busy_wait") in w.text() for w in ov.feed.findChildren(SystemLine))


def test_menu_and_refresh_count_the_inventory_check_as_busy(overlay):
    ov = overlay
    ov._reading_inventory = True
    finished = []
    ov.sync_finished.connect(finished.append)
    ov.sync_profile()
    assert not getattr(ov, "_syncing", False) and finished == [False]


def test_a_refresh_refused_mid_answer_still_ends_for_the_exp_meter(overlay):
    ov = overlay
    ov.busy = True
    finished = []
    ov.sync_finished.connect(finished.append)
    ov.sync_profile()
    assert finished == [False]            # the tools window drops its pending reading instead of waiting forever
    ov.busy, ov._syncing = False, True    # a read already running answers for itself
    ov.sync_profile()
    assert finished == [False]


def test_clearing_the_history_mid_answer_keeps_the_answer_out_of_it(overlay, fake_worker, isolated_store):
    ov = overlay
    ov.settings["instant_answers"] = False
    assert ov.ask("how do I get to Ellinia?")
    h = isolated_store.History(ov.profiles.active_id)
    h.clear()
    ov.clear_feed()
    fake_worker.made[-1].done.emit(Answer(text="Take the boat."))
    pump(20)
    assert h.recent(10) == []


def test_old_notices_and_choices_wait_while_an_answer_streams(overlay):
    ov = overlay
    other = ov.profiles.add("Hero1", "Warrior", "Fighter", 50)
    first = ov.profiles.characters[0]
    ov.profiles.set_active(first.id)
    ans = Answer(text="x", profile_update={"name": "Hero1", "level": 51})
    assert ov._offer_other_character(ans, None, None)
    from maplehelper.ui.widgets import NoticeCard
    card = ov.feed.findChildren(NoticeCard)[-1]
    ov.busy = True
    card.btn.click()
    assert ov.profiles.active_id == first.id          # not switched mid-answer
    ov.busy = False
    card.btn.click()
    assert ov.profiles.active_id == other.id          # and still works once the answer is in
    # the "update / add as new" choices keep their row when refused
    ov._apply_profile_update({"job": "Cleric", "level": 20})
    rows = [w for w in ov.feed.findChildren(QObject) if hasattr(w, "chips")]
    ov.busy = True
    rows[-1].chips[0].click()
    assert rows[-1].isEnabled()
    ov.busy = False
    rows[-1].chips[2].click()             # cancel closes it
    assert not rows[-1].isEnabled()


def test_voice_indicator_stays_on_while_a_new_recording_runs():
    import numpy as np

    from maplehelper import voice
    vc = voice.VoiceController()
    states = []
    vc.state.connect(states.append)
    vc.transcriber = SimpleNamespace(transcribe=lambda a: "hi", downloaded=lambda: True)
    vc._stream = object()                 # the follow-up is recording while the first clip finishes
    vc._run(np.zeros(16000, dtype=np.float32))
    assert "idle" not in states
    vc._stream = None
    vc._run(np.zeros(16000, dtype=np.float32))
    assert states[-1] == "idle"


def test_voice_download_failure_says_it_needs_the_internet():
    import numpy as np

    from maplehelper import voice
    vc = voice.VoiceController()
    failed = []
    vc.failed.connect(failed.append)

    def offline(_audio):
        raise OSError("cannot find the appropriate snapshot folder")
    vc.transcriber = SimpleNamespace(transcribe=offline, downloaded=lambda: False)
    vc._run(np.zeros(16000, dtype=np.float32))
    assert failed[-1].startswith("download:")
    assert "internet" in I18n("en")("voice_download_failed")


# ------------------------------------------------------------------ character menu and cards from the keyboard

def test_menu_rows_run_from_the_keyboard_and_light_up():
    from maplehelper.ui.widgets import SplitMenu
    menu = SplitMenu()
    hits = []
    menu.add_row("add", "Add character", lambda: hits.append("add"))
    menu.add_row("edit", "Edit", lambda: hits.append("edit"), enabled=False)
    a, b = menu.actions()
    assert not b.isEnabled()
    a.trigger()                                # what Enter on the row does
    assert hits == ["add"]
    menu.hovered.emit(a)
    row = a.defaultWidget().findChild(QObject, "MenuRow")
    assert row.property("active") == "true"
    menu.hovered.emit(b)
    assert row.property("active") == "false"


def test_character_card_opens_the_menu_from_the_keyboard(overlay, monkeypatch):
    from maplehelper.ui.widgets import SplitMenu
    ov = overlay
    assert ov.profile_card.focusPolicy() & Qt.TabFocus
    assert ov.profile_card in ov.tab_order()
    opened = []
    monkeypatch.setattr(SplitMenu, "exec", lambda self, *a: opened.append(self))
    ov.profile_card.setFocus(Qt.TabFocusReason)
    QTest.keyClick(ov.profile_card, Qt.Key_Return)
    assert opened
    pump(20)
    # and the menu doesn't stay behind under the chat each time it opens
    ov._menu_closed_at = 0
    ov.character_menu()
    ov._menu_closed_at = 0
    ov.character_menu()
    pump(20)
    assert len(ov.findChildren(SplitMenu)) == 0


def test_cards_tag_from_the_keyboard(kb):
    from maplehelper.ui.widgets import SELECTION, EntityCard
    card = EntityCard(kb, "monster/100100", "en")
    picked = []
    SELECTION.picked.connect(picked.append)
    try:
        assert card.focusPolicy() & Qt.TabFocus and card.accessibleName() == "Snail"
        QTest.keyClick(card, Qt.Key_Space)
        assert picked == ["monster/100100"]
    finally:
        SELECTION.picked.disconnect(picked.append)


def test_tab_stays_inside_the_tour(overlay):
    ov = overlay
    ov.start_tour()
    tour = ov._tour
    tour.go(2)
    buttons = [b for b in (tour.skip_btn, tour.back_btn, tour.next_btn) if b.isVisible()]
    tour.next_btn.setFocus()
    for _ in range(4):
        QTest.keyClick(ov.focusWidget() or tour.next_btn, Qt.Key_Tab)
        assert ov.focusWidget() in buttons
    tour.finish()


def test_icon_buttons_have_names_for_screen_readers(overlay):
    ov = overlay
    for b in (ov.history_btn, ov.tools_btn, ov.guides_btn, ov.wish_btn, ov.settings_btn, ov.min_btn, ov.close_btn,
              ov.mic_btn, ov.recapture_btn, ov.send_btn, ov.profile_card.refresh):
        name = b.accessibleName()
        assert name and not any(0xE000 <= ord(ch) <= 0xF8FF for ch in name), b.objectName()


# ------------------------------------------------------------------ language switch, copy, bullets, gutter

def test_language_switch_redraws_the_cards(overlay):
    from maplehelper.ui.widgets import EntityCard, TileGrid
    ov = overlay
    ov.add_cards(["monster/100100", "monster/100101", "monster/130101", "item/2000000"])
    ov.settings["language"] = "en"
    app.setLayoutDirection(Qt.LeftToRight)
    ov.apply_language()
    pump(20)
    cards = [c for c in ov.feed.findChildren(EntityCard) if c.isVisible()]
    assert cards and all(c._t.lang == "en" and c.layoutDirection() == Qt.LeftToRight for c in cards)
    grids = [g for g in ov.feed.findChildren(TileGrid) if g.isVisible()]
    assert grids and all(g.layoutDirection() == Qt.LeftToRight for g in grids)
    assert ov.feed_lay.contentsMargins().right() == 6 and ov.feed_lay.contentsMargins().left() == 0


def test_hebrew_feed_keeps_its_gap_on_the_scrollbar_side(overlay):
    m = overlay.feed_lay.contentsMargins()
    assert (m.left(), m.right()) == (6, 0)
    from maplehelper.ui.patchnotes import gutter
    assert gutter(True) == (6, 0, 0, 0) and gutter(False) == (0, 0, 6, 0)


def test_copying_a_tagged_card_keeps_its_tag_border(kb):
    from maplehelper.ui.widgets import EntityCard
    card = EntityCard(kb, "monster/100100", "en")
    card._on_selection(["monster/100100"])
    card.copy_image()
    assert card.property("selected") == "true"


def test_session_card_bullets_lead_in_an_english_card():
    from maplehelper import bidi
    from maplehelper.ui.widgets import SessionCard
    q = bidi.name_block("כמה EXP צריך?", False)
    card = SessionCard("Last session", [{"name": "Kiwi", "lines": [], "questions": [q]}], False)
    line = [lb.text() for lb in card.findChildren(QObject) if getattr(lb, "objectName", lambda: "")() == "CardSub"]
    assert line and line[0].startswith("‎• ")
    he = SessionCard("x", [{"name": "Kiwi", "lines": [], "questions": ["Where is Pio?"]}], True)
    line = [lb.text() for lb in he.findChildren(QObject) if getattr(lb, "objectName", lambda: "")() == "CardSub"]
    assert line[0].startswith(bidi.RLM + "• ")


def test_item_cards_show_the_kbs_own_stats():
    from maplehelper.ui.widgets import EntityCard
    en = I18n("en")
    top = {"category": "item", "props": {"Level Requirement": 50, "LUK": 3, "Weapon Defense": 56, "MP": 20,
                                         "Upgrade Slots": 7}}
    text = EntityCard._stats(top, en).replace(" ", " ")     # (a value never wraps away from its label)
    assert "Required level: 50" in text and "Weapon Defense: 56" in text and "LUK +3" in text
    mail = {"category": "item", "props": {"Level Requirement": 25, "HP": 5}}
    assert "HP +5" in EntityCard._stats(mail, en)
    snail = {"category": "monster", "props": {"Level": 1, "HP": 8, "EXP": 3}}
    assert EntityCard._stats(snail, en).count(":") == 3


# ------------------------------------------------------------------ a long chat stays quick

def test_streamed_pieces_are_drawn_at_most_every_few_frames(overlay, monkeypatch):
    ov = overlay
    b = ov.add_bubble("", "assistant")
    ov._pending_bubble = b
    drawn = []
    monkeypatch.setattr(b, "set_text", drawn.append)
    for i in range(1, 30):
        ov._on_delta("x" * i)
    assert drawn == []
    pump(ov.DELTA_MS + 80)
    assert drawn == ["x" * 29]
    ov._on_delta("late piece")
    ov._on_done(Answer(text="final"), None)
    pump(ov.DELTA_MS + 80)
    assert drawn[-1] == "final"               # a queued piece never lands over the finished answer


def test_the_feed_keeps_only_its_newest_rows(overlay, monkeypatch):
    ov = overlay
    monkeypatch.setattr(type(ov), "FEED_MAX", 10)
    for i in range(25):
        ov.add_system(f"line {i}")
    assert ov.feed_lay.count() - 1 == 10


def test_open_close_leaves_no_animations_behind(overlay):
    from PySide6.QtCore import QParallelAnimationGroup
    ov = overlay
    for _ in range(5):
        ov._materialize(True)
        ov._materialize(False)
    pump(20)
    assert len(ov.findChildren(QParallelAnimationGroup)) <= 1


def test_saved_position_mostly_off_screen_comes_back_whole():
    from maplehelper.ui.overlay import Overlay
    screens = [QRect(0, 0, 2752, 1104)]
    spot = Overlay.on_screen(QRect(-410, 100, 420, 640), screens)
    assert spot == QRect(0, 100, 420, 640)
    tall = Overlay.on_screen(QRect(100, 0, 420, 1600), screens)
    assert tall.height() == 1104 and screens[0].contains(tall)
    assert Overlay.on_screen(QRect(-5000, 0, 420, 640), screens) is None


# ------------------------------------------------------------------ app: windows, tour, tray, second launch

def test_the_tour_starts_the_first_time_the_chat_opens(overlay, monkeypatch):
    ov = overlay
    ov.settings["tour_done"] = False
    started = []
    monkeypatch.setattr(ov, "start_tour", lambda: started.append(True))
    ov.hide()
    ov.open_overlay(None, None)
    pump(800)
    assert started


def test_whats_new_waits_for_the_tour(monkeypatch):
    from maplehelper import app as app_mod, whatsnew
    shown = []
    ov = SimpleNamespace(add_notice=lambda *a: None, tour_ended=_Sig())
    fake = SimpleNamespace(settings={"seen_version": "0.7.8", "tour_done": False}, overlay=ov,
                           show_whats_new=lambda notes: shown.append(notes))
    monkeypatch.setattr(whatsnew, "since", lambda a, b: [{"version": "0.8.1"}])
    monkeypatch.setattr(app_mod.sys, "argv", ["x", app_mod.UPDATED_ARG])
    monkeypatch.setattr(app_mod.telemetry, "track", lambda *a, **k: None)
    app_mod.MapleHelperApp.announce_whats_new(fake, False)
    pump(1000)
    assert shown == []
    ov.tour_ended.emit()
    assert shown == [[{"version": "0.8.1"}]]
    ov.tour_ended.emit()
    assert len(shown) == 1


class _Sig(QObject):
    sig = Signal()

    def connect(self, fn, kind=None):
        return self.sig.connect(fn, kind) if kind is not None else self.sig.connect(fn)

    def emit(self):
        self.sig.emit()


def test_tray_open_never_closes_an_open_chat():
    from maplehelper.app import MapleHelperApp
    calls = []
    ov = SimpleNamespace(is_open=lambda: True, toggle=lambda c: calls.append("toggle"),
                         raise_=lambda: calls.append("raise"), activateWindow=lambda: calls.append("activate"))
    MapleHelperApp.show_chat(SimpleNamespace(overlay=ov, capture=None))
    assert calls == ["raise", "activate"]


def test_second_launch_during_onboarding_brings_it_forward(monkeypatch):
    from maplehelper.app import MapleHelperApp
    raised = []
    win = SimpleNamespace(raise_=lambda: raised.append("raise"), activateWindow=lambda: raised.append("activate"))
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: win))
    server = SimpleNamespace(hasPendingConnections=lambda: False)
    fake = SimpleNamespace(_instance_server=server, bring_dialogs_forward=lambda: None)
    MapleHelperApp._on_second_launch(fake)
    assert raised == ["raise", "activate"]


def test_windows_reopen_where_the_player_was():
    from maplehelper.app import MapleHelperApp
    calls = []
    chars = [SimpleNamespace(id="A"), SimpleNamespace(id="B")]
    fake = SimpleNamespace(
        profiles=SimpleNamespace(active=chars[1], characters=chars),
        show_tools=lambda page: calls.append(("tools", page)), show_guides=lambda key: calls.append(("guides", key)),
        show_patch_notes=lambda e, tab: calls.append(("notes", e, tab)), show_whats_new=lambda n: calls.append(("new", n)),
        show_history=lambda cid: calls.append(("history", cid)), show_wishlist=lambda cid: calls.append(("wish", cid)))
    fake._character = lambda cid: MapleHelperApp._character(fake, cid)
    reopen = lambda kind, dlg: MapleHelperApp._reopen_call(fake, kind, dlg)  # noqa: E731
    reopen("tools", SimpleNamespace(stack=SimpleNamespace(currentIndex=lambda: 6)))()
    reopen("guides", SimpleNamespace(_reading="guide/x"))()
    reopen("patch_notes", SimpleNamespace(entries=["e"], tab="news"))()     # on the tab it was on
    reopen("whats_new", SimpleNamespace(notes=["n"]))()
    reopen("history:A", None)()
    reopen("wishlist:A", None)()
    assert reopen("history:gone", None) is None
    assert calls == [("tools", "build"), ("guides", "guide/x"), ("notes", ["e"], "news"), ("new", ["n"]),
                     ("history", "A"), ("wish", "A")]


# ------------------------------------------------------------------ toasts and patch notes

def test_toasts_reuse_free_room_and_stay_on_screen(monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from maplehelper.ui import toast
    area = QRect(0, 0, 1366, 728)
    monkeypatch.setattr(toast.Toast, "_live", [])
    fake_screen = SimpleNamespace(availableGeometry=lambda: area)
    monkeypatch.setattr(QGuiApplication, "primaryScreen", staticmethod(lambda: fake_screen))
    made = [toast.notify(f"t{i}", "body", rtl=False, timeout_ms=60000) for i in range(8)]
    pump(300)
    live = [t for t in toast.Toast._live]
    assert live and all(area.top() - toast.SHADOW <= t._slot.top() for t in live)
    lowest = max(t._slot.bottom() for t in live)
    # the bottom one goes: the next toast takes its place instead of climbing past the top one
    bottom = next(t for t in live if t._slot.bottom() == lowest)
    bottom.dismiss()
    new = toast.notify("again", "body", rtl=False, timeout_ms=60000)
    assert new._slot.bottom() == lowest
    assert len(toast.Toast._live) < 9          # the oldest made way instead of going off the top
    from shiboken6 import isValid
    for t in made + [new]:
        if isValid(t):
            t.close()


def test_toast_follows_a_theme_switch():
    from maplehelper.ui import theme, toast
    old = theme.MODE
    try:
        theme.set_mode("dark")
        t = toast.Toast("t", "b", False, "Segoe UI")
        theme.set_mode("light")
        t.restyle()
        assert theme.P()["text"] in t.styleSheet()
        t.close()
    finally:
        theme.set_mode(old)


def test_patch_notes_count_each_page_once_over_several_updates():
    from maplehelper.ui.patchnotes import summary, totals
    entries = [
        {"version": "3", "counts": {"added": 1, "updated": 2},
         "added": [{"key": "guide/new"}], "updated": [{"key": "guide/a"}, {"key": "guide/b"}]},
        {"version": "2", "counts": {"updated": 2}, "updated": [{"key": "guide/a"}, {"key": "guide/c"}]},
        {"version": "1", "counts": {"added": 1, "updated": 1}, "added": [{"key": "guide/x"}],
         "updated": [{"key": "guide/b"}]},
    ]
    assert totals(entries) == {"added": 2, "changed": 0, "updated": 3, "removed": 0}
    assert summary(I18n("en"), entries).startswith("2 new")
    gone = [{"version": "1", "counts": {"added": 1}, "added": [{"key": "k"}]},
            {"version": "2", "counts": {"removed": 1}, "removed": [{"key": "k"}]}]
    assert sum(totals(gone).values()) == 0


def test_an_api_key_error_names_the_key_not_a_sign_in(overlay):
    """On an API key there's nothing to sign in to: "sign in to Claude again" / "your Claude plan" were wrong
    (audit PRV-20)."""
    ov = overlay
    shown = []
    for mode in (False, True):
        ov.settings.set_api_key_mode(ov.settings["provider"], mode)
        for err in ("not_logged_in", "usage_limit", "cli_outdated"):
            ov._pending_bubble = b = ov.add_bubble("", "assistant")
            b.set_text = shown.append
            ov._on_done(Answer(error=err), None)
    provider = ov.settings["provider"]
    assert shown[:3] == [ov.t.p("err_not_logged_in", provider), ov.t.p("err_usage_limit", provider),
                         ov.t.p("err_cli_outdated", provider)]
    assert shown[3:] == [ov.t("err_not_logged_in_key"), ov.t("err_usage_limit_key"), ov.t.p("err_cli_outdated", provider)]
