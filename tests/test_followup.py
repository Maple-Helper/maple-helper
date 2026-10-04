"""Follow-up fixes after the audit merge (offscreen Qt): quest pre-requisites and gender rewards on the quest card,
the COT2 label beside shop prices, a quest marked done leaving the active quests, keyboard and accessible names."""
import os
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from maplehelper import market, quests, quick  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402

app = QApplication.instance() or QApplication([])


def pump(ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def tools(isolated_store, kb):
    from maplehelper.ui.tools import ToolsDialog
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    p.add("Elipaz", "Thief", "Assassin", 32)
    made = []

    def make(lang="en", page="quests"):
        d = ToolsDialog(kb, p, s, lang, "", {}, page)
        made.append(d)
        return d
    yield make
    for d in made:
        d.close()


def texts(w) -> str:
    """What the labels read, without their markup (a term's "?" link splits "10 Fame" in the HTML)."""
    from PySide6.QtGui import QTextDocument
    out = []
    for lb in w.findChildren(QLabel):
        doc = QTextDocument()
        doc.setHtml(lb.text())
        out.append(doc.toPlainText())
    return "\n".join(out)


# ------------------------------------------------------------------ 1: the quest card

def test_quest_card_shows_prerequisites_and_gender_rewards(tools):
    d = tools()
    q = quests.Quest("quest/1", "Sauna Day", 20, min_fame=10, accept_cost=1000, notes=["Must not already have: X"],
                     gender_rewards={"Male": ["Blue Sauna Robe x 1"], "Female": ["Red Sauna Robe x 1"]})
    shown = texts(d._quest_card(q))
    assert "Needs at least 10 Fame" in shown and "Costs 1,000 mesos" in shown
    assert "Must not already have: X" in shown
    assert "Depends on your character's gender:" in shown
    shown = re.sub("[\u200e\u200f\u202a-\u202e]", "", shown)
    # each with its picture and count, the gender after it
    assert "Blue Sauna Robe x1 (male character)" in shown and "Red Sauna Robe x1 (female character)" in shown


# ------------------------------------------------------------------ 2: COT2 prices are labelled where they show

def test_cot2_label_beside_the_shop_price(tools, kb, monkeypatch):
    from maplehelper import combat
    shop = ("Mia", "Henesys · Potion Shop", 50)
    prices = market.NpcPrices(None, shops=[shop], labels={shop[:2]: "COT2"})
    monkeypatch.setattr(market, "npc_prices", lambda *a: prices)
    monkeypatch.setattr(combat, "released", lambda *a: True)
    monkeypatch.setattr(market, "free_market", lambda n: None)
    d = tools(page="prices")
    d.price_input.setText("Red Potion")
    d._fill_prices()
    # the price's own label is its chip, at the line's start, the test's meaning in its tooltip
    assert "(cheapest at Mia, Potion Shop)" in texts(d.pages["prices"])
    assert [c.text() for c in d.price_chips] == ["COT2"] and "second closed test" in d.price_chips[0].toolTip()
    assert d.fm_chip.text() == "Community"
    a = quick.answer("where to buy red potion", kb, I18n("en"))
    assert a and "Mia · Henesys · Potion Shop · 50 mesos (COT2 test price)" in a.text
    # a price the KB doesn't label stays plain
    prices.labels.clear()
    a = quick.answer("where to buy red potion", kb, I18n("en"))
    assert a and "COT2" not in a.text


# ------------------------------------------------------------------ 10: done in Play tools, done for the chat

def test_quest_marked_done_leaves_the_active_quests(tools, isolated_store):
    d = tools()
    c = d.c
    c.active_quests = ["mai's first  training", "Another Quest"]
    d._quest_done("quest/1000")              # "Mai's First Training" in the fixture KB
    assert c.quests_done == ["quest/1000"] and c.active_quests == ["Another Quest"]
    assert isolated_store.Profiles().active.active_quests == ["Another Quest"]      # saved


# ------------------------------------------------------------------ 3: the glossary "?" from the keyboard

def test_term_links_open_from_the_keyboard(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

    from maplehelper import glossary
    from maplehelper.ui import terms
    monkeypatch.setattr(terms, "LANG", "en")
    monkeypatch.setattr(terms, "_popup", None)            # a card a Hebrew window before left behind
    opened = []
    monkeypatch.setattr(terms, "show", lambda link, lang, near=None: opened.append((link, near is not None)))
    from PySide6.QtWidgets import QApplication
    for other in QApplication.topLevelWidgets():         # windows earlier tests left open took the focus
        other.hide()
    w = QWidget()
    w.setLayoutDirection(Qt.LeftToRight)              # an English window, whatever a Hebrew one before it set
    QApplication.setLayoutDirection(Qt.LeftToRight)
    lay = QVBoxLayout(w)
    before, after = QLineEdit(), QLineEdit()
    lb = terms.watch(QLabel(glossary.annotate("Your HP and ACC", "en")))
    plain = terms.watch(QLabel("No terms here"))
    for x in (before, lb, plain, after):
        lay.addWidget(x)
    w.show()
    # its own window active: in a full run another test's window still held it, and Tab moved nothing
    w.raise_()
    w.activateWindow()
    QTest.qWaitForWindowActive(w, 1000)
    pump()
    assert plain.focusPolicy() == Qt.NoFocus              # nothing to open: not a Tab stop
    assert "Your HP and ACC" in lb.accessibleName() and "HP, ACC" in lb.accessibleDescription()
    before.setFocus()
    pump()
    QTest.keyClick(before, Qt.Key_Tab)
    pump()
    assert lb.hasFocus()
    QTest.keyClick(lb, Qt.Key_Return)                     # the first "?" right away, no empty stop before it
    QTest.keyClick(lb, Qt.Key_Tab)
    QTest.keyClick(lb, Qt.Key_Space)
    assert opened == [("g:HP", True), ("g:ACC", True)]
    QTest.keyClick(lb, Qt.Key_Tab)
    pump()
    assert after.hasFocus()                               # out of the label, past the one without a "?"
    lb.setText("plain now")
    pump()
    assert lb.focusPolicy() == Qt.NoFocus
    w.close()


# ------------------------------------------------------------------ 5: names for screen readers, the tip strip

def test_search_boxes_and_close_have_names(tools):
    from maplehelper.ui.glass import GlassDialog
    d = tools(page="quests")
    assert d.q_search.accessibleName() == I18n("en")("q_search")
    for name in ("calc_input", "price_input"):
        assert getattr(d, name).accessibleName(), name
    assert d.close_btn.accessibleName() == "Close"
    assert GlassDialog("x", rtl=True).close_btn.accessibleName() == I18n("he")("close")


def test_tip_strip_asks_from_the_keyboard():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from maplehelper import plan
    from maplehelper.ui.plancard import TipStrip
    strip = TipStrip()
    tip = plan.Tip(kind="k", key="tip_hide", args={}, question="What now?")
    strip.show_tip(tip, I18n("en"), rtl=False)
    asked = []
    strip.asked.connect(asked.append)
    assert strip.focusPolicy() & Qt.TabFocus and strip.accessibleName() == "Hide until the next level"
    QTest.keyClick(strip, Qt.Key_Return)
    QTest.keyClick(strip, Qt.Key_Space)
    assert asked == ["What now?", "What now?"]


# ------------------------------------------------------------------ 9: a wide guide table fits by its padding

def test_wide_table_gives_up_cell_padding_before_it_scrolls(monkeypatch):
    from PySide6.QtGui import QTextTable
    from PySide6.QtWidgets import QTextBrowser

    from maplehelper.ui import guides as gui
    words = " ".join(f"<td>Doombringer{i}</td>" for i in range(8))
    html = f"<table cellpadding='5' border='1' cellspacing='0'><tr>{words}</tr></table>"
    made = []

    def fitted(room: int) -> tuple[float, QTextBrowser]:
        """The table's width after fit_tables in a view with this much room."""
        b = QTextBrowser()
        made.append(b)
        b.setStyleSheet("font-size: 14px;")
        b.setHtml(html)
        b.show()
        pump()
        b.resize(room + int(2 * b.document().documentMargin()) + b.width() - b.viewport().width(), 200)
        pump()
        gui.fit_tables(b)
        table = next(f for f in b.document().rootFrame().childFrames() if isinstance(f, QTextTable))
        return b.document().documentLayout().frameBoundingRect(table).width(), b

    # the least the table can be: at the smallest font with the guides' padding, and with the least padding
    narrow, _ = fitted(60)
    with monkeypatch.context() as m:
        m.setattr(gui, "TABLE_MIN_PAD", 5)
        wide, _ = fitted(60)
    assert narrow < wide - 20
    room = int((wide + narrow) / 2)
    width, b = fitted(room)
    assert width <= room + 1 and b.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    for b in made:
        b.close()


# ------------------------------------------------------------------ 11: a dropped dialog goes at once, not at a GC

def test_a_dropped_glass_dialog_is_freed_without_the_garbage_collector():
    # its backdrop and title bar kept the window: a cycle, so the window and its widgets went whenever Python's
    # garbage collector next ran, deleting them under the code using them then
    import gc

    from maplehelper.ui.glass import GlassDialog
    gone = []
    gc.disable()
    try:
        d = GlassDialog("x", rtl=False)
        d.content.destroyed.connect(lambda *_: gone.append(True))
        assert d.backdrop.widget is d
        del d
        app.processEvents()
        assert gone == [True]
    finally:
        gc.enable()


def test_chat_input_has_a_name_in_both_languages(isolated_store, kb, monkeypatch):
    from PySide6.QtCore import QRect

    from maplehelper import osapi
    from maplehelper.ui import terms
    from maplehelper.ui.overlay import Overlay
    for name in ("float_over_fullscreen", "activate_self", "focus_window"):
        monkeypatch.setattr(osapi, name, lambda *a: None)
    monkeypatch.setattr(osapi, "find_game_window", lambda: None)
    monkeypatch.setattr(terms, "LANG", terms.LANG)
    s, p = isolated_store.Settings(), isolated_store.Profiles()
    s["language"] = "he"
    p.set_active(p.add("Elipaz", "Thief", "Assassin", 32).id)
    ov = Overlay(s, p, kb, None)
    ov.setGeometry(QRect(-3000, -3000, 460, 700))
    try:
        assert ov.input.accessibleName() == I18n("he")("input_a11y")
        s["language"] = "en"
        ov.apply_language()
        assert ov.input.accessibleName() == "Your question"
    finally:
        ov.bubble.hide()
        ov.deleteLater()
