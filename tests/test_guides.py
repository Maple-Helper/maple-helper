"""Guides library: the reader keeps only the article, guides sort into categories, picks fit the character."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import guides

PAGE = """---
{"name": "Assassin Guide", "category": "guide"}
---

# MapleStory Classic Assassin Guide: Lv 30-70

Assassin guide for levels 30-70.

Explore the database
Items Monsters Maps World Map Classes
Ad blocked? Fair. NiaMeowDB pays for catnip with ads.
Buy us a coffee →
Pros
Highest ranged damage.
Cons
Stars cost mesos.
Contents
Starting at level 30
Claws and stars
Starting at level 30
This guide continues from the Thief guide.
Claws and stars
Claw | Level | ATT
Steelguards | 30 | 15
"""


def test_parse_keeps_only_the_article():
    g = guides.parse("guide/assassin-class-guide", PAGE)
    assert g.title == "MapleStory Classic Assassin Guide: Lv 30-70"
    assert g.intro == "Assassin guide for levels 30-70."
    assert g.pros == ["Highest ranged damage."] and g.cons == ["Stars cost mesos."]
    assert [h for h, _ in g.sections] == ["Starting at level 30", "Claws and stars"]
    html = guides.to_html(g, {"pros": "Pros", "cons": "Cons"})
    assert "<table" in html and "<td>Steelguards</td>" in html
    assert "Explore the database" not in html and "catnip" not in html


MECH_PAGE = """---
{"name": "EXP Table", "category": "guide"}
---

# MapleStory Classic EXP Table: Levels 1-100

EXP requirements for levels 1-100.

Explore the database
Mechanics 5 min read · By Nia Meow
Damage Formula Atk Speed Attack Styles HP/MP Gain EXP Table Spawn Rate Shop Item Efficiency Every EXP requirement in MapleStory Classic World, level by level, with the formula behind it.
Contents
Per-level EXP requirement
The EXP Formula
Per-level EXP requirement
Level | EXP to next
1 | 15
The EXP Formula
EXP grows with level.
"""

HOLLOW_PAGE = """---
{"name": "Forgotten Hollow Guide", "category": "guide"}
---

# Forgotten Hollow Guide

Forgotten Hollow exists nowhere else in MapleStory.

Forgotten Hollow is a brand-new area that exists nowhere else in MapleStory, with its own fairy town.
Getting into the Hollow
The lore
Level range
Getting into the Hollow
The front door is The Tree Tunnel.
The lore
Grendel the Really Old
Level range
Levels 39 to 60.
"""


def test_parse_fallback_keeps_the_opening_and_finds_unlabeled_contents():
    # the reader's fallback for a guide the nightly KB adds before it has a book: the mechanics tabs glued onto
    # the first paragraph dropped it, and a contents list without "Contents" made one untitled section
    g = guides.parse("guide/exp-table-level-1-to-100", MECH_PAGE)
    assert [h for h, _ in g.sections] == ["", "Per-level EXP requirement", "The EXP Formula"]
    assert g.sections[0][1][0].startswith("Every EXP requirement in MapleStory Classic World")
    assert "Atk Speed" not in guides.to_html(g, {"pros": "Pros", "cons": "Cons"})
    g = guides.parse("guide/forgotten-hollow-the-new-endgame-area", HOLLOW_PAGE)
    assert [h for h, _ in g.sections] == ["", "Getting into the Hollow", "The lore", "Level range"]
    assert g.sections[2][1] == ["Grendel the Really Old"]


@pytest.mark.parametrize("key,cat", [("guide/fighter-class-guide", "classes"),
                                     ("guide/best-grind-maps-every-level", "leveling"),
                                     ("guide/weapon-reach", "mechanics"),
                                     ("guide/maplestory-classic-glossary", "general")])
def test_categories(key, cat):
    assert guides.category(key) == cat


REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"


@pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
def test_picks_for_a_character_start_with_their_job():
    from maplehelper.kb import KnowledgeBase
    kb = KnowledgeBase(REAL_KB)
    picks = guides.for_you(kb, SimpleNamespace(base_class="Thief", job="Assassin", level=34))
    assert picks[:2] == ["guide/assassin-class-guide", "guide/thief-class-guide"]
    assert "guide/best-grind-maps-every-level" in picks
    assert all(g.sections for g in (guides.parse(x["key"], kb.page(x["key"])) for x in guides.all_guides(kb)))


def test_every_guide_ships_whole_and_translated():
    """Each built guide has its pictures on disk and a Hebrew translation with the same blocks."""
    import json
    en_files = sorted((guides.TRANSLATIONS / "en").glob("*.json"))
    assert len(en_files) >= 30, "run tools/build_guides.py"
    for f in en_files:
        en = json.loads(f.read_text(encoding="utf-8"))
        names = set(guides._ICON.findall(json.dumps(en))) | {b["img"] for b in en["blocks"] if "img" in b}
        assert all((guides.IMAGES / n).exists() for n in names), f.stem
        he = json.loads((guides.TRANSLATIONS / "he" / f.name).read_text(encoding="utf-8"))
        assert [next(iter(b)) for b in he["blocks"]] == [next(iter(b)) for b in en["blocks"]], f.stem
        assert he["source_hash"] == en["hash"], f"{f.stem}: translation is stale"


ARTICLE = """<html><head><meta name="description" content="Short summary."></head><body><nav>Menu</nav><main>
<div><a href="/msclassic/guides">All guides</a><span>8 min read</span></div>
<header><p>Level 1 to 30 class guide</p><h1>Warrior Guide</h1><p>Warriors hit hard.</p>
<div><img src="/msclassic/classes/renders/warrior.png" width="130" height="160"></div></header>
<div data-nitro-ad="true"><p>Ad blocked? Fair.</p></div>
<nav aria-labelledby="contents-heading"><strong>Contents</strong><ol><li>Accuracy</li></ol></nav>
<section id="accuracy"><h2 id="accuracy">Accuracy
</h2><p>Your hit rate depends on <strong>level</strong>.</p>
<div class="callout"><strong>Fun fact:</strong> The <a href="/item/1">Club</a> is best.</div>
<div class="table-wrap"><table><thead><tr>
<th>Skill</th>
<th>Max</th></tr></thead><tbody><tr>
<td><img src="/icons/1.png">Power Strike</td>
<td>20</td>
</tr></tbody></table></div>
<div class="sm:hidden"><p>Phone copy</p></div>
<div class="link-row"><a class="button-link" href="/msclassic/tools/acc">Open Accuracy Simulator</a>
<a class="button-link" href="/msclassic/guides/fighter-class-guide">Continue with Fighter</a></div>
</section>
<div class="correction-loop"><strong>Spot a mistake?</strong></div>
<div><p>More guides</p><a href="/msclassic/guides/x"><h3>Other</h3></a></div>
</main></body></html>"""


def test_build_keeps_the_article_with_its_structure(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_guides as bg
    images = bg.Images(tmp_path)
    monkeypatch.setattr(images, "get", lambda src, max_w=360: (Path(src).stem + ".png", 32, 32) if "icons" in src
                        else (Path(src).stem + ".png", 130, 160))
    g = bg.convert(ARTICLE, images)
    assert g["title"] == "Warrior Guide" and g["intro"] == "Warriors hit hard." and g["hero"] == "warrior.png"
    assert g["blocks"] == [
        {"h2": "Accuracy"},
        {"p": "Your hit rate depends on **level**."},
        {"note": "**Fun fact:** The Club is best."},
        {"table": [["Skill", "Max"], ["[[img:1.png]]Power Strike", "20"]]},
        {"guide": "fighter-class-guide", "text": "Continue with Fighter"},
    ]


def test_build_drops_links_to_pages_that_are_not_guides_and_chart_controls(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_guides as bg
    images = bg.Images(tmp_path)
    monkeypatch.setattr(images, "get", lambda src, max_w=360: (Path(src).stem + ".png", 32, 32))
    page = ARTICLE.replace('<div class="sm:hidden">',
                           '<p>Target matchup</p><p>Tap a bar for build details. Warrior Bowman</p>'
                           '<a class="button-link" href="/msclassic/guides/best-buy-shop-efficiency">Compare</a>'
                           '<div class="sm:hidden">')
    g = bg.convert(page, images, known={"fighter-class-guide"})
    assert [b for b in g["blocks"] if "guide" in b] == [{"guide": "fighter-class-guide", "text": "Continue with Fighter"}]
    assert not [b for b in g["blocks"] if b.get("p", "").startswith(("Target matchup", "Tap a bar"))]


def test_build_holds_back_text_the_kb_page_does_not_have():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_guides as bg
    page = "# Warrior Guide\nWarriors hit hard.\nSkill Max\nPower Strike 20\nYour hit rate depends on level."
    g = {"title": "Warrior Guide", "intro": "Warriors hit hard.",
         "blocks": [{"table": [["Skill", "Max"], ["[[img:1.png]]Power Strike", "20"]]},
                    {"p": "Your hit rate depends on **level**."}, {"guide": "fighter-class-guide", "text": "Fighter"}]}
    assert bg.kb_gaps(g, page) == []
    g["blocks"].append({"ul": ["Taxis are 90% off for Beginners."]})      # newer on the site than in the KB
    assert bg.kb_gaps(g, page) == ["Taxis are 90% off for Beginners."]


def test_every_guide_link_opens_a_guide():
    """A {"guide": slug} block is drawn as a link: it must lead to a guide the reader has."""
    import json
    books = {f.stem for f in (guides.TRANSLATIONS / "en").glob("*.json")}
    dead = {(f.parent.name, f.stem, b["guide"]) for f in guides.TRANSLATIONS.glob("*/*.json")
            for b in json.loads(f.read_text(encoding="utf-8")).get("blocks", []) if "guide" in b and b["guide"] not in books}
    assert not dead


def test_translation_round_trip_keeps_blocks():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import translate_guides as tg
    en = {"title": "Guide", "intro": "Hi.", "hash": "h1",
          "blocks": [{"h2": "Skills"}, {"table": [["Skill", "Max"], ["Power Strike", "20"]]}, {"ul": ["Hit **hard**."]}]}
    assert tg.strings(en) == ["Guide", "Hi.", "Skills", "Skill", "Max", "Power Strike", "Hit **hard**."]
    he = tg.translate(en, {"Skills": "סקילים", "Hit **hard**.": "להכות **חזק**."})
    assert he["blocks"][0] == {"h2": "סקילים"} and he["blocks"][1]["table"][1] == ["Power Strike", "20"]
    assert he["blocks"][2] == {"ul": ["להכות **חזק**."]}


def test_translation_import_follows_the_english_text_not_positions(monkeypatch, tmp_path):
    """The English guide is rebuilt between export and import: no translation lands on another sentence,
    and the result is marked outdated."""
    import json
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import translate_guides as tg
    monkeypatch.setattr(tg, "GUIDES", tmp_path / "guides")
    (tmp_path / "guides" / "en").mkdir(parents=True)
    en_file = tmp_path / "guides" / "en" / "g.json"

    def write_en(blocks, h):
        en_file.write_text(json.dumps({"title": "T", "intro": "Intro text", "hash": h, "blocks": blocks}), encoding="utf-8")
    write_en([{"p": "Level 10 drops 30 mesos"}, {"p": "Level 20 drops 50 mesos"}], "h1")
    tg.export("he", tmp_path / "out")
    ids = json.loads((tmp_path / "out" / "g.json").read_text(encoding="utf-8"))["strings"]
    (tmp_path / "out" / "g.json").write_text(json.dumps({i: f"HE[{s}]" for i, s in ids.items()}), encoding="utf-8")
    write_en([{"p": "New tip: buy potions"}, {"p": "Level 10 drops 30 mesos"}, {"p": "Level 20 drops 50 mesos"}], "h2")
    tg.import_("he", tmp_path / "out")
    he = json.loads((tmp_path / "guides" / "he" / "g.json").read_text(encoding="utf-8"))
    assert [b["p"] for b in he["blocks"]] == ["New tip: buy potions", "HE[Level 10 drops 30 mesos]",
                                             "HE[Level 20 drops 50 mesos]"]
    assert he["source_hash"] == "h1"                  # != the English "h2": the reader shows it as outdated


def test_reader_html_for_a_hebrew_guide():
    b = {"lang": "he", "title": "מדריך", "intro": "", "hero": "x.png",
         "blocks": [{"h2": "סקילים של Warrior"}, {"p": "[[img:a.png]] **Power Strike** פוגע ב-2 מובים"},
                    {"note": "הערה"}, {"guide": "fighter-class-guide", "text": "להמשיך ל-Fighter"},
                    {"table": [["סקיל", "Max"], ["Power Strike", "20"]]}, {"img": "y.png", "w": 100, "h": 50}]}
    h = guides.book_html(b, "dark")
    assert "dir='rtl'" in h and "<b>" in h and "a.png" in h and "href='guide:fighter-class-guide'" in h
    assert "[[img:" not in h and guides.NOTE_COLORS["dark"]["note"] in h and "y.png" in h and "<table" in h


def test_hovering_a_guide_picture_finds_it():
    import sys
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap, QTextCursor
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper.ui.guides import GuidesDialog, zoomed
    assert zoomed(QPixmap(32, 32)).size().toTuple() == (96, 96)
    assert zoomed(QPixmap(360, 200)).size().toTuple() == (720, 400)
    d = GuidesDialog(SimpleNamespace(entities={}, page=lambda k: "", get=lambda k: None), None, "en", "")
    d.resize(560, 900)
    d.show()
    d.open_guide("guide/warrior-class-guide")
    app.processEvents()
    b, c = d.browser, QTextCursor(d.browser.document())
    while not c.atEnd():
        n = QTextCursor(c)
        n.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        if n.charFormat().isImageFormat():
            break
        c.movePosition(QTextCursor.Right)
    img = n.charFormat().toImageFormat()
    want = img.name().rsplit("/", 1)[-1]
    # somewhere over the picture the hover finds it, and nowhere does it find another picture's name
    vp = b.viewport()
    hits = {d.zoom.image_at(QPoint(x, y)) for x in range(0, vp.width(), 4) for y in range(0, vp.height(), 4)}
    assert any(h and h.endswith(want) for h in hits)
    d.close()


def test_hovering_a_guide_cover_shows_it_large(tmp_path):
    import sys
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent, QImage
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from maplehelper.ui.guides import CoverPic
    img = QImage(1200, 630, QImage.Format_RGB32)
    img.fill(0)
    img.save(str(tmp_path / "cover.png"))
    pic = CoverPic(str(tmp_path / "cover.png"))
    QApplication.sendEvent(pic, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert pic.pop.isVisible() and pic.pop.pixmap().width() == 480
    QApplication.sendEvent(pic, QEvent(QEvent.Leave))
    assert not pic.pop.isVisible()
    app.processEvents()
