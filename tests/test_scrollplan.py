"""The scroll planner (maplehelper/scrollplan.py and its window): which scrolls an equip takes — the slot
name mapping between the two tables, the stat grouping, the exact odds of a scroll session (closed forms,
checked against the attempt-by-attempt definition), the prices nothing invents, and the window's own life
(shown from its setting, rows in both languages, the ✕, a Free Market price that never blocks the GUI thread).
The real-KB tests cross-check the mapping against the equip pages' own "Scrolls for <item>" tables."""
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
def win(qapp, settings, profiles, kb):
    from maplehelper.i18n import I18n
    from maplehelper.ui.scrollplanner import ScrollPlanner
    w = ScrollPlanner(kb, settings, profiles, I18n("en"))
    yield w
    delete_window(qapp, w)


def _pick_stat(w, name: str) -> None:
    """Click the stat pill of this name (its bidi marks aside)."""
    for b in w._stats:
        if "".join(ch for ch in b.text() if ord(ch) < 0x2000) == name:
            b.click()
            return
    raise AssertionError(f"no stat pill {name} in {[b.text() for b in w._stats]}")


def _lines(lay) -> str:
    """A card text's lines as one string (the card keeps one single-line label per measured line)."""
    return " ".join(lay.itemAt(i).widget().text() for i in range(lay.count()) if lay.itemAt(i).widget())


def _card_texts(w):
    return [(c["scroll"].grade, c["name"].text(), _lines(c["odds"]), _lines(c["price"])) for c in w._cards]


def test_window_shows_the_plan_and_the_x_turns_the_setting_off(qapp, settings, win):
    settings["scroll_planner"] = True
    win.refresh()
    assert win.isVisible() and win._plan.isVisibleTo(win) or win.isVisible()
    # an empty plan asks for a pick; a non-equip key only says so
    assert win._hint.text()
    win.open_item("npc/5")
    assert "isn't a piece of gear" in win._status.text()
    # the top: its header, its clean slots, and every grade of its HP scrolls
    win.open_item("item/1000")
    qapp.processEvents()
    assert win._head.name.text() == "Silver Master Sergeant"
    assert win._head.sub.text() == "Top · 7 upgrade slots"
    assert ["".join(ch for ch in b.text() if ord(ch) < 0x2000) for b in win._stats] == ["DEF", "HP"]
    assert win._spin.value() == 7
    texts = {g: t for g, _, t, _ in _card_texts(win)}              # DEF first: its grades' odds and prices
    prices = {g: p for g, _, _, p in _card_texts(win)}
    assert "100%" in texts["Lesser"] and "+14 Weapon Def." in texts["Lesser"]
    assert "NPC: 15,000 mesos" in prices["Lesser"]
    assert "2.8%" in texts["Intermediate"]
    _pick_stat(win, "HP")                                          # the acceptance numbers, on show
    qapp.processEvents()
    hp = {g: t for g, _, t, _ in _card_texts(win)}
    assert "all 7 pass: 100%" in hp["Lesser"] and "+70 Max HP" in hp["Lesser"]
    ladders = {c["scroll"].grade: _lines(c["ladder"]) for c in win._cards}
    assert "+70: 100%" in ladders["Lesser"]
    assert "+0:" not in ladders["Lesser"]                         # a sure success shows its one outcome only
    assert "all 7 pass: 2.8%" in hp["Intermediate"] and "+63 Max HP" in hp["Intermediate"]
    # Hebrew re-texts everything, right to left
    from PySide6.QtCore import Qt

    from maplehelper.i18n import STRINGS, I18n
    win.apply_language(I18n("he"))
    qapp.processEvents()
    assert win._title.text().replace(chr(0x200F), "") == STRINGS["sp_title"]["he"]   # bidi.plain's RLM marks aside
    assert win.layoutDirection() == Qt.RightToLeft
    he = {g: t for g, _, t, _ in _card_texts(win)}
    assert "100%" in he["Lesser"]
    # every Latin run is one whole block: numbers, "mesos", percents — pieced out they scrambled ("mesos
    # 15,000 :NPC-מה", "<0.1%" flipping, the ladder running right-to-left)
    he_prices = {g: p for g, _, _, p in _card_texts(win)}
    assert "\u206615,000 mesos\u2069" in he_prices["Lesser"]
    assert "\u2066<0.1%\u2069" in he["Greater"] or "\u2066<0.1%\u2069" in he["Intermediate"]
    he_ladder = _lines(win._cards[0]["ladder"])
    assert "תוצאות" in he_ladder and "\u2066(Max HP): +70: 100%\u2069" in he_ladder   # one block, Hebrew head
    assert "\u2066" in win._summary.text()
    assert "חולצה" in win._head.sub.text()                        # the slot word in Hebrew, per itemterms
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
    _pick_stat(win, "HP")     # Intermediate and Greater have no NPC price of their own
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not answered.is_set():
        qapp.processEvents()
        time.sleep(0.01)
    pump(qapp, 100)                                                # the answer crosses to the GUI thread
    assert answered.is_set() and threads and all(t is not threading.main_thread() for t in threads)
    prices = {g: p for g, _, _, p in _card_texts(win)}
    assert "FM usual: 12,345 mesos" in prices["Intermediate"]
    assert "FM usual: 12,345 mesos" in prices["Greater"]
    assert "NPC: 15,000 mesos" in prices["Lesser"]                 # the KB's price never waits on the network


def test_window_typed_price_reprices_that_grade(qapp, settings, win):
    win.open_item("item/1000")
    _pick_stat(win, "HP")
    inter = next(c for c in win._cards if c["scroll"].grade == "Intermediate")
    inter["edit"].setText("20000")
    qapp.processEvents()
    assert "expected cost: 140,000 mesos" in _lines(inter["cost"])
    assert "your price per scroll" in _lines(inter["price"])
    assert "Intermediate 2,222" in win._summary.text()             # 140,000 mesos over an expected +63 HP


def test_window_defaults_to_what_the_character_wears(qapp, settings, profiles, win):
    c = profiles.add("Test", "Warrior", "Fighter", 30)
    c.gear = {"Top": "item/1000"}
    profiles.save()
    settings["scroll_planner"] = True
    win.refresh()
    assert win._head.name.text() == "Silver Master Sergeant"       # the character's top, until they pick
    win.open_item("item/663")
    win.character_changed()
    assert win._head.name.text() == "War Bow"                      # the player's own pick always wins
