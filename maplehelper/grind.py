"""Grind tracker: a play session measured from screenshot reads (Start, Update, End), never from the game's memory.

Each read gives what the screenshot shows: the level and EXP bar (always on the HUD), the map, the monster being
hunted, and, when the inventory is open on its Use tab, the mesos and the potion stacks. From two reads:
- measured: EXP gained (the EXP table bridges a level-up), mesos gained, potions used (a stack's count dropping);
- worked out from the KB: what those potions cost (the NPC shop price, which the KB labels with a build: "COT2");
- estimated ("~"): kills = EXP gained / the main monster's EXP, and the mesos the community's drop reports
  expect from that many kills.
A value that a read didn't give (the inventory was closed) starts at the first read that has it, so opening the
inventory at an Update still measures the mesos from then on.
"""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field, fields

from . import market, plan
from .store import DATA_DIR, _read_json, _write_json

MAX_RECENT = 20           # finished sessions kept per character
MAX_READS = 60            # reads kept in a running session (Start + Updates); the first and the newest matter


@dataclass
class Reading:
    t: float
    level: int | None = None
    exp_pct: float | None = None
    map: str = ""
    monster: str = ""
    mesos: int | None = None
    potions: dict | None = None        # potion name -> count in the Use tab; None: the Use tab wasn't read
    inventory: bool = False            # the inventory window was open in the screenshot


def _whole(v) -> int | None:
    """1234, 1234.0, "1,234" or "1 234" as an int; never a bool, a fraction or a word."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    if isinstance(v, str) and re.fullmatch(r"\s*\d[\d, ]*\s*", v):
        return int(re.sub(r"[, ]", "", v))
    return None


def _text(v) -> str:
    return v.strip()[:80] if isinstance(v, str) else ""


def _map(v) -> str:
    """The map's name without the street the game writes before it ("Victoria Road: Ant Tunnel I", read live)."""
    return _text(v).rpartition(": ")[2].strip()


def reading(t: float, update: dict | None, grind: dict | None) -> Reading:
    """One read from the AI's reply: profile_update (level, EXP bar, map) and its "grind" object (map, monster,
    inventory, mesos, potions). Anything malformed is left out, never guessed."""
    update = update if isinstance(update, dict) else {}
    g = grind if isinstance(grind, dict) else {}
    lv = _whole(update.get("level"))
    pct = update.get("exp_percent")
    pct = round(float(pct), 2) if isinstance(pct, (int, float)) and not isinstance(pct, bool) and 0 <= pct <= 100 \
        else None
    mesos = _whole(g.get("mesos"))
    pots = g.get("potions")
    potions = None
    if isinstance(pots, dict):
        potions = {}
        for name, n in pots.items():
            n = _whole(n)
            if isinstance(name, str) and name.strip() and n is not None and 0 <= n < 100_000:
                potions[name.strip()] = potions.get(name.strip(), 0) + n
    inv = g.get("inventory_open") is True or mesos is not None or potions is not None
    return Reading(t, lv if lv and 1 <= lv <= 250 else None, pct, _map(g.get("map")) or _map(update.get("map")),
                   _text(g.get("monster")), mesos if mesos is not None and 0 <= mesos < 10**11 else None,
                   potions, inv)


@dataclass
class Session:
    reads: list[Reading] = field(default_factory=list)
    monster: str = ""
    picked: bool = False               # the player chose the monster: a read doesn't replace it
    ended: float | None = None

    @property
    def start(self) -> float:
        return self.reads[0].t if self.reads else time.time()

    def add(self, r: Reading) -> None:
        self.reads.append(r)
        if len(self.reads) > MAX_READS:
            del self.reads[1:len(self.reads) - MAX_READS + 1]
        if r.monster and not self.picked:
            self.monster = r.monster

    @property
    def map(self) -> str:
        return next((r.map for r in reversed(self.reads) if r.map), "")

    def to_json(self) -> dict:
        return {"reads": [asdict(r) for r in self.reads], "monster": self.monster, "picked": self.picked,
                "ended": self.ended}

    @classmethod
    def from_json(cls, d) -> Session | None:
        if not isinstance(d, dict) or not isinstance(d.get("reads"), list):
            return None
        known = {f.name for f in fields(Reading)}
        reads = []
        for r in d["reads"]:
            try:
                reads.append(Reading(**{k: v for k, v in r.items() if k in known}))
            except (TypeError, AttributeError):
                continue
        if not reads:
            return None
        ended = d.get("ended")
        return cls(reads, _text(d.get("monster")), d.get("picked") is True,
                   ended if isinstance(ended, (int, float)) and not isinstance(ended, bool) else None)


# ---------------------------------------------------------------- the numbers

def potion_key(kb, name: str) -> str | None:
    """The KB item a read's potion name means: exact, else the shortest item name containing it ("Red Pot")."""
    q = (name or "").strip().lower()
    if not q:
        return None
    key = kb._item_by_name.get(q)
    if not key:
        part = sorted((n for n in kb._item_by_name if q in n), key=lambda n: (len(n), n))
        key = kb._item_by_name[part[0]] if part else None
    e = kb.get(key) if key else None
    return key if e and str(e.get("type", "")).startswith("Use") else None


_RECOVERY = re.compile(r"^(?:HP|MP) Recovery\b", re.M)


def is_potion(kb, key: str) -> bool:
    """An item that restores HP or MP (its page's "HP Recovery +100"): potions, elixirs, food."""
    e = kb.get(key) or {}
    return str(e.get("type", "")).startswith("Use") and bool(_RECOVERY.search(kb.page(key)))


def inventory_hint(slots: list, kb) -> str:
    """For a grind read's prompt: the potions the app recognised in the inventory by their icons (inventory.read),
    so the AI only reads each one's count and names them as the KB does."""
    found = []
    for s in slots:
        if getattr(s, "status", "") == "certain" and s.matches and is_potion(kb, s.matches[0][0]):
            found.append(f"slot {s.index}: {kb.get(s.matches[0][0])['name']}")
    if not found:
        return ""
    return ("\n<inventory_read>The app matched the inventory's icons to the database (slots count left to right, top "
            "to bottom): " + "; ".join(found) + ". Use these names in \"potions\" and read each slot's stack count "
            "(the small number at its bottom left).</inventory_read>")


def potion_price(kb, name: str) -> tuple[int, str] | None:
    """(price, source) of the cheapest NPC shop in the game that sells it; the source is the build the KB labels the
    price with ("COT2"), else MeowDB's own."""
    from .combat import released
    key = potion_key(kb, name)
    if not key:
        return None
    npc = market.npc_prices(kb, key)
    shops = [s for s in npc.shops if released(kb, s[1])]       # no El Nath shop before Ossyria is out
    return (shops[0][2], npc.source(shops[0])) if shops else None


def community_mesos(kb, monster: str) -> tuple[float, int] | None:
    """(mesos one kill drops on average, number of player reports) from the community's mesos reports, when the KB
    has them (kb.community_mesos: (min, max, chance, count) or None)."""
    get = getattr(kb, "community_mesos", None)
    if not get or not monster:
        return None
    for key in kb.monster_keys(monster):
        try:
            got = get(key)
        except Exception:      # noqa: BLE001 - a malformed report must never break the page
            got = None
        if not got:
            continue
        try:
            lo, hi, chance, count = got
            chance = float(chance)
            chance = chance / 100 if chance > 1 else chance       # a percentage or a fraction
            avg = (float(lo) + float(hi)) / 2 * chance
        except (TypeError, ValueError):
            continue
        if avg > 0:
            return avg, int(count or 0)
    return None


@dataclass
class Summary:
    seconds: float                        # session time (to now while it runs)
    map: str = ""
    monster: str = ""
    level_from: int | None = None
    level_to: int | None = None
    exp: int | None = None                # EXP gained (across level-ups)
    exp_h: int | None = None
    pct_h: float | None = None            # % of the current level per hour (what the EXP meter showed)
    to_level: int | None = None           # seconds to the next level at this rate
    exp_note: str = ""                    # why there's no EXP: "one_read" | "no_table" | "no_gain" | "no_exp"
    exp_level: int | None = None          # the level whose EXP table row the numbers use (its source tag)
    mesos: int | None = None              # mesos gained (negative: spent more than earned)
    mesos_h: int | None = None
    potions: list = field(default_factory=list)      # (name, used, price or None, source or "")
    restocked: list = field(default_factory=list)    # potions whose count went up: bought more, use unknown
    potions_cost: int | None = None
    potions_read: bool = False            # the Use tab was read twice: potions used is a measured number
    net: int | None = None                # mesos gained - potions cost
    monster_exp: int | None = None
    kills: int | None = None              # ~ EXP gained / the monster's EXP
    kills_h: int | None = None
    expected: int | None = None           # ~ mesos the community reports expect from the kills
    reports: int = 0


def _per_hour(value: float, seconds: float) -> int | None:
    return round(value * 3600 / seconds) if seconds >= 60 else None     # under a minute a rate means nothing


def summarize(kb, s: Session, now: float | None = None) -> Summary:
    now = now or time.time()
    end = s.ended or now
    out = Summary(max(0.0, end - s.start), s.map, s.monster)
    exp_reads = [r for r in s.reads if r.level and r.exp_pct is not None]
    if exp_reads:
        a, b = exp_reads[0], exp_reads[-1]
        out.level_from, out.level_to, out.exp_level = a.level, b.level, b.level
        pa, pb = plan.exp_position(kb, a.level, a.exp_pct), plan.exp_position(kb, b.level, b.exp_pct)
        if a is b:
            out.exp_note = "one_read"
        elif pa is None or pb is None:
            out.exp_note = "no_table"           # past the KB's EXP table (Lv. 100+)
        elif pb <= pa:
            out.exp_note = "no_gain"
            out.exp = 0 if pb == pa else None
        else:
            out.exp = round(pb - pa)
            rate = plan.exp_rate(kb, (a.t, a.level, a.exp_pct), (b.t, b.level, b.exp_pct))
            if rate and b.t - a.t >= 60:
                out.exp_h, out.pct_h, out.to_level = rate["per_hour"], rate.get("pct_hour"), rate.get("to_level")
    else:
        out.exp_note = "no_exp"
    mesos_reads = [r for r in s.reads if r.mesos is not None]
    if len(mesos_reads) >= 2:
        a, b = mesos_reads[0], mesos_reads[-1]
        out.mesos = b.mesos - a.mesos
        out.mesos_h = _per_hour(out.mesos, b.t - a.t)
    pot_reads = [r for r in s.reads if r.potions is not None]
    if len(pot_reads) >= 2:
        a, b = pot_reads[0], pot_reads[-1]
        out.potions_read, cost = True, 0
        for name, n0 in a.potions.items():
            n1 = b.potions.get(name, 0)           # a stack gone from the Use tab ran out
            if n1 > n0:
                out.restocked.append(name)
                continue
            if n1 == n0:
                continue
            price = potion_price(kb, name)
            out.potions.append((name, n0 - n1, price[0] if price else None, price[1] if price else ""))
            cost += (n0 - n1) * price[0] if price else 0
        out.potions.sort(key=lambda p: -p[1])
        out.potions_cost = cost
        if out.mesos is not None:
            out.net = out.mesos - cost
    out.monster_exp = plan.monster_exp(kb, s.monster) if s.monster else None
    if out.exp and out.monster_exp:
        out.kills = round(out.exp / out.monster_exp)
        a, b = exp_reads[0], exp_reads[-1]
        out.kills_h = _per_hour(out.kills, b.t - a.t)
        cm = community_mesos(kb, s.monster)
        if cm:
            out.expected, out.reports = round(out.kills * cm[0]), cm[1]
    return out


def record(s: Summary, start: float) -> dict:
    """A finished session as the Recent sessions table keeps it."""
    return {"start": round(start), "seconds": round(s.seconds), "map": s.map, "monster": s.monster,
            "level_from": s.level_from, "level_to": s.level_to, "exp": s.exp, "exp_h": s.exp_h,
            "mesos": s.mesos, "mesos_h": s.mesos_h, "potions_cost": s.potions_cost, "net": s.net,
            "kills": s.kills, "expected": s.expected}


# ---------------------------------------------------------------- saved per character

class Store:
    """The running session and the last finished ones, per character, in grind.json (the app's data folder): a
    session outlives the tools window and an app restart."""
    path = DATA_DIR / "grind.json"

    def __init__(self):
        raw = _read_json(self.path, {"active": {}, "recent": {}})
        self._active = raw.get("active") if isinstance(raw.get("active"), dict) else {}
        self._recent = raw.get("recent") if isinstance(raw.get("recent"), dict) else {}

    def session(self, cid: str) -> Session | None:
        return Session.from_json(self._active.get(cid))

    def running(self, cid: str) -> Session | None:
        s = self.session(cid)
        return s if s and not s.ended else None

    def start(self, cid: str, r: Reading, monster: str = "", picked: bool = False) -> Session:
        s = Session(monster=monster, picked=picked and bool(monster))
        s.add(r)
        self._put(cid, s)
        return s

    def add(self, cid: str, r: Reading) -> Session | None:
        s = self.running(cid)
        if s:
            s.add(r)
            self._put(cid, s)
        return s

    def set_monster(self, cid: str, name: str) -> None:
        s = self.session(cid)
        if s:
            s.monster, s.picked = name, bool(name)
            self._put(cid, s)

    def end(self, cid: str, kb, now: float | None = None) -> dict | None:
        """Stop the running session; it joins Recent sessions when it measured something. Returns its record."""
        s = self.running(cid)
        if not s:
            return None
        s.ended = now or time.time()
        summary = summarize(kb, s, s.ended)
        rec = record(summary, s.start)
        if summary.seconds >= 60 and any(rec[k] is not None for k in ("exp", "mesos", "potions_cost")):
            self._recent[cid] = ([rec] + [r for r in self._recent.get(cid, []) if isinstance(r, dict)])[:MAX_RECENT]
        else:
            rec = None
        self._put(cid, s)
        return rec

    def recent(self, cid: str) -> list[dict]:
        return [r for r in self._recent.get(cid, []) if isinstance(r, dict)]

    def forget(self, cid: str) -> None:
        """A deleted character's sessions go with it."""
        self._active.pop(cid, None)
        self._recent.pop(cid, None)
        self.save()

    def _put(self, cid: str, s: Session) -> None:
        self._active[cid] = s.to_json()
        self.save()

    def save(self) -> None:
        _write_json(self.path, {"active": self._active, "recent": self._recent})
