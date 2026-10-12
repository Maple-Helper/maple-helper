"""Usage stats (PostHog): always on (the owner's call, 2026-10-12); only $MAPLEHELPER_NO_TELEMETRY=1 turns them off.

What is sent: event names ("question_asked", "window_opened", ...), the app version, the OS and its version, the
app language and AI provider, the active character's class, job and level, and a few short values. Each install
has a lasting random id (settings["install_id"]), so PostHog counts unique players, new installs, updates and who
comes back; PostHog derives the country from the IP address. Never sent: questions, answers, screenshots, voice,
character names, API keys. Values are limited to short scalars (see _clean), so a question can't be sent by mistake.

Events wait in a queue and a daemon thread posts them in batches. A failed post is dropped:
telemetry never blocks the UI or breaks the app.
"""
from __future__ import annotations

import json
import os
import platform
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
_state = {"key": "", "enabled": False, "id": "", "base": {}, "thread": None, "settings": None, "context": None}


def _key() -> str:
    return os.environ.get("MAPLEHELPER_POSTHOG_KEY", PROJECT_KEY)


def install_id(settings) -> str:
    """A random id for this install, created on first use and kept in settings."""
    if not settings["install_id"]:
        settings["install_id"] = uuid.uuid4().hex
    return settings["install_id"]


def _os_version() -> str:
    try:
        return platform.mac_ver()[0] if sys.platform == "darwin" else platform.version()
    except Exception:       # noqa: BLE001 - stats never fail anything
        return ""


def base_props(version: str) -> dict:
    """What every event says about the app (also the presence ping's, see presence.py)."""
    return {
        "app_version": version,
        "$os": {"win32": "Windows", "darwin": "Mac OS X"}.get(sys.platform, sys.platform),
        "$os_version": _os_version(),
        "frozen": bool(getattr(sys, "frozen", False)),   # False = a source run (development)
        "$lib": "maplehelper",
    }


def set_context(fn) -> None:
    """`fn()` -> what the app is like right now (language, provider, the active character's class/job/level), read
    for every event and ping, so a level-up or a provider switch shows in the next one."""
    _state["context"] = fn


def context_props() -> dict:
    """The app's context now (see set_context); never raises."""
    if _state["context"] is None:
        return {}
    try:
        return _clean(_state["context"]() or {})
    except Exception:   # noqa: BLE001
        return {}


def props() -> dict:
    """What every event carries: the base props plus the app's context now."""
    return {**_state["base"], **context_props()}


def init(settings, version: str) -> None:
    """Call once at startup. Always on, unless $MAPLEHELPER_NO_TELEMETRY is set or no key ships."""
    _state["key"] = _key()
    _state["settings"] = settings
    _state["base"] = base_props(version)
    _state["enabled"] = bool(_state["key"] and not os.environ.get("MAPLEHELPER_NO_TELEMETRY"))
    if not _state["enabled"]:
        _drain()
        return
    _state["id"] = install_id(settings)
    if _state["thread"] is None:
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


def track(event: str, person: dict | None = None, **values) -> None:
    """Queue an event; returns at once (never blocks, never raises). `person`: the player's profile in PostHog
    (version, character...), kept up to date on the install's person ($set)."""
    if not _state["enabled"]:
        return
    try:
        properties = {**props(), **_clean(values)}
        if person:
            properties["$set"] = _clean(person)
        _queue.put_nowait({
            "event": event,
            "distinct_id": _state["id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "properties": properties,
        })
    except queue.Full:
        pass
    except Exception:   # noqa: BLE001
        pass


def install_crash_hook() -> None:
    """An uncaught exception (main thread or another) is counted as "app_crashed": its type and where it was
    raised (file:line inside the app), never its message, which may hold a path or a player's text."""
    prev, prev_thread = sys.excepthook, threading.excepthook

    def where(tb) -> str:
        spot = ""
        while tb is not None:
            name = tb.tb_frame.f_code.co_filename.replace("\\", "/")
            if "/maplehelper/" in name:
                spot = f"{name.rsplit('/maplehelper/', 1)[1]}:{tb.tb_lineno}"
            tb = tb.tb_next
        return spot[-MAX_VALUE_LEN:]

    def on_crash(exc_type, exc, tb):
        track("app_crashed", error=exc_type.__name__[:MAX_VALUE_LEN], where=where(tb), thread="main")
        prev(exc_type, exc, tb)

    def on_thread_crash(a):
        if a.exc_type is not SystemExit:
            track("app_crashed", error=a.exc_type.__name__[:MAX_VALUE_LEN], where=where(a.exc_traceback),
                  thread=(a.thread.name if a.thread else "")[:MAX_VALUE_LEN])
        prev_thread(a)
    sys.excepthook, threading.excepthook = on_crash, on_thread_crash


def _drain() -> list[dict]:
    items = []
    while True:
        try:
            items.append(_queue.get_nowait())
        except queue.Empty:
            return items


def _post(batch: list[dict], key: str | None = None) -> bool:
    """`key`: the presence ping posts while these stats are off (and init's key unset).
    `sent_at` is this computer's clock at sending: PostHog compares it with its own clock and moves the events'
    timestamps by the difference, so a computer whose clock is wrong still files its events at the right time."""
    sent_at = datetime.now(timezone.utc).isoformat()
    body = json.dumps({"api_key": key or _state["key"], "batch": batch, "sent_at": sent_at}).encode("utf-8")
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
