"""The gear upgrade advisor (maplehelper/gearadvice.py and its window over the game): who can wear what —
level, class and the weapons each 2nd job actually fights with, gender, stat requirements against the player's
typed stats (unknown ones flag, never hide) — the transparent ranking (main attack first, the class's main and
secondary stat, weapon speed; armour by stats then defense), the "soon" horizon, every price and dropper from
the KB's own tables, and the window's life: rows in both languages, the worn-item picker saving to the
character, the ✕ turning its setting off. The real-KB tests prove a Lv 32 Assassin never gets a bow, a
Warrior-only item, an over-level item in "now" or a Female-only one."""
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from maplehelper import gearadvice, store, tables  # noqa: E402
from maplehelper.kb import KnowledgeBase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


# ---------------------------------------------------------------- a tiny KB with the shapes that matter

def _equip_page(name, typ, stats=(), req="REQ LEV 1", slots=None, shop="", gender=""):
    body = f"\n{name}\nEquip · {typ.split(' / ', 1)[-1]} · No. 0000\n{req}\n"
    body += "".join(f"{s}\n" for s in stats)
    body += f"Upgrade Slots {slots}\n" if slots is not None else ""
    body += "NPC Sell-back 1 mesos\n"
    body += f"{gender} only\n" if gender else ""
    body += "Tradeable\n"
    body += shop
    return {"name": name, "typ": typ, "props": {}, "body": body}


MEBA_SHOP = """Where to buy
Tulcus Armor Seller
Victoria Road: Sleepy Dungeon · Sleepywood
7,000
mesos
COT2 prices"""

PAGES = {
    # claws: the worn one, the ones that beat it, the ones that don't, the soon and the far
    "item/100": _equip_page("Meba", "Equip / Claw", ("W.ATK +19", "Attack Speed Fast (4)"),
                            "REQ LEV 25 REQ LUK 45 JOB Thief", 7, MEBA_SHOP),
    "item/101": _equip_page("Steel Titans", "Equip / Claw", ("W.ATK +15", "Attack Speed Fast (4)"),
                            "REQ LEV 15 REQ LUK 35 JOB Thief", 7),
    "item/102": _equip_page("Adamantium Guards", "Equip / Claw", ("W.ATK +23", "Attack Speed Slow (7)"),
                            "REQ LEV 30 REQ LUK 100 JOB Thief", 7),
    "item/103": _equip_page("Fast Guard", "Equip / Claw", ("W.ATK +23", "Attack Speed Fast (4)", "CRIT% +1"),
                            "REQ LEV 30 REQ LUK 100 JOB Thief", 7),
    "item/104": _equip_page("Guardians of Doom", "Equip / Claw", ("W.ATK +26",),
                            "REQ LEV 37 REQ LUK 130 JOB Thief", 7),
    "item/105": _equip_page("Far Claw", "Equip / Claw", ("W.ATK +30",), "REQ LEV 40 JOB Thief", 7),
    "item/106": _equip_page("Heavy Claw", "Equip / Claw", ("W.ATK +15", "Attack Speed Fast (4)"),
                            "REQ LEV 20 JOB Thief", 7),          # exactly the worn one's rank: not an upgrade
    "item/107": _equip_page("Weak Claw", "Equip / Claw", ("W.ATK +10",), "REQ LEV 10 JOB Thief", 7),
    # the same attack and speed: CRIT breaks the tie, then ACC (itemterms' CRIT% is the table's crit column)
    "item/108": _equip_page("Plain Guard", "Equip / Claw", ("W.ATK +23", "Attack Speed Fast (4)"),
                            "REQ LEV 30 REQ LUK 100 JOB Thief", 7),
    "item/109": _equip_page("Keen Guard", "Equip / Claw", ("W.ATK +23", "Attack Speed Fast (4)", "CRIT% +1",
                                                           "ACC +2"),
                            "REQ LEV 30 REQ LUK 100 JOB Thief", 7),
    # what the equips table's job column allows but the job itself doesn't fight with
    "item/110": _equip_page("Reef Dagger", "Equip / Dagger", ("W.ATK +40",), "REQ LEV 30 JOB Thief", 7),
    "item/120": _equip_page("War Bow", "Equip / Bow", ("W.ATK +40",), "REQ LEV 30 JOB Bowman", 7),
    "item/131": _equip_page("Iron Casque", "Equip / Hat", ("W.DEF +50",), "REQ LEV 30 JOB Warrior", 7),
    # armour: the class's stats rank before defense, gender hides nothing until said, stats flag when untyped
    "item/130": _equip_page("LUK Bamboo Hat", "Equip / Hat", ("LUK +2", "W.DEF +10"), "REQ LEV 20", 7),
    "item/132": _equip_page("Rose Circlet", "Equip / Hat", ("LUK +5", "W.DEF +12"), "REQ LEV 25", 7, gender="Female"),
    "item/140": _equip_page("Lukewire Shirt", "Equip / Top", ("LUK +4",), "REQ LEV 20 REQ LUK 200", 7),
    "item/150": _equip_page("Sauna Robe", "Equip / Overall", ("W.DEF +75",), "REQ LEV 30", 10),
    # a Magician's weapon ranks by M.ATK, not W.ATK
    "item/160": _equip_page("Spell Wand", "Equip / Wand", ("M.ATK +40", "W.ATK +60"), "REQ LEV 25 JOB Magician", 7),
    "item/161": _equip_page("Lesser Wand", "Equip / Wand", ("M.ATK +35", "W.ATK +70"), "REQ LEV 20 JOB Magician", 7),
}

DROPS = [  # the drops table's rows, as rows() types them, for the tests that need droppers
    {"monster": "Jr. Wraith", "monster_level": 34, "monster_key": "monster/50", "item": "Keen Guard",
     "item_type": "Equip / Claw", "item_key": "item/109", "source": "community", "votes": "16 up 2 down"},
    {"monster": "Jr. Wraith", "monster_level": 34, "monster_key": "monster/50", "item": "Keen Guard",
     "item_type": "Equip / Claw", "item_key": "item/109", "source": "MSEA", "votes": "2 up 0 down"},
    {"monster": "Croco", "monster_level": 52, "monster_key": "monster/46", "item": "Keen Guard",
     "item_type": "Equip / Claw", "item_key": "item/109", "source": "community", "votes": "2 up 0 down"},
]


def make_kb(root: Path) -> KnowledgeBase:
    index = []
    for key, p in PAGES.items():
        cat, _, slug = key.partition("/")
        (root / "pages" / cat).mkdir(parents=True, exist_ok=True)
        front = {"name": p["name"], "category": cat, "props": p["props"], "type": p["typ"]}
        (root / "pages" / cat / f"{slug}.md").write_text(
            "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + f"\n---\n\n# {p['name']}\n{p['body']}",
            encoding="utf-8")
        index.append({"key": key, "id": slug, "name": p["name"], "category": cat, "props": p["props"],
                      "type": p["typ"]})
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "aliases.json").write_text("{}", encoding="utf-8")
    (root / "routes.json").write_text('{"maps": []}', encoding="utf-8")
    return KnowledgeBase(root)


@pytest.fixture(scope="module")
def _own_kb(tmp_path_factory) -> Path:
    """This file's own tiny KB (the shared fixture KB holds no equips), its tables built once here."""
    root = tmp_path_factory.mktemp("gearadvice") / "kb"
    kb = make_kb(root)
    assert tables.ensure(kb)
    return root


@pytest.fixture
def kb(_own_kb):
    return KnowledgeBase(_own_kb)


@pytest.fixture
def drops(kb, monkeypatch):
    """The drops table, said our way: everything else still reads the tiny KB's own tables."""
    real = tables.rows
    monkeypatch.setattr(tables, "rows",
                        lambda k, name, *a, **kw: DROPS if name == "drops" else real(k, name, *a, **kw))


def char(**kw):
    """The sample player: a Lv 32 Male Assassin with STR / DEX / LUK typed."""
    args = {"id": "t", "name": "ShadowK", "base_class": "Thief", "job": "Assassin", "level": 32,
            "gender": "Male", "ap": {"str": 4, "dex": 50, "luk": 120}}
    args.update(kw)
    return store.Character(**args)


def item(kb, key):
    """One of the tiny KB's equips rows, as the table has it (the picker's own filter aside)."""
    return next(r for r in tables.rows(kb, "equips") if r["key"] == key)

def plan(kb, character, slot):
    return next(p for p in gearadvice.upgrades(kb, character) if p.slot == slot)


# ---------------------------------------------------------------- wearable: level, class/job, gender, stats

def test_an_assassin_wears_claws_not_daggers_bows_or_warrior_gear(kb):
    c = char()
    assert gearadvice.wearable(kb, c, item(kb, "item/102")).ok                    # a claw of its class
    for bad in ("item/110", "item/120", "item/131"):                              # another job's, or another class's
        fit = gearadvice.wearable(kb, c, item(kb, bad))
        assert not fit.ok and fit.reasons == ("job",), bad
    # a 1st-job Thief still holds both claws and daggers (the class guides equip both)
    rogue = store.Character(id="t", name="Rook", base_class="Thief", job="Thief", level=30)
    assert gearadvice.wearable(kb, rogue, item(kb, "item/110")).ok
    assert gearadvice.wearable(kb, rogue, item(kb, "item/101")).ok
    # a Beginner wears only what no job column keeps from them
    beg = store.Character(id="t", name="Newb", base_class="Beginner", job="Beginner", level=32)
    assert gearadvice.wearable(kb, beg, item(kb, "item/130")).ok                   # job Any
    assert gearadvice.wearable(kb, beg, item(kb, "item/102")).reasons == ("job",)


def test_second_jobs_fight_with_their_own_weapons():
    pairs = {"Fighter": ("Warrior", "One-Handed Sword", "Spear"), "Page": ("Warrior", "One-Handed Sword",
              "Two-Handed Axe"), "Spearman": ("Warrior", "Spear", "One-Handed Sword"), "Hunter": ("Bowman",
              "Bow", "Crossbow"), "Crossbowman": ("Bowman", "Crossbow", "Bow"), "Assassin": ("Thief", "Claw",
              "Dagger"), "Bandit": ("Thief", "Dagger", "Claw"), "Cleric": ("Magician", "Staff", "Bow")}
    for job, (base, yes, no) in pairs.items():
        c = store.Character(id="t", name="X", base_class=base, job=job, level=50)
        assert yes in gearadvice.JOB_WEAPONS[job] and no not in gearadvice.JOB_WEAPONS[job]
        assert gearadvice.holds(c, {"slot": yes, "job": base})                     # the KB's column allows the class
        assert not gearadvice.holds(c, {"slot": no, "job": base})                  # the job fights with one of them


def test_level_blocks_and_sets_the_gap(kb):
    fit = gearadvice.wearable(kb, char(), item(kb, "item/104"))                    # REQ LEV 37 (and LUK 130) at 32
    assert not fit.ok and "level" in fit.reasons and fit.level_gap == 5
    strong = char(level=37, ap={"str": 4, "dex": 50, "luk": 140})
    assert gearadvice.wearable(kb, strong, item(kb, "item/104")).ok                # the boundary itself passes


def test_gender_hides_only_once_said(kb):
    rose = item(kb, "item/132")                                                    # Female only
    assert gearadvice.wearable(kb, char(gender="Male"), rose).reasons == ("gender",)
    assert gearadvice.wearable(kb, char(gender="Female"), rose).ok
    unsaid = gearadvice.wearable(kb, char(gender=""), rose)
    assert unsaid.ok and unsaid.flags == ("gender",)                               # flagged, never hidden


def test_stat_requirements_against_the_typed_stats(kb):
    shirt = item(kb, "item/140")                                                   # REQ LUK 200
    assert gearadvice.wearable(kb, char(), shirt).reasons == ("stat",)             # 120 typed: short
    assert gearadvice.wearable(kb, char(ap={"str": 4, "dex": 50, "luk": 200}), shirt).ok  # the boundary passes
    unsaid = gearadvice.wearable(kb, char(ap={}), shirt)
    assert unsaid.ok and unsaid.missing == (("luk", 200, None),)                   # not typed: flagged, not hidden


# ---------------------------------------------------------------- the ranking and the advice

def test_weapons_rank_by_attack_stats_speed_then_crit_and_acc(kb):
    c = char(gear={"Claw": "item/101"})                                            # Steel Titans, W.ATK 15
    now = plan(kb, c, "Claw").now
    # the same W.ATK 23: faster first, then CRIT, then ACC — Keen Guard over Fast Guard over Plain Guard
    assert [u.row["item"] for u in now] == ["Keen Guard", "Fast Guard", "Plain Guard"]
    assert dict(now[0].deltas)["watk"] == 8 and dict(now[0].deltas)["crit"] == 1    # the why of the rank
    assert dict(now[0].deltas)["acc"] == 2
    # exactly the worn one's rank never shows (Heavy Claw), nor anything below it (Weak Claw)
    names = [u.row["item"] for u in now]
    assert "Heavy Claw" not in names and "Weak Claw" not in names
    # with nothing worn every wearable item counts, best first, up to the cap the window lists
    bare = [u.row["item"] for u in plan(kb, char(), "Claw").now]
    assert bare == ["Keen Guard", "Fast Guard", "Plain Guard"]
    assert [u.row["item"] for u in plan(kb, char(level=12), "Claw").now] == ["Weak Claw"]  # even the weakest


def test_armour_ranks_by_the_class_stats_before_defense(kb):
    c = char(gender="", gear={"Hat": "item/130"})                                  # LUK +2, W.DEF +10 worn
    now = plan(kb, c, "Hat").now
    assert [u.row["item"] for u in now] == ["Rose Circlet"]                        # LUK 5 beats any defense here
    assert dict(now[0].deltas)["luk"] == 3
    assert {"Top", "Overall"} <= {p.slot for p in gearadvice.upgrades(kb, c)}     # (no Bottom in this tiny KB)


def test_a_magician_ranks_magic_attack(kb):
    c = store.Character(id="t", name="Wiz", base_class="Magician", job="Cleric", level=30)
    assert [u.row["item"] for u in plan(kb, c, "Wand").now] == ["Spell Wand", "Lesser Wand"]  # M.ATK over W.ATK


def test_soon_items_open_within_the_horizon(kb):
    strong = char(gear={"Claw": "item/102"}, ap={"str": 4, "dex": 50, "luk": 140})  # W.ATK 23 slow worn
    p = plan(kb, strong, "Claw")
    assert [u.row["item"] for u in p.now] == ["Keen Guard", "Fast Guard", "Plain Guard"]  # 23 and faster
    assert [(u.row["item"], u.soon) for u in p.soon] == [("Guardians of Doom", 5)]  # level 37 at 32
    assert "Far Claw" not in [u.row["item"] for u in p.now + p.soon]              # level 40: past the horizon
    # a stat the player typed short keeps even a soon item away; untyped, it only flags it
    short = plan(kb, char(gear={"Claw": "item/102"}), "Claw")                      # LUK 120 < the 130 it asks
    assert "Guardians of Doom" not in [u.row["item"] for u in short.soon]
    unsaid = plan(kb, char(gear={"Claw": "item/102"}, ap={}), "Claw").soon
    guard = next(u for u in unsaid if u.row["item"] == "Guardians of Doom")
    assert "stat:luk:130" in guard.flags


def test_prices_and_droppers_come_from_the_tables(kb, drops):
    meba = next(u for u in plan(kb, char(level=26), "Claw").now if u.row["item"] == "Meba")
    assert meba.buy == 7000 and "Tulcus" in meba.seller and "Sleepywood" in meba.seller
    keen = plan(kb, char(), "Claw").now[0]                                         # Keen Guard, no shop
    assert keen.row["item"] == "Keen Guard" and keen.buy is None
    assert keen.drops == (("Jr. Wraith", 34), ("Croco", 52))                       # best voted first, once each
    plain = plan(kb, char(), "Claw").now[2]                                        # neither price nor droppers
    assert plain.row["item"] == "Plain Guard" and plain.buy is None and plain.drops == ()
    robe = plan(kb, char(), "Overall").now[0]
    assert robe.buy is None and robe.drops == ()                                   # neither: the window says so


def test_the_picker_lists_the_class_items_any_level(kb):
    names = [r["item"] for r in gearadvice.slot_items(kb, char(), "Claw")]
    assert "Far Claw" in names and "Reef Dagger" not in names                      # any level, own class only
    assert names == sorted(names, key=str.casefold)


def test_delta_text_uses_the_game_own_words():
    assert gearadvice.delta_text((("watk", 5), ("str", 2))) == "W.ATK +5 · STR +2"
    assert gearadvice.delta_text((("luk", -1),)) == "LUK -1"


# ---------------------------------------------------------------- the real knowledge base

@pytest.fixture(scope="module")
def real():
    return KnowledgeBase(REAL_KB)


@needs_kb
def test_a_lv32_assassin_gets_none_of_the_wrong_gear(real):
    c = char()
    plans = gearadvice.upgrades(real, c)
    assert plans, "the real KB's equips table is missing"
    for p in plans:
        for u in p.now:
            assert gearadvice.wearable(real, c, u.row).ok                          # wearable now, every rule
            assert not u.row.get("req_lv") or u.row["req_lv"] <= 32                # nothing over-level in "now"
        for u in p.now + p.soon:
            assert u.row["slot"] not in ("Dagger", "Bow", "Crossbow")              # not another job's weapon
            assert u.row.get("job") in ("Any", "Thief", "Warrior/Thief", "Warrior/Bowman/Thief")
            assert u.row.get("gender") != "Female"                                 # said Male
            assert not u.row.get("req_lv") or u.row["req_lv"] <= 32 + gearadvice.SOON_LEVELS


@needs_kb
def test_upgrades_strictly_improve_what_is_worn(real):
    c = char()
    wearable_claws = [r for r in gearadvice.slot_items(real, c, "Claw") if gearadvice.wearable(real, c, r).ok]
    worn = min(wearable_claws, key=lambda r: gearadvice.rank_key("Thief", r))       # the least of them worn
    c.gear["Claw"] = worn["key"]
    p = next(x for x in gearadvice.upgrades(real, c) if x.slot == "Claw")
    key = gearadvice.rank_key("Thief", worn)
    assert p.now                                                                    # something wearable beats it
    assert all(gearadvice.rank_key("Thief", u.row) > key for u in p.now)
    assert worn["key"] not in [u.row["key"] for u in p.now]                         # never its own upgrade
    # nothing worn: every kept row is simply wearable, and the best claw is listed first
    bare = next(x for x in gearadvice.upgrades(real, char()) if x.slot == "Claw")
    assert bare.now and all(gearadvice.wearable(real, char(), u.row).ok for u in bare.now)
    assert gearadvice.rank_key("Thief", bare.now[0].row) == max(gearadvice.rank_key("Thief", u.row)
                                                                for u in bare.now)


@needs_kb
def test_sources_match_the_tables_exactly(real):
    equips = {str(r["key"]): r for r in tables.rows(real, "equips")}
    dropped = {}
    for r in tables.rows(real, "drops"):
        dropped.setdefault(str(r.get("item_key")), set()).add((r.get("monster"), r.get("monster_level")))
    for p in gearadvice.upgrades(real, char()):
        for u in p.now + p.soon:
            src = equips[str(u.row["key"])]
            assert u.buy == src.get("buy") and u.seller == str(src.get("seller") or "")
            for name, lv in u.drops:
                assert (name, lv) in dropped[str(u.row["key"])]


@needs_kb
def test_untyped_stats_flag_instead_of_hiding(real):
    c = char(ap={})                                                                # nothing typed
    flagged = 0
    for p in gearadvice.upgrades(real, c):
        for u in p.now + p.soon:
            for st in gearadvice.STAT_KEYS:
                if isinstance(u.row.get(f"req_{st}"), int) and u.row[f"req_{st}"]:
                    assert f"stat:{st}:{u.row[f'req_{st}']}" in u.flags
                    flagged += 1
    assert flagged                                                                 # the real KB does ask stats


# ---------------------------------------------------------------- the window

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(isolated_store):
    return isolated_store.Settings()


@pytest.fixture
def profiles(isolated_store):
    return isolated_store.Profiles()


@pytest.fixture
def win(qapp, settings, profiles, kb, drops):
    from maplehelper.i18n import I18n
    from maplehelper.ui.gearadvisor import GearAdvisor
    c = profiles.add("ShadowK", "Thief", "Assassin", 32)
    c.gender, c.ap = "Male", {"str": 4, "dex": 50, "luk": 120}
    profiles.save()
    settings["gear_advisor"] = True
    w = GearAdvisor(kb, settings, profiles, I18n("en"))
    yield w
    from PySide6.QtCore import QEvent
    for timer in (w._typing, w._stats_typing, w._geom):
        timer.stop()
    w.hide()
    w.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def test_the_window_shows_rows_in_both_languages_and_the_x_turns_it_off(qapp, win, settings):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from maplehelper.i18n import STRINGS, I18n
    win.refresh()
    assert win.isVisible()
    assert win._title.text() == STRINGS["ga_title"]["en"]
    names = {x.text() for x in win.findChildren(QLabel) if x.objectName() == "Name"}
    assert {"Keen Guard", "Fast Guard", "Plain Guard"} <= names                    # the advice, in English
    subs = [x.text() for x in win.findChildren(QLabel) if x.objectName() == "Sub"]
    assert any("Lv. 30" in s for s in subs) and not any("Lv. Lv" in s for s in subs)  # never "Lv. Lv. 30"
    win.apply_language(I18n("he"))
    assert win._title.text().replace(chr(0x200F), "") == STRINGS["ga_title"]["he"]
    assert win.layoutDirection() == Qt.RightToLeft
    assert {x.text() for x in win.findChildren(QLabel) if x.objectName() == "Name"} >= {"Fast Guard"}
    closed = []
    win.closed.connect(lambda: closed.append(True))
    win._close.click()
    qapp.processEvents()
    assert settings["gear_advisor"] is False and not win.isVisible() and closed == [True]


def test_the_worn_picker_saves_what_you_pick(qapp, win, profiles):
    from maplehelper.ui.gamesearch import _Row
    win.refresh()
    win._worn_clicked("Claw")
    qapp.processEvents()
    assert win._pick_slot == "Claw" and win._pick_box.isVisibleTo(win)
    rows = [r for r in win._pick_rows if isinstance(r, _Row)]
    assert rows[0].name.text() == "Nothing worn here"                              # taking it off is a choice
    expected = next(p for p in win._plans if p.slot == "Claw").picker[0]["key"]
    rows[1].clicked.emit()                                                         # the first claw of the list
    qapp.processEvents()
    assert profiles.active.gear.get("Claw") == expected                            # saved to the character
    win._worn_clicked("Claw")
    rows = [r for r in win._pick_rows if isinstance(r, _Row)]
    rows[0].clicked.emit()                                                         # nothing: taken off
    qapp.processEvents()
    assert "Claw" not in profiles.active.gear


def test_a_folded_slot_counts_its_upgrades_in_hebrew_too(qapp, win):
    """The folded badge passed the count as a number to bidi.ltr_block, which takes text: folding a slot with
    upgrades crashed in Hebrew (English skips the wrap, so only Hebrew hit it)."""
    from maplehelper.i18n import I18n
    win.refresh()
    win.apply_language(I18n("he"))
    plan = next(p for p in win._plans if p.now)
    if plan.slot not in (win._folded or set()):
        win._fold_clicked(plan.slot)
    assert str(len(plan.now)) in win._badge_of[plan.slot].text()
