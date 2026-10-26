"""MesoWatch's data files read from inside a page of its site, by a real browser engine (Qt WebEngine).

Since 2026-10-26 Cloudflare refuses every request for meso.watch/data/ that doesn't come from a page of the site (403
"Attention Required"; a browser's User-Agent doesn't help, nor does Edge itself opening a data file), while the site's
page, in a browser, reads the same files as always. MesoWatch can't change that and asked the owner to solve it alone;
their permission still covers the app reading the data. So the app does what a visitor's browser does: a hidden page
opens meso.watch/robots.txt (a tiny page of the site; the site's own page would download and draw the whole market each
time) and asks for the files from there with fetch(). Nothing is disguised: the engine is Chromium, under its own name
(QtWebEngine in its User-Agent), and the requests are the same ones, as often, as before.

The page is open while it's used and closes after IDLE_SECONDS (its renderer process with it); the next request opens
it again, a fraction of a second. Requests come from background threads (mesowatch's checks, an item's own file, a
seller's picture) and wait; the engine runs on the UI thread. Before install(), without Qt WebEngine, or on the UI
thread itself (it can't wait on itself), available() is False and mesowatch asks directly, as before."""
from __future__ import annotations

import base64
import itertools
import json
import logging
import threading
import urllib.error

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, QUrl, Signal

log = logging.getLogger(__name__)

HOST_PAGE = "https://meso.watch/robots.txt"
IDLE_SECONDS = 120
POLL_MS = 100

# the answer is kept on the page until asked for: runJavaScript returns at once, before a fetch() is done. The body
# comes back as base64 (FileReader's data URL): the files are JSON, the sellers' pictures are not
_FETCH = """(() => {
  const id = %(id)s, etag = %(etag)s;
  const box = (window.__mh = window.__mh || {});
  fetch(%(url)s, {cache: 'no-store', headers: etag ? {'If-None-Match': etag} : {}}).then(async r => {
    const blob = r.status === 200 ? await r.blob() : new Blob();
    const url = blob.size ? await new Promise((ok, no) => {
      const f = new FileReader(); f.onload = () => ok(f.result); f.onerror = () => no(f.error); f.readAsDataURL(blob);
    }) : ',';
    box[id] = {s: r.status, e: r.headers.get('ETag') || '', b: url.slice(url.indexOf(',') + 1)};
  }).catch(e => { box[id] = {s: 0, x: String(e)}; });
  return 1;
})()"""
_TAKE = """(() => {
  const box = window.__mh || {}, v = box[%(id)s];
  if (v) delete box[%(id)s];
  return v ? JSON.stringify(v) : null;
})()"""

_engine: _Engine | None = None
_ids = itertools.count(1)


class _Job:
    def __init__(self, url: str, etag: str):
        self.id, self.url, self.etag = next(_ids), url, etag
        self.done = threading.Event()
        self.result: tuple[int, bytes, str] | None = None
        self.error = ""

    def finish(self, status: int, body: bytes, etag: str) -> None:
        self.result = (status, body, etag)
        self.done.set()

    def fail(self, why: str) -> None:
        self.error = why or "failed"
        self.done.set()


class _Engine(QObject):
    """The hidden page, on the UI thread. A request is a signal (queued from the asking thread)."""
    asked = Signal(object)

    def __init__(self):
        super().__init__()
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
        self._profile = QWebEngineProfile(self)     # off the record: nothing of it is kept on disk
        settings = self._profile.settings()
        for attr in (QWebEngineSettings.AutoLoadIconsForPage, QWebEngineSettings.AutoLoadImages,
                     QWebEngineSettings.LocalStorageEnabled):
            settings.setAttribute(attr, False)      # only the files asked for: no favicon.ico on each opening
        self._page = None
        self._ready = False
        self._waiting: list[_Job] = []              # asked while the page was opening
        self._busy = 0
        self._idle = QTimer(self, singleShot=True, interval=IDLE_SECONDS * 1000, timeout=self._close)
        self.asked.connect(self._take)
        QCoreApplication.instance().aboutToQuit.connect(self._close)

    def _take(self, job: _Job) -> None:
        self._idle.stop()
        if self._ready:
            self._run(job)
            return
        self._waiting.append(job)
        if self._page is None:
            from PySide6.QtWebEngineCore import QWebEnginePage
            self._page = QWebEnginePage(self._profile, self)
            self._page.loadFinished.connect(self._opened)
            self._page.load(QUrl(HOST_PAGE))

    def _opened(self, ok: bool) -> None:
        waiting, self._waiting = self._waiting, []
        if not ok:
            for job in waiting:
                job.fail("could not open " + HOST_PAGE)
            self._close()
            return
        self._ready = True
        for job in waiting:
            self._run(job)

    def _run(self, job: _Job) -> None:
        self._busy += 1
        page = self._page
        script = _FETCH % {"id": job.id, "url": json.dumps(job.url), "etag": json.dumps(job.etag)}
        page.runJavaScript(script, 0, lambda _: self._poll(job, page))

    def _poll(self, job: _Job, page) -> None:
        if page is not self._page or job.done.is_set():    # closed meanwhile, or the asker gave up
            job.fail("the page closed")
            self._settle()
            return
        page.runJavaScript(_TAKE % {"id": job.id}, 0, lambda v: self._got(job, page, v))

    def _got(self, job: _Job, page, value) -> None:
        if not value:
            QTimer.singleShot(POLL_MS, lambda: self._poll(job, page))
            return
        try:
            r = json.loads(value)
            status = int(r.get("s") or 0)
            if status:
                job.finish(status, base64.b64decode(r.get("b") or ""), str(r.get("e") or ""))
            else:
                job.fail(str(r.get("x") or "no answer"))
        except (ValueError, TypeError) as e:
            job.fail(repr(e))
            status = 0
        if status == 403:
            self._close()               # refused: the next request opens the page afresh
        self._settle()

    def _settle(self) -> None:
        self._busy -= 1
        if self._busy <= 0 and not self._waiting:
            self._busy = 0
            self._idle.start()

    def _close(self) -> None:
        self._idle.stop()
        self._ready = False
        waiting, self._waiting = self._waiting, []
        for job in waiting:
            job.fail("the page closed")
        if self._page is not None:
            self._page.deleteLater()
            self._page = None


def install() -> bool:
    """Starts the engine (on the UI thread, once the QApplication exists); False when this build has no Qt WebEngine."""
    global _engine
    if _engine is None:
        try:
            _engine = _Engine()
        except Exception:          # noqa: BLE001 - no WebEngine in this build: MesoWatch is asked directly
            log.warning("Qt WebEngine unavailable: MesoWatch is asked directly", exc_info=True)
            return False
    return True


def available() -> bool:
    return _engine is not None and QThread.currentThread() is not _engine.thread()


def request(url: str, etag: str = "", timeout: float = 30) -> tuple[int, bytes, str]:
    """(status, body, etag) as mesowatch._http answers: 304 when unchanged; an error status raises HTTPError, no
    answer an OSError. Blocking: call it off the UI thread (available())."""
    job = _Job(url, etag)
    _engine.asked.emit(job)
    if not job.done.wait(timeout):
        job.done.set()                 # the page stops asking for it
        raise TimeoutError(f"no answer from {url} in {timeout:g} s")
    if job.result is None:
        raise OSError(f"{url}: {job.error}")
    status, body, tag = job.result
    if status == 304:
        return 304, b"", etag
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "HTTP %d" % status, None, None)
    return status, body, tag
