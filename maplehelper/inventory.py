"""Read the in-game inventory from a screenshot: find its slot grid, cut out every item icon and match it to the
knowledge base's item pictures.

The AI couldn't name the items from a screenshot (on a wide screen an icon is a few dozen pixels in a big picture,
seen live). Matching pixels against the database's own icons is exact where the AI guessed: the game draws the
same pictures, only scaled.

Both sides go through one rule: a KB picture is first drawn on the slot's beige, then the icon is every pixel clearly
off that beige, without the drop shadow below it, cropped to those pixels and compared at SIZE x SIZE. The slot's
speckle stays under that line, so an in-game icon is cropped as tightly as the KB's.

Some pictures are shared: every scroll of a tier, the NPC letters, the Black Sacks. The game draws them alike, so
no picture can tell them apart. Such a slot is "ambiguous": the player is asked to point the mouse at it so the
game's tooltip shows its name in the next screenshot, where the AI reads it.
"""
from __future__ import annotations

import io
import threading
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

SLOT_BG = np.array([224, 222, 212], np.int16)      # the beige of an inventory slot
SIZE = 24                                            # icons are compared at this size
ICON_DIFF = 40         # a pixel this far off the beige is the icon's (the slot's speckle stays within ~35)
TWIN = 3.0             # KB pictures this close to each other are one picture (every scroll of a tier)
RATIO, MARGIN = 1.08, 0.5   # the best match must beat every other item's picture by this much (d * RATIO + MARGIN)
UNKNOWN = 25.0         # a best match this far off is no item the KB has a picture of
UNKNOWN_ALIKE = 28.0   # the same for a picture several items share (a scroll's is distinctive, its count covers it)
REFINE = 40            # the nearest pictures compared again with the icon moved by a pixel
SMALL_SLOT = 70        # a slot smaller than this (px) is read doubled
CLEAR = 12.0           # a plain reading named this close is the item: a reading without the stack count is skipped
COVERED = 0.4          # this much of a slot's side under one flat colour: something covers the slot (_whole)


def _slot_mask(rgb: np.ndarray) -> np.ndarray:
    return (np.abs(rgb.astype(np.int16) - SLOT_BG) <= 14).all(axis=2)


def _slot_like(rgb: np.ndarray) -> np.ndarray:
    """The slot's own pixels: its beige, the speckle on it and its darker frame, not the white lines around it."""
    a = rgb.astype(np.int16)
    return (np.abs(a - SLOT_BG).max(axis=2) <= 30) & ~(a >= 230).all(axis=2)


def _runs(row: np.ndarray, lo: int, hi: int) -> list[tuple[int, int]]:
    if not row.any():
        return []
    d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
    s, e = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(int(a), int(b - a)) for a, b in zip(s, e) if lo <= b - a <= hi]


def _columns(mask: np.ndarray, candidates: int = 8) -> list[tuple[list[int], int]]:
    """Possible slot columns (their left x) and slot width, likeliest first: image rows where three or more equally
    long, equally spaced slot runs start (a row of slots, above or beside the icons)."""
    best: dict[tuple, list[list[tuple[int, int]]]] = {}      # grid -> per image row, its runs on that grid
    for y in range(mask.shape[0]):
        runs = _runs(mask[y], 24, 200)
        i = 0
        while i < len(runs) - 2:          # every grid along the row (the game's scenery can look like one too)
            (x0, n0), (x1, n1), (x2, n2) = runs[i:i + 3]
            pitch = x1 - x0
            if abs(n1 - n0) <= 3 and abs(n2 - n0) <= 3 and abs((x2 - x1) - pitch) <= 3 and pitch - n0 <= n0 // 3:
                on = [j for j, (x, n) in enumerate(runs) if j >= i and abs(n - n0) <= 3
                      and (x - x0) % pitch in (0, 1, 2, pitch - 1, pitch - 2)]
                best.setdefault((x0 // 4, n0 // 4, pitch // 4), []).append([runs[j] for j in on])
                i = on[-1] + 1
            else:
                i += 1
    out = []
    for rows in sorted(best.values(), key=len, reverse=True)[:candidates]:
        found = max(rows, key=len)        # the row where most slots show their full width
        size = int(np.median([n for _, n in found]))
        pitch = int(np.median(np.diff([x for x, _ in found])))
        x0 = found[0][0]
        out.append((sorted({x0 + round((x - x0) / pitch) * pitch for x, _ in found}), size))
    return out


def _lines(band: np.ndarray) -> np.ndarray:
    """Per line of a band of slots (lines along axis 0): a line between slots, or the grid's frame. The game draws
    them white; another plain colour that isn't the slot's beige counts too. Icons never fill every slot of the
    band with one colour."""
    a = band.astype(np.int16)
    light = (a >= 230).all(axis=2).mean(axis=1) >= 0.8
    med = np.median(a, axis=1).astype(np.int16)
    same = (np.abs(a - med[:, None, :]) <= 12).all(axis=2).mean(axis=1) >= 0.85
    beige = (np.abs(med - SLOT_BG) <= 30).all(axis=1)
    return light | (same & ~beige)


def _between(lines: np.ndarray, size: int) -> list[tuple[int, int]]:
    """(start, length) of the gaps between lines as long as a slot (±1/8), each with a line on both sides (a slot
    the screen's edge cuts has none past it); the longest run of them at one steady pitch."""
    d = np.diff(np.concatenate(([1], lines.astype(np.int8), [1])))
    starts, ends = np.flatnonzero(d == -1), np.flatnonzero(d == 1)
    tol = max(3, size // 8)
    cells = [(int(s), int(e - s)) for s, e in zip(starts, ends)
             if 0 < s and e < len(lines) and abs((e - s) - size) <= tol]
    if not cells:
        return []
    chains: list[list[tuple[int, int]]] = [[cells[0]]]
    for c in cells[1:]:
        last = chains[-1]
        gap = c[0] - last[-1][0]
        if gap <= size * 1.5 and (len(last) < 2 or abs(gap - (last[1][0] - last[0][0])) <= 3):
            last.append(c)
        else:
            chains.append([c])
    return max(chains, key=len)


def _grid(rgb: np.ndarray, xs: list[int], size: int) -> list[tuple[int, int, int]]:
    xs = [x for x in xs if x + size <= rgb.shape[1]]
    if len(xs) < 3:
        return []
    rows = _between(_lines(np.concatenate([rgb[:, x:x + size] for x in xs], axis=1)), size)
    if not rows:
        return []
    # the columns between their own lines, along the rows found
    across = np.concatenate([rgb[y:y + n] for y, n in rows], axis=0).transpose(1, 0, 2)
    exact = _between(_lines(across), size)
    if len(exact) >= 3 and len(exact) >= len(xs) - 1:
        xs = [x for x, _ in exact]
        size = int(np.median([n for _, n in exact]))
    return [(x, y, size) for y, _ in rows for x in xs if y + size <= rgb.shape[0]]


def find_slots(rgb: np.ndarray) -> list[tuple[int, int, int]]:
    """Inventory slots as (x, y, size). The slot runs across a row of slots give the columns roughly; the rows are
    then measured on their own, between the lines that separate them (their pitch differs from the columns'),
    and the columns again between theirs. A slot the window's edge cuts has no line past it and isn't one. Of the
    candidate grids, the one with the most slots."""
    grids = [_grid(rgb, xs, size) for xs, size in _columns(_slot_like(rgb))]
    return max(grids, key=len, default=[])


def _icon_mask(rgb: np.ndarray, hidden: np.ndarray | None = None, shadow: bool = False) -> np.ndarray:
    """The icon's pixels on a slot: clearly off the beige (the speckle isn't), without the drop shadow under it.
    The game draws that shadow on its own (not always where the KB picture has it), so neither side keeps it,
    unless shadow: then it anchors an icon whose bottom a stack count covers."""
    a = rgb.astype(np.float32)
    mask = np.abs(a - SLOT_BG).max(axis=2) > ICON_DIFF
    if hidden is not None:
        mask &= ~hidden              # covered by something else (a stack count)
    if shadow:
        return mask
    # the shadow darkens the beige: a pixel that is the beige times t (an icon's black outline is darker still)
    t = a.mean(axis=2) / float(SLOT_BG.mean())
    shade = mask & (t >= 0.3) & (np.abs(a - t[..., None] * SLOT_BG).max(axis=2) <= 14)
    body = mask & ~shade
    # ...below every other pixel of the icon in its column
    rows = np.arange(a.shape[0])[:, None]
    lowest = np.where(body, rows, -1).max(axis=0)
    return mask & ~(shade & (rows > lowest[None, :]))


def _normalise(rgb: np.ndarray, mask: np.ndarray, hidden: np.ndarray | None = None):
    """The masked icon cropped to its pixels, on the slot colour, SIZE x SIZE. With hidden (the pixels something
    covers, like a stack count): also the weight of each compared pixel, 0 where it was covered."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 12:
        return (None, None) if hidden is not None else None
    a = rgb.astype(np.uint8).copy()
    a[~mask] = SLOT_BG
    box = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
    crop = Image.fromarray(a[box])
    side = max(crop.size)
    at = ((side - crop.width) // 2, (side - crop.height) // 2)
    square = Image.new("RGB", (side, side), tuple(int(v) for v in SLOT_BG))
    square.paste(crop, at)
    vec = np.asarray(square.resize((SIZE, SIZE), Image.BOX)).astype(np.float32)
    if hidden is None:
        return vec
    cover = Image.new("F", (side, side), 0.0)
    cover.paste(Image.fromarray(hidden[box].astype(np.float32), "F"), at)
    weight = 1.0 - np.asarray(cover.resize((SIZE, SIZE), Image.BOX))
    return vec, np.where(weight >= 0.5, weight, 0.0).astype(np.float32)


def _on_slot(path) -> np.ndarray | None:
    """A KB picture drawn on a slot, as the game draws it (its soft shadow blends into the beige)."""
    try:
        im = Image.open(path).convert("RGBA")
    except OSError:
        return None
    slot = Image.new("RGBA", im.size, tuple(int(v) for v in SLOT_BG) + (255,))
    return np.asarray(Image.alpha_composite(slot, im).convert("RGB"))


def _icon_vectors(path) -> tuple[np.ndarray, np.ndarray] | None:
    """A KB picture as compared: without its shadow, and with it."""
    rgb = _on_slot(path)
    if rgb is None:
        return None
    plain, shaded = _normalise(rgb, _icon_mask(rgb)), _normalise(rgb, _icon_mask(rgb, shadow=True))
    return None if plain is None or shaded is None else (plain, shaded)


def _soft(a: np.ndarray) -> np.ndarray:
    """Pictures (... x SIZE x SIZE x 3) softened by a 3x3 box: a sharp pixel-art edge a pixel off (the crop of an
    upscaled icon never lands exactly where the KB picture's does) no longer counts as a whole wrong pixel, while a
    colour still does (a brown Tree Branch and a purple Rotten Root Fragment are one shape)."""
    pad = [(0, 0)] * (a.ndim - 3) + [(1, 1), (1, 1), (0, 0)]
    p = np.pad(a, pad, mode="edge")
    return sum(p[..., i:i + SIZE, j:j + SIZE, :] for i in range(3) for j in range(3)) / 9


def _shifts(v: np.ndarray) -> list[np.ndarray]:
    """A reading moved by up to a pixel each way (itself first)."""
    p = np.pad(v, [(1, 1), (1, 1), (0, 0)], mode="edge")
    order = [(1, 1)] + [(i, j) for i in range(3) for j in range(3) if (i, j) != (1, 1)]
    return [p[i:i + SIZE, j:j + SIZE] for i, j in order]


@dataclass
class Index:
    keys: list[str]
    vecs: np.ndarray                 # N x SIZE x SIZE x 3, the pictures without their shadow (to find twins)
    soft: np.ndarray                 # the same softened (_soft), as compared
    soft_shaded: np.ndarray          # the pictures with their shadow, softened (the shaded ones aren't kept: 19 MB)


_INDEX: dict[str, Index] = {}
_INDEX_LOCK = threading.Lock()     # the background warm-up and a read must not both build it


def _index(kb) -> Index:
    # by folder and size: a KB update keeps the folder but changes its items (a stale key broke describe())
    root = f"{getattr(kb, 'root', '')}|{len(kb.entities)}|{id(kb)}"
    with _INDEX_LOCK:
        return _INDEX.get(root) or _build_index(kb, root)


def _build_index(kb, root: str) -> Index:
    """Every item picture of the KB as a comparable vector (called under _INDEX_LOCK). Items the KB has no source
    for in the game (half of them, availability.item_open) are candidates too: the bag is the truth. Left out, such
    an item was named after a look-alike (its other colour) one time in three, and one drawn with the very picture
    of an item in the game (Roger's Apple, the tutorial's Apple) was named as that one for certain."""
    _INDEX.clear()
    keys, vecs, shaded = [], [], []
    for k, e in kb.entities.items():
        if e.get("category") != "item":
            continue
        path = kb.image_path(k)
        v = _icon_vectors(path) if path else None
        if v is not None:
            keys.append(k)
            vecs.append(v[0])
            shaded.append(v[1])
    empty = np.zeros((0, SIZE, SIZE, 3), np.float32)
    plain = np.stack(vecs) if vecs else empty
    vecs.clear()                                    # (the lists and the softening's sums peaked at 155 MB)
    soft_shaded = _soft(np.stack(shaded)) if shaded else empty
    shaded.clear()
    _INDEX[root] = Index(keys, plain, _soft(plain), soft_shaded)
    return _INDEX[root]


@dataclass
class Slot:
    index: int                      # 1-based, left to right, top to bottom
    picture: bytes                  # the icon as the game shows it (PNG)
    # (item key, distance), best first. certain: the first is the item; ambiguous: every item drawn with this
    # very picture (any of them); unknown: the nearest pictures, none close enough to name the item
    matches: list[tuple[str, float]] = field(default_factory=list)
    status: str = "certain"         # "certain" | "ambiguous" | "unknown" | "hovered"


def warm(kb) -> None:
    """Build the icon index ahead of time (~2,700 pictures: ~2-3 s of CPU and ~76 MB, on a background thread):
    the first inventory check doesn't wait."""
    _index(kb)


def _whole(c: np.ndarray) -> bool:
    """The slot is drawn whole: at least two of its corners show the slot's beige (an icon may reach one or two
    corners; a window, a tooltip or the screen's edge over the slot covers more)."""
    k = max(2, c.shape[0] // 16)
    corners = (c[:k, :k], c[:k, -k:], c[-k:, :k], c[-k:, -k:])
    if sum((~_icon_mask(q)).mean() >= 0.6 for q in corners) < 2:
        return False
    # a box over one side (a tooltip, a white window) leaves the two other corners beige, and the rest of the icon
    # was named for certain as another item (Blue Snail Shell as Blue Ghetto Beanie, an empty slot as Letter I).
    # Such a box is one flat colour reaching a fifth of the slot in along much of that side, or a thin bar along all
    # of it; an icon that touches the side never is (the KB's pictures at 32-85 px: at most a quarter of a side a
    # fifth in, 0.8 of it a few pixels in)
    n = c.shape[0]
    for depth, part in ((max(3, round(n * 0.2)), COVERED), (max(2, round(n * 0.08)), 0.95)):
        for side in (c[:depth].transpose(1, 0, 2), c[-depth:].transpose(1, 0, 2), c[:, :depth], c[:, -depth:]):
            a = side.astype(np.int16)                  # along the side x into the slot x 3
            off = ~_slot_like(a)
            if not off.any():
                continue
            fill = np.median(a[off], axis=0)
            if (off & (np.abs(a - fill).max(axis=2) <= 12)).all(axis=1).mean() >= part:
                return False
    return True


def _count_box(c: np.ndarray) -> tuple[int, int] | None:
    """(top, right) of the stack count the game prints at a slot's bottom left ("92": digits outlined in black,
    filled white fading to blue), or None. The fill's columns (white or blue over a black outline; the slot's beige
    and grey speckle are neither) are the digits, the outline next to them is theirs too. Digits stand a few
    pixels apart at a big scale: a gap that narrow doesn't end the count (only the "1" of "16" was hidden, and the
    "6" kept the item from matching)."""
    size = c.shape[0]
    top = int(size * 0.6)
    band = c[top:, : int(size * 0.8)].astype(np.int16)
    black = band.max(axis=2) <= 60
    blue = (band[..., 2] - band[..., 0] >= 30) & (band[..., 2] >= 90)
    white = (band.min(axis=2) >= 235) & (band.max(axis=2) - band.min(axis=2) <= 12)
    outline = black.sum(axis=0) >= 2
    # a digit's fill touches its outline above or below; an icon's own white or blue doesn't, or the box ran on
    # under it ("150" over a big icon hid most of it and the slot went unnamed, audit P83-4)
    r = max(2, size // 30)
    edge = np.zeros_like(black)
    for i in range(1, r + 1):
        edge[:-i] |= black[i:]
        edge[i:] |= black[:-i]
    texty = outline & ((blue | white) & edge).any(axis=0)
    if not texty.any():
        return None
    start = end = int(np.argmax(texty))
    gap = max(2, size // 10)
    while end < len(texty) and texty[end:end + gap + 1].any():
        end += 1
    while not texty[end - 1]:
        end -= 1
    reach = max(2, size // 10)                  # the outline (and a "1"'s grey foot) around the fill
    for _ in range(reach):
        if start == 0 or not outline[start - 1]:
            break
        start -= 1
    for _ in range(reach):
        if end == len(outline) or not outline[end]:
            break
        end += 1
    if start > size * 0.12 or end - start < size * 0.08:
        return None
    rows = np.flatnonzero(black[:, start:end].any(axis=1))
    first = max(int(rows[0]), int(rows[-1]) - int(size * 0.36))   # a digit's height: not the icon's outline above
    return top + first, end


def _vectors(c: np.ndarray) -> list[tuple[np.ndarray, np.ndarray | None, bool]]:
    """The slot's icon as comparable readings (vector, weight of each pixel or None for all, with the shadow): as
    it is, and without a stack count when it seems to have one. Under a count the icon isn't compared, and its
    shadow, still in view to the right, keeps the crop where the KB picture's is."""
    mask = _icon_mask(c)
    v = _normalise(c, mask)
    out = [(v, None, False)] if v is not None else []
    box = _count_box(c)
    if box:
        hidden = np.zeros(mask.shape, bool)
        hidden[box[0]:, :box[1] + 1] = True
        v, w = _normalise(c, _icon_mask(c, hidden, shadow=True), hidden)
        if v is not None and w.sum() > SIZE * SIZE / 3:
            out.append((v, w, True))
    return out


def _distances(vecs: list[tuple[np.ndarray, np.ndarray | None, bool]], index: Index) -> np.ndarray:
    """Per KB picture, its distance to the slot's icon (the closest of the slot's readings), both softened. The
    REFINE nearest pictures are compared again with the reading moved by up to a pixel: an icon's crop can be a
    pixel off the KB picture's, and every edge then counted as wrong (a plain copy of a KB icon missed its own
    picture by 40-50, as far as another item)."""
    out = []
    for v, w, shaded in vecs:
        ref = index.soft_shaded if shaded else index.soft
        sv = _soft(v)
        sw = np.ones((SIZE, SIZE, 1), np.float32) if w is None else w[..., None]
        # the weighted mean as one matrix product over the flat pictures: a pass over the whole index is most of
        # a read's time (a 12-item tab took 1.3 s)
        flat_w = np.broadcast_to(sw, (SIZE, SIZE, 3)).reshape(-1) / (3 * sw.sum())
        d = np.abs(ref.reshape(len(ref), -1) - sv.reshape(-1)) @ flat_w
        near = np.argsort(d, kind="stable")[:REFINE]
        for mv, mw in zip(_shifts(sv)[1:], _shifts(sw)[1:]):
            if mw.sum() <= 0:
                continue
            dn = (np.abs(ref[near] - mv).mean(axis=3) * mw[..., 0]).sum(axis=(1, 2)) / mw.sum()
            d[near] = np.minimum(d[near], dn)
        out.append(d)
    return np.min(out, axis=0)


def _refined(vecs: list, index: Index, j: int) -> float:
    """One KB picture's distance with every reading moved by up to a pixel, as _distances does for the nearest."""
    best = np.inf
    for v, w, shaded in vecs:
        ref = (index.soft_shaded if shaded else index.soft)[j]
        sw = np.ones((SIZE, SIZE, 1), np.float32) if w is None else w[..., None]
        for mv, mw in zip(_shifts(_soft(v)), _shifts(sw)):
            if mw.sum() > 0:
                best = min(best, float((np.abs(ref - mv).mean(axis=2) * mw[..., 0]).sum() / mw.sum()))
    return best


def _match(d: np.ndarray, index: Index, kb, top: int, vecs: list | None = None) -> tuple[str, list[tuple[str, float]]]:
    """(status, matches) for a slot from its distances (_distances over vecs, its readings): see Slot."""
    order = np.argsort(d, kind="stable")
    b = order[0]
    nearest = [(index.keys[j], float(d[j])) for j in order[:top]]
    # the KB pictures that are the best one's twins: the game draws them alike
    flat = index.vecs.reshape(len(index.vecs), -1)
    twin = np.abs(flat - flat[b]).mean(axis=1) <= TWIN
    twins = [j for j in order if twin[j]]

    def name(j):
        return (kb.get(index.keys[j]) or {}).get("name")
    alike = len({name(j) for j in twins}) > 1
    if d[b] > (UNKNOWN_ALIKE if alike else UNKNOWN):
        return "unknown", nearest
    # the nearest other item (one of the same name drawn apart, like a quest's copy of an item, is no rival)
    rival = next((j for j in order if not twin[j] and name(j) != name(b)), None)
    if rival is not None and vecs:
        # compared moved by a pixel too: past a group of REFINE twins (a scroll tier has 53) the rival wasn't,
        # and kept a larger distance than the best's
        d[rival] = min(d[rival], _refined(vecs, index, rival))
    if rival is not None and d[rival] < d[b] * RATIO + MARGIN:
        return "unknown", nearest          # another picture fits as well: no telling which
    if alike:
        # a picture shared with items the KB has no source for in the game is the one in the game: a new player's
        # Apple and Long Sword went "one of 2 look-alikes" (Roger's Apple, Beginner's Long Sword), unpriced and
        # dropped from the grind read. The tutorial's Roger's Apple is then named Apple (a trade-off for the owner
        # to confirm, audit P83-1); the AI still sees it among the close pictures. Several in the game: the slot
        # stays ambiguous, those listed first
        from . import availability
        open_ = availability.of(kb)
        playable = [j for j in twins if open_.item_open(index.keys[j])]
        if len({name(j) for j in playable}) == 1:
            b = playable[0]
            return "certain", [(index.keys[b], float(d[b]))] + [p for p in nearest if p[0] != index.keys[b]][:top - 1]
        twins = playable + [j for j in twins if j not in playable]
        return "ambiguous", [(index.keys[j], float(d[j])) for j in twins]
    return "certain", nearest


def read(img: Image.Image, kb, top: int = 3, cursor: tuple[int, int] | None = None) -> list[Slot]:
    """Every filled slot of the inventory in a full-resolution screenshot, with its database matches.
    cursor: the mouse position in the image. The game's hand cursor over a slot is no item: that slot comes back
    as "hovered" (its name is in the game's tooltip in the same screenshot)."""
    rgb = np.asarray(img.convert("RGB"))
    index = _index(kb)
    out = []
    for i, (x, y, size) in enumerate(find_slots(rgb), 1):
        # the hand sprite hangs below and right of the mouse point (~0.6 slot): any slot it touches
        if cursor and x - size * 0.6 <= cursor[0] < x + size and y - size * 0.7 <= cursor[1] < y + size:
            if x <= cursor[0] < x + size and y <= cursor[1] < y + size:
                out.append(Slot(i, b"", status="hovered"))      # the one the mouse points at
            continue
        c = rgb[y:y + size, x:x + size]
        if not _whole(c):
            continue                 # covered (a window, a tooltip) or cut by the screen's edge
        if _icon_mask(c).mean() < 0.02:
            continue                 # an empty slot: just the speckled beige
        # a small game window (~42 px slots): the stack count's outline is a pixel thin and its box wasn't found,
        # so the item went "unknown". Doubled pixel for pixel it is found (audit SCR-15; never a wrong name)
        vecs = _vectors(np.repeat(np.repeat(c, 2, 0), 2, 1) if size < SMALL_SLOT else c)
        if not vecs:
            continue
        buf = io.BytesIO()
        Image.fromarray(c).save(buf, "PNG")
        slot = Slot(i, buf.getvalue())
        if len(index.keys):
            # a plain reading named clearly needs no second one without a stack count: a third of the icons with
            # no count look like they have one (an Attack Scroll's outline), and each reading is a pass over the
            # whole index
            d = _distances(vecs[:1], index)
            slot.status, slot.matches = _match(d, index, kb, top, vecs[:1])
            if len(vecs) > 1 and not (slot.status == "certain" and slot.matches[0][1] <= CLEAR):
                d = np.minimum(d, _distances(vecs[1:], index))
                slot.status, slot.matches = _match(d, index, kb, top, vecs)
        else:
            slot.status = "unknown"
        out.append(slot)
    return out


def ambiguous_example(slot: Slot, kb) -> str:
    """A name that stands for an ambiguous slot's look-alikes ("Gloves Attack Scroll: Lesser")."""
    for k, _ in slot.matches:
        e = kb.get(k)
        if e:
            return e["name"]
    return ""


def describe(slots: list[Slot], kb) -> str:
    """The reading for the AI: per slot, what the app knows about it."""
    lines = []
    for s in slots:
        names = [f"{kb.get(k)['name']} [{k}]" for k, _ in s.matches if kb.get(k)]
        if s.status == "hovered":
            lines.append(f"Slot {s.index}: under the mouse pointer. If the screenshot shows the game's item tooltip "
                         "(the box with an item name next to the pointer), that is this slot's item: read its name "
                         "there.")
        elif s.status == "ambiguous":
            shown = ", ".join(names[:8]) + (f", … ({len(names) - 8} more)" if len(names) > 8 else "")
            lines.append(f"Slot {s.index}: AMBIGUOUS, one of {len(names)} items drawn with the very same picture "
                         f"({shown}). The picture can't tell them apart: don't guess which one.")
        elif s.status == "unknown":
            lines.append(f"Slot {s.index}: not recognised (no single KB picture matches it; nearest: "
                         f"{', '.join(names) or 'none'}). Don't name it from these.")
        else:
            # a copy of the same item under another key is no other picture ("Jr. Sentinel Shellpiece [item/347]
            # (other close pictures: Jr. Sentinel Shellpiece [item/2584]") confused the prompt)
            same = names[0].rsplit(" [", 1)[0] + " [" if names else ""
            others = [n for n in names[1:] if not n.startswith(same)]
            alt = f" (other close pictures: {', '.join(others)})" if others else ""
            lines.append(f"Slot {s.index}: {names[0] if names else 'unknown'}{alt}")
    return "\n".join(lines)


def for_ai(slots: list[Slot], kb) -> str:
    """The hidden context the AI gets with an inventory check: the reading, and how to treat each kind of slot."""
    described = describe(slots, kb)
    if not described:
        return ""
    return ("<inventory_read>\nThe app matched each filled inventory slot's icon to the database pictures "
            "(slots count left to right, top to bottom):\n" + described + "\n\n"
            "How to use it:\n"
            "- A slot with a name was recognised from its picture: that is the item.\n"
            "- An AMBIGUOUS slot holds one of several items drawn with the very same picture (every scroll of a tier "
            "looks alike, so do the NPC letters). Never pick one of them yourself. If the screenshot shows the "
            "game's item tooltip for that slot (the box with the item's name next to the mouse pointer), use the "
            "name written there. Otherwise tell the player it is one of those look-alike items, and that to know "
            "which, they hover the mouse over it in the game (without clicking) until its name shows, then press "
            "F5 in the chat to check again.\n"
            "- A slot that is not recognised: say you couldn't tell what it is (same hover-and-F5 tip); never name "
            "it from the nearest pictures.\n"
            "- The slot under the mouse pointer: read its name from the tooltip in the screenshot.\n"
            "</inventory_read>")
