"""Which map the player is on, from a picture of the game's minimap (the box the player drew around it), and where
on it they stand (the yellow dot). The map is read from the minimap's title: RapidOCR reads the header's street
and map-name lines, the name resolves against the KB's map names (exact, then OCR-tolerant fuzzy, the street
breaking ties), and only then is the KB's minimap picture (img/map/<id>.png: the game's own minimap art) of that
one map aligned onto the content panel to place the yellow dot. No Qt: runs on a worker thread."""
from __future__ import annotations

from dataclasses import dataclass
import difflib
import logging
import re
import threading
from typing import TYPE_CHECKING
import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from . import routes

log = logging.getLogger("maplehelper")


@dataclass(frozen=True)
class Here:
    """Where the player is: a routes.Graph map id, and the yellow dot's spot on that map's minimap picture as
    fractions of its size (as routes.Leg.spot), None when the dot wasn't seen."""
    map: str
    spot: tuple[float, float] | None = None


#: The one known map's picture only counts as aligned when it matches this well (normalized cross-correlation,
#: 0-1): below it the dot has no placing, and the map is still returned from its title text (Here with spot None).
_ALIGN_MIN = 0.5
#: The picture tie-break between same-name maps only counts with this clear a margin: closer, and the maps are
#: indistinguishable art (or none of them fits), so the deterministic pick below decides instead of a coin flip.
_TIE_MARGIN = 0.05
#: A read header line only names a map this alike (difflib ratio, best of plain and OCR-confusion-folded): below it
#: the box's text is something else (a mistyped chat line behind the window, OCR garbage on noise), and no map is
#: named at all — a wrong map is worse than none. 'Henesys Markel' still clears it at 0.93.
_NAME_MIN = 0.8
#: ... with this clear a margin over the next distinct name: closer, and the line could be either ('Rocky Road'
#: ties I/II/III at a zero margin), so no map is named.
_NAME_MARGIN = 0.07
#: A read street line only backs a candidate with a street this alike (same ratio): it prefers, never vetoes — a
#: misread street must not lose a well-read map name. 'Victoría Road' (tight-crop accent) still clears it.
_STREET_MIN = 0.85
_CHROME = ("minimap", "world")   # the window furniture ('MINI MAP' in any case/spacing, 'WORLD'): never a map name
_TOP_BAND = 0.12    # ... nor is any line up here (the title bar): tight header crops misread WORLD as 'RLO'/'ORLD',
                    # which no text match would catch, but nothing below the bar ever sits this high (live headers
                    # centre on ~17-35% of the box height)
_HEADER_PAD = 70    # the learned header crop keeps this many rows below the header's last line: a tight crop reads
                    # worse, not faster (measured: a 100-row crop took 0.34 s and lost WORLD, a 160-row one 0.20 s),
                    # so the crop stays generous and the whole box is the fallback when it yields no name
_COV_MAX = 160   # the single-map alignment tries its trial scales shrunken (longest side); the best is refined
                # full-size, where the dot is placed
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
_BG_LIGHT = 120   # ... unless that median reads this bright or brighter while this share of the panel is darker:
_BG_DARK_FRAC = 0.15    # then the backdrop is a saturated dark (Henesys Market's navy (19, 38, 76) fails the flat
                        # test at saturation 57, and the light frame rows and stall flats win the median) — the
                        # median of the dark pixels instead
_DOT_R, _DOT_G, _DOT_B = 200, 190, 130  # a yellow blob: strong red and green, little blue ...
_DOT_MIN, _DOT_MAX = 4, 800             # ... of a dot's size: smaller is a stray pixel, bigger a piece of the art
_DOT_BIG = 20   # ... but only this big a blob may be a dot painted over yellow art (a bright speck stays smaller)
_ART_YELLOW = 150   # KB art this yellow, resampled to the matched size, is what a blob is checked against
_VETO_SHIFT = 0.06  # ... slid this far looking for it: the refined scale can still be ~6% off, so a blob never sits
                    # exactly on its art pixels (half the blob must still land on yellow to call it art)
_BRIGHT_MIN = 40    # ... unless the blob outshines the art by this much (R+G): the game's dot painted over yellow
                    # lava reads ~510 against ~420, while resampling noise stays within ~15


# ------------------------------------------------------- header names


def _plain(text: str) -> str:
    """A name with its whitespace settled, for the exact match ('3-Way   Road-Split' is '3-Way Road-Split')."""
    return re.sub(r"\s+", " ", text.strip()).lower()


def _folded(text: str) -> str:
    """A name with OCR's usual confusions flattened, for the fuzzy match: O/0, l/I/1, accents, and dropped
    spaces and hyphens ('PerI0n' reads 'perion', '3-Way RoadSplit' reads 'wayroadsplit')."""
    t = text.lower()
    for a, b in (("0", "o"), ("1", "l"), ("|", "l"), ("5", "s")):
        t = t.replace(a, b)
    t = t.replace("rn", "m")
    return re.sub(r"[^a-z0-9]", "", t)


def _ratio(a: str, b: str) -> float:
    """How alike two header lines are (difflib, 0-1), best of plain and confusion-folded."""
    return max(difflib.SequenceMatcher(None, _plain(a), _plain(b)).ratio(),
               difflib.SequenceMatcher(None, _folded(a), _folded(b)).ratio())


def _deterministic(graph, mids: list[str]) -> str:
    """One map id from several sharing a name, without looking at any picture: the town first, then the one with
    the most ways out, then the id — the same order routes.exact picks."""
    return min(mids, key=lambda m: (not graph.maps[m].town, -len(graph.edges.get(m, ())), m))


def _candidates_for(graph, map_text: str, street_text: str | None = None) -> list[str]:
    """The map ids sharing the accepted name for a read header line (street-narrowed when several share it), []
    when the line names none confidently: exact first, else the OCR-tolerant fuzzy best with its strict score and
    clear margin. Shared with the locator, which adds the picture tie-break on top."""
    if not map_text or not map_text.strip():
        return []
    exact = [mid for mid, m in graph.maps.items() if _plain(m.name) == _plain(map_text)]
    if exact:
        cands = exact
    else:
        best: dict[str, float] = {}
        for m in graph.maps.values():
            s = _ratio(map_text, m.name)
            if m.name not in best or s > best[m.name]:
                best[m.name] = s
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        if not ranked or ranked[0][1] < _NAME_MIN:
            return []
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < _NAME_MARGIN:
            return []
        cands = [mid for mid, m in graph.maps.items() if m.name == ranked[0][0]]
    if len(cands) > 1 and street_text and street_text.strip():
        on_street = [mid for mid in cands if _street_ok(street_text, graph.maps[mid].street)]
        if on_street:
            cands = on_street
    return cands


def resolve_name(graph, map_text: str, street_text: str | None = None, prev: str | None = None,
                 scores: dict[str, float] | None = None) -> str | None:
    """The graph map id a read header line names, None when it names none confidently. The accepted name's maps
    break ties by the previous located map or one of its graph neighbours, then — when the caller passes
    picture-alignment scores for the tied maps — the clearly best-fitting picture, else the deterministic pick.
    Pure: no OCR, no pictures, so tests call it directly."""
    cands = _candidates_for(graph, map_text, street_text)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if prev in cands:
        return prev
    if prev is not None:
        doors = {leg.to for leg in graph.edges.get(prev, ())} & set(cands)
        if doors:
            return _deterministic(graph, sorted(doors))
    if scores:
        fitted = sorted(((scores.get(mid, 0.0), mid) for mid in cands), reverse=True)
        if fitted[0][0] >= _ALIGN_MIN and (len(fitted) < 2 or fitted[0][0] - fitted[1][0] >= _TIE_MARGIN):
            return fitted[0][1]
    return _deterministic(graph, cands)


def _street_ok(read: str, street: str) -> bool:
    """Whether a read street line is this KB street (exact settled, or confusion-folded, or plainly close)."""
    return bool(street) and (_plain(read) == _plain(street) or _folded(read) == _folded(street)
                             or _ratio(read, street) >= _STREET_MIN)


def is_chrome(text: str, y_center: float, height: int) -> bool:
    """Whether an OCR line is the window furniture, not the header: 'MINI MAP' in any case/spacing, 'WORLD', or
    anything up in the title bar (tight crops misread WORLD past any text match, but nothing below the bar ever
    sits that high)."""
    return re.sub(r"\s+", "", text).lower() in _CHROME or y_center < _TOP_BAND * height


def collapsed_title(text: str, streets) -> tuple[str, str] | None:
    """A collapsed minimap's one-line title, (map, street): inside a building the game folds the window to its title
    bar, 'Victoria Road : Warriors' Sanctuary' (no KB map name has a colon). The colon read as nothing, the line
    still starts with a known street ('Victoria Road Warriors'Sanctuary'). None for any other line."""
    t = re.sub(r"\s+", " ", re.sub(r"(?i)\bworld\s*$", "", text)).strip()
    street, sep, name = t.partition(":") if ":" in t else t.partition("\uff1a")
    if sep and street.strip() and name.strip():
        return name.strip(), street.strip()
    low = t.lower()
    for s in sorted({s for s in streets if s}, key=len, reverse=True):
        if low.startswith(s.lower() + " ") and t[len(s):].strip():
            return t[len(s):].strip(), s
    return None


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


def _ncc_over(image: np.ndarray, templ: np.ndarray) -> tuple[float, int, int] | None:
    """Where the template sits on the image when it may hang off any edge (a cropped view: the scaled picture is
    bigger than the panel, so the panel is a sub-window of it). The best score with the template's top-left in
    image pixels (negative when cut off); None when no placement overlaps enough to be a view."""
    H, W = image.shape
    h, w = templ.shape
    oy_all = np.arange(-(h - 1), H)      # every placement, including overhangs ...
    ox_all = np.arange(-(w - 1), W)
    oh_all = np.minimum(H, oy_all + h) - np.maximum(0, oy_all)
    ow_all = np.minimum(W, ox_all + w) - np.maximum(0, ox_all)
    need = _OVERLAP_MIN * min(H * W, h * w)
    ok_y = (oh_all >= _MIN_SIDE) & (oh_all * min(W, w) >= need)
    ok_x = (ow_all >= _MIN_SIDE) & (ow_all * min(H, h) >= need)
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
    """Which map a minimap box shows, read from the minimap's title: RapidOCR (created lazily, once, on first use)
    reads the header's street and map-name lines — the map line is the last header line above the map panel, the
    street the one over it — and the name resolves against the KB (exact, then OCR-tolerant fuzzy; the street, the
    previous map and its neighbours break ties between maps sharing a name). No recognizable name is None: the box
    is never guessed from pictures alone. The one known map's KB picture is then aligned onto the content panel
    (overlay markers masked out, the picture composited onto the panel's own background colour, the panel slid over
    it for cropped live views) to place the yellow dot; a map whose picture won't align still returns with a None
    spot. The header rows learned from a read crop the next reads (the whole box is the fallback), the last header
    text reuses its map, and the aligned (map, scale, offset) is just rechecked, so a steady read costs the OCR plus
    a comparison or two."""

    def __init__(self, graph: routes.Graph) -> None:
        self._graph = graph
        self._pics: dict[str, tuple[Image.Image, np.ndarray, Image.Image] | None] = {}
        self._locked: tuple[str, float, int, int] | None = None
        self._prev: str | None = None
        self._panel: tuple[int, int, tuple[int, int, int, int]] | None = None
        self._comp_cache: dict[tuple[str, int, int, int], np.ndarray] = {}
        self._ocr: object | None = None
        self._ocr_lock = threading.Lock()
        self._ocr_failed = False
        self._header_rows: int | None = None
        self._last_key: tuple | None = None
        self._last_mid: str | None = None
        self._collapsed = False         # the last read header was a folded window's one-line title (no map shown)

    def reset(self) -> None:
        """Forget the remembered map, scale and header: the next locate() reads the whole box again."""
        self._locked = None
        self._prev = None
        self._header_rows = None
        self._last_key = None
        self._last_mid = None

    def _panel_view(self, arr: np.ndarray, fresh: bool) -> tuple[np.ndarray, tuple[int, int, int], np.ndarray]:
        """The content panel with its background colour and its marker-inpainted grey shape: the correlation works
        on the inpainted shape (markers would fight the art), the dot finder on the raw panel. The detected frame
        is reused while the box keeps its size; a fresh detection runs when it changes and when alignment fails."""
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
        if 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2] >= _BG_LIGHT:
            dark = flat[(0.299 * flat[:, 0] + 0.587 * flat[:, 1] + 0.114 * flat[:, 2]) < _BG_LIGHT]
            if len(dark) >= _BG_DARK_FRAC * len(flat):
                bg = np.median(dark, axis=0)
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
        no recognizable header text. The map always comes from the title: a box whose text won't resolve is never
        guessed from pictures. A known map whose picture won't align (or which has none) still returns, with a
        None spot."""
        if img is None or min(img.size) < _MIN_SIDE:
            return None
        arr = np.asarray(img.convert("RGB"))
        panel, bg, clean = self._panel_view(arr, self._locked is None)
        panel_y0 = self._panel[2][1] if self._panel is not None else 0
        text = self._read_text(arr, panel_y0)
        if text is None:
            return None
        mid = self._resolve_text(text[0], text[1], panel, bg)
        if mid is None:
            return None
        self._prev = mid
        if self._collapsed or self._pic(mid) is None:
            return Here(mid, None)          # a folded window shows no map to place the dot on
        hit = self._align(panel, clean, bg, mid)
        if hit is None:
            panel, bg, clean = self._panel_view(arr, True)
            hit = self._align(panel, clean, bg, mid)
        if hit is None:
            return Here(mid, None)
        return self._remember(hit, panel)

    # ------------------------------------------------------------------ OCR

    def _engine(self):
        """The RapidOCR reader, made once no matter how many scans read through it (0.3 s to load); None when it
        won't load, logged once — a read then has no answer. Imported late so the module stays light without it."""
        if self._ocr is None and not self._ocr_failed:
            with self._ocr_lock:
                if self._ocr is None and not self._ocr_failed:
                    try:
                        from rapidocr import RapidOCR
                        self._ocr = RapidOCR()
                    except Exception as e:  # noqa: BLE001 - no reader, no answer, never a crash
                        self._ocr_failed = True
                        log.warning("minimap OCR unavailable: %s", e)
        return self._ocr

    def _ocr_rows(self, view: np.ndarray) -> list[tuple[str, float, float]] | None:
        """The read text rows (text, y centre, y bottom, in view pixels, top-down): None when the reader is down or
        a read fails, [] when it sees nothing."""
        eng = self._engine()
        if eng is None:
            return None
        try:
            with self._ocr_lock:
                found = eng(view)
        except Exception:
            log.debug("minimap OCR read failed", exc_info=True)
            return None
        if found is None or not found.txts:
            return []
        rows = []
        for text, box in zip(found.txts, np.asarray(found.boxes).reshape(len(found.txts), -1, 2)):
            ys = box[:, 1]
            rows.append((text, float(ys.mean()), float(ys.max())))
        rows.sort(key=lambda r: r[1])
        return rows

    def _header(self, rows: list[tuple[str, float, float]], height: int, panel_y0: int
                ) -> tuple[tuple[str, str | None], float | None] | None:
        """The header's (map line, street line) from read rows. A collapsed window first: its one-line 'Street : Map'
        title sits up in the title bar (which the furniture rule drops; an unfolded window has only MINI MAP and
        WORLD there), and under a folded window the game itself shows, whose names and bubbles must not pass for a
        header. Otherwise the window furniture dropped, anything at or below the map panel dropped, and of what stays
        the map line is the last one down, the street the one over it. Also the header's bottom row, which the next
        reads crop to (None for a folded title: unfolded, the header sits much lower). None when no header line
        stays. Sets self._collapsed for the read."""
        streets = [m.street for m in self._graph.maps.values()]
        for t, yc, _b in rows:
            got = collapsed_title(t, streets) if yc < _TOP_BAND * height else None
            if got is not None and _candidates_for(self._graph, *got):
                self._collapsed = True
                return got, None
        self._collapsed = False
        kept = [(t, b) for t, yc, b in rows
                if t.strip() and not is_chrome(t, yc, height) and (panel_y0 < 40 or b <= panel_y0 + 8)]
        if not kept:
            return None
        street = kept[-2][0] if len(kept) > 1 else None
        return (kept[-1][0], street), max(b for _, b in kept)

    def _read_text(self, arr: np.ndarray, panel_y0: int) -> tuple[str, str | None] | None:
        """The header's (map line, street line): the learned header crop first (a small strip), the whole box when
        that yields no header (a redrawn box heals the crop)."""
        H = arr.shape[0]
        if self._header_rows is not None:
            rows = self._ocr_rows(arr[:max(_MIN_SIDE, min(H, self._header_rows))])
            if rows is not None:
                got = self._header(rows, H, panel_y0)
                if got is not None:
                    return got[0]
        rows = self._ocr_rows(arr)
        if rows is None:
            return None
        got = self._header(rows, H, panel_y0)
        if got is None:
            return None
        if got[1] is not None:
            self._header_rows = min(H, max(int(got[1]) + _HEADER_PAD, 1))
        return got[0]

    def _resolve_text(self, map_text: str, street: str | None, panel: np.ndarray, bg: tuple[int, int, int]
                      ) -> str | None:
        """The header's map id: the last header text reuses its map (resolving walks every map name), except across
        same-name ties, whose picture scores are re-read every time. The tie-break order lives in resolve_name;
        the locator only adds the picture scores for it."""
        key = (map_text, street or "", self._prev)
        cands = _candidates_for(self._graph, map_text, street)
        if len(cands) > 1:
            self._last_key, self._last_mid = key, resolve_name(
                self._graph, map_text, street, self._prev, self._tie_scores(panel, bg, cands))
            return self._last_mid
        if key != self._last_key:
            self._last_key = key
            self._last_mid = resolve_name(self._graph, map_text, street, self._prev)
        return self._last_mid

    # ------------------------------------------------------------- pictures

    def _pic(self, mid: str) -> tuple[Image.Image, np.ndarray, Image.Image] | None:
        """The KB picture, loaded once no matter how many scans compare against it: its grey shape on black (for
        the alignment), its colours (for the yellow-art veto), and its alpha (so each scan can composite it onto
        that panel's own background colour: transparent means the game's backdrop there)."""
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

    # --------------------------------------------------------------- alignment

    def _match(self, img: np.ndarray, mid: str, scale: float, bg: tuple[int, int, int], k: float = 1.0
               ) -> _Hit | None:
        """This map at exactly this scale on this panel (full-size for the recheck and the refinement, shrunken
        for the coarse trials): None when it cannot be a view of the panel. A picture that fits slides over the
        panel; a bigger one (a cropped live view) has the panel slide over it instead — either way the score is
        the NCC over their overlap, blended towards how much of the panel it explains (a small patch matching a
        corner at 0.8 is a fluke; the true view covers the panel and keeps its score)."""
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
        templ = self._comp(mid, scale, bg, k)
        if templ is None:
            return None
        if sw <= W and sh <= H:
            if sw * sh < _MIN_AREA * W * H:
                return None
            found = _ncc(img, templ)
            cover = sw * sh / (W * H)
        else:
            if min(W, sw) * min(H, sh) < _OVERLAP_MIN * min(W * H, sw * sh):
                return None
            found = _ncc_over(img, templ)
            if found is not None:
                cover = ((min(W, found[1] + sw) - max(0, found[1]))
                         * (min(H, found[2] + sh) - max(0, found[2])) / (W * H))
            else:
                cover = 0.0
        if found is None:
            return None
        return _Hit(mid, found[0] * (0.5 + 0.5 * min(1.0, cover)), scale, found[1], found[2])

    def _trial_scales(self, mid: str, W: int, H: int) -> list[float]:
        """The trial scales for this one map on this panel. A full view sits at its largest fitting scale; a
        cropped live view sits above fitting (the panel is a sub-window of the scaled picture), so a 1.25 ladder
        climbs from fitting to the overhang cap — some rung always lands within ~12% of the player's scale, inside
        the refinement's pull."""
        pic = self._pic(mid)
        if pic is None:
            return []
        art, _, _ = pic
        fit = min(3.0, W / art.width, H / art.height) * 0.999
        cap = min(3.0, _OV * W / art.width, _OV * H / art.height) * 0.999
        if cap < 0.5:
            return []
        out = []
        if fit >= 0.5:
            out.append(fit)
            for s in (fit / 1.12, fit * 1.12):
                if 0.5 <= s <= 3.0:
                    out.append(s)
        s = max(fit, 0.5) * 1.25
        while s <= cap:
            out.append(s)
            s *= 1.25
        return out

    def _coarse_best(self, small: np.ndarray, bg: tuple[int, int, int], k: float, W: int, H: int, mid: str
                     ) -> _Hit | None:
        """This map's best placing on the shrunken panel over its trial scales (offsets thrown away)."""
        best: _Hit | None = None
        for s in self._trial_scales(mid, W, H):
            hit = self._match(small, mid, s, bg, k)
            if hit is not None and (best is None or hit.score > best.score):
                best = hit
        return best

    def _align(self, panel: np.ndarray, clean: np.ndarray, bg: tuple[int, int, int], mid: str) -> _Hit | None:
        """This known map's placing on the panel, None when its picture won't fit (the map still stands: the
        caller returns it with a None spot). The locked scale rechecks first — a steady read costs one comparison;
        else the trial scales run shrunken (markers masked out, as in the panel view) and the best refines
        full-size around itself."""
        if self._locked is not None and self._locked[0] == mid:
            hit = self._match(clean, mid, self._locked[1], bg)
            if hit is not None and hit.score >= _ALIGN_MIN:
                return hit
        H, W = clean.shape
        bgL = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        small, k = self._shrunk(panel, _marker_mask(panel), bgL, _COV_MAX)
        coarse = self._coarse_best(small, bg, k, W, H, mid)
        if coarse is None:
            return None
        best: _Hit | None = None
        for i in range(7):    # ±12% around the coarse scale: the ladder lands inside it, the peak is broad
            s = min(3.0, max(0.5, coarse.scale * (0.88 + i * (0.24 / 6))))
            hit = self._match(clean, mid, s, bg)
            if hit is not None and (best is None or hit.score > best.score):
                best = hit
        if best is None or best.score < _ALIGN_MIN:
            return None
        return best

    def _tie_scores(self, panel: np.ndarray, bg: tuple[int, int, int], mids: list[str]) -> dict[str, float]:
        """Each tied same-name map's coarse fit on this panel, for resolve_name's picture tie-break."""
        H, W = panel.shape[0], panel.shape[1]
        bgL = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        small, k = self._shrunk(panel, _marker_mask(panel), bgL, _COV_MAX)
        out = {}
        for mid in mids:
            hit = self._coarse_best(small, bg, k, W, H, mid)
            out[mid] = hit.score if hit is not None else 0.0
        return out


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
