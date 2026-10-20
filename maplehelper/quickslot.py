"""The game's quick slots, read on this computer: which potion sits in each slot and how many are left, so the loot
log can count the potions used (no AI, nothing sent to the game).

The quick slots are a panel of 4 x 2 light slots in a dark frame, above the bottom bar on its right ("Shift", "Ins",
"Hm", "Pup" / "Ctrl", "Del", "End", "Pdn"). A slot holding an item shows its picture and, at its bottom left, the
count of that item in the inventory ("501", "0" when it ran out). The game's interface scales with its window, so the
panel is found by its shape: five dark vertical lines and three dark horizontal ones, evenly spaced (`find`), searched
in the bottom right of the game's picture, then each line is placed to the pixel. Measured live at 2562x1440 (the
panel at x 0.69-0.79, y 0.82-0.90 of the picture, a slot ~63 px apart, icons drawn ~1.6x their KB pictures).

A slot's item is the KB potion whose picture, scaled and laid on the slot's own light background, fits it best
(`identify`); the key's name printed over the slot's top and the count over its bottom left are left out of the
comparison by clipping each pixel's error, so they can't decide it. Only items that restore HP or MP are compared
(grind.is_potion): a skill's or a throwing star's picture in a slot is never named. The counts are read digit by digit
against the game's own digits (`read_count`).

`Watcher` turns successive reads into potions used: a count that went down, read the same twice in a row, is used;
a count that went up was bought or picked up, never used. No Qt: the loot scanner runs it on its worker thread."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

log = logging.getLogger("maplehelper")

SEARCH = (0.55, 0.65, 0.97, 0.98)   # where the panel may be, as fractions of the game's picture (left, top, right, bottom)
PITCH = (0.036, 0.052)              # a slot's pitch as a fraction of the picture's height (measured 0.044)
DARK = 50                           # the frame's dark lines: every channel at most this
LINES_MIN = 0.45                    # the lines must be this dark on average (live: 0.57)...
INNER_MAX = 0.2                     # ... and the slots' middles mostly not dark (live: 0.02)
NATIVE_PITCH = 39.0                 # a slot's pitch when the game draws icons at their KB size
CLIP = 60.0                         # a pixel's error counts at most this much: the key name and the count over the icon
FIT_MAX = 38.0                      # a best fit worse than this names no potion (an empty slot, a skill)
FIT_MARGIN = 3.0                    # ... and it must beat a potion with another picture by this much


@dataclass(frozen=True)
class Grid:
    """The panel: its vertical lines' x and horizontal lines' y in the picture's pixels (5 and 3, left / top first)."""
    xs: tuple[int, ...]
    ys: tuple[int, ...]

    def slots(self) -> list[tuple[int, int, int, int]]:
        """Each slot's inside (x0, y0, x1, y1), left to right, top row first."""
        out = []
        for j in range(2):
            for i in range(4):
                t = max(2, round((self.xs[i + 1] - self.xs[i]) * 0.07))    # the line and the slot's shaded rim
                out.append((self.xs[i] + t, self.ys[j] + t, self.xs[i + 1] - t + 1, self.ys[j + 1] - t + 1))
        return out

    @property
    def pitch(self) -> float:
        return (self.xs[-1] - self.xs[0]) / 4

    def box(self) -> tuple[int, int, int, int]:
        return self.xs[0], self.ys[0], self.xs[-1] + 1, self.ys[-1] + 1


def _integral(m: np.ndarray) -> np.ndarray:
    return np.pad(m.astype(np.float32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def _sums(i: np.ndarray, x, y, w: int, h: int):
    return i[y + h, x + w] - i[y, x + w] - i[y + h, x] + i[y, x]


def _coarse(dark: np.ndarray, lo: int, hi: int) -> tuple[float, int, int, int] | None:
    """(score, x, y, pitch) of the best 4 x 2 grid of dark lines in `dark`, pitches lo..hi."""
    ii = _integral(dark)
    rows, cols = dark.shape
    best = None
    for p in range(max(4, lo), hi + 1):
        t = max(1, round(p * 0.05))
        gw, gh = 4 * p, 2 * p
        if gw + t >= cols or gh + t >= rows:
            continue
        y, x = np.meshgrid(np.arange(rows - gh - t), np.arange(cols - gw - t), indexing="ij")
        lines = sum(_sums(ii, x + k * p, y, t, gh) for k in range(5)) / (5 * t * gh) * 5 / 8 \
            + sum(_sums(ii, x, y + k * p, gw, t) for k in range(3)) / (3 * t * gw) * 3 / 8
        m = max(1, round(p * 0.25))
        inner = sum(_sums(ii, x + a * p + m, y + b * p + m, p - 2 * m, p - 2 * m)
                    for a in range(4) for b in range(2)) / (8 * (p - 2 * m) ** 2)
        score = lines - inner
        at = np.unravel_index(int(np.argmax(score)), score.shape)
        if lines[at] < LINES_MIN or inner[at] > INNER_MAX:
            continue
        if best is None or score[at] > best[0]:
            best = (float(score[at]), int(x[at]), int(y[at]), p)
    return best


def _place(profile: np.ndarray, guess: float, reach: int) -> int:
    """The darkest line near `guess` in a 1-D darkness profile: the middle of the darkest run of its window."""
    lo, hi = max(0, int(guess) - reach), min(len(profile), int(guess) + reach + 1)
    win = profile[lo:hi]
    if not len(win):
        return int(guess)
    top = win.max()
    idx = np.flatnonzero(win >= top * 0.85)
    near = idx[np.argmin(np.abs(idx + lo - guess))]      # the run nearest the guess
    a = b = near
    while a - 1 >= 0 and win[a - 1] >= top * 0.85:
        a -= 1
    while b + 1 < len(win) and win[b + 1] >= top * 0.85:
        b += 1
    return lo + (a + b) // 2


def find(rgb: np.ndarray, play: tuple[int, int, int, int] | None = None) -> Grid | None:
    """The quick slot panel in a picture (RGB array; `play`: the game's picture in it, x, y, w, h), or None when it
    isn't shown (the player folded it, or a window covers it)."""
    if play is None:
        play = (0, 0, rgb.shape[1], rgb.shape[0])
    x, y, w, h = play
    left, top, right, bottom = SEARCH
    x0, y0 = x + int(w * left), y + int(h * top)
    x1, y1 = min(rgb.shape[1], x + int(w * right)), min(rgb.shape[0], y + int(h * bottom))
    area = rgb[y0:y1, x0:x1]
    if area.size == 0:
        return None
    return locate(area, (int(h * PITCH[0]), int(h * PITCH[1]) + 2), (x0, y0))


def locate(area: np.ndarray, pitch: tuple[int, int], at: tuple[int, int] = (0, 0)) -> Grid | None:
    """The panel in `area` (RGB), a slot's pitch in pitch's range of pixels; `at`: where area lies in the picture."""
    x0, y0 = at
    dark = area.max(axis=2) <= DARK
    # a first look at half size (four times faster), then every line to the pixel at full size
    half = dark[::2, ::2]
    got = _coarse(half, pitch[0] // 2, pitch[1] // 2)
    if got is None:
        return None
    _, gx, gy, p = got
    gx, gy, p = gx * 2, gy * 2, p * 2
    reach = max(3, p // 6)
    band_y = slice(max(0, gy), min(dark.shape[0], gy + 2 * p))
    band_x = slice(max(0, gx), min(dark.shape[1], gx + 4 * p))
    col = dark[band_y].mean(axis=0)
    row = dark[:, band_x].mean(axis=1)
    xs = tuple(x0 + _place(col, gx + k * p, reach) for k in range(5))
    ys = tuple(y0 + _place(row, gy + k * p, reach) for k in range(3))
    steps = np.diff(xs + ())
    if steps.min() < 0.8 * steps.mean() or steps.max() > 1.2 * steps.mean():
        return None
    return Grid(xs, ys)


# ---------------------------------------------------------------- which potion

@dataclass
class _Picture:
    key: str
    rgb: np.ndarray             # float32 h x w x 3
    alpha: np.ndarray           # float32 h x w, 0-1
    group: int                  # potions drawn with the very same picture share a group (no picture can tell them apart)


_pictures_lock = threading.Lock()
_pictures: dict[str, list[_Picture]] = {}


def _potion_pictures(kb) -> list[_Picture]:
    from . import grind
    root = str(getattr(kb, "root", id(kb)))
    with _pictures_lock:
        if root in _pictures:
            return _pictures[root]
        out: list[_Picture] = []
        seen: dict[bytes, int] = {}
        for k, e in kb.entities.items():
            if e.get("category") != "item" or not grind.is_potion(kb, k):
                continue
            path = kb.picture(k)
            if not path:
                continue
            try:
                im = Image.open(path).convert("RGBA")
            except OSError:
                continue
            a = np.asarray(im, np.float32)
            sig = a.astype(np.uint8).tobytes()
            group = seen.setdefault(sig, len(seen))
            out.append(_Picture(k, a[..., :3], a[..., 3] / 255.0, group))
        _pictures[root] = out
        return out


def _scaled(pic: _Picture, k: float) -> tuple[np.ndarray, np.ndarray]:
    h, w = pic.alpha.shape
    size = (max(1, round(w * k)), max(1, round(h * k)))
    rgba = np.dstack([pic.rgb, pic.alpha[..., None] * 255]).astype(np.uint8)
    im = Image.fromarray(rgba, "RGBA").resize(size, Image.BILINEAR)
    a = np.asarray(im, np.float32)
    return a[..., :3], a[..., 3] / 255.0


def _fit(slot: np.ndarray, pic: _Picture, k: float) -> float:
    """How far the slot is from the picture scaled by k and laid on the slot's background, at the best of a few
    places around the slot's middle (mean clipped error over the picture's own pixels)."""
    rgb, alpha = _scaled(pic, k)
    ph, pw = alpha.shape
    sh, sw = slot.shape[:2]
    if ph > sh or pw > sw:
        return float("inf")
    solid = alpha > 0.5
    if solid.sum() < 20:
        return float("inf")
    best = float("inf")
    cy, cx = (sh - ph) / 2, (sw - pw) / 2
    reach = max(2, round(sh * 0.1))
    for dy in range(-reach, reach + 1, 2):
        for dx in range(-reach, reach + 1, 2):
            y0, x0 = round(cy + dy), round(cx + dx)
            if y0 < 0 or x0 < 0 or y0 + ph > sh or x0 + pw > sw:
                continue
            part = slot[y0:y0 + ph, x0:x0 + pw]
            err = np.minimum(np.abs(part - rgb).mean(axis=2), CLIP)
            d = float(err[solid].mean())
            best = min(best, d)
    return best


def identify(slot: np.ndarray, kb, pitch: float) -> str | None:
    """The KB potion a slot's picture (RGB array of its inside) shows, or None (empty, or no potion fits)."""
    s = slot.astype(np.float32)
    sat = s.max(axis=2) - s.min(axis=2)
    if (sat > 60).mean() < 0.05:
        return None                 # just the light slot and its key name: empty
    pics = _potion_pictures(kb)
    base = pitch / NATIVE_PITCH
    scored: list[tuple[float, _Picture]] = []
    for pic in pics:
        d = min(_fit(s, pic, base * f) for f in (0.92, 1.0, 1.08))
        scored.append((d, pic))
    if not scored:
        return None
    scored.sort(key=lambda r: r[0])
    d, best = scored[0]
    other = next((dd for dd, p in scored[1:] if p.group != best.group), float("inf"))
    if d > FIT_MAX or other - d < FIT_MARGIN:
        return None
    return best.key


# ---------------------------------------------------------------- counts
# The count is the game's own digit font (white fading to blue, outlined in black), drawn over the slot's bottom left.
# OCR read it as noise ("501" as "5下": the digits are ~17 px tall and sit on the item's picture), so each digit is
# matched against the font's own digits (assets/quickslot/<digit>.png: the brightness of each digit at a slot pitch of
# DIGIT_PITCH, averaged over live reads at 2562x1440): normalized cross-correlation, which a dimmed panel doesn't
# change. The digits stand left to right from the slot's left edge; the first must match DIGIT_FIRST well, each next
# one DIGIT_NEXT. A count with a digit that has no picture yet (7: never seen live) reads as no count, so nothing is
# counted for it rather than a wrong number.

DIGIT_PITCH = 63.0
DIGIT_FIRST = 0.7           # empty slots and skills matched at most 0.59, real first digits at least 0.77
DIGIT_NEXT = 0.62           # real next digits matched 0.67 and up
DIGIT_MAX = 5

_digits_lock = threading.Lock()
_digits: dict[str, np.ndarray] | None = None


def _digit_templates() -> dict[str, np.ndarray]:
    global _digits
    with _digits_lock:
        if _digits is None:
            from .store import ASSETS
            out = {}
            for path in sorted((ASSETS / "quickslot").glob("[0-9].png")):
                a = np.asarray(Image.open(path).convert("L"), np.float32)
                out[path.stem] = (a - a.mean()) / (a.std() or 1.0)
            _digits = out
        return _digits


def _lum(c: np.ndarray) -> np.ndarray:
    return c.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _count_area(rgb: np.ndarray, grid: Grid, i: int) -> np.ndarray:
    """The slot's bottom left, where the count stands, as brightness at DIGIT_PITCH (the rows end at the slot's
    bottom line)."""
    a, b = i % 4, i // 4
    k = DIGIT_PITCH / grid.pitch
    x0, y1 = grid.xs[a], grid.ys[b + 1]
    c = rgb[max(0, y1 - int(30 / k)):y1 + 1, x0:x0 + int(60 / k)]
    if abs(k - 1) > 0.02 and c.size:
        c = np.asarray(Image.fromarray(np.ascontiguousarray(c)).resize(
            (max(1, round(c.shape[1] * k)), max(1, round(c.shape[0] * k))), Image.BILINEAR))
    return _lum(c)


def _ncc(a: np.ndarray, t: np.ndarray) -> float:
    a = a - a.mean()
    d = float(np.sqrt((a * a).sum() * (t * t).sum()))
    return float((a * t).sum() / d) if d > 0 else -1.0


def _best(area: np.ndarray, temps: dict[str, np.ndarray], xs, ys) -> tuple[float, str, int, int]:
    best = (-2.0, "", 0, 0)
    for d, t in temps.items():
        h, w = t.shape
        for y in ys:
            for x in xs:
                if x < 0 or y < 0 or y + h > area.shape[0] or x + w > area.shape[1]:
                    continue
                v = _ncc(area[y:y + h, x:x + w], t)
                if v > best[0]:
                    best = (v, d, x, y)
    return best


def read_count(area: np.ndarray) -> int | None:
    """The count in a slot's count area (_count_area), or None when no count is read."""
    temps = _digit_templates()
    if not temps:
        return None
    v, d, x, y = _best(area, temps, range(2, 8), range(0, 10))
    if v < DIGIT_FIRST:
        return None
    out = d
    x += temps[d].shape[1]
    while len(out) < DIGIT_MAX:
        v, d, xx, yy = _best(area, temps, range(x - 2, x + 4), range(y - 1, y + 2))
        if v < DIGIT_NEXT:
            break
        out += d
        x, y = xx + temps[d].shape[1], yy
    return int(out)


@dataclass
class Read:
    """One read of the panel: slot index -> (KB potion, count); a slot with no potion, or its count unread, is left
    out."""
    potions: dict[int, tuple[str, int]] = field(default_factory=dict)


class Reader:
    """Reads the panel in a grab of the game's picture; the panel's place and each slot's item are kept between reads
    and worked out again only when the slot's picture changes (identifying is the slow part)."""

    def __init__(self, kb):
        self.kb = kb
        self._grid: Grid | None = None
        self._grid_size: tuple[int, int] | None = None
        self._items: dict[int, tuple[bytes, str | None]] = {}

    def reset(self) -> None:
        self._grid, self._grid_size, self._items = None, None, {}

    def _sig(self, slot: np.ndarray) -> bytes:
        """A slot's picture without its count (bottom left), coarse: the same item reads the same."""
        h = slot.shape[0]
        part = slot[: int(h * 0.6)]
        small = np.asarray(Image.fromarray(np.ascontiguousarray(part)).resize((8, 5), Image.BILINEAR), np.int16)
        return (small // 24).astype(np.uint8).tobytes()

    def read(self, rgb: np.ndarray, play: tuple[int, int, int, int] | None = None) -> Read | None:
        """None: no panel (folded, covered)."""
        size = (rgb.shape[1], rgb.shape[0])
        grid = self._grid if self._grid_size == size else None
        if grid is not None and not _still(rgb, grid):
            grid = None
        if grid is None:
            grid = find(rgb, play)
            self._items = {}
            if grid is None:
                self._grid = None
                return None
            self._grid, self._grid_size = grid, size
        out = Read()
        for i, (x0, y0, x1, y1) in enumerate(grid.slots()):
            slot = rgb[y0:y1, x0:x1]
            sig = self._sig(slot)
            known = self._items.get(i)
            if known is None or known[0] != sig:
                self._items[i] = (sig, identify(slot, self.kb, grid.pitch))
            key = self._items[i][1]
            if key:
                n = read_count(_count_area(rgb, grid, i))
                if n is not None:
                    out.potions[i] = (key, n)
        return out


def _still(rgb: np.ndarray, grid: Grid) -> bool:
    """The panel is still where it was: its lines are still dark."""
    x0, y0, x1, y1 = grid.box()
    if x1 > rgb.shape[1] or y1 > rgb.shape[0]:
        return False
    dark = rgb.max(axis=2) <= DARK
    vals = [dark[y0:y1, x].mean() for x in grid.xs] + [dark[y, x0:x1].mean() for y in grid.ys]
    return float(np.mean(vals)) >= LINES_MIN * 0.8


# ---------------------------------------------------------------- potions used

CONFIRM = 2         # a new count must be read this many times in a row before it counts
DROP_BASE = 20      # a drop counts as used only up to this many ...
DROP_PER_S = 3.0    # ... plus this many a second since the count was last read (more: a misread, or potions moved
                    # to storage); such a count, read RESET_READS times in a row, becomes the count: nothing used
RESET_READS = 6


@dataclass
class _Stack:
    count: int | None           # the last count that counted; None until a first count is read CONFIRM times (a
                                # dimmed frame once read another potion's picture there)
    at: float = 0.0             # when it was last read
    pending: int | None = None  # a new count, read `seen` times
    seen: int = 0


class Watcher:
    """Potions used, from successive reads: per potion (not per slot: two slots can hold the same potion, and show the
    same count), a drop in its count is used; a rise (bought, picked up) only moves the count it is measured from. A
    potion picked up between two reads while others were used hides that many uses: a few, at most. A count read
    differently in two slots of the same potion is skipped that read."""

    def __init__(self):
        self._stacks: dict[str, _Stack] = {}

    def reset(self) -> None:
        self._stacks = {}

    def read(self, rd: Read, at: float = 0.0) -> dict[str, int]:
        """Potion key -> how many were used since the last read (only those used). `at`: the read's time (s)."""
        seen: dict[str, set[int]] = {}
        for key, n in rd.potions.values():
            seen.setdefault(key, set()).add(n)
        used: dict[str, int] = {}
        for key, ns in seen.items():
            if len(ns) != 1:
                continue
            n = ns.pop()
            st = self._stacks.setdefault(key, _Stack(None, at))
            if st.count is None:                        # the baseline, once read the same CONFIRM times
                st.seen = st.seen + 1 if st.pending == n else 1
                st.pending = n
                if st.seen >= CONFIRM:
                    st.count, st.pending, st.seen, st.at = n, None, 0, at
                continue
            if n == st.count:
                st.pending, st.seen, st.at = None, 0, at
                continue
            if st.pending == n:
                st.seen += 1
            else:
                st.pending, st.seen = n, 1
            if st.seen < CONFIRM:
                continue
            drop = st.count - n
            if drop > DROP_BASE + DROP_PER_S * max(0.0, at - st.at):
                if st.seen >= RESET_READS:
                    st.count, st.pending, st.seen, st.at = n, None, 0, at
                continue
            if drop > 0:
                used[key] = drop
            st.count, st.pending, st.seen, st.at = n, None, 0, at
        return used
