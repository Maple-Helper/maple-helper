"""Screen grabs: the black bars of a pillarboxed game are cut off before the AI gets the picture."""
import io

from PIL import Image, ImageDraw

from maplehelper import capture


def game_shot(w=3440, h=1440, bars=440, top=0, fill=(90, 140, 60)):
    img = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(img).rectangle((bars, top, w - bars - 1, h - top - 1), fill=fill)
    return img


def grab(monkeypatch, img, cursor=None):
    monkeypatch.setattr(capture, "grab_image", lambda *rect: img.copy())
    monkeypatch.setattr(capture, "_cursor_in", lambda *rect: cursor)
    return Image.open(io.BytesIO(capture.grab_jpeg((0, 0, *img.size))))


def test_pillarbox_bars_are_trimmed(monkeypatch):
    out = grab(monkeypatch, game_shot(), cursor=(1000, 700))
    assert capture.LAST_FULL.size == (2560, 1440)
    assert out.size == (1568, 882)                        # the game gets the whole width the AI takes in
    assert capture.LAST_CURSOR == (560, 700)              # same coordinates as LAST_FULL


def test_cursor_over_a_bar_is_dropped(monkeypatch):
    grab(monkeypatch, game_shot(), cursor=(100, 700))
    assert capture.LAST_CURSOR is None


def test_letterbox_bars_are_trimmed(monkeypatch):
    grab(monkeypatch, game_shot(1600, 1200, bars=0, top=150))
    assert capture.LAST_FULL.size == (1600, 900)


def test_a_picture_without_bars_is_left_alone(monkeypatch):
    grab(monkeypatch, game_shot(1600, 900, bars=0), cursor=(5, 5))
    assert capture.LAST_FULL.size == (1600, 900) and capture.LAST_CURSOR == (5, 5)


def test_dark_scenes_are_not_mistaken_for_bars(monkeypatch):
    grab(monkeypatch, Image.new("RGB", (1600, 900)))                     # all black: a loading screen
    assert capture.LAST_FULL.size == (1600, 900)
    img = game_shot(1600, 900, bars=0)
    ImageDraw.Draw(img).rectangle((0, 0, 299, 899), fill=(0, 0, 0))      # dark on one side only: game content
    grab(monkeypatch, img)
    assert capture.LAST_FULL.size == (1600, 900)
    grab(monkeypatch, game_shot(1600, 900, bars=500))                    # most of it black: not bars
    assert capture.LAST_FULL.size == (1600, 900)


def test_problem_keys():
    capture.LAST_PROBLEM = "screen_permission"
    assert capture.problem_key() == "perm_screen_body"
    capture.LAST_PROBLEM = None
    assert capture.problem_key() is None


def test_the_play_area_is_the_picture_without_its_bars(monkeypatch):
    # a game in front keeps the mouse inside its picture: the chat and its bubble go there, never onto the bars
    grab(monkeypatch, game_shot())
    assert capture.PLAY_AREA == (440, 0, 2560, 1440)
    grab(monkeypatch, game_shot(bars=0))
    assert capture.PLAY_AREA == (0, 0, 3440, 1440)
