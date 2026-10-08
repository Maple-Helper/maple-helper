"""Where the player is, refreshed every few seconds from the game's minimap (the box the player drew around it).

A QTimer ticks on the GUI thread; the read itself (the screenshot, then reading the minimap's title for the map
and aligning its picture for the dot) runs on a worker thread, so the chat never freezes on it. The answer comes
back through a signal, and the chat's character card and the map windows read it from ui/location.LOCATION. No box
drawn yet: nothing runs."""
from __future__ import annotations

import logging
import math
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .. import capture, minimap, routes
from ..kb import KnowledgeBase
from ..store import Settings
from .location import LOCATION

log = logging.getLogger("maplehelper")

MIN_INTERVAL = 0.2      # s: faster only burned CPU rereading the same picture
MAX_INTERVAL = 60.0     # s: slower, and the "where you are" line went stale
DEFAULT_INTERVAL = 1.0  # s, the setting's own default (store.DEFAULT_SETTINGS)
CONFIRM = 2             # reads in a row that must name a new map before the player counts as moved


class MinimapScanner(QObject):
    """Reads the drawn minimap box every few seconds and reports it through LOCATION.

    The Locator is built lazily once per KB and kept between reads (it holds the OCR engine, the header rows and
    its lock on the picture); a KB update drops it so the next read rebuilds it. A read still going when the timer
    ticks skips that tick (no pile-up). A box with no recognizable title backs off (a wasted read costs most of a
    second of CPU); a hit reads every interval again, as does a restart (a new box)."""

    _found = Signal(object)       # Here | None: a read's answer, from the worker thread
    _failed = Signal(str)         # a read's error as text ("Kind: message"), from the worker thread

    def __init__(self, kb: KnowledgeBase, settings: Settings, parent=None):
        super().__init__(parent)
        self._kb = kb
        self._settings = settings
        self._locator: minimap.Locator | None = None
        self._graph: routes.Graph | None = None
        self._region: dict | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._guard = threading.Lock()
        self._reading = False
        self._cooldown_until = 0.0    # monotonic deadline before the next read (0: no backoff)
        self._miss_since: float | None = None   # when the current run of misses began (None: the last read hit)
        self._last_map: str | None = None     # the last map logged at INFO (None: none yet)
        self._logged_errors: set[str] = set()  # error texts already logged: each distinct one logs once
        self._pending: tuple[str | None, int] = (None, 0)   # another map read, and how many reads in a row
        self._miss_logged = False             # the current run of misses was logged once already
        self._found.connect(self._deliver)
        self._failed.connect(self._complain)

    def set_kb(self, kb: KnowledgeBase) -> None:
        """A KB update: the locator is rebuilt from it on the next read."""
        self._kb = kb
        self._locator = None
        self._graph = None

    def reset_locator(self) -> None:
        """Drop the locator's lock on the picture (a newly drawn box): the next read locks on again."""
        if self._locator is not None:
            try:
                self._locator.reset()
            except Exception as e:  # noqa: BLE001 - a failed reset just locks on again at the next read
                log.debug("minimap locator reset failed: %r", e)

    def restart(self) -> None:
        """Settings changed (the box, the interval): pick them up. No box: stop scanning and forget the player."""
        region = self._settings["minimap_region"]
        # ints for grab_image (a hand-edited float still reads the same box, not an error every interval)
        self._region = {k: int(region[k]) for k in ("x", "y", "w", "h")} if self._valid(region) else None
        self.reset_locator()
        self._cooldown_until = 0.0     # a new box or interval: read right away, don't keep an old backoff
        self._miss_since = None
        self._pending = (None, 0)
        if self._region is None:
            self._timer.stop()
            LOCATION.set(None)
            LOCATION.set_state("")
            return
        self._timer.start(int(self._interval() * 1000))

    def stop(self) -> None:
        """No read while the app goes (a read on its way finishes on its own)."""
        self._timer.stop()

    @staticmethod
    def _valid(region) -> bool:
        """A box the picker could have drawn: ints (or floats) with a size, not a stray dict."""
        return (isinstance(region, dict)
                and all(isinstance(region.get(k), (int, float)) and not isinstance(region.get(k), bool)
                        for k in ("x", "y", "w", "h"))
                and region["w"] > 0 and region["h"] > 0)

    def _interval(self) -> float:
        """The setting's seconds between reads, inside [MIN_INTERVAL, MAX_INTERVAL]."""
        try:
            interval = float(self._settings["minimap_scan_interval"])
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL
        if not math.isfinite(interval):
            return DEFAULT_INTERVAL
        return min(MAX_INTERVAL, max(MIN_INTERVAL, interval))

    def _tick(self) -> None:
        if self._region is None:
            return
        if time.monotonic() < self._cooldown_until:
            return          # the box shows no minimap: a cold search costs seconds of CPU, don't run it every tick
        with self._guard:
            if self._reading:
                return          # the last read is still going: skip this tick, don't pile up
            self._reading = True
        threading.Thread(target=self._read, args=(dict(self._region),), daemon=True).start()

    def _locator_for(self) -> tuple:
        if self._locator is None:
            self._graph = routes.of(self._kb)
            self._locator = minimap.Locator(self._graph)
        return self._locator, self._graph

    def _read(self, region: dict) -> None:
        try:
            locator, _ = self._locator_for()
            img = capture.grab_image(region["x"], region["y"], region["w"], region["h"])
            here = locator.locate(img)
        except Exception as e:      # noqa: BLE001 - a bad read is "unknown", never a crash
            with self._guard:
                self._reading = False
            self._failed.emit(f"{type(e).__name__}: {e}")
            return
        with self._guard:
            self._reading = False
        self._found.emit(here)

    def _grace(self) -> float:
        """How long a known map outlives reads that find no title: max(3 s, 3 reads). A single frame can hide it
        (a chat bubble over the header, the loading screen between two maps), and the card flipped to "not
        recognized" and back on its own while the player stood still (live)."""
        return max(3.0, 3.0 * self._interval())

    def _miss(self) -> None:
        """A read with no recognizable title. A known map is never dropped for it (the owner's, 2026-10-08: the
        card flipped to "not recognized" and back every few seconds and reset the way shown mid-walk): it stays
        until a read names another map. Misses past the grace (or with no map known yet) only slow the reads to
        max(2 s, 2 x interval) with a map kept, max(5 s, 5 x interval) with none: a wasted read costs most of a second of CPU; without this it ran back-to-back
        forever. With no map known at all the card says "not recognized"."""
        now = time.monotonic()
        if self._miss_since is None:
            self._miss_since = now
        if LOCATION.here is not None and now - self._miss_since < self._grace():
            return
        if LOCATION.here is not None:
            # a known map kept: slow down less, so a real map change after a long run of misses shows soon
            self._cooldown_until = now + max(2.0, 2.0 * self._interval())
            if not self._miss_logged:
                self._miss_logged = True
                log.info("minimap: no title for %.1f s, keeping the last map", now - self._miss_since)
            return
        self._cooldown_until = now + max(5.0, 5.0 * self._interval())
        LOCATION.set_state("unknown")

    def _deliver(self, here) -> None:
        """A read's answer, back on the GUI thread. Another map than the known one counts only once CONFIRM reads
        in a row name it: one misread (the street line read alone, "Victoria Road", is a map's name too) must not
        move the player and reset the way shown."""
        if here is None:
            self._miss()
            return
        self._miss_since = None
        self._miss_logged = False
        self._cooldown_until = 0.0     # found: back to reading every interval
        known = LOCATION.here
        if known is not None and here.map != known.map:
            seen = self._pending[1] + 1 if self._pending[0] == here.map else 1
            self._pending = (here.map, seen)
            if seen < CONFIRM:
                return
        self._pending = (None, 0)
        LOCATION.set(here)
        LOCATION.set_state("")
        if here.map != self._last_map:
            self._last_map = here.map
            try:
                name = self._graph.name(here.map) if self._graph is not None else here.map
            except Exception:  # noqa: BLE001 - a name lookup must not lose the location
                name = here.map
            log.info("minimap: %s", name)

    def _complain(self, error: str) -> None:
        """A read's error, back on the GUI thread: logged once per distinct error, then a miss like an unrecognized
        read (often the same cause)."""
        if error not in self._logged_errors:
            if len(self._logged_errors) > 100:
                self._logged_errors.clear()     # pathological: new errors every read must not grow forever
            self._logged_errors.add(error)
            log.warning("minimap read failed: %s", error)
        self._miss()
