"""The scroll planner (maplehelper/scrollplan.py and its window): which scrolls an equip takes — the slot
name mapping between the two tables, the stat grouping, the exact odds of a scroll session (closed forms,
checked against the attempt-by-attempt definition), the prices nothing invents, the plain answer's facts
(summary: never a "best" on a missing price), and the window's own life (its header and pictures, the stat
segments, a card per grade with its outcome chart, a price typed in, both languages, the accent, the ✕, a Free
Market price that never blocks the GUI thread). The real-KB tests cross-check the mapping against the equip
pages' own "Scrolls for <item>" tables."""
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from maplehelper import availability, scrollplan, tables
from maplehelper.kb import KnowledgeBase

ROOT = Path(__file__).resolve().parent.parent
REAL_KB = ROOT / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "index.json").exists(), reason="no real knowledge base")


# ---------------------------------------------------------------- a tiny KB with the shapes that matter

def _scroll_page(name, rate, stats, shop=""):
    return {"name": name, "typ": "Use / Scroll", "props": {}, "body": f"""
Improves a piece of gear.
Success rate: {rate}%, {stats}

{name}
Use · Scroll · No. 0000
{stats.replace(", ", "\n")}
NPC Sell-back (per unit) 1 mesos
Max per Stack 100
Tradeable
{shop}"""}


def _equip_page(name, typ, stats=(), slots=None):
    body = f"\n{name}\nEquip · {typ.split(' / ', 1)[-1]} · No. 0000\nREQ LEV 10\n"
    body += "".join(f"{s}\n" for s in stats)
    body += f"Upgrade Slots {slots}\n" if slots is not None else ""
    body += "NPC Sell-back 1 mesos\nMale + Female Tradeable\n"
    return {"name": name, "typ": typ, "props": {}, "body": body}


TOP_SHOP = """Where to buy
Tulcus Armor Seller
Victoria Road: Sleepy Dungeon · Sleepywood
15,000
mesos
COT2 prices"""

PAGES = {
    # a 7-slot top: the whole HP ladder, and a DEF group whose grades grant different effects
    "item/1000": _equip_page("Silver Master Sergeant", "Equip / Top", ("W.DEF +32",), 7),
    "item/203": _scroll_page("Topwear HP Scroll: Lesser", 100, "Max HP +10", TOP_SHOP),
    "item/205": _scroll_page("Topwear HP Scroll: Intermediate", 60, "Max HP +15"),
    "item/206": _scroll_page("Topwear HP Scroll: Greater", 10, "Max HP +20"),
    "item/207": _scroll_page("Topwear DEF Scroll: Lesser", 100, "Weapon Def. +2", TOP_SHOP),
    "item/208": _scroll_page("Topwear DEF Scroll: Intermediate", 60, "Weapon Def. +4, Magic Def. +2"),
    # a bow (an exact slot-name match) and one whose slot count the KB lacks
    "item/663": _equip_page("War Bow", "Equip / Bow", ("W.ATK +30",), 7),
    "item/900": _equip_page("Ranger Bow", "Equip / Bow", ("W.ATK +12",)),
    "item/210": _scroll_page("Bow Attack Scroll: Lesser", 100, "Weapon Attack +1",
                             TOP_SHOP.replace("Tulcus Armor Seller", "Karl Weapon Seller")
                                     .replace("Sleepy Dungeon · Sleepywood", "Henesys Weapon Store · Henesys")),
    "item/211": _scroll_page("Bow Attack Scroll: Greater", 10, "Weapon Attack +3"),
    # an equip nothing scrolls
    "item/910": _equip_page("Battle Shield", "Equip / Shield", ("W.DEF +8",), 3),
    # the two tables' word for the same slot differs by case, and by a whole word for an overall
    "item/920": _equip_page("Zard", "Equip / Two-Handed Axe", ("W.ATK +45",), 7),
    "item/930": _scroll_page("Two-handed Axe Attack Scroll: Greater", 10, "Weapon Attack +3"),
    "item/940": _equip_page("Green Lua", "Equip / Overall", ("W.DEF +21",), 10),
    "item/950": _scroll_page("Overall Armor DEX Scroll: Lesser", 100, "DEX +1"),
    "npc/5": {"name": "Tulcus", "typ": None, "props": {}, "body": "\nTulcus\n"},
}


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
    """This file's own tiny KB (the shared fixture KB has no equips), its tables built once here."""
    root = tmp_path_factory.mktemp("scrollplan") / "kb"
    kb = make_kb(root)
    assert tables.ensure(kb)
    return root


@pytest.fixture
def kb(_own_kb):
    return KnowledgeBase(_own_kb)


# ---------------------------------------------------------------- the slot mapping

def test_scroll_slot_mapping():
    assert scrollplan.scroll_slot("Top") == "topwear"
    assert scrollplan.scroll_slot("Bottom") == "bottomwear"
    assert scrollplan.scroll_slot("Overall") == "overall armor"
    assert scrollplan.scroll_slot("Two-Handed Axe") == "two handed axe"      # case and hyphens aside
    assert scrollplan.scroll_slot("Two-handed Blunt Weapon") == "two handed blunt weapon"
    assert scrollplan.scroll_slot("Gloves") == "gloves"                      # most slots match word for word
    assert scrollplan.scroll_slot("") == ""


# ---------------------------------------------------------------- scrolls_for

def test_scrolls_for_groups_by_the_kb_own_stat_word(kb):
    es = scrollplan.scrolls_for(kb, "item/1000")
    assert (es.item, es.key, es.slot, es.slots) == ("Silver Master Sergeant", "item/1000", "Top", 7)
    assert [g.stat for g in es.groups] == ["DEF", "HP"]          # the attack-type stats first, then by name
    hp = es.groups[1]
    # the whole ladder as the KB states it: grade, per-slot success, gain, and the Lesser-only NPC price
    assert [(s.grade, s.success, s.gains) for s in hp.grades] == [
        ("Lesser", 100, (("Max HP", 10),)), ("Intermediate", 60, (("Max HP", 15),)), ("Greater", 10, (("Max HP", 20),))]
    lesser = hp.grades[0]
    assert lesser.buy == 15000 and lesser.seller.startswith("Tulcus (Sleepywood")
    assert hp.grades[1].buy is None and hp.grades[1].seller == ""
    # one group even though only the Intermediate grade adds M.DEF: each Scroll keeps its own gains
    deff = es.groups[0]
    assert [(s.grade, s.gains) for s in deff.grades] == [
        ("Lesser", (("Weapon Def.", 2),)), ("Intermediate", (("Weapon Def.", 4), ("Magic Def.", 2)))]
    assert deff.primary == "Weapon Def."


def test_scrolls_for_the_slots_the_tables_name_differently(kb):
    assert scrollplan.scrolls_for(kb, "item/920").groups[0].stat == "Attack"       # Two-Handed / Two-handed
    assert scrollplan.scrolls_for(kb, "item/940").groups[0].stat == "DEX"          # Overall / Overall Armor
    assert scrollplan.scrolls_for(kb, "item/663").groups[0].stat == "Attack"       # word for word


def test_scrolls_for_equips_without_scrolls_and_keys_that_are_not_equips(kb):
    shield = scrollplan.scrolls_for(kb, "item/910")
    assert shield is not None and shield.groups == ()            # an equip, just nothing scrolls it
    assert scrollplan.scrolls_for(kb, "item/203") is None        # a scroll is Use, not an equip
    assert scrollplan.scrolls_for(kb, "npc/5") is None
    assert scrollplan.scrolls_for(kb, "item/nope") is None
    # a slot count the KB lacks still plans (the window says so instead of guessing one)
    assert scrollplan.scrolls_for(kb, "item/900").slots is None
    assert scrollplan.scrolls_for(kb, "item/900").groups


# ---------------------------------------------------------------- the exact odds

def test_plan_is_the_binomial_when_nothing_destroys():
    pl = scrollplan.plan(7, 0.6)
    assert sum(pl.dist) == pytest.approx(1.0)
    assert pl.destroyed == 0.0
    assert pl.dist == pytest.approx(tuple(math.comb(7, k) * 0.6 ** k * 0.4 ** (7 - k) for k in range(8)))
    assert pl.all_pass == pytest.approx(0.6 ** 7)
    assert pl.at_least(7) == pytest.approx(0.6 ** 7)
    assert pl.at_least(0) == pytest.approx(1.0)
    assert pl.at_least(5) == pytest.approx(sum(math.comb(7, k) * 0.6 ** k * 0.4 ** (7 - k) for k in (5, 6, 7)))
    assert pl.expected_successes == pytest.approx(4.2)
    assert pl.expected_attempts == 7


def test_plan_boundaries():
    assert scrollplan.plan(0, 0.6).dist == (1.0,)                    # no slots used: nothing to roll
    sure = scrollplan.plan(7, 1.0)
    assert sure.dist[-1] == 1.0 and sum(sure.dist[:-1]) == pytest.approx(0.0)
    assert sure.expected_attempts == 7 and sure.expected_successes == 7
    never = scrollplan.plan(7, 0.0)
    assert never.dist[0] == 1.0 and never.expected_successes == 0.0
    assert scrollplan.plan(2, 0.5, 5.0) == scrollplan.plan(2, 0.5, 1.0)   # d is clamped


def _markov(n, p, d):
    """The same session worked out attempt by attempt (its definition, no shortcuts): alive[k] is P(alive
    with k successes so far). A fail destroys with d and ends the run."""
    alive = [1.0] + [0.0] * n
    destroyed = 0.0
    attempts = 0.0
    for _ in range(n):
        nxt = [0.0] * (n + 1)
        for k, mass in enumerate(alive):
            if not mass:
                continue
            attempts += mass
            nxt[k + 1] += mass * p
            nxt[k] += mass * (1 - p) * (1 - d)
            destroyed += mass * (1 - p) * d
        alive = nxt
    return tuple(alive), destroyed, attempts


def test_plan_with_destruction_matches_the_attempt_sequence():
    n, p, d = 7, 0.1, 0.5
    pl = scrollplan.plan(n, p, d)
    dist, destroyed, attempts = _markov(n, p, d)
    assert pl.dist == pytest.approx(dist)
    assert pl.destroyed == pytest.approx(destroyed)
    assert pl.destroyed == pytest.approx(1 - 0.55 ** n)            # the closed form: 1 - (1 - 0.9 x 0.5) ** 7
    assert pl.expected_attempts == pytest.approx(attempts)
    assert pl.expected_successes == pytest.approx(p * attempts)
    assert sum(pl.dist) + pl.destroyed == pytest.approx(1.0)
    assert pl.at_least(1) == pytest.approx(sum(dist[1:]))
    # with nothing destroying, the general form is the binomial again
    assert scrollplan.plan(5, 0.6, 0.0).dist == pytest.approx(scrollplan.plan(5, 0.6).dist)


# ---------------------------------------------------------------- the plan over the KB's own scrolls

def test_plans_the_acceptance_numbers(kb):
    """A 7-slot top, HP scrolls: Lesser is a sure +70, Intermediate's all-7 is 0.6 ** 7 and its expected +63,
    Greater's expected +14 — every number read out of the KB rows these plans were built from."""
    hp = next(g for g in scrollplan.scrolls_for(kb, "item/1000").groups if g.stat == "HP")
    prices = {s.key: s.buy for s in hp.grades if s.buy is not None}
    lesser, inter, greater = scrollplan.plans(hp, 7, prices)
    assert inter.plan.all_pass == pytest.approx(0.6 ** 7) and inter.plan.all_pass < 0.028
    assert inter.expected == (("Max HP", 15 * 0.6 * 7),)
    assert greater.expected[0][0] == "Max HP" and greater.expected[0][1] == pytest.approx(20 * 0.1 * 7)
    # the Lesser NPC price is the KB's own: 7 x 15,000, and 1,500 per expected +1 HP
    assert lesser.price == 15000 and lesser.expected_cost == 105000.0 and lesser.per_point == 1500.0


def test_plans_without_a_price_show_odds_and_no_cost(kb):
    hp = next(g for g in scrollplan.scrolls_for(kb, "item/1000").groups if g.stat == "HP")
    inter = scrollplan.plans(hp, 7)[1]
    assert inter.price is None and inter.expected_cost is None and inter.per_point is None
    assert inter.plan.all_pass == pytest.approx(0.6 ** 7)          # the odds never wait for a price
    priced = scrollplan.plans(hp, 7, {inter.scroll.key: 20000})
    assert priced[1].price == 20000 and priced[1].expected_cost == 140000.0
    assert priced[1].per_point == pytest.approx(140000 / 63)


def test_plans_costs_stop_when_the_item_can_break(kb):
    """A destroying scroll stops the run mid-way: the expected attempts (and so the expected cost) are the
    closed form's, not n — what the KB's scrolls never do, kept honest for the day one does."""
    hp = next(g for g in scrollplan.scrolls_for(kb, "item/1000").groups if g.stat == "HP")
    s = hp.grades[1]
    pl = scrollplan.plan(7, s.success / 100, 0.5)
    assert pl.expected_attempts == pytest.approx((1 - 0.8 ** 7) / 0.2)   # go = 1 - 0.4 x 0.5 = 0.8
    # the cost of such a run is a GradePlan's business: price x those expected attempts
    gp = scrollplan.GradePlan(scroll=s, plan=pl, expected=(), price=100, expected_cost=100 * pl.expected_attempts,
                              per_point=None)
    assert gp.expected_cost == pytest.approx(100 * (1 - 0.8 ** 7) / 0.2)


# ---------------------------------------------------------------- the plain answer's facts

def _hp(kb):
    return next(g for g in scrollplan.scrolls_for(kb, "item/1000").groups if g.stat == "HP")


def _attack(*grades):
    """A stat group of these (grade, success %, gain) rows, as the bows' Weapon Attack scrolls are."""
    return scrollplan.StatGroup(stat="Attack", grades=tuple(
        scrollplan.Scroll(key=f"item/{900 + i}", scroll=f"Bow Attack Scroll: {g}", grade=g, success=s,
                          gains=(("Weapon Attack", gain),))
        for i, (g, s, gain) in enumerate(grades)))


def test_summary_with_only_the_npc_price_ranks_no_cost(kb):
    """Only Lesser has a price (the KB's NPC one): it leads as the sure grade, the riskier grades are compared on
    what they add, and no cheapest is named — the unpriced grades are said to be unpriced, not ranked."""
    hp = _hp(kb)
    got = scrollplan.plans(hp, 7, {s.key: s.buy for s in hp.grades if s.buy is not None})
    sm = scrollplan.summary(got)
    assert (sm.n, sm.effect, sm.lead.scroll.grade, sm.sure) == (7, "Max HP", "Lesser", True)
    assert [gp.scroll.grade for gp in sm.others] == ["Intermediate", "Greater"]
    assert sm.more == ()                                   # +63 and +14 against Lesser's sure +70
    assert sm.cheapest is None and not sm.best
    assert [gp.scroll.grade for gp in sm.unpriced] == ["Intermediate", "Greater"]
    assert scrollplan.gain(sm.lead, "Max HP") == 70 and sm.lead.expected_cost == 105000


def test_summary_with_every_price_names_the_cheapest(kb):
    hp = _hp(kb)
    lesser, inter, greater = (s.key for s in hp.grades)
    sm = scrollplan.summary(scrollplan.plans(hp, 7, {lesser: 15000, inter: 20000, greater: 5000}))
    # per +1 HP: Lesser 105,000 / 70 = 1,500; Intermediate 140,000 / 63 = 2,222; Greater 35,000 / 14 = 2,500
    assert sm.cheapest is sm.lead and sm.unpriced == () and sm.best
    # a riskier grade cheaper per +1 is named, and the sure grade is no longer "the best"
    sm = scrollplan.summary(scrollplan.plans(hp, 7, {lesser: 15000, inter: 10000, greater: 5000}))
    assert sm.cheapest.scroll.grade == "Intermediate" and sm.cheapest.per_point == pytest.approx(70000 / 63)
    assert sm.lead.scroll.grade == "Lesser" and not sm.best


def test_summary_without_prices_still_answers_on_the_odds(kb):
    sm = scrollplan.summary(scrollplan.plans(_hp(kb), 7))
    assert sm.lead.scroll.grade == "Lesser" and sm.sure          # the odds never wait for a price
    assert sm.cheapest is None and not sm.best
    assert [gp.scroll.grade for gp in sm.unpriced] == ["Lesser", "Intermediate", "Greater"]


def test_summary_the_sure_grade_leads_even_when_a_riskier_one_averages_more():
    """A 100% grade is the answer's lead — what it adds is certain — and a riskier grade expected to add more is
    named as such (Weapon Attack: Lesser's sure +7 against Intermediate's +8.4 on average). With no sure grade the
    lead is the one expected to add the most."""
    got = scrollplan.plans(_attack(("Lesser", 100, 1), ("Intermediate", 60, 2), ("Greater", 10, 3)), 7,
                           {"item/900": 25000, "item/901": 1000, "item/902": 1000})
    sm = scrollplan.summary(got)
    assert sm.lead.scroll.grade == "Lesser" and sm.sure and sm.lead.plan.all_pass == 1.0
    assert [gp.scroll.grade for gp in sm.more] == ["Intermediate"]
    assert scrollplan.gain(sm.more[0], "Weapon Attack") == pytest.approx(8.4)
    assert sm.cheapest.scroll.grade == "Intermediate" and not sm.best
    risky = scrollplan.summary(scrollplan.plans(_attack(("Intermediate", 60, 2), ("Greater", 10, 3)), 7))
    assert risky.lead.scroll.grade == "Intermediate" and not risky.sure and risky.more == ()


def test_summary_with_no_slots_has_nothing_to_roll(kb):
    sm = scrollplan.summary(scrollplan.plans(_hp(kb), 0))
    assert (sm.n, sm.lead, sm.sure, sm.others, sm.cheapest, sm.unpriced, sm.best) == (0, None, False, (), None, (), False)
    assert scrollplan.summary(()).lead is None


# ---------------------------------------------------------------- the equip search

def test_find_equips(kb):
    assert [e.item for e in scrollplan.find_equips(kb, "silver master")] == ["Silver Master Sergeant"]
    assert [e.item for e in scrollplan.find_equips(kb, "bow")] == ["Ranger Bow", "War Bow"]
    assert scrollplan.find_equips(kb, "bow sergeant") == []        # a query's words must all be in one name
    assert scrollplan.find_equips(kb, "w") == []                  # still being typed
    assert scrollplan.find_equips(kb, "zzz") == []
    assert [e.item for e in scrollplan.find_equips(kb, "zard")] == ["Zard"]


# ---------------------------------------------------------------- the real KB

def _page_scrolls_section(kb, key):
    """An equip page's own 'Scrolls for <item>' table: {stat: {grade: the numbers its cell states}} for the
    three in-game grades (the Chaos column aside — the page lists scrolls the game doesn't confirm)."""
    rows, on = {}, False
    for ln in (s.strip() for s in kb.page(key).splitlines()):
        if ln.startswith("Scrolls for "):
            on = True
        elif on and (ln.startswith(("A failed Chaos", "Simulator runs", "Change history")) or not ln):
            if rows:
                break
        elif on and " | " in ln and not ln.startswith("Stat "):
            cells = ln.split(" | ")
            if len(cells) == 5:
                rows[cells[0]] = {"Lesser": cells[1], "Intermediate": cells[2], "Greater": cells[3]}
    return rows


def _page_key(stat: str, page: dict) -> str | None:
    """The page's own word for a scroll's stat: the same word, its prefix, or its synonym (the pages write
    'Avoidability' for Evasion scrolls, 'Crit' for Crit. Damage, 'Magic Def' for 'Magic Def.')."""
    want = stat.rstrip(".").lower()
    if want in page:
        return want
    if want == "evasion" and "avoidability" in page:
        return "avoidability"
    return next((k for k in page if want.startswith(k.lower())), None)


@needs_kb
def test_real_kb_every_scrolled_slot_finds_the_pages_scrolls():
    """Every equip slot the scrolls table covers maps to it (no slot missed, none cross-wired), and the KB's
    own equip pages agree: each group's stat and each grade's gains are what its page's table states."""
    real = KnowledgeBase(REAL_KB)
    table = tables.rows(real, "scrolls")
    slots = {r["slot"] for r in table}
    equips = tables.rows(real, "equips")
    covered = {scrollplan.scroll_slot(r["slot"]) for r in equips} & {scrollplan._canon(s) for s in slots}
    assert covered == {scrollplan._canon(s) for s in slots} - {"pet equip"}   # pet equips are no one's slot
    by_slot: dict[str, list[dict]] = {}
    for r in equips:
        by_slot.setdefault(r["slot"], []).append(r)
    for slot, rows in sorted(by_slot.items()):
        groups = scrollplan.scrolls_for(real, rows[0]["key"]).groups
        assert groups, f"{slot} has scrolls in the table but scrolls_for finds none"
        checked = 0
        for r in rows[:3]:                     # the slot's first pages: enough to see a cross-wired mapping
            page = _page_scrolls_section(real, r["key"])
            if not page:                       # one odd page (Old Gladius) carries no Scrolls-for table at all
                continue
            checked += 1
            low = {k.rstrip(".").lower(): v for k, v in page.items()}   # 'Magic Def' ~ 'Magic Def.' etc
            for g in groups:
                key = _page_key(g.stat, low)
                assert key, f"{slot}: the table's {g.stat} scrolls missing from {r['item']}'s page"
                cells = low[key]
                for s in g.grades:
                    stated = {int(x) for x in re.findall(r"\d+", cells[s.grade])}
                    assert stated == {gain for _, gain in s.gains}, \
                        f"{r['item']} {g.stat} {s.grade}: page says {cells[s.grade]}, table {s.gains}"
        assert checked, f"{slot}: none of its sampled pages carries a Scrolls-for table to check against"


@needs_kb
def test_real_kb_a_seven_slot_top_and_its_hp_scrolls():
    real = KnowledgeBase(REAL_KB)
    top = next(r for r in tables.rows(real, "equips") if r["slot"] == "Top" and r["slots"] == 7)
    es = scrollplan.scrolls_for(real, top["key"])
    assert es.slots == 7
    hp = next(g for g in es.groups if g.stat == "HP")
    lesser = hp.grades[0]
    assert (lesser.grade, lesser.success, lesser.gains) == ("Lesser", 100, (("Max HP", 10),))
    got = scrollplan.plans(hp, 7)[0]
    assert got.plan.all_pass == 1.0 and got.expected == (("Max HP", 70.0),)


@needs_kb
def test_real_kb_chaos_scrolls_are_not_confirmed_in_the_game():
    """The scrolls table carries no Chaos row because the KB confirms no source of any Chaos scroll (they are
    pages only). The planner follows the KB: it offers none of them, and says so in its window."""
    real = KnowledgeBase(REAL_KB)
    rows = tables.rows(real, "scrolls")
    assert rows and not any("Chaos" in r["scroll"] for r in rows)
    open_ = availability.of(real)
    chaos = [k for k, e in real.entities.items() if str(e.get("type") or "") == "Use / Scroll"
             and "Chaos" in str(e.get("name") or "")]
    assert chaos and not any(open_.item_open(k) for k in chaos)


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


def pump(qapp, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


def delete_window(qapp, w):
    from PySide6.QtCore import QEvent
    w.hide()
    w.close()
    for timer in (w._typing, w._geom):
        timer.stop()
    w.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def no_fm_price(monkeypatch):
    """MeowDB answers, with no Free Market price for any scroll: the grades without an NPC price have none."""
    monkeypatch.setattr("maplehelper.market.item_market", lambda item_id, timeout=8: SimpleNamespace(usual=None))


@pytest.fixture
def win(qapp, settings, profiles, kb, no_fm_price):
    from maplehelper.i18n import I18n
    from maplehelper.ui.scrollplanner import ScrollPlanner
    w = ScrollPlanner(kb, settings, profiles, I18n("en"))
    yield w
    delete_window(qapp, w)


_MARKS = dict.fromkeys(map(ord, "\u2066\u2067\u2069\u200e\u200f\u202a\u202c\u2060"))


def plain(text: str) -> str:
    """A label's words as read: the bidi marks, the rich text's tags and the no-break spaces out."""
    return re.sub(r"<[^>]+>", "", text).replace("&nbsp;", " ").translate(_MARKS).replace("\xa0", " ")


def shown_text(w) -> str:
    from PySide6.QtWidgets import QAbstractButton, QLabel
    shown = [x for kind in (QLabel, QAbstractButton) for x in w.findChildren(kind) if x.isVisibleTo(w)]
    return " ".join(plain(x.text()) for x in shown)


def pick_stat(w, name: str) -> None:
    """Click the stat segment of this name."""
    for b in w._picker.buttons:
        if plain(b.text()) == name:
            b.click()
            return
    raise AssertionError(f"no stat {name} in {[plain(b.text()) for b in w._picker.buttons]}")


def card(w, grade: str):
    return next(c for c in w._cards if c.scroll.grade == grade)


def settle(qapp, w, timeout: float = 5.0) -> None:
    """Until every grade's Free Market lookup (on its own thread) has answered."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and any(w._price_kind(c.scroll) == "loading" for c in w._cards):
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def type_price(qapp, c, text: str) -> None:
    """A grade's price typed the player's way: its button opens the box, the keys, Enter."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    (c.set_btn if c.set_btn.isVisible() else c.price_btn).click()
    assert c.edit.isVisible()
    QTest.keyClicks(c.edit, text)
    QTest.keyClick(c.edit, Qt.Key_Return)
    qapp.processEvents()


def test_window_header_shows_the_picture_level_and_slots(qapp, settings, win):
    """The picked equip on top: its picture, its name, its slot word and level, its clean slots big on the end; a
    picture on every search result too. A key that isn't an equip only says so."""
    from maplehelper.ui.scrollplanner import ITEM_PIC
    settings["scroll_planner"] = True
    win.refresh()
    assert not win._item.isVisibleTo(win) and "Pick a piece of gear" in win._hint.text()
    win.open_item("npc/5")
    assert win._status.isVisibleTo(win) and "isn't a piece of gear" in win._status.text()
    win.open_item("item/1000")
    head = win._item
    assert head.isVisibleTo(win) and not head.pic.pixmap().isNull()
    assert head.pic.width() == head.pic.height() == ITEM_PIC
    assert plain(head.name.text()) == "Silver Master Sergeant"
    assert plain(head.sub.text()) == "Top · Lv. 10"
    assert (plain(head.slots_num.text()), plain(head.slots_word.text())) == ("7", "upgrade slots")
    assert win._spin.value() == 7 and plain(win._of.text()) == "of 7"
    win._box.setText("bow")
    win._run()
    rows = win._rows
    assert [plain(r.name.text()) for r in rows] == ["Ranger Bow", "War Bow"]
    assert all(not r.pic.pixmap().isNull() for r in rows)
    assert plain(rows[1].sub.text()) == "Bow · Lv. 10 · 7 upgrade slots"
    assert plain(rows[0].sub.text()) == "Bow · Lv. 10 · No upgrade-slot count in the database"
    rows[1].clicked.emit()
    assert plain(win._item.name.text()) == "War Bow" and not win._rows and not win._box.text()


def test_window_stats_are_segments_with_each_grades_gain_on_hover(qapp, win):
    win.open_item("item/1000")
    segs = win._picker.buttons
    assert [plain(b.text()) for b in segs] == ["DEF", "HP"] and segs[0].isChecked()
    tip = plain(segs[1].toolTip())
    assert "Lesser +10 Max HP" in tip and "Intermediate +15 Max HP" in tip and "Greater +20 Max HP" in tip
    pick_stat(win, "HP")
    assert win._picker.buttons[1].isChecked()
    assert [c.scroll.grade for c in win._cards] == ["Lesser", "Intermediate", "Greater"]
    assert plain(card(win, "Lesser").each.text()) == "+10 Max HP per scroll"


def test_window_grade_cards_show_the_expected_gain_and_the_chance_all_pass(qapp, win):
    win.open_item("item/1000")
    # DEF: Intermediate adds M.DEF too, said under its expected W.DEF
    inter = card(win, "Intermediate")
    assert plain(inter.expected.text()) == "+16.8 Weapon Def."
    assert plain(inter.expected_word.text()) == "expected, plus +8.4 Magic Def."
    pick_stat(win, "HP")
    got = {c.scroll.grade: (plain(c.expected.text()), plain(c.expected_word.text()), plain(c.odds.text()),
                            plain(c.odds_word.text())) for c in win._cards}
    assert got == {"Lesser": ("+70 Max HP", "guaranteed", "100%", "all 7 pass"),
                   "Intermediate": ("+63 Max HP", "expected", "2.8%", "all 7 pass"),
                   "Greater": ("+14 Max HP", "expected", "<0.1%", "all 7 pass")}
    badges = {c.scroll.grade: (plain(c.badge.text()), c.badge.property("risk")) for c in win._cards}
    assert badges == {"Lesser": ("100%", "sure"), "Intermediate": ("60%", "risky"), "Greater": ("10%", "long")}
    win._spin.setValue(3)                                          # fewer slots: every card follows
    inter = card(win, "Intermediate")
    assert (plain(inter.expected.text()), plain(inter.odds.text()), plain(inter.odds_word.text())) == (
        "+27 Max HP", "21.6%", "all 3 pass")
    assert plain(card(win, "Lesser").odds_word.text()) == "all 3 pass"


def test_window_chart_has_a_bar_per_total_that_can_happen(qapp, win):
    """One bar per final total with a chance above 0 — a sure scroll's one, a 60% scroll's eight — each as tall as
    its chance against the likeliest, a near-impossible total still a sliver, the exact chance on hover."""
    win.open_item("item/1000")
    pick_stat(win, "HP")
    pump(qapp, 30)                                                 # the cards laid out: the charts have a width
    lesser = card(win, "Lesser").chart
    assert [(b.label, b.chance) for b in lesser.bars] == [("+70", 1.0)]     # +0 … +60 can't happen: no bars
    chart = card(win, "Intermediate").chart
    assert [b.label for b in chart.bars] == [f"+{15 * k}" for k in range(8)]
    assert [b.chance for b in chart.bars] == pytest.approx(
        [math.comb(7, k) * 0.6 ** k * 0.4 ** (7 - k) for k in range(8)])
    peak = chart.peak()
    assert chart.bars[peak].label == "+60"                         # 4 of the 7: 29.03%
    assert plain(chart.bars[peak].tip) == "+60 Max HP in total: 29.03%\n+60 or more: 71.02%"
    assert chart.bar_rect(peak).height() == chart.BARS
    for i, b in enumerate(chart.bars):
        assert chart.bar_rect(i).height() == pytest.approx(
            max(chart.MIN_BAR, b.chance / chart.bars[peak].chance * chart.BARS))
    greater = card(win, "Greater").chart
    assert len(greater.bars) == 8 and greater.bars[-1].chance == pytest.approx(0.1 ** 7)
    assert greater.bar_rect(7).height() == greater.MIN_BAR         # 0.00001%: still drawn
    assert plain(greater.bars[-1].tip) == "+140 Max HP in total: 0.00001%"
    assert chart.width() > 0 and chart.bar_rect(0).center().x() < chart.bar_rect(7).center().x()   # +0 on the left


def test_window_set_price_opens_a_box_and_the_price_applies(qapp, win):
    """No price yet: a Set price button; clicked, a price box; typed, the card's cost and the answer follow. Esc in
    the box puts back what was there."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    win.open_item("item/1000")
    pick_stat(win, "HP")
    settle(qapp, win)
    lesser, inter = card(win, "Lesser"), card(win, "Intermediate")
    assert (plain(lesser.tag.text()), plain(lesser.price_btn.text())) == ("NPC · Tulcus", "15,000 each")
    assert plain(lesser.total.text()) == "105,000 mesos" and plain(lesser.per_point.text()) == "1,500 per +1"
    assert plain(inter.tag.text()) == "No price yet" and inter.set_btn.isVisibleTo(win)
    assert not inter.edit.isVisibleTo(win) and not inter.total.isVisibleTo(win)
    inter.set_btn.click()
    assert inter.edit.isVisibleTo(win) and not inter.set_btn.isVisibleTo(win)
    QTest.keyClicks(inter.edit, "20000")
    QTest.keyClick(inter.edit, Qt.Key_Return)
    assert not inter.edit.isVisibleTo(win) and inter.price_btn.isVisibleTo(win)
    assert plain(inter.tag.text()) == "Your price" and inter.tag.property("kind") == "yours"
    assert plain(inter.price_btn.text()) == "20,000 each"
    assert plain(inter.total.text()) == "140,000 mesos"
    assert plain(inter.per_point.text()) == "2,222 per +1"         # 140,000 mesos over an expected +63 HP
    assert plain(win._answer.note.text()) == "No price yet for Greater, so the cheapest per +1 Max HP isn't known."
    inter.price_btn.click()                                        # Esc: the price it had, the box closed
    QTest.keyClicks(inter.edit, "999")
    assert plain(inter.total.text()) == "6,993 mesos"
    QTest.keyClick(inter.edit, Qt.Key_Escape)
    assert not inter.edit.isVisibleTo(win) and plain(inter.total.text()) == "140,000 mesos"
    type_price(qapp, card(win, "Greater"), "5000")               # every grade priced: the cheapest is named
    assert plain(win._answer.title.text()) == "Lesser is the best pick"
    assert plain(win._answer.note.text()) == "Cheapest per +1 Max HP: Lesser, 1,500 mesos."


def test_window_answers_first_in_plain_words(qapp, win):
    """The answer card over the cards, from scrollplan.summary: the sure grade as the safe pick with its cost, each
    riskier grade's average and its all-pass odds, and what the missing prices leave unknown — never "best"."""
    win.open_item("item/1000")
    pick_stat(win, "HP")
    assert "Checking the Free Market" in plain(win._answer.note.text())    # (before the lookups answer)
    settle(qapp, win)
    a = win._answer
    assert plain(a.title.text()) == "Lesser is the safe pick"
    assert [plain(x.text()) for x in a.lines] == [
        "+70 Max HP guaranteed for 105,000 mesos.",
        "Intermediate averages +63, all 7 pass only 2.8% of the time.",
        "Greater averages +14, all 7 pass only <0.1% of the time."]
    assert plain(a.note.text()) == "No price yet for Intermediate and Greater, so the cheapest per +1 Max HP isn't known."
    assert "best" not in shown_text(a)
    win.open_item("item/663")                                      # the bow: its NPC Lesser and a 10% Greater
    settle(qapp, win)
    assert plain(a.title.text()) == "Lesser is the safe pick"
    assert [plain(x.text()) for x in a.lines] == [
        "+7 Weapon Attack guaranteed for 105,000 mesos.",
        "Greater averages +2.1, all 7 pass only <0.1% of the time."]


def test_window_notes_fold_under_about_these_numbers(qapp, win):
    win.open_item("item/1000")
    about = win._about
    assert about.isVisibleTo(win) and not about.body.isVisibleTo(win)
    about.head.clicked.emit()
    assert about.body.isVisibleTo(win)
    assert "Chaos scrolls aren't offered" in plain(about.body.text()) and "NiaMeowDB" in about.body.text()


def test_window_hebrew_rebuilds_everything_right_to_left(qapp, win):
    """A language switch draws the plan again as it stood — the stat, the slots and a typed price kept — every word
    Hebrew but the game's own names, every Latin run one left-to-right block, the chart's totals from the right."""
    from PySide6.QtCore import Qt

    from maplehelper.i18n import STRINGS, I18n
    win.open_item("item/1000")
    pick_stat(win, "HP")
    settle(qapp, win)
    win._spin.setValue(6)
    type_price(qapp, card(win, "Intermediate"), "20000")
    win.apply_language(I18n("he"))
    pump(qapp, 30)
    assert win.layoutDirection() == Qt.RightToLeft
    assert plain(win._title.text()) == STRINGS["sp_title"]["he"]
    assert plain(win._item.sub.text()) == "חולצה · רמה 10"
    assert win._picker.buttons[1].isChecked() and win._spin.value() == 6
    inter = card(win, "Intermediate")
    assert plain(inter.total.text()) == "120,000 mesos" and plain(inter.tag.text()) == "המחיר שלכם"
    assert plain(win._answer.title.text()) == "Lesser היא הבחירה הבטוחה"
    first, *_middle, last = win._answer.lines
    assert "\u2066+60\xa0Max\xa0HP\u2069" in first.text() and "\u206690,000\xa0mesos\u2069" in first.text()
    assert "\u2066<0.1%\u2069" in last.text()
    assert "\u2066+15\xa0Max\xa0HP\u2069" in inter.each.text()
    chart = inter.chart
    assert chart.width() > 0 and chart.bar_rect(0).center().x() > chart.bar_rect(len(chart.bars) - 1).center().x()
    text = shown_text(win)
    assert "Lv" not in text and "רמה 10" in text
    assert "Max HP" in text and "Intermediate" in text             # the game's own words stay English


def test_window_follows_the_accent(qapp, settings, win):
    """The lit stat, the answer card and the charts' likeliest bars in the accent the ⚙ panel picks; a refresh with
    the same accent doesn't restyle."""
    from PySide6.QtGui import QColor

    from maplehelper.ui import npcoverlay
    settings["scroll_planner"] = True
    settings["overlay_accent"] = "green"
    win.refresh()
    win.open_item("item/1000")
    green, blue = npcoverlay.ACCENTS["green"][1], npcoverlay.ACCENTS["blue"][1]
    assert green in win.styleSheet()
    before = win.styleSheet()
    win.refresh()
    assert win.styleSheet() == before
    settings["overlay_accent"] = "blue"
    win.refresh()
    assert blue in win.styleSheet() and green not in win.styleSheet()
    assert win._cards and all(c.chart._accent == QColor(blue) for c in win._cards)
    pick_stat(win, "HP")                                           # new cards take it too
    assert all(c.chart._accent == QColor(blue) for c in win._cards)


def test_window_x_turns_the_setting_off(qapp, settings, win):
    settings["scroll_planner"] = True
    win.refresh()
    win.open_item("item/1000")
    closed = []
    win.closed.connect(lambda: closed.append(True))
    win._close.click()
    qapp.processEvents()
    assert settings["scroll_planner"] is False and not win.isVisible() and closed == [True]


def test_window_free_market_price_off_the_gui_thread(qapp, settings, win, monkeypatch):
    threads, answered = [], threading.Event()

    def fake(item_id, timeout=8):
        threads.append(threading.current_thread())
        answered.set()
        return SimpleNamespace(usual=12345)

    monkeypatch.setattr("maplehelper.market.item_market", fake)
    win.open_item("item/1000")
    pick_stat(win, "HP")      # Intermediate and Greater have no NPC price of their own
    assert plain(card(win, "Lesser").price_btn.text()) == "15,000 each"   # the KB's price never waits on the network
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not answered.is_set():
        qapp.processEvents()
        time.sleep(0.01)
    settle(qapp, win)                                              # the answers cross to the GUI thread
    assert answered.is_set() and threads and all(t is not threading.main_thread() for t in threads)
    for grade in ("Intermediate", "Greater"):
        c = card(win, grade)
        assert (plain(c.tag.text()), plain(c.price_btn.text())) == ("Free Market usual", "12,345 each")
    assert plain(card(win, "Intermediate").total.text()) == "86,415 mesos"


def test_window_defaults_to_what_the_character_wears(qapp, settings, profiles, win):
    c = profiles.add("Test", "Warrior", "Fighter", 30)
    c.gear = {"Top": "item/1000"}
    profiles.save()
    settings["scroll_planner"] = True
    win.refresh()
    assert plain(win._item.name.text()) == "Silver Master Sergeant"  # the character's top, until they pick
    win.open_item("item/663")
    win.character_changed()
    win.refresh()
    assert plain(win._item.name.text()) == "War Bow"                 # the player's own pick always wins


def test_window_made_for_plan_scrolls_keeps_that_item(qapp, settings, profiles, kb, no_fm_price):
    """The gear advisor's "Plan scrolls" makes the window and opens it on an item at once: the next refresh (a look
    change, the bar) keeps that item, not the character's worn top it hadn't read yet."""
    from maplehelper.i18n import I18n
    from maplehelper.ui.scrollplanner import ScrollPlanner
    c = profiles.add("Test", "Warrior", "Fighter", 30)
    c.gear = {"Top": "item/1000"}
    profiles.save()
    w = ScrollPlanner(kb, settings, profiles, I18n("en"))
    try:
        w.open_item("item/663")
        w.refresh()
        assert plain(w._item.name.text()) == "War Bow"
    finally:
        delete_window(qapp, w)


@needs_kb
def test_real_kb_window_shows_each_things_own_picture(qapp, settings, profiles, no_fm_price):
    """A 7-slot hat in the real KB: the header shows its own sprite (large on hover), each grade card its scroll's
    picture."""
    from maplehelper.i18n import I18n
    from maplehelper.ui.scrollplanner import ScrollPlanner
    real = KnowledgeBase(REAL_KB)
    hat = next(r for r in tables.rows(real, "equips")
               if r["slot"] == "Hat" and r["slots"] == 7 and real.image_path(r["key"])
               and scrollplan.scrolls_for(real, r["key"]).groups)
    w = ScrollPlanner(real, settings, profiles, I18n("en"))
    try:
        w.open_item(hat["key"])
        assert real.image_path(hat["key"]).name in w._item.pic.toolTip()
        assert w._cards and all(real.picture(c.scroll.key).name in c.pic.toolTip() for c in w._cards)
    finally:
        delete_window(qapp, w)
