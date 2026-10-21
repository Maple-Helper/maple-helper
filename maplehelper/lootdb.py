"""Every loot log, kept for good in a SQLite database (loot.sqlite in the app's data folder): each log, and each EXP,
mesos and item pickup it counted, with the time it came, the map it came on and what an NPC paid for the item then;
the potions it saw used (and what an NPC shop asked for them then); how long it ran on each map. loot.json keeps
only the running log and a short list of finished ones; this keeps everything, to look back at any day ("how much
did I make on Saturday?": `day`, `days`), at past logs (`sessions`) and at which map paid best (`spots`).

Writes come from the loot scanner on the GUI thread, a few rows every few seconds; each call opens its own
connection (sqlite3 connections don't cross threads), so a reader on another thread is fine too. No Qt."""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field

from .store import DATA_DIR

log = logging.getLogger("maplehelper")

PATH = DATA_DIR / "loot.sqlite"
VERSION = 2
_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL,             -- the character's id ("" with none)
    character_name TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL,                  -- unix seconds
    ended REAL,                             -- NULL while it runs
    level INTEGER                           -- the character's level when it started, NULL when unknown
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    t REAL NOT NULL,                        -- unix seconds the message came
    kind TEXT NOT NULL CHECK (kind IN ('exp', 'mesos', 'item', 'potion')),
    amount INTEGER NOT NULL,                -- EXP, mesos, the item's count, or the potions used
    item TEXT NOT NULL DEFAULT '',          -- the item's name (the KB's, or as read when the KB has none)
    item_key TEXT NOT NULL DEFAULT '',      -- the KB item ("item/358"), '' when unknown
    npc_price INTEGER,                      -- what an NPC paid for one then (the KB's page), NULL when it has none;
                                            -- a potion: what an NPC shop asked for one then
    map TEXT NOT NULL DEFAULT ''            -- the map it came on (a routes map id), '' when unknown
);
CREATE TABLE IF NOT EXISTS spots (
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    map TEXT NOT NULL,                      -- a routes map id
    seconds REAL NOT NULL,                  -- how long the log ran on it
    PRIMARY KEY (log_id, map)
);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
CREATE INDEX IF NOT EXISTS events_log ON events(log_id);
CREATE INDEX IF NOT EXISTS logs_character ON logs(character_id, started);
"""


def _upgrade(con: sqlite3.Connection, version: int) -> None:
    """A version-1 database to this one, its rows kept: events gets "potion" and the map (SQLite can't change a CHECK,
    so the table is copied), logs the level, and spots is new."""
    if version >= 1:
        con.execute("PRAGMA foreign_keys = OFF")
        con.executescript("""
BEGIN;
ALTER TABLE events RENAME TO events_v1;
ALTER TABLE logs ADD COLUMN level INTEGER;
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    t REAL NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exp', 'mesos', 'item', 'potion')),
    amount INTEGER NOT NULL,
    item TEXT NOT NULL DEFAULT '',
    item_key TEXT NOT NULL DEFAULT '',
    npc_price INTEGER,
    map TEXT NOT NULL DEFAULT ''
);
INSERT INTO events (id, log_id, t, kind, amount, item, item_key, npc_price)
    SELECT id, log_id, t, kind, amount, item, item_key, npc_price FROM events_v1;
DROP TABLE events_v1;
DROP INDEX IF EXISTS events_t;
DROP INDEX IF EXISTS events_log;
COMMIT;
""")
        con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_SCHEMA)


def _connect() -> sqlite3.Connection:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATH, timeout=5)
    con.execute("PRAGMA foreign_keys = ON")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < VERSION:
        _upgrade(con, version)
        con.execute(f"PRAGMA user_version = {VERSION}")
        con.execute("PRAGMA journal_mode = WAL")     # a reader never waits on the scanner's writes
    return con


def open_log(character_id: str, character_name: str, started: float, level: int | None = None) -> int:
    """A new log's row, its id. One log runs at a time: a row still open (the app closed mid-log and never came back
    to it) ends at its last pickup."""
    with closing(_connect()) as con, con:
        con.execute("UPDATE logs SET ended = COALESCE((SELECT MAX(t) FROM events WHERE log_id = logs.id), started) "
                    "WHERE ended IS NULL")
        cur = con.execute("INSERT INTO logs (character_id, character_name, started, level) VALUES (?, ?, ?, ?)",
                          (character_id, character_name, started, level))
        return int(cur.lastrowid)


def end_log(log_id: int, ended: float) -> None:
    with closing(_connect()) as con, con:
        con.execute("UPDATE logs SET ended = ? WHERE id = ?", (ended, log_id))


def add_events(log_id: int, events: Iterable, kb) -> None:
    """The log's pickups and potions used (lootlog.Event), each with what an NPC pays for its item now (a potion:
    what an NPC shop asks for it)."""
    from . import lootlog
    rows = []
    for e in events:
        price = None
        named = e.kind in ("item", "potion")
        if e.kind == "item" and e.key:
            price = lootlog.sell_price(kb, e.key)
        elif e.kind == "potion" and e.key:
            price = lootlog.potion_cost(kb, e.key)
        rows.append((log_id, e.t, e.kind, e.amount, e.name if named else "", e.key if named else "", price,
                     getattr(e, "map", "") or ""))
    if rows:
        with closing(_connect()) as con, con:
            con.executemany("INSERT INTO events (log_id, t, kind, amount, item, item_key, npc_price, map) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def set_spots(log_id: int, maps: dict[str, float]) -> None:
    """How long the log ran on each map so far (lootlog.Log.maps), replacing what was saved before."""
    rows = [(log_id, m, float(sec)) for m, sec in maps.items() if m and sec > 0]
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM spots WHERE log_id = ?", (log_id,))
        con.executemany("INSERT INTO spots (log_id, map, seconds) VALUES (?, ?, ?)", rows)


def delete_log(log_id: int) -> None:
    """A log that counted nothing (started and stopped at once): it isn't history."""
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM logs WHERE id = ?", (log_id,))


def forget(character_id: str) -> None:
    """A deleted character's logs go with it."""
    if not PATH.exists():
        return
    with closing(_connect()) as con, con:
        con.execute("DELETE FROM logs WHERE character_id = ?", (character_id,))


# ---------------------------------------------------------------- looking back

@dataclass
class Day:
    date: dt.date
    seconds: float = 0.0                    # time logs ran that day
    exp: int = 0
    mesos: int = 0
    items: list[tuple[str, int, str, int | None]] = field(default_factory=list)   # (name, count, key, NPC price)
    item_value: int = 0                     # what an NPC paid for that day's items
    potions_cost: int = 0                   # what the potions used that day cost at an NPC shop

    @property
    def value(self) -> int:
        """All the day made: the mesos picked up and what its items sell to an NPC for."""
        return self.mesos + self.item_value


def _bounds(date: dt.date) -> tuple[float, float]:
    """The local day's [start, end) in unix seconds (a daylight-saving day is 23 or 25 hours)."""
    start = dt.datetime.combine(date, dt.time.min)
    return start.timestamp(), (start + dt.timedelta(days=1)).timestamp()


def day(character_id: str, date: dt.date, now: float | None = None) -> Day:
    """What the character's logs counted on a (local) day; a log over midnight counts on both days, each its part."""
    lo, hi = _bounds(date)
    out = Day(date)
    if not PATH.exists():
        return out
    now = now or time.time()
    with closing(_connect()) as con:
        for started, ended in con.execute(
                "SELECT started, ended FROM logs WHERE character_id = ? AND started < ? AND COALESCE(ended, ?) > ?",
                (character_id, hi, now, lo)):
            out.seconds += max(0.0, min(ended if ended is not None else now, hi) - max(started, lo))
        rows = con.execute(
            "SELECT e.kind, e.item, e.item_key, SUM(e.amount), MAX(e.npc_price), SUM(e.amount * COALESCE(e.npc_price, 0)) "
            "FROM events e JOIN logs l ON l.id = e.log_id "
            "WHERE l.character_id = ? AND e.t >= ? AND e.t < ? GROUP BY e.kind, e.item, e.item_key",
            (character_id, lo, hi)).fetchall()
    for kind, item, key, total, price, worth in rows:
        if kind == "exp":
            out.exp += total
        elif kind == "mesos":
            out.mesos += total
        elif kind == "item":
            out.items.append((item, total, key, price))
            out.item_value += worth
        else:
            out.potions_cost += worth
    out.items.sort(key=lambda r: (-r[1], r[0].lower()))
    return out


def days(character_id: str, limit: int = 30) -> list[Day]:
    """The character's last `limit` (local) days with any pickup, newest first."""
    if not PATH.exists():
        return []
    with closing(_connect()) as con:
        dates = [dt.date.fromisoformat(r[0]) for r in con.execute(
            "SELECT DISTINCT date(e.t, 'unixepoch', 'localtime') AS d FROM events e JOIN logs l ON l.id = e.log_id "
            "WHERE l.character_id = ? ORDER BY d DESC LIMIT ?", (character_id, limit))]
    return [day(character_id, d) for d in dates]


# ---------------------------------------------------------------- past logs and the maps they ran on

@dataclass
class Totals:
    """What some logs (or a part of them) counted: the sums the history and the comparison are made of."""
    seconds: float = 0.0
    exp: int = 0
    mesos: int = 0
    etc_value: int = 0                      # what an NPC pays for the Etc items picked up
    other_value: int = 0                    # ... for the other items
    potions_cost: int = 0                   # what the potions used cost at an NPC shop
    potions: int = 0                        # potions used
    potions_seen: bool = False              # the quick slots were read: potions_cost is measured, not just 0

    @property
    def value(self) -> int:
        return self.mesos + self.etc_value + self.other_value

    @property
    def net(self) -> int:
        return self.value - self.potions_cost

    def per_hour(self, v: float) -> int | None:
        from .lootlog import RATE_FROM_S
        return round(v * 3600 / self.seconds) if self.seconds >= RATE_FROM_S else None


@dataclass
class Session(Totals):
    """A finished (or running) log."""
    id: int = 0
    started: float = 0.0
    ended: float | None = None
    level: int | None = None
    maps: dict[str, float] = field(default_factory=dict)     # map id -> seconds on it, longest first
    items: list[tuple[str, int, str, int | None]] = field(default_factory=list)  # (name, count, key, price)
    potion_list: list[tuple[str, int, str, int | None]] = field(default_factory=list)  # (name, used, key, cost)
    exp_counts: dict[int, int] = field(default_factory=dict)     # an EXP message's amount -> how many (its kills)


@dataclass
class Spot(Totals):
    """One map, over every log that ran on it: what it gave per hour there."""
    map: str = ""
    sessions: int = 0
    levels: tuple[int, int] | None = None   # the lowest and highest level a log there started at
    last: float = 0.0                       # when a log last ran there


def _add_event(tot: Totals, kind: str, amount: int, worth: int, etc: bool) -> None:
    """amount: EXP, mesos, items or potions; worth: what the items sold / the potions cost, all of them."""
    if kind == "exp":
        tot.exp += amount
    elif kind == "mesos":
        tot.mesos += amount
    elif kind == "item":
        if etc:
            tot.etc_value += worth
        else:
            tot.other_value += worth
    elif kind == "potion":
        tot.potions_cost += worth
        tot.potions += amount
        tot.potions_seen = True


def _etc_keys(con, kb) -> set[str]:
    from . import lootlog
    keys = {k for (k,) in con.execute("SELECT DISTINCT item_key FROM events WHERE kind = 'item' AND item_key != ''")}
    return {k for k in keys if kb is not None and lootlog.is_etc(kb, k)}


def sessions(character_id: str, kb=None, limit: int = 50, now: float | None = None) -> list[Session]:
    """The character's last `limit` logs that counted something, newest first."""
    if not PATH.exists():
        return []
    now = now or time.time()
    out: list[Session] = []
    with closing(_connect()) as con:
        etc = _etc_keys(con, kb)
        logs = con.execute(
            "SELECT id, started, ended, level FROM logs WHERE character_id = ? AND EXISTS "
            "(SELECT 1 FROM events WHERE log_id = logs.id) ORDER BY started DESC LIMIT ?",
            (character_id, limit)).fetchall()
        for lid, started, ended, level in logs:
            s = Session(id=lid, started=started, ended=ended, level=level)
            s.seconds = max(0.0, (ended if ended is not None else now) - started)
            items: dict[tuple[str, str], list] = {}
            for kind, item, key, amount, price, worth in con.execute(
                    "SELECT kind, item, item_key, SUM(amount), MAX(npc_price), SUM(amount * COALESCE(npc_price, 0)) "
                    "FROM events WHERE log_id = ? GROUP BY kind, item, item_key", (lid,)):
                _add_event(s, kind, amount, worth, key in etc)
                if kind == "item":
                    items[(item, key)] = [amount, price]
                elif kind == "potion":
                    s.potion_list.append((item, amount, key, price))
            s.items = sorted(((n, c, k, p) for (n, k), (c, p) in items.items()), key=lambda r: (-r[1], r[0].lower()))
            s.potion_list.sort(key=lambda r: (-r[1], r[0].lower()))
            s.maps = dict(con.execute("SELECT map, seconds FROM spots WHERE log_id = ? ORDER BY seconds DESC",
                                      (lid,)).fetchall())
            s.exp_counts = dict(con.execute("SELECT amount, COUNT(*) FROM events WHERE log_id = ? AND kind = 'exp' "
                                            "GROUP BY amount", (lid,)).fetchall())
            out.append(s)
    return out


def spots(character_id: str, kb=None) -> list[Spot]:
    """Every map the character's logs ran on, with what each gave there (an event counts on the map it came on, the
    time from spots), most time first. Logs from before the map was kept, or with the minimap unread, have none."""
    if not PATH.exists():
        return []
    by: dict[str, Spot] = {}
    with closing(_connect()) as con:
        etc = _etc_keys(con, kb)
        for mid, seconds, n, lo, hi, last in con.execute(
                "SELECT s.map, SUM(s.seconds), COUNT(DISTINCT s.log_id), MIN(l.level), MAX(l.level), MAX(l.started) "
                "FROM spots s JOIN logs l ON l.id = s.log_id WHERE l.character_id = ? GROUP BY s.map",
                (character_id,)):
            by[mid] = Spot(seconds=seconds, map=mid, sessions=n, levels=(lo, hi) if lo else None, last=last or 0.0)
        for mid, kind, key, amount, worth in con.execute(
                "SELECT e.map, e.kind, e.item_key, SUM(e.amount), SUM(e.amount * COALESCE(e.npc_price, 0)) FROM events e "
                "JOIN logs l ON l.id = e.log_id WHERE l.character_id = ? AND e.map != '' "
                "GROUP BY e.map, e.kind, e.item, e.item_key", (character_id,)):
            sp = by.get(mid)
            if sp is not None:
                _add_event(sp, kind, amount, worth, key in etc)
    return sorted(by.values(), key=lambda sp: -sp.seconds)
