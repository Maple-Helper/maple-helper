"""Where the player is right now, read from the game's minimap every few seconds (ui/minimapscan.py): the chat's
character card says it under the level, and the map windows draw the way from there."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..minimap import Here


class _Location(QObject):
    """The latest read. `changed` fires only when it differs (the map, or the player's dot moved)."""

    changed = Signal(object)          # Here | None
    status = Signal(str)              # "" (no minimap chosen, or found), "unknown" (chosen, not recognized)

    def __init__(self):
        super().__init__()
        self.here: Here | None = None
        self.state = ""

    def set(self, here: Here | None) -> None:
        if here != self.here:
            self.here = here
            self.changed.emit(here)

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.status.emit(state)


LOCATION = _Location()
