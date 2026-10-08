"""The character portrait: find the player's name tag in a screenshot and crop the sprite standing on it.

The AI's avatar_box is only a rough pointer: on a wide screen its fractions were off by a sprite or more (live test:
an NPC, a treetop, a sprite cut in half). A name tag is easy to find with plain pixels: a translucent dark plate with
white letters, right under the character's feet. The plate darkens whatever is behind it by the same factor, so its
top and bottom edges are straight lines where the brightness drops (and comes back) by that factor. The sprite is
about five tag-heights tall, centred on the tag.
"""
from __future__ import annotations

import numpy as np

# a name's letters: near white. The official client draws its 1366 px picture scaled up to the screen (2560 px on a
# 3440 ultrawide), which softens them to light grey: at 225 the player's own tag went unseen (live, 2026-10-07)
WHITE = 200


def _runs(row: np.ndarray, lo: int = 30, hi: int = 420) -> list[tuple[int, int]]:
    """(start, length) of the runs of True in a 1-D bool array, lo <= length <= hi."""
    if not row.any():
        return []
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(int(s), int(e - s)) for s, e in zip(starts, ends) if lo <= e - s <= hi]


def find_name_tags(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Name-tag plates in an RGB array: [(x, y, w, h)]."""
    f = rgb.astype(np.float32)
    luma = f[..., 0] * 0.3 + f[..., 1] * 0.59 + f[..., 2] * 0.11 + 1.0
    ratio = luma[1:] / luma[:-1]                       # ratio[y] = row y+1 against row y
    white = (f.min(axis=2) >= WHITE) & (f.max(axis=2) - f.min(axis=2) < 30)
    bottom = (ratio > 1.5) & (ratio < 4.5)              # bottom[y]: row y is a plate's last one
    found = []
    for y in range(ratio.shape[0]):
        y0 = y + 1                                     # a plate's first row (darker than the row above)
        for x0, n in _runs((ratio[y] > 0.22) & (ratio[y] < 0.65)):
            for h in range(9, 61):
                y1 = y0 + h - 1
                if y1 >= bottom.shape[0]:
                    break
                # most of its bottom edge: chat text or a sprite may cover part of it (seen live)
                if bottom[y1, x0:x0 + n].mean() >= 0.55:
                    letters = white[y0:y1 + 1, x0:x0 + n].mean()
                    if 2.2 <= n / h <= 14 and 0.04 <= letters <= 0.5:
                        found.append((x0, y0, n, h))
                    break
    return usual_height(found)


def usual_height(tags: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Every name tag on a screen is the same height: with three or more, one well off the usual height is a piece of
    two overlapping tags ("Pink Bunn" under "GreatFortune", live) or of the game's UI, not a whole tag."""
    if len(tags) < 3:
        return tags
    hs = sorted(t[3] for t in tags)
    usual = hs[len(hs) // 2]
    return [t for t in tags if abs(t[3] - usual) <= max(3, usual * 0.2)]


def tag_fits_name(tag: tuple[int, int, int, int], name: str) -> bool:
    """Could this plate hold that name? Its width grows with the name's length (the game's font is about
    0.4 tag-heights a letter): another player's tag, or a box of the game's own UI, usually doesn't fit."""
    _, _, w, h = tag
    want = 0.40 * len(name) + 0.45
    return abs(w / h - want) <= 0.22 * want


# ---------------------------------------------------------------- whose tag is it: the name's letters

# the name drawn in a common sans font: the game's own font is close enough to tell the player's tag from others
# (65 live frames: the player's tag 0.42-0.63, any other <= 0.31). Once found that way, the tag's own letters are
# kept (learned) and match far more sharply (0.6-0.8 against <= 0.26)
_FONTS = ("C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
          "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/tahoma.ttf")
DRAWN_MIN, DRAWN_LEAD = 0.40, 0.12        # a drawn name: this score, and this far ahead of the next tag
LEARNED_MIN = 0.55                         # the tag's own letters, learned from an earlier find (live: 0.62-0.85)


def _luma(a: np.ndarray) -> np.ndarray:
    f = a.astype(np.float32)
    return f[..., 0] * 0.3 + f[..., 1] * 0.59 + f[..., 2] * 0.11


def tag_letters(rgb: np.ndarray, tag: tuple[int, int, int, int]) -> np.ndarray | None:
    """The letters on a plate as a 0/1 array cut to their bounding box: brighter than the plate by half the way to
    its brightest (the letters' grey depends on the client's scaling, the plate's darkness on the scenery)."""
    x, y, w, h = tag
    lum = _luma(rgb[y:y + h, x:x + w])
    if lum.size == 0:
        return None
    bg = float(np.median(lum))
    m = (lum > bg + 0.5 * (float(lum.max()) - bg)).astype(np.float32)
    ys, xs = np.nonzero(m)
    if len(xs) < 10:
        return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]


def drawn_name(name: str) -> np.ndarray | None:
    """The name drawn in a sans font, as tag_letters cuts a plate's letters; None without a font."""
    from PIL import Image, ImageDraw, ImageFont
    for path in _FONTS:
        try:
            font = ImageFont.truetype(path, 48)
            break
        except OSError:
            continue
    else:
        return None
    img = Image.new("L", (48 * len(name) + 40, 80), 0)
    ImageDraw.Draw(img).text((10, 5), name, fill=255, font=font)
    m = (np.asarray(img) > 128).astype(np.float32)
    ys, xs = np.nonzero(m)
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(xs) else None


def letters_match(m: np.ndarray, template: np.ndarray) -> float:
    """How alike two letter shapes are (normalized correlation, -1..1), the template stretched to m's size."""
    from PIL import Image
    t = np.asarray(Image.fromarray((template * 255).astype(np.uint8)).resize((m.shape[1], m.shape[0]),
                                                                              Image.BILINEAR), np.float32) / 255
    a, b = m - m.mean(), t - t.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum()) / d if d else -1.0


def own_tag(rgb: np.ndarray, tags: list, name: str):
    """The player's own tag among `tags`, by the drawn name's letters: (tag, letters) or None when no tag is clearly
    theirs (the player hidden behind a shop window: the old width check took another player's tag)."""
    scored = [(t, tag_letters(rgb, t)) for t in tags]
    scored = [(t, m) for t, m in scored if m is not None and m.shape[0] >= 6]
    for template, need, lead in ((drawn_name(name), DRAWN_MIN, DRAWN_LEAD),):
        if template is None or not scored:
            continue
        ranked = sorted(((letters_match(m, template), t, m) for t, m in scored), key=lambda r: -r[0])
        best = ranked[0]
        # the same plate found twice (a row apart) is not a rival
        rivals = [r[0] for r in ranked[1:] if abs(r[1][0] - best[1][0]) > 4 or abs(r[1][1] - best[1][1]) > 4]
        if best[0] >= need and best[0] - max(rivals, default=-1.0) >= lead:
            return best[1], best[2]
    return None


def _whiteness(rgb: np.ndarray) -> np.ndarray:
    f = rgb.astype(np.float32)
    lo = f.min(axis=2)
    return ((lo >= 170) & (f.max(axis=2) - lo < 45)).astype(np.float32)


def _ncc_map(img: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Normalized correlation of template t at every spot of a 0/1 image (FFT; [y, x] = t's top-left there)."""
    th, tw = t.shape
    H, W = img.shape
    z = t - t.mean()
    shape = (H + th, W + tw)
    corr = np.fft.irfft2(np.fft.rfft2(img, shape) * np.fft.rfft2(z[::-1, ::-1], shape), shape)[th - 1:H, tw - 1:W]
    s = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    win = s[th:, tw:] - s[:-th, tw:] - s[th:, :-tw] + s[:-th, :-tw]
    var = np.maximum(win - win * win / (th * tw), 1e-6)            # a 0/1 image: the sum of squares is the sum
    return corr / (np.sqrt(var) * np.sqrt(float((z * z).sum())) + 1e-9)


def _plate_around(lum: np.ndarray, x: int, y: int, tw: int, th: int) -> tuple[int, int] | None:
    """(top, height) of the dark plate behind letters at (x, y, tw, th): its top edge darkens what is behind by the
    plate's factor along most of the letters' width. None without one: the same name in the game's own UI (the
    status bar at the bottom, a shop window) is no name tag."""
    xs = slice(x, x + tw)
    top = None
    for yy in range(y - 2, max(1, y - 11), -1):
        r = lum[yy, xs] / lum[yy - 1, xs]
        if ((r > 0.22) & (r < 0.65)).mean() >= 0.75:
            top = yy
            break
    if top is None:
        return None
    height = int(round(th * 1.7))           # the plate's bottom edge, when the chat box doesn't cover it
    for yy in range(y + th, min(lum.shape[0] - 1, y + th + 11)):
        r = lum[yy + 1, xs] / lum[yy, xs]
        if ((r > 1.5) & (r < 4.5)).mean() >= 0.5:
            height = yy - top + 1
            break
    return top, height


def find_learned(rgb: np.ndarray, learned: np.ndarray) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
    """The player's tag by the learned letters, searched across the whole screen: tags that overlap or merge in a
    crowd ("Oldcc Kalimero") break plate finding, not this. (tag, letters) or None."""
    th, tw = learned.shape
    if rgb.shape[0] <= th or rgb.shape[1] <= tw:
        return None
    score = _ncc_map(_whiteness(rgb), learned)
    lum = _luma(rgb) + 1.0
    for _ in range(4):
        y, x = np.unravel_index(int(np.argmax(score)), score.shape)
        if score[y, x] < LEARNED_MIN:
            return None
        plate = _plate_around(lum, int(x), int(y), tw, th)
        if plate:
            top, height = plate
            pad = max(2, th // 3)
            return (int(x) - pad, top, tw + 2 * pad, height), learned
        score[max(0, y - th):y + th, max(0, x - tw):x + tw] = -1      # not a tag: the next best spot
    return None


def portrait_rect(rgb: np.ndarray, box: list[float] | None, name: str = "", learned: np.ndarray | None = None,
                  found: dict | None = None) -> tuple[int, int, int, int] | None:
    """Pixel rect (left, top, right, bottom) of the player's sprite, from the AI's rough box (fractions).
    With the character's name, only the tag whose letters spell it (the learned letters, else the drawn name via
    own_tag); `found["letters"]` then holds them, to be learned. A plate of the right width near the box is not
    enough. Without a name: the tag nearest the box, or the only one without a box."""
    H, W = rgb.shape[:2]
    if name:
        hit = (find_learned(rgb, learned) if learned is not None else None) or own_tag(rgb, find_name_tags(rgb), name)
        if hit:
            (tx, ty, tw, th), letters = hit
            if found is not None and letters is not learned:
                found["letters"] = letters              # found by the drawn name: learn its own letters
            side = int(th * 5.4)
            mid = tx + tw / 2
            rect = (int(mid - side / 2), ty - side, int(mid + side / 2), ty + int(th * 0.2))
            return None if rect[0] < 0 or rect[1] < 0 or rect[2] > W else rect
        if box is None:
            return None

    def fitting(tags):
        # With the name, whole tags were already judged by their letters (own_tag) and none spelled it: a plate's
        # width alone near the box matched scenery live (a beam for a magician), so nothing more counts.
        return [] if name else tags

    if box is None:
        tags = fitting(find_name_tags(rgb))
        if len(tags) != 1:
            return None
        tx, ty, tw, th = tags[0]
        box = [tx / W, (ty - th * 5) / H, tw / W, th * 5 / H]
    x, y, w, h = box
    # look around the AI's box: the tag is under the feet, and the box itself may be off by a sprite or two
    cx, cy = (x + w / 2) * W, (y + h / 2) * H
    rw, rh = max(w * W * 3, W * 0.06), max(h * H * 2.5, H * 0.12)
    left, top = int(max(0, cx - rw)), int(max(0, cy - rh))
    right, bottom = int(min(W, cx + rw)), int(min(H, cy + rh * 1.4))
    sub = rgb[top:bottom, left:right]
    tags = fitting(find_name_tags(sub))
    if not tags:
        # the box was further off than that (live test: it pointed at a TAXI sign 400 px away): every tag on screen,
        # nearest to the box (other players have tags too, NPCs don't: theirs are opaque yellow plates)
        tags, left, top = fitting(find_name_tags(rgb)), 0, 0
        if not tags:
            return None
    fx, fy = cx - left, (y + h) * H - top           # the box's feet
    tx, ty, tw, th = min(tags, key=lambda t: (t[0] + t[2] / 2 - fx) ** 2 + (t[1] - fy) ** 2)
    side = int(th * 5.4)
    mid = left + tx + tw / 2
    feet = top + ty
    rect = (int(mid - side / 2), feet - side, int(mid + side / 2), feet + int(th * 0.2))
    if rect[0] < 0 or rect[1] < 0 or rect[2] > W:
        return None
    return rect


def _grow(seed: np.ndarray, free: np.ndarray) -> np.ndarray:
    """Flood fill: every `free` pixel 4-connected to `seed`."""
    m = seed & free
    while True:
        g = m.copy()
        g[1:] |= m[:-1]
        g[:-1] |= m[1:]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        g &= free
        if (g == m).all():
            return m
        m = g


def _components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    lab = np.zeros(mask.shape, np.int32)
    n = 0
    for y, x in zip(*np.nonzero(mask)):
        if lab[y, x]:
            continue
        n += 1
        seed = np.zeros(mask.shape, bool)
        seed[y, x] = True
        lab[_grow(seed, mask & (lab == 0))] = n
    return lab, n


def sprite_mask(rgb: np.ndarray) -> np.ndarray | None:
    """Which pixels of a portrait crop are the character: game sprites have a dark outline, so a flood from the
    crop's edges that stops at dark pixels covers the background; pockets it can't reach (between a bow and its
    string) go too when they have the background's colours. None when that doesn't look like a sprite."""
    a = rgb.astype(np.int16)
    wall = (a[..., 0] * 0.3 + a[..., 1] * 0.59 + a[..., 2] * 0.11) < 70
    border = np.zeros(wall.shape, bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg = _grow(border, ~wall)
    q = (a // 24).astype(np.int32)
    key = q[..., 0] * 10000 + q[..., 1] * 100 + q[..., 2]
    lab, n = _components(np.isin(key, np.unique(key[bg])) & ~bg & ~wall)
    ids = np.flatnonzero(np.bincount(lab.ravel(), minlength=n + 1) >= 25)
    bg |= np.isin(lab, ids[ids > 0])
    lab, n = _components(~bg)
    if not n:
        return None
    sizes = np.bincount(lab.ravel())[1:]
    fg = lab == 1 + int(sizes.argmax())                 # the character; stray dark grass at the edges goes
    fg = _without_scenery(a, fg)
    return fg if 0.12 <= fg.mean() <= 0.8 else _soft_outline_mask(a)


def mask_reaches_feet(mask: np.ndarray) -> bool:
    """A portrait crop stands on the tag at its bottom, so its cut-out reaches the bottom third. A bit of scenery
    (live, 2026-10-07: bricks and a beam cut out for a magician) is a band across the top with nothing below."""
    return bool(mask[2 * mask.shape[0] // 3:].any())


def _dilate(m: np.ndarray) -> np.ndarray:
    g = m.copy()
    g[1:] |= m[:-1]
    g[:-1] |= m[1:]
    g[:, 1:] |= m[:, :-1]
    g[:, :-1] |= m[:, 1:]
    return g


def _soft_outline_mask(a: np.ndarray) -> np.ndarray | None:
    """The official client scales its picture up, which softens a sprite's dark outline: the flood from the edges
    leaked in through it and took the hat and face with the background (live, 2026-10-07, against bricks). The
    outline thickened by two pixels holds the flood; what peeling then cuts off (a brick line touching the hat)
    goes. None unless it is a whole standing figure (a dark sprite on a dark scene gives only a piece)."""
    wall = (a[..., 0] * 0.3 + a[..., 1] * 0.59 + a[..., 2] * 0.11) < 70
    for _ in range(2):
        wall = _dilate(wall)
    border = np.zeros(wall.shape, bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg = _grow(border, ~wall)
    lab, n = _components(~bg)
    if not n:
        return None
    sizes = np.bincount(lab.ravel())[1:]
    fg = lab == 1 + int(sizes.argmax())
    fg = _dilate(_dilate(fg)) & ~bg | fg                  # the outline the thickening took
    fg = _core(fg, 3)
    rows = np.flatnonzero(fg.any(axis=1))
    if not len(rows) or rows[-1] - rows[0] < 0.7 * fg.shape[0] or not 0.2 <= fg.mean() <= 0.6:
        return None
    return fg


def _erode(m: np.ndarray) -> np.ndarray:
    e = m.copy()
    e[1:] &= m[:-1]
    e[:-1] &= m[1:]
    e[:, 1:] &= m[:, :-1]
    e[:, :-1] &= m[:, 1:]
    return e


def _core(fg: np.ndarray, k: int) -> np.ndarray:
    """The body: what is left after peeling k pixels off, the part over the crop's middle third, grown back k + 1
    pixels inside fg. A thick bush that only touches the sprite somewhere is cut off; so is a thin bowstring."""
    core = fg
    for _ in range(k):
        core = _erode(core)
    lab, n = _components(core)
    if n <= 1:
        return fg
    W = fg.shape[1]
    score = np.bincount(lab[:, W // 3: 2 * W // 3].ravel(), minlength=n + 1)
    score[0] = 0
    keep = lab == int(score.argmax())
    for _ in range(k + 1):
        g = keep.copy()
        g[1:] |= keep[:-1]
        g[:-1] |= keep[1:]
        g[:, 1:] |= keep[:, :-1]
        g[:, :-1] |= keep[:, 1:]
        keep = g & fg
    return keep


def _without_scenery(a: np.ndarray, fg: np.ndarray) -> np.ndarray:
    """A bush or leaves the sprite stands against (live, 2026-10-04: a dark green bush beside an archer's legs): its
    dark mass has no outline between it and the sprite, so the flood above keeps it. A chunk that peeling cuts off,
    reaches the crop's edge and is mostly dark green is scenery, with the dark green pixels joined to it; a weapon or
    a cape (not green, inside the crop) stays."""
    peeled = fg & ~_core(fg, 3)
    lab, n = _components(peeled)
    if not n:
        return fg
    luma = a[..., 0] * 0.3 + a[..., 1] * 0.59 + a[..., 2] * 0.11
    leafy = fg & (a[..., 1] - np.maximum(a[..., 0], a[..., 2]) >= 12) & (luma < 110)
    edge = np.ones(fg.shape, bool)
    edge[4:-4, 4:-4] = False
    sizes = np.bincount(lab.ravel(), minlength=n + 1)
    chunks = [i for i in range(1, n + 1) if sizes[i] >= 40]
    scenery = np.zeros(fg.shape, bool)
    for i in chunks:
        part = lab == i
        if (part & edge).any() and (part & leafy).sum() >= 0.3 * sizes[i]:
            scenery |= part
    if not scenery.any():
        return fg
    rest = fg & ~_grow(scenery, leafy | scenery)
    body = _grow(_core(rest, 2), rest)                # the outline bits the bush leaves hanging go too
    lab, n = _components(body)
    if not n:
        return fg
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(sizes.argmax())
