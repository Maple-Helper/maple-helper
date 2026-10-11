"""The crafting advisor (maplehelper/craftplan.py and the Crafting page's Craft plan tab): the recipe tree
expanded to raw mats with the makes rounded up, the bag spent before anything is farmed, the level-up math (the
Mastery bar %, the plan's own crafts' EXP, the player's measured EXP winning over the KB's table, the character
level a crossing asks for), the totals equal to the sum of the steps, and the tab's own life (inside the Tools
window, the active character's, rows in both languages, the numbers typed saved, a crafted step filling the bag). The real-KB tests check a known
chain — the Mithril Wand, whose own page carries its "From Scratch" numbers — against the recipes table."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from maplehelper import bidi, craftplan, tables
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")

WAND = "item/653"


# ---------------------------------------------------------------- a tiny KB with the shapes that matter


def _equip_page(name, req, matk, shop="", craftable=""):
    body = f"\n{name}\nEquip · Wand · No. 0000\nREQ LEV {req} JOB Mage\nM.ATK +{matk}\n"
    body += "NPC Sell-back 1 mesos\nMale + Female Tradeable\n" + shop + craftable
    return {"name": name, "typ": "Equip / Wand", "props": {"Level Requirement": req, "Magic Attack": matk}, "body": body}


def _craftable(disc, kind, makes, lv, exp, fee, ings):
    out = f"{disc} {kind} Produces × {makes}\nReq. Level Lv. {lv}\nCraft EXP + {exp}\nMeso Cost {fee:,}\nIngredients\n"
    for name, qty, optional in ings:
        out += f"{name}{' Optional' if optional else ''}\n× {qty}\n"
    return out


def _etc_page(name, typ, shop=""):
    return {"name": name, "typ": f"Etc / {typ}", "props": {}, "body": f"\n{name}\n{shop}"}


# the wand's own recipe, the materials it chains down to, the level-up candidates, and the raw mats.
# Bronze Ore is the one raw mat an NPC sells — the priced path through the cost math.
WAND_SHOP = """Where to buy
Flora the Fairy
Victoria Road: Ellinia · Ellinia
22,000
mesos
COT2 prices
"""
ORE_SHOP = """Where to buy
Muel the Merchant
Victoria Road: Victoria Road: Ellinia · Ellinia
50
mesos
COT2 prices
"""
PAGES = {
    WAND: _equip_page(
        "Mithril Wand",
        30,
        55,
        WAND_SHOP,
        _craftable(
            "Arcforge",
            "Equipment",
            1,
            6,
            120,
            5500,
            [
                ("Mithril Ingot", 6, False),
                ("Aquamarine", 4, False),
                ("Screw", 18, False),
                ("One-Handed Weapon Crafting Catalyst", 1, True),
            ],
        ),
    ),
    "item/422": _etc_page(
        "Mithril Ingot", "Ore & Mineral", _craftable("Smithing", "Materials", 1, 2, 6, 200, [("Mithril Ore", 5, False)])
    ),
    "item/423": _etc_page("Aquamarine", "Gem", _craftable("Arcforge", "Materials", 1, 1, 3, 200, [("Aquamarine Ore", 1, False)])),
    # the two Screw recipes: ingots (makes 10) or Iron Hog's Metal Hoofs (makes 3, a higher level)
    "item/305": _etc_page(
        "Screw",
        "Crafting Material",
        _craftable("Weaponcrafting", "Materials", 10, 1, 3, 0, [("Bronze Ingot", 1, False), ("Iron Ingot", 1, False)])
        + _craftable("Weaponcrafting", "Materials", 3, 5, 15, 0, [("Iron Hog's Metal Hoof", 10, False)]),
    ),
    "item/420": _etc_page(
        "Bronze Ingot", "Ore & Mineral", _craftable("Smithing", "Materials", 1, 1, 3, 100, [("Bronze Ore", 5, False)])
    ),
    "item/421": _etc_page(
        "Iron Ingot", "Ore & Mineral", _craftable("Smithing", "Materials", 1, 1, 3, 100, [("Iron Ore", 5, False)])
    ),
    # the level-up candidates: the parchment is the cheapest per EXP (200/6), the wand isn't (3500/80)
    "item/340": _etc_page(
        "Processed Parchment", "Crafting Material", _craftable("Arcforge", "Materials", 1, 2, 6, 200, [("Leather", 3, False)])
    ),
    "item/651": _equip_page(
        "Metal Wand",
        20,
        41,
        "",
        _craftable(
            "Arcforge",
            "Equipment",
            1,
            4,
            80,
            3500,
            [("Iron Ingot", 4, False), ("Blue Mushroom Cap", 30, False), ("Screw", 12, False)],
        ),
    ),
    "item/415": _etc_page("Mithril Ore", "Ore & Mineral"),
    "item/413": _etc_page("Bronze Ore", "Ore & Mineral", ORE_SHOP),
    "item/414": _etc_page("Iron Ore", "Ore & Mineral"),
    "item/322": _etc_page("Aquamarine Ore", "Gem"),
    "item/361": _etc_page("Blue Mushroom Cap", "Monster Drop"),
    "item/356": _etc_page("Leather", "Monster Drop"),
    "item/370": _etc_page("Iron Hog's Metal Hoof", "Monster Drop"),
    "item/520": _etc_page("One-Handed Weapon Crafting Catalyst", "Crafting Material"),
    # the KB's Crafting Levels table, the same numbers the real one has up to the crossing the plan needs
    "formula/leveling": {
        "name": "Leveling & Tables Formulas",
        "typ": None,
        "props": {},
        "body": """
Citizenship and crafting
Crafting Levels
Crafting level | EXP to next level | Total EXP | Character level
1 | 50 | 0 | 10
2 | 115 | 50 | 10
3 | 199 | 165 | 15
4 | 308 | 364 | 20
5 | 450 | 672 | 25
6 | 635 | 1,122 | 30
Crafting currently caps at level 10.""",
    },
}


def make_kb(root: Path) -> KnowledgeBase:
    index = []
    for key, p in PAGES.items():
        cat, _, slug = key.partition("/")
        (root / "pages" / cat).mkdir(parents=True, exist_ok=True)
        front = {"name": p["name"], "category": cat, "props": p["props"], "type": p["typ"]}
        (root / "pages" / cat / f"{slug}.md").write_text(
            "---\n" + json.dumps(front, ensure_ascii=False, indent=1) + f"\n---\n\n# {p['name']}\n{p['body']}", encoding="utf-8"
        )
        index.append({"key": key, "id": slug, "name": p["name"], "category": cat, "props": p["props"], "type": p["typ"]})
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    (root / "aliases.json").write_text("{}", encoding="utf-8")
    (root / "routes.json").write_text('{"maps": []}', encoding="utf-8")
    return KnowledgeBase(root)


@pytest.fixture(scope="module")
def _own_kb(tmp_path_factory) -> Path:
    """This file's own tiny KB (the shared fixture KB has no recipes), its tables built once here."""
    root = tmp_path_factory.mktemp("craftplan") / "kb"
    make_kb(root)
    assert tables.ensure(kb=KnowledgeBase(root), wait=30)
    return root


@pytest.fixture
def kb(_own_kb) -> KnowledgeBase:
    return KnowledgeBase(_own_kb)


@pytest.fixture
def character():
    from maplehelper.store import Character

    c = Character(id="w", name="Wiz", base_class="Magician", job="Wizard", level=30)
    c.crafts = {"arcforge": 4}
    c.craft_pct = {"arcforge": 40}
    return c


# ---------------------------------------------------------------- the tree, with makes rounding


def test_expand_rounds_makes_up_and_leaves_the_rest(kb):
    """18 Screws from a recipe that makes 10: 2 crafts, 20 made, 2 left over — and the whole wand's raw
    mats are the fees (7,900) plus the one raw mat an NPC sells (Bronze Ore ×10 at 50)."""
    tree = craftplan.expand(kb, "Mithril Wand")
    raws = {name: (qty, price) for name, _, qty, price in tree.raws}
    assert raws == {"Mithril Ore": (30, None), "Aquamarine Ore": (4, None), "Bronze Ore": (10, 50), "Iron Ore": (10, None)}
    assert tree.crafts == 17  # 1 wand + 6 ingots + 4 gems + 2+2 ingots + 2 screw batches
    assert tree.cost == 5500 + 6 * 200 + 4 * 200 + 2 * 100 + 2 * 100 + 10 * 50
    assert tree.leftovers == (("Screw", 2),)


def test_expand_counts_two_of_a_target_rounding_once(kb):
    """Two wands share one Screw run: 36 Screws is 4 crafts of 10 (not 2 + 2 crafts' worth of ingredients
    demanded apart), and everything that makes more than one craft's worth merges."""
    tree = craftplan.expand(kb, "Mithril Wand", 2)
    raws = {name: qty for name, _, qty, _ in tree.raws}
    assert raws["Mithril Ore"] == 60 and raws["Bronze Ore"] == 20  # 2× one wand's, no rounding twice
    assert tree.leftovers == (("Screw", 4),)  # 36 Screws from 4 crafts of 10


def test_swap_uses_the_other_screw_recipe(kb):
    """The player's own pick (the Swap button) beats the mode: Screw from Iron Hog's Metal Hoofs."""
    tree = craftplan.expand(kb, "Mithril Wand", choices={"screw": 2})
    raws = {name: qty for name, _, qty, _ in tree.raws}
    assert raws["Iron Hog's Metal Hoof"] == 60 and "Bronze Ore" not in raws


# ---------------------------------------------------------------- the bag is spent first


def test_plan_spends_the_bag_before_farming(kb, character):
    """22 Mithril Ore in the bag cover 22 of the 30: only 8 are farmed, and 8 Screws shrink the Screw
    batch to one craft (no leftover)."""
    character.craft_targets = [WAND]
    bag = {"item/415": 22, "item/305": 8}
    p = craftplan.plan(kb, character, [WAND], bag)
    by_name = {f.name: f for f in p.farm}
    assert by_name["Mithril Ore"].need == 30 and by_name["Mithril Ore"].bag == 22
    assert by_name["Mithril Ore"].farm == 8
    assert dict(p.bag_used)["Mithril Ore"] == 22 and dict(p.bag_used)["Screw"] == 8
    assert ("Screw", 2) not in p.leftovers and not any(n == "Screw" and q for n, q in p.leftovers)
    screw = next(s for st in p.stages for s in st.steps if s.name == "Screw")
    assert screw.crafts == 1  # 18 needed, 8 in the bag, one craft of 10 covers the rest


def test_plan_bag_of_the_item_itself_makes_no_crafts(kb, character):
    """A carried Mithril Wand is the plan: nothing to craft, nothing to farm."""
    p = craftplan.plan(kb, character, [WAND], {WAND: 1})
    assert p.stages == () or all(not st.steps for st in p.stages)
    assert not [f for f in p.farm if f.farm > 0]


# ---------------------------------------------------------------- the level-up math


def test_plan_levels_arcforge_from_the_bar_percent(kb, character):
    """Arcforge 4 at 40% to 6: 308+450 EXP minus the 123 the bar earned, minus the 12 EXP the plan's own
    Aquamarine crafts give, earned by the cheapest recipe per EXP — the parchment, 104 crafts of it."""
    p = craftplan.plan(kb, character, [WAND], {})
    stage = next(st for st in p.stages if st.kind == "level" and st.discipline == "Arcforge")
    assert (stage.level_from, stage.level_to) == (4, 6) and not stage.blocked
    assert stage.exp_needed == 308 + 450 - (308 * 40 // 100) - 4 * 3
    lift = next(s for s in stage.steps if s.level_up)
    assert (lift.name, lift.exp, lift.crafts) == ("Processed Parchment", 6, -(-(stage.exp_needed) // 6))
    assert lift.crafts == 104


def test_plan_blocks_on_the_character_level_the_table_asks(kb, character):
    """Crossing 5 → 6 asks for character level 30 (the KB's own rule: 5 × the crafting level); a level-26
    character is told so, and the stage stays in the plan."""
    character.level = 26
    p = craftplan.plan(kb, character, [WAND], {})
    stage = next(st for st in p.stages if st.kind == "level" and st.discipline == "Arcforge")
    assert stage.blocked == "char:30" and stage.level_from == 4 and stage.level_to == 6


def test_plan_measured_exp_overrides_the_kbs(kb, character):
    """The player measured 900 EXP for 5 → 6 (the KB says 450): the level-up asks for that much more, and
    exp_needs marks the level as theirs, the others as the KB's."""
    needs = craftplan.exp_needs(kb, {"5": 900})
    assert [(e.level, e.exp, e.measured) for e in needs if e.level == 5] == [(5, 900, True)]
    assert [(e.level, e.exp, e.measured) for e in needs if e.level == 4] == [(4, 308, False)]
    p = craftplan.plan(kb, character, [WAND], {}, exp_overrides={"5": 900})
    stage = next(st for st in p.stages if st.kind == "level" and st.discipline == "Arcforge")
    assert stage.exp_needed == 308 + 900 - (308 * 40 // 100) - 12
    lift = next(s for s in stage.steps if s.level_up)
    assert lift.crafts == -(-stage.exp_needed // 6)


def test_plan_lifts_smithing_before_its_own_material_crafts(kb, character):
    """The Mithril Ingot step needs Smithing 2 and the character is unlearned (level 1): that level-up
    comes before the material stage, and only the crafts already possible count their EXP toward it."""
    p = craftplan.plan(kb, character, [WAND], {})
    kinds = [(st.kind, st.discipline) for st in p.stages]
    assert kinds.index(("level", "Smithing")) < next(i for i, (k, d) in enumerate(kinds) if k == "prep")
    smith = next(st for st in p.stages if st.kind == "level" and st.discipline == "Smithing")
    assert (smith.level_from, smith.level_to) == (1, 2)
    # 50 EXP to level 2, minus the 12 the plan's own Bronze and Iron Ingots give, at 3 EXP a craft; the
    # cheapest recipe per EXP is the Iron Ingot (its ore is free; Bronze Ore costs 50 mesos each)
    assert smith.exp_needed == 50 - 12
    lift = next(s for s in smith.steps if s.level_up)
    assert (lift.name, lift.crafts) == ("Iron Ingot", -(-(50 - 12) // 3))


# ---------------------------------------------------------------- the totals


def test_plan_cost_is_the_sum_of_its_steps(kb, character):
    p = craftplan.plan(kb, character, [WAND], {})
    steps = [s for st in p.stages for s in st.steps]
    assert p.cost == sum(s.cost + s.fee_total for s in steps)
    assert p.crafts == sum(st.crafts for st in p.stages)
    assert p.exp == sum(s.exp_total for s in steps)
    assert p.equip_lv == 30


def test_plan_owns_its_final_craft_and_the_priced_raw(kb, character):
    """The last stage is the wand itself; the one priced raw mat lands in the farm list with its seller,
    the unsold ones in the notes."""
    p = craftplan.plan(kb, character, [WAND], {})
    final = p.stages[-1]
    assert final.kind == "final"
    (step,) = final.steps
    assert (step.name, step.crafts, step.fee_total) == ("Mithril Wand", 1, 5500)
    bronze = next(f for f in p.farm if f.name == "Bronze Ore")
    assert bronze.price == 50 and bronze.sellers and bronze.sellers[0][0] == "Muel the Merchant"
    assert ("no_price", "Mithril Ore") in p.notes


def test_craftable_rows_show_the_class_stat_and_the_saving(kb, character):
    rows = {r.name: r for r in craftplan.craftable(kb, character)}
    wand = rows["Mithril Wand"]
    assert (wand.lv, wand.stat, wand.stat_word) == (30, 55, "M.ATK")
    assert wand.discipline == "Arcforge" and wand.prof_lv == 6
    assert wand.buy == 22000 and wand.cost == 8400 and wand.save == 13600
    # a Magician's default table holds the wands; the parchment is still there for a search
    assert wand.equip and rows["Processed Parchment"].equip is False


def test_craftable_hides_another_class_weapon(kb):
    from maplehelper.store import Character

    rows = {
        r.name: r for r in craftplan.craftable(kb, Character(id="w", name="Bash", base_class="Warrior", job="Fighter", level=30))
    }
    assert "Mithril Wand" not in rows or rows["Mithril Wand"].equip is False


# ---------------------------------------------------------------- the real KB's known chain


@needs_kb
def test_real_kb_mithril_wand_matches_its_own_page():
    """The wand's own page carries its From Scratch numbers (Total Meso 7,900 · Craft Actions 17 · the
    four ores × 30/4/10/10): the expansion from the recipes table must land on exactly those."""
    kb = KnowledgeBase(REAL_KB)
    tree = craftplan.expand(kb, "Mithril Wand")
    raws = {name: qty for name, _, qty, _ in tree.raws}
    assert (tree.cost, tree.crafts) == (7900, 17)
    assert raws == {"Mithril Ore": 30, "Aquamarine Ore": 4, "Bronze Ore": 10, "Iron Ore": 10}
    assert tree.leftovers == (("Screw", 2),)


@needs_kb
def test_real_kb_plan_for_the_sample_character():
    """The acceptance sample: a level-30 Wizard, Arcforge 4 at 40%, some ores in the bag, one wand — the
    farm list, a prep stage, the Arcforge level-up, the final craft, every total the sum of its steps."""
    from maplehelper.store import Character

    kb = KnowledgeBase(REAL_KB)
    c = Character(id="w", name="Wiz", base_class="Magician", job="Wizard", level=30)
    c.crafts = {"arcforge": 4, "tailoring": 4}
    c.craft_pct = {"arcforge": 40}
    bag = {
        "item/415": 22,
        "item/322": 4,
        "item/413": 10,
        "item/414": 12,
        "item/357": 60,
        "item/409": 2,
        "item/321": 3,
        "item/422": 2,
    }
    p = craftplan.plan(kb, c, [WAND], bag)
    steps = [s for st in p.stages for s in st.steps]
    assert p.cost == sum(s.cost + s.fee_total for s in steps)
    assert p.stages[-1].kind == "final" and p.stages[-1].steps[-1].name == "Mithril Wand"
    assert any(st.kind == "level" and st.discipline == "Arcforge" and st.level_to == 6 for st in p.stages)
    assert any(st.kind == "prep" for st in p.stages)
    used = dict(p.bag_used)
    assert used["Mithril Ore"] == 20 and used["Bronze Ore"] == 10  # 2 carried ingots cut the ore need
    farm = {f.name: f for f in p.farm}
    assert farm["Mithril Ore"].farm == 0 and farm["Mithril Ore"].bag == 20  # 2 carried ingots cover the rest
    assert farm["Leather"].farm > 0  # the level-up recipe's own raw mat
    assert all(f.droppers or f.sellers for f in p.farm if f.farm > 0)  # every farmed mat says where it comes from
    rows = {r.name: r for r in craftplan.craftable(kb, c)}
    # what crafting saves is the shop's price less the craft's own cost, whatever the KB's shops say today (a
    # launch-values KB took the wand off the shop it was in: then nothing to compare with)
    r = rows["Mithril Wand"]
    assert r.save == (r.buy - r.cost if r.buy is not None else None)
    assert all(x.save == (x.buy - x.cost if x.buy is not None else None) for x in rows.values())


# ---------------------------------------------------------------- the Crafting page's Craft plan tab


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def profiles(isolated_store, character):
    p = isolated_store.Profiles()
    p.add(character.name, character.base_class, character.job, character.level)
    active = p.active
    active.crafts, active.craft_pct, active.craft_targets = dict(character.crafts), dict(character.craft_pct), [WAND]
    p.save()
    return p


def _pump(qapp, ms=30):
    import time

    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()


@pytest.fixture
def tools(qapp, isolated_store, profiles, kb):
    """open(lang="en") -> the Tools window on its Crafting page, this file's tiny KB, the plan tab picked."""
    from maplehelper.ui.tools import ToolsDialog

    opened = []

    def open_(lang="en"):
        d = ToolsDialog(kb, profiles, isolated_store.Settings(), lang, "", {}, "crafting")
        opened.append(d)
        d.craft_tabs.group.buttons()[1].click()
        return d

    yield open_
    from PySide6.QtCore import QEvent

    for d in opened:                # gone for good: a later test's app-wide restyle never walks these pages
        d.close()
        d.deleteLater()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _texts(d) -> str:
    """Every label in the plan tab, one string, the bidi isolates dropped (asserting on words, not on layout)."""
    from PySide6.QtWidgets import QLabel

    marks = "".join(map(chr, (0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A))))
    text = " ".join(x.text() for x in d.craft_advisor.findChildren(QLabel) if x.isVisibleTo(d.craft_advisor))
    return text.translate({ord(m): None for m in marks})


def test_the_crafting_page_has_two_tabs_the_professions_and_the_plan(tools):
    """The plan is the Crafting page's second tab (as Build & Skills has two), never a window of its own: the first
    tab is the professions page as it was."""
    from PySide6.QtWidgets import QWidget

    d = tools()
    assert d.craft_tabs.value() == "plan" and d.craft_stack.currentIndex() == 1
    assert d.craft_stack.currentWidget().widget().isAncestorOf(d.craft_advisor)
    assert d.craft_advisor.isWindow() is False and isinstance(d.craft_advisor, QWidget)
    d.craft_tabs.group.buttons()[0].click()
    assert d.craft_stack.currentIndex() == 0 and d.craft_stack.currentWidget() is d.craft_scroll
    assert d.craft_scroll.widget().isAncestorOf(d.craft_mode)          # the teacher & quests / recipes tabs


def test_the_plan_shows_what_to_farm_and_craft_for_the_characters_targets(tools):
    d = tools()
    text = _texts(d)
    assert "Mithril Wand" in text and "Forge plan" in text
    assert "8,400 mesos from raw mats" in text           # the wand's own cost from raw mats (the empty-bag table)
    assert "Leather ×312" in text                       # the farm list's raw mat
    assert "Wiz · Wizard" in text


def test_a_crafted_step_moves_the_craft_into_the_bag(qapp, tools, profiles):
    from PySide6.QtWidgets import QPushButton

    d = tools()
    done = [b for b in d.craft_advisor.findChildren(QPushButton) if b.objectName() == "NowChip"]
    assert done
    done[-1].click()                                    # the plan's last step is the Mithril Wand craft
    _pump(qapp)
    assert profiles.active.craft_bag.get(WAND) == 1     # the crafted wand entered the bag …
    plan = d.craft_advisor._plan
    assert plan is None or not [f for f in plan.farm if f.farm > 0]     # … and nothing is left to farm for it


def test_the_make_stepper_is_the_characters_targets(qapp, tools, profiles):
    from maplehelper.ui.controls import Stepper

    d = tools()
    wand = next(s for s in d.craft_advisor.findChildren(Stepper) if s.value() == 1)   # the wand's Make stepper
    wand.setValue(2)
    assert profiles.active.craft_targets == [WAND, WAND]
    wand.setValue(0)
    assert profiles.active.craft_targets == []
    assert "Pick what to craft first." in _texts(d)


def test_the_typed_numbers_are_saved_to_the_character_when_the_page_is_left(qapp, tools, profiles, kb):
    """The profession boxes, a bag count and a measured EXP are saved after the pause, and at once when the page or
    the tab is left (the gear page's rule): never lost to a quick switch. An EXP typed back to the KB's own is the
    KB's again."""
    d = tools()
    adv = d.craft_advisor
    lv, pct = adv._prof_boxes["Arcforge"]
    lv.setText("5")
    pct.setText("70")
    key = next(iter(adv._bag_boxes))
    adv._bag_boxes[key].setText("3")
    d.craft_tabs.group.buttons()[0].click()              # the tab left before the pause ends
    c = profiles.active
    assert c.crafts["arcforge"] == 5 and c.craft_pct["arcforge"] == 70 and c.craft_bag[key] == 3
    d.craft_tabs.group.buttons()[1].click()
    level, box = next(iter(adv._exp_boxes.items()))
    kb_exp = int(box.text())
    box.setText(str(kb_exp + 11))
    adv.leave()
    assert d.settings["craft_exp_table"] == {str(level): kb_exp + 11}
    adv._exp_boxes[level].setText(str(kb_exp))
    adv.leave()
    assert d.settings["craft_exp_table"] is None


def test_the_plan_follows_the_active_character(qapp, tools, profiles):
    d = tools()
    other = profiles.add("Nobody", "Warrior", "Fighter", 20)
    profiles.set_active(other.id)
    d.profile_changed()
    assert "Nobody · Fighter" in _texts(d) and "Pick what to craft first." in _texts(d)
    profiles.set_active(next(c for c in profiles.characters if c.name == "Wiz").id)
    d.profile_changed()
    assert "Mithril Wand" in _texts(d)


def test_the_plan_tab_in_hebrew(qapp, tools):
    d = tools("he")
    text = _texts(d)
    assert "תוכנית ההכנה" in text and "Arcforge" in text          # the game's own English word for the profession
    assert "מסו" not in text                                       # mesos in English letters
    assert [b.text() for b in d.craft_tabs.group.buttons()] == [bidi.plain("מקצועות", True),
                                                                 bidi.plain("תוכנית ייצור", True)]
