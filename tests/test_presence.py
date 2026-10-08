"""Presence ping: on for everyone, no stored id, counted by flags, and never a crash or a block."""
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


def test_switch_env_and_key_turn_it_off(pres, monkeypatch):
    settings, posts = pres
    settings["presence"] = False
    presence.start(settings, "0.14.0")
    assert not presence.enabled() and not presence.ping(DAY1) and posts == []

    settings["presence"] = True
    monkeypatch.setenv("MAPLEHELPER_NO_TELEMETRY", "1")
    presence.start(settings, "0.14.0")
    assert not presence.enabled()

    monkeypatch.delenv("MAPLEHELPER_NO_TELEMETRY")
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "")
    presence.start(settings, "0.14.0")
    assert not presence.enabled()


def test_event_carries_no_install_id_and_only_the_listed_values(pres):
    settings, posts = pres
    settings["install_id"] = "a" * 32                  # an opted-in player: the ping still never uses it
    presence.start(settings, "0.14.0")
    assert presence.ping(DAY1)
    [[e]] = posts
    assert e["event"] == "app_running"
    assert e["distinct_id"] != settings["install_id"] and len(e["distinct_id"]) == 32
    assert set(e["properties"]) == {"app_version", "$os", "frozen", "$lib", "first_ping", "first_today",
                                    "first_this_month", "$process_person_profile", "$geoip_disable"}
    assert e["properties"]["app_version"] == "0.14.0"
    assert e["properties"]["$process_person_profile"] is False and e["properties"]["$geoip_disable"] is True


def test_a_new_run_gets_a_new_id(pres):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    presence.start(settings, "0.14.0")
    presence.ping(DAY1)
    assert posts[0][0]["distinct_id"] != posts[1][0]["distinct_id"]
    assert "presence" not in str(settings.data.get("install_id"))     # nothing id-like is stored


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


def test_turning_off_stops_the_pings(pres):
    settings, posts = pres
    presence.start(settings, "0.14.0")
    presence.set_enabled(False)
    assert not presence.ping(DAY1) and posts == []


def test_opt_in_stats_stay_off_by_default(pres):
    settings, _ = pres
    presence.start(settings, "0.14.0")
    telemetry.init(settings, "0.14.0")
    assert presence.enabled() and not telemetry.enabled() and settings["install_id"] == ""


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
