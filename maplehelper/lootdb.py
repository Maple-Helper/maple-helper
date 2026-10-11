"""Every loot log, kept for good in a SQLite database (loot.sqlite in the app's data folder): each log, and each EXP,
mesos and item pickup it counted, with the time it came and what an NPC paid for the item then. loot.json keeps only
the running log and a short list of finished ones; this keeps everything, to look back at any day ("how much did I
make on Saturday?": `day`, `days`) or anything else later.

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
VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY,
    character_id TEXT NOT NULL,             -- the character's id ("" with none)
    character_name TEXT NOT NULL DEFAULT '',
    started REAL NOT NULL,                  -- unix seconds
    ended REAL                              -- NULL while it runs
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    log_id INTEGER NOT NULL REFERENCES logs(id) ON DELETE CASCADE,
    t REAL NOT NULL,                        -- unix seconds the message came
    kind TEXT NOT NULL CHECK (kind IN ('exp', 'mesos', 'item')),
    amount INTEGER NOT NULL,                -- EXP, mesos, or the item's count
    item TEXT NOT NULL DEFAULT '',          -- the item's name (the KB's, or as read when the KB has none)
    item_key TEXT NOT NULL DEFAULT '',      -- the KB item ("item/358"), '' when unknown
    npc_price INTEGER                       -- what an NPC paid for one then (the KB's page), NULL when it has none
);
CREATE INDEX IF NOT EXISTS events_t ON events(t);
CREATE INDEX IF NOT EXISTS events_log ON events(log_id);
CREATE INDEX IF NOT EXISTS logs_character ON logs(character_id, started);
"""


def _connect() -> sqlite3.Connection:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(PATH, timeout=5)
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("PRAGMA user_version").fetchone()[0] < VERSION:
        con.executescript(_SCHEMA)
        con.execute(f"PRAGMA user_version = {VERSION}")
        con.execute("PRAGMA journal_mode = WAL")     # a reader never waits on the scanner's writes
    return con


def open_log(character_id: str, character_name: str, started: float) -> int:
    """A new log's row, its id. One log runs at a time: a row still open (the app closed mid-log and never came back
    to it) ends at its last pickup."""
    with closing(_connect()) as con, con:
        con.execute("UPDATE logs SET ended = COALESCE((SELECT MAX(t) FROM events WHERE log_id = logs.id), started) "
                    "WHERE ended IS NULL")
        cur = con.execute("INSERT INTO logs (character_id, character_name, started) VALUES (?, ?, ?)",
                          (character_id, character_name, started))
        return int(cur.lastrowid)


def end_log(log_id: int, ended: float) -> None:
    with closing(_connect()) as con, con:
        con.execute("UPDATE logs SET ended = ? WHERE id = ?", (ended, log_id))


def add_events(log_id: int, events: Iterable, kb) -> None:
    """The log's pickups (lootlog.Event), each with what an NPC pays for its item now."""
    from . import farm
    rows = []
    for e in events:
        price = None
        if e.kind == "item" and e.key:
            v = farm.value(kb, e.key)
            price = v.price if v else None
        rows.append((log_id, e.t, e.kind, e.amount, e.name if e.kind == "item" else "",
                     e.key if e.kind == "item" else "", price))
    if rows:
        with closing(_connect()) as con, con:
            con.executemany("INSERT INTO events (log_id, t, kind, amount, item, item_key, npc_price) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


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
        else:
            out.items.append((item, total, key, price))
            out.item_value += worth
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
