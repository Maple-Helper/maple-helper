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
