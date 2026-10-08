"""The minimap reader: recognizing the map from a picture of the game's minimap, and the player's yellow dot on it.

Synthetic boxes from the real KB pictures (skipped without them, like the other real-KB tests): the art pasted
into a dark frame with a title strip, at game scales, with a yellow player dot and red/green other dots on top.
"""
from pathlib import Path

import numpy as np
import pytest

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")


@pytest.fixture(scope="module")
def graph():
    from maplehelper.kb import KnowledgeBase
    from maplehelper import routes
    return routes.of(KnowledgeBase(REAL_KB))


@pytest.fixture(scope="module")
def loc(graph):
    """One reader for the search tests: its KB pictures load once, and whichever map the previous test left locked
    never matches the next box, so every test still searches (the lock test below uses its own reader)."""
    from maplehelper.minimap import Locator
    return Locator(graph)


_DOT_SPOTS = ((0.30, 0.62), (0.55, 0.40), (0.75, 0.70), (0.20, 0.30), (0.65, 0.55), (0.12, 0.55), (0.42, 0.25),
              (0.85, 0.45), (0.62, 0.80), (0.35, 0.15))


def _shot(graph, mid, scale=1.0, pick=0, dot=True):
    """A player's drawn box around this map's minimap: the art at this scale on black (as the game draws it), a
    dark frame with a title strip and fake title text around it, red and green dots on top, and (unless dot=False)
    the yellow player dot. Returns the box and the dot's fractions of the KB picture (None without it). The dot
    goes on plain background, never on yellowish art (which the reader rightly refuses to call the player)."""
    from PIL import Image, ImageDraw
    art = Image.open(graph.minimap(mid)).convert("RGBA")
    sw, sh = max(1, round(art.width * scale)), max(1, round(art.height * scale))
    flat = Image.alpha_composite(Image.new("RGBA", (sw, sh), (0, 0, 0, 255)),
                                 art.resize((sw, sh), Image.BILINEAR)).convert("RGB")
    free = []
    for fx, fy in _DOT_SPOTS:
        x, y = min(int(fx * sw), sw - 1), min(int(fy * sh), sh - 1)
        patch = np.asarray(flat.crop((max(0, x - 2), max(0, y - 2), min(sw, x + 3), min(sh, y + 3)))).reshape(-1, 3)
        if all(r <= 120 or g <= 120 or b >= 180 for r, g, b in patch):
            free.append((x / sw, y / sh))
    dot = free[pick % len(free)] if dot and free else None
    title, margin = 24, 12
    box = Image.new("RGB", (sw + margin * 2, sh + title + margin * 2), (24, 22, 28))
    d = ImageDraw.Draw(box)
    d.rectangle([margin, margin, margin + sw - 1, margin + title - 8], fill=(10, 10, 14))
    d.rectangle([margin + 8, margin + 6, margin + 70, margin + 12], fill=(205, 205, 205))
    box.paste(flat, (margin, margin + title))
    d.rectangle([margin - 1, margin + title - 1, margin + sw, margin + title + sh], outline=(95, 95, 105))
    r = max(2, round(3 * scale))
    ax, ay = margin, margin + title
    dots = [((0.70, 0.30), (255, 0, 0)), ((0.52, 0.80), (0, 200, 0))]
    if dot is not None:
        dots.append((dot, (255, 255, 0)))
    for frac, colour in dots:
        cx, cy = ax + frac[0] * sw, ay + frac[1] * sh
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour)
    return box, dot


@needs_kb
@pytest.mark.parametrize("scale", [1.0, 1.25, 1.75, 2.0])
def test_finds_henesys_at_any_scale(graph, loc, scale):
    """In-game scaling is arbitrary (a 125% Windows makes 1.25): every grid gap must land a trial near it."""
    box, dot = _shot(graph, "010001000", scale)
    here = loc.locate(box)
    assert here is not None and here.map == "010001000"
    assert here.spot == pytest.approx(dot, abs=0.03)


@needs_kb
def test_no_dot_means_no_spot(graph):
    """The map is recognized from its art alone; but a yellow speck painted on the KB picture itself (blown up to
    dot size at 2x) is art, not the player."""
    from maplehelper.minimap import Locator
    box, dot = _shot(graph, "010001000", 2.0, dot=False)
    assert dot is None
    here = Locator(graph).locate(box)
    assert here is not None and here.map == "010001000" and here.spot is None


@needs_kb
@pytest.mark.parametrize("mid,scale", [("000000060", 1.5), ("010000000", 1.0)])
def test_finds_other_sizes_and_scales(graph, loc, mid, scale):
    """A compact map drawn half again as big, and a wide town at 1:1: the scale is the player's setup, not the map."""
    box, dot = _shot(graph, mid, scale)
    here = loc.locate(box)
    assert here is not None and here.map == mid
    assert here.spot == pytest.approx(dot, abs=0.03)


@needs_kb
def test_noise_is_no_map(loc):
    """Static that matches nothing: no map, however vaguely, rather than a wrong one."""
    from PIL import Image
    rnd = np.random.RandomState(7).randint(0, 256, (250, 300, 3)).astype(np.uint8)
    assert loc.locate(Image.fromarray(rnd)) is None


@needs_kb
def test_duplicate_pictures_are_not_rejected(graph, loc):
    """The Free Market booths share one drawing: six maps tie within the margin, so the reader must pick one of
    the family (deterministically) rather than answer nothing."""
    from PIL import Image
    fam = sorted(m for m in graph.maps if graph.name(m).startswith("Free Market <")
                 and Image.open(graph.minimap(m)).size == (93, 64))
    assert len(fam) > 2
    box, dot = _shot(graph, "080002006", 1.0)
    first = loc.locate(box)
    assert first is not None and first.map in fam and first.spot == pytest.approx(dot, abs=0.03)
    again = loc.locate(box)
    assert again is not None and again.map == first.map


@needs_kb
def test_portrait_maps_match(graph, loc):
    """Tall narrow pictures (a tree, a field) match as well as wide ones, at 1:1 and enlarged."""
    for mid, scale in [("010002061", 1.0), ("010002061", 1.5), ("010002010", 1.0)]:
        box, dot = _shot(graph, mid, scale)
        here = loc.locate(box)
        assert here is not None and here.map == mid
        assert here.spot == pytest.approx(dot, abs=0.03)

@needs_kb
def test_second_call_uses_the_lock_and_reset_drops_it(graph, monkeypatch):
    """The dot moved but the map and scale didn't: recognized without searching everything; after reset the full
    search runs again."""
    from maplehelper.minimap import Locator
    loc = Locator(graph)
    box, dot = _shot(graph, "010001000", 1.0, pick=0)
    first = loc.locate(box)
    assert first is not None and first.map == "010001000" and first.spot == pytest.approx(dot, abs=0.03)
    calls = []
    orig = Locator._search

    def spy(self, *args, **kwargs):
        calls.append(1)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Locator, "_search", spy)
    moved, moved_dot = _shot(graph, "010001000", 1.0, pick=1)
    second = loc.locate(moved)
    assert second is not None and second.map == "010001000" and second.spot == pytest.approx(moved_dot, abs=0.03)
    assert not calls
    loc.reset()
    third = loc.locate(moved)
    assert calls and third is not None and third.map == "010001000"


def _partial_box(graph, mid, scale, x0, win_w=300, win_h=150, pick=0):
    """A cropped live view of this map's minimap: the art at this scale with a win_w-wide slice from x0 on a grey
    (65,65,65) panel (taller than the art, so grey letterboxes it top and bottom, as a scrolled view does), a
    light-blue frame with a title strip around it, red/green/blue marker pills and the yellow player dot on top.
    Returns the box and the dot's fractions of the KB picture: the dot goes on plain background (never on
    yellowish art, which the reader rightly refuses to call the player) and must sit inside the window."""
    from PIL import Image, ImageDraw
    art = Image.open(graph.minimap(mid)).convert("RGBA")
    sw, sh = max(1, round(art.width * scale)), max(1, round(art.height * scale))
    big = np.asarray(art.resize((sw, sh), Image.BILINEAR))
    free = []
    for fx, fy in _DOT_SPOTS:
        x, y = min(int(fx * sw), sw - 1), min(int(fy * sh), sh - 1)
        patch = np.asarray(Image.fromarray(big[..., :3]).crop(
            (max(0, x - 2), max(0, y - 2), min(sw, x + 3), min(sh, y + 3)))).reshape(-1, 3)
        if all(r <= 120 or g <= 120 or b >= 180 for r, g, b in patch):
            free.append((fx, fy))
    assert free, "no plain-background dot spot on this art"
    dot = free[pick % len(free)]
    assert x0 / sw <= dot[0] <= (x0 + win_w) / sw, "dot outside the cropped window"
    bg = (65, 65, 65)
    crop = big[:, x0:x0 + win_w]
    panel = Image.new("RGB", (win_w, win_h), bg)
    yoff = (win_h - crop.shape[0]) // 2
    panel.paste(Image.alpha_composite(
        Image.new("RGBA", (crop.shape[1], crop.shape[0]), bg + (255,)),
        Image.fromarray(crop)).convert("RGB"), (0, yoff))
    d = ImageDraw.Draw(panel)
    for (fx, fy), colour in [((0.62, 0.50), (255, 0, 0)), ((0.40, 0.70), (0, 200, 0))]:
        cx, cy = fx * sw - x0, fy * sh + yoff
        if 0 <= cx < win_w and 0 <= cy < win_h:
            d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=colour)
    cx, cy = 0.55 * sw - x0, 0.30 * sh + yoff
    if 0 <= cx < win_w and 0 <= cy < win_h:
        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(30, 120, 220), width=2)
    cx, cy = dot[0] * sw - x0, dot[1] * sh + yoff
    d.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 255, 0))
    b, w = 3, 2
    box = Image.new("RGB", (win_w + 2 * (b + w), 30 + win_h + 2 * (b + w)), (140, 170, 200))
    dd = ImageDraw.Draw(box)
    dd.rectangle([b + w, b + w + 30 - 1, b + w + win_w - 1, b + w + 30 + win_h - 1], fill=(255, 255, 255))
    box.paste(panel, (b + w, b + w + 30))
    dd.rectangle([b + w + 10, b + w + 8, b + w + 90, b + w + 16], fill=(240, 240, 240))
    return box, dot


@needs_kb
def test_live_perion_cropped_view(loc):
    """A real capture (tests/fixtures/minimap_perion_live.png, 336x316): the MINI MAP window with its title bar, a
    light-blue Victoria Road / Perion header, and a dark-grey map panel showing only the top ~60% of Perion at
    ~1.5x, with portal rings, NPC/monster pills, a cyan arrow and the yellow dot on top. The expected spot is
    hand-derived: the dot's bright core (R>240, G>200, B<100 in rows 150-230 x cols 50-130: 40 px) centroids at
    ~(88.2, 192.3) box pixels, i.e. ~(84, 77) on the detected (4, 115)-(324, 290) panel; the verified match (a
    side-by-side overlay blend of the panel against the composited KB art, plus the refined NCC peak) puts the
    204x192 picture at ~1.55x with its top-left ~(0, -58) panel pixels, so the dot sits ((84-0)/318,
    (77+58)/299) = (0.26, 0.45) of the full picture."""
    from PIL import Image
    box = Image.open(Path(__file__).resolve().parent / "fixtures" / "minimap_perion_live.png").convert("RGB")
    here = loc.locate(box)
    assert here is not None and here.map == "010004000"
    assert here.spot == pytest.approx((0.26, 0.45), abs=0.03)


@needs_kb
def test_live_three_way_road_split(loc):
    """A second real capture from the same player's box (tests/fixtures/minimap_3way_live.png): 3-Way Road-Split
    (318x156) on the same dark panel, crowded with red monster pills and the yellow dot, read as that map."""
    from PIL import Image
    box = Image.open(Path(__file__).resolve().parent / "fixtures" / "minimap_3way_live.png").convert("RGB")
    here = loc.locate(box)
    assert here is not None and here.map == "010000020" and here.spot is not None


@needs_kb
def test_partial_window_cropped_view(graph, loc):
    """A synthetic cropped view: Henesys at 1.5x (646x111) seen through a 300x150 window from its middle, so the
    scaled picture overhangs the grey panel left and right while the panel letterboxes it top and bottom, with a
    frame, a title strip and marker pills around/over it. The map still matches, and the dot maps through the
    partial-view offset onto the full picture."""
    box, dot = _partial_box(graph, "010001000", 1.5, 173)
    here = loc.locate(box)
    assert here is not None and here.map == "010001000"
    assert here.spot == pytest.approx(dot, abs=0.03)


@needs_kb
def test_scrolled_view_holds_the_lock(graph, monkeypatch):
    """The player walked right: the same map at the same scale, the window slid 50 art pixels over. The locked
    map re-verifies by re-searching offsets at the locked scale (no full search), and the unmoved dot lands on
    the same fractions of the picture."""
    from maplehelper.minimap import Locator
    loc = Locator(graph)
    first_box, dot = _partial_box(graph, "010001000", 1.5, 100)
    first = loc.locate(first_box)
    assert first is not None and first.map == "010001000" and first.spot == pytest.approx(dot, abs=0.03)
    calls = []
    orig = Locator._search

    def spy(self, *args, **kwargs):
        calls.append(1)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Locator, "_search", spy)
    second_box, _ = _partial_box(graph, "010001000", 1.5, 150)
    second = loc.locate(second_box)
    assert second is not None and second.map == "010001000" and second.spot == pytest.approx(dot, abs=0.03)
    assert not calls
