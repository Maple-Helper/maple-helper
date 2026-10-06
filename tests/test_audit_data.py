"""Game-data audit fixes: quest rewards by class and gender, quest pre-requisites, citizenship openers, COT2
shop prices, the KPQ guide pick, dead guide links and glued text in the shipped guides.

Checked against the real knowledge base when it is present (data/kb)."""
import json
import re
import sys
import warnings
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


# the real pages these parsers were fixed on (pages/quest/<id>.md), frozen: the exact rewards stay pinned here, while
# the real KB is checked for what holds on every page, so NiaMeowDB editing one quest never holds the nightly back
FROZEN_QUESTS = {
    "quest/10007": ("[Deep Forest of Patience] John's Present", 45, "Blue Viola x 20", "6,581 EXP 1,316 Mesos\n"
                    "Beginner Work Gloves x 1 Warrior Dark Knuckle x 1 Magician Dark Arten x 1 Bowman Dark Brace x 1 "
                    "Thief Dark Cleave x 1"),
    "quest/10308": ("Shoes for Cutthroat Manny", 22, "Defeat Evil Eye x 50 Evil Eye Tail x 30", "2,306 EXP 643 Mesos\n"
                    "Beginner Red Whitebottom Boots x 1 Warrior Mithril War Boots x 1 Magician Silver Wind Shoes x 1 "
                    "Bowman Brown Jack Boots x 1 Thief Blue Lappy Boots x 1"),
    "quest/10208": ("Third Material Delivery", 41, "Defeat Fire Boar x 50", "5,568 EXP 1,199 Mesos\n"
                    "Warrior Potion x 5 Blue Potion x 30"),
    "quest/10508": ("Returned Secret Book", 32, "Secret Book x 1", "3,487 EXP 936 Mesos\n"
                    "Male Blue Sauna Robe x 1 Female Red Sauna Robe x 1"),
    "quest/10401": ("Building a New House For Blackbull", 48, "Screw x 30", "7,425 EXP 1,404 Mesos + 2 Fame\n"
                    "Pick one (class-specific):\nMagician\nWand Magic Attack Scroll: Greater x 1"),
    "quest/10303": ("Nella's Commission", 10, "Orange Mushroom Cap x 10", "171 EXP 292 Mesos\nLemon x 15"),
}
FROZEN_PREREQS = {"quest/10401": "Fame 10 +\nQuest Complete Fixing Blackbull's House\n",
                  "quest/10303": "Pay 1,000 mesos to accept.\n"}
FROZEN_ITEMS = ("Work Gloves", "Dark Knuckle", "Dark Arten", "Dark Brace", "Dark Cleave", "Red Whitebottom Boots",
                "Mithril War Boots", "Silver Wind Shoes", "Brown Jack Boots", "Blue Lappy Boots", "Warrior Potion",
                "Blue Potion", "Blue Sauna Robe", "Red Sauna Robe", "Lemon", "Wand Magic Attack Scroll: Greater")


@pytest.fixture
def frozen(tmp_path):
    """A KB of those quest pages as the real KB wrote them, and the items they give."""
    entities, pages = [], tmp_path / "pages"
    (pages / "quest").mkdir(parents=True)
    for key, (name, level, needs, rewards) in FROZEN_QUESTS.items():
        entities.append({"key": key, "name": name, "category": "quest",
                         "props": {"Minimum Level": level, "Area": "Victoria Island"}})
        (pages / "quest" / f"{key.split('/')[1]}.md").write_text(
            f"---\n{{}}\n---\n\n# {name}\n\nPre-requisites\nLevel Lv. {level}+\n{FROZEN_PREREQS.get(key, '')}"
            f"Requirements\n{needs}\nRewards\n{rewards}\nDescription\n01 Words.\n", encoding="utf-8")
    entities += [{"key": f"item/{n}", "name": name, "category": "item", "props": {}}
                 for n, name in enumerate(FROZEN_ITEMS, 1)]
    (tmp_path / "index.json").write_text(json.dumps(entities), encoding="utf-8")
    (tmp_path / "aliases.json").write_text("{}", encoding="utf-8")
    quests._quest.cache_clear()
    yield KnowledgeBase(tmp_path)
    quests._quest.cache_clear()


def _quests(kb):
    return [q for q in (quests.quest(kb, k) for k, e in kb.entities.items() if e.get("category") == "quest") if q]


# ------------------------------------------------------------------ quest rewards by class / gender

def test_class_prefixed_rewards_go_to_their_class(frozen):
    # pages/quest/10007.md: "Beginner Work Gloves x 1 Warrior Dark Knuckle x 1 Magician Dark Arten x 1 ..."
    q = quests.quest(frozen, "quest/10007")
    assert q.rewards == []
    assert q.rewards_pick("Thief") == ["Dark Cleave x 1"]
    assert q.rewards_pick("Warrior") == ["Dark Knuckle x 1"]
    assert q.rewards_pick("Beginner") == ["Work Gloves x 1"]
    q = quests.quest(frozen, "quest/10308")
    assert q.rewards == [] and q.rewards_pick("Magician") == ["Silver Wind Shoes x 1"]


def test_an_item_named_after_a_class_stays_a_reward(frozen):
    # "Warrior Potion" is an item of its own (pages/quest/10208.md), not a Warrior's "Potion"
    assert "Warrior Potion x 5" in quests.quest(frozen, "quest/10208").rewards


def test_gender_rewards_are_not_promised_to_everyone(frozen):
    # pages/quest/10508.md: "Male Blue Sauna Robe x 1 Female Red Sauna Robe x 1"
    q = quests.quest(frozen, "quest/10508")
    assert q.rewards == []
    assert q.gender_rewards == {"Male": ["Blue Sauna Robe x 1"], "Female": ["Red Sauna Robe x 1"]}
    assert q.rewards_gender(t) == ["Blue Sauna Robe x 1 (male character)", "Red Sauna Robe x 1 (female character)"]
    assert q.matches("sauna robe")


@needs_kb
def test_class_and_gender_rewards_on_every_real_quest(real):
    """What holds on every page, whatever NiaMeowDB rewrites: no class or gender word left glued to a sure reward,
    and a class's pick is the real items (so their pictures are found)."""
    rows = _quests(real)
    assert len(rows) > 200
    for q in rows:
        assert not any(r.split(" ", 1)[0] in ("Male", "Female") for r in q.rewards), q.key
        assert not any(r.split(" ", 1)[0] in quests.CLASSES and r.rsplit(" x ", 1)[0].lower() not in real._item_by_name
                       for r in q.rewards), q.key
    picks = [r.rsplit(" x ", 1)[0] for q in rows for cls in quests.CLASSES for r in q.rewards_pick(cls)]
    assert picks and sum(p.lower() in real._item_by_name for p in picks) > 0.95 * len(picks)
    assert any(q.gender_rewards for q in rows) and any(q.class_rewards for q in rows)    # the parsers still find them


# ------------------------------------------------------------------ pre-requisites

def test_fame_and_fee_prerequisites_are_kept(frozen):
    q = quests.quest(frozen, "quest/10401")                 # "Fame 10 +"
    assert q.min_fame == 10 and "Needs at least 10 Fame" in q.prereq_hints(t)
    q = quests.quest(frozen, "quest/10303")                 # "Pay 1,000 mesos to accept."
    assert q.accept_cost == 1000 and "Costs 1,000 mesos to accept" in q.prereq_hints(t)
    # every pre-requisite line ends up somewhere: a field, or word for word in the notes
    q = quests.quest(frozen, "quest/10401")
    assert q.notes == [] and q.afters == ["Fixing Blackbull's House"]


@needs_kb
def test_fame_and_fee_prerequisites_match_every_real_page(real):
    """Each page's own "Fame N +" / "Pay N mesos to accept." line, read here on its own, is the quest's field."""
    fame = re.compile(r"^Fame ([\d,]+) \+", re.M)
    fee = re.compile(r"^Pay ([\d,]+) mesos to accept", re.M | re.I)
    seen = 0
    for q in _quests(real):
        pre = real.page(q.key).split("Pre-requisites", 1)[-1].split("Requirements", 1)[0]
        f, c = fame.search(pre), fee.search(pre)
        assert q.min_fame == (int(f.group(1).replace(",", "")) if f else 0), q.key
        assert q.accept_cost == (int(c.group(1).replace(",", "")) if c else 0), q.key
        seen += bool(f or c)
    assert seen                                              # the pages still have some, so the check checks


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

def test_citizenship_openers_and_npcs_without_a_town():
    """The openers name their town ("To Henesys, the Prairie Town" is self-started; "To the Gray City, Kerning City"
    is handed out by Henesys' Arthur), and Jake's / Mr. Goldstein's pages name no town: their grade does."""
    npc_pages = {"npc/1": "# Arthur\nLocation\nHenesys\n", "npc/2": "# Jake\nA miner.\n"}
    kb = SimpleNamespace(_npc_by_name={"arthur": "npc/1", "jake": "npc/2"}, page=lambda k: npc_pages.get(k, ""))
    Q = quests.Quest
    assert quests.town_of(kb, Q("quest/506000", "To Henesys, the Prairie Town", 1, self_start=True)) == "Henesys"
    assert quests.town_of(kb, Q("quest/506100", "To the Gray City, Kerning City", 1, npc="Arthur")) == "Kerning City"
    assert quests.town_of(kb, Q("quest/3", "Stirge Phobia", 20, npc="Jake", grade=("Kerning City", 3))) == "Kerning City"
    assert quests.town_of(kb, Q("quest/4", "Errand", 20, npc="Arthur")) == "Henesys"


@needs_kb
def test_every_real_citizenship_quest_has_one_town(real):
    lost = [k for k, e in real.entities.items() if e.get("category") == "quest"
            and (e.get("props") or {}).get("Area") == "Citizenship" and not quests.town_of(real, quests.quest(real, k))]
    assert lost == []
    by_town = {town: {q.key for q in quests.citizenship(real, town, 200)} for town in quests.TOWNS}
    assert by_town["Henesys"] and by_town["Kerning City"]
    keys = [k for ks in by_town.values() for k in ks]
    assert len(keys) == len(set(keys))                       # a quest counts for one town only
    for town, ks in by_town.items():                         # an opener that names one town is that town's
        for k in ks:
            named = [x for x in quests.TOWNS if re.search(rf"\b{re.escape(x)}\b", real.get(k)["name"])]
            assert len(named) != 1 or named == [town] or quests.quest(real, k).grade, k


# ------------------------------------------------------------------ COT2 shop prices

@needs_kb
def test_shop_prices_keep_the_kbs_cot2_label(real):
    # pages/item/274.md: "3,000 / mesos / COT2 prices Citizen of Honor +": every item page whose shops say "COT2
    # prices" keeps the label (whichever items carry it tonight)
    labelled = [k for k, e in real.entities.items() if e.get("category") == "item"
                and "\nCOT2 prices" in real.page(k).split("Where to buy", 1)[-1].split("Dropped By", 1)[0]][:40]
    assert labelled
    for k in labelled:
        p = market.npc_prices(real, k)
        assert p.shops and any(p.test_price(s) and p.source(s) == "COT2" for s in p.shops), k
    assert "COT2" in t("price_hint") and "COT2" in I18n("he")("price_hint")
    assert sources.price_note(t, "COT2") == "(COT2 test price)"


# ------------------------------------------------------------------ guides

@needs_kb
def test_kpq_guide_is_picked_past_level_30(real):
    kpq = "guide/kerning-city-party-quest-kpq-guide"
    # guides.for_you opens it at Lv. 21 with no cap, as the guide says ("You unlock KPQ at level 21. There doesn't
    # appear to be any level cap"): a rewritten guide is reported for the app's rule, it doesn't hold the KB back
    page = real.page(kpq)
    assert page
    if not re.search(r"level 21\b", page, re.I) or "level cap" not in page:
        warnings.warn("the KPQ guide no longer says 'level 21' / no level cap: check guides.for_you", stacklevel=2)
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
