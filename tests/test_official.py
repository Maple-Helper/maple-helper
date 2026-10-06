"""Official game facts: well-formed, given to the AI, and the code agrees with them."""
from maplehelper import official
from maplehelper.jobs import JOBS


def test_every_fact_is_complete_in_both_languages():
    ids = [f["id"] for f in official.facts()]
    assert ids and len(ids) == len(set(ids))
    for f in official.facts():
        assert f["en"].strip() and f["he"].strip() and len(f["added"]) == 10


def test_the_ai_is_told_the_facts_outrank_the_kb():
    note = official.prompt_note()
    assert "override the knowledge base" in note
    for f in official.facts():
        assert f["en"] in note


def test_first_jobs_open_at_the_official_level():
    levels = official.value("first_job_level")
    assert set(levels) == {"Warrior", "Magician", "Bowman", "Thief"}
    for cls, level in levels.items():
        first = next(lv for job, lv in JOBS[cls] if job != "Beginner")
        assert first == level, cls


def test_the_level_cap_is_official_and_reaches_the_prompt(kb):
    """The KB's exp-table guide still says "Nexon has never announced a level cap"; the release notes say 100 (AST-12)."""
    from maplehelper.brain import Brain
    cap = next(f for f in official.facts() if f["id"] == "level-cap")
    assert "100" in cap["en"] and "100" in cap["he"] and "Founder's Access" in cap["en"]
    assert cap["en"] in Brain(kb, provider="claude").system_prompt()
