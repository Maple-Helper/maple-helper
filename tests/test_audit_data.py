"""Game-data audit fixes: quest rewards by class and gender, quest pre-requisites, citizenship openers, COT2
shop prices, the KPQ guide pick, dead guide links and glued text in the shipped guides.

Checked against the real knowledge base when it is present (data/kb)."""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import guides, market, quests, sources
from maplehelper.i18n import I18n
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
t = I18n("en")


@pytest.fixture(scope="module")
def real():
    return KnowledgeBase(REAL_KB)


# ------------------------------------------------------------------ quest rewards by class / gender

@needs_kb
def test_class_prefixed_rewards_go_to_their_class(real):
    # pages/quest/10007.md: "Beginner Work Gloves x 1 Warrior Dark Knuckle x 1 Magician Dark Arten x 1 ..."
    q = quests.quest(real, "quest/10007")
    assert q.rewards == []
    assert q.rewards_pick("Thief") == ["Dark Cleave x 1"]
    assert q.rewards_pick("Warrior") == ["Dark Knuckle x 1"]
    assert q.rewards_pick("Beginner") == ["Work Gloves x 1"]
    for item in ("Dark Cleave", "Dark Knuckle", "Work Gloves"):
        assert item.lower() in real._item_by_name          # the real item names, so their pictures are found
    q = quests.quest(real, "quest/10308")
    assert q.rewards == [] and q.rewards_pick("Magician") == ["Silver Wind Shoes x 1"]


@needs_kb
def test_an_item_named_after_a_class_stays_a_reward(real):
    # "Warrior Potion" is an item of its own (pages/quest/10208.md), not a Warrior's "Potion"
    assert "Warrior Potion x 5" in quests.quest(real, "quest/10208").rewards


@needs_kb
def test_gender_rewards_are_not_promised_to_everyone(real):
    # pages/quest/10508.md: "Male Blue Sauna Robe x 1 Female Red Sauna Robe x 1"
    q = quests.quest(real, "quest/10508")
    assert q.rewards == []
    assert q.gender_rewards == {"Male": ["Blue Sauna Robe x 1"], "Female": ["Red Sauna Robe x 1"]}
    assert q.rewards_gender(t) == ["Blue Sauna Robe x 1 (male character)", "Red Sauna Robe x 1 (female character)"]
    assert q.matches("sauna robe")
    for q in (quests.quest(real, k) for k, e in real.entities.items() if e.get("category") == "quest"):
        assert not any(r.split(" ", 1)[0] in ("Male", "Female") for r in q.rewards), q.key
        assert not any(r.split(" ", 1)[0] in quests.CLASSES and r.rsplit(" x ", 1)[0].lower() not in real._item_by_name
                       for r in q.rewards), q.key


# ------------------------------------------------------------------ pre-requisites

@needs_kb
def test_fame_and_fee_prerequisites_are_kept(real):
    q = quests.quest(real, "quest/10401")                   # "Fame 10 +"
    assert q.min_fame == 10 and "Needs at least 10 Fame" in q.prereq_hints(t)
    q = quests.quest(real, "quest/10303")                   # "Pay 1,000 mesos to accept."
    assert q.accept_cost == 1000 and "Costs 1,000 mesos to accept" in q.prereq_hints(t)
    # every pre-requisite line ends up somewhere: a field, or word for word in the notes
    q = quests.quest(real, "quest/10401")
    assert q.notes == [] and q.afters == ["Fixing Blackbull's House"]


def test_unknown_prerequisite_lines_are_kept_word_for_word(tmp_path):
    page = ("---\n{}\n---\n\n# Q\n\nPre-requisites\nLevel Lv. 10+\nMust not already have: Pale Maple Leaf\n"
            "Rewards\n100 EXP\n")
    (tmp_path / "pages" / "quest").mkdir(parents=True)
    (tmp_path / "pages" / "quest" / "1.md").write_text(page, encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps([{"key": "quest/1", "name": "Q", "category": "quest",
                                                      "props": {"Minimum Level": 10}}]), encoding="utf-8")
    (tmp_path / "aliases.json").write_text("{}", encoding="utf-8")
    q = quests.quest(KnowledgeBase(tmp_path), "quest/1")
    assert q.notes == ["Must not already have: Pale Maple Leaf"] and q.prereq_hints(t) == q.notes


def test_rewards_table_lists_every_quest_reward_with_its_key(tmp_path):
    # "which quests give a cape" took Claude 35 tool calls over the item pages: rewards.tsv answers it in one grep
    page = ("---\n{}\n---\n\n# Q\n\nRewards\n100 EXP\nOld Raggedy Cape x 1\nRandom reward - one of:\n"
            "Green Icarus Cape x 1 50 % Blue Icarus Cape x 1 50 %\nDescription\n")
    (tmp_path / "pages" / "quest").mkdir(parents=True)
    (tmp_path / "pages" / "quest" / "1.md").write_text(page, encoding="utf-8")
    items = [{"key": f"item/{i}", "name": n, "category": "item", "type": "Equip / Cape"}
             for i, n in ((1, "Old Raggedy Cape"), (2, "Green Icarus Cape"), (3, "Blue Icarus Cape"))]
    (tmp_path / "index.json").write_text(json.dumps(items + [{"key": "quest/1", "name": "Q", "category": "quest",
                                                              "props": {"Minimum Level": 23, "Area": "Kerning City"}}]),
                                         encoding="utf-8")
    (tmp_path / "aliases.json").write_text("{}", encoding="utf-8")
    KnowledgeBase(tmp_path).ensure_drop_table()
    rows = [r.split("\t") for r in (tmp_path / "rewards.tsv").read_text(encoding="utf-8").splitlines()]
    assert rows[0][:5] == ["quest", "quest_level", "quest_key", "area", "item"]
    assert [(r[4], r[7], r[8]) for r in rows[1:]] == [("Old Raggedy Cape", "item/1", "sure"),
                                                      ("Green Icarus Cape", "item/2", "random 50%"),
                                                      ("Blue Icarus Cape", "item/3", "random 50%")]
    assert all(r[:4] == ["Q", "23", "quest/1", "Kerning City"] and r[6] == "Equip / Cape" for r in rows[1:])


# ------------------------------------------------------------------ citizenship

@needs_kb
def test_citizenship_openers_and_npcs_without_a_town(real):
    henesys = [q.name for q in quests.citizenship(real, "Henesys", 60)]
    kerning = [q.name for q in quests.citizenship(real, "Kerning City", 60)]
    assert "To Henesys, the Prairie Town" in henesys                  # self-started, no NPC (quest/506000)
    assert "To the Gray City, Kerning City" in kerning                # Henesys' Arthur sends you to Kerning
    assert "To the Gray City, Kerning City" not in henesys
    assert {"Stirge Phobia", "Grandfather's Vitamin Gummy"} <= set(kerning)      # Jake, Mr. Goldstein
    lost = [k for k, e in real.entities.items() if e.get("category") == "quest"
            and (e.get("props") or {}).get("Area") == "Citizenship" and not quests.town_of(real, quests.quest(real, k))]
    assert lost == []


# ------------------------------------------------------------------ COT2 shop prices

@needs_kb
def test_shop_prices_keep_the_kbs_cot2_label(real):
    # pages/item/274.md: "3,000 / mesos / COT2 prices Citizen of Honor +"
    p = market.npc_prices(real, "item/274")
    assert p.shops and all(p.test_price(s) and p.source(s) == "COT2" for s in p.shops)
    assert "COT2" in t("price_hint") and "COT2" in I18n("he")("price_hint")
    assert sources.price_note(t, "COT2") == "(COT2 test price)"


# ------------------------------------------------------------------ guides

@needs_kb
def test_kpq_guide_is_picked_past_level_30(real):
    # kerning-city-party-quest-kpq-guide.md: "You unlock KPQ at level 21. There doesn't appear to be any level cap"
    assert "There doesn't appear to be any level cap" in real.page("guide/kerning-city-party-quest-kpq-guide")
    kpq = "guide/kerning-city-party-quest-kpq-guide"
    pick = lambda lv: guides.for_you(real, SimpleNamespace(level=lv, base_class="Thief", job="Assassin"))  # noqa: E731
    assert kpq not in pick(20) and kpq in pick(21) and kpq in pick(31) and kpq in pick(55)


def test_links_to_unbuilt_guides_are_not_shown():
    html = guides.book_html({"lang": "en", "blocks": [{"guide": "best-buy-shop-efficiency", "text": "Best Buy"},
                                                      {"guide": "fighter-class-guide", "text": "Fighter"}]})
    assert "best-buy-shop-efficiency" not in html and "guide:fighter-class-guide" in html


def test_every_shipped_guide_link_opens_a_guide():
    for f in (ROOT / "assets" / "guides" / "en").glob("*.json"):
        for b in json.loads(f.read_text(encoding="utf-8"))["blocks"]:
            if "guide" in b and b["guide"] != "best-buy-shop-efficiency":     # the site's own page, hidden
                assert guides.has_book(b["guide"]), (f.stem, b["guide"])


GLUED = re.compile(r"\d{4}Starts|\d\.\d(MIN|MAX|Magic)|\)(MIN|MAX)|MATKMIN|\*\*\d+[A-Za-z֐-׿]{3}|:timingTier")


def test_shipped_guides_have_no_glued_text():
    for lang in ("en", "he"):
        for f in (ROOT / "assets" / "guides" / lang).glob("*.json"):
            text = json.dumps(json.loads(f.read_text(encoding="utf-8")).get("blocks", []), ensure_ascii=False)
            assert not GLUED.search(text), (lang, f.stem, GLUED.search(text).group(0))
    en = json.loads((ROOT / "assets/guides/en/explaining-the-damage-formula.json").read_text(encoding="utf-8"))
    he = json.loads((ROOT / "assets/guides/he/explaining-the-damage-formula.json").read_text(encoding="utf-8"))
    assert {"p": "MIN = K × TotalWATK ×\n(0.8 + (P × m × W + Q + 2A) / 100)"} in en["blocks"]
    assert he["source_hash"] == en["hash"] and len(he["blocks"]) == len(en["blocks"])     # still in step


def _builder():
    sys.path.insert(0, str(ROOT / "tools"))
    import build_guides as bg
    return bg


def test_builder_keeps_formula_lines_bold_lines_and_hidden_separators(monkeypatch, tmp_path):
    bg = _builder()
    images = bg.Images(tmp_path)
    monkeypatch.setattr(images, "get", lambda src, max_w=360: None)
    page = ("<html><body><main><h1>G</h1><p>Intro.</p>"
            "<p><time>October 21, 2026</time><span aria-hidden=\"true\"> · </span>Starts 11:00 AM PDT</p>"
            "<div><pre>K = SkillDamage / 100\nm = 0.8</pre><pre>MIN = K ×\n  (0.8)</pre></div>"
            "<div><span><strong>MIN</strong> = <strong>60</strong></span><br/>"
            "<span><strong>MAX</strong> = 172</span></div>"
            "</main></body></html>")
    blocks = bg.convert(page, images)["blocks"]
    assert blocks == [{"p": "October 21, 2026 · Starts 11:00 AM PDT"},
                      {"p": "K = SkillDamage / 100\nm = 0.8"}, {"p": "MIN = K ×\n(0.8)"},
                      {"p": "**MIN** = **60**\n**MAX** = 172"}]
