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


def find_cut_tags(rgb: np.ndarray, name: str) -> list[tuple[int, int, int, int]]:
    """Tags whose lower part is hidden (the chat box covers the bottom of the screen, where players often stand):
    only the top edge and the start of the letters show. Its height comes from its width and the name's length."""
    f = rgb.astype(np.float32)
    luma = f[..., 0] * 0.3 + f[..., 1] * 0.59 + f[..., 2] * 0.11 + 1.0
    ratio = luma[1:] / luma[:-1]
    white = (f.min(axis=2) >= WHITE) & (f.max(axis=2) - f.min(axis=2) < 30)
    if len(name) < 4:
        return []        # a short name's plate is too small to tell from scenery by its top edge alone
    want = 0.40 * len(name) + 0.45
    found = []
    for y in range(ratio.shape[0] - 8):
        for x0, n in _runs((ratio[y] > 0.3) & (ratio[y] < 0.5), lo=40):     # the plate's own darkening, ~0.4
            h = int(round(n / want))
            if 12 <= h <= 60 and white[y + 1 + h // 4:y + 1 + h // 2, x0:x0 + n].mean() >= 0.04:
                found.append((x0, y + 1, n, h))
    return found


def tag_fits_name(tag: tuple[int, int, int, int], name: str) -> bool:
    """Could this plate hold that name? Its width grows with the name's length (the game's font is about
    0.4 tag-heights a letter): another player's tag, or a box of the game's own UI, usually doesn't fit."""
    _, _, w, h = tag
    want = 0.40 * len(name) + 0.45
    return abs(w / h - want) <= 0.22 * want


def portrait_rect(rgb: np.ndarray, box: list[float] | None, name: str = "") -> tuple[int, int, int, int] | None:
    """Pixel rect (left, top, right, bottom) of the player's sprite, from the AI's rough box (fractions).
    With the character's name, only tags that can hold it count (other players stand around). Without a box,
    only when exactly one tag is left."""
    H, W = rgb.shape[:2]

    def fitting(tags, region=None):
        if not name:
            return tags
        fit = [t for t in tags if tag_fits_name(t, name)]
        # none whole: one cut off by the chat box (its top edge and letters still show), only near the AI's box:
        # across the whole screen a top edge alone matches scenery too
        return fit or (find_cut_tags(region, name) if region is not None else [])

    if box is None:
        tags = fitting(find_name_tags(rgb), rgb)
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
    tags = fitting(find_name_tags(sub), sub)
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
    return fg if 0.12 <= fg.mean() <= 0.8 else None


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
