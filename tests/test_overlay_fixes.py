"""The chat window's layout and state fixes (offscreen Qt): tags, pins, open/close, captures, scrolling."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QTextDocumentFragment  # noqa: E402

from maplehelper.brain import META, streamed_text  # noqa: E402
from maplehelper.i18n import STRINGS, I18n  # noqa: E402

NBSP = chr(0xA0)


# ------------------------------------------------------------------ pure helpers

def test_streaming_never_shows_a_half_arrived_marker():
    assert streamed_text("Mano drops a shell.\n@@ME") == "Mano drops a shell."
    assert streamed_text("Mano drops a shell.\n@") == "Mano drops a shell."
    assert streamed_text(f"Mano drops a shell.\n{META}\n{{\"entities\"") == "Mano drops a shell."
    assert streamed_text("email me @ home") == "email me @ home"


def test_singular_forms():
    he, en = I18n("he"), I18n("en")
    assert he("history_count", n=1) == "תוצאה אחת" and en("history_count", n=1) == "1 result"
    assert en("history_count", n=3) == "3 results"
    assert "1" not in he("tip_job_soon", n=1, jobs="Hermit") and en("sess_questions", n=1) == "1 question"


@pytest.mark.parametrize("key", sorted(k for k in STRINGS if k.endswith("_one")))
def test_singular_variants_have_a_base_string(key):
    assert key[:-4] in STRINGS


def test_stat_changes_in_words():
    from maplehelper.ui.overlay import stats_text
    assert stats_text(I18n("en"), "acc 55, dmg_min 30, dmg_max 80").replace(NBSP, " ") == \
        "Accuracy (ACC) 55, Min damage 30, Max damage 80"
    assert "dmg_min" not in stats_text(I18n("he"), "dmg_min 30")


def test_saved_spot_is_moved_onto_a_screen():
    from maplehelper.ui.overlay import visible_rect
    screens = [QRect(0, 0, 1920, 1080)]
    assert visible_rect(QRect(-20000, 50, 72, 72), screens) is None          # that monitor is gone
    assert visible_rect(QRect(1900, 1070, 72, 72), screens) == QRect(1848, 1008, 72, 72)
    assert visible_rect(QRect(100, 100, 72, 72), screens) == QRect(100, 100, 72, 72)


def test_card_subtitle_says_the_category_once():
    from maplehelper.ui.widgets import card_subtitle
    assert card_subtitle(I18n("he"), "monster", "Monster") == "מפלצת"
    assert card_subtitle(I18n("en"), "item", "Etc / Monster Drop") == "Item · Etc / Monster Drop"
    assert card_subtitle(I18n("he"), "crafting", None) == "קראפטינג"


# ------------------------------------------------------------------ the window

@pytest.fixture
def overlay(isolated_store, kb, monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from maplehelper import osapi
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    from maplehelper.ui import terms
    monkeypatch.setattr(terms, "LANG", terms.LANG)       # the chat's language must not leak into other tests
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 460, 640))
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


def test_tagging_cards_does_not_widen_the_window(overlay):
    w = overlay.width()
    overlay.set_tags(["monster/100100", "monster/100101", "monster/130101", "map/100000000", "npc/1012100"])
    pump(overlay.app, 100)
    assert overlay.minimumSizeHint().width() <= w and overlay.width() == w


def test_quick_open_close_keeps_the_window_size(overlay):
    overlay.save_geometry()                 # a remembered window, so opening doesn't place a default one
    overlay.hide()
    start = overlay.geometry()
    for _ in range(3):
        overlay.open_overlay(None, None)
        pump(overlay.app, 50)              # closed again mid-animation (F9 double-tap)
        overlay.close_overlay()
        pump(overlay.app, 300)
    assert overlay.geometry().size() == start.size()
    assert (overlay.settings["window"]["w"], overlay.settings["window"]["h"]) == (start.width(), start.height())


def test_the_chat_is_a_normal_window_so_discord_can_share_it(overlay):
    import sys
    from PySide6.QtCore import Qt
    kind = overlay.windowFlags() & Qt.WindowType_Mask
    # Discord, OBS and Alt+Tab skip tool windows (WS_EX_TOOLWINDOW): the chat couldn't be picked for a stream
    assert kind == (Qt.Tool if sys.platform == "darwin" else Qt.Window)


def test_f9_brings_a_minimized_chat_back(overlay):
    overlay.save_geometry()
    overlay.showMinimized()                 # Win+D / "Show desktop" minimizes a normal window
    pump(overlay.app, 50)
    assert overlay.isVisible() and not overlay.is_open()
    overlay.toggle(lambda _hwnd: None)
    pump(overlay.app, 400)
    assert overlay.is_open() and not overlay.isMinimized()


def test_a_failing_screenshot_brings_the_chat_back(overlay, monkeypatch):
    def boom(_hwnd):
        raise RuntimeError("capture failed")
    from maplehelper import osapi
    monkeypatch.setattr(osapi, "find_game_window", lambda: 1234)
    overlay.shot_provider = boom
    overlay.setWindowOpacity(0.0)
    overlay._fresh_shot()
    assert overlay.windowOpacity() == 1.0
    assert overlay._safe_shot(1234) is None


def test_what_now_while_busy_says_so_without_hiding_the_chat(overlay):
    overlay.busy = True
    before = overlay.feed_lay.count()
    overlay.what_now()
    overlay.what_now()
    assert overlay.windowOpacity() == 1.0
    assert overlay.feed_lay.count() - before == 1         # one notice, not one per click


def test_screenshot_hint_names_the_players_hotkey(overlay):
    overlay.settings["hotkey_toggle"] = "F8"
    overlay.game_hwnd, overlay.shot = None, None
    overlay._update_shot_hint()
    # the words the player reads: the label's HTML also carries the badge PNG as base64, which can spell "F9"
    shown = QTextDocumentFragment.fromHtml(overlay.shot_hint.text()).toPlainText()
    assert "F8" in shown and "F9" not in shown


def test_language_switch_updates_tooltips_and_term_language(overlay):
    from maplehelper.ui import terms
    overlay.settings["language"] = "en"
    overlay.apply_language()
    assert overlay.tools_btn.toolTip() == "Play tools" and overlay.clear_tags_btn.toolTip() == "Clear all tags"
    assert terms.LANG == "en"


def test_another_character_in_game_is_offered_not_overwritten(overlay):
    """A new character in game while the app's active one is another: nothing changes until the player adds it."""
    from maplehelper.brain import Answer
    from maplehelper.ui.widgets import NoticeCard
    before = overlay.profiles.active
    ans = Answer(text="ok", profile_update={"name": "NewGuy99", "level": 3, "job": "Beginner"})
    assert overlay._offer_other_character(ans, None, None)
    assert overlay.profiles.active is before and before.name == "Elipaz" and before.level == 32
    notices = overlay.findChildren(NoticeCard)
    assert notices
    notices[-1].clicked.emit()
    notices[-1].clicked.emit()                       # a double click adds it once
    new = overlay.profiles.active
    assert new.name == "NewGuy99" and new.level == 3 and len(overlay.profiles.characters) == 2
    # back on the first one, the same read offers to switch instead of adding again
    overlay.switch_character(before.id)
    assert overlay._offer_other_character(ans, None, None)
    overlay.findChildren(NoticeCard)[-1].clicked.emit()
    assert overlay.profiles.active.name == "NewGuy99" and len(overlay.profiles.characters) == 2
    # once the HUD confirmed "NewGuy99", a longer name is another character (an alt), not a rename
    assert overlay._offer_other_character(Answer(text="ok", profile_update={"name": "NewGuy99x"}), None, None)
    # the exact name is no offer
    assert not overlay._offer_other_character(Answer(text="ok", profile_update={"name": "NewGuy99"}), None, None)


# ------------------------------------------------------------------ review round: display fixes

def test_profile_change_lines_keep_english_values_in_one_block():
    from maplehelper import bidi
    from maplehelper.ui.overlay import change_line, stats_text
    en, he = I18n("en"), I18n("he")
    assert change_line(en, "map", "Tree Dungeon, Monkey Forest I") == "✓ Updated · Map: Tree Dungeon, Monkey Forest I"
    assert change_line(en, "stats", "hp 900") == f"✓ Updated · Stats: HP{NBSP}900"
    # Hebrew: the English map name is one isolated block ("Monkey Forest I ,Tree Dungeon" came out reversed)
    assert f"{bidi.LRI}Tree Dungeon, Monkey Forest I{bidi.PDI}" in change_line(he, "map", "Tree Dungeon, Monkey Forest I")
    # each English stat with its number is one piece, followed by an RLM; Hebrew labels stay plain
    shown = stats_text(he, "acc 55, dmg_min 30, hp 900")
    assert f"{bidi.LRI}Accuracy (ACC){NBSP}55{bidi.PDI}{bidi.RLM}" in shown
    assert f"{bidi.LRI}HP{NBSP}900{bidi.PDI}{bidi.RLM}" in shown        # never wrapped between "HP" and "900"
    assert f"נזק מינימלי{NBSP}30" in shown


def test_notice_button_goes_under_the_text_when_narrow():
    from maplehelper.ui.widgets import NoticeCard
    assert NoticeCard.button_below(420, 100)          # at 470 px "Add Kalimba" would squeeze the text
    assert not NoticeCard.button_below(720, 100)


def test_history_shows_the_question_without_the_focus_tag():
    from maplehelper import pins
    from maplehelper.ui.pinsview import short_text
    assert pins.shown_question("[about Mano] what does it drop?") == "what does it drop?"
    assert pins.shown_question("what about [about Mano]?") == "what about [about Mano]?"   # only a leading tag
    assert pins.shown_question("[about Mano]") == "[about Mano]"                            # never empty
    assert short_text("a  b\n\nc", 70) == "a b c"
    cut = short_text("word " * 30, 70)
    assert cut.endswith("…") and len(cut) <= 71 and not cut[:-1].endswith(" ")


def test_continue_from_history_strips_the_tag_and_keeps_following_a_streaming_answer(overlay):
    from maplehelper.ui.widgets import Bubble
    pending = overlay.add_bubble("…", "assistant")
    overlay._start_reading(pending)
    overlay.busy = True
    overlay.continue_from("[about Mano] what does it drop?", "Mano drops a shell.", [])
    assert overlay._reading is pending
    users = [b for b in overlay.findChildren(Bubble) if b.role == "user"]
    assert "[about" not in users[-1].label.text() and "what does it drop?" in users[-1].label.text()
    overlay.busy = False
    overlay.continue_from("again?", "Yes.", [])
    assert overlay._reading is not pending


def test_magician_stretch_hint_mentions_only_misses():
    for lang in ("he", "en"):
        s = I18n(lang)("train_stretch_magician")
        assert "רגילות" not in s and "basic" not in s and "סקילים" not in s and "Skills" not in s


def test_dropper_map_line_splits_the_region(kb_copy):
    from maplehelper.kb import KnowledgeBase
    d = kb_copy / "pages" / "map"
    d.mkdir(parents=True, exist_ok=True)
    (d / "999.md").write_text("# Snail Hunting Ground I\n\nLocation Maple Road / Maple Island\n", encoding="utf-8")
    kb = KnowledgeBase(kb_copy)
    assert kb.map_label("Snail Hunting Ground I Maple Road") == "Snail Hunting Ground I · Maple Road"
    assert kb.map_label("Somewhere Else") == "Somewhere Else"
    assert kb.map_label("Maple Road") == "Maple Road"


def test_ask_in_chat_while_busy_says_so_and_continue_closes_the_history():
    from types import SimpleNamespace

    from maplehelper.app import MapleHelperApp
    calls = []
    ov = SimpleNamespace(isVisible=lambda: True, is_open=lambda: True, _is_busy=lambda: True, _say_busy=lambda: calls.append("busy"),
                         ask=lambda q: calls.append("ask"), continue_from=lambda *a: calls.append("continue"))
    history = SimpleNamespace(close=lambda: calls.append("closed"))
    fake = SimpleNamespace(overlay=ov, profiles=SimpleNamespace(active=SimpleNamespace(id="c1")),
                           _windows={"history:c1": history})
    MapleHelperApp.ask_from_tools(fake, "Who drops it?", False)
    assert calls == ["busy"]                         # not silently dropped
    calls.clear()
    MapleHelperApp.continue_conversation(fake, "q", "a", [])
    assert calls == ["closed", "continue"]


def test_every_edge_and_corner_resizes_the_chat(overlay):
    """Only the bottom corner's grip resized it; now the frameless window's whole rim does, like a border."""
    from PySide6.QtCore import QPoint, Qt
    ov = overlay
    ov.resize(500, 700)
    w, h, z = ov.width(), ov.height(), ov.SHADOW + ov.EDGE
    E = ov._edges_at
    assert E(QPoint(2, 2)) == Qt.LeftEdge | Qt.TopEdge
    assert E(QPoint(w - 2, 2)) == Qt.RightEdge | Qt.TopEdge
    assert E(QPoint(2, h - 2)) == Qt.LeftEdge | Qt.BottomEdge
    assert E(QPoint(w - 2, h - 2)) == Qt.RightEdge | Qt.BottomEdge
    assert E(QPoint(w // 2, 3)) == Qt.TopEdge and E(QPoint(w - 3, h // 2)) == Qt.RightEdge
    assert E(QPoint(z + 5, h // 2)) == Qt.Edge(0) and E(QPoint(w // 2, h // 2)) == Qt.Edge(0)
    assert ov._edge_cursor(Qt.LeftEdge | Qt.TopEdge) == Qt.SizeFDiagCursor
    assert ov._edge_cursor(Qt.LeftEdge | Qt.BottomEdge) == Qt.SizeBDiagCursor
    assert ov._edge_cursor(Qt.TopEdge) == Qt.SizeVerCursor and ov._edge_cursor(Qt.Edge(0)) is None
    # the rim is free of controls: the header and the input start inside it
    assert ov.title_bar.geometry().top() >= z and ov.title_bar.geometry().left() >= z


def test_a_notice_in_the_chat_can_be_closed():
    """A KB update's note ("33 changes that affect you…") had no way out of the chat (the owner's report)."""
    from PySide6.QtWidgets import QApplication
    from maplehelper.ui.widgets import NoticeCard
    app = QApplication.instance() or QApplication([])
    card = NoticeCard("33 שינויים במאגר נוגעים לכם", "מה השתנה?", True)
    card.show()
    assert card.close_btn is not None and card.close_btn.toolTip() == "סגירה"
    card.close_btn.click()
    app.processEvents()
    assert not card.isVisible()
    assert NoticeCard("", "", True, stacked=True, closable=False).close_btn is None


def test_the_instant_answer_picks_its_language_with_the_kb_like_the_ai(overlay, monkeypatch):
    """A name holding "of"/"to" ("Transforming Dark Jr. Yeti hp") is no English sentence only when the KB's names are
    read: the AI's prompt passes the KB, the instant answer didn't, so it came in English and "Ask Claude anyway" in
    Hebrew (review CORE-3, AI-13 x P84B-1)."""
    from maplehelper import brain, quick
    seen = []
    monkeypatch.setattr(brain, "reply_language", lambda q, ui="he", kb=None: seen.append(kb) or "Hebrew")
    monkeypatch.setattr(quick, "answer", lambda *a, **k: None)
    overlay.settings["instant_answers"] = True
    overlay.brain = None
    try:
        overlay.ask("Mano hp")
    except Exception:
        pass                # no AI provider here: only the instant path's language matters
    assert seen and seen[0] is overlay.kb
