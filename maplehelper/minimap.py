"""Which map the player is on, from a picture of the game's minimap (the box the player drew around it), and where
on it they stand (the yellow dot). Compares the picture with the KB's minimap pictures (img/map/<id>.png: the
game's own minimap art). No Qt: runs on a worker thread."""
from __future__ import annotations

from dataclasses import dataclass
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
#: How well the remembered map must still fit for the cheap path (same for neighbours): the same map at the wrong
#: scale scores ~0.69 and look-alikes fluke ~0.5, while the same map at the same scale scores ~0.99, so anything
#: lower falls through to the full search (slower that once, but never a wrong map or a mis-scaled dot).
VERIFY_THRESHOLD = 0.8
_GRID = 1.25   # neighbouring trial scales differ by this much: each candidate is tried from its largest scale that
                # fits the box downwards, so some trial always lands within ~12% of the player's scale (and fits)
_NEEDY_DROP = 0.18     # a picture losing this much matching itself 3% off needs dense trials (thin features crater;
                       # tolerant maps lose under 0.14 there, needy ones over 0.24)
_ULTRA_DROP = 0.27     # ... and losing this much gets the narrow comb as well (tolerant maps lose under 0.14 here,
                       # while Snail and Hair at 0.27 already need it)
_HYPER_DROP = 0.38     # ... and losing this much needs sub-percent coarse trials too (its match lives in a ±0.2%
                       # wide peak no sparser grid can rank: only ~20 such pictures exist, so the comb stays ok)
_COARSE_MAX = 160   # the full search compares shrunken pictures (longest side); the best are rechecked full-size
_RERANK_MAX = 320   # ... re-ranked half-size-ish (their offsets are thrown away; only scores and scales go on)
_MIN_SIDE = 8       # a match smaller than this is noise, not a minimap ...
_MIN_AREA = 0.05    # ... and so is one this small next to the box (the box is drawn around the minimap, not the town)
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
    """One candidate placed on the box: which map, how well it fits, at which scale, and where (box pixels)."""
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
    score with its box offset. None when the template doesn't fit or is flat (a blank picture matches anywhere)."""
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
            base, _ = pic
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
    """Which map a minimap box shows, by sliding every KB minimap picture over it (normalized cross-correlation):
    the box holds more than the minimap (its frame, the title, margins) at an unknown scale, so each candidate is
    tried shrunken (densely for thin-featured pictures, which crater a few percent off), the best two dozen
    re-ranked bigger, the six best refined at full resolution, where the call is made (a tie between one drawing
    shared by several maps goes to the tie-break, any other tie to None). The winner (map, scale, offset) is then
    remembered and just rechecked, neighbours first, so the every-second scan costs a comparison or two."""

    def __init__(self, graph: routes.Graph) -> None:
        self._graph = graph
        self._candidates = [mid for mid in graph.maps if self._exists(graph.minimap(mid))]
        self._pics: dict[str, tuple[Image.Image, np.ndarray] | None] = {}
        self._locked: tuple[str, float, int, int] | None = None
        self._need: dict[str, float] = {}
        self._sim: dict[tuple[str, str], float] = {}

    @staticmethod
    def _exists(p) -> bool:
        return p is not None and Path(p).exists()

    def reset(self) -> None:
        """Forget the remembered map and scale: the next locate() searches everything again."""
        self._locked = None

    def locate(self, img: Image.Image) -> Here | None:
        """The map the box shows, with the yellow dot's spot (fractions of the KB picture); None when the box shows
        no known minimap confidently."""
        if img is None or min(img.size) < _MIN_SIDE:
            return None
        rgb = img.convert("RGB")
        grey = rgb.convert("L")
        full = np.asarray(grey, dtype=np.float32)
        if self._locked is not None:
            hit = self._recheck(full)
            if hit is not None:
                return self._remember(hit, np.asarray(rgb))
        k = min(1.0, _COARSE_MAX / max(rgb.size))
        small_img = grey.resize((max(1, round(rgb.width * k)), max(1, round(rgb.height * k))), Image.BILINEAR)
        rk = min(1.0, _RERANK_MAX / max(rgb.size))      # the re-rank looks full-size-ish, not full-size: its
        med_img = grey.resize((max(1, round(rgb.width * rk)), max(1, round(rgb.height * rk))), Image.BILINEAR)
        hit = self._search(full, np.asarray(med_img, dtype=np.float32),
                           np.asarray(small_img, dtype=np.float32), k, *rgb.size)
        if hit is None:
            return None
        return self._remember(hit, np.asarray(rgb))

    # ------------------------------------------------------------- pictures

    def _pic(self, mid: str) -> tuple[Image.Image, np.ndarray] | None:
        """The KB picture composited onto black (as the game draws it): its grey shape and its colours, loaded
        once no matter how many scans compare against it."""
        if mid not in self._pics:
            entry = None
            p = self._graph.minimap(mid)
            try:
                art = Image.open(p).convert("RGBA") if p is not None else None
            except (OSError, ValueError):
                art = None
            if art is not None:
                flat = Image.alpha_composite(Image.new("RGBA", art.size, (0, 0, 0, 255)), art).convert("RGB")
                entry = (flat.convert("L"), np.asarray(flat))
            self._pics[mid] = entry
        return self._pics[mid]

    def _neediness(self, mid: str) -> float:
        """How much this picture loses matching itself 3% off (0-1): thin features crater past a few percent while
        chunky ones shrug it off, so needy maps get dense trial scales and the rest stay cheap. Calibrated once per
        picture on disk (the module cache shares it between readers: 275 pictures take ~a second the first time)."""
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

    def _match(self, img: np.ndarray, mid: str, scale: float, k: float = 1.0) -> _Hit | None:
        """This candidate at exactly this scale on this box (full-size for the cheap recheck and the refinement,
        half-size for the re-rank, whose offsets are thrown away): None when it cannot fit. k shrinks the template
        to the box's size; the area share is the same at any size, so one prefilter serves all."""
        pic = self._pic(mid)
        if pic is None:
            return None
        art, _ = pic
        sw, sh = max(1, round(art.width * scale * k)), max(1, round(art.height * scale * k))
        H, W = img.shape
        if sw > W or sh > H or min(sw, sh) < _MIN_SIDE or sw * sh < _MIN_AREA * W * H:
            return None
        found = _ncc(img, np.asarray(art.resize((sw, sh), Image.BILINEAR), dtype=np.float32))
        if found is None:
            return None
        return _Hit(mid, found[0], scale, found[1], found[2])

    def _recheck(self, full: np.ndarray) -> _Hit | None:
        """The remembered map at its scale first; then the maps next door (the player walked through a portal) at
        the same scale. None when neither fits: something else is on screen, search everything."""
        mid, scale, _, _ = self._locked
        hit = self._match(full, mid, scale)
        if hit is not None and hit.score >= VERIFY_THRESHOLD:
            return hit
        best: _Hit | None = None
        seen = {mid}
        for leg in self._graph.edges.get(mid, ()):
            if leg.to in seen:
                continue
            seen.add(leg.to)
            hit = self._match(full, leg.to, scale)
            if hit is not None and hit.score >= VERIFY_THRESHOLD and (best is None or hit.score > best.score):
                best = hit
        return best

    def _search(self, full: np.ndarray, med: np.ndarray, small: np.ndarray, k: float, W: int, H: int) -> _Hit | None:
        """Every candidate on the shrunken box, each from its largest fitting scale downwards; the best two dozen
        re-ranked bigger (but not full-size: only their scores and scales go on), the six best of those refined
        around it, where the call is made."""
        contenders = self._top(small, k, W, H, 24)
        rk = med.shape[1] / W
        ranked = [hit for c in contenders if (hit := self._match(med, c.mid, c.scale, rk)) is not None]
        ranked.sort(key=lambda h: h.score, reverse=True)
        short = ranked[:6]      # ... plus ultra-steep maps, which the single med scale can bury: their true scale
        short += [c for c in ranked[6:24] if self._neediness(c.mid) >= _ULTRA_DROP][:10 - len(short)]
        scored = [hit for c in short if (hit := self._refine(full, c)) is not None]
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
        next door to the lock (the player walks through portals), else the best match itself (on its own box the
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

    def _top(self, small: np.ndarray, k: float, W: int, H: int, n: int) -> list[_Hit]:
        """Each candidate's best placing on the shrunken box; the n highest, each a different map. Trials crowd
        near the fitting scale, where a tight box's true scale lives — densely for thin-featured pictures (which
        crater a few percent off, a sparse grid's best there can be a small-scale fluke), sparsely with satellites
        for the rest — plus a sparse tail for loose boxes. Candidates that cannot fit the box, or would be specks
        next to it, are skipped unread."""
        per_mid: dict[str, _Hit] = {}
        for mid in self._candidates:
            pic = self._pic(mid)
            if pic is None:
                continue
            art, _ = pic

            def check(s: float, art=art, mid=mid) -> _Hit | None:
                fw, fh = art.width * s, art.height * s
                if fw > W or fh > H or min(fw, fh) < _MIN_SIDE or fw * fh < _MIN_AREA * W * H:
                    return None
                sw, sh = max(1, round(fw * k)), max(1, round(fh * k))
                if sw > small.shape[1] or sh > small.shape[0]:
                    return None
                found = _ncc(small, np.asarray(art.resize((sw, sh), Image.BILINEAR), dtype=np.float32))
                return _Hit(mid, found[0], s, found[1], found[2]) if found is not None else None

            best: _Hit | None = None
            top = min(3.0, W / art.width, H / art.height) * 0.999
            need = self._neediness(mid)
            if need >= _HYPER_DROP:
                step, floor, dense = 1.004, max(0.5, top * 0.75), True
            elif need >= _NEEDY_DROP:
                step, floor, dense = 1.06, max(0.5, top * 0.6), True
            else:
                step, floor, dense = _GRID, max(0.5, top * 0.6), False
            s = top
            while s >= floor:
                hit = check(s)
                if hit is not None and (best is None or hit.score > best.score):
                    best = hit
                s /= step
            if not dense and best is not None:
                for s in (best.scale / 1.12, best.scale * 1.12):
                    if 0.5 <= s <= 3.0:
                        hit = check(s)
                        if hit is not None and hit.score > best.score:
                            best = hit
            s = floor / _GRID               # ... sparse below, for loose boxes
            while s >= 0.5:
                hit = check(s)
                if hit is not None and (best is None or hit.score > best.score):
                    best = hit
                s /= _GRID
            if best is not None and (mid not in per_mid or best.score > per_mid[mid].score):
                per_mid[mid] = best
        return sorted(per_mid.values(), key=lambda h: h.score, reverse=True)[:n]

    def _refine(self, full: np.ndarray, coarse: _Hit) -> _Hit | None:
        """The coarse trials under the microscope, at full resolution. Most pictures get a sparse look around the
        coarse scale; steep ones a denser one; the steepest (whose match lives in a sub-percent peak) a narrow,
        dense comb around it — the coarse grid already lands within ~3%."""
        need = self._neediness(coarse.mid)
        n, lo, span = (13, 0.97, 0.06) if need >= _ULTRA_DROP else ((9, 0.86, 0.28) if need >= _NEEDY_DROP
                                                                    else (5, 0.86, 0.28))
        best: _Hit | None = None
        for i in range(n):
            hit = self._match(full, coarse.mid, min(3.0, max(0.5, coarse.scale * (lo + i * (span / (n - 1))))))
            if hit is not None and (best is None or hit.score > best.score):
                best = hit
        return best

    def _remember(self, hit: _Hit, arr: np.ndarray) -> Here:
        self._locked = (hit.mid, hit.scale, hit.x, hit.y)
        return Here(hit.mid, self._dot(arr, hit))

    # ------------------------------------------------------------ player dot

    def _dot(self, arr: np.ndarray, hit: _Hit) -> tuple[float, float] | None:
        """The yellow player dot's centre as fractions of the KB picture, None when not seen. Only the matched
        rectangle is looked at (the title text is yellowish too). A blob the art cannot explain is the player; one
        the art half-covers is art — unless it is big and far brighter (the game's dot painted over yellow lava,
        which a small bright speck can never be). Anything dimmer or ambiguous is left unseen."""
        pic = self._pic(hit.mid)
        if pic is None:
            return None
        _, art = pic
        H, W, _ = arr.shape
        sw = max(1, round(art.shape[1] * hit.scale))
        sh = max(1, round(art.shape[0] * hit.scale))
        region = arr[max(hit.y, 0):min(hit.y + sh, H), max(hit.x, 0):min(hit.x + sw, W)]
        if region.size == 0:
            return None
        rs = np.asarray(Image.fromarray(art).resize((region.shape[1], region.shape[0]), Image.BILINEAR))
        yellow = (rs[:, :, 0] >= _ART_YELLOW) & (rs[:, :, 1] >= _ART_YELLOW) & (rs[:, :, 2] <= _ART_YELLOW)
        mask = (region[:, :, 0] >= _DOT_R) & (region[:, :, 1] >= _DOT_G) & (region[:, :, 2] <= _DOT_B)
        for one in sorted(_blobs(mask), key=len, reverse=True):
            if not _DOT_MIN <= len(one) <= _DOT_MAX:
                continue
            ys = np.fromiter((y for y, _ in one), dtype=np.intp, count=len(one))
            xs = np.fromiter((x for _, x in one), dtype=np.intp, count=len(one))
            frac, bright = _art_blob(yellow, rs, region, ys, xs)
            if frac >= 0.5 and (len(one) < _DOT_BIG or bright < _BRIGHT_MIN):
                continue
            return (min(max(float(xs.mean()) / sw, 0.0), 1.0), min(max(float(ys.mean()) / sh, 0.0), 1.0))
        return None
