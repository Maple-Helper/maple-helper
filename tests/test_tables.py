"""The knowledge base's grep tables (maplehelper/tables.py): every parser on page snippets copied from the real
pages, staleness and rebuilds, whole-or-nothing writes, a bad page or table never stopping the build, and the real
KB's tables (data/kb, built in memory: nothing is written into it)."""
import json
import os
import shutil
from pathlib import Path

import pytest

from maplehelper import brain, quests, tables
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")

# page bodies as the real pages write them (pages/item/663.md, 270.md, 2.md, monster/2.md, map/010001010.md ...)
PAGES = {
    "item/663": ("War Bow", "Equip / Bow", {"Level Requirement": 10, "Weapon Attack": 30, "Upgrade Slots": 7}, """
War Bow
Equip · Bow · No. 0663
REQ LEV 10 REQ DEX 25 JOB Bowman
W.ATK +30
KB 20%
Upgrade Slots 7
Attack Speed Normal (6)
Required Ammunition Arrows for Bows
NPC Sell-back 2,500 mesos
Weapon Details Show Hide ▾ Weapon Multipliers Firing 2.5×
Male + Female Tradeable
Craftable
Woodcrafting Equipment Produces × 1
Req. Level Lv. 2
Craft EXP + 40
Meso Cost 2,500
Ingredients
Processed Wood
× 4
Blue Snail Shell
× 30
Two-Handed Weapon Crafting Catalyst Optional
× 1
From Scratch
Total Meso 3,100
Where to buy
Karl Weapon Seller
Victoria Road: Henesys Weapon Store · Henesys
5,000
mesos
COT2 prices
Dropped By
Community sourced
"""),
    "item/680": ("Garnier", "Equip / Claw", {"Level Requirement": 10}, """
Garnier
Equip · Claw · No. 0680
REQ LEV 10 REQ LUK 25 JOB Thief
W.ATK +10
Upgrade Slots 7
Attack Speed Fast (5)
NPC Sell-back 2,500 mesos
Weapon Details Show Hide ▾
"""),
    "item/1435": ("Work Gloves", "Equip / Gloves", {}, """
Work Gloves
Equip · Gloves · No. 1435
REQ LEV 0
W.DEF +2
Upgrade Slots 5
NPC Sell-back 1 mesos
Male + Female Tradeable
"""),
    "item/270": ("Red Potion", "Use / Potion", {}, """
Red Potion
Use · Potion · No. 0270
HP Recovery +100
NPC Sell-back (per unit) 5 mesos
Max per Stack 100
Tradeable
Where to buy
Arturo Grocer cheapest
Victoria Road: Perion Department Store · Perion
50
mesos
COT2 prices
Hana Grocer cheapest
El Nath: El Nath Department Store · El Nath
40
mesos
COT2 prices
"""),
    "item/274": ("Elixir", "Use / Potion", {}, """
Elixir
Use · Potion · No. 0274
HP Recovery 35%
MP Recovery 35%
Use Cooldown 30s
NPC Sell-back (per unit) 500 mesos
Tradeable
"""),
    "item/2": ("One-Handed Axe Attack Scroll: Greater", "Use / Scroll", {}, """
Improves Weapon Attack on one-handed axes.
Success rate: 10%, Weapon Attack +3

One-Handed Axe Attack Scroll: Greater
Use · Scroll · No. 0002
W.ATK +3
NPC Sell-back (per unit) 1 mesos
Max per Stack 100
Tradeable
"""),
    "item/30": ("Processed Wood", "Etc / Crafting Material", {}, "\nProcessed Wood\nEtc · Crafting Material · No. 0030\n"),
    "item/31": ("Blue Snail Shell", "Etc / Monster Drop", {}, "\nBlue Snail Shell\n"),
    "item/32": ("Two-Handed Weapon Crafting Catalyst", "Etc", {}, "\nCatalyst\n"),
    "item/20": ("Bronze Ore", "Etc / Ore & Mineral", {}, "\nBronze Ore\n"),
    "item/21": ("Bronze Ingot", "Etc / Crafting Material", {}, "\nBronze Ingot\n"),
    "monster/2": ("Snail", "Monster", {"Level": 1, "HP": 45, "MP": 30, "EXP": 2, "Accuracy": 33, "Avoidability": 0}, """
No. 002 Standard · Bestiary
Snail
Level 1
No elemental affinity
Mesos per kill Loading...
HP
45
Drops (MS Classic)
Community sourced
MSEA reference drops
Blue Snail Shell
Etc
Map Locations ( 2 )
Map | Count ↓ | Share ↓ | Types ↓ | Mob Rate | Respawn
Snail Hunting Ground I Maple Road | 40 | 40 / 40 100 % | 1 | 1.0 x | ~7.5s
Henesys Hunting Ground I Victoria Road | 4 | 4 / 39 10 % | 7 | 1.5 x | ~7.5s
Change history
"""),
    "monster/4": ("Ligator", "Monster", {"Level": 22, "HP": 650, "EXP": 32, "Avoidability": 15,
                                         "Physical Defense": 30}, """
Ligator
Level 22
Fire weak Ice strong
Map Locations ( 1 )
Map | Count ↓ | Share ↓ | Types ↓ | Mob Rate | Respawn
Henesys Hunting Ground I Victoria Road | 5 | 5 / 39 13 % | 7 | 1.5 x | ~7.5s
"""),
    "map/010001010": ("Henesys Hunting Ground I", None, {}, """
Henesys Hunting Ground I
Add to watchlist Find path here
Location Victoria Road / Victoria Island
Monster levels Lv 2-14
Spawn points 39
EXP rank #244 / 279
Map Summary
EXP /hr
41,429
27 % of ceiling 153,216
"""),
    "map/000000040": ("Snail Hunting Ground I", None, {}, "\nSnail Hunting Ground I\nLocation Maple Road / Maple Island\n"
                                                       "Monster levels Lv 1\nSpawn points 40\n"),
    "npc/5": ("Arturo", None, {}, """
Shopkeeper in Perion.

Arturo
Grocer
Shopkeeper
Location
Henesys Hunting Ground I Victoria Road
What Arturo Says
Need a potion?
Shop inventory (3 items)
COT2 prices full shop page →
Sold at: Victoria Road: Perion Department Store
| Item | Price
| Red Potion | 50 mesos
| Elixir Citizen of Honor + | 3,000 mesos
| Work Gloves | 100 mesos
Source : COT2 in-game footage, 2026-08-09
What They Do
"""),
    "npc/7": ("Karl", None, {}, "\nKarl\nWeapon Seller\nLocation\nHenesys Hunting Ground I Victoria Road\n"),
    "quest/1": ("Arturo's Errand", "Quest", {"Minimum Level": 10, "Area": "Victoria Island", "NPC": "Arturo",
                                            "EXP Reward": 100, "Meso Reward": 50}, """
Arturo's Errand
Victoria Island · Start: Arturo · Turn in: Arturo
Pre-requisites
Level Lv. 10+
Quest Complete First Steps
Requirements
Defeat Snail x 10 Red Potion x 5
Rewards
100 EXP 50 Mesos
War Bow x 1
Description
01 Hunt snails for Arturo.
Questline · Step 2 of 3
"""),
    "skill/bowman__double-shot": ("Double Shot", "Character Skill",
                                  {"Max Level": 20, "Job": "Bowman", "Job Rank": "1st Job", "Target Cap": 2,
                                   "Prerequisite": "At least Level 1 on Arrow Blow"}, """
Bowman : 1st Job
Double Shot
ACTIVE Cast: Ground only PROJECTILE
Level 1
MP -8; Damage 80%
TARGETS 2 RANGE 300-420 px
Level 20 (MAX)
MP -16; Damage 120%
TARGETS 2 RANGE 300-420 px
Element None The skill has no elemental advantages or disadvantages.
Weapon requirement Any weapon The skill does not require a specific weapon.
"""),
    "skill/ranger__inferno": ("Inferno", "Character Skill", {"Max Level": 30, "Job": "Ranger", "Job Rank": "3rd Job"},
                              "\nInferno\nACTIVE\nLevel 30 (MAX)\nMP -20; Damage 150%\nNot in initial launch\n"),
    "crafting/efficiency__smithing": ("Smithing Efficiency", None, {}, """
Smithing Efficiency
Lv. 1
needs 50 EXP · char Lv. 10 + ( 1 recipes )
# | Recipe / Ingredients | EXP | Catalyst | Mat value | Mat Craft Value | Sell-back | Net | EXP / meso | Mats
1 | Bronze Ingot
5 x Bronze Ore
| 3 | 100 | + 100 | - | + 100 | -100 | 0.030 | Farm only
"""),
}
ROUTES = {"maps": [{"id": "010001010", "name": "Henesys Hunting Ground I", "street": "Victoria Road", "town": False,
                    "portals": [{"to": "000000040", "name": "out00"}], "npcs": [{"id": "5", "name": "Arturo"}]},
                   {"id": "000000040", "name": "Snail Hunting Ground I", "street": "Maple Road", "town": False,
                    "portals": [], "npcs": []}]}


def make_kb(root: Path, pages: dict = PAGES) -> KnowledgeBase:
    index = []
    for key, (name, typ, props, body) in pages.items():
        cat, _, slug = key.partition("/")
        (root / "pages" / cat).mkdir(parents=True, exist_ok=True)
        front = {"name": name, "category": cat, "props": props, "type": typ}
        (root / "pages" / cat / f"{slug}.md").write_text(
            "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + f"\n---\n\n# {name}\n{body}", encoding="utf-8")
        index.append({"key": key, "id": slug, "name": name, "category": cat, "props": props, "type": typ})
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "aliases.json").write_text("{}", encoding="utf-8")
    (root / "routes.json").write_text(json.dumps(ROUTES), encoding="utf-8")
    quests._quest.cache_clear()
    return KnowledgeBase(root)


@pytest.fixture
def tiny(tmp_path):
    kb = make_kb(tmp_path)
    assert tables.ensure(kb)
    return kb


def one(rows, **match):
    hits = [r for r in rows if all(r.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, (match, hits)
    return hits[0]


# ---------------------------------------------------------------- parsers

def test_equips_read_the_stat_header(tiny):
    bow = one(tables.rows(tiny, "equips"), key="item/663")
    assert (bow["slot"], bow["job"], bow["req_lv"], bow["req_dex"], bow["watk"]) == ("Bow", "Bowman", 10, 25, 30)
    assert (bow["attack_speed"], bow["slots"], bow["sell"], bow["buy"]) == ("Normal (6)", 7, 2500, 5000)
    assert bow["seller"] == "Karl (Henesys, COT2 price)"          # the price's label, for the answer's "(COT2)"
    claw = one(tables.rows(tiny, "equips"), key="item/680")
    assert (claw["job"], claw["req_luk"], claw["attack_speed"]) == ("Thief", 25, "Fast (5)")
    gloves = one(tables.rows(tiny, "equips"), key="item/1435")
    assert (gloves["job"], gloves["req_lv"], gloves["wdef"], gloves["watk"]) == ("Any", 0, 2, None)


def test_consumables_and_scrolls(tiny):
    red = one(tables.rows(tiny, "consumables"), key="item/270")
    # the El Nath shop is cheaper, but not in the game (no release guide in a tiny KB: Perion and El Nath both open,
    # so the cheapest one shows; the real KB's test below checks the place rule)
    assert (red["type"], red["hp"], red["mp"], red["sell"]) == ("Potion", "100", None, 5)
    elixir = one(tables.rows(tiny, "consumables"), key="item/274")
    assert (elixir["hp"], elixir["mp"], elixir["effect"]) == ("35%", "35%", "Use Cooldown 30s")
    s = one(tables.rows(tiny, "scrolls"), key="item/2")
    assert (s["slot"], s["grade"], s["success"], s["stats"]) == ("One-Handed Axe", "Greater", 10, "Weapon Attack +3")


def test_monsters_spawns_and_maps(tiny):
    snail = one(tables.rows(tiny, "monsters"), key="monster/2")
    assert (snail["level"], snail["hp"], snail["exp"], snail["hp_per_exp"], snail["element"]) == (1, 45, 2, 22.5, None)
    assert snail["acc_needed"] == 0 and snail["respawn"] == 7.5
    assert snail["maps"] == "Snail Hunting Ground I; Henesys Hunting Ground I"
    assert one(tables.rows(tiny, "monsters"), key="monster/4")["element"] == "Fire weak Ice strong"
    sp = one(tables.rows(tiny, "spawns"), monster_key="monster/2", map_key="map/010001010")
    assert (sp["count"], sp["share"], sp["mob_rate"], sp["street"]) == (4, 10, 1.5, "Victoria Road")
    hhg = one(tables.rows(tiny, "maps"), key="map/010001010")
    assert (hhg["region"], hhg["lv_min"], hhg["lv_max"], hhg["spawn_points"], hhg["exp_hr"], hhg["exp_rank"]) == \
        ("Victoria Island", 2, 14, 39, 41429, 244)
    assert (hhg["monsters"], hhg["npcs"], hhg["connects"]) == ("Ligator; Snail", "Arturo", "Snail Hunting Ground I")


def test_npcs_and_shops(tiny):
    arturo = one(tables.rows(tiny, "npcs"), key="npc/5")
    assert (arturo["role"], arturo["map_key"], arturo["street"]) == ("Grocer · Shopkeeper", "map/010001010",
                                                                     "Victoria Road")
    shops = tables.rows(tiny, "shops")
    # the item page's row and the NPC page's row are one sale: listed once, with the town the item page names
    red = one(shops, npc_key="npc/5", item_key="item/270")
    assert (red["price"], red["label"], red["place"]) == (50, "COT2", "Victoria Road: Perion Department Store · Perion")
    elixir = one(shops, npc_key="npc/5", item_key="item/274")     # "Elixir Citizen of Honor +": the grade split off
    assert (elixir["item"], elixir["price"], elixir["rank"]) == ("Elixir", 3000, "Citizen of Honor")
    assert one(shops, item_key="item/1435")["price"] == 100
    assert one(shops, item_key="item/663")["npc"] == "Karl"


def test_quests_and_what_they_ask(tiny):
    q = one(tables.rows(tiny, "quests"), key="quest/1")
    assert (q["level"], q["npc_key"], q["turn_in"], q["exp"], q["mesos"]) == (10, "npc/5", "Arturo", 100, 50)
    assert (q["after"], q["questline"], q["rewards"]) == ("First Steps", "2/3", "War Bow x 1")
    reqs = [(r["kind"], r["target"], r["target_key"], r["count"]) for r in tables.rows(tiny, "quest_reqs")]
    assert reqs == [("defeat", "Snail", "monster/2", 10), ("collect", "Red Potion", "item/270", 5)]
    assert one(tables.rows(tiny, "rewards"), quest_key="quest/1")["item_key"] == "item/663"


def test_recipes_from_item_pages_and_crafting_pages(tiny):
    bow = [r for r in tables.rows(tiny, "recipes") if r["product_key"] == "item/663"]
    assert [(r["ingredient"], r["ingredient_key"], r["qty"], r["optional"]) for r in bow] == [
        ("Processed Wood", "item/30", 4, None), ("Blue Snail Shell", "item/31", 30, None),
        ("Two-Handed Weapon Crafting Catalyst", "item/32", 1, "yes")]
    assert {(r["discipline"], r["prof_lv"], r["craft_exp"], r["meso_cost"], r["makes"]) for r in bow} == \
        {("Woodcrafting", 2, 40, 2500, 1)}
    # Bronze Ingot's page has no recipe: the Smithing table's row stands in
    ingot = one(tables.rows(tiny, "recipes"), product="Bronze Ingot")
    assert (ingot["ingredient_key"], ingot["qty"], ingot["discipline"], ingot["prof_lv"]) == ("item/20", 5, "Smithing", 1)


def test_skills_at_max_level_and_only_in_the_game(tiny):
    ds = one(tables.rows(tiny, "skills"), key="skill/bowman__double-shot")
    assert (ds["job"], ds["rank"], ds["max_lv"], ds["mp"], ds["damage"], ds["targets"]) == \
        ("Bowman", "1st Job", 20, 16, 120, 2)
    assert (ds["kind"], ds["element"], ds["weapon"], ds["effect"]) == ("active", None, "Any weapon",
                                                                       "MP -16; Damage 120%")
    assert not [r for r in tables.rows(tiny, "skills") if r["job"] == "Ranger"]       # "Not in initial launch"


def test_every_key_is_the_kbs(tiny):
    for name, (cols, _) in tables.TABLES.items():
        for r in tables.rows(tiny, name):
            for c in cols:
                if (c == "key" or c.endswith("_key")) and r[c]:
                    assert tiny.get(r[c]), (name, c, r[c])


# ---------------------------------------------------------------- freshness, writes, failures

def test_headers_and_prompt_come_from_one_schema(tiny):
    for name, (cols, _) in tables.TABLES.items():
        assert (tiny.root / f"{name}.tsv").read_text(encoding="utf-8").split("\n", 1)[0] == "\t".join(cols)
        if name != "names":
            assert f"{name}.tsv: {', '.join(cols)}" in brain.SYSTEM_PROMPT
    assert "{" not in tables.prompt_note()          # SYSTEM_PROMPT is a str.format template


def test_current_tables_are_left_alone(tiny):
    before = {f: (tiny.root / f).stat().st_mtime_ns for f in tables.GENERATED}
    tables._fresh.clear()
    assert tables.ensure(tiny)
    assert before == {f: (tiny.root / f).stat().st_mtime_ns for f in tables.GENERATED}


def test_a_kb_unpacked_elsewhere_keeps_its_tables(tiny, tmp_path):
    # a kb.zip carries its tables: new file times after unzipping, the same content, so they are current at once
    moved = tmp_path / "unpacked"
    shutil.copytree(tiny.root, moved)
    for f in moved.rglob("*"):
        os.utime(f, (1, 1))
    assert tables.current(moved)


@pytest.mark.parametrize("change", ["index", "community", "page", "schema", "old mark", "missing table"])
def test_stale_tables_are_rebuilt(tiny, monkeypatch, change):
    root = tiny.root
    if change == "index":
        index = json.loads((root / "index.json").read_text(encoding="utf-8"))
        index.append({"key": "item/99", "name": "Brand New Cape", "category": "item", "type": "Equip / Cape"})
        (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    elif change == "community":
        (root / "community.json").write_text(json.dumps({"monsters": {}}), encoding="utf-8")
    elif change == "page":
        p = root / "pages" / "monster" / "2.md"
        # (a real page change comes with index.json's new page hash; a page alone counts by its size)
        p.write_text(p.read_text(encoding="utf-8").replace("| 40 | 40 / 40", "| 400 | 400 / 400"), encoding="utf-8")
    elif change == "schema":
        monkeypatch.setattr(tables, "TABLES_VERSION", tables.TABLES_VERSION + 1)
    elif change == "old mark":
        (root / tables.MARK_FILE).write_text(tables.DROPS_MARK, encoding="utf-8")     # the mark before tables.py
    else:
        (root / "spawns.tsv").unlink()
    assert not tables.current(root)
    tables._fresh.clear()             # a new start (a page or the app's tables changed: an update, a restart)
    kb = KnowledgeBase(root)          # (a changed KB is loaded anew, as the app does)
    assert tables.ensure(kb) and tables.current(root)
    if change == "index":
        assert one(tables.rows(kb, "names"), key="item/99")["name"] == "Brand New Cape"
    if change == "page":
        assert one(tables.rows(kb, "spawns"), map_key="map/000000040")["count"] == 400


def test_a_swapped_folder_is_never_written(tiny):
    """The KB object was loaded from the folder, then an update swapped another KB in: its tables would be wrong."""
    (tiny.root / "spawns.tsv").unlink()
    (tiny.root / "index.json").write_text("[]", encoding="utf-8")
    tables._fresh.clear()
    tables._failed.clear()
    assert not tables.ensure(tiny)
    assert not (tiny.root / "spawns.tsv").exists()


def test_writes_are_whole_or_nothing(tiny, monkeypatch):
    path = tiny.root / "equips.tsv"
    old = path.read_text(encoding="utf-8")
    real = os.replace
    calls = []

    def held(src, dst):             # a reader (a grep on Windows) holds the old file a moment
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)
    monkeypatch.setattr(tables.os, "replace", held)
    monkeypatch.setattr(tables.time, "sleep", lambda s: None)
    tables._write(path, "new")
    assert path.read_text(encoding="utf-8") == "new" and len(calls) == 3
    monkeypatch.setattr(tables.os, "replace", lambda src, dst: (_ for _ in ()).throw(PermissionError("in use")))
    with pytest.raises(PermissionError):
        tables._write(path, "newer")
    assert path.read_text(encoding="utf-8") == "new"
    assert not list(tiny.root.glob("*.tmp"))
    # a build that can't write leaves no mark: the next question tries again
    path.write_text(old, encoding="utf-8")
    (tiny.root / tables.MARK_FILE).unlink()
    assert not tables.build(tiny)
    assert not (tiny.root / tables.MARK_FILE).exists() and not list(tiny.root.glob("*.tmp"))


def test_a_failed_build_waits_before_trying_again(tiny, monkeypatch):
    (tiny.root / tables.MARK_FILE).unlink()
    tables._fresh.clear()
    tables._failed.clear()
    built = []
    monkeypatch.setattr(tables, "build", lambda kb, out=None: built.append(1) and False)
    assert not tables.ensure(tiny) and not tables.ensure(tiny)
    assert len(built) == 1                  # a read-only folder: not a 2 s build before every question


def test_a_held_table_fails_the_build_before_any_is_replaced_and_retries_soon(tiny, monkeypatch):
    """Written one by one, a table an antivirus held failed the build midway: old and new tables mixed, for 10 min."""
    (tiny.root / tables.MARK_FILE).unlink()
    tables._fresh.clear()
    tables._failed.clear()
    staged = []

    def held(src, dst):
        staged.append(len(list(tiny.root.glob("*.tmp"))))
        raise PermissionError("in use")
    monkeypatch.setattr(tables.os, "replace", held)
    monkeypatch.setattr(tables.time, "sleep", lambda s: None)
    assert not tables.ensure(tiny)
    assert staged[0] == len(tables.GENERATED)          # every table (and the mark) written before the first replace
    assert not list(tiny.root.glob("*.tmp")) and not (tiny.root / tables.MARK_FILE).exists()
    assert tables._failed[str(tiny.root)][2] == tables.HELD_RETRY


def test_a_bad_page_or_table_never_stops_the_build(tiny, monkeypatch):
    real = tables._equip

    def broken(ctx, key, e):
        if key == "item/663":
            raise ValueError("a page in a new layout")
        return real(ctx, key, e)
    monkeypatch.setattr(tables, "_equip", broken)
    monkeypatch.setitem(tables.BUILDERS, "skills", lambda ctx: 1 / 0)
    out = tables.generate(tiny)
    assert [r["key"] for r in out["equips"]] == ["item/680", "item/1435"]
    assert out["skills"] == [] and len(out["monsters"]) == 2


def test_rows_are_typed_and_cached(tiny):
    rows = tables.rows(tiny, "monsters")
    assert isinstance(rows[0]["level"], int) and isinstance(rows[0]["hp_per_exp"], float)
    assert tables.rows(tiny, "monsters") is rows
    with pytest.raises(KeyError):
        tables.rows(tiny, "nope")


def test_building_holds_off_a_kb_swap(tiny):
    assert not tables.building()
    tables._fresh.clear()
    with tables._lock:              # a build running elsewhere: a question waits a moment, then goes on without
        assert tables.building()
        assert not tables.ensure(KnowledgeBase(tiny.root), wait=0.01)
    assert tables.ensure(KnowledgeBase(tiny.root)) and not tables.building()


def test_the_old_name_still_builds(tmp_path):
    kb = make_kb(tmp_path)
    kb.ensure_drop_table()
    assert all((tmp_path / f).exists() for f in tables.GENERATED)


def test_kb_release_refreshes_the_shipped_tables(tmp_path):
    import kb_release
    make_kb(tmp_path)
    assert kb_release.refresh_tables(tmp_path) and tables.current(tmp_path)


# ---------------------------------------------------------------- the real knowledge base

@pytest.fixture(scope="module")
def real():
    kb = KnowledgeBase(REAL_KB)
    return kb, tables.generate(kb)          # in memory: nothing is written into data/kb


@needs_kb
def test_real_tables_counts(real):
    _, t = real
    least = {"names": 3000, "drops": 800, "rewards": 250, "equips": 600, "consumables": 40, "scrolls": 80,
             "monsters": 50, "maps": 200, "spawns": 400, "npcs": 100, "shops": 500, "quests": 200, "quest_reqs": 250,
             "recipes": 800, "skills": 60}
    assert {n: len(t[n]) >= m for n, m in least.items()} == {n: True for n in least}


@needs_kb
def test_real_tables_known_facts(real):
    kb, t = real
    assert one(t["rewards"], quest="Stranger's Identity", item="Old Raggedy Cape")["kind"] == "sure"
    bow = one(t["equips"], item="War Bow")
    assert (bow["job"], bow["req_lv"], bow["watk"], bow["attack_speed"]) == ("Bowman", 10, 30, "Normal (6)")
    red = [r for r in t["shops"] if r["item"] == "Red Potion"]
    assert red and all(r["price"] == 50 for r in red)
    assert not [r for r in red if "El Nath" in r["place"] or "Orbis" in r["place"]]       # not in the game yet
    ingot = [r for r in t["recipes"] if r["product"] == "Bronze Ingot"]
    assert [(r["ingredient"], r["qty"], r["discipline"]) for r in ingot] == [("Bronze Ore", 5, "Smithing")]
    assert one(t["monsters"], monster="Snail")["level"] == 1
    assert one(t["spawns"], monster="Snail", map="Snail Hunting Ground I")["count"] > 0
    hhg = one(t["maps"], map="Henesys Hunting Ground I")
    assert (hhg["lv_min"], hhg["lv_max"], hhg["region"]) == (2, 14, "Victoria Island") and "Blue Snail" in hhg["monsters"]
    ds = one(t["skills"], skill="Double Shot")
    assert (ds["mp"], ds["damage"], ds["max_lv"]) == (16, 120, 20)
    assert one(t["skills"], skill="Steal")["damage"] == 180                # "apply 180% in damage"
    assert not [r for r in t["skills"] if r["rank"] == "3rd Job"]          # 3rd job isn't out
    # the cheapest seller's citizen rank (Raymond sells these for 2 mesos to a Helpful Stranger only)
    assert "citizen rank Helpful Stranger" in one(t["consumables"], item="Bronze Arrows for Bows")["seller"]
    assert not [r for r in t["monsters"] if "spawns.tsv" in str(r["maps"])]     # answers never name files


@needs_kb
def test_real_tables_cover_the_pages(real):
    from maplehelper import availability
    kb, t = real
    a = availability.of(kb)
    for name, (cols, _) in tables.TABLES.items():
        for r in t[name]:
            for c in cols:
                if (c == "key" or c.endswith("_key")) and r.get(c):
                    assert kb.get(r[c]), (name, c, r[c])
    sellers = {r["npc_key"] for r in t["shops"]}
    assert [k for k, e in kb.entities.items() if e["category"] == "npc" and a.npc_open(k)
            and "Shop inventory" in kb.page(k) and k not in sellers] == []
    given = {r["quest_key"] for r in t["rewards"]}
    for k, e in kb.entities.items():
        if e["category"] == "quest" and a.quest_open(k):
            q = quests.quest(kb, k)
            if q and (q.rewards or q.class_rewards or q.random_rewards or q.gender_rewards):
                assert k in given, k
    # only what is in the game
    assert all(a.monster_key_open(r["key"]) for r in t["monsters"])
    assert all(a.entity_open(r["key"]) for r in t["maps"])
    assert all(a.quest_open(r["key"]) for r in t["quests"])
    assert all(a.item_open(r["key"]) for r in t["equips"])
    # most shop items, spawn maps and NPC places resolve to their keys
    assert sum(1 for r in t["shops"] if r["item_key"]) > 0.95 * len(t["shops"])
    assert all(r["map_key"] for r in t["spawns"])
