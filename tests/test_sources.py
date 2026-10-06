"""Source tags: every datum the app shows says where it comes from, in the knowledge base's own classification
(sources.py), the drop lists split by list (kb.drop_lists), the "Updated" chip and the update notes that put the
player's own changes first (recent.py).

The coverage tests at the end scan every page of the real knowledge base (data/kb) when it is present."""
import json
import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from maplehelper import bidi, brain, market, quick, recent, sources
from maplehelper.i18n import I18n
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")
en, he = I18n("en"), I18n("he")
# one "today" for the change log the fixture writes and what the tests expect of it: a run crossing midnight between
# the two no longer fails
TODAY = date.today()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def page(name: str, body: str, props: dict | None = None) -> str:
    return "---\n" + json.dumps({"name": name, "props": props or {}}) + "\n---\n\n# " + name + "\n\n" + body


def make_kb(tmp_path: Path, entries: list[tuple[str, str, dict, str]], log: list[dict] | None = None) -> KnowledgeBase:
    """A tiny KB: (key, name, props, page body) each; a changelog.json beside it when given."""
    index = []
    for key, name, props, body in entries:
        cat, _, slug = key.partition("/")
        etype = props.pop("_type", "Monster" if cat == "monster" else None)
        index.append({"key": key, "name": name, "category": cat, "props": props, "type": etype})
        (tmp_path / "pages" / cat).mkdir(parents=True, exist_ok=True)
        (tmp_path / "pages" / cat / f"{slug}.md").write_text(page(name, body, props), encoding="utf-8")
    (tmp_path / "index.json").write_text(json.dumps(index), encoding="utf-8")
    if log is not None:
        (tmp_path / "changelog.json").write_text(json.dumps(log), encoding="utf-8")
    return KnowledgeBase(tmp_path)


SNAIL = """Drops (MS Classic)
Community sourced
Items players have personally seen drop in-game. Upvote what you've seen, downvote what you haven't.
Red Potion
Potion
MSEA reference drops
Read-only · 2 items
Classic World appears to draw from the pre-Big-Bang MapleSEA drop tables. This list is historical reference only.
Snail Shell
Monster Drop
Red Potion
Potion
Map Locations ( 1 )
Map | Count ↓ | Share ↓ | Types ↓ | Mob Rate | Respawn
Snail Hunting Ground I Maple Road | 9 | 9 / 9 100 % | 1 | 1.0 x | ~7.5s
Change history
updated in COT2 ▾ Stat | COT1 | COT2 | Change
HP | 10 | 8 | -2
ACC | 30 | 33 | +3
Similar monsters
"""

LAUNCH = """Change history
updated in Launch ▾ Stat | COT2 | Launch | Change
WATK | 50 | 53 | +3
updated in COT2 ▾ Stat | COT1 | COT2 | Change
WATK | 47 | 50 | +3
"""

SHOP = """Where to buy
Mia cheapest
Henesys Potion Shop · Henesys
50
mesos
Launch prices Citizen of Honor +
Rina
Henesys Market · Henesys
60
mesos
Dropped By
Community sourced
Loading…
"""


@pytest.fixture
def tiny(tmp_path):
    today = TODAY
    log = [{"version": "2", "date": today.isoformat(), "counts": {"changed": 3, "added": 0, "removed": 0, "updated": 0},
            "changed": [{"key": "item/10", "name": "Scimitar", "category": "item", "props": [["Weapon Attack", 50, 53]]},
                        {"key": "monster/1", "name": "Snail", "category": "monster", "props": [["HP", 10, 8]]},
                        {"key": "item/12", "name": "Far Helm", "category": "item", "props": [["Weapon Defense", 1, 2]]}]},
           {"version": "1", "date": (today - timedelta(days=9)).isoformat(),
            "changed": [{"key": "item/11", "name": "Red Potion", "category": "item", "props": [["HP", 40, 50]]}]}]
    return make_kb(tmp_path, [
        ("monster/1", "Snail", {"Level": 30, "HP": 8, "EXP": 3}, SNAIL),
        ("item/10", "Scimitar", {"Level Requirement": 30, "Weapon Attack": 53, "_type": "Equip / Two-Handed Sword"},
         "REQ LEV 30 REQ STR 95 JOB Warrior\n" + LAUNCH),
        ("item/11", "Red Potion", {"_type": "Use / Potion"}, SHOP),
        ("item/12", "Far Helm", {"Level Requirement": 70, "_type": "Equip / Hat"}, "REQ LEV 70 JOB Mage\n"),
        ("item/13", "Snail Shell", {"_type": "Etc / Monster Drop"}, ""),
    ], log)


# ---------------------------------------------------------------- the classifier

def test_change_history_reads_the_build_and_its_changes():
    s = sources.history(page("Snail", SNAIL))
    assert s.source == "COT2" and s.before == "COT1"
    assert [(c.stat, c.old, c.new) for c in s.changes] == [("HP", "10", "8"), ("ACC", "30", "33")]
    assert sources.history(page("X", "nothing labelled here")) is None


def test_a_launch_table_flips_the_tag_by_itself():
    # MeowDB's launch values: "updated in Launch ▾ Stat | COT2 | Launch | Change" above the older COT2 block
    s = sources.history(page("Scimitar", LAUNCH))
    assert (s.source, s.before) == ("Launch", "COT2")
    assert [(c.old, c.new) for c in s.changes] == [("50", "53")]
    # in either order: the newest is the label no table changed from
    older_first = ("Change history\nupdated in COT2 ▾ Stat | COT1 | COT2 | Change\nWATK | 47 | 50 | +3\n"
                   "updated in Launch ▾ Stat | COT2 | Launch | Change\nWATK | 50 | 53 | +3\n")
    assert sources.history(page("Scimitar", older_first)).source == "Launch"
    assert sources.tag(en, "Launch") == "Launch" and sources.tag(he, "Launch") == "השקה"
    assert sources.tag(en, "Grand Launch") == "Launch" and "Launch" in sources.tip(en, "Launch")
    # a label nobody planned for is shown as the KB writes it
    assert sources.tag(he, "COT3") == "COT3" and "COT3" in sources.tip(he, "COT3")
    assert sources.tag(en, "Beta 7") == "Beta 7" and "Beta 7" in sources.tip(en, "Beta 7")


def test_labels_and_tooltips_in_both_languages():
    assert [sources.tag(en, s) for s in (sources.MSEA, sources.COMMUNITY, sources.OFFICIAL, sources.MEOWDB, "COT2")] \
        == ["MSEA", "Community", "Official · Nexon", "MeowDB", "COT2"]
    assert [sources.tag(he, s) for s in (sources.COMMUNITY, sources.OFFICIAL)] == ["קהילה", "רשמי · Nexon"]
    assert sources.tip(he, "COT2") == "נתוני גרסת הניסיון השנייה (COT2), לא מאושר להשקה."
    assert sources.tip(en, "COT1").startswith("Data from the first closed test (COT1)")
    for s in sources.FIXED:
        assert sources.tip(en, s) != f"src_{s.lower()}_tip" and sources.tip(he, s) != f"src_{s.lower()}_tip"


def test_price_labels_are_label_lines_not_prose():
    assert sources.price_label("COT2 prices") == "COT2"
    assert sources.price_label("COT2 prices Citizen of Honor +") == "COT2"
    assert sources.price_rest("COT2 prices Citizen of Honor +") == "Citizen of Honor +"
    assert sources.price_label("Launch prices full shop page →") == "Launch"
    assert sources.price_label("Great prices around here!") is None
    assert sources.price_label("Buyable-ingredient prices pulled from the NPC shop database ; cheapest wins.") is None
    assert sources.price_label("Free Market Prices") is None
    assert sources.price_note(en, "COT2") == "(COT2 test price)" and sources.price_note(en, "Launch") == "(Launch price)"


def test_shop_prices_carry_their_own_label(tiny):
    p = market.npc_prices(tiny, "item/11")
    assert p.shops == [("Mia", "Henesys Potion Shop · Henesys", 50), ("Rina", "Henesys Market · Henesys", 60)]
    assert p.source(p.shops[0]) == "Launch" and p.ranks[p.shops[0][:2]] == "Citizen of Honor"
    assert p.source(p.shops[1]) == sources.MEOWDB and not p.test_price(p.shops[1])


def test_respawn_timer_source():
    kb = SimpleNamespace(page=lambda k: page("Mano", "COT2 map data stores a 1-hour base timer. Live boss respawns "
                                                     "can run closer to 90 minutes, so this timer uses an unconfirmed "
                                                     "1 to 1.5 hour window."))
    assert sources.respawn_source(kb, "monster/700000") == ("COT2", True)


def test_change_line_is_one_left_to_right_block_in_hebrew():
    line = sources.change_line("Weapon Attack", 30, 33, "COT2", "Launch")
    assert line == f"{bidi.LRI}Weapon Attack 30 → 33 (COT2 → Launch){bidi.PDI}"
    # in a Hebrew tooltip the block stays whole: no run marks inside it, the arrow inside the isolate
    html = bidi.to_html(he("src_changed_head", before="COT2") + "\n" + line, "rtl")
    inner = html[html.index(bidi.LRI):html.index(bidi.PDI)]
    assert bidi.LRE not in inner and bidi.RLM not in inner and "30 → 33 (COT2 → Launch)" in inner
    assert html.count('dir="rtl"') == 2
    # and drawn: old value left of the arrow, the arrow left of the new value, the labels in order, in brackets
    from test_bidi import _reading_order
    shown = bidi.isolate_ltr_runs("שינוי: " + line)
    assert "WeaponAttack30→33(COT2→Launch)" in _reading_order(shown)


# ---------------------------------------------------------------- the drop split

def test_drops_split_by_list(tiny):
    lists = tiny.drop_lists("monster/1")
    assert lists == {sources.COMMUNITY: ["item/11"], sources.MSEA: ["item/13"]}      # Red Potion once, players' list
    assert tiny.monster_drops("monster/1") == ["item/11", "item/13"]
    assert tiny.drop_source("monster/1", "item/13") == sources.MSEA
    assert tiny.drop_source("monster/1", "item/11") == sources.COMMUNITY
    assert tiny.drop_source("monster/1", "item/10") is None
    g = tiny.drop_group("monster/1", ["item/11", "item/13"])
    assert g["sources"] == {"item/11": sources.COMMUNITY, "item/13": sources.MSEA}
    assert all(gr["sources"] for gr in tiny.drop_groups(["item/11", "item/13"]))
    digest = tiny.drops_digest("monster/1")
    assert "community-confirmed in Classic" in digest.split("\n")[0] and "Red Potion [item/11]" in digest.split("\n")[0]
    assert "MSEA reference list" in digest.split("\n")[1] and "Snail Shell [item/13]" in digest.split("\n")[1]


def test_drop_table_has_a_source_column(tiny):
    tiny.ensure_drop_table()
    rows = (tiny.root / "drops.tsv").read_text(encoding="utf-8").splitlines()
    assert rows[0].split("\t")[6:] == ["source", "votes"]     # (votes: players' votes on a community drop)
    by_item = {r.split("\t")[5]: r.split("\t")[6] for r in rows[1:]}
    assert by_item == {"item/11": "community", "item/13": "MSEA"}
    # a table from before the column is redone
    (tiny.root / "drops.ingame").write_text("drops.tsv lists only monsters the KB confirms are in the game\n",
                                            encoding="utf-8")
    (tiny.root / "drops.tsv").write_text("old", encoding="utf-8")
    tiny.ensure_drop_table()
    assert (tiny.root / "drops.tsv").read_text(encoding="utf-8").startswith("monster\t")
    assert "source column" in brain.SYSTEM_PROMPT or "source)" in brain.SYSTEM_PROMPT


def test_instant_answers_carry_their_sources(tiny):
    a = quick.answer("Snail drops", tiny, en)
    assert a.sources == [sources.COMMUNITY, sources.MSEA] and en("quick_drops_note_both") in a.text
    a = quick.answer("Snail hp", tiny, en)
    assert a.sources == ["COT2"]
    a = quick.answer("who drops Snail Shell", tiny, en)
    assert a.sources == [sources.MSEA] and en("quick_drops_note") in a.text
    a = quick.answer("where to buy red potion", tiny, en)
    assert a.sources == ["Launch", sources.MEOWDB] and "50 mesos (for citizens of grade Citizen of Honor and up) " \
        "(Launch price)" in a.text
    assert quick.drops_note(en, [sources.COMMUNITY]) == en("quick_drops_note_community")


def test_ai_gets_the_sources(tiny):
    note = sources.page_note(tiny, "monster/1")
    assert note.startswith("[sources: stats are COT2 values (changed from COT1: HP 10 -> 8; ACC 30 -> 33)")
    assert "drop list is the MSEA reference list" in note
    prompt = brain.build_prompt("what does Snail drop?", None, None, tiny, False)
    assert "[sources: stats are COT2 values" in prompt and "community-confirmed in Classic" in prompt
    assert "(MSEA)" in brain.SYSTEM_PROMPT and "(COT2)" in brain.REPLY_RULES
    reverse = brain.build_prompt("which monsters drop snail shell?", None, None, tiny, False)
    assert "Snail Shell [item/13] (MSEA)" in reverse
    # players' reports don't replace the MSEA list: only the game's official data would (the owner's rule)
    assert "MSEA reference drops" in brain._page(tiny, "monster/1", 6000)
    # official values from the released game: the test builds' table is gone from the AI's page (item/10 is "Launch")
    assert "Change history" not in brain._page(tiny, "item/10", 6000)
    assert "Change history" in brain._page(tiny, "monster/1", 6000)


# ---------------------------------------------------------------- recent changes

def test_recent_changes_last_a_week_from_the_updates_own_date(tiny):
    found = recent.recent(tiny)
    assert set(found) == {"item/10", "monster/1", "item/12"}            # the 9-day-old Red Potion change is gone
    assert recent.recent(tiny, today=TODAY + timedelta(days=8)) == {}
    r = found["item/10"]
    # the page's change history shows that very change (Launch table WATK 50 -> 53): the line names the builds
    assert recent.lines(en, tiny, r) == [sources.change_line("Weapon Attack", "50", "53", "COT2", "Launch")]
    assert recent.lines(en, tiny, found["item/12"]) == [sources.change_line("Weapon Defense", "1", "2")]
    tip = recent.tip(he, tiny, r)
    assert tip.startswith("עודכן במאגר ב-") and "Weapon Attack 50 → 53 (COT2 → Launch)" in tip


def test_ai_hears_about_recent_changes(tiny):
    assert recent.ai_lines(tiny, ["item/10"]) == \
        [f"Recent KB change ({TODAY.isoformat()}): Scimitar: Weapon Attack 50 -> 53 (COT2 -> Launch)"]
    prompt = brain.build_prompt("is Scimitar good?", None, None, tiny, False)
    assert "Recent KB change (" in prompt and "Scimitar: Weapon Attack 50 -> 53 (COT2 -> Launch)" in prompt
    assert "Recent KB change" in brain.SYSTEM_PROMPT


def test_changes_that_affect_the_character_come_first(tiny):
    warrior = SimpleNamespace(level=32, base_class="Warrior")
    entries = recent.changelog(tiny)[:1]
    mine, rest = recent.split(entries, tiny, warrior, [])
    assert [(why, r["key"]) for why, _, r in mine] == [("gear", "item/10"), ("train", "monster/1")]
    assert [r["key"] for r in rest[0]["changed"]] == ["item/12"] and rest[0]["counts"]["changed"] == 1
    # a Magician gets no Warrior sword; a wished item always counts; a level 70 helm is too far off
    mage = SimpleNamespace(level=32, base_class="Magician")
    assert [r["key"] for _, _, r in recent.split(entries, tiny, mage, ["item/12"])[0]] == ["monster/1", "item/12"]
    assert recent.item_jobs(tiny, "item/12") == {"Magician"} and recent.item_jobs(tiny, "item/13") is None
    from maplehelper.ui.patchnotes import update_notice
    text = update_notice(he, entries, tiny, warrior, [])
    first, second = text.split("\n")
    assert first.startswith("2 שינויים במאגר נוגעים בכם: ")
    # each name one unbreakable left-to-right block in the Hebrew line
    assert f"{bidi.LRI}Scimitar{bidi.PDI}, {bidi.LRI}Snail{bidi.PDI}" in first
    assert second == "ועוד: שינוי אחד"
    assert update_notice(en, entries, tiny, None, []) == "Database updated: 3 changed"


# ---------------------------------------------------------------- the widgets

def test_cards_and_groups_show_their_source(tiny, qapp):
    from PySide6.QtWidgets import QLabel

    from maplehelper.ui.widgets import DropGroupCard, EntityCard, TileGrid

    def chips(w, name="SourceTag"):
        return [c.text().strip("‏‪‬") for c in w.findChildren(QLabel) if c.objectName() == name]
    card = EntityCard(tiny, "monster/1", "en")
    assert chips(card) == ["COT2"] and "ACC 30 → 33 (COT1 → COT2)" in card.source_chip.toolTip()
    assert chips(card, "UpdatedTag") == ["Updated"] and "HP 10 → 8" in \
        [c for c in card.findChildren(QLabel) if c.objectName() == "UpdatedTag"][0].toolTip()
    assert chips(EntityCard(tiny, "item/10", "he")) == ["השקה"]
    assert chips(EntityCard(tiny, "item/13", "en")) == []            # no stat line, nothing to label
    group = DropGroupCard(tiny, "monster/1", ["item/13", "item/11"], en)
    assert chips(group) == ["Community", "MSEA"]                      # a group that mixes lists: one chip each
    # the change from the test before, in sight (it was only in the tag's tooltip)
    assert "ACC 30 → 33" in card.changed_label.text() and "COT1" in card.changed_label.text()
    assert not hasattr(EntityCard(tiny, "item/10", "en"), "changed_label")      # official values: no test change
    assert chips(TileGrid(tiny, ["item/13"], "Snail drops", False, t=en, srcs=["MSEA"])) == ["MSEA"]


# ---------------------------------------------------------------- coverage over the real knowledge base

# every kind of label line the KB writes, found here independently of sources.py: a page with one must get
# that marker from the classifier
FAMILIES = {
    "history": re.compile(r"^updated in .+ ▾"),
    "prices": re.compile(r"^\S+ prices(?: .*[+→])?$"),
    "respawn": re.compile(r"^\S+ map data stores "),
    "drops": re.compile(r"^MSEA reference drops$", re.I),
    "community_list": re.compile(r"^Community sourced"),
    "fm_reports": re.compile(r"^(Community price check|Player reported|Saw it in a shop[?] Add a price)$"),
    "official": re.compile(r"^Source ?: .*\bNexon\b|^Official sources:"),
    ("shop_list", "source"): re.compile(r"^Source ?: (?!.*\bNexon\b)"),
    "guide_data": re.compile(r"\buse current [A-Z0-9]\S* data\b"),
    "exp_table": re.compile(r"reproduce a historical reference"),
    "party_exp": re.compile(r"^Party EXP\. \S+ grants"),
    "mob_rate": re.compile(r"from closed-beta play"),
    "cash_price": re.compile(r"^Closed-test price"),
    "cash_shop": re.compile(r" COT\d+ only$"),
}


@pytest.fixture(scope="module")
def real():
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_every_marker_in_the_kb_is_recognised(real):
    found: dict[str, int] = {}
    for key in real.entities:
        lines = sources._body(real.page(key))
        kinds = {m.kind for m in sources.markers(real, key)}
        for kind, rx in FAMILIES.items():
            if any(rx.search(ln) for ln in lines):
                ok = set(kind) if isinstance(kind, tuple) else {kind}
                for k in (ok & kinds) or ok:
                    found[k] = found.get(k, 0) + 1
                assert ok & kinds, f"{key}: a {kind!r} label the classifier missed"
    # the KB as it is now has every family the app reads (a family that vanished would hide a broken reader)
    for kind in ("history", "prices", "respawn", "drops", "community_list", "fm_reports", "official", "shop_list",
                 "guide_data", "exp_table", "cash_price", "cash_shop"):
        assert found.get(kind), f"no {kind!r} label anywhere in the KB"


@needs_kb
def test_no_drop_price_or_stat_without_a_source(real):
    from maplehelper import availability
    open_ = availability.of(real)
    labelled = 0
    for key, e in real.entities.items():
        cat = e.get("category")
        if cat == "monster" and open_.monster_key_open(key):
            lists = real.drop_lists(key)
            assert sorted(real.monster_drops(key)) == sorted(k for ks in lists.values() for k in ks)
            for item in real.monster_drops(key):
                assert real.drop_source(key, item) in (sources.COMMUNITY, sources.MSEA)
        if cat == "item":
            p = market.npc_prices(real, key)
            labels = {sources.price_label(ln) for ln in sources._body(real.page(key))} - {None}
            for shop in p.shops:
                assert p.source(shop), f"{key}: a shop price without a source"
                if labels:
                    labelled += 1
                    assert p.source(shop) in labels, f"{key}: {shop} lost its {labels} label"
        if cat in ("monster", "item", "skill"):
            s = sources.stat_source(real, key)
            marked = any(FAMILIES["history"].search(ln) for ln in sources._body(real.page(key)))
            assert bool(s) == marked and (not s or s.source)
            assert sources.source_of(real, key) == (s.source if s else sources.MEOWDB)
    assert labelled > 500
    for g in real.drop_groups([k for k in real.droppers][:200], limit=200):
        assert set(g["sources"]) == set(g["items"]) and all(g["sources"].values())
    # every boss timer's line, read here, against respawn_source (the exact parse: test_respawn_timer_source); no
    # boss or build named, so a timer confirmed or a new build doesn't stop the nightly
    timers = 0
    for key, e in real.entities.items():
        if e.get("category") == "monster":
            line = next((ln for ln in sources._body(real.page(key)) if FAMILIES["respawn"].search(ln)), None)
            assert sources.respawn_source(real, key) == ((line.split()[0], "unconfirmed" in line) if line else None)
            timers += bool(line)
    assert timers


@needs_kb
def test_every_stat_card_and_instant_answer_is_tagged(real, qapp):
    from maplehelper import availability
    from maplehelper.ui.widgets import EntityCard
    open_ = availability.of(real)
    checked = 0
    for key, e in real.entities.items():
        if e.get("category") == "monster" and open_.monster_key_open(key) and sources.stat_source(real, key):
            card = EntityCard(real, key, "en")
            if EntityCard._stats(e, en):
                assert card.source_chip.text() == sources.stat_source(real, key).source
                checked += 1
            a = quick.answer(f"{e['name']} hp", real, en)
            if a and a.text and "HP" in a.text:
                assert a.sources == [sources.stat_source(real, key).source]
            card.deleteLater()
    assert checked > 30
    for key in [k for k, e in real.entities.items() if e.get("category") == "item"][:300]:
        if EntityCard._stats(real.get(key), en):
            card = EntityCard(real, key, "he")
            assert card.source_chip.text().strip("‏‪‬") == sources.tag(he, sources.source_of(real, key))
            card.deleteLater()
    # deleteLater needs an event loop this test never runs: without this ~290 cards stayed alive for the rest of the
    # session, and every later app-wide setStyleSheet repolished them all (the share-card test took 6-17 s, not 0.1)
    from PySide6.QtCore import QEvent
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)


@needs_kb
def test_every_labelled_page_tells_the_ai(real):
    for key in real.entities:
        ms = sources.markers(real, key)
        kinds = {m.kind for m in ms} - {"community_list", "fm_reports"}      # (empty in a KB copy: loaded live)
        if kinds:
            assert sources.page_note(real, key).startswith("[sources: "), key
