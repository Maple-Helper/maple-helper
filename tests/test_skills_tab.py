"""Play tools' "Build & Skills" page: the build plan as it was, and the Skills tab (a tab per class line, in it a tab
per job up to the 2nd, each skill with its picture, name, description, max level, prerequisite and its level 1 and
max level effects), with its Hebrew (assets/skills/he.json, then the nightly's kind "skill_desc")."""
import html
import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

from maplehelper import skillbook, translations  # noqa: E402

app = QApplication.instance() or QApplication([])
ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
ASSET = ROOT / "assets" / "skills" / "he.json"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
SECOND = {"Warrior": ["Fighter", "Page", "Spearman"], "Magician": ["F/P Wizard", "I/L Wizard", "Cleric"],
          "Bowman": ["Hunter", "Crossbowman"], "Thief": ["Assassin", "Bandit"]}
THIRD = {"Crusader", "White Knight", "Dragon Knight", "F/P Mage", "I/L Mage", "Priest", "Ranger", "Sniper", "Hermit",
         "Chief Bandit"}


def bare(text: str) -> str:
    return "".join(ch for ch in text if ch not in "\u200e\u200f\u202a\u202b\u202c\u2066\u2067\u2069\u2060")


def plain_text(label: QLabel) -> str:
    """A rich-text label's words, its tags and direction marks aside."""
    from maplehelper import glossary
    text = html.unescape(re.sub(r"<[^>]+>", "", label.text().replace(glossary.MARK, "")))    # its "?" aside
    text = re.sub(r"\s+", " ", bare(text))
    return re.sub(r" ([.,;:])", r"\1", text).strip()          # (the "?" left its space before a comma)


def pump(ms=60):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture(scope="module")
def real_kb():
    if not (REAL_KB / "index.json").exists():
        pytest.skip("no real knowledge base")
    from maplehelper.kb import KnowledgeBase
    return KnowledgeBase(REAL_KB)


# ------------------------------------------------------------------ the tabs and the skills

def test_five_class_tabs_each_with_its_1st_and_2nd_jobs_and_no_3rd():
    tabs = skillbook.class_jobs()
    assert [c for c, _ in tabs] == ["Beginner", "Warrior", "Magician", "Bowman", "Thief"]
    jobs = dict(tabs)
    assert jobs["Beginner"] == ["Beginner"]
    assert jobs["Thief"] == ["Thief", "Assassin", "Bandit"]
    for cls, second in SECOND.items():
        assert jobs[cls] == [cls] + second
    assert not THIRD & {j for js in jobs.values() for j in js}


def test_the_tab_to_open_on_is_the_characters_own():
    assert skillbook.default_tab("Thief", "Assassin") == ("Thief", "Assassin")
    assert skillbook.default_tab("Magician", "Magician") == ("Magician", "Magician")
    assert skillbook.default_tab("Thief", "Hermit") == ("Thief", "Assassin")     # a later job: the 2nd it came from
    assert skillbook.default_tab("Bowman", "Beginner") == ("Beginner", "Beginner")
    assert skillbook.default_tab(None, None) == ("Beginner", "Beginner")


@needs_kb
def test_every_shown_job_has_its_skills_each_with_all_its_parts(real_kb):
    book = skillbook.book(real_kb)
    counts = {j: len(book[j]) for _, js in skillbook.class_jobs(real_kb) for j in js}
    assert counts["Beginner"] == 3 and counts["Thief"] == 6 and counts["Assassin"] == 6 and counts["Bandit"] == 6
    assert counts["Fighter"] == counts["Page"] == counts["Spearman"] == 8
    assert sum(counts.values()) == 93
    for job, skills in book.items():
        assert not THIRD & {job}
        for s in skills:
            assert s.job == job and s.name and s.desc and s.lv1 and s.max and s.max_lv
            assert s.picture and s.picture.exists() and "fallback" not in s.picture.parts, s.key
            props = real_kb.get(s.key)["props"]
            assert (s.prereq is not None) == bool(props.get("Prerequisite")), s.key
            assert s.desc in real_kb.page(s.key) and s.max in real_kb.page(s.key)
    haste = next(s for s in book["Assassin"] if s.name == "Claw Booster")
    assert haste.prereq == ("Claw Mastery", 5) and haste.max_lv == 20
    assert haste.lv1.startswith("HP -30, MP -30") and haste.max.startswith("HP -10, MP -10")


# ------------------------------------------------------------------ Hebrew

@needs_kb
def test_the_hebrew_asset_covers_every_shown_text_made_from_its_english(real_kb):
    from maplehelper.brain import _LEVEL_WORD
    rows = json.loads(ASSET.read_text(encoding="utf-8"))
    shown = {k: en for skills in skillbook.book(real_kb).values() for s in skills
             for k, en in skillbook.texts(s).items()}
    assert len(shown) == 279 and set(rows) == set(shown)
    names = {s.name for skills in skillbook.book(real_kb).values() for s in skills}
    for key, en in shown.items():
        he = rows[key]["he"]
        assert rows[key]["en"] == en, key
        assert re.search(r"[א-ת]", he) and not _LEVEL_WORD.search(he), key
        assert sorted(re.findall(r"\d+", en)) == sorted(re.findall(r"\d+", he)), key     # numbers kept
        for name in names:          # a skill named in the English stays in English letters (one-word names
            if " " in name and re.search(rf"(?<![\w:]){re.escape(name)}(?![\w:])", en):    # are words too: "Focus to")
                assert name in he, (key, name)
        assert not re.search(r"(?:^|\s)[בהוכלמש]{1,2}[A-Za-z]", he), key        # a prefix takes a hyphen: "ב-Heal"
    # the app shows it, and only while the English holds
    s = next(s for s in skillbook.book(real_kb)["Assassin"] if s.name == "Haste")
    assert skillbook.text(real_kb, s, "desc", "he") == rows[s.key]["he"]
    assert skillbook.text(real_kb, s, "max", "en") == s.max
    from dataclasses import replace
    rewritten = replace(s, desc=s.desc + " Now stronger.")
    assert skillbook.text(real_kb, rewritten, "desc", "he") == rewritten.desc


def test_the_nightlys_skill_text_is_read_and_the_asset_wins_from_the_same_english(tmp_path):
    (tmp_path / "he.json").write_text(json.dumps({"skill_desc": {
        "skill/x": {"en": "Hits hard.", "he": "מכה חזק."},
        "skill/assassin__haste": {"en": json.loads(ASSET.read_text(encoding="utf-8"))["skill/assassin__haste"]["en"],
                                  "he": "תרגום של הלילה"}}}, ensure_ascii=False), encoding="utf-8")
    assert translations.he(tmp_path, "skill_desc", "skill/x", "Hits hard.") == "מכה חזק."
    assert translations.he(tmp_path, "skill_desc", "skill/x", "Hits harder.") is None
    asset = json.loads(ASSET.read_text(encoding="utf-8"))["skill/assassin__haste"]
    assert translations.he(tmp_path, "skill_desc", "skill/assassin__haste", asset["en"]) == asset["he"]


@needs_kb
def test_a_night_translates_a_rewritten_skill_text_into_the_kb(tmp_path, monkeypatch):
    import translate_kb

    from maplehelper.kb import KnowledgeBase
    kb = tmp_path / "kb"
    shutil.copytree(REAL_KB, kb, ignore=shutil.ignore_patterns("img", "pages"))
    shutil.copytree(REAL_KB / "pages" / "skill", kb / "pages" / "skill")
    page = kb / "pages" / "skill" / "bandit__savage-blow.md"
    old = "Use MP to attack an enemy up to 6 times in a row with a dagger."
    page.write_text(page.read_text(encoding="utf-8").replace(old, old.replace("a dagger", "a sharp dagger")),
                    encoding="utf-8")
    jobs = [j for j in translate_kb.missing(kb) if j["kind"] == "skill_desc"]
    assert [(j["key"], j["en"]) for j in jobs] == [("skill/bandit__savage-blow", old.replace("a dagger", "a sharp dagger"))]
    calls = []

    def fake(key, texts):                       # no API: a stand-in translation
        calls.append(list(texts))
        return ["תוקפים עד 6 פעמים ברצף בפגיון חד." for _ in texts]
    monkeypatch.setattr(translate_kb, "translate", fake)
    assert translate_kb.run(kb, "key", jobs) == 1 and calls == [[jobs[0]["en"]]]
    made = json.loads((kb / "he.json").read_text(encoding="utf-8"))["skill_desc"]
    assert made["skill/bandit__savage-blow"] == {"en": jobs[0]["en"], "he": "תוקפים עד 6 פעמים ברצף בפגיון חד."}
    fresh = KnowledgeBase(kb)
    s = next(s for s in skillbook.book(fresh)["Bandit"] if s.name == "Savage Blow")
    assert skillbook.text(fresh, s, "desc", "he") == "תוקפים עד 6 פעמים ברצף בפגיון חד."
    assert not [j for j in translate_kb.missing(kb) if j["kind"] == "skill_desc"]


# ------------------------------------------------------------------ the window

@pytest.fixture
def tools(isolated_store, real_kb):
    from maplehelper.ui.tools import ToolsDialog
    made = []

    def make(base="Thief", job="Assassin", level=35, lang="en"):
        p = isolated_store.Profiles()
        p.add("Kiwi", base, job, level)
        d = ToolsDialog(real_kb, p, isolated_store.Settings(), lang, "", {}, "build")
        made.append(d)
        d.show()
        pump()
        return d
    yield make
    for d in made:
        d.close()


def skills_tab(d):
    d.build_tabs.group.buttons()[1].click()
    pump()


def cards(d):
    return [w for w in d.build_stack.widget(1).widget().findChildren(QFrame, "Card") if w.property("skill")
            and w.isVisibleTo(d)]


def job_buttons(d):
    return [b for b in d.build_stack.widget(1).findChildren(QPushButton, "Segment") if b.isVisibleTo(d)]


@pytest.mark.parametrize("lang,name", [("he", "בילד וסקילים"), ("en", "Build & Skills")])
def test_the_page_is_build_and_skills_and_opens_on_the_build_plan(tools, lang, name):
    from maplehelper.ui.tools import PAGES
    d = tools(lang=lang)
    chip = d.nav.button(PAGES.index("build"))
    assert bare(chip.text()).replace("&&", "&") == name
    assert [bare(b.text()) for b in d.build_tabs.group.buttons()] == (["בילד", "סקילים"] if lang == "he"
                                                                     else ["Build", "Skills"])
    assert d.build_tabs.value() == "build" and d.build_stack.currentIndex() == 0
    assert not d.build_view.isHidden() and d.build_view.toPlainText()          # the build plan, as before


@needs_kb
@pytest.mark.parametrize("lang", ["he", "en"])
def test_skills_tab_shows_the_characters_job_with_every_part_of_each_skill(tools, lang):
    d = tools(lang=lang)
    skills_tab(d)
    assert d.build_stack.currentIndex() == 1
    classes = d.skills_class.buttons()
    assert [bare(b.text()) for b in classes] == ["Beginner", "Warrior", "Magician", "Bowman", "Thief"]
    assert [b.property("value") for b in classes if b.isChecked()] == ["Thief"]
    assert [bare(b.text()) for b in job_buttons(d)] == ["Thief", "Assassin", "Bandit"]
    assert [b.property("value") for b in job_buttons(d) if b.isChecked()] == ["Assassin"]
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    book = skillbook.book(d.kb)
    for cls, jobs in skillbook.class_jobs(d.kb):
        for job in jobs:
            d._pick_skills(cls, job)
            pump(20)
            shown = cards(d)
            assert [c.property("skill") for c in shown] == [s.key for s in book[job]], job
            for card, s in zip(shown, book[job]):
                pic = card.findChild(QLabel, "SkillPicture")
                assert pic.pixmap() is not None and not pic.pixmap().isNull(), s.key
                assert bare(card.findChild(QLabel, "CardName").text()) == s.name
                parts = {lb.property("part"): plain_text(lb) for lb in card.findChildren(QLabel) if lb.property("part")}
                assert set(parts) == {"desc", "lv1", "max"} | ({"needs"} if s.prereq else set()), s.key
                tags = [bare(t.text()) for t in card.findChildren(QLabel, "Tag")]
                assert ("רמה מקסימלית " if lang == "he" else "Max level ") + str(s.max_lv) in tags
                if lang == "he":
                    assert asset[s.key]["he"].split(" ")[0] in parts["desc"]
                    assert asset[s.key + "#max"]["he"].split(";")[0] in parts["max"]
                    assert parts["max"].startswith(f"ברמה {s.max_lv} (מקסימום):")
                    if s.prereq:
                        assert parts["needs"] == f"דורש {s.prereq[0]} ברמה {s.prereq[1]}"
                else:
                    assert re.sub(r"\s+", " ", s.desc) in parts["desc"] and s.lv1 in parts["lv1"]
                    assert parts["lv1"].startswith("Level 1:") and parts["max"].startswith(f"Level {s.max_lv} (max):")
                    if s.prereq:
                        assert parts["needs"] == f"Requires {s.prereq[0]} at level {s.prereq[1]}"
                assert card.findChild(QPushButton, "Link")          # "Ask in chat", as on the other cards


@needs_kb
def test_a_class_tab_remembers_its_job_while_the_window_is_open(tools):
    d = tools()
    skills_tab(d)
    d._pick_skills("Thief", "Bandit")
    d.skills_class.button(1).click()                 # Warrior: its 1st job
    pump()
    assert d._skills_pick == ("Warrior", "Warrior")
    d.skills_class.button(4).click()                 # back to Thief: on Bandit again
    pump()
    assert d._skills_pick == ("Thief", "Bandit")
    assert [b.property("value") for b in job_buttons(d) if b.isChecked()] == ["Bandit"]
    d.show_page(0)
    from maplehelper.ui.tools import PAGES
    d.show_page(PAGES.index("build"))                # another page and back: the same tabs
    assert d.build_tabs.value() == "skills" and d._skills_pick == ("Thief", "Bandit")


@needs_kb
def test_a_beginner_opens_on_beginner_and_tabs_read_right_to_left_by_keyboard(tools):
    d = tools("Beginner", "Beginner", 8, lang="he")
    skills_tab(d)
    assert d._skills_pick == ("Beginner", "Beginner") and not job_buttons(d)
    assert len(cards(d)) == 3
    d._pick_skills("Magician", None)
    pump()
    classes, jobs = d.skills_class.buttons(), job_buttons(d)
    for row in (classes, jobs):
        assert all(b.focusPolicy() & Qt.TabFocus for b in row)
        xs = [b.mapTo(d, b.rect().center()).x() for b in row]
        assert xs == sorted(xs, reverse=True)            # the first tab on the right, the next to its left


@needs_kb
@pytest.mark.parametrize("lang", ["he", "en"])
def test_skills_tab_fits_the_narrow_window(tools, lang):
    d = tools(lang=lang)
    skills_tab(d)
    area = d.build_stack.widget(1)
    for width in (580, 480):
        d.resize(width, 800)
        for cls, jobs in skillbook.class_jobs(d.kb):
            d._pick_skills(cls, jobs[-1])
            pump(40)
            assert area.widget().minimumSizeHint().width() <= area.viewport().width(), (cls, width)
            for b in d.skills_class.buttons() + job_buttons(d):
                assert b.width() >= b.fontMetrics().horizontalAdvance(bare(b.text())), (bare(b.text()), width)
