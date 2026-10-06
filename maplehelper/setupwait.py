"""Windows: never start while an update is being installed.

The installer holds the "MapleHelperSetup" mutex (SetupMutex in packaging/installer.iss). Starting the app
then loads DLLs (Qt, numpy) the installer is about to replace; it can't, gives up halfway and leaves a
half-updated install. This runs before anything heavy is imported (standard library only), so it must stay
the first thing the launchers do.
"""
from __future__ import annotations

import sys
import time

SETUP_MUTEX = "MapleHelperSetup"
SYNCHRONIZE = 0x00100000


def setup_running() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenMutexW(SYNCHRONIZE, False, SETUP_MUTEX)
    if not h:
        return False
    k32.CloseHandle(h)
    return True


def wait_for_setup(limit_s: float = 900, step_s: float = 0.5) -> bool:
    """Wait while an update installs (up to limit_s). True when we had to wait."""
    waited = False
    start = time.monotonic()
    told = False
    while setup_running() and time.monotonic() - start < limit_s:
        waited = True
        if not told and time.monotonic() - start > 8:
            told = True
            _tell_waiting()      # an installer window left open would otherwise mean a silent, long wait
        time.sleep(step_s)
    return waited


WAITING_TEXT = {
    "he": "Maple Helper מתעדכן או מותקן כרגע. הוא ייפתח מעצמו כשההתקנה תסתיים.",
    "en": "Maple Helper is being updated or installed right now. It opens by itself when that's done.",
}


def _tell_waiting() -> None:
    """A small note while we wait (in its own thread: the wait goes on, and ends when the setup does)."""
    if sys.platform != "win32":
        return
    import ctypes
    import threading
    lang = _language()
    flags = 0x40 | 0x10000 | (0x80000 | 0x100000 if lang == "he" else 0)   # info, foreground, RTL in Hebrew
    threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(None, WAITING_TEXT.get(lang, WAITING_TEXT["en"]),
                                                                     "Maple Helper", flags), daemon=True).start()


DOWNLOAD_URL = "https://github.com/Maple-Helper/maple-helper/releases/latest/download/MapleHelper-Setup.exe"
BROKEN_TEXT = {
    "he": "חלק מהקבצים של Maple Helper חסרים או פגומים: כנראה עדכון שנקטע, או אנטי-וירוס שחסם קובץ.\n\n"
          "להתקין מחדש? ההגדרות והדמויות שלכם יישמרו.",
    "en": "Some Maple Helper files are missing or damaged (probably an interrupted update, or an antivirus "
          "blocked a file).\n\nReinstall now? Your settings and characters are kept.",
}


def _language() -> str:
    """The app's language from settings.json (standard library only: Qt may be what's missing)."""
    import json
    try:
        from .store import DATA_DIR
        return json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8")).get("language") or "he"
    except Exception:
        return "he"


STARTUP_TEXT = {
    "he": "Maple Helper לא הצליח לעלות. הפרטים נשמרו בקובץ:\n%s\n\nאפשר לשלוח אותו אלינו (דיווח על תקלה), "
          "או לנסות להפעיל מחדש את המחשב.",
    "en": "Maple Helper couldn't start. The details were saved to:\n%s\n\nYou can send it to us (Report a problem), "
          "or try restarting the PC.",
}
# a release that fails at start never reaches its own update check: the way out is the latest installer
STARTUP_NEWER_TEXT = {
    "he": "ייתכן שגרסה חדשה יותר כבר מתקנת את זה. להוריד את הגרסה האחרונה?",
    "en": "A newer version may already fix this. Download the latest version?",
}


RELEASES_URL = "https://github.com/Maple-Helper/maple-helper/releases/latest"
MAC_TEXT = {
    "startup": {"he": STARTUP_TEXT["he"].replace("המחשב", "ה-Mac"), "en": STARTUP_TEXT["en"].replace("the PC", "the Mac")},
    # written whole: an interrupted update or an antivirus (the Windows causes) don't fit a drag-installed Mac app
    "broken": {"he": "חלק מהקבצים של Maple Helper חסרים או פגומים (למשל, האפליקציה נפתחה מתוך קובץ ההתקנה אחרי שנסגר).\n\n"
                     "להוריד מחדש? (גררו את האפליקציה שוב לתיקיית Applications.) ההגדרות והדמויות שלכם יישמרו.",
               "en": "Some Maple Helper files are missing or damaged (for example, the app was opened from the disk "
                     "image after it was ejected).\n\nDownload it again? (Drag the app into Applications again.) "
                     "Your settings and characters are kept."},
    "download": {"he": "להורדה", "en": "Download"},
    "close": {"he": "סגירה", "en": "Close"},
}


def _mac_alert(message: str, buttons: list[str]) -> str:
    """A system alert on macOS without Qt (osascript): the bundle is a menu-bar app with no Dock icon, so a failed
    start showed nothing at all. Returns the button clicked ("" if the alert couldn't be shown)."""
    import subprocess
    script = ["on run argv",
              "set btns to items 2 thru -1 of argv",
              "display alert \"Maple Helper\" message (item 1 of argv) as critical buttons btns "
              "default button (count of btns)",
              "return button returned of result",
              "end run"]
    cmd = ["osascript"] + [a for line in script for a in ("-e", line)] + [message, *buttons]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _report_on_mac(exc: BaseException, where: str) -> None:
    lang = _language()
    pick = lambda key: MAC_TEXT[key].get(lang, MAC_TEXT[key]["en"])   # noqa: E731
    if not _damaged(exc):
        _mac_alert(pick("startup") % where, [pick("close")])
        return
    if _mac_alert(pick("broken"), [pick("close"), pick("download")]) == pick("download"):
        import webbrowser
        webbrowser.open(RELEASES_URL)


def _damaged(exc: BaseException) -> bool:
    """A file of the install is missing or broken (reinstalling helps), not some other startup error."""
    import zlib
    return isinstance(exc, (ImportError, EOFError, zlib.error)) or "bad marshal" in str(exc)


def report_broken_install(exc: BaseException) -> None:
    """A frozen build that can't import its own modules (a file is missing): explain and offer a reinstall,
    instead of PyInstaller's bare traceback window. Standard library only: Qt itself may be what's missing."""
    import traceback
    try:
        from .store import DATA_DIR
        logs = DATA_DIR / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "startup-error.log").write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            from .store import DATA_DIR
            where = str(DATA_DIR / "logs" / "startup-error.log")
        except Exception:
            where = "startup-error.log"
        _report_on_mac(exc, where)
        return
    if sys.platform != "win32":
        return
    import ctypes
    MB_YESNO, MB_ICONERROR, MB_SETFOREGROUND, IDYES = 0x4, 0x10, 0x10000, 6
    MB_RIGHT, MB_RTLREADING = 0x80000, 0x100000
    lang = _language()
    if not _damaged(exc):
        # a reinstall wouldn't help (e.g. the data folder can't be written): say what happened instead
        try:
            from .store import DATA_DIR
            where = str(DATA_DIR / "logs" / "startup-error.log")
        except Exception:
            where = "startup-error.log"
        rtl = MB_RIGHT | MB_RTLREADING if lang == "he" else 0
        text = STARTUP_TEXT.get(lang, STARTUP_TEXT["en"]) % where + "\n\n" + \
            STARTUP_NEWER_TEXT.get(lang, STARTUP_NEWER_TEXT["en"])
        if ctypes.windll.user32.MessageBoxW(None, text, "Maple Helper",
                                            MB_YESNO | MB_ICONERROR | MB_SETFOREGROUND | rtl) == IDYES:
            import webbrowser
            webbrowser.open(DOWNLOAD_URL)       # always the latest release's installer
        return
    # one language per box: Hebrew in a left-to-right box came out scrambled (seen in testing)
    flags = MB_YESNO | MB_ICONERROR | MB_SETFOREGROUND | (MB_RIGHT | MB_RTLREADING if lang == "he" else 0)
    answer = ctypes.windll.user32.MessageBoxW(None, BROKEN_TEXT.get(lang, BROKEN_TEXT["en"]), "Maple Helper", flags)
    if answer != IDYES:
        return
    # the update that broke it is usually still downloaded (and was checksum-verified then): run it again
    try:
        from .store import DATA_DIR
        cached = sorted((DATA_DIR / "updates").glob("MapleHelper-Setup-*.exe"), key=lambda f: f.stat().st_mtime)
    except Exception:
        cached = []
    if cached:
        import subprocess
        try:
            subprocess.Popen([str(cached[-1])], close_fds=True)
            return
        except OSError:
            pass
    import webbrowser
    webbrowser.open(DOWNLOAD_URL)
