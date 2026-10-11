"""The gear upgrade advisor (maplehelper/gearadvice.py and its window over the game): who can wear what —
level, class and the weapons each 2nd job actually fights with, gender, stat requirements against the player's
typed stats (unknown ones flag, never hide) — the transparent ranking (main attack first, the class's main and
secondary stat, weapon speed; armour by stats then defense) and why each item ranks where it does, the "soon"
horizon and the next item past it, a slot's one-line answer as facts (slot_summary), every price and dropper
from the KB's own tables, and the window: the equipment grid (a tile per slot, the worn item's picture, the
upgrade count), the picked slot's panel (the best card marked, the stat changes as signed chips, Plan scrolls
only for an item with upgrade slots, the Free Market price for an item no NPC sells), the worn-item picker
saving to the character, both languages, the accent, and the ✕ turning its setting off. The real-KB tests prove
a Lv 32 Assassin never gets a bow, a Warrior-only item, an over-level item in "now" or a Female-only one."""
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
    "item/141": _equip_page("Plain Tee", "Equip / Top", ("LUK +1",), "REQ LEV 10"),     # no upgrade slots
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

# the sprites the KB has for some items (img/item/<id>.png, as the real KB's index names them): the grid and the
# cards show these, the rest the category's stand-in
PICTURES = {"item/101": ((6, 10), (200, 40, 40)), "item/109": ((10, 6), (40, 200, 40))}


def make_kb(root: Path) -> KnowledgeBase:
    from PIL import Image
    index = []
    for key, p in PAGES.items():
        cat, _, slug = key.partition("/")
        (root / "pages" / cat).mkdir(parents=True, exist_ok=True)
        front = {"name": p["name"], "category": cat, "props": p["props"], "type": p["typ"]}
        (root / "pages" / cat / f"{slug}.md").write_text(
            "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + f"\n---\n\n# {p['name']}\n{p['body']}",
            encoding="utf-8")
        entry = {"key": key, "id": slug, "name": p["name"], "category": cat, "props": p["props"], "type": p["typ"]}
        if key in PICTURES:
            (root / "img" / cat).mkdir(parents=True, exist_ok=True)
            size, rgb = PICTURES[key]
            Image.new("RGB", size, rgb).save(root / "img" / cat / f"{slug}.png")
            entry["image"] = f"img/{cat}/{slug}.png"
        index.append(entry)
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


def test_why_names_the_first_ranked_stat_an_upgrade_wins_by(kb):
    keen = plan(kb, char(gear={"Claw": "item/101"}), "Claw").now[0]               # Keen Guard over Steel Titans
    assert keen.why == ("watk", 8)                                                 # W.ATK first, before CRIT/ACC
    strong = char(gear={"Claw": "item/102"}, ap={"str": 4, "dex": 50, "luk": 140})  # W.ATK 23 Slow (7) worn
    assert plan(kb, strong, "Claw").now[0].why == (gearadvice.SPEED, 3)            # same W.ATK: 3 stages faster
    assert plan(kb, char(gender="", gear={"Hat": "item/130"}), "Hat").now[0].why == ("luk", 3)   # armour: LUK
    assert gearadvice.rank_stats("Thief", "Claw") == ("watk", "luk", "dex", gearadvice.SPEED, "crit", "acc")
    assert gearadvice.rank_stats("Magician", "Hat") == ("int", "luk", "wdef", "hp", "crit", "acc")
    assert gearadvice.rank_stats("Beginner", "Wand") == ("watk", gearadvice.SPEED, "crit", "acc")


def test_changes_show_the_ranked_stats_first_with_losses_and_equals(kb):
    worn, keen = item(kb, "item/101"), item(kb, "item/109")                        # Steel Titans -> Keen Guard
    assert gearadvice.changes("Thief", worn, keen) == (("watk", 8), ("crit", 1), ("acc", 2))
    slow, fast = item(kb, "item/102"), item(kb, "item/103")                        # the same W.ATK 23, faster
    assert gearadvice.changes("Thief", slow, fast) == (("watk", 0), (gearadvice.SPEED, 3), ("crit", 1))
    lesser, spell = item(kb, "item/161"), item(kb, "item/160")                     # M.ATK up, W.ATK down
    assert gearadvice.changes("Magician", lesser, spell) == (("matk", 5), ("watk", -10))
    assert gearadvice.changes("Thief", None, keen)[:2] == (("watk", 23), ("crit", 1))   # nothing worn: all gains
    assert gearadvice.change_text("watk", 10, keen) == "+10 W.ATK"
    assert gearadvice.change_text("dex", -2, keen) == "-2 DEX"
    assert gearadvice.change_text("watk", 0, keen) == "±0 W.ATK"
    assert gearadvice.change_text(gearadvice.SPEED, 3, fast) == "Attack Speed Fast (4)"


def test_the_next_item_past_the_horizon_is_kept(kb):
    top = plan(kb, char(gear={"Claw": "item/109"}), "Claw")                        # Keen Guard: the best wearable
    assert not top.now and not top.soon                                            # (Guardians asks LUK 130)
    assert top.later is not None and top.later.row["item"] == "Far Claw" and top.later.soon == 8
    assert top.later.why == ("watk", 7)


def test_slot_summary_says_the_best_upgrade_and_what_opens_soon(kb):
    s = gearadvice.slot_summary(plan(kb, char(gear={"Claw": "item/101"}), "Claw"))
    assert s.worn == "Steel Titans" and s.best.row["item"] == "Keen Guard" and s.best.why == ("watk", 8)
    assert s.soon == 0 and s.next is None                                          # the best is the answer
    strong = char(gear={"Claw": "item/102"}, ap={"str": 4, "dex": 50, "luk": 140})
    s = gearadvice.slot_summary(plan(kb, strong, "Claw"))
    assert s.best.row["item"] == "Keen Guard" and s.best.why == (gearadvice.SPEED, 3)
    assert (s.soon, s.soon_from, s.soon_to) == (1, 37, 37)                         # Guardians of Doom at 37


def test_slot_summary_nothing_better_names_the_next_item(kb):
    s = gearadvice.slot_summary(plan(kb, char(gear={"Claw": "item/109"}), "Claw"))
    assert s.worn == "Keen Guard" and s.best is None and s.soon == 0
    assert s.next.row["item"] == "Far Claw" and s.next.row["req_lv"] == 40         # past the horizon, still named
    top = char(level=40, gear={"Claw": "item/105"})                                # Far Claw: nothing beats it
    s = gearadvice.slot_summary(plan(kb, top, "Claw"))
    assert s.best is None and s.next is None and s.worn == "Far Claw"


def test_slot_summary_soon_only_and_nothing_worn(kb):
    strong = char(gear={"Claw": "item/109"}, ap={"str": 4, "dex": 50, "luk": 140})  # Keen Guard, LUK for Guardians
    s = gearadvice.slot_summary(plan(kb, strong, "Claw"))
    assert s.best is None and (s.soon, s.soon_from, s.soon_to) == (1, 37, 37)
    assert s.next.row["item"] == "Guardians of Doom" and s.next.soon == 5          # the soonest soon one first
    bare = gearadvice.slot_summary(plan(kb, char(), "Claw"))
    assert bare.worn == "" and bare.best.row["item"] == "Keen Guard"               # nothing worn: the best to wear


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
    """The window on the sample player wearing Steel Titans (item/101, a sprite of its own in the tiny KB)."""
    from maplehelper.i18n import I18n
    from maplehelper.ui.gearadvisor import GearAdvisor
    c = profiles.add("ShadowK", "Thief", "Assassin", 32)
    c.gender, c.ap, c.gear = "Male", {"str": 4, "dex": 50, "luk": 120}, {"Claw": "item/101"}
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


def settle() -> None:
    """Widgets added to a window that shows already show on the loop's next turn (Qt queues it): that turn now."""
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()


def shown(win, cls, name):
    """The window's widgets of this class and object name that show now (a replaced card is hidden at once)."""
    settle()
    return [w for w in win.findChildren(cls, name) if w.isVisibleTo(win)]


def spaced(text: str) -> str:
    """A label's text as read: its no-break spaces (a name or a level kept on one line) as plain ones, and in Hebrew
    without the direction marks around its English blocks."""
    for mark in ("\u200f", "\u2066", "\u2069", "\u202a", "\u202c", "\u2060"):
        text = text.replace(mark, "")
    return text.replace("\u00a0", " ")


def texts(parent, name):
    from PySide6.QtWidgets import QLabel
    settle()
    return [spaced(w.text()) for w in parent.findChildren(QLabel, name) if w.isVisibleTo(parent)]


def card_of(win, item_name):
    """The panel's card naming this item (the worn card or an upgrade's)."""
    from PySide6.QtWidgets import QFrame
    return next(c for c in shown(win, QFrame, "Card") if item_name in texts(c, "ItemName"))


def centre(label) -> tuple[int, int, int]:
    """A picture label's middle pixel: which sprite it shows (the tiny KB's are solid colours)."""
    img = label.pixmap().toImage()
    return img.pixelColor(img.width() // 2, img.height() // 2).getRgb()[:3]


def tiles(win) -> dict:
    return {t.slot: t for t in win._tiles}


def test_the_grid_has_a_tile_per_slot_with_the_worn_picture_and_the_upgrade_count(qapp, win):
    win.refresh()
    grid = tiles(win)
    assert set(grid) == {p.slot for p in win._plans} == {"Claw", "Hat", "Top", "Overall"}
    assert centre(grid["Claw"].pic) == PICTURES["item/101"][1]                      # Steel Titans' own sprite
    assert grid["Hat"].pic is None and grid["Hat"].property("empty") is True       # nothing worn: an outline
    for slot, tile in grid.items():
        n = len(win._plan(slot).now)
        assert (tile.badge.text() if tile.badge is not None else 0) == (str(n) if n else 0), slot
    assert grid["Claw"].badge.text() == "3"                                        # Keen, Fast and Plain Guard
    assert grid["Claw"].property("sel") is True                                    # it opens on the worn slot
    assert not any(t.property("sel") for s, t in grid.items() if s != "Claw")


def test_clicking_a_tile_switches_the_panel_and_says_the_answer(qapp, win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win.refresh()
    qapp.processEvents()                                                           # the grid laid out: tiles to click
    assert spaced(win._answer.text()) == "Keen Guard is your best claw now: +8 W.ATK over Steel Titans."
    QTest.mouseClick(tiles(win)["Hat"], Qt.LeftButton)
    assert win._slot == "Hat" and tiles(win)["Hat"].property("sel") and not tiles(win)["Claw"].property("sel")
    assert win._views.group.buttons()[0].text() == "Hat" and win._views.value() == "slot"
    assert spaced(win._answer.text()) == "Pick what you wear to compare. Best you can wear now: LUK Bamboo Hat."
    assert "Nothing set" in texts(win._scroll.widget(), "ItemMuted")              # the worn card: an empty slot
    QTest.mouseClick(tiles(win)["Claw"], Qt.LeftButton)
    assert win._slot == "Claw" and "Steel Titans" in texts(card_of(win, "Steel Titans"), "ItemName")
    QTest.mouseClick(tiles(win)["Claw"], Qt.LeftButton)                          # the picked tile again: its picker
    settle()
    assert win._pick_box is not None and win._pick_box.isVisibleTo(win)


def test_the_first_card_is_marked_best_and_its_changes_are_signed_chips(qapp, win, profiles):
    from PySide6.QtWidgets import QFrame
    win.refresh()
    cards = [c for c in shown(win, QFrame, "Card") if c.property("best") is not None]
    upgrades = [c for c in cards if "Steel Titans" not in texts(c, "ItemName")]
    assert [c.property("best") for c in upgrades] == [True, False, False]          # the first, and only it
    best = upgrades[0]
    assert texts(best, "ItemName") == ["Keen Guard"] and texts(best, "BestCaption") == ["BEST UPGRADE"]
    assert centre(best.findChild(type(win._answer), "Pic")) == PICTURES["item/109"][1]
    assert texts(best, "ChipLv") == ["Lv. 30"]
    assert texts(best, "ChipGain") == ["+8 W.ATK", "+1 CRIT", "+2 ACC"]
    assert not texts(best, "ChipLoss")
    # the same W.ATK, faster: an equal chip, then why it wins (the attack speed it has)
    c = profiles.active
    c.ap, c.gear = {"str": 4, "dex": 50, "luk": 140}, {"Claw": "item/102"}
    profiles.save()
    win.character_changed()
    keen = card_of(win, "Keen Guard")
    assert texts(keen, "ChipSame") == ["±0 W.ATK"]
    assert texts(keen, "ChipGain") == ["Attack Speed Fast (4)", "+1 CRIT", "+2 ACC"]
    assert texts(card_of(win, "Guardians of Doom"), "ChipSoon") == ["Lv. 37 · in 5 levels"]
    # a stat given up shows red: a Cleric's Spell Wand has more M.ATK and less W.ATK than its Lesser Wand
    wiz = profiles.add("Wiz", "Magician", "Cleric", 30)
    wiz.gear = {"Wand": "item/161"}
    profiles.save()
    win.character_changed()
    spell = card_of(win, "Spell Wand")
    assert texts(spell, "ChipGain") == ["+5 M.ATK"] and texts(spell, "ChipLoss") == ["-10 W.ATK"]


def test_plan_scrolls_only_for_items_with_upgrade_slots(qapp, win):
    from PySide6.QtWidgets import QPushButton
    asked = []
    win.scroll_requested.connect(asked.append)
    win.refresh()
    buttons = shown(win, QPushButton, "Plan")
    assert len(buttons) == 3                                                       # three claws, 7 slots each
    card_of(win, "Keen Guard").findChild(QPushButton, "Plan").click()
    assert asked == ["item/109"]
    win._select("Top")                                                             # Plain Tee: no upgrade slots
    assert texts(card_of(win, "Plain Tee"), "ItemName") == ["Plain Tee"]
    assert not shown(win, QPushButton, "Plan") and asked == ["item/109"]


def test_an_item_no_npc_sells_shows_its_free_market_price(qapp, win, profiles, monkeypatch):
    """Mithril Guards said "no NPC price in the database" while MeowDB had its usual Free Market price (~69.9k, the
    owner's report): a card whose item no NPC sells shows the usual price live from MeowDB, says when there is none
    lately, and says MeowDB was out of reach rather than nothing."""
    import time

    from maplehelper import market
    answers = {109: market.ItemMarket(usual=69900, checks=7), 103: market.ItemMarket(), 108: None}
    monkeypatch.setattr(market, "item_market", lambda item_id, timeout=8: answers.get(item_id))
    win.refresh()
    end = time.time() + 5
    while time.time() < end and any("Checking" in x for x in texts(win._scroll.widget(), "WhereMuted")):
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()
    keen = card_of(win, "Keen Guard")
    assert "Free Market: usually 69,900 mesos (last 14 days)" in " ".join(texts(keen, "Where"))
    assert any("Drops from Jr. Wraith (Lv. 34)" in x for x in texts(keen, "Where"))
    assert "No NPC price in the database, and no Free Market price in the last 14 days" in " ".join(
        texts(card_of(win, "Fast Guard"), "WhereMuted"))
    assert "couldn't reach MeowDB" in " ".join(texts(card_of(win, "Plain Guard"), "WhereMuted"))
    # an item an NPC sells: the price and the seller, its price label left to the hover
    profiles.active.level = 26
    profiles.save()
    win.character_changed()
    shop = card_of(win, "Meba").findChild(type(win._answer), "Where")
    assert "7,000 mesos" in spaced(shop.text()) and "Tulcus Armor Seller (Sleepywood)" in spaced(shop.text())
    assert "COT2" not in shop.text() and "COT2 price" in shop.toolTip()


def test_the_worn_picker_saves_what_you_pick(qapp, win, profiles):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    win.refresh()
    win._worn_btn.click()                                                          # the worn card's Change
    settle()
    assert win._pick_box is not None and win._pick_box.isVisibleTo(win)
    rows = win._pick_rows
    assert rows[0].name.text() == "Nothing worn here"                              # taking it off is a choice
    assert not rows[1].findChild(QLabel, "Pic").pixmap().isNull()                  # each item with its picture
    expected = win._plan("Claw").picker[0]["key"]
    rows[1].clicked.emit()                                                         # the first claw of the list
    qapp.processEvents()
    assert profiles.active.gear.get("Claw") == expected and win._pick_box is None  # saved, the picker closed
    QTest.mouseClick(tiles(win)["Claw"], Qt.LeftButton)                            # the picked tile: the picker
    win._pick_box.setText("keen")
    win._fill_picker()                                                             # (the typing pause, now)
    assert [r.name.text() for r in win._pick_rows] == ["Nothing worn here", "Keen Guard"]
    win._pick_rows[0].clicked.emit()                                               # nothing: taken off
    qapp.processEvents()
    assert "Claw" not in profiles.active.gear


def test_your_stats_fold_once_typed_and_still_save(qapp, win, profiles):
    win.refresh()
    assert not win._stats_edit.isVisibleTo(win) and not win._hint.isVisibleTo(win)  # typed: folded to a line
    assert spaced(win._stats_line.text()) == "STR 4 · DEX 50 · LUK 120"
    win._stats_btn.click()                                                         # Edit
    assert win._stats_edit.isVisibleTo(win)
    win._editors["luk"].setText("140")
    win._stats_saved()                                                             # (the typing pause, now)
    assert profiles.active.ap["luk"] == 140
    assert "Guardians of Doom" in texts(win._scroll.widget(), "ItemName")          # LUK 130 met: it opens soon
    profiles.add("Newbie", "Thief", "Assassin", 32)                                # nothing typed yet
    profiles.save()
    win.character_changed()
    assert win._stats_edit.isVisibleTo(win) and win._hint.isVisibleTo(win)
    assert win._hint.text() == "Type your STR / DEX / INT / LUK to check gear requirements"


def test_a_first_job_class_has_one_weapon_tile_and_a_type_box(qapp, win, profiles):
    """A 1st-job Thief holds claws and daggers both: one weapon tile, as the game's equip window has one weapon
    slot, and a drop-down of the types in the panel (ten chips for a 1st-job Warrior took five rows)."""
    rogue = profiles.add("Rook", "Thief", "Thief", 30)
    rogue.gender, rogue.ap = "Male", {"luk": 120}
    profiles.save()
    win.refresh()
    weapon = win._tiles[0]
    assert weapon.group == {"Claw", "Dagger"} and len(win._tiles) == 4             # the weapon, Hat, Top, Overall
    assert weapon.slot == "Claw" and weapon.property("sel") is True and weapon.pic is None
    assert win._views.group.buttons()[0].text() == "Weapon"
    assert [win._types.itemData(i) for i in range(win._types.count())] == ["Claw", "Dagger"]
    assert win._types.itemText(0) == "Claw · 3 upgrades" and win._types.itemText(1) == "Dagger · 1 upgrade"
    win._types.activated.emit(1)
    settle()                                                                       # (picked after the pop-up closes)
    assert win._slot == "Dagger" and win._tiles[0].slot == "Dagger" and win._tiles[0].property("sel") is True
    assert spaced(win._answer.text()) == "Pick what you wear to compare. Best you can wear now: Reef Dagger."


def test_all_upgrades_lists_every_slots_best_and_opens_one(qapp, win):
    from PySide6.QtWidgets import QFrame
    win.refresh()
    win._views.group.buttons()[1].click()                                          # All upgrades
    rows = shown(win, QFrame, "AllRow")
    assert [texts(r, "ItemName")[0] for r in rows] == ["Keen Guard", "LUK Bamboo Hat", "Plain Tee", "Sauna Robe"]
    assert texts(rows[0], "AllSlot") == ["Claw · you wear Steel Titans"]
    rows[1].clicked.emit()
    assert win._view == "slot" and win._slot == "Hat" and tiles(win)["Hat"].property("sel")


def test_hebrew_mirrors_the_grid_and_keeps_the_counts(qapp, win, settings):
    """Hebrew rebuilds everything: the grid's columns run right to left and each tile keeps its upgrade count. (The
    folded slot's badge once passed the count as a number to bidi.ltr_block, which takes text, and folding a slot
    with upgrades crashed in Hebrew only; the grid's badges and tips carry the count the same way.)"""
    from PySide6.QtCore import Qt
    from maplehelper.i18n import STRINGS, I18n
    win.refresh()
    qapp.processEvents()
    assert tiles(win)["Claw"].x() < tiles(win)["Hat"].x()
    win.apply_language(I18n("he"))
    qapp.processEvents()
    assert win.layoutDirection() == Qt.RightToLeft
    assert win._title.text().replace(chr(0x200F), "") == STRINGS["ga_title"]["he"]
    assert tiles(win)["Claw"].x() > tiles(win)["Hat"].x()                          # mirrored: the weapon on the right
    for slot, tile in tiles(win).items():
        n = len(win._plan(slot).now)
        if n:
            assert tile.badge.text() == str(n) and (str(n) in tile.toolTip() or n == 1), slot
    answer = spaced(win._answer.text())
    assert "Keen Guard" in answer and "+8 W.ATK" in answer and "Lv" not in answer
    assert "Keen Guard" in texts(win._scroll.widget(), "ItemName")
    closed = []
    win.closed.connect(lambda: closed.append(True))
    win._close.click()
    qapp.processEvents()
    assert settings["gear_advisor"] is False and not win.isVisible() and closed == [True]


def test_the_window_follows_the_accent(qapp, win, settings):
    from maplehelper.ui.npcoverlay import ACCENTS, accent_of
    win.refresh()
    assert accent_of(settings)[0] in win.styleSheet()
    settings["overlay_accent"] = "purple"
    win.refresh()                                                                  # the ⚙ panel's pick lands here
    assert ACCENTS["purple"][1] in win.styleSheet() and accent_of(None)[0] not in win.styleSheet()
