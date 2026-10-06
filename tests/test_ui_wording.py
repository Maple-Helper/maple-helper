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


def test_english_ui_spells_one_way():
    """American spelling, "Lv." with its period, the feature's own name "Tracked items" (HEB-15, UX-23)."""
    en = {k: v["en"] for k, v in STRINGS.items()} | {k: e for k, (_he, e) in glossary.APP.items()}
    assert not [k for k, e in en.items() if re.search(r"\bLv\b(?!\.)|[Ll]evelled|defence|wishlist", e)]
    assert not [k for k, e in en.items() if re.search(r"\bin settings\b|Open settings", e)]


def test_adding_a_character_ends_with_add_character(isolated_store):
    """The Add character window's last button read "Done, let's play!", the first run's words (UX-23)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from maplehelper.kb import KnowledgeBase
    from maplehelper.ui.dialogs import Onboarding
    s = isolated_store.Settings()
    s["language"] = "en"
    dlg = Onboarding(s, isolated_store.Profiles(), KnowledgeBase(Path(__file__).parent / "fixtures" / "kb"),
                     lambda *_: "", only_character=True)
    dlg.stack.setCurrentIndex(dlg.stack.count() - 1)
    dlg._update_nav()
    assert dlg.next.text() == "Add character"
    dlg.close()


def test_english_ui_says_database_and_names_the_app_one_way():
    """One word for the data ("database", as in "Update database") and one name for the app: "Maple Helper", never
    "I", "the helper" or "the app" (UX-22). sell_q is the AI's prompt, not UI text."""
    en = {k: v["en"] for k, v in STRINGS.items() if k != "sell_q"}
    assert not [k for k, e in en.items() if re.search(r"knowledge.base", e, re.I)]
    assert not [k for k, e in en.items() if re.search(r"\b(the helper|the app)\b|^I\b|[.:] I\b|\bI'll\b", e, re.I)]


def test_official_facts_say_job_in_the_apps_word():
    """The UI says "ג'וב", so the facts do too, not a literal "עבודה" (AST-13)."""
    from maplehelper import official
    assert not [f["id"] for f in official.facts() if "עבודה" in f["he"]]


def test_the_chats_not_installed_errors_match_settings():
    """review3 DLG20-a / UX10-a: each CLI by the name Settings uses, "this computer", and another AI as a way out."""
    for p in ("", "_codex", "_gemini", "_grok"):
        he, en = STRINGS["err_not_installed" + p]["he"], STRINGS["err_not_installed" + p]["en"]
        assert he.endswith("לא מותקן במחשב. פתחו את ההגדרות כדי להתקין אותו או לחבר AI אחר.")
        assert en.endswith("isn't installed on this computer. Open Settings to install it or connect another AI.")
    for p, tool in (("_codex", "Codex (של ChatGPT)"), ("_gemini", "Google Antigravity (של Gemini)"),
                    ("_grok", "Grok Build (של Grok)")):
        assert STRINGS["err_not_installed" + p]["he"].startswith("הכלי " + tool)
        assert tool in STRINGS["ob_not_installed" + p]["he"]


def test_the_speech_model_has_one_name_and_its_failure_no_stale_size():
    """review3 UX12-b: "speech model" everywhere in English; the failure line names no size (an NVIDIA PC's is 2.2 GB)."""
    assert not [k for k, v in STRINGS.items() if "voice model" in v["en"]]
    assert "GB" not in STRINGS["voice_download_failed"]["he"] + STRINGS["voice_download_failed"]["en"]


def test_the_no_ai_note_turns_the_next_button_on():
    """review3 UX10-b: a button "is turned on", not "opens"; the Play tools by their button's own name."""
    note = STRINGS["ob_no_ai_note"]
    assert note["he"].startswith("הכפתור \"הבא\" יופעל ") and STRINGS["tools"]["he"] in note["he"]
    assert note["en"].startswith("Next turns on once")
