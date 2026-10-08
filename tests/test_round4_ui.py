"""Round 4 UI and logic fixes (offscreen Qt): keyboard focus, tab order, history paging, pins, confirmations,
language switches, prices, profile safety (another character), the ⟳ sync, and the worker-thread reads."""
import os
import re
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import bidi, brain, pins  # noqa: E402
from maplehelper.brain import Answer  # noqa: E402
from maplehelper.i18n import NBSP, STRINGS, I18n  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(cond, ms=3000):
    end = time.time() + ms / 1000
    while time.time() < end and not cond():
        app.processEvents()
        time.sleep(0.005)
    return cond()


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
    c = p.add("Elipaz", "Thief", "Assassin", 32)
    p.set_active(c.id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 460, 700))
    ov.show()
    yield ov
    ov.hide()
    ov.deleteLater()


def lines(ov) -> list[str]:
    from maplehelper.ui.widgets import SystemLine
    return [w.text() for w in ov.feed.findChildren(SystemLine)]


# ------------------------------------------------------------------ 2, 3: focus ring and the first focus

def test_focus_ring_only_for_keyboard_focus():
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget
    from maplehelper.ui import theme
    theme.install_focus_ring()
    w = QWidget()
    lay = QVBoxLayout(w)
    a, b = QPushButton("a"), QPushButton("b")
    lay.addWidget(a)
    lay.addWidget(b)
    w.show()
    pump()
    b.setFocus(Qt.TabFocusReason)
    pump()
    ring = b.findChild(theme._Ring)
    assert ring is not None and ring.isVisible() and ring.geometry() == b.rect()
    a.setFocus(Qt.MouseFocusReason)          # a click: no ring anywhere
    pump()
    assert a.findChild(theme._Ring) is None and theme._FOCUS_RING.ring is None
    w.close()


def test_a_clicked_button_that_hides_itself_rings_nothing():
    """"Update now" turns into the download's progress: Qt hands its focus to the next button as if Tab were
    pressed, and the search button showed the orange ring (the owner's, 2026-10-08)."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget
    from maplehelper.ui import theme
    theme.install_focus_ring()
    w = QWidget()
    lay = QVBoxLayout(w)
    search, update = QPushButton("search"), QPushButton("update")
    lay.addWidget(search)
    lay.addWidget(update)
    w.show()
    pump()
    update.setFocus(Qt.MouseFocusReason)      # clicked
    pump()
    update.hide()                             # its own click hid it
    pump()
    assert search.findChild(theme._Ring) is None and theme._FOCUS_RING.ring is None
    update.show()
    update.setFocus(Qt.MouseFocusReason)
    pump()
    search.setFocus(Qt.TabFocusReason)        # a real Tab still rings
    pump()
    assert search.findChild(theme._Ring) is not None
    w.close()


def test_dialogs_open_on_their_search_field_not_the_close_button(kb):
    from maplehelper.ui.guides import GuidesDialog
    from maplehelper.ui.pinsview import HistoryDialog
    pairs = [{"q": "where?", "a": "here", "t": time.time()}]
    for d in (HistoryDialog(pairs, "Elipaz", "he", ""), GuidesDialog(kb, None, "en", "")):
        d.move(-4000, -4000)
        d.show()
        assert wait_for(lambda d=d: d.focusWidget() is d.search)
        d.close()


def test_an_empty_history_shows_no_search_field():
    """review3 VIS6-a: nothing to search: no field, and the focus isn't on one."""
    from maplehelper.ui.pinsview import HistoryDialog
    d = HistoryDialog([], "Elipaz", "he", "")
    d.move(-4000, -4000)
    d.show()
    pump()
    assert not d.search.isVisible() and d.focusWidget() is not d.search
    d.close()


def test_a_confirmation_opens_on_its_safe_answer():
    from maplehelper.ui.dialogs import ConfirmDialog
    d = ConfirmDialog("t", "body", "Delete", "Cancel", False, "")
    d.move(-4000, -4000)
    d.show()
    assert wait_for(lambda: d.focusWidget() is not None and d.focusWidget().text() == "Cancel")
    assert d.focusWidget() is not d.close_btn
    d.close()


# ------------------------------------------------------------------ 4: tab order follows the screen

def test_tab_order_follows_the_screen(overlay):
    overlay.set_tags(["monster/100100"])
    overlay.add_confirm("?", lambda: None)
    pump(100)
    order = overlay.tab_order()
    pos = {w: i for i, w in enumerate(order)}
    must = [overlay.history_btn, overlay.close_btn, overlay.profile_card.refresh, overlay.profile_card.now_btn,
            overlay.clear_tags_btn, overlay.recapture_btn, overlay.input, overlay.clear_btn, overlay.mic_btn]
    assert all(w in pos for w in must)
    # header, then the character card, then the tagged cards, then the input row (camera, field, clear, mic)
    assert pos[overlay.history_btn] < pos[overlay.profile_card.now_btn] < pos[overlay.clear_tags_btn] \
        < pos[overlay.recapture_btn] < pos[overlay.input] < pos[overlay.clear_btn] < pos[overlay.mic_btn]
    overlay.activateWindow()
    overlay.input.setFocus()
    pump(50)
    if app.focusWidget() is not overlay.input:
        pytest.skip("the window didn't get keyboard focus (another window is active during the full run)")
    assert overlay.focusNextPrevChild(True) and app.focusWidget() is overlay.clear_btn
    assert overlay.focusNextPrevChild(True) and app.focusWidget() is overlay.mic_btn
    overlay.focusNextPrevChild(True)
    assert app.focusWidget() is order[0]                 # round to the header


# ------------------------------------------------------------------ 5: history paging and debounce

def test_history_shows_a_page_with_a_count_and_more(kb):
    from maplehelper.ui.pinsview import HistoryDialog
    pairs = [{"q": f"q{i} Mano", "a": f"a{i}", "t": time.time() - i} for i in range(200)]
    d = HistoryDialog(pairs, "Elipaz", "en", "")
    cards = lambda: [f for f in d.findChildren(type(d.count).__mro__[1]) if f.objectName() == "Card" and f.isVisibleTo(d)]  # noqa: E731
    assert "80 of 200" in d.count.text().replace(bidi.RLM, "")
    assert len(cards()) == 80 and d.more_btn.text() == "Show more"
    d.more_btn.click()
    pump()
    assert "160 of 200" in d.count.text() and len(cards()) == 160
    d.search.setText("q19")                    # debounced: nothing rebuilt on the key itself
    assert "160 of 200" in d.count.text()
    assert wait_for(lambda: "11 results" in d.count.text())        # q19, q190-q199
    d.close()


def test_history_preview_keeps_an_english_ellipsis_at_its_end(kb):
    from maplehelper.ui.pinsview import HistoryDialog
    d = HistoryDialog([{"q": "where?", "a": "word " * 60, "t": time.time()}], "Elipaz", "he", "")
    # laid out left to right as itself (not an isolate inside a right-to-left paragraph, which clipped the first
    # letter, see test_tools_windows): its "…" stays after the English text
    previews = [lb for lb in d.findChildren(type(d.count)) if lb.text().endswith("word…")]
    assert previews and all(lb.layoutDirection() == Qt.LeftToRight for lb in previews)
    d.close()


# ------------------------------------------------------------------ 6: the 13th pin asks

def test_thirteenth_pin_asks_before_dropping_the_oldest(overlay):
    cid = overlay.profiles.active_id
    for i in range(pins.MAX_PINS):
        pins.add(overlay.settings, cid, f"q{i}", f"a{i}", now=i)
    overlay.pin_answer("new q", "new a")
    assert len(pins.items(overlay.settings, cid)) == pins.MAX_PINS
    assert not any(p["a"] == "new a" for p in pins.items(overlay.settings, cid))      # nothing dropped yet
    row = overlay.feed_lay.itemAt(overlay.feed_lay.count() - 1).widget()
    yes = row.chips[0]
    yes.click()
    have = [p["a"] for p in pins.items(overlay.settings, cid)]
    assert have[0] == "new a" and "a0" not in have and len(have) == pins.MAX_PINS
    assert any("q0" in ln for ln in lines(overlay))                       # says which pin went


# ------------------------------------------------------------------ 7: switch account asks

def test_switch_account_asks_first(isolated_store, kb, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui import dialogs
    for n in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(n)), "account", lambda self: {"status": "ok", "email": "a@b.c"})
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    d = dialogs.SettingsDialog(s, p, kb, lambda o=None: "")
    d._account_status = "ok"
    asked, logged_out = [], []
    monkeypatch.setattr(dialogs.ConfirmDialog, "exec", lambda self: asked.append(self.windowTitle()) or 0)
    monkeypatch.setattr(type(providers.get("claude")), "logout", lambda self: logged_out.append(1))
    d._switch_account()
    pump()
    assert asked == [I18n("he")("account_switch")] and not logged_out and d.switch_btn.isEnabled()
    d.close()


def test_settings_close_with_unsaved_changes_asks(isolated_store, kb, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui import dialogs
    monkeypatch.setattr(type(providers.get("claude")), "account", lambda self: {"status": "ok", "email": None})
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    d = dialogs.SettingsDialog(s, p, kb, lambda o=None: "")
    d.show()
    choice = {"v": None}

    def fake_exec(self):
        self.choice = choice["v"]
        return 1 if choice["v"] == "yes" else 0
    monkeypatch.setattr(dialogs.ConfirmDialog, "exec", fake_exec)
    d.voice_send.setChecked(not d.voice_send.isChecked())
    assert d.unsaved()
    d.close_btn.click()                   # closed the question: Settings stays open
    assert d.isVisible()
    choice["v"] = "yes"
    d.close_btn.click()                   # save
    assert not d.isVisible() and s["voice_send_immediately"] == d.voice_send.isChecked()


# ------------------------------------------------------------------ 8: a language switch redraws the feed

def test_language_switch_redraws_system_lines_and_choices(overlay):
    overlay.add_system(lambda t: t("no_game"))
    row = overlay.add_confirm(lambda t: t("confirm_other_char", desc="Cleric", name="Elipaz"), lambda: None)
    overlay.settings["language"] = "en"
    overlay.apply_language()
    assert any("Game window not found" in ln for ln in lines(overlay))
    assert [b.text() for b in row.chips] == ["Yes", "No"]


# ------------------------------------------------------------------ 9: tag chips

def test_tag_chip_name_is_one_block_after_a_gap(overlay):
    overlay.set_tags(["monster/100100"])
    from PySide6.QtWidgets import QPushButton
    chip = next(b for b in overlay.focus_bar.findChildren(QPushButton) if b.objectName() == "TagChip")
    assert chip.text().startswith(" " + bidi.LRI) and bidi.PDI in chip.text()


# ------------------------------------------------------------------ 10: one "?" per term on the prices page

def test_prices_page_explains_a_term_once(isolated_store, kb):
    from maplehelper.ui.tools import ToolsDialog
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    p.add("Elipaz", "Thief", "Assassin", 32)
    d = ToolsDialog(kb, p, s, "en", "", {}, "prices")
    d.price_input.setText("Red Potion")
    d._fill_prices()
    from PySide6.QtWidgets import QLabel
    page = d.pages["prices"]
    marks = sum(lb.text().count("g:NPC") for lb in page.findChildren(QLabel))
    assert marks == 1
    d.close()


# ------------------------------------------------------------------ low: elided hint, notices, names, i18n

def test_input_hint_is_elided_not_cut(overlay):
    overlay.input.resize(120, 30)
    pump()
    shown = overlay.input.placeholderText()
    assert "…" in shown and len(shown) < len(overlay.input._hint)


def test_no_character_card_stacks_its_button_in_both_languages(overlay):
    assert overlay.no_char_card._stacked


def test_hebrew_name_is_one_block_in_an_english_line():
    from maplehelper.session import lines as session_lines
    summary = {"chars": [{"name": "אליפז", "start_level": 3, "end_level": 5, "start_job": "Beginner",
                          "end_job": "Beginner", "questions": 0, "quests_done": [], "quests_started": []}]}
    line = session_lines(summary, I18n("en"))[0]
    assert line.startswith(bidi.RLI + "אליפז" + bidi.PDI) and bidi.direction(line) == "ltr"
    assert bidi.plain(line) == line          # an English line: no Hebrew run marks added


@pytest.mark.parametrize("key", ["slow", "retry", "asking_about", "sign_in", "tray_show", "show_in_captures",
                                 "show_in_captures_hint", "glass_strength", "glass_strength_hint", "job_hint", "about",
                                 "spot_where", "calc_at_level", "calc_acc_by_level", "calc_maps_head", "calc_maps",
                                 "ob_choose_lang", "confirm_profile"])
def test_unused_strings_are_gone(key):
    assert key not in STRINGS


def test_wording_round4():
    he, en = I18n("he"), I18n("en")
    assert he("craft_head", n=1, prof="Smithing", lv=3).count("1") == 0       # "מתכון אחד", not "1 מתכונים"
    assert en("spot_kills", n=1) == "1 more kill to the next level"
    assert "Claude" + NBSP + "Code" in he("ob_install") and "Claude Code" in en("ob_install")
    assert "Maple" + NBSP + "Helper" in he("whats_new_notice", version="1")
    assert en("card_req_level") == "Required level" and en("asking_about_short") == "Asking about:"
    assert not any("Tap" in v.get("en", "") or "..." in v.get("en", "") + v.get("he", "")
                   for v in STRINGS.values())
    assert he("usage_5h", pct=5, at="10:00").endswith("איפוס בשעה 10:00")


def test_no_break_space_keeps_a_name_one_run():
    shown = bidi.isolate_ltr_runs("התקנת Claude" + NBSP + "Code עכשיו")
    assert shown.count(bidi.LRE) == 1


def test_api_key_hint_reads_right_to_left_in_hebrew(isolated_store, kb, monkeypatch):
    from maplehelper import providers
    from maplehelper.ui import dialogs
    for n in ("claude", "codex"):
        monkeypatch.setattr(type(providers.get(n)), "account", lambda self: {"status": "logged_out", "email": None})
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["language"] = "he"
    ob = dialogs.Onboarding(s, p, kb, lambda o=None: "")
    assert ob.key_edit.layoutDirection() == Qt.RightToLeft
    assert bidi.LRI + "sk-ant-" + bidi.PDI in ob.key_edit.placeholderText()
    ob.key_edit.setText("sk-ant-123")
    assert ob.key_edit.layoutDirection() == Qt.LeftToRight
    ob.close()


# ------------------------------------------------------------------ L1: reads off the GUI thread

def test_inventory_read_runs_in_a_worker_and_cards_come_first(overlay, monkeypatch):
    import threading

    from maplehelper import capture
    from maplehelper.ui import overlay as ov_mod
    main = threading.current_thread()
    seen = {}

    def fake_read(full, cursor, kb):
        seen["thread"] = threading.current_thread()
        return [], [SimpleNamespace(matches=[("item/2000000", 0.1)])], "slot 1: Red Potion"
    monkeypatch.setattr(ov_mod, "read_inventory", fake_read)

    def fake_shot():
        overlay.shot, overlay.shot_used = b"jpeg", False
        capture.LAST_FULL = object()
    monkeypatch.setattr(overlay, "_fresh_shot", fake_shot)
    asked = []
    monkeypatch.setattr(overlay, "ask", lambda q, shown=None, **k: asked.append((q, overlay._hidden_context)) or True)
    overlay._capture_and_ask("sort my bag", detail=True, shown="Inventory check")
    assert overlay._is_busy() and not asked               # reading: the question waits for it
    assert wait_for(lambda: asked)
    assert seen["thread"] is not main
    assert "Red Potion" in asked[0][1] and asked[0][0] == "sort my bag"
    assert any("1" in ln or "אחד" in ln for ln in lines(overlay))     # "I recognized 1 item" before the question


def test_look_alike_items_get_no_card_and_the_player_is_asked_to_hover(overlay, monkeypatch):
    """Every scroll of a tier has one picture: the app doesn't pick one. It says the slot is one of N look-alikes
    and asks for the mouse on the item (its tooltip names it), then F5 checks again without a click."""
    from PySide6.QtGui import QKeyEvent
    from maplehelper.ui import overlay as ov_mod
    t = I18n("he")
    look_alikes = [("item/2000000", 0.0), ("item/2000001", 0.0), ("item/2000002", 0.0)]
    slots = [SimpleNamespace(index=4, matches=look_alikes, status="ambiguous"),
             SimpleNamespace(index=9, matches=look_alikes, status="ambiguous"),
             SimpleNamespace(index=2, matches=[("item/2000000", 50.0)], status="unknown")]
    cards = []
    monkeypatch.setattr(overlay, "add_cards", lambda keys: cards.append(keys))
    overlay._show_inventory_read(slots)
    import re
    plain = lambda s: re.sub("[‎‏‪-‮⁦-⁩]", "", s).replace(NBSP, " ")     # noqa: E731
    shown = [plain(ln) for ln in lines(overlay)]
    assert not cards                                            # no card for a guess
    assert any("4, 9" in ln and "3" in ln and "Red Potion" in ln for ln in shown)
    assert plain(t("inv_unknown", n=1, slots="2")) in shown and plain(t("inv_hover")) in shown
    assert plain(t("inv_not_found")) not in shown
    asked = []
    monkeypatch.setattr(overlay, "ask_with_screenshot", lambda q, detail=False, shown=None: asked.append((q, detail)))
    overlay.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_F5, Qt.NoModifier))
    assert asked == [(t("sell_q"), True)]
    from PySide6.QtTest import QTest
    QTest.keyClick(overlay.input, Qt.Key_F5)                   # typing in the chat, the mouse on the game
    assert asked == [(t("sell_q"), True)] * 2
    assert ov_mod.read_inventory.__doc__ and "inventory_read" in ov_mod.read_inventory.__doc__


def test_portrait_is_cropped_in_a_worker(overlay, monkeypatch):
    import threading

    from maplehelper.ui import overlay as ov_mod
    where = {}

    def fake_crop(*a):
        where["t"] = threading.current_thread()
        return b"\x89PNG fake"
    monkeypatch.setattr(ov_mod, "crop_portrait", fake_crop)
    got, done = [], []
    monkeypatch.setattr(overlay.profiles, "set_avatar", lambda png: got.append(png))
    overlay._update_avatar(b"jpeg", None, on_done=done.append)
    assert wait_for(lambda: done)
    assert where["t"] is not threading.main_thread() and got == [b"\x89PNG fake"] and done == [True]


# ------------------------------------------------------------------ L2, L3: numbers and the prompt

def test_profile_numbers_become_ints():
    _, meta = brain.split_meta('ok\n@@META@@\n{"profile_update": {"level": "12", "stats": {"acc": 45.0, "hp": "900", "x": true}}}')
    assert meta["profile_update"] == {"level": 12, "stats": {"acc": 45, "hp": 900}}
    for bad in ("true", '"twelve"', "12.5"):
        _, meta = brain.split_meta(f'ok\n@@META@@\n{{"profile_update": {{"level": {bad}}}}}')
        assert "level" not in meta["profile_update"]


def test_the_question_goes_once_in_the_prompt(kb, isolated_store):
    h = isolated_store.History("c1")
    h.append("user", "earlier?")
    h.append("assistant", "earlier answer")
    h.append("user", "[about Mano] what does Mano drop?")
    prompt = brain.build_prompt("what does Mano drop?", None, h, kb, False)
    assert prompt.count("what does Mano drop?") == 1 and "earlier answer" in prompt


def test_ask_claude_anyway_pairs_both_answers_with_one_question():
    records = [{"role": "user", "text": "Mano HP", "t": 1}, {"role": "assistant", "text": "7420", "t": 2},
               {"role": "assistant", "text": "Mano has 7,420 HP.", "t": 3}]
    assert [(p["q"], p["a"]) for p in pins.conversations(records)] == [("Mano HP", "7420"),
                                                                       ("Mano HP", "Mano has 7,420 HP.")]


def test_sync_prompt_is_light(kb):
    prompt = brain.build_prompt("read my HUD", None, None, kb, True, kb_context=False)
    assert "<kb_context>" not in prompt and "<question>" in prompt and "attached above" in prompt


# ------------------------------------------------------------------ L4: prices from the shop lists

def test_unpriced_sellers_and_citizen_grades(tmp_path):
    import json

    from maplehelper import market
    from maplehelper.kb import KnowledgeBase
    page = ("---\n{}\n---\n# Pizza\nWhere to buy\nRaymond Town General Store cheapest\n"
            "Victoria Road: Henesys Town Hall · Henesys\n252\nmesos\nCOT2 prices Recognized Guest +\n"
            "Jane\nVictoria Road: Lith Harbor · Lith Harbor\n-\nmesos\nCOT2 prices\nDropped By\n")
    (tmp_path / "index.json").write_text(json.dumps([{"key": "item/1", "name": "Pizza", "category": "item"}]))
    (tmp_path / "pages" / "item").mkdir(parents=True)
    (tmp_path / "pages" / "item" / "1.md").write_text(page, encoding="utf-8")
    prices = market.npc_prices(KnowledgeBase(tmp_path), "item/1")
    assert prices.shops == [("Raymond Town General Store", "Victoria Road: Henesys Town Hall · Henesys", 252)]
    assert prices.unpriced == [("Jane", "Victoria Road: Lith Harbor · Lith Harbor")]
    assert prices.ranks[prices.shops[0][:2]] == "Recognized Guest"


# ------------------------------------------------------------------ S2, S3: the ⟳ sync

def test_sync_shows_what_it_does_and_times_out(overlay, monkeypatch):
    from maplehelper import osapi
    overlay.SYNC_TIMEOUT_MS = 50
    monkeypatch.setattr(osapi, "capture_game", lambda h: None)
    overlay.brain = SimpleNamespace(cancel=lambda: None)
    overlay._syncing = False
    overlay.sync_profile()
    assert overlay.profile_card.status.isVisibleTo(overlay.profile_card)
    assert overlay.profile_card.status.text().replace(bidi.RLM, "") == I18n("he")("sync_reading")
    # no game: it ends at once, the line says so and the spinner stops
    assert wait_for(lambda: not overlay._syncing)
    assert not overlay.profile_card.status.isVisibleTo(overlay.profile_card)
    # a read that never answers: the timer stops it with a clear line
    overlay._syncing = True
    overlay.profile_card.set_busy(True, "", "x")
    overlay._sync_worker = object()
    overlay._sync_timer.start()
    assert wait_for(lambda: not overlay._syncing)
    assert any(I18n("he")("sync_timeout") in ln.replace(bidi.RLM, "") for ln in lines(overlay))
    assert overlay.profile_card.refresh.isEnabled()


def test_sync_seeing_another_character_stops_at_once_and_asks(overlay):
    overlay._syncing, overlay._sync_cid, overlay._sync_shot = True, overlay.profiles.active_id, b"jpeg"
    overlay.profile_card.set_busy(True, "", "x")
    overlay._on_sync_done(Answer(text="ok", profile_update={"name": "Elipazz", "level": 55, "job": "Cleric"}))
    assert not overlay._syncing and overlay.profile_card.refresh.isEnabled()
    from maplehelper.ui.widgets import NoticeCard
    cards = overlay.feed.findChildren(NoticeCard)
    assert cards and cards[-1].btn2 is not None                 # add as new / the same character
    c = overlay.profiles.active
    assert c.name == "Elipaz" and c.level == 32 and c.job == "Assassin"     # nothing touched yet


# ------------------------------------------------------------------ O1, O2: another character never overwrites

def test_ayashii_via_sync_is_offered_not_renamed(overlay):
    c = overlay.profiles.active
    c.name, c.base_class, c.job, c.level = "Ayash", "Thief", "Assassin", 131
    overlay._syncing, overlay._sync_cid, overlay._sync_shot = True, c.id, b"jpeg"
    overlay._on_sync_done(Answer(text="ok", profile_update={"name": "Ayashii", "level": 55, "job": "Cleric"}))
    assert (c.name, c.level, c.job) == ("Ayash", 131, "Assassin")
    from maplehelper.ui.widgets import NoticeCard
    card = overlay.feed.findChildren(NoticeCard)[-1]
    card.btn.click()                          # add Ayashii as a new character
    new = overlay.profiles.active
    assert new.name == "Ayashii" and new.level == 55 and new.job == "Cleric" and new.id != c.id
    assert (c.name, c.level) == ("Ayash", 131)


MISREADS = ({"name": "Elipaz", "level": 10, "job": "Beginner", "base_class": "Beginner"},   # the owner's "Beginner 10"
            {"level": 3, "exp_percent": 12.5}, {"job": "Thief", "level": 32}, {"job": "Bandit", "level": 32},
            {"job": "Cleric", "level": 32}, {"base_class": "Beginner"})


@pytest.mark.parametrize("update", MISREADS)
def test_a_sync_read_asks_before_a_demotion(overlay, update):
    """⟳ that reads a lower level, another class or a job back/sideways asks first (audit SCR-1 / AI-1)."""
    c = overlay.profiles.active
    overlay._syncing, overlay._sync_cid, overlay._sync_shot = True, c.id, b"jpeg"
    overlay._on_sync_done(Answer(text="ok", profile_update=dict(update)))
    assert (c.base_class, c.job, c.level, c.exp_pct) == ("Thief", "Assassin", 32, None)
    from PySide6.QtWidgets import QPushButton
    yes = bidi.plain(I18n("he")("profile_update_yes"), True)
    assert [b for b in overlay.feed.findChildren(QPushButton) if b.text() == yes]     # update / add as new / cancel


@pytest.mark.parametrize("update", MISREADS)
def test_the_minute_grind_read_never_demotes(overlay, update):
    c = overlay.profiles.active
    got = []
    overlay.grind_read.connect(got.append)
    overlay._sync_cid = c.id
    overlay._quiet_grind_read(Answer(text="ok", profile_update=dict(update)))
    assert (c.base_class, c.job, c.level, c.exp_pct) == ("Thief", "Assassin", 32, None)
    assert got and not lines(overlay)                         # the tracker still gets its numbers; no prompt
    overlay._quiet_grind_read(Answer(text="ok", profile_update={"level": 33, "exp_percent": 2.0}))
    assert (c.level, c.exp_pct) == (33, 2.0)                  # a level up still follows


def test_same_character_button_takes_the_hud_name(overlay):
    c = overlay.profiles.active
    c.name = "Kalimero"
    overlay._question_shot = None
    assert overlay._offer_other_character(Answer(text="x", profile_update={"name": "KalimeroZz", "level": 33}), None, None)
    from maplehelper.ui.widgets import NoticeCard
    overlay.feed.findChildren(NoticeCard)[-1].btn2.click()
    assert c.name == "KalimeroZz" and c.name_seen and c.level == 33


def test_ayashii_via_chat_is_offered_too(overlay):
    c = overlay.profiles.active
    c.name, c.level = "Ayash", 131
    overlay._asked_cid, overlay._pending_bubble = c.id, overlay.add_bubble("", "assistant")
    overlay._on_done(Answer(text="Switched.", profile_update={"name": "Ayashii", "level": 55, "job": "Cleric"}), None)
    assert (c.name, c.level, c.job) == ("Ayash", 131, "Assassin")


def test_class_change_in_chat_asks_and_advancement_does_not(overlay):
    c = overlay.profiles.active                    # Thief / Assassin, Lv. 32
    overlay._apply_profile_update({"job": "Cleric", "level": 55})
    assert (c.job, c.level) == ("Assassin", 32)
    row = overlay.feed_lay.itemAt(overlay.feed_lay.count() - 1).widget()
    assert len(row.chips) == 3
    row.chips[0].click()                           # "Update"
    assert c.job == "Cleric" and c.level == 55 and c.base_class == "Magician"
    # a job advancement within the class and a level up: at once
    c.base_class, c.job, c.level = "Magician", "Magician", 29
    overlay._apply_profile_update({"job": "Cleric", "level": 30})
    assert (c.job, c.level) == ("Cleric", 30)
    # a lower level asks
    overlay._apply_profile_update({"level": 20})
    assert c.level == 30
    # back to Beginner, to the 1st job, or sideways to the other 2nd job: asked, not applied (audit AI-2)
    c.base_class, c.job, c.level = "Thief", "Assassin", 31
    for update in ({"job": "Beginner"}, {"job": "Beginner", "level": 31}, {"job": "Thief", "level": 31},
                   {"job": "Bandit", "level": 31}, {"base_class": "Beginner"}):
        overlay._apply_profile_update(update)
        assert (c.base_class, c.job, c.level) == ("Thief", "Assassin", 31), update
    overlay._apply_profile_update({"job": "Hermit", "level": 70})
    assert (c.job, c.level) == ("Hermit", 70)


def test_job_advances_only_along_its_line():
    from maplehelper.jobs import advances
    assert advances("Beginner", "Thief") and advances("Thief", "Assassin") and advances("Thief", "Chief Bandit")
    assert advances("Assassin", "Hermit") and advances("Fighter", "Crusader") and advances("Assassin", "Assassin")
    assert not advances("Assassin", "Chief Bandit") and not advances("Fighter", "White Knight")
    assert not advances("Assassin", "Beginner") and not advances("Thief", "Beginner")
    assert not advances("Assassin", "Thief") and not advances("Assassin", "Bandit")
    assert not advances("Hermit", "Assassin")


def test_reply_rules_keep_profile_update_to_the_active_character():
    assert "ONLY the character in <player_profile>" in brain.REPLY_RULES


def test_claude_sync_runs_its_own_process_without_tools(monkeypatch):
    from maplehelper.providers import claude
    b = SimpleNamespace(model="sonnet", length="short", api_key=None, kb=SimpleNamespace(root="."),
                        system_prompt=lambda: "sys")
    backend = claude.ClaudeBackend.__new__(claude.ClaudeBackend)
    backend.brain, backend.exe = b, "claude.exe"
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        raise OSError("not started in a test")
    monkeypatch.setattr(claude.subprocess, "Popen", fake_popen)
    backend._warm = None
    backend._warm_lock = __import__("threading").Lock()
    r = backend.run("p", None, None, model="haiku", tools=False)
    assert r.error.startswith("launch_failed")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--model") + 1] == "haiku" and cmd[cmd.index("--tools") + 1] == ""


REAL_KB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "kb")


@pytest.mark.skipif(not os.path.exists(os.path.join(REAL_KB, "index.json")), reason="no real knowledge base")
def test_real_kb_shop_grades_and_unpriced_sellers():
    from pathlib import Path

    from maplehelper import market
    from maplehelper.kb import KnowledgeBase
    real = KnowledgeBase(Path(REAL_KB))
    try:
        # every shop list in the KB, read here line by line, against what npc_prices makes of it: a price, a "-"
        # with no price (pages/item/241.md: Jane), a "<build> prices <grade> +" grade (pages/item/274.md: Citizen
        # of Honor). The exact parse is test_unpriced_sellers_and_citizen_grades; here no live shop or grade is
        # named, so a store changing its stock or grades doesn't stop the nightly
        priced = unpriced = graded = 0
        for key, e in real.entities.items():
            if e.get("category") != "item":
                continue
            lines = [ln.strip() for ln in real.page(key).split("\n---", 2)[-1].splitlines()]
            if "Where to buy" not in lines:
                continue
            i, blocks = lines.index("Where to buy") + 1, []
            while i + 3 < len(lines) and lines[i + 3] == "mesos":
                grade = lines[i + 4] if i + 4 < len(lines) and re.match(r"^\S+ prices\b", lines[i + 4]) else None
                blocks.append((lines[i + 2], grade))
                i += 5 if grade else 4
            p = market.npc_prices(real, key)
            assert len(p.shops) + len(p.unpriced) == len(blocks), key
            assert len(p.unpriced) == sum(1 for price, _ in blocks if price == "-"), key
            grades = {g.split(" prices", 1)[1].rstrip("+ ").strip() for _, g in blocks if g and g.endswith("+")}
            assert set(p.ranks.values()) == grades - {""}, key
            priced, unpriced, graded = priced + len(p.shops), unpriced + len(p.unpriced), graded + len(p.ranks)
        assert priced > 200 and graded > 0          # a broken page scrape reads no shops (or no grades) at all
    finally:
        bidi.set_names([])
