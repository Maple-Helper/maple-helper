"""Play tools, guides, history, wishlist and pins: the audit's tools-windows findings, one test each."""
import datetime
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from maplehelper import pins  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


def pump(ms=30):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def deleted_on_close(d):
    """Close the way app.open_window's windows close: deleted right after."""
    d.setAttribute(Qt.WA_DeleteOnClose)
    d.close()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    pump()


@pytest.fixture(scope="module")
def real_kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture
def tools(isolated_store, real_kb):
    """make(cls, job, level, page, lang) -> (ToolsDialog, character)."""
    from maplehelper.ui.tools import ToolsDialog
    made = []

    def make(base, job, level, page="train", lang="en"):
        p = isolated_store.Profiles()
        c = p.add("Kiwi", base, job, level)
        d = ToolsDialog(real_kb, p, isolated_store.Settings(), lang, "", {}, page)
        made.append(d)
        return d, c
    yield make
    for d in made:
        try:
            d.close()
        except RuntimeError:
            pass


def cards(layout):
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None and layout.itemAt(i).widget().objectName() == "Card"]


def more_button(layout):
    return next((w for i in range(layout.count()) if isinstance(w := layout.itemAt(i).widget(), QPushButton)), None)


# ------------------------------------------------------------------ Play tools

@needs_kb
def test_quest_lists_past_40_say_so_and_show_the_rest(tools):
    """The header counted every quest but only 40 cards were made, with nothing saying the rest were hidden."""
    from maplehelper import quests
    from maplehelper.ui.tools import MAX_QUESTS
    d, c = tools("Warrior", "Fighter", 20, "quests")
    rows = len(quests.for_level(d.kb, c.level, c.base_class, c.job, c.quests_done)["now"])
    assert rows > MAX_QUESTS
    assert len(cards(d.q_list)) == MAX_QUESTS
    btn = more_button(d.q_list)
    hint = [w.text() for w in d.findChildren(QLabel, "RowHint") if f"40 of {rows} quests" in w.text()]
    assert btn is not None and btn.text() == "Show more quests" and hint
    btn.click()
    assert len(cards(d.q_list)) == min(rows, 2 * MAX_QUESTS)
    d._fill_quests(new_list=True)                     # another list starts at its first 40 again
    assert d._q_limit == MAX_QUESTS


@needs_kb
def test_citizenship_list_past_40_has_more_too(tools):
    from maplehelper import quests
    from maplehelper.ui.tools import MAX_QUESTS
    d, c = tools("Bowman", "Hunter", 50, "town")
    c.town = "Henesys"
    d.refresh("town")
    rows = quests.citizenship(d.kb, "Henesys", 50, c.quests_done)
    assert len(rows) > MAX_QUESTS and len(cards(d.town_list)) == MAX_QUESTS
    more_button(d.town_list).click()
    assert len(cards(d.town_list)) == len(rows) and more_button(d.town_list) is None


@needs_kb
def test_opening_on_the_build_page_keeps_the_other_pages_for_later(tools):
    """_skill_icons' hasattr("_skills") went through __getattr__, which built all eight other pages at once."""
    d, _ = tools("Thief", "Assassin", 34, "build")
    assert d._skill_icons()                        # the build tables have their icons
    assert len(d._pending) == 8
    end = time.time() + 10
    while d._pending and time.time() < end:
        pump()
    assert not d._pending                          # and the rest still come right after
    assert d.q_list is not None                    # a public widget of an unbuilt page is still built on demand


@needs_kb
def test_done_toggle_closes_when_the_last_done_quest_is_undone(tools):
    from maplehelper import quests
    d, c = tools("Thief", "Assassin", 34, "quests")
    key = quests.for_level(d.kb, 34, "Thief", "Assassin", [])["now"][0].key
    d._quest_done(key)
    d.q_done_toggle.setChecked(True)
    d._quest_undo(key)
    assert not d.q_done_toggle.isChecked() and d.q_done_toggle.isHidden()
    d._quest_done(key)                              # the next one marked done doesn't open the list on its own
    assert not d.q_done_toggle.isChecked() and cards(d.q_done) == []


@needs_kb
def test_tab_switch_back_keeps_the_quest_cards(tools):
    """Every switch to Quests / Citizenship rebuilt up to 40 cards (~0.3 s) though nothing had changed."""
    from maplehelper.ui.tools import PAGES
    d, c = tools("Thief", "Assassin", 34, "quests")
    first = cards(d.q_list)[0]
    d.show_page(PAGES.index("train"))
    d.show_page(PAGES.index("quests"))
    assert cards(d.q_list)[0] is first
    c.level = 35                                    # the character changed: the page follows
    d.show_page(PAGES.index("train"))
    d.show_page(PAGES.index("quests"))
    assert cards(d.q_list)[0] is not first
    d.show_page(PAGES.index("town"))
    town_first = cards(d.town_list)[0]
    d.show_page(PAGES.index("train"))
    d.show_page(PAGES.index("town"))
    assert cards(d.town_list)[0] is town_first


@needs_kb
def test_closing_during_a_free_market_lookup_logs_nothing(tools, monkeypatch):
    from maplehelper import market
    release, errors = threading.Event(), []
    monkeypatch.setattr(market, "free_market", lambda n: release.wait(5) and None)
    monkeypatch.setattr(market, "item_market", lambda i: release.wait(5) and None)
    monkeypatch.setattr(threading, "excepthook", lambda a: errors.append(a.exc_value))
    d, _ = tools("Thief", "Assassin", 34, "prices")
    before = set(threading.enumerate())
    d.price_input.setText("Red Potion")
    d._fill_prices()
    worker = next(iter(set(threading.enumerate()) - before))
    deleted_on_close(d)
    release.set()
    worker.join(5)
    assert errors == []


@needs_kb
def test_pickers_offer_only_what_the_kb_says_is_in_the_game(tools, real_kb):
    from maplehelper import availability
    from maplehelper.ui.tools import map_rows, monster_rows
    maps = map_rows(real_kb)
    assert maps and not [s for s, _, _ in maps if "Ossyria" in s]
    for bad in ("Dead Mine I", "Wolf Territory I", "Cloud Park I", "The Door to Zakum"):
        assert bad not in {n for _, n, _ in maps}
    names = {n for _, n, _ in monster_rows(real_kb)}
    assert "Green Mushroom" in names
    assert not names & {"Star Pixie", "Hector", "White Fang", "Ratz", "Crimson Balrog", "Jr. Sentinel"}
    # typed by hand, the calculator doesn't find them either
    d, _ = tools("Thief", "Assassin", 34, "calc")
    d.calc_input.setText("Star Pixie")
    assert d._calc_monster() is None
    d.calc_input.setText("Green Mushroom")
    assert d._calc_monster().name == "Green Mushroom"
    assert availability.of(real_kb).known


# ------------------------------------------------------------------ wishlist

@needs_kb
def test_wishlist_shows_only_droppers_and_maps_in_the_game(real_kb):
    from maplehelper.ui.wishlist import WishlistDialog
    # One-Handed Axe Attack Scroll: Greater listed Tick-Tock and Crimson Balrog ("No map data"); Stiff Feather a
    # Jr. Cellion at "Garden of Red I · Orbis"
    items = ["item/2", real_kb._item_by_name["stiff feather"]]
    d = WishlistDialog(items, real_kb, "en", "")
    texts = [lb.text() for lb in d.findChildren(QLabel)]
    assert not [x for x in texts if "Crimson Balrog" in x or "Tick-Tock" in x]
    assert not [x for x in texts if "Orbis" in x or "El Nath" in x]
    assert any("Green Mushroom" in x for x in texts)
    assert sum("reference data" in x for x in texts) == 2          # the MSEA reference caveat under each list
    d.close()


# ------------------------------------------------------------------ guides

GUIDES = {f"guide/{slug}": {"category": "guide", "name": name} for slug, name in (
    ("thief-class-guide", "Thief Class Guide"), ("assassin-class-guide", "Assassin Class Guide"),
    ("kerning-city-party-quest-kpq-guide", "Kerning City Party Quest (KPQ) Guide"))}


def _fake_kb():
    return SimpleNamespace(entities=GUIDES, page=lambda k: "", get=GUIDES.get, picture=lambda k: None)


def test_zoom_popup_goes_with_its_window():
    from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

    from maplehelper.ui.guides import ImageZoom
    dlg = QDialog()
    QVBoxLayout(dlg).addWidget(br := QTextBrowser())
    zoom = ImageZoom(br)
    dlg.show()
    pump()
    img = Path(os.environ["APPDATA"]) / "zoom-test.png"
    QImage(10, 10, QImage.Format_RGB32).save(str(img))
    zoom.show(str(img))
    pop = zoom.pop
    assert pop.isVisible()
    tops = lambda: [w for w in QApplication.topLevelWidgets() if w is pop]  # noqa: E731
    deleted_on_close(dlg)
    assert not tops()                    # hidden at once and deleted, not left floating over the game


def test_guide_rows_open_from_the_keyboard():
    from maplehelper.ui.guides import GuideRow, GuidesDialog
    d = GuidesDialog(_fake_kb(), None, "en", "")
    d.show()
    opened = []
    d.open_guide = opened.append           # rows connect to it when made: made again below
    next(b for b in d.cats.buttons() if b.property("cat") == "classes").click()
    rows = d.findChildren(GuideRow)
    assert rows and rows[0].focusPolicy() & Qt.TabFocus and rows[0].accessibleName()
    rows[0].setFocus()
    QTest.keyClick(rows[0], Qt.Key_Return)
    QTest.keyClick(rows[0], Qt.Key_Space)
    assert opened == [rows[0].key, rows[0].key]
    d.search.setText("guide")
    d.search.returnPressed.emit()          # Enter in the search box opens the top result
    assert len(opened) == 3
    d.close()


def test_hebrew_names_find_guides():
    from maplehelper.ui.guides import GuidesDialog
    kb = _fake_kb()
    kb.resolve_names = lambda q: q.replace("קרנינג", "Kerning City")
    d = GuidesDialog(kb, None, "he", "")
    d.search.setText("קרנינג")
    assert d._queries("קרנינג") == ["קרנינג", "kerning city"]
    from maplehelper.ui.guides import GuideRow
    keys = [r.key for r in d.findChildren(GuideRow) if not r.isHidden()]
    assert "guide/kerning-city-party-quest-kpq-guide" in keys
    d.close()


@needs_kb
def test_hebrew_guide_search_with_the_real_aliases(real_kb):
    from maplehelper.ui.guides import GuideRow, GuidesDialog
    d = GuidesDialog(real_kb, None, "he", "")
    d.search.setText("קרנינג")
    assert [r for r in d.findChildren(GuideRow) if not r.isHidden()]
    d.close()


def test_a_link_inside_a_guide_and_back_returns_to_the_place():
    from maplehelper.ui.guides import GuidesDialog
    d = GuidesDialog(_fake_kb(), None, "en", "")
    d.resize(560, 700)
    d.show()
    d.open_guide("guide/thief-class-guide")
    pump()
    bar = d.browser.verticalScrollBar()
    bar.setValue(min(1500, bar.maximum()))
    at = bar.value()
    assert at > 0
    d._on_link(QUrl("guide:assassin-class-guide"))
    assert d._reading == "guide/assassin-class-guide" and "Back to" in d.back.text()
    QTest.keyClick(d, Qt.Key_Escape)        # Esc (like Back) returns to the Thief guide where it was
    pump()
    assert d._reading == "guide/thief-class-guide" and d.stack.currentIndex() == 1 and bar.value() == at
    assert "All guides" in d.back.text()
    d.back.click()                          # then to the list
    assert d.stack.currentIndex() == 0
    d.close()


def test_wide_guide_tables_never_break_inside_a_word():
    """At the window's width the DPS table read "341,7" / "82" for one number, "Savag" / "e Blow"."""
    from maplehelper.ui.guides import GuidesDialog
    d = GuidesDialog(_fake_kb(), None, "en", "")
    d.resize(560, 760)
    d.show()
    d.open_guide("guide/class-dps-rankings")
    pump()
    doc, broken = d.browser.document(), []
    blk = doc.begin()
    while blk.isValid():
        lay, txt = blk.layout(), blk.text()
        for i in range(1, lay.lineCount()):
            st = lay.lineAt(i).textStart()
            if 0 < st < len(txt) and txt[st - 1].isalnum() and txt[st].isalnum():
                broken.append(txt[st - 6:st + 4])
        blk = blk.next()
    assert broken == []
    # a smaller font makes the table fit the window: no sideways bar, no column cut off
    assert doc.size().width() <= d.browser.viewport().width() + 2
    assert d.browser.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    d.close()


# ------------------------------------------------------------------ history

def _pairs(n, now=None):
    now = now or time.time()
    return [{"q": f"question {i}", "a": f"answer {i}", "t": now - i * 600} for i in reversed(range(n))]   # oldest first


def test_history_show_more_adds_cards_without_remaking_the_shown_ones():
    from maplehelper.ui.pinsview import HistoryDialog
    d = HistoryDialog(_pairs(200), "Elipaz", "en", "")
    first = cards(d.rows)[0]
    d.more_btn.click()
    now = cards(d.rows)
    assert len(now) == 160 and now[0] is first
    heads = [d.rows.itemAt(i).widget().text() for i in range(d.rows.count())
             if isinstance(d.rows.itemAt(i).widget(), QLabel) and d.rows.itemAt(i).widget().objectName() == "SectionHeader"]
    assert len(heads) == len(set(heads))                  # a day carried on under its header, not a second one
    d.more_btn.click()
    assert len(cards(d.rows)) == 200 and "200 results" in d.count.text()
    assert not [w for w in d.findChildren(QPushButton) if w.text().startswith("Show") and w.isVisibleTo(d)]
    d.close()


def test_history_card_opens_from_the_keyboard_and_makes_its_answer_then():
    from maplehelper.ui.pinsview import HistoryDialog
    d = HistoryDialog(_pairs(3), "Elipaz", "en", "")
    d.show()
    card = cards(d.rows)[0]
    assert not card.findChildren(QLabel, "PinAnswer")            # the whole answer waits for the first tap
    assert card.focusPolicy() & Qt.TabFocus and card.accessibleName() == "question 0"
    card.setFocus()
    QTest.keyClick(card, Qt.Key_Return)
    go = [b for b in card.findChildren(QPushButton) if b.text() == "Continue in chat"]
    assert go and go[0].isVisibleTo(d)
    got = []
    d.continue_requested.connect(lambda q, a, k: got.append(q))
    go[0].click()
    assert got == ["question 0"]
    QTest.keyClick(card, Qt.Key_Space)                           # and closes again
    assert not go[0].isVisibleTo(d)
    d.close()


def test_english_in_the_hebrew_history_is_laid_out_as_itself():
    """Wrapped as an isolate in a right-to-left paragraph, a nearly full line stuck out a space on the left and its
    first letter was cut. Rendered with a margin: no ink left of the text's own box."""
    from maplehelper import bidi
    from maplehelper.ui.pinsview import set_wrapped_name
    words = "Mano drops a shell and Red Potion when you hit the snail at the edge of the map".split()
    margin = 8

    def overflows(set_text) -> int:
        out = 0
        for n in range(40):
            text = " ".join(words[n % 7:] + words[:n % 7])
            lb = QLabel()
            lb.setStyleSheet("background: white; color: black;")
            lb.setWordWrap(True)
            lb.setContentsMargins(margin, 0, margin, 0)
            lb.setAlignment(Qt.AlignRight | Qt.AlignAbsolute)
            lb.setLayoutDirection(Qt.RightToLeft)
            set_text(lb, text)
            lb.setFixedWidth(150 + n * 3)
            lb.resize(lb.width(), lb.heightForWidth(lb.width()))
            img = lb.grab().toImage()
            ink = [x for x in range(margin - 1) for y in range(img.height()) if img.pixelColor(x, y).value() < 160]
            out += bool(ink)
        return out
    assert overflows(lambda lb, text: lb.setText(bidi.ltr_name(text, True))) > 0      # the old way did
    assert overflows(lambda lb, text: set_wrapped_name(lb, text, True)) == 0
    lb = QLabel()
    set_wrapped_name(lb, "Mano drops a shell", True)
    assert lb.layoutDirection() == Qt.LeftToRight and lb.text() == "Mano drops a shell"
    hebrew = QLabel()
    set_wrapped_name(hebrew, "מה מפיל מאנו?", True)
    assert hebrew.layoutDirection() == Qt.RightToLeft


@needs_kb
def test_history_pictures_skip_guide_banners(real_kb):
    from maplehelper.ui.pinsview import HistoryDialog
    d = HistoryDialog([], "Elipaz", "en", "", kb=real_kb)
    mob = real_kb._monster_by_name["green mushroom"] if hasattr(real_kb, "_monster_by_name") else "monster/13"
    row = d._pictures([mob, "guide/what-is-maplestory-classic-worlds"])
    assert row is not None and len(row.findChildren(QLabel)) == 1
    assert d._pictures(["guide/what-is-maplestory-classic-worlds"]) is None
    d.close()


def test_yesterday_follows_the_calendar_across_a_clock_change(monkeypatch):
    """Israel, clocks forward Fri 27 Mar 2026 02:00 (+2 -> +3): at 28 Mar 00:30 "now minus 24 h" was 26 Mar."""
    from maplehelper.ui import pinsview
    switch = datetime.datetime(2026, 3, 27, 0, 0, tzinfo=datetime.timezone.utc).timestamp()

    class IsraelDate(datetime.date):
        @classmethod
        def fromtimestamp(cls, ts):
            local = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) + datetime.timedelta(
                hours=3 if ts >= switch else 2)
            return cls(local.year, local.month, local.day)

    def local_ts(y, m, d, hh, mm):
        naive = datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.timezone.utc).timestamp()
        return naive - (3 if naive - 3 * 3600 >= switch else 2) * 3600

    monkeypatch.setattr(pinsview, "dt", SimpleNamespace(date=IsraelDate, timedelta=datetime.timedelta))
    now = local_ts(2026, 3, 28, 0, 30)
    monkeypatch.setattr(pinsview.time, "time", lambda: now)
    d = pinsview.HistoryDialog([], "Elipaz", "en", "")
    assert d._day(local_ts(2026, 3, 27, 10, 0)) == "Yesterday"
    assert d._day(local_ts(2026, 3, 26, 23, 45)) == "26.03.2026"
    assert d._day(local_ts(2026, 3, 28, 0, 10)) == "Today"
    d.close()


def test_history_search_matches_only_what_the_cards_show():
    pairs = [{"q": "[about Mano] what does it drop?", "a": "**Mano** drops a Snail Shell", "t": 1},
             {"q": "where to train", "a": "Ant Tunnel", "t": 2}]
    assert pins.search(pairs, "about") == [] and pins.search(pairs, "[about") == []
    assert pins.search(pairs, "**") == []
    assert pins.search(pairs, "mano drops") == [pairs[0]]       # the name is still found in the answer
    assert pins.search(pairs, "drop") == [pairs[0]]


@needs_kb
def test_share_card_ellipsizes_a_long_name_and_wraps_a_long_map(isolated_store, real_kb):
    from maplehelper.i18n import I18n
    from maplehelper.ui.pinsview import character_card_image
    p = isolated_store.Profiles()
    short = p.add("Kiwi", "Thief", "Assassin", 34)
    short.map = "Ant Tunnel I"
    long = p.add("ElipazTheVeryLongNameXY", "Thief", "Assassin", 34)
    long.map = "Physical Fitness Test <Normal Waiting Room>"
    from maplehelper.ui import theme
    app.setStyleSheet(theme.stylesheet(theme.load_fonts(), 14))      # the card's real fonts (bold, 22 px name)
    labels = []
    orig = QLabel.setText

    def spy(self, text):
        labels.append((self.objectName(), text, self.wordWrap()))
        orig(self, text)
    QLabel.setText = spy
    try:
        a = character_card_image(short, None, real_kb, None, I18n("en"))
        b = character_card_image(long, None, real_kb, None, I18n("en"))
    finally:
        QLabel.setText = orig
        app.setStyleSheet("")
    names = [t for o, t, _ in labels if o == "ShareName"]
    assert "Kiwi" in names and any(t.endswith("…") and t.startswith("ElipazTheVery") for t in names)
    assert a.width() == b.width() and b.height() >= a.height()        # the long map took a second line
