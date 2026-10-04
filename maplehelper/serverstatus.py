"""Game server status, live from NiaMeowDB's server-status page (meowdb.com/msclassic/server-status).

This one is NOT knowledge-base data: the KB is rebuilt once a night, and a maintenance starts and ends within hours,
so a nightly copy would always be stale. The chat asks the same public endpoint the site's page reads, lightly (the
header dot, ui/serverdot.py: every few minutes while the chat is open, backing off when the site can't be reached,
never while it is closed).

The endpoint answers {"verdict", "notice", "foundersAt", "grandAt", ...}: the verdict is NiaMeowDB's reading of
Nexon's maintenance notices and players' "can't log in" reports:
    prelaunch  the game isn't open yet            checking  not decided yet
    up         servers up                         scheduled up, a maintenance is scheduled
    maintenance  in maintenance                   rising    players report problems
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass

URL = "https://meowdb.com/msclassic/api/server-status"
PAGE = "https://meowdb.com/msclassic/server-status"
UA = "Maple Helper (https://github.com/Maple-Helper/maple-helper)"

# the dot's state per verdict: green (up), orange (maintenance or players report trouble), grey (unknown / not open)
STATES = {"up": "up", "scheduled": "up", "maintenance": "maintenance", "rising": "issues",
          "prelaunch": "prelaunch", "checking": "unknown"}


@dataclass
class Status:
    state: str                      # up | maintenance | issues | prelaunch | unknown
    verdict: str = ""               # the site's own word (STATES' keys)
    notice_title: str = ""          # Nexon's newest maintenance notice, when the site has one
    notice_url: str = ""
    notice_start: float | None = None      # unix seconds
    notice_end: float | None = None
    notice_done: bool = False
    opens_at: float | None = None   # before launch: when the game opens (Founder's Access, then Grand Launch)
    checked: float = 0.0            # when this app asked

    @property
    def scheduled(self) -> bool:
        """A maintenance is announced and still ahead."""
        return bool(self.notice_start and not self.notice_done and self.notice_start > self.checked)


def _secs(v) -> float | None:
    if isinstance(v, (int, float)) and v > 0:
        return v / 1000 if v > 1e11 else float(v)
    return None


def parse(data, now: float | None = None) -> Status:
    """A Status from the endpoint's JSON; an answer without a known verdict is "unknown" (never a guess)."""
    now = time.time() if now is None else now
    if not isinstance(data, dict):
        return Status("unknown", checked=now)
    verdict = str(data.get("verdict") or "")
    st = Status(STATES.get(verdict, "unknown"), verdict, checked=now)
    notice = data.get("notice")
    if isinstance(notice, dict):
        st.notice_title = str(notice.get("title") or "")
        url = str(notice.get("url") or "")
        st.notice_url = url if url.startswith("https://") else ""
        st.notice_start, st.notice_end = _secs(notice.get("startAt")), _secs(notice.get("endAt"))
        st.notice_done = bool(notice.get("completed"))
    if verdict == "prelaunch":
        st.opens_at = next((t for t in (_secs(data.get("foundersAt")), _secs(data.get("grandAt"))) if t and t > now),
                           None)
    return st


def fetch(timeout: float = 8) -> Status | None:
    """The status now; None when the site can't be reached or answers nonsense."""
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:          # noqa: BLE001 - offline, DNS, a timeout, an HTML error page: all "don't know"
        return None
    return parse(data) if isinstance(data, dict) else None


def transition(before: Status | None, now: Status) -> str | None:
    """"started" when the servers went into maintenance, "ended" when they came back from it, else None.
    The first status of a session counts only for a maintenance in progress (the player wants to know), never for
    "ended" (nothing ended that this session saw start)."""
    if now.state == "maintenance" and (before is None or before.state != "maintenance"):
        return "started"
    if before is not None and before.state == "maintenance" and now.state in ("up", "issues"):
        return "ended"
    return None
