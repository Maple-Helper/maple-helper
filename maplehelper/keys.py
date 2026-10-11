"""Hotkey names, as Settings keeps them: the modifiers, then one key, joined by "+" ("Shift+F11", "Ctrl+Alt+T";
"" is no key). One key uses them, the bar over the game's show/hide (game_toolbar_hotkey): each OS layer turns a
name into its own codes (winapi.register_hotkey, macapi.carbon_combo), and the key box in Settings and the bar's
⚙ panel turns a key press into a name (ui/hotkeybox.py).

The game binds the plain F-keys to skills and items, and a plain letter is typing: an F-key needs a modifier, any
other key Ctrl, Alt or the Windows key (on a Mac ⌘, ⌥ or ⌃). Shift alone with a letter would take the capitals
from every program."""
from __future__ import annotations

import sys

# in the order a name lists them, by Qt's names: on a Mac Ctrl is ⌘ (Command) and Meta is ⌃ (Control)
MODIFIERS = ("Ctrl", "Alt", "Shift", "Meta")
FKEYS = tuple(f"F{i}" for i in range(1, 25))          # F13-F24: the macro keys of many gaming keyboards
LETTERS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
DIGITS = tuple("0123456789")
NAMED = ("Ins", "Del", "Home", "End", "PgUp", "PgDown")
KEYS = FKEYS + LETTERS + DIGITS + NAMED
# a key the system keeps for itself: Windows never lets a program register F12 (the debugger's), and macOS shows
# the desktop on F11 (with Shift too) while still reporting the key registered
RESERVED = {"win": "F12", "mac": "F11"}

# Windows virtual-key codes (RegisterHotKey)
WIN_VK = {**{f"F{i}": 0x6F + i for i in range(1, 25)}, **{c: ord(c) for c in LETTERS + DIGITS},
          "Ins": 0x2D, "Del": 0x2E, "Home": 0x24, "End": 0x23, "PgUp": 0x21, "PgDown": 0x22}
# a Mac's virtual key codes (HIToolbox kVK_*: positions on the ANSI layout; Ins is the Help key)
MAC_KEYCODES = {"F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98, "F8": 100, "F9": 101,
                "F10": 109, "F11": 103, "F12": 111, "F13": 105, "F14": 107, "F15": 113, "F16": 106, "F17": 64,
                "F18": 79, "F19": 80, "F20": 90,
                "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8, "V": 9, "B": 11, "Q": 12,
                "W": 13, "E": 14, "R": 15, "Y": 16, "T": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
                "9": 25, "7": 26, "8": 28, "0": 29, "O": 31, "U": 32, "I": 34, "P": 35, "L": 37, "J": 38, "K": 40,
                "N": 45, "M": 46,
                "Ins": 114, "Del": 117, "Home": 115, "End": 119, "PgUp": 116, "PgDown": 121}
MAC_SYMBOLS = {"Meta": "⌃", "Alt": "⌥", "Shift": "⇧", "Ctrl": "⌘"}      # in Apple's order


def _mac(mac: bool | None) -> bool:
    return sys.platform == "darwin" if mac is None else mac


def split(name) -> tuple[frozenset[str], str] | None:
    """("Shift+F11") -> ({"Shift"}, "F11"); None for no key ("") or a name that is no key here."""
    if not isinstance(name, str) or not name:
        return None
    *mods, key = name.split("+")
    if key not in KEYS or len(set(mods)) != len(mods) or not set(mods) <= set(MODIFIERS):
        return None
    return frozenset(mods), key


def join(mods, key: str) -> str:
    """The name of a key with these modifiers, listed in the one order (Ctrl, Alt, Shift, Meta)."""
    return "+".join([m for m in MODIFIERS if m in mods] + [key])


def usable(name, mac: bool | None = None) -> bool:
    """A name the bar's key may be on this system: a known key with a modifier (Ctrl, Alt or Meta unless it is an
    F-key), and not the one the system keeps (RESERVED)."""
    parts = split(name)
    if parts is None:
        return False
    mods, key = parts
    if key == RESERVED["mac" if _mac(mac) else "win"]:
        return False
    return bool(mods) if key in FKEYS else bool(mods & {"Ctrl", "Alt", "Meta"})


def label(name, mac: bool | None = None) -> str:
    """The name as the player reads it: "Shift+F11" ("Win" for the Windows key), on a Mac "⇧F10" in Apple's order
    (⌃⌥⇧⌘). A name that is no key comes back as it is."""
    parts = split(name)
    if parts is None:
        return name if isinstance(name, str) else ""
    mods, key = parts
    if _mac(mac):
        return "".join(sym for m, sym in MAC_SYMBOLS.items() if m in mods) + key
    return "+".join(["Win" if m == "Meta" else m for m in MODIFIERS if m in mods] + [key])


def from_native(code: int, mac: bool | None = None) -> str | None:
    """The key a native key code is (Windows' virtual key, a Mac's key code): for a press Qt names by the character
    it types (Shift+1 is "!" to Qt, a Hebrew layout's letter keys their Hebrew letters)."""
    table = MAC_KEYCODES if _mac(mac) else WIN_VK
    return next((k for k, c in table.items() if c == code), None)
