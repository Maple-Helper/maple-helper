"""Counting the players: an anonymous "Maple Helper is running" ping, on for everyone unless switched off
(Settings > Privacy & system), apart from the usage stats (telemetry.py).

What is sent, at start and then every INTERVAL seconds: the app version, the OS, and three flags (the first
ping of this install ever, of the UTC day, of the UTC month). The id is random for each run and never stored,
so two runs can't be linked: PostHog counts players online (distinct run ids in the last minutes) and players
in total, per day and per month (pings with a flag), without knowing who anyone is.

A flag is marked as sent only after a successful post: a ping lost offline hands it to the next one.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

from . import telemetry

EVENT = "app_running"
INTERVAL = 600           # 10 min: 6 events per player-hour (PostHog's free tier is 1M a month)

_state = {"enabled": False, "settings": None, "version": "", "run_id": "", "thread": None,
          "wake": threading.Event()}


def start(settings, version: str) -> None:
    """Call once at startup, after onboarding. A new run id each time (a new run)."""
    _state["settings"], _state["version"], _state["run_id"] = settings, version, uuid.uuid4().hex
    set_enabled(bool(settings["presence"]))


def set_enabled(on: bool) -> None:
    """Follows the Settings switch; turned on again, it pings at once."""
    was = _state["enabled"]
    _state["enabled"] = bool(on and telemetry._key() and not os.environ.get("MAPLEHELPER_NO_TELEMETRY"))
    if _state["enabled"] and not was:
        _state["wake"].set()
        _start_worker()


def enabled() -> bool:
    return _state["enabled"]


def stop() -> None:
    """On quit: no ping while the app goes."""
    _state["enabled"] = False


def _sent(settings) -> dict:
    v = settings["presence_sent"]
    return v if isinstance(v, dict) else {}


def payload(settings, version: str, run_id: str, now: datetime) -> tuple[dict, dict]:
    """The event to post at `now`, and what to store as sent once it is posted."""
    sent = _sent(settings)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    event = {
        "event": EVENT,
        "distinct_id": run_id,
        "timestamp": now.isoformat(),
        "properties": {
            **telemetry.base_props(version),
            "first_ping": not sent.get("ever"),
            "first_today": sent.get("day") != day,
            "first_this_month": sent.get("month") != month,
            "$process_person_profile": False,
            "$geoip_disable": True,
        },
    }
    return event, {"ever": True, "day": day, "month": month}


def ping(now: datetime | None = None) -> bool:
    """Post one ping; True when PostHog took it. Never raises (telemetry._post swallows every error)."""
    settings = _state["settings"]
    if not _state["enabled"] or settings is None:
        return False
    event, sent = payload(settings, _state["version"], _state["run_id"], now or datetime.now(timezone.utc))
    if not telemetry._post([event], key=telemetry._key()):
        return False
    if sent != _sent(settings):          # a write about once a day, not every ping
        settings["presence_sent"] = sent
    return True


def _start_worker() -> None:
    if _state["thread"] is None:
        _state["thread"] = threading.Thread(target=_worker, name="presence", daemon=True)
        _state["thread"].start()


def _worker():
    while True:
        _state["wake"].wait(INTERVAL)
        _state["wake"].clear()
        ping()
