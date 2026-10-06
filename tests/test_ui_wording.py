"""The owner's wording rules for UI strings (HEB-13..16, UX-22, UX-23): one word for one thing, in each language."""
import os
import re
from pathlib import Path

import pytest

from maplehelper import glossary
from maplehelper.i18n import STRINGS

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


def _hebrew():
    yield from ((k, v["he"]) for k, v in STRINGS.items())
    yield from ((k, he) for k, (he, _en) in glossary.APP.items())


def test_hebrew_ui_says_rama_not_lv():
    """Hebrew says "ברמה 12" / "רמה {n}", never "Lv." (HEB-14)."""
    bad = [k for k, he in _hebrew() if re.search(r"\bLv\b", he)]
    assert not bad


def test_hebrew_ui_writes_cash_shop_in_english():
    """An in-game name stays in English, like "Free Market": "ב-Cash Shop", not "בקאש שופ" (HEB-13)."""
    assert not [k for k, he in _hebrew() if "קאש" in he]


@needs_kb
def test_hebrew_droppers_say_the_level_in_hebrew(isolated_store):
    """A quest's "where to get them" said "Snail (Lv. 1)" and "Arcforge Lv. 4" in the Hebrew UI (HEB-14)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.tools import ToolsDialog
    kb = KnowledgeBase(REAL_KB)
    p = isolated_store.Profiles()
    p.add("Kiwi", "Warrior", "Fighter", 30)
    for lang, word in (("he", "רמה "), ("en", "Lv. ")):
        d = ToolsDialog(kb, p, isolated_store.Settings(), lang, "", {}, "train")
        text = d._droppers_label(["Snail Shell x 2", "Bottomwear HP Scroll: Lesser x 1"]).text()
        assert word in text and "Snail" in text and "Arcforge" in text
        if lang == "he":
            assert "Lv" not in text
        d.close()


def test_dates_read_oct_6_in_english_and_6_10_in_hebrew():
    """English never writes day.month ("3.10" reads as March 10 in the US); Hebrew keeps "6.10" (HEB-16)."""
    from datetime import date

    from maplehelper import dates, news, recent
    from maplehelper.i18n import I18n
    from maplehelper.ui import patchnotes
    this = date.today().year
    assert dates.day(date(this, 10, 6), False) == "Oct 6" and dates.day(date(this, 10, 6), True) == "6.10"
    assert dates.day(date(2025, 10, 6), False, None if this != 2025 else True) == "Oct 6, 2025"
    assert dates.day(date(2025, 10, 6), True, True) == "6.10.2025"
    assert dates.iso("2025-01-02", False, True) == "Jan 2, 2025" and dates.iso("soon", True) == "soon"
    item = {"date": f"{this}-10-03"}
    assert news.short_date(item, False) == "Oct 3" and news.short_date(item, True) == "3.10"
    assert patchnotes._date(item, False) == "Oct 3" and patchnotes._date({"version": "0.9"}, True) == "0.9"
    r = recent.Recent("item/1", "Snail Shell", f"{this}-10-03")
    assert "Oct 3" in recent.tip(I18n("en"), None, r) and "3.10" in recent.tip(I18n("he"), None, r)
