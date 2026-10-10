"""Where the player is right now, read from the game's minimap every few seconds (ui/minimapscan.py): the chat's
character card says it under the level, and the map windows draw the way from there."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..minimap import Here


class _Location(QObject):
    """The latest read. `changed` fires only when it differs (the map, or the player's dot moved). `followed` is the
    fast follow between reads (ui/minimapscan.py): where the last aligned map's picture lies in the box now, as
    (map, View | None), None when it no longer matches (another map, the loading screen)."""

    changed = Signal(object)          # Here | None
    status = Signal(str)              # "" (no minimap chosen, or found), "unknown" (chosen, not recognized)
    followed = Signal(object)         # (map, View | None)

    def __init__(self):
        super().__init__()
        self.here: Here | None = None
        self.state = ""
        self.follow: tuple | None = None

    def set(self, here: Here | None) -> None:
        if here != self.here:
            self.here = here
            self.changed.emit(here)

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.status.emit(state)

    def set_follow(self, follow: tuple | None) -> None:
        if follow != self.follow:
            self.follow = follow
            self.followed.emit(follow)


LOCATION = _Location()
