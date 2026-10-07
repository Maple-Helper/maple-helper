"""Hotkey names: an F-key, alone ("F9", older settings) or with Shift ("Shift+F9", the default since 0.11).

Shift keeps the plain F-keys free for the game, which binds them to skills and items."""
from __future__ import annotations

import re

SHIFT = "Shift+"
_NAME = re.compile(r"(Shift\+)?(F(?:1[0-2]|[1-9]))")


def split_key(name: str) -> tuple[bool, str]:
    """("Shift+F9") -> (True, "F9"); ("F9") -> (False, "F9"). An unknown name comes back as (False, name)."""
    m = _NAME.fullmatch(name or "")
    return (bool(m.group(1)), m.group(2)) if m else (False, name or "")


def with_shift(name: str) -> str:
    """The Shift version of a plain F-key ("F9" -> "Shift+F9"); any other name unchanged."""
    shift, fkey = split_key(name)
    return f"{SHIFT}{fkey}" if not shift and _NAME.fullmatch(fkey) else name
