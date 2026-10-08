"""Which map the player is on, from a picture of the game's minimap (the box the player drew around it), and where
on it they stand (the yellow dot). Compares the picture with the KB's minimap pictures (img/map/<id>.png: the
game's own minimap art). No Qt: runs on a worker thread."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from . import routes


@dataclass(frozen=True)
class Here:
    """Where the player is: a routes.Graph map id, and the yellow dot's spot on that map's minimap picture as
    fractions of its size (as routes.Leg.spot), None when the dot wasn't seen."""
    map: str
    spot: tuple[float, float] | None = None


#: The map only counts when its picture matches this well (normalized cross-correlation, 0-1): below it the box is
#: probably not a minimap at all, and naming a map anyway would plant the player on the wrong one.
MATCH_THRESHOLD = 0.5
#: How much better the best map must be than the next-best one: below it the box is ambiguous (a tie goes to the
#: duplicate-picture waiver, anything else to None). 0.05: the closest genuinely different pictures (two tree maps
#: at 0.962 vs 0.900) still clear it, while look-alike ties sit far below.
MATCH_MARGIN = 0.05
_GROUP_SIM = 0.93    # two KB pictures matching each other this well are the same drawing (a door moved, a booth
                    # renumbered): the margin must not reject between them; the tie-break below picks
#: How well the remembered map must still fit for the cheap path: the same map at the wrong scale scores ~0.69
#: and look-alikes fluke ~0.5, while the same map at the same scale scores ~0.99 on a full view (and lower, ~0.7,
#: on a cropped live view, where markers and the crop eat into it) — anything lower falls through to the full
#: search (slower that once, but never a wrong map or a mis-scaled dot). Below 0.8 the lock must also beat every
#: neighbour by the margin (a look-alike neighbour can fluke 0.65 too), and a neighbour on its own needs 0.8.
VERIFY_THRESHOLD = 0.65
_GRID = 1.25   # neighbouring trial scales differ by this much: the coarse trials sit this densely near fitting, so
                # some trial always lands within ~12% of the player's scale there (tighter combs take it from there)
_NEEDY_DROP = 0.18     # a picture losing this much matching itself 3% off needs dense trials (thin features crater;
                       # tolerant maps lose under 0.14 there, needy ones over 0.24)
_ULTRA_DROP = 0.27     # ... and losing this much gets the narrow comb as well (tolerant maps lose under 0.14 here,
                       # while Snail and Hair at 0.27 already need it)
_HYPER_DROP = 0.38     # ... and losing this much needs sub-percent coarse trials too (its match lives in a ±0.2%
                       # wide peak no sparser grid can rank: only ~20 such pictures exist, so the comb stays ok)
_COV_MAX = 160   # the coarse search compares shrunken pictures (longest side); the best are rechecked full-size
_TINY_MAX = 60    # ... after a tiny pre-rank (longest side) shortlists the maps going on to the coarse search
_TINY_TOP = 36    # ... this many maps: the true one ranks far above the cut on a clean correlation
_WORKERS = min(8, os.cpu_count() or 4)   # the sweeps compare every map on its own (numpy and PIL release the GIL):
                                         # threads only ever recompute each other's cache entries, never wrong ones
_RERANK_MAX = 320   # ... re-ranked half-size-ish (their offsets are thrown away; only scores and scales go on)
_MIN_SIDE = 8       # a match smaller than this is noise, not a minimap ...
_MIN_AREA = 0.05    # ... and so is one this small next to the panel (the panel is cut around the map, not the town)
_OV = 2.5   # how much bigger than the panel the scaled picture may be (a cropped live view shows only part of the
            # map: the panel is a sub-window of the scaled picture, up to this far per side)
_OVERLAP_MIN = 0.5    # ... while still covering this much of the smaller side: less is a corner touching, not a view
_PANEL_SMALL = 170   # the surround fill that finds the content panel runs this small (longest side): a blurred 1px
                      # outline still blocks the fill there, while quarter-scale would ramp it into a passable slope
_PANEL_STEP = 16  # a colour step this big stops the fill: frames and title edges are far steeper, their insides far
                  # flatter, so the fill covers margins, title and frame but never the map itself (even the 1px
                  # synthetic outline blurred down: black art against the dark margin still steps ~28 there)
_BG_SAT = 36      # the panel background colour is the median of pixels flatter than this (max-min across channels):
                  # the dark backdrop, not the art or the markers
_DOT_R, _DOT_G, _DOT_B = 200, 190, 130  # a yellow blob: strong red and green, little blue ...
_DOT_MIN, _DOT_MAX = 4, 800             # ... of a dot's size: smaller is a stray pixel, bigger a piece of the art
_DOT_BIG = 20   # ... but only this big a blob may be a dot painted over yellow art (a bright speck stays smaller)
_ART_YELLOW = 150   # KB art this yellow, resampled to the matched size, is what a blob is checked against
_VETO_SHIFT = 0.06  # ... slid this far looking for it: the refined scale can still be ~6% off, so a blob never sits
                    # exactly on its art pixels (half the blob must still land on yellow to call it art)
_BRIGHT_MIN = 40    # ... unless the blob outshines the art by this much (R+G): the game's dot painted over yellow
                    # lava reads ~510 against ~420, while resampling noise stays within ~15


@dataclass(frozen=True)
class _Hit:
    """One candidate placed on the panel: which map, how well it fits, at which scale, and where (panel pixels:
    the picture's top-left, negative when the cropped view cuts it off)."""
    mid: str
    score: float
    scale: float
    x: int
    y: int


def _fast_len(n: int) -> int:
    """The next size the FFT likes (only prime factors 2, 3, 5): a power of two overshoots small pictures by a lot
    (262 -> 512), and the search does thousands of these transforms."""
    n = max(int(n), 2)
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _ncc(image: np.ndarray, templ: np.ndarray) -> tuple[float, int, int] | None:
    """Where the small picture sits inside the big one (normalized cross-correlation through the FFT): the best
    score with its panel offset. None when the template doesn't fit or is flat (a blank picture matches anywhere)."""
    h, w = templ.shape
    H, W = image.shape
    if h > H or w > W:
        return None
    t = templ.astype(np.float32)
    t -= float(t.mean())
    if float(np.dot(t.ravel(), t.ravel())) <= 1e-9:
        return None
    size = (_fast_len(H + h - 1), _fast_len(W + w - 1))
    corr = np.fft.irfft2(np.fft.rfft2(image, s=size) * np.conj(np.fft.rfft2(t, s=size)), s=size)
    corr = corr[: H - h + 1, : W - w + 1]
    img = image.astype(np.float64)
    box = np.zeros((H + 1, W + 1), dtype=np.float64)
    box[1:, 1:] = img.cumsum(axis=0).cumsum(axis=1)         # adds up any window in four lookups
    sums = box[h:, w:] - box[:-h, w:] - box[h:, :-w] + box[:-h, :-w]
    box[1:, 1:] = (img * img).cumsum(axis=0).cumsum(axis=1)
    sumsq = box[h:, w:] - box[:-h, w:] - box[h:, :-w] + box[:-h, :-w]
    n = h * w
    denom = np.sqrt(np.maximum(sumsq - sums * sums / n, 0.0) * float(np.dot(t.ravel(), t.ravel())))
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.divide(corr, denom, out=np.zeros_like(corr), where=denom > 1e-9)
    y, x = divmod(int(np.argmax(scores)), scores.shape[1])
    return float(scores[y, x]), x, y


def _ncc_over(image: np.ndarray, templ: np.ndarray,
              win: tuple[int, int, int, int] | None = None) -> tuple[float, int, int] | None:
    """Where the template sits on the image when it may hang off any edge (a cropped view: the scaled picture is
    bigger than the panel, so the panel is a sub-window of it). The best score with the template's top-left in
    image pixels (negative when cut off); None when no placement overlaps enough to be a view. win boxes the
    searched placements to (x_lo, x_hi, y_lo, y_hi): the refinement already knows roughly where the coarse search
    put it, and a 100px box costs a fraction of the whole grid."""
    H, W = image.shape
    h, w = templ.shape
    oy_all = np.arange(-(h - 1), H)      # every placement, including overhangs ...
    ox_all = np.arange(-(w - 1), W)
    oh_all = np.minimum(H, oy_all + h) - np.maximum(0, oy_all)
    ow_all = np.minimum(W, ox_all + w) - np.maximum(0, ox_all)
    need = _OVERLAP_MIN * min(H * W, h * w)
    ok_y = (oh_all >= _MIN_SIDE) & (oh_all * min(W, w) >= need)
    ok_x = (ow_all >= _MIN_SIDE) & (ow_all * min(H, h) >= need)
    if win is not None:   # ... and to the refinement's window around the coarse placement
        ok_y &= (oy_all >= win[2]) & (oy_all <= win[3])
        ok_x &= (ox_all >= win[0]) & (ox_all <= win[1])
    if not bool(ok_y.max()) or not bool(ok_x.max()):   # no placement overlaps enough to be a view
        return None
    oy = oy_all[ok_y][:, None]          # ... but the heavy sums only run on the bounding box of placements
    ox = ox_all[ok_x][None, :]          # that can still pass the overlap guards (a cropped view sits well inside)
    oh = oh_all[ok_y][:, None]
    ow = ow_all[ok_x][None, :]
    n = oh * ow
    y0 = np.clip(oy, 0, H)
    y1 = np.clip(oy + h, 0, H)
    x0 = np.clip(ox, 0, W)
    x1 = np.clip(ox + w, 0, W)
    img = image.astype(np.float64)
    tm = templ.astype(np.float64)
    ii = np.zeros((H + 1, W + 1), dtype=np.float64)      # window sums over any overlap in four lookups ...
    ii[1:, 1:] = img.cumsum(axis=0).cumsum(axis=1)
    iq = np.zeros((H + 1, W + 1), dtype=np.float64)
    iq[1:, 1:] = (img * img).cumsum(axis=0).cumsum(axis=1)
    ti = np.zeros((h + 1, w + 1), dtype=np.float64)
    ti[1:, 1:] = tm.cumsum(axis=0).cumsum(axis=1)
    tq = np.zeros((h + 1, w + 1), dtype=np.float64)
    tq[1:, 1:] = (tm * tm).cumsum(axis=0).cumsum(axis=1)
    ty0, tx0 = y0 - oy, x0 - ox
    ty1, tx1 = y1 - oy, x1 - ox
    si = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    qi = iq[y1, x1] - iq[y0, x1] - iq[y1, x0] + iq[y0, x0]
    st = ti[ty1, tx1] - ti[ty0, tx1] - ti[ty1, tx0] + ti[ty0, tx0]
    qt = tq[ty1, tx1] - tq[ty0, tx1] - tq[ty1, tx0] + tq[ty0, tx0]
    size = (_fast_len(H + h - 1), _fast_len(W + w - 1))
    corr = np.fft.irfft2(np.fft.rfft2(image, s=size) * np.conj(np.fft.rfft2(templ, s=size)), s=size)
    cc = corr[oy % size[0], ox % size[1]]     # the overlap's sum of products at every placement
    with np.errstate(divide="ignore", invalid="ignore"):
        n1 = np.maximum(n, 1)
        num = cc - si * st / n1
        vi = qi - si * si / n1
        vt = qt - st * st / n1
        denom = np.sqrt(np.maximum(vi, 0.0) * np.maximum(vt, 0.0))
        scores = np.divide(num, denom, out=np.full_like(cc, -2.0), where=denom > 1e-9)
    valid = ((oh >= _MIN_SIDE) & (ow >= _MIN_SIDE) & (n >= _OVERLAP_MIN * min(H * W, h * w))
             & (vi > n1) & (vt > n1))   # both sides must actually vary (a flat backdrop against a flat backdrop
    scores = np.where(valid, scores, -2.0)   # divides rounding dust by rounding dust, scoring in the hundreds)
    i, j = divmod(int(np.argmax(scores)), scores.shape[1])
    if float(scores[i, j]) <= -1.0:
        return None
    return float(scores[i, j]), int(ox[0, j]), int(oy[i, 0])


def _blobs(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """The connected lit patches (8-way), each a list of (y, x): the mask only ever holds a handful of lit pixels,
    so a plain flood fill is plenty."""
    ys, xs = np.nonzero(mask)
    left = set(zip(ys.tolist(), xs.tolist()))
    out = []
    while left:
        seed = left.pop()
        stack, one = [seed], [seed]
        while stack:
            y, x = stack.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    q = (y + dy, x + dx)
                    if q in left:
                        left.discard(q)
                        stack.append(q)
                        one.append(q)
        out.append(one)
    return out


def _detect_panel(arr: np.ndarray) -> tuple[int, int, int, int]:
    """The minimap content panel inside the drawn box (x0, y0, x1, y1, exclusive): the box holds the window's title
    bar, header and frame around the map, and a cropped live view needs them gone before matching. A flood fill
    from the border through smoothly connected colours covers margins, title and frame but stops at the frame's
    steep edge; the biggest remaining hole is the panel, snapped back out to its frame lines. The whole box when
    the fill barely spreads (no surround: noise, or art drawn edge to edge)."""
    H, W, _ = arr.shape
    q = max(1, min(4, round(max(H, W) / _PANEL_SMALL)))
    sw, sh = max(1, W // q), max(1, H // q)
    small = np.asarray(arr if (sw, sh) == (W, H)
                       else Image.fromarray(arr).resize((sw, sh), Image.BILINEAR)).astype(np.int16)
    grown = np.zeros((sh, sw), dtype=bool)
    grown[0, :] = grown[-1, :] = grown[:, 0] = grown[:, -1] = True
    gaps = []   # the passable steps per direction, computed once: the loop below is then boolean ops only
    for ay, ax in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rolled = np.roll(small, (ay, ax), (0, 1))
        gap = np.abs(small - rolled).max(axis=2) <= _PANEL_STEP
        if ay > 0:
            gap[0, :] = False
        elif ay < 0:
            gap[-1, :] = False
        if ax > 0:
            gap[:, 0] = False
        elif ax < 0:
            gap[:, -1] = False
        gaps.append(((ay, ax), gap))
    while True:
        new = np.zeros((sh, sw), dtype=bool)
        for (ay, ax), gap in gaps:
            prev = np.roll(grown, (ay, ax), (0, 1))
            if ay > 0:
                prev[0, :] = False
            elif ay < 0:
                prev[-1, :] = False
            if ax > 0:
                prev[:, 0] = False
            elif ax < 0:
                prev[:, -1] = False
            new = new | (prev & ~grown & gap)
        if not bool(new.max()):
            break
        grown |= new
    if grown.mean() < 0.02:
        return 0, 0, W, H
    rest = ~grown
    rest[0, :] = rest[-1, :] = rest[:, 0] = rest[:, -1] = False
    if not bool(rest.max()):
        return 0, 0, W, H
    coarse = rest[::2, ::2]   # the panel is the biggest hole by far: label it at half again the resolution ...
    if not bool(coarse.max()):
        return 0, 0, W, H
    biggest = max(_blobs(coarse), key=len)
    if len(biggest) < _MIN_AREA * coarse.shape[0] * coarse.shape[1]:
        return 0, 0, W, H
    ys = [y for y, _ in biggest]
    xs = [x for _, x in biggest]
    qq = q * 2   # ... so its box counts back up through both downscalings (a couple of pixels off, which the snap
    x0, y0, x1, y1 = min(xs) * qq, min(ys) * qq, min(W, (max(xs) + 1) * qq), min(H, (max(ys) + 1) * qq)
    x0, y0, x1, y1 = _snap_out(arr, x0, y0, x1, y1)
    for _ in range(8):   # the blurred frame bleeds a pixel or two into the box: strip near-white edges back to art
        if y1 - y0 <= _MIN_SIDE or x1 - x0 <= _MIN_SIDE:
            break
        white = lambda px: bool((px.min(axis=-1) > 200).mean() > 0.5)
        stripped = False
        if white(arr[y0, x0:x1].astype(np.int16)):
            y0 += 1
            stripped = True
        if white(arr[y1 - 1, x0:x1].astype(np.int16)):
            y1 -= 1
            stripped = True
        if white(arr[y0:y1, x0].astype(np.int16)):
            x0 += 1
            stripped = True
        if white(arr[y0:y1, x1 - 1].astype(np.int16)):
            x1 -= 1
            stripped = True
        if not stripped:
            break
    return (x0, y0, x1, y1)


def _snap_out(arr: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """Push each panel edge back out to its frame line: the shrunken fill leaks through a blurred 1px outline into
    flat black art (a gradient ramp no colour step can block), so the box can start rows inside the picture. The
    leaked zone is flat, the frame a long steep step across most of the edge: the first such line scanning outward
    (40px) is the frame, and snapping just inside it only ever grows the panel, never crops it. Title text is too
    short to count as a line."""
    H, W, _ = arr.shape

    def rows(y: int) -> bool:
        if y < 0 or y + 1 >= H:
            return False
        step = np.abs(arr[y, x0:x1].astype(np.int16) - arr[y + 1, x0:x1].astype(np.int16)).max(axis=1)
        return bool((step >= 40).mean() >= 0.7)

    def cols(x: int) -> bool:
        if x < 0 or x + 1 >= W:
            return False
        step = np.abs(arr[y0:y1, x].astype(np.int16) - arr[y0:y1, x + 1].astype(np.int16)).max(axis=1)
        return bool((step >= 40).mean() >= 0.7)

    for y in range(y0 - 1, max(-1, y0 - 41), -1):
        if rows(y):
            y0 = y + 1
            break
    for y in range(y1 - 1, min(H - 1, y1 + 39)):
        if rows(y):
            y1 = y + 1
            break
    for x in range(x0 - 1, max(-1, x0 - 41), -1):
        if cols(x):
            x0 = x + 1
            break
    for x in range(x1 - 1, min(W - 1, x1 + 39)):
        if cols(x):
            x1 = x + 1
            break
    return (x0, y0, x1, y1)


def _marker_mask(arr: np.ndarray) -> np.ndarray:
    """Which panel pixels are overlay markers, not map art: the game's saturated portal rings (blue), NPC pills
    (green), monster/other-player pills (red), the cyan arrow and bright yellows. Map browns and tans never reach
    these extremes (a rock reads ~(170, 125, 70), a tan ~(238, 208, 169)), so masking them out of the correlation
    only ever removes paint, never art."""
    rgb = arr.astype(np.int16)
    R, G, B = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    red = (R > 200) & (G < 120) & (B < 120)
    green = (G > 150) & (R < 100) & (B < 100)
    blue = (B > 140) & (R < 120) & ((B - R) > 60)
    yellow = (R > 220) & (G > 190) & (B < 120)
    cyan = (G > 150) & (B > 150) & (R < 110)
    return red | green | blue | yellow | cyan


_NEED_CACHE: dict[tuple[str, int, int], float] = {}


def _calibrated(loc: Locator, mid: str) -> float:
    """A picture's self-drop at 3% off, shared between readers by file identity (path, size, mtime): the nightly KB
    refresh replaces pictures, and the key notices."""
    drop = 1.0
    pic = loc._pic(mid)
    if pic is not None:
        p = loc._graph.minimap(mid)
        try:
            st = Path(p).stat() if p is not None else None
        except OSError:
            st = None
        key = (str(p), st.st_size if st else -1, st.st_mtime_ns if st else -1)
        if key not in _NEED_CACHE:
            base, _, _ = pic
            big = base.resize((max(1, round(base.width * 1.03)), max(1, round(base.height * 1.03))),
                              Image.BILINEAR)
            found = _ncc(np.asarray(big, dtype=np.float32), np.asarray(base, dtype=np.float32))
            _NEED_CACHE[key] = 1.0 - found[0] if found is not None else 1.0
        drop = _NEED_CACHE[key]
    return drop


def _art_blob(yellow: np.ndarray, art_rs: np.ndarray, region: np.ndarray, ys: np.ndarray, xs: np.ndarray
              ) -> tuple[float, float]:
    """How much of this yellow blob the KB art explains: its best overlap with the art's yellow over small shifts
    (the matched scale is a few percent off, so it never sits exactly on its art pixels), as a fraction of the
    blob — plus how much brighter the blob reads than the art there (R+G). Of the best placements the nearest to
    no shift counts, so shade differences farther out cannot fake a bright blob."""
    H, W = yellow.shape
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1
    r = max(8, round(_VETO_SHIFT * max(H, W)))
    wy0, wy1, wx0, wx1 = max(0, y0 - r), min(H, y1 + r), max(0, x0 - r), min(W, x1 + r)
    a = yellow[wy0:wy1, wx0:wx1].astype(np.float64)
    b = np.zeros((y1 - y0, x1 - x0))
    b[ys - y0, xs - x0] = 1.0
    size = (_fast_len(a.shape[0] + b.shape[0] - 1), _fast_len(a.shape[1] + b.shape[1] - 1))
    corr = np.fft.ifft2(np.fft.fft2(a, s=size) * np.conj(np.fft.fft2(b, s=size))).real
    grid = corr[:a.shape[0] - b.shape[0] + 1, :a.shape[1] - b.shape[1] + 1]
    best = float(grid.max())
    frac = best / len(ys)
    if frac < 0.5:
        return frac, 0.0
    cy, cx = y0 - wy0, x0 - wx0
    cand_y, cand_x = np.nonzero(grid >= best - 0.5)
    i = int(np.argmin((cand_y - cy) ** 2 + (cand_x - cx) ** 2))
    py, px = int(cand_y[i]), int(cand_x[i])
    ay = wy0 + py + (ys - y0)
    ax = wx0 + px + (xs - x0)
    shot = region[ys, xs].astype(np.int32)
    art = art_rs[ay, ax].astype(np.int32)
    return frac, float(((shot[:, 0] + shot[:, 1]) - (art[:, 0] + art[:, 1])).mean())


class Locator:
    """Which map a minimap box shows, by sliding every KB minimap picture over its content panel (normalized
    cross-correlation): the box holds the window's title, header and frame around the map at an unknown scale, so
    the panel is cut out first, each candidate composited onto the panel's own background colour (the KB pictures
    are transparent where empty: the game draws the dark backdrop there), and overlay markers masked out of the
    panel side. A cropped live view shows only part of the map, so candidates may also be bigger than the panel:
    then the panel is the sub-window placed on the scaled picture. Each candidate is tried shrunken (a tiny
    pre-rank first, densely for thin-featured pictures, which crater a few percent off), the best two dozen
    re-ranked bigger, the six best refined at full resolution, where the call is made (a tie between one drawing
    shared by several maps goes to the tie-break, any other tie to None). The winner (map, scale, offset) is then
    remembered and just rechecked, neighbours first, so the every-second scan costs a comparison or two — a
    scrolled view just moves the offset, which the recheck re-finds at the locked scale."""

    def __init__(self, graph: routes.Graph) -> None:
        self._graph = graph
        self._candidates = [mid for mid in graph.maps if self._exists(graph.minimap(mid))]
        self._pics: dict[str, tuple[Image.Image, np.ndarray, Image.Image] | None] = {}
        self._locked: tuple[str, float, int, int] | None = None
        self._need: dict[str, float] = {}
        self._sim: dict[tuple[str, str], float] = {}
        self._panel: tuple[int, int, tuple[int, int, int, int]] | None = None
        self._comp_cache: dict[tuple[str, int, int, int], np.ndarray] = {}

    @staticmethod
    def _exists(p) -> bool:
        return p is not None and Path(p).exists()

    def reset(self) -> None:
        """Forget the remembered map and scale: the next locate() searches everything again."""
        self._locked = None

    def _panel_view(self, arr: np.ndarray, fresh: bool) -> tuple[np.ndarray, tuple[int, int, int], np.ndarray]:
        """The content panel with its background colour and its marker-inpainted grey shape: the correlation works
        on the inpainted shape (markers would fight the art), the dot finder on the raw panel. The detected frame
        is reused while the box keeps its size (the cheap path); a fresh detection runs for every full search and
        whenever the cheap path fails."""
        H, W, _ = arr.shape
        if fresh or self._panel is None or self._panel[0] != W or self._panel[1] != H:
            self._panel = (W, H, _detect_panel(arr))
        x0, y0, x1, y1 = self._panel[2]
        panel = arr[y0:y1, x0:x1]
        if min(panel.shape[0], panel.shape[1]) < _MIN_SIDE:
            panel = arr
        flat = panel.reshape(-1, 3).astype(np.int16)
        sat = flat.max(axis=1) - flat.min(axis=1)
        base = flat[sat <= _BG_SAT]
        bg = np.median(base, axis=0) if len(base) else np.median(flat, axis=0)
        bg_rgb = (int(bg[0]), int(bg[1]), int(bg[2]))
        bgL = 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2]
        grey = np.asarray(Image.fromarray(panel).convert("L"), dtype=np.float32)
        clean = grey.copy()
        clean[_marker_mask(panel)] = bgL
        return panel, bg_rgb, clean

    @staticmethod
    def _shrunk(panel: np.ndarray, mask: np.ndarray, bgL: float, max_side: int
                ) -> tuple[np.ndarray, float]:
        """This shrunken panel stage, its marker mask downscaled along (markers blur away when shrunk, so the
        full-size mask decides) and inpainted with the background."""
        ph, pw = panel.shape[0], panel.shape[1]
        k = min(1.0, max_side / max(pw, ph))
        size = (max(1, round(pw * k)), max(1, round(ph * k)))
        grey = np.asarray(Image.fromarray(panel).resize(size, Image.BILINEAR).convert("L"), dtype=np.float32)
        small_mask = np.asarray(Image.fromarray(mask).resize(size, Image.NEAREST), dtype=bool)
        grey[small_mask] = bgL
        return grey, k

    def locate(self, img: Image.Image) -> Here | None:
        """The map the box shows, with the yellow dot's spot (fractions of the KB picture); None when the box shows
        no known minimap confidently."""
        if img is None or min(img.size) < _MIN_SIDE:
            return None
        arr = np.asarray(img.convert("RGB"))
        panel, bg, clean = self._panel_view(arr, self._locked is None)
        if self._locked is not None:
            hit = self._recheck(clean, bg)
            if hit is None:
                panel, bg, clean = self._panel_view(arr, True)
                hit = self._recheck(clean, bg)
            if hit is not None:
                return self._remember(hit, panel)
        pw, ph = panel.shape[1], panel.shape[0]
        bgL = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        mask = _marker_mask(panel)
        small, k = self._shrunk(panel, mask, bgL, _COV_MAX)
        tiny, kt = self._shrunk(panel, mask, bgL, _TINY_MAX)
        med, _ = self._shrunk(panel, mask, bgL, _RERANK_MAX)
        hit = self._search(clean, med, small, tiny, bg, k, kt, pw, ph)
        if hit is None:
            return None
        return self._remember(hit, panel)

    # ------------------------------------------------------------- pictures

    def _pic(self, mid: str) -> tuple[Image.Image, np.ndarray, Image.Image] | None:
        """The KB picture, loaded once no matter how many scans compare against it: its grey shape on black (for
        the KB-against-KB similarity), its colours (for the yellow-art veto), and its alpha (so each scan can
        composite it onto that panel's own background colour: transparent means the game's backdrop there)."""
        if mid not in self._pics:
            entry = None
            p = self._graph.minimap(mid)
            try:
                art = Image.open(p).convert("RGBA") if p is not None else None
            except (OSError, ValueError):
                art = None
            if art is not None:
                flat = Image.alpha_composite(Image.new("RGBA", art.size, (0, 0, 0, 255)), art).convert("RGB")
                a = art.split()[3]
                entry = (flat.convert("L"), np.asarray(art.convert("RGB")), a)
            self._pics[mid] = entry
        return self._pics[mid]

    def _comp(self, mid: str, scale: float, bg: tuple[int, int, int], k: float = 1.0) -> np.ndarray | None:
        """This candidate's grey shape at exactly this scale, composited onto the panel's background colour (as the
        game draws it): transparent KB areas read as backdrop, matching the inpainted panel. Full-size shapes are
        remembered per background; shrunken ones are cheap enough to rebuild."""
        pic = self._pic(mid)
        if pic is None:
            return None
        base, _, alpha = pic
        sw, sh = max(1, round(base.width * scale * k)), max(1, round(base.height * scale * k))
        if k == 1.0:
            key = (mid, bg[0], bg[1] * 256 + bg[2], sw * 4096 + sh)
            got = self._comp_cache.get(key)
            if got is not None:
                return got
            if len(self._comp_cache) > 2048:
                self._comp_cache.clear()
        else:
            key = None
        bgL = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        g = np.asarray(base.resize((sw, sh), Image.BILINEAR), dtype=np.float32)
        a = np.asarray(alpha.resize((sw, sh), Image.BILINEAR), dtype=np.float32) / 255.0
        out = g + (1.0 - a) * bgL
        if key is not None:
            self._comp_cache[key] = out
        return out

    def _neediness(self, mid: str) -> float:
        """How much this picture loses matching itself 3% off (0-1): thin features crater past a few percent while
        chunky ones shrug it off, so needy maps get dense trial scales and the rest stay cheap. Calibrated once per
        picture on disk, on demand (the module cache shares it between readers)."""
        if mid not in self._need:
            self._need[mid] = _calibrated(self, mid)
        return self._need[mid]

    def _picture_sim(self, a: str, b: str) -> float:
        """How alike two KB pictures are (0-1): the smaller matched onto the bigger one's size, once, remembered.
        Only runs when the margin already failed, so look-alike families cost one comparison per pair."""
        key = (a, b) if a <= b else (b, a)
        if key not in self._sim:
            sim = 0.0
            pa, pb = self._pic(a), self._pic(b)
            if pa is not None and pb is not None:
                la, lb = pa[0], pb[0]
                big, small = (la, lb) if la.width * la.height >= lb.width * lb.height else (lb, la)
                fit = small.resize(big.size, Image.BILINEAR)
                found = _ncc(np.asarray(big, dtype=np.float32), np.asarray(fit, dtype=np.float32))
                if found is not None:
                    sim = max(0.0, found[0])
            self._sim[key] = sim
        return self._sim[key]

    # ---------------------------------------------------------------- search

    def _match(self, img: np.ndarray, mid: str, scale: float, bg: tuple[int, int, int], k: float = 1.0,
               win: tuple[int, int, int, int] | None = None) -> _Hit | None:
        """This candidate at exactly this scale on this panel (full-size for the cheap recheck and the refinement,
        shrunken for the pre-rank, coarse and re-rank stages, whose offsets are thrown away): None when it cannot
        be a view of the panel. A picture that fits slides over the panel; a bigger one (a cropped live view) has
        the panel slide over it instead — either way the score is the NCC over their overlap, blended towards how
        much of the panel it explains (a small patch matching a corner at 0.8 is a fluke; the true view covers the
        panel and keeps its score)."""
        pic = self._pic(mid)
        if pic is None:
            return None
        art, _, _ = pic
        tw, th = art.width * scale * k, art.height * scale * k
        H, W = img.shape
        if min(tw, th) < _MIN_SIDE or tw > _OV * W or th > _OV * H:
            return None
        sw, sh = max(1, round(tw)), max(1, round(th))
        if min(sw, sh) < _MIN_SIDE:
            return None
        if sw <= W and sh <= H:
            if sw * sh < _MIN_AREA * W * H:
                return None
            templ = self._comp(mid, scale, bg, k)
            if templ is None:
                return None
            if win is not None:   # the placement stays in this box: crop the panel to the box plus one template
                cx0, cx1 = max(0, win[0]), min(W - sw, win[1])   # (same argmax, a fraction of the grid)
                cy0, cy1 = max(0, win[2]), min(H - sh, win[3])
                if cx1 < cx0 or cy1 < cy0:
                    return None
                found = _ncc(img[cy0:cy1 + sh, cx0:cx1 + sw], templ)
                if found is not None:
                    found = (found[0], found[1] + cx0, found[2] + cy0)
            else:
                found = _ncc(img, templ)
            cover = sw * sh / (W * H)
        else:
            if min(W, sw) * min(H, sh) < _OVERLAP_MIN * min(W * H, sw * sh):
                return None
            templ = self._comp(mid, scale, bg, k)
            if templ is None:
                return None
            found = _ncc_over(img, templ, win)
            if found is not None:
                cover = ((min(W, found[1] + sw) - max(0, found[1]))
                         * (min(H, found[2] + sh) - max(0, found[2])) / (W * H))
            else:
                cover = 0.0
        if found is None:
            return None
        return _Hit(mid, found[0] * (0.5 + 0.5 * min(1.0, cover)), scale, found[1], found[2])

    def _recheck(self, panel: np.ndarray, bg: tuple[int, int, int]) -> _Hit | None:
        """The remembered map at its scale first; then the maps next door (the player walked through a portal) at
        the same scale. A scrolled view just moves the offset, which the correlation re-finds. The remembered map
        verifies lower than a neighbour (a cropped live view only ever scores ~0.7): but a look-alike neighbour
        can fluke that too, so below 0.8 the lock must also beat every neighbour by the margin, and a neighbour
        on its own never suffices below 0.8. None when nothing fits: something else is on screen, search
        everything."""
        mid, scale, _, _ = self._locked
        hit = self._match(panel, mid, scale, bg)
        if hit is not None and hit.score >= 0.8:   # a clean full view re-verifies on one comparison
            return hit
        best: _Hit | None = None
        seen = {mid}
        for leg in self._graph.edges.get(mid, ()):
            if leg.to in seen:
                continue
            seen.add(leg.to)
            near = self._match(panel, leg.to, scale, bg)
            if near is not None and (best is None or near.score > best.score):
                best = near
        if hit is not None and hit.score >= VERIFY_THRESHOLD \
                and (hit.score >= 0.8 or best is None or hit.score - best.score >= MATCH_MARGIN):
            return hit
        if best is not None and best.score >= 0.8:
            return best
        return None

    def _search(self, full: np.ndarray, med: np.ndarray, small: np.ndarray, tiny: np.ndarray,
                bg: tuple[int, int, int], k: float, kt: float, W: int, H: int) -> _Hit | None:
        """Every candidate on the tiny panel first; the best few dozen on the shrunken panel, each from its largest
        scale downwards; the best two dozen of those re-ranked bigger (but not full-size: only their scores and
        scales go on), the six best of those refined around it, where the call is made."""
        early = self._sweep(tiny, bg, kt, W, H, True)
        early.sort(key=lambda h: h.score, reverse=True)
        keep = {h.mid for h in early[:_TINY_TOP]}
        contenders = [h for h in self._top(small, k, W, H, 24, bg, keep) ]
        rk = med.shape[1] / W
        f = rk / k    # the coarse offsets count in shrunken pixels: this wide a box around them, in med pixels,
        hw = 48      # holds the re-ranked placement (a fluke peak can still sit outside it: then the whole panel)
        ranked = []
        for c in contenders[:12]:
            win = (round(c.x * f - hw), round(c.x * f + hw), round(c.y * f - hw), round(c.y * f + hw))
            hit = self._match(med, c.mid, c.scale, bg, rk, win=win)
            if hit is None:
                hit = self._match(med, c.mid, c.scale, bg, rk)
            if hit is not None:
                ranked.append(hit)
        ranked.sort(key=lambda h: h.score, reverse=True)
        short = ranked[:6]      # ... plus ultra-steep maps, which the single med scale can bury: their true scale
        short += [c for c in ranked[6:12] if self._neediness(c.mid) >= _ULTRA_DROP][:8 - len(short)]
        scored, bar = [], float("-inf")
        for c in short:
            hit, bar = self._refine(full, bg, c, rk, bar)
            if hit is not None:
                scored.append(hit)
        scored.sort(key=lambda h: h.score, reverse=True)
        if not scored or scored[0].score < MATCH_THRESHOLD:
            return None
        if len(scored) > 1 and scored[0].score - scored[1].score < MATCH_MARGIN:
            if self._picture_sim(scored[0].mid, scored[1].mid) < _GROUP_SIM:
                return None
            near = [h for h in scored if scored[0].score - h.score < MATCH_MARGIN
                    and self._picture_sim(scored[0].mid, h.mid) >= _GROUP_SIM]
            return self._group_hit(near)
        return scored[0]

    def _group_hit(self, near: list[_Hit]) -> _Hit:
        """One winner from look-alike pictures: the locked map for continuity when it is one of them, else a map
        next door to the lock (the player walks through portals), else the best match itself (on its own panel the
        true picture still outscores its siblings, however narrowly), else town-first for determinism."""
        by_mid = {h.mid: h for h in near}
        if self._locked is not None:
            if self._locked[0] in by_mid:
                return by_mid[self._locked[0]]
            doors = {leg.to for leg in self._graph.edges.get(self._locked[0], ())} & set(by_mid)
            if doors:
                return by_mid[max(doors, key=lambda m: by_mid[m].score)]
        scored = sorted(near, key=lambda h: h.score, reverse=True)
        if scored[0].score > scored[1].score:
            return scored[0]
        return by_mid[min(by_mid, key=lambda m: (not self._graph.maps[m].town,
                                                 -len(self._graph.edges.get(m, ())), m))]

    def _scales(self, mid: str, W: int, H: int, sparse: bool) -> list[float]:
        """The trial scales for this candidate on this panel. A full view sits at its largest fitting scale (the
        panel is cut out tight, so no loose tail below: the dense trials crowd at fitting for thin-featured
        pictures, which crater a few percent off, and stay sparse for the rest). A cropped view sits above fitting
        (the panel is a sub-window of the scaled picture), so a sparse ladder runs from fitting up to the overhang
        cap for every map: the re-rank and the refinement still lift the true scale out. The tiny pre-rank always
        runs sparse."""
        pic = self._pic(mid)
        if pic is None:
            return []
        art, _, _ = pic
        fit = min(3.0, W / art.width, H / art.height) * 0.999
        cap = min(3.0, _OV * W / art.width, _OV * H / art.height) * 0.999
        if cap < 0.5:
            return []
        out = []
        dense = False
        if fit >= 0.5:
            if sparse:
                out.append(fit)   # the pre-rank only needs truth on the board: tight panels put full views at
                s = fit * 1.5     # fitting, and the ladder below still lands cropped views within the tiny peak's
                while s <= cap:   # broad plateau (Perion reads 0.55 a fifth off, twice the cutoff)
                    out.append(s)
                    s *= 1.5
                return out
            else:
                need = self._neediness(mid)
                if need >= _HYPER_DROP:
                    step, floor, dense = 1.004, max(0.5, fit * 0.85), True
                elif need >= _NEEDY_DROP:
                    step, floor, dense = 1.06, max(0.5, fit * 0.85), True
                else:
                    out.append(fit)   # tolerant peaks are broad: fitting plus the satellites below is plenty
                if dense:
                    s = fit
                    while s >= floor:
                        out.append(s)
                        s /= step
            if out and not dense:
                for s in (out[0] / 1.12, out[0] * 1.12):
                    if 0.5 <= s <= 3.0:
                        out.append(s)
        s = max(fit, 0.5) * 1.5   # ... and sparse above fitting, for cropped views (a 1.5 ladder still lands
        while s <= cap:           # within the refinement's pull at tiny size: Perion reads 0.55 a fifth off)
            out.append(s)
            s *= 1.5
        return out

    def _best_at(self, img: np.ndarray, mid: str, bg: tuple[int, int, int], k: float, W: int, H: int,
                 sparse: bool) -> _Hit | None:
        """This candidate's best placing on this shrunken panel over its trial scales (offsets thrown away)."""
        best: _Hit | None = None
        for s in self._scales(mid, W, H, sparse):
            hit = self._match(img, mid, s, bg, k)
            if hit is not None and (best is None or hit.score > best.score):
                best = hit
        return best

    def _sweep(self, img: np.ndarray, bg: tuple[int, int, int], k: float, W: int, H: int,
               sparse: bool) -> list[_Hit]:
        """Each candidate's best placing on this shrunken panel: one thread per map, merged in candidate order."""
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            jobs = [ex.submit(self._best_at, img, mid, bg, k, W, H, sparse) for mid in self._candidates]
            return [hit for job in jobs if (hit := job.result()) is not None]

    def _top(self, small: np.ndarray, k: float, W: int, H: int, n: int, bg: tuple[int, int, int],
             keep: set[str] | None = None) -> list[_Hit]:
        """Each shortlisted candidate's best placing on the shrunken panel; the n highest, each a different map."""
        mids = [mid for mid in self._candidates if keep is None or mid in keep]
        with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
            jobs = [(mid, ex.submit(self._best_at, small, mid, bg, k, W, H, False)) for mid in mids]
            per_mid = {mid: hit for mid, job in jobs if (hit := job.result()) is not None}
        return sorted(per_mid.values(), key=lambda h: h.score, reverse=True)[:n]

    def _refine(self, full: np.ndarray, bg: tuple[int, int, int], coarse: _Hit, rk: float,
                bar: float) -> tuple[_Hit | None, float]:
        """The coarse trials under the microscope, at full resolution. Most pictures get a sparse look around the
        coarse scale; steep ones a denser one; the steepest (whose match lives in a sub-percent peak) a narrow,
        dense comb around it — the coarse grid already lands within ~3%. A cropped view always gets the denser
        look too: hanging off the panel narrows the peak past what the sparse comb can rank (Perion drops 0.2 a
        mere 3% off). The placement only moves with the scale, so the offsets stay boxed to a window around the
        re-ranked one. Trials run centre-out (the coarse scale first, where truth sits for tight panels), and a
        smooth-peaked map far below the best refined so far stops early: losers refine flat, so their remaining
        trials cannot jump the gap (steep maps, whose combs spike, never stop). Returns the best with the bar."""
        need = self._neediness(coarse.mid)
        pic = self._pic(coarse.mid)
        over = pic is not None and (pic[0].width * coarse.scale > full.shape[1]
                                    or pic[0].height * coarse.scale > full.shape[0])
        if need >= _ULTRA_DROP:
            n, lo, span = 13, 0.97, 0.06
        elif need >= _NEEDY_DROP or over:
            n, lo, span = 9, 0.86, 0.28
        else:
            n, lo, span = 5, 0.86, 0.28
        hw = 64    # ... the full-size placement stays this near the re-ranked one (in panel pixels each way):
        cx, cy = coarse.x / rk, coarse.y / rk   # the re-rank's offsets count in its own shrunken pixels
        win = (round(cx - hw), round(cx + hw), round(cy - hw), round(cy + hw))
        order = sorted(range(n), key=lambda i: (abs(i - (n - 1) / 2), i))
        best: _Hit | None = None
        for t, i in enumerate(order):
            s = min(3.0, max(0.5, coarse.scale * (lo + i * (span / (n - 1)))))
            hit = self._match(full, coarse.mid, s, bg, win=win)
            if hit is None:   # the placement sat outside the window after all: the whole panel, just this once
                hit = self._match(full, coarse.mid, s, bg)
            if hit is not None:
                if best is None or hit.score > best.score:
                    best = hit
                bar = max(bar, hit.score)
            if need < _ULTRA_DROP and t >= 3 and best is not None and best.score < bar - 0.3:
                break
        return best, bar

    def _remember(self, hit: _Hit, arr: np.ndarray) -> Here:
        self._locked = (hit.mid, hit.scale, hit.x, hit.y)
        return Here(hit.mid, self._dot(arr, hit))

    # ------------------------------------------------------------ player dot

    def _dot(self, arr: np.ndarray, hit: _Hit) -> tuple[float, float] | None:
        """The yellow player dot's centre as fractions of the KB picture, None when not seen. Only the matched
        rectangle is looked at (the title text is yellowish too). A blob the art cannot explain is the player; one
        the art half-covers is art — unless it is big and far brighter (the game's dot painted over yellow lava,
        which a small bright speck can never be). Anything dimmer or ambiguous is left unseen. With a cropped view
        the matched rectangle hangs off the panel: the fractions still count over the whole picture."""
        pic = self._pic(hit.mid)
        if pic is None:
            return None
        _, art, _ = pic
        H, W, _ = arr.shape
        sw = max(1, round(art.shape[1] * hit.scale))
        sh = max(1, round(art.shape[0] * hit.scale))
        region = arr[max(hit.y, 0):min(hit.y + sh, H), max(hit.x, 0):min(hit.x + sw, W)]
        if region.size == 0:
            return None
        rs = np.asarray(Image.fromarray(art).resize((region.shape[1], region.shape[0]), Image.BILINEAR))
        yellow = (rs[:, :, 0] >= _ART_YELLOW) & (rs[:, :, 1] >= _ART_YELLOW) & (rs[:, :, 2] <= _ART_YELLOW)
        mask = (region[:, :, 0] >= _DOT_R) & (region[:, :, 1] >= _DOT_G) & (region[:, :, 2] <= _DOT_B)
        ox, oy = max(hit.x, 0), max(hit.y, 0)
        for one in sorted(_blobs(mask), key=len, reverse=True):
            if not _DOT_MIN <= len(one) <= _DOT_MAX:
                continue
            ys = np.fromiter((y for y, _ in one), dtype=np.intp, count=len(one))
            xs = np.fromiter((x for _, x in one), dtype=np.intp, count=len(one))
            frac, bright = _art_blob(yellow, rs, region, ys, xs)
            if frac >= 0.5 and (len(one) < _DOT_BIG or bright < _BRIGHT_MIN):
                continue
            return (min(max(float(xs.mean() + ox - hit.x) / sw, 0.0), 1.0),
                    min(max(float(ys.mean() + oy - hit.y) / sh, 0.0), 1.0))
        return None
