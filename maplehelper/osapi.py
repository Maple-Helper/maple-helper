"""The OS layer the app talks to: winapi on Windows, macapi on macOS (same names in both)."""
import sys

if sys.platform == "darwin":
    from .macapi import (SCREEN_COORDS_ARE_PHYSICAL, Hotkeys, activate_self, capture_game, find_game_window,
                         float_over_fullscreen, focus_window, grab_screen, missing_permissions, prepare_process,
                         seconds_since_self_activation, set_autostart, window_rect)
else:
    from .winapi import (SCREEN_COORDS_ARE_PHYSICAL, Hotkeys, activate_self, capture_game, find_game_window,
                         float_over_fullscreen, focus_window, grab_screen, missing_permissions, prepare_process,
                         seconds_since_self_activation, set_autostart, window_rect)

IS_MAC = sys.platform == "darwin"

__all__ = ["IS_MAC", "SCREEN_COORDS_ARE_PHYSICAL", "Hotkeys", "activate_self", "capture_game", "find_game_window",
           "float_over_fullscreen", "focus_window", "grab_screen", "missing_permissions", "prepare_process",
           "seconds_since_self_activation", "set_autostart", "window_rect"]
