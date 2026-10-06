"""Telemetry: opt-in only, anonymous, short values only, and never a crash or a block."""
import json
import urllib.error

import pytest

from maplehelper import telemetry


@pytest.fixture
def tel(isolated_store, monkeypatch):
    """A clean telemetry state with a key set, no worker thread and no real network."""
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "phc_test")
    monkeypatch.delenv("MAPLEHELPER_NO_TELEMETRY", raising=False)
    monkeypatch.setitem(telemetry._state, "thread", object())   # pretend the worker runs; tests drain by hand
    monkeypatch.setitem(telemetry._state, "enabled", False)
    telemetry._drain()
    posts = []
    monkeypatch.setattr(telemetry, "_post", lambda batch: posts.append(batch) or True)
    yield isolated_store.Settings(), posts
    telemetry._drain()


def test_off_by_default(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started")
    assert not telemetry.enabled()
    assert telemetry._drain() == []


def test_no_key_means_off_even_when_opted_in(tel, monkeypatch):
    settings, _ = tel
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "")
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    assert not telemetry.enabled()


def test_env_kill_switch(tel, monkeypatch):
    settings, _ = tel
    monkeypatch.setenv("MAPLEHELPER_NO_TELEMETRY", "1")
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    assert not telemetry.enabled()


def test_opted_in_event_shape(tel):
    settings, _ = tel
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    telemetry.track("question_asked", answered_by="claude", tagged=True)
    [e] = telemetry._drain()
    assert e["event"] == "question_asked"
    assert e["distinct_id"] == settings["install_id"] and len(e["distinct_id"]) == 32
    p = e["properties"]
    assert p["app_version"] == "0.6.0" and p["answered_by"] == "claude" and p["tagged"] is True
    assert p["$process_person_profile"] is False


def test_no_id_until_stats_are_turned_on(tel):
    """The random id was created at every start, opted in or not (audit SEC-16)."""
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    assert settings["install_id"] == "" and telemetry._state["id"] == ""
    settings["telemetry"] = True
    telemetry.set_enabled(True)
    telemetry.track("app_started")
    [e] = telemetry._drain()
    assert len(settings["install_id"]) == 32 and e["distinct_id"] == settings["install_id"]


def test_install_id_is_stable(tel):
    settings, _ = tel
    first = telemetry.install_id(settings)
    assert telemetry.install_id(settings) == first


def test_long_strings_and_objects_are_dropped(tel):
    settings, _ = tel
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    question = "where does the zakum helmet drop and how many runs do I need " * 3
    telemetry.track("question_asked", text=question, shot=b"png", kind="ai", n=3)
    [e] = telemetry._drain()
    assert "text" not in e["properties"] and "shot" not in e["properties"]
    assert e["properties"]["kind"] == "ai" and e["properties"]["n"] == 3


def test_turning_off_drops_the_queue(tel):
    settings, _ = tel
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started")
    telemetry.set_enabled(False)
    telemetry.track("voice_used")
    assert telemetry._drain() == []


def test_full_queue_never_blocks(tel, monkeypatch):
    settings, _ = tel
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    for _ in range(telemetry.MAX_QUEUE + 50):
        telemetry.track("app_started")
    assert len(telemetry._drain()) == telemetry.MAX_QUEUE


def test_flush_posts_what_is_queued(tel):
    settings, posts = tel
    settings["telemetry"] = True
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started")
    telemetry.track("voice_used")
    telemetry.flush()
    assert [e["event"] for e in posts[0]] == ["app_started", "voice_used"]


def test_post_failure_is_swallowed(monkeypatch):
    def offline(*a, **kw):
        raise urllib.error.URLError("no network")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", offline)
    assert telemetry._post([{"event": "x"}]) is False


def test_post_body(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return Resp()
    monkeypatch.setitem(telemetry._state, "key", "phc_test")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake)
    assert telemetry._post([{"event": "x"}])
    assert seen["url"].endswith("/batch/")
    assert seen["body"] == {"api_key": "phc_test", "batch": [{"event": "x"}]}
