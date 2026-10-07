"""Screen grabs shared by the Windows and macOS layers (both capture like any screenshot tool)."""
from __future__ import annotations

import io

import mss
from PIL import Image

# the most the AI takes in without shrinking it again: on a 3440 px ultrawide, 1280 left inventory icons
# ~12 px wide and the AI couldn't tell them apart (live test)
MAX_SIDE = 1568
# the latest grab at full resolution: the portrait is cut from it (a name tag is ~10 px tall at that size)
LAST_FULL: Image.Image | None = None
# where the mouse was in that grab (image pixels): the game draws its own hand cursor, and over an inventory
# slot it looked like an item (live test)
LAST_CURSOR: tuple[int, int] | None = None
# the game's picture in that grab, in screen pixels (x, y, w, h), without its pillarbox / letterbox bars: a game in
# front keeps the mouse inside it, so the chat and its bubble must sit there (on the bars they couldn't be clicked)
PLAY_AREA: tuple[int, int, int, int] | None = None
# why the latest look for the game gave no screenshot although the game may be open: "covered" (another window is
# over it: its pixels are never sent as the game), "screen_permission" (macOS Screen Recording is off: the grab would
# be the wallpaper), None otherwise. Reset by every find_game_window; the chat says it instead of "no game".
LAST_PROBLEM: str | None = None
PROBLEM_TEXT = {"covered": "shot_game_covered", "screen_permission": "perm_screen_body",
                "minimized": "shot_game_minimized"}


def problem_key() -> str | None:
    """The i18n key explaining the latest capture that gave nothing, or None (then: the game isn't open)."""
    return PROBLEM_TEXT.get(LAST_PROBLEM or "")


def _cursor_in(x: int, y: int, w: int, h: int) -> tuple[int, int] | None:
    import sys
    if sys.platform != "win32":
        return None
    import ctypes
    import ctypes.wintypes as wt
    p = wt.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
        return None
    return (p.x - x, p.y - y) if 0 <= p.x - x < w and 0 <= p.y - y < h else None


def grab_image(x: int, y: int, w: int, h: int) -> Image.Image:
    """RGB capture of a screen rectangle, in mss coordinates (Windows: physical pixels, macOS: points)."""
    with mss.MSS() if hasattr(mss, "MSS") else mss.mss() as s:
        shot = s.grab({"left": x, "top": y, "width": max(1, w), "height": max(1, h)})
    return Image.frombytes("RGB", shot.size, shot.rgb)


def detail_tiles(img: Image.Image | None, width: int = 1200) -> list[bytes]:
    """The latest grab at full resolution, cut left to right into tiles the AI reads without shrinking them:
    small things (inventory icons) stay legible on a wide screen. Nothing when the grab is small already."""
    if img is None or max(img.size) <= MAX_SIDE:
        return []
    n = -(-img.width // width)
    step = img.width / n
    tiles = []
    for i in range(n):
        left, right = max(0, int(i * step) - 40), min(img.width, int((i + 1) * step) + 40)   # a little overlap
        tile = img.crop((left, 0, right, img.height))
        tile.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        tile.save(buf, "JPEG", quality=85)
        tiles.append(buf.getvalue())
    return tiles


BLACK = 12          # a pillarbox bar is pure black; JPEG-free screen pixels, so a little noise at most


def content_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """The picture without the black bars a game draws to keep its aspect ratio (a 16:9 game on a 3440 px
    ultrawide: a quarter of the width was black, and the game got that much less of the AI's resolution).
    Only bars: the same width on both sides (or above and below), with most of the picture left. None: no bars."""
    box = img.convert("L").point(lambda v: 255 if v > BLACK else 0).getbbox()
    if not box:
        return None             # all black (a loading screen): nothing to trim
    w, h = img.size
    left, top, right, bottom = box
    if not (left >= w * 0.02 and abs(left - (w - right)) <= max(4, w * 0.01) and right - left >= w * 0.5):
        left, right = 0, w
    if not (top >= h * 0.02 and abs(top - (h - bottom)) <= max(4, h * 0.01) and bottom - top >= h * 0.5):
        top, bottom = 0, h
    return None if (left, top, right, bottom) == (0, 0, w, h) else (left, top, right, bottom)


def grab_jpeg(rect: tuple[int, int, int, int]) -> bytes:
    """JPEG of a screen rectangle (x, y, w, h), longest side MAX_SIDE, without pillarbox / letterbox bars."""
    global LAST_FULL, LAST_CURSOR, PLAY_AREA
    img = grab_image(*rect)
    PLAY_AREA = rect
    try:
        cursor = _cursor_in(*rect)
    except Exception:      # noqa: BLE001
        cursor = None
    box = content_box(img)
    if box:
        # the grab may be scaled from the screen rectangle (macOS takes points, a Retina grab has more pixels)
        sx, sy = rect[2] / img.width, rect[3] / img.height
        PLAY_AREA = (rect[0] + round(box[0] * sx), rect[1] + round(box[1] * sy),
                     round((box[2] - box[0]) * sx), round((box[3] - box[1]) * sy))
        img = img.crop(box)     # the screenshot, LAST_FULL and the cursor all in the same (trimmed) coordinates
        if cursor:
            cx, cy = cursor[0] - box[0], cursor[1] - box[1]
            cursor = (cx, cy) if 0 <= cx < img.width and 0 <= cy < img.height else None
    LAST_FULL = img.copy()
    LAST_CURSOR = cursor
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return buf.getvalue()
