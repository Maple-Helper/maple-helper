"""macOS layer logic that needs no Mac: window matching, hotkey codes, the login item (pyobjc/Carbon load lazily)."""
import ctypes
import plistlib

from maplehelper import macapi

OWN_PID = 4242


def win(number, name="", owner="", pid=100, layer=0, w=1280, h=720):
    return {"kCGWindowNumber": number, "kCGWindowName": name, "kCGWindowOwnerName": owner,
            "kCGWindowOwnerPID": pid, "kCGWindowLayer": layer,
            "kCGWindowBounds": {"X": 0, "Y": 25, "Width": w, "Height": h}}


def test_picks_the_game_by_title():
    infos = [win(1, "Safari", "Safari"), win(7, "MapleStory Classic", "wine64-preloader")]
    assert macapi.pick_game_window(infos, OWN_PID) == 7


def test_picks_the_game_by_owner_without_screen_recording():
    # no Screen Recording grant: macOS hides window titles, the owning app's name still shows
    assert macapi.pick_game_window([win(3, "", "MapleStory")], OWN_PID) == 3


def test_frontmost_match_wins():
    infos = [win(5, "MapleStory Classic"), win(6, "MapleStory Classic")]
    assert macapi.pick_game_window(infos, OWN_PID) == 5


def test_skips_own_windows_menu_bar_and_tiny_windows():
    infos = [
        win(1, "Maple Helper", "Maple Helper", pid=OWN_PID),        # the overlay itself
        win(2, "MapleStory updates", "Python", pid=OWN_PID),         # anything of ours
        win(3, "MapleStory", "Menu bar", layer=25),                  # status item, not a document window
        win(4, "MapleStory", "Launcher", w=40, h=40),                # tiny helper window
        win(5, "Maple Helper - notes", "TextEdit"),                  # someone else's window about us
    ]
    assert macapi.pick_game_window(infos, OWN_PID) is None


def test_no_game_running():
    assert macapi.pick_game_window([win(1, "Finder", "Finder")], OWN_PID) is None


def test_every_hotkey_choice_has_a_mac_key_code():
    assert set(macapi.KEYCODES) == {f"F{i}" for i in range(1, 13)}
    assert len(set(macapi.KEYCODES.values())) == 12


def test_carbon_constants_and_struct_layout():
    # Carbon reads these by value/pointer: the layouts must match HIToolbox exactly
    assert macapi.fourcc("keyb") == 0x6B657962
    assert macapi.kEventParamDirectObject == 0x2D2D2D2D
    assert ctypes.sizeof(macapi.EventHotKeyID) == 8
    assert ctypes.sizeof(macapi.EventTypeSpec) == 8


def test_autostart_writes_and_removes_a_launch_agent(tmp_path, monkeypatch):
    agent = tmp_path / "LaunchAgents" / "com.maplehelper.app.plist"
    exe = "/Applications/Maple Helper.app/Contents/MacOS/Maple Helper"
    monkeypatch.setattr(macapi.sys, "frozen", True, raising=False)
    monkeypatch.setattr(macapi.sys, "executable", exe)

    macapi.set_autostart(True, ["--background"], agent)
    data = plistlib.loads(agent.read_bytes())
    assert data["Label"] == "com.maplehelper.app"
    assert data["ProgramArguments"] == [exe, "--background"]
    assert data["RunAtLoad"] is True

    macapi.set_autostart(False, ["--background"], agent)
    assert not agent.exists()
    macapi.set_autostart(False, ["--background"], agent)   # already off: no error


def test_autostart_from_source_runs_the_module(tmp_path, monkeypatch):
    agent = tmp_path / "agent.plist"
    monkeypatch.delattr(macapi.sys, "frozen", raising=False)
    monkeypatch.setattr(macapi.sys, "executable", "/usr/local/bin/python3")
    macapi.set_autostart(True, ["--background"], agent)
    assert plistlib.loads(agent.read_bytes())["ProgramArguments"] == ["/usr/local/bin/python3", "-m", "maplehelper",
                                                                       "--background"]


def at(w, x, y, width, height, **extra):
    return {**w, "kCGWindowBounds": {"X": x, "Y": y, "Width": width, "Height": height}, **extra}


GAME = win(11, "MapleStory Worlds-Old School Maple", "MapleStory Worlds", pid=300, w=1600, h=900)


def test_a_browser_tab_in_front_never_beats_the_game():
    infos = [win(21, "MapleStory Classic Night Lord guide - YouTube", "Google Chrome"),
             win(22, "#maplestory-classic", "Discord"),
             GAME]
    assert macapi.pick_game_window(infos, OWN_PID) == 11


def test_browser_and_discord_alone_are_not_the_game():
    infos = [win(21, "MapleStory Classic Night Lord guide - YouTube", "Google Chrome"),
             win(22, "MapleStory Classic guide", "Safari"), win(23, "", "Discord"),
             win(24, "MapleStory Classic guide — Arc", "Arc"), win(25, "MapleStory", "Finder")]
    assert macapi.pick_game_window(infos, OWN_PID) is None
    # a terminal or a player at a MapleStory file (audit SCR-2)
    infos = [win(26, "maplestory — -zsh", "Terminal"), win(27, "MapleStory_BGM.mp3", "VLC")]
    assert macapi.pick_game_window(infos, OWN_PID) is None


def test_the_games_own_name_wins_over_a_separator_title():
    infos = [win(31, "MapleStory - patch notes", "SomeViewer"), win(32, "MapleStory", "wine64-preloader")]
    assert macapi.pick_game_window(infos, OWN_PID) == 32


def test_a_window_over_the_game_counts_as_covering():
    chrome = at(win(21, "", "Google Chrome", pid=500), 0, 25, 1600, 900)
    assert macapi.covering_window([chrome, GAME], 11, OWN_PID)
    # behind the game, ours, the game's own, the menu bar, invisible, or barely touching: not covering
    assert not macapi.covering_window([GAME, chrome], 11, OWN_PID)
    ours = at(win(1, "", "Maple Helper", pid=OWN_PID), 0, 25, 800, 800)
    popup = at(win(2, "", "MapleStory Worlds", pid=300), 100, 100, 400, 300)
    menubar = at(win(3, "", "Window Server", pid=1, layer=24), 0, 0, 1600, 25)
    hidden = at(win(4, "", "Ghost", pid=600), 0, 0, 1600, 900, kCGWindowAlpha=0)
    edge = at(win(5, "", "Notes", pid=700), 1590, 25, 300, 300)          # ~0.2% of the game
    assert not macapi.covering_window([ours, popup, menubar, hidden, edge, GAME], 11, OWN_PID)
    floating = at(win(6, "", "Discord", pid=800, layer=3), 1000, 500, 400, 300)   # a picture-in-picture panel
    assert macapi.covering_window([floating, GAME], 11, OWN_PID)


def capture_env(monkeypatch, infos, granted=True):
    from maplehelper import capture
    monkeypatch.setattr(macapi, "screen_recording_granted", lambda: granted)
    monkeypatch.setattr(macapi, "_on_screen_windows", lambda: infos)
    monkeypatch.setattr(macapi, "window_rect", lambda wid: (0, 25, 1600, 900))
    monkeypatch.setattr(macapi, "grab_jpeg", lambda rect: b"jpeg")
    return capture


def test_without_screen_recording_nothing_is_captured(monkeypatch):
    """The grab would be the wallpaper: never sent as the game; the chat says how to grant it."""
    capture = capture_env(monkeypatch, [GAME], granted=False)
    assert macapi.find_game_window() == 11 and capture.problem_key() == "perm_screen_body"
    assert macapi.capture_game(11) is None and capture.problem_key() == "perm_screen_body"


def test_covered_game_is_not_captured_and_front_game_is(monkeypatch):
    chrome = at(win(21, "", "Google Chrome", pid=500), 0, 25, 1600, 900)
    capture = capture_env(monkeypatch, [chrome, GAME])
    assert macapi.capture_game(11) is None and capture.problem_key() == "shot_game_covered"
    capture = capture_env(monkeypatch, [GAME, chrome])
    assert macapi.capture_game(11) == b"jpeg" and capture.problem_key() is None


def test_microphone_check_is_quiet_off_a_mac():
    import sys
    if sys.platform == "darwin":
        return
    assert macapi.microphone_denied() is False


def test_autostart_refuses_a_disk_image_or_translocated_copy(tmp_path, monkeypatch):
    agent = tmp_path / "agent.plist"
    agent.write_bytes(b"the login item from /Applications")
    monkeypatch.setattr(macapi.sys, "frozen", True, raising=False)
    for exe in ("/Volumes/Maple Helper/Maple Helper.app/Contents/MacOS/Maple Helper",
                "/private/var/folders/x/T/AppTranslocation/ABC/d/Maple Helper.app/Contents/MacOS/Maple Helper"):
        monkeypatch.setattr(macapi.sys, "executable", exe)
        assert macapi.set_autostart(True, ["--background"], agent) is False
        assert agent.read_bytes() == b"the login item from /Applications"       # left as it was
    monkeypatch.setattr(macapi.sys, "executable", "/Applications/Maple Helper.app/Contents/MacOS/Maple Helper")
    assert macapi.set_autostart(True, ["--background"], agent) is True
    assert plistlib.loads(agent.read_bytes())["ProgramArguments"][0].startswith("/Applications/")


def test_mac_icon_has_transparent_corners():
    # the .app/.dmg icon is built from icon-source.png: an opaque white square showed around the rounded tile
    from pathlib import Path

    from PySide6.QtGui import QImage
    img = QImage(str(Path(__file__).resolve().parent.parent / "assets" / "brand" / "icon-source.png"))
    assert img.hasAlphaChannel()
    w, h = img.width(), img.height()
    assert all(img.pixelColor(x, y).alpha() == 0 for x, y in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)))
    assert img.pixelColor(w // 2, h // 2).alpha() == 255
