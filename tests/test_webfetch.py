"""webfetch: MesoWatch's files asked for from inside a page of the site, by Qt WebEngine (a local site stands in)."""
import http.server
import sys
import threading
import time
import urllib.error

import pytest

from maplehelper import mesowatch, webfetch

PICTURE = bytes(range(256)) * 4         # not text: every byte value, as a seller's picture


class Site(http.server.BaseHTTPRequestHandler):
    asked: list = []

    def do_GET(self):  # noqa: N802 - http.server's name
        Site.asked.append((self.path, self.headers.get("If-None-Match"), self.headers.get("Sec-Fetch-Site")))
        if self.path == "/robots.txt":
            return self._send(200, b"User-agent: *", "text/plain")
        if self.path == "/data/list.json":
            if self.headers.get("If-None-Match") == '"v1"':
                return self._send(304, b"")
            return self._send(200, '{"kind": "list", "name": "Adamantium Igor"}'.encode(), etag='"v1"')
        if self.path == "/data/face.webp":
            return self._send(200, PICTURE, "image/webp")
        if self.path == "/data/blocked.json":
            return self._send(403, b"Attention Required!", "text/html")
        return self._send(404, b"no", "text/plain")

    def _send(self, status, body, kind="application/json", etag=""):
        self.send_response(status)
        if status != 304:
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
        if etag:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def engine(app, monkeypatch):
    pytest.importorskip("PySide6.QtWebEngineCore")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(webfetch, "HOST_PAGE", base + "/robots.txt")
    monkeypatch.setattr(webfetch, "_engine", None)
    webfetch._engine = webfetch._Engine()
    Site.asked = []
    yield base
    webfetch._engine._close()
    webfetch._engine.deleteLater()
    server.shutdown()
    server.server_close()


def ask(*args, timeout=30):
    """webfetch.request on a background thread (as the app asks), the UI thread turning meanwhile."""
    from PySide6.QtCore import QCoreApplication
    out = {}

    def run():
        out["available"] = webfetch.available()
        try:
            out["result"] = webfetch.request(*args)
        except Exception as e:     # noqa: BLE001 - the test looks at it
            out["error"] = e
    t = threading.Thread(target=run)
    t.start()
    end = time.monotonic() + timeout
    while t.is_alive() and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert not t.is_alive(), "no answer"
    return out


def test_a_file_comes_from_inside_the_sites_page(engine):
    out = ask(engine + "/data/list.json")
    assert out["available"] and out["result"] == (200, b'{"kind": "list", "name": "Adamantium Igor"}', '"v1"')
    assert Site.asked[0][0] == "/robots.txt"                              # the page opened first
    assert Site.asked[1:] == [("/data/list.json", None, "same-origin")]     # then asked from inside it, alone
    assert not webfetch.available()                                       # the UI thread can't wait on itself


def test_an_unchanged_file_answers_304_and_a_picture_stays_bytes(engine):
    assert ask(engine + "/data/list.json", '"v1"')["result"] == (304, b"", '"v1"')
    assert ask(engine + "/data/face.webp")["result"] == (200, PICTURE, "")
    assert [p for p, *_ in Site.asked].count("/robots.txt") == 1          # one page for both


def test_an_error_status_raises_as_urllib_does_and_a_refusal_reopens_the_page(engine):
    err = ask(engine + "/data/missing.json")["error"]
    assert isinstance(err, urllib.error.HTTPError) and err.code == 404
    assert webfetch._engine._page is not None
    err = ask(engine + "/data/blocked.json")["error"]
    assert isinstance(err, urllib.error.HTTPError) and err.code == 403
    assert webfetch._engine._page is None                                  # refused: closed
    assert ask(engine + "/data/list.json")["result"][0] == 200
    assert [p for p, *_ in Site.asked].count("/robots.txt") == 2          # opened afresh


def test_the_page_closes_when_idle(engine, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    webfetch._engine._idle.setInterval(50)
    ask(engine + "/data/list.json")
    end = time.monotonic() + 5
    while webfetch._engine._page is not None and time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert webfetch._engine._page is None


def test_mesowatch_asks_through_the_page_when_there_is_one(monkeypatch):
    asked = []
    monkeypatch.setattr(webfetch, "available", lambda: True)
    monkeypatch.setattr(webfetch, "request", lambda url, etag="", timeout=30: asked.append(url) or (200, b"{}", ""))
    monkeypatch.setattr(mesowatch, "_http", lambda *a, **k: pytest.fail("asked directly"))
    assert mesowatch._fetch(mesowatch.INDEX_URL) == (200, b"{}", "") and asked == [mesowatch.INDEX_URL]
    monkeypatch.setattr(webfetch, "available", lambda: False)
    monkeypatch.setattr(mesowatch, "_http", lambda url, etag="", timeout=30: (200, b"direct", ""))
    assert mesowatch._fetch(mesowatch.INDEX_URL) == (200, b"direct", "")


def test_the_copy_on_hand_does_not_wait_for_a_check_under_way(monkeypatch):
    """A check's requests wait on the UI thread, which asks snapshot(refresh=False): it must not wait on the check."""
    monkeypatch.setattr(mesowatch, "_loaded", True)
    monkeypatch.setattr(mesowatch, "_snap", None)
    with mesowatch._lock:                                                   # a check under way
        t0 = time.monotonic()
        assert mesowatch.snapshot(refresh=False) is None
        assert time.monotonic() - t0 < 1
