"""NiaMeowDB's list pages: skill changes between builds, pets and the community tier list. Read from recorded pages
(tests/fixtures/meowdb, no network), checked by the KB gate, diffed into the patch notes, and shown in the app."""
import copy
import json
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import kb_release  # noqa: E402
import meowdb_sections as ms  # noqa: E402

from maplehelper import availability, brain, sitedata  # noqa: E402
from maplehelper.i18n import I18n  # noqa: E402
from maplehelper.kb import KnowledgeBase  # noqa: E402

PAGES = Path(__file__).parent / "fixtures" / "meowdb"
REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


def bare(text: str) -> str:
    """A label's text without its direction marks (and the word joiner that keeps "\u05d1-" with what follows)."""
    return "".join(ch for ch in text if ch not in "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2069\u2060")


def page(name: str) -> str:
    return (PAGES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed() -> dict:
    return {ms.SKILL_CHANGES: ms.parse_skill_changes(page("skill-changes")),
            ms.PETS: ms.parse_pets(page("pets")),
            ms.TIERS: ms.parse_tiers(page("tier-list"))}


def add_entries(kb: Path, data: dict) -> None:
    """Give every row of the list pages an entry (and a page) in a small KB."""
    index = json.loads((kb / "index.json").read_text(encoding="utf-8"))
    have = {e["key"] for e in index}
    for name, d in data.items():
        for r in ms.rows_of(name, d):
            if r["key"] in have:
                continue
            have.add(r["key"])
            cat, _, slug = r["key"].partition("/")
            index.append({"key": r["key"], "id": slug, "name": r["name"], "category": cat, "props": {}})
            (kb / "pages" / cat).mkdir(parents=True, exist_ok=True)
            (kb / "pages" / cat / f"{slug}.md").write_text(f"# {r['name']}\n", encoding="utf-8")
    (kb / "index.json").write_text(json.dumps(index), encoding="utf-8")


def write(kb: Path, data: dict) -> None:
    for name, d in data.items():
        (kb / name).write_text(json.dumps(d), encoding="utf-8")


@pytest.fixture
def site_kb(kb_copy, parsed):
    """The fixture KB with the three list pages' files and an entry for every row."""
    add_entries(kb_copy, parsed)
    write(kb_copy, parsed)
    return kb_copy


# ------------------------------------------------------------------ reading the pages

def test_skill_changes_page(parsed):
    d = parsed[ms.SKILL_CHANGES]
    # the builds are read from the page itself, never assumed
    assert (d["before"], d["after"]) == ("COT1", "COT2")
    by = {s["key"]: s for s in d["skills"]}
    assert len(by) == 37
    fa = by["skill/fighter__final-attack-sword"]
    assert (fa["name"], fa["job"], fa["tier"]) == ("Final Attack: Sword", "Fighter", 2)
    assert [(c["field"], c["before"], c["after"]) for c in fa["changes"]] == [("Chance", "35%", "50%"),
                                                                            ("Damage", "180%", "140%")]
    assert [(c["field"], c["before"], c["after"]) for c in by["skill/warrior__iron-body"]["changes"]] == \
        [("Defense bonus", "30%", "25%")]
    slow = by["skill/f-p-wizard__slow"]
    assert [(c["field"], c["before"], c["after"]) for c in slow["changes"]] == [("Duration", "25", "20"),
                                                                              ("Targets", "4", "6")]
    assert slow["note"].startswith("New in COT2: enemies hit by Slow also lose 5 Evasion")
    # a change told only in words keeps its words; the three moves into 3rd-job books are tier 3
    assert by["skill/fighter__rush"]["changes"] == [] and "Power Guard moved to Crusader" in by["skill/fighter__rush"]["note"]
    assert sorted(k for k, s in by.items() if s["tier"] == 3) == ["skill/crusader__power-guard",
                                                                  "skill/dragon-knight__hyper-body",
                                                                  "skill/white-knight__power-guard"]


def test_pets_page(parsed):
    pets = {p["name"]: p for p in parsed[ms.PETS]["pets"]}
    assert len(pets) == 12
    kitty = pets["Brown Kitty"]
    assert kitty == {"key": "item/1524", "name": "Brown Kitty", "lifespan": "7 days", "hunger": 2, "level": 30,
                     "commands": 25000, "commands_text": "~25.0k", "availability": "In Cash Shop", "sold": True,
                     "closed_test": ["lifespan"]}
    assert sorted(n for n, p in pets.items() if p["sold"]) == ["Brown Kitty", "Husky", "Pink Bunny"]
    assert pets["Black Pig"]["hunger"] == 5 and pets["Black Pig"]["commands"] == 19100
    assert pets["Snail"]["lifespan"] == "5 hours" and pets["Snail"]["closed_test"] == []


def test_tier_list_page(parsed):
    d = parsed[ms.TIERS]
    assert d["level"] == 70 and d["builds_by"] == "Grummash"
    assert d["columns"] == ["ST DPS", "AOE DPS", "Damage/MP", "Physical EHP", "Magic EHP", "Mobility", "Support", "Range"]
    base = [r for r in d["rows"] if not r["variant"]]
    assert len(base) == 10 and "Islander" not in {r["name"] for r in d["rows"]}      # every cell N/A: left out
    bandit = base[0]
    assert (bandit["key"], bandit["line"]) == ("class/bandit", "Thief")
    assert bandit["cells"]["ST DPS"] == {"value": "5,696", "grade": "S"}
    shields = [r for r in d["rows"] if r["variant"]]
    assert [(r["name"], r["variant"]) for r in shields] == [("Fighter", "1H + Shield"), ("Page", "1H + Shield")]
    assert {r["key"] for r in base} >= {"class/f-p-wizard", "class/i-l-wizard", "class/crossbowman"}


def test_nightly_scrape_writes_once_and_keeps_a_good_file(tmp_path):
    pages = {"skill-changes": page("skill-changes"), "pets": page("pets"), "tier-list": page("tier-list")}
    fetched = []

    def fetch(url):
        fetched.append(url)
        return pages[url.rsplit("/", 1)[1]]
    assert ms.scrape(tmp_path, fetch, delay=0) == 3
    assert fetched == [f"{ms.BASE}/skill-changes", f"{ms.BASE}/pets", f"{ms.BASE}/tier-list"]      # one at a time
    assert ms.scrape(tmp_path, fetch, delay=0) == 0          # nothing new: no change counted
    before = (tmp_path / ms.PETS).read_text(encoding="utf-8")
    pages["pets"] = "<html>Maintenance</html>"               # the site down or a new layout
    assert ms.scrape(tmp_path, fetch, delay=0) == 0
    assert (tmp_path / ms.PETS).read_text(encoding="utf-8") == before


# ------------------------------------------------------------------ the KB gate and the patch notes

def test_gate_checks_the_list_pages(site_kb):
    assert kb_release.validate(site_kb)
    (site_kb / ms.PETS).write_text(json.dumps({"pets": [{"key": "item/1524"}]}), encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="pets.json: 1 pets"):
        kb_release.validate(site_kb)
    (site_kb / ms.PETS).write_text("{", encoding="utf-8")
    with pytest.raises(kb_release.InvalidKB, match="pets.json unreadable"):
        kb_release.validate(site_kb)


def test_gate_refuses_rows_that_name_nothing(kb_copy, parsed):
    write(kb_copy, {ms.TIERS: parsed[ms.TIERS]})          # no class entries in the small KB
    with pytest.raises(kb_release.InvalidKB, match="tiers.json: .* name no KB entry"):
        kb_release.validate(kb_copy)


def test_patch_notes_list_the_list_pages_changes(tmp_path, site_kb, parsed):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(site_kb, old)
    new = copy.deepcopy(parsed)
    fa = next(s for s in new[ms.SKILL_CHANGES]["skills"] if s["key"] == "skill/fighter__final-attack-sword")
    fa["changes"][0]["after"] = "55%"
    kitty = next(p for p in new[ms.PETS]["pets"] if p["key"] == "item/1524")
    kitty.update(lifespan="90 days", availability="Not sold right now", sold=False)
    bandit = next(r for r in new[ms.TIERS]["rows"] if r["key"] == "class/bandit")
    bandit["cells"]["ST DPS"] = {"value": "5,800", "grade": "S"}
    write(site_kb, new)
    d = kb_release.diff_kb(old, site_kb)
    rows = {r["key"]: r for r in d["changed"]}
    assert rows["skill/fighter__final-attack-sword"]["props"] == [["Chance", "50%", "55%"]]
    assert rows["item/1524"]["props"] == [["Lifespan", "7 days", "90 days"], ["Cash Shop", "In Cash Shop", "Not sold right now"]]
    assert rows["class/bandit"]["props"] == [["ST DPS (community tier list)", "S 5,696", "S 5,800"]]
    assert d["counts"]["changed"] == 3
    # into the changelog: the app's "Updated" chips and patch notes read it
    kb_release.record_changes(site_kb, old, "2026.10.04.0100")
    from maplehelper import recent
    log = json.loads((site_kb / "changelog.json").read_text(encoding="utf-8"))
    found = recent.recent(KnowledgeBase(site_kb), log=log, today=__import__("datetime").date.fromisoformat(log[0]["date"]))
    assert found["item/1524"].props["Lifespan"] == ["7 days", "90 days"]


def test_a_list_page_new_to_the_kb_is_no_news(tmp_path, site_kb):
    import shutil
    old = tmp_path / "old"
    shutil.copytree(site_kb, old)
    for name in ms.FILES:
        (old / name).unlink()
    assert kb_release.section_changes(old, site_kb) == []


# ------------------------------------------------------------------ the app's view

def test_skill_changes_in_the_app(site_kb):
    kb = KnowledgeBase(site_kb)
    ch = sitedata.skill_change(kb, "skill/fighter__final-attack-sword")
    assert (ch.before, ch.after, ch.changes) == ("COT1", "COT2", (("Chance", "35%", "50%"), ("Damage", "180%", "140%")))
    he, en = I18n("he"), I18n("en")
    assert sitedata.chip_label(he, ch) == "השתנה ב-COT2" and sitedata.chip_label(en, ch) == "Changed in COT2"
    text = sitedata.change_text(he, ch)
    assert text.startswith("סיכוי: ") and "35% → 50%" in text and "נזק: " in text
    assert sitedata.change_text(en, ch).replace(" ", " ").replace("⁦", "").replace("⁩", "") == \
        "Chance: 35% → 50% · Damage: 180% → 140%"
    # a Fighter's line: the Warrior and Fighter skills, never the Page's same-named Final Attack
    keys = {c.key for c in sitedata.changes_for(kb, "Warrior", "Fighter")}
    assert "skill/warrior__iron-body" in keys and "skill/page__final-attack-sword" not in keys


def test_third_job_skills_stay_hidden_until_3rd_job_is_out(site_kb):
    kb = KnowledgeBase(site_kb)
    assert availability.of(kb).job_tier == 2
    assert sitedata.skill_change(kb, "skill/crusader__power-guard") is None
    # the prompt says which advancements ARE in the game too: told only "3rd job is not", the AI said 2nd wasn't
    note = availability.of(kb).scope_note()
    assert "Job advancements in the game: 1st, 2nd " in note and "3rd job advancement is not in the game" in note
    availability.of(kb).job_tier = 3            # the release guide confirms 3rd job
    assert sitedata.skill_change(kb, "skill/crusader__power-guard") is not None
    assert "1st, 2nd, 3rd " in availability.of(kb).scope_note()


def test_a_launch_comparison_reads_launch(site_kb, parsed):
    data = copy.deepcopy(parsed[ms.SKILL_CHANGES])
    data.update(before="COT2", after="Launch")
    write(site_kb, {ms.SKILL_CHANGES: data})
    ch = sitedata.skill_change(KnowledgeBase(site_kb), "skill/fighter__final-attack-sword")
    assert sitedata.chip_label(I18n("he"), ch) == "השתנה בהשקה" and sitedata.chip_label(I18n("en"), ch) == "Changed in Launch"


def test_pets_and_tiers_in_the_app(site_kb):
    kb = KnowledgeBase(site_kb)
    pets = sitedata.pets(kb)
    assert len(pets) == 12 and [p.name for p in pets if p.sold] == ["Brown Kitty", "Pink Bunny", "Husky"]
    assert sitedata.easiest(pets)[0].name == "Black Pig"
    he = I18n("he")
    assert sitedata.lifespan_text(he, sitedata.pet(kb, "item/1524")) == "7 ימים"
    assert sitedata.lifespan_text(he, sitedata.pet(kb, "item/1527")) == "5 שעות"
    fighter = sitedata.tiers_for(kb, "Warrior", "Fighter")
    assert [r.name for r in fighter] == ["Fighter"] and fighter[0].ranks["AOE DPS"] == (1, 9)     # (Assassin has no AoE)
    assert [r.name for r in sitedata.tiers_for(kb, "Magician", "Magician")] == ["F/P Wizard", "I/L Wizard", "Cleric"]
    assert sitedata.tiers_for(kb, "Beginner", "Beginner") == []
    assert [r.name for r in sitedata.tiers_for(kb, "Warrior", "Crusader")] == ["Fighter"]


def test_no_list_pages_no_crash(kb):
    assert sitedata.skill_changes(kb) == {} and sitedata.pets(kb) == [] and sitedata.tier_rows(kb) == []


def test_the_ai_gets_the_list_pages(site_kb):
    from maplehelper.store import Character
    kb = KnowledgeBase(site_kb)
    c = Character(id="1", name="Kiwi", base_class="Warrior", job="Fighter", level=35)
    prompt = brain.build_prompt("which skills should I put SP in?", c, None, kb, False)
    assert "Skill change COT1 -> COT2 (Final Attack: Sword, Fighter;" in prompt and "Chance 35% -> 50%" in prompt
    assert "(Power Guard, Crusader;" not in prompt                   # 3rd job isn't out
    assert "Community tier list" in prompt and "- Fighter (Warrior): ST DPS A 4,390" in prompt
    pets = brain.build_prompt("what pets can I buy?", c, None, kb, False)
    assert "Pet Brown Kitty [item/1524]: lifespan 7 days (closed-test value" in pets and "In Cash Shop" in pets
    tiers = brain.build_prompt("which class is the strongest class?", None, None, kb, False)
    assert "- Bandit (Thief): ST DPS S 5,696" in tiers and "community opinion" in tiers
    plain = brain.build_prompt("where do I train?", c, None, kb, False)
    assert "Skill change" not in plain and "Community tier list" not in plain and "Pet " not in plain


# ------------------------------------------------------------------ UI (offscreen)

@pytest.fixture
def real_site(monkeypatch, parsed):
    """The real KB with the recorded list pages (CI's KB may be from before the first nightly that has them)."""
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    monkeypatch.setattr(sitedata, "_file", lambda kb, name: parsed.get(name, {}))
    return KnowledgeBase(REAL_KB)


def test_chat_cards_show_skill_changes_and_pets(real_site):
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication([])
    from maplehelper.ui.widgets import EntityCard
    for lang, chip in (("he", "השתנה ב-COT2"), ("en", "Changed in COT2")):
        card = EntityCard(real_site, "skill/fighter__final-attack-sword", lang)
        tag = card.findChild(QLabel, "ChangedTag")
        assert bare(tag.text()) == chip and "35%" in tag.toolTip() and "140%" in tag.toolTip()
    assert EntityCard(real_site, "skill/crusader__power-guard", "en").findChild(QLabel, "ChangedTag") is None
    pet = EntityCard(real_site, "item/1524", "en")
    pills = [bare(w.text()) for w in pet.findChildren(QLabel, "StatPill")]
    assert pills == ["Lifespan: 7 days", "Hunger: 2", "Lv 30: ~25,000 commands"]
    assert bare(pet.findChild(QLabel, "TagGood").text()) == "In Cash Shop"
    assert any(bare(w.text()) == "Closed test" for w in pet.findChildren(QLabel, "SourceTag"))


@pytest.mark.parametrize("lang", ["he", "en"])
def test_play_tools_build_and_bag_pages(real_site, isolated_store, lang):
    from PySide6.QtWidgets import QApplication, QFrame, QLabel
    app = QApplication.instance() or QApplication([])
    from maplehelper.ui.tools import PAGES, ToolsDialog
    p = isolated_store.Profiles()
    p.add("Kiwi", "Warrior", "Fighter", 35)
    d = ToolsDialog(real_site, p, isolated_store.Settings(), lang, "", {}, "build")
    d.show()
    app.processEvents()
    # the SP table: a chip at the first mention of a changed skill, its changes on hover
    html = d.build_view.toHtml()
    assert html.count("change:skill/fighter__final-attack-axe") == 1 and "change:skill/fighter__rage" in html
    assert "change:skill/page__" not in html
    shown = []
    from maplehelper.ui import terms
    real = terms.show_html
    terms.show_html = lambda body, rtl=False: shown.append(body)
    try:
        d._skill_change_tip("change:skill/fighter__rage")
    finally:
        terms.show_html = real
    assert shown and "35" in shown[0] and "40" in shown[0]
    # the community tier card: one row for a Fighter, a grade per column
    cards = d.build_tier.itemAt(0).widget().findChildren(QFrame, "Card")
    assert len(cards) == 1                                     # a card a 2nd job (a Bowman gets two)
    card = cards[0]
    assert isinstance(card, QFrame) and any(bare(w.text()) in ("קהילה", "Community") for w in card.findChildren(QLabel, "SourceTag"))
    assert len(card.findChildren(QLabel, "TagGood")) + len(card.findChildren(QLabel, "TagWarn")) + \
        len([w for w in card.findChildren(QLabel, "Tag")]) == 8
    # the bag page: pets sold now first, then easiest to level, then all twelve
    d.show_page(PAGES.index("more"))
    app.processEvents()

    def names():
        return [bare(d.pet_list.itemAt(i).widget().findChild(QLabel, "CardName").text())
                for i in range(d.pet_list.count())]
    assert names() == ["Brown Kitty", "Pink Bunny", "Husky"]
    d.pet_filter.group.buttons()[1].click()
    assert names()[0] == "Black Pig" and len(names()) == 12
    d.pet_filter.group.buttons()[2].click()
    assert names()[0] == "Brown Kitty" and names()[-1] == "Snail"
    d.close()


def test_a_skill_change_note_reads_in_hebrew_when_translated(real_site):
    """The tooltip showed NiaMeowDB's English note under "(in English)" and read as a jumble (the owner's report)."""
    from maplehelper.i18n import I18n
    from maplehelper.sitedata import SkillChange, change_tip, note_he
    ch = sitedata.skill_change(real_site, "skill/assassin__drain")
    tip = bare(change_tip(I18n("he"), ch))
    assert "בסיכוי של 12%" in tip and "באנגלית" not in tip and "always absorbed" not in tip
    # NiaMeowDB rewrote the note since: the English comes back, said to be English
    changed = SkillChange(**{**ch.__dict__, "note": ch.note + " (updated)"})
    assert note_he(changed) is None and "always absorbed" in change_tip(I18n("he"), changed)


@needs_kb
def test_a_first_jobs_table_of_both_paths_splits_into_one_a_path():
    # "Bowman" over a table of Bow and Crossbow columns read as one job: a table a path, the shared columns in each
    from maplehelper import buildplan
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    _, tables = buildplan.tables(kb, "Bowman", "Bowman", 20, "en")
    parts = [(path, part) for tb in tables for path, part in buildplan.split_paths(kb, tb)]
    ap = [(path, part.rows[0]) for path, part in parts if part.kind == "ap"]
    assert [p for p, _ in ap] == ["Bow", "Crossbow"]
    assert all(not any("Crossbow" in h for h in head) for p, head in ap if p == "Bow")
    assert all(head[0].startswith("Level") for _, head in ap)
    _, tables = buildplan.tables(kb, "Thief", "Thief", 20, "en")
    assert all(path == "" for tb in tables for path, _ in buildplan.split_paths(kb, tb))


def test_the_pets_launch_lifespan_note_is_the_kbs_own(site_kb):
    """The note came from a string in code; the pets page says it, and says it again when it changes."""
    real = Path(__file__).resolve().parent.parent / "data" / "kb"
    for kb in (KnowledgeBase(site_kb), *([KnowledgeBase(real)] if (real / "index.json").exists() else [])):
        page = kb.page("formula/pets") if kb.get("formula/pets") else ""
        m = re.search(r"Lifespans? at launch[^.\n]*\.", page, re.I)
        closed = [ln for ln in sitedata.ai_pet_lines(kb) if "closed-test value" in ln]
        assert closed and all((f"NiaMeowDB: {m.group(0)})" if m else "(closed-test value)") in ln for ln in closed)
    assert "30 to 90 days" not in Path(sitedata.__file__).read_text(encoding="utf-8")
