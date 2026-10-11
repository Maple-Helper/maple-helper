"""The loot log's reads: the box around the game's pickup messages, read every READ_INTERVAL seconds while a log runs.

A QTimer ticks on the GUI thread; the grab and the OCR run on a worker thread (the chat never freezes on them), and
the read lines come back through a signal to lootlog.Tracker, which turns them into the log's events. Nothing reads
while no log runs, or with no box drawn. The running log is kept here, live, and saved to loot.json every few
seconds and when it ends (a log outlives the tools window and an app restart)."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from .. import capture, lootlog
from ..kb import KnowledgeBase
from ..store import Profiles, Settings

log = logging.getLogger("maplehelper")

READ_INTERVAL = 0.5     # s: a message stays ~6 s and a kill writes up to 3 at once; two reads a second follow them
SAVE_EVERY_MS = 5000    # the running log goes to disk this often while it counts (and at once when it ends)


def valid_region(region) -> bool:
    """A box the picker could have drawn: ints (or floats) with a size, not a stray dict."""
    return (isinstance(region, dict)
            and all(isinstance(region.get(k), (int, float)) and not isinstance(region.get(k), bool)
                    for k in ("x", "y", "w", "h"))
            and region["w"] > 0 and region["h"] > 0)


class LootScanner(QObject):
    changed = Signal()            # the log counted something, started or ended
    _read_done = Signal(object, float)   # (lines read or None: no reader, the read's time), from the worker thread
    _failed = Signal(str)

    def __init__(self, kb: KnowledgeBase, profiles: Profiles, settings: Settings, parent=None):
        super().__init__(parent)
        self.kb = kb
        self._profiles = profiles
        self._settings = settings
        self.store = lootlog.Store()
        self._tracker = lootlog.Tracker(lootlog.item_names(kb))
        self._cid: str | None = None          # whose log runs
        self._log: lootlog.Log | None = None  # ... live
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._save_timer = QTimer(self, singleShot=True)
        self._save_timer.timeout.connect(self._save)
        self._guard = threading.Lock()
        self._reading = False
        self._logged: set[str] = set()
        self.ocr_missing = False              # the reader won't load: the page says so
        self.last_read: float | None = None   # when a read last found a message line
        self._read_done.connect(self._deliver)
        self._failed.connect(self._complain)
        self.resume()

    # ---------------------------------------------------------------- whose log, which box

    def cid(self) -> str:
        """The active character's id ("" with none: the log works without a character)."""
        c = self._profiles.active
        return c.id if c else ""

    def region(self) -> dict | None:
        r = self._settings["loot_region"]
        return {k: int(r[k]) for k in ("x", "y", "w", "h")} if valid_region(r) else None

    def set_kb(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self._tracker = lootlog.Tracker(lootlog.item_names(kb))

    def resume(self) -> None:
        """A log running when the app last closed goes on."""
        cid = self.store.running_any()
        if cid is not None:
            self._cid, self._log = cid, self.store.running(cid)
        self.restart()

    def restart(self) -> None:
        """The box changed (or a log started / ended): read from it, from a clean start, while a log runs."""
        self._tracker.reset()
        if self._log is not None and self.region() is not None:
            self._timer.start(int(READ_INTERVAL * 1000))
        else:
            self._timer.stop()

    def stop(self) -> None:
        """No read while the app goes; the running log is saved as it is (it goes on at the next start)."""
        self._timer.stop()
        self._save()

    # ---------------------------------------------------------------- the log

    def log_for(self, cid: str) -> lootlog.Log | None:
        """A character's log: the live one while it runs, else the last one saved."""
        if cid == self._cid and self._log is not None:
            return self._log
        return self.store.log(cid)

    def running(self, cid: str) -> bool:
        return cid == self._cid and self._log is not None

    def start(self) -> None:
        if self._log is not None:
            self.end()
        cid = self.cid()
        self._cid, self._log = cid, self.store.start(cid, self.kb)
        self.last_read = None
        self.restart()
        self.changed.emit()

    def end(self) -> dict | None:
        if self._log is None:
            return None
        rec = self.store.end(self._cid, self.kb, self._log)
        self._cid, self._log = None, None
        self._save_timer.stop()
        self.restart()
        self.changed.emit()
        return rec

    def _save(self) -> None:
        if self._log is not None and self._cid is not None:
            self.store.put(self._cid, self._log)

    # ---------------------------------------------------------------- reading

    def _tick(self) -> None:
        region = self.region()
        if region is None or self._log is None:
            return
        with self._guard:
            if self._reading:
                return          # the last read is still going: skip this tick, don't pile up
            self._reading = True
        threading.Thread(target=self._read, args=(region,), daemon=True).start()

    def _read(self, region: dict) -> None:
        try:
            img = capture.grab_image(region["x"], region["y"], region["w"], region["h"])
            rows = lootlog.ocr_rows(np.asarray(img))
        except Exception as e:      # noqa: BLE001 - a bad read is a missed read, never a crash
            with self._guard:
                self._reading = False
            self._failed.emit(f"{type(e).__name__}: {e}")
            return
        with self._guard:
            self._reading = False
        self._read_done.emit(rows, time.time())

    def _deliver(self, rows, at: float) -> None:
        if self._log is None:
            return
        self.ocr_missing = rows is None
        if not rows:
            return
        evs = self._tracker.read(rows, at)
        if any(lootlog.parse(r[0]) for r in rows):
            self.last_read = at
        if evs:
            self._log.add(evs)
            log.debug("loot: %s", ", ".join(f"{e.kind} {e.amount} {e.name}".strip() for e in evs))
            if not self._save_timer.isActive():
                self._save_timer.start(SAVE_EVERY_MS)
        # (a later read may correct an earlier event's value: redraw on every read with a message in it)
        if evs or self.last_read == at:
            self.changed.emit()

    def _complain(self, error: str) -> None:
        if error not in self._logged:
            self._logged.add(error)
            log.warning("loot read failed: %s", error)
