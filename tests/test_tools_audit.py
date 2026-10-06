"""Launch-audit fixes in the Play tools window, its grind runner, the market cache and the keyboard (offscreen Qt):
TL1 / TL2 (tools pages) and UX (Enter on a Tabbed button, the tour's count, the pin's name)."""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def real_kb():
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture
def tools(app, real_kb, tmp_path, monkeypatch):
    """open(base_class, job, level, lang="en", page="train") -> a ToolsDialog on the real KB, its own store."""
    from maplehelper import grind, store
    from maplehelper.ui.tools import ToolsDialog
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    opened = []

    def open_(base="Thief", job="Assassin", level=35, lang="en", page="train", profiles=None):
        p = profiles or store.Profiles()
        if base:
            p.set_active(p.add("Kiwi", base, job, level).id)
        d = ToolsDialog(real_kb, p, store.Settings(), lang, "", None, page)
        opened.append(d)
        return d
    yield open_
    for d in opened:
        d.close()
    app.processEvents()


def _texts(layout):
    out = []
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        if w is not None and hasattr(w, "text"):
            out.append(w.text())
    return " ".join(out)


@needs_kb
def test_build_page_of_a_beginner_says_there_is_no_plan(tools):
    # TL1-2: "The plan ... from the guide. The orange row is where you are now." over "No build tables", in a box
    # too short for its one line
    d = tools("Beginner", "Beginner", 5, page="build")
    assert "No build tables" in d.build_head.text() and "orange row" not in d.build_head.text()
    assert d.build_view.isHidden()
    d.profiles.active.base_class, d.profiles.active.job, d.profiles.active.level = "Warrior", "Fighter", 35
    d.refresh("build")
    assert not d.build_view.isHidden() and "orange row" in d.build_head.text()


@needs_kb
def test_a_minute_read_redraws_only_pages_that_show_it(tools, monkeypatch):
    # TL1-3: every grind read redrew the current page, the Skipped quests list (~0.45 s) included
    from maplehelper.ui.tools import PAGES
    d = tools(page="build")
    calls = []
    monkeypatch.setattr(d, "_fill_build", lambda: calls.append("build"))
    monkeypatch.setattr(d, "_fill_quests", lambda: calls.append("quests"))
    monkeypatch.setattr(d, "_fill_exp", lambda: calls.append("exp"))
    d.sync_done(True)
    assert calls == []
    d.show_page(PAGES.index("quests"))                    # (built first: a placeholder page swapped the index)
    d.__dict__.setdefault("_filled", {})["quests"] = d._page_state("quests")
    calls.clear()
    d.sync_done(True)
    assert calls == []                                    # the same cards: kept
    d.profiles.active.quests_done.append("quest/none")
    d.sync_done(True)
    assert calls == ["quests"]
    d.show_page(PAGES.index("exp"))
    calls.clear()
    d.sync_done(True)
    assert calls == ["exp"]


@needs_kb
def test_quests_for_a_level_is_worked_out_once_and_handed_out_fresh(real_kb):
    # TL1-4: the quests page, its search and a jump to a quest each worked the lists out again
    from maplehelper import quests
    a = quests.for_level(real_kb, 31, "Thief", "Assassin", [])
    a["now"].clear()
    b = quests.for_level(real_kb, 31, "Thief", "Assassin", [])
    assert b["now"] and b["now"][0] is quests.for_level(real_kb, 31, "Thief", "Assassin", [])["now"][0]
    done = quests.for_level(real_kb, 31, "Thief", "Assassin", [b["now"][0].key])
    assert b["now"][0].key not in [q.key for q in done["now"]]


@needs_kb
def test_quest_search_header_says_the_level_only_on_the_levels_list(tools):
    # TL1-5: "1 of 43 quests at Lv. 31 match" on the Later tab, "0 of 0" over an empty list
    d = tools("Thief", "Assassin", 32, page="quests")
    later = quests_mode(d, "later")
    d.q_search.setText(later[0].name)
    d._fill_quests()
    assert "match" in d.q_head.text() and "at Lv." not in d.q_head.text()
    quests_mode(d, "level")
    d._fill_quests()
    assert "at Lv. 32" in d.q_head.text()


def quests_mode(d, mode):
    from maplehelper import quests
    modes = ("level", "missed", "soon", "later")
    d.q_mode.group.buttons()[modes.index(mode)].setChecked(True)
    c = d.c
    return quests.for_level(d.kb, c.level, c.base_class, c.job, c.quests_done)[mode]


@needs_kb
def test_quest_search_over_an_empty_list_says_the_list_is_empty(tools, monkeypatch):
    from maplehelper import quests
    d = tools("Thief", "Assassin", 32, page="quests")
    empty = {k: [] for k in ("now", "level", "missed", "soon", "later", "town")}
    monkeypatch.setattr(quests, "for_level", lambda *a, **k: dict(empty, done=0))
    d.q_search.setText("anything")
    d._fill_quests()
    assert "0 of 0" not in d.q_head.text() and "No quests here" in _texts(d.q_list)


@needs_kb
def test_crafting_header_follows_the_steppers_top(tools):
    # TL1-6: a saved Smithing 50 showed "at Smithing level 50" beside a stepper at the KB's top
    from maplehelper import crafting
    d = tools(page="crafting")
    d.profiles.active.crafts = {"smithing": 50}
    d.craft_mode.group.buttons()[1].setChecked(True)
    d._fill_crafting()
    top = crafting.max_level(d.kb, "smithing")
    assert d.craft_level.value() == top and f"level {top}" in d.craft_head.text().replace("⁨", "").replace("⁩", "")
    assert "level 50" not in d.craft_head.text()


def test_an_end_pressed_before_a_character_switch_still_ends_that_session(real_kb, tmp_path, monkeypatch, app):
    # TL1-10: the reply came after another character was picked: the End was lost, without a word
    from maplehelper import grind, store
    from maplehelper.grind import Reading
    from maplehelper.ui.grindrunner import GrindRunner
    monkeypatch.setattr(store.Profiles, "path", tmp_path / "profiles.json")
    monkeypatch.setattr(store.Settings, "path", tmp_path / "settings.json")
    monkeypatch.setattr(grind.Store, "path", tmp_path / "grind.json")
    p = store.Profiles()
    a, b = p.add("Kiwi", "Thief", "Assassin", 35), p.add("Lime", "Warrior", "Fighter", 30)
    p.set_active(a.id)
    r = GrindRunner(real_kb, p, store.Settings())
    r.store.start(a.id, Reading(time.time() - 600, 35, 10.0))
    r.pending, r.got = ("end", a.id, time.time(), ""), None
    p.set_active(b.id)
    r.take(True)
    assert not r.store.running(a.id) and "grind_end_no_read" in r.note
    r.pending, r.got = ("start", a.id, time.time(), ""), None
    r.take(True)
    assert r.note == ["exp_failed"] and not r.store.running(a.id)


@needs_kb
def test_another_character_drops_the_last_ones_picks_and_sell_verdicts(tools):
    # TL2-5 / TL2-14: B's session started on A's monster, and the sell list showed A's quest needs
    from maplehelper import store
    p = store.Profiles()
    a = p.add("Kiwi", "Thief", "Assassin", 35)
    b = p.add("Lime", "Warrior", "Fighter", 30)
    p.set_active(a.id)
    d = tools(None, profiles=p, page="exp")
    d._grind_choice = "Stirge"
    d._sell_verdicts = ["A's"]
    read = d._sell_read = 7
    d.profile_changed()                     # the same character read again: kept
    assert d._grind_choice == "Stirge"
    p.set_active(b.id)
    d.profile_changed()
    assert d._grind_choice == "" and d._sell_verdicts is None and d._sell_read != read
    d._sell_verdicts, read = ["B's"], d._sell_read
    d._leave("more")
    assert d._sell_verdicts is None and d._sell_read != read


@needs_kb
def test_price_card_names_the_item_and_folds_hyphens(tools):
    # TL2-7: "brown kitty" stayed the title; TL2-22: "Steely Throwing-Knives" found nothing
    d = tools(page="prices")
    d.price_input.setText("brown kitty")
    d._fill_prices()
    assert "Brown Kitty" in _texts(d.price_box) or "Brown Kitty" in d._price_for
    d.price_input.setText("Steely Throwing-Knives")
    d._fill_prices()
    assert d._price_for == "Steely Throwing Knives"


@needs_kb
def test_route_to_a_map_the_graph_doesnt_know_isnt_the_last_route(tools, monkeypatch):
    # TL2-8: the "To" box kept Sleepywood and its way showed under the new map
    d = tools(page="route")
    d.route_to.setText("Sleepywood")
    d._find_route()
    monkeypatch.setattr(d.route_graph, "of_key", lambda key: None)
    d.route_to_map("map/020000000")
    assert d.route_to.text() == "" and "Sleepywood" not in _texts(d.route_out)


@needs_kb
def test_sell_check_without_a_character_doesnt_take_a_screenshot(tools, monkeypatch):
    # TL2-13: the window hid for 1.5 s and took a shot before saying "no character"
    d = tools(None, page="more")
    monkeypatch.setattr(d, "_step_aside", lambda then: pytest.fail("stepped aside"))
    d._sell_check()
    assert d.sell_box.count()


@needs_kb
def test_big_numbers_never_read_1000_k(tools):
    # TL2-19: 999,960 was "1000.0K"
    d = tools(page="exp")
    assert "1.00M" in d._num(999_960) and "999.9K" in d._num(999_940) and "123.4K" in d._num(123_400)


def test_one_community_report_is_singular():
    # TL2-15
    from maplehelper.i18n import I18n
    for lang, word in (("en", "By 1 community report,"), ("he", "דיווח אחד")):
        assert word in I18n(lang)("grind_expected_one", n="5", k="10", r=1)


def test_market_cache_is_shared_safely_between_threads(monkeypatch):
    # TL2-10: the price card's and the sell check's threads both evicted and inserted with no lock
    import threading

    from maplehelper import market
    monkeypatch.setattr(market, "_item_cache", {})
    monkeypatch.setattr(market, "_get", lambda url, timeout: None)
    errors = []

    def run(start):
        try:
            for i in range(start, start + 400):
                market._lookup(i)
        except Exception as e:      # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=run, args=(n * 1000,)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and len(market._item_cache) <= market.CACHE_ITEMS


@needs_kb
@pytest.mark.parametrize("lang", ["en", "he"])
def test_grind_spot_and_farm_cards_fit_the_page(tools, app, lang):
    # UX-1 / TL2-2: four links in one row made each English monster card 561 px in a 514 px page; a farm drop line
    # (link + chips) was up to 518 px. UX-2: "Hit & damage" showed as "Hit _damage"
    from PySide6.QtWidgets import QPushButton

    from maplehelper.ui.tools import PAGES
    d = tools("Thief", "Assassin", 35, lang=lang, page="train")
    for width in (580, 480):
        d.resize(width, 800)
        for page in ("train", "farm"):
            d.show_page(PAGES.index(page))
            app.processEvents()
            area = d.pages[page]
            assert area.widget().minimumSizeHint().width() <= area.viewport().width(), (page, width)
    hit = [b for b in d.pages["train"].findChildren(QPushButton) if "damage" in b.text()]
    assert lang == "he" or (hit and all("&&" in b.text() for b in hit))


def test_enter_on_a_tabbed_button_clicks_that_button(app):
    # UX-14: Enter on a focused Back went forward; on Save (no Enter button) it did nothing
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QPushButton, QVBoxLayout

    from maplehelper.ui import glass
    d = glass.GlassDialog("t", False)
    lay = QVBoxLayout(d.content)
    clicked = []
    back, nxt = QPushButton("Back"), QPushButton("Next")
    back.clicked.connect(lambda: clicked.append("back"))
    nxt.clicked.connect(lambda: clicked.append("next"))
    lay.addWidget(back)
    lay.addWidget(nxt)
    d.enter_button = nxt
    d.show()
    d.activateWindow()
    app.processEvents()

    def enter():
        d.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    back.setFocus(Qt.OtherFocusReason)        # the window's own first focus: Enter is still Next
    app.processEvents()
    enter()
    assert clicked == ["next"]
    nxt.setFocus(Qt.OtherFocusReason)
    app.processEvents()
    glass._TabKeys._at = time.monotonic()      # a Tab the player pressed
    back.setFocus(Qt.TabFocusReason)
    app.processEvents()
    if not back.hasFocus():
        pytest.skip("no focus offscreen")
    enter()
    assert clicked == ["next", "back"]
    d.close()


def test_tour_counts_its_numbered_cards_from_one(app, isolated_store, kb):
    # UX-20: the first numbered card read "2 / 15"
    from maplehelper.ui.overlay import Overlay
    ov = Overlay(isolated_store.Settings(), isolated_store.Profiles(), kb, None)
    ov.resize(520, 760)
    ov.show()
    ov.start_tour()
    tr = ov._tour
    n = len(tr.steps) - 2
    tr.go(1)
    assert tr.count.text() == f"1 / {n}"
    tr.go(len(tr.steps) - 2)
    assert tr.count.text() == f"{n} / {n}"
    tr.finish()
    ov.hide()


def test_the_pin_under_an_answer_has_a_name(app):
    # UX-15: an icon-font glyph with a tooltip only: a screen reader read nothing
    from PySide6.QtWidgets import QToolButton

    from maplehelper.ui.widgets import Bubble
    b = Bubble("an answer", "assistant", False)
    b.add_pin(lambda: None, "Pin this answer")
    pins = [x for x in b.findChildren(QToolButton) if x.toolTip() == "Pin this answer"]
    assert pins and pins[0].accessibleName() == "Pin this answer"
