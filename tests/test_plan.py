"""The player's plan from the KB's guides: EXP left, where to train, the next job, and one tip."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import plan
from maplehelper.i18n import I18n

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
t = I18n("en")


def char(**kw):
    base = dict(id="a", name="Kiwi", base_class="Thief", job="Thief", level=28, map="", exp_pct=None)
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("base,job,level,expected", [
    ("Thief", "Thief", 29, (["Assassin", "Bandit"], 30)),
    ("Thief", "Assassin", 34, None),          # 3rd job isn't open yet (jobs.open_tier)
    ("Magician", "F/P Mage", 80, None),
    ("Beginner", "Beginner", 7, (["Warrior", "Magician", "Bowman", "Thief"], 10)),
    ("Beginner", "Beginner", 11, (["Warrior", "Magician", "Bowman", "Thief"], 10)),     # not taken yet (GAM-4)
])
def test_next_job(base, job, level, expected):
    assert plan.next_job(base, job, level) == expected


GRIND = """Level 31-35
Map | Street | Max EXP/hr | Dominant mob | Portals to pots | Why
Line 2 <Area 1> | Kerning City Subway | 2,270,000 | Jr. Wraith (lv34) | 5 (Pharmacy) | Great.
The Burnt Land V | Warning Street | 1,080,714 | Fire Boar (lv32) | 8 (Store) | Fine.
Level 1-10
Map | Street | Max EXP/hr | Dominant mob | Portals to pots | Why
The Tree That Grew II | Victoria Road | 318,571 | Slime (lv6) | 3 (x) | Early.
"""
EXP = "Level | EXP to next | Cumulative\n34 | 155,540 | 1 |\n"


def fake_kb():
    pages = {plan.GRIND_GUIDE: GRIND, plan.EXP_GUIDE: EXP}
    entities = {"monster/9": {"category": "monster", "name": "Jr. Wraith", "props": {"EXP": 70}}}
    return SimpleNamespace(page=lambda k: pages.get(k, ""), entities=entities, get=entities.get)


def test_route_and_progress_from_the_guides():
    kb = fake_kb()
    best = plan.spots_for(kb, 33, 1)[0]
    assert (best.map, best.mob, best.mob_level) == ("Line 2 <Area 1>", "Jr. Wraith", 34)
    p = plan.progress(kb, 34, 50.0)
    assert p["left"] == 77770 and p["mob"] == "Jr. Wraith" and p["kills"] == 1111


def test_one_tip_at_a_time_job_first_then_map():
    kb = fake_kb()
    assert plan.tip(kb, char(level=28), t).kind == "job"
    assert plan.tip(kb, char(level=28), t, {"job": 28}) is None                 # hidden until the next level
    assert plan.tip(kb, char(level=29), t, {"job": 28}).kind == "job"
    low = char(base_class="Thief", job="Assassin", level=33, map="The Tree That Grew II")
    tip = plan.tip(kb, low, t)
    assert tip.kind == "map" and "Line 2 <Area 1>" in t(tip.key, **tip.args)


@pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
def test_real_guides_parse():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    assert len(plan.exp_table(kb)) >= 90 and len(plan.brackets(kb)) >= 10
    assert plan.class_guide(kb, "Magician", "F/P Wizard") == "guide/fp-wizard-class-guide"
