"""The minimap reader: the map from the minimap's title text, the player's yellow dot from its picture.

Live captures (skipped without the real KB, like the other real-KB tests): the whole drawn box goes through
RapidOCR — the header's street and map-name lines name the map, and the one KB picture only places the dot.
Perion's spot was hand-derived before (the dot's bright core against the verified match); the 3-Way and Market
spots below are read-derived values verified to sit on the yellow dot. Name resolution is pure (resolve_name)
and tested without OCR; stubbed OCR rows steer synthetic panels through the same path. The OCR engine loads
once for the module (under half a second).
"""
from pathlib import Path

import numpy as np
import pytest

REAL_KB = Path(__file__).resolve().parent.parent / "data" / "kb"
needs_kb = pytest.mark.skipif(not (REAL_KB / "routes.json").exists(), reason="no routes.json in the real knowledge base")

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module")
def graph():
    from maplehelper import routes
    from maplehelper.kb import KnowledgeBase
    return routes.of(KnowledgeBase(REAL_KB))


@pytest.fixture(scope="module")
def loc(graph):
    """One reader for the live tests: its OCR engine loads once, and the header rows it learns carry over."""
    from maplehelper.minimap import Locator
    return Locator(graph)


def _box(name):
    from PIL import Image
    return Image.open(FIXTURES / name).convert("RGB")


# ------------------------------------------------------------ live captures


@needs_kb
def test_live_perion(loc):
    """The title names Perion (picture matching alone once read other maps wrongly), and the dot lands where the
    verified picture match put it."""
    here = loc.locate(_box("minimap_perion_live.png"))
    assert here is not None and here.map == "010004000"
    assert here.spot == pytest.approx((0.26, 0.45), abs=0.03)


@needs_kb
def test_live_three_way_road_split(loc):
    """3-Way Road-Split on the same dark panel, crowded with red monster pills: named from the title, dotted."""
    here = loc.locate(_box("minimap_3way_live.png"))
    assert here is not None and here.map == "010000020"
    assert here.spot == pytest.approx((0.22, 0.70), abs=0.03)


@needs_kb
def test_live_henesys_market(loc):
    """Henesys Market: named from the title; its navy backdrop fails the flat-background test, so the background
    falls back to the dark pixels before the picture aligns."""
    here = loc.locate(_box("minimap_henesys_market_live.png"))
    assert here is not None and here.map == "010001040"
    assert here.spot == pytest.approx((0.46, 0.40), abs=0.03)


@needs_kb
def test_live_kerning_city_without_the_dot_in_view(loc):
    """Kerning City with the player's dot outside the part of the map the window shows: the map, no spot."""
    here = loc.locate(_box("minimap_kerning_live.png"))
    assert here is not None and here.map == "010003000" and here.spot is None


@needs_kb
def test_live_collapsed_window_in_a_building(loc):
    """Inside a building the game folds the window to one title line, 'Victoria Road : Warriors' Sanctuary', over
    the game itself: the map from that line (up where the title bar's furniture is dropped), no spot (no map
    shown); then unfolded again, the two-line header reads as before."""
    here = loc.locate(_box("minimap_collapsed_live.png"))
    assert here is not None and here.map == "010004003" and here.spot is None
    here = loc.locate(_box("minimap_perion_live.png"))
    assert here is not None and here.map == "010004000"


@pytest.mark.parametrize("text,expected", [
    ("Victoria Road :Warriors'Sanctuary", ("Warriors'Sanctuary", "Victoria Road")),
    ("Victoria Road : Warriors' Sanctuary WORLD", ("Warriors' Sanctuary", "Victoria Road")),
    ("Victoria Road Warriors' Sanctuary", ("Warriors' Sanctuary", "Victoria Road")),    # the colon read as nothing
    ("MINI MAP", None),
    ("Victoria Road", None),
    ("Perion", None),
])
def test_collapsed_title_splits_street_and_map(text, expected):
    """The folded title's 'Street : Map', with or without the colon read, WORLD trailing; anything else is None."""
    from maplehelper.minimap import collapsed_title
    assert collapsed_title(text, ["Victoria Road", "Hidden Street", ""]) == expected


@needs_kb
def test_header_crop_falls_back_to_the_whole_box(loc):
    """A stale crop (a redrawn box) yields no header: the whole box is read and the crop relearned."""
    loc._header_rows = 20
    here = loc.locate(_box("minimap_perion_live.png"))
    assert here is not None and here.map == "010004000"
    assert here.spot == pytest.approx((0.26, 0.45), abs=0.03)
    assert loc._header_rows > 100


@needs_kb
def test_second_read_rechecks_the_lock(graph, monkeypatch):
    """The same box again: the locked scale re-verifies with one comparison (no trial sweep); after reset the
    sweep runs again. OCR rows are stubbed — the panel, alignment and dot are real."""
    from maplehelper.minimap import Locator
    rows = [("MINI MAP", 21.0, 27.0), ("WORLD", 21.0, 28.0),
            ("Victoria Road", 56.0, 67.0), ("Perion", 78.0, 89.0)]
    loc = Locator(graph)
    monkeypatch.setattr(loc, "_ocr_rows", lambda view: list(rows))
    box = _box("minimap_perion_live.png")
    first = loc.locate(box)
    assert first is not None and first.map == "010004000"
    assert first.spot == pytest.approx((0.26, 0.45), abs=0.03)
    calls = []
    orig = Locator._coarse_best

    def spy(self, *args, **kwargs):
        calls.append(1)
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(Locator, "_coarse_best", spy)
    second = loc.locate(box)
    assert second == first and not calls
    loc.reset()
    third = loc.locate(box)
    assert calls and third == first


# ------------------------------------------------------- name resolution


@needs_kb
@pytest.mark.parametrize("text,street,expected", [
    ("Perion", None, "010004000"),
    ("perion", None, "010004000"),
    ("  3-Way   Road-Split ", "Victoria Road", "010000020"),
    ("Henesys Market", "Victoria Road", "010001040"),
    ("Henesys Markel", None, "010001040"),      # OCR confusions: l/i, missing hyphen
    ("3-Way RoadSplit", None, "010000020"),
    ("PerI0n", None, "010004000"),
])
def test_resolve_names(graph, text, street, expected):
    """Exact (any case, settled whitespace) and OCR-tolerant fuzzy reads name their maps."""
    from maplehelper.minimap import resolve_name
    assert resolve_name(graph, text, street) == expected


@needs_kb
@pytest.mark.parametrize("street,expected", [
    ("Hidden Street", "010001021"),
    ("Rainbow Street", "000001003"),
    (None, "010001021"),                        # no street: town first, the deterministic pick
])
def test_resolve_street_breaks_the_tie(graph, street, expected):
    """Two maps share 'Mushroom Garden': the street line picks, else the town-first rule."""
    from maplehelper.minimap import resolve_name
    assert resolve_name(graph, "Mushroom Garden", street) == expected


@needs_kb
def test_resolve_previous_map_and_neighbours_break_the_tie(graph):
    """Two maps share 'Mushroom Town': the previous map wins, else one of its neighbours, else deterministic."""
    from maplehelper.minimap import resolve_name
    assert resolve_name(graph, "Mushroom Town", None, "000000010") == "000000010"
    assert resolve_name(graph, "Mushroom Town", None, "000000021") == "000000020"
    assert resolve_name(graph, "Mushroom Town") == "000000020"


@needs_kb
def test_resolve_picture_scores_break_the_tie(graph):
    """The same tie with picture fits: a clear winner counts, a near-tie falls back to deterministic."""
    from maplehelper.minimap import resolve_name
    assert resolve_name(graph, "Mushroom Town", scores={"000000010": 0.9, "000000020": 0.6}) == "000000010"
    assert resolve_name(graph, "Mushroom Town", scores={"000000010": 0.62, "000000020": 0.60}) == "000000020"
    assert resolve_name(graph, "Mushroom Town", scores={"000000010": 0.4, "000000020": 0.3}) == "000000020"


@needs_kb
@pytest.mark.parametrize("text", ["Potion Shop", "Rocky Road", "", "Xyzzy"])
def test_resolve_unrelated_text_is_no_map(graph, text):
    """Garbage, ambiguity between two names, and nothing: no map rather than a wrong one."""
    from maplehelper.minimap import resolve_name
    assert resolve_name(graph, text) is None


@pytest.mark.parametrize("text,yc,height,expected", [
    ("MINI MAP", 21.0, 316, True),
    ("minI mAP", 21.0, 316, True),
    ("WORLD", 21.0, 316, True),
    ("RLO", 20.0, 316, True),        # a tight crop's WORLD fragment: caught by the title-bar band
    ("Perion", 78.0, 316, False),
    ("Victoria Road", 56.0, 316, False),
])
def test_chrome_lines_are_not_header(text, yc, height, expected):
    """The window furniture in any case/spacing, and anything up in the title bar, never names a map."""
    from maplehelper.minimap import is_chrome
    assert is_chrome(text, yc, height) is expected


# ------------------------------------------------------------- no guessing


@needs_kb
def test_noise_is_no_map(loc):
    """Static through the real reader: no text, no map — however vaguely the art might fit."""
    rnd = np.random.RandomState(7).randint(0, 256, (250, 300, 3)).astype(np.uint8)
    from PIL import Image
    assert loc.locate(Image.fromarray(rnd)) is None


@needs_kb
def test_no_header_text_is_no_map_even_with_matching_art(loc, monkeypatch):
    """A real minimap box whose header yields nothing: None, though the art would match perfectly."""
    monkeypatch.setattr(loc, "_ocr_rows", lambda view: [])
    assert loc.locate(_box("minimap_perion_live.png")) is None


@needs_kb
def test_unalignable_picture_keeps_the_map(graph):
    """The title names Perion but the panel is flat grey: the map stands, with a None spot."""
    from PIL import Image
    from maplehelper.minimap import Here, Locator
    loc = Locator(graph)
    loc._ocr_rows = lambda view: [("Perion", 60.0, 70.0)]
    assert loc.locate(Image.new("RGB", (300, 250), (60, 60, 60))) == Here("010004000", None)


@needs_kb
def test_map_without_a_picture_keeps_the_map(graph):
    """A known map with no KB picture: the title still names it, with a None spot."""
    from types import SimpleNamespace
    from PIL import Image
    from maplehelper.minimap import Here, Locator
    from maplehelper.routes import MapInfo
    stub = SimpleNamespace(
        maps={"999": MapInfo("999", "Testville", "Test Street", True, "", None, [])},
        edges={},
        minimap=lambda mid: None,
    )
    loc = Locator(stub)
    loc._ocr_rows = lambda view: [("Testville", 60.0, 70.0)]
    assert loc.locate(Image.new("RGB", (300, 250), (60, 60, 60))) == Here("999", None)


@needs_kb
def test_dead_engine_is_no_answer_and_logs_once(graph, monkeypatch, caplog):
    """RapidOCR won't load: reads are None, and the warning fires once no matter how many reads."""
    import logging
    import sys
    from PIL import Image
    from maplehelper.minimap import Locator
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    loc = Locator(graph)
    box = Image.new("RGB", (300, 250), (60, 60, 60))
    with caplog.at_level(logging.WARNING, logger="maplehelper"):
        assert loc.locate(box) is None
        assert loc.locate(box) is None
    assert sum("OCR unavailable" in r.message for r in caplog.records) == 1


def test_engine_is_one_thread_and_never_upscales(monkeypatch):
    """The OCR engine is built with one ONNX thread and a shrink-only detector resize: the defaults spun every
    core for ~1 s per read (the scanner reads every second) and blew a header strip up to 736 px tall."""
    import sys
    import types
    from maplehelper.minimap import Locator
    made = []
    fake = types.ModuleType("rapidocr")
    fake.RapidOCR = lambda **kw: made.append(kw) or object()
    monkeypatch.setitem(sys.modules, "rapidocr", fake)
    loc = Locator(None)
    assert loc._engine() is not None
    assert loc._engine() is loc._engine()          # built once
    assert len(made) == 1
    params = made[0]["params"]
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 1
    assert params["EngineConfig.onnxruntime.inter_op_num_threads"] == 1
    assert params["Det.limit_type"] == "max"


@needs_kb
def test_the_known_maps_street_read_alone_is_no_map(graph):
    """Only the street line read ("Victoria Road", the map line under it missed): Victoria Road is a map's name too,
    and the player seemed to jump there and back every few seconds (live, 2026-10-08). On a known map on that street,
    a lone street line is no answer; with both lines, the map line still wins."""
    from maplehelper.minimap import Locator
    loc = Locator(graph)
    loc._prev = "010003010"                     # Kerning City Construction Site, on Victoria Road
    assert loc._header([("Victoria Road", 60.0, 68.0)], 300, 0) is None
    got = loc._header([("Victoria Road", 60.0, 68.0), ("Kerning City Construction Site", 80.0, 90.0)], 300, 0)
    assert got is not None and got[0] == ("Kerning City Construction Site", "Victoria Road")
    loc._prev = None                            # nothing known yet: a lone line is still read as it is
    assert loc._header([("Victoria Road", 60.0, 68.0)], 300, 0) is not None


@needs_kb
def test_live_busy_construction_site_places_the_dot(graph):
    """Other players' red dots all over and the game showing through: the fit peaks sharply (0.55 at one scale,
    under 0.5 a 4% step either side), and the player's dot was never placed (live, 2026-10-08)."""
    from maplehelper.minimap import Locator
    here = Locator(graph).locate(_box("minimap_construction_busy_live.png"))
    assert here is not None and here.map == "010003010"
    assert here.spot == pytest.approx((0.78, 0.65), abs=0.04)
