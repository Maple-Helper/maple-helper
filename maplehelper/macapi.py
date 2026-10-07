"""macOS helpers, same interface as winapi.py: find the game window, capture it, focus, hotkeys, autostart.

Just as non-invasive as on Windows: no event taps, no key-state polling, no process access. Windows come
from the Quartz window list, capture is a screen grab, and keys are ordinary system hotkeys
(Carbon RegisterEventHotKey, the macOS counterpart of RegisterHotKey). The one privacy grant the player
gives is Screen Recording (window titles + the screenshot).

pyobjc and Carbon are loaded lazily, so the pure parts of this module import (and are tested) on any OS.
"""
from __future__ import annotations

import ctypes
import os
import plistlib
import re
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import capture
from .capture import grab_image, grab_jpeg
from .keys import split_key

GAME_TITLES = ("MapleStory Classic", "MapleStory", "Classic World", "MapleRoyals")
# virtual key codes (HIToolbox kVK_F1...). Macs send F-keys only with fn held, unless
# "Use F1, F2, etc. keys as standard function keys" is on (System Settings > Keyboard).
KEYCODES = {"F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97,
            "F7": 98, "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111}
LAUNCH_AGENT_LABEL = "com.maplehelper.app"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
# mss on macOS takes points (Qt's logical coordinates), not physical pixels
SCREEN_COORDS_ARE_PHYSICAL = False

# NSWindowCollectionBehavior: show on every Space, including another app's fullscreen Space
_CAN_JOIN_ALL_SPACES = 1 << 0
_FULLSCREEN_AUXILIARY = 1 << 8
_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1


def prepare_process() -> None:
    """Nothing to do: the bundle's Info.plist (LSUIElement) keeps the app out of the Dock."""


def missing_permissions(request: bool = False) -> list[str]:
    """['screen'] while Screen Recording is not granted. request=True shows macOS's prompt (first time only;
    after that the player flips the switch in System Settings > Privacy & Security)."""
    try:
        import Quartz
    except ImportError:
        return []
    if Quartz.CGPreflightScreenCaptureAccess():
        return []
    if request:
        Quartz.CGRequestScreenCaptureAccess()
    return ["screen"]


def screen_recording_granted() -> bool:
    """Without the grant macOS hands a screen grab back as the wallpaper and the menu bar, with no error: that
    went to the AI as "the game". (True where it can't be checked.)"""
    try:
        import Quartz
    except ImportError:
        return True
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def microphone_denied() -> bool:
    """The microphone is switched off for the app (System Settings > Privacy & Security > Microphone). macOS then
    records silence instead of failing, which read as "I didn't hear anything". AVFoundation is loaded through
    pyobjc's bridge (no extra package); False where it can't be checked."""
    try:
        import objc
        objc.loadBundle("AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework")
        device = objc.lookUpClass("AVCaptureDevice")
        # AVMediaTypeAudio is "soun"; AVAuthorizationStatus: 1 restricted, 2 denied
        return int(device.authorizationStatusForMediaType_("soun")) in (1, 2)
    except Exception:      # noqa: BLE001 - not on a Mac, or an older pyobjc: record as before
        return False


# ---------------------------------------------------------------- windows

# apps whose windows only mention the game (a guide in a browser, a Discord channel, a folder): never "the game"
NOT_GAME_OWNERS = {"safari", "google chrome", "chrome", "firefox", "microsoft edge", "arc", "brave browser", "opera",
                   "vivaldi", "orion", "discord", "finder", "preview", "textedit", "notes", "telegram", "whatsapp",
                   "slack", "messages", "mail", "terminal", "iterm2", "vlc", "steam", "code", "spotify"}
NOT_GAME_APPS = re.compile(r"\b(chrome|safari|edge|firefox|opera|brave|vivaldi|discord|youtube|telegram|whatsapp|"
                           r"twitch|reddit)\b", re.IGNORECASE)
SEPARATORS = (" - ", " | ", " — ", " – ")


def pick_game_window(infos, own_pid: int) -> int | None:
    """The game's window number from a Quartz window list (frontmost first), or None.

    Matches the window title, or the owning app's name (titles are empty without the Screen
    Recording grant). Under CrossOver/Wine the owner is a wine process, so the title is what
    identifies the game there. A browser tab or Discord channel that mentions the game is never it, even in front.
    """
    found = []
    for w in infos:
        if w.get("kCGWindowOwnerPID") == own_pid or w.get("kCGWindowLayer", 0) != 0:
            continue
        title, owner = str(w.get("kCGWindowName") or ""), str(w.get("kCGWindowOwnerName") or "")
        names = f"{title} {owner}".lower()
        if "maple helper" in names or owner.strip().lower() in NOT_GAME_OWNERS or NOT_GAME_APPS.search(names):
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) <= 50 or b.get("Height", 0) <= 50:
            continue
        if any(g.lower() in names for g in GAME_TITLES):
            found.append((title.strip() not in GAME_TITLES and owner.strip() not in GAME_TITLES,
                          any(sep in title for sep in SEPARATORS), len(found), int(w["kCGWindowNumber"])))
    return min(found)[-1] if found else None   # the game's own name first, then front to back


def covering_window(infos, game_id: int, own_pid: int) -> bool:
    """Another app's window lies over the game (frontmost first in infos). A screen grab takes whatever is on top,
    so a covered game is not captured at all. Our own windows, the game's, invisible ones and the menu bar / Dock
    (layer 20 and up) don't count; nor does an overlap under 1% of the game (a shadow's edge)."""
    game = next((w for w in infos if int(w.get("kCGWindowNumber", -1)) == int(game_id)), None)
    if game is None:
        return False
    gb = game.get("kCGWindowBounds") or {}
    gx, gy, gw, gh = (float(gb.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
    for w in infos:
        if w is game:
            return False                  # everything after it is behind the game
        if w.get("kCGWindowOwnerPID") in (own_pid, game.get("kCGWindowOwnerPID")):
            continue
        if not 0 <= int(w.get("kCGWindowLayer", 0)) < 20 or float(w.get("kCGWindowAlpha", 1)) == 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        x, y, ww, wh = (float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
        overlap = max(0.0, min(x + ww, gx + gw) - max(x, gx)) * max(0.0, min(y + wh, gy + gh) - max(y, gy))
        if overlap > 0.01 * gw * gh:
            return True
    return False


def _on_screen_windows() -> list[dict]:
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    return [dict(w) for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []]


def _window_info(window_id: int) -> dict | None:
    import Quartz
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionIncludingWindow, window_id) or []
    return dict(infos[0]) if infos else None


def find_game_window() -> int | None:
    # without Screen Recording the game may not be found at all (under CrossOver the title is what names it):
    # the chat then says how to grant it, not "the game isn't open"
    capture.LAST_PROBLEM = None if screen_recording_granted() else "screen_permission"
    return pick_game_window(_on_screen_windows(), os.getpid())


def window_rect(window_id) -> tuple[int, int, int, int] | None:
    """Bounds in desktop points (top-left origin, the coordinates Qt uses)."""
    info = _window_info(window_id) if window_id else None
    b = (info or {}).get("kCGWindowBounds")
    if not b:
        return None
    x, y, w, h = (int(b[k]) for k in ("X", "Y", "Width", "Height"))
    return (x, y, w, h) if w > 50 and h > 50 else None


def capture_game(window_id: int | None = None) -> bytes | None:
    """JPEG of the game window (longest side capture.MAX_SIDE), or None if the game isn't found, Screen Recording
    is off (the grab would be the wallpaper) or another window covers the game; capture.LAST_PROBLEM says which."""
    window_id = window_id or find_game_window()
    if not window_id:
        return None
    if not screen_recording_granted():
        capture.LAST_PROBLEM = "screen_permission"
        return None
    rect = window_rect(window_id)
    if not rect:
        return None
    try:
        hidden = covering_window(_on_screen_windows(), window_id, os.getpid())
    except Exception:      # noqa: BLE001 - can't tell what is on top: never risk sending another window
        hidden = True
    capture.LAST_PROBLEM = "covered" if hidden else None
    return None if hidden else grab_jpeg(rect)


def grab_screen(x: int, y: int, w: int, h: int):
    """Raw RGB capture of a screen rectangle (points) → PIL image."""
    return grab_image(x, y, w, h)


def focus_window(window_id: int) -> None:
    """Bring the app owning this window to the front (macOS activates apps, not windows)."""
    info = _window_info(window_id) if window_id else None
    if not info:
        return
    from AppKit import NSRunningApplication
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(info["kCGWindowOwnerPID"])
    if app:
        app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS)


_self_activated_at = 0.0


def activate_self(win_id: int) -> None:
    """Bring our own app (and with it, its Qt windows) to the front."""
    global _self_activated_at
    from AppKit import NSApplication
    _self_activated_at = time.monotonic()
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def seconds_since_self_activation() -> float:
    """How long ago activate_self ran: an activation right after it is ours, not the player reopening the app."""
    return time.monotonic() - _self_activated_at


def cursor_clip() -> tuple[int, int, int, int] | None:
    """Windows only (a game there keeps the mouse inside its picture)."""
    return None


def float_over_fullscreen(win_id: int) -> None:
    """Let a Qt window (winId = its NSView) appear on every Space, also over a fullscreen game."""
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.platformName() != "cocoa":
        return      # offscreen (tests, renders): the id is no NSView, and messaging it crashed the process
    try:
        import objc
        window = objc.objc_object(c_void_p=ctypes.c_void_p(int(win_id))).window()
        if window is not None:
            window.setCollectionBehavior_(window.collectionBehavior() | _CAN_JOIN_ALL_SPACES | _FULLSCREEN_AUXILIARY)
    except Exception:
        pass   # cosmetic: the window still works on the game's Space when the game is windowed


# ---------------------------------------------------------------- hotkeys (Carbon)

def fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


_EventHandler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
SIGNATURE = fourcc("MplH")
kEventClassKeyboard = fourcc("keyb")
kEventHotKeyPressed = 5
kEventParamDirectObject = fourcc("----")
typeEventHotKeyID = fourcc("hkid")
eventNotHandledErr = -9874
SHIFT_KEY = 1 << 9          # Carbon modifier flag (shiftKey)
_carbon_lib = None


def carbon():
    """Carbon's HIToolbox, with the signatures of the five calls hotkeys need."""
    global _carbon_lib
    if _carbon_lib is None:
        c = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        vp, u32, ulong = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_ulong   # ItemCount/ByteCount: unsigned long
        c.GetApplicationEventTarget.restype, c.GetApplicationEventTarget.argtypes = vp, []
        c.InstallEventHandler.restype = ctypes.c_int32
        c.InstallEventHandler.argtypes = [vp, _EventHandler, ulong, ctypes.POINTER(EventTypeSpec), vp, ctypes.POINTER(vp)]
        c.RegisterEventHotKey.restype = ctypes.c_int32
        c.RegisterEventHotKey.argtypes = [u32, u32, EventHotKeyID, vp, u32, ctypes.POINTER(vp)]
        c.UnregisterEventHotKey.restype, c.UnregisterEventHotKey.argtypes = ctypes.c_int32, [vp]
        c.GetEventParameter.restype = ctypes.c_int32
        c.GetEventParameter.argtypes = [vp, u32, u32, vp, ulong, vp, vp]
        _carbon_lib = c
    return _carbon_lib


class Hotkeys(QObject):
    """System-wide hotkeys (RegisterEventHotKey; delivered through the app's event loop). Emits pressed(hotkey_id)."""

    pressed = Signal(int)

    def __init__(self):
        super().__init__()
        c = carbon()
        self._refs: dict[int, ctypes.c_void_p] = {}
        self._handler = _EventHandler(self._on_event)   # keep a reference: Carbon holds only the pointer
        spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        self._handler_ref = ctypes.c_void_p()
        c.InstallEventHandler(c.GetApplicationEventTarget(), self._handler, 1, ctypes.byref(spec), None,
                              ctypes.byref(self._handler_ref))

    def _on_event(self, _call, event, _user_data) -> int:
        try:
            hk = EventHotKeyID()
            err = carbon().GetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID, None,
                                             ctypes.sizeof(hk), None, ctypes.byref(hk))
            if err == 0 and hk.signature == SIGNATURE and hk.id in self._refs:
                self.pressed.emit(int(hk.id))
                return 0
        except Exception:
            pass
        return eventNotHandledErr

    def register(self, hotkey_id: int, key_name: str) -> bool:
        """(Re)binds hotkey_id to key_name. False when the key is unknown or another app owns it."""
        self.unregister(hotkey_id)
        shift, fkey = split_key(key_name)
        code = KEYCODES.get(fkey)
        if code is None:
            return False
        c = carbon()
        ref = ctypes.c_void_p()
        err = c.RegisterEventHotKey(code, SHIFT_KEY if shift else 0, EventHotKeyID(SIGNATURE, hotkey_id), c.GetApplicationEventTarget(), 0,
                                    ctypes.byref(ref))
        if err != 0:
            return False
        self._refs[hotkey_id] = ref
        return True

    def unregister(self, hotkey_id: int) -> None:
        ref = self._refs.pop(hotkey_id, None)
        if ref:
            carbon().UnregisterEventHotKey(ref)

    def close(self) -> None:
        for hid in list(self._refs):
            self.unregister(hid)


# ---------------------------------------------------------------- autostart

def launch_agent(program: list[str]) -> dict:
    """A per-user LaunchAgent that starts the app at login."""
    return {"Label": LAUNCH_AGENT_LABEL, "ProgramArguments": program, "RunAtLoad": True,
            "ProcessType": "Interactive"}


def runs_from_a_temporary_place(exe: str) -> bool:
    """Opened straight from the disk image (/Volumes/...) or from a quarantined download macOS moved to a random
    path (App Translocation): that path is gone after an eject or a restart."""
    return exe.startswith("/Volumes/") or "/AppTranslocation/" in exe


def set_autostart(enabled: bool, args: list[str], path: Path = LAUNCH_AGENT) -> bool:
    """Start at login with `args` appended to the app's command line. False when it can't: the app runs from the
    disk image or a translocated copy (a login item there failed silently after the next restart); the player is
    asked to move it to Applications, and an existing login item is left as it is."""
    try:
        if enabled:
            frozen = getattr(sys, "frozen", False)
            if frozen and runs_from_a_temporary_place(sys.executable):
                return False
            program = [sys.executable] if frozen else [sys.executable, "-m", "maplehelper"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(launch_agent(program + list(args))))
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    return True
