"""The grind tracker's reads, kept by the app rather than the tools window: a session's automatic read every minute
goes on while the window is closed, and a read the player asked for lands even if the window closed meanwhile.

A read is one screenshot the chat sends to the AI (overlay.sync_profile / auto_grind_read); this object asks for it,
takes the reply (grind_read, then sync_finished) and keeps the session (grind.Store). The page only draws it."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from .. import grind


class GrindRunner(QObject):
    changed = Signal()            # a read was taken, failed or skipped: the page redraws
    auto_requested = Signal()     # time for an automatic read (the chat runs it, quietly)

    AUTO_MS = 60_000              # one read a minute while a session runs with auto-update on
    READ_WAIT = 90                # s: a read asked for this long ago is over (the chat stops one after a minute)
    # a session with no read for this long (one left running since yesterday) doesn't start reading again by itself
    # when the app starts or the character comes back: Update resumes it. The plan's quota isn't spent on a session
    # nobody plays. (A running timer goes on through a game closed for a while: those ticks cost nothing.)
    IDLE = 20 * 60

    def __init__(self, kb, profiles, settings):
        super().__init__()
        self.kb, self.profiles, self.settings = kb, profiles, settings
        self.store = grind.Store()
        self.pending: tuple | None = None       # (what, character id, asked at): "start" | "update" | "end" | "auto"
        self.got = None                          # the AI's reply to it (character id, profile_update, grind)
        self.note: list[str] = []                # what the last read the player asked for came to (string keys)
        self.waiting = ""                        # an automatic read found no game to read: "no_game" | "covered"
        self.timer = QTimer(self, interval=self.AUTO_MS, timeout=self._tick)

    # ------------------------------------------------------------------ state

    @property
    def auto(self) -> bool:
        return self.settings["grind_auto"] is not False

    def set_auto(self, on: bool) -> None:
        self.settings["grind_auto"] = bool(on)
        self.settings.save()
        self.waiting = ""
        self.sync()

    def busy(self) -> tuple | None:
        """The read on its way, or None. One whose reply never came (the chat had no window for it, a crash) is
        dropped after READ_WAIT, or the buttons stayed off for good."""
        if self.pending and time.time() - self.pending[2] > self.READ_WAIT:
            self.pending = None
        return self.pending

    def idle(self, s) -> bool:
        """Paused: auto-update is on, but the timer isn't running for this session's long silence."""
        return not self.timer.isActive() and time.time() - s.reads[-1].t > self.IDLE

    def sync(self) -> None:
        """The minute timer runs only while the active character has a session running and auto-update is on (and,
        to start, a read in the last IDLE minutes)."""
        c = self.profiles.active
        s = self.store.running(c.id) if c else None
        run = bool(s and self.auto and not self.idle(s))
        if run and not self.timer.isActive():
            self.timer.start()
        elif not run:
            self.timer.stop()
            self.waiting = ""

    def stop(self) -> None:
        self.timer.stop()

    # ------------------------------------------------------------------ reads

    def ask(self, what: str, cid: str, monster: str = "") -> str | None:
        """A read the player asked for (Start / Update / End): "read" when the chat should read the game now,
        "joined" when the minute's automatic read already on its way serves it (Update / End pressed meanwhile: no
        second read, and the buttons never wait on a read they didn't ask for), None while another is on its way."""
        p = self.busy()
        if p and p[0] == "auto" and p[1] == cid and what != "start":
            self.pending, self.note = (what, cid, p[2], monster), []
            return "joined"
        if p:
            return None
        self.pending, self.got, self.note = (what, cid, time.time(), monster), None, []
        return "read"

    def _tick(self) -> None:
        c = self.profiles.active
        s = self.store.running(c.id) if c else None
        if not (s and self.auto):
            self.sync()
            self.changed.emit()        # the page says why it stopped
            return
        if self.busy():
            return                  # the last read is still on its way: this minute is skipped, not queued
        self.pending, self.got = ("auto", c.id, time.time(), ""), None
        self.auto_requested.emit()

    def grind_read(self, r) -> None:
        if self.pending:
            self.got = r

    def skipped(self, reason: str) -> None:
        """An automatic read that didn't run: the chat was busy (next minute), or the game is closed or covered."""
        if self.pending and self.pending[0] == "auto":
            self.pending = None
            self.waiting = reason if reason in ("no_game", "covered") else self.waiting
            self.changed.emit()

    def sync_done(self, ok: bool) -> None:
        if not self.busy():
            return
        self.take(ok)
        self.sync()
        self.changed.emit()

    def take(self, ok: bool) -> None:
        """The read's reply into the session (see the page for what each note says)."""
        what, cid, _, monster = self.pending
        got, self.pending, self.got = self.got, None, None
        c = self.profiles.active
        if not c or c.id != cid:
            # another character since the press: the screen may show them, so the read isn't added; an End still
            # ends the session it was pressed for, without a reading, and a Start / Update says it didn't take
            # (it was dropped without a word, TL1-10)
            if what == "end":
                rec = self.store.end(cid, self.kb)
                self.note = ["grind_end_no_read", "grind_saved" if rec else "grind_not_saved"]
            elif what != "auto":
                self.note = ["exp_failed"]
            return
        now = time.time()
        if ok and got and got[0] == cid:
            r = grind.reading(now, got[1], got[2])
            if r.exp_pct is not None and r.level is None:
                r.level = c.level              # the HUD's level unread, its bar read: the level hasn't changed
        elif ok:
            # a read already on its way when this one was asked (the ⟳ on the card): its profile is all there is
            r = grind.reading(now, {"level": c.level, "exp_percent": c.exp_pct}, None)
        else:
            r = None
        store = self.store
        if what == "auto":
            if r is not None and (r.exp_pct is not None or r.inventory):
                store.add(cid, r)
                self.waiting = ""
            return                             # a failed automatic read says nothing: the next minute tries again
        if what == "start":
            if r is None or r.exp_pct is None:
                self.note = ["exp_failed" if r is None else "grind_no_exp"]
                return
            store.start(cid, r, monster, picked=bool(monster))
            self.note = ["grind_started_ok"] + ([] if r.inventory else ["grind_no_inv"])
            return
        if r is not None:
            store.add(cid, r)
        if what == "update":
            if r is None:
                self.note = ["exp_failed"]
            elif r.exp_pct is None:
                self.note = ["grind_no_exp"]
            else:
                self.note = ["grind_updated"] + ([] if r.inventory else ["grind_no_inv"])
            return
        rec = store.end(cid, self.kb)
        self.note = ([] if r is not None else ["grind_end_no_read"]) + ["grind_saved" if rec else "grind_not_saved"]
