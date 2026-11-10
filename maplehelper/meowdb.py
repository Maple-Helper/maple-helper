"""Every live request the app sends to meowdb.com (NiaDB), under the site's tool rules (meowdb.com/reuse#tool-rules):
one request every GAP_SECONDS, one at a time, never in parallel; a User-Agent with our name and a way to reach us; the
same address for the same question (no cache-busting); and a full stop on a 403 or 429.

The knowledge base never comes from here (players get it from GitHub Releases, updater.py); this is only what has to
be live, the chat's server-status dot (serverstatus.py) and the Free Market lookups (market.py). After a 403 or 429
nothing more is sent to the site until the app restarts: every caller then gets None, its "can't reach it" answer.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from . import __version__

log = logging.getLogger("maplehelper")

UA = (f"MapleHelper/{__version__} (+https://github.com/Maple-Helper/maple-helper; "
      "contact: https://github.com/Maple-Helper/maple-helper/issues)")
GAP_SECONDS = 10.0             # from the end of one answer to the next request

_gate = threading.Lock()       # held over a request: one at a time, whichever thread asks
_last = [-GAP_SECONDS]         # when the last answer ended (time.monotonic)
_stopped: list[str] = []       # the 403/429 that stopped us, once one came


def get_json(url: str, timeout: float = 8):
    """The JSON at `url`, or None: offline, a timeout, an error page, nonsense, or the site told us to stop. Waits its
    turn (GAP_SECONDS after the last answer); callers ask from background threads."""
    if _stopped:
        return None
    with _gate:
        if _stopped:
            return None
        wait = _last[0] + GAP_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                _stopped.append(f"{e.code} from {url}")
                log.warning("meowdb.com answered %s: no more requests to it until the app restarts", _stopped[0])
            return None
        except Exception:          # noqa: BLE001 - offline, DNS, a timeout, an HTML error page: all "don't know"
            return None
        finally:
            _last[0] = time.monotonic()


def stopped() -> str | None:
    """Why the app stopped asking meowdb.com (its 403/429), None while it still asks."""
    return _stopped[0] if _stopped else None
