"""Presence ping: always on, under the install's lasting id, flags per day and month, never a crash or a block."""
from datetime import datetime, timezone

import pytest

from maplehelper import presence, telemetry

DAY1 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
DAY1_LATER = datetime(2026, 10, 8, 23, 50, tzinfo=timezone.utc)
DAY2 = datetime(2026, 10, 9, 0, 5, tzinfo=timezone.utc)
NEXT_MONTH = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def pres(isolated_store, monkeypatch):
    """A clean presence state with a key set, no worker thread and no real network."""
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "phc_test")
    monkeypatch.delenv("MAPLEHELPER_NO_TELEMETRY", raising=False)
    monkeypatch.setattr(presence, "_start_worker", lambda: None)      # tests ping by hand
    monkeypatch.setitem(presence._state, "enabled", False)
    monkeypatch.setitem(telemetry._state, "context", None)
    posts = []
    monkeypatch.setattr(telemetry, "_post", lambda batch, key=None: posts.append(batch) or True)
    yield isolated_store.Settings(), posts
    presence._state["enabled"] = False


def flags(event):
    p = event["properties"]
    return p["first_ping"], p["first_today"], p["first_this_month"]


def test_on_by_default(pres):
    settings, _ = pres
    presence.start(settings, "0.14.0")
    assert presence.enabled()


def test_an_old_switched_off_setting_is_ignored_but_env_and_key_turn_it_off(pres, monkeypatch):
    settings, posts = pres
    settings.data["presence"] = False                   # the removed Settings switch
    presence.start(settings, "0.14.0")
    assert presence.enabled()

    monkeypatch.setenv("MAPLEHELPER_NO_TELEMETRY", "1")
    presence.start(settings, "0.14.0")
    assert not presence.enabled() and not presence.ping(DAY1) and posts == []

    monkeypatch.delenv("MAPLEHELPER_NO_TELEMETRY")
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "")
    presence.start(settings, "0.14.0")
    assert not presence.enabled()


def test_event_is_under_the_install_id_with_the_context(pres, monkeypatch):
    settings, posts = pres
    monkeypatch.setitem(telemetry._state, "context", lambda: {"char_level": 40, "language": "he"})
    presence.start(settings, "0.14.0")
    assert presence.ping(DAY1)
    [[e]] = posts
    assert e["event"] == "app_running"
    assert e["distinct_id"] == settings["install_id"] and len(e["distinct_id"]) == 32
    p = e["properties"]
    assert p["app_version"] == "0.14.0" and p["char_level"] == 40 and p["language"] == "he"
    assert len(p["run_id"]) == 32 and "$geoip_disable" not in p


def test_a_new_run_gets_a_new_run_id_but_the_same_install_id(pres):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    assert posts[0][0]["distinct_id"] == posts[1][0]["distinct_id"]
    assert posts[0][0]["properties"]["run_id"] != posts[1][0]["properties"]["run_id"]


def test_flags_over_days_and_months(pres):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    for when in (DAY1, DAY1_LATER, DAY2, NEXT_MONTH):
        presence.ping(when)
    assert [flags(b[0]) for b in posts] == [
        (True, True, True),        # first ever
        (False, False, False),     # same day
        (False, True, False),      # a new UTC day
        (False, True, True),       # a new month
    ]


def test_flags_survive_a_restart(pres, isolated_store):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    again = isolated_store.Settings()                   # read back from disk
    presence.start(again, "0.14.0")
    presence.ping(DAY1_LATER)
    assert flags(posts[1][0]) == (False, False, False)


def test_a_failed_post_keeps_the_flags_for_the_next_ping(pres, monkeypatch):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    monkeypatch.setattr(telemetry, "_post", lambda batch, key=None: False)
    assert not presence.ping(DAY1)
    assert settings["presence_sent"] == {}
    monkeypatch.setattr(telemetry, "_post", lambda batch, key=None: posts.append(batch) or True)
    assert presence.ping(DAY1_LATER)
    assert flags(posts[0][0]) == (True, True, True)


def test_settings_are_written_only_when_a_flag_changes(pres, monkeypatch):
    settings, _ = pres
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    saves = []
    monkeypatch.setattr(type(settings), "save", lambda self: saves.append(1))
    presence.ping(DAY1_LATER)
    assert saves == []
    presence.ping(DAY2)
    assert saves == [1]


def test_a_broken_setting_counts_as_never_sent(pres):
    settings, posts = pres
    settings.data["presence_sent"] = "garbage"
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    assert flags(posts[0][0]) == (True, True, True)


def test_stop_ends_the_pings(pres):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    presence.stop()
    assert not presence.ping(DAY1) and posts == []


def test_post_uses_the_given_key(monkeypatch):
    import json
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout):
        seen["body"] = json.loads(req.data)
        return Resp()
    monkeypatch.setitem(telemetry._state, "key", "")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake)
    assert telemetry._post([{"event": "x"}], key="phc_other")
    assert seen["body"]["api_key"] == "phc_other"
