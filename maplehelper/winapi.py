"""Small Windows helpers: find the game window, capture it, focus handling, hotkeys, autostart.

Deliberately non-invasive: no keyboard hooks, no key-state polling, no process access, nothing hidden
from screen capture. The game window is found by its title and captured from the screen like any
screenshot tool; keys are ordinary Windows hotkeys (RegisterHotKey).
Same interface as macapi.py; the app picks one through osapi.py.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

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
    return []   # Windows needs no extra grant for screen capture or hotkeys


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


def is_exclusive_fullscreen(hwnd: int | None) -> bool:
    """Best-effort: a window covering its monitor with no Borderless flag set by the game.

    True exclusive fullscreen can't be detected reliably from outside; the overlay
    shows a hint only when it failed to appear over a covering window.
    """
    return False


def foreground_window() -> int:
    return user32.GetForegroundWindow()


def focus_window(hwnd: int) -> None:
    if hwnd and user32.IsWindow(hwnd):
        user32.SetForegroundWindow(hwnd)


def activate_self(win_id: int) -> None:
    """Bring one of our own windows (Qt winId) to the front."""
    focus_window(win_id)


def float_over_fullscreen(win_id: int) -> None:
    """Nothing to do: on Windows a topmost window already floats over a borderless game."""


MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312


def register_hotkey(hwnd: int, hotkey_id: int, key_name: str) -> bool:
    vk = VK.get(key_name)
    return bool(vk and user32.RegisterHotKey(wt.HWND(hwnd), hotkey_id, MOD_NOREPEAT, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    user32.UnregisterHotKey(wt.HWND(hwnd), hotkey_id)


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self.on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wt.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self.on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class Hotkeys(QObject):
    """System-wide hotkeys (RegisterHotKey on a hidden native window). Emits pressed(hotkey_id)."""

    pressed = Signal(int)

    def __init__(self):
        super().__init__()
        self._host = QWidget()        # hotkeys live on a hidden native window
        self._host.winId()
        self._ids: set[int] = set()
        self._filter = _HotkeyFilter(self.pressed.emit)
        QApplication.instance().installNativeEventFilter(self._filter)

    def register(self, hotkey_id: int, key_name: str) -> bool:
        """(Re)binds hotkey_id to key_name. False when another app already owns the key."""
        self.unregister(hotkey_id)
        ok = register_hotkey(int(self._host.winId()), hotkey_id, key_name)
        if ok:
            self._ids.add(hotkey_id)
        return ok

    def unregister(self, hotkey_id: int) -> None:
        unregister_hotkey(int(self._host.winId()), hotkey_id)
        self._ids.discard(hotkey_id)

    def close(self) -> None:
        for hid in list(self._ids):
            self.unregister(hid)


def set_autostart(enabled: bool, args: list[str]) -> None:
    """Start at sign-in (HKCU Run key) with `args` appended to the app's command line."""
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
    except OSError:
        pass


# ---------------------------------------------------------------- glass material

class _ACCENT(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WCAD(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


def transparency_enabled() -> bool:
    """Windows 'Transparency effects' setting (the reduced-transparency preference)."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "EnableTransparency")[0])
    except OSError:
        return True


def enable_acrylic(hwnd: int, tint_rgba: tuple[int, int, int, int] = (18, 14, 12, 80)) -> bool:
    """Blur what is behind the window (the game) — the glass material. Returns False if unsupported."""
    try:
        r, g, b, a = tint_rgba
        accent = _ACCENT(ACCENT_ENABLE_ACRYLICBLURBEHIND, 0x20 | 0x40 | 0x80 | 0x100, (a << 24) | (b << 16) | (g << 8) | r, 0)
        data = _WCAD(WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent))
        return bool(user32.SetWindowCompositionAttribute(wt.HWND(hwnd), ctypes.byref(data)))
    except (AttributeError, OSError):
        return False


def round_window(hwnd: int, w: int, h: int, radius: int) -> None:
    """Clip the window (and its blur) to a rounded rectangle."""
    gdi32 = ctypes.windll.gdi32
    rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
    user32.SetWindowRgn(wt.HWND(hwnd), rgn, True)


class _BLURBEHIND(ctypes.Structure):
    _fields_ = [("dwFlags", wt.DWORD), ("fEnable", wt.BOOL), ("hRgnBlur", wt.HRGN),
                ("fTransitionOnMaximized", wt.BOOL)]


class _MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def glass_window(hwnd: int, tint_rgba=(18, 14, 12, 70), shadow: bool = True) -> bool:
    """Acrylic material for a normal (non-layered) frameless window, the way DWM expects it:
    blur-behind on the client area + acrylic accent + rounded corners and a system shadow."""
    ok = False
    try:
        bb = _BLURBEHIND(1, True, None, False)
        dwmapi.DwmEnableBlurBehindWindow(wt.HWND(hwnd), ctypes.byref(bb))
        ok = enable_acrylic(hwnd, tint_rgba)
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        dwmapi.DwmSetWindowAttribute(wt.HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))
        if shadow:
            m = _MARGINS(-1, -1, -1, -1)
            dwmapi.DwmExtendFrameIntoClientArea(wt.HWND(hwnd), ctypes.byref(m))
    except (AttributeError, OSError):
        return False
    return ok


# mss on Windows takes physical pixels: callers scale Qt's logical coordinates by the device pixel ratio
SCREEN_COORDS_ARE_PHYSICAL = True


def grab_screen(x: int, y: int, w: int, h: int):
    """Raw RGB capture of a screen rectangle (physical pixels) → PIL image."""
    return grab_image(x, y, w, h)
