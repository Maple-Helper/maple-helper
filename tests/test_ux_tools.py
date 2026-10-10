"""UX fixes in the Play tools window, the guides, the patch notes and the wishlist (TOOL-xx, PERF-04/05)."""
import os
import sys
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
def tools(app, real_kb, isolated_store):
    """open(base_class, job, level, lang="en", page="train"); base None: no character."""
    from maplehelper.ui.tools import ToolsDialog
    opened = []

    def open_(base="Thief", job="Assassin", level=35, lang="en", page="train"):
        p = isolated_store.Profiles()
        if base:
            p.set_active(p.add("Kiwi", base, job, level).id)
        d = ToolsDialog(real_kb, p, isolated_store.Settings(), lang, "", None, page)
        opened.append(d)
        return d
    yield open_
    for d in opened:
        d.close()
    app.processEvents()


def test_a_picked_chip_has_room_for_its_bold_text(app):
    """TOOL-02: a picked "All levels" was sized for its regular text and drew "ll levels"."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QPushButton, QWidget

    from maplehelper.ui import theme
    from maplehelper.ui.controls import FlowLayout
    w = QWidget()
    w.setStyleSheet(theme.stylesheet(theme.load_fonts(), 14))
    flow = FlowLayout(w)
    b = QPushButton("All levels", objectName="Chip")
    b.setCheckable(True)
    b.setChecked(True)
    flow.addWidget(b)
    w.resize(400, 80)
    w.show()
    app.processEvents()
    bold = b.font()
    bold.setBold(True)
    assert b.width() >= QFontMetrics(bold).horizontalAdvance("All levels") + 26      # its 13 px padding each side
    w.close()


@needs_kb
def test_no_character_shows_one_card_that_adds_one(tools, app):
    """TOOL-03: a live stats editor and pickers stood above a grey "you need a character"."""
    from PySide6.QtWidgets import QPushButton
    d = tools(base=None, page="train")
    d.show()
    app.processEvents()
    gate = d.stack.currentWidget()
    assert gate.currentIndex() == 1 and not d.pages["train"].isVisible()
    asked = []
    d.add_character_requested.connect(lambda: asked.append(1))
    add = next(b for b in gate.currentWidget().findChildren(QPushButton) if b.objectName() == "Primary")
    add.click()
    assert asked == [1]
    # a character added: the page itself
    d.profiles.set_active(d.profiles.add("Kiwi", "Thief", "Assassin", 35).id)
    d.profile_changed()
    assert gate.currentIndex() == 0
    d.show_page(d.nav.id(d.nav.buttons()[1]))           # another character page follows on its first visit
    assert d.stack.currentWidget().currentIndex() == 0


@needs_kb
def test_grind_links_wait_for_a_monster(tools):
    """TOOL-17: "How to get there" and "Hit & damage" under an empty monster box did nothing."""
    d = tools(page="exp")
    d.refresh("exp")
    links = [d.grind_links.itemAt(i).widget() for i in range(d.grind_links.count())]
    d.grind_monster.clear()
    assert links and not any(b.isEnabled() for b in links)
    d.grind_monster.setText("Stirge")
    assert all(b.isEnabled() for b in links)


@needs_kb
def test_citizenship_grade_chips_are_short_and_the_done_toggle_is_on_top(tools):
    """TOOL-16 / TOOL-10."""
    from PySide6.QtWidgets import QPushButton
    d = tools(page="town")
    d.refresh("town")
    chips = [d.town_grades.itemAt(i).widget() for i in range(d.town_grades.count())]
    texts = [b.text() for b in chips if isinstance(b, QPushButton)]
    assert texts and not any("Citizenship" in x for x in texts) and any(x.endswith("Grade 1") for x in texts)
    lay = d.pages["town"].widget().layout()
    at = [lay.indexOf(d.town_done_toggle)] + [i for i in range(lay.count()) if lay.itemAt(i).layout() is d.town_list]
    assert at[0] < at[1]


@needs_kb
def test_beginner_build_page_leads_to_the_skills_and_guides(tools):
    """TOOL-19: one grey line on an empty page."""
    from PySide6.QtWidgets import QPushButton
    d = tools("Beginner", "Beginner", 8, page="build")
    d.refresh("build")
    links = [b for i in range(d.build_tier.count()) if (w := d.build_tier.itemAt(i).widget())
             for b in w.findChildren(QPushButton)]
    assert [b.text() for b in links][:1] == ["See the skills"]
    links[0].click()
    assert d.build_tabs.value() == "skills" and d.build_stack.currentIndex() == 1


@needs_kb
def test_hit_and_damage_maps_offer_the_way_there(tools):
    """TOOL-08: three "Ask in chat" links, one a map, asked the AI what the route finder knows."""
    from PySide6.QtWidgets import QPushButton
    d = tools(page="calc")
    d.calc_input.setText("Stirge")
    d._fill_calc()
    maps = d.calc_box.itemAt(d.calc_box.count() - 1).widget()
    texts = [b.text() for b in maps.findChildren(QPushButton)]
    assert texts and all(x == "How to get there" for x in texts)


@needs_kb
def test_an_offline_free_market_offers_to_try_again(tools, monkeypatch):
    """TOOL-12."""
    from PySide6.QtWidgets import QPushButton

    from maplehelper import market
    monkeypatch.setattr(market, "item_market", lambda i: None)
    monkeypatch.setattr(market, "free_market", lambda n: None)
    d = tools(page="prices")
    d.price_input.setText("Red Potion")
    d._fill_prices()
    d._on_market(("Red Potion", None))
    again = [b for i in range(d.fm_more.count()) if (w := d.fm_more.itemAt(i).widget())
             for b in w.findChildren(QPushButton) if b.text() == "Try again"]
    assert again
    looked = []
    monkeypatch.setattr(d, "_fm_lookup", lambda n, i: looked.append(n))
    again[0].click()
    assert looked == ["Red Potion"] and "Free Market" in d.fm_label.text()


@needs_kb
@pytest.mark.parametrize("lang", ["en", "he"])
def test_the_price_card_shows_mesowatch_sales_with_its_credit(tools, monkeypatch, lang):
    import time

    from PySide6.QtWidgets import QPushButton

    from maplehelper import mesowatch
    snap = mesowatch.Snapshot("Windia", time.time() - 600, {})
    ore = mesowatch.Sales(4010001, "Iron Ore", 375, 299, 499, 2243, False, 500, trend_pct=-22, trend_days=3,
                          shops=[mesowatch.Shop("The Rain-Forest East of Henesys", 399, 1, 8, time.time() - 300)])
    monkeypatch.setattr(mesowatch, "for_item", lambda kb, key, *a, **k: (snap, ore))
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    d = tools(page="prices", lang=lang)
    d.price_input.setText("Iron Ore")
    d._fill_prices()
    assert "MesoWatch" in d.mw_label.text()                              # checking…, before the lookup is in
    d._on_mesowatch((d._price_key, (snap, ore)))
    text = d.mw_label.text()
    assert "375" in text and "Windia" in text
    more = [d.mw_more.itemAt(i).widget().text() for i in range(d.mw_more.count())]
    assert any("299" in x and "499" in x for x in more) and any("399" in x and "Rain-Forest" in x for x in more)
    link = [b for b in d.findChildren(QPushButton) if "MesoWatch" in b.text()]
    assert link
    link[0].click()
    assert opened == ["https://meso.watch/?item=04010001"]
    d._on_mesowatch((d._price_key, (None, None)))                       # can't reach it
    assert "MesoWatch" in d.mw_label.text() and d.mw_more.count() == 0


def test_guide_search_lights_no_category_and_counts(app):
    """TOOL-11: the search looks in every guide while "For you" stayed lit."""
    from types import SimpleNamespace

    from maplehelper.ui.guides import GuidesDialog
    kb = SimpleNamespace(entities={}, page=lambda k: "", get=lambda k: None, picture=lambda k: None)
    d = GuidesDialog(kb, None, "en", "")
    d.all = [{"key": f"guide/g{i}", "title": f"Henesys {i}", "category": "general", "minutes": None} for i in range(3)]
    d._texts = {g["key"]: "" for g in d.all}
    d.search.setText("henesys")
    assert d.cats.checkedButton() is None
    shown = [d.rows.itemAt(i).widget() for i in range(d.rows.count())]
    assert shown[0].text() == "3 guides from every category"
    d.search.clear()
    assert d.cats.checkedButton() is d.cats.buttons()[0]
    d.search.setText("henesys")
    d.cats.buttons()[2].click()                  # a category chip: browse it, the search emptied
    assert d.search.text() == "" and d.cats.checkedButton() is d.cats.buttons()[2]
    d.close()


def test_patch_note_item_lists_keep_names_whole_and_cut_long_lists():
    """TOOL-04."""
    from maplehelper import bidi, recent
    from maplehelper.i18n import NBSP, I18n
    names = [f"Earring STR Scroll {i}" for i in range(12)]
    he = recent._items(I18n("he"), names)
    assert f"{bidi.LRI}Earring{NBSP}STR{NBSP}Scroll{NBSP}0{bidi.PDI}" in he and he.endswith("ועוד 4")
    assert recent._items(I18n("en"), names[:2]) == "Earring STR Scroll 0, Earring STR Scroll 1"
    assert "One-\u2060Handed" in recent._items(I18n("he"), ["Scroll for One-Handed Sword"])


@needs_kb
def test_patch_notes_show_fast_and_the_rest_on_demand(app, real_kb):
    """PERF-04 / TOOL-21: every card at once held the window blank for 1.3-1.8 s; "and N more" was a dead end."""
    from PySide6.QtWidgets import QPushButton

    from maplehelper.ui import patchnotes
    from maplehelper.ui.widgets import EntityCard
    monsters = [k for k, e in real_kb.entities.items() if e.get("category") == "monster"][:30]
    entry = {"version": "2026.10.04.0100", "date": "2026-10-04", "counts": {"added": 30},
             "added": [{"key": k, "name": real_kb.get(k)["name"], "category": "monster"} for k in monsters]}
    d = patchnotes.PatchNotesDialog([entry], "en", "", real_kb)
    cards = lambda: d.findChildren(EntityCard)       # noqa: E731
    assert len(cards()) == patchnotes.FIRST_CARDS                 # the rest after the window is up
    for _ in range(50):
        app.processEvents()
    assert len(cards()) == patchnotes.SHOWN
    assert all(c._lazy is None for c in cards())                  # the pictures followed
    more = next(b for b in d.findChildren(QPushButton) if b.text() == "Show more")
    more.click()
    for _ in range(50):
        app.processEvents()
    assert len(cards()) == 30 and more.isHidden()
    d.close()


@needs_kb
def test_farm_needs_leave_out_professions_not_taken(real_kb):
    """TOOL-05: Garnet Ore "for a quest" of a profession the player never took."""
    from maplehelper import farm, quests
    crafting_quests = {q.name for k, e in real_kb.entities.items() if e.get("category") == "quest"
                       for q in [quests.quest(real_kb, k)] if q and q.area == "Crafting"}
    none = farm.needs(real_kb, 35, "Thief", "Assassin", [], None, [])
    assert not [v for v in none.values() if v[0] == "quest" and v[1] in crafting_quests]
    smith = farm.needs(real_kb, 35, "Thief", "Assassin", [], {"smithing": 5}, [])
    assert any(v[0] == "quest" and v[1] in crafting_quests for v in smith.values())


@needs_kb
def test_item_pages_are_read_once_per_kb(real_kb, monkeypatch):
    """PERF-05: the item list and the Farm tab read ~2,000 pages on every open."""
    from maplehelper import market, sitedata
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui import tools
    key = real_kb._item_by_name["red potion"]
    first = market.npc_prices(real_kb, key)
    sitedata.untradeable(real_kb, key)
    tools.item_rows(real_kb)
    reads = []
    monkeypatch.setattr(KnowledgeBase, "page", lambda self, k: reads.append(k) or "")
    assert market.npc_prices(real_kb, key) is first
    sitedata.untradeable(real_kb, key)
    tools.item_rows(real_kb)
    assert reads == []


@needs_kb
def test_wishlist_droppers_say_their_level_in_hebrew_and_lead_to_the_tools(app, real_kb):
    """TOOL-07: "Snail • Lv. 1" in Hebrew, and "Ask in chat" the only way on."""
    from PySide6.QtWidgets import QLabel, QPushButton

    from maplehelper.ui.wishlist import WishlistDialog
    key = real_kb._item_by_name["garnet ore"]
    d = WishlistDialog([key], real_kb, "he", "", 35)
    names = [lb.text() for lb in d.findChildren(QLabel) if lb.objectName() == "CardName"]
    assert any("רמה" in n for n in names) and not any("Lv." in n for n in names)
    asked = []
    d.farm_requested.connect(asked.append)
    d.route_requested.connect(asked.append)
    links = {b.text().strip("‏"): b for b in d.findChildren(QPushButton) if b.objectName() == "Link"}
    next(b for t, b in links.items() if "פארם" in t).click()
    next(b for t, b in links.items() if "איך מגיעים" in t).click()
    assert asked[0] == "Garnet Ore" and len(asked) == 2
    d.close()
