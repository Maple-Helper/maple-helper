"""The OS layer the app talks to: winapi on Windows, macapi on macOS (same names in both)."""
import sys

if sys.platform == "darwin":
    from .macapi import (SCREEN_COORDS_ARE_PHYSICAL, Hotkeys, activate_self, capture_game, cursor_clip,
                         find_game_window,
                         float_over_fullscreen, focus_window, grab_screen, missing_permissions, prepare_process,
                         seconds_since_self_activation, set_autostart, window_rect)
else:
    from .winapi import (SCREEN_COORDS_ARE_PHYSICAL, Hotkeys, activate_self, capture_game, cursor_clip,
                         find_game_window,
                         float_over_fullscreen, focus_window, grab_screen, missing_permissions, prepare_process,
                         seconds_since_self_activation, set_autostart, window_rect)

IS_MAC = sys.platform == "darwin"


def open_url(url) -> bool:
    """A web page in the browser, and nothing else: on Windows webbrowser.open hands any other string to
    os.startfile, so a bad link from the knowledge base or the news feed (a path, a program) would be run."""
    import webbrowser
    if not isinstance(url, str) or not url.lower().startswith(("https://", "http://")):
        return False
    return webbrowser.open(url)

__all__ = ["IS_MAC", "SCREEN_COORDS_ARE_PHYSICAL", "Hotkeys", "activate_self", "capture_game", "cursor_clip",
           "find_game_window",
           "float_over_fullscreen", "focus_window", "grab_screen", "missing_permissions", "open_url", "prepare_process",
           "seconds_since_self_activation", "set_autostart", "window_rect"]
