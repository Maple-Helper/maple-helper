"""Telemetry: always on, a lasting id per install, short values only, and never a crash or a block."""
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
    monkeypatch.setitem(telemetry._state, "context", None)
    telemetry._drain()
    posts = []
    monkeypatch.setattr(telemetry, "_post", lambda batch: posts.append(batch) or True)
    yield isolated_store.Settings(), posts
    telemetry._drain()


def test_always_on_even_for_an_install_that_had_turned_it_off(tel):
    """Always on (the owner's call, 2026-10-12): the old Settings switch's stored False is ignored."""
    settings, _ = tel
    settings.data["telemetry"] = False
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started")
    assert telemetry.enabled()
    assert [e["event"] for e in telemetry._drain()] == ["app_started"]


def test_no_key_means_off(tel, monkeypatch):
    settings, _ = tel
    monkeypatch.setenv("MAPLEHELPER_POSTHOG_KEY", "")
    telemetry.init(settings, "0.6.0")
    assert not telemetry.enabled()


def test_env_kill_switch(tel, monkeypatch):
    settings, _ = tel
    monkeypatch.setenv("MAPLEHELPER_NO_TELEMETRY", "1")
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started")
    assert not telemetry.enabled() and telemetry._drain() == []


def test_event_shape(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    telemetry.track("question_asked", answered_by="claude", tagged=True)
    [e] = telemetry._drain()
    assert e["event"] == "question_asked"
    assert e["distinct_id"] == settings["install_id"] and len(e["distinct_id"]) == 32
    p = e["properties"]
    assert p["app_version"] == "0.6.0" and p["answered_by"] == "claude" and p["tagged"] is True
    assert "$process_person_profile" not in p and "$os_version" in p      # a person per install: uniques, retention


def test_context_is_read_for_every_event(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    level = {"n": 30}
    telemetry.set_context(lambda: {"char_level": level["n"], "char_job": "Hunter", "bad": {"a": 1}})
    telemetry.track("app_started")
    level["n"] = 31
    telemetry.track("voice_used")
    a, b = telemetry._drain()
    assert a["properties"]["char_level"] == 30 and b["properties"]["char_level"] == 31
    assert a["properties"]["char_job"] == "Hunter" and "bad" not in a["properties"]


def test_a_broken_context_never_breaks_an_event(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    telemetry.set_context(lambda: 1 / 0)
    telemetry.track("app_started")
    [e] = telemetry._drain()
    assert e["properties"]["app_version"] == "0.6.0"


def test_person_props_go_in_set(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    telemetry.track("app_started", person={"char_level": 12, "note": "x" * 200})
    [e] = telemetry._drain()
    assert e["properties"]["$set"] == {"char_level": 12}


def test_crash_hook_counts_the_type_and_place_never_the_message(tel, monkeypatch):
    import sys
    import threading
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    monkeypatch.setattr(threading, "excepthook", lambda a: None)
    telemetry.install_crash_hook()
    try:
        raise KeyError("C:/Users/someone/secret question")
    except KeyError:
        sys.excepthook(*sys.exc_info())
    [e] = telemetry._drain()
    assert e["event"] == "app_crashed" and e["properties"]["error"] == "KeyError"
    assert "secret" not in json.dumps(e)


def test_install_id_is_stable(tel):
    settings, _ = tel
    first = telemetry.install_id(settings)
    assert telemetry.install_id(settings) == first


def test_long_strings_and_objects_are_dropped(tel):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    question = "where does the zakum helmet drop and how many runs do I need " * 3
    telemetry.track("question_asked", text=question, shot=b"png", kind="ai", n=3)
    [e] = telemetry._drain()
    assert "text" not in e["properties"] and "shot" not in e["properties"]
    assert e["properties"]["kind"] == "ai" and e["properties"]["n"] == 3


def test_full_queue_never_blocks(tel, monkeypatch):
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    for _ in range(telemetry.MAX_QUEUE + 50):
        telemetry.track("app_started")
    assert len(telemetry._drain()) == telemetry.MAX_QUEUE


def test_flush_posts_what_is_queued(tel):
    settings, posts = tel
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


def test_a_broken_http_answer_never_kills_the_worker(monkeypatch):
    # LIF-11: BadStatusLine isn't an OSError: it killed the worker thread and the stats stopped for the session
    import http.client

    def garbled(*a, **kw):
        raise http.client.BadStatusLine("garbage")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", garbled)
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
    assert seen["body"].pop("sent_at")              # checked in the next test
    assert seen["body"] == {"api_key": "phc_test", "batch": [{"event": "x"}]}


def test_post_says_when_it_was_sent(monkeypatch):
    """sent_at lets PostHog correct a computer whose clock is wrong (it shifts timestamps by server - sent_at)."""
    from datetime import datetime, timezone
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout):
        seen["body"] = json.loads(req.data)
        return Resp()
    monkeypatch.setitem(telemetry._state, "key", "phc_test")
    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake)
    before = datetime.now(timezone.utc)
    assert telemetry._post([{"event": "x"}])
    sent = datetime.fromisoformat(seen["body"]["sent_at"])
    assert sent.tzinfo is not None and abs((sent - before).total_seconds()) < 5


def test_a_failed_kb_update_is_counted_without_the_error_text(tel):
    from maplehelper import updater
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    updater._failed("the KB folder can't be swapped ([WinError 5] C:/Users/someone/kb)")
    [e] = telemetry._drain()
    assert e["event"] == "kb_update_failed" and e["properties"]["reason"] == "the KB folder can't be swapped"


def test_crash_place_in_a_release_build(tel, monkeypatch):
    """PyInstaller keeps relative file names (maplehelper\\app.py): the place must still be found."""
    import sys
    import threading
    settings, _ = tel
    telemetry.init(settings, "0.6.0")
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    monkeypatch.setattr(threading, "excepthook", lambda a: None)
    telemetry.install_crash_hook()
    code = compile("def boom():\n    raise ValueError('x')\n", "maplehelper\\app.py", "exec")
    ns = {}
    exec(code, ns)
    try:
        ns["boom"]()
    except ValueError:
        sys.excepthook(*sys.exc_info())
    [e] = telemetry._drain()
    assert e["properties"]["where"] == "app.py:2"
