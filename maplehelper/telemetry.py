"""Anonymous usage stats (PostHog), only when the player opts in (Settings > Privacy & system).

What is sent: event names ("question_asked", ...), the app version, the OS, and a few short
values such as which provider answered. Each install gets a random id that is not linked to the
player's identity. Never sent: questions, answers, screenshots, voice, character names, API keys.
Values are limited to short scalars (see _clean), so a question can't be sent by mistake.

Events wait in a queue and a daemon thread posts them in batches. A failed post is dropped:
telemetry never blocks the UI or breaks the app.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from . import report

# PostHog project API key (phc_...). It can only write events, so shipping it in the app is safe.
# Empty = telemetry off for everyone. $MAPLEHELPER_POSTHOG_KEY overrides it (local testing).
PROJECT_KEY = "phc_mUxe6uw9XcoyfVDMytYXY4swn7YM78B2ZmbK7awwnrJk"
HOST = "https://us.i.posthog.com"   # the project (643525) is on PostHog US cloud
BATCH_SIZE = 20
FLUSH_SECONDS = 30
MAX_QUEUE = 500          # offline for hours: drop new events rather than grow without limit
MAX_VALUE_LEN = 64

_queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
_state = {"key": "", "enabled": False, "id": "", "base": {}, "thread": None, "settings": None}


def _key() -> str:
    return os.environ.get("MAPLEHELPER_POSTHOG_KEY", PROJECT_KEY)


def install_id(settings) -> str:
    """A random id for this install, created on first use and kept in settings."""
    if not settings["install_id"]:
        settings["install_id"] = uuid.uuid4().hex
    return settings["install_id"]


def base_props(version: str) -> dict:
    """What every event says about the app (also the presence ping's, see presence.py)."""
    return {
        "app_version": version,
        "$os": {"win32": "Windows", "darwin": "Mac OS X"}.get(sys.platform, sys.platform),
        "frozen": bool(getattr(sys, "frozen", False)),   # False = a source run (development)
        "$lib": "maplehelper",
    }


def init(settings, version: str) -> None:
    """Call once at startup. Nothing is sent unless the player turned stats on and a key is set."""
    _state["key"] = _key()
    # the id is made only when stats are turned on: a player who never opts in never gets one
    _state["settings"], _state["id"] = settings, settings["install_id"] or ""
    _state["base"] = base_props(version)
    set_enabled(bool(settings["telemetry"]))


def set_enabled(on: bool) -> None:
    """Follows the Settings switch. Turning it off drops whatever is still queued."""
    _state["enabled"] = bool(on and _state["key"] and not os.environ.get("MAPLEHELPER_NO_TELEMETRY"))
    if _state["enabled"] and not _state["id"] and _state["settings"] is not None:
        _state["id"] = install_id(_state["settings"])
    if not _state["enabled"]:
        _drain()
    elif _state["thread"] is None:
        _state["thread"] = threading.Thread(target=_worker, name="telemetry", daemon=True)
        _state["thread"].start()


def enabled() -> bool:
    return _state["enabled"]


def _clean(props: dict) -> dict:
    """Short scalars only: a long string (a question, a transcript) is dropped, not cut short."""
    out = {}
    for k, v in props.items():
        if isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str) and len(v) <= MAX_VALUE_LEN:
            out[k] = v
    return out


def track(event: str, **props) -> None:
    """Queue an event; returns at once (never blocks, never raises)."""
    if not _state["enabled"]:
        return
    try:
        _queue.put_nowait({
            "event": event,
            "distinct_id": _state["id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": {**_state["base"], **_clean(props), "$process_person_profile": False},
        })
    except queue.Full:
        pass


def _drain() -> list[dict]:
    items = []
    while True:
        try:
            items.append(_queue.get_nowait())
        except queue.Empty:
            return items


def _post(batch: list[dict], key: str | None = None) -> bool:
    """`key`: the presence ping posts while these stats are off (and init's key unset)."""
    body = json.dumps({"api_key": key or _state["key"], "batch": batch}).encode("utf-8")
    req = urllib.request.Request(f"{HOST}/batch/", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "MapleHelper"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:      # noqa: BLE001 - http.client's BadStatusLine/IncompleteRead aren't OSError: they
        # killed the worker thread and the stats stopped for the session. Stats must never fail anything
        report.log.info("telemetry post failed: %s", e)
        return False


def _worker():
    while True:
        batch = []
        deadline = time.monotonic() + FLUSH_SECONDS
        while len(batch) < BATCH_SIZE:
            try:
                batch.append(_queue.get(timeout=max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                break
        if batch and _state["enabled"]:
            _post(batch)


def flush(timeout: float = 2.0) -> None:
    """On quit: post what is still queued, giving up after `timeout` seconds."""
    if not _state["enabled"]:
        return
    batch = _drain()
    if batch:
        t = threading.Thread(target=_post, args=(batch,), daemon=True)
        t.start()
        t.join(timeout)
