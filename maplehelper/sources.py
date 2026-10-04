"""Where each piece of game data comes from, read from the knowledge base's own labels.

NiaMeowDB says on its pages what kind of data each block is, and the app passes that on to the player:
- a "Change history" block, "updated in COT2 ▾ Stat | COT1 | COT2 | Change": the page's values are that build's
  (the second closed test), with what changed since the build before;
- "COT2 prices" under a shop's price: the price is that build's;
- "COT2 map data stores a 1-hour base timer ... an unconfirmed 1 to 1.5 hour window": a boss respawn timer;
- "MSEA reference drops": old MapleSEA's drop table, historical reference; "Community sourced": what players saw
  in Classic themselves (the Free Market reports too);
- "Source: Nexon report", and the release guide built from Nexon's own posts: official statements;
- the EXP guide's "Levels 50 to 99 reproduce a historical reference table".
Nothing here names a build: the labels are read from the pages, so when MeowDB moves an item from "COT2" to
launch values ("updated in Launch ▾ Stat | COT2 | Launch | Change", "Launch prices") its tag follows by itself.
Data the KB doesn't label is MeowDB's own (MEOWDB).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import bidi

# the fixed kinds of source; any other source string is a build label the KB names ("COT1", "COT2", "Launch")
MSEA = "MSEA"
COMMUNITY = "community"
OFFICIAL = "official"
MEOWDB = "MeowDB"
REFERENCE = "reference"        # a historical reference table (the EXP guide's levels 50+)
CLOSED_TEST = "closed_test"    # a value the KB marks "(closed test)" without naming the build (a pet's lifespan)
FIXED = (MSEA, COMMUNITY, OFFICIAL, MEOWDB, REFERENCE, CLOSED_TEST)

# readable names for the build labels the KB uses or is likely to (an unknown one is shown as written)
BUILD_NAMES = {
    "COT1": {"he": "COT1", "en": "COT1"},
    "COT2": {"he": "COT2", "en": "COT2"},
    "Launch": {"he": "השקה", "en": "Launch"},
    "Grand Launch": {"he": "השקה", "en": "Launch"},
    "Founder's Access": {"he": "Founder's Access", "en": "Founder's Access"},
    "Live": {"he": "המשחק החי", "en": "Live"},
}

_HISTORY = re.compile(r"^updated in (?P<label>.+?) ▾ ?(?P<rest>.*)$")
_TABLE = re.compile(r"^Stat \| (?P<old>.+?) \| (?P<new>.+?) \| Change\s*$")
# "COT2 prices", "COT2 prices Citizen of Honor +", "COT2 prices full shop page →"; never "Free Market Prices"
# a label line, not a sentence: bare, with a citizen grade ("... +") or a link ("... →"); "Buyable-ingredient
# prices pulled from the NPC shop database" and an NPC's "Great prices around here!" are prose
_PRICES = re.compile(r"^(?P<label>[A-Z0-9][\w'-]*(?: [A-Z][\w'-]*)?) prices(?P<rest>(?: .*[+→])?)\s*$")
_MAP_DATA = re.compile(r"^(?P<label>.+?) map data stores (?P<rest>.*)$")
_PARTY_EXP = re.compile(r"^Party EXP\. (?P<label>.+?) grants ")
_GUIDE_DATA = re.compile(r"\buse current (?P<label>[A-Z0-9]\S*) data\b")       # not "use current spawn data"
_SOURCE_LINE = re.compile(r"^Source ?: (?P<who>.+)$")
_CLOSED_PRICE = re.compile(r"^Closed-test price\b(?P<rest>.*)$")
_EXP_REFERENCE = re.compile(r"Levels (\d+) to (\d+) reproduce a historical reference")
_MSEA_DROPS = re.compile(r"^MSEA reference drops$", re.I)
EXP_GUIDE = "guide/exp-table-level-1-to-100"
RELEASE_GUIDE = "guide/maplestory-classic-worlds-release-date"


@dataclass(frozen=True)
class Change:
    stat: str          # as the page's table names it ("ACC", "WATK", "Shop price (mesos)")
    old: str
    new: str
    delta: str = ""


@dataclass(frozen=True)
class Stamp:
    """The build a page's values are from: "COT2", changed from "COT1" (changes: the table's rows)."""
    source: str
    before: str = ""
    changes: tuple[Change, ...] = ()


@dataclass(frozen=True)
class Marker:
    """One classification the KB writes on a page: what kind of data, and its source."""
    kind: str          # history | prices | respawn | drops | community_list | fm_reports | shop_list | official
    source: str        # | guide_data | exp_table | party_exp | mob_rate | cash_price | cash_shop
    line: str = ""


def _body(text: str) -> list[str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        text = text[end + 4:] if end > 0 else text
    return [ln.strip() for ln in text.splitlines()]


# ---------------------------------------------------------------- change history (stats)

def history(text: str) -> Stamp | None:
    """The page's newest build and its changes, from its "Change history" block. With more than one block
    ("updated in Launch ▾ Stat | COT2 | Launch" and an older "updated in COT2 ▾ ..."), the newest is the label
    no table names as the one it changed from."""
    lines = _body(text)
    blocks: list[tuple[str, str, list[Change]]] = []         # (label, before, rows)
    for i, ln in enumerate(lines):
        m = _HISTORY.match(ln)
        if not m:
            continue
        label, rows, before = m.group("label").strip(), [], ""
        tm = _TABLE.match(m.group("rest"))
        if tm:
            before, label = tm.group("old").strip(), tm.group("new").strip() or label
            for row in lines[i + 1:]:
                cols = [c.strip() for c in row.split(" | ")]
                if len(cols) != 4 or _HISTORY.match(row):       # (the next build's header splits in four too)
                    break
                rows.append(Change(*cols))
        blocks.append((label, before, rows))
    if not blocks:
        return None
    older = {b for _, b, _ in blocks if b}
    newest = next((b for b in blocks if b[0] not in older), blocks[0])
    same = [b for b in blocks if b[0] == newest[0]]
    rows = tuple(r for b in same for r in b[2])
    return Stamp(newest[0], next((b[1] for b in same if b[1]), ""), rows)


def stat_source(kb, key: str) -> Stamp | None:
    """The build a monster's, item's or skill's values are from, or None when the KB doesn't label them (MEOWDB)."""
    memo = kb.__dict__.setdefault("_stamps", {}) if hasattr(kb, "__dict__") else {}
    if key not in memo:
        memo[key] = history(kb.page(key)) if kb.get(key) else None
    return memo[key]


def source_of(kb, key: str) -> str:
    """The one source a card's values carry: the page's build, else MeowDB's own."""
    s = stat_source(kb, key)
    return s.source if s else MEOWDB


# a prop of index.json (what cards and patch notes show) -> the stat its page's change table names
TABLE_STAT = {"Weapon Attack": "WATK", "Magic Attack": "MATK", "Weapon Defense": "WDEF", "Magic Defense": "MDEF",
              "Accuracy": "ACC", "Avoidability": "AVOID", "Speed": "SPD", "Jump": "JUMP", "Crit Rate": "CRIT",
              "Crit Damage": "CRIT DMG", "Physical Damage": "P.DMG", "Physical Defense": "P.DEF",
              "Magic Damage": "M.DMG", "HP": "HP", "MP": "MP", "STR": "STR", "DEX": "DEX", "INT": "INT",
              "LUK": "LUK", "Knockback": "Knockback", "EXP": "EXP", "Level": "Level"}


def change_for(stamp: Stamp | None, prop: str, new=None) -> Change | None:
    """The change-table row behind a prop ("Weapon Attack" -> the "WATK" row), when its new value is `new`."""
    if not stamp:
        return None
    stat = TABLE_STAT.get(prop, prop)
    for c in stamp.changes:
        if c.stat == stat and (new is None or _num(c.new) == _num(new)):
            return c
    return None


def _num(v) -> str:
    return re.sub(r"[,\s+]", "", str(v))


# ---------------------------------------------------------------- other labels

def price_label(line: str) -> str | None:
    """The build a shop price is from, by the line under it ("COT2 prices Helpful Stranger +" -> "COT2")."""
    m = _PRICES.match(line.strip())
    return m.group("label") if m else None


def price_rest(line: str) -> str:
    """What follows "<label> prices": the citizen grade the price needs ("Citizen of Honor +"), or ""."""
    m = _PRICES.match(line.strip())
    return m.group("rest").strip() if m else ""


def respawn_source(kb, key: str) -> tuple[str, bool] | None:
    """A boss timer's source: (build label, unconfirmed) from "COT2 map data stores a 1-hour base timer ...
    this timer uses an unconfirmed 1 to 1.5 hour window"; None when the page doesn't label it."""
    for ln in _body(kb.page(key)):
        m = _MAP_DATA.match(ln)
        if m:
            return m.group("label"), "unconfirmed" in m.group("rest")
    return None


def guide_source(kb, key: str) -> str | None:
    """The build a class guide's numbers are from ("The damage, Accuracy, skill ... examples use current COT2
    data"): the build plan tables come from these guides."""
    m = _GUIDE_DATA.search(kb.page(key)) if key and kb.get(key) else None
    return m.group("label") if m else None


def exp_source(kb, level: int) -> str:
    """The EXP table's level `level`: confirmed in the current game (MeowDB's own) or the historical reference
    table, as the EXP guide's confidence boundary says."""
    text = kb.page(EXP_GUIDE) if kb.get(EXP_GUIDE) else ""
    for lo, hi in _EXP_REFERENCE.findall(text):
        if int(lo) <= level <= int(hi):
            return REFERENCE
    return MEOWDB


def build_labels(kb) -> set[str]:
    """Every build label the KB uses in its change histories and price labels ("COT1", "COT2", ...): read, not
    listed, so shop "Source :" lines can be matched against them."""
    memo = kb.__dict__.get("_build_labels") if hasattr(kb, "__dict__") else None
    if memo is not None:
        return memo
    found: set[str] = set()
    for key in kb.entities:
        if key.partition("/")[0] not in ("item", "monster", "skill"):
            continue
        for ln in _body(kb.page(key)):
            m = _HISTORY.match(ln)
            if m:
                found.add(m.group("label").strip())
                t = _TABLE.match(m.group("rest"))
                if t:
                    found |= {t.group("old").strip(), t.group("new").strip()}
            elif (p := price_label(ln)):
                found.add(p)
    if hasattr(kb, "__dict__"):
        kb._build_labels = found
    return found


def _label_in(text: str, labels: set[str]) -> str | None:
    hits = [lb for lb in labels if re.search(rf"(?<![\w]){re.escape(lb)}(?![\w])", text)]
    return max(hits, key=len) if hits else None


def markers(kb, key: str) -> list[Marker]:
    """Every classification the KB writes on one page (the coverage test checks them all, the AI gets them)."""
    text = kb.page(key)
    lines = _body(text)
    out: list[Marker] = []
    known: list[set[str]] = []

    def labels() -> set[str]:            # read across the KB only for the few lines that need it
        if not known:
            known.append(build_labels(kb))
        return known[0]
    stamp = history(text)
    if stamp:
        out.append(Marker("history", stamp.source))
    for ln in lines:
        if (lb := price_label(ln)):
            out.append(Marker("prices", lb, ln))
        elif (m := _MAP_DATA.match(ln)):
            out.append(Marker("respawn", m.group("label"), ln))
        elif _MSEA_DROPS.match(ln):
            out.append(Marker("drops", MSEA, ln))
        elif ln.startswith("Community sourced"):
            out.append(Marker("community_list", COMMUNITY, ln))
        elif ln == "Community price check":
            out.append(Marker("fm_reports", COMMUNITY, ln))
        elif (m := _SOURCE_LINE.match(ln)):
            who = m.group("who")
            if "Nexon" in who:
                out.append(Marker("official", OFFICIAL, ln))
            else:
                # a shop list read from a test's screenshots: that build's, and players' when they took them
                build = _label_in(who, labels())
                if build:
                    out.append(Marker("shop_list", build, ln))
                if re.search(r"community|tester|Discord|Reported", who, re.I):
                    out.append(Marker("shop_list", COMMUNITY, ln))
                if not out or out[-1].line != ln:
                    # anything else names MeowDB's own tools ("builds from the meowdb.com Character Builder")
                    out.append(Marker("source", MEOWDB, ln))
        elif (m := _PARTY_EXP.match(ln)):
            out.append(Marker("party_exp", m.group("label"), ln))
        elif "from closed-beta play" in ln:
            out.append(Marker("mob_rate", MEOWDB, ln))       # MeowDB's own reading of the tests
        elif (m := _CLOSED_PRICE.match(ln)):
            out.append(Marker("cash_price", _label_in(m.group("rest"), labels()) or "closed test", ln))
        elif ln.endswith(" only") and (build := _label_in(ln, labels())) and ln.endswith(f"{build} only"):
            out.append(Marker("cash_shop", build, ln))
        if (m := _GUIDE_DATA.search(ln)):
            out.append(Marker("guide_data", m.group("label"), ln))
    if _EXP_REFERENCE.search(text):
        out.append(Marker("exp_table", REFERENCE))
    if key == RELEASE_GUIDE and re.search(r"^Official sources:", text, re.M):
        out.append(Marker("official", OFFICIAL))
    return out


# ---------------------------------------------------------------- for the player (he/en)

def tag(t, source: str) -> str:
    """The short label of a source, in the UI's language: "MSEA", "COT2", "קהילה" / "Community"."""
    if source in FIXED:
        return t(f"src_{source.lower()}")
    names = BUILD_NAMES.get(source)
    return names.get(t.lang, names["en"]) if names else source


def tip(t, source: str) -> str:
    """What a source means, for its tooltip."""
    if source in FIXED:
        return t(f"src_{source.lower()}_tip")
    test = re.fullmatch(r"COT(\d+)", source)
    if test:
        nth = f"src_nth_{test.group(1)}"
        return t("src_cot_tip", label=source, nth=t(nth) if t(nth) != nth else t("src_nth"))
    if source in BUILD_NAMES:
        return t("src_launch_tip", label=tag(t, source))
    return t("src_build_tip", label=source)


def price_note(t, label: str) -> str:
    """The words after a shop price in a text answer: "(COT2 test price)", "(Launch price)"."""
    if re.fullmatch(r"COT\d+", label):
        return t("price_test", label=label)
    return t("price_build", label=tag(t, label))


def mesos_line(t, mesos) -> str:
    """ "מזו 18–23 (קהילה)" / "Mesos 18–23 (Community)" for kb.community_mesos's (min, max, chance, reports)."""
    lo, hi = mesos[0], mesos[1]
    return t("mesos_line", range=f"{lo:,}" if lo == hi else f"{lo:,}–{hi:,}", src=tag(t, COMMUNITY))


def change_line(stat: str, old, new, before: str = "", after: str = "") -> str:
    """ "ACC 62 → 64 (COT1 → COT2)" as one left-to-right block (bidi.ltr_block's isolate): in a Hebrew line the
    arrow still points from the old value to the new one and the parentheses stay around the labels."""
    labels = f" ({before} → {after})" if before and after else ""
    return f"{bidi.LRI}{stat} {old} → {new}{labels}{bidi.PDI}"


def stamp_tip(t, source: str, stamp: Stamp | None = None, limit: int = 6) -> str:
    """A tag's tooltip: what the source means, then (a build's values) what changed from the build before."""
    text = tip(t, source)
    if stamp and stamp.source == source and stamp.changes:
        lines = [change_line(c.stat, c.old, c.new, stamp.before, stamp.source) for c in stamp.changes[:limit]]
        if len(stamp.changes) > limit:
            lines.append(t("pn_more", n=len(stamp.changes) - limit))
        text += "\n" + t("src_changed_head", before=stamp.before or "?") + "\n" + "\n".join(lines)
    return text


# ---------------------------------------------------------------- for the AI (English)

AI_NAMES = {MSEA: "MSEA reference (old MapleSEA, not confirmed for Classic)",
            COMMUNITY: "community (player-reported on MeowDB)",
            OFFICIAL: "official (Nexon)",
            MEOWDB: "MeowDB (no build label)",
            REFERENCE: "historical reference table (an estimate until verified)",
            CLOSED_TEST: "closed-test value (not confirmed for launch)"}


def ai_name(source: str) -> str:
    return AI_NAMES.get(source, f"{source} values")


def page_note(kb, key: str) -> str:
    """One line for the AI about a pre-fetched page: what each kind of data on it is."""
    bits: list[str] = []
    stamp = stat_source(kb, key)
    if stamp:
        ch = "; ".join(f"{c.stat} {c.old} -> {c.new}" for c in stamp.changes[:6])
        bits.append(f"stats are {stamp.source} values" + (f" (changed from {stamp.before}: {ch})" if ch else ""))
    elif key.partition("/")[0] in ("monster", "item", "skill"):
        bits.append("stats: MeowDB (no build label)")
    seen = set()
    for m in markers(kb, key):
        what = {"prices": f"shop prices are {m.source} prices",
                "respawn": f"respawn timer from {m.source} map data" + (", unconfirmed" if "unconfirmed" in m.line else ""),
                "drops": "the drop list is the MSEA reference list (old MapleSEA, not confirmed for Classic)",
                "shop_list": f"shop list: {ai_name(m.source)}",
                "official": "official (Nexon)",
                "guide_data": f"numbers are {m.source} data",
                "cash_price": f"Cash Shop price from the {m.source} test",
                "cash_shop": f"Cash Shop listing: {m.source} only",
                "source": "numbers from MeowDB's own tools",
                "party_exp": f"party EXP bonus from {m.source}",
                "mob_rate": "mob rate is MeowDB's reading of the closed tests",
                "exp_table": "levels 50+ are a historical reference table"}.get(m.kind)
        if what and what not in seen:
            seen.add(what)
            bits.append(what)
    return f"[sources: {'; '.join(bits)}]" if bits else ""
