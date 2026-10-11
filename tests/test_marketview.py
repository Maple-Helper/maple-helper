"""The Free Market details window (ui/marketview.py) and the Free Market page (ui/marketpage.py): MesoWatch's item card
laid out in the app, and every item on the market by tab, the pets a tab of it."""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from maplehelper import mesowatch  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


class FakeKB:
    root = None

    def __init__(self, items):
        self.entities = {k: {"name": n, "url": ""} for k, n in items.items()}

    def get(self, key):
        return self.entities.get(key)

    def picture(self, key):
        return None

    def page(self, key):
        return ""


KB = FakeKB({"item/3198": "Black Cat Beanie", "item/414": "Iron Ore", "item/1": "Work Gloves"})


def hat(now):
    return mesowatch.Sales(
        1007145, "Black Cat Beanie", 229000, 99999, 265000, 19, False, 299999, mw_id="cash:01007145", asks=111,
        listing_shops=62, days=4, last3=299999, cash=True, cash_on_sale=False, category="Cash Shop",
        shops=[mesowatch.Shop("", 499999, seen=now - 1800, seller="Countdown"),
               mesowatch.Shop("Free Market 1", 399999, seen=now - 3600, seller="Tagged")],
        recent=[mesowatch.Sale(250000, 1, "", now - 6 * 3600, "Space Ghost"),
                mesowatch.Sale(349999, 1, "Free Market 2", now - 7 * 3600, "ShadowBlade")],
        history=[mesowatch.Day("2026-10-08", 74999, 50000, 75000, 5), mesowatch.Day("2026-10-09", 199999, 199999, 221750, 6),
                 mesowatch.Day("2026-10-10", 299999, 265000, 349999, 7)])


def market(now):
    items = {1007145: hat(now),
             4010001: mesowatch.Sales(4010001, "Iron Ore", 375, 299, 499, 2243, False, category="Etc",
                                      history=[mesowatch.Day("2026-10-06", 499, sold=9)]),
             1082002: mesowatch.Sales(1082002, "Work Gloves", 2222, sold=556, low_data=False, category="Equips",
                                      history=[mesowatch.Day(time.strftime("%Y-%m-%d", time.localtime(now)), 2222, sold=3)]),
             1302000: mesowatch.Sales(1302000, "Sword", 900, sold=12, low_data=True, category="Equips")}
    return mesowatch.Snapshot("Windia", now - 600, items,
                              by_name={"black cat beanie": 1007145, "iron ore": 4010001, "work gloves": 1082002})


@pytest.mark.parametrize("lang", ["en", "he"])
def test_the_details_window_shows_mesowatchs_card(app, monkeypatch, lang):
    from PySide6.QtWidgets import QLabel

    from maplehelper.ui.marketview import MarketDetailsDialog
    now = time.time()
    snap = market(now)
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: snap)
    monkeypatch.setattr(mesowatch, "face_file", lambda snap_, who, timeout=10: None)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    d = MarketDetailsDialog(KB, lang, "", None, None)
    d.show_item("item/3198")
    texts = " ".join(w.text() for w in d.findChildren(QLabel))
    for want in ("229,000", "250,000", "299,999", "111", "Countdown", "Tagged", "Space Ghost"):
        assert want in texts
    # where to buy: cheapest first, then the newest sales on the other tab
    names = [w.text() for w in d.buy_list.findChildren(QLabel, "MwRowName")]
    assert "Tagged" in names[0] and "Countdown" in names[1]
    assert d.buy_list.isVisibleTo(d) and not d.sold_list.isVisibleTo(d)
    d.tabs.group.buttons()[1].click()
    assert d.sold_list.isVisibleTo(d) and not d.buy_list.isVisibleTo(d)
    assert not hasattr(d, "chart")                       # no charts (the owner)
    d.site.click()
    assert opened == ["https://meso.watch/?item=cash:01007145"]
    d.close()


def test_a_sellers_picture_replaces_the_initial(app, monkeypatch, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from maplehelper.ui.marketview import MarketDetailsDialog
    now = time.time()
    snap = market(now)
    face = tmp_path / "f.png"
    img = QImage(34, 66, QImage.Format_ARGB32)
    img.fill(QColor("#336699"))
    img.save(str(face))
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: snap)
    monkeypatch.setattr(mesowatch, "face_file", lambda snap_, who, timeout=10: face if who == "Tagged" else None)
    d = MarketDetailsDialog(KB, "en", "", None, None)
    d.show_item("item/3198")
    d._on_face("Tagged", face)
    tagged = d._avatars["Tagged"][0]
    assert tagged.text() == "" and not tagged.pixmap().isNull()
    assert d._avatars["Countdown"][0].text() == "C"
    d.close()


def test_an_item_nobody_sold_says_so(app, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from maplehelper.ui.marketview import MarketDetailsDialog
    snap = mesowatch.Snapshot("Windia", time.time(), {})
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: snap)
    d = MarketDetailsDialog(KB, "en", "", None, None)
    d.show_item("item/414")
    note = [w for w in d.findChildren(QLabel) if "hasn't seen" in w.text()]
    assert note and "**" not in note[0].text() and note[0].objectName() == "DialogBody"      # plain, body size
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: None)
    d.show_item("item/414")
    assert any("couldn't reach" in w.text().lower() for w in d.findChildren(QLabel))
    d.close()


def pump(app, until, ms=3000):
    end = time.time() + ms / 1000
    while not until() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.mark.parametrize("lang", ["en", "he"])
def test_the_free_market_page_lists_every_item_by_tab(app, monkeypatch, lang):
    from PySide6.QtWidgets import QLabel, QWidget

    from maplehelper.ui.marketpage import ItemRow, MarketPage
    from maplehelper.ui.widgets import MARKET_REQUESTS
    now = time.time()
    snap = market(now)
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: snap)
    pets = QWidget()
    page = MarketPage(KB, __import__("maplehelper.i18n", fromlist=["I18n"]).I18n(lang), pets=pets)
    pump(app, lambda: page.snap is not None)
    assert "2" in page.tab_buttons["Equips"].text() and "1" in page.tab_buttons["Cash Shop"].text()

    def rows():
        return [page.list.itemAt(i).widget() for i in range(page.list.count())
                if isinstance(page.list.itemAt(i).widget(), ItemRow)]
    # Equips: best selling first; the new one tagged, the thin one warned
    assert [r.key for r in rows()] == ["item/1", None]
    first = rows()[0]
    assert first.findChildren(QLabel, "TagAccent") and rows()[1].findChildren(QLabel, "TagWarn")
    page.sort.group.buttons()[1].click()                  # cheapest
    assert [r.key for r in rows()] == [None, "item/1"]
    page.search.setText("glove")
    assert [r.key for r in rows()] == ["item/1"]
    page.search.clear()
    page.pick("Cash Shop")
    (row,) = rows()
    assert any("Cash" in w.text() and "Shop" in w.text() for w in row.findChildren(QLabel))   # bidi marks between
    asked = []
    MARKET_REQUESTS.requested.connect(asked.append)
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    row.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.LeftButton,
                                    Qt.LeftButton, Qt.NoModifier))
    MARKET_REQUESTS.requested.disconnect(asked.append)
    assert asked == ["item/3198"]
    page.pick("pets")
    assert page.stack.currentWidget() is pets
    # a newer market (the app's 15-minute refresh): the page reads it again
    loads = []
    monkeypatch.setattr(page, "load", lambda: loads.append(1))
    monkeypatch.setattr(mesowatch, "version", lambda: page._version + 1)
    page._poll()
    assert loads == [1]
    page.timer.stop()


def test_a_long_description_is_cut_at_a_word():
    from maplehelper.ui.marketpage import shorten
    text = "word " * 40
    cut = shorten(text, 30)
    assert cut.endswith("…") and len(cut) <= 31 and not cut[:-1].endswith(" ")
    assert shorten("short") == "short"


def test_an_open_window_follows_a_newer_market(app, monkeypatch):
    from maplehelper.ui.marketview import MarketDetailsDialog
    now = time.time()
    snap = market(now)
    monkeypatch.setattr(mesowatch, "snapshot", lambda refresh=True, timeout=30: snap)
    monkeypatch.setattr(mesowatch, "face_file", lambda snap_, who, timeout=10: None)
    d = MarketDetailsDialog(KB, "en", "", None, None)
    d.show_item("item/3198")
    d.tabs.group.buttons()[1].click()                   # the player is on "Recent sales"
    shown = []
    real = d.show_item
    monkeypatch.setattr(d, "show_item", lambda key: (shown.append(key), real(key)))
    d._follow()
    assert shown == []                                  # nothing newer
    monkeypatch.setattr(mesowatch, "version", lambda: d._version + 1)
    d._follow()
    assert shown == ["item/3198"] and d.tabs.value() == "sold"
    d.close()


def test_a_sellers_picture_is_the_whole_character(app, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from maplehelper.ui.marketview import face_pixmap
    img = QImage(34, 66, QImage.Format_ARGB32)
    img.fill(QColor("#336699"))
    path = tmp_path / "f.png"
    img.save(str(path))
    out = face_pixmap(path, 40).toImage()
    # the figure's feet are drawn too (the bottom of the picture), not cut to the head
    blue = [y for y in range(out.height()) if out.pixelColor(out.width() // 2, y).blue() > 120]
    assert blue and max(blue) > out.height() * 0.85 and min(blue) < out.height() * 0.15
