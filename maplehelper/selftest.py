"""`Maple Helper(.exe) --selftest <report file> [--require-kb]`: proves a built app can run.

Release builds are windowed, so there is no console to print to: the result is the
exit code (0 = healthy) plus a small report file. Checks the things a frozen build
typically loses: dynamically imported modules, bundled assets, fonts and the KB.
Nothing here touches the network, the game, the microphone or the single-instance lock.
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback
from pathlib import Path

# every module the app imports lazily or that PyInstaller cannot see statically
MODULES = [
    "PySide6.QtWidgets", "PySide6.QtNetwork", "mss", "PIL.Image", "numpy", "keyring", "sounddevice",
    "faster_whisper", "ctranslate2",
    "maplehelper.app", "maplehelper.osapi", "maplehelper.brain", "maplehelper.voice", "maplehelper.providers",
    "maplehelper.updater", "maplehelper.inventory", "maplehelper.portrait", "maplehelper.jobs", "maplehelper.webfetch",
    "PySide6.QtWebEngineCore",
    "maplehelper.ui.overlay", "maplehelper.ui.dialogs", "maplehelper.ui.toast",
]
if sys.platform == "darwin":
    MODULES += ["objc", "Quartz", "AppKit"]   # pyobjc bridges, imported lazily by macapi
ASSET_FILES = ["brand/app.ico", "brand/icon-64.png", "brand/wordmark.png", "fonts/Rubik-Variable.ttf"]


def run(require_kb: bool = False) -> tuple[bool, list[str]]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    ok = True

    def check(name: str, fn):
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"ok   {name}{': ' + str(detail) if detail else ''}")
        except Exception as e:  # report every failure, not just the first
            ok = False
            lines.append(f"FAIL {name}: {e!r}")
            lines.extend("     " + ln for ln in traceback.format_exc().splitlines()[-4:])

    from . import __version__
    lines.append(f"Maple Helper {__version__} frozen={getattr(sys, 'frozen', False)}")
    for mod in MODULES:
        check(f"import {mod}", lambda m=mod: importlib.import_module(m) and None)

    from .store import ASSETS

    def assets():
        missing = [f for f in ASSET_FILES if not (ASSETS / f).exists()]
        if missing:
            raise FileNotFoundError(", ".join(missing))
    check("assets", assets)

    def keyring_backend():
        import keyring
        name = type(keyring.get_keyring()).__module__
        if "fail" in name:
            raise RuntimeError(f"no usable keyring backend ({name})")
        return name
    check("keyring backend", keyring_backend)

    def voice_runtime():
        # voice.py transcribes with vad_filter=True: needs onnxruntime + the bundled Silero model
        import numpy as np
        from faster_whisper.vad import get_speech_timestamps
        get_speech_timestamps(np.zeros(16_000, dtype=np.float32))
        import ctranslate2
        return f"ctranslate2 {ctranslate2.__version__}"
    check("voice runtime (VAD + ctranslate2)", voice_runtime)

    def qt_and_fonts():
        from PySide6.QtWidgets import QApplication
        from .ui import theme
        _app = QApplication.instance() or QApplication([])
        fam = theme.load_fonts()
        if fam != "Rubik":
            raise RuntimeError(f"bundled font not loaded (got {fam!r})")
        return fam
    check("qt + fonts", qt_and_fonts)

    def web_engine():
        # MesoWatch's files come through a hidden page (webfetch.py): the build must carry Qt WebEngine and its
        # renderer process. The same path as the app's, against a site on this machine (no network)
        import http.server
        import threading
        import time

        from PySide6.QtCore import QCoreApplication

        from . import webfetch

        class Site(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's name
                body = b'{"ok": 42}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        webfetch.HOST_PAGE = base + "/robots.txt"
        out: dict = {}

        def ask():
            try:
                out["got"] = webfetch.request(base + "/data/selftest.json", timeout=90)
            except Exception as e:     # noqa: BLE001 - reported below
                out["error"] = e
        try:
            if not webfetch.install():
                raise RuntimeError("Qt WebEngine unavailable")
            t = threading.Thread(target=ask, daemon=True)
            t.start()
            end = time.monotonic() + 100
            while t.is_alive() and time.monotonic() < end:
                QCoreApplication.processEvents()
                time.sleep(0.01)
        finally:
            server.shutdown()
            server.server_close()
            if webfetch._engine is not None:
                webfetch._engine._close()
        if out.get("got") != (200, b'{"ok": 42}', ""):
            raise RuntimeError(f"the hidden page brought no file ({out!r})")
        return "a page fetched a file"
    check("web engine (MesoWatch)", web_engine)

    def kb():
        from . import store
        from .kb import KnowledgeBase
        # --require-kb proves the build carries its KB: only the bundled copy counts. The one in use may be a
        # download in this PC's %APPDATA%, which a fresh install won't have
        base = KnowledgeBase(store.BUNDLED_KB) if require_kb else KnowledgeBase()
        n = len(base.entities)
        if require_kb and n == 0:
            raise RuntimeError(f"no knowledge base bundled with this build ({base.root})")
        detail = f"{n} entities, version {_kb_version(base.root) or '?'}"
        used = store.kb_dir()
        if used != base.root:
            detail += f" (in use: {used}, version {_kb_version(used) or '?'})"
        return detail
    check("knowledge base", kb)

    def os_layer():
        # lists windows through the native API (no permission needed; a CI runner has no game)
        from . import osapi
        osapi.find_game_window()
        return sys.platform
    check("os layer", os_layer)

    # informational: CI machines and fresh PCs have no AI CLI, and onboarding installs the chosen one
    from . import providers
    for ai in providers.PROVIDERS.values():
        check(f"{ai.label} CLI", lambda ai=ai: ai.find_exe() or "not installed (onboarding offers to install it)")

    lines.append("SELFTEST OK" if ok else "SELFTEST FAILED")
    return ok, lines


def _kb_version(root: Path) -> str:
    import json
    try:
        return json.loads((root / "meta.json").read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        return ""


def _report_path(argv: list[str]) -> str:
    for i, a in enumerate(argv):
        if a.startswith("--selftest="):
            return a.split("=", 1)[1]
        if a == "--selftest" and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            return argv[i + 1]
    return ""


def main(argv: list[str]) -> int:
    report = _report_path(argv)
    ok, lines = run(require_kb="--require-kb" in argv)
    text = "\n".join(lines) + "\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout:  # None in a windowed build
        sys.stdout.write(text)
    return 0 if ok else 1
