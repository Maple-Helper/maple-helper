"""The app's live requests to meowdb.com follow the site's tool rules (meowdb.com/reuse#tool-rules): one every 10
seconds, one at a time, our name and a way to reach us in the User-Agent, and a full stop on a 403 or 429."""
import re
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maplehelper import meowdb  # noqa: E402


class _Answer:
    def __init__(self, body=b'{"verdict": "up"}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


@pytest.fixture
def site(monkeypatch):
    """A fake clock and site: `sent` collects (url, when, User-Agent); `answers` is what the next requests get."""
    clock, sent, answers = [1000.0], [], []
    monkeypatch.setattr(meowdb, "_last", [-meowdb.GAP_SECONDS])
    monkeypatch.setattr(meowdb, "_stopped", [])
    monkeypatch.setattr(meowdb.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(meowdb.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    def urlopen(req, timeout=8):
        sent.append((req.full_url, clock[0], req.get_header("User-agent")))
        clock[0] += 1
        a = answers.pop(0) if answers else _Answer()
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(meowdb.urllib.request, "urlopen", urlopen)
    return sent, answers


def test_requests_wait_ten_seconds_and_carry_our_name(site):
    sent, _ = site
    for _ in range(3):
        assert meowdb.get_json("https://meowdb.com/msclassic/api/server-status") == {"verdict": "up"}
    times = [t for _, t, _ in sent]
    assert all(b - a >= 10 for a, b in zip(times, times[1:]))
    assert all("MapleHelper/" in ua and "github.com/Maple-Helper/maple-helper" in ua for _, _, ua in sent)


@pytest.mark.parametrize("code", [403, 429])
def test_a_403_or_429_stops_every_request_until_a_restart(site, code):
    sent, answers = site
    answers.append(urllib.error.HTTPError("u", code, "no", {}, None))
    assert meowdb.get_json("https://meowdb.com/a") is None
    assert meowdb.get_json("https://meowdb.com/b") is None
    assert len(sent) == 1 and str(code) in meowdb.stopped()


def test_every_live_meowdb_request_goes_through_the_gate():
    """(conftest stubs serverstatus.fetch for the whole session, so this reads the code instead of calling it)"""
    root = Path(__file__).resolve().parents[1] / "maplehelper"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name != "meowdb.py" and re.search(r"[\"']https://meowdb\.com", text) and "urlopen" in text:
            pytest.fail(f"{path.name} sends its own request to meowdb.com: use meowdb.get_json")
