"""Loot log: the EXP, mesos and items the game's pickup messages report, read on this computer (no AI, no memory).

The game writes each gain as a line at the bottom right of the play area, right of the skill bar:
"You received EXP (+21)", "You have gained mesos (+27)", "Leaf x1 earned. (Etc)". A new line appears at the
bottom and pushes the older ones up a line; each fades out after a few seconds (its right end first). The game's
interface scales with its window, so the messages sit at the same place in the picture at any size: `auto_box`
(measured live at 1368x800, 1922x1112 and 2734x1568). The player may draw the box instead (the Loot log page's
"Mark it yourself"). While a log runs, RapidOCR reads the box twice a second (ui/lootscan.py).

A line stays on screen for several reads, so a read can't simply be added up. `Tracker` keeps the lines it has seen
with their row (0 = the bottom one) and, for every read, finds how many rows the stack moved up since the last one:
the shift that explains the read best (a line where it was, moved up by the shift, agrees; a different line there
disagrees). The rows below the moved stack are the new messages. A line read in part ("You have gained mes", fading)
still holds its row, and the value is taken from the clearest read of the line (OCR read "(+43)" as "(443)" while the
line faded, seen live). No Qt: the scanner runs it on a worker thread."""
from __future__ import annotations

import difflib
import logging
import re
import statistics
import time
import weakref
from dataclasses import asdict, dataclass, field

import numpy as np

from .store import DATA_DIR, _read_json, _write_json

log = logging.getLogger("maplehelper")

MAX_RECENT = 20             # finished logs kept per character
MAX_EVENTS = 5000           # events kept in a running log (a long session's newest; the totals keep counting)
MAX_ROWS = 10               # rows a stack is followed up to
UNSEEN_S = 1.6              # a line not read for this long has faded: it stops holding its row
LIFETIME_S = 12.0           # ... and none outlives this (the game fades them in ~6 s, measured live)
ITEM_FUZZY = 0.8            # how close a read item name must be to a KB name to count as it ("Red Srail Shell")
_EARNED = re.compile(r"\s*\.?\s*ea[rm]n?ed\b.*$", re.IGNORECASE)
_PAREN = re.compile(r"[(\[{]\s*(?P<sign>[+t4]?)\s*(?P<num>\d[\d,.]*)\s*[)\]}]")
# the count after an item's name as OCR reads it: " x1", "x1", "xl", "xd", "xdl", or a lone " d" (the "x1" faded)
_QTY = re.compile(r"(?:\s+[x×X][\dlIi|dD]{0,3}|[x×X][\dlIi|dD]{1,3}|\s+[\dlIi|dD]{1,3})\s*$")
_ITEM = re.compile(r"^(?P<name>.+?)\s*[x×X]\s*(?P<n>[\dlIi|]{1,4})\s*\.?\s*[eE]\w*[rn]\w*\b", re.IGNORECASE)
# where the messages are, as fractions of the game's picture (left, top, right, bottom). Measured at three window
# sizes: lines from x 0.86 to the right edge, the stack's top line no higher than 0.72 (5 lines), its bottom line's
# bottom at 0.81-0.82, text 2% of the height. The box leaves room for ~8 lines, and stops short of the chat bubbles
# above the player ("mesos? This", read at 0.375).
AUTO_BOX = (0.78, 0.57, 1.0, 0.86)


def auto_box(play: tuple[int, int, int, int]) -> dict:
    """The messages box ({"x","y","w","h"}, capture coordinates) in the game's picture (x, y, w, h)."""
    x, y, w, h = play
    left, top, right, bottom = AUTO_BOX
    x0, y0 = x + round(w * left), y + round(h * top)
    return {"x": x0, "y": y0, "w": max(1, x + round(w * right) - x0), "h": max(1, y + round(h * bottom) - y0)}


# a box shorter than this is enlarged before it is read: on a 1368x800 window the messages are ~17 px tall and the
# reader missed half of them (41 EXP of the 87 the frames show); enlarged to this height it read all 87, and the
# bigger windows' counts didn't change
OCR_MIN_H = 450


def ocr_rows(arr: np.ndarray) -> list[tuple[str, float, float]] | None:
    """(text, y centre, height) of every line read in the box, in the box's own pixels; None when the reader won't
    load. The text-angle classifier stays off: it turned some message lines upside down ("(g+) sosam paugeb ae
    nog", seen live)."""
    from . import hud
    eng = hud._ocr()
    if eng is None:
        return None
    k = 1.0
    if 0 < arr.shape[0] < OCR_MIN_H:
        from PIL import Image
        k = OCR_MIN_H / arr.shape[0]
        arr = np.asarray(Image.fromarray(arr).resize((round(arr.shape[1] * k), OCR_MIN_H), Image.LANCZOS))
    with hud._engine_lock:
        found = eng(arr, use_cls=False)
    if found is None or not found.txts:
        return []
    out = []
    for text, box in zip(found.txts, np.asarray(found.boxes).reshape(len(found.txts), -1, 2)):
        out.append((str(text).strip(), float(box[:, 1].mean()) / k, float(box[:, 1].max() - box[:, 1].min()) / k))
    return out


@dataclass
class Line:
    """One message as read: what kind of gain, and its value when the read shows it whole."""
    kind: str                   # "exp" | "mesos" | "item"
    amount: int | None = None   # EXP or mesos; None: cut off (fading) or unreadable
    sure: bool = False          # the amount had its "+": no "(+43)" read as "(443)"
    name: str = ""              # an item's name as read
    qty: int | None = None      # an item's count


def _digits(text: str) -> int | None:
    t = text.replace(",", "").replace(".", "")
    return int(t) if t.isdigit() else None


def _amount(text: str) -> tuple[int | None, bool]:
    """(amount, sure) from "(+27)"; a "+" read as "4" ("(443)") is no sure amount, a cut-off "(+2" none at all."""
    m = _PAREN.search(text)
    if not m:
        return None, False
    n = _digits(m.group("num"))
    if n is None:
        return None, False
    sign = m.group("sign")
    if sign == "+":
        return n, True
    if sign in ("t", "4"):
        return n, False     # "+" misread: the number after it is right, but not to be trusted alone
    return n, False


def parse(text: str) -> Line | None:
    """A pickup message from one read line; None for anything else (a name tag passing behind the box)."""
    t = " ".join(text.split())
    low = t.lower()
    squashed = low.replace(" ", "")
    if "exp" in squashed and ("rece" in squashed or "eived" in squashed or "ceiv" in squashed or "recel" in squashed):
        n, sure = _amount(t)
        return Line("exp", n, sure)
    if re.search(r"me[s5][o0g]s|mesos|mes\b|mes$", low) or ("gained" in squashed or "have" in squashed) and "mes" in squashed:
        n, sure = _amount(t)
        return Line("mesos", n, sure)
    m = _ITEM.search(t)
    if m or _EARNED.search(t):
        name = m.group("name") if m else _EARNED.sub("", t)
        q = None
        if m:
            q = int(m.group("n").translate(str.maketrans("lIi|", "1111")) or 0) or None
        name = _QTY.sub("", name).strip(" .,:;-")
        return Line("item", name=name, qty=q, sure=bool(m and name))
    return None


def _name_ratio(a: str, b: str) -> float:
    a, b = a.lower(), b.lower()
    return 1.0 if a == b else difflib.SequenceMatcher(None, a, b).ratio()


def match(a: Line, b: Line) -> int:
    """How two reads fit as one message: 1 they agree, 0 they may (a value cut off, or one OCR slip apart: "+26"
    read "+28" on the next read, seen live), -1 they are two different messages."""
    if a.kind != b.kind:
        return -1
    if a.kind == "item":
        if a.qty is not None and b.qty is not None and a.qty != b.qty and a.sure and b.sure:
            return -1
        if not (a.name and b.name):
            return 0
        r = _name_ratio(a.name, b.name)
        if r >= 0.75:
            return 1
        return 0 if r >= 0.5 or not (a.sure and b.sure) else -1
    if a.amount is None or b.amount is None:
        return 0
    if a.amount == b.amount:
        return 1
    x, y = str(a.amount), str(b.amount)
    if len(x) == len(y) >= 2 and sum(c != d for c, d in zip(x, y, strict=True)) == 1:
        return 0
    # "(443)" or "(243)" for "(+43)": a misread "+" adds a digit in front
    lo, hi = sorted((x, y), key=len)
    if len(hi) == len(lo) + 1 and hi.endswith(lo) and not (a.sure and b.sure):
        return 0
    return -1


def agree(a: Line, b: Line) -> bool:
    """Two reads can be the same message."""
    return match(a, b) >= 0


KINDS = ("exp", "mesos", "item", "potion")


@dataclass
class Event:
    """A gain, counted once: kind, value and when. "potion" is the other way: potions used, read off the game's quick
    slots (quickslot.py), not from a pickup message."""
    t: float
    kind: str
    amount: int = 0             # EXP / mesos; an item's count; potions used
    name: str = ""              # the item as the KB names it (or as read when the KB has no such item)
    key: str = ""               # the KB item, "" when unknown
    saved: bool = False         # written to the loot database (lootdb.py)
    map: str = ""               # the map the player was on (the minimap's read, ui/location.py), "" when unknown

    def to_json(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v not in ("", None)}
        if not self.saved:
            del out["saved"]
        return out

    @classmethod
    def from_json(cls, raw) -> Event | None:
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
            return None
        t, amount = raw.get("t"), raw.get("amount", 0)
        if not isinstance(t, (int, float)) or not isinstance(amount, int) or isinstance(amount, bool):
            return None
        return cls(float(t), raw["kind"], amount, str(raw.get("name") or ""), str(raw.get("key") or ""),
                   raw.get("saved") is True, str(raw.get("map") or ""))


@dataclass
class _Tracked:
    row: int
    line: Line                  # the first read: what later reads are compared with
    first: float
    seen: float
    event: Event | None = None
    reads: list[Line] = field(default_factory=list)     # every read that fits it: the value is their vote
    owed: bool = False          # a new message: counted, or to be counted once a read shows its value


class Tracker:
    """Turns successive reads of the messages box into Events, each message once (see the module's notes).

    `read(rows, now)` takes the read lines (text, y centre, height) and returns the new Events; a later read may
    still correct an Event it returned (its value, from a clearer read of the same line)."""

    def __init__(self, items: dict[str, str] | None = None):
        self._items = items or {}               # lower-case KB item name -> key
        self._names = [n for n in self._items if not n.startswith("\0")]
        self._lines: list[_Tracked] = []
        self._bottom: float | None = None       # the bottom row's y (the newest message's), learned
        self._pitch: float | None = None        # px between two rows, learned
        self._started = False

    def reset(self) -> None:
        """A new box, or a new log: nothing on screen is known; the next read is a baseline."""
        self._lines, self._bottom, self._pitch, self._started = [], None, None, False

    # ---------------------------------------------------------------- rows

    def _rows(self, found: list[tuple[Line, float, float]]) -> dict[int, Line]:
        ys = sorted(y for _, y, _ in found)
        gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0]
        h = statistics.median(hh for _, _, hh in found)
        if gaps:
            g = min(gaps)
            if self._pitch is None or abs(g - self._pitch) < 0.3 * self._pitch:
                self._pitch = g if self._pitch is None else 0.7 * self._pitch + 0.3 * g
        pitch = self._pitch or 1.4 * h
        low = ys[-1]
        if self._bottom is None or low > self._bottom + 0.5 * pitch:
            self._bottom = low                  # (a lower line: the bottom row is lower than thought)
        elif abs(low - self._bottom) < 0.25 * pitch:
            self._bottom = 0.8 * self._bottom + 0.2 * low
        out: dict[int, Line] = {}
        for line, y, _ in sorted(found, key=lambda f: -f[1]):
            r = max(0, round((self._bottom - y) / pitch))
            if r < MAX_ROWS and r not in out:
                out[r] = line
        return out

    @staticmethod
    def _score(old: dict[int, _Tracked], cur: dict[int, Line], k: int) -> float:
        score = 0.0
        for r, tr in old.items():
            c = cur.get(r + k)
            if c is not None:
                score += match(tr.line, c)
        for r in cur:
            if r >= k and (r - k) not in old:
                score -= 0.5        # a line where none was: missed by the last read, or the stack moved
        return score

    # ---------------------------------------------------------------- reading

    def read(self, rows: list[tuple[str, float, float]], now: float | None = None) -> list[Event]:
        now = time.time() if now is None else now
        self._lines = [tr for tr in self._lines
                       if now - tr.seen <= UNSEEN_S and now - tr.first <= LIFETIME_S]
        found = [(ln, y, h) for text, y, h in rows if (ln := parse(text)) is not None]
        if not found:
            return []
        cur = self._rows(found)
        old = {tr.row: tr for tr in self._lines}
        if not self._started:
            # the first read: what is on screen came before the log started
            self._started = True
            self._lines = [_Tracked(r, ln, now, now, None, [ln]) for r, ln in cur.items()]
            return []
        top = max(cur) + 1
        k = max(range(top + 1), key=lambda k: (self._score(old, cur, k), -k))
        moved = {r + k: tr for r, tr in old.items() if r + k < MAX_ROWS}
        new: list[Event] = []
        kept: list[_Tracked] = []
        for r in sorted(set(moved) | set(cur), reverse=True):   # oldest (top) first
            tr, ln = moved.get(r), cur.get(r)
            if tr is not None:
                tr.row = r
                if ln is not None:
                    tr.seen = now
                    if agree(tr.line, ln):
                        tr.reads.append(ln)
                        if tr.owed and tr.event is None:
                            tr.event = self._event(ln, tr.first)    # its value is read now: counted now
                            if tr.event is not None:
                                new.append(tr.event)
                        self._settle(tr)
                    # (else a misread of the line there: a fading one reads "Sralnghell", "(45)")
                kept.append(tr)
                continue
            # a line where none was known: below the moved stack it is new; higher up, one the last reads missed,
            # counted only when read whole (a fading line's half read is no message of its own)
            tr = _Tracked(r, ln, now, now, None, [ln])
            if r < k or ln.sure:
                tr.owed = True
                tr.event = self._event(ln, now)
                if tr.event is not None:
                    new.append(tr.event)
            kept.append(tr)
        self._lines = kept
        return new

    def _item(self, name: str) -> tuple[str, str]:
        """(KB name, key) of a read item name: exact, else the closest KB name; else the read name, no key."""
        low = name.lower()
        if low in self._items:
            return self._title(low), self._items[low]
        close = difflib.get_close_matches(low, self._names, n=1, cutoff=ITEM_FUZZY) if low else []
        if close:
            return self._title(close[0]), self._items[close[0]]
        return name, ""

    def _title(self, low: str) -> str:
        return self._items.get("\0" + low, low)

    def _event(self, ln: Line, now: float) -> Event | None:
        """The Event a new line counts as; None while its value is unread (an item without its name, mesos without
        the amount): the next read that shows it counts it."""
        if ln.kind == "item":
            if not ln.name:
                return None
            name, key = self._item(ln.name)
            return Event(now, "item", ln.qty or 1, name, key)
        return Event(now, ln.kind, ln.amount) if ln.amount is not None else None

    def _settle(self, tr: _Tracked) -> None:
        """Another read of a line already counted: its Event takes the reads' vote (the totals follow). One read in
        a few slips a digit ("+26" as "+28", seen live); the value most reads give wins, the first on a tie."""
        ev = tr.event
        if ev is None:
            return
        if ev.kind == "item":
            names = [self._item(ln.name) for ln in tr.reads if ln.name]
            known = [n for n in names if n[1]] or names
            if known:
                ev.name, ev.key = _vote(known)
            qty = [ln.qty for ln in tr.reads if ln.qty and ln.sure]
            ev.amount = _vote(qty) if qty else ev.amount
            return
        sure = [ln.amount for ln in tr.reads if ln.amount is not None and ln.sure]
        some = sure or [ln.amount for ln in tr.reads if ln.amount is not None]
        if some:
            ev.amount = _vote(some)


def _vote(values: list):
    """The value given most often; the earliest of the tied ones."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda v: (counts[v], -values.index(v)))


def item_names(kb) -> dict[str, str]:
    """Lower-case KB item name -> key, with each name's own spelling under "\\0" + lower (for the log's text)."""
    out: dict[str, str] = {}
    for k, e in kb.entities.items():
        if e.get("category") == "item" and e.get("name"):
            low = e["name"].strip().lower()
            if low not in out:
                out[low] = k
                out["\0" + low] = e["name"].strip()
    return out


# ---------------------------------------------------------------- a log and its totals

@dataclass
class Log:
    """One loot log: its events (the newest MAX_EVENTS), and the totals of the older ones it let go."""
    start: float = field(default_factory=time.time)
    ended: float | None = None
    events: list[Event] = field(default_factory=list)
    base: dict = field(default_factory=lambda: {"exp": 0, "mesos": 0, "items": {}, "potions": {}})
    db_id: int | None = None    # its row in the loot database (lootdb.py); None until it has one
    maps: dict = field(default_factory=dict)    # map id -> seconds the log ran on it (the minimap's reads)
    level: int | None = None    # the character's level when it started

    def add(self, evs: list[Event]) -> None:
        self.events.extend(evs)
        if len(self.events) > MAX_EVENTS:
            gone = self.events[:len(self.events) - MAX_EVENTS]
            del self.events[:len(gone)]
            _total(gone, self.base)

    def totals(self) -> tuple[int, int, dict[str, list]]:
        """(EXP, mesos, {item name: [count, key]}), from the events as they read now (a later read of a line can
        still correct a value)."""
        out = self.sums()
        return out["exp"], out["mesos"], out["items"]

    def potions(self) -> dict[str, list]:
        """{potion name: [used, key]}."""
        return self.sums()["potions"]

    def sums(self) -> dict:
        """{"exp", "mesos", "items": {name: [count, key]}, "potions": {name: [used, key]}}."""
        out = {"exp": self.base["exp"], "mesos": self.base["mesos"],
               "items": {n: list(v) for n, v in self.base["items"].items()},
               "potions": {n: list(v) for n, v in self.base.get("potions", {}).items()}}
        _total(self.events, out)
        return out

    def on_map(self, mid: str, seconds: float) -> None:
        """`seconds` more of the log on map `mid`."""
        if mid and seconds > 0:
            self.maps[mid] = self.maps.get(mid, 0.0) + seconds

    def seconds(self, now: float | None = None) -> float:
        return max(0.0, (self.ended or now or time.time()) - self.start)

    def to_json(self) -> dict:
        return {"start": self.start, "ended": self.ended, "events": [e.to_json() for e in self.events],
                "base": self.base, "db_id": self.db_id, "maps": self.maps, "level": self.level}

    @classmethod
    def from_json(cls, raw) -> Log | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("start"), (int, float)):
            return None
        ended = raw.get("ended")
        lg = cls(float(raw["start"]), float(ended) if isinstance(ended, (int, float)) else None,
                 [e for r in raw.get("events") or [] if (e := Event.from_json(r))])
        base = raw.get("base")
        if isinstance(base, dict) and isinstance(base.get("items"), dict):
            pots = base.get("potions") if isinstance(base.get("potions"), dict) else {}
            lg.base = {"exp": _int(base.get("exp")), "mesos": _int(base.get("mesos")),
                       "items": {str(n): [_int(v[0]), str(v[1] or "")] for n, v in base["items"].items()
                                 if isinstance(v, list) and len(v) == 2},
                       "potions": {str(n): [_int(v[0]), str(v[1] or "")] for n, v in pots.items()
                                   if isinstance(v, list) and len(v) == 2}}
        maps = raw.get("maps")
        if isinstance(maps, dict):
            lg.maps = {str(k): float(v) for k, v in maps.items()
                       if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}
        level = raw.get("level")
        lg.level = level if isinstance(level, int) and not isinstance(level, bool) and level > 0 else None
        db_id = raw.get("db_id")
        lg.db_id = db_id if isinstance(db_id, int) and not isinstance(db_id, bool) else None
        return lg


def _int(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _total(evs: list[Event], out: dict) -> None:
    for e in evs:
        if e.kind in ("item", "potion"):
            got = out["items" if e.kind == "item" else "potions"].setdefault(e.name, [0, e.key])
            got[0] += e.amount
        else:
            out[e.kind] += e.amount


def per_hour(value: float, seconds: float) -> int | None:
    return round(value * 3600 / seconds) if seconds >= 60 else None     # under a minute a rate means nothing


def is_etc(kb, key: str) -> bool:
    """An Etc item (the inventory's Etc tab: monster drops, ores, quest items), by its KB type."""
    e = kb.get(key) if key else None
    return bool(e) and str(e.get("type", "")).startswith("Etc")


def sell_price(kb, key: str) -> int | None:
    """What an NPC pays for one (the KB's item page), None when it has no sell price."""
    from . import farm
    v = farm.value(kb, key) if key else None
    return v.price if v else None


def potion_cost(kb, key: str) -> int | None:
    """What one costs at the cheapest NPC shop the game has open (grind.potion_price, the KB's shops), None when no
    open shop sells it."""
    from . import grind
    e = kb.get(key) if key else None
    got = grind.potion_price(kb, e["name"]) if e else None
    return got[0] if got else None


@dataclass
class Summary:
    seconds: float
    exp: int
    exp_h: int | None
    mesos: int
    mesos_h: int | None
    items: list[tuple[str, int, str, int | None]]    # (name, count, key, NPC price each), most first
    picked: int                                     # items picked up, all counted
    value: int                                      # what an NPC pays for them all, plus the mesos
    value_h: int | None
    etc: int = 0                                    # Etc items picked up (is_etc) ...
    etc_value: int = 0                              # ... and what an NPC pays for them
    other_value: int = 0                            # what an NPC pays for the other items (equips, use items)
    potions: list = field(default_factory=list)     # (name, used, key, NPC shop price each), most first
    used: int = 0                                   # potions used, all counted
    potions_cost: int = 0                           # what the potions used cost at an NPC shop
    net: int = 0                                    # value - potions_cost: what the log made, all told
    net_h: int | None = None


def summarize(kb, log: Log, now: float | None = None) -> Summary:
    secs = log.seconds(now)
    sums = log.sums()
    exp, mesos, got = sums["exp"], sums["mesos"], sums["items"]
    items = []
    worth = etc = etc_value = 0
    for name, (n, key) in sorted(got.items(), key=lambda kv: (-kv[1][0], kv[0].lower())):
        price = sell_price(kb, key)
        worth += n * (price or 0)
        if is_etc(kb, key):
            etc += n
            etc_value += n * (price or 0)
        items.append((name, n, key, price))
    pots = []
    cost = 0
    for name, (n, key) in sorted(sums["potions"].items(), key=lambda kv: (-kv[1][0], kv[0].lower())):
        price = potion_cost(kb, key)
        cost += n * (price or 0)
        pots.append((name, n, key, price))
    value = worth + mesos
    net = value - cost
    return Summary(secs, exp, per_hour(exp, secs), mesos, per_hour(mesos, secs), items,
                   sum(n for _, n, _, _ in items), value, per_hour(value, secs), etc, etc_value, worth - etc_value,
                   pots, sum(n for _, n, _, _ in pots), cost, net, per_hour(net, secs))


def record(s: Summary, start: float) -> dict:
    """A finished log as the Recent logs list keeps it."""
    return {"start": start, "seconds": round(s.seconds), "exp": s.exp, "mesos": s.mesos, "picked": s.picked,
            "value": s.value, "top": [[name, n] for name, n, _, _ in s.items[:5]], "etc_value": s.etc_value,
            "potions_cost": s.potions_cost, "net": s.net}


class Store:
    """The running log and the last finished ones, per character, in loot.json (the app's data folder): a log
    outlives the tools window and an app restart."""
    path = DATA_DIR / "loot.json"

    def __init__(self):
        raw = _read_json(self.path, {"active": {}, "recent": {}})
        self._active = raw.get("active") if isinstance(raw.get("active"), dict) else {}
        self._recent = raw.get("recent") if isinstance(raw.get("recent"), dict) else {}
        _LIVE.add(self)

    def log(self, cid: str) -> Log | None:
        return Log.from_json(self._active.get(cid))

    def running(self, cid: str) -> Log | None:
        lg = self.log(cid)
        return lg if lg and not lg.ended else None

    def running_any(self) -> str | None:
        """The character whose log runs (one at a time: the game shows one character's messages)."""
        return next((cid for cid in self._active if self.running(cid)), None)

    def start(self, cid: str, kb, now: float | None = None) -> Log:
        """A new log for `cid`; one still running (another character's too) ends first."""
        for other in list(self._active):
            self.end(other, kb, now=now)
        lg = Log(start=now or time.time())
        self.put(cid, lg)
        return lg

    def put(self, cid: str, lg: Log) -> None:
        self._active[cid] = lg.to_json()
        self.save()

    def end(self, cid: str, kb, lg: Log | None = None, now: float | None = None) -> dict | None:
        """Stop the running log (`lg`: its live copy, newer than the saved one); it joins Recent logs when it
        counted something. Returns its record."""
        lg = lg or self.running(cid)
        if not lg or lg.ended:
            return None
        lg.ended = now or time.time()
        s = summarize(kb, lg, lg.ended)
        rec = record(s, lg.start) if (s.exp or s.mesos or s.picked) else None
        if rec:
            self._recent[cid] = ([rec] + [r for r in self._recent.get(cid, []) if isinstance(r, dict)])[:MAX_RECENT]
        self.put(cid, lg)
        return rec

    def drop(self, cid: str) -> None:
        """The character's last log goes (it counted nothing): the one shown is none, not an empty one."""
        self._active.pop(cid, None)
        self.save()

    def recent(self, cid: str) -> list[dict]:
        return [r for r in self._recent.get(cid, []) if isinstance(r, dict)]

    def forget(self, cid: str) -> None:
        """A deleted character's logs go with it."""
        self._active.pop(cid, None)
        self._recent.pop(cid, None)
        self.save()

    def save(self) -> None:
        _write_json(self.path, {"active": self._active, "recent": self._recent})


_LIVE: "weakref.WeakSet[Store]" = weakref.WeakSet()   # every Store in memory: a deleted character leaves each of them
_ON_FORGET: list = []                                  # weak callbacks(cid) of the scanners: a running log of it stops


def on_forget(method) -> None:
    """`method(cid)` (a bound method, held weakly) is called when a character is deleted, before its data goes."""
    _ON_FORGET.append(weakref.WeakMethod(method))


def forget(cid: str) -> None:
    """A deleted character: a scanner running its log lets it go first, then every Store in memory drops it (one
    saving later never writes it back) and loot.json is saved without it."""
    for ref in list(_ON_FORGET):
        fn = ref()
        if fn is None:
            _ON_FORGET.remove(ref)
        else:
            fn(cid)
    for st in list(_LIVE):
        st._active.pop(cid, None)
        st._recent.pop(cid, None)
    Store().forget(cid)
