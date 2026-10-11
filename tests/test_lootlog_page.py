"""The Loot log tab: the messages area, Start / End, and the totals of the running log (ui/tools.py, ui/lootscan.py)."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from maplehelper import lootlog  # noqa: E402

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"


@pytest.fixture(scope="module")
def kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


@pytest.fixture
def page(isolated_store, kb):
    from maplehelper.ui.tools import ToolsDialog
    p = isolated_store.Profiles()
    p.add("Kiwi", "Magician", "Magician", 21)
    s = isolated_store.Settings()
    d = ToolsDialog(kb, p, s, "en", "", {}, "loot")
    yield d, s
    d.loot.stop()
    d.close()


def _cell(d, key):
    return d.loot_cells[key][1].text()


def test_no_area_no_start(page):
    """Nothing to read before the box is drawn: Start waits, and the page says where to draw it."""
    d, _ = page
    d.loot_changed()
    assert not d.loot_start.isEnabled() and d.loot_start.isVisibleTo(d)
    assert "messages area" in d.loot_status.text()


def test_the_area_button_asks_the_app_to_draw_the_box(page):
    d, _ = page
    asked = []
    d.loot_area_requested.connect(lambda: asked.append(True))
    d.loot_pick.click()
    assert asked == [True]


def test_a_running_log_shows_its_totals_and_ends_into_recent(page):
    d, s = page
    s["loot_region"] = {"x": 0, "y": 0, "w": 400, "h": 260}
    d.loot.restart()
    d.loot_changed()
    assert d.loot_start.isEnabled()
    d.loot_start.click()
    assert d.loot.running(d.c.id) and d.loot_end.isVisibleTo(d) and not d.loot_start.isVisibleTo(d)
    d.loot._log.add([lootlog.Event(1.0, "exp", 21), lootlog.Event(2.0, "mesos", 27),
                     lootlog.Event(3.0, "item", 2, "Leaf", "item/358")])
    d.loot.changed.emit()
    assert _cell(d, "exp") == "21" and _cell(d, "mesos") == "27" and _cell(d, "picked") == "2"
    assert any("Leaf" in w.text() for w in d.findChildren(type(d.loot_state)) if w.isVisibleTo(d))
    d.loot_end.click()
    assert not d.loot.running(d.c.id) and d.loot.store.recent(d.c.id)[0]["exp"] == 21


def test_the_card_over_the_game_follows_the_log_and_its_switch(page, kb):
    """Its own window over the game while a log runs; its × turns it off and the page's switch follows."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    from maplehelper.ui.lootoverlay import LootOverlay
    d, s = page
    s["loot_region"] = {"x": 0, "y": 0, "w": 400, "h": 260}
    d.loot_changed()                    # (Start waits for the box)
    card = LootOverlay(d.loot, s, kb)
    d.loot.changed.connect(card.sync)
    try:
        assert card.isWindow() and card.parent() is None and not card.isVisible()
        d.loot_start.click()
        assert card.isVisible()
        d.loot._log.add([lootlog.Event(1.0, "mesos", 1234), lootlog.Event(2.0, "item", 1, "Leaf", "item/358")])
        d.loot.changed.emit()
        texts = [text for text, _, _ in card._lines]
        assert any("1,234" in x for x in texts) and any("Leaf" in x for x in texts)
        at = QPointF(card._close_rect().center())
        card.mousePressEvent(QMouseEvent(QMouseEvent.MouseButtonPress, at, at, Qt.LeftButton, Qt.LeftButton,
                                         Qt.NoModifier))
        assert not card.isVisible() and s["loot_overlay"] is False and not d.loot_overlay_switch.isChecked()
        d.loot_overlay_switch.setChecked(True)
        assert card.isVisible()
        d.loot_end.click()
        assert not card.isVisible()
    finally:
        card.close()
