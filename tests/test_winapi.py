"""Windows: the game window is the game itself, and a covered game is never captured (the OS calls are faked)."""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows API layer")

GAME = "MapleStory Worlds-Old School Maple"


class FakeUser32:
    def __init__(self, windows):
        self.windows = windows            # hwnd -> dict(title, cls, pid, iconic)

    def EnumWindows(self, proc, _):
        for h in self.windows:
            proc(h, 0)

    def IsWindowVisible(self, h):
        return True

    def IsIconic(self, h):
        return self.windows[h].get("iconic", False)

    def GetAncestor(self, h, _):
        return self.windows.get(h, {}).get("root", h)

    def GetWindowLongW(self, h, _):
        return 0


@pytest.fixture
def win(monkeypatch):
    from maplehelper import capture, winapi

    def setup(windows, hits=None):
        fake = FakeUser32(windows)
        monkeypatch.setattr(winapi, "user32", fake)
        monkeypatch.setattr(winapi, "_title", lambda h: windows[h]["title"])
        monkeypatch.setattr(winapi, "_class_name", lambda h: windows[h].get("cls", "UnityWndClass"))
        monkeypatch.setattr(winapi, "_pid", lambda h: windows[h].get("pid", 100 + h))
        monkeypatch.setattr(winapi, "_window_from_point", lambda x, y: (hits or (lambda x, y: 1))(x, y))
        monkeypatch.setattr(winapi, "window_rect", lambda h: (0, 0, 1600, 900))
        monkeypatch.setattr(winapi, "grab_jpeg", lambda rect: b"jpeg")
        return winapi, capture
    return setup


CHROME = {"title": "‪MapleStory Classic best training spots - YouTube - Google Chrome‬",
          "cls": "Chrome_WidgetWin_1"}
DISCORD = {"title": "Discord | #maplestory-classic | MapleGuild", "cls": "Chrome_WidgetWin_1"}


@pytest.mark.parametrize("others", [[CHROME], [DISCORD], [CHROME, DISCORD],
                                    [{"title": "MapleStory notes.txt - Notepad", "cls": "Notepad"}],
                                    [{"title": "MapleStory", "cls": "CabinetWClass"}],                # a folder
                                    [{"title": "MapleStory guide - Mozilla Firefox", "cls": "SomethingNew"}],
                                    # audit SCR-2: a player, a terminal, a PDF, a photo, Paint, Steam
                                    [{"title": "MapleStory_BGM.mp3 - VLC media player", "cls": "Qt5QWindowIcon"}],
                                    [{"title": "C:\\Games\\MapleStory", "cls": "CASCADIA_HOSTING_WINDOW_CLASS"}],
                                    [{"title": "C:\\Windows\\system32\\cmd.exe - python maplestory.py"}],
                                    [{"title": "MapleStory guide.pdf - Adobe Acrobat Reader (64-bit)"}],
                                    [{"title": "MapleStory.png - Photos"}], [{"title": "MapleStory - Paint"}],
                                    [{"title": "MapleStory Worlds - Steam"}]])
def test_a_window_that_only_mentions_the_game_is_never_the_game(win, others):
    winapi, _ = win({i + 1: w for i, w in enumerate(others)})
    assert winapi.find_game_window() is None
    # with the game there (minimized: skipped), still nothing else
    windows = {i + 1: w for i, w in enumerate(others)} | {9: {"title": GAME, "iconic": True}}
    winapi, _ = win(windows)
    assert winapi.find_game_window() is None


def test_the_game_is_found_among_browser_windows(win):
    winapi, _ = win({1: CHROME, 2: DISCORD, 3: {"title": GAME}})
    assert winapi.find_game_window() == 3


def test_our_own_window_is_never_the_game(win, monkeypatch):
    import os
    winapi, _ = win({1: {"title": "MapleStory", "pid": os.getpid()}})
    assert winapi.find_game_window() is None


def test_a_covered_game_is_not_captured(win):
    """Chrome maximized over the game: its pixels must never go to the AI as the game."""
    winapi, capture = win({3: {"title": GAME}, 1: CHROME}, hits=lambda x, y: 1)
    assert winapi.capture_game(3) is None
    assert capture.LAST_PROBLEM == "covered" and capture.problem_key() == "shot_game_covered"


def test_partly_covered_counts_as_covered(win):
    winapi, capture = win({3: {"title": GAME}, 1: DISCORD}, hits=lambda x, y: 1 if x > 1000 else 3)
    assert winapi.capture_game(3) is None and capture.LAST_PROBLEM == "covered"


def test_game_in_front_is_captured(win):
    import os
    own = {"title": "Maple Helper", "pid": os.getpid()}
    tray = {"title": "", "cls": "Shell_TrayWnd"}
    popup = {"title": "Quest", "pid": 103}                  # the game's own popup (same process)
    # the chat stepping aside (ours), the taskbar over the edge, the game's popup, off-screen points
    seq = iter([3, 5, 6, 7, 0, 3, 3, 3, 3])
    winapi, capture = win({3: {"title": GAME}, 5: own, 6: tray, 7: popup}, hits=lambda x, y: next(seq))
    assert winapi.capture_game(3) == b"jpeg" and capture.LAST_PROBLEM is None and capture.problem_key() is None


def test_a_hidden_layered_window_does_not_count(win, monkeypatch):
    winapi, capture = win({3: {"title": GAME}, 1: {"title": "invisible helper"}}, hits=lambda x, y: 1)
    monkeypatch.setattr(winapi, "_invisible", lambda h: h == 1)
    assert winapi.capture_game(3) == b"jpeg"


def test_unknown_cover_state_is_never_captured(win, monkeypatch):
    winapi, capture = win({3: {"title": GAME}})

    def broken(x, y):
        raise OSError("no")
    monkeypatch.setattr(winapi, "_window_from_point", broken)
    assert winapi.capture_game(3) is None and capture.LAST_PROBLEM == "covered"


def test_a_minimized_game_says_so(win):
    """Not "the game isn't open": the player is told to bring it back (audit SCR-12)."""
    winapi, capture = win({1: CHROME, 3: {"title": GAME, "iconic": True}})
    assert winapi.find_game_window() is None and capture.problem_key() == "shot_game_minimized"
    assert winapi.capture_game(3) is None and capture.LAST_PROBLEM == "minimized"
    winapi, capture = win({1: CHROME})
    assert winapi.find_game_window() is None and capture.problem_key() is None


def test_looking_for_the_game_resets_the_last_problem(win):
    winapi, capture = win({1: CHROME})
    capture.LAST_PROBLEM = "covered"
    assert winapi.find_game_window() is None and capture.problem_key() is None
