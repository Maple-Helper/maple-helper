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
