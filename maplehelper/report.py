"""Diagnostics: a rotating log file, and a one-click problem report for players to send.

The report holds what helps fix a bug (log, version, settings, system) and nothing private:
no conversations, no screenshots, no characters, no API key, no email.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import platform
import sys
import threading
import time
import zipfile
from pathlib import Path

from .store import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "maplehelper.log"
# the settings a report carries, by name: a new setting stays out until it is added here (a deny-list let the stats'
# install_id in, which tied the anonymous usage stats to the player who sent the report)
REPORT_SETTINGS = ("language", "hotkey_toggle", "hotkey_voice", "appearance", "font_size", "answer_length",
                   "start_with_windows", "voice_send_immediately", "provider", "model", "codex_model", "grok_model",
                   "gemini_model", "last_model", "api_key_fallback", "onboarding_done", "tour_done", "usage",
                   "saver_mode", "seen_version", "instant_answers", "telemetry", "grind_auto")
log = logging.getLogger("maplehelper")


def setup_logging() -> None:
    """Log to %APPDATA%/MapleHelper/logs (3 x 1 MB), including crashes on any thread."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    def on_crash(exc_type, exc, tb):
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = on_crash
    threading.excepthook = lambda a: log.critical(f"uncaught exception in thread {a.thread and a.thread.name}",
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def system_info(version: str, kb_version: str, ai_status: str) -> dict:
    """ai_status: the active AI provider and its sign-in state, e.g. "Codex: ok"."""
    return {"app_version": version, "kb_version": kb_version, "ai": ai_status,
            "windows": platform.platform(), "python": sys.version.split()[0],
            "frozen": bool(getattr(sys, "frozen", False)), "created": time.strftime("%Y-%m-%d %H:%M:%S")}


def build_report(out_dir: Path, info: dict, settings: dict) -> Path:
    """Zip the logs + info + settings (minus private bits) into out_dir; returns the zip path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"MapleHelper-report-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    clean = {k: settings[k] for k in REPORT_SETTINGS if k in settings}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=1))
        z.writestr("settings.json", json.dumps(clean, ensure_ascii=False, indent=1))
        for f in sorted(LOG_DIR.glob("maplehelper.log*")) + sorted(LOG_DIR.glob("update-*.log"))[-2:] + \
                sorted(LOG_DIR.glob("startup-error.log")):
            z.write(f, f"logs/{f.name}")
    return path


def save_report(desktop: Path, info: dict, settings: dict) -> tuple[Path, str]:
    """The report on the desktop, or in the app's data folder when the desktop can't take it (Controlled Folder
    Access, an offline OneDrive folder). Returns the zip and the i18n key naming where it went.

    macOS goes straight to the data folder: Desktop (and Downloads) are privacy-protected there, so a write asks
    for access with a system prompt that blocks the chat, and "Don't Allow" left it in the data folder anyway."""
    if sys.platform != "darwin":
        try:
            return build_report(desktop, info, settings), "report_saved"
        except OSError:
            pass
    return build_report(DATA_DIR, info, settings), "report_saved_data"
