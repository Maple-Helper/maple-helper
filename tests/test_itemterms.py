"""An item's kind and stat lines in Hebrew in a Hebrew app (the item details window, the cards)."""
import re
from pathlib import Path

import pytest

from maplehelper import itemterms

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


def test_stat_lines_read_in_hebrew_and_keep_stats_numbers_and_jobs():
    he = lambda s: itemterms.stat_line(s, "he")
    assert he("NPC Sell-back (per unit) 2 mesos") == "מחיר מכירה ל-NPC (ליחידה) 2 mesos"
    assert he("REQ LEV 35 REQ STR 25 REQ DEX 60 JOB Bowman") == "רמה נדרשת 35 · STR נדרש 25 · DEX נדרש 60 · ג'וב: Bowman"
    assert he("Upgrade Slots 7") == "סלוטים לשדרוג 7" and he("W.DEF +44") == "W.DEF +44"
    assert he("Fits over 2H Axe & 2H Blunt") == "מתאים מעל גרזן לשתי ידיים ונשק קהה לשתי ידיים"
    assert he("Shared with Power Elixir and Mana Elixir") == "משותף עם Power Elixir ו-Mana Elixir"
    assert he("Duration 60 min") == "משך 60 דק'" and he("EXP Bonus x2") == "בונוס EXP פי 2"
    assert itemterms.stat_line("Upgrade Slots 7", "en") == "Upgrade Slots 7"


def test_kinds_and_trade_read_in_hebrew():
    assert itemterms.kind("Etc / Monster Drop", "he") == "שונות · דרופ ממפלצת"
    assert itemterms.kind("Equip · Shield", "he") == "ציוד · מגן"
    assert itemterms.kind("Equip · Shield", "en") == "Equip · Shield"
    assert itemterms.tradeable("Untradeable", "he") == "אי אפשר לסחור בו"


@needs_kb
def test_every_line_the_real_kb_has_reads_in_hebrew():
    # every stat line shape of every item: no English word left but stat names, jobs, mesos and item names
    from maplehelper import itemdetails
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    allowed = {"STR", "DEX", "INT", "LUK", "HP", "MP", "W.DEF", "M.DEF", "W.ATK", "M.ATK", "ACC", "AVOID", "SPEED",
               "JUMP", "CRIT%", "CDMG", "KB", "NPC", "EXP", "mesos", "Cash", "Power", "Mana", "Elixir"}
    jobs = ("Warrior", "Thief", "Bowman", "Mage", "Beginner")
    for k in (k for k in kb.entities if k.startswith("item/")):
        d = itemdetails.details(kb, k)
        for ln in d.stats:
            he = itemterms.stat_line(ln, "he")
            left = [w for w in re.findall(r"[A-Za-z][A-Za-z.%]*", he) if w not in allowed and not w.startswith(jobs)]
            assert not left, (k, ln, he)
        assert not re.search(r"[A-Za-z]", itemterms.kind(d.kind, "he").replace("Cash", "").replace("EXP", "")
                             .replace("mesos", "")), (k, d.kind)
