"""The item details window: what it reads from an item's page (stats, description, Meow Notes) and where it opens."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication

from maplehelper import itemdetails
from maplehelper.ui import itemview

app = QApplication.instance() or QApplication([])
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
SCREEN = QRect(0, 0, 1920, 1040)
SIZE = QSize(504, 744)


class _KB:
    """One item page, as the knowledge base keeps it."""

    def __init__(self, text: str, name: str = "Pan Lid"):
        self.text, self.name = text, name

    def page(self, key):
        return "---\n{}\n---\n" + self.text

    def get(self, key):
        return {"name": self.name}


PAN_LID = """
# Pan Lid

Gives a chance to block enemy attacks entirely.

Pan Lid
Equip · Shield · No. 0917
REQ LEV 10
W.DEF +44
Upgrade Slots 7
Shield Guard Chance 8.1%
Male + Female Tradeable
Worn on
Meow Notes
This shield's WDEF reduces physical damage and sets its Shield Guard chance against eligible regular physical hits.
M
MeowDB Admin Aug 10, 2026
This shield is better for Mages than mage shields IF you care more about guarding against Physical attacks.
Observed Stat Rolls
no rolls yet Log in to submit
"""


def test_reads_the_stats_description_and_meow_notes():
    d = itemdetails.details(_KB(PAN_LID), "item/917")
    assert d.kind == "Equip · Shield" and d.description == "Gives a chance to block enemy attacks entirely."
    assert d.stats == ["REQ LEV 10", "W.DEF +44", "Upgrade Slots 7", "Shield Guard Chance 8.1%"]
    assert d.about == ["This shield's WDEF reduces physical damage and sets its Shield Guard chance against eligible "
                       "regular physical hits."]
    (post,) = d.posts
    assert (post.author, post.when) == ("MeowDB Admin", "Aug 10, 2026") and post.text.startswith("This shield is better")


def test_a_post_runs_over_several_lines_and_stops_at_the_next_section():
    page = """
# Letter L
Setup · No. 0531
Max per Stack 100
Tradeable
Meow Notes
M
MeowDB Admin May 3, 2026 edited
These dropped from pretty much every mob during the Closed Online Test, to spell out L E A F P O I N T S.
Leaf Points (1,000) Exchange Coupon
The quest can be completed once a day and resets daily at 12:00 AM UTC, so you can stack the letters up!
M
MeowDB Admin 6h ago
Leaf Points were used in place of NX during the closed play test, and were worth a lot.
Dropped By
Community sourced
"""
    d = itemdetails.details(_KB(page, "Letter L"), "item/531")
    assert d.kind == "Setup" and d.stats == ["Max per Stack 100"] and not d.about
    first, second = d.posts
    assert first.text.count("\n") == 2 and "Exchange Coupon" in first.text       # a label line inside the post stays
    assert (second.when, second.text.endswith("worth a lot.")) == ("6h ago", True)
    assert "Dropped By" not in second.text and "Community" not in second.text


def test_an_item_with_no_notes_has_none():
    d = itemdetails.details(_KB("# Pan Lid\nEquip · Shield · No. 0917\nREQ LEV 10\nTradeable\n"), "item/917")
    assert d.stats == ["REQ LEV 10"] and not d.about and not d.posts and not d.description


def test_opens_right_of_the_chat_or_left_when_there_is_no_room():
    chat = QRect(400, 60, 624, 664)
    at = itemview.beside(chat, SIZE, [SCREEN])
    assert at == QPoint(chat.right() + 1 + itemview.GAP, 60)
    corner = QRect(1272, 60, 624, 664)                  # the chat's own spot: the screen's top-right
    at = itemview.beside(corner, SIZE, [SCREEN])
    assert at.x() + SIZE.width() <= corner.left() + 2 * itemview.SHADOW and SCREEN.contains(QRect(at, SIZE))


def test_the_window_reopens_where_the_player_left_it(kb_copy, isolated_store):
    from maplehelper.kb import KnowledgeBase
    from maplehelper.store import Settings
    kb = KnowledgeBase(kb_copy)
    dlg = itemview.ItemDetailsDialog(kb, "en", "", Settings(), QRect(400, 60, 624, 664))
    dlg.move(150, 20)                                   # the player drags it away, then closes it
    dlg.reject()
    assert Settings()[itemview.POS_SETTING] == {"x": 150, "y": 20}
    assert itemview.ItemDetailsDialog(kb, "en", "", Settings(), QRect(400, 60, 624, 664)).pos() == QPoint(150, 20)


@needs_kb
def test_the_pan_lids_window_shows_its_stats_dropper_and_notes():
    from PySide6.QtWidgets import QLabel

    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    if not kb.get("item/917") or kb.get("item/917")["name"] != "Pan Lid":
        pytest.skip("no Pan Lid in this knowledge base")
    dlg = itemview.ItemDetailsDialog(kb, "en", "")
    dlg.show_item("item/917")
    texts = " | ".join(lb.text() for lb in dlg.findChildren(QLabel))
    assert "W.DEF +44" in texts and "Meow Notes" in texts and "Shield Guard" in texts
    assert all(kb.get(m)["name"] in texts for m in kb.droppers.get("item/917", []))
    dlg.close()


def test_the_card_in_the_details_window_has_no_details_button(kb):
    from PySide6.QtWidgets import QToolButton

    from maplehelper.ui.widgets import EntityCard
    from maplehelper.i18n import I18n
    key = next(k for k in kb.entities if k.startswith("item/"))
    tip = I18n("en")("card_item_details")
    tips = lambda c: [b.toolTip() for b in c.findChildren(QToolButton)]
    assert tip in tips(EntityCard(kb, key, "en"))
    assert tip not in tips(EntityCard(kb, key, "en", details=False))
