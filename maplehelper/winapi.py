"""Small Windows helpers: find the game window, capture it, focus handling, autostart.

Deliberately non-invasive: no keyboard hooks, no key-state polling, no process access, nothing hidden
from screen capture. The game window is found by its title and captured from the screen like any
screenshot tool. No keyboard shortcuts: the official client in front takes every key from other programs.
Same interface as macapi.py; the app picks one through osapi.py.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys


from . import APP_NAME, capture
from .capture import grab_image, grab_jpeg

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GAME_TITLES = ("MapleStory Classic", "MapleStory", "Classic World", "MapleRoyals")
VK = {f"F{i}": 0x6F + i for i in range(1, 13)}   # F1=0x70 ... F12=0x7B
APP_ID = "MapleHelper.App"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def prepare_process() -> None:
    """Windows shows this identity (not "Python") for the taskbar and notifications."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def missing_permissions(request: bool = False) -> list[str]:
    return []   # Windows needs no extra grant for screen capture


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# windows that only mention the game: a browser tab, Discord, a folder, a text file ("MapleStory guide - YouTube -
# Google Chrome"). Never "the game": its screenshot went to the AI when the game was closed or minimized
NOT_GAME_CLASSES = {"Chrome_WidgetWin_0", "Chrome_WidgetWin_1", "MozillaWindowClass", "MozillaDialogClass",
                    "CabinetWClass", "ExploreWClass", "ApplicationFrameWindow", "Windows.UI.Core.CoreWindow",
                    "IEFrame", "OperaWindowClass", "Notepad", "Notepad++", "OpusApp", "XLMAIN", "PPTFrameClass",
                    "CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass"}        # Windows Terminal, a console
# a media player, a PDF, a photo, a terminal at a MapleStory folder were taken for the game too (audit SCR-2)
NOT_GAME_APPS = re.compile(r"\b(chrome|edge|firefox|opera|brave|vivaldi|discord|youtube|explorer|notepad|telegram|"
                           r"whatsapp|twitch|reddit|obs|vlc|acrobat|photos|paint|steam|spotify|powershell|cmd\.exe|"
                           r"terminal|visual studio code)\b", re.IGNORECASE)
_BIDI_MARKS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")    # Chrome wraps titles in them


def is_game_window(title: str, class_name: str) -> bool:
    """A window of the game itself, by its title and window class (no process access)."""
    t = _BIDI_MARKS.sub("", title).strip()
    if not any(g.lower() in t.lower() for g in GAME_TITLES) or "maple helper" in t.lower():
        return False
    return class_name not in NOT_GAME_CLASSES and not NOT_GAME_APPS.search(t)


def find_game_window() -> int | None:
    capture.LAST_PROBLEM = None
    found: list[int] = []
    minimized: list[int] = []
    own = os.getpid()

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and is_game_window(_title(hwnd), _class_name(hwnd)) and _pid(hwnd) != own:
            (minimized if user32.IsIconic(hwnd) else found).append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    if not found and minimized:
        capture.LAST_PROBLEM = "minimized"     # "the game isn't open" sent the player to open it (audit SCR-12)
    # the game's own title first; a title with a separator last (a window of some other app that slipped through)
    found.sort(key=lambda h: (_title(h) not in GAME_TITLES, any(s in _title(h) for s in (" - ", " | ", " — "))))
    return found[0] if found else None


def window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """Visible bounds (without the invisible resize border)."""
    r = wt.RECT()
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
    w, h = r.right - r.left, r.bottom - r.top
    return (r.left, r.top, w, h) if w > 50 and h > 50 else None


GA_ROOT = 2
WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
LWA_ALPHA = 0x2
# the taskbar may overlap a windowed game's edge: never private, never mistaken for the game
SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def _window_from_point(x: int, y: int) -> int:
    return user32.WindowFromPoint(wt.POINT(x, y)) or 0


def _invisible(hwnd) -> bool:
    """A layered window drawn fully transparent (alpha 0): it covers nothing the player sees."""
    if not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_LAYERED:
        return False
    alpha, flags = ctypes.c_ubyte(), wt.DWORD()
    if not user32.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags)):
        return False
    return bool(flags.value & LWA_ALPHA) and alpha.value == 0


def covered(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    """Another app's window is over the game (a browser or Discord in front, the game on another desktop).

    The capture is a screen grab: it takes whatever is drawn on top, so a covered game must not be captured at all
    (the covering window's pixels went to the AI as "the game"). Checked like a mouse click would be (WindowFromPoint)
    at a few points across the window; our own windows (the chat steps aside, transparent) and the game's own
    popups don't count, nor do click-through overlays, which WindowFromPoint passes by."""
    x, y, w, h = rect
    game_pid, own = _pid(hwnd), os.getpid()
    for fy in (0.15, 0.5, 0.85):
        for fx in (0.15, 0.5, 0.85):
            hit = _window_from_point(int(x + w * fx), int(y + h * fy))
            if not hit:
                continue           # off every monitor: nothing there to leak
            root = user32.GetAncestor(hit, GA_ROOT) or hit
            if root == hwnd or _pid(root) in (game_pid, own):
                continue
            if _class_name(root) in SHELL_CLASSES or _invisible(root):
                continue
            return True
    return False


def capture_game(hwnd: int | None = None) -> bytes | None:
    """JPEG of the game window (longest side capture.MAX_SIDE), or None if the game isn't found or another window
    covers it (then capture.LAST_PROBLEM says so, and the chat asks to bring the game to the front)."""
    hwnd = hwnd or find_game_window()
    if not hwnd:
        return None
    if user32.IsIconic(hwnd):          # the game remembered from earlier, minimized since
        capture.LAST_PROBLEM = "minimized"
        return None
    rect = window_rect(hwnd)
    if not rect:
        return None
    try:
        hidden = covered(hwnd, rect)
    except (OSError, AttributeError, ValueError):
        hidden = True        # can't tell what is on top: never risk sending another window
    capture.LAST_PROBLEM = "covered" if hidden else None
    return None if hidden else grab_jpeg(rect)


def focus_window(hwnd: int) -> None:
    if hwnd and user32.IsWindow(hwnd):
        user32.SetForegroundWindow(hwnd)


def activate_self(win_id: int) -> None:
    """Bring one of our own windows (Qt winId) to the front."""
    focus_window(win_id)


def seconds_since_self_activation() -> float:
    """Only macOS reads this (its reopen check): a second launch on Windows reaches the running copy itself."""
    return float("inf")


def float_over_fullscreen(win_id: int) -> None:
    """Nothing to do: on Windows a topmost window already floats over a borderless game."""


def set_autostart(enabled: bool, args: list[str]) -> bool:
    """Start at sign-in (HKCU Run key) with `args` appended to the app's command line. False (and logged) when the
    Run key couldn't be written: the setting looked on but wasn't (audit SCR-17)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                exe = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" -m maplehelper'
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, " ".join([exe, *args]))
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        import logging
        logging.getLogger("maplehelper").warning("start with Windows could not be set: %s", e)
        return False
    return True


# mss on Windows takes physical pixels: callers scale Qt's logical coordinates by the device pixel ratio
SCREEN_COORDS_ARE_PHYSICAL = True


def grab_screen(x: int, y: int, w: int, h: int):
    """Raw RGB capture of a screen rectangle (physical pixels) → PIL image."""
    return grab_image(x, y, w, h)
