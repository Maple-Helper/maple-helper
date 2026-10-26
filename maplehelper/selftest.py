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
        # renderer process. A page with no network runs a script
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        profile = QWebEngineProfile()
        page = QWebEnginePage(profile)
        loop, got = QEventLoop(), []
        page.loadFinished.connect(lambda ok: page.runJavaScript("6 * 7", 0, lambda v: (got.append(v), loop.quit())))
        QTimer.singleShot(30_000, loop.quit)
        page.setHtml("<p>selftest</p>")
        loop.exec()
        page.deleteLater()
        if got != [42]:
            raise RuntimeError(f"the hidden page ran no script (got {got!r})")
        return "page runs scripts"
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
