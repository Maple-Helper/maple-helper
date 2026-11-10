"""meowdb.com's pages and files read from inside a page of the site, by a real browser engine (Qt WebEngine).

Since 2026-11-10 the site refuses every request that doesn't come from a browser (403 "Blocked: this tool..."), for
all fan tools at once: NiaDB can't lift it for one project, and told the owner to get around it while their
permission still covers us. So the scraper does what the app does for MesoWatch (maplehelper/webfetch.py): a hidden
page opens meowdb.com/robots.txt (a tiny page of the site) and asks for each file from there with fetch(). The engine
is Chromium under its own name (QtWebEngine in its User-Agent); every request also says who we are in an X-Tool
header, and scrape_meowdb.fetch still keeps the site's pace (one request every 10 s, one at a time) and stops on a
403 or 429.

run(work) starts the engine on the main thread and calls work() on a worker thread, where get() blocks for each
answer. Without Qt WebEngine run() raises ImportError.
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import sys
import threading
import urllib.error

HOST_PAGE = "https://meowdb.com/robots.txt"
POLL_MS = 100
TOOL = "MapleHelper-KB/1.0 (+https://github.com/Maple-Helper/maple-helper)"

_FETCH = """(() => {
  const id = %(id)s, box = (window.__mh = window.__mh || {});
  fetch(%(url)s, {headers: {'X-Tool': %(tool)s}}).then(async r => {
    const blob = r.status === 200 ? await r.blob() : new Blob();
    const url = blob.size ? await new Promise((ok, no) => {
      const f = new FileReader(); f.onload = () => ok(f.result); f.onerror = () => no(f.error); f.readAsDataURL(blob);
    }) : ',';
    box[id] = {s: r.status, b: url.slice(url.indexOf(',') + 1)};
  }).catch(e => { box[id] = {s: 0, x: String(e)}; });
  return 1;
})()"""
_TAKE = """(() => {
  const box = window.__mh || {}, v = box[%(id)s];
  if (v) delete box[%(id)s];
  return v ? JSON.stringify(v) : null;
})()"""

_engine = None
_ids = itertools.count(1)


class _Job:
    def __init__(self, url: str):
        self.id, self.url = next(_ids), url
        self.done = threading.Event()
        self.status, self.body, self.error = 0, b"", ""


def _make_engine():
    from PySide6.QtCore import QObject, QTimer, QUrl, Signal
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings

    class Engine(QObject):
        asked = Signal(object)
        finished = Signal()             # from the worker thread: the work is done, the app quits

        def __init__(self):
            super().__init__()
            self._profile = QWebEngineProfile(self)        # off the record
            s = self._profile.settings()
            for attr in (QWebEngineSettings.AutoLoadIconsForPage, QWebEngineSettings.AutoLoadImages):
                s.setAttribute(attr, False)                 # only the files asked for
            self._page = None
            self._ready = False
            self._waiting: list[_Job] = []
            self.asked.connect(self._take)

        def _take(self, job):
            if self._ready:
                self._run(job)
                return
            self._waiting.append(job)
            if self._page is None:
                self._page = QWebEnginePage(self._profile, self)
                self._page.loadFinished.connect(self._opened)
                self._page.load(QUrl(HOST_PAGE))

        def _opened(self, ok):
            waiting, self._waiting = self._waiting, []
            self._ready = ok
            for job in waiting:
                if ok:
                    self._run(job)
                else:
                    job.error = "could not open " + HOST_PAGE
                    job.done.set()
            if not ok:
                self._page.deleteLater()
                self._page = None

        def _run(self, job):
            page = self._page
            script = _FETCH % {"id": job.id, "url": json.dumps(job.url), "tool": json.dumps(TOOL)}
            page.runJavaScript(script, 0, lambda _: self._poll(job, page))

        def _poll(self, job, page):
            if job.done.is_set():
                return
            page.runJavaScript(_TAKE % {"id": job.id}, 0, lambda v: self._got(job, page, v))

        def _got(self, job, page, value):
            if not value:
                QTimer.singleShot(POLL_MS, lambda: self._poll(job, page))
                return
            try:
                r = json.loads(value)
                job.status = int(r.get("s") or 0)
                job.body = base64.b64decode(r.get("b") or "")
                job.error = str(r.get("x") or "")
            except (ValueError, TypeError) as e:
                job.error = repr(e)
            job.done.set()

    return Engine()


def active() -> bool:
    return _engine is not None


def get(url: str, timeout: float = 60) -> bytes:
    """The body of a 200 answer; an error status raises HTTPError (as urllib does), no answer an OSError."""
    job = _Job(url)
    _engine.asked.emit(job)
    if not job.done.wait(timeout):
        job.done.set()
        raise TimeoutError(f"no answer from {url} in {timeout:g} s")
    if not job.status:
        raise OSError(f"{url}: {job.error or 'no answer'}")
    if job.status >= 400:
        raise urllib.error.HTTPError(url, job.status, f"HTTP {job.status}", None, None)
    return job.body


def run(work) -> int:
    """work() on a worker thread while the engine runs here; its return value (an exit code), 1 if it raised."""
    global _engine
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import PySide6.QtWebEngineCore  # noqa: F401 - ImportError here: this Qt has no WebEngine

    app = QApplication.instance() or QApplication(sys.argv[:1])
    _engine = _make_engine()
    _engine.finished.connect(app.quit)
    result = [1]

    def worker():
        try:
            result[0] = work() or 0
        except SystemExit as e:
            result[0] = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code:
                print(e.code, file=sys.stderr)
        except BaseException:
            import traceback
            traceback.print_exc()
        finally:
            _engine.finished.emit()
    QTimer.singleShot(0, lambda: threading.Thread(target=worker, daemon=True).start())
    app.exec()
    _engine = None
    return result[0]
